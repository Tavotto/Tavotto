"""「生成可编辑版本」缺包时的依赖决定（`engine/convertdeps.py`，ADR 0143 修订，维护者 2026-10-23 两次裁决）。

名单（大名单代替小闭集）/ 四类环境各自的分支 / 常驻同意与撤销 / 点卡片回显摘要 / 取消传到安装。
`deprepair` 的计划 / 安装入口与 `pool.resolve_worker_python` 全部 monkeypatch——不装任何真包、不碰真实环境。
"""

from __future__ import annotations

import inspect
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from tavotto.engine import config, convertdeps, deprepair, pool

#: 各平台「OS 自带解释器」的一个代表路径（Windows 没有 /usr/bin：那里在 Windows 上就是普通目录）
OS_PYTHON = (
    os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "py.exe")
    if os.name == "nt"
    else "/usr/bin/python3"
)

SCRIPT = "tavotto_convert_probe_x.py"


# --------------------------------------------------------------------------- 名单
def test_allowlist_file_is_normalized_sorted_free_data_and_covers_the_seed():
    names = convertdeps.allowlist()
    assert convertdeps.SEED_DISTRIBUTIONS <= names
    assert all(n == convertdeps.depresolve.normalize_distribution(n) for n in names), (
        "文件里每一行都是 PEP 503 规范名"
    )
    text = convertdeps.ALLOWLIST_PATH.read_text(encoding="utf-8")
    assert text.startswith("#") and "来源：" in text and "快照日期：" in text, (
        "文件头写明来源 / 快照日期"
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("pandas", True),
        ("Pandas", True),
        ("scikit_learn", True),
        ("Scikit.Learn", True),
        ("AdjustText", True),
        ("definitely-not-a-real-dist-xyz", False),
        ("", False),
    ],
)
def test_allowlist_hit_miss_and_normalized_names(name, expected):
    assert convertdeps.in_allowlist(name) is expected


def test_a_missing_allowlist_file_degrades_to_the_seed_never_larger(monkeypatch, tmp_path):
    monkeypatch.setattr(convertdeps, "ALLOWLIST_PATH", tmp_path / "nope.txt")
    convertdeps.allowlist.cache_clear()
    try:
        assert convertdeps.allowlist() == convertdeps.SEED_DISTRIBUTIONS
    finally:
        convertdeps.allowlist.cache_clear()


def test_allowlist_parses_comments_blanks_and_normalizes(monkeypatch, tmp_path):
    f = tmp_path / "l.txt"
    f.write_text("# c\n\nFoo_Bar  # trailing\nbaz.qux\n", encoding="utf-8")
    monkeypatch.setattr(convertdeps, "ALLOWLIST_PATH", f)
    convertdeps.allowlist.cache_clear()
    try:
        names = convertdeps.allowlist()
        assert {"foo-bar", "baz-qux"} <= names and "# c" not in names
    finally:
        convertdeps.allowlist.cache_clear()


def test_the_spec_ships_the_allowlist_file():
    spec = (Path(__file__).resolve().parent.parent / "packaging" / "tavotto.spec").read_text(
        encoding="utf-8"
    )
    assert 'str(PKG / "engine" / "autoinstall_dists.txt")' in spec


# --------------------------------------------------------------------------- 夹具
def _joint(requirements=("pandas>=2.0", "statsmodels"), **over):
    base = dict(
        status="ready",
        blocked=(),
        unknown=(),
        not_pip_installable=(),
        requirements=tuple(requirements),
    )
    base.update(over)
    return SimpleNamespace(**base)


