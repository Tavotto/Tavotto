#!/usr/bin/env python3
"""构建 Tavotto 内置渲染 runtime（Windows / macOS 桌面版）。

产出一个自成一体的 CPython + 科学栈目录，跟着安装包一起发：

    runtime/                                  runtime/
      python.exe  python313.dll                 bin/python3  bin/python3.13
      python313.zip  python313._pth              lib/libpython3.13.dylib
      Lib/site-packages/{numpy,…}                lib/python3.13/site-packages/{numpy,…}
      licenses/                                  licenses/
      runtime-manifest.json                      runtime-manifest.json
      （windows-embeddable）                    （macos-standalone）

这样普通用户装完 Tavotto 就能渲染：不需要先装 Python、首次渲染不联网、
不依赖 PATH / Store Python / Homebrew / Conda，也**不碰用户已有的任何环境**。

两个平台的上游发行版不同，理由也不同：

* **Windows —— 官方 embeddable 发行版。** Python 官方就把它定位成「应用私有的
  运行时，第三方包由安装程序一起提供」
  （https://docs.python.org/3/using/windows.html#the-embeddable-package）。
* **macOS —— python-build-standalone（astral-sh）的 install_only 发行版。**
  官方 macOS 安装器装的是 `/Library/Frameworks/Python.framework` 下的固定路径，
  **不可重定位**，嵌不进 `.app`；Homebrew / Conda 是用户自己的环境，我们不碰。
  pbs 的构建 prefix 由解释器自身路径推导，挪到哪都能跑，而且是逐个可 codesign
  的普通 Mach-O——公证链要求每个嵌套二进制都签得到名。

**两边都不把 worker 冻结成黑盒**：worker 必须是**真解释器**跑 `engine/worker.py`，
用户的论文脚本要动态 import 各种东西，冻成第二个 PyInstaller 包立刻就 import 不进去。

一切输入来自 `packaging/runtime-lock.json`（schema 2，按目标分层）：CPython 的
下载地址 + SHA-256，以及**完整传递闭包**的精确版本。脚本本身不做版本决策
（`--resolve` 除外，那是维护者更新锁文件时才跑的）。

用法：
    python scripts/build_worker_runtime.py                    # 按本机平台构建
    python scripts/build_worker_runtime.py --target macos-arm64
    python scripts/build_worker_runtime.py --clean            # 先删旧产物
    python scripts/build_worker_runtime.py --list-targets
    # 维护者：更新锁文件
    python scripts/build_worker_runtime.py --resolve --target macos-arm64
    python scripts/build_worker_runtime.py --resolve --target windows-amd64 \
        --python-version 3.13.16
    python scripts/build_worker_runtime.py --resolve --target macos-arm64 \
        --pbs-release 20260814
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
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
#: 锁文件的 schema（schema 2 起按目标分层）
LOCK_SCHEMA = 2
#: 产物清单的 schema，**必须与 engine/runtime.MANIFEST_SCHEMA 对齐**
MANIFEST_SCHEMA = 2

KIND_WINDOWS = "windows-embeddable"
KIND_MACOS = "macos-standalone"

#: 每种 runtime 只认一个上游来源。写死前缀是供应链上的一道硬闸：
#: 锁文件被人改成从别处取一个「CPython」时，这里当场拒绝，而不是照单下载。
KIND_SOURCE_PREFIX = {
    KIND_WINDOWS: "https://www.python.org/ftp/python/",
    KIND_MACOS: "https://github.com/astral-sh/python-build-standalone/releases/download/",
}

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
    if lock.get("schema") != LOCK_SCHEMA:
        raise BuildError(
            f"锁文件 schema={lock.get('schema')}，本脚本只认 "
            f"{LOCK_SCHEMA}（schema 1 是按 Windows 单档平铺的旧格式）"
        )
    top = lock.get("top_level")
    if not isinstance(top, list) or not top:
        raise BuildError("锁文件 top_level 缺失或为空")
    targets = lock.get("targets")
    if not isinstance(targets, dict) or not targets:
        raise BuildError("锁文件 targets 缺失或为空")
    for name, target in targets.items():
        _validate_target(name, target, top)
    if not [n for n, t in targets.items() if t.get("shipped")]:
        raise BuildError("锁文件里一个 shipped=true 的目标都没有——那样发行链没有任何东西可发")


def _validate_target(name: str, target: dict, top_level: list[str]) -> None:
    if not isinstance(target, dict):
        raise BuildError(f"目标 {name} 不是对象")
    kind = target.get("kind")
    if kind not in KIND_SOURCE_PREFIX:
        raise BuildError(
            f"目标 {name} 的 kind={kind!r} 不认识（只认 {sorted(KIND_SOURCE_PREFIX)}）"
        )
    for key in ("os", "arch"):
        if not target.get(key):
            raise BuildError(f"目标 {name} 缺 {key}")

    py = target.get("python") or {}
    need = ["version", "url", "sha256"]
    if kind == KIND_WINDOWS:
        need += ["stdlib_zip", "pth"]
    else:
        need += ["archive_root", "triple"]
    for key in need:
        if not py.get(key):
            raise BuildError(f"目标 {name} 的 python.{key} 缺失")
    if not EXACT_VERSION.match(str(py["version"])):
        raise BuildError(f"{name}: python.version 必须是精确补丁版本，拿到 {py['version']}")
    if not str(py["version"]).startswith("3.13."):
        raise BuildError(f"{name}: 内置 runtime 钉死 CPython 3.13.x，拿到 {py['version']}")
    if not re.fullmatch(r"[0-9a-f]{64}", str(py["sha256"] or "")):
        raise BuildError(f"{name}: python.sha256 必须是 64 位十六进制 SHA-256")
    prefix = KIND_SOURCE_PREFIX[kind]
    if not str(py["url"]).startswith(prefix):
        raise BuildError(f"{name}: {kind} 的 CPython 只从 {prefix} 取，不接受其他来源")

    pip = target.get("pip") or {}
    if not isinstance(pip.get("platforms"), list) or not pip["platforms"]:
        raise BuildError(
            f"目标 {name} 的 pip.platforms 缺失——没有它 pip 会按**构建机**的平台装 wheel"
        )
    for key in ("python_version", "implementation", "abi"):
        if not pip.get(key):
            raise BuildError(f"目标 {name} 的 pip.{key} 缺失")

    pkgs = target.get("packages") or {}
    if not pkgs:
        raise BuildError(f"目标 {name} 的 packages 为空")
    for pkg, ver in pkgs.items():
        if not EXACT_VERSION.match(str(ver)):
            raise BuildError(
                f"{name}: {pkg} 的版本 {ver!r} 不是精确版本（不允许范围 / latest / 空）"
            )
    missing = [n for n in top_level if n not in pkgs]
    if missing:
        raise BuildError(f"{name}: top_level 里的 {missing} 不在 packages 闭包中")


def target_names(lock: dict) -> list[str]:
    return sorted(lock.get("targets") or {})


def get_target(lock: dict, name: str) -> dict:
    try:
        return lock["targets"][name]
    except KeyError:
        raise BuildError(f"锁文件里没有目标 {name!r}；可用的是 {target_names(lock)}") from None


def default_target_name(lock: dict) -> str:
    """本机该构建哪个目标。

    只按**本机**选，不猜「你大概想要哪个」：交叉构建必须显式 `--target`，
    否则一次手滑就会把 Windows 的 runtime 打进 macOS 的包里，而两边的
    构建日志看起来一模一样。
    """
    machine = (platform.machine() or "").lower()
    arch = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}.get(
        machine, machine
    )
    if os.name == "nt":
        want_os = "windows"
    elif sys.platform == "darwin":
        want_os = "macos"
    else:
        raise BuildError(
            f"本平台（{sys.platform}）没有内置 runtime 的发行形态；"
            f"要交叉构建请显式指定 --target（可用：{target_names(lock)}）"
        )
    for name, target in (lock.get("targets") or {}).items():
        if target.get("os") == want_os and target.get("arch") == arch:
            return name
    raise BuildError(f"锁文件里没有 {want_os}/{arch} 的目标（可用：{target_names(lock)}）")


def requirement_list(target: dict) -> list[str]:
    """闭包 → `name==version` 列表（顺序稳定，便于 diff 与复现）。"""
    return [f"{n}=={v}" for n, v in sorted((target.get("packages") or {}).items())]


#: 各家对同一个架构的叫法 → 规范名（与 engine/runtime._ARCH_ALIASES 同义）
ARCH_ALIASES = {
    "amd64": "x86_64",
    "x86_64": "x86_64",
    "x64": "x86_64",
    "arm64": "arm64",
    "aarch64": "arm64",
}


def host_target() -> tuple[str, str]:
    """本机构建时**应该**配哪个平台/架构的 runtime。"""
    if os.name == "nt":
        host_os = "windows"
    elif sys.platform == "darwin":
        host_os = "macos"
    else:
        host_os = "linux"
    return host_os, ARCH_ALIASES.get((platform.machine() or "").lower(), "")


def check_runtime_dir(
    manifest_file: Path, require_smoke: bool, host: tuple[str, str] | None = None
) -> dict:
    """判定「磁盘上这份 runtime 能不能进本次构建的包」，不行就抛 BuildError。

    **packaging/tavotto.spec 与 scripts/build_desktop.py 共用这一份判据**——
    分头各写一遍的话，迟早一边放行另一边拦住，而放行的那一边才是发出去的。

    光看清单在不在是不够的：`runtime/` 里躺着的可能是上一次给另一个平台构建的
    产物（交叉构建、切分支、忘了 --clean）。把 Windows 的 runtime 打进 .app，
    用户那边的症状是「渲染环境不可用」，而构建全程绿灯。

    `require_smoke` 给发行构建用：`--allow-skip-smoke` 产出的中间件一个 import
    都没跑过，混进安装包等于把验证推给用户。
    """
    try:
        info = json.loads(Path(manifest_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BuildError(f"读不懂 runtime 清单 {manifest_file}: {exc}") from exc
    if info.get("schema") != MANIFEST_SCHEMA:
        raise BuildError(
            f"runtime 清单 schema={info.get('schema')}，本仓库要 {MANIFEST_SCHEMA}"
            "——重跑 scripts/build_worker_runtime.py"
        )

    plat = info.get("platform") or {}
    want_os, want_arch = host or host_target()
    got_os = str(plat.get("os") or "")
    got_arch = ARCH_ALIASES.get(str(plat.get("arch") or "").lower(), "")
    if got_os != want_os or (want_arch and got_arch and got_arch != want_arch):
        raise BuildError(
            f"runtime 是给 {got_os}/{got_arch} 的，这次构建的是 {want_os}/{want_arch}。\n"
            "  重建：python scripts/build_worker_runtime.py --clean\n"
            "  （交叉构建请用 --target 显式指定，并用 TAVOTTO_RUNTIME_SRC 指过来）"
        )

    smoke = (info.get("build") or {}).get("smoke") or ""
    if require_smoke and smoke != "passed":
        raise BuildError(
            f"这份 runtime 的冒烟状态是 {smoke!r}（不是 passed）。\n"
            "  未经 import + 真实绘图验证的 runtime 不得进发行包——"
            "在能执行该架构的机器上重新构建。"
        )
    # 锁文件把某个目标标成 shipped=false，意思是「版本锁着，但**没构建过也
    # 没冒烟过**，不许发」（当前的 macos-x86_64 就是这一格）。构建脚本里那句
    # 只是一条 warning，构建照常继续——发行链上没有任何一道闸拦它，于是
    # `macos-latest` 这种浮动 runner 哪天换成 Intel，我们就会把一个文档里
    # 明写着「不支持」的目标发出去，而且全程绿灯。
    if require_smoke and not (info.get("build") or {}).get("shipped"):
        plat = info.get("platform") or {}
        raise BuildError(
            f"这份 runtime 的目标 {plat.get('os')}/{plat.get('arch')} 在锁文件里是 "
            "shipped=false（锁着版本但未验证），不得进发行包。\n"
            "  要发它：先在能执行该架构的机器上真构建 + 真冒烟，"
            "再把 packaging/runtime-lock.json 里该目标改成 shipped=true。"
        )
    return info


def pth_lines(stdlib_zip: str) -> list[str]:
    """embeddable 的 `._pth` 内容（**仅 Windows**）。

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
        "# Tavotto: 上面一行是内置科学栈的落点；下面这行让 site.main() 跑起来，",
        "# 否则 site-packages 里的 .pth 文件不会被处理。",
        "import site",
    ]


