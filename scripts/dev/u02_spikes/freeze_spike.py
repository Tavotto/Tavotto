"""U02 render_spike 的**最小候选 freeze**：PyInstaller 把「父进程 + render child」冻成一个 onedir 产物，
验证两件事——native 资源（PDFium 的共享库）与数据资源（批准字体清单里的字体文件）在冻结产物里
找得到，以及冻结的 exe 能以 `--render-child` **再起自己**当 child 并真渲染一张 PNG。

    PYTHONPATH=scripts:src <spike venv>/bin/python -m dev.u02_spikes.freeze_spike \\
        --fonts <字体目录> --pdf <evidence spike.pdf> --out <evidence dir>/freeze

它**不是** `packaging/tavotto.spec`（那是产品的配方，本模块一个字不改）——这里只回答
「候选 native 栅格库 + 字体数据 + child 自起」这三件事能不能过 PyInstaller，给 U07 / U10 的
真正打包留证据。冻结产物本身不进 git（几十 MB），只记 hash 与运行结果。

同一个文件既是构建驱动（`main()`），也是被冻结的入口（`frozen_entry()`）：
    Tavotto-u02-freeze[.exe]                      → 父进程：起 child 渲染，打印 JSON 结果
    Tavotto-u02-freeze[.exe] --render-child …     → child：`render_child.child_main()`
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

# Windows 上 stdout / stderr 被重定向成管道时会退回系统区域编码（cp1252 / cp936），
# 第一句中文就 UnicodeEncodeError；两条流都钉成 UTF-8（tests/test_windows_regressions.py 看护）。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def _resource_root() -> Path:
    """冻结产物里的数据根（`_MEIPASS`）；源码树里就是本包目录。产品里这条判断的唯一出处是
    `engine/runtime.py`，spike 只做最小的等价物。"""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def frozen_entry(argv: list[str]) -> int:
    from dev.u02_spikes import render_child as rc

    if argv[:1] == ["--render-child"]:
        return rc.child_main(argv[1:])
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--width", type=int, default=400)
    args = ap.parse_args(argv)
    root = _resource_root()
    fonts_dir = root / "u02_fonts"
    fonts = sorted(p.name for p in fonts_dir.rglob("*") if p.suffix.lower() in (".ttf", ".otf"))
    licenses = sorted(str(p.relative_to(fonts_dir)) for p in fonts_dir.rglob("LICENSE"))
    # PDFium 的共享库：pypdfium2 的 hook 把它放在 pypdfium2_raw/ 下
    libs = sorted(
        str(p.relative_to(root))
        for p in root.rglob("*pdfium*")
        if p.suffix in (".dylib", ".so", ".dll")
    )
    client = rc.RenderChildClient([sys.executable, "--render-child"], default_timeout=120)
    try:
        t0 = time.perf_counter()
        ping = client.ping()
        resp = client.render(args.pdf, args.out, args.width)
        result = {
            "ok": True,
            "frozen": bool(getattr(sys, "frozen", False)),
            "executable": sys.executable,
            "resource_root": str(root),
            "fonts_bundled": fonts,
            "font_licenses_bundled": licenses,
            "pdfium_libraries": libs,
            "child_pid": ping["pid"],
            "child_memory_limit": ping["memory_limit"],
            "render": {k: resp[k] for k in ("width", "height", "ms")},
            "png_sha256": hashlib.sha256(args.out.read_bytes()).hexdigest(),
            "seconds": round(time.perf_counter() - t0, 2),
        }
    except rc.RenderChildError as exc:
        result = {"ok": False, "error": {"code": exc.code, "message": exc.message}}
    finally:
        client.close()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


def build(fonts: Path, out: Path, workdir: Path) -> Path:
    """跑 PyInstaller；返回冻结 exe 路径。"""
    entry = workdir / "u02_freeze_entry.py"
    entry.write_text(
        "import sys\nfrom dev.u02_spikes.freeze_spike import frozen_entry\nsys.exit(frozen_entry(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    scripts_dir = Path(__file__).resolve().parents[2]
    sep = ";" if os.name == "nt" else ":"
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        "Tavotto-u02-freeze",
        "--distpath",
        str(workdir / "dist"),
        "--workpath",
        str(workdir / "build"),
        "--specpath",
        str(workdir),
        "--paths",
        str(scripts_dir),
        "--hidden-import",
        "dev.u02_spikes.render_child",
        "--collect-binaries",
        "pypdfium2_raw",
        "--add-data",
        f"{fonts}{sep}u02_fonts",
        str(entry),
    ]
    log = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(workdir)
    )
    (out / "pyinstaller.log").write_text(
        log.stdout + "\n--- stderr ---\n" + log.stderr, encoding="utf-8"
    )
    if log.returncode != 0:
        raise RuntimeError(f"PyInstaller 退出码 {log.returncode}，日志见 {out / 'pyinstaller.log'}")
    exe = (
        workdir
        / "dist"
        / "Tavotto-u02-freeze"
        / ("Tavotto-u02-freeze.exe" if os.name == "nt" else "Tavotto-u02-freeze")
    )
    if not exe.is_file():
        raise RuntimeError(f"冻结产物不在预期位置：{exe}")
    return exe


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fonts", required=True, type=Path)
    ap.add_argument("--pdf", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="u02-freeze-"))
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
        exe = build(args.fonts, args.out, workdir)
        report["build_seconds"] = round(time.perf_counter() - t0, 1)
        dist = exe.parent
        size = sum(p.stat().st_size for p in dist.rglob("*") if p.is_file())
        report["dist"] = {
            "files": sum(1 for p in dist.rglob("*") if p.is_file()),
            "bytes": size,
            "exe_sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
        }
        # 在一个干净的环境里跑冻结产物：没有 PYTHONPATH、没有 spike venv 的 site-packages
        png = workdir / "frozen-render.png"
        env = {
            k: v
            for k, v in os.environ.items()
            if k in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE")
        }
        run = subprocess.run(
            [str(exe), "--pdf", str(args.pdf), "--out", str(png)],
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
            ("child_is_the_same_exe", result.get("child_pid") is not None),
            ("pdfium_library_bundled", bool(result.get("pdfium_libraries"))),
            ("fonts_bundled_13", len(result.get("fonts_bundled", [])) == 13),
            ("font_licenses_bundled_2", len(result.get("font_licenses_bundled", [])) == 2),
            ("png_rendered", png.is_file() and result.get("render", {}).get("width") == 400),
        ]
        report["checks"] = [{"check": c, "ok": ok} for c, ok in checks]
        report["all_ok"] = all(ok for _, ok in checks)
        if png.is_file():
            shutil.copyfile(png, args.out / f"frozen-render-{target}.png")
    except Exception as exc:  # 构建失败也要留报告
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
        + f" → {args.out / f'report-{target}.json'}"
        + (f"  ({report['error']})" if "error" in report else "")
    )
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
