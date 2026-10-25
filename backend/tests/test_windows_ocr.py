import os
import sys

import pytest
from test_receipts import base, confirm, review, upload

from app.ocr.sample import sample_receipt

pytest_plugins = ["test_finance"]


@pytest.mark.skipif(
    sys.platform != "win32" or os.getenv("FINSCOPE_RUN_WINDOWS_OCR_TEST") != "1",
    reason="需显式开启 Windows 本地 OCR 实机测试",
)
def test_real_windows_ocr_then_human_review_and_confirm(ledger):
    receipt = upload(ledger, sample_receipt(), "真实引擎虚构票据.jpg").json()
    response = ledger["client"].post(
        f"{base(ledger)}/{receipt['id']}/recognize",
        headers=ledger["headers"],
        json={"version": receipt["version"]},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ocr_provider"] == "windows" and result["ocr_status"] == "ready"
    assert "88" in result["ocr_text"]
    assert ledger["client"].get(f"{ledger['base']}/transactions").json()["total"] == 0
    assert confirm(ledger, review(ledger, result).json()).status_code == 200
    assert (
        ledger["client"].get(f"{ledger['base']}/summary").json()["total_balance"]
        == "11.50"
    )
