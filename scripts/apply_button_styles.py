"""Codemod: أضف style= لأزرار InlineKeyboard حسب مخطط الألوان المعتمد.

يُشغَّل يدوياً:  python scripts/apply_button_styles.py
لا يلمس أزرار التنقّل (رجوع/صفحات/القائمة الرئيسية) فتبقى بالنمط الافتراضي.

جداول التصنيف مستوردة من keyboards/style_utils.py (المصدر الوحيد).
"""

from __future__ import annotations

import pathlib
import sys

import libcst as cst
import libcst.matchers as m

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
KEYBOARDS = ROOT / "keyboards"

from keyboards.style_utils import classify  # noqa: E402


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


class StyleAuditor(cst.CSTTransformer):
    """يفحص ألوان الأزرار الحالية ويبلّغ عن أي لون لا يطابق التصنيف.

    يلتقط حالتين:
    - زر ملوّن بلون يخالف التصنيف (مثل زر «preset» المصبوغ أحمر).
    - زر بلا لون بينما يستحق لوناً (يفوته الـ codemod لسبب ما).
    """

    def __init__(self) -> None:
        self.mismatches: list[tuple[str, str, str]] = []

    def _record(self, callback: str, current: str, expected: str | None) -> None:
        if expected is None and current:
            return  # الأزرار الملونة يدوياً بلا قاعدة تُترك كما هي.
        if current != (expected or ""):
            self.mismatches.append((callback, current or "-", expected or "-"))

    def leave_Call(self, original: cst.Call, updated: cst.Call) -> cst.Call:
        func = updated.func
        is_button = (
            m.matches(func, m.Attribute(attr=m.Name("button")))
            or m.matches(func, m.Name("InlineKeyboardButton"))
        )
        if not is_button:
            return updated

        kwargs = {arg.keyword.value: arg for arg in updated.args if arg.keyword is not None}
        cb_arg = kwargs.get("callback_data")
        if cb_arg is None:
            return updated

        callback = _literal_callback(cb_arg.value)
        if callback is None:
            return updated

        text_arg = kwargs.get("text")
        text_hint = _literal_callback(text_arg.value) if text_arg is not None else ""
        expected = classify(callback, text_hint or "")

        style_arg = kwargs.get("style")
        current = ""
        if style_arg is not None:
            current = _literal_callback(style_arg.value) or ""
        self._record(callback, current, expected)
        return updated


def audit() -> int:
    """يعيد عدد الأزرار التي لا يطابق لونها التصنيف (0 = لا ملاحظات)."""
    total = 0
    for path in sorted(KEYBOARDS.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = cst.parse_module(source)
        auditor = StyleAuditor()
        tree.visit(auditor)
        if auditor.mismatches:
            total += len(auditor.mismatches)
            print(f"{path.name}:")
            for callback, current, expected in auditor.mismatches:
                print(f"  {callback!r}: الحالي={current} المتوقع={expected}")
    print(f"total mismatches: {total}")
    return total


def main() -> None:
    if "--check" in sys.argv:
        raise SystemExit(1 if audit() else 0)

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
