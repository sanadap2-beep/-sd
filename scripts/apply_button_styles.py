"""Codemod: أضف style= لأزرار InlineKeyboard حسب مخطط الألوان المعتمد.

يُشغَّل يدوياً:  python scripts/apply_button_styles.py
لا يلمس أزرار التنقّل (رجوع/صفحات/القائمة الرئيسية) فتبقى بالنمط الافتراضي.
"""

from __future__ import annotations

import pathlib
import re

import libcst as cst
import libcst.matchers as m

ROOT = pathlib.Path(__file__).resolve().parent.parent
KEYBOARDS = ROOT / "keyboards"

SUCCESS = "success"
PRIMARY = "primary"
DANGER = "danger"

# ── تصنيف حسب callback_data (يُطابَق كـ prefix/substring) ──
NAV_EXACT = {
    "back_to_main",
    "noop",
    "ignore",
    "close",
    "store:home",
}
NAV_PATTERNS = (
    r"^back",
    r":back$",
    r":back:",
    r"^page:",
    r":page:",
    r"_page:",
    r"^nav:",
    r"page",
)

DANGER_PATTERNS = (
    r"terms",
    r"cancel",
    r"delete",
    r"del:",
    r"_del",
    r"remove",
    r"revoke",
    r"void",
    r"ban",
    r"reject",
    r"refund",
    r"reset",
    r"maintenance_on",
    r"warn",
)

PRIMARY_PATTERNS = (
    r"account",
    r"balance",
    r"deposit",
    r"transfer",
    r"points",
    r"loyalty",
    r"orders",
    r"order",
    r"my_",
    r"wallet",
    r"pay",
    r"coupon",
    r"gift",
    r"redeem",
    r"promo",
    r"referral",
    r"agent",
    r"fund",
    r"invoice",
    r"rates",
    r"margin",
    r"stats",
    r"cart",
    r"checkout",
    r"buy",
    r"confirm",
)

SUCCESS_PATTERNS = (
    r"^store",
    r"^shop",
    r"^cat",
    r"^prod",
    r"^subcat",
    r"^num",
    r"^svc",
    r"^service",
    r"^games",
    r"^smm",
    r"^market",
    r"^extras",
    r"^menu:search",
    r"add$",
    r"_add",
    r":add",
    r"create",
    r"enable",
    r"^admin:main$",
)


NAV_TEXT_MARKERS = ("🔙", "🏠", "◀", "▶", "⬅", "➡", "⏮", "⏭", "«", "»", "رجوع", "عودة", "السابق", "التالي", "back", "next", "prev", "home")


def is_nav_text(text: str) -> bool:
    low = text.strip().lower()
    return any(marker in low for marker in NAV_TEXT_MARKERS)


def classify(callback: str, text_hint: str = "") -> str | None:
    if text_hint and is_nav_text(text_hint):
        return None
    cb = callback.strip().lower()
    if not cb:
        return None
    if cb in NAV_EXACT and cb != "store:home":
        return None
    for pat in NAV_PATTERNS:
        if re.search(pat, cb):
            return None
    for pat in DANGER_PATTERNS:
        if re.search(pat, cb):
            return DANGER
    for pat in PRIMARY_PATTERNS:
        if re.search(pat, cb):
            return PRIMARY
    for pat in SUCCESS_PATTERNS:
        if re.search(pat, cb):
            return SUCCESS
    return None


def _literal_callback(node: cst.BaseExpression) -> str | None:
    """يستخرج نصاً ثابتاً من callback_data (يدعم f-strings البسيطة)."""
    if isinstance(node, cst.SimpleString):
        return node.evaluated_value or ""
    if isinstance(node, cst.FormattedString):
        parts = []
        for piece in node.parts:
            if isinstance(piece, cst.FormattedStringText):
                parts.append(piece.value)
            else:
                parts.append("")
        return "".join(parts)
    if isinstance(node, cst.ConcatenatedString):
        left = _literal_callback(node.left) or ""
        right = _literal_callback(node.right) or ""
        return left + right
    return None


class StyleAdder(cst.CSTTransformer):
    def __init__(self) -> None:
        self.changed = 0

    def leave_Call(self, original: cst.Call, updated: cst.Call) -> cst.Call:
        func = updated.func
        is_button = (
            m.matches(func, m.Attribute(attr=m.Name("button")))
            or m.matches(func, m.Name("InlineKeyboardButton"))
        )
        if not is_button:
            return updated

        kwargs = {
            arg.keyword.value: arg for arg in updated.args if arg.keyword is not None
        }
        if "style" in kwargs:
            return updated
        cb_arg = kwargs.get("callback_data")
        if cb_arg is None:
            return updated  # url / web_app / pay → بدون style
        callback = _literal_callback(cb_arg.value)
        if callback is None:
            return updated
        text_arg = kwargs.get("text")
        text_hint = _literal_callback(text_arg.value) if text_arg is not None else ""
        style = classify(callback, text_hint or "")
        if style is None:
            return updated

        self.changed += 1
        new_args = list(updated.args)
        index = new_args.index(cb_arg)
        # نضع style مباشرة بعد callback_data ونرث نفس تنسيق فاصلتها
        # (يحافظ على المسافات البادئة في الاستدعاءات متعددة الأسطر).
        comma = cb_arg.comma
        if comma is cst.MaybeSentinel.DEFAULT:
            comma = cst.MaybeSentinel.DEFAULT
        new_args[index] = cb_arg.with_changes(
            comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" "))
        )
        style_arg = cst.Arg(
            keyword=cst.Name("style"),
            value=cst.SimpleString(f'"{style}"'),
            equal=cst.AssignEqual(
                whitespace_before=cst.SimpleWhitespace(""),
                whitespace_after=cst.SimpleWhitespace(""),
            ),
            comma=comma,
        )
        new_args.insert(index + 1, style_arg)
        return updated.with_changes(args=new_args)


def main() -> None:
    total = 0
    for path in sorted(KEYBOARDS.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = cst.parse_module(source)
        transformer = StyleAdder()
        new_tree = tree.visit(transformer)
        if transformer.changed:
            path.write_text(new_tree.code, encoding="utf-8")
            total += transformer.changed
            print(f"{path.name}: {transformer.changed} buttons styled")
    print(f"total: {total}")


if __name__ == "__main__":
    main()