def _bound(requirements=("pandas>=2.0", "statsmodels"), **over):
    base = dict(
        plan_id="p1",
        target_kind=deprepair.TARGET_PROJECT_VENV,
        creates_environment=False,
        private_python=None,
        requirements=tuple(requirements),
        impact_digest="digest-1",
        impact={"target_kind": deprepair.TARGET_PROJECT_VENV},
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def store(monkeypatch):
    """用户配置的内存替身：`ai_settings` / `set_ai_settings` 读写同一份 dict。"""
    data: dict = {}
    monkeypatch.setattr(config, "ai_settings", lambda: data)
    monkeypatch.setattr(config, "set_ai_settings", lambda patch: data.update(patch) or data)
    monkeypatch.delenv("TAVOTTO_NO_CONVERT_AUTO_INSTALL", raising=False)
    return data


@pytest.fixture
def fake_repair(monkeypatch, store, tmp_path):
    st = SimpleNamespace(
        joint=_joint(),
        kind=deprepair.TARGET_PROJECT_VENV,
        source=pool.SOURCE_PROJECT_VENV,
        python=str(tmp_path / "proj" / ".venv" / "bin" / "python"),
        bound=_bound(),
        prepare_result={"state": deprepair.STATE_DONE},
        prepare_error=None,
        calls=[],
        discarded=[],
    )

    def joint_plan_for(project, script, **kw):
        st.calls.append(("joint_plan_for", project, script))
        return st.joint, st.kind, st.python

    def joint_target_for(project, script):
        st.calls.append(("joint_target_for", project, script))
        return st.kind, st.python, st.source

    def create_joint_plan(project, script, **kw):
        st.calls.append(("create_joint_plan", project, script, kw))
        if isinstance(st.bound, Exception):
            raise st.bound
        return st.bound

    def prepare(plan_id, on_event=None, *, claimed=False, confirmed_impact=None):
        st.calls.append(("prepare", plan_id, confirmed_impact))
        if st.prepare_error is not None:
            raise st.prepare_error
        return st.prepare_result

    monkeypatch.setattr(deprepair, "joint_plan_for", joint_plan_for)
    monkeypatch.setattr(deprepair, "joint_target_for", joint_target_for)
    monkeypatch.setattr(deprepair, "create_joint_plan", create_joint_plan)
    monkeypatch.setattr(deprepair, "prepare", prepare)
    monkeypatch.setattr(deprepair, "_discard_plan", lambda pid: st.discarded.append(pid))
    monkeypatch.setattr(
        pool, "resolve_worker_python", lambda project, script=None, **kw: (st.python, st.source)
    )
    return st


def _names(st):
    return [c[0] for c in st.calls]


def _as(
    st, tmp_path, *, source, kind=deprepair.TARGET_MANAGED, python=None, conda=False, ext=False
):
    """把假环境换成某一类：解释器放在 tmp 下的真目录里，让路径 / 标记文件判据有东西可看。"""
    prefix = tmp_path / ("envs" if conda else "other") / "myenv"
    (prefix / "bin").mkdir(parents=True, exist_ok=True)
    if conda:
        (prefix / "conda-meta").mkdir(exist_ok=True)
    if ext:
        (prefix / "lib" / "python3.12").mkdir(parents=True, exist_ok=True)
        (prefix / "lib" / "python3.12" / "EXTERNALLY-MANAGED").write_text("[externally-managed]\n")
    st.python = python or str(prefix / "bin" / "python")
    st.source = source
    st.kind = kind
    st.bound = _bound(target_kind=kind, impact={"target_kind": kind, "creates_environment": False})


# --------------------------------------------------------------------------- 环境分类
def test_env_key_is_a_stable_hash_without_the_path():
    k = convertdeps.env_key("/a/b/python")
    assert k == convertdeps.env_key("/a/b/python") and len(k) == 16 and "/a" not in k
    assert k != convertdeps.env_key("/a/c/python")


def test_describe_env_classifies_the_four_families(tmp_path, fake_repair):
    conda = tmp_path / "envs" / "myenv"
    (conda / "bin").mkdir(parents=True)
    (conda / "conda-meta").mkdir()
    got = convertdeps.describe_env(str(conda / "bin" / "python"), pool.SOURCE_SYSTEM)
    assert (got["kind"], got["name"]) == ("conda", "myenv") and "key" in got
    homebrew = tmp_path / "brew"
    (homebrew / "lib" / "python3.12").mkdir(parents=True)
    (homebrew / "lib" / "python3.12" / "EXTERNALLY-MANAGED").write_text("x")
    (homebrew / "bin").mkdir()
    assert (
        convertdeps.describe_env(str(homebrew / "bin" / "python3"), pool.SOURCE_SYSTEM)["kind"]
        == "system"
    )
    assert convertdeps.describe_env(OS_PYTHON, pool.SOURCE_SYSTEM)["kind"] == "system"
    assert convertdeps.describe_env("/x/py", pool.SOURCE_BUNDLED)["kind"] == "builtin"
    assert convertdeps.describe_env("/x/py", pool.SOURCE_CURRENT)["kind"] == "builtin"
    assert convertdeps.describe_env("/x/py", pool.SOURCE_PROJECT_VENV)["kind"] == "project_venv"
    assert convertdeps.describe_env("/x/py", pool.SOURCE_MANAGED_PROJECT)["kind"] == "managed"
    assert (
        convertdeps.describe_env(str(tmp_path / "o" / "bin" / "python"), pool.SOURCE_CONFIGURED)[
            "kind"
        ]
        == "other"
    )


def test_a_venv_python_is_not_mistaken_for_the_system_python_it_links_to(tmp_path):
    base = tmp_path / "usrlike"
    (base / "bin").mkdir(parents=True)
    (base / "bin" / "python3").write_text("")
    venv = tmp_path / "proj" / ".venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").symlink_to(base / "bin" / "python3")
    got = convertdeps.describe_env(str(venv / "bin" / "python"), pool.SOURCE_CONFIGURED)
    assert got["kind"] == "other" and got["name"] == "proj/.venv"


