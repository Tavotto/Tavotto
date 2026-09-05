#!/usr/bin/env python3
"""构建 Codex 内嵌画布（MCP App UI）的单文件 HTML。

    python scripts/build_mcp_widget.py                       # 构建并写入本地插件目录（开发态）
    python scripts/build_mcp_widget.py --out build/canvas.html   # 写到显式位置（CI staging 用）
    python scripts/build_mcp_widget.py --check [--out PATH]  # 校验：0 一致 / 1 过期 / 2 还没构建
    python scripts/build_mcp_widget.py --fingerprint         # 只打印源码指纹

默认产物位置 `codex-plugin/mcp/widget/canvas.html`——那是 MCP server 加载画布的
既有位置（`tavotto_mcp/widget.py` 的默认路径，`TAVOTTO_MCP_WIDGET` 可覆盖）。
本地开发：构建一次，`python codex-plugin/mcp/server.py` 或指向工作副本的
marketplace 装出来的插件就带上了它。**它是构建产物，不进版本库**（ADR 0043）；
用户装到的画布来自发行分支 `plugin-stable`，由 CI 从固定源码状态构建、验证、
发布（`scripts/plugin_stage.py` / `scripts/plugin_publish.py`）。

为什么要单文件：MCP 资源的内容就是一段 HTML 文本，host 把它直接塞进 iframe——
外链的 JS/CSS 没有可寻址的来源，`_meta.ui.csp` 声明的 `resourceDomains` 我们也
刻意留空（这块画布不发任何跨源请求，与后端的往来全部走 `tools/call`）。

画布的源码是 `web/src/mcp/`，它 import 的是 Tavotto 前端**同一份**
`canvas/` + stores + types——拖拽、命中测试、吸附、undo、patch 状态没有第二份实现。

三条写盘纪律：

* vite 的输出落在**临时目录**，不在 `web/` 下留下中间产物；
* 最终 HTML 先写同目录临时文件、成功后 `os.replace` 换上——构建失败时旧产物
  原样不动，也**不会**把旧产物报成「刚构建成功」；
* 构建结束不改任何 tracked 源码。

纯标准库。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePath

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
OUT = ROOT / "codex-plugin" / "mcp" / "widget" / "canvas.html"
#: 产物开头的指纹注释：`--check` 靠它判断「源码改了但没重新构建」
STAMP = "<!-- tavotto-mcp-widget "

#: `web/src/**` 之外**也参与**编译的输入。漏一个的表现是「改了它、产物指纹没变、
#: 用户装到旧画布、零报错」——所以这张表宁可宽一点：
#:   * 锁文件：依赖升级会改产物字节；
#:   * tsconfig 三份：`tsc -b` 与 vite 都读；
#:   * 本脚本自己：内联规则变了产物也变；
#:   * 规范 JSON 与字形覆盖表：经路径别名整份进 bundle。
EXTRA_INPUTS = (
    "web/mcp.html",
    "web/vite.mcp.config.ts",
    "web/package.json",
    "web/pnpm-lock.yaml",
    "web/pnpm-workspace.yaml",
    "web/tsconfig.json",
    "web/tsconfig.app.json",
    "web/tsconfig.node.json",
    "scripts/build_mcp_widget.py",
    "src/tavotto/profiles/publication.json",
    "src/tavotto/pdfbackend/canvas_coverage.json",
)


def _force_utf8() -> None:
    """把自己的 stdout/stderr 钉成 UTF-8。

    输出里全是中文，而被 subprocess 捕获（pytest 就是这么调的）或重定向时，
    Windows 上 stdout 会退回系统区域编码 cp1252/cp936——第一次 print 就
    UnicodeEncodeError 打死进程，调用方看到的是「脚本挂了」而不是那行结论。
    同 `codex-plugin/skills/tavotto-figure/scripts/handoff.py` 的 `_force_utf8()`。
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def _pnpm() -> list[str]:
    for name in ("pnpm", "npx"):
        found = shutil.which(name)
        if found:
            return [found] if name == "pnpm" else [found, "vite"]
    raise SystemExit("没找到 pnpm 或 npx —— 构建前端需要 Node 工具链")


