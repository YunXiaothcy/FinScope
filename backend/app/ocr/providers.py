import base64
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class OcrUnavailable(Exception):
    pass


@dataclass(frozen=True)
class OcrResult:
    text: str
    provider: str
    language: str = ""


class OcrProvider(Protocol):
    def recognize(self, image: bytes) -> OcrResult: ...


class WindowsOcrProvider:
    def recognize(self, image: bytes) -> OcrResult:
        if sys.platform != "win32":
            raise OcrUnavailable(
                "本地 Windows OCR 不可用，请配置其他 OCR 适配器或手工填写"
            )
        try:
            result = subprocess.run(
                [
                    str(
                        Path(os.environ.get("SystemRoot", r"C:\Windows"))
                        / "System32"
                        / "WindowsPowerShell"
                        / "v1.0"
                        / "powershell.exe"
                    ),
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(Path(__file__).with_name("windows_ocr.ps1")),
                    "-Language",
                    os.getenv("FINSCOPE_OCR_LANGUAGE", "zh-Hans-CN"),
                ],
                input=base64.b64encode(image),
                capture_output=True,
                check=False,
                timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode or len(result.stdout) > 100000:
                raise ValueError("ocr_failed")
            payload = json.loads(result.stdout.decode("utf-8-sig"))
            text = base64.b64decode(payload["text_base64"], validate=True).decode(
                "utf-8"
            )
            if not text.strip():
                raise OcrUnavailable("未识别到文字，请换清晰图片或手工填写")
            return OcrResult(text[:12000], "windows", payload["language"])
        except OcrUnavailable:
            raise
        except (
            OSError,
            subprocess.SubprocessError,
            ValueError,
            KeyError,
            UnicodeError,
        ) as exc:
            raise OcrUnavailable("本地 OCR 识别失败或超时，可重试或手工填写") from exc


class DisabledOcrProvider:
    def recognize(self, image: bytes) -> OcrResult:
        raise OcrUnavailable("OCR 已关闭，请手工填写票据信息")


class MockOcrProvider:
    """仅供测试注入，不通过生产配置暴露模拟识别。"""

    def __init__(self, text: str):
        self.text = text

    def recognize(self, image: bytes) -> OcrResult:
        return OcrResult(self.text, "mock")


def get_ocr_provider() -> OcrProvider:
    if (
        os.getenv(
            "FINSCOPE_OCR_PROVIDER",
            "windows" if sys.platform == "win32" else "disabled",
        )
        == "windows"
    ):
        return WindowsOcrProvider()
    return DisabledOcrProvider()
