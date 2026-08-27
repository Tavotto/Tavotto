"""workerd 的**发行侧**看护：进不进包、落在哪、CI 有没有真验过。

为什么值得一个文件：找不到二进制时 `pool._new_worker()` **静默**回退到 Python
渲染池——这是刻意的降级（加速件起不来不该让渲染整个不可用），代价是「没打进
包」在界面上毫无症状：功能一样不缺，只是慢，日志里也只有一行 warning。
所以这条链的每一环都得有人盯着，而不是等用户来报「怎么有点卡」。

三个环节：
  1. `packaging/tavotto.spec` 把它收进 `_internal/`，缺了当场失败；
  2. `scripts/build_desktop.py` 在 PyInstaller 之前 cargo build，并回头确认落点；
  3. CI 两条腿（desktop-tauri 的发布链 + ci.yml 的 windows-exe-smoke）
     都**不设 TAVOTTO_WORKERD**，靠 `--expect-control-plane workerd` 断言
     产物自带的那份真的被找到并用上了。
"""
import re
import sys
from pathlib import Path

import pytest

from tavotto.engine import workerd_client

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import build_desktop as bd  # noqa: E402

SPEC = (REPO / "packaging" / "tavotto.spec").read_text(encoding="utf-8")
DESKTOP_WF = (REPO / ".github" / "workflows"
              / "desktop-tauri.yml").read_text(encoding="utf-8")
CI_WF = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def test_the_spec_ships_workerd_as_a_binary_not_a_data_file():
    """必须走 binaries：PyInstaller 只对 binaries 保留可执行位。

    当成 datas 收进去的表现最难查——文件明明在，起进程时 Permission denied，
    而 workerd 起不来又是静默回退的。
    """
    assert "binaries=binaries" in SPEC, "Analysis 得真的用上这份 binaries"
    assert 'binaries = [(str(WORKERD), ".")]' in SPEC, \
        'workerd 要落在收集根目录（"."），也就是冻结后的 sys._MEIPASS'


def test_the_spec_takes_workerd_from_cargos_own_output_dir():
    """约定位置就是 cargo 的产出目录，不再造第二个落点。

    `workerd_client._dev_tree_candidates()` 认的也是它——两边一旦分叉，
    「开发机上好好的、打出来的包里没有」这种事就会周期性地发生。
    """
    assert '"workerd" / "target" / "release"' in SPEC
    assert "TAVOTTO_WORKERD_BIN" in SPEC, "交叉编译/分步构建要能指到别处"


def test_the_spec_refuses_to_build_without_workerd():
    """缺了就当场失败——不能像内置 runtime 那样「没有也行」。"""
    assert "raise SystemExit(" in SPEC and "缺少 Rust supervisor 二进制" in SPEC
    assert "cargo build --release" in SPEC, "报错要直接给出补救命令"


@pytest.mark.parametrize("path_fragment", ["_MEIPASS", "_internal"])
def test_find_workerd_looks_where_the_spec_puts_it(path_fragment):
    """spec 的落点必须在 `find_workerd()` 的 frozen 候选路径里。

    onedir 产物里 `sys._MEIPASS` 就是 `_internal/`，两条路径其实指同一处；
    两条都留着是因为 PyInstaller 换过一次布局。
    """
    src = Path(workerd_client.__file__).read_text(encoding="utf-8")
    assert path_fragment in src


def test_the_packaged_name_is_the_one_the_client_looks_for():
    """构建脚本拼的文件名与客户端找的必须是同一个（Windows 的 .exe 后缀）。"""
    assert bd.WORKERD_NAME == workerd_client.EXE_NAME