def _entry(rel: PurePath, data: bytes) -> tuple[bytes, bytes]:
    r"""一条参与指纹的记录：(相对路径, 文件内容)。

    **路径统一成 POSIX 分隔符、行尾统一成 LF**，两者缺一不可：

    * `str(Path("web/src/a.ts"))` 在 Windows 上是 `web\src\a.ts`；
    * GitHub 的 Windows runner 默认 `core.autocrlf=true`，检出的文本文件是 CRLF。

    （docstring 用 raw string：这里有反斜杠，普通字符串在 3.12+ 会发
    SyntaxWarning，而这个脚本是被捕获着调用的，warning 会混进它的输出里。）
    """
    return rel.as_posix().encode("utf-8"), data.replace(b"\r\n", b"\n")


def digest(items) -> str:
    """一组 (相对路径, 内容) → 指纹。**与遍历顺序无关，与平台无关。**

    排序放在这里而不是交给调用方：`sorted(Path)` 在 Windows 上比的是
    **小写化**后的字符串（大小写不敏感），POSIX 上是原字符串——同一棵目录树
    在两个平台上会给出不同的遍历顺序，于是指纹不同，这个门禁在 Windows 腿上
    永远是红的。规范化之后按 bytes 排，两边必然一致。
    """
    h = hashlib.sha256()
    for rel, data in sorted(_entry(rel, data) for rel, data in items):
        h.update(rel)
        h.update(data)
    return h.hexdigest()[:16]


def _is_test_file(p: Path) -> bool:
    return (
        ".test." in p.name or "__tests__" in p.parts or p.name.endswith((".spec.ts", ".spec.tsx"))
    )


def source_inputs() -> list[Path]:
    """参与画布编译的全部输入文件（存在的那些）。

    `web/src/**` 下**所有**普通文件都算（.ts/.tsx/.css 之外还有 locale JSON、
    示例 .py、示例 .webp、生成的 resources.d.ts——它们都经 import 进 bundle），
    只剔掉测试文件；再加 `EXTRA_INPUTS`。**不假设「扫了 TS/TSX/CSS 就覆盖全部依赖」。**
    """
    files: list[Path] = []
    for p in (WEB / "src").rglob("*"):
        if p.is_file() and not _is_test_file(p) and "node_modules" not in p.parts:
            files.append(p)
    files += [ROOT / rel for rel in EXTRA_INPUTS]
    return [p for p in files if p.is_file()]


def source_fingerprint() -> str:
    """画布源码的指纹。收集顺序无所谓——排序与规范化都在 `digest()` 里。"""
    return digest((p.relative_to(ROOT), p.read_bytes()) for p in source_inputs())


