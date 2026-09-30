"""经确认改写脚本里的数据路径常量（ADR 0110）——改指表救不回的那几档（探路 / glob / C++ 读取器）。

用户在「找不到脚本要读的数据」里（主条目是这几档时）指认数据现在的位置之后：

* `plan()` —— 按那次指认推出的规则（`inputremap.derive_location`）挑出脚本里**以字符串常量出现**、
  与某条缺失路径相等或按路径段是它前缀、此刻自己也不存在的那几串（§二），换成新位置的**绝对路径**
  （`/` 分隔，§三），生成新字节；四条静态自检（§四）任一不过就抛，原脚本零改动。
* `undo()` —— 复原时「只撤销这几处路径」：之后用户又改过脚本也能逐处换回，其余修改保留（§七）。
* `TOKENS` —— 预览签发、提交核销的一次性确认令牌：绑定浏览器会话（cookie）、写前与写后的 sha256，
  单次、十分钟（§六）。令牌从不出现在 MCP 结果里。

**块外一个字节不动**：只替换常量那几段字节；BOM、换行风格、混合换行、结尾无换行原样。不改 f-string、
隐式拼接、bytes、三引号、跨行的串，也不改下标 / 字典键 / 比较里的串与存图调用的实参——判不出就不改，
逐条说出原因。

纯标准库（Flask 父进程侧）。
"""

from __future__ import annotations

import ast
import dataclasses
import io
import os
import re
import secrets
import threading
import time
import tokenize
from pathlib import Path

from . import figcapture, inputremap, scriptbackup

#: 稳定错误码（`tests/test_error_codes.py` 读这张表）。
ERROR_NOTHING_TO_CHANGE = "script_edit_nothing_to_change"
ERROR_UNREADABLE = "script_edit_unreadable"
ERROR_SELF_CHECK = "script_edit_self_check_failed"
ERROR_TOKEN_INVALID = "script_edit_token_invalid"
ERROR_PREVIEW_STALE = "script_edit_preview_stale"
ERROR_NEEDS_UI = "script_edit_needs_ui"
ERROR_RESTORE_CONFLICT = "script_restore_conflict"
#: 复原时界面看到的那一版（`expected_sha256`）已经不是磁盘上的了：状态是旧的，按它选的复原方式不再成立
ERROR_RESTORE_STALE = "script_restore_stale"
ERROR_CODES = (
    ERROR_NOTHING_TO_CHANGE,
    ERROR_UNREADABLE,
    ERROR_SELF_CHECK,
    ERROR_TOKEN_INVALID,
    ERROR_PREVIEW_STALE,
    ERROR_NEEDS_UI,
    ERROR_RESTORE_CONFLICT,
    ERROR_RESTORE_STALE,
)

#: 某个对得上的常量为什么没改（闭集；前端逐条翻译）。
SKIP_FSTRING = "fstring"  # f-string 里的一段：路径是拼出来的
SKIP_CONCATENATED = "concatenated"  # 隐式拼接 `"a" "b"`
SKIP_MULTILINE = "multiline"  # 跨行 / 三引号
SKIP_CONTEXT = "context"  # 下标、字典键、比较、存图调用的实参……不是在当路径用
SKIP_RULE_MISMATCH = "rule_mismatch"  # 指认推出的规则换不了它（比如文件改了名，而它是目录）
SKIP_TARGET_MISSING = "target_missing"  # 换过去之后那里也没有
SKIP_UNREPRESENTABLE = "unrepresentable"  # raw 串装不下新值（引号 / 结尾反斜杠 / 控制字符）
SKIP_ENCODING = "encoding"  # 脚本的编码写不下新值
SKIP_REASONS = (
    SKIP_FSTRING,
    SKIP_CONCATENATED,
    SKIP_MULTILINE,
    SKIP_CONTEXT,
    SKIP_RULE_MISMATCH,
    SKIP_TARGET_MISSING,
    SKIP_UNREPRESENTABLE,
    SKIP_ENCODING,
)

KIND = "input_path"
TOKEN_TTL_S = 600.0

Error = scriptbackup.ScriptEditError


@dataclasses.dataclass
class Plan:
    edits: list[dict]
    skipped: list[dict]
    new_bytes: bytes
    before_sha: str
    after_sha: str
    rows: list[dict]
    encoding: str

    def public(self) -> dict:
        return {
            "edits": [
                {k: e[k] for k in ("line", "before", "after", "value_before", "value_after")}
                for e in self.edits
            ],
            "skipped": self.skipped,
            "rows": self.rows,
            "encoding": self.encoding,
        }


