#!/usr/bin/env python3
"""Audit untranslated Arabic user-facing literals.

By default this counts Arabic strings that are likely visible to normal users in
critical handlers. It ignores comments/docstrings, admin-only notifications, and
logs. Use --strict to count every Arabic string constant in the files.
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

TARGETS = [
    "handlers/numbers.py",
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
IGNORED_CALLS = {"notify_admin", "notify_admin_photo", "logger", "debug", "info", "warning", "error", "exception"}


def has_arabic(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06ff" for ch in text)


def parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    out = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            out[child] = parent
    return out


def is_docstring(node: ast.Constant, parents: dict[ast.AST, ast.AST]) -> bool:
    parent = parents.get(node)
    grand = parents.get(parent) if parent else None
    return isinstance(parent, ast.Expr) and isinstance(grand, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))


def call_name(call: ast.Call) -> str:
    fn = call.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    if isinstance(fn, ast.Name):
        return fn.id
    return ""


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="count all Arabic string constants")
    args = parser.parse_args()
    total = 0
    for path in TARGETS:
        file_path = Path(path)
        if not file_path.exists():
            continue
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
        parents = parent_map(tree)
        count = 0
        samples = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str) and has_arabic(node.value)):
                continue
            if is_docstring(node, parents):
                continue
            if not args.strict and not is_user_visible(node, parents):
                continue
            count += 1
            if len(samples) < 3:
                samples.append((getattr(node, "lineno", 0), node.value.replace("\n", "\\n")[:90]))
        total += count
        print(f"{path}: {count} Arabic user-facing literals" + (" (strict)" if args.strict else ""))
        for line, sample in samples:
            print(f"  line {line}: {sample}")
    print(f"TOTAL: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
