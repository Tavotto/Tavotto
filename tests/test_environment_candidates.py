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
import posixpath
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
    monkeypatch.setattr(userenvs, "_is_python_file", lambda p, **k: p == venv_exe)
    assert userenvs._prefix_python(prefix, nt=True) == venv_exe
    monkeypatch.setattr(userenvs, "_is_python_file", lambda p, **k: p == conda_exe)
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
    if os.name == "nt":  # Windows 的布局是 `<目录>\\python.exe`，PATH 查找按 PATHEXT
        local = tmp_path / "local" / "python.exe"
        local.parent.mkdir()
        local.write_text("MZ", encoding="utf-8")
        monkeypatch.setenv("PATHEXT", ".EXE")
    else:
        local = _python(tmp_path / "local")
        local.chmod(0o755)  # PATH 查找要求可执行位
    monkeypatch.setenv("PATH", os.pathsep.join([UNC_DIR, str(local.parent)]))
    monkeypatch.setattr(pool, "system_python_candidates", _REAL_SYSTEM_CANDIDATES)
    touched: list[str] = []
    _forbid_network(monkeypatch, touched)

    budget = scanbudget.Budget()
    found = pool.system_python_candidates(budget=budget)

    assert touched == []
    # 本地条目在列表里；不假设唯一或排序（真实机器还有别的 Python）。Windows 的 PATHEXT 查找返回 `python.EXE`
    # （扩展名取 PATHEXT 的写法，文件系统不分大小写）：按 normcase 比身份，失败时打印候选与账本便于看 CI 日志
    got = [(os.path.normcase(py), src) for py, src in found]
    assert (os.path.normcase(str(local)), "system") in got, (
        f"found={found!r} issues={budget.issues()!r} PATH={os.environ.get('PATH')!r}"
    )


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


def _forbid_following(monkeypatch, root: Path, touched: list[str]) -> None:
    """`root` 之下（含它自己）的路径一旦进入跟随型谓词（isfile / exists / stat / access / is_file）就记下并失败。"""
    prefix = str(root)

    def guard(real):
        def wrapped(path, *a, **k):
            text = os.fspath(path)
            if text.startswith(prefix):
                touched.append(text)
                raise AssertionError(f"扫描对重定向条目调用了跟随型谓词：{text}")
            return real(path, *a, **k)

        return wrapped

    monkeypatch.setattr(os.path, "isfile", guard(os.path.isfile))
    monkeypatch.setattr(os.path, "exists", guard(os.path.exists))
    monkeypatch.setattr(os, "stat", guard(os.stat))
    monkeypatch.setattr(os, "access", guard(os.access))
    monkeypatch.setattr(Path, "is_file", lambda self, **k: guard(os.path.isfile)(self))
    monkeypatch.setattr(Path, "exists", lambda self, **k: guard(os.path.exists)(self))


def _nt_scan_env(monkeypatch, path_dirs: list[str]) -> None:
    monkeypatch.setenv("PATH", os.pathsep.join(path_dirs))
    monkeypatch.setenv("PATHEXT", ".exe")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)


def test_windows_scan_skips_a_redirected_python_exe_in_a_local_path_dir(
    tmp_path, home, monkeypatch, no_process
):
    """PATH 里本地样子的目录放着指向（可能断线的）共享的 python.exe 重定向条目：不 isfile、不进候选。"""
    target = tmp_path / "share" / "python.exe"
    target.parent.mkdir()
    target.write_text("MZ", encoding="utf-8")
    pathdir = tmp_path / "bin"
    pathdir.mkdir()
    (pathdir / "python.exe").symlink_to(target)
    good = tmp_path / "good"
    good.mkdir()
    (good / "python.exe").write_text("MZ", encoding="utf-8")
    _nt_scan_env(monkeypatch, [str(pathdir), str(good)])
    touched: list[str] = []
    _forbid_following(monkeypatch, pathdir / "python.exe", touched)
    budget = scanbudget.Budget()

    found = _REAL_SYSTEM_CANDIDATES(budget=budget, nt=True)

    assert touched == []
    assert all(not py.startswith(str(pathdir)) for py, _ in found)
    assert (str(good / "python.exe"), "system") in found  # 干净的本地条目照常出现
    assert any(
        r["code"] == scanbudget.ISSUE_SYMLINK_DIR and "path" not in r  # 机器级路径不进报告（P2-1）
        for r in budget.issues()
    )


