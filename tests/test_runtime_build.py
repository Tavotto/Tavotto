"""内置 runtime 的**构建侧**看护：锁文件、`._pth`、清单、打包卫生。

真去下载 11 MiB 的 CPython 再装 200 MiB 科学栈只能在 CI 的 Windows 上做，
这里盯的是不需要网络也能出错的那些地方——而它们恰好是最容易出错的：
锁文件被人改成范围版本、`._pth` 漏了 site-packages、runtime 混进了 wheel。
"""
import json
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # tomllib 是 3.11 才进标准库的；3.10 上只跳过用到它的那一条
    tomllib = None

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import build_worker_runtime as brt  # noqa: E402

LOCK_PATH = REPO / "packaging" / "runtime-lock.json"


@pytest.fixture
def lock():
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


# ---------------- 锁文件 ------------------------------------------------------
def test_shipped_lock_file_is_valid(lock):
    """仓库里这份锁文件本身必须是合法的——它是所有用户拿到的渲染环境。"""
    brt.validate_lock(lock)


def test_lock_pins_the_scientific_stack_we_promise(lock):
    """README 承诺内置这几样，锁文件里就得真有。删一个都要先改文档。"""
    for name in ("numpy", "matplotlib", "pandas", "scipy", "seaborn", "pillow"):
        assert name in lock["packages"], f"{name} 不在锁文件里"
        assert name in lock["top_level"], f"{name} 不在 top_level 里"


def test_every_version_is_exact(lock):
    """范围版本 = 两次构建可能装出不同的东西，用户报的 bug 就没法复现。"""
    for name, ver in lock["packages"].items():
        assert brt.EXACT_VERSION.match(ver), f"{name}={ver} 不是精确版本"
        assert not re.search(r"[><=~*^]|latest", ver), f"{name}={ver} 含范围符号"


def test_cpython_is_pinned_to_a_313_patch_from_python_org(lock):
    py = lock["python"]
    assert py["version"].startswith("3.13.")
    assert brt.EXACT_VERSION.match(py["version"])
    assert py["url"].startswith("https://www.python.org/ftp/python/")
    assert py["version"] in py["url"], "URL 与版本号对不上"
    assert re.fullmatch(r"[0-9a-f]{64}", py["sha256"])


def test_closure_is_complete_not_just_top_level(lock):
    """只锁顶层的话，某个传递依赖发新版就会让两次构建装出不同的东西。
    matplotlib 的这几个依赖是最容易被漏掉的。"""
    for dep in ("contourpy", "cycler", "fonttools", "kiwisolver", "pyparsing",
                "packaging", "python-dateutil"):
        assert dep in lock["packages"], f"闭包里少了 {dep}"


@pytest.mark.parametrize("mutate, why", [
    (lambda k: k["packages"].update(numpy=">=2.0"), "范围版本"),
    (lambda k: k["packages"].update(numpy="latest"), "latest"),
    (lambda k: k["packages"].clear(), "空闭包"),
    (lambda k: k["python"].update(sha256="deadbeef"), "sha256 长度不对"),
    (lambda k: k["python"].update(version="3.13"), "不是补丁版本"),
    (lambda k: k["python"].update(version="3.12.9"), "不是 3.13"),
    (lambda k: k["python"].update(url="https://evil.example/py.zip"), "非官方下载源"),
    (lambda k: k["top_level"].append("rdkit"), "top_level 不在闭包里"),
])
def test_validate_lock_rejects(lock, mutate, why):
    mutate(lock)
    with pytest.raises(brt.BuildError):
        brt.validate_lock(lock)


def test_requirement_list_is_pinned_and_stable(lock):
    reqs = brt.requirement_list(lock)
    assert all("==" in r for r in reqs)
    assert reqs == sorted(reqs), "顺序不稳定的话每次构建的 diff 都没法看"
    assert f"numpy=={lock['packages']['numpy']}" in reqs