def test_build_desktop_builds_workerd_before_pyinstaller_and_checks_the_result():
    """顺序即依赖：cargo → PyInstaller。之后还要回头确认二进制真的落进去了。"""
    src = (REPO / "scripts" / "build_desktop.py").read_text(encoding="utf-8")
    # 两个探针都用**容忍空白的正则**：
    #
    # * `"-m", "PyInstaller"` 经 `ruff format` 之后是分行的；
    # * `build_workerd()` 更要紧——原本写的是 `src.index("build_workerd()")`，
    #   而它命中的是**函数定义**那一行（`def build_workerd() -> Path:` 里就含
    #   这个子串），位置恒在文件靠前，于是「调用在 PyInstaller 之前」这条断言
    #   **无论调用挪到哪里都成立**。实测：把调用整个挪到 PyInstaller 之后，
    #   这条用例照样绿。改成只认**语句位置上的调用**。
    pyinstaller = re.search(r'"-m",\s*"PyInstaller"', src)
    assert pyinstaller, "build_desktop.py 里找不到 PyInstaller 调用——改名了？"
    call = re.search(r"(?m)^[ \t]+build_workerd\(\)[ \t]*$", src)
    assert call, "build_desktop.py 里找不到 build_workerd() 的调用（不是定义）"
    assert call.start() < pyinstaller.start(), (
        "build_workerd() 的调用跑到 PyInstaller 之后了——顺序即依赖："
        "spec 从 cargo 的产物位置取二进制，先打包就什么都取不到")
    assert '"_internal" / WORKERD_NAME' in src, \
        "打完要确认 _internal/ 里真有它——打包器换版本改落点是无声的"
    assert "shutil.which(\"cargo\")" in src, "没有 cargo 要给可读的错误并中止"


def test_the_release_workflow_gates_the_rust_crate():
    """fmt / clippy -D warnings / cargo test 三件套（Linux 上跑一份即可）。"""
    assert "cargo fmt --check" in DESKTOP_WF
    assert "cargo clippy --all-targets -- -D warnings" in DESKTOP_WF
    assert "cargo test" in DESKTOP_WF
    assert "workspaces: |" in DESKTOP_WF and "workerd" in DESKTOP_WF, \
        "两个 crate 都要进 rust-cache，否则每次发布都从零编"


@pytest.mark.parametrize("name", ["desktop-tauri.yml", "ci.yml"])
def test_the_smoke_legs_assert_the_control_plane_without_forcing_it(name):
    """两条冒烟腿都**不设 TAVOTTO_WORKERD**，只断言结果。

    设了就等于自己把答案填上：要验的恰恰是「产物自带的那份被自动找到」。

    **按文件名参数化，不要按文件内容**：pytest 会用参数值拼测试 id，再把 id
    塞进 `PYTEST_CURRENT_TEST` 环境变量。Windows 的环境变量上限是 32767 字符
    ——工作流文件长到那个数就 `ValueError: the environment variable is longer
    than 32767 characters`，整条用例在别的平台上全绿、只在 Windows 上炸，而且
    报错跟被测的东西毫无关系。
    """
    wf = {"desktop-tauri.yml": DESKTOP_WF, "ci.yml": CI_WF}[name]
    assert "--expect-control-plane workerd" in wf, f"{name} 少了控制面断言"
    # 注释里提它是好事（写清楚为什么不设），要挡的是真把它设进环境
    steps = "\n".join(ln for ln in wf.splitlines()
                      if not ln.lstrip().startswith("#"))
    assert "TAVOTTO_WORKERD" not in steps, \
        f"{name} 里不该真的设 TAVOTTO_WORKERD——自动发现才是要验的东西"


def test_ci_builds_and_asserts_the_binary_in_the_windows_artifact():
    """windows-exe-smoke 直接调 PyInstaller，所以它自己要先 cargo build。"""
    assert "cargo build --release --manifest-path workerd/Cargo.toml" in CI_WF
    assert "_internal\\tavotto-workerd.exe" in CI_WF, \
        "产物里有没有它，先用一次 Test-Path 确认，再谈冒烟"


def test_the_release_profile_stays_small_and_stripped():
    """workerd 是随桌面产物一起下发的，体积与符号表都算在安装包上。"""
    cargo = (REPO / "workerd" / "Cargo.toml").read_text(encoding="utf-8")
    assert 'lto = "thin"' in cargo
    assert "strip = true" in cargo