# ---------------------------------------------------------------- 源码的字节视图


_NEWLINE_RE = re.compile(r"\r\n|\r|\n")


class _Source:
    """脚本字节 ↔ 文本 ↔ 行（与 Python 解析器同一种分行：`\\r\\n` / `\\r` / `\\n`）。

    解码按 PEP 263（`tokenize.detect_encoding`，含 BOM）；**解码再编码必须逐字节等于原字节**，否则拒绝
    （那样的文件改一处就可能改了别处的字节）。
    """

    def __init__(self, data: bytes):
        try:
            enc, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
        except SyntaxError as exc:
            raise Error(ERROR_UNREADABLE, f"脚本的编码声明认不出：{exc}") from exc
        self.bom = b""
        body = data
        if enc == "utf-8-sig":
            enc = "utf-8"
            if data.startswith(b"\xef\xbb\xbf"):
                self.bom, body = data[:3], data[3:]
        self.encoding = enc
        try:
            self.text = body.decode(enc)
        except (UnicodeDecodeError, LookupError) as exc:
            raise Error(ERROR_UNREADABLE, f"脚本按 {enc} 解不开：{exc}") from exc
        if self.text.encode(enc) != body:
            raise Error(ERROR_UNREADABLE, f"脚本按 {enc} 解码后回不到原字节")
        self.data = data
        self.lines: list[str] = []
        pos = 0
        for m in _NEWLINE_RE.finditer(self.text):
            self.lines.append(self.text[pos : m.end()])
            pos = m.end()
        self.lines.append(self.text[pos:])
        self.line_start: list[int] = []
        off = len(self.bom)
        for ln in self.lines:
            self.line_start.append(off)
            off += len(ln.encode(enc))

    def span(self, node: ast.AST) -> tuple[int, int, int] | None:
        """单行节点 → `(文件里的字节起点, 字节终点, 行内字符列)`；跨行回 None。

        `ast` 的列是**这一行按 UTF-8 编码**的字节偏移，与文件自己的编码无关——先换回字符列再按文件编码量。
        """
        if node.lineno != node.end_lineno or node.lineno > len(self.lines):
            return None
        line = self.lines[node.lineno - 1]
        u8 = line.encode("utf-8")
        c0 = len(u8[: node.col_offset].decode("utf-8"))
        c1 = len(u8[: node.end_col_offset].decode("utf-8"))
        start = self.line_start[node.lineno - 1]
        b0 = start + len(line[:c0].encode(self.encoding))
        b1 = start + len(line[:c1].encode(self.encoding))
        return b0, b1, c0


# ---------------------------------------------------------------- 候选


def _parents(tree: ast.AST) -> dict[int, ast.AST]:
    out: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            out[id(child)] = node
    return out


def _path_context(node: ast.Constant, parents: dict[int, ast.AST]) -> bool:
    """这个常量是不是**在当路径用**的位置上（§二.3）。正面列举；不认得的一律不是。

    先沿父节点一路走到语句为止：途中是存图 / 写出 / 写模式打开 / 建目录的调用（`inputremap.is_write_call`，
    与「只认读取调用」同一套判据），它就是输出路径的一部分——`fig.savefig(Path("/old/data") / "out.png")`
    里的 `/old/data` 直接父节点是 `Path(...)`，只看一层会把输出目的地也改掉（Codex 评 #730 P2）。"""
    up = parents.get(id(node))
    while up is not None and not isinstance(up, ast.stmt):
        if isinstance(up, ast.Call) and inputremap.is_write_call(up):
            return False
        up = parents.get(id(up))
    parent = parents.get(id(node))
    if isinstance(parent, ast.keyword):
        parent = parents.get(id(parent))
        return isinstance(parent, ast.Call) and not _is_save_call(parent)
    if isinstance(parent, ast.Call):
        return node in parent.args and not _is_save_call(parent)
    if isinstance(parent, (ast.Assign, ast.AnnAssign, ast.Return)):
        return parent.value is node
    if isinstance(parent, ast.arguments):
        return True  # 参数默认值
    if isinstance(parent, ast.BinOp):
        return isinstance(parent.op, (ast.Div, ast.Add))
    if isinstance(parent, (ast.List, ast.Tuple, ast.Set)):
        return True
    if isinstance(parent, ast.Dict):
        return any(v is node for v in parent.values)
    return False


