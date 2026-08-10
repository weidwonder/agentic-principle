#!/usr/bin/env python3
"""按 agentic-principle 的维度矩阵扫描 Agent 仓库，产出线索与证据缺口。

只产线索，不产结论。本脚本用的是关键词与正则启发式：
  - 命中不等于做对了（出现 `retry` 不代表退避正确）；
  - 零命中不等于没做（换个命名就漏），所以报告零命中时会一并打印所用的匹配模式。

因此它的输出不得直接写进评审报告。每一条都要人工读过对应代码、
按 references/modes/review-mode.md §四 补齐证据之后，才能升格为 finding。

用法：
    python3 scan_agent.py <目标路径> [--format markdown|json] [--max-files N] [--max-bytes N]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

SKIP_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vscode",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    ".venv", "venv", "env", "__pycache__", "node_modules",
    "build", "dist", "target", "vendor", "coverage", ".next", ".nuxt",
}

TEXT_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".kt",
    ".rb", ".php", ".cs", ".c", ".cc", ".cpp", ".h", ".hpp", ".swift",
    ".sh", ".bash", ".zsh", ".sql",
    ".md", ".mdx", ".txt", ".rst",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env", ".jinja", ".j2",
}

SPECIAL_NAMES = {
    "Dockerfile", "Makefile", "AGENTS.md", "CLAUDE.md", "SKILL.md", ".env",
}

# 疑似承载系统提示词的文件：用于 D2 的字符数硬判定
PROMPT_FILE_HINT = re.compile(
    r"(prompt|instruction|persona|system[_-]?msg|agents?\.md|claude\.md|skill\.md|"
    r"guideline|playbook|sop)",
    re.IGNORECASE,
)

PROMPT_CHAR_LIMIT = 10000

# 文档、测试、夹具里的命中多半是示例而非实现，单独分组，避免淹没真正的实现命中
EXAMPLE_PATH_HINT = re.compile(
    r"(^|/)(tests?|__tests__|fixtures?|examples?|evals?|samples?|docs?|specs?)/"
    r"|(^|/)test_[^/]*$|_test\.[a-z]+$|\.(md|mdx|rst|txt)$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# 证据缺口：按维度找"有没有出现过这类痕迹"
# ---------------------------------------------------------------------------

DIMENSION_SIGNALS: Dict[str, Dict[str, Any]] = {
    "D4 工具返回体量": {
        "pattern": re.compile(
            r"\b(max[_-]?(tokens|bytes|chars|results|rows|items)|truncat\w*|"
            r"page[_-]?size|per[_-]?page|cursor|offset|limit\b|summar(y|ize|ise)|"
            r"spill|write[_-]?to[_-]?file|save[_-]?result)\b",
            re.IGNORECASE,
        ),
        "means": "没有任何分页、上限、摘要或落盘的痕迹，工具返回可能不设上限地进上下文",
        "ref": "references/tools/tool-output.md",
    },
    "D5 授权与闸门": {
        "pattern": re.compile(
            r"\b(allow[_-]?list|deny[_-]?list|approval|authoriz\w*|authenticat\w*|"
            r"permission|access[_-]?control|human[_-]?in[_-]?the[_-]?loop|HITL|"
            r"least[_-]?privilege|sandbox|confirm[_-]?before)\b",
            re.IGNORECASE,
        ),
        "means": "没有授权、审批或沙箱的痕迹，高影响动作可能没有任何程序侧闸门",
        "ref": "references/safety/safety.md",
    },
    "D5 不可信输入标注": {
        "pattern": re.compile(
            r"\b(prompt[_-]?injection|untrusted|external[_-]?content|provenance|"
            r"instruction[_-]?vs[_-]?data|sanitiz\w*|不可信|待处理数据)\b",
            re.IGNORECASE,
        ),
        "means": "没有不可信内容标注或注入防护的痕迹",
        "ref": "references/safety/safety.md",
    },
    "D6 执行边界": {
        "pattern": re.compile(
            r"\b(max[_-]?(iterations|steps|turns|rounds|depth)|step[_-]?budget|"
            r"token[_-]?budget|cost[_-]?budget|recursion[_-]?limit|deadline|"
            r"time[_-]?limit|stop[_-]?reason|termination)\b",
            re.IGNORECASE,
        ),
        "means": "没有步数、时间、成本上限或终止原因的痕迹，loop 可能停不下来",
        "ref": "references/runtime/agent-loop.md",
    },
    "D7 恢复": {
        "pattern": re.compile(
            r"\b(backoff|jitter|circuit[_-]?breaker|idempoten\w*|retry|retries|"
            r"rollback|compensat\w*|reconcil\w*|watchdog|timeout)\b",
            re.IGNORECASE,
        ),
        "means": "没有重试、退避、幂等或回滚的痕迹",
        "ref": "references/runtime/reliability.md",
    },
    "D8 记忆与检索": {
        "pattern": re.compile(
            r"\b(long[_-]?term[_-]?memory|memory[_-]?store|vector[_-]?(store|db|database)|"
            r"embedding|RAG|retriev\w*|knowledge[_-]?base)\b",
            re.IGNORECASE,
        ),
        "means": "没有记忆或检索设施——若设计文档声称有，这本身就是矛盾",
        "ref": "references/runtime/memory.md",
        "gap_is_normal": True,  # 无状态 Agent 本就该零命中，不算缺口
    },
    "D8 租户隔离": {
        "pattern": re.compile(
            r"\b(tenant[_-]?id|tenant[_-]?filter|org[_-]?id|workspace[_-]?id|"
            r"row[_-]?level[_-]?security|\bRLS\b|namespace[_-]?filter|"
            r"scope[_-]?to[_-]?user)\b",
            re.IGNORECASE,
        ),
        "means": "没有租户或身份过滤的痕迹",
        "ref": "references/runtime/memory.md",
        "only_if": "D8 记忆与检索",  # 只在有检索设施时才算缺口
    },
    "D9 成本与可观测": {
        "pattern": re.compile(
            r"\b(prompt[_-]?tokens|completion[_-]?tokens|cached[_-]?tokens|"
            r"token[_-]?usage|cost[_-]?limit|budget|OpenTelemetry|trace[_-]?id|"
            r"span[_-]?id|telemetry|latency|p95|p99)\b",
            re.IGNORECASE,
        ),
        "means": "没有 token 计量、预算或 trace 的痕迹",
        "ref": "references/runtime/cost-and-cache.md",
    },
    "D10 多 Agent": {
        "pattern": re.compile(
            r"\b(sub[_-]?agent|subagent|worker[_-]?agent|orchestrat\w*|handoff|"
            r"delegate|spawn[_-]?agent|multi[_-]?agent)\b",
            re.IGNORECASE,
        ),
        "means": "没有子代理或编排的痕迹——若设计文档声称是多 Agent，这本身就是矛盾",
        "ref": "references/multi-agent/multi-agent.md",
        "gap_is_normal": True,
    },
    "D11 测试与评测": {
        "pattern": re.compile(
            r"\b(test[_-]?case|assert|expect\(|eval[_-]?set|evals?\b|benchmark|"
            r"rubric|golden|fixture|regression)\b",
            re.IGNORECASE,
        ),
        "means": "没有测试、评测集或断言的痕迹",
        "ref": "references/delivery/testing.md",
    },
}


# ---------------------------------------------------------------------------
# 风险线索：命中即值得人工去看
# ---------------------------------------------------------------------------

RULES: Tuple[Dict[str, Any], ...] = (
    {
        "id": "SEC001",
        "severity": "P0",
        "dimension": "D5",
        "pattern": re.compile(
            r"\b(sk-[A-Za-z0-9_\-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16}|"
            r"xox[baprs]-[A-Za-z0-9\-]{10,})\b"
        ),
        "title": "疑似真实凭据出现在仓库文本中",
        "why": "提交进提示词、代码、夹具或轨迹的凭据会直接跨越信任边界。",
        "recommendation": "先吊销该凭据，再从历史与产物中清除，改由受控的 secret 提供方注入。",
        "redact": True,
    },
    {
        "id": "SEC002",
        "severity": "P1",
        "dimension": "D8",
        "pattern": re.compile(
            r"(print|console\.log|logger\.\w+|log\.\w+)\s*\([^\n)]*"
            r"(api[_-]?key|password|passwd|secret|token|credential)",
            re.IGNORECASE,
        ),
        "title": "敏感值可能被写进日志",
        "why": "轨迹与日志通常长期保留，还会被拿去做评测样本与回归夹具。",
        "recommendation": "在写入日志之前脱敏，并为遥测边界补一条用例。",
        "redact": True,
    },
    {
        "id": "SAFE001",
        "severity": "P1",
        "dimension": "D5",
        "pattern": re.compile(
            r"except[^\n:]*:\s*(?:#[^\n]*)?\n\s*(return\s+\S|pass\b)",
        ),
        "title": "异常分支疑似放行（需人工核对是否 fail-open）",
        "why": "安全检查或审批闸门的 catch 分支若直接返回原内容或 pass，等于检查挂掉时放行。",
        "recommendation": "确认该函数是否承担检查/闸门职责；是则改为抑制输出走兜底（fail-closed）。",
        "note": "宽松匹配，假阳性多。只用于缩小搜索范围。",
        "redact": False,
    },
    {
        "id": "EXEC001",
        "severity": "P1",
        "dimension": "D5",
        "pattern": re.compile(r"\bshell\s*=\s*True\b", re.IGNORECASE),
        "title": "shell 执行接受拼接的命令字符串",
        "why": "不可信内容或模型生成的文本会获得 shell 解析语义。",
        "recommendation": "改用参数数组或受约束的命令适配器；必须用 shell 时加白名单与隔离。",
        "redact": False,
    },
    {
        "id": "EXEC002",
        "severity": "P1",
        "dimension": "D5",
        "pattern": re.compile(r"(?<![\w.])(eval|exec)\s*\("),
        "title": "动态代码执行需要复核信任边界",
        "why": "模型或外部输入到达动态执行点，可以逃出既定的能力边界。",
        "recommendation": "换成解析器，或放进受限沙箱并限定输入、输出、网络与资源。",
        "redact": False,
    },
    {
        "id": "EXEC003",
        "severity": "P1",
        "dimension": "D5",
        "pattern": re.compile(
            r"(\brm\s+-rf\b|\bgit\s+reset\s+--hard\b|\bgit\s+push\s+--force\b|"
            r"\bDROP\s+(DATABASE|TABLE)\b|Remove-Item[^\n]*-Recurse[^\n]*-Force)",
            re.IGNORECASE,
        ),
        "title": "潜在的破坏性操作",
        "why": "破坏性命令只有在确切范围的授权、预览、备份与回滚之下才可能是合理的。",
        "recommendation": "确认可达性，并给规范化后的确切操作加审批与恢复控制。",
        "redact": False,
    },
    {
        "id": "NET001",
        "severity": "P1",
        "dimension": "D5",
        "pattern": re.compile(
            r"(verify\s*=\s*False|--no-sandbox|NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*['\"]?0|"
            r"InsecureSkipVerify\s*:\s*true)",
            re.IGNORECASE,
        ),
        "title": "疑似关闭了传输层校验或沙箱",
        "why": "关闭校验会让外部内容与凭据同时暴露在可篡改的通道上。",
        "recommendation": "恢复校验；确需关闭的写明理由、范围与补偿控制。",
        "redact": False,
    },
    {
        "id": "LOOP001",
        "severity": "P2",
        "dimension": "D6",
        "pattern": re.compile(r"\bwhile\s*(?:\(\s*)?(True|true|1)\s*(?:\))?\s*[:{]"),
        "title": "无条件循环，需确认有外部上限",
        "why": "agent loop 的无条件循环若没有步数、时间或成本上限，会一直转到把预算耗尽。",
        "recommendation": "确认循环体内有四类上限的检查点与可读的触顶返回。",
        "note": "宽松匹配，非 agent loop 的无条件循环也会命中。",
        "redact": False,
    },
    {
        "id": "CACHE001",
        "severity": "P2",
        "dimension": "D2",
        "pattern": re.compile(
            r"(system[_-]?prompt|system[_-]?message|instructions)[^\n]{0,80}"
            r"(datetime\.now|time\.time|Date\.now|uuid4|random)",
            re.IGNORECASE,
        ),
        "title": "系统提示词疑似拼入了每次都变的值",
        "why": "前缀每次不同会让 prompt cache 全量失效，成本可能翻数倍。",
        "recommendation": "把动态值移到消息尾部；确需时间时确认它真的被用到了。",
        "redact": False,
    },
)


def _is_text_file(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or path.name in SPECIAL_NAMES


def iter_text_files(root: Path, max_files: int, max_bytes: int, oversized: List[str]) -> Iterable[Path]:
    count = 0
    for path in sorted(root.rglob("*")):
        if count >= max_files:
            return
        if not path.is_file() or path.is_symlink():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts[:-1]):
            continue
        if not _is_text_file(path):
            continue
        try:
            if path.stat().st_size > max_bytes:
                oversized.append(str(path.relative_to(root)))
                continue
        except OSError:
            continue
        count += 1
        yield path


def _redact(line: str) -> str:
    """脱敏：保留结构，抹掉可能的密文本体。"""
    out = re.sub(r"[A-Za-z0-9_\-]{16,}", "<REDACTED>", line)
    return out.strip()[:200]


def _snippet(line: str, redact: bool) -> str:
    return _redact(line) if redact else line.strip()[:200]


def scan(root: Path, max_files: int = 5000, max_bytes: int = 1_000_000) -> Dict[str, Any]:
    findings: List[Dict[str, Any]] = []
    seen_signals: Dict[str, int] = {name: 0 for name in DIMENSION_SIGNALS}
    prompt_files: List[Dict[str, Any]] = []
    scanned = 0
    skipped_unreadable = 0
    oversized: List[str] = []

    for path in iter_text_files(root, max_files, max_bytes, oversized):
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            skipped_unreadable += 1
            continue
        scanned += 1
        rel = str(path.relative_to(root))

        for name, spec in DIMENSION_SIGNALS.items():
            if spec["pattern"].search(text):
                seen_signals[name] += 1

        if PROMPT_FILE_HINT.search(rel) and len(text) > PROMPT_CHAR_LIMIT:
            prompt_files.append({"file": rel, "chars": len(text)})

        lines = text.splitlines()
        for rule in RULES:
            for match in rule["pattern"].finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                line = lines[line_no - 1] if line_no - 1 < len(lines) else ""
                findings.append({
                    "id": rule["id"],
                    "severity": rule["severity"],
                    "dimension": rule["dimension"],
                    "title": rule["title"],
                    "why": rule["why"],
                    "recommendation": rule["recommendation"],
                    "note": rule.get("note"),
                    "file": rel,
                    "line": line_no,
                    "snippet": _snippet(line, rule.get("redact", False)),
                    "likely_example": bool(EXAMPLE_PATH_HINT.search(rel)),
                })

    gaps = []
    for name, spec in DIMENSION_SIGNALS.items():
        if seen_signals[name] > 0:
            continue
        if spec.get("gap_is_normal"):
            continue
        dep = spec.get("only_if")
        if dep and seen_signals.get(dep, 0) == 0:
            continue
        gaps.append({
            "dimension": name,
            "means": spec["means"],
            "ref": spec["ref"],
            "pattern": spec["pattern"].pattern,
        })

    order = {"P0": 0, "P1": 1, "P2": 2}
    findings.sort(key=lambda f: (order.get(f["severity"], 9), f["file"], f["line"]))

    return {
        "root": str(root),
        "files_scanned": scanned,
        "files_skipped_unreadable": skipped_unreadable,
        "files_skipped_oversized": oversized,
        "hit_files_truncated": scanned >= max_files,
        "signal_hit_files": seen_signals,
        "evidence_gaps": gaps,
        "oversized_prompt_files": prompt_files,
        "findings": findings,
    }


DISCLAIMER = """\
> **这份输出是线索，不是结论。** 每一条都要人工读过对应代码才能升格为 finding，
> 并按 `references/modes/review-mode.md` §四 补齐证据与影响。
> 命中不等于做对（出现 `retry` 不代表退避正确）；零命中不等于没做（换个命名就漏），
> 所以每条证据缺口都附了所用的匹配模式，请据此判断是真没有还是命名不同。"""


def to_markdown(report: Dict[str, Any]) -> str:
    out: List[str] = []
    out.append(f"# Agent 仓库扫描线索：{report['root']}\n")
    out.append(DISCLAIMER + "\n")
    out.append(
        f"扫描文本文件 {report['files_scanned']} 个"
        + (f"，跳过无法解码 {report['files_skipped_unreadable']} 个" if report["files_skipped_unreadable"] else "")
        + ("\n\n**注意：已达到文件数上限，扫描不完整。**" if report["hit_files_truncated"] else "")
        + "\n"
    )
    if report["files_skipped_oversized"]:
        out.append(
            f"**注意：{len(report['files_skipped_oversized'])} 个文件因超过单文件字节上限被跳过，"
            "它们的内容未参与任何判定——下方的「证据缺口」可能因此失真。**\n"
        )
        for f in report["files_skipped_oversized"]:
            out.append(f"- `{f}`")
        out.append("\n用 `--max-bytes` 调大上限后重跑，或单独检视这些文件。\n")

    out.append("## 证据缺口\n")
    if report["evidence_gaps"]:
        out.append("以下维度在全仓库**零命中**，需要人工确认是真没做，还是用了别的命名：\n")
        for gap in report["evidence_gaps"]:
            out.append(f"### {gap['dimension']}\n")
            out.append(f"- 含义：{gap['means']}")
            out.append(f"- 对照：`{gap['ref']}`")
            out.append(f"- 所用匹配模式：`{gap['pattern']}`\n")
    else:
        out.append("各维度均有信号命中（**命中不等于做对**，仍需按维度矩阵逐项核对）。\n")

    if report["oversized_prompt_files"]:
        out.append("## D2 提示词长度（硬判定）\n")
        out.append(f"以下疑似提示词文件超过 {PROMPT_CHAR_LIMIT} 字符红线。**先确认它是否真的整段进入某个 Agent 的 system 位**——skill 的 SKILL.md、AGENTS.md 属渐进披露入口，D2 的红线本不适用于它们：\n")
        for item in report["oversized_prompt_files"]:
            over = item["chars"] - PROMPT_CHAR_LIMIT
            out.append(f"- `{item['file']}` — {item['chars']} 字符（超 {over}）")
        out.append("")

    def render(items: List[Dict[str, Any]]) -> None:
        current = None
        for f in items:
            if f["severity"] != current:
                current = f["severity"]
                out.append(f"#### {current}\n")
            out.append(f"- **[{f['id']}] {f['title']}**（{f['dimension']}）")
            out.append(f"  - 位置：`{f['file']}:{f['line']}`")
            out.append(f"  - 片段：`{f['snippet']}`")
            out.append(f"  - 为什么值得看：{f['why']}")
            out.append(f"  - 建议：{f['recommendation']}")
            if f.get("note"):
                out.append(f"  - 说明：{f['note']}")
        out.append("")

    impl = [f for f in report["findings"] if not f["likely_example"]]
    example = [f for f in report["findings"] if f["likely_example"]]

    out.append("## 风险线索（实现代码）\n")
    if not impl:
        out.append("无命中。**这不代表没有风险**，只代表这些正则没有匹配到。\n")
    else:
        render(impl)

    if example:
        out.append("## 风险线索（文档 / 测试 / 夹具）\n")
        out.append(
            "这些命中位于文档、测试或评测夹具里，**多为示例而非实现**——"
            "但也可能是真放在夹具里的凭据，或反例被误当正例引用，仍需扫一眼。\n"
        )
        render(example)

    out.append("## 信号命中文件数（供参考，不作判定）\n")
    for name, count in report["signal_hit_files"].items():
        out.append(f"- {name}：{count} 个文件")
    out.append("")
    return "\n".join(out)


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="按 agentic-principle 维度矩阵扫描 Agent 仓库，只产线索不产结论。"
    )
    parser.add_argument("target", help="要扫描的目录")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--max-files", type=int, default=5000)
    parser.add_argument("--max-bytes", type=int, default=1_000_000, help="单文件字节上限")
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    root = Path(args.target).expanduser().resolve()
    if not root.is_dir():
        print(f"错误：{root} 不是目录", file=sys.stderr)
        return 2
    report = scan(root, max_files=args.max_files, max_bytes=args.max_bytes)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(to_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
