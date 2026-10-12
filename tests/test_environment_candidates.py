"""P04（E01–E07，AC-10 / AC-11）：环境候选的统一入口与发现率。

判据的主语：
* **候选集合**（`deprepair.user_environment_candidates`＝执行路径、`projscan.environment_evidence`＝证据、
  `envadvice.recommend`＝建议卡）三处看到的是同一批身份（`userenvs.env_id`），system 候选不再只进执行；
* **IDE 线索**（VSCode `python.defaultInterpreterPath`）指向解释器文件或环境文件夹，二者同一身份。测试用非惯例名
  `custom-env`、隔离其他枚举源——常规 `.venv` 发现会掩盖这个缺陷；
* **预算前排序**：体检上限（12）之前先按静态可得的项目关联排序，第 13 个唯一项目匹配者不被前 12 个无关候选饿死；
* **只读**：建议 / 证据 / 排序全程不起任何解释器；
* **探测状态**：未检查 / 无法启动 / 模块找不到 / 模块存在但独立 import 失败 / 通过 分开说，探测通过不冒充脚本成功。

所有枚举源都隔离（假 HOME、只留 HOME 下的 Conda 根、注入的 system 候选），不读真实用户配置。
"""

from __future__ import annotations

import ntpath
import os
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import (
    deprepair,
    envadvice,
    pool,
    projectenv,
    projscan,
    scanbudget,
    userenvs,
)

pytest_plugins = ("support.dependency_repair",)

# conftest 的 autouse 夹具会把 `pool.system_python_candidates` 置空；这里要测真函数，导入期先留一份
_REAL_SYSTEM_CANDIDATES = pool.system_python_candidates

POSIX = pytest.mark.skipif(os.name == "nt", reason="bin/python 布局只在 POSIX 上")


@pytest.fixture(autouse=True)
def _clean(clean_state, monkeypatch):
    monkeypatch.delenv("TAVOTTO_USER_ENV_DISCOVERY", raising=False)
    userenvs.reset_cache()
    yield
    userenvs.reset_cache()


def _python(prefix: Path, name: str = "python3") -> Path:
    exe = prefix / "bin" / name
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    return exe


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """隔离全部机器级枚举源：假 HOME、无 PYENV_ROOT、Conda 根只留 HOME 下的、登录 shell 不问、system 候选默认为空。"""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    monkeypatch.delenv("PYENV_ROOT", raising=False)
    monkeypatch.setattr(userenvs, "login_shell_pythons", lambda: [])
    monkeypatch.setattr(userenvs, "cached_login_shell_pythons", lambda: [])
    real_roots = userenvs._conda_roots
    monkeypatch.setattr(
        userenvs, "_conda_roots", lambda: [r for r in real_roots() if r.startswith(str(h))]
    )
    monkeypatch.setattr(pool, "system_python_candidates", lambda **_: [])
    return h


@pytest.fixture
def no_process(monkeypatch):
    """只读阶段不许起任何子进程 / 体检：一旦被调用即失败。"""

    def boom(*a, **k):
        raise AssertionError("只读的候选枚举不许起解释器")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(projectenv, "probe_environment", boom)


def _project(tmp_path: Path, name: str = "paper") -> Path:
    project = tmp_path / name
    project.mkdir()
    (project / "fig.py").write_text("import x\n", encoding="utf-8")
    return project


def _vscode_hint(project: Path, value: str) -> None:
    (project / ".vscode").mkdir(exist_ok=True)
    (project / ".vscode" / "settings.json").write_text(
        '{"python.defaultInterpreterPath": "%s"}' % value, encoding="utf-8"
    )


# ------------------------------------------------------------------ E01：IDE 线索（文件 / 文件夹）


