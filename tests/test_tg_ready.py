"""الجلسات الجاهزة: فرز الملف تلقائياً + السعر + نقصان المخزون."""

from __future__ import annotations

import time
from decimal import Decimal

from database.engine import async_session_maker
from services.tg_ready_service import (
    build_account_zip,
    calc_sell_price,
    detect_country,
    dl_cloude_base,
    extract_code_link,
    extract_file_link,
    extract_login_link,
    extract_zip_files,
    parse_text_entries,
    parse_uploaded_file,
    save_account_files,
    TgReadyService,
)


def test_detect_country_flag_and_name():
    assert detect_country("+14155550123") == ("1", "أمريكا/كندا", "🇺🇸")
    assert detect_country("+963944000000") == ("963", "سوريا", "🇸🇾")
    assert detect_country("+966500000000") == ("966", "السعودية", "🇸🇦")
    assert detect_country("+971500000000")[2] == "🇦🇪"
    assert detect_country("123")[0] == "unknown"


def test_calc_sell_price_margin():
    assert calc_sell_price(Decimal("0.40"), Decimal("50")) == Decimal("0.6000")
    assert calc_sell_price(Decimal("1"), Decimal("0")) == Decimal("1.0000")
    assert calc_sell_price(Decimal("0"), Decimal("50")) == Decimal("0")


def test_parse_text_entries_dedupes():
    entries = parse_text_entries(
        "+14155550123|session-aaa|pass1\n"
        "+14155550123|session-aaa|pass1\n"
        "+963944000001\n"
        "not-a-number\n"
    )
    assert [e.phone for e in entries] == ["+14155550123", "+963944000001"]
    assert "session-aaa" in entries[0].payload


def test_parse_zip_by_filename():
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("+14155550123/session.json", "{}")
        zf.writestr("+963944000002/tdata.bin", "x")
    entries = parse_uploaded_file("batch.zip", buf.getvalue())
    phones = sorted(e.phone for e in entries)
    assert phones == ["+14155550123", "+963944000002"]


def test_extract_zip_files_groups_blobs_per_phone(tmp_path, monkeypatch):
    import io
    import zipfile

    import services.tg_ready_service as mod

    monkeypatch.setattr(mod, "READY_FILES_ROOT", tmp_path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("+14155550123/tdata/D15", "tdata-bytes")
        zf.writestr("+14155550123/session.json", '{"phone": "+14155550123"}')
    files_map = extract_zip_files(buf.getvalue())
    assert set(files_map) == {"+14155550123"}
    assert len(files_map["+14155550123"]) == 2

    saved = save_account_files(99, "+14155550123", files_map["+14155550123"])
    assert len(saved) == 2
    built = build_account_zip("+14155550123", saved)
    assert built is not None
    fname, blob = built
    assert fname == "telegram_session_14155550123.zip"
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = zf.namelist()
    assert len(names) == 2
    assert all(n.startswith("14155550123/") for n in names)


def test_extract_login_link():
    assert (
        extract_login_link("+1415|https://datamoll.com/order/abc login")
        == "https://datamoll.com/order/abc"
    )
    assert extract_login_link("+14155550123|just text") is None


def test_supplier_line_file_phone_code():
    line = (
        "https://dl-cloude.org/files/C1YlaNBn10woHlYTIT2_gg97SkvRbil0NSM6b9EPjFU"
        "|+639557095073"
        "|https://dl-cloude.org/c/W_TvrreSHMs7LmAXXa82c1Nx7fykXfPY"
    )
    entries = parse_text_entries(line)
    assert [e.phone for e in entries] == ["+639557095073"]
    assert entries[0].payload == line[:2000]
    assert extract_file_link(line) == (
        "https://dl-cloude.org/files/C1YlaNBn10woHlYTIT2_gg97SkvRbil0NSM6b9EPjFU"
    )
    assert extract_code_link(line) == "https://dl-cloude.org/c/W_TvrreSHMs7LmAXXa82c1Nx7fykXfPY"
    base, cid = dl_cloude_base(extract_code_link(line))
    assert base == "https://dl-cloude.org/c/W_TvrreSHMs7LmAXXa82c1Nx7fykXfPY"
    assert cid == "W_TvrreSHMs7LmAXXa82c1Nx7fykXfPY"


async def test_fetch_dl_cloude_code_delivered(monkeypatch):
    import services.tg_ready_service as mod

    async def fake_fetch_json(url, timeout_s=20, method="GET"):
        if url.endswith("/code") and method == "POST":
            return {"status": "delivered", "code": "47291"}
        if url.endswith("/twofa"):
            return {"password": "secret123"}
        return None

    monkeypatch.setattr(mod, "_fetch_json", fake_fetch_json)
    res = await mod.fetch_code_for_payload(
        "https://dl-cloude.org/files/abc|+639557095073|https://dl-cloude.org/c/XYZ123"
    )
    assert res["ok"] is True
    assert res["codes"] == ["47291"]
    assert res["twofa_remote"] == "secret123"


async def test_fetch_dl_cloude_code_pending(monkeypatch):
    import services.tg_ready_service as mod

    async def fake_fetch_json(url, timeout_s=20, method="GET"):
        if url.endswith("/code") and method == "POST":
            return {"status": "pending"}
        return None

    monkeypatch.setattr(mod, "_fetch_json", fake_fetch_json)
    res = await mod.fetch_code_for_payload(
        "https://dl-cloude.org/files/abc|+639557095073|https://dl-cloude.org/c/XYZ123"
    )
    assert res["ok"] is False
    assert res["error"] == "no_code_yet"


async def test_import_groups_countries_and_buy_decrements_stock():
    from sqlalchemy import delete

    from database.models import TgReadyCountry, TgReadyItem

    suffix = str(int(time.time() * 1000))[-8:]
    entries = parse_text_entries(
        f"+1415555{suffix}|sess1\n+1415556{suffix}|sess2\n+963944{suffix}|sess3\n"
    )
    assert len(entries) == 3
    async with async_session_maker() as session:
        result = await TgReadyService.import_entries(
            session, entries, Decimal("0.40"), Decimal("50"), file_name="test.txt"
        )
    assert result["added"] == 3
    assert result["sell"] == Decimal("0.6000")
    assert set(result["countries"]) == {"1", "963"}

    async with async_session_maker() as session:
        overview = await TgReadyService.stock_overview(session)
        usa = next(c for c in overview if c["key"] == "1")
        assert usa["flag"] == "🇺🇸"
        assert usa["price"] == Decimal("0.6000")
        before = usa["stock"]
        assert before >= 2

        item, price = await TgReadyService.buy_one(session, 1, "1")
        assert item is not None and price == Decimal("0.6000")
        after_overview = await TgReadyService.stock_overview(session)
        usa_after = next(c for c in after_overview if c["key"] == "1")
        assert usa_after["stock"] == before - 1

        # تنظيف عناصر الاختبار فقط
        phones = [e.phone for e in entries]
        await session.execute(delete(TgReadyItem).where(TgReadyItem.phone_number.in_(phones)))
        # أبقِ سجل الدولة (يُعاد استخدامه) لكن أعد سعره الافتراضي إن لزم
        await session.commit()
    assert True
