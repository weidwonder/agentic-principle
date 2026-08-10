#!/usr/bin/env python3
"""scan_agent.py 的用例。跑法：python3 scripts/test_scan_agent.py

注意：本文件中形如 `sk-abcdef...`、`ghp_aaaa...` 的字符串是**检测规则的测试夹具，
不是真实凭据**——它们的存在正是为了验证 SEC001 规则能识别并脱敏这类模式。
改动 scan_agent.py 的规则或维度后必须重跑本文件。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import scan_agent  # noqa: E402


def build(files: dict) -> Path:
    root = Path(tempfile.mkdtemp())
    for name, content in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def ids(report) -> set:
    return {f["id"] for f in report["findings"]}


def gaps(report) -> set:
    return {g["dimension"] for g in report["evidence_gaps"]}


class TestRules(unittest.TestCase):
    def test_credential_is_p0_and_redacted(self):
        root = build({"cfg.py": 'KEY = "sk-abcdefghijklmnopqrstuvwxyz012345"\n'})
        r = scan_agent.scan(root)
        self.assertIn("SEC001", ids(r))
        hit = next(f for f in r["findings"] if f["id"] == "SEC001")
        self.assertEqual(hit["severity"], "P0")
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", hit["snippet"])
        self.assertIn("REDACTED", hit["snippet"])

    def test_secret_in_log_redacted(self):
        root = build({"a.py": 'logger.info("api_key=%s", api_key_value_here)\n'})
        r = scan_agent.scan(root)
        self.assertIn("SEC002", ids(r))

    def test_fail_open_catch_branch(self):
        root = build({"guard.py": "try:\n    check(x)\nexcept Exception:\n    return content\n"})
        self.assertIn("SAFE001", ids(scan_agent.scan(root)))

    def test_dangerous_execution(self):
        root = build({
            "a.py": "subprocess.run(cmd, shell=True)\n",
            "b.py": "eval(user_input)\n",
            "c.sh": "rm -rf /tmp/x\n",
            "d.py": "requests.get(url, verify=False)\n",
        })
        found = ids(scan_agent.scan(root))
        for rule_id in ("EXEC001", "EXEC002", "EXEC003", "NET001"):
            self.assertIn(rule_id, found)

    def test_eval_not_matched_as_attribute(self):
        """model.evaluate() 与 self.eval() 不应命中 EXEC002。"""
        root = build({"a.py": "model.eval()\nresult = self.eval(x)\n"})
        self.assertNotIn("EXEC002", ids(scan_agent.scan(root)))

    def test_unbounded_loop_is_p2(self):
        root = build({"loop.py": "while True:\n    step()\n"})
        r = scan_agent.scan(root)
        self.assertIn("LOOP001", ids(r))
        self.assertEqual(next(f for f in r["findings"] if f["id"] == "LOOP001")["severity"], "P2")

    def test_cache_busting_prompt(self):
        root = build({"p.py": 'system_prompt = f"You are... {datetime.now()}"\n'})
        self.assertIn("CACHE001", ids(scan_agent.scan(root)))

    def test_findings_sorted_p0_first(self):
        root = build({
            "z.py": "while True:\n    pass\n",
            "a.py": 'K = "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n',
        })
        sev = [f["severity"] for f in scan_agent.scan(root)["findings"]]
        self.assertEqual(sev[0], "P0")


class TestEvidenceGaps(unittest.TestCase):
    def test_empty_repo_reports_gaps_with_patterns(self):
        root = build({"readme.md": "hello\n"})
        r = scan_agent.scan(root)
        self.assertIn("D6 执行边界", gaps(r))
        for g in r["evidence_gaps"]:
            self.assertTrue(g["pattern"], "零命中必须附带所用匹配模式")

    def test_signal_present_removes_gap(self):
        root = build({"loop.py": "MAX_STEPS = 20\nstop_reason = None\n"})
        self.assertNotIn("D6 执行边界", gaps(scan_agent.scan(root)))

    def test_stateless_agent_does_not_report_memory_gap(self):
        """无检索设施时，D8 两项都不该报缺口。"""
        root = build({"a.py": "print(1)\n"})
        g = gaps(scan_agent.scan(root))
        self.assertNotIn("D8 记忆与检索", g)
        self.assertNotIn("D8 租户隔离", g)

    def test_rag_without_tenant_filter_reports_gap(self):
        root = build({"r.py": "index = vector_store.search(q)\n"})
        self.assertIn("D8 租户隔离", gaps(scan_agent.scan(root)))

    def test_rag_with_tenant_filter_no_gap(self):
        root = build({"r.py": "index = vector_store.search(q, tenant_id=ctx.tenant_id)\n"})
        self.assertNotIn("D8 租户隔离", gaps(scan_agent.scan(root)))


class TestPromptLength(unittest.TestCase):
    def test_oversized_prompt_file_flagged(self):
        root = build({"prompts/system_prompt.md": "你" * 10001})
        r = scan_agent.scan(root)
        self.assertEqual(len(r["oversized_prompt_files"]), 1)
        self.assertGreater(r["oversized_prompt_files"][0]["chars"], scan_agent.PROMPT_CHAR_LIMIT)

    def test_short_prompt_not_flagged(self):
        root = build({"prompts/system_prompt.md": "短提示词"})
        self.assertEqual(scan_agent.scan(root)["oversized_prompt_files"], [])

    def test_non_prompt_long_file_not_flagged(self):
        root = build({"data/dump.txt": "x" * 20000})
        self.assertEqual(scan_agent.scan(root)["oversized_prompt_files"], [])


class TestScanHygiene(unittest.TestCase):
    def test_skips_noise_dirs(self):
        root = build({
            "node_modules/pkg/a.js": "eval(x)\n",
            ".venv/lib/b.py": "eval(x)\n",
            "src/c.py": "print(1)\n",
        })
        r = scan_agent.scan(root)
        self.assertEqual(r["files_scanned"], 1)
        self.assertNotIn("EXEC002", ids(r))

    def test_truncation_is_reported_not_silent(self):
        root = build({f"f{i}.py": "print(1)\n" for i in range(5)})
        r = scan_agent.scan(root, max_files=2)
        self.assertTrue(r["hit_files_truncated"])
        self.assertIn("扫描不完整", scan_agent.to_markdown(r))

    def test_oversized_file_skip_is_reported_not_silent(self):
        """超过 --max-bytes 的文件被跳过时必须出现在报告里，否则会造出假的证据缺口。"""
        root = build({"big.py": "x" * 5000, "small.py": "print(1)\n"})
        r = scan_agent.scan(root, max_bytes=1000)
        self.assertIn("big.py", r["files_skipped_oversized"])
        md = scan_agent.to_markdown(r)
        self.assertIn("big.py", md)
        self.assertIn("证据缺口", md)
        self.assertIn("未参与任何判定", md)

    def test_no_oversized_notice_when_nothing_skipped(self):
        root = build({"a.py": "print(1)\n"})
        r = scan_agent.scan(root)
        self.assertEqual(r["files_skipped_oversized"], [])
        self.assertNotIn("未参与任何判定", scan_agent.to_markdown(r))

    def test_markdown_always_carries_disclaimer(self):
        md = scan_agent.to_markdown(scan_agent.scan(build({"a.py": "print(1)\n"})))
        self.assertIn("线索，不是结论", md)
        self.assertIn("零命中不等于没做", md)


class TestExampleGrouping(unittest.TestCase):
    def test_doc_and_test_hits_are_marked(self):
        root = build({
            "src/run.py": "subprocess.run(cmd, shell=True)\n",
            "docs/security.md": "avoid shell=True in production\n",
            "tests/test_x.py": "subprocess.run(cmd, shell=True)\n",
        })
        r = scan_agent.scan(root)
        by_file = {f["file"]: f["likely_example"] for f in r["findings"] if f["id"] == "EXEC001"}
        self.assertFalse(by_file["src/run.py"])
        self.assertTrue(by_file["docs/security.md"])
        self.assertTrue(by_file["tests/test_x.py"])

    def test_markdown_separates_the_two_groups(self):
        root = build({
            "src/run.py": "subprocess.run(cmd, shell=True)\n",
            "docs/security.md": "avoid shell=True\n",
        })
        md = scan_agent.to_markdown(scan_agent.scan(root))
        self.assertIn("风险线索（实现代码）", md)
        self.assertIn("风险线索（文档 / 测试 / 夹具）", md)

    def test_no_example_section_when_all_hits_are_implementation(self):
        root = build({"src/run.py": "subprocess.run(cmd, shell=True)\n"})
        md = scan_agent.to_markdown(scan_agent.scan(root))
        self.assertNotIn("风险线索（文档 / 测试 / 夹具）", md)


if __name__ == "__main__":
    unittest.main(verbosity=2)
