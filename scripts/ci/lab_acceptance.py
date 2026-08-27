#!/usr/bin/env python3
"""候选包验收：拿 **build job 产出的那一份 wheel**，装进干净环境跑完整用户路径。

这条门禁的全部价值在「exact bytes」四个字上。**绝不在这里重新 build 一个
wheel 再测**——那样测的是「同一个 commit 能造出一个能用的包」，而发出去的是
另一次构建的产物。本仓库 release.yml 的既有原则是 build once / test exact
artifact / publish exact artifact，这里只是把 test 那一环加深。

结构断言（快，先跑）：
    import tavotto → __version__ → 包内 web/index.html → engine/worker.py
    → console script → `--help` 在没有 Flask 的路径上也能跑

行为验收（慢，后跑）：直接调用既有的 `scripts/smoke_app.py`，走
    启动 → /api/version → 渲染环境自检 → 打开项目 → 渲染 → 热渲染
    → 导出 → 覆盖导出 → 干净退出（并断言无残留 worker）
**刻意不另写一套启动/渲染协议**：smoke_app 就是用户真实路径的那一份，
再造一份 fake protocol 只会让两边慢慢跑偏，而跑偏的那天没人会发现。

用法：
    python scripts/ci/lab_acceptance.py --dist dist/
    python scripts/ci/lab_acceptance.py --dist dist/ --skip-smoke   # 只做结构断言
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    CiError,
    ensure_layout,
    run_metadata,
    summary,
    summary_table,
    write_report,
)

REPO = Path(__file__).resolve().parents[2]
DEFAULT_FIGURES = REPO / "examples" / "figures"


def find_wheel(dist: Path) -> Path:
    """dist/ 里的唯一 wheel。多于一个就报错——「随便挑一个」在发行链上不可接受。"""
    wheels = sorted(dist.glob("*.whl"))
    if not wheels:
        raise CiError("no_wheel", f"{dist} 里没有 wheel；lab gate 必须消费 build job 的产物")
    if len(wheels) > 1:
        raise CiError("ambiguous_wheel",
                      f"{dist} 里有 {len(wheels)} 个 wheel：{[w.name for w in wheels]}。"
                      "候选包必须唯一，否则验收的和发布的可能不是同一个")
    return wheels[0]


def sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_venv(where: Path) -> Path:
    """全新 venv。返回解释器路径。"""
    subprocess.run([sys.executable, "-m", "venv", str(where)], check=True,
                   capture_output=True, timeout=300)
    py = where / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
    if not py.exists():
        raise CiError("venv_failed", f"建完 venv 但找不到解释器：{py}")
    return py


def pip_install(py: Path, args: list[str], timeout: int = 1800) -> None:
    out = subprocess.run([str(py), "-m", "pip", "install", "--disable-pip-version-check", *args],
                         capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise CiError("pip_install_failed",
                      f"pip install {' '.join(args)} 失败：\n{out.stdout[-2000:]}\n{out.stderr[-2000:]}")


# --------------------------------------------------------------------------
def structural_checks(py: Path) -> list[tuple[str, bool, str]]:
    """装完之后包本身长得对不对。每条都直指一种真实发生过的坏法。"""
    results: list[tuple[str, bool, str]] = []

    probe = r"""
