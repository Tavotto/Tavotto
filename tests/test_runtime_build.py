"""内置 runtime 的**构建侧**看护：锁文件、布局、清单、签名前的判据、打包卫生。

真去下载 25 MiB 的 CPython 再装 300 MiB 科学栈只能在 CI 上做（而且只有能
执行目标架构的机器才验得了），这里盯的是不需要网络也能出错的那些地方——
而它们恰好是最容易出错的：锁文件被人改成范围版本、`._pth` 漏了 site-packages、
Windows 的 runtime 被打进 .app、runtime 混进了 wheel。

schema 2（2026-08-18）起锁文件按**目标**分层（windows-amd64 / macos-arm64 /
macos-x86_64）。分层的意义只有一条：一个平台的 wheel 绝不能被另一个平台复用。
下面每条针对单个目标的用例都对**所有目标**跑一遍，免得新增目标时漏掉看护。
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
#: README / 文档里承诺的科学栈。删一个都要先改文档。
PROMISED = ("numpy", "matplotlib", "pandas", "scipy", "seaborn", "pillow")


@pytest.fixture
def lock():
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def _targets():
    """参数化用：仓库里锁文件的全部目标名。"""
    data = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    return sorted(data["targets"])


ALL_TARGETS = _targets()


# ---------------- 锁文件（整体）-----------------------------------------------
def test_shipped_lock_file_is_valid(lock):
    """仓库里这份锁文件本身必须是合法的——它是所有用户拿到的渲染环境。"""
    brt.validate_lock(lock)


def test_lock_declares_the_targets_we_actually_ship(lock):
    """两个桌面平台都得有目标，而且都得是 shipped。

    macOS 的 arm64 是 v0.8.0 起的新增项；哪天它被误标成 shipped=false，
    发行链会安静地退回「用户自己的 Python」，而这条用例会先炸。
    """
    shipped = {n for n, t in lock["targets"].items() if t.get("shipped")}
    assert "windows-amd64" in shipped
    assert "macos-arm64" in shipped


def test_intel_mac_is_locked_but_honestly_marked_unshipped(lock):
    """Intel 的目标锁着是为了「要发时不用临时定版本」，但**没构建过也没冒烟过**
    （CI 只有 Apple Silicon 一档 runner）。

    标成 shipped=true 而没有对应的构建/冒烟，就是「空转的门禁还在报平安」。
    真要发 Intel 版，先有 runner 与真实绘图冒烟，再改这个标记和 README。
    """
    t = lock["targets"]["macos-x86_64"]
    assert t.get("shipped") is False, \
        "要把 Intel 改成 shipped，得先有 Intel runner 上的真实冒烟"


@pytest.mark.parametrize("name", ALL_TARGETS)
def test_every_target_pins_the_promised_stack(lock, name):
    """README 承诺内置这几样，每个目标的闭包里就得真有。"""
    target = lock["targets"][name]
    for pkg in PROMISED:
        assert pkg in target["packages"], f"{name}: {pkg} 不在闭包里"
        assert pkg in lock["top_level"], f"{pkg} 不在 top_level 里"


@pytest.mark.parametrize("name", ALL_TARGETS)
def test_every_version_is_exact(lock, name):
    """范围版本 = 两次构建可能装出不同的东西，用户报的 bug 就没法复现。"""
    for pkg, ver in lock["targets"][name]["packages"].items():
        assert brt.EXACT_VERSION.match(ver), f"{name}: {pkg}={ver} 不是精确版本"
        assert not re.search(r"[><=~*^]|latest", ver), \
            f"{name}: {pkg}={ver} 含范围符号"


@pytest.mark.parametrize("name", ALL_TARGETS)
def test_every_target_pins_cpython_313_with_a_real_digest(lock, name):
    py = lock["targets"][name]["python"]
    assert py["version"].startswith("3.13.")
    assert brt.EXACT_VERSION.match(py["version"])
    assert re.fullmatch(r"[0-9a-f]{64}", py["sha256"])
    assert py["version"] in py["url"], f"{name}: URL 与版本号对不上"


@pytest.mark.parametrize("name", ALL_TARGETS)
def test_cpython_comes_only_from_the_one_allowed_source(lock, name):
    """每种 runtime 只认一个上游。写死前缀是供应链上的一道硬闸：
    锁文件被人改成从别处取一个「CPython」时当场拒绝，而不是照单下载。"""
    target = lock["targets"][name]
    assert target["python"]["url"].startswith(
        brt.KIND_SOURCE_PREFIX[target["kind"]])


@pytest.mark.parametrize("name", ALL_TARGETS)
def test_closure_is_complete_not_just_top_level(lock, name):
    """只锁顶层的话，某个传递依赖发新版就会让两次构建装出不同的东西。
    matplotlib 的这几个依赖是最容易被漏掉的。"""
    for dep in ("contourpy", "cycler", "fonttools", "kiwisolver", "pyparsing",
                "packaging", "python-dateutil"):
        assert dep in lock["targets"][name]["packages"], \
            f"{name}: 闭包里少了 {dep}"


def test_all_targets_pin_the_same_versions(lock):
    """**同一个脚本在 Windows 与 macOS 上要画出同一张图。**

    这是排版工具的硬要求：matplotlib 3.11 与 3.12 的图例 bbox、默认字体
    度量都不一样，跨平台版本漂移会让同一份布局在两台机器上错位。
    解析结果哪天真的分叉了，不要硬凑——如实记下来并在发布说明里讲清楚，
    同时改掉这条用例。
    """
    closures = {n: lock["targets"][n]["packages"] for n in lock["targets"]}
    first = next(iter(closures))
    for name, pkgs in closures.items():
        assert pkgs == closures[first], (
            f"{name} 与 {first} 的闭包不一致：\n"
            f"  只在 {first}: {set(closures[first]) - set(pkgs)}\n"
            f"  只在 {name}: {set(pkgs) - set(closures[first])}\n"
            f"  版本不同: {{k: (closures[first][k], pkgs[k]) "
            f"for k in set(pkgs) & set(closures[first]) "
            f"if closures[first][k] != pkgs[k]}}")


@pytest.mark.parametrize("name", ALL_TARGETS)
def test_pip_platform_tags_are_declared(lock, name):
    """没有 `pip.platforms`，pip 会按**构建机**的平台挑 wheel——
    在 macOS 上构建 Windows 的 runtime 时，装进去的会是一堆 macOS 的 .so。"""
    pip = lock["targets"][name]["pip"]
    assert pip["platforms"], f"{name}: 缺 pip.platforms"
    arch_token = {"x86_64": ("amd64", "x86_64"), "arm64": ("arm64",)}
    tokens = arch_token[lock["targets"][name]["arch"]]
    for tag in pip["platforms"]:
        assert any(tok in tag for tok in tokens), \
            f"{name}: 平台标签 {tag} 与架构 {lock['targets'][name]['arch']} 对不上"


def test_macos_urls_percent_encode_the_plus(lock):
    """python-build-standalone 的文件名里有 `+`。GitHub 的下载路径把裸 `+`
    当成空格，直接拼会 404——而 404 在构建日志里长得像「网络抖了一下」。"""
    for name, target in lock["targets"].items():
        if target["kind"] != brt.KIND_MACOS:
            continue
        assert "%2B" in target["python"]["url"], f"{name}: URL 里的 + 没转义"
        assert "+" not in target["python"]["url"].rsplit("/", 1)[-1]


# ---------------- 锁文件（拒绝的形状）-----------------------------------------
@pytest.mark.parametrize("mutate, why", [
    (lambda t: t["packages"].update(numpy=">=2.0"), "范围版本"),
    (lambda t: t["packages"].update(numpy="latest"), "latest"),
    (lambda t: t["packages"].clear(), "空闭包"),
    (lambda t: t["python"].update(sha256="deadbeef"), "sha256 长度不对"),
    (lambda t: t["python"].update(version="3.13"), "不是补丁版本"),
    (lambda t: t["python"].update(version="3.12.9"), "不是 3.13"),
    (lambda t: t["python"].update(url="https://evil.example/py.zip"), "非官方下载源"),
    (lambda t: t.update(kind="something-else"), "不认识的 kind"),
    (lambda t: t.pop("arch"), "缺架构"),
    (lambda t: t.get("pip", {}).pop("platforms"), "缺 pip 平台标签"),
])
def test_validate_lock_rejects_broken_targets(lock, mutate, why):
    mutate(lock["targets"]["macos-arm64"])
    with pytest.raises(brt.BuildError):
        brt.validate_lock(lock)


@pytest.mark.parametrize("mutate, why", [
    (lambda k: k.update(schema=1), "schema 1 是旧的平铺格式"),
    (lambda k: k["top_level"].append("rdkit"), "top_level 不在闭包里"),
    (lambda k: k.update(targets={}), "一个目标都没有"),
    (lambda k: [t.update(shipped=False) for t in k["targets"].values()],
     "一个 shipped 都没有"),
])
def test_validate_lock_rejects_broken_files(lock, mutate, why):
    mutate(lock)
    with pytest.raises(brt.BuildError):
        brt.validate_lock(lock)


def test_schema_1_lock_is_refused_outright(lock):
    """旧格式（Windows 单档平铺）必须报错，不能被当成「只有一个目标」。

    默默接受的话，macOS 构建会拿着一份没有 macOS 目标的锁文件跑，
    最后产出一个不带 runtime 的 .app。
    """
    legacy = {"schema": 1, "python": lock["targets"]["windows-amd64"]["python"],
              "packages": lock["targets"]["windows-amd64"]["packages"],
              "top_level": lock["top_level"]}
    with pytest.raises(brt.BuildError, match="schema"):
        brt.validate_lock(legacy)


def test_requirement_list_is_pinned_and_stable(lock):
    target = lock["targets"]["macos-arm64"]
    reqs = brt.requirement_list(target)
    assert all("==" in r for r in reqs)
    assert reqs == sorted(reqs), "顺序不稳定的话每次构建的 diff 都没法看"
    assert f"numpy=={target['packages']['numpy']}" in reqs


# ---------------- 目标选择 ----------------------------------------------------
def test_default_target_follows_the_build_host(lock, monkeypatch):
    """默认目标只按**本机**挑。交叉构建必须显式 --target——否则一次手滑
    就会把 Windows 的 runtime 打进 macOS 的包里，而两边构建日志一模一样。"""
    monkeypatch.setattr(brt.os, "name", "posix")
    monkeypatch.setattr(brt.sys, "platform", "darwin")
    monkeypatch.setattr(brt.platform, "machine", lambda: "arm64")
    assert brt.default_target_name(lock) == "macos-arm64"

    monkeypatch.setattr(brt.platform, "machine", lambda: "x86_64")
    assert brt.default_target_name(lock) == "macos-x86_64"

    monkeypatch.setattr(brt.os, "name", "nt")
    monkeypatch.setattr(brt.platform, "machine", lambda: "AMD64")
    assert brt.default_target_name(lock) == "windows-amd64"


def test_linux_has_no_desktop_runtime_target(lock, monkeypatch):
    """Linux 没有桌面发行形态；含糊地挑一个目标只会产出没人要的东西。"""
    monkeypatch.setattr(brt.os, "name", "posix")
    monkeypatch.setattr(brt.sys, "platform", "linux")
    with pytest.raises(brt.BuildError, match="没有内置 runtime 的发行形态"):
        brt.default_target_name(lock)


# ---------------- 布局（两种上游发行版形状不同）--------------------------------
def test_layout_paths_match_each_upstream_distribution(lock):
    """site-packages 的落点必须跟着 kind 走，不是跟着构建机走。"""
    win = lock["targets"]["windows-amd64"]
    mac = lock["targets"]["macos-arm64"]
    out = Path("/tmp/rt")

    assert brt.site_packages(out, win) == out / "Lib" / "site-packages"
    assert brt.interpreter(out, win) == out / "python.exe"
    assert brt.site_packages(out, mac) == out / "lib" / "python3.13" / "site-packages"


def _fake_pbs_bin(root: Path) -> Path:
    """摆一个 python-build-standalone 的 bin/ 布局：实体 + 两个别名符号链接。"""
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    real = bin_dir / "python3.13"
    real.write_text("#!/bin/sh\n", encoding="utf-8")
    real.chmod(0o755)
    # `-config` 是 shell 脚本不是解释器，故意摆进来看它会不会被误选
    (bin_dir / "python3.13-config").write_text("#!/bin/sh\n", encoding="utf-8")
    for alias in ("python", "python3"):
        (bin_dir / alias).symlink_to("python3.13")
    return real


def test_alias_prune_only_removes_symlinks_and_keeps_the_real_interpreter(
        tmp_path, lock):
    """剪别名是为了省下 Tauri 拍平符号链接带来的 34 MiB。

    **只删符号链接**：万一上游哪天把 `bin/python3` 换成实体文件，这里必须
    原地不动——把唯一的解释器删掉，可比多 17 MiB 严重得多。
    """
    mac = lock["targets"]["macos-arm64"]
    real = _fake_pbs_bin(tmp_path)

    assert brt.interpreter(tmp_path, mac) == tmp_path / "bin" / "python3"
    removed, _ = brt.prune_aliases(tmp_path, mac)
    assert removed == 2
    assert not (tmp_path / "bin" / "python").exists()
    assert not (tmp_path / "bin" / "python3").exists()
    assert real.is_file(), "实体解释器绝不能被剪掉"
    assert brt.interpreter(tmp_path, mac) == real

    # 上游把 python3 换成实体文件的那一天：一个都不许删
    root2 = tmp_path / "upstream-changed"
    (root2 / "bin").mkdir(parents=True)
    solid = root2 / "bin" / "python3"
    solid.write_text("#!/bin/sh\n", encoding="utf-8")
    assert brt.prune_aliases(root2, mac) == (0, 0)
    assert solid.is_file()


def test_windows_target_never_gets_alias_pruning(tmp_path, lock):
    """embeddable 里根本没有这些别名，误动只会把 python.exe 弄没。"""
    win = lock["targets"]["windows-amd64"]
    (tmp_path / "bin").mkdir()
    assert brt.prune_aliases(tmp_path, win) == (0, 0)


def test_layout_matches_what_the_runtime_module_will_look_for(tmp_path, lock):
    """构建脚本放的解释器，engine/runtime.py 必须找得到——**剪别名前后都要**。

    两边各写各的查找顺序，是「构建期用一个、运行期用另一个，只有其中一个
    被冒烟验过」这类 bug 的源头。所以这里在真磁盘上比对两个实现。
    """
    from tavotto.engine import runtime

    mac = lock["targets"]["macos-arm64"]
    real = _fake_pbs_bin(tmp_path)

    # 剪之前：两边都该挑 bin/python3
    assert str(brt.interpreter(tmp_path, mac)) == runtime.resolve_python(str(tmp_path))

    # 剪之后：两边都该退到版本化实体名，且都不能选中 `-config` 脚本
    brt.prune_aliases(tmp_path, mac)
    got = runtime.resolve_python(str(tmp_path))
    assert got == str(real)
    assert str(brt.interpreter(tmp_path, mac)) == got
    assert not got.endswith("-config")


def test_interpreter_discovery_is_not_hardcoded_to_313(tmp_path):
    """升到 CPython 3.14 时，解释器不能突然找不到。

    写死 `python3.13` 的话，症状会是「安装文件不完整」——一个与真实原因
    （只是换了个小版本）毫不相干的提示。
    """
    from tavotto.engine import runtime

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    future = bin_dir / "python3.14"
    future.write_text("#!/bin/sh\n", encoding="utf-8")
    (bin_dir / "python3.14-config").write_text("#!/bin/sh\n", encoding="utf-8")
    assert runtime.resolve_python(str(tmp_path)) == str(future)


def test_windows_layout_is_still_found(tmp_path):
    """别为了 macOS 把 Windows 的 python.exe 弄丢。"""
    from tavotto.engine import runtime

    exe = tmp_path / "python.exe"
    exe.write_text("", encoding="utf-8")
    assert runtime.resolve_python(str(tmp_path)) == str(exe)


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
@pytest.mark.parametrize("name", ALL_TARGETS)
def test_manifest_records_everything_needed_to_diagnose(lock, name):
    target = lock["targets"][name]
    info = brt.manifest_dict(lock, name, target, "run-42",
                             "2026-08-18T00:00:00Z", "a" * 64, "passed")
    assert info["schema"] == brt.MANIFEST_SCHEMA
    assert info["target"] == name
    assert info["kind"] == target["kind"]
    assert info["python"]["version"] == target["python"]["version"]
    assert info["python"]["sha256"] == target["python"]["sha256"]
    assert info["platform"]["os"] == target["os"]
    assert info["platform"]["arch"] == target["arch"]
    assert info["packages"] == dict(sorted(target["packages"].items()))
    assert info["top_level"] == lock["top_level"]
    assert info["build"]["id"] == "run-42"
    assert info["build"]["built_at"].endswith("Z")
    assert info["build"]["lock_sha256"] == "a" * 64
    assert info["build"]["smoke"] == "passed"
    assert info["build"]["shipped"] == bool(target.get("shipped"))


def test_manifest_schema_matches_the_reader():
    """构建脚本写的 schema 与 engine/runtime.py 认的必须是同一个数字，
    否则打出来的包一装上就被自己判成「损坏」。"""
    from tavotto.engine import runtime
    assert brt.MANIFEST_SCHEMA == runtime.MANIFEST_SCHEMA
    assert brt.MANIFEST_NAME == runtime.MANIFEST_NAME


@pytest.mark.parametrize("name", ALL_TARGETS)
def test_manifest_is_readable_by_the_runtime_module(tmp_path, lock, name,
                                                    monkeypatch):
    """端到端：构建脚本产出的清单，engine/runtime.py 必须能读懂，
    而且**架构核对要判它是给本机的**（打桩成该目标的平台再问一次）。"""
    from tavotto.engine import runtime

    target = lock["targets"][name]
    root = tmp_path / "rt"
    root.mkdir()
    info = brt.manifest_dict(lock, name, target, "x", "2026-08-18T00:00:00Z",
                             "b" * 64, "passed")
    (root / brt.MANIFEST_NAME).write_text(json.dumps(info), encoding="utf-8")

    got = runtime.read_manifest(str(root))
    assert got is not None, f"{name}: engine/runtime.py 读不懂构建脚本写的清单"
    assert got["packages"]["scipy"] == target["packages"]["scipy"]

    monkeypatch.setattr(runtime, "host_os", lambda: target["os"])
    monkeypatch.setattr(runtime, "host_arch", lambda: target["arch"])
    assert runtime.platform_mismatch(got) == ""


# ---------------- 「这份 runtime 配不配得上这次构建」---------------------------
def _write_manifest(tmp_path, lock, name, smoke="passed"):
    target = lock["targets"][name]
    info = brt.manifest_dict(lock, name, target, "x", "2026-08-18T00:00:00Z",
                            "c" * 64, smoke)
    path = tmp_path / brt.MANIFEST_NAME
    path.write_text(json.dumps(info), encoding="utf-8")
    return path


def test_check_runtime_dir_accepts_a_matching_runtime(tmp_path, lock):
    path = _write_manifest(tmp_path, lock, "macos-arm64")
    info = brt.check_runtime_dir(path, require_smoke=True,
                                 host=("macos", "arm64"))
    assert info["target"] == "macos-arm64"


def test_check_runtime_dir_rejects_a_foreign_platform(tmp_path, lock):
    """**这条挡的是最贵的一种错**：Windows 的 runtime 被打进 .app。
    用户那边的症状是「渲染环境不可用」，而构建全程绿灯。"""
    path = _write_manifest(tmp_path, lock, "windows-amd64")
    with pytest.raises(brt.BuildError, match="windows"):
        brt.check_runtime_dir(path, require_smoke=True, host=("macos", "arm64"))


def test_check_runtime_dir_rejects_an_unshipped_target_in_release_builds(tmp_path, lock):
    """`shipped=false` = 「锁着版本，但没构建过也没冒烟过，不许发」。

    构建脚本里那句只是 warning，构建照常继续；发行链上以前没有任何一道闸
    拦它。`macos-latest` 这种浮动 runner 哪天换成 Intel，我们就会把一个
    文档里明写着「不支持 Intel」的目标发出去，而且全程绿灯。
    """
    path = _write_manifest(tmp_path, lock, "macos-x86_64")
    with pytest.raises(brt.BuildError, match="shipped=false"):
        brt.check_runtime_dir(path, require_smoke=True, host=("macos", "x86_64"))
    # 非发行构建（--allow-skip-smoke 那一档）照旧放行：本地要能拿它调试
    info = brt.check_runtime_dir(path, require_smoke=False, host=("macos", "x86_64"))
    assert info["target"] == "macos-x86_64"


def test_check_runtime_dir_rejects_a_foreign_arch(tmp_path, lock):
    path = _write_manifest(tmp_path, lock, "macos-x86_64")
    with pytest.raises(brt.BuildError, match="x86_64"):
        brt.check_runtime_dir(path, require_smoke=True, host=("macos", "arm64"))


def test_release_build_refuses_a_runtime_that_never_ran_its_smoke(tmp_path, lock):
    """`--allow-skip-smoke` 产出的中间件一个 import 都没跑过。
    混进安装包等于把验证推给用户。"""
    path = _write_manifest(tmp_path, lock, "macos-arm64",
                           smoke="skipped:foreign-host")
    with pytest.raises(brt.BuildError, match="冒烟"):
        brt.check_runtime_dir(path, require_smoke=True, host=("macos", "arm64"))
    # 开发态（不要求发行）仍然可用，只是不许发
    assert brt.check_runtime_dir(path, require_smoke=False,
                                 host=("macos", "arm64"))


def test_check_runtime_dir_rejects_an_unreadable_or_stale_manifest(tmp_path, lock):
    bad = tmp_path / brt.MANIFEST_NAME
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(brt.BuildError):
        brt.check_runtime_dir(bad, require_smoke=False)

    stale = tmp_path / "old" / brt.MANIFEST_NAME
    stale.parent.mkdir()
    stale.write_text(json.dumps({"schema": 1}), encoding="utf-8")
    with pytest.raises(brt.BuildError, match="schema"):
        brt.check_runtime_dir(stale, require_smoke=False)


def test_spec_and_build_script_share_one_judgement():
    """两处各写一遍的话，迟早一边放行另一边拦住——而放行的那一边才是发出去的。"""
    spec = (REPO / "packaging" / "tavotto.spec").read_text(encoding="utf-8")
    desktop = (REPO / "scripts" / "build_desktop.py").read_text(encoding="utf-8")
    assert "check_runtime_dir" in spec
    assert "check_runtime_dir" in desktop


# ---------------- 精简规则 ----------------------------------------------------
def test_prune_never_touches_public_testing_apis():
    """`numpy.testing` / `pandas._testing` 是公开 API，用户脚本里
    `from numpy.testing import assert_allclose` 很常见。按前缀匹配会把它们
    一起删掉，所以只认精确目录名。"""
    assert brt.PRUNE_DIRS == {"tests", "test", "__pycache__"}
    for keep in ("testing", "_testing", "_test_utils", "testsuite"):
        assert keep not in brt.PRUNE_DIRS


@pytest.mark.parametrize("kind,rel", [
    (brt.KIND_WINDOWS, ("Lib", "site-packages")),
    (brt.KIND_MACOS, ("lib", "python3.13", "site-packages")),
])
def test_prune_removes_test_dirs_only(tmp_path, kind, rel):
    target = {"kind": kind, "python": {"version": "3.13.15"}}
    site = tmp_path.joinpath(*rel)
    (site / "numpy" / "tests").mkdir(parents=True)
    (site / "numpy" / "tests" / "big.py").write_text("x" * 100)
    (site / "numpy" / "testing").mkdir(parents=True)
    (site / "numpy" / "testing" / "__init__.py").write_text("keep")
    (site / "pandas" / "_testing").mkdir(parents=True)
    (site / "pandas" / "_testing" / "__init__.py").write_text("keep")

    removed, freed = brt.prune(tmp_path, target)
    assert removed == 1 and freed >= 100
    assert not (site / "numpy" / "tests").exists()
    assert (site / "numpy" / "testing" / "__init__.py").is_file()
    assert (site / "pandas" / "_testing" / "__init__.py").is_file()


# ---------------- 预编译 ------------------------------------------------------
def test_precompile_uses_hash_invalidation_not_timestamps():
    """运行时带 `-B` 起（安装目录零写入），所以 .pyc 必须在构建期编好。

    默认的时间戳失效模式依赖源文件 mtime——安装程序解压、杀毒软件扫描、
    从 dmg 拷贝都可能改动它。一旦被判过期，`-B` 又不让重写，于是每次冷启动
    白编译一遍 numpy/matplotlib/pandas。哈希模式与 mtime 无关。
    """
    src = (REPO / "scripts" / "build_worker_runtime.py").read_text(encoding="utf-8")
    body = src.split("def precompile", 1)[1].split("\ndef ", 1)[0]
    assert "UNCHECKED_HASH" in body
    assert "force=True" in body, "重建时必须覆盖旧 .pyc"


def test_prune_runs_before_precompile():
    """反过来的话，刚编好的 __pycache__ 会被精简步骤当场删掉。"""
    src = (REPO / "scripts" / "build_worker_runtime.py").read_text(encoding="utf-8")
    build = src.split("\ndef build(", 1)[1]
    assert build.index("prune(out, target)") < build.index("precompile(out, target)")


def test_smoke_is_mandatory_unless_explicitly_waived():
    """冒烟跳过必须是**显式**的一个开关，不能因为「构建机跑不了」就自动放行。"""
    src = (REPO / "scripts" / "build_worker_runtime.py").read_text(encoding="utf-8")
    build = src.split("\ndef build(", 1)[1]
    assert "allow_skip_smoke" in build
    assert "不得直接发给用户" in build


# ---------------- 打包卫生 ----------------------------------------------------
def test_runtime_is_gitignored():
    """300 MiB 的平台二进制绝不进 Git。锁文件才是要提交的东西。"""
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "/runtime/" in ignore
    assert LOCK_PATH.is_file(), "锁文件必须在仓库里"


@pytest.mark.skipif(tomllib is None, reason="需要 tomllib（Python ≥ 3.11）")
def test_wheel_and_sdist_never_pick_up_the_runtime():
    """pip 用户拿到的是轻量包，不该被塞进一堆平台二进制。"""
    cfg = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    build = cfg["tool"]["hatch"]["build"]
    assert any(p.startswith("runtime") for p in build.get("exclude", [])), \
        "pyproject 必须显式把 runtime 排除在构建之外"
    assert not any("runtime" in a for a in build.get("artifacts", [])), \
        "artifacts 是「强行收回被 gitignore 的东西」，runtime 绝不能在里面"
    sdist = cfg["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
    assert not any(p.strip("/").startswith("runtime") for p in sdist)


def test_spec_ships_runtime_when_it_exists():
    """没跑过构建脚本的开发机上，spec 必须照常工作（只是产物不可发行）。"""
    spec = (REPO / "packaging" / "tavotto.spec").read_text(encoding="utf-8")
    assert "runtime-manifest.json" in spec, "spec 要按清单判断 runtime 在不在"
    assert "TAVOTTO_REQUIRE_RUNTIME" in spec, "发行流水线要能把「必须带」打开"
    assert '"runtime"' in spec, "runtime 要作为 datas 进包"


def test_spec_ships_every_module_the_worker_imports():
    """worker 平铺 import 的**整条传递闭包**都必须作为真 .py 进包。

    worker 是**外部解释器**按路径起的子进程，只编进 PyInstaller 归档它读不到。
    漏一个的表现是「装完的桌面版一渲染就 ModuleNotFoundError」，而源码模式下
    一切正常——所以这条不能靠人记得改 spec，得从 worker.py 自己的 import 反推。

    **必须是传递闭包，不能只看 worker.py 一层。** 只看一层的时候，
    `manifest.py` 新引进来的 `pathgeom` 一路绿灯到了 macOS / Windows 的
    冒烟腿上（`workerd 会话建立失败…pathgeom 在当前渲染环境里没有` →
    回退的 Python 池也崩）。一个只查半条链的门禁比没有门禁更坏：它在报平安。
    """
    import ast

    engine = REPO / "src" / "tavotto" / "engine"

    def flat_imports(path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {a.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                 for a in node.names}
        names |= {node.module for node in ast.walk(tree)
                  if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module}
        # 只要**同目录**的平铺模块：worker 把 engine/ 插进 sys.path 之后
        # `import manifest` 找的就是它们；`matplotlib` 这类第三方不在此列
        return {n for n in names if (engine / f"{n}.py").is_file()}

    closure, todo = set(), ["worker"]
    while todo:
        name = todo.pop()
        if name in closure:
            continue
        closure.add(name)
        todo += list(flat_imports(engine / f"{name}.py"))
    siblings = {f"{n}.py" for n in closure}
    assert "patchspec.py" in siblings, "用例前提：worker 确实平铺 import 了 patchspec"
    assert "pathgeom.py" in siblings, \
        "用例前提：manifest 确实平铺 import 了 pathgeom（传递闭包这一层的样本）"

    spec = (REPO / "packaging" / "tavotto.spec").read_text(encoding="utf-8")
    shipped = set(re.findall(r'"(\w+\.py)"', spec))
    missing = siblings - shipped
    assert not missing, f"packaging/tavotto.spec 漏了 worker 要用的模块: {missing}"


def test_release_chain_refuses_to_ship_without_the_runtime():
    """漏了 runtime 照样能编出安装包，而那个包只有到了用户手里才暴露问题。

    **两个平台**都由发行工作流扛：构建 sidecar 时开 TAVOTTO_REQUIRE_RUNTIME
    （spec 当场失败），安装包经 tauri.conf.json 的 bundle.resources 把整个
    sidecar 目录（含 _internal/runtime）收走，打完还要过
    `--expect-source bundled --expect-runtime` 的真渲染冒烟。
    """
    wf = (REPO / ".github" / "workflows" / "desktop-tauri.yml").read_text(
        encoding="utf-8")
    assert "TAVOTTO_REQUIRE_RUNTIME" in wf, "构建必须能把「必须带 runtime」打开"
    assert "--expect-source bundled" in wf, "打包后必须断言渲染真的走了内置 runtime"
    assert "--expect-runtime" in wf, "还要断言 runtime 本身 expected + valid"
    conf = (REPO / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    assert "../dist/Tavotto" in conf, "NSIS/.app 必须把整个 sidecar 目录作为资源收走"


def test_macos_ci_no_longer_fakes_a_worker_env():
    """macOS 这条腿曾经现建一个 worker-env 再设 TAVOTTO_WORKER_PYTHON。

    代价是整条门禁失去意义：借来的解释器让冒烟一路绿灯，而「内置 runtime
    根本没打进安装包」这件事没有任何一处会发现——那正是用户装完打不开图的原因。
    这条用例盯着它别被人「顺手加回来」。
    """
    wf = (REPO / ".github" / "workflows" / "desktop-tauri.yml").read_text(
        encoding="utf-8")
    assert "TAVOTTO_WORKER_PYTHON=" not in wf, \
        "发行冒烟不许借用外部解释器——那会让「runtime 没打进去」全程绿灯"
    assert "python -m venv worker-env" not in wf


def test_orphan_check_does_not_rely_on_a_dead_parent_pid():
    """孤儿 worker 的检测不许再用 `pgrep -P <sidecar pid>`。

    那条断言恒真：进程一终止，它还活着的子进程立刻被重新挂到 init/launchd
    （PID 1）名下——PPID 在父进程死亡的那一刻就变了，而这段检查恰恰跑在
    「已确认 sidecar 退出」之后。于是不管有没有真的泄漏，都查不到任何东西，
    `ok("无孤儿子进程")` 照打。**空转的门禁比没有门禁更坏**：它还在报平安。
    正确做法是按命令行内容做全局扫描（`smoke_app._leftover_workers`），
    两个冒烟脚本共用同一把尺。
    """
    src = (REPO / "scripts" / "smoke_desktop.py").read_text(encoding="utf-8")
    # 只盯**调用形态**（argv 列表里的 "pgrep"），别把解释这件事的注释也判红
    assert '"pgrep"' not in src, \
        "父进程已退出时按 PPID 查孤儿必然一无所获——这条断言恒真"
    # 两条判据缺一不可：pid 快照精确（连没有命令行特征的 tavotto-workerd 也
    # 盖得住），命令行扫描兜住父子关系没记全的那些
    assert "_descendants(proc.pid)" in src, "退出前要把后代 pid 快照下来"
    assert "_leftover_workers" in src, "还要按命令行内容做一次全局扫描"


def test_macos_release_signs_and_verifies_every_nested_macho():
    """`--deep` 签不到 Resources 里的内置 runtime（500+ 个 .so）：它们不被
    识别为嵌套代码。漏签的表现是公证 Invalid，或者更坏——公证过了但
    Gatekeeper 在用户机器上拦下某个 .so。"""
    wf = (REPO / ".github" / "workflows" / "desktop-tauri.yml").read_text(
        encoding="utf-8")
    assert "codesign_macos.py sign" in wf
    assert "codesign_macos.py verify" in wf
    assert "--expect-arch" in wf, "还要核对架构：混进另一个架构的 .so 签名照样能过"


def _release_signing_gate() -> str:
    """desktop-tauri.yml 里那一步「发行签名门禁」的正文。"""
    wf = (REPO / ".github" / "workflows" / "desktop-tauri.yml").read_text(
        encoding="utf-8")
    assert "发行签名门禁" in wf, "签名门禁整个不见了"
    step = wf.split("发行签名门禁", 1)[1].split("\n      - name:", 1)[0]
    assert "IS_RELEASE_BUILD" in step, "门禁必须只对发行构建生效"
    return step


def test_release_signing_gate_still_hard_fails_on_everything_but_authenticode():
    """Windows 的 Authenticode 是这道门禁**唯一**的例外，别顺手再开第二个。

    2026-08-22（v0.9.0）把 SignPath 那条从硬失败降成警告：拿不到开源订阅之前，
    它挡掉的不是「未签名的安装包」而是**整个 Windows 桌面版**，还连带
    updater-manifest 的两平台硬要求一起落空，于是 macOS 用户也收不到更新。

    但这条例外**极容易被复制**——下一个被某个 secret 卡住的人会照着把 macOS
    那几条也改成 warning，而那时门禁就只剩一句好听的话。所以逐条钉死：更新包
    的 minisign 私钥与 macOS 的证书/身份/公证账号仍必须让发行构建**失败**。
    """
    step = _release_signing_gate()
    hard = [ln.strip() for ln in step.splitlines() if "missing+=" in ln]
    joined = "\n".join(hard)
    for cred in ("TAURI_SIGNING_PRIVATE_KEY", "MACOS_CERTIFICATE",
                 "MACOS_SIGN_IDENTITY", "APPLE_ID"):
        assert cred in joined, f"{cred} 不再让发行构建失败——门禁被掏空了"
    assert "exit 1" in step, "凑齐 missing 之后必须真的退出非零"
    # 例外只有这一个，而且不许扩散到别处
    assert "SIGNPATH" not in joined,         "SignPath 是自觉的例外（见 docs/code-signing-policy.md），不该回到硬失败；"         "要恢复的话连同这条用例一起改"


def test_unsigned_windows_release_is_loud_not_silent():
    """降级成 warning 的那一支必须**看得见**，否则就是 P1-07 原本要挡的东西。

    审计 P1-07 的真正指控不是「没签名」，是「没签名而且工作流全绿」。所以
    例外成立的前提是它自己会喊：运行页顶部一条 annotation + job summary 里
    一段说明。把这两样删掉，这道门禁就退化成一句注释。
    """
    step = _release_signing_gate()
    assert "::warning" in step, "未签名的发行必须在运行页顶部留下 annotation"
    assert "GITHUB_STEP_SUMMARY" in step,         "还要写进 job summary——日志第 33 步里的一行 warning 没人会翻到"
    assert "minisign" in step,         "摘要要说清更新链仍可信，否则读的人会以为自动更新也不安全了"


# ---------------- 真产物（构建过才跑）------------------------------------------
RUNTIME_DIR = REPO / "runtime"
_has_runtime = (RUNTIME_DIR / brt.MANIFEST_NAME).is_file()


@pytest.mark.skipif(not _has_runtime,
                    reason="本机没构建过 runtime（跑 scripts/build_worker_runtime.py）")
def test_built_runtime_ships_licences_and_notices():
    """AGPL 的项目分发别人的二进制，许可证义务是硬的：BSD/MIT/HPND/Apache-2.0
    全都要求随分发附带版权声明。用户装到的是我们打的包，义务落在我们身上。"""
    lic = RUNTIME_DIR / "licenses"
    assert lic.is_dir(), "构建产物里必须有 licenses/"
    notices = lic / "THIRD-PARTY-NOTICES.md"
    assert notices.is_file(), "必须有一份索引（NOTICE）"
    text = notices.read_text(encoding="utf-8")
    assert "CPython" in text
    for pkg in PROMISED:
        assert pkg.lower() in text.lower(), f"NOTICE 里没有 {pkg}"
    assert (lic / "cpython").is_dir()


@pytest.mark.skipif(not _has_runtime, reason="本机没构建过 runtime")
def test_built_runtime_manifest_passes_its_own_gate():
    """构建脚本产出的东西，必须过构建脚本自己的验收（本机同平台同架构）。"""
    info = brt.check_runtime_dir(RUNTIME_DIR / brt.MANIFEST_NAME,
                                 require_smoke=False)
    assert info["packages"]["matplotlib"]
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    target = lock["targets"][info["target"]]
    assert info["packages"] == dict(sorted(target["packages"].items())), \
        "产物里的版本与锁文件对不上——锁文件改过但没重新构建？"


@pytest.mark.skipif(not _has_runtime, reason="本机没构建过 runtime")
def test_built_runtime_is_usable_by_the_engine_and_really_renders():
    """**不是「文件存在」测试**：真去起那个解释器，import 整套科学栈，
    再画一张 PDF 出来。

    「装完了但用不了」是最难查的一档（缺 VC 运行库、某个 .so 没签名被
    Gatekeeper 拦下），只看清单说装了什么不算数。
    """
    import subprocess

    from tavotto.engine import runtime

    st = runtime.status()
    assert st["valid"] is True, f"engine 认为这份 runtime 不可用: {st}"
    py = st["python"]

    got = runtime.probe_packages(py, ["numpy", "matplotlib", "pandas",
                                      "scipy", "seaborn", "PIL"])
    missing = [n for n, v in got.items() if not v]
    assert not missing, f"这些包 import 不到: {missing}"

    code = (
        "import matplotlib; matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt, numpy as np, seaborn as sns\n"
        "fig, ax = plt.subplots()\n"
        "sns.lineplot(x=np.arange(5.0), y=np.arange(5.0) ** 2, ax=ax)\n"
        "import io; b = io.BytesIO(); fig.savefig(b, format='pdf')\n"
        "assert b.getbuffer().nbytes > 500\n"
        "print('render-ok')\n"
    )
    proc = subprocess.run([py, *runtime.child_args(), "-c", code],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
                          env=runtime.child_env())
    assert "render-ok" in proc.stdout, f"真实渲染失败：\n{proc.stderr[-2000:]}"


@pytest.mark.skipif(not _has_runtime, reason="本机没构建过 runtime")
def test_built_runtime_does_not_write_into_itself_while_rendering():
    """安装目录（Program Files / 签过名的 .app）一个字节都不许写。

    macOS 上后果更硬：往签过名的 .app 里写 `__pycache__` 当场破坏代码签名，
    用户下次启动看到的是「应用已损坏」。
    """
    import subprocess

    from tavotto.engine import runtime

    before = {p for p in RUNTIME_DIR.rglob("*") if p.is_file()}
    py = runtime.status()["python"]
    code = ("import matplotlib; matplotlib.use('Agg')\n"
            "import matplotlib.pyplot as plt, pandas, scipy\n"
            "plt.subplots()\nprint('ok')\n")
    subprocess.run([py, *runtime.child_args(), "-c", code],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
                   env=runtime.child_env())
    after = {p for p in RUNTIME_DIR.rglob("*") if p.is_file()}
    created = after - before
    assert not created, f"渲染往安装目录里写了东西: {sorted(created)[:10]}"
