"""从代码中的 F 品牌图形导出标准应用图标，不读取用户图片。"""

from pathlib import Path

from PIL import Image, ImageDraw

output = Path(__file__).resolve().parents[1] / "frontend/public/icons"
output.mkdir(parents=True, exist_ok=True)
for filename, size in (
    ("icon-192.png", 192),
    ("icon-512.png", 512),
    ("maskable-512.png", 512),
    ("apple-touch-icon.png", 180),
):
    image = Image.new("RGB", (size, size), "#102032")
    draw = ImageDraw.Draw(image)

    # 品牌主体位于中央安全区域，适应圆形等遮罩。
    def box(x1, y1, x2, y2, icon_size=size):
        return tuple(round(value * icon_size / 100) for value in (x1, y1, x2, y2))

    draw.rounded_rectangle(box(17, 17, 83, 83), radius=round(size / 7), fill="#91ead0")
    for coordinates in ((36, 31, 44, 69), (36, 31, 65, 39), (36, 47, 60, 55)):
        draw.rectangle(box(*coordinates), fill="#102032")
    image.save(output / filename, "PNG", optimize=True)
print("Generated four FinScope icons")
