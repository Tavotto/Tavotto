"""Windows 上找到用户能跑脚本的 Python 环境（ADR 0079 修订 2026-10-21）：新来源、排序、落选原因。

判据的主语：
* **发现来源**：`.condarc` 的 `envs_dirs` / `pkgs_dirs`、PATH 里 `conda.exe` / `mamba.exe` / `micromamba.exe` 反推的安装根
  （只 `is_file`、不执行）、PEP 514 注册表；Windows 路径一律注入 / monkeypatch，不依赖真 Windows；
* **排序**：确认能跑本脚本的排在没量到的、确认跑不了的前面，哪怕后者来源更靠前；
* **落选原因**：每个被评估但没选用的候选一句结构化原因（稳定码、不带路径）。
"""

from __future__ import annotations

import ntpath
import os
from pathlib import Path

import pytest

from tavotto.engine import scanbudget, userenvs

POSIX = pytest.mark.skipif(os.name == "nt", reason="bin/python 布局只在 POSIX 上")


# ----------------------------------------------------------------- .condarc


def test_condarc_simple_lists_block_inline_quotes_and_comments():
    text = (
        "# 注释\n"
        "channels:\n  - defaults\n"
        "envs_dirs:\n"
        "  - D:\\envs   # 行尾注释\n"
        '  - "E:\\my envs"\n'
        "  - 'F:/x'\n"
        "pkgs_dirs: [D:\\conda\\pkgs, 'E:\\pk gs']\n"
        "other:\n  - ignored\n"
    )
    got = userenvs._parse_condarc_lists(text, ("envs_dirs", "pkgs_dirs"))
    assert got["envs_dirs"] == ["D:\\envs", "E:\\my envs", "F:/x"]
    assert got["pkgs_dirs"] == ["D:\\conda\\pkgs", "E:\\pk gs"]


def test_condarc_items_are_bounded():
    text = "envs_dirs:\n" + "".join(f"  - /e/{i}\n" for i in range(500))
    got = userenvs._parse_condarc_lists(text, ("envs_dirs",))
    assert len(got["envs_dirs"]) == userenvs.CONDARC_MAX_ITEMS


def test_condarc_info_reads_envs_dirs_and_the_root_beside_pkgs_dirs(tmp_path):
    rc = tmp_path / ".condarc"
    rc.write_text(
        f"envs_dirs:\n  - {tmp_path / 'myenvs'}\npkgs_dirs:\n  - {tmp_path / 'conda' / 'pkgs'}\n"
        f"  - {tmp_path / 'notpkgs'}\n",
        encoding="utf-8",
    )
    envs, roots = userenvs._condarc_info(files=[str(tmp_path / "missing"), str(rc)])
    assert envs == [str(tmp_path / "myenvs")]
    assert [Path(r) for r in roots] == [tmp_path / "conda"]


def test_condarc_honours_the_condarc_variable_and_reads_it_first(tmp_path, monkeypatch):
    monkeypatch.setattr(userenvs, "_home", lambda: str(tmp_path / "home"))
    files = userenvs._condarc_files({"CONDARC": "/x/rc"}, nt=False)
    assert files[0] == "/x/rc" and files[1].endswith(".condarc")
    assert any(
        f.lower().endswith("programdata\\conda\\.condarc")
        for f in userenvs._condarc_files({}, nt=True)
    )


def test_unreadable_condarc_is_noted_not_raised(tmp_path):
    rc = tmp_path / ".condarc"
    rc.mkdir()  # 不是普通文件
    budget = scanbudget.Budget() if hasattr(scanbudget, "Budget") else None
    assert userenvs._condarc_info(budget, files=[str(rc)]) == ([], [])


