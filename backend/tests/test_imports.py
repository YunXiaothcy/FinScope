import io
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi import HTTPException
from openpyxl import Workbook
from sqlalchemy import select
from test_business import report
from test_finance import post

from app.db.session import get_db
from app.main import app
from app.models import ImportBatch
from app.services import import_batch as import_service
from app.services.import_parser import MAX_BYTES, parse_file

pytest_plugins = ["test_finance", "test_business"]
CSV = "日期,收支,金额,分类,商户,备注,单号,币种\n2026-10-01,收入,2000.00,工资,虚构公司,工资,DEMO-1,CNY\n2026-10-02,支出,10.25,餐饮,虚构餐厅,午餐,DEMO-2,CNY\n2026-10-02,支出,10.25,餐饮,虚构餐厅,午餐,DEMO-2,CNY\n2026-10-03,支出,abc,餐饮,虚构餐厅,错误金额,DEMO-3,CNY\n"


def base(data):
    return data["base"].removesuffix("/finance") + "/imports"


def upload(data, content=CSV, name="测试.csv", headers=None):
    return data["client"].post(
        base(data),
        headers=headers
        if headers is not None
        else {
            **data["headers"],
            "Content-Type": "application/octet-stream",
            "X-File-Name": quote(name),
        },
        content=content.encode("utf-8-sig") if isinstance(content, str) else content,
    )


def mapping(data, **overrides):
    account = data["accounts"][0] if "accounts" in data else data["account"]
    income = data["categories"].get("income", data["categories"].get("revenue"))
    expense = data["categories"].get("expense")
    return {
        "account_id": account["id"],
        "date_column": 0,
        "type_column": 1,
        "amount_column": 2,
        "category_column": 3,
        "merchant_column": 4,
        "description_column": 5,
        "external_id_column": 6,
        "currency_column": 7,
        "category_map": {"工资": income, "餐饮": expense},
        **overrides,
    }


def preview(data, batch, **overrides):
    return data["client"].put(
        f"{base(data)}/{batch['id']}/preview",
        headers=data["headers"],
        json={"version": batch["version"], "mapping": mapping(data, **overrides)},
    )


def confirm(data, batch, key=None, rows=None, version=None):
    return data["client"].post(
        f"{base(data)}/{batch['id']}/confirm",
        headers={**data["headers"], "Idempotency-Key": key or str(uuid4())},
        json={
            "version": version or batch["version"],
            "row_ids": rows
            if rows is not None
            else [row["id"] for row in batch["rows"] if row["status"] == "ready"],
        },
    )


def xlsx(values):
    book = Workbook()
    for row in values:
        book.active.append(row)
    output = io.BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def test_upload_preview_does_not_post_confirm_is_atomic_and_retry_safe(ledger):
    response = upload(ledger)
    assert response.status_code == 201, response.text
    original = response.json()
    assert original["counts"] == {"uploaded": 4}
    data = preview(ledger, original).json()
    assert data["counts"] == {"ready": 2, "duplicate": 1, "error": 1}
    assert (
        ledger["client"].get(f"{ledger['base']}/summary").json()["total_balance"]
        == "100.00"
    )
    assert preview(ledger, original).status_code == 409
    key = str(uuid4())
    result = confirm(ledger, data, key)
    assert result.status_code == 200, result.text
    assert result.json()["counts"] == {"imported": 2, "duplicate": 1, "error": 1}
    assert confirm(ledger, data, key).json() == result.json()
    assert confirm(ledger, data).status_code == 409
    assert upload(ledger).json()["id"] == original["id"]
    summary = (
        ledger["client"]
        .get(f"{ledger['base']}/summary?start_date=2026-10-01&end_date=2026-10-31")
        .json()
    )
    assert (summary["income"], summary["expense"], summary["total_balance"]) == (
        "2000.00",
        "10.25",
        "2089.75",
    )
    records = (
        ledger["client"]
        .get(f"{ledger['base']}/transactions?start_date=2026-10-01&end_date=2026-10-31")
        .json()["items"]
    )
    assert len(records) == 2 and all(item["source"] == "import" for item in records)
    detail = ledger["client"].get(f"{base(ledger)}/{data['id']}")
    assert detail.headers["cache-control"] == "no-store"
    assert len([row for row in detail.json()["rows"] if row["transaction_id"]]) == 2