def test_windows_scan_skips_a_path_dir_that_is_itself_a_junction(
    tmp_path, home, monkeypatch, no_process
):
    real = tmp_path / "share"
    real.mkdir()
    (real / "python.exe").write_text("MZ", encoding="utf-8")
    link = tmp_path / "linkdir"
    link.symlink_to(real, target_is_directory=True)
    _nt_scan_env(monkeypatch, [str(link)])
    touched: list[str] = []
    _forbid_following(monkeypatch, link, touched)

    found = _REAL_SYSTEM_CANDIDATES(budget=scanbudget.Budget(), nt=True)

    assert touched == []
    assert all(not py.startswith(str(link)) for py, _ in found)


@POSIX
def test_posix_scan_keeps_a_symlinked_local_system_python(tmp_path, home, monkeypatch, no_process):
    """POSIX 的 venv / Homebrew python 本来就是软链接：只在 Windows 语义下拒重定向，本地系统 Python 不能从扫描里消失。"""
    real = _python(tmp_path / "real")
    real.chmod(0o755)
    link = tmp_path / "linked" / "python3"
    link.parent.mkdir()
    link.symlink_to(real)
    monkeypatch.setenv("PATH", str(link.parent))
    monkeypatch.setattr(pool, "system_python_candidates", _REAL_SYSTEM_CANDIDATES)

    found = pool.system_python_candidates(budget=scanbudget.Budget())

    assert (str(link), "system") in found


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


# ------------------------------------------------------------------ 机器级候选：祖先重定向（#957 第三轮）
#
# 只验叶子不够：对后代路径 lstat 时系统先解析重定向的祖先（断线 UNC / WebDAV 共享会卡住或发 NTLM）。
# `scanbudget.PathWalker` 从根起每一级只 lstat 该级本身，被拒的那一级之下零 lstat、零跟随型调用。


def _forbid_following_all(monkeypatch, root: Path, touched: list[str]) -> None:
    """`_forbid_following` 之上再封 isdir / realpath / resolve / scandir / glob：这些也会跟随链接。"""
    import glob as glob_mod

    _forbid_following(monkeypatch, root, touched)
    prefix = str(root)

    def guard(real):
        def wrapped(path, *a, **k):
            text = os.fspath(path)
            if text.startswith(prefix):
                touched.append(text)
                raise AssertionError(f"扫描对重定向条目调用了跟随型谓词：{text}")
            return real(path, *a, **k)

        return wrapped

    monkeypatch.setattr(os.path, "isdir", guard(os.path.isdir))
    monkeypatch.setattr(os.path, "realpath", guard(os.path.realpath))
    monkeypatch.setattr(os, "scandir", guard(os.scandir))
    monkeypatch.setattr(glob_mod, "glob", guard(glob_mod.glob))
    monkeypatch.setattr(Path, "resolve", lambda self, **k: guard(os.path.realpath)(self))
    monkeypatch.setattr(Path, "is_dir", lambda self, **k: guard(os.path.isdir)(self))


def _record_lstat(monkeypatch) -> list[str]:
    seen: list[str] = []
    real = os.lstat

    def spy(path, *a, **k):
        seen.append(os.fspath(path))
        return real(path, *a, **k)

    monkeypatch.setattr(os, "lstat", spy)
    return seen


def _nt_walker(monkeypatch) -> None:
    """让 `userenvs` 里所有扫描用的 walker 按 Windows 语义求值（本机是 macOS，注入）。"""
    monkeypatch.setattr(
        userenvs,
        "_walker",
        lambda budget=None, walker=None, nt=None: (
            walker or scanbudget.PathWalker(nt=True if nt is None else nt, budget=budget)
        ),
    )


