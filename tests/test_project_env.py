"""Compatibility Bridge Session 7：项目 Python 环境自动发现与无感切换。

四层看护：

* **发现**：只认带 `pyvenv.cfg` 的真 venv；范围锁在项目根内（不上溯、不顺
  软链接跳出去）；同时存在多个时的优先级是确定的，不是随机的。
* **体检**：真的在候选解释器里跑一次——`sys.executable` / `sys.prefix` 必须
  是那个 venv 的（只断言「字符串选中了 /tmp/.venv/bin/python」证明不了任何
  事）；matplotlib 与缺的那个模块分别确认；**project venv 不需要安装 Tavotto
  本体也能起 worker**。
* **切换**：整个解释器为单位，绝不混装 site-packages；worker 身份包含解释器；
  热态 / 干净重放 / 导出用同一个；项目之间互不串环境；用户显式选择压过自动。
* **边界**：只有 `missing_dependency` 触发；一次最多自动切一次；不装任何东西。

真 venv 用例**不联网**：从当前 worker 解释器建一个 venv，matplotlib 就用宿主
那份（`support.venvfixture` 负责——注意 `--system-site-packages` 继承的是
**base 解释器**，宿主自己是 venv 时还得把它自己的 site-packages 接进来，
见 #225）；要「这个环境里有而别处没有」的包时，往它自己的 site-packages 里写
一个纯 Python 的 fixture 模块。
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from support import venvfixture
from tavotto.engine import (
    config as engine_config,
    execspec,
    pool as engine_pool,
    projectenv,
)

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

#: 只在测试建出来的 venv 里存在的纯 Python 包。名字刻意不像任何真包——
#: 它必须在宿主解释器里 import 不到，否则整组 fallback 用例会假绿。
FIXTURE_MODULE = "tavotto_probe_fixture"

SCRIPT = f"""\
import {FIXTURE_MODULE}          # 只有项目 .venv 里有
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([1, 2, 3], [4, 5, 6])
fig.savefig("Fig1.pdf")
"""


# --------------------------------------------------------------- 工具
def fake_venv(path: Path, *, cfg: bool = True, exe: bool = True) -> Path:
    """一个**形状**上的 venv（不能执行）——发现规则的用例只关心形状。"""
    path.mkdir(parents=True, exist_ok=True)
    if cfg:
        (path / "pyvenv.cfg").write_text("home = /nowhere\n", encoding="utf-8")
    if exe:
        rel = "Scripts/python.exe" if os.name == "nt" else "bin/python"
        exe_path = path / rel
        exe_path.parent.mkdir(parents=True, exist_ok=True)
        exe_path.write_text("", encoding="utf-8")
    return path


def real_venv(root: Path, *, name: str = ".venv", with_fixture: bool = True) -> Path:
    """在 `root` 下建一个**能执行**的 venv（创建细节见 `support.venvfixture`）。

    `with_fixture` 往它自己的 site-packages 写一个纯 Python 模块——「这个环境
    有而别处没有」这件事就是靠它成立的。
    """
    venv = venvfixture.make_project_venv(root, name, python=WORKER_PY)
    if with_fixture:
        add_fixture_module(venv)
    return venv


def add_fixture_module(venv: Path) -> Path:
    mod = venvfixture.site_packages(venv) / f"{FIXTURE_MODULE}.py"
    mod.write_text("VALUE = 42\n", encoding="utf-8")
    return mod


@pytest.fixture(autouse=True)
def _clean_env_state():
    """每个用例前后都把项目环境的进程缓存清干净。

    `_resolved` / `_attempted` 是模块级的：留着上一个用例的结论，下一个用例
    会在「已经切过了」的状态下开始，重试上限那条用例首当其冲变成假绿。
    """
    projectenv.reset_cache()
    engine_pool.reset_worker_python()
    yield
    projectenv.reset_cache()
    engine_pool.reset_worker_python()


# ----------------------------------------------------------- 夹具自身
# 下面三条测的是**夹具**而不是产品。它们在这里，是因为夹具的前提失效时，红的
# 是本文件里十几条看起来在测别的东西的用例（#225：14 条全红，第一条报
# `assert 'project_env_no_matplotlib' == 'project_env_module_missing'`，
# 读的人第一反应是产品坏了）。


@needs_worker
def test_the_fixture_venv_inherits_from_the_host_not_from_its_base(tmp_path):
    """宿主解释器**自己是个 venv** 时，它自己的包也要进得来。

    这就是实验室 runner 的形状：`_lab-qualification.yml` 把 Tavotto 与按
    `packaging/runtime-lock.json` 钉版的科学栈装进一个一次性 venv，pytest 与
    `WORKER_PY` 都是它，而 base（`/usr/bin/python3`）按设计不带 matplotlib。
    `--system-site-packages` 继承的是 **base** 而不是那个 venv，于是项目 venv
    里 `import matplotlib` 失败（#225）。

    marker 是 matplotlib 的**替身**：实验室 runner 上 matplotlib 正是「只在
    宿主自己的 site-packages 里、base 里没有」的那种包。这里不直接拿
    matplotlib 当尺子，是因为开发机与 GitHub runner 上 base 恰好也带着它——
    那把尺子量不到这一维，用例会恒真。
    """
    host_root = tmp_path / "host"
    host_root.mkdir()
    host = venvfixture.make_project_venv(host_root, "hostenv", python=WORKER_PY)
    host_python = venvfixture.interpreter_of(host)
    assert host_python, host
    # **只写进宿主自己的 site-packages**——base 里没有这个名字。
    (venvfixture.site_packages(host) / "tavotto_host_only_marker.py").write_text(
        "VALUE = 7\n", encoding="utf-8"
    )

    project = tmp_path / "figs"
    project.mkdir()
    venv = venvfixture.make_project_venv(project, ".venv", python=host_python)
    got = subprocess.run(
        [
            venvfixture.interpreter_of(venv),
            "-I",
            "-c",
            "import tavotto_host_only_marker as m; print(m.VALUE)",
        ],
        capture_output=True,
        text=True,
        # 失败时读的是子解释器的 traceback（Windows 上按代码页写）——不钉编码的话
        # `got.stderr` 会在这条断言真的红掉的那一刻变成空的。
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == "7"


def test_the_inherited_path_file_is_pure_ascii_whatever_the_paths_are():
    """接目录用的那个 `.pth` **字节必须全是 ASCII**——那是「谁来解码都一样」
    的唯一保证。

    `site` 怎么读 `.pth` 由目标解释器决定：实测 3.11 的 `site.addpackage` 用
    `encoding="locale"`，3.13 才先试 UTF-8、失败再退回 locale。路径里有非
    ASCII（`C:\\Users\\张三`）时，写 UTF-8 和写 locale **各对一头、各错一头**，
    错的那头解出另一串字符、目录「不存在」、`site` 静默跳过。ASCII 是两种读法
    的交集（Windows 的每个 ANSI 代码页与 UTF-8 都是 ASCII 的超集）。

    **判据量的是字节，不是「能不能 import」**：本机 locale 是 UTF-8，两种读法
    在这里恰好一致，import 那把尺子量不到这一维，会恒真。
    """
    dirs = ["/tmp/plain/site-packages", "/Users/张三/env/site-packages"]
    line = venvfixture._pth_line(dirs)
    assert line.encode("utf-8").isascii(), line
    assert line.startswith("import "), "不以 `import ` 开头的话 site 会把它当路径，不执行"

    # ASCII 不能是「把路径丢了」换来的：跑一遍 `site` 会 exec 的那一行，
    # 看路径是不是逐字还原回来。
    before = list(sys.path)
    try:
        exec(line, {})  # noqa: S102 —— 这正是 site.addpackage 对 `import ` 行做的事
        added = sys.path[len(before) :]
    finally:
        sys.path[:] = before
    assert added == dirs


@needs_worker
def test_the_fixture_grades_a_broken_premise_instead_of_blaming_the_product(tmp_path):
    """前提不成立时给**分档**结论：这些成因是不同的答案，不许合并。

    「宿主自己就没有 matplotlib」是机器/环境侧的事（夹具不装任何东西，帮不上
    忙）；「宿主有、没接进来」和「遮蔽失效」都是夹具侧的缺陷，但要改的地方
    不同；「根本没问出宿主的情况」是**「不知道」这一档**，把它并进「宿主没装」
    就是拿一次失败的观测当证据。合并成一句「环境有问题」等于把该找的人也合并了。
    """
    bare = tmp_path / "bare"
    subprocess.run(
        [WORKER_PY, "-m", "venv", "--without-pip", str(bare)],
        check=True,
        capture_output=True,
        timeout=300,
    )
    bare_python = venvfixture.interpreter_of(bare)
    assert bare_python, bare

    # 1) 宿主自己就没有 matplotlib → 机器/环境侧
    with pytest.raises(venvfixture.VenvFixtureError) as err:
        venvfixture.verify(bare, bare_python)
    assert err.value.code == venvfixture.PREMISE_HOST_NO_MATPLOTLIB

    # 2) **同一个 venv、同一次体检**，只换一个带 matplotlib 的宿主 → 夹具侧。
    #    换的是宿主这一个变量，结论就该换一档——两档没有被合并成一句话。
    with pytest.raises(venvfixture.VenvFixtureError) as err:
        venvfixture.verify(bare, WORKER_PY)
    assert err.value.code == venvfixture.PREMISE_NOT_INHERITED

    # 3) 宿主那次探测自己就失败了 → 「不知道」是独立一档。并进 1) 的话，
    #    一次坏掉的观测会被当成「宿主没装 matplotlib」的证据。
    with pytest.raises(venvfixture.VenvFixtureError) as err:
        venvfixture.verify(bare, WORKER_PY, {"_error": "宿主探测起不来"})
    assert err.value.code == venvfixture.PREMISE_HOST_UNREADABLE

    # 4) 遮蔽失效 → 夹具侧的另一档（把替身换成一个 import 得动的模块）
    ok_root = tmp_path / "ok"
    ok_root.mkdir()
    good = venvfixture.make_project_venv(ok_root, ".venv", python=WORKER_PY)
    (venvfixture.site_packages(good) / "tavotto.py").write_text(
        "__version__ = 'not really'\n", encoding="utf-8"
    )
    with pytest.raises(venvfixture.VenvFixtureError) as err:
        venvfixture.verify(good, WORKER_PY)
    assert err.value.code == venvfixture.PREMISE_MASK_INEFFECTIVE


@needs_worker
def test_make_project_venv_never_returns_a_venv_it_has_not_verified(tmp_path, monkeypatch):
    """体检的结论要**进控制流**。

    「跑了体检」和「结论挡住了下一步」是两件事：把结论记进日志然后照样把 venv
    还回去的话，前提失效时红的仍然是十几条断言产品错误码的用例，分档白分。
    """
    boom = venvfixture.VenvFixtureError(venvfixture.PREMISE_NOT_INHERITED, "变异")
    seen: list[str] = []

    def _spy(venv, python, facts=None):
        seen.append(str(venv))
        raise boom

    monkeypatch.setattr(venvfixture, "verify", _spy)
    root = tmp_path / "p"
    root.mkdir()
    with pytest.raises(venvfixture.VenvFixtureError) as err:
        venvfixture.make_project_venv(root, ".venv", python=WORKER_PY)
    assert err.value is boom
    assert seen, "`make_project_venv` 根本没体检"


# --------------------------------------------------------------- 发现
def test_only_a_real_venv_counts(tmp_path):
    """光有目录名不算数——项目里叫 `env/` 的经常是别的东西。"""
    assert projectenv.interpreter_of(fake_venv(tmp_path / "a" / ".venv")) is not None
    # 有 pyvenv.cfg 没解释器、有解释器没 pyvenv.cfg，两种都不是可用环境
    assert projectenv.interpreter_of(fake_venv(tmp_path / "b" / ".venv", exe=False)) is None
    assert projectenv.interpreter_of(fake_venv(tmp_path / "c" / ".venv", cfg=False)) is None
    assert projectenv.interpreter_of(tmp_path / "d" / "nope") is None


def test_discovery_prefers_the_nearest_then_dotvenv(tmp_path):
    """离脚本最近的一层优先；同一层里 `.venv` > `venv` > `env`。"""
    root = tmp_path / "paper"
    (root / "src" / "plots").mkdir(parents=True)
    fake_venv(root / "env")
    fake_venv(root / "venv")
    fake_venv(root / ".venv")
    fake_venv(root / "src" / "venv")
    found = projectenv.discover(root, "src/plots/figure.py")
    assert [Path(p).relative_to(root).as_posix() for p in found] == [
        "src/venv",
        ".venv",
        "venv",
        "env",
    ]


def test_discovery_is_deterministic(tmp_path):
    """同一棵目录树问十次给同一个答案——「随机选一个」是不可诊断的。"""
    root = tmp_path / "paper"
    root.mkdir()
    for name in (".venv", "venv", "env"):
        fake_venv(root / name)
    answers = {tuple(projectenv.discover(root, "figure.py")) for _ in range(10)}
    assert len(answers) == 1


def test_discovery_never_leaves_the_project_root(tmp_path):
    """项目外的 venv 一个都不认——那是**别人的**项目。"""
    fake_venv(tmp_path / ".venv")  # 项目根的上一层
    root = tmp_path / "paper"
    root.mkdir()
    assert projectenv.discover(root, "figure.py") == []


@pytest.mark.skipif(os.name == "nt", reason="需要 POSIX 软链接语义")
def test_discovery_does_not_follow_a_symlink_out_of_the_project(tmp_path):
    """`.venv -> ~/envs/paper`：字符串看着在项目内，实体在项目外。

    按字符串前缀判就会把项目外的环境当成项目自带的——发现范围必须按
    realpath 收敛，否则「只在项目内找」这条边界形同虚设。
    """
    outside = fake_venv(tmp_path / "elsewhere" / "env")
    root = tmp_path / "paper"
    root.mkdir()
    (root / ".venv").symlink_to(outside, target_is_directory=True)
    assert projectenv.discover(root, "figure.py") == []


def test_module_name_must_be_a_bare_identifier():
    """体检要在目标解释器里 import 这个名字，它终究来自用户脚本的 traceback。"""
    assert projectenv.valid_module_name("lmfit")
    assert projectenv.valid_module_name("ovito")
    for bad in ("", "os; import shutil", "a.b", "../x", "-c", "os,sys", "1abc"):
        assert not projectenv.valid_module_name(bad), bad


def test_support_status_is_asymmetric_between_python_and_matplotlib():
    """Python 版本是硬边界，matplotlib 是软边界——**刻意不对称**。"""
    assert projectenv.support_status((3, 13), "3.10.8") == projectenv.SUPPORT_VERIFIED
    # 支持区间外的 Python 不自动使用
    assert projectenv.support_status((3, 9), "3.10.8") == projectenv.SUPPORT_UNSUPPORTED
    assert projectenv.support_status((3, 14), "3.10.8") == projectenv.SUPPORT_UNSUPPORTED
    # 钉版之外但能 import 的 matplotlib 照用，只是如实标注
    assert projectenv.support_status((3, 13), "3.12.0") == projectenv.SUPPORT_UNVERIFIED


# --------------------------------------------------------------- 体检
@needs_worker
def test_health_probe_really_runs_inside_the_venv(tmp_path):
    """**证明跑的是 venv 的 Python**，不只是「字符串选中了它」。"""
    venv = real_venv(tmp_path)
    info = projectenv.probe_environment(projectenv.interpreter_of(venv))
    assert info["ok"], info
    assert Path(info["executable"]).is_relative_to(venv), info["executable"]
    assert Path(info["prefix"]) == venv.resolve() or Path(info["prefix"]) == venv
    assert info["matplotlib_version"]
    assert info["arch"]


@needs_worker
def test_project_venv_starts_the_worker_without_installing_tavotto(tmp_path):
    """项目 venv 里**没有** Tavotto，照样能起 worker。

    这是整套方案成立的前提：Tavotto 把 worker 代码交给用户的解释器执行
    （`worker.py` 是 `sys.path.insert(0, HERE)` 的平铺 import），而不是要求
    用户往自己的环境里 `pip install tavotto`。这条断言红了，就意味着我们开始
    要求修改用户环境——那是本轮明确禁止的事。
    """
    venv = real_venv(tmp_path)
    python = projectenv.interpreter_of(venv)
    # **前提是被夹具「造出来」的，不是碰运气碰上的**：`--system-site-packages`
    # 会把**基础解释器**的 site-packages 带进来，而 CI 的 backend-fast 正是
    # `pip install -e ".[dev]"` 装进那个基础解释器的。少了这个替身，这条用例
    # 在 CI 上前提当场失效（真红过一次），而本地永远复现不出来——本地从 venv
    # 建 venv，继承的是基础解释器的 site-packages，不是父 venv 的。
    # 先断言替身在：把遮蔽拆掉的人**在本地**就会看到红，不必等 CI。
    assert (venvfixture.site_packages(venv) / "tavotto.py").is_file(), (
        "夹具 venv 少了遮蔽宿主 Tavotto 的替身，下面那条断言就只是碰运气"
    )
    # `-I`：跑测试时父进程带着 `PYTHONPATH=src`，不隔离的话这条断言会看到
    # 仓库源码目录里的 tavotto 而不是 venv 里装了什么，用例当场假绿。
    installed = subprocess.run(
        [python, "-I", "-c", "import tavotto"], capture_output=True, timeout=120
    )
    assert installed.returncode != 0, "fixture venv 不该装着 Tavotto，用例前提失效"
    info = projectenv.probe_environment(python)
    assert info["tavotto_worker_ok"], info


@needs_worker
def test_health_probe_separates_missing_module_from_broken_env(tmp_path):
    """「找到了 .venv」≠「它解决问题」——缺的那个包必须单独确认。"""
    venv = real_venv(tmp_path, with_fixture=False)
    python = projectenv.interpreter_of(venv)
    miss = projectenv.probe_environment(python, FIXTURE_MODULE)
    assert not miss["ok"]
    assert miss["code"] == projectenv.ERROR_MODULE_MISSING
    assert miss["requested_module_ok"] is False
    add_fixture_module(venv)
    hit = projectenv.probe_environment(python, FIXTURE_MODULE)
    assert hit["ok"], hit
    assert hit["requested_module_ok"] is True


def test_health_probe_reports_an_unusable_interpreter(tmp_path):
    """起不来的解释器归 `project_env_unusable`，不是「缺包」。"""
    info = projectenv.probe_environment(str(tmp_path / "not-a-python"))
    assert not info["ok"]
    assert info["code"] == projectenv.ERROR_UNUSABLE


# --------------------------------------------- 自动切换（真 worker，端到端）
@pytest.fixture
def project(tmp_path):
    """一个图库目录 + 一个 import 了 fixture 模块的脚本。

    **退出前必须把项目关掉**：走 `client` 的用例会 `open_project()`，那会给
    这个目录起一个 watcher；本文件的项目目录里还建着一个真 venv（几千个
    文件），watcher 留着不收，整个 pytest 进程剩下的时间都在监视一堆已经被
    删掉的临时目录。
    """
    from tavotto import app as m

    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "figure.py").write_text(SCRIPT, encoding="utf-8")
    yield figs
    for pid in [p for p, ctx in list(m.PROJECTS.items()) if str(ctx.path) == str(figs)]:
        m.close_project(pid, wait=True)
    engine_pool.shutdown_all(str(figs), wait=True)


@needs_worker
def test_missing_dependency_falls_back_to_the_project_venv(project):
    """内置/默认环境缺包 → 自动发现 `.venv` → 整体换解释器 → 图正常打开。

    本轮的产品结果就是这一条：用户不需要知道这一切是怎么完成的。
    """
    real_venv(project)
    worker, resp = engine_pool.build("figure.py", str(project), "__main__")
    assert sorted(resp.get("stems") or {}) == ["Fig1"]
    assert worker.python_source == engine_pool.SOURCE_PROJECT_VENV
    assert Path(worker.python).is_relative_to(project)


@needs_worker
def test_the_switch_is_remembered_project_scoped_not_globally(project, tmp_path):
    """A 项目找到的 `.venv` 绝不能变成 B 项目的渲染环境。"""
    real_venv(project)
    engine_pool.build("figure.py", str(project), "__main__")
    other = tmp_path / "other"
    other.mkdir()
    assert projectenv.remembered(project)
    assert projectenv.remembered(other) is None
    # 全局设置一个字节都没被动过——那才是会污染其它项目的地方
    assert engine_config.worker_python() is None
    assert engine_pool.resolve_worker_python(other)[1] != engine_pool.SOURCE_PROJECT_VENV


@needs_worker
def test_worker_identity_includes_the_interpreter(project):
    """环境换了还复用旧会话 = 「明明切了环境，还是报缺包」。"""
    real_venv(project)
    stale = engine_pool.get("figure.py", str(project), "__main__")
    assert stale.python_source != engine_pool.SOURCE_PROJECT_VENV
    outcome = engine_pool.try_project_env(str(project), "figure.py", FIXTURE_MODULE)
    assert outcome["ok"], outcome
    fresh = engine_pool.get("figure.py", str(project), "__main__")
    assert fresh is not stale
    assert fresh.python_source == engine_pool.SOURCE_PROJECT_VENV
    # 反向也要成立，而且这一半才真正看护 `get()` 里那条守卫：用户把项目切回
    # 内置环境走的是 `forget()`，它**不作废任何 worker**（自动 fallback 那条
    # 路顺手 invalidate 过，所以只测那一半是空门禁——抽掉守卫照样绿）。
    projectenv.forget(project)
    engine_pool.reset_worker_python()
    back = engine_pool.get("figure.py", str(project), "__main__")
    assert back is not fresh
    assert back.python_source != engine_pool.SOURCE_PROJECT_VENV


@needs_worker
def test_replay_and_export_use_the_same_interpreter(project):
    """热态 / 干净重放 / 导出必须是同一个解释器。

    写回自检要保证的是「热态所见 == 全量重放出来的」。热态跑在项目 `.venv`、
    重放跑回内置 runtime 的话，两边的 matplotlib 可能根本不是同一个版本——
    比对必然发散，而原因深埋在两个进程之间。
    """
    real_venv(project)
    hot, _ = engine_pool.build("figure.py", str(project), "__main__")
    assert hot.python_source == engine_pool.SOURCE_PROJECT_VENV
    replay = engine_pool.one_shot("figure.py", str(project), "__main__")
    try:
        assert engine_pool.same_python(replay.python, hot.python)
        assert replay.python_source == engine_pool.SOURCE_PROJECT_VENV
        # 重放是**从零跑一遍脚本**：它真的在项目环境里跑通了，才说明
        # 「热态所见 == 全量重放出来的」这条写回不变式没有跨环境断掉。
        assert sorted(replay.ensure_built().get("stems") or {}) == ["Fig1"]
    finally:
        engine_pool.discard(replay)
    # 导出走同一条构造路径——它也必须落在项目解释器上，否则「所见 != 所出」。
    out = hot.export_dir / "Fig1.pdf"
    hot.export("Fig1", [], str(out), "pdf", 200)
    assert out.exists() and out.stat().st_size > 0


@needs_worker
def test_never_mixes_site_packages(project):
    """**整个解释器为单位**切换，绝不把用户 venv 的 site-packages 塞给别人。

    混装是 ABI 灾难（venv 里的 cp311 扩展 / 对 numpy 1.x 编译的 scipy 进到
    内置 Python 3.13 + numpy 2.x）。这条守卫盯的是那个「省事」的实现：给
    bundled worker 注一条 `PYTHONPATH=<venv>/site-packages` 就好像也能跑。
    """
    real_venv(project)
    worker, _ = engine_pool.build("figure.py", str(project), "__main__")
    # 1) 真正被执行的就是 venv 自己的解释器
    argv = execspec.worker_argv(
        worker.spec, worker_py=engine_pool.WORKER_PY, out_dir=worker.out_dir, runtime_args=[]
    )
    assert engine_pool.same_python(argv[0], worker.python)
    assert Path(worker.python).is_relative_to(project)
    # 2) 注入环境里没有任何 PYTHONPATH——有它就说明有人在拼 sys.path
    assert "PYTHONPATH" not in (worker.spec.env or {})
    # 3) 我们自己这个进程也没被污染
    assert not any("site-packages" in p and str(project) in p for p in sys.path)


@needs_worker
def test_explicit_configuration_wins_over_automatic_discovery(project, monkeypatch):
    """用户显式挑过的环境，任何时候都不该被自动决策盖掉。"""
    real_venv(project)
    engine_pool.build("figure.py", str(project), "__main__")
    assert engine_pool.resolve_worker_python(project)[1] == engine_pool.SOURCE_PROJECT_VENV
    # 用户现在去设置里明确指了一条
    monkeypatch.setattr(engine_config, "worker_python", lambda: WORKER_PY)
    engine_pool.reset_worker_python()
    python, source = engine_pool.resolve_worker_python(project)
    assert source != engine_pool.SOURCE_PROJECT_VENV
    assert engine_pool.same_python(python, WORKER_PY)
    # 环境变量同理（优先级最高的那一条）
    monkeypatch.setattr(engine_config, "worker_python", lambda: None)
    monkeypatch.setenv(engine_pool.WORKER_PYTHON_ENV, WORKER_PY)
    engine_pool.reset_worker_python()
    assert engine_pool.resolve_worker_python(project)[1] != engine_pool.SOURCE_PROJECT_VENV


@needs_worker
def test_a_stale_env_var_does_not_hide_an_explicit_setting(project, monkeypatch):
    """环境变量指着一条已经不存在的路径时，设置里那条**仍然**压过自动决策。

    `env or configured` 那种短路写法会在这里当场失效：环境变量非空但没用，
    设置里那条根本没被看一眼，自动发现的 `.venv` 于是盖过了用户的显式选择。
    这个形状不是假想的——全量套件里就有别的用例漏出一个不存在的
    `TAVOTTO_WORKER_PYTHON`，这条断言最早就是那样红的。
    """
    real_venv(project)
    engine_pool.build("figure.py", str(project), "__main__")
    monkeypatch.setenv(engine_pool.WORKER_PYTHON_ENV, "/tmp/gone/python")
    monkeypatch.setattr(engine_config, "worker_python", lambda: WORKER_PY)
    engine_pool.reset_worker_python()
    python, source = engine_pool.resolve_worker_python(project)
    assert source != engine_pool.SOURCE_PROJECT_VENV
    assert engine_pool.same_python(python, WORKER_PY)


@needs_worker
def test_fallback_is_attempted_at_most_once(project):
    """venv 里也没有那个包 → 停下来报错，**不来回打转**。

    没有这条上限，「内置缺包 → 切 venv → venv 也缺 → 切回内置」会一直循环，
    用户看到的是界面卡在「正在运行」而后台在反复起 Python。
    """
    real_venv(project, with_fixture=False)
    first = engine_pool.try_project_env(str(project), "figure.py", FIXTURE_MODULE)
    assert not first["ok"]
    assert first["code"] == projectenv.ERROR_MODULE_MISSING
    second = engine_pool.try_project_env(str(project), "figure.py", FIXTURE_MODULE)
    assert second["code"] == engine_pool.PROJECT_ENV_ALREADY_ATTEMPTED
    # 用户手动重试才重新开一轮
    projectenv.reset_cache(project)
    third = engine_pool.try_project_env(str(project), "figure.py", FIXTURE_MODULE)
    assert third["code"] == projectenv.ERROR_MODULE_MISSING


@needs_worker
def test_only_missing_dependency_triggers_a_switch(project):
    """脚本自己的 bug 换个解释器一样错——为它切环境是把代码错误伪装成环境问题。"""
    real_venv(project)
    (project / "boom.py").write_text(
        "import matplotlib.pyplot as plt\nraise ValueError('script bug')\n", encoding="utf-8"
    )
    with pytest.raises(engine_pool.WorkerError) as err:
        engine_pool.build("boom.py", str(project), "__main__")
    assert err.value.code != "missing_dependency"
    # 一次自动切换都没发生：这个项目仍然没有记住任何环境
    assert projectenv.remembered(project) is None
    # 判据本身也钉住——两个消费者（pool.build 与 app 的端点重试）共用这一份
    assert not engine_pool.should_try_project_env(err.value)
    assert engine_pool.should_try_project_env(
        engine_pool.WorkerError("x", code="missing_dependency", module="lmfit")
    )


def test_the_app_endpoint_retry_shares_the_same_predicate():
    """端点侧的自动重试不许自己再写一遍「什么错该换环境」。

    两处各写一份 `exc.code == …` 的话，其中一处迟早会放宽，而只有另一处有
    用例看着——这正是「抽掉门禁却不红」的典型来源。
    """
    from tavotto import app as m

    class _FakeWorker:
        script_name = "figure.py"

    for code in ("script_error", "worker_timeout", "", "protocol_mismatch"):
        exc = engine_pool.WorkerError("boom", code=code)
        assert m._switched_to_project_env(_FakeWorker(), exc) is False
        assert not hasattr(exc, "project_env")


def test_no_automatic_switch_without_a_module_name(tmp_path):
    """认不出缺的是哪个包就没有可验证的目标——不切。"""
    root = tmp_path / "figs"
    root.mkdir()
    fake_venv(root / ".venv")
    for bad in ("", "os; import shutil"):
        outcome = engine_pool.try_project_env(str(root), "figure.py", bad)
        assert not outcome["ok"]
        assert outcome["code"] == projectenv.ERROR_NOT_FOUND


def test_nothing_is_ever_installed(monkeypatch, tmp_path):
    """本轮不装任何东西：发现 + 体检的全过程都不许出现 pip。"""
    root = tmp_path / "figs"
    root.mkdir()
    fake_venv(root / ".venv")
    seen: list[list] = []
    real_run = subprocess.run

    def spy(argv, *a, **kw):
        seen.append(list(argv) if isinstance(argv, (list, tuple)) else [argv])
        return real_run(argv, *a, **kw)

    monkeypatch.setattr(subprocess, "run", spy)
    engine_pool.try_project_env(str(root), "figure.py", FIXTURE_MODULE)
    assert seen, "一个子进程都没起，用例没有观测到任何东西"
    for argv in seen:
        # 按 **token** 判而不是按整行 substring：临时目录名里就带着 install
        # （`test_nothing_is_ever_installed0`），按子串判会永远红。
        tokens = [str(x) for x in argv]
        assert "pip" not in tokens, tokens
        assert "install" not in tokens, tokens
        assert not any(t.endswith("pip") or t.endswith("pip.exe") for t in tokens), tokens


@needs_worker
def test_remembered_environment_survives_a_project_move(project, tmp_path):
    """持久化的是**项目相对**路径：项目挪了地方，决策仍然成立。

    存绝对路径的话，用户把 `~/paper` 挪到 `/Volumes/T7/paper`（或换台机器
    同步过去）之后，记住的解释器当场失效，又回到「每次打开先失败一下」。
    """
    real_venv(project)
    engine_pool.build("figure.py", str(project), "__main__")
    state = projectenv.state(project)
    assert state["automatic"] is True
    assert state["trigger"] == "missing_dependency"
    assert state["module"] == FIXTURE_MODULE
    assert state["python_relative"].startswith(".venv")
    stored = engine_config.project_settings(str(project))[projectenv.SETTINGS_KEY]
    assert "python" not in stored, "项目内的解释器不该存绝对路径"
    assert stored["python_relative"]


@needs_worker
def test_forget_returns_the_project_to_the_default_chain(project):
    """用户选回内置环境时，自动决策要能干净地撤掉。"""
    real_venv(project)
    engine_pool.build("figure.py", str(project), "__main__")
    assert engine_pool.resolve_worker_python(project)[1] == engine_pool.SOURCE_PROJECT_VENV
    projectenv.forget(project)
    engine_pool.reset_worker_python()
    assert projectenv.remembered(project) is None
    assert engine_pool.resolve_worker_python(project)[1] != engine_pool.SOURCE_PROJECT_VENV


# ----------------------------------------------------------------- 产品 API
@pytest.fixture
def client():
    from tavotto import app as m

    m.app.config["TESTING"] = True
    return m.app.test_client()


@needs_worker
def test_environment_endpoint_reports_the_project_environment(client, project):
    """界面要能显示「项目 .venv · Python 3.12」，而不是含糊的一句「Python」。"""
    from tavotto import app as m

    real_venv(project)
    m.open_project(str(project))
    before = client.get("/api/engine/environment").get_json()["project"]
    assert before["open"] is True
    assert before["source"] != engine_pool.SOURCE_PROJECT_VENV
    # 发现到的候选也要交出来——「找到了但没在用」是用户该看得见的状态
    assert before["can_use_project_venv"] == [".venv"]

    engine_pool.build("figure.py", str(project), "__main__")
    after = client.get("/api/engine/environment").get_json()["project"]
    assert after["source"] == engine_pool.SOURCE_PROJECT_VENV
    assert after["automatic"] is True
    assert after["trigger"] == "missing_dependency"
    assert after["module"] == FIXTURE_MODULE
    assert after["python"].startswith(".venv")


def test_environment_endpoint_still_works_without_a_project(client, monkeypatch):
    """没打开项目时环境状态端点**必须照常返回**，不能变成 409。

    首次运行的「渲染环境」界面就在没有项目的状态下打开——那一屏 409 掉，
    用户连「自动安装渲染环境」的按钮都看不到。加项目环境这一段时正是这么
    把整个端点带崩过一次：`require_project()` 抛的是 `NoProjectError`，
    不是 `HTTPException`。
    """
    from tavotto import app as m

    # **经 monkeypatch 改**：直接 `PROJECTS.clear()` 会把同一进程里别的用例
    # 打开的项目一起清掉，后面那些用例于是在「没有项目」的状态下继续跑。
    monkeypatch.setattr(m, "DEFAULT_PROJECT", None)
    monkeypatch.setattr(m, "PROJECTS", {})
    body = client.get("/api/engine/environment").get_json()
    assert "ok" in body, body
    assert body["project"] == {"open": False}


@needs_worker
def test_project_scope_patch_never_touches_the_global_setting(client, project):
    """为**这个项目**选环境不许写全局设置——那是 A 污染 B 的唯一途径。"""
    from tavotto import app as m

    venv = real_venv(project)
    m.open_project(str(project))
    rel = str(Path(projectenv.interpreter_of(venv)).relative_to(project))
    resp = client.patch("/api/engine/environment", json={"scope": "project", "python": rel})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["project"]["source"] == engine_pool.SOURCE_PROJECT_VENV
    assert engine_config.worker_python() is None
    # 清掉 = 回到默认链条
    resp = client.patch("/api/engine/environment", json={"scope": "project", "python": ""})
    assert resp.status_code == 200
    assert resp.get_json()["project"]["source"] != engine_pool.SOURCE_PROJECT_VENV


@needs_worker
def test_project_scope_patch_refuses_an_unusable_environment(client, project, tmp_path):
    """「选了但用不了」比「没选」更难查——存下来之前先真体检一遍。"""
    from tavotto import app as m

    real_venv(project)
    m.open_project(str(project))
    resp = client.patch(
        "/api/engine/environment", json={"scope": "project", "python": "nope/bin/python"}
    )
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "interpreter_not_found"
    assert projectenv.remembered(project) is None


@needs_worker
def test_diagnostics_explains_why_this_python_was_chosen(client, project):
    """诊断包要能回答「为什么用了这个 Python」，且**不在生成时重新体检**。"""
    import io
    import json
    import zipfile

    from tavotto import app as m

    real_venv(project)
    m.open_project(str(project))
    engine_pool.build("figure.py", str(project), "__main__")
    blob = client.get("/api/diagnostics/bundle").data
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = next(n for n in z.namelist() if n.endswith(".json"))
        report = json.loads(z.read(name).decode("utf-8"))
    res = report["project"]["environment_resolution"]
    assert res["source"] == engine_pool.SOURCE_PROJECT_VENV
    assert res["automatic"] is True
    assert res["trigger"] == "missing_dependency"
    assert res["python_version"]
    assert res["matplotlib_version"]
    # 项目内的解释器只出项目相对路径：用户主目录名不该无谓地进诊断包
    assert res["executable"].startswith(".venv")
    assert not Path(res["executable"]).is_absolute()
