#!/usr/bin/env python3
"""Extract remaining user-visible Arabic string constants to locales.

This is a one-shot maintainer utility used during the sale-readiness cleanup.
It keeps admin/internal strings untouched and targets only calls that send text to
normal users.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    "handlers/marketplace.py",
    "handlers/games.py",
    "handlers/deposit.py",
    "handlers/account.py",
]
USER_CALLS = {
    "answer",
    "edit_text",
    "answer_photo",
    "send_message",
    "notify_user",
    "notify_insufficient_balance",
    "notify_order_completed",
    "notify_order_failed",
    "notify_deposit_approved",
    "notify_deposit_rejected",
}
IGNORED_CALLS = {"notify_admin", "notify_admin_photo", "debug", "info", "warning", "error", "exception"}


def has_arabic(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06ff" for ch in text)


def call_name(call: ast.Call) -> str:
    fn = call.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    if isinstance(fn, ast.Name):
        return fn.id
    return ""


def parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    out = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            out[child] = parent
    return out


def is_docstring(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    parent = parents.get(node)
    grand = parents.get(parent) if parent else None
    return isinstance(parent, ast.Expr) and isinstance(grand, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))


def is_user_visible(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, ast.Call):
            name = call_name(current)
            if name in IGNORED_CALLS:
                return False
            if name in USER_CALLS:
                return True
    return False


def key_for(path: str, lineno: int, index: int) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", path.replace("handlers/", "").replace(".py", "").lower()).strip("_")
    return f"ux_{base}_{lineno}_{index}"


class Extractor(ast.NodeTransformer):
    def __init__(self, path: str, parents: dict[ast.AST, ast.AST], ar: dict, en: dict):
        self.path = path
        self.parents = parents
        self.ar = ar
        self.en = en
        self.index = 0

    def i18n_call(self, text: str, lineno: int) -> ast.Call:
        self.index += 1
        key = key_for(self.path, lineno, self.index)
        self.ar.setdefault(key, text)
        # Fallback English uses the same source text for now if no translator was supplied.
        # The important part is that UI copy is no longer hardcoded and can be edited centrally.
        self.en.setdefault(key, text)
        return ast.Call(
            func=ast.Attribute(value=ast.Name(id="I18nService", ctx=ast.Load()), attr="t", ctx=ast.Load()),
            args=[ast.Constant(key), ast.Call(func=ast.Name(id="_auto_lang", ctx=ast.Load()), args=[ast.Call(func=ast.Name(id="locals", ctx=ast.Load()), args=[], keywords=[])], keywords=[])],
            keywords=[],
        )

    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, str) and has_arabic(node.value) and is_user_visible(node, self.parents) and not is_docstring(node, self.parents):
            return ast.copy_location(self.i18n_call(node.value, getattr(node, "lineno", 0)), node)
        return node

    def visit_JoinedStr(self, node: ast.JoinedStr):
        if not is_user_visible(node, self.parents):
            return self.generic_visit(node)
        values = []
        changed = False
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str) and has_arabic(value.value):
                call = self.i18n_call(value.value, getattr(value, "lineno", getattr(node, "lineno", 0)))
                values.append(ast.copy_location(ast.FormattedValue(value=call, conversion=-1), value))
                changed = True
            else:
                values.append(self.visit(value))
        if changed:
            node.values = values
        return node


HELPER = '''\n\ndef _auto_lang(scope=None) -> str:\n    user = (scope or {}).get("db_user")\n    if user is None:\n        callback = (scope or {}).get("callback")\n        user = getattr(callback, "from_user", None)\n    return getattr(user, "language_code", "ar") or "ar"\n'''


def main() -> int:
    ar_path = ROOT / "locales/ar.json"
    en_path = ROOT / "locales/en.json"
    ar = json.loads(ar_path.read_text(encoding="utf-8"))
    en = json.loads(en_path.read_text(encoding="utf-8"))
    for rel in TARGETS:
        path = ROOT / rel
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        parents = parent_map(tree)
        tree = Extractor(rel, parents, ar, en).visit(tree)
        ast.fix_missing_locations(tree)
        new_source = ast.unparse(tree) + "\n"
        if "def _auto_lang" not in new_source:
            insert_after = new_source.find("router = Router")
            if insert_after != -1:
                line_end = new_source.find("\n", insert_after)
                new_source = new_source[: line_end + 1] + HELPER + new_source[line_end + 1 :]
            else:
                new_source += HELPER
        path.write_text(new_source, encoding="utf-8")
    ar_path.write_text(json.dumps(ar, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    en_path.write_text(json.dumps(en, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