# --------------------------------------------------------------------------- 直接装的两类（上一轮口径，名单内）
def test_project_venv_installs_listed_packages_directly_through_the_existing_entry(fake_repair):
    got = convertdeps.ensure("/proj", SCRIPT)
    assert got["status"] == "installed" and got["installed"] == ["pandas", "statsmodels"]
    created = next(c for c in fake_repair.calls if c[0] == "create_joint_plan")
    assert created[3] == {"target_kind": deprepair.TARGET_PROJECT_VENV}
    assert ("prepare", "p1", "digest-1") in fake_repair.calls, (
        "已授权口径：服务端回显同次调用算出的摘要"
    )


def test_existing_managed_generation_installs_directly(fake_repair, tmp_path):
    _as(fake_repair, tmp_path, source=pool.SOURCE_MANAGED_PROJECT)
    assert convertdeps.ensure("/proj", SCRIPT)["status"] == "installed"
    assert any(c[0] == "prepare" for c in fake_repair.calls)


@pytest.mark.parametrize("over", [{"creates_environment": True}, {"private_python": {"bytes": 1}}])
def test_managed_target_that_would_create_or_download_asks_instead_of_installing(
    fake_repair, tmp_path, over
):
    _as(fake_repair, tmp_path, source=pool.SOURCE_MANAGED_PROJECT)
    fake_repair.bound = _bound(
        target_kind=deprepair.TARGET_MANAGED,
        impact={"target_kind": deprepair.TARGET_MANAGED, **over},
        **over,
    )
    got = convertdeps.ensure("/proj", SCRIPT)
    assert got["status"] == "hold" and got["card"]["kind"] == "ask_once"
    assert got["card"]["impact_digest"] == "digest-1"
    assert "prepare" not in _names(fake_repair) and fake_repair.discarded == ["p1"]


# --------------------------------------------------------------------------- 名单外：点名卡片，永不自动装
@pytest.mark.parametrize("source", [pool.SOURCE_PROJECT_VENV, pool.SOURCE_CONFIGURED])
def test_unlisted_packages_never_auto_install_and_are_named_on_the_card(
    fake_repair, tmp_path, source
):
    fake_repair.joint = _joint(("pandas", "obscure-pkg"))
    fake_repair.bound = _bound(("pandas", "obscure-pkg"))
    if source != pool.SOURCE_PROJECT_VENV:
        _as(fake_repair, tmp_path, source=source)
        fake_repair.bound = _bound(("pandas", "obscure-pkg"), target_kind=deprepair.TARGET_MANAGED)
    got = convertdeps.ensure("/proj", SCRIPT)
    assert got["status"] == "hold" and got["card"]["kind"] == "uncommon"
    assert got["card"]["outside_list"] == ["obscure-pkg"]
    assert got["card"]["packages"] == ["obscure-pkg", "pandas"]
    assert "prepare" not in _names(fake_repair)