def python_xy(target: dict) -> str:
    """`3.13.15` → `3.13`（macOS 的 site-packages 路径里要用）。"""
    return ".".join(str(target["python"]["version"]).split(".")[:2])


def site_packages(out: Path, target: dict) -> Path:
    """第三方包在这个 runtime 里的落点。"""
    if target["kind"] == KIND_WINDOWS:
        return out / "Lib" / "site-packages"
    return out / "lib" / f"python{python_xy(target)}" / "site-packages"


#: macOS runtime 里纯属别名的解释器入口。**它们是符号链接**，在源产物里不占空间，
#: 但 Tauri 复制资源时会把符号链接**拍平成真副本**——两个别名 = 白搭 34 MiB
#: （实测：`bin/python` 17.2 MiB + `bin/python3` 17.2 MiB，而整份 runtime 才 306 MiB）。
#:
#: 剪掉它们是安全的，因为谁都不靠这两个名字：
#:   * 上游自带的 pip / idle / pydoc 包装脚本 exec 的是 `python3.13`（版本化实体名）；
#:   * Tavotto 自己经 `engine/runtime.resolve_python()` 按 glob 找实体；
#:   * 用户脚本里的 `python3` 走的是 PATH，而 runtime 的 bin 从来不在 PATH 上，
#:     所以剪掉前后行为完全一致。
ALIAS_BINARIES = ("python", "python3")