@POSIX  # 用符号链接在 macOS / Linux 上模拟 Windows 语义；Windows 真机不跑（建链需要特权）
def test_windows_prefix_python_rejects_a_redirected_scripts_dir_before_probing(
    tmp_path, home, monkeypatch
):
    """P2-1：VSCode 指到本地 env 文件夹，但其 `Scripts` 是 junction：不碰 `Scripts\\python.exe`。"""
    share = tmp_path / "share" / "Scripts"
    share.mkdir(parents=True)
    (share / "python.exe").write_text("MZ", encoding="utf-8")
    env = tmp_path / "envs" / "proj-env"
    env.mkdir(parents=True)
    (env / "Scripts").symlink_to(share, target_is_directory=True)
    monkeypatch.setattr(
        userenvs, "ntpath", posixpath
    )  # 本机是 POSIX：让 Scripts 布局拼成真能落盘的路径
    touched: list[str] = []
    _forbid_following_all(monkeypatch, env / "Scripts", touched)
    lstats = _record_lstat(monkeypatch)

    assert userenvs._prefix_python(str(env), nt=True) is None

    assert touched == []
    assert not any(p.replace("\\", "/").startswith(str(env / "Scripts") + "/") for p in lstats)


@POSIX  # 用符号链接在 macOS / Linux 上模拟 Windows 语义；Windows 真机不跑（建链需要特权）
def test_windows_prefix_python_rejects_a_redirected_python_exe_leaf(tmp_path, home, monkeypatch):
    target = tmp_path / "share" / "python.exe"
    target.parent.mkdir()
    target.write_text("MZ", encoding="utf-8")
    env = tmp_path / "envs" / "proj-env"
    (env / "Scripts").mkdir(parents=True)
    (env / "Scripts" / "python.exe").symlink_to(target)
    monkeypatch.setattr(userenvs, "ntpath", posixpath)
    touched: list[str] = []
    _forbid_following_all(monkeypatch, env / "Scripts" / "python.exe", touched)

    assert userenvs._prefix_python(str(env), nt=True) is None
    assert touched == []
    # 正例：普通文件不被 walker 拒
    (env / "Scripts" / "python.exe").unlink()
    (env / "Scripts" / "python.exe").write_text("MZ", encoding="utf-8")
    monkeypatch.undo()
    monkeypatch.setattr(userenvs, "ntpath", posixpath)
    assert userenvs._prefix_python(str(env), nt=True) == str(env / "Scripts" / "python.exe")


@POSIX
def test_vscode_folder_hint_with_redirected_bin_yields_no_candidate_and_no_following_call(
    tmp_path, home, monkeypatch
):
    """discover 整条链：机器级（项目之外）的 env 文件夹，`bin` 是重定向 → 无候选、零跟随调用。"""
    project = _project(tmp_path)
    share = tmp_path / "share" / "bin"
    share.mkdir(parents=True)
    (share / "python3").write_text("#!/bin/sh\n", encoding="utf-8")
    env = tmp_path / "envs" / "custom-env"
    env.mkdir(parents=True)
    (env / "bin").symlink_to(share, target_is_directory=True)
    _vscode_hint(project, str(env))
    _nt_walker(monkeypatch)
    touched: list[str] = []
    _forbid_following_all(monkeypatch, env / "bin", touched)
    lstats = _record_lstat(monkeypatch)
    budget = scanbudget.Budget()

    assert userenvs.discover(project, "fig.py", ask_login_shell=False, budget=budget) == []

    assert touched == []
    assert not any(p.startswith(str(env / "bin") + "/") for p in lstats)
    assert any(r["code"] == scanbudget.ISSUE_SYMLINK_DIR for r in budget.issues())


@POSIX  # 用符号链接在 macOS / Linux 上模拟 Windows 语义；Windows 真机不跑（建链需要特权）
def test_windows_scan_never_lstats_below_a_redirected_path_ancestor(
    tmp_path, home, monkeypatch, no_process
):
    """P2-2：PATH 项 `…/local-junction/bin`，只有祖先 `local-junction` 是重定向：祖先之下零 lstat、零跟随。"""
    share = tmp_path / "share"
    (share / "bin").mkdir(parents=True)
    (share / "bin" / "python.exe").write_text("MZ", encoding="utf-8")
    link = tmp_path / "local-junction"
    link.symlink_to(share, target_is_directory=True)
    good = tmp_path / "good"
    good.mkdir()
    (good / "python.exe").write_text("MZ", encoding="utf-8")
    _nt_scan_env(monkeypatch, [str(link / "bin"), str(good)])
    touched: list[str] = []
    _forbid_following_all(monkeypatch, link, touched)
    lstats = _record_lstat(monkeypatch)
    budget = scanbudget.Budget()

    found = _REAL_SYSTEM_CANDIDATES(budget=budget, nt=True)

    assert touched == []
    assert str(link) in lstats  # 重定向的那一级自己被 lstat（唯一一次）
    assert not any(p.startswith(str(link) + "/") for p in lstats)
    assert all(not py.startswith(str(link)) for py, _ in found)
    assert (str(good / "python.exe"), "system") in found
    assert any(r["code"] == scanbudget.ISSUE_SYMLINK_DIR for r in budget.issues())
    # 机器上某条 PATH 按策略不碰是设计内的剪枝：不把整次项目扫描降成 partial
    assert {r["severity"] for r in budget.issues()} == {scanbudget.SEVERITY_NOTE}