def test_a_consented_environment_still_asks_for_unlisted_packages(fake_repair, tmp_path):
    _as(fake_repair, tmp_path, source=pool.SOURCE_CONFIGURED)
    env = convertdeps.describe_env(fake_repair.python, pool.SOURCE_CONFIGURED)
    convertdeps.grant(env)
    fake_repair.bound = _bound(("pandas", "obscure-pkg"), target_kind=deprepair.TARGET_MANAGED)
    got = convertdeps.ensure("/proj", SCRIPT)
    assert got["card"]["kind"] == "uncommon" and "prepare" not in _names(fake_repair)


def test_a_bound_plan_that_grew_beyond_the_list_asks_instead_of_installing(fake_repair):
    fake_repair.bound = _bound(("pandas", "declared-in-requirements-txt"))
    got = convertdeps.ensure("/proj", SCRIPT)
    assert got["card"]["kind"] == "uncommon" and got["card"]["outside_list"] == [
        "declared-in-requirements-txt"
    ]
    assert fake_repair.discarded == ["p1"] and "prepare" not in _names(fake_repair)


# --------------------------------------------------------------------------- 系统 Python / 内置 runtime：永不装
@pytest.mark.parametrize(
    ("source", "kw"),
    [
        (pool.SOURCE_SYSTEM, {"ext": True}),
        (pool.SOURCE_CONFIGURED, {"python": OS_PYTHON}),
        (pool.SOURCE_BUNDLED, {}),
        (pool.SOURCE_CURRENT, {}),
    ],
)
def test_system_and_builtin_pythons_are_never_installed_into(fake_repair, tmp_path, source, kw):
    _as(fake_repair, tmp_path, source=source, **kw)
    got = convertdeps.ensure("/proj", SCRIPT)
    assert got["status"] == "hold" and got["card"]["kind"] == "system"
    assert got["card"]["action"] == "choose_env" and got["card"]["packages"]
    assert "create_joint_plan" not in _names(fake_repair) and "prepare" not in _names(fake_repair)
    # 点了也不装：这是边界不是偏好
    again = convertdeps.resolve("/proj", SCRIPT, confirmed_impact="anything")
    assert again["status"] == "hold" and again["card"]["kind"] == "system"
    assert "prepare" not in _names(fake_repair)


# --------------------------------------------------------------------------- 其余环境：问一次 + 常驻同意
def test_other_environments_ask_once_with_a_readable_name_and_no_machine_path(
    fake_repair, tmp_path
):
    _as(fake_repair, tmp_path, source=pool.SOURCE_SYSTEM, conda=True)
    got = convertdeps.ensure("/proj", SCRIPT)
    card = got["card"]
    assert got["status"] == "hold" and card["kind"] == "ask_once"
    assert card["env"] == {"kind": "conda", "name": "myenv"}
    assert card["action"] == "install_managed", "现有链条对这类环境的标准动作 = 受管环境"
    assert card["packages"] == ["pandas", "statsmodels"] and card["outside_list"] == []
    assert card["impact_digest"] == "digest-1"
    assert str(tmp_path) not in repr(got), "卡片里没有机器路径"
    assert "prepare" not in _names(fake_repair), "问之前一个字节不装"
    created = next(c for c in fake_repair.calls if c[0] == "create_joint_plan")
    assert created[3] == {"target_kind": deprepair.TARGET_MANAGED}
    assert fake_repair.discarded == ["p1"]