def interpreter(out: Path, target: dict) -> Path:
    """runtime 自己的解释器（**engine/runtime.resolve_python() 认的就是它**）。

    去磁盘上找而不是拼一个固定名字：`prune_aliases()` 之后 `bin/python3`
    已经不在了，实体是版本化的 `bin/python3.13`。两边的查找顺序必须一致，
    否则构建期用一个、运行期用另一个，只有其中一个被冒烟验过。
    """
    if target["kind"] == KIND_WINDOWS:
        return out / "python.exe"
    bin_dir = out / "bin"
    versioned = f"python{python_xy(target)}"
    for name in ("python3", versioned):
        cand = bin_dir / name
        if cand.exists():
            return cand
    # 上游哪天换了命名（或升了小版本）也别硬失败：按 glob 兜一次
    matches = sorted(p for p in bin_dir.glob("python3.*") if not p.name.endswith("-config"))
    return matches[0] if matches else bin_dir / versioned


def prune_aliases(out: Path, target: dict) -> tuple[int, int]:
    """剪掉 macOS runtime 里那两个纯别名的解释器入口（见 ALIAS_BINARIES）。

    **只删符号链接**：万一上游哪天把 `bin/python3` 换成实体文件，这里必须
    原地不动——把唯一的解释器删掉可比多 17 MiB 严重得多。
    """
    if target["kind"] != KIND_MACOS:
        return 0, 0
    removed = freed = 0
    for name in ALIAS_BINARIES:
        p = out / "bin" / name
        if not p.is_symlink():
            if p.exists():
                log(f"! {p.name} 不是符号链接（上游改了布局？）——保留不动")
            continue
        try:
            freed += p.resolve().stat().st_size
            p.unlink()
            removed += 1
        except OSError as exc:
            log(f"! 删不掉 {p}: {exc}")
    if removed:
        log(
            f"✓ 剪掉 {removed} 个解释器别名，打包后省下 "
            f"{freed / 1024 / 1024:.0f} MiB（Tauri 会把符号链接拍平成真副本）"
        )
    return removed, freed


