#!/usr/bin/env python3
"""构建 Magplot 内置渲染 runtime（Windows 桌面版专用）。

产出一个自成一体的 CPython + 科学栈目录，跟着安装包一起发：

    runtime/
      python.exe  python313.dll  python313.zip  python313._pth
      Lib/site-packages/{numpy,matplotlib,pandas,scipy,seaborn,PIL,…}
      licenses/            第三方许可证与 NOTICE
      runtime-manifest.json 机器可读清单（engine/runtime.py 读它）

这样普通 Windows 用户装完 Magplot 就能渲染：不需要先装 Python、首次渲染不联网、
不依赖 PATH / Store Python / Conda，也**不碰用户已有的任何环境**。

为什么是官方 embeddable 发行版而不是再打一个 PyInstaller：
Python 官方就把 embeddable 定位成「应用私有的运行时，第三方包由安装程序一起
提供」（https://docs.python.org/3/using/windows.html#the-embeddable-package）。
worker 必须是**真解释器**跑 `engine/worker.py`——用户的图表脚本会 import 各种
东西，冻结成第二个黑盒立刻就动态 import 不进去了。

一切输入来自 `packaging/runtime-lock.json`：CPython 的下载地址 + SHA-256，
以及**完整传递闭包**的精确版本。脚本本身不做版本决策（`--resolve` 除外，
那是维护者更新锁文件时才跑的）。

用法：
    python scripts/build_worker_runtime.py                 # 按锁文件构建
    python scripts/build_worker_runtime.py --clean         # 先删旧产物
    python scripts/build_worker_runtime.py --resolve       # 更新锁文件（维护者）
    python scripts/build_worker_runtime.py --resolve --python-version 3.13.16
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
import time
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_LOCK = REPO / "packaging" / "runtime-lock.json"
DEFAULT_OUT = REPO / "runtime"
DEFAULT_CACHE = REPO / "build" / "runtime-cache"

MANIFEST_NAME = "runtime-manifest.json"
MANIFEST_SCHEMA = 1          # 与 engine/runtime.MANIFEST_SCHEMA 对齐
SITE_PACKAGES = ("Lib", "site-packages")

#: 分发包名 → import 名（只列对不上的）
IMPORT_NAMES = {"pillow": "PIL", "python-dateutil": "dateutil"}

#: 精确版本：PEP 440 的发布号 + 可选的 post/rc 等后缀。范围、latest、
#: 空串都要在构建**开始前**被挡下——发出去以后才发现两台机器装的不一样就晚了。
EXACT_VERSION = re.compile(r"^\d+(\.\d+)*((a|b|rc|\.post|\.dev)\d+)*$")


class BuildError(RuntimeError):
    pass


def log(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        # Windows 的控制台/管道默认 cp1252/cp936，编不出「↓」这类符号——
        # 一条日志绝不能打死整个构建
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(msg.encode(enc, "backslashreplace").decode(enc, "replace"), flush=True)


# ---------------------------------------------------------------------------
# 锁文件（纯函数，便于单测）
# ---------------------------------------------------------------------------
def load_lock(path: Path) -> dict:
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BuildError(f"读不到锁文件 {path}: {exc}") from exc
    except ValueError as exc:
        raise BuildError(f"锁文件不是合法 JSON {path}: {exc}") from exc
    validate_lock(lock)
    return lock


def validate_lock(lock: dict) -> None:
    """锁文件必须真的「锁住」——这是可复现构建的全部依据。"""
    if lock.get("schema") != MANIFEST_SCHEMA:
        raise BuildError(f"锁文件 schema={lock.get('schema')}，本脚本只认 "
                         f"{MANIFEST_SCHEMA}")
    py = lock.get("python") or {}
    for key in ("version", "url", "sha256", "stdlib_zip", "pth"):
        if not py.get(key):
            raise BuildError(f"锁文件 python.{key} 缺失")
    if not EXACT_VERSION.match(str(py["version"])):
        raise BuildError(f"python.version 必须是精确补丁版本，拿到 {py['version']}")
    if not str(py["version"]).startswith("3.13."):
        raise BuildError(f"内置 runtime 钉死 CPython 3.13.x，拿到 {py['version']}")
    if not re.fullmatch(r"[0-9a-f]{64}", str(py["sha256"] or "")):
        raise BuildError("python.sha256 必须是 64 位十六进制 SHA-256")
    if not str(py["url"]).startswith("https://www.python.org/ftp/python/"):
        raise BuildError("CPython 只从 python.org 官方下载点取，不接受其他来源")

    pkgs = lock.get("packages") or {}
    if not pkgs:
        raise BuildError("锁文件 packages 为空")
    for name, ver in pkgs.items():
        if not EXACT_VERSION.match(str(ver)):
            raise BuildError(f"{name} 的版本 {ver!r} 不是精确版本"
                             "（不允许范围 / latest / 空）")
    missing = [n for n in (lock.get("top_level") or []) if n not in pkgs]
    if missing:
        raise BuildError(f"top_level 里的 {missing} 不在 packages 闭包中")


def requirement_list(lock: dict) -> list[str]:
    """闭包 → `name==version` 列表（顺序稳定，便于 diff 与复现）。"""
    return [f"{n}=={v}" for n, v in sorted((lock.get("packages") or {}).items())]


def pth_lines(stdlib_zip: str) -> list[str]:
    """embeddable 的 `._pth` 内容。

    官方默认版本只有 `python313.zip` 和 `.`，不含 site-packages 也不跑
    `site.main()`——照抄的话装进去的 numpy 一个都 import 不到。两处都要改：
      * 加 `Lib\\site-packages`：第三方包的落点；
      * 放开 `import site`：让 site 处理 `.pth` 文件（部分包靠它注册路径）。
    """
    return [
        stdlib_zip,
        ".",
        "Lib\\site-packages",
        "",
        "# Magplot: 上面一行是内置科学栈的落点；下面这行让 site.main() 跑起来，",
        "# 否则 site-packages 里的 .pth 文件不会被处理。",
        "import site",
    ]


def manifest_dict(lock: dict, build_id: str, built_at: str,
                  lock_sha256: str, smoke: str) -> dict:
    """写进 runtime-manifest.json 的内容（engine/runtime.py 的输入）。"""
    py = lock["python"]
    plat = lock.get("platform") or {}
    return {
        "schema": MANIFEST_SCHEMA,
        "product": "Magplot",
        "kind": "windows-embeddable",
        "python": {
            "version": py["version"],
            "implementation": py.get("implementation", "cpython"),
            "source": py["url"],
            "sha256": py["sha256"],
        },
        "platform": {
            "os": plat.get("os", "windows"),
            "arch": plat.get("arch", "amd64"),
            "tag": f'{plat.get("pip_abi", "cp313")}-{plat.get("pip_platform", "win_amd64")}',
        },
        "top_level": list(lock.get("top_level") or []),
        "packages": dict(sorted((lock.get("packages") or {}).items())),
        "build": {
            "id": build_id,
            "built_at": built_at,
            "builder": "scripts/build_worker_runtime.py",
            "lock_sha256": lock_sha256,
            "smoke": smoke,
        },
    }


# ---------------------------------------------------------------------------
# 下载与校验
# ---------------------------------------------------------------------------
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, expect_sha: str) -> Path:
    """下载并校验 SHA-256；缓存命中且校验通过就直接用。

    校验失败必须**当场失败**，不能「重下一次算了」之后接着用——供应链上出问题
    的时候，安静地接受一个对不上的文件是最坏的选择。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        got = sha256_file(dest)
        if got == expect_sha:
            log(f"✓ 缓存命中 {dest.name}（sha256 已校验）")
            return dest
        log(f"! 缓存文件校验不符，重新下载：{dest.name}")
        dest.unlink()

    log(f"↓ {url}")
    tmp = dest.with_name(dest.name + ".part")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp, tmp.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise BuildError(f"下载失败 {url}: {exc}") from exc
    got = sha256_file(tmp)
    if got != expect_sha:
        tmp.unlink(missing_ok=True)
        raise BuildError(
            f"SHA-256 不符！\n  期望 {expect_sha}\n  实得 {got}\n"
            f"  来源 {url}\n拒绝继续构建。")
    tmp.replace(dest)
    log(f"✓ 已下载并校验 {dest.name}")
    return dest