def test_overlapping_files_do_not_reimport(ledger):
    first = preview(ledger, upload(ledger).json()).json()
    assert confirm(ledger, first).status_code == 200
    second = upload(
        ledger,
        CSV + "2026-10-04,支出,2.00,餐饮,,,DEMO-4,CNY\n",
    ).json()
    data = preview(ledger, second).json()
    assert data["counts"] == {"duplicate": 3, "error": 1, "ready": 1}
    assert confirm(ledger, data).json()["counts"]["imported"] == 1


def test_selected_subset_and_tampered_rows_are_rejected(ledger):
    data = preview(ledger, upload(ledger).json()).json()
    invalid_id = next(row["id"] for row in data["rows"] if row["status"] == "error")
    assert confirm(ledger, data, rows=[invalid_id]).status_code == 422
    assert confirm(ledger, data, rows=[str(uuid4())]).status_code == 422
    ready = [row["id"] for row in data["rows"] if row["status"] == "ready"]
    assert confirm(ledger, data, rows=[ready[0], ready[0]]).status_code == 422
    result = confirm(ledger, data, rows=[ready[1]])
    assert result.status_code == 200 and result.json()["counts"]["skipped"] == 1
    assert (
        ledger["client"].get(f"{ledger['base']}/accounts").json()[0]["balance"]
        == "89.75"
    )


def test_confirm_rechecks_duplicates_created_after_preview(ledger):
    data = preview(ledger, upload(ledger).json()).json()
    assert (
        post(
            ledger,
            "income",
            "2000.00",
            occurred_at="2026-09-30T16:00:00Z",
            merchant_name="虚构公司",
            description="工资",
        ).status_code
        == 201
    )
    result = confirm(ledger, data)
    assert result.status_code == 200 and result.json()["counts"]["imported"] == 1
    assert result.json()["counts"]["duplicate"] == 2


def test_inactive_account_causes_full_rollback(ledger):
    data = preview(ledger, upload(ledger).json()).json()
    account = ledger["accounts"][0]
    assert (
        ledger["client"]
        .patch(
            f"{ledger['base']}/accounts/{account['id']}",
            headers=ledger["headers"],
            json={"name": account["name"], "is_active": False},
        )
        .status_code
        == 200
    )
    assert confirm(ledger, data).status_code == 422
    assert ledger["client"].get(f"{ledger['base']}/transactions").json()["total"] == 0
    assert (
        ledger["client"].get(f"{base(ledger)}/{data['id']}").json()["status"] == "draft"
    )


def test_failure_after_first_post_rolls_back_funds_audit_and_batch_then_retries(
    ledger, monkeypatch
):
    data = preview(ledger, upload(ledger).json()).json()
    real_post = import_service.post_transaction
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise HTTPException(503, "模拟批量入账中断")
        return real_post(*args, **kwargs)

    key = str(uuid4())
    with monkeypatch.context() as context:
        context.setattr(import_service, "post_transaction", fail_second)
        assert confirm(ledger, data, key).status_code == 503
    assert ledger["client"].get(f"{ledger['base']}/transactions").json()["total"] == 0
    assert (
        ledger["client"].get(f"{ledger['base']}/summary").json()["total_balance"]
        == "100.00"
    )
    assert (
        ledger["client"].get(f"{base(ledger)}/{data['id']}").json()["status"] == "draft"
    )
    assert confirm(ledger, data, key).json()["counts"]["imported"] == 2


