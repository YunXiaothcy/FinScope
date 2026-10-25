import hashlib
import io
import warnings
from dataclasses import dataclass

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 16_000_000


@dataclass(frozen=True)
class SafeImage:
    data: bytes
    image_hash: str
    width: int
    height: int


def sanitize_image(filename: str, data: bytes) -> SafeImage:
    if (
        not filename
        or len(filename) > 160
        or any(char in filename for char in "/\\:\x00")
        or any(ord(char) < 32 for char in filename)
    ):
        raise HTTPException(422, "图片文件名无效，不得含路径")
    suffix = filename.rsplit(".", 1)[-1].lower()
    expected = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}.get(suffix)
    if expected is None:
        raise HTTPException(
            422, "只支持静态 JPEG、PNG 和 WebP 图片，不支持 PDF、SVG、GIF 或 HEIC"
        )
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "图片不能为空且不得超过 5 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=["JPEG", "PNG", "WEBP"]) as image:
                if image.format != expected or getattr(image, "n_frames", 1) != 1:
                    raise HTTPException(422, "图片真实类型与扩展名不符，或包含动画")
                if (
                    not 1 <= image.width <= 10000
                    or not 1 <= image.height <= 10000
                    or image.width * image.height > MAX_PIXELS
                ):
                    raise HTTPException(
                        422, "图片尺寸过大，最多 1600 万像素且单边不超过 10000"
                    )
                image.load()
                oriented = ImageOps.exif_transpose(image)
                oriented.thumbnail((2400, 2400))
                # 重新绘制到新图像，去掉 EXIF/GPS、注释及其他元数据。
                canvas = Image.new("RGB", oriented.size, "white")
                rgba = oriented.convert("RGBA")
                canvas.paste(rgba, mask=rgba.getchannel("A"))
                output = io.BytesIO()
                canvas.save(output, format="JPEG", quality=90)
                value = output.getvalue()
                if len(value) > MAX_IMAGE_BYTES:
                    raise HTTPException(413, "处理后的图片过大，请裁剪或压缩")
                return SafeImage(
                    value,
                    hashlib.sha256(value).hexdigest(),
                    canvas.width,
                    canvas.height,
                )
    except HTTPException:
        raise
    except (
        OSError,
        ValueError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise HTTPException(
            422, "图片损坏或不安全，请重新保存为普通 JPEG/PNG 后再试"
        ) from exc