@POSIX
def test_vscode_hint_to_custom_env_folder_equals_hint_to_its_python(tmp_path, home, no_process):
    env = tmp_path / "somewhere" / "custom-env"  # 非惯例名：不是 .venv / venv / env
    python = _python(env)
    project = _project(tmp_path)

    _vscode_hint(project, str(python))
    by_file = userenvs.discover(project, "fig.py", ask_login_shell=False)
    _vscode_hint(project, str(env))
    by_folder = userenvs.discover(project, "fig.py", ask_login_shell=False)

    # 其他枚举源都被隔离：唯一的候选只能来自 VSCode 线索
    assert [(c["python"], c["source"]) for c in by_file] == [(str(python), userenvs.SOURCE_VSCODE)]
    assert [(c["python"], c["source"]) for c in by_folder] == [
        (str(python), userenvs.SOURCE_VSCODE)
    ]
    assert userenvs.env_id(by_folder[0]["python"]) == userenvs.env_id(by_file[0]["python"])


@POSIX
def test_vscode_folder_hint_follows_workspace_folder_and_relative_forms(tmp_path, home):
    project = _project(tmp_path)
    python = _python(project / "tools" / "custom-env")
    _vscode_hint(project, "${workspaceFolder}/tools/custom-env")
    got = userenvs.discover(project, "fig.py", ask_login_shell=False)
    assert [c["python"] for c in got] == [str(python)]
    _vscode_hint(project, "tools/custom-env")
    got = userenvs.discover(project, "fig.py", ask_login_shell=False)
    assert [c["python"] for c in got] == [str(python)]


@POSIX
def test_vscode_folder_hint_without_interpreter_and_redirected_folder_yield_nothing(tmp_path, home):
    project = _project(tmp_path)
    (tmp_path / "empty-env").mkdir()
    _vscode_hint(project, str(tmp_path / "empty-env"))
    assert userenvs.discover(project, "fig.py", ask_login_shell=False) == []
    # 项目里的符号链接文件夹：与文件线索同一条规矩，被重定向的路径不探目标
    target = tmp_path / "elsewhere" / "custom-env"
    _python(target)
    (project / "linked-env").symlink_to(target, target_is_directory=True)
    _vscode_hint(project, str(project / "linked-env"))
    assert userenvs.discover(project, "fig.py", ask_login_shell=False) == []


def test_prefix_python_knows_the_windows_venv_layout(monkeypatch):
    """Windows 的 venv 解释器在 `Scripts\\python.exe`（Conda 在前缀根）：文件夹线索两种布局都认。
    用 ntpath 求值路径组合（本机不是 Windows，文件存在性由注入的谓词代替）。"""
    prefix = r"C:\work\custom-env"
    venv_exe = ntpath.join(prefix, "Scripts", "python.exe")
    conda_exe = ntpath.join(prefix, "python.exe")
    monkeypatch.setattr(userenvs, "_is_python_file", lambda p: p == venv_exe)
    assert userenvs._prefix_python(prefix, nt=True) == venv_exe
    monkeypatch.setattr(userenvs, "_is_python_file", lambda p: p == conda_exe)
    assert userenvs._prefix_python(prefix, nt=True) == conda_exe


# ------------------------------------------------------------------ E02：system 候选三处一致


@POSIX
def test_system_only_candidates_are_the_same_in_execution_evidence_and_advice(
    tmp_path, home, monkeypatch, no_process
):
    system = _python(tmp_path / "usr-local")
    monkeypatch.setattr(pool, "system_python_candidates", lambda **_: [(str(system), "system")])
    project = _project(tmp_path)

    execution = deprepair.user_environment_candidates(project, "fig.py")
    evidence = projscan.environment_evidence(project, "fig.py", private=True)["candidates"]
    advice = envadvice.recommend(project, "fig.py")["candidates"]

    want = userenvs.env_id(str(system))
    assert [userenvs.env_id(c["python"]) for c in execution] == [want]
    assert [c["id"] for c in evidence] == [want]
    assert evidence[0]["source"] == userenvs.SOURCE_SYSTEM
    assert [c["id"] for c in advice if c["id"] != envadvice.BUILTIN_ID] == [want]
    sys_row = next(c for c in advice if c["id"] == want)
    assert sys_row["sources"] == [userenvs.SOURCE_SYSTEM]
    assert sys_row["checked"] is False  # 没检查过就如实写没检查过
    assert "_python" not in sys_row and str(system) not in str(advice)  # 公开投影不带机器路径