def test_without_external_id_content_dedup_ignores_category_change(ledger):
    data = preview(ledger, upload(ledger).json(), external_id_column=None).json()
    assert confirm(ledger, data).status_code == 200
    new_category = (
        ledger["client"]
        .post(
            f"{ledger['base']}/categories",
            headers=ledger["headers"],
            json={"name": "新餐饮", "type": "expense"},
        )
        .json()
    )
    other = upload(ledger, CSV + "\n").json()
    result = preview(
        ledger,
        other,
        external_id_column=None,
        category_map={
            "工资": ledger["categories"]["income"],
            "餐饮": new_category["id"],
        },
    ).json()
    assert result["counts"] == {"duplicate": 3, "error": 1}


@pytest.mark.parametrize("first_has_id", [True, False])
def test_changing_external_id_mapping_cannot_bypass_content_duplicates(
    ledger, first_has_id
):
    data = preview(
        ledger, upload(ledger).json(), external_id_column=6 if first_has_id else None
    ).json()
    assert confirm(ledger, data).status_code == 200
    other = upload(ledger, CSV + "\n").json()
    result = preview(
        ledger, other, external_id_column=None if first_has_id else 6
    ).json()
    assert result["counts"] == {"duplicate": 3, "error": 1}


def test_imported_record_can_be_voided_but_receipt_stays_reserved(ledger):
    data = preview(ledger, upload(ledger).json()).json()
    assert confirm(ledger, data).status_code == 200
    records = (
        ledger["client"]
        .get(f"{ledger['base']}/transactions?start_date=2026-10-01&end_date=2026-10-31")
        .json()["items"]
    )
    for item in records:
        assert (
            ledger["client"]
            .post(
                f"{ledger['base']}/transactions/{item['id']}/void",
                headers=ledger["headers"],
                json={"version": item["version"]},
            )
            .status_code
            == 200
        )
    other = upload(ledger, CSV + "\n").json()
    assert preview(ledger, other).json()["counts"] == {"duplicate": 3, "error": 1}


def test_xlsx_numeric_amount_raw_decimal_and_excel_dates(ledger):
    data = xlsx(
        [
            ["日期", "收支", "金额", "分类"],
            [datetime(2026, 10, 1, 0, 0), "收入", 0.1, "工资"],
            [datetime(2026, 10, 2), "收入", "9999999999999999.99", "工资"],
        ]
    )
    uploaded = upload(ledger, data, "测试.xlsx")
    assert uploaded.status_code == 201, uploaded.text
    result = preview(
        ledger,
        uploaded.json(),
        merchant_column=None,
        description_column=None,
        external_id_column=None,
        currency_column=None,
    ).json()
    assert result["counts"] == {"ready": 2}
    assert result["rows"][0]["normalized"]["amount"] == "0.10"
    assert result["rows"][0]["normalized"]["occurred_at"] == "2026-09-30T16:00:00Z"
    assert result["rows"][1]["normalized"]["amount"] == "9999999999999999.99"
    assert confirm(ledger, result).status_code == 200


def test_formula_row_is_error_and_never_executed(ledger):
    uploaded = upload(
        ledger,
        xlsx(
            [["日期", "收支", "金额", "分类"], ["2026-10-01", "收入", "=1+1", "工资"]]
        ),
        "公式.xlsx",
    ).json()
    data = preview(
        ledger,
        uploaded,
        merchant_column=None,
        description_column=None,
        external_id_column=None,
        currency_column=None,
    ).json()
    assert data["counts"] == {"error": 1}
    assert "公式" in data["rows"][0]["messages"][0]


@pytest.mark.parametrize(
    "name,content,status",
    [
        ("bad.xls", b"file", 422),
        ("../bad.csv", b"x", 422),
        ("bad.xlsx", b"not-zip", 422),
        ("bad.csv", b"\0\xff\0", 422),
        ("bad.csv", b"", 413),
        ("bad.csv", b"x" * (MAX_BYTES + 1), 413),
        ("bad.csv", b"a,a\n1,2", 422),
    ],
    ids=[
        "old-xls",
        "path-name",
        "bad-xlsx",
        "binary-csv",
        "empty",
        "oversize",
        "duplicate-header",
    ],
)
def test_invalid_files(ledger, name, content, status):
    assert upload(ledger, content, name).status_code == status