def test_clicking_the_main_button_records_consent_then_installs_with_the_echoed_digest(
    fake_repair, tmp_path, store
):
    _as(fake_repair, tmp_path, source=pool.SOURCE_CONFIGURED)
    env = convertdeps.describe_env(fake_repair.python, pool.SOURCE_CONFIGURED)
    assert convertdeps.consented_envs() == []
    got = convertdeps.resolve("/proj", SCRIPT, confirmed_impact="digest-1")
    assert got["status"] == "installed" and got["installed"] == ["pandas", "statsmodels"]
    assert ("prepare", "p1", "digest-1") in fake_repair.calls
    (rec,) = convertdeps.consented_envs()
    assert rec["key"] == env["key"] and rec["name"] == "myenv"
    stored = repr(store)
    assert fake_repair.python not in stored, "只存指纹 + 种类 + 名字，不存明文路径"


def test_after_consent_the_same_environment_installs_without_asking(fake_repair, tmp_path):
    _as(fake_repair, tmp_path, source=pool.SOURCE_CONFIGURED)
    convertdeps.grant(convertdeps.describe_env(fake_repair.python, pool.SOURCE_CONFIGURED))
    got = convertdeps.ensure("/proj", SCRIPT)
    assert got["status"] == "installed"
    created = next(c for c in fake_repair.calls if c[0] == "create_joint_plan")
    assert created[3] == {"target_kind": deprepair.TARGET_MANAGED}, "走现有链条对这类环境的标准动作"
    assert ("prepare", "p1", "digest-1") in fake_repair.calls, (
        "已记过同意的环境：服务端回显同次算出的摘要"
    )


def test_consent_is_per_environment_not_global(fake_repair, tmp_path):
    _as(fake_repair, tmp_path, source=pool.SOURCE_CONFIGURED)
    convertdeps.grant(convertdeps.describe_env("/some/other/python", pool.SOURCE_CONFIGURED))
    assert convertdeps.ensure("/proj", SCRIPT)["status"] == "hold"


def test_consent_can_be_revoked_and_the_next_install_asks_again(fake_repair, tmp_path):
    _as(fake_repair, tmp_path, source=pool.SOURCE_CONFIGURED)
    env = convertdeps.describe_env(fake_repair.python, pool.SOURCE_CONFIGURED)
    convertdeps.grant(env)
    assert convertdeps.revoke(env["key"]) is True and convertdeps.revoke(env["key"]) is False
    assert convertdeps.consented_envs() == []
    assert convertdeps.ensure("/proj", SCRIPT)["status"] == "hold"


def test_a_consented_environment_that_would_download_python_still_asks(fake_repair, tmp_path):
    _as(fake_repair, tmp_path, source=pool.SOURCE_CONFIGURED)
    convertdeps.grant(convertdeps.describe_env(fake_repair.python, pool.SOURCE_CONFIGURED))
    fake_repair.bound = _bound(
        target_kind=deprepair.TARGET_MANAGED,
        private_python={"bytes": 1},
        impact={"target_kind": deprepair.TARGET_MANAGED, "private_python": {"bytes": 1}},
    )
    got = convertdeps.ensure("/proj", SCRIPT)
    assert got["status"] == "hold" and got["card"]["downloads_python"] is True
    assert "prepare" not in _names(fake_repair)


def test_clicking_a_stale_digest_does_not_install_and_returns_a_fresh_card(fake_repair, tmp_path):
    """问过一次的口径：确认来自用户这次真实点击——摘要对不上就不装，也不自回显。"""
    _as(fake_repair, tmp_path, source=pool.SOURCE_CONFIGURED)
    got = convertdeps.resolve("/proj", SCRIPT, confirmed_impact="digest-OLD")
    assert got["status"] == "hold" and got["code"] == "dependency_impact_changed"
    assert got["card"]["impact_digest"] == "digest-1"
    assert "prepare" not in _names(fake_repair) and convertdeps.consented_envs() == []
    missing = convertdeps.resolve("/proj", SCRIPT, confirmed_impact=None)
    assert missing["status"] == "hold" and "prepare" not in _names(fake_repair)