@POSIX
def test_unchecked_system_python_is_listed_but_not_recommended(tmp_path, home, monkeypatch):
    """system 候选进入建议是为了「看得见」；它没被检查过，不抢推荐位（推荐仍要有项目 / 机器线索或检查结论）。"""
    system = _python(tmp_path / "usr-local")
    monkeypatch.setattr(pool, "system_python_candidates", lambda **_: [(str(system), "system")])
    project = _project(tmp_path)
    rec = envadvice.recommend(project, "fig.py")
    assert userenvs.env_id(str(system)) in [c["id"] for c in rec["candidates"]]
    assert rec["recommended_id"] is None


# ------------------------------------------------------------------ E03：预算前排序


@POSIX
def test_the_13th_unique_project_match_is_not_starved_by_12_unrelated_candidates(
    tmp_path, home, monkeypatch
):
    project = _project(tmp_path, name="zeta")
    # 12 个无关 Conda 环境（名字排在前）+ 第 13 个名字与项目目录对得上的
    for i in range(12):
        _python(home / "miniforge3" / "envs" / f"a{i:02d}")
    mine = _python(home / "miniforge3" / "envs" / "zeta")

    cands = deprepair.user_environment_candidates(project, "fig.py")
    assert len(cands) == 13
    assert cands[0]["python"] == str(mine)  # 静态相关性排在预算之前

    probed: list[str] = []

    def fake_probe(python, module=None, *, modules=(), **kw):
        probed.append(python)
        return {
            "ok": True,
            "python_version": "3.12.0",
            "support": "verified",
            "modules_ok": {m: python == str(mine) for m in modules},
            "modules_detail": {
                m: ("found" if python == str(mine) else "not_found") for m in modules
            },
        }

    monkeypatch.setattr(projectenv, "probe_environment", fake_probe)
    needed = [{"import_name": "qa_dep", "distribution": "qa-dep"}]
    entries = userenvs.evaluate(cands, needed, [])
    assert str(mine) in probed
    assert len(probed) <= userenvs.PROBE_LIMIT  # 预算仍有界
    assert userenvs.best(entries, "zeta")["python"] == str(mine)


@POSIX
def test_evaluate_itself_picks_by_relevance_when_handed_an_unsorted_13(tmp_path, home, monkeypatch):
    """直接把 13 个未排序的候选交给 `evaluate`（不经 `user_environment_candidates`）：入选 12 个按静态关联度，
    回表仍是输入的相对顺序。"""
    cands = [
        {"python": str(_python(tmp_path / f"p{i}")), "source": "pyenv", "label": f"3.{i}"}
        for i in range(12)
    ]
    mine = {"python": str(_python(tmp_path / "zeta")), "source": "conda", "label": "zeta"}
    cands.append(mine)
    seen: list[str] = []
    monkeypatch.setattr(
        projectenv,
        "probe_environment",
        lambda python, module=None, *, modules=(), **kw: (
            seen.append(python) or {"ok": True, "modules_ok": {}, "python_version": "3.12.0"}
        ),
    )
    out = userenvs.evaluate(cands, [], [], project_name="zeta")
    assert len(out) == userenvs.PROBE_LIMIT
    assert mine["python"] in seen
    assert [e["python"] for e in out] == [c["python"] for c in cands if c["python"] in seen]


@POSIX
def test_two_venvs_sharing_a_base_interpreter_stay_two_identities(tmp_path):
    base = _python(tmp_path / "base")
    a, b = tmp_path / "a" / "bin", tmp_path / "b" / "bin"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    (a / "python").symlink_to(base)
    (b / "python").symlink_to(base)
    assert userenvs.env_id(str(a / "python")) != userenvs.env_id(str(b / "python"))


# ------------------------------------------------------------------ E04：pyenv-win 路径组合（ntpath 注入，非 Windows 真机）