def test_zip_bomb_dtd_malformed_xml_and_row_limits(ledger):
    for xml in [b'<!DOCTYPE x [<!ENTITY a "bad">]><x>&a;</x>', b"<broken>"]:
        output = io.BytesIO()
        with ZipFile(output, "w") as archive:
            archive.writestr("xl/workbook.xml", xml)
        assert upload(ledger, output.getvalue(), "bad.xlsx").status_code == 422
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("huge.xml", b"0" * (10 * 1024 * 1024 + 1))
    assert upload(ledger, output.getvalue(), "large.xlsx").status_code == 422
    assert upload(ledger, "a,b\n" + "1,2\n" * 1001).status_code == 422
    assert (
        upload(
            ledger,
            ",".join(str(i) for i in range(41))
            + "\n"
            + ",".join("x" for _ in range(41)),
        ).status_code
        == 422
    )


def test_csv_gb18030_semicolon_and_ambiguous_dates(ledger):
    text = "日期;收支;金额;分类\n24/10/2026;支出;1.20;餐饮\n"
    uploaded = upload(ledger, text.encode("gb18030")).json()
    assert uploaded["encoding"] == "gb18030"
    options = {
        "merchant_column": None,
        "description_column": None,
        "external_id_column": None,
        "currency_column": None,
        "date_format": "dmy",
    }
    data = preview(ledger, uploaded, **options).json()
    assert data["counts"] == {"ready": 1}
    assert data["rows"][0]["normalized"]["occurred_at"] == "2026-10-23T16:00:00Z"


@pytest.mark.parametrize(
    "amount,kind,currency",
    [
        ("-1.00", "支出", "CNY"),
        ("0", "收入", "CNY"),
        ("1.001", "收入", "CNY"),
        ("NaN", "收入", "CNY"),
        ("10", "转账", "CNY"),
        ("10", "退款", "CNY"),
        ("10", "收入", "USD"),
    ],
)
def test_unsafe_semantics_or_precision_become_row_errors(
    ledger, amount, kind, currency
):
    uploaded = upload(
        ledger,
        f"日期,收支,金额,分类,商户,备注,单号,币种\n2026-10-01,{kind},{amount},工资,,,A,{currency}\n",
    ).json()
    data = preview(ledger, uploaded).json()
    assert data["counts"] == {"error": 1}


def test_cancel_and_expiration_clear_raw_rows(ledger):
    uploaded = upload(ledger).json()
    result = ledger["client"].post(
        f"{base(ledger)}/{uploaded['id']}/cancel",
        headers=ledger["headers"],
        json={"version": uploaded["version"]},
    )
    assert result.status_code == 200
    assert all(
        row["raw"] is None
        for row in ledger["client"]
        .get(f"{base(ledger)}/{uploaded['id']}")
        .json()["rows"]
    )
    newer = upload(ledger).json()
    assert newer["id"] != uploaded["id"]
    session_generator = app.dependency_overrides[get_db]()
    session = next(session_generator)
    session.scalar(
        select(ImportBatch).where(ImportBatch.id == newer["id"])
    ).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    session_generator.close()
    expired = ledger["client"].get(f"{base(ledger)}/{newer['id']}").json()
    assert expired["status"] == "expired" and all(
        row["raw"] is None for row in expired["rows"]
    )
    assert preview(ledger, expired).status_code == 409