@POSIX  # 用符号链接在 macOS / Linux 上模拟 Windows 语义；Windows 真机不跑（建链需要特权）
def test_windows_scan_does_not_glob_under_a_redirected_localappdata(
    tmp_path, home, monkeypatch, no_process
):
    share = tmp_path / "share" / "Programs" / "Python" / "Python312"
    share.mkdir(parents=True)
    (share / "python.exe").write_text("MZ", encoding="utf-8")
    link = tmp_path / "AppDataLocal"
    link.symlink_to(tmp_path / "share", target_is_directory=True)
    _nt_scan_env(monkeypatch, [])
    monkeypatch.setenv("LOCALAPPDATA", str(link))
    touched: list[str] = []
    _forbid_following_all(monkeypatch, link, touched)
    lstats = _record_lstat(monkeypatch)

    found = _REAL_SYSTEM_CANDIDATES(budget=scanbudget.Budget(), nt=True)

    assert touched == []
    assert not any(p.startswith(str(link) + "/") for p in lstats)
    assert all(not py.startswith(str(link)) for py, _ in found)


def test_path_walker_checks_each_directory_once_and_charges_the_budget(tmp_path, monkeypatch):
    base = tmp_path / "a" / "b"
    base.mkdir(parents=True)
    for n in ("x", "y", "z"):
        (base / n).mkdir()
    lstats = _record_lstat(monkeypatch)
    budget = scanbudget.Budget()
    walker = scanbudget.PathWalker(nt=True, budget=budget)

    for n in ("x", "y", "z"):
        assert walker.redirected(str(base / n)) is None

    assert lstats.count(str(base)) == 1  # 共同祖先按目录缓存，不因 PATH 变长而重复
    assert budget.entries == len(set(lstats))


def test_path_walker_rejects_unc_and_dotdot_without_any_lstat(tmp_path, monkeypatch):
    (tmp_path / "a").mkdir()
    lstats = _record_lstat(monkeypatch)
    walker = scanbudget.PathWalker(nt=True)
    assert walker.redirected("\\\\corp-fileserver\\share\\bin\\python.exe")
    assert walker.redirected("//corp-fileserver/share/bin")
    assert walker.redirected(str(tmp_path / "a" / ".." / "b"))
    assert not any("corp-fileserver" in p for p in lstats)


def test_path_walker_does_not_reject_an_app_exec_link(tmp_path, monkeypatch):
    """商店版 python 是 AppExecLink（reparse tag 0x8000001B，非 name-surrogate）：不被当作重定向。"""
    import stat as stat_mod
    import types

    exe = tmp_path / "WindowsApps" / "python.exe"
    exe.parent.mkdir()
    exe.write_text("", encoding="utf-8")
    fake = types.SimpleNamespace(
        st_mode=stat_mod.S_IFREG | 0o644, st_file_attributes=0x400, st_reparse_tag=0x8000001B
    )
    real = os.lstat
    monkeypatch.setattr(os, "lstat", lambda p, *a, **k: fake if str(p) == str(exe) else real(p))

    assert scanbudget.PathWalker(nt=True).redirected(str(exe)) is None


@POSIX
def test_posix_scan_keeps_symlinked_ancestors_and_symlinked_python(
    tmp_path, home, monkeypatch, no_process
):
    """POSIX 语义下祖先 / 叶子软链接是常态（Homebrew、pyenv、venv）：扫描照常出现，walker 是空操作。"""
    real = _python(tmp_path / "real")
    real.chmod(0o755)
    link = tmp_path / "linked-dir"
    link.symlink_to(real.parent, target_is_directory=True)
    monkeypatch.setenv("PATH", str(link))
    monkeypatch.setattr(pool, "system_python_candidates", _REAL_SYSTEM_CANDIDATES)

    found = pool.system_python_candidates(budget=scanbudget.Budget())

    assert (str(link / "python3"), "system") in found
    assert scanbudget.PathWalker(nt=False).redirected(str(link / "python3")) is None