def test_pyenv_win_root_variants_compose_without_doubling(monkeypatch):
    monkeypatch.setenv("PYENV_ROOT", r"C:\Users\u\.pyenv\pyenv-win")
    assert userenvs._pyenv_version_dirs(nt=True) == [r"C:\Users\u\.pyenv\pyenv-win\versions"]
    monkeypatch.setenv("PYENV_ROOT", "C:\\Users\\u\\.pyenv\\pyenv-win\\")
    assert userenvs._pyenv_version_dirs(nt=True) == [r"C:\Users\u\.pyenv\pyenv-win\versions"]
    monkeypatch.setenv("PYENV_ROOT", r"C:\Users\u\.pyenv")
    assert userenvs._pyenv_version_dirs(nt=True) == [r"C:\Users\u\.pyenv\pyenv-win\versions"]
    monkeypatch.delenv("PYENV_ROOT")
    monkeypatch.setattr(userenvs, "_home", lambda: r"C:\Users\u")
    assert userenvs._pyenv_version_dirs(nt=True) == [r"C:\Users\u\.pyenv\pyenv-win\versions"]
    assert ntpath.basename(userenvs._pyenv_version_dirs(nt=True)[0]) == "versions"


@POSIX
def test_pyenv_posix_layout_unchanged(monkeypatch):
    monkeypatch.setenv("PYENV_ROOT", "/opt/pyenv")
    assert userenvs._pyenv_version_dirs() == ["/opt/pyenv/versions"]


# ------------------------------------------------------------------ E05：空的 login-shell 结论可失效 / 刷新


def test_empty_login_shell_answer_expires_and_can_be_forgotten(monkeypatch):
    answers = [[], ["/opt/py/bin/python3"]]
    asked: list[int] = []

    def ask(shell):
        asked.append(1)
        return list(answers[min(len(asked) - 1, len(answers) - 1)])

    now = [1000.0]
    monkeypatch.setattr(userenvs, "_login_shell", lambda: "/bin/zsh")
    monkeypatch.setattr(userenvs, "_ask_login_shell", ask)
    monkeypatch.setattr(userenvs, "_monotonic", lambda: now[0])

    assert userenvs.login_shell_pythons() == []
    assert (
        userenvs.login_shell_pythons() == [] and len(asked) == 1
    )  # TTL 内不重复问（一次最长 8 秒）
    now[0] += userenvs.EMPTY_LOGIN_SHELL_TTL_S + 1
    assert userenvs.login_shell_pythons() == ["/opt/py/bin/python3"] and len(asked) == 2
    now[0] += 10_000  # 有结论的不因时间重问
    assert userenvs.login_shell_pythons() == ["/opt/py/bin/python3"] and len(asked) == 2


def test_forget_empty_login_shell_refreshes_only_the_empty_answer(monkeypatch):
    answers = iter([[], ["/opt/py/bin/python3"]])
    asked: list[int] = []
    monkeypatch.setattr(userenvs, "_login_shell", lambda: "/bin/zsh")
    monkeypatch.setattr(
        userenvs, "_ask_login_shell", lambda shell: (asked.append(1), next(answers))[1]
    )
    assert userenvs.login_shell_pythons() == []
    userenvs.forget_empty_login_shell()
    assert userenvs.login_shell_pythons() == ["/opt/py/bin/python3"]
    userenvs.forget_empty_login_shell()  # 有结论的保留
    assert userenvs.login_shell_pythons() == ["/opt/py/bin/python3"] and len(asked) == 2


def test_cached_reader_never_starts_a_shell(monkeypatch):
    monkeypatch.setattr(userenvs, "_login_shell", lambda: "/bin/zsh")
    monkeypatch.setattr(
        userenvs, "_ask_login_shell", lambda shell: pytest.fail("纯读缓存不许问 shell")
    )
    assert userenvs.cached_login_shell_pythons() == []


# ------------------------------------------------------------------ E07：探测状态分开说（AC-11）


def _entry(monkeypatch, health: dict | None, modules=("sdk",), **kw):
    userenvs.reset_cache()  # 体检结论按 (路径, 代, import 集合) 缓存：每种情形各量一次
    if health is not None:
        monkeypatch.setattr(
            projectenv, "probe_environment", lambda python, module=None, **k: health
        )
    cand = {"python": "/x/bin/python3", "source": "conda", "label": "lab"}
    needed = [{"import_name": m, "distribution": m} for m in modules]
    return userenvs.evaluate([cand], needed, [], **kw)[0]