def _is_save_call(call: ast.Call) -> bool:
    return inputremap.is_write_call(call)


def _literal_form(segment: str) -> tuple[str, str] | None:
    """源码里的那一段 → `(前缀, 引号)`；不是**恰好一个**单行普通字符串 token 回 None。"""
    try:
        toks = [
            t
            for t in tokenize.generate_tokens(io.StringIO(segment).readline)
            if t.type not in (tokenize.NEWLINE, tokenize.NL, tokenize.ENDMARKER)
        ]
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return None
    if len(toks) != 1 or toks[0].type != tokenize.STRING or toks[0].string != segment:
        return None
    m = re.match(r"^([A-Za-z]*)('''|\"\"\"|'|\")", segment)
    if m is None or len(m.group(2)) == 3:
        return None
    return m.group(1), m.group(2)


def _render(prefix: str, quote: str, value: str) -> str | None:
    """新值 → 同一前缀、同一引号的字面量；表示不了回 None。"""
    if any(ord(ch) < 32 or ord(ch) == 0x7F for ch in value):
        return None
    if "r" in prefix.lower():
        if quote in value or value.endswith("\\"):
            return None
        return f"{prefix}{quote}{value}{quote}"
    return prefix + quote + value.replace("\\", "\\\\").replace(quote, "\\" + quote) + quote


def _prefix_of(value_parts, missing_parts) -> bool:
    absolute, parts = value_parts
    m_abs, m_parts = missing_parts
    return absolute == m_abs and bool(parts) and m_parts[: len(parts)] == parts


def _exists_as_written(value: str, script_dir: Path, root: Path) -> bool:
    """脚本写的这串此刻在不在（相对的看脚本目录与项目根，两处都不在才算不在）。"""
    try:
        if figcapture.remap_parts(value) is None:
            return True  # 说不清的不碰
        if os.path.isabs(value) or re.match(r"^[A-Za-z]:[\\/]", value):
            return inputremap.location_exists(value)
        return inputremap.location_exists(str(script_dir / value)) or inputremap.location_exists(
            str(root / value)
        )
    except (OSError, ValueError):
        return True