# ------------------------------------------------------------------ 第四轮：报告隐私 / 映射盘 / 通配段 junction（#957）


def _fixed_or(remote: set[str]):
    """注入的 GetDriveTypeW：`remote` 里的盘符是 DRIVE_REMOTE(4)，Q 盘是 NO_ROOT_DIR(1)，别的是固定盘(3)。"""
    calls: list[str] = []

    def drive_type(root: str) -> int:
        calls.append(root)
        letter = root[0].upper()
        return 4 if letter in remote else 1 if letter == "Q" else 3

    return drive_type, calls


@POSIX
def test_project_scan_report_has_no_machine_paths_for_rejected_candidates(
    tmp_path, home, monkeypatch, no_process
):
    """P2-1：机器级候选被拒（重定向 PATH 项、UNC、映射盘）后，`projscan.scan` 的公开报告里没有绝对路径 / 用户名 / UNC 主机。"""
    import json

    user_home = tmp_path / "users" / "alice-secret"
    share = tmp_path / "share"
    share.mkdir()
    redirected = user_home / "tools"
    redirected.parent.mkdir(parents=True)
    redirected.symlink_to(share, target_is_directory=True)
    unc = r"\\corp-fileserver-x\share\bin"
    _nt_scan_env(monkeypatch, [str(redirected), unc, r"Z:\tools"])
    drive_type, _calls = _fixed_or({"Z"})
    real_walker = scanbudget.PathWalker
    monkeypatch.setattr(
        scanbudget,
        "PathWalker",
        lambda **k: real_walker(drive_type=drive_type, **k),
    )
    monkeypatch.setattr(
        pool,
        "system_python_candidates",
        lambda budget=None, nt=None: _REAL_SYSTEM_CANDIDATES(budget=budget, nt=True),
    )
    project = _project(tmp_path)

    report = projscan.scan(project)

    text = json.dumps(report, ensure_ascii=False)
    for secret in ("alice-secret", "corp-fileserver-x", str(tmp_path), "Z:", "tools"):
        assert secret not in text, secret
    rows = [i for i in report["issues"] if i["code"] == scanbudget.ISSUE_SYMLINK_DIR]
    assert rows and all("path" not in r and r["severity"] == scanbudget.SEVERITY_NOTE for r in rows)


def test_path_walker_issue_rows_never_carry_a_path(tmp_path, monkeypatch):
    (tmp_path / "x").mkdir()
    budget = scanbudget.Budget()
    walker = scanbudget.PathWalker(nt=True, budget=budget, drive_type=lambda r: 4)
    assert walker.redirected(r"\\corp-fileserver-x\share")
    assert walker.redirected(r"Z:\a\b")
    assert walker.redirected(str(tmp_path / "x" / ".." / "y"))
    assert all("path" not in r for r in budget.issues())


def test_windows_mapped_remote_drive_is_rejected_before_any_lstat(tmp_path, monkeypatch):
    """P2-2：Z:（远程 / 断开的映射盘）在 GetDriveTypeW 判完之前、之后都零 lstat；Q:（无根）同拒；固定盘 C: 照常查，类型按盘符缓存。"""
    lstats = _record_lstat(monkeypatch)
    drive_type, calls = _fixed_or({"Z"})
    walker = scanbudget.PathWalker(nt=True, drive_type=drive_type)

    assert walker.redirected(r"Z:\tools") is not None
    assert walker.redirected(r"z:\other\bin") is not None
    assert walker.redirected(r"Q:\x") is not None
    assert not any(p[:1].upper() in ("Z", "Q") and p[1:2] == ":" for p in lstats)
    assert walker.redirected(r"C:\Python312") is None  # 固定盘：照常逐级 lstat（本机不存在 → 干净）
    assert any(p.upper().startswith("C:") for p in lstats)
    assert calls.count("Z:\\") == 1  # 同一盘符只问一次