def test_probe_status_separates_the_five_situations(monkeypatch):
    ok = {"ok": True, "python_version": "3.12.1", "modules_ok": {"sdk": True}}
    passed = _entry(monkeypatch, ok)
    assert passed["probe_status"] == userenvs.PROBE_PASSED and passed["satisfies"] is True

    missing = _entry(
        monkeypatch,
        {**ok, "modules_ok": {"sdk": False}, "modules_detail": {"sdk": "not_found"}},
    )
    assert missing["probe_status"] == userenvs.PROBE_MODULE_MISSING
    assert missing["missing"] == ["sdk"] and missing["import_failed"] == []

    broken_import = _entry(
        monkeypatch,
        {**ok, "modules_ok": {"sdk": False}, "modules_detail": {"sdk": "import_error"}},
    )
    assert broken_import["probe_status"] == userenvs.PROBE_IMPORT_FAILED
    assert broken_import["import_failed"] == ["sdk"]
    assert broken_import["satisfies"] is False  # 探测过不了不等于自动采用……

    unlaunchable = _entry(monkeypatch, {"ok": False, "code": "project_env_unusable"})
    assert unlaunchable["probe_status"] == userenvs.PROBE_UNLAUNCHABLE
    assert unlaunchable["missing"] == [] and unlaunchable["import_failed"] == []


def test_cache_only_candidate_is_unchecked_not_missing():
    cand = {"python": "/x/bin/python3", "source": "conda", "label": "lab"}
    out = userenvs.evaluate(
        [cand], [{"import_name": "sdk", "distribution": "sdk"}], [], cache_only=True
    )[0]
    assert out["probe_status"] == userenvs.PROBE_UNCHECKED
    assert out["satisfies"] is None and out["missing"] == []


def test_import_failed_is_classified_apart_from_missing_and_stays_a_candidate(monkeypatch):
    """SDK 要脚本里先初始化再 import：裸 import 失败 → `import_failed`（包在，装它救不了），与「模块找不到」分开；
    环境也不因此从候选表里消失；探测通过 / 失败都不冒充「脚本成功 / 失败」。"""
    health = {
        "ok": True,
        "python_version": "3.12.1",
        "modules_ok": {"sdk": False},
        "modules_detail": {"sdk": "import_error"},
    }
    out = _entry(monkeypatch, health)
    assert out["import_failed"] == [
        "sdk"
    ]  # `missing` 保持旧语义（import 不到），分类看 probe_status / import_failed
    assert out["probe_status"] == userenvs.PROBE_IMPORT_FAILED
    pub = userenvs.public(out)
    assert pub["probe_status"] == userenvs.PROBE_IMPORT_FAILED
    assert "script" not in pub["probe_status"]
    assert userenvs.best([out]) is None  # 不自动采用，但……
    assert out["python"] == "/x/bin/python3"  # ……仍是一条可见、可由用户选择的候选


# ------------------------------------------------------------------ 自动扫描的 system 枚举：预算、取消、UNC

UNC_DIR = "\\\\corp-fileserver\\share\\python"


def _forbid_network(monkeypatch, touched: list[str]) -> None:
    """文件系统入口一碰到 UNC / 网络路径就失败（Windows 上这一步会联系共享，断线时卡住）。"""
    import glob as glob_mod
    import shutil

    def guard(real):
        def wrapped(path, *a, **k):
            text = os.fspath(path)
            if text.startswith(("\\\\", "//")) or "corp-fileserver" in text:
                touched.append(text)
                raise AssertionError(f"自动扫描碰了网络路径：{text}")
            return real(path, *a, **k)

        return wrapped

    monkeypatch.setattr(os.path, "isfile", guard(os.path.isfile))
    monkeypatch.setattr(os, "access", guard(os.access))
    monkeypatch.setattr(os.path, "exists", guard(os.path.exists))
    monkeypatch.setattr(glob_mod, "glob", guard(glob_mod.glob))
    monkeypatch.setattr(Path, "is_file", lambda self: guard(os.path.isfile)(str(self)))

    def no_which(*a, **k):
        raise AssertionError("自动扫描不许走 shutil.which（它不看预算、不跳 UNC）")

    monkeypatch.setattr(shutil, "which", no_which)