import json, sys
from pathlib import Path
out = {}
import tavotto
out["version"] = getattr(tavotto, "__version__", "")
root = Path(tavotto.__file__).parent
out["web_index"] = (root / "web" / "index.html").is_file()
out["worker"] = (root / "engine" / "worker.py").is_file()
out["patchspec"] = (root / "engine" / "patchspec.py").is_file()
out["profiles"] = (root / "profiles" / "publication.json").is_file()
out["root"] = str(root)
print(json.dumps(out))
"""
    r = subprocess.run([str(py), "-c", probe], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        results.append(("import tavotto", False, r.stderr.strip()[-400:]))
        return results
    info = json.loads(r.stdout.strip().splitlines()[-1])

    results.append(("import tavotto", True, f"版本 {info['version']}"))
    results.append(("版本号非空", bool(info["version"]), info["version"] or "(空)"))
    # 这一条是 CLAUDE.md 里点名的坑：hatchling 默认跳过 VCS 忽略的文件，
    # 前端产物必须靠 pyproject 的 artifacts 收回，漏了的话 wheel 里没有界面。
    results.append(("包内前端产物 web/index.html", info["web_index"],
                    "在" if info["web_index"] else "缺失 —— 装完首页会 404"))
    results.append(("worker 入口 engine/worker.py", info["worker"],
                    "在" if info["worker"] else "缺失 —— 一张图都渲染不出来"))
    results.append(("patch 规范化 engine/patchspec.py", info["patchspec"], "在" if info["patchspec"] else "缺失"))
    results.append(("出版规范 profiles/publication.json", info["profiles"],
                    "在" if info["profiles"] else "缺失 —— 预检规则随 wheel 分发，少了它 MCP 侧会崩"))
    return results


def cli_checks(py: Path) -> list[tuple[str, bool, str]]:
    """console script 与轻量子命令。

    `doctor` 那条尤其重要：它本该是「装坏了怎么查」的工具，所以必须在
    Flask / PyMuPDF import 失败时自己也能跑——CLAUDE.md 里专门有一条纪律。
    """
    results: list[tuple[str, bool, str]] = []
    bindir = py.parent
    exe = bindir / ("tavotto.exe" if os.name == "nt" else "tavotto")
    results.append(("console script 存在", exe.exists(), str(exe) if exe.exists() else "缺失"))
    if exe.exists():
        r = subprocess.run([str(exe), "--help"], capture_output=True, text=True, timeout=120)
        results.append(("tavotto --help", r.returncode == 0,
                        "ok" if r.returncode == 0 else r.stderr.strip()[-300:]))
    r = subprocess.run([str(py), "-m", "tavotto", "doctor", "--json"],
                       capture_output=True, text=True, timeout=180)
    ok = r.returncode == 0 and r.stdout.strip().startswith("{")
    results.append(("tavotto doctor --json", ok,
                    "输出合法 JSON" if ok else (r.stderr or r.stdout).strip()[-300:]))
    return results


def smoke(py: Path, figures: Path, workdir: Path) -> tuple[bool, str]:
    """跑既有的端到端冒烟。返回 (通过, 摘要)。"""
    cmd = [sys.executable, str(REPO / "scripts" / "smoke_app.py"),
           "--python", str(py), "--figures", str(figures), "--workdir", str(workdir)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=2400)
    tail = (r.stdout + r.stderr).strip().splitlines()
    return r.returncode == 0, "\n".join(tail[-25:])


# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="候选 wheel 验收（exact artifact）")
    ap.add_argument("--dist", default="dist", help="build job 下载下来的 dist 目录")
    ap.add_argument("--figures", default=str(DEFAULT_FIGURES))
    ap.add_argument("--skip-smoke", action="store_true", help="只做结构与 CLI 断言")
    ap.add_argument("--keep", action="store_true", help="保留一次性 venv 便于排查")
    args = ap.parse_args(argv)

    started = time.time()
    root = ensure_layout()
    try:
        wheel = find_wheel(Path(args.dist))
    except CiError as exc:
        print(f"::error::{exc.message}", file=sys.stderr)
        summary(f"\n> **候选包验收失败** `{exc.code}` — {exc.message}\n")
        return 1

    digest = sha256(wheel)
    print(f"候选 wheel: {wheel.name}\nsha256: {digest}\n大小: {wheel.stat().st_size} 字节")

    tmp_base = root / "tmp"
    tmp_base.mkdir(parents=True, exist_ok=True)
    venv_dir = Path(tempfile.mkdtemp(prefix="venv-acceptance-", dir=str(tmp_base)))
    work_dir = Path(tempfile.mkdtemp(prefix="artifact-acceptance-", dir=str(tmp_base)))

    rows: list[tuple[str, str, str]] = []
    checks: list[tuple[str, bool, str]] = []
    ok = True
    try:
        py = make_venv(venv_dir)
        # 装的是 wheel 本体，不是 `pip install tavotto`——后者会去 PyPI 拿
        # 一个**已经发布过**的版本，而我们要验的正是还没发布的这一份。
        pip_install(py, [str(wheel)])
        # 科学栈单独装：产品主依赖刻意不含它（Flask 父进程的依赖边界），
        # 但渲染路径要真跑起来就必须有。
        pip_install(py, ["matplotlib"])

        checks += structural_checks(py)
        checks += cli_checks(py)
        for name, good, detail in checks:
            rows.append((name, "✅" if good else "❌", detail))
            ok = ok and good

        if args.skip_smoke:
            rows.append(("端到端冒烟", "⏭️", "--skip-smoke"))
        elif not ok:
            # 结构就不对时没必要再等十几分钟的冒烟——先修结构。
            rows.append(("端到端冒烟", "⏭️", "结构断言已失败，跳过"))
        else:
            good, tail = smoke(py, Path(args.figures), work_dir)
            ok = ok and good
            rows.append(("端到端冒烟（smoke_app）", "✅" if good else "❌",
                         "启动/渲染/热渲染/导出/覆盖导出/干净退出" if good
                         else "见下方日志"))
            if not good:
                print("---- smoke_app 输出尾部 ----", file=sys.stderr)
                print(tail, file=sys.stderr)
                summary(f"\n<details><summary>smoke_app 失败输出</summary>\n\n```\n{tail}\n```\n</details>\n")
    except CiError as exc:
        ok = False
        rows.append((exc.code, "❌", exc.message))
        print(f"::error::{exc.message}", file=sys.stderr)
    except subprocess.SubprocessError as exc:
        ok = False
        rows.append(("子进程", "❌", str(exc)[:300]))
    finally:
        if not args.keep:
            import shutil
            for d in (venv_dir, work_dir):
                shutil.rmtree(d, ignore_errors=True)

    payload = {
        "ok": ok,
        "wheel": wheel.name,
        "sha256": digest,
        "size": wheel.stat().st_size,
        "checks": [{"name": n, "ok": g, "detail": d} for n, g, d in checks],
        "elapsed_s": round(time.time() - started, 1),
        "metadata": run_metadata(),
    }
    write_report("acceptance.json", payload)

    summary(f"\n### 候选包验收\n\n`{wheel.name}`  sha256 `{digest[:16]}…`\n\n"
            + summary_table(rows))
    print("\n候选包验收：" + ("通过" if ok else "失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