def test_windows_path_entry_on_a_mapped_drive_never_reaches_the_filesystem(
    tmp_path, home, monkeypatch, no_process
):
    _nt_scan_env(monkeypatch, [r"Z:\tools", str(tmp_path)])
    drive_type, _ = _fixed_or({"Z"})
    real_walker = scanbudget.PathWalker
    monkeypatch.setattr(
        scanbudget, "PathWalker", lambda **k: real_walker(drive_type=drive_type, **k)
    )
    lstats = _record_lstat(monkeypatch)

    _REAL_SYSTEM_CANDIDATES(budget=scanbudget.Budget(), nt=True)

    assert not any(p.upper().startswith("Z:") for p in lstats)


def test_default_drive_type_is_never_the_windows_api_off_windows(monkeypatch):
    if os.name != "nt":
        assert scanbudget._windows_drive_type("C:\\") == 3  # 非 Windows 不碰 ctypes.windll


@POSIX  # 符号链接模拟 junction；Windows 真机未验证
def test_windows_scan_glob_does_not_descend_into_a_wildcard_matched_junction(
    tmp_path, home, monkeypatch, no_process
):
    """P2-3：`<LOCALAPPDATA>\\Programs\\Python\\Python312` 自己是 junction：通配展开不再交给 glob，命中的目录先过 walker，之下零访问。"""
    import glob as glob_mod

    local = tmp_path / "Local"
    pydir = local / "Programs" / "Python"
    pydir.mkdir(parents=True)
    share = tmp_path / "share" / "Python312"
    share.mkdir(parents=True)
    (share / "python.exe").write_text("MZ", encoding="utf-8")
    link = pydir / "Python312"
    link.symlink_to(share, target_is_directory=True)
    good = pydir / "Python311"
    good.mkdir()
    (good / "python.exe").write_text("MZ", encoding="utf-8")
    _nt_scan_env(monkeypatch, [])
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    touched: list[str] = []
    _forbid_following_all(monkeypatch, link, touched)

    def no_glob(*a, **k):
        raise AssertionError("扫描的通配展开不许走 glob.glob")

    monkeypatch.setattr(glob_mod, "glob", no_glob)
    lstats = _record_lstat(monkeypatch)
    scandirs: list[str] = []
    real_scandir = os.scandir
    monkeypatch.setattr(
        os, "scandir", lambda p=".": (scandirs.append(os.fspath(p)), real_scandir(p))[1]
    )
    budget = scanbudget.Budget()

    found = _REAL_SYSTEM_CANDIDATES(budget=budget, nt=True)

    assert touched == []
    assert str(link) in lstats  # junction 自己被 lstat 一次后拒
    assert not any(p.startswith(str(link) + os.sep) for p in lstats)
    assert not any(p.startswith(str(link)) for p in scandirs)
    assert all(not py.startswith(str(link)) for py, _ in found)
    assert (str(good / "python.exe"), "system") in found  # 干净的同级匹配照常出现
    assert all("path" not in r for r in budget.issues())


def test_execution_glob_keeps_the_plain_glob_behaviour(tmp_path, monkeypatch):
    """执行路径（budget=None）仍是原 `_glob`：这一轮只改只读扫描。"""
    (tmp_path / "Python311").mkdir()
    (tmp_path / "Python312").mkdir()
    got = pool._glob(str(tmp_path / "Python*"))
    assert [os.path.basename(x) for x in got] == ["Python312", "Python311"]


@POSIX
def test_scan_glob_nt_itself_drops_a_redirected_match(tmp_path):
    """`_scan_glob_nt` 自己就拒重定向命中（不靠下游再查叶子）：只回干净的那个。"""
    base = tmp_path / "Py"
    base.mkdir()
    (base / "Python311").mkdir()
    (tmp_path / "share").mkdir()
    (base / "Python312").symlink_to(tmp_path / "share", target_is_directory=True)
    walker = scanbudget.PathWalker(nt=True)

    got = pool._scan_glob_nt(str(base) + "/Python*/python.exe", scanbudget.Budget(), walker)

    assert got == [str(base / "Python311" / "python.exe")]