# ---------------- ._pth -------------------------------------------------------
def test_pth_adds_site_packages_and_enables_site():
    """官方默认的 `._pth` 既没有 site-packages 也不跑 site.main()。
    照抄的话装进去的 numpy 一个都 import 不到——这是 embeddable 最经典的坑。"""
    lines = brt.pth_lines("python313.zip")
    assert lines[0] == "python313.zip"
    assert "Lib\\site-packages" in lines
    assert "import site" in lines
    assert not any(ln.strip() == "#import site" for ln in lines), \
        "import site 必须是放开的，不能还注释着"


def test_pth_uses_windows_separator():
    """`._pth` 是 Windows 解释器读的，写成 `Lib/site-packages` 在某些版本上不生效。"""
    assert "/" not in [ln for ln in brt.pth_lines("python313.zip")
                       if "site-packages" in ln][0]


# ---------------- 清单 --------------------------------------------------------
def test_manifest_records_everything_needed_to_diagnose(lock):
    info = brt.manifest_dict(lock, "run-42", "2026-08-17T00:00:00Z", "a" * 64,
                             "passed")
    assert info["schema"] == brt.MANIFEST_SCHEMA
    assert info["python"]["version"] == lock["python"]["version"]
    assert info["platform"]["tag"] == "cp313-win_amd64"
    assert info["packages"] == dict(sorted(lock["packages"].items()))
    assert info["build"]["id"] == "run-42"
    assert info["build"]["built_at"].endswith("Z")
    assert info["build"]["lock_sha256"] == "a" * 64
    assert info["build"]["smoke"] == "passed"


def test_manifest_schema_matches_the_reader():
    """构建脚本写的 schema 与 engine/runtime.py 认的必须是同一个数字，
    否则打出来的包一装上就被自己判成「损坏」。"""
    from magplot.engine import runtime
    assert brt.MANIFEST_SCHEMA == runtime.MANIFEST_SCHEMA
    assert brt.MANIFEST_NAME == runtime.MANIFEST_NAME


def test_manifest_is_readable_by_the_runtime_module(tmp_path, lock):
    """端到端：构建脚本产出的清单，engine/runtime.py 必须能读懂。
    两边各写各的结构是这类「装完发现自己不认识自己」bug 的源头。"""
    from magplot.engine import runtime

    root = tmp_path / "rt"
    root.mkdir()
    info = brt.manifest_dict(lock, "x", "2026-08-17T00:00:00Z", "b" * 64, "passed")
    (root / brt.MANIFEST_NAME).write_text(json.dumps(info), encoding="utf-8")
    got = runtime.read_manifest(str(root))
    assert got is not None
    assert got["packages"]["scipy"] == lock["packages"]["scipy"]


# ---------------- 精简规则 ----------------------------------------------------
def test_prune_never_touches_public_testing_apis():
    """`numpy.testing` / `pandas._testing` 是公开 API，用户脚本里
    `from numpy.testing import assert_allclose` 很常见。按前缀匹配会把它们
    一起删掉，所以只认精确目录名。"""
    assert brt.PRUNE_DIRS == {"tests", "test", "__pycache__"}
    for keep in ("testing", "_testing", "_test_utils", "testsuite"):
        assert keep not in brt.PRUNE_DIRS


def test_prune_removes_test_dirs_only(tmp_path):
    site = tmp_path / "Lib" / "site-packages"
    (site / "numpy" / "tests").mkdir(parents=True)
    (site / "numpy" / "tests" / "big.py").write_text("x" * 100)
    (site / "numpy" / "testing").mkdir(parents=True)
    (site / "numpy" / "testing" / "__init__.py").write_text("keep")
    (site / "pandas" / "_testing").mkdir(parents=True)
    (site / "pandas" / "_testing" / "__init__.py").write_text("keep")

    removed, freed = brt.prune(tmp_path)
    assert removed == 1 and freed >= 100
    assert not (site / "numpy" / "tests").exists()
    assert (site / "numpy" / "testing" / "__init__.py").is_file()
    assert (site / "pandas" / "_testing" / "__init__.py").is_file()