def test_workspace_membership_csrf_and_entity_ownership(ledger):
    uploaded = upload(ledger).json()
    other = (
        ledger["client"]
        .post(
            "/api/v1/workspaces",
            headers=ledger["headers"],
            json={"name": "另一个空间", "type": "personal"},
        )
        .json()
    )
    assert (
        ledger["client"]
        .get(f"/api/v1/workspaces/{other['id']}/imports/{uploaded['id']}")
        .status_code
        == 404
    )
    other_account = (
        ledger["client"]
        .post(
            f"/api/v1/workspaces/{other['id']}/finance/accounts",
            headers=ledger["headers"],
            json={"name": "另一账户"},
        )
        .json()
    )
    assert preview(ledger, uploaded, account_id=other_account["id"]).status_code == 404
    assert (
        preview(ledger, uploaded, category_map={"工资": str(uuid4())}).status_code
        == 404
    )
    assert (
        upload(
            ledger,
            headers={
                "Content-Type": "application/octet-stream",
                "X-File-Name": "x.csv",
            },
        ).status_code
        == 403
    )
    owner_cookie = ledger["client"].cookies.get("finscope_session")
    ledger["client"].cookies.clear()
    assert ledger["client"].get(base(ledger)).status_code == 401
    auth = (
        ledger["client"]
        .post(
            "/api/v1/auth/register",
            json={
                "email": "import-viewer@example.com",
                "password": "StrongPassword123!",
                "display_name": "只读导入",
            },
        )
        .json()
    )
    viewer_cookie = ledger["client"].cookies.get("finscope_session")
    assert ledger["client"].get(base(ledger)).status_code == 404
    ledger["client"].cookies.set("finscope_session", owner_cookie)
    assert (
        ledger["client"]
        .post(
            f"/api/v1/workspaces/{ledger['space']['id']}/members",
            headers=ledger["headers"],
            json={"email": "import-viewer@example.com", "role": "viewer"},
        )
        .status_code
        == 201
    )
    ledger["client"].cookies.set("finscope_session", viewer_cookie)
    assert ledger["client"].get(base(ledger)).status_code == 200
    viewer_data = {**ledger, "headers": {"X-CSRF-Token": auth["csrf_token"]}}
    assert upload(viewer_data).status_code == 403
    assert preview(viewer_data, uploaded).status_code == 403


def test_business_cost_import_uses_existing_profit_and_cash_rules(business):
    data = (
        "日期,收支,金额,分类\n2026-10-01,收入,100.00,销售\n2026-10-01,支出,40.00,采购\n"
    )
    uploaded = upload(business, data).json()
    value = preview(
        business,
        uploaded,
        merchant_column=None,
        description_column=None,
        external_id_column=None,
        currency_column=None,
        category_map={
            "销售": business["categories"]["revenue"],
            "采购": business["categories"]["cost"],
        },
    ).json()
    assert confirm(business, value).status_code == 200
    result = report(business)
    assert (
        result["revenue"],
        result["cost"],
        result["profit"],
        result["net_cash_flow"],
    ) == ("100.00", "40.00", "60.00", "60.00")


def test_xlsx_preserves_raw_numeric_xml_and_rejects_external_links():
    data = xlsx([["日期", "金额"], ["2026-10-01", 1]])
    output = io.BytesIO()
    with ZipFile(io.BytesIO(data)) as original, ZipFile(output, "w") as target:
        for entry in original.infolist():
            content = original.read(entry.filename)
            if entry.filename == "xl/worksheets/sheet1.xml":
                content = content.replace(b"<v>1</v>", b"<v>9999999999999999.99</v>")
            target.writestr(entry, content)
    assert (
        parse_file("exact.xlsx", output.getvalue()).rows[0][1][1]
        == "9999999999999999.99"
    )
    long_title = io.BytesIO()
    with ZipFile(io.BytesIO(data)) as original, ZipFile(long_title, "w") as target:
        for entry in original.infolist():
            content = original.read(entry.filename)
            if entry.filename == "xl/workbook.xml":
                content = content.replace(b'name="Sheet"', b'name="' + b"A" * 81 + b'"')
            target.writestr(entry, content)
    with pytest.raises(HTTPException) as exc:
        parse_file("long-title.xlsx", long_title.getvalue())
    assert exc.value.status_code == 422
    with ZipFile(output, "a") as target:
        target.writestr("xl/externalLinks/externalLink1.xml", "<x/>")
    with pytest.raises(HTTPException) as exc:
        parse_file("external.xlsx", output.getvalue())
    assert exc.value.status_code == 422
