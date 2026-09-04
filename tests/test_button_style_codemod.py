""":اختبارات codemod ألوان الأزرار.

الهدف: ألا يلتقط نمط قصير كلمة أطول تحتويه. الحادثة الأصلية: نمط
``reset`` (حذف) طابق كلمة ``preset`` (قالب إنشاء) فصُبغت أزرار إنشاء
المزودين بالأحمر بدل الأخضر.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

libcst = pytest.importorskip("libcst", reason="libcst غير مثبّت (أداة تطوير)")

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "apply_button_styles.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("apply_button_styles", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preset_is_not_classified_as_reset():
    """:«preset» = قالب إنشاء (أخضر) ولا يجوز أن يطابقه نمط «reset»."""
    module = _load_module()

    assert module.classify("admin:aprov_custom_preset:hyper_store") == "success"
    assert module.classify("admin:aprov_custom_preset:ggsoma") == "success"
    assert module.classify("mb:presets") == "success"
    assert module.classify("mb:preset:support") == "success"


def test_real_resets_and_cancels_stay_dangerous():
    """:«reset» الحقيقي يبقى أحمر — الإصلاح لا يوسّع الاستثناء بلا حدود."""
    module = _load_module()

    assert module.classify("mb:reset") == "danger"
    assert module.classify("admin:reset_bonus") == "danger"
    assert module.classify("admin:order_refund:5") == "danger"
    assert module.classify("num_cancel:12") == "danger"
    assert module.classify("admin:user_ban:9") == "danger"


def test_separator_prefixed_words_still_match():
    """:الفواصل الحقيقية (``:`` و``_``) لا تمنع المطابقة."""
    module = _load_module()

    assert module.classify("admin:user_add_balance:3") == "primary"
    assert module.classify("admin:deposit_reject:7") == "danger"
    assert module.classify("admin:agent_revoke:2") == "danger"


def test_audit_reports_no_mismatch_in_keyboards():
    """:كل أزرار مجلد keyboards مطابقة لتصنيفها — لا انحراف صامت."""
    module = _load_module()

    assert module.audit() == 0


def test_provider_preset_buttons_are_green():
    from keyboards.admin_providers_v2 import custom_provider_presets_kb

    buttons = {
        button.callback_data: button
        for row in custom_provider_presets_kb().inline_keyboard
        for button in row
    }
    for callback in (
        "admin:aprov_custom_preset:tlbkenne",
        "admin:aprov_custom_preset:ggsoma",
        "admin:aprov_custom_preset:hyper_store",
        "admin:aprov_custom_preset:store_rest",
        "admin:aprov_custom_preset:simple_json",
        "admin:aprov_custom_preset:query_key",
    ):
        assert buttons[callback].style == "success"
    # زر الحذف/الرجوع لا يتأثر
    assert buttons["admin:aprov_custom_wizard"].style is None


def test_main_button_presets_are_green():
    from keyboards.admin_main_buttons import main_button_presets_kb, main_buttons_kb

    presets = main_button_presets_kb().inline_keyboard
    assert presets
    for row in presets[:-1]:  # آخر صف زر الرجوع
        assert row[0].callback_data.startswith("mb:preset:")
        assert row[0].style == "success"

    menu = {
        button.callback_data: button
        for row in main_buttons_kb([]).inline_keyboard
        for button in row
    }
    assert menu["mb:presets"].style == "success"
    assert menu["mb:reset"].style == "danger"
    assert menu["mb:add"].style == "success"
