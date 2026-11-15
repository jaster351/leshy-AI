"""All RealtimeChatManager._build_context wrappers must preserve session_id.

Regression for sequential TypeError from marketplace/freshness layers in 1.10.154.
This test audits every assignment anywhere in src/leshy, including future ones.
"""
from __future__ import annotations

import ast
from pathlib import Path
import unittest


class ContextSessionKeywordContractTests(unittest.TestCase):
    def test_all_installed_context_wrappers_accept_and_forward_session_id(self):
        root = Path(__file__).resolve().parents[1] / "src" / "leshy"
        wrappers = []

        for path in root.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            functions = {
                node.name: node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            for assign in ast.walk(tree):
                if not isinstance(assign, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = assign.targets if isinstance(assign, ast.Assign) else [assign.target]
                if not any(
                    isinstance(target, ast.Attribute)
                    and target.attr == "_build_context"
                    for target in targets
                ):
                    continue
                value = assign.value
                if not isinstance(value, ast.Name):
                    continue
                wrapper = functions.get(value.id)
                if wrapper is None:
                    continue
                wrappers.append((path.name, value.id))

                arguments = {
                    arg.arg for arg in
                    (*wrapper.args.posonlyargs, *wrapper.args.args, *wrapper.args.kwonlyargs)
                }
                self.assertTrue(
                    "session_id" in arguments or wrapper.args.kwarg is not None,
                    f"{path.name}::{value.id} must accept session_id keyword",
                )
                delegates = [
                    call for call in ast.walk(wrapper)
                    if isinstance(call, ast.Call) and
                    (isinstance(call.func, ast.Name)
                     and (call.func.id.startswith("_ORIGINAL_BUILD_CONTEXT")
                          or call.func.id == "original"))
                ]
                self.assertTrue(delegates, f"{path.name}::{value.id} missing delegate")
                self.assertTrue(
                    any(
                        any(k.arg == "session_id" for k in call.keywords)
                        or any(k.arg is None for k in call.keywords)
                        for call in delegates
                    ),
                    f"{path.name}::{value.id} must forward session_id to the delegate",
                )

        self.assertGreaterEqual(len(wrappers), 5, wrappers)
        names = {file for file, _ in wrappers}
        self.assertTrue({
            "freshness_cache_runtime.py",
            "freshness_memory_runtime.py",
            "reliable_web_runtime.py",
            "runtime_latency_fix.py",
            "marketplace_chat.py",
        }.issubset(names), names)


if __name__ == "__main__":
    unittest.main()