def test_clicking_an_uncommon_card_installs_once_without_recording_consent(fake_repair, tmp_path):
    _as(fake_repair, tmp_path, source=pool.SOURCE_CONFIGURED)
    fake_repair.bound = _bound(
        ("pandas", "obscure-pkg"), target_kind=deprepair.TARGET_MANAGED, impact_digest="d2"
    )
    got = convertdeps.resolve("/proj", SCRIPT, confirmed_impact="d2")
    assert got["status"] == "installed"
    assert convertdeps.consented_envs() == [], "名单外的包只装这一次，不记常驻同意"


# --------------------------------------------------------------------------- 不可装的计划与开关
@pytest.mark.parametrize(
    "over",
    [
        {"status": "blocked"},
        {"blocked": ({"reason": "x"},)},
        {"unknown": ("mystery",)},
        {"not_pip_installable": ({"name": "arcpy"},)},
    ],
)
def test_blocked_or_unresolved_plans_are_skipped_without_any_action(fake_repair, over):
    fake_repair.joint = _joint(**over)
    got = convertdeps.ensure("/proj", SCRIPT)
    assert (got["status"], got["reason"]) == ("skipped", "plan_blocked")
    assert "prepare" not in _names(fake_repair) and "create_joint_plan" not in _names(fake_repair)


def test_nothing_missing_installs_nothing(fake_repair):
    fake_repair.joint = _joint(status="nothing_needed", requirements=())
    assert convertdeps.ensure("/proj", SCRIPT)["status"] == "none"
    assert "create_joint_plan" not in _names(fake_repair)


def test_the_repair_chains_own_refusals_are_skipped_with_the_code(fake_repair):
    fake_repair.bound = deprepair.RepairError(deprepair.ERROR_INTERPRETER_PINNED, "pinned")
    got = convertdeps.ensure("/proj", SCRIPT)
    assert (got["status"], got["reason"], got["code"]) == (
        "skipped",
        "plan_refused",
        "dependency_interpreter_pinned",
    )


def test_unresolvable_interpreter_is_skipped(monkeypatch, fake_repair):
    def boom(*a, **k):
        raise pool.WorkerError("x", "no_worker_python")

    monkeypatch.setattr(deprepair, "joint_plan_for", boom)
    assert convertdeps.ensure("/proj", SCRIPT)["reason"] == "plan_unavailable"


@pytest.mark.parametrize(
    "result",
    [
        {"state": deprepair.STATE_FAILED, "code": "dependency_network_unavailable"},
        None,
    ],
)
def test_install_failure_is_reported_with_the_repair_code(fake_repair, result):
    fake_repair.prepare_result = result
    got = convertdeps.ensure("/proj", SCRIPT)
    assert got["status"] == "failed" and got["missing"] == ["pandas", "statsmodels"]
    assert got["code"] == ((result or {}).get("code") or deprepair.ERROR_FAILED)


def test_a_raising_prepare_is_a_failure_not_a_crash(fake_repair):
    fake_repair.prepare_error = deprepair.RepairError(deprepair.ERROR_IMPACT_CHANGED, "changed")
    got = convertdeps.ensure("/proj", SCRIPT)
    assert (got["status"], got["code"]) == ("failed", "dependency_impact_changed")
    fake_repair.prepare_error = RuntimeError("boom")
    assert convertdeps.ensure("/proj", SCRIPT)["status"] == "failed"


def test_the_switch_turns_auto_install_off_everywhere(fake_repair, store, monkeypatch):
    store["convert"] = {"auto_install": False}
    got = convertdeps.ensure("/proj", SCRIPT)
    assert (got["status"], got["reason"]) == ("skipped", "disabled") and not fake_repair.calls
    store.clear()
    monkeypatch.setenv("TAVOTTO_NO_CONVERT_AUTO_INSTALL", "1")
    assert convertdeps.ensure("/proj", SCRIPT)["reason"] == "disabled" and not fake_repair.calls


def test_auto_install_is_on_by_default_and_only_an_explicit_false_turns_it_off(store):
    assert convertdeps.enabled() is True
    store["convert"] = {"auto_install": None}
    assert convertdeps.enabled() is True
    store["convert"] = {"auto_install": False}
    assert convertdeps.enabled() is False


