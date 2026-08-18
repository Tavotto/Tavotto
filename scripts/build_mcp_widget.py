#!/usr/bin/env python3
"""构建 Codex 内嵌画布（MCP App UI）的单文件 HTML。

    python scripts/build_mcp_widget.py            # 构建并写入插件目录
    python scripts/build_mcp_widget.py --check    # 只校验产物是否与源码同步

产物：`codex-plugin/mcp/widget/canvas.html`（**进 git**）。

为什么要单文件、为什么要提交进仓库：

* MCP 资源的内容就是一段 HTML 文本，host 把它直接塞进 iframe——外链的
  JS/CSS 没有可寻址的来源，`_meta.ui.csp` 声明的 `resourceDomains` 我们也刻意
  留空（这块画布不发任何跨源请求，与后端的往来全部走 `tools/call`）；
* 插件是从**仓库本体**分发的（`.agents/plugins/marketplace.json` 指向
  `./codex-plugin`）。产物不进 git，用户装完插件就只有一个空目录——
  MCP server 会如实降级成「没有 UI，五个工具照常可用」，但那不是我们想要的
  默认状态。所以它和 `src-tauri/windows/installer.nsi` 一样是**受管的产物**：
  由脚本生成，改前端后必须重跑（`--check` 在 CI 里看着）。

画布的源码是 `web/src/mcp/`，它 import 的是 Magplot 前端**同一份**
`canvas/` + stores + types——拖拽、命中测试、吸附、undo、patch 状态没有第二份实现。

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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DIST = WEB / "dist-mcp"
OUT = ROOT / "codex-plugin" / "mcp" / "widget" / "canvas.html"
#: 产物开头的指纹注释：`--check` 靠它判断「源码改了但没重新构建」
STAMP = "<!-- magplot-mcp-widget "


def _force_utf8() -> None:
    """把自己的 stdout/stderr 钉成 UTF-8。

    输出里全是中文，而被 subprocess 捕获（pytest 就是这么调的）或重定向时，
    Windows 上 stdout 会退回系统区域编码 cp1252/cp936——第一次 print 就
    UnicodeEncodeError 打死进程，调用方看到的是「脚本挂了」而不是那行结论。
    同 `codex-plugin/skills/magplot-figure/scripts/handoff.py` 的 `_force_utf8()`。
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


def source_fingerprint() -> str:
    """画布源码的指纹：`web/src/**` + 规范文件 + 构建配置。

    只盯这几处：改了它们而没重新构建，用户装到的画布就是旧的。
    """
    h = hashlib.sha256()
    files: list[Path] = []
    for base in (WEB / "src",):
        for p in sorted(base.rglob("*")):
            if p.is_file() and p.suffix in (".ts", ".tsx", ".css") and ".test." not in p.name:
                files.append(p)
    files += [WEB / "mcp.html", WEB / "vite.mcp.config.ts", WEB / "package.json",
              ROOT / "src" / "magplot" / "profiles" / "publication.json"]
    for p in files:
        if not p.is_file():
            continue
        h.update(str(p.relative_to(ROOT)).encode("utf-8"))
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def build() -> str:
    """跑 vite，把 JS/CSS 内联成一份 HTML 文本。"""
    cmd = [*_pnpm(), "exec", "vite", "build", "--config", "vite.mcp.config.ts"] \
        if _pnpm()[0].endswith("pnpm") else [*_pnpm(), "build", "--config", "vite.mcp.config.ts"]
    proc = subprocess.run(cmd, cwd=WEB, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SystemExit(f"vite build 失败（退出码 {proc.returncode}）")

    html = (DIST / "mcp.html").read_text(encoding="utf-8")
    js = (DIST / "canvas.js").read_text(encoding="utf-8")
    css_path = DIST / "canvas.css"
    css = css_path.read_text(encoding="utf-8") if css_path.is_file() else ""

    # `</script>` 出现在 JS 字符串里会提前关掉标签（比如某段代码里带着它）。
    # 转义成 `<\/script>` 在 JS 里等价，在 HTML 解析器眼里则不再是结束标签。
    js = js.replace("</script>", r"<\/script>")

    # 替换文本必须走 lambda：`re.sub` 会解释替换串里的反斜杠转义，
    # 而打包出来的 JS 里到处是 `\u`、`\d`——直接当模板传进去当场 PatternError
    html = re.sub(r'<script[^>]*src="[^"]*canvas\.js"[^>]*>\s*</script>',
                  lambda _m: f'<script type="module">{js}</script>', html)
    html = re.sub(r'<link[^>]*href="[^"]*canvas\.css"[^>]*>',
                  lambda _m: f"<style>{css}</style>", html)
    if "canvas.js" in html or "canvas.css" in html:
        raise SystemExit("内联失败：产物里还留着外链引用（vite 的输出形状变了？）")

    stamp = f"{STAMP}{source_fingerprint()} -->\n"
    return stamp + html


def current_fingerprint() -> str | None:
    if not OUT.is_file():
        return None
    head = OUT.read_text(encoding="utf-8")[:200]
    m = re.match(re.escape(STAMP) + r"([0-9a-f]+) -->", head)
    return m.group(1) if m else None


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="只校验产物是否与源码同步（CI 用），不构建")
    ap.add_argument("--json", action="store_true", help="输出机器可读结果")
    args = ap.parse_args(argv)

    want = source_fingerprint()
    have = current_fingerprint()
    if args.check:
        ok = have == want
        report = {"ok": ok, "expected": want, "found": have, "path": str(OUT)}
        print(json.dumps(report, ensure_ascii=False) if args.json else
              (f"画布产物与源码一致（{want}）" if ok else
               f"画布产物过期：源码指纹 {want}，产物里是 {have}。"
               f"跑一次 python scripts/build_mcp_widget.py"),
              file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    html = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    size = OUT.stat().st_size
    report = {"ok": True, "path": str(OUT), "bytes": size, "fingerprint": want}
    print(json.dumps(report, ensure_ascii=False) if args.json else
          f"已写入 {OUT}（{size / 1024:.0f} KiB，指纹 {want}）")
    # dist-mcp 是中间产物，留着只会让人以为它是发布物
    shutil.rmtree(DIST, ignore_errors=True)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("NODE_ENV", "production")
    raise SystemExit(main())
