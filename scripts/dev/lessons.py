"""解析并检查错题本 `docs/rules/repo/lessons/<层>.md`。

错题本的格式写在同目录 `README.md`；这里是它的唯一解析器：看护用例
`tests/test_lessons_format.py` 与维护者本机的每日复盘脚本都调本模块，不各写一份。

用法::

    python scripts/dev/lessons.py --check   # 解析 + 全部检查，有问题退出码 1
    python scripts/dev/lessons.py --due     # 列出 Seen ≥ 3、待升级的条目
    python scripts/dev/lessons.py --json    # 解析结果（给复盘脚本）
    python scripts/dev/lessons.py --stats   # 每层体积与余量、下一个可用编号

纯标准库。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
LESSONS = ROOT / "docs" / "rules" / "repo" / "lessons"

#: 每层一份的体积上限：一个 agent 读的就是一份，上限按读的单位定。超了：夜审先升级或删条目，不加新条目。
CAP_BYTES = 16 * 1024
#: 同一条在这么多个不同日子出现就升级。
PROMOTE_AT = 3
#: 每层一个文件，与 `docs/rules/` 的层一一对应。
LAYERS = ("repo", "backend", "frontend", "ci", "plugin", "packaging", "desktop")
KINDS = ("别这样", "照这样")
FIELDS = ("类", "Seen", "为什么", "证据")
#: Seen 按北京时间记（错题本「怎么用」与每日复盘约定），「不在未来」也按北京时间的今天判。
#: 不用 runner 本地日期：CI 是 UTC，复盘 04:30（北京）开工时 UTC 还在前一天，当天的 Seen 会被判成未来。
#: 用固定 +08:00 而不是 zoneinfo：中国不用夏令时，且 Windows 上没装 tzdata 时 zoneinfo 取不到。
BEIJING = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")

#: 公开仓库里不许出现的东西：机器地址、反馈包编号、本机用户目录、邮箱、凭据形状。
PRIVATE = re.compile(
    r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])"
    r"|\bTV[DM]-[0-9A-Z]{4}\b"
    r"|/Users/[^/\s`]+|/home/[^/\s`]+|[A-Za-z]:\\Users\\"
    r"|[\w.+-]+@[\w-]+\.[\w.]+"
    r"|qyapi\.weixin|api[_-]?key\s*=",
    re.IGNORECASE,
)

HEAD = re.compile(r"^### (L-(\d{3,}))\s+(\S.*)$")
FIELD = re.compile(r"^- ([^:：]+)[:：]\s*(.*)$")
#: 已升级的条目只留标题这一行：`### L-001 规则 → docs/rules/<层>/<主题>.md`。
PROMOTED = re.compile(r"^(.*\S)\s+→\s*(docs/rules/[a-z]+/[\w.-]+\.md)$")


@dataclass(frozen=True)
class Lesson:
    id: str
    number: int
    rule: str
    layer: str
    kind: str  # 已升级的条目为空：详情都在细则里
    status: str  # "active" 或 "promoted"
    promoted_to: str | None
    seen: tuple[str, ...]  # ISO 日期，升序、不重复
    why: str
    evidence: str
    line: int

    @property
    def due(self) -> bool:
        return self.status == "active" and len(self.seen) >= PROMOTE_AT


class LessonsError(ValueError):
    pass


def parse(text: str, layer: str) -> list[Lesson]:
    """按条目解析一层的文件；格式不对抛 `LessonsError`，消息带行号。"""
    lessons: list[Lesson] = []
    current: dict | None = None

    def close() -> None:
        nonlocal current
        if current is None:
            return
        missing = [f for f in FIELDS if f not in current["fields"]]
        if missing:
            raise LessonsError(f"第 {current['line']} 行 {current['id']}: 缺字段 {missing}")
        lessons.append(_build(current))
        current = None

    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        m = HEAD.match(line)
        if m:
            close()
            p = PROMOTED.match(m.group(3))
            if p:
                lessons.append(
                    Lesson(
                        id=m.group(1),
                        number=int(m.group(2)),
                        rule=p.group(1),
                        layer=layer,
                        kind="",
                        status="promoted",
                        promoted_to=p.group(2),
                        seen=(),
                        why="",
                        evidence="",
                        line=n,
                    )
                )
                continue
            current = {
                "id": m.group(1),
                "number": int(m.group(2)),
                "rule": m.group(3).strip(),
                "layer": layer,
                "line": n,
                "fields": {},
            }
            continue
        if current is None:
            if line.startswith("- "):
                raise LessonsError(f"第 {n} 行: 已升级的条目只留标题一行：{line[:60]}")
            continue
        f = FIELD.match(line)
        if f and f.group(1).strip() in FIELDS:
            key = f.group(1).strip()
            if key in current["fields"]:
                raise LessonsError(f"第 {n} 行 {current['id']}: 字段「{key}」重复")
            current["fields"][key] = f.group(2).strip()
        elif line.startswith("  ") and current["fields"]:
            # 续行：接到上一个字段后面
            last = list(current["fields"])[-1]
            current["fields"][last] += " " + line.strip()
        elif line.strip():
            raise LessonsError(f"第 {n} 行 {current['id']}: 认不出的行：{line[:60]}")
    close()
    return lessons


def _build(c: dict) -> Lesson:
    f = c["fields"]
    where = f"第 {c['line']} 行 {c['id']}"
    if f["类"] not in KINDS:
        raise LessonsError(f"{where}: 类须是 {KINDS} 之一，得到「{f['类']}」")
    seen = tuple(s.strip() for s in f["Seen"].split(",") if s.strip())
    if not seen:
        raise LessonsError(f"{where}: Seen 至少一个日期")
    for s in seen:
        try:
            dt.date.fromisoformat(s)
        except ValueError:
            raise LessonsError(f"{where}: Seen 里「{s}」不是 YYYY-MM-DD") from None
    if list(seen) != sorted(set(seen)):
        raise LessonsError(f"{where}: Seen 须升序且同一天只记一次")
    if not f["为什么"] or not f["证据"]:
        raise LessonsError(f"{where}: 为什么 / 证据 不能空")
    return Lesson(
        id=c["id"],
        number=c["number"],
        rule=c["rule"],
        layer=c["layer"],
        kind=f["类"],
        status="active",
        promoted_to=None,
        seen=seen,
        why=f["为什么"],
        evidence=f["证据"],
        line=c["line"],
    )


def beijing_today(now: dt.datetime | None = None) -> dt.date:
    """北京时间的今天；now 不带时区时按 UTC 解读。"""
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    return now.astimezone(BEIJING).date()


def problems(
    text: str, layer: str, *, root: Path = ROOT, today: dt.date | None = None
) -> list[str]:
    """一层文件解析之外的检查：节内编号、体积、脱敏、升级目标、日期不在未来。"""
    out: list[str] = []
    try:
        lessons = parse(text, layer)
    except LessonsError as e:
        return [str(e)]
    nums = [x.number for x in lessons]
    if nums != sorted(nums):
        out.append("文件内编号没按升序排")
    size = len(text.encode("utf-8"))
    if size > CAP_BYTES:
        out.append(f"体积 {size} B 超过上限 {CAP_BYTES} B：先升级或删条目")
    for n, line in enumerate(text.splitlines(), start=1):
        m = PRIVATE.search(line)
        if m:
            out.append(f"第 {n} 行有私有信息形状「{m.group(0)}」（公开仓库）")
    today = today or beijing_today()
    for x in lessons:
        if x.promoted_to:
            target = root / x.promoted_to
            if not target.is_file():
                out.append(f"{x.id}: 升级目标 {x.promoted_to} 不存在")
            elif x.id not in target.read_text(encoding="utf-8"):
                # 指针留下、规则却没写进细则，等于这条教训丢了
                out.append(f"{x.id}: 升级目标 {x.promoted_to} 里找不到 {x.id}")
        if x.seen and dt.date.fromisoformat(x.seen[-1]) > today:
            out.append(f"{x.id}: Seen 里有未来的日期 {x.seen[-1]}")
    return out


def _book_paths(directory: Path) -> tuple[list[tuple[str, Path]], list[str]]:
    """目录里每层的文件；目录里多出来的 .md（README 除外）没人读，算问题。"""
    known = {f"{x}.md" for x in LAYERS} | {"README.md"}
    stray = sorted(p.name for p in directory.glob("*.md") if p.name not in known)
    errs = [f"{name}: 不是层名（须是 {LAYERS} 之一）" for name in stray]
    missing = [x for x in LAYERS if not (directory / f"{x}.md").is_file()]
    errs += [f"{x}.md: 缺这一层的文件" for x in missing]
    return [(x, directory / f"{x}.md") for x in LAYERS if x not in missing], errs


def load(directory: Path = LESSONS) -> list[Lesson]:
    books, errs = _book_paths(directory)
    if errs:
        raise LessonsError("; ".join(errs))
    out: list[Lesson] = []
    for layer, path in books:
        try:
            out += parse(path.read_text(encoding="utf-8"), layer)
        except LessonsError as e:
            raise LessonsError(f"{path.name} {e}") from None
    return out


def check_all(
    directory: Path = LESSONS, *, root: Path = ROOT, today: dt.date | None = None
) -> list[str]:
    """全部检查：逐层 `problems` + 跨文件的编号不重复。"""
    books, out = _book_paths(directory)
    numbers: list[int] = []
    for layer, path in books:
        text = path.read_text(encoding="utf-8")
        out += [f"{path.name} {e}" for e in problems(text, layer, root=root, today=today)]
        try:
            numbers += [x.number for x in parse(text, layer)]
        except LessonsError:
            pass  # 上面已报
    dup = sorted({n for n in numbers if numbers.count(n) > 1})
    if dup:
        out.append(f"编号重复: {dup}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="全部检查（默认）")
    g.add_argument("--due", action="store_true", help="列出待升级的条目")
    g.add_argument("--json", action="store_true", help="输出解析结果")
    g.add_argument("--stats", action="store_true", help="每层体积与余量、下一个可用编号")
    ap.add_argument("--dir", type=Path, default=LESSONS)
    args = ap.parse_args(argv)
    if args.json or args.due or args.stats:
        try:
            lessons = load(args.dir)
        except LessonsError as e:
            print(e, file=sys.stderr)
            return 1
        if args.json:
            print(
                json.dumps(
                    [asdict(x) | {"due": x.due} for x in lessons], ensure_ascii=False, indent=2
                )
            )
        elif args.due:
            for x in lessons:
                if x.due:
                    print(f"{x.id}\t{x.layer}\t{len(x.seen)}×\t{x.rule}")
        else:
            for layer in LAYERS:
                size = len((args.dir / f"{layer}.md").read_text(encoding="utf-8").encode("utf-8"))
                print(f"{layer}\t{size}\t余 {CAP_BYTES - size}")
            print(f"下一个编号\tL-{max(x.number for x in lessons) + 1:03d}")
        return 0
    errs = check_all(args.dir)
    for e in errs:
        print(e, file=sys.stderr)
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