def test_scan_glob_nt_charges_every_entry_while_enumerating(tmp_path):
    """通配段枚举逐条记账：不命中的条目也扣预算，预算用完就停，不先把整个目录读完（Codex #957）。"""
    base = tmp_path / "Root"
    base.mkdir()
    for n in range(40):
        (base / f"other{n:02d}").mkdir()
    (base / "Python312").mkdir()

    class Counting:
        def __init__(self, limit):
            self.limit, self.used = limit, 0

        def charge_entry(self):
            self.used += 1
            return self.used <= self.limit

    walker = scanbudget.PathWalker(nt=True)
    full = Counting(1000)
    assert pool._scan_glob_nt(str(base) + "/Python*/python.exe", full, walker) == [
        str(base / "Python312" / "python.exe")
    ]
    assert full.used >= 41  # 每个条目都记了账，不只是命中的那个
    tight = Counting(5)
    pool._scan_glob_nt(str(base) + "/Python*/python.exe", tight, walker)
    assert tight.used == 6  # 第 6 次扣账失败即停，没有继续枚举


# ---- 系统候选的独立子预算（#957 windows-exe-smoke：真 Windows runner 的 PATH 很长、盘根条目多）--------------


def _static_project(tmp_path: Path) -> Path:
    root = tmp_path / "static"
    root.mkdir()
    (root / "Only_Static.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    return root


def _fake_windows_machine(monkeypatch, *, path_dirs: int, drive_entries: int) -> list[int]:
    """在任意平台上造一台「PATH 很长 + `C:\\` 条目很多」的 Windows 机器（nt=True 注入）。
    `C:` 下的 lstat 全部记数并答「不存在」，`os.scandir("C:\\")` 吐 `drive_entries` 个假条目。"""
    calls = [0]
    real_lstat, real_scandir = os.lstat, os.scandir

    def lstat(path, *a, **k):
        if os.fspath(path)[:2].upper() == "C:":
            calls[0] += 1
            raise FileNotFoundError(path)
        return real_lstat(path, *a, **k)

    class _Entry:
        def __init__(self, name):
            self.name = name

    class _Scan:
        def __init__(self, n):
            self._n = n

        def __enter__(self):
            return (_Entry(f"Dir{i}") for i in range(self._n))

        def __exit__(self, *exc):
            return False

    def scandir(path="."):
        p = os.fspath(path)
        if p[:2].upper() == "C:":
            # 只有盘根吐大量条目（`C:\\Python*` 那支）；其它 `C:` 目录（Conda / pyenv / LOCALAPPDATA 下）为空——
            # 否则真 Windows 上 `userenvs.discover` 的机器级枚举也会拿到 5000 个假条目、记在项目账上
            return _Scan(drive_entries if p.rstrip("\\/").upper() == "C:" else 0)
        return real_scandir(path)

    monkeypatch.setattr(os, "lstat", lstat)
    monkeypatch.setattr(os, "scandir", scandir)
    monkeypatch.setattr(scanbudget, "_windows_drive_type", lambda root: 3)
    # Windows 单个环境变量上限 32767 字符：目录名取短（600 项约 5 KB）；子预算的耗尽主要靠 `C:\\` 的假条目
    monkeypatch.setenv("PATH", os.pathsep.join(f"C:\\p{i}" for i in range(path_dirs)))
    monkeypatch.setenv("PATHEXT", ".EXE;.BAT;.CMD")
    monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\x\\AppData\\Local")
    return calls


def test_exhausted_system_subbudget_leaves_the_project_scan_complete(
    tmp_path, home, monkeypatch, no_process
):
    root = _static_project(tmp_path)
    _fake_windows_machine(monkeypatch, path_dirs=600, drive_entries=5000)
    monkeypatch.setattr(
        pool,
        "system_python_candidates",
        lambda budget=None, **_: _REAL_SYSTEM_CANDIDATES(budget=budget, nt=True),
    )

    # 总预算故意收得很紧（300 项）：旧行为下系统枚举会把它吃光、整次扫描降成 partial
    report = projscan.scan(root, limits=scanbudget.Limits(max_entries=300))

    assert report["state"] == projscan.STATE_COMPLETE, report["issues"]
    system_rows = [i for i in report["issues"] if i["scope"] == scanbudget.SCOPE_SYSTEM]
    assert system_rows, report["issues"]
    assert all(i["severity"] == scanbudget.SEVERITY_NOTE and "path" not in i for i in system_rows)
    assert not [i for i in report["issues"] if i["severity"] == scanbudget.SEVERITY_PARTIAL]
    assert report["budget"]["entries"] < 300  # 系统枚举的 lstat / 条目没记在项目账上


def test_system_subbudget_is_bounded_in_entries_and_time(tmp_path, monkeypatch):
    calls = _fake_windows_machine(monkeypatch, path_dirs=600, drive_entries=5000)
    parent = scanbudget.Budget()
    sub = scanbudget.system_sub_budget(parent)

    _REAL_SYSTEM_CANDIDATES(budget=sub, nt=True)

    assert sub.entries <= scanbudget.SYSTEM_MAX_ENTRIES + 1
    assert calls[0] <= scanbudget.SYSTEM_MAX_ENTRIES
    assert parent.entries == 0
    clock = [0.0]
    timed = scanbudget.Budget(
        limits=scanbudget.Limits(max_seconds=scanbudget.SYSTEM_MAX_SECONDS), clock=lambda: clock[0]
    )
    clock[0] = scanbudget.SYSTEM_MAX_SECONDS + 1
    assert timed.stop_reason() == scanbudget.ISSUE_TIME


def test_system_subbudget_still_lists_a_local_system_python(
    tmp_path, home, monkeypatch, no_process
):
    root = _static_project(tmp_path)
    local = tmp_path / "local" / "python3"
    local.parent.mkdir()
    local.write_text("", encoding="utf-8")
    monkeypatch.setattr(pool, "system_python_candidates", lambda **_: [(str(local), "system")])

    report = projscan.scan(root)

    assert report["state"] == projscan.STATE_COMPLETE
    assert any(c["source"] == "system" for c in report["environment"]["candidates"])
    assert not [i for i in report["issues"] if i["scope"] == scanbudget.SCOPE_SYSTEM]


def test_system_subbudget_never_outlives_the_parent_deadline():
    """父预算只剩 0.1 秒时开始系统枚举：子预算最多给 0.1 秒，不能再白送整整 3 秒（Codex #957）。"""
    now = [0.0]
    parent = scanbudget.Budget(clock=lambda: now[0])
    now[0] = parent.limits.max_seconds - 0.1
    sub = scanbudget.system_sub_budget(parent)
    assert sub.limits.max_seconds == pytest.approx(0.1)
    now[0] += 0.2
    assert sub.stop_reason() is not None  # 父预算的截止一过，子预算也停
    now[0] = parent.limits.max_seconds + 5
    late = scanbudget.system_sub_budget(parent)
    assert late.limits.max_seconds == 0.0


def test_machine_walk_pruning_does_not_make_a_static_project_partial(tmp_path, monkeypatch):
    """纯静态项目（没有脚本）：机器级路径被逐级检查剪掉（machine 级 note）不能把扫描升级成 partial（Codex #957）。"""
    from tavotto.engine import projscan

    budget = scanbudget.Budget()
    scanbudget.PathWalker(nt=False, budget=budget)._note("symlinked_dir")
    rows = budget.issues()
    assert rows and all(r["scope"] == scanbudget.SCOPE_MACHINE and "path" not in r for r in rows)

    real = projscan.environment_evidence

    def with_machine_pruning(root, script, budget=None, **kw):
        if budget is not None:
            scanbudget.PathWalker(nt=False, budget=budget)._note("symlinked_dir")
        return real(root, script, budget=budget, **kw)

    monkeypatch.setattr(projscan, "environment_evidence", with_machine_pruning)
    project = tmp_path / "static"
    project.mkdir()
    (project / "only.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    report = projscan.scan(project)
    assert report["state"] == projscan.STATE_COMPLETE, report.get("issues")


def test_environment_evidence_without_a_budget_uses_the_scan_safe_enumerator(tmp_path, monkeypatch):
    """页面环境建议（无预算）也只走有界、不碰网络的只读枚举：传进 system_candidates 的一定是一份预算（Codex #957）。"""
    from tavotto.engine import projscan

    seen = []
    monkeypatch.setattr(
        projscan.userenvs, "system_candidates", lambda budget=None: seen.append(budget) or []
    )
    projscan.environment_evidence(tmp_path, None, budget=None, private=True)
    assert seen and all(isinstance(b, scanbudget.Budget) for b in seen)