@POSIX
def test_condarc_envs_dirs_environments_are_discovered(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(userenvs, "_home", lambda: str(home))
    monkeypatch.delenv("CONDARC", raising=False)
    envs = tmp_path / "bigdisk" / "envs"
    py = envs / "rootenv" / "bin" / "python3"
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\n", encoding="utf-8")
    (home / ".condarc").write_text(f"envs_dirs:\n  - {envs}\n", encoding="utf-8")
    monkeypatch.setattr(userenvs, "_conda_roots", lambda **_: [])
    monkeypatch.setattr(userenvs, "_pyenv_pythons", lambda *a, **k: [])
    monkeypatch.setattr(userenvs, "_windows_registry_pythons", lambda **_: [])
    proj = tmp_path / "proj"
    proj.mkdir()
    got = userenvs.discover(proj, ask_login_shell=False)
    assert [(c["source"], c["label"]) for c in got] == [(userenvs.SOURCE_CONDA, "rootenv")]


# ----------------------------------------------------------------- PATH 反推安装根


def _isfile_in(*files: str):
    wanted = {f.lower() for f in files}
    return lambda p: p.lower() in wanted


def test_path_conda_exe_infers_the_install_root_without_running_it():
    path = ";".join(
        [
            "C:\\Windows\\System32",
            "D:\\anaconda3\\Scripts",
            "D:\\anaconda3\\condabin",
            "E:\\mm\\Library\\bin",
            "F:\\tools\\bin",  # 没有那些可执行文件
            "\\\\server\\share\\anaconda3\\Scripts",  # 网络路径不碰
        ]
    )
    isfile = _isfile_in(
        "D:\\anaconda3\\Scripts\\conda.exe",
        "D:\\anaconda3\\condabin\\conda.bat",
        "E:\\mm\\Library\\bin\\micromamba.exe",
        "\\\\server\\share\\anaconda3\\Scripts\\conda.exe",
    )
    # 盘符类型固定为本地盘：真 Windows runner 上 E: 不存在（DRIVE_NO_ROOT_DIR），会被盘类型检查当成拒绝
    got = userenvs._path_exe_roots(path, nt=True, isfile=isfile, walker=_local_walker())
    assert got == ["D:\\anaconda3", "D:\\anaconda3", "E:\\mm"]


def test_path_roots_ignore_directories_that_are_not_conda_layouts():
    isfile = _isfile_in("D:\\Python\\mamba.exe", "D:\\bin\\conda.exe")
    assert userenvs._path_exe_roots("D:\\Python;D:\\bin", nt=True, isfile=isfile) == []


def test_windows_conda_roots_include_micromamba_mamba_root_prefix_and_path_roots(monkeypatch):
    monkeypatch.setattr(userenvs, "_home", lambda: "C:\\Users\\u")
    got = userenvs._conda_roots(
        nt=True,
        environ={"LOCALAPPDATA": "C:\\Users\\u\\AppData\\Local", "MAMBA_ROOT_PREFIX": "D:\\mm"},
        path_roots=["D:\\anaconda3"],
    )
    norm = [g.replace("/", "\\") for g in got]
    assert any(n.endswith("\\micromamba") for n in norm)
    assert "D:\\mm" in norm and "D:\\anaconda3" in norm


# ----------------------------------------------------------------- PEP 514 注册表


class FakeRegistry:
    """`{(hive, view, path): {"keys": [...], "values": {...}}}`"""

    def __init__(self, table):
        self.table = table
        self.reads = []

    def subkeys(self, hive, view, path, limit=None, stop=None):
        self.reads.append((hive, view, path))
        keys = list(self.table.get((hive, view, path), {}).get("keys", []))
        return keys if limit is None else keys[:limit]

    def values(self, hive, view, path):
        return dict(self.table.get((hive, view, path), {}).get("values", {}))


def _reg():
    P = "Software\\Python"
    return FakeRegistry(
        {
            ("HKCU", 64, P): {"keys": ["PythonCore"]},
            ("HKCU", 64, P + "\\PythonCore"): {"keys": ["3.12"]},
            ("HKCU", 64, P + "\\PythonCore\\3.12\\InstallPath"): {
                "values": {"": "C:\\Users\\u\\AppData\\Local\\Programs\\Python\\Python312\\"}
            },
            ("HKLM", 64, P): {"keys": ["ContinuumAnalytics"]},
            ("HKLM", 64, P + "\\ContinuumAnalytics"): {"keys": ["Anaconda312-64"]},
            ("HKLM", 64, P + "\\ContinuumAnalytics\\Anaconda312-64\\InstallPath"): {
                "values": {"": "D:\\anaconda3", "ExecutablePath": "D:\\anaconda3\\python.exe"}
            },
            ("HKLM", 32, P): {"keys": ["PythonCore"]},
            ("HKLM", 32, P + "\\PythonCore"): {"keys": ["3.9-32"]},
            ("HKLM", 32, P + "\\PythonCore\\3.9-32\\InstallPath"): {"values": {"": "C:\\Py39-32"}},
            # 没有 InstallPath 值 / 相对路径：丢弃
            ("HKLM", 64, P + "\\PythonCore"): {"keys": ["bad", "rel"]},
            ("HKLM", 64, P + "\\PythonCore\\rel\\InstallPath"): {
                "values": {"ExecutablePath": "python.exe"}
            },
        }
    )


def test_registry_pythons_cover_hkcu_hklm_both_views_and_prefer_executable_path():
    got = userenvs._windows_registry_pythons(_reg(), nt=True)
    by_label = dict((label, py) for py, label in got)
    assert by_label["PythonCore\\3.12"] == ntpath.join(
        "C:\\Users\\u\\AppData\\Local\\Programs\\Python\\Python312\\", "python.exe"
    )
    assert by_label["ContinuumAnalytics\\Anaconda312-64"] == "D:\\anaconda3\\python.exe"
    assert by_label["PythonCore\\3.9-32"] == "C:\\Py39-32\\python.exe"
    assert "PythonCore\\rel" not in by_label and "PythonCore\\bad" not in by_label


def test_registry_is_empty_off_windows_and_never_touches_the_reader():
    reg = _reg()
    assert userenvs._windows_registry_pythons(reg, nt=False) == []
    assert reg.reads == []


def test_registry_stops_when_the_scan_budget_runs_out():
    class Spent:
        def charge_entry(self):
            return False

        def stop_reason(self):
            return "time_budget"

    assert userenvs._windows_registry_pythons(_reg(), nt=True, budget=Spent()) == []


def test_registry_source_is_discovered_and_ranked_with_the_users_own_environments(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(userenvs, "_home", lambda: str(tmp_path / "home"))
    monkeypatch.setattr(userenvs, "_conda_prefixes", lambda *a, **k: [])
    monkeypatch.setattr(userenvs, "_pyenv_pythons", lambda *a, **k: [])
    exe = tmp_path / "Python312" / "python.exe"
    exe.parent.mkdir()
    exe.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        userenvs, "_windows_registry_pythons", lambda **_: [(str(exe), "PythonCore\\3.12")]
    )
    proj = tmp_path / "proj"
    proj.mkdir()
    got = userenvs.discover(proj, ask_login_shell=False)
    assert got == [
        {"python": str(exe), "source": userenvs.SOURCE_REGISTRY, "label": "PythonCore\\3.12"}
    ]
    # 档位：和 Conda / pyenv 同档（用户自己装的），高于老链条的系统解释器
    assert userenvs.source_tier(got[0]) == userenvs.source_tier({"source": userenvs.SOURCE_CONDA})
    assert userenvs.source_tier(got[0]) < userenvs.source_tier({"source": userenvs.SOURCE_SYSTEM})


# ----------------------------------------------------------------- 排序


def _entry(source, *, satisfies, checked=True, version="3.12.0", support="verified", **kw):
    return {
        "python": f"/{source}/{satisfies}/{version}/python",
        "source": source,
        "label": "",
        "satisfies": satisfies,
        "checked": checked,
        "python_version": version,
        "support": support,
        **kw,
    }


def test_confirmed_runnable_outranks_a_higher_source_that_cannot_run_the_script():
    shell_missing = _entry(userenvs.SOURCE_LOGIN_SHELL, satisfies=False, ok=True)
    project_unchecked = _entry(
        userenvs.SOURCE_VSCODE, satisfies=None, checked=False, version="3.13.0"
    )
    conda_ok = _entry(userenvs.SOURCE_CONDA, satisfies=True)
    registry_ok = _entry(userenvs.SOURCE_REGISTRY, satisfies=True, version="3.11.0")
    ordered = sorted([shell_missing, project_unchecked, registry_ok, conda_ok], key=userenvs.rank)
    assert ordered == [conda_ok, registry_ok, project_unchecked, shell_missing]


def test_ranking_among_confirmed_candidates_is_unchanged():
    shell = _entry(userenvs.SOURCE_LOGIN_SHELL, satisfies=True, version="3.9.0")
    conda_new = _entry(userenvs.SOURCE_CONDA, satisfies=True, version="3.13.0")
    assert userenvs.best([conda_new, shell]) is shell


# ----------------------------------------------------------------- 落选原因


@pytest.mark.parametrize(
    ("entry", "code"),
    [
        (
            {"ok": True, "satisfies": False, "checked": True, "probe_status": "module_missing"},
            userenvs.REJECT_MISSING_IMPORTS,
        ),
        (
            {"ok": True, "satisfies": False, "checked": True, "probe_status": "import_failed"},
            userenvs.REJECT_IMPORT_FAILED,
        ),
        (
            {
                "ok": False,
                "satisfies": False,
                "checked": True,
                "code": "project_env_unsupported_python",
            },
            userenvs.REJECT_PYTHON_OUT_OF_RANGE,
        ),
        (
            {"ok": False, "satisfies": False, "checked": True, "code": "project_env_no_matplotlib"},
            userenvs.REJECT_NO_MATPLOTLIB,
        ),
        (
            {
                "ok": False,
                "satisfies": False,
                "checked": True,
                "code": "project_env_worker_import_failed",
            },
            userenvs.REJECT_WORKER_CHAIN_FAILED,
        ),
        (
            {
                "ok": False,
                "satisfies": False,
                "checked": True,
                "code": "conda_launcher_unavailable",
            },
            userenvs.REJECT_ACTIVATION_FAILED,
        ),
        (
            {
                "ok": False,
                "satisfies": False,
                "checked": True,
                "code": "project_env_unusable",
                "timed_out": True,
            },
            userenvs.REJECT_PROBE_TIMEOUT,
        ),
        (
            {"ok": False, "satisfies": False, "checked": True, "code": "project_env_unusable"},
            userenvs.REJECT_UNUSABLE,
        ),
        ({"ok": None, "satisfies": None, "checked": False}, userenvs.REJECT_NOT_CHECKED),
        ({"ok": True, "satisfies": True, "checked": True}, userenvs.REJECT_OUTRANKED),
    ],
)
def test_every_rejection_carries_one_stable_code(entry, code):
    got = userenvs.rejection_reason(
        {"python": "/p/python", "source": userenvs.SOURCE_CONDA, "label": "lab", **entry}
    )
    assert got["reason"] == code and code in userenvs.REJECT_CODES
    assert got["source"] == userenvs.SOURCE_CONDA and got["label"] == "lab"


def test_a_rejection_names_what_is_missing_and_never_carries_a_path():
    got = userenvs.rejection_reason(
        {
            "python": "/secret/home/envs/lab/bin/python",
            "source": userenvs.SOURCE_CONDA,
            "label": "lab",
            "ok": True,
            "satisfies": False,
            "checked": True,
            "probe_status": "module_missing",
            "missing": ["root", "uproot"],
        }
    )
    assert got["missing"] == ["root", "uproot"]
    assert got["id"] == userenvs.env_id("/secret/home/envs/lab/bin/python")
    assert "/secret" not in repr(got)


def test_rejections_leave_out_the_selected_one():
    a = _entry(userenvs.SOURCE_CONDA, satisfies=True, version="3.12.0")
    b = _entry(userenvs.SOURCE_PYENV, satisfies=False, ok=True, probe_status="module_missing")
    got = userenvs.rejections([a, b], a)
    assert [g["reason"] for g in got] == [userenvs.REJECT_MISSING_IMPORTS]


_REAL_WINREG_READER = userenvs._WinregReader  # conftest 默认把它换成空读取器；这里要真类


# ------------------------------------------------- Codex #1274：落盘前先过守卫 / 枚举中就受限


def _local_walker():
    from tavotto.engine import scanbudget

    return scanbudget.PathWalker(nt=True, drive_type=lambda root: 3)  # DRIVE_FIXED


def _remote_walker():
    from tavotto.engine import scanbudget

    return scanbudget.PathWalker(nt=True, drive_type=lambda root: 4)  # DRIVE_REMOTE


def test_path_exe_roots_never_probes_a_directory_on_a_remote_drive():
    probed = []

    def isfile(p):
        probed.append(p)
        return True

    got = userenvs._path_exe_roots(
        "Z:\\anaconda3\\Scripts", nt=True, isfile=isfile, walker=_remote_walker()
    )
    assert got == [] and probed == []


def test_condarc_on_a_remote_drive_or_unc_is_never_opened(monkeypatch):
    from tavotto.engine import scanbudget

    opened = []
    monkeypatch.setattr(
        scanbudget,
        "read_regular_text",
        lambda *a, **k: opened.append(a) or "envs_dirs:\n  - D:\\e\n",
    )
    files = ["Z:\\share\\.condarc", "\\\\host\\share\\.condarc"]
    assert userenvs._condarc_info(files=files, walker=_remote_walker()) == ([], [])
    assert opened == []


def test_winreg_subkey_enumeration_stops_at_the_cap_and_when_the_budget_is_spent():
    calls = []

    class FakeWinreg:
        HKEY_CURRENT_USER = HKEY_LOCAL_MACHINE = 0
        KEY_READ = KEY_WOW64_32KEY = KEY_WOW64_64KEY = 0

        class _Key:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        @classmethod
        def OpenKey(cls, *a):
            return cls._Key()

        @staticmethod
        def EnumKey(key, i):
            calls.append(i)
            if i >= 4096:
                raise OSError
            return f"k{i}"

    reader = _REAL_WINREG_READER.__new__(_REAL_WINREG_READER)
    reader._w = FakeWinreg
    assert len(reader.subkeys("HKCU", 64, "x", limit=5)) == 5 and len(calls) == 5
    calls.clear()
    assert reader.subkeys("HKCU", 64, "x", limit=5, stop=lambda: True) == [] and calls == []