def plan(
    data: bytes,
    *,
    rule: dict,
    missing: list[str],
    script_dir: Path,
    root: Path,
) -> Plan:
    """按一条规则改写脚本里对得上缺失路径的字符串常量。一处都改不了抛 `script_edit_nothing_to_change`。"""
    src = _Source(data)
    try:
        tree = ast.parse(src.text)
    except (SyntaxError, ValueError) as exc:
        raise Error(ERROR_UNREADABLE, f"脚本有语法错误，不改：{exc}") from exc
    parents = _parents(tree)
    wanted = [(m, figcapture.remap_parts(m)) for m in missing]
    wanted = [(m, p) for m, p in wanted if p is not None and p[1]]
    # 规则只换得了与它 `from` 同一侧（相对 / 绝对）的串；另一侧的与这次指认无关，不报
    rule_from = figcapture.remap_parts(rule.get("from") or "") if rule.get("from") else (False, ())
    rule_absolute = bool(rule_from and rule_from[0])
    edits: list[dict] = []
    skipped: list[dict] = []
    seen: set[int] = set()

    missing_set = {m for m, _p in wanted}

    def skip(node: ast.AST, value: str, reason: str) -> None:
        # 只报「看得出是路径」的：整串就是缺的那条，或带分隔符。`"data"` 这种单词多半是键 / 标签，
        # 它恰好是 `data/x.csv` 的前缀段不值得一行「没改」
        if value in missing_set or "/" in value.replace("\\", "/"):
            skipped.append({"line": node.lineno, "value": value, "reason": reason})

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in seen:
            continue
        seen.add(id(node))
        value = node.value
        vparts = figcapture.remap_parts(value)
        if vparts is None or not vparts[1]:
            continue
        matched = [m for m, p in wanted if _prefix_of(vparts, p)]
        if not matched or vparts[0] != rule_absolute:
            continue
        if _exists_as_written(value, script_dir, root):
            continue  # 存在的路径一个都不改（缺的是更深的那一段）
        if isinstance(parents.get(id(node)), ast.JoinedStr):
            skip(node, value, SKIP_FSTRING)
            continue
        if not _path_context(node, parents):
            skip(node, value, SKIP_CONTEXT)
            continue
        span = src.span(node)
        if span is None:
            skip(node, value, SKIP_MULTILINE)
            continue
        b0, b1, col = span
        segment = data[b0:b1].decode(src.encoding)
        form = _literal_form(segment)
        if form is None:
            triple = segment.lstrip("rRuUbBfF")[:3] in ("'''", '"""')
            skip(node, value, SKIP_MULTILINE if triple else SKIP_CONCATENATED)
            continue
        new_value = figcapture.remap_target([rule], value, whole=True)
        if new_value is None:
            skip(node, value, SKIP_RULE_MISMATCH)
            continue
        new_value = new_value.replace("\\", "/")
        if not any(
            inputremap.location_exists(
                (figcapture.remap_target([rule], m, whole=True) or "").replace("\\", "/")
            )
            for m in matched
        ):
            skip(node, value, SKIP_TARGET_MISSING)
            continue
        literal = _render(form[0], form[1], new_value)
        if literal is None:
            skip(node, value, SKIP_UNREPRESENTABLE)
            continue
        try:
            new_seg = literal.encode(src.encoding)
        except UnicodeEncodeError:
            skip(node, value, SKIP_ENCODING)
            continue
        edits.append(
            {
                "line": node.lineno,
                "col": col,
                "start": b0,
                "end": b1,
                "before": segment,
                "after": literal,
                "after_bytes": new_seg,
                "value_before": value,
                "value_after": new_value,
            }
        )
    if not edits:
        raise Error(
            ERROR_NOTHING_TO_CHANGE,
            "脚本里没有能安全改写的路径常量",
            skipped=skipped,
        )
    edits.sort(key=lambda e: e["start"])
    new = _apply(data, [(e["start"], e["end"], e["after_bytes"]) for e in edits])
    self_check(data, new, edits)
    for e in edits:
        e.pop("after_bytes")
    return Plan(
        edits=edits,
        skipped=skipped,
        new_bytes=new,
        before_sha=scriptbackup.sha256(data),
        after_sha=scriptbackup.sha256(new),
        rows=_rows(src, _Source(new), {e["line"] for e in edits}),
        encoding=src.encoding,
    )


def _apply(data: bytes, spans: list[tuple[int, int, bytes]]) -> bytes:
    out = bytearray()
    pos = 0
    for b0, b1, repl in sorted(spans):
        out += data[pos:b0] + repl
        pos = b1
    out += data[pos:]
    return bytes(out)


def _rows(old: _Source, new: _Source, lines: set[int]) -> list[dict]:
    """逐行 diff（后端生成、前端只渲染）：改过的每一行的前后两版，不带行尾。"""
    return [
        {
            "line": n,
            "before": old.lines[n - 1].rstrip("\r\n"),
            "after": new.lines[n - 1].rstrip("\r\n"),
        }
        for n in sorted(lines)
    ]


# ---------------------------------------------------------------- 自检（§四）


def _str_constants(tree: ast.AST) -> list[ast.Constant]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def self_check(old: bytes, new: bytes, edits: list[dict]) -> None:
    """写之前证明「只换了这几串」。任一不过抛 `script_edit_self_check_failed`。

    1. 新字节能解析；2. 新旧 AST 把被换的常量值对调回去后 `ast.dump` 逐字相等；3. 每处替换的字节区间
    之外逐字节相等；（4. 目标存在，在 `plan` 里逐条判过。）
    """
    try:
        new_src = _Source(new)
        new_tree = ast.parse(new_src.text)
        old_tree = ast.parse(_Source(old).text)
    except (Error, SyntaxError, ValueError) as exc:
        raise Error(ERROR_SELF_CHECK, f"改写后的脚本解析不了：{exc}") from exc
    olds = _str_constants(old_tree)
    news = _str_constants(new_tree)
    if len(olds) != len(news):
        raise Error(ERROR_SELF_CHECK, "改写后字符串常量的个数变了")
    pairs = {(e["value_before"], e["value_after"]) for e in edits}
    changed = 0
    for o, n in zip(olds, news, strict=True):
        if o.value != n.value:
            if (o.value, n.value) not in pairs:
                raise Error(ERROR_SELF_CHECK, f"改到了不该改的常量：{o.value!r}")
            n.value = o.value
            changed += 1
    if changed != len(edits) or ast.dump(old_tree) != ast.dump(new_tree):
        raise Error(ERROR_SELF_CHECK, "改写后除了这几处路径还有别的变化")
    pos_old = pos_new = 0
    for e in sorted(edits, key=lambda e: e["start"]):
        gap = e["start"] - pos_old
        if old[pos_old : e["start"]] != new[pos_new : pos_new + gap]:
            raise Error(ERROR_SELF_CHECK, f"第 {e['line']} 行之前的字节变了")
        pos_new += gap + len(e["after"].encode(new_src.encoding))
        pos_old = e["end"]
    if old[pos_old:] != new[pos_new:]:
        raise Error(ERROR_SELF_CHECK, "最后一处改动之后的字节变了")