# ---------------- 预编译 ------------------------------------------------------
def test_precompile_uses_hash_invalidation_not_timestamps():
    """运行时带 `-B` 起（安装目录零写入），所以 .pyc 必须在构建期编好。

    默认的时间戳失效模式依赖源文件 mtime——安装程序解压、杀毒软件扫描都可能
    改动它。一旦被判过期，`-B` 又不让重写，于是每次冷启动白编译一遍
    numpy/matplotlib/pandas。哈希模式与 mtime 无关。
    """
    src = (REPO / "scripts" / "build_worker_runtime.py").read_text(encoding="utf-8")
    body = src.split("def precompile", 1)[1].split("\ndef ", 1)[0]
    assert "UNCHECKED_HASH" in body
    assert "force=True" in body, "重建时必须覆盖旧 .pyc"


def test_prune_runs_before_precompile():
    """反过来的话，刚编好的 __pycache__ 会被精简步骤当场删掉。"""
    src = (REPO / "scripts" / "build_worker_runtime.py").read_text(encoding="utf-8")
    build = src.split("def build(", 1)[1]
    assert build.index("prune(out)") < build.index("precompile(out)")


# ---------------- 打包卫生 ----------------------------------------------------
def test_runtime_is_gitignored():
    """200 MiB 的 Windows 二进制绝不进 Git。锁文件才是要提交的东西。"""
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "/runtime/" in ignore
    assert LOCK_PATH.is_file(), "锁文件必须在仓库里"


@pytest.mark.skipif(tomllib is None, reason="需要 tomllib（Python ≥ 3.11）")
def test_wheel_and_sdist_never_pick_up_the_runtime():
    """pip 用户拿到的是轻量包，不该被塞进一堆 Windows 二进制。"""
    cfg = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    build = cfg["tool"]["hatch"]["build"]
    assert any(p.startswith("runtime") for p in build.get("exclude", [])), \
        "pyproject 必须显式把 runtime 排除在构建之外"
    assert not any("runtime" in a for a in build.get("artifacts", [])), \
        "artifacts 是「强行收回被 gitignore 的东西」，runtime 绝不能在里面"
    sdist = cfg["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
    assert not any(p.strip("/").startswith("runtime") for p in sdist)


def test_spec_ships_runtime_only_when_it_exists():
    """macOS 构建、以及还没跑过构建脚本的开发机上，spec 必须照常工作。"""
    spec = (REPO / "packaging" / "magplot.spec").read_text(encoding="utf-8")
    assert "runtime-manifest.json" in spec, "spec 要按清单判断 runtime 在不在"
    assert "MAGPLOT_REQUIRE_RUNTIME" in spec, "发行流水线要能把「必须带」打开"
    assert '"runtime"' in spec, "runtime 要作为 datas 进包"


def test_release_chain_refuses_to_ship_without_the_runtime():
    """漏了 runtime 照样能编出安装包，而那个包只有到了用户手里才暴露问题。

    旧链的看护在 Inno Setup 脚本的 #error 里；旧链退役后这条线由发行工作流
    扛：Windows 构建 sidecar 时必须开 MAGPLOT_REQUIRE_RUNTIME（spec 当场失败），
    NSIS 经 tauri.conf.json 的 bundle.resources 把整个 sidecar 目录（含
    _internal/runtime）收走，打完还要过 --expect-source bundled 的真渲染冒烟。
    """
    wf = (REPO / ".github" / "workflows" / "desktop-tauri.yml").read_text(encoding="utf-8")
    assert "MAGPLOT_REQUIRE_RUNTIME" in wf, "Windows 构建必须能把「必须带 runtime」打开"
    assert "--expect-source bundled" in wf, "打包后必须断言渲染真的走了内置 runtime"
    conf = (REPO / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    assert "../dist/Magplot" in conf, "NSIS/.app 必须把整个 sidecar 目录作为资源收走"