def manifest_dict(
    lock: dict, name: str, target: dict, build_id: str, built_at: str, lock_sha256: str, smoke: str
) -> dict:
    """写进 runtime-manifest.json 的内容（engine/runtime.py 的输入）。

    `platform.os` / `platform.arch` 在 schema 2 里是**会被校验的**，不再只是
    记录：装错架构的安装包必须在启动时就报 bundled_runtime_invalid，而不是
    等第一次渲染时甩一段 import 错误。
    """
    py = target["python"]
    pip = target.get("pip") or {}
    return {
        "schema": MANIFEST_SCHEMA,
        "product": "Tavotto",
        "kind": target["kind"],
        "target": name,
        "python": {
            "version": py["version"],
            "implementation": py.get("implementation", "cpython"),
            "source": py["url"],
            "sha256": py["sha256"],
            "release": py.get("release"),
            "triple": py.get("triple"),
        },
        "platform": {
            "os": target["os"],
            "arch": target["arch"],
            "tag": f"{pip.get('abi', '')}-{(pip.get('platforms') or [''])[0]}",
            "pip_platforms": list(pip.get("platforms") or []),
        },
        "top_level": list(lock.get("top_level") or []),
        "packages": dict(sorted((target.get("packages") or {}).items())),
        "build": {
            "id": build_id,
            "built_at": built_at,
            "builder": "scripts/build_worker_runtime.py",
            "lock_sha256": lock_sha256,
            "smoke": smoke,
            "shipped": bool(target.get("shipped")),
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
        with urllib.request.urlopen(url, timeout=300) as resp, tmp.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise BuildError(f"下载失败 {url}: {exc}") from exc
    got = sha256_file(tmp)
    if got != expect_sha:
        tmp.unlink(missing_ok=True)
        raise BuildError(
            f"SHA-256 不符！\n  期望 {expect_sha}\n  实得 {got}\n  来源 {url}\n拒绝继续构建。"
        )
    tmp.replace(dest)
    log(f"✓ 已下载并校验 {dest.name}")
    return dest


# ---------------------------------------------------------------------------
# 解包（两种上游发行版形状不同，只有这一段需要分叉）
# ---------------------------------------------------------------------------
def extract_windows_embeddable(archive: Path, out: Path, target: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        need = {"python.exe", target["python"]["stdlib_zip"], target["python"]["pth"]}
        missing = need - set(names)
        if missing:
            raise BuildError(
                f"embeddable 压缩包里少了 {sorted(missing)}——"
                "锁文件里的 stdlib_zip / pth 名字对不上这个版本？"
            )
        zf.extractall(out)
    # `._pth` 必须重写，否则 site-packages 根本不在 sys.path 上
    pth = out / target["python"]["pth"]
    pth.write_text("\n".join(pth_lines(target["python"]["stdlib_zip"])) + "\n", encoding="utf-8")
    log(f"✓ 解压 embeddable 到 {out}，并重写 {pth.name}（site-packages + import site）")


def extract_macos_standalone(archive: Path, out: Path, target: dict) -> None:
    """解开 python-build-standalone 的 tar.gz，剥掉外面那层 `python/`。

    `filter="data"` 是刻意的（Python 3.12+）：它拒绝绝对路径与指向包外的
    符号链接。上游是可信的，但「解压一个 tar 就能写到任意路径」这种事不该
    靠信任来防——尤其这一步跑在 CI 的构建机上。

    符号链接必须**保留**（`bin/python3 → python3.13`、几个 .dylib）：
    拍平成副本会让 200 MB 的产物凭空再胖一圈，而 PyInstaller 那边的
    datas 复制才是真正会拍平它们的地方（见 packaging/tavotto.spec 的说明）。
    """
    root = target["python"]["archive_root"]
    staging = out.with_name(out.name + ".unpack")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(staging, filter="data")
        inner = staging / root
        if not inner.is_dir():
            raise BuildError(
                f"压缩包里没有预期的顶层目录 {root!r}"
                f"（实际有 {[p.name for p in staging.iterdir()][:5]}）——"
                "锁文件的 archive_root 对不上这个发行版？"
            )
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            out.rmdir()  # build() 保证过它是空的
        inner.replace(out)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    py = interpreter(out, target)
    if not py.exists():
        raise BuildError(f"解压完没有解释器: {py}")
    log(f"✓ 解压 python-build-standalone 到 {out}（已剥掉 {root}/ 一层）")


def materialize(archive: Path, out: Path, target: dict) -> None:
    if target["kind"] == KIND_WINDOWS:
        extract_windows_embeddable(archive, out, target)
    else:
        extract_macos_standalone(archive, out, target)


# ---------------------------------------------------------------------------
# 装包
# ---------------------------------------------------------------------------
def install_packages(out: Path, target: dict, pip_python: str) -> None:
    """把锁定的 wheel 装进 runtime 的 site-packages。

    用**宿主机**的 pip 加 `--platform/--python-version/--abi` 交叉安装，
    而不是用 runtime 自己的 pip：
      * Windows 的 embeddable 发行版里根本没有 pip（官方就是这么设计的）；
      * macOS 的 pbs 发行版有 pip，但用它就要求构建机能**执行**目标架构的
        二进制——那样就再也没法在 arm64 的 runner 上产出 x86_64 的 runtime。
    交叉安装两个平台同一套写法，CI 想换 runner 不用重写这一段。

    `--no-deps` 是有意的：锁文件里已经是完整闭包，交给 pip 再解析一次等于
    把「装什么」的决定权交还给网络，两次构建就可能不一样。
    """
    site = site_packages(out, target)
    site.mkdir(parents=True, exist_ok=True)
    pip = target.get("pip") or {}
    reqs = requirement_list(target)
    cmd = [
        pip_python,
        "-m",
        "pip",
        "install",
        "--no-deps",  # 闭包已锁死，不允许 pip 再自行取舍
        "--only-binary=:all:",  # 不在构建机上编译 C 扩展
        "--disable-pip-version-check",
        "--no-input",
    ]
    # 多个 --platform：同一个 wheel 在不同 macOS 最低版本标签下发布，
    # 只给一个的话 numpy 有、scipy 没有，构建会以「找不到 wheel」失败
    for plat in pip["platforms"]:
        cmd += ["--platform", plat]
    cmd += [
        "--python-version",
        pip["python_version"],
        "--implementation",
        pip["implementation"],
        "--abi",
        pip["abi"],
        "--target",
        str(site),
        "--upgrade",
        *reqs,
    ]
    log(f"→ pip install {len(reqs)} 个锁定 wheel → {site}")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise BuildError("pip 安装失败：\n" + (proc.stdout or "") + (proc.stderr or ""))
    log("✓ 科学栈已就位")


def verify_installed(out: Path, target: dict) -> dict[str, str]:
    """按 .dist-info 核对装进去的版本与锁文件一致。

    pip 说成功不等于装对了：`--target` 遇到已有目录时的覆盖行为历来有坑，
    残留一个旧版本会让 manifest 撒谎。
    """
    site = site_packages(out, target)
    found: dict[str, str] = {}
    for info in site.glob("*.dist-info"):
        name, _, ver = info.name[: -len(".dist-info")].rpartition("-")
        if name:
            found[name.replace("_", "-").lower()] = ver
    problems = []
    for name, want in (target.get("packages") or {}).items():
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


def prune(out: Path, target: dict) -> tuple[int, int]:
    """删掉科学栈自带的测试套件与字节码缓存。

    scipy + pandas 的 tests 占 85 MiB 上下——用户装 Tavotto 是来画图的，
    不会去跑 scipy 的测试。删完仍然要过 import + 真实渲染冒烟，
    所以这不是「赌它不影响」，是被验证过的。

    **只动 site-packages**：标准库那边 pbs 已经剥过 test/，而 tcl/tk 只有
    3 MiB——为省这点体积去赌 `matplotlib.use("TkAgg")` 的用户不存在，不划算。
    """
    site = site_packages(out, target)
    removed = freed = 0
    # 自底向上：先删深层的，避免删掉父目录后再去遍历它
    for path in sorted(site.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if not path.is_dir() or path.is_symlink() or path.name not in PRUNE_DIRS:
            continue
        try:
            freed += sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
            shutil.rmtree(path)
            removed += 1
        except OSError:
            continue
    log(f"✓ 精简：删除 {removed} 个 tests/__pycache__ 目录，省下 {freed / 1024 / 1024:.0f} MiB")
    return removed, freed


# ---------------------------------------------------------------------------
# 只有「构建机能执行目标二进制」时才做得了的两步
# ---------------------------------------------------------------------------
def can_execute(python: Path) -> bool:
    """构建机能不能跑这个 runtime 的解释器。

    **真去跑一次**，而不是拿 `os.name`/`platform.machine()` 去推断：
    Apple Silicon 上装了 Rosetta 就能跑 x86_64 的产物，没装就不能，
    这件事只有试过才知道。推断错的代价是「本来能验的没验」——而验不了的
    产物最后会以「装完打不开」的形式出现在用户那里。
    """
    try:
        proc = subprocess.run([str(python), "-c", "print(1)"], capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def precompile(out: Path, target: dict) -> str:
    """把 site-packages 的 .py 预编译成 .pyc 随包发出。

    运行时 worker 是带 `-B` 起的（安装目录一个字节都不写，见
    engine/runtime.child_args）；`-B` 只禁止**写** .pyc，读现成的不受影响。
    所以这一步是 `-B` 的配套：没有它，每次冷启动都要重新编译 numpy /
    matplotlib / pandas 的几千个 .py。

    macOS 上还多一层：`.app` 是签过名的，运行时往里写 `__pycache__` 会
    **当场破坏代码签名**，用户下次启动看到的是「应用已损坏」。

    invalidation_mode 用 **UNCHECKED_HASH**：默认的时间戳模式依赖源文件的
    mtime，而安装程序解压、杀毒软件扫描、dmg 拷贝都可能改动它——一旦对不上，
    .pyc 会被判为过期，`-B` 又不让重写，于是每次启动都白编译一遍。
    哈希模式与 mtime 无关。

    必须用 runtime **自己的**解释器编：.pyc 的魔数跟解释器版本走。
    """
    python = interpreter(out, target)
    if not python.is_file() or not can_execute(python):
        log(
            f"! 跳过预编译（构建机跑不了 {target['os']}/{target['arch']} 的解释器）"
            "——冷启动会慢一些，功能不受影响"
        )
        return "skipped:foreign-host"
    site = site_packages(out, target)
    code = (
        "import compileall, py_compile, sys\n"
        "ok = compileall.compile_dir("
        f"    {str(site)!r}, quiet=2, force=True, workers=0,\n"
        "    invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)\n"
        # 个别第三方包会带上故意语法错误的样例文件，编不过很正常，
        # 所以不按 compile_dir 的返回值判成败——真正的判据是随后的 import 冒烟。
        "sys.stdout.write('done' if ok else 'partial')\n"
    )
    proc = subprocess.run(
        [str(python), "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONNOUSERSITE": "1"},
        timeout=1800,
    )
    if proc.returncode != 0:
        raise BuildError("预编译失败：\n" + (proc.stdout or "") + (proc.stderr or ""))
    n = sum(1 for _ in site.rglob("*.pyc"))
    log(f"✓ 预编译 {n} 个 .pyc（{proc.stdout.strip()}）——运行时可以带 -B 起，安装目录零写入")
    return f"unchecked-hash:{n}"


def smoke_imports(
    out: Path, target: dict, top_level: list[str]
) -> tuple[str, dict[str, str | None]]:
    """逐个 import 一遍并画一张真图——**构建的最后一道闸**。

    只看文件在不在没有意义：常见失败是 wheel 装错平台、缺 VC 运行库 /
    某个 .dylib、`._pth` 写坏导致 site-packages 根本不在 sys.path 上。
    这些全都只有真跑一次才暴露，而暴露的地方必须是构建机，不是用户的电脑。
    """
    python = interpreter(out, target)
    if not python.is_file():
        raise BuildError(f"没有 {python}")
    if not can_execute(python):
        return "skipped:foreign-host", {}

    env = {
        **os.environ,
        # 构建产物要干净可复现：不往 runtime 里落 __pycache__
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "MPLBACKEND": "Agg",
    }
    # 构建机自己的 shell 里可能有这些（Conda / 自家项目），带进去会让冒烟
    # 验的不是我们刚装的那套。运行时也摘（engine/runtime._HOSTILE_ENV）。
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONUSERBASE"):
        env.pop(key, None)

    results: dict[str, str | None] = {}
    failed: list[str] = []
    for dist in top_level:
        mod = IMPORT_NAMES.get(dist.lower(), dist.replace("-", "_"))
        code = f"import {mod} as m; print(getattr(m, '__version__', 'unknown'))"
        proc = subprocess.run(
            [str(python), "-c", code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=300,
        )
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
        proc = subprocess.run(
            [str(python), "-c", code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=600,
        )
        if proc.returncode != 0 or "render-ok" not in proc.stdout:
            failed.append("真实渲染: " + (proc.stderr or "").strip()[-800:])
        else:
            log("✓ 真实渲染（seaborn + scipy + PDF 输出）通过")

    if failed:
        raise BuildError("内置 runtime 冒烟失败：\n  " + "\n  ".join(failed))
    return "passed", results


# ---------------------------------------------------------------------------
# 许可证
# ---------------------------------------------------------------------------
#: CPython 自带的 LICENSE 在两种发行版里的位置不同
CPYTHON_LICENSE_RELPATH = {
    KIND_WINDOWS: ("LICENSE.txt",),
    KIND_MACOS: ("lib", "python{xy}", "LICENSE.txt"),
}


def _looks_like_license(name: str) -> bool:
    low = name.lower()
    return low.startswith(("license", "licence", "copying", "notice", "authors"))


def collect_licenses(out: Path, name: str, target: dict) -> int:
    """收齐第三方许可证 / NOTICE，随安装包一起发。

    AGPL 的项目分发别人的二进制，许可证义务是硬的：BSD/MIT/HPND/Apache-2.0
    全都要求随分发附带版权声明。用户装到的是我们打的包，这份义务落在我们身上。
    """
    site = site_packages(out, target)
    lic_root = out / "licenses"
    if lic_root.exists():
        shutil.rmtree(lic_root)
    lic_root.mkdir(parents=True, exist_ok=True)

    index = [
        "# Tavotto 内置渲染环境 · 第三方组件许可证",
        "",
        f"目标：`{name}`（{target['os']}/{target['arch']}，{target['kind']}）",
        "",
        "本目录随 Tavotto 安装包一起分发。下列组件均为各自作者版权所有，",
        "以其原始许可证条款提供；完整许可证正文见各子目录。",
        "",
    ]

    count = 0
    rel = CPYTHON_LICENSE_RELPATH[target["kind"]]
    cpython_lic = out.joinpath(*[p.format(xy=python_xy(target)) for p in rel])
    if cpython_lic.is_file():
        dst = lic_root / "cpython"
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cpython_lic, dst / "LICENSE.txt")
        index.append(
            f"- **CPython {target['python']['version']}** — PSF License "
            f"(`licenses/cpython/LICENSE.txt`)"
        )
        count += 1

    if target["kind"] == KIND_MACOS:
        # pbs 的 install_only 发行版不随包带各静态依赖的许可证正文，只能指路。
        # 这些依赖**全部是宽松许可**（OpenSSL/Apache-2.0、SQLite/公有领域、
        # libffi/MIT、libedit/BSD、Tcl-Tk/BSD 式、zlib、bzip2/BSD、XZ）——
        # macOS 上的行编辑走的是 **libedit 而不是 GNU readline**（实测
        # `readline._READLINE_LIBRARY_VERSION == "EditLine wrapper"`），
        # 因此桌面分发不引入任何 copyleft 义务。换上游或换 flavor 时**必须
        # 重新确认这一条**，别默认它还成立。
        (lic_root / "cpython").mkdir(parents=True, exist_ok=True)
        (lic_root / "cpython" / "UPSTREAM-BUILD.md").write_text(
            "# CPython 二进制的来源\n\n"
            f"- 发行版：python-build-standalone `{target['python'].get('release')}`\n"
            f"- 目标三元组：`{target['python'].get('triple')}`\n"
            f"- 归档：{target['python']['url']}\n"
            f"- SHA-256：`{target['python']['sha256']}`\n"
            f"- 上游校验和清单：{target['python'].get('checksums', '（未记录）')}\n\n"
            "该发行版把 OpenSSL、SQLite、libffi、libedit、Tcl/Tk、zlib、bzip2、XZ\n"
            "等依赖静态链接进 CPython，均为宽松许可（Apache-2.0 / MIT / BSD /\n"
            "公有领域）。行编辑用的是 libedit（BSD），**不是** GNU readline，\n"
            "因此不引入 copyleft 义务。各组件的许可证正文见上游仓库：\n"
            "https://github.com/astral-sh/python-build-standalone\n",
            encoding="utf-8",
        )
        index.append(
            "- **CPython 静态依赖**（OpenSSL / SQLite / libffi / libedit / "
            "Tcl-Tk / zlib / bzip2 / XZ）— 均为宽松许可，"
            "来源与说明见 `licenses/cpython/UPSTREAM-BUILD.md`"
        )

    for info in sorted(site.glob("*.dist-info")):
        pkg, _, ver = info.name[: -len(".dist-info")].rpartition("-")
        dst = lic_root / pkg.lower()
        files = [p for p in info.rglob("*") if p.is_file() and _looks_like_license(p.name)]
        # 没有单独许可证文件的包，METADATA 里的 License / Classifier 也算交代
        meta = info / "METADATA"
        if not files and meta.is_file():
            files = [meta]
        if not files:
            continue
        dst.mkdir(parents=True, exist_ok=True)
        for src in files:
            shutil.copy2(src, dst / src.name)
        index.append(f"- **{pkg} {ver}** — `licenses/{pkg.lower()}/`")
        count += 1

    (lic_root / "THIRD-PARTY-NOTICES.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    log(f"✓ 许可证收集完成（{count} 个组件）→ {lic_root}")
    return count


# ---------------------------------------------------------------------------
# --resolve：更新锁文件（维护者用）
# ---------------------------------------------------------------------------
def _resolve_windows_python(target: dict, python_version: str) -> None:
    url = (
        f"https://www.python.org/ftp/python/{python_version}/"
        f"python-{python_version}-embed-amd64.zip"
    )
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "embed.zip"
        log(f"↓ {url}")
        try:
            with urllib.request.urlopen(url, timeout=300) as resp, dest.open("wb") as fh:
                shutil.copyfileobj(resp, fh)
        except OSError as exc:
            raise BuildError(f"下载失败 {url}: {exc}") from exc
        digest = sha256_file(dest)
        size = dest.stat().st_size
        with zipfile.ZipFile(dest) as zf:
            names = set(zf.namelist())
    tag = "python" + ".".join(python_version.split(".")[:2]).replace(".", "")
    if f"{tag}.zip" not in names or f"{tag}._pth" not in names:
        raise BuildError(f"{url} 里没有 {tag}.zip / {tag}._pth")
    target["python"].update(
        version=python_version,
        url=url,
        sha256=digest,
        size=size,
        stdlib_zip=f"{tag}.zip",
        pth=f"{tag}._pth",
    )
    log(f"✓ CPython {python_version} sha256={digest}")


def _resolve_macos_python(target: dict, release: str, python_version: str | None) -> None:
    """从 pbs 的 **上游 SHA256SUMS** 取校验和，不自己下整包再算。

    上游发布的那份清单是可引用的第三方证据：锁文件里的 sha256 因此有出处，
    而不只是「某台机器某次下载算出来的值」。构建时 `download()` 仍然会实测
    一遍，两处对不上就当场失败。
    """
    version = python_version or target["python"]["version"]
    triple = target["python"]["triple"]
    flavor = target["python"].get("flavor", "install_only")
    asset = f"cpython-{version}+{release}-{triple}-{flavor}.tar.gz"
    base = f"https://github.com/astral-sh/python-build-standalone/releases/download/{release}"
    sums_url = f"{base}/SHA256SUMS"
    log(f"↓ {sums_url}")
    try:
        with urllib.request.urlopen(sums_url, timeout=300) as resp:
            sums = resp.read().decode("utf-8", "replace")
    except OSError as exc:
        raise BuildError(f"取不到上游校验和清单 {sums_url}: {exc}") from exc

    digest = ""
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == asset:
            digest = parts[0]
            break
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise BuildError(
            f"上游 SHA256SUMS 里没有 {asset}——"
            f"这个 release（{release}）可能没构建 {triple}，或者 flavor 名变了"
        )

    # URL 里的 `+` 必须百分号转义：GitHub 的下载路径把裸 `+` 当成空格，
    # 直接拼会 404（而 404 在构建日志里长得像「网络抖了一下」）
    url = f"{base}/{asset.replace('+', '%2B')}"
    target["python"].update(version=version, release=release, url=url, sha256=digest, flavor=flavor)
    # size 只是给人看的参考值；取不到不该拦住 resolve
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=120) as resp:
            length = resp.headers.get("Content-Length")
        if length:
            target["python"]["size"] = int(length)
    except (OSError, ValueError):
        pass
    target["python"]["checksums"] = sums_url
    log(f"✓ CPython {version}+{release} {triple} sha256={digest}（取自上游清单）")


def resolve_lock(
    lock: dict, name: str, pip_python: str, python_version: str | None, pbs_release: str | None
) -> dict:
    """用 pip 真实解析出该目标的传递闭包，写回锁文件。

    不手写闭包的理由很实际：手写迟早漏一个传递依赖，而漏掉的那个会在用户机器上
    以 ModuleNotFoundError 的形式出现。
    """
    target = get_target(lock, name)
    tops = lock.get("top_level") or []
    if not tops:
        raise BuildError("top_level 为空，没有可解析的顶层包")
    if python_version and not EXACT_VERSION.match(python_version):
        raise BuildError(f"--python-version 要精确补丁版本，拿到 {python_version}")

    if target["kind"] == KIND_WINDOWS:
        if python_version:
            _resolve_windows_python(target, python_version)
    else:
        if pbs_release or python_version:
            _resolve_macos_python(
                target, pbs_release or target["python"].get("release", ""), python_version
            )

    pip = target.get("pip") or {}
    with tempfile.TemporaryDirectory() as td:
        report = Path(td) / "report.json"
        cmd = [
            pip_python,
            "-m",
            "pip",
            "install",
            "--dry-run",
            "--quiet",
            "--report",
            str(report),
            "--disable-pip-version-check",
            "--no-input",
            "--only-binary=:all:",
        ]
        for plat in pip.get("platforms") or []:
            cmd += ["--platform", plat]
        cmd += [
            "--python-version",
            pip.get("python_version", "3.13"),
            "--implementation",
            pip.get("implementation", "cp"),
            "--abi",
            pip.get("abi", "cp313"),
            "--target",
            str(Path(td) / "unused"),
            *[
                f"{n}=={target['packages'][n]}" if n in target.get("packages", {}) else n
                for n in tops
            ],
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        if proc.returncode != 0:
            raise BuildError("解析失败：\n" + (proc.stdout or "") + (proc.stderr or ""))
        data = json.loads(report.read_text(encoding="utf-8"))

    closure = {}
    for item in data.get("install", []):
        meta = item.get("metadata") or {}
        closure[str(meta["name"]).lower()] = str(meta["version"])
    target["packages"] = dict(sorted(closure.items()))
    validate_lock(lock)
    log(f"✓ {name} 闭包解析完成：{len(closure)} 个包")
    return lock


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def build(
    lock: dict,
    name: str,
    out: Path,
    cache: Path,
    lock_sha: str,
    pip_python: str,
    allow_skip_smoke: bool,
    clean: bool,
    do_prune: bool = True,
) -> dict:
    target = get_target(lock, name)
    log(
        f"* 目标 {name}：{target['os']}/{target['arch']}（{target['kind']}）"
        f"{'' if target.get('shipped') else ' —— 注意：锁文件标记 shipped=false'}"
    )

    if clean and out.exists():
        shutil.rmtree(out)
        log(f"✓ 已清理 {out}")
    if out.exists() and any(out.iterdir()):
        raise BuildError(
            f"{out} 非空——加 --clean 重建，或换 --out。在旧产物上叠加会留下上一版的包。"
        )
    out.mkdir(parents=True, exist_ok=True)

    archive_name = target["python"]["url"].rsplit("/", 1)[-1].replace("%2B", "+")
    archive = download(target["python"]["url"], cache / archive_name, target["python"]["sha256"])
    materialize(archive, out, target)
    install_packages(out, target, pip_python)
    verify_installed(out, target)
    # 先收许可证再精简：许可证在 .dist-info 里，精简动不到它，但顺序写死更省心
    collect_licenses(out, name, target)
    # 精简必须在预编译**之前**：反过来会把刚编好的 __pycache__ 又删掉
    pruned = prune(out, target)[0] if do_prune else 0
    # 别名也要在冒烟**之前**剪掉——剪完的这份才是用户拿到的布局，
    # 冒烟要验的正是它（剪完再验，而不是验完再剪）
    aliases, alias_bytes = prune_aliases(out, target) if do_prune else (0, 0)
    compiled = precompile(out, target)

    smoke, versions = smoke_imports(out, target, lock.get("top_level") or [])
    if smoke != "passed":
        if not allow_skip_smoke:
            raise BuildError(
                f"import 冒烟没跑（{smoke}）。内置 runtime 是"
                f" {target['os']}/{target['arch']} 的原生二进制，只有能执行它的"
                "机器才验得了；确实要在别的平台产出中间件时加 --allow-skip-smoke，"
                "但那份产物**不得直接发给用户**。"
            )
        log(f"! import 冒烟跳过（{smoke}）——此产物未经运行时验证")

    built_at = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(os.environ.get("SOURCE_DATE_EPOCH") or time.time()))
    )
    build_id = os.environ.get("GITHUB_RUN_ID") or f"local-{lock_sha[:12]}"
    info = manifest_dict(lock, name, target, build_id, built_at, lock_sha, smoke)
    info["build"]["pruned_dirs"] = pruned
    info["build"]["pruned_aliases"] = aliases
    info["build"]["precompiled"] = compiled
    info["build"]["interpreter"] = str(interpreter(out, target).relative_to(out))
    if versions:
        info["build"]["imported"] = versions
    (out / MANIFEST_NAME).write_text(
        json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    log(f"✓ 写入 {MANIFEST_NAME}")

    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file() and not p.is_symlink())
    log(
        f"\n完成：{out}\n  {name}  Python {info['python']['version']}"
        f"  包 {len(info['packages'])} 个  体积 {size / 1024 / 1024:.0f} MiB"
        f"  冒烟 {smoke}"
    )
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
    ap.add_argument("--target", default=None, help="构建哪个目标（默认按本机平台/架构挑）")
    ap.add_argument("--list-targets", action="store_true")
    ap.add_argument(
        "--cache", default=str(DEFAULT_CACHE), help="下载缓存目录（CI 可缓存以省下每次几十 MiB）"
    )
    ap.add_argument(
        "--pip-python",
        default=sys.executable,
        help="用哪个解释器的 pip 做交叉安装（默认当前解释器）",
    )
    ap.add_argument("--clean", action="store_true")
    ap.add_argument(
        "--no-prune",
        dest="prune",
        action="store_false",
        help="保留科学栈自带的 tests/（体积多 80 MiB 上下）",
    )
    ap.add_argument(
        "--allow-skip-smoke",
        action="store_true",
        help="构建机跑不了目标二进制时允许跳过冒烟（产物不得直接发布）",
    )
    ap.add_argument(
        "--resolve", action="store_true", help="维护者模式：重新解析闭包并写回锁文件，不构建"
    )
    ap.add_argument(
        "--python-version", default=None, help="配合 --resolve：换到指定 CPython 补丁版本"
    )
    ap.add_argument(
        "--pbs-release",
        default=None,
        help="配合 --resolve：python-build-standalone 的 release "
        "标签（如 20260814），仅 macOS 目标有意义",
    )
    args = ap.parse_args(argv)

    lock_path = Path(args.lock)
    try:
        if args.list_targets:
            lock = load_lock(lock_path)
            for name in target_names(lock):
                t = lock["targets"][name]
                flag = "发行" if t.get("shipped") else "锁定但未发行"
                print(f"{name:16} {t['os']}/{t['arch']:8} {t['kind']:20} {flag}")
            return 0

        if args.resolve:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            name = args.target or default_target_name(lock)
            lock = resolve_lock(lock, name, args.pip_python, args.python_version, args.pbs_release)
            lock_path.write_text(
                json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            log(f"✓ 已更新 {lock_path}")
            return 0

        lock = load_lock(lock_path)
        name = args.target or default_target_name(lock)
        lock_sha = hashlib.sha256(lock_path.read_bytes()).hexdigest()
        build(
            lock,
            name,
            Path(args.out),
            Path(args.cache),
            lock_sha,
            args.pip_python,
            args.allow_skip_smoke,
            args.clean,
            args.prune,
        )
    except BuildError as exc:
        print(f"::error::构建内置 runtime 失败: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
