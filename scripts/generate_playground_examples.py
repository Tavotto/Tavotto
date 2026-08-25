#!/usr/bin/env python3
"""生成 playground 案例的封面——封面必须来自真实源码的真实执行。

    python scripts/generate_playground_examples.py           # 生成封面 + manifest
    python scripts/generate_playground_examples.py --check   # 只校验，不生成

输入：web/src/playground/examples/*.py（案例源码的唯一真源）。
输出（进 git，前端直接 import）：

    web/src/playground/generated/
      kinetics.webp / calibration.webp / spectrum.webp
      examples-manifest.json      # 每个案例：源码 sha256 + 封面尺寸

纪律：

* **封面只用于案例卡片的首屏展示**。用户启动案例时仍然把 .py 源码交给
  Pyodide 真实执行（ADR 0007：不许用预烤产物代替执行）。
* 执行环境必须与浏览器端钉死的 matplotlib 同版本
  （packaging/playground-runtime.json 的 packages.matplotlib）——封面要是
  另一个版本画的，卡片上的图和用户点进去看到的就是两张图。
* 每个案例在**隔离临时目录**里以非交互 backend 执行，脚本写出的 PDF 留在
  临时目录里即弃——生成过程不碰仓库、不联网。
* manifest 记的是**源码**的 sha256：`--check` 抓「改了 .py 却没重新生成
  封面」；封面字节本身不进哈希（跨机器字体渲染有微小差异，按字节比对
  会制造假红）。空白封面（像素方差为零）当场失败。

CI / 测试挂在 `--check` 上（web/src/playground/examples.test.ts 也会
读同一份 manifest 比对源码哈希）。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import runpy
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "web" / "src" / "playground" / "examples"
GENERATED = ROOT / "web" / "src" / "playground" / "generated"
RUNTIME_LOCK = ROOT / "packaging" / "playground-runtime.json"
MANIFEST = GENERATED / "examples-manifest.json"

#: 卡片封面的渲染 dpi：3.4in 宽的 figure 出 ~750px，两倍屏上的卡片够用。
COVER_DPI = 220
WEBP_QUALITY = 90


def source_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def example_sources() -> list[Path]:
    files = sorted(EXAMPLES.glob("*.py"))
    if not files:
        raise SystemExit(f"没有案例源码（{EXAMPLES} 下没有 .py）")
    return files


def _require_pinned_matplotlib() -> None:
    lock = json.loads(RUNTIME_LOCK.read_text(encoding="utf-8"))
    pinned = lock["packages"]["matplotlib"]
    import matplotlib

    if matplotlib.__version__ != pinned:
        raise SystemExit(
            f"封面必须用钉死的 matplotlib {pinned} 生成（当前解释器是 "
            f"{matplotlib.__version__}）。换一个装了该版本的解释器再跑。"
        )


def render_cover(script: Path) -> tuple[bytes, int, int]:
    """在隔离临时目录里真实执行案例脚本，返回 (webp 字节, 宽, 高)。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from PIL import Image

    plt.close("all")
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="tavotto-example-") as tmp:
        # 脚本自己的 savefig 落在临时目录里，跑完即弃——生成过程不碰仓库
        os.chdir(tmp)
        try:
            runpy.run_path(str(script), run_name="__main__")
        finally:
            os.chdir(cwd)

    figs = [plt.figure(n) for n in plt.get_fignums()]
    if len(figs) != 1:
        raise SystemExit(f"{script.name} 产出了 {len(figs)} 张图，案例必须恰好一张")

    buf = io.BytesIO()
    figs[0].savefig(buf, format="png", dpi=COVER_DPI)
    plt.close("all")
    buf.seek(0)
    img = Image.open(buf).convert("RGB")

    # 空白封面当场失败：一张全是同色像素的图不是封面，是事故
    extrema = img.convert("L").getextrema()
    if extrema[0] == extrema[1]:
        raise SystemExit(f"{script.name} 的封面是一张空白图（像素无差异）")

    out = io.BytesIO()
    img.save(out, format="WEBP", quality=WEBP_QUALITY, method=6)
    return out.getvalue(), img.width, img.height


def generate() -> int:
    _require_pinned_matplotlib()
    GENERATED.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict] = {}
    for script in example_sources():
        stem = script.stem
        webp, width, height = render_cover(script)
        (GENERATED / f"{stem}.webp").write_bytes(webp)
        manifest[stem] = {
            "sourceSha256": source_sha256(script),
            "width": width,
            "height": height,
        }
        print(f"{stem}.webp  {width}×{height}  {len(webp) / 1024:.0f} KiB")

    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"已写入 {MANIFEST.relative_to(ROOT)}（{len(manifest)} 个案例）")
    return 0


def check() -> int:
    if not MANIFEST.is_file():
        print(f"没有封面 manifest（{MANIFEST} 不存在）。"
              f"先跑 python scripts/generate_playground_examples.py", file=sys.stderr)
        return 1
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    ok = True
    sources = {p.stem: p for p in example_sources()}

    for stem, path in sources.items():
        entry = manifest.get(stem)
        if entry is None:
            print(f"{stem}.py 没有对应的封面条目——重新生成", file=sys.stderr)
            ok = False
            continue
        if entry.get("sourceSha256") != source_sha256(path):
            print(f"{stem}.py 已改动但封面没有重新生成"
                  f"（manifest 里的源码哈希已过期）", file=sys.stderr)
            ok = False
        cover = GENERATED / f"{stem}.webp"
        if not cover.is_file() or cover.stat().st_size == 0:
            print(f"封面缺失或为空：{cover.relative_to(ROOT)}", file=sys.stderr)
            ok = False
    for stem in manifest:
        if stem not in sources:
            print(f"manifest 里的 {stem} 已经没有对应源码——重新生成", file=sys.stderr)
            ok = False

    if ok:
        print(f"案例封面与源码一致（{len(sources)} 个案例）")
        return 0
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="只校验封面与源码是否一致，不生成")
    args = ap.parse_args(argv)
    return check() if args.check else generate()


if __name__ == "__main__":
    raise SystemExit(main())