def test_scan_never_touches_a_unc_path_entry_and_still_finds_the_local_python(
    tmp_path, home, monkeypatch, no_process
):
    local = _python(tmp_path / "local")
    local.chmod(0o755)  # PATH 查找要求可执行位
    monkeypatch.setenv("PATH", os.pathsep.join([UNC_DIR, str(local.parent)]))
    monkeypatch.setattr(pool, "system_python_candidates", _REAL_SYSTEM_CANDIDATES)
    touched: list[str] = []
    _forbid_network(monkeypatch, touched)

    found = pool.system_python_candidates(budget=scanbudget.Budget())

    assert touched == []
    assert (str(local), "system") in found


def test_scan_skips_unc_roots_of_the_windows_list_too(tmp_path, home, monkeypatch, no_process):
    """Windows 语义（`nt=True`，在 macOS 上注入）：UNC 的 PATH 项、重定向到共享的 LOCALAPPDATA / HOME 都不碰。"""
    monkeypatch.setenv("PATH", UNC_DIR)
    monkeypatch.setenv("PATHEXT", ".EXE")
    monkeypatch.setenv("LOCALAPPDATA", "\\\\corp-fileserver\\profiles\\me\\AppData")
    monkeypatch.setenv("HOME", "\\\\corp-fileserver\\home\\me")
    touched: list[str] = []
    _forbid_network(monkeypatch, touched)

    found = _REAL_SYSTEM_CANDIDATES(budget=scanbudget.Budget(), nt=True)

    assert touched == []
    assert all(not py.startswith("\\\\") for py, _ in found)


def test_scan_stops_on_cancel_and_on_budget_instead_of_walking_the_whole_path(
    tmp_path, home, monkeypatch, no_process
):
    dirs = []
    for i in range(300):
        d = tmp_path / f"p{i}"
        d.mkdir()
        dirs.append(str(d))
    monkeypatch.setenv("PATH", os.pathsep.join(dirs))
    calls: list[str] = []
    real_isfile = os.path.isfile

    def counting(path):
        calls.append(os.fspath(path))
        return real_isfile(path)

    monkeypatch.setattr(os.path, "isfile", counting)

    cancelled = scanbudget.Budget(cancel=lambda: True)
    _REAL_SYSTEM_CANDIDATES(budget=cancelled)
    assert cancelled.stopped == scanbudget.ISSUE_CANCELLED
    assert len(calls) < 100  # 300 个 PATH 项里只走了第一批

    calls.clear()
    tight = scanbudget.Budget(limits=scanbudget.Limits(max_entries=5))
    _REAL_SYSTEM_CANDIDATES(budget=tight)
    assert tight.stopped == scanbudget.ISSUE_ENTRIES
    assert len(calls) <= 6


@POSIX
def test_scan_and_execution_system_candidates_share_the_same_identities(
    tmp_path, home, monkeypatch, no_process
):
    local = _python(tmp_path / "local")
    local.chmod(0o755)  # PATH 查找要求可执行位
    monkeypatch.setenv("PATH", str(local.parent))
    monkeypatch.setattr(pool, "system_python_candidates", _REAL_SYSTEM_CANDIDATES)

    scan = {
        userenvs.env_id(py) for py, _ in pool.system_python_candidates(budget=scanbudget.Budget())
    }
    execution = {userenvs.env_id(py) for py, _ in pool.system_python_candidates()}

    assert scan == execution
    assert userenvs.env_id(str(local)) in scan


@POSIX
def test_project_scan_evidence_lists_local_system_python_but_skips_unc(
    tmp_path, home, monkeypatch, no_process
):
    local = _python(tmp_path / "local")
    local.chmod(0o755)  # PATH 查找要求可执行位
    monkeypatch.setenv("PATH", os.pathsep.join([UNC_DIR, str(local.parent)]))
    monkeypatch.setattr(pool, "system_python_candidates", _REAL_SYSTEM_CANDIDATES)
    project = _project(tmp_path)
    touched: list[str] = []
    _forbid_network(monkeypatch, touched)

    evidence = projscan.environment_evidence(project, "fig.py", scanbudget.Budget(), private=True)[
        "candidates"
    ]

    assert touched == []
    want = userenvs.env_id(str(local))
    row = next(c for c in evidence if c["id"] == want)
    assert row["source"] == userenvs.SOURCE_SYSTEM