# ---------------------------------------------------------------------------
# 构建
# ---------------------------------------------------------------------------
def extract_embeddable(zip_path: Path, out: Path, lock: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        need = {"python.exe", lock["python"]["stdlib_zip"], lock["python"]["pth"]}
        missing = need - set(names)
        if missing:
            raise BuildError(f"embeddable 压缩包里少了 {sorted(missing)}——"
                             "锁文件里的 stdlib_zip / pth 名字对不上这个版本？")
        zf.extractall(out)
    log(f"✓ 解压 embeddable 到 {out}")


def write_pth(out: Path, lock: dict) -> Path:
    pth = out / lock["python"]["pth"]
    pth.write_text("\n".join(pth_lines(lock["python"]["stdlib_zip"])) + "\n",
                   encoding="utf-8")
    log(f"✓ 写入 {pth.name}（site-packages + import site）")
    return pth


def install_packages(out: Path, lock: dict, pip_python: str) -> None:
    """把锁定的 wheel 装进 runtime 的 site-packages。

    用**宿主机**的 pip 加 `--platform/--python-version/--abi` 交叉安装：
    embeddable 发行版里没有 pip（官方就是这么设计的），而且这样在 Linux/macOS
    上也能构建出 Windows 的 runtime，CI 想换 runner 不用重写这一段。

    `--no-deps` 是有意的：锁文件里已经是完整闭包，交给 pip 再解析一次等于
    把「装什么」的决定权交还给网络，两次构建就可能不一样。
    """
    target = out.joinpath(*SITE_PACKAGES)
    target.mkdir(parents=True, exist_ok=True)
    plat = lock.get("platform") or {}
    reqs = requirement_list(lock)
    cmd = [
        pip_python, "-m", "pip", "install",
        "--no-deps",                       # 闭包已锁死，不允许 pip 再自行取舍
        "--only-binary=:all:",             # 不在构建机上编译 C 扩展
        "--disable-pip-version-check",
        "--no-input",
        "--platform", plat.get("pip_platform", "win_amd64"),
        "--python-version", plat.get("pip_python_version", "3.13"),
        "--implementation", plat.get("pip_implementation", "cp"),
        "--abi", plat.get("pip_abi", "cp313"),
        "--target", str(target),
        "--upgrade",
        *reqs,
    ]
    log(f"→ pip install {len(reqs)} 个锁定 wheel → {target}")
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise BuildError("pip 安装失败：\n" + (proc.stdout or "") + (proc.stderr or ""))
    log("✓ 科学栈已就位")


def verify_installed(out: Path, lock: dict) -> dict[str, str]:
    """按 .dist-info 核对装进去的版本与锁文件一致。

    pip 说成功不等于装对了：`--target` 遇到已有目录时的覆盖行为历来有坑，
    残留一个旧版本会让 manifest 撒谎。
    """
    target = out.joinpath(*SITE_PACKAGES)
    found: dict[str, str] = {}
    for info in target.glob("*.dist-info"):
        name, _, ver = info.name[: -len(".dist-info")].rpartition("-")
        if name:
            found[name.replace("_", "-").lower()] = ver
    problems = []
    for name, want in (lock.get("packages") or {}).items():
        got = found.get(name.replace("_", "-").lower())
        if got is None:
            problems.append(f"{name}: 没装上")
        elif got != want:
            problems.append(f"{name}: 锁 {want} 实得 {got}")
    if problems:
        raise BuildError("装出来的版本与锁文件不符：\n  " + "\n  ".join(problems))
    log(f"✓ 版本核对通过（{len(found)} 个 dist-info）")
    return found


#: 可以安全删掉的目录名。**只认这三个精确名字**——`numpy.testing` 与
#: `pandas._testing` 是公开 API（用户脚本里 `from numpy.testing import
#: assert_allclose` 很常见），按前缀匹配会把它们一起删掉。
PRUNE_DIRS = {"tests", "test", "__pycache__"}


def prune(out: Path) -> tuple[int, int]:
    """删掉科学栈自带的测试套件与字节码缓存。

    scipy + pandas 的 tests 占 85 MiB 上下——用户装 Magplot 是来画图的，
    不会去跑 scipy 的测试。删完仍然要过 Windows 上的 import + 真实渲染冒烟，
    所以这不是「赌它不影响」，是被验证过的。
    """
    site = out.joinpath(*SITE_PACKAGES)
    removed = freed = 0
    # 自底向上：先删深层的，避免删掉父目录后再去遍历它
    for path in sorted(site.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if not path.is_dir() or path.name not in PRUNE_DIRS:
            continue
        try:
            freed += sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
            shutil.rmtree(path)
            removed += 1
        except OSError:
            continue
    log(f"✓ 精简：删除 {removed} 个 tests/__pycache__ 目录，"
        f"省下 {freed / 1024 / 1024:.0f} MiB")
    return removed, freed


def precompile(out: Path) -> str:
    """把 site-packages 的 .py 预编译成 .pyc 随包发出。

    运行时 worker 是带 `-B` 起的（安装目录一个字节都不写，见
    engine/runtime.child_args）；`-B` 只禁止**写** .pyc，读现成的不受影响。
    所以这一步是 `-B` 的配套：没有它，每次冷启动都要重新编译 numpy /
    matplotlib / pandas 的几千个 .py。

    invalidation_mode 用 **UNCHECKED_HASH**：默认的时间戳模式依赖源文件的
    mtime，而安装程序解压、杀毒软件扫描都可能改动它——一旦对不上，.pyc 会被
    判为过期，`-B` 又不让重写，于是每次启动都白编译一遍。哈希模式与 mtime 无关。

    必须用 runtime **自己的** python.exe 编：.pyc 的魔数跟解释器版本走。
    """
    python = out / "python.exe"
    if os.name != "nt" or not python.is_file():
        log("! 跳过预编译（需要 Windows 宿主机）——冷启动会慢一些，功能不受影响")
        return "skipped:non-windows-host"
    site = out.joinpath(*SITE_PACKAGES)
    code = (
        "import compileall, py_compile, sys\n"
        "ok = compileall.compile_dir("
        f"    {str(site)!r}, quiet=2, force=True, workers=0,\n"
        "    invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)\n"
        # 个别第三方包会带上故意语法错误的样例文件，编不过很正常，
        # 所以不按 compile_dir 的返回值判成败——真正的判据是随后的 import 冒烟。
        "sys.stdout.write('done' if ok else 'partial')\n"
    )
    proc = subprocess.run([str(python), "-c", code], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          env={**os.environ, "PYTHONNOUSERSITE": "1"},
                          timeout=1800)
    if proc.returncode != 0:
        raise BuildError("预编译失败：\n" + (proc.stdout or "") + (proc.stderr or ""))
    n = sum(1 for _ in site.rglob("*.pyc"))
    log(f"✓ 预编译 {n} 个 .pyc（{proc.stdout.strip()}）——运行时可以带 -B 起，"
        "安装目录零写入")
    return f"unchecked-hash:{n}"


def collect_licenses(out: Path, lock: dict) -> int:
    """收齐第三方许可证 / NOTICE，随安装包一起发。

    AGPL 的项目分发别人的二进制，许可证义务是硬的：BSD/MIT/HPND 全都要求
    随分发附带版权声明。用户装到的是我们打的包，这份义务落在我们身上。
    """
    site = out.joinpath(*SITE_PACKAGES)
    lic_root = out / "licenses"
    if lic_root.exists():
        shutil.rmtree(lic_root)
    lic_root.mkdir(parents=True, exist_ok=True)

    index = ["# Magplot 内置渲染环境 · 第三方组件许可证",
             "",
             "本目录随 Magplot Windows 安装包一起分发。下列组件均为各自作者版权所有，",
             "以其原始许可证条款提供；完整许可证正文见各子目录。",
             ""]

    # CPython 自己的 LICENSE.txt 就在 embeddable 包根目录
    cpython_lic = out / "LICENSE.txt"
    if cpython_lic.is_file():
        dst = lic_root / "cpython"
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cpython_lic, dst / "LICENSE.txt")
        index.append(f"- **CPython {lock['python']['version']}** — PSF License "
                     f"(`licenses/cpython/LICENSE.txt`)")

    count = 1 if cpython_lic.is_file() else 0
    for info in sorted(site.glob("*.dist-info")):
        name, _, ver = info.name[: -len(".dist-info")].rpartition("-")
        dst = lic_root / name.lower()
        files = [p for p in info.rglob("*")
                 if p.is_file() and _looks_like_license(p.name)]
        # 没有单独许可证文件的包，METADATA 里的 License / Classifier 也算交代
        meta = info / "METADATA"
        if not files and meta.is_file():
            files = [meta]
        if not files:
            continue
        dst.mkdir(parents=True, exist_ok=True)
        for src in files:
            shutil.copy2(src, dst / src.name)
        index.append(f"- **{name} {ver}** — `licenses/{name.lower()}/`")
        count += 1

    (lic_root / "THIRD-PARTY-NOTICES.md").write_text(
        "\n".join(index) + "\n", encoding="utf-8")
    log(f"✓ 许可证收集完成（{count} 个组件）→ {lic_root}")
    return count


def _looks_like_license(name: str) -> bool:
    low = name.lower()
    return low.startswith(("license", "licence", "copying", "notice", "authors"))


def smoke_imports(out: Path, lock: dict) -> tuple[str, dict[str, str | None]]:
    """逐个 import 一遍并报版本——**构建的最后一道闸**。

    只看文件在不在没有意义：常见失败是 wheel 装错平台、缺 VC 运行库、
    `._pth` 写坏导致 site-packages 根本不在 sys.path 上。这些全都只有真跑一次
    才暴露，而暴露的地方必须是构建机，不是用户的电脑。
    """
    python = out / "python.exe"
    if os.name != "nt":
        return "skipped:non-windows-host", {}
    if not python.is_file():
        raise BuildError(f"没有 {python}")

    env = {**os.environ,
           # 构建产物要干净可复现：不往 runtime 里落 __pycache__
           "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONNOUSERSITE": "1",
           "MPLBACKEND": "Agg"}
    results: dict[str, str | None] = {}
    failed: list[str] = []
    for dist in lock.get("top_level") or []:
        mod = IMPORT_NAMES.get(dist.lower(), dist.replace("-", "_"))
        code = (f"import {mod} as m; "
                f"print(getattr(m, '__version__', 'unknown'))")
        proc = subprocess.run([str(python), "-c", code], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              env=env, timeout=300)
        if proc.returncode != 0:
            results[mod] = None
            failed.append(f"{mod}: {(proc.stderr or '').strip()[-400:]}")
            log(f"✗ import {mod} 失败")
        else:
            results[mod] = proc.stdout.strip()
            log(f"✓ import {mod} → {results[mod]}")

    # 再画一张真图：import 得进来不代表画得出来（字体缓存、后端、C 扩展）
    if not failed:
        code = (
            "import matplotlib; matplotlib.use('Agg');\n"
            "import matplotlib.pyplot as plt, numpy as np, pandas as pd\n"
            "import seaborn as sns, scipy.optimize as so\n"
            "df = pd.DataFrame({'x': np.arange(5.0), 'y': np.arange(5.0) ** 1.5})\n"
            "fig, ax = plt.subplots()\n"
            "sns.scatterplot(data=df, x='x', y='y', ax=ax)\n"
            "so.curve_fit(lambda t, a, b: a * t + b, df['x'].to_numpy(), "
            "df['y'].to_numpy())\n"
            "import io; buf = io.BytesIO(); fig.savefig(buf, format='pdf')\n"
            "assert buf.getbuffer().nbytes > 500\n"
            "print('render-ok')\n"
        )
        proc = subprocess.run([str(python), "-c", code], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              env=env, timeout=600)
        if proc.returncode != 0 or "render-ok" not in proc.stdout:
            failed.append("真实渲染: " + (proc.stderr or "").strip()[-800:])
        else:
            log("✓ 真实渲染（seaborn + scipy + PDF 输出）通过")

    if failed:
        raise BuildError("内置 runtime 冒烟失败：\n  " + "\n  ".join(failed))
    return "passed", results


# ---------------------------------------------------------------------------
# --resolve：更新锁文件（维护者用）
# ---------------------------------------------------------------------------
def resolve_lock(lock: dict, pip_python: str,
                 python_version: str | None) -> dict:
    """用 pip 真实解析出 cp313/win_amd64 的传递闭包，写回锁文件。

    不手写闭包的理由很实际：手写迟早漏一个传递依赖，而漏掉的那个会在用户机器上
    以 ModuleNotFoundError 的形式出现。
    """
    plat = lock.get("platform") or {}
    tops = lock.get("top_level") or []
    if not tops:
        raise BuildError("top_level 为空，没有可解析的顶层包")

    if python_version:
        if not EXACT_VERSION.match(python_version):
            raise BuildError(f"--python-version 要精确补丁版本，拿到 {python_version}")
        url = (f"https://www.python.org/ftp/python/{python_version}/"
               f"python-{python_version}-embed-amd64.zip")
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "embed.zip"
            log(f"↓ {url}")
            try:
                with urllib.request.urlopen(url, timeout=120) as resp, \
                        dest.open("wb") as fh:
                    shutil.copyfileobj(resp, fh)
            except OSError as exc:
                raise BuildError(f"下载失败 {url}: {exc}") from exc
            digest = sha256_file(dest)
            size = dest.stat().st_size
            with zipfile.ZipFile(dest) as zf:
                names = set(zf.namelist())
        major_minor = ".".join(python_version.split(".")[:2])
        tag = "python" + major_minor.replace(".", "")
        if f"{tag}.zip" not in names or f"{tag}._pth" not in names:
            raise BuildError(f"{url} 里没有 {tag}.zip / {tag}._pth")
        lock["python"].update(version=python_version, url=url, sha256=digest,
                              size=size, stdlib_zip=f"{tag}.zip",
                              pth=f"{tag}._pth")
        log(f"✓ CPython {python_version} sha256={digest}")

    with tempfile.TemporaryDirectory() as td:
        report = Path(td) / "report.json"
        cmd = [
            pip_python, "-m", "pip", "install", "--dry-run", "--quiet",
            "--report", str(report), "--disable-pip-version-check", "--no-input",
            "--only-binary=:all:",
            "--platform", plat.get("pip_platform", "win_amd64"),
            "--python-version", plat.get("pip_python_version", "3.13"),
            "--implementation", plat.get("pip_implementation", "cp"),
            "--abi", plat.get("pip_abi", "cp313"),
            "--target", str(Path(td) / "unused"),
            *[f"{n}=={lock['packages'][n]}" if n in lock.get("packages", {})
              else n for n in tops],
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise BuildError("解析失败：\n" + (proc.stdout or "") + (proc.stderr or ""))
        data = json.loads(report.read_text(encoding="utf-8"))

    closure = {}
    for item in data.get("install", []):
        meta = item.get("metadata") or {}
        closure[str(meta["name"]).lower()] = str(meta["version"])
    lock["packages"] = dict(sorted(closure.items()))
    validate_lock(lock)
    log(f"✓ 闭包解析完成：{len(closure)} 个包")
    return lock


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def build(lock: dict, out: Path, cache: Path, lock_sha: str,
          pip_python: str, allow_skip_smoke: bool, clean: bool,
          do_prune: bool = True) -> dict:
    if clean and out.exists():
        shutil.rmtree(out)
        log(f"✓ 已清理 {out}")
    if out.exists() and any(out.iterdir()):
        raise BuildError(f"{out} 非空——加 --clean 重建，或换 --out。"
                         "在旧产物上叠加会留下上一版的包。")

    zip_name = lock["python"]["url"].rsplit("/", 1)[-1]
    archive = download(lock["python"]["url"], cache / zip_name,
                       lock["python"]["sha256"])
    extract_embeddable(archive, out, lock)
    write_pth(out, lock)
    install_packages(out, lock, pip_python)
    verify_installed(out, lock)
    # 先收许可证再精简：许可证在 .dist-info 里，精简动不到它，但顺序写死更省心
    collect_licenses(out, lock)
    # 精简必须在预编译**之前**：反过来会把刚编好的 __pycache__ 又删掉
    pruned = prune(out)[0] if do_prune else 0
    compiled = precompile(out)

    smoke, versions = smoke_imports(out, lock)
    if smoke != "passed":
        if not allow_skip_smoke:
            raise BuildError(
                f"import 冒烟没跑（{smoke}）。内置 runtime 是 Windows 二进制，"
                "只有在 Windows 上才验得了；确实要在别的平台产出中间件时加 "
                "--allow-skip-smoke，但那份产物**不得直接发给用户**。")
        log(f"! import 冒烟跳过（{smoke}）——此产物未经运行时验证")

    built_at = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(int(os.environ.get("SOURCE_DATE_EPOCH") or time.time())))
    build_id = (os.environ.get("GITHUB_RUN_ID")
                or f"local-{lock_sha[:12]}")
    info = manifest_dict(lock, build_id, built_at, lock_sha, smoke)
    info["build"]["pruned_dirs"] = pruned
    info["build"]["precompiled"] = compiled
    if versions:
        info["build"]["imported"] = versions
    (out / MANIFEST_NAME).write_text(
        json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"✓ 写入 {MANIFEST_NAME}")

    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    log(f"\n完成：{out}\n  Python {info['python']['version']}"
        f"  包 {len(info['packages'])} 个  体积 {size / 1024 / 1024:.0f} MiB"
        f"  冒烟 {smoke}")
    return info


def main(argv: list[str] | None = None) -> int:
    # 与 app.py 同一手：stdout 不是真控制台时会退回系统区域编码（cp1252/cp936）
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lock", default=str(DEFAULT_LOCK))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--cache", default=str(DEFAULT_CACHE),
                    help="下载缓存目录（CI 可缓存以省下每次 11 MiB）")
    ap.add_argument("--pip-python", default=sys.executable,
                    help="用哪个解释器的 pip 做交叉安装（默认当前解释器）")
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--no-prune", dest="prune", action="store_false",
                    help="保留科学栈自带的 tests/（体积多 80 MiB 上下）")
    ap.add_argument("--allow-skip-smoke", action="store_true",
                    help="非 Windows 宿主机上允许跳过 import 冒烟（产物不得直接发布）")
    ap.add_argument("--resolve", action="store_true",
                    help="维护者模式：重新解析闭包并写回锁文件，不构建")
    ap.add_argument("--python-version", default=None,
                    help="配合 --resolve：换到指定 CPython 补丁版本并重算 sha256")
    args = ap.parse_args(argv)

    lock_path = Path(args.lock)
    try:
        if args.resolve:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            lock = resolve_lock(lock, args.pip_python, args.python_version)
            lock_path.write_text(
                json.dumps(lock, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
            log(f"✓ 已更新 {lock_path}")
            return 0

        lock = load_lock(lock_path)
        lock_sha = hashlib.sha256(
            lock_path.read_bytes()).hexdigest()
        build(lock, Path(args.out), Path(args.cache), lock_sha,
              args.pip_python, args.allow_skip_smoke, args.clean, args.prune)
    except BuildError as exc:
        print(f"::error::构建内置 runtime 失败: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