# ---------------------------------------------------------------- 复原（§七）


def undo(current: bytes, edits: list[dict]) -> bytes:
    """「只撤销这几处路径」：每处 `after` 字面量还在原来那一行的原来那一列、或全文唯一一处，才换回 `before`。

    任何一处找不到 / 不唯一 → `script_restore_conflict`（界面只剩「整份恢复」）。换完照样过自检。
    """
    src = _Source(current)
    spans: list[tuple[int, int, bytes]] = []
    back: list[dict] = []
    for e in edits:
        after, before = e.get("after"), e.get("before")
        if not isinstance(after, str) or not isinstance(before, str):
            raise Error(ERROR_RESTORE_CONFLICT, "备份记录里缺改动明细")
        hit = None
        line = e.get("line")
        if isinstance(line, int) and 0 < line <= len(src.lines):
            text = src.lines[line - 1]
            col = e.get("col")
            if isinstance(col, int):
                # `col` 是改写**前**那一行里的列：同一行更靠前的几处换了长度，这一处在改后的行里跟着挪
                col += sum(
                    len(o["after"]) - len(o["before"])
                    for o in edits
                    if o is not e
                    and o.get("line") == line
                    and isinstance(o.get("col"), int)
                    and o["col"] < e["col"]
                    and isinstance(o.get("after"), str)
                    and isinstance(o.get("before"), str)
                )
            if isinstance(col, int) and text[col : col + len(after)] == after:
                start = src.line_start[line - 1] + len(text[:col].encode(src.encoding))
                hit = start
        if hit is None:
            found = [m.start() for m in re.finditer(re.escape(after), src.text)]
            if len(found) != 1:
                raise Error(
                    ERROR_RESTORE_CONFLICT,
                    f"第 {line} 行那处路径已经被改过，没法只撤销它",
                    line=line,
                )
            hit = len(src.bom) + len(src.text[: found[0]].encode(src.encoding))
        end = hit + len(after.encode(src.encoding))
        spans.append((hit, end, before.encode(src.encoding)))
        back.append(
            {
                "line": line,
                "start": hit,
                "end": end,
                "after": before,
                "value_before": e.get("value_after"),
                "value_after": e.get("value_before"),
            }
        )
    if len({s[0] for s in spans}) != len(spans):
        raise Error(ERROR_RESTORE_CONFLICT, "几处改动落到了同一个位置")
    new = _apply(current, spans)
    self_check(current, new, back)
    return new


# ---------------------------------------------------------------- 确认令牌（§六）


class TokenStore:
    """预览签发、提交核销的一次性令牌（Flask 进程内存）。绑定会话、写前 / 写后 sha256；单次、限时。"""

    def __init__(self, ttl: float = TOKEN_TTL_S):
        self.ttl = ttl
        self._items: dict[str, dict] = {}
        self._lock = threading.Lock()

    def issue(self, *, binding: str, **fields) -> str:
        token = secrets.token_urlsafe(24)
        now = time.monotonic()
        with self._lock:
            for k in [k for k, v in self._items.items() if v["expires"] < now]:
                del self._items[k]
            self._items[token] = {"binding": binding, "expires": now + self.ttl, **fields}
        return token

    def redeem(self, token: str, *, binding: str) -> dict:
        """核销（无论成败都作废）；会话对不上、过期、不存在一律 `script_edit_token_invalid`。"""
        with self._lock:
            item = self._items.pop(token, None) if isinstance(token, str) else None
        if (
            item is None
            or item["expires"] < time.monotonic()
            or not secrets.compare_digest(item["binding"], binding)
        ):
            raise Error(ERROR_TOKEN_INVALID, "确认已失效，请重新预览")
        return item


TOKENS = TokenStore()
