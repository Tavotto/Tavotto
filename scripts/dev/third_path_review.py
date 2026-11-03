"""校验 PR 正文里「Third-Path Review」一节的**结构一致性**（本地工具，不进 CI）。

规则全文在 docs/rules/repo/third-path-review.md。这里只判机器判得了的那部分：

* 有 `## Third-Path Review` 节时，九个字段都在且非空（`not_applicable` 只要求「结论」），
  结论在五档闭集里；
* `temporary_mitigation` / `promising_unverified` 要有 `#N` 跟踪，且正文**没有**用 GitHub 的
  关闭关键字（Fixes / Closes / Resolves 及变形）指向任何 issue——否则合并时 GitHub 会把根问题
  自动关掉，与声明的结论自相矛盾；`temporary_mitigation` 另要那行声明；
* `necessary_tradeoff` 的「证据」不能是「无 / 待补 / TBD」这类非答案。

判据的主语是**这份正文此刻的文本**；它判不了审查做得好不好（出现字段名不等于做过架构思考），
所以不当必需检查。正文没有这一节 = 本 PR 没触发审查，返回 0；取不到正文（`gh` 失败、空输出）
返回 2——「没取到」不是「没有」。

用法：`python scripts/dev/third_path_review.py <正文文件 | -> [--pr N]`
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

VERDICTS = (
    "structural_solution_found",
    "promising_unverified",
    "necessary_tradeoff",
    "temporary_mitigation",
    "not_applicable",
)
FIELDS = (
    "结论",
    "用户目标",
    "现有冲突",
    "共同前提",
    "第三路径候选",
    "证据",
    "最终决定",
    "遗留限制",
    "跟踪",
)
#: 必须挂根因跟踪、且不得自动关闭 issue 的结论。
OPEN_ROOT_VERDICTS = {"temporary_mitigation", "promising_unverified"}
MITIGATION_BANNER = re.compile(r"Temporary mitigation\s*(?:—|--|-)\s*root problem unresolved", re.I)

SECTION_HEAD = re.compile(r"^##\s+Third-Path Review\s*$", re.M)
NEXT_HEAD = re.compile(r"^##\s+", re.M)
FIELD_LINE = re.compile(r"^\s*[-*]\s*([^:：\n]+?)\s*[:：]\s*(.*?)\s*$", re.M)
COMMENT = re.compile(r"<!--.*?-->", re.S)
ISSUE_REF = re.compile(r"#\d+")
#: GitHub 的关闭关键字（docs: "Linking a pull request to an issue"），后接 `#N`、`owner/repo#N` 或 issue URL。
CLOSING = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b:?\s+"
    r"(?:(?:[\w.-]+/[\w.-]+)?#\d+|https://github\.com/[\w.-]+/[\w.-]+/issues/\d+)",
    re.I,
)
NON_ANSWERS = {"", "无", "暂无", "待补", "待定", "见上", "略", "tbd", "todo", "n/a", "-", "—"}


def _placeholder(value: str) -> bool:
    v = value.strip()
    return v.lower() in NON_ANSWERS or bool(re.fullmatch(r"<[^<>]*>", v))


def extract_section(body: str) -> str | None:
    """返回 `## Third-Path Review` 节的正文（去掉 HTML 注释）；没有这一节返回 None。"""
    text = COMMENT.sub("", body)
    m = SECTION_HEAD.search(text)
    if m is None:
        return None
    rest = text[m.end() :]
    nxt = NEXT_HEAD.search(rest)
    return rest[: nxt.start()] if nxt else rest


def parse_fields(section: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in FIELD_LINE.findall(section):
        key = key.strip()
        if key in FIELDS and key not in out:
            out[key] = value.strip()
    return out


def check(body: str) -> list[str]:
    """返回问题列表；空列表 = 结构一致（含「没有这一节」）。"""
    section = extract_section(body)
    if section is None:
        return []
    fields = parse_fields(section)
    verdict = fields.get("结论", "").strip("` ")
    if verdict not in VERDICTS:
        return [f"结论必须是 {' / '.join(VERDICTS)} 之一，实际是 {verdict or '（空）'!r}"]
    if verdict == "not_applicable":
        return []

    problems = []
    for key in FIELDS:
        if key == "跟踪" and verdict not in OPEN_ROOT_VERDICTS:
            continue
        if key not in fields:
            problems.append(f"缺字段「{key}」")
        elif key != "遗留限制" and _placeholder(fields[key]):
            problems.append(f"字段「{key}」是空的或只是占位")

    if verdict in OPEN_ROOT_VERDICTS:
        if not ISSUE_REF.search(fields.get("跟踪", "")):
            problems.append(f"{verdict} 必须在「跟踪」里引用根因 issue（#N）")
        closing = CLOSING.findall(COMMENT.sub("", body))
        if closing:
            problems.append(
                f"{verdict} 不得用关闭关键字（合并时 GitHub 会自动关掉根 issue）：{closing}"
                "——改成 Refs #N"
            )
    if verdict == "temporary_mitigation" and not MITIGATION_BANNER.search(body):
        problems.append(
            "temporary_mitigation 要在正文写明一行「Temporary mitigation — root problem unresolved」"
        )
    return problems


def _read_body(path: str | None, pr: int | None) -> str:
    if pr is not None:
        out = subprocess.run(
            ["gh", "pr", "view", str(pr), "--json", "body", "-q", ".body"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if out.returncode != 0:
            raise RuntimeError(out.stderr.strip() or f"gh 退出码 {out.returncode}")
        return out.stdout
    if path in (None, "-"):
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("body", nargs="?", help="PR 正文文件；- 或省略 = 读 stdin")
    ap.add_argument("--pr", type=int, help="经 gh 取这个 PR 的正文")
    args = ap.parse_args(argv)
    # Windows 上管道 / 重定向会退回系统区域编码（cp1252/cp936），而正文与报告全是中文。
    # 放在 main 里：被 import（用例）时不去动调用方的流。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    try:
        body = _read_body(args.body, args.pr)
    except (OSError, RuntimeError) as exc:
        print(f"取不到正文：{exc}", file=sys.stderr)
        return 2
    if not body.strip():
        print("正文是空的——取不到不等于没有这一节", file=sys.stderr)
        return 2
    if extract_section(body) is None:
        print("没有 Third-Path Review 节（本 PR 未触发审查）")
        return 0
    problems = check(body)
    for p in problems:
        print(f"- {p}")
    if not problems:
        print("Third-Path Review 节结构一致")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