def test_granting_consent_keeps_the_other_convert_settings(store):
    store["convert"] = {"enabled": True, "auto_install": True}
    convertdeps.grant({"key": "abc", "kind": "conda", "name": "e"})
    assert store["convert"]["enabled"] is True and store["convert"]["auto_install"] is True
    convertdeps.revoke("abc")
    assert convertdeps.CONSENT_KEY not in store["convert"] and store["convert"]["enabled"] is True


# --------------------------------------------------------------------------- 取消传到安装（Codex #1330 P1-1）
def test_cancel_reaches_a_slow_prepare_and_nothing_is_written_afterwards(fake_repair, monkeypatch):
    """慢的 prepare 执行中取消：安装入口收到 deprepair 的取消信号（`cancel_status` 置位计划的取消句柄），
    它在检查点停手；之后没有任何写入调用，结局 cancelled，也没有激活受管环境一代。"""
    started = threading.Event()
    writes: list[str] = []
    seen: dict = {}

    def slow_prepare(plan_id, on_event=None, *, claimed=False, confirmed_impact=None):
        ev = deprepair._register_cancel(plan_id)  # 与真 prepare 同一个句柄
        started.set()
        seen["got_signal"] = ev.wait(10)
        if ev.is_set():
            return {"state": deprepair.STATE_CANCELLED, "code": deprepair.ERROR_CANCELLED}
        writes.append("pip install")  # 没收到取消才会走到这里：真 prepare 在这一步之后写环境
        return {"state": deprepair.STATE_DONE}

    monkeypatch.setattr(deprepair, "prepare", slow_prepare)
    cancel = threading.Event()
    out: dict = {}
    t = threading.Thread(target=lambda: out.update(convertdeps.ensure("/proj", SCRIPT, cancel)))
    t.start()
    assert started.wait(10)
    cancel.set()
    t.join(15)
    assert not t.is_alive()
    assert seen["got_signal"] is True and writes == []
    assert out["status"] == "cancelled" and out["code"] == deprepair.ERROR_CANCELLED
    assert out["installed"] == []
    assert "pid" not in repr(deprepair._cancels) and "p1" not in deprepair._cancels


def test_cancel_set_before_the_install_starts_never_calls_a_write(fake_repair, monkeypatch):
    called = []

    def prepare(plan_id, on_event=None, *, claimed=False, confirmed_impact=None):
        ev = deprepair._register_cancel(plan_id)
        called.append(ev.is_set())
        return (
            {"state": deprepair.STATE_CANCELLED, "code": deprepair.ERROR_CANCELLED}
            if ev.is_set()
            else {"state": deprepair.STATE_DONE}
        )

    monkeypatch.setattr(deprepair, "prepare", prepare)
    cancel = threading.Event()
    cancel.set()
    got = convertdeps.ensure("/proj", SCRIPT, cancel)
    assert got["status"] == "cancelled" and called == [True], "prepare 进去时取消句柄已经是置位的"


def test_a_cancel_that_arrives_after_the_commit_point_reports_the_truth(fake_repair, monkeypatch):
    """过了提交点的取消被 `cancel_status` 拒绝：环境已经换代，结果如实回 installed（不假装取消成功）。"""

    def prepare(plan_id, on_event=None, *, claimed=False, confirmed_impact=None):
        deprepair._register_cancel(plan_id)
        return {"state": deprepair.STATE_DONE}

    monkeypatch.setattr(deprepair, "prepare", prepare)
    cancel = threading.Event()
    cancel.set()
    assert convertdeps.ensure("/proj", SCRIPT, cancel)["status"] == "installed"


def test_no_new_pip_or_subprocess_path_in_convertdeps():
    """自检：convertdeps 不新写任何安装 / 子进程路径，fixture 替换的正是它调用的 deprepair 入口名。"""
    for name in (
        "joint_plan_for",
        "joint_target_for",
        "create_joint_plan",
        "prepare",
        "_discard_plan",
        "_register_cancel",
        "cancel_status",
    ):
        assert callable(getattr(deprepair, name))
    src = inspect.getsource(convertdeps)
    assert "subprocess" not in src and "pip install" not in src.replace("`pip install`", "")
