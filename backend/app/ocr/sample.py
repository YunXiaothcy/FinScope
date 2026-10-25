import io
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.services.finance import HK


def sample_receipt() -> bytes:
    image = Image.new("RGB", (1000, 800), "white")
    drawer = ImageDraw.Draw(image)
    today = datetime.now(HK).strftime("%Y/%m/%d")
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    if font_path.exists():
        font = ImageFont.truetype(str(font_path), 44)
        lines = [
            "FinScope DEMO",
            "商户：虚构餐厅",
            f"日期：{today} 12:30",
            "合计：88.50 CNY",
            "仅用于测试，不是真实票据",
        ]
    else:
        font = ImageFont.load_default(size=42)
        lines = [
            "FinScope DEMO",
            "Merchant: Demo Restaurant",
            f"Date: {today} 12:30",
            "Total: 88.50 CNY",
            "For testing only",
        ]
    for index, line in enumerate(lines):
        drawer.text((55, 70 + 120 * index), line, fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()