def build() -> str:
    """跑 vite（输出进临时目录），把 JS/CSS 内联成一份 HTML 文本并打上指纹戳。"""
    tool = _pnpm()
    fingerprint = source_fingerprint()  # 构建**之前**算：构建不改源码，之后算也一样
    with tempfile.TemporaryDirectory(prefix="tavotto-mcp-dist-") as dist_str:
        dist = Path(dist_str)
        cmd = (
            [*tool, "exec", "vite", "build", "--config", "vite.mcp.config.ts"]
            if tool[0].endswith("pnpm")
            else [*tool, "build", "--config", "vite.mcp.config.ts"]
        )
        cmd += ["--outDir", str(dist), "--emptyOutDir"]
        proc = subprocess.run(cmd, cwd=WEB, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise SystemExit(f"vite build 失败（退出码 {proc.returncode}）")

        html = (dist / "mcp.html").read_text(encoding="utf-8")
        js = (dist / "canvas.js").read_text(encoding="utf-8")
        css_path = dist / "canvas.css"
        css = css_path.read_text(encoding="utf-8") if css_path.is_file() else ""

    # `</script>` 出现在 JS 字符串里会提前关掉标签（比如某段代码里带着它）。
    # 转义成 `<\/script>` 在 JS 里等价，在 HTML 解析器眼里则不再是结束标签。
    js = js.replace("</script>", r"<\/script>")

    # 替换文本必须走 lambda：`re.sub` 会解释替换串里的反斜杠转义，
    # 而打包出来的 JS 里到处是 `\u`、`\d`——直接当模板传进去当场 PatternError
    html = re.sub(
        r'<script[^>]*src="[^"]*canvas\.js"[^>]*>\s*</script>',
        lambda _m: f'<script type="module">{js}</script>',
        html,
    )
    html = re.sub(
        r'<link[^>]*href="[^"]*canvas\.css"[^>]*>', lambda _m: f"<style>{css}</style>", html
    )
    if 'canvas.js"' in html[:4096] or 'canvas.css"' in html[:4096]:
        raise SystemExit("内联失败：产物里还留着外链引用（vite 的输出形状变了？）")
    if '<div id="root">' not in html:
        raise SystemExit('内联失败：产物里没有 <div id="root">')

    return f"{STAMP}{fingerprint} -->\n" + html


def write_output(html: str, out: Path) -> None:
    """先写同目录临时文件再 `os.replace`：失败不留半个文件，旧产物原样不动。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(html, encoding="utf-8", newline="\n")
        os.replace(tmp, out)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def current_fingerprint(out: Path = OUT) -> str | None:
    """产物里那枚指纹；**文件不在与指纹读不出来都回 `None`**。

    两种 `None` 的**处置不同**，所以判「在不在」要另问 `out.is_file()`，别拿这个
    返回值当代理：一份存在但被截断、或早于打戳那一版的产物，指纹读不出来——那是
    「过期」（重建一次就好），不是「还没构建」。
    """
    if not out.is_file():
        return None
    with out.open("rb") as fh:
        head = fh.read(200).decode("utf-8", "replace")
    m = re.match(re.escape(STAMP) + r"([0-9a-f]+) -->", head)
    return m.group(1) if m else None


def check(out: Path, *, as_json: bool) -> int:
    """三档，不是两档：0 一致 / 1 过期 / 2 产物不存在。

    「产物不存在」与「产物过期」处置不同：刚 clone 下来还没构建过的人看到「过期」
    会去找自己改坏了什么；而在发布链上「不存在」意味着打出去的插件没有画布。
    调用方按退出码分流，不靠读那句中文。**「在不在」问文件，不问指纹。**
    """
    want = source_fingerprint()
    have = current_fingerprint(out)
    missing = not out.is_file()
    ok = (not missing) and have is not None and have == want
    status = "ok" if ok else ("missing" if missing else "stale")
    report = {"ok": ok, "status": status, "expected": want, "found": have, "path": str(out)}
    if as_json:
        print(json.dumps(report, ensure_ascii=False), file=sys.stdout if ok else sys.stderr)
    elif ok:
        print(f"画布产物与源码一致（{want}）")
    elif missing:
        print(
            f"画布产物还没构建：{out} 不存在。跑一次 python scripts/build_mcp_widget.py",
            file=sys.stderr,
        )
    else:
        found = have if have is not None else "读不出指纹（截断或旧格式）"
        print(
            f"画布产物过期：源码指纹 {want}，产物里是 {found}。"
            f"跑一次 python scripts/build_mcp_widget.py",
            file=sys.stderr,
        )
    return 0 if ok else (2 if missing else 1)


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--check", action="store_true", help="只校验产物是否与源码同步（0/1/2 三档），不构建"
    )
    ap.add_argument("--fingerprint", action="store_true", help="只打印源码指纹")
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="产物位置（默认 codex-plugin/mcp/widget/canvas.html，MCP server 加载画布的既有位置）",
    )
    ap.add_argument("--json", action="store_true", help="输出机器可读结果")
    args = ap.parse_args(argv)
    out = args.out if args.out is not None else OUT

    if args.fingerprint:
        print(source_fingerprint())
        return 0
    if args.check:
        return check(out, as_json=args.json)

    html = build()
    write_output(html, out)
    size = out.stat().st_size
    fingerprint = current_fingerprint(out)
    report = {"ok": True, "path": str(out), "bytes": size, "fingerprint": fingerprint}
    print(
        json.dumps(report, ensure_ascii=False)
        if args.json
        else f"已写入 {out}（{size / 1024:.0f} KiB，指纹 {fingerprint}）"
    )
    return 0


if __name__ == "__main__":
    os.environ.setdefault("NODE_ENV", "production")
    raise SystemExit(main())
