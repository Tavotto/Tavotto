"""解析并检查错题本 `docs/rules/repo/lessons/<层>.md`。

错题本的格式写在同目录 `README.md`；这里是它的唯一解析器：看护用例
`tests/test_lessons_format.py` 与维护者本机的每日复盘脚本都调本模块，不各写一份。

用法::

    python scripts/dev/lessons.py --check   # 解析 + 全部检查，有问题退出码 1
    python scripts/dev/lessons.py --due     # 列出 Seen ≥ 3、待升级的条目
    python scripts/dev/lessons.py --json    # 解析结果（给复盘脚本）
    python scripts/dev/lessons.py --stats   # 每层体积与余量、下一个可用编号
    python scripts/dev/lessons.py --recurrence [--since YYYY-MM-DD]
                                            # 已升级条目：看护在哪、升级后又犯了几次

按改动文件取命中的条目见同目录 `lessons_for_diff.py`（匹配规则 `trigger_matches` 在本模块）。

纯标准库。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
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
#: 未升级的条目另须有「触发」（看护用例判），解析时可缺：旧格式文本仍能读。
OPTIONAL_FIELDS = ("触发",)
#: 触发里这个特殊值 = 任何改动都适用的通用纪律；克制：条数上限在看护用例里。
UNIVERSAL = "*"
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
#: 细则里升级那段的标记：`（错题本 L-017 升级，2026-11-11）`。日期是升级那天，之后写进这段的日期都算再犯。
UPGRADE_MARK = r"错题本\s*{id}\s*升级[，,]\s*(\d{{4}}-\d{{2}}-\d{{2}})"
#: 升级那段写不出机械看护时的明示标记：诚实地说「只靠人记」，好过假装有看护。
MANUAL_GUARD = "看护：只靠人记"
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TICKED = re.compile(r"`([^`\s]+)`")
#: 另一条升级的标记（可能折行：`错题本 L-072` 行尾、`升级` 在下一行）：一段到这里为止。
_OTHER_MARK = re.compile(r"错题本\s*L-\d{3,}(?:\s*升级|\s*$)")


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
    triggers: tuple[str, ...]  # 路径条目（语法见 `trigger_matches`）；已升级的为空
    why: str
    evidence: str
    line: int

    @property
    def universal(self) -> bool:
        return self.triggers == (UNIVERSAL,)

    def triggered_by(self, paths: list[str]) -> bool:
        """改动文件里有没有命中这条的触发（通用条目不在此列，见 `universal`）。"""
        return any(trigger_matches(t, p) for t in self.triggers for p in paths)

    @property
    def due(self) -> bool:
        return self.status == "active" and len(self.seen) >= PROMOTE_AT


class LessonsError(ValueError):
    pass


def trigger_syntax_error(entry: str) -> str | None:
    """触发路径条目的语法（与 `.github/judge-paths.json` 对齐）；合法返回 None。

    `/` 结尾 = 目录前缀；`*` 结尾 = 同一目录下文件名前缀；否则精确路径；不支持别的通配。
    """
    if not entry:
        return "空条目"
    if entry == UNIVERSAL:
        return None
    if entry != entry.strip() or re.search(r"\s", entry):
        return "含空白"
    if entry.startswith("/") or "\\" in entry or ".." in entry.split("/"):
        return "须是仓库内相对的 / 分隔路径"
    body = entry[:-1] if entry.endswith("*") else entry
    if any(c in body for c in "*?[]{}"):
        return "只支持结尾一个 *（文件名前缀），不支持别的通配"
    if entry == "/":
        return "空目录前缀"
    return None


def trigger_matches(entry: str, path: str) -> bool:
    """`entry` 是否命中 `path`（POSIX 相对路径）。`*`（通用）不在这里命中：它不按路径触发。"""
    if entry == UNIVERSAL:
        return False
    if entry.endswith("/"):
        return path.startswith(entry)
    if entry.endswith("*"):
        prefix = entry[:-1]
        head, _, name_prefix = prefix.rpartition("/")
        phead, _, pname = path.rpartition("/")
        return phead == head and pname.startswith(name_prefix)
    return path == entry


def tracked_files(root: Path = ROOT) -> list[str]:
    """`git ls-files` 的结果；取不到抛 `LessonsError`（空清单会让「条目至少匹配一个文件」恒红或恒绿）。"""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=True,
            capture_output=True,
        ).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.CalledProcessError) as e:
        raise LessonsError(f"取不到 git ls-files：{e}") from None
    files = [x for x in out.split("\0") if x]
    if not files:
        raise LessonsError("git ls-files 为空")
    return files


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
                        triggers=(),
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
        if f and f.group(1).strip() in FIELDS + OPTIONAL_FIELDS:
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
        triggers=tuple(t.strip() for t in f.get("触发", "").split(",") if t.strip()),
        why=f["为什么"],
        evidence=f["证据"],
        line=c["line"],
    )


@dataclass(frozen=True)
class Upgrade:
    """一条已升级条目在细则里的那一段。"""

    id: str
    target: str
    date: str  # 升级那天
    guards: tuple[str, ...]  # 段里点名、且在仓库里找得到的测试 / 门禁文件
    manual: bool  # 段里写了 MANUAL_GUARD
    recurrences: tuple[str, ...]  # 段里晚于升级日的日期 = 升级后再犯的日子（升序、不重复）


def upgrade_block(text: str, lesson_id: str) -> tuple[str, str] | None:
    """细则 `text` 里 `lesson_id` 升级那一段：返回 (升级日, 段落原文)；找不到标记返回 None。

    段落的边界：标记在标题上，到下一个同级或更高级标题为止；标记在条目 / 粗体段首，到空行、
    标题，或缩进不深于它的下一个 `- ` 条目 / `**` 粗体段首为止。两种都在下一条升级标记前停下。"""
    m = re.search(UPGRADE_MARK.format(id=re.escape(lesson_id)), text)
    if not m:
        return None
    lines = text.splitlines()
    first = text.count("\n", 0, m.start())
    head = lines[first]
    end = len(lines)
    last_mark_line = text.count("\n", 0, m.end())
    level = len(head) - len(head.lstrip("#")) if head.startswith("#") else 0
    indent = len(head) - len(head.lstrip(" "))
    for i in range(last_mark_line + 1, len(lines)):
        ln = lines[i]
        body = ln.lstrip(" ")
        if _OTHER_MARK.search(ln):
            end = i  # 下一条升级从这里开始，它的再犯不算到这条头上
            break
        if level:
            if ln.startswith("#") and len(ln) - len(ln.lstrip("#")) <= level:
                end = i
                break
            continue
        if not body or ln.startswith("#"):
            end = i
            break
        if len(ln) - len(body) <= indent and (body.startswith("- ") or body.startswith("**")):
            end = i
            break
    return m.group(1), "\n".join(lines[first:end])


def _is_guard_token(tok: str) -> bool:
    name = tok.rsplit("/", 1)[-1]
    return (
        (name.startswith("test_") and name.endswith(".py"))
        or ".test." in name
        or ".spec." in name
        or tok.startswith(("tests/", "scripts/ci/", "web/e2e/"))
    )


def block_guards(body: str, tracked: list[str]) -> tuple[str, ...]:
    """段里反引号点名的测试 / 门禁文件，且能在已跟踪文件里按后缀找到（写错名 = 不算看护）。"""
    out: list[str] = []
    for tok in _TICKED.findall(body):
        tok = tok.split("::", 1)[0].rstrip("，。、；:")
        if not _is_guard_token(tok) or tok in out:
            continue
        if any(p == tok or p.endswith("/" + tok) for p in tracked):
            out.append(tok)
    return tuple(out)


def upgrades(
    lessons: list[Lesson], *, root: Path = ROOT, tracked: list[str] | None = None
) -> tuple[list[Upgrade], list[str]]:
    """每条已升级条目在细则里那一段的看护与再犯；第二项是找不到那一段的问题。"""
    out: list[Upgrade] = []
    errs: list[str] = []
    for x in lessons:
        if not x.promoted_to:
            continue
        target = root / x.promoted_to
        if not target.is_file():
            continue  # `problems` 已报
        found = upgrade_block(target.read_text(encoding="utf-8"), x.id)
        if found is None:
            errs.append(
                f"{x.id}: {x.promoted_to} 里找不到「错题本 {x.id} 升级，YYYY-MM-DD」标记"
                "（升级日是数再犯的起点）"
            )
            continue
        date, body = found
        if tracked is None:
            tracked = tracked_files(root)
        later = sorted({d for d in _DATE.findall(body) if d > date})
        out.append(
            Upgrade(
                id=x.id,
                target=x.promoted_to,
                date=date,
                guards=block_guards(body, tracked),
                manual=MANUAL_GUARD in body,
                recurrences=tuple(later),
            )
        )
    return out, errs


def upgrade_problems(
    lessons: list[Lesson], *, root: Path = ROOT, tracked: list[str] | None = None
) -> list[str]:
    """升级那一段必须带机械看护（点名一个找得到的测试 / 门禁文件），或明写「看护：只靠人记」。"""
    ups, errs = upgrades(lessons, root=root, tracked=tracked)
    for u in ups:
        if not u.guards and not u.manual:
            errs.append(
                f"{u.id}: {u.target} 里升级那一段没点名看护（反引号写出测试 / 门禁文件），"
                f"写不出就明写「{MANUAL_GUARD}」"
            )
    return errs


def beijing_today(now: dt.datetime | None = None) -> dt.date:
    """北京时间的今天；now 不带时区时按 UTC 解读。"""
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    return now.astimezone(BEIJING).date()


def problems(
    text: str,
    layer: str,
    *,
    root: Path = ROOT,
    today: dt.date | None = None,
    tracked: list[str] | None = None,
) -> list[str]:
    """一层文件解析之外的检查：节内编号、体积、脱敏、升级目标、日期不在未来、触发字段。

    `tracked` 是 `git ls-files` 的清单；不传则取 `root` 的。"""
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
    out += trigger_problems(lessons, root=root, tracked=tracked)
    out += upgrade_problems(lessons, root=root, tracked=tracked)
    return out


def trigger_problems(
    lessons: list[Lesson], *, root: Path = ROOT, tracked: list[str] | None = None
) -> list[str]:
    """未升级的条目必须有触发；每个路径条目语法合法、至少匹配一个已跟踪文件（否则是空转）。"""
    out: list[str] = []
    files: list[str] | None = tracked
    for x in lessons:
        if x.status != "active":
            continue
        if not x.triggers:
            out.append(f"{x.id}: 缺「触发」字段（路径条目，或通用的 *）")
            continue
        if UNIVERSAL in x.triggers and x.triggers != (UNIVERSAL,):
            out.append(f"{x.id}: 触发里的 * 只能单独出现")
            continue
        if len(set(x.triggers)) != len(x.triggers):
            out.append(f"{x.id}: 触发里有重复条目")
        for t in x.triggers:
            err = trigger_syntax_error(t)
            if err:
                out.append(f"{x.id}: 触发「{t}」不合法：{err}")
            elif t != UNIVERSAL:
                if files is None:
                    files = tracked_files(root)
                if not any(trigger_matches(t, p) for p in files):
                    out.append(f"{x.id}: 触发「{t}」没匹配到任何已跟踪文件（空转）")
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
    directory: Path = LESSONS,
    *,
    root: Path = ROOT,
    today: dt.date | None = None,
    tracked: list[str] | None = None,
) -> list[str]:
    """全部检查：逐层 `problems` + 跨文件的编号不重复。"""
    books, out = _book_paths(directory)
    if tracked is None:
        try:
            tracked = tracked_files(root)
        except LessonsError as e:
            return out + [str(e)]
    numbers: list[int] = []
    for layer, path in books:
        text = path.read_text(encoding="utf-8")
        out += [
            f"{path.name} {e}"
            for e in problems(text, layer, root=root, today=today, tracked=tracked)
        ]
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
    g.add_argument(
        "--recurrence", action="store_true", help="已升级条目的看护与升级后再犯（复盘的效果数）"
    )
    ap.add_argument("--since", help="--recurrence 只数这天及以后的再犯（YYYY-MM-DD）")
    ap.add_argument("--dir", type=Path, default=LESSONS)
    args = ap.parse_args(argv)
    if args.recurrence:
        try:
            ups, errs = upgrades(load(args.dir), root=args.dir.parents[3])
        except LessonsError as e:
            print(e, file=sys.stderr)
            return 1
        return _print_recurrence(ups, errs, args.since)
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


def _print_recurrence(ups: list[Upgrade], errs: list[str], since: str | None) -> int:
    if since is not None:
        try:
            dt.date.fromisoformat(since)
        except ValueError:
            print(f"--since 须是 YYYY-MM-DD，得到「{since}」", file=sys.stderr)
            return 2
    total = 0
    hit = 0
    for u in sorted(ups, key=lambda u: (-len(u.recurrences), u.id)):
        dates = [d for d in u.recurrences if since is None or d >= since]
        total += len(dates)
        hit += bool(dates)
        guard = "、".join(u.guards) if u.guards else ("只靠人记" if u.manual else "缺看护")
        again = f"再犯 {len(dates)}（{'、'.join(dates)}）" if dates else "再犯 0"
        print(f"{u.id}\t升级 {u.date}\t{again}\t看护 {guard}")
    manual = sum(1 for u in ups if not u.guards)
    scope = f"{since} 起" if since else "升级后"
    print(
        f"合计\t{len(ups)} 条已升级，{scope}再犯 {total} 次、涉及 {hit} 条；{manual} 条没有机械看护"
    )
    for e in errs:
        print(e, file=sys.stderr)
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
