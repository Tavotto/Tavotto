#!/usr/bin/env python3
"""U07 的**最小候选 freeze**：PyInstaller 把「父进程 + 产品里的 render child」冻成一个 onedir 产物，验证三件事——
PDFium 的共享库（pypdfium2_raw）、pikepdf 的 native 库、批准字体在冻结产物里找得到；冻结的 exe 以
`--render-child` **再起自己**当 child（`renderchild.child_argv()` 在 frozen 下的形状，RC-049：不重复弹 GUI、
不用 `-m`）并真 probe / 真渲染；产物在干净环境（没有 PYTHONPATH / site-packages）里跑。

    PYTHONPATH=src <rc-venv>/bin/python scripts/dev/u07_freeze_child.py \\
        --pdf tests/fixtures/foundation/pdf_png_assets/page.pdf --out <evidence dir>/freeze

它**不是** `packaging/tavotto.spec`（那是产品的配方，一个字不改）——最终签名 / 公证与产品打包归 U10 / U11。
U02 的 `freeze_spike.py` 收编于此（那份随 spike 退役）。冻结产物本身不进 git，只记 hash 与运行结果。

同一个文件既是构建驱动（`main()`），也是被冻结的入口（`frozen_entry()`）：
    Tavotto-u07-freeze[.exe]                      → 父进程：起 child、ping / probe / render，打印 JSON 结果
    Tavotto-u07-freeze[.exe] --render-child …     → child：`renderchild.main()`
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]


def frozen_entry(argv: list[str]) -> int:
    if argv[:1] == ["--render-child"]:
        from tavotto.rendercore import renderchild

        return renderchild.child_main(argv[1:])
    from tavotto.rendercore import fonts, raster, renderchild, renderhost

    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--width", type=int, default=400)
    args = ap.parse_args(argv)
    root = Path(getattr(sys, "_MEIPASS", ROOT / "src"))
    libs = sorted(
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix in (".dylib", ".so", ".dll", ".pyd")
        and ("pdfium" in p.name or "qpdf" in p.name.lower() or "pikepdf" in str(p))
    )
    reg = fonts.FontRegistry.discover()
    host = renderhost.RenderHost(default_timeout=120)
    try:
        t0 = time.perf_counter()
        ping = host.ping()
        probe = host.probe(args.pdf)
        buf = host.render(
            args.pdf, width_px=args.width, page_size_pt=(probe["width_pt"], probe["height_pt"])
        )
        args.out.write_bytes(raster.encode_png(buf))
        result = {
            "ok": True,
            "frozen": bool(getattr(sys, "frozen", False)),
            "executable": sys.executable,
            "child_argv": renderchild.child_argv(),
            "resource_root": str(root),
            "fonts_found": len(reg.faces),
            "fonts_missing": list(reg.missing),
            "native_libraries": libs,
            "child_pid": ping["pid"],
            "child_frozen": ping["frozen"],
            "child_memory_limit": ping["memory_limit"],
            "pdfium": ping["pdfium"],
            "probe": {k: probe[k] for k in ("pages", "width_pt", "height_pt", "user_unit")},
            "render": {"width": buf.width, "height": buf.height, "channels": buf.channels},
            "png_sha256": hashlib.sha256(args.out.read_bytes()).hexdigest(),
            "seconds": round(time.perf_counter() - t0, 2),
        }
    except renderhost.RenderChildError as exc:
        result = {"ok": False, "error": {"code": exc.code, "message": exc.message}}
    finally:
        host.close()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


def build(out: Path, workdir: Path) -> Path:
    entry = workdir / "u07_freeze_entry.py"
    entry.write_text(
        "import sys\nfrom dev.u07_freeze_child import frozen_entry\nsys.exit(frozen_entry(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    sep = ";" if os.name == "nt" else ":"
    fonts_dir = ROOT / "src" / "tavotto" / "resources" / "fonts"
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        "Tavotto-u07-freeze",
        "--distpath",
        str(workdir / "dist"),
        "--workpath",
        str(workdir / "build"),
        "--specpath",
        str(workdir),
        "--paths",
        str(ROOT / "scripts"),
        "--paths",
        str(ROOT / "src"),
        "--hidden-import",
        "tavotto.rendercore.renderchild",
        "--hidden-import",
        "tavotto.rendercore.renderhost",
        "--collect-binaries",
        "pypdfium2_raw",
        "--collect-all",
        "pikepdf",
        "--add-data",
        f"{fonts_dir}{sep}tavotto/resources/fonts",
        "--add-data",
        f"{ROOT / 'src' / 'tavotto' / 'rendercore' / 'fonts_allowlist.json'}{sep}tavotto/rendercore",
        str(entry),
    ]
    log = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(workdir)
    )
    (out / "pyinstaller-log.txt").write_text(
        log.stdout + "\n--- stderr ---\n" + log.stderr, encoding="utf-8"
    )
    if log.returncode != 0:
        raise RuntimeError(
            f"PyInstaller 退出码 {log.returncode}，日志见 {out / 'pyinstaller-log.txt'}"
        )
    exe = (
        workdir
        / "dist"
        / "Tavotto-u07-freeze"
        / ("Tavotto-u07-freeze.exe" if os.name == "nt" else "Tavotto-u07-freeze")
    )
    if not exe.is_file():
        raise RuntimeError(f"冻结产物不在预期位置：{exe}")
    return exe


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pdf", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="u07-freeze-"))
    target = f"{platform.system().lower()}-{platform.machine().lower()}"
    report: dict = {
        "platform": {
            "python": platform.python_version(),
            "system": platform.system(),
            "machine": platform.machine(),
        }
    }
    try:
        import PyInstaller

        report["pyinstaller"] = PyInstaller.__version__
        t0 = time.perf_counter()
        exe = build(args.out, workdir)
        report["build_seconds"] = round(time.perf_counter() - t0, 1)
        dist = exe.parent
        report["dist"] = {
            "files": sum(1 for p in dist.rglob("*") if p.is_file()),
            "bytes": sum(p.stat().st_size for p in dist.rglob("*") if p.is_file()),
            "exe_sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
        }
        png = workdir / "frozen-render.png"
        env = {
            k: v
            for k, v in os.environ.items()
            if k in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE")
        }
        run = subprocess.run(
            [str(exe), "--pdf", str(args.pdf.resolve()), "--out", str(png)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=300,
        )
        report["run"] = {
            "returncode": run.returncode,
            "stdout": run.stdout.strip()[-2000:],
            "stderr": run.stderr.strip()[-1500:],
        }
        result = (
            json.loads(run.stdout.strip().splitlines()[-1]) if run.stdout.strip() else {"ok": False}
        )
        report["result"] = result
        checks = [
            ("frozen_exe_runs", run.returncode == 0 and result.get("ok") is True),
            ("frozen_flag_set", result.get("frozen") is True),
            (
                "child_is_the_same_exe_with_render_child_flag",
                result.get("child_argv", [None])[-1:] == ["--render-child"]
                and result.get("child_frozen") is True,
            ),
            (
                "pdfium_library_bundled",
                any("pdfium" in lib for lib in result.get("native_libraries", [])),
            ),
            (
                "fonts_bundled_13",
                result.get("fonts_found") == 13 and not result.get("fonts_missing"),
            ),
            (
                "probe_reports_the_cropbox_size",
                result.get("probe", {}).get("width_pt") == 270.0
                and result.get("probe", {}).get("height_pt") == 160.0,
            ),
            ("png_rendered", png.is_file() and result.get("render", {}).get("width") == 400),
        ]
        report["checks"] = [{"check": c, "ok": ok} for c, ok in checks]
        report["all_ok"] = all(ok for _, ok in checks)
        if png.is_file():
            shutil.copyfile(png, args.out / f"frozen-render-{target}.png")
    except Exception as exc:  # noqa: BLE001 —— 构建失败也要留报告
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["all_ok"] = False
    finally:
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)
        else:
            report["workdir"] = str(workdir)
    (args.out / f"report-{target}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    for c in report.get("checks", []):
        print(("PASS " if c["ok"] else "FAIL ") + c["check"])
    print(
        ("ALL OK" if report["all_ok"] else "FAILED")
        + f" {sum(c['ok'] for c in report.get('checks', []))}/{len(report.get('checks', []))}"
    )
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
