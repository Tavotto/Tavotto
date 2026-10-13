"""桌面拥有的 native 启动身份的合同（P06，ADR 0125）。

有意变更的旧合同：「native 禁 run_config / env 恒 None / 公开投影沿用终端形状」。这里同时钉住
**没变的**（terminal 拥有的 native 与 safe 的身份逐字节不变），以及**新的**：桌面拥有的身份
必须写全 cwd / env 来源 / stdin 通道 / 运行配置引用，且 safe 拿不到任何一个开关。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from support import nativekit
from tavotto.engine import execspec, preparation

PY = os.path.abspath(sys.executable)
ROOT = os.path.abspath("/tmp/p06-project")
SCRIPT = os.path.join(ROOT, "fig.py")
CWD = os.path.abspath("/tmp/p06-cwd")


def desktop(**kw):
    base = {
        "interpreter": PY,
        "cwd": CWD,
        "project_root": ROOT,
        "argv": ("--seed", "s3cret"),
        "run_config": "rc_1",
        "env": {"K": "v-secret"},
    }
    base.update(kw)
    return execspec.desktop_native_spec(SCRIPT, **base)


# ---------------------------------------------------------------------------
# 没变的：terminal 拥有的 native 与 safe 逐字节不变
# ---------------------------------------------------------------------------
def test_terminal_owned_native_identity_is_byte_for_byte_the_old_shape():
    spec = execspec.native_spec(
        SCRIPT, interpreter=PY, cwd=CWD, project_root=ROOT, argv=("--a", "1")
    )
    assert spec.owner == execspec.OWNER_TERMINAL
    assert spec.stable_payload() == {
        "profile": "native",
        "target_kind": "script",
        "target": "fig.py",
        "entry": None,
        "argv": ["--a", "1"],  # 终端自己的命令行：沿用旧形状
        "passthrough_savefig": True,
        "cwd_mode": "sandbox",
        "spec_version": 1,
    }
    ctx = execspec.launch_context(spec)
    assert ctx["cwd_origin"] == "invocation.cwd" and ctx["argv"] == ["--a", "1"]
    assert not {"owner", "env_source", "env_keys", "stdin", "argv_count"} & set(ctx)


def test_terminal_owned_native_still_refuses_run_config_env_and_a_non_inherited_stdin():
    base = {"interpreter": PY, "cwd": CWD, "project_root": ROOT}
    ok = execspec.native_spec(SCRIPT, **base)
    for field, value in (
        ("run_config", "rc_x"),
        ("env", {"A": "b"}),
        ("stdin", execspec.STDIN_NULL),
        ("env_source", execspec.ENV_SOURCE_SIDECAR),
    ):
        import dataclasses

        with pytest.raises(ValueError):
            dataclasses.replace(ok, **{field: value})


def test_safe_spec_cannot_be_given_the_desktop_switches():
    """不能给 safe 加开关绕保护：owner / env_source / stdin 只属于 native。"""
    import dataclasses

    safe = execspec.safe_spec("fig.py", ROOT, "__main__", interpreter=PY, sandbox="/tmp/sb")
    assert safe.owner == ""
    for field, value in (
        ("owner", execspec.OWNER_DESKTOP),
        ("owner", execspec.OWNER_TERMINAL),
        ("env_source", execspec.ENV_SOURCE_SIDECAR),
        ("stdin", execspec.STDIN_PIPE),
    ):
        with pytest.raises(ValueError, match="native"):
            dataclasses.replace(safe, **{field: value})
    # safe 的守卫 / 吞捕获一字不动
    assert safe.passthrough_savefig is False


# ---------------------------------------------------------------------------
# 新的：桌面拥有的启动身份
# ---------------------------------------------------------------------------
def test_desktop_identity_carries_everything_the_desktop_must_own():
    spec = desktop(stdin=execspec.STDIN_PIPE)
    assert (spec.profile, spec.owner) == ("native", "desktop")
    assert spec.entry is None and spec.passthrough_savefig is True  # 仍是 native 的语义
    assert spec.cwd == CWD and spec.env == {"K": "v-secret"}
    assert spec.env_source == execspec.ENV_SOURCE_SIDECAR and spec.stdin == "pipe"
    assert spec.raw_target == SCRIPT and spec.run_config == "rc_1"


@pytest.mark.parametrize(
    ("kw", "why"),
    [
        ({"cwd": "relative/dir"}, "cwd 必须显式绝对"),
        ({"interpreter": "python"}, "解释器必须绝对"),
        ({"run_config": ""}, "argv 非空必须有运行配置引用"),
        ({"stdin": execspec.STDIN_INHERIT}, "桌面没有可继承的 stdin"),
    ],
)
def test_desktop_identity_rejects_incomplete_launches(kw, why):
    with pytest.raises(ValueError):
        desktop(**kw)


def test_desktop_identity_without_argv_needs_no_run_config():
    assert desktop(argv=(), run_config="").run_config == ""


def test_public_projections_carry_counts_references_and_key_names_never_values():
    spec = desktop()
    stable, ctx = spec.stable_payload(), execspec.launch_context(spec)
    blob = repr(stable) + repr(ctx) + repr(spec)
    assert "s3cret" not in blob and "v-secret" not in blob
    assert stable["owner"] == "desktop" and stable["argv"] == []
    assert (stable["argv_count"], stable["run_config"]) == (2, "rc_1")
    assert (ctx["owner"], ctx["env_keys"], ctx["stdin"]) == ("desktop", ["K"], "null")
    assert (ctx["cwd_origin"], ctx["write_mode"]) == ("desktop.cwd", "unrestricted")
    assert ctx["env_source"] == "sidecar_inherited" and ctx["argv"] == []
    # 身份随进程所有权而不同：同一份脚本，terminal 与 desktop 不共用 fingerprint
    terminal = execspec.native_spec(SCRIPT, interpreter=PY, cwd=CWD, project_root=ROOT)
    assert terminal.stable_payload() != desktop(argv=(), run_config="").stable_payload()


def test_payload_roundtrip_keeps_the_ownership_fields():
    for spec in (
        desktop(),
        execspec.native_spec(SCRIPT, interpreter=PY, cwd=CWD, project_root=ROOT),
    ):
        again = execspec.spec_from_payload(spec.to_payload())
        assert again == spec and again.owner == spec.owner


def test_bridge_argv_is_shared_by_both_owners():
    """同一个 `bridge_argv`：桌面拥有的不另拼一份命令行。"""
    terminal = execspec.native_spec(
        SCRIPT, interpreter=PY, cwd=CWD, project_root=ROOT, argv=("--x",)
    )
    owned = desktop(argv=("--x",), run_config="rc_1", env=None)
    kw = {"runner_py": "/r.py", "out_dir": "/o", "control_host": "127.0.0.1", "control_port": 5}
    t_argv, o_argv = execspec.bridge_argv(terminal, **kw), execspec.bridge_argv(owned, **kw)
    assert "--run-config" not in t_argv  # terminal 的命令行逐字节不变
    # 桌面拥有的只多一个不透明引用，其余同一份
    i = o_argv.index("--run-config")
    assert o_argv[i : i + 2] == ["--run-config", "rc_1"]
    assert o_argv[:i] + o_argv[i + 2 :] == t_argv
    # 不带配置的桌面启动与 terminal 相同
    plain = desktop(argv=(), run_config="", env=None)
    plain_t = execspec.native_spec(SCRIPT, interpreter=PY, cwd=CWD, project_root=ROOT)
    assert execspec.bridge_argv(plain, **kw) == execspec.bridge_argv(plain_t, **kw)


# ---------------------------------------------------------------------------
# 准备计划：执行语义是显式的一维，不是 safe 的开关
# ---------------------------------------------------------------------------
@nativekit.needs_user_python
def test_plan_for_states_the_execution_semantics_it_will_use(tmp_path):
    root = tmp_path / "proj"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "fig.py").write_text("print('x')\n", encoding="utf-8")

    def plan(**kw):
        return preparation.plan_for(
            project_id="p",
            project_root=str(root),
            asset_id="",
            stem="",
            script="scripts/fig.py",
            entry="__main__",
            original_artifact=None,
            target=preparation.TARGET_SCRIPT,
            **kw,
        )

    safe = plan().launch_context
    assert safe["profile"] == "safe" and safe["write_mode"] == "sandboxed"
    assert "owner" not in safe  # 旧调用形状逐字节不变

    native = plan(execution=execspec.PROFILE_NATIVE).launch_context
    assert native["profile"] == "native" and native["owner"] == "desktop"
    assert native["write_mode"] == "unrestricted" and native["cwd_origin"] == "desktop.cwd"
    assert native["passthrough_savefig"] is True and native["entry"] is None
    # 没有沙盒这一档：默认 cwd 是脚本所在目录（用户自己的 Python，像终端里那样跑）
    assert Path(plan(execution=execspec.PROFILE_NATIVE).interpreter).is_absolute()

    with pytest.raises(ValueError, match="execution"):
        plan(execution="bogus")


def test_desktop_cwd_has_no_sandbox_tier():
    root = os.path.abspath("/tmp/p06-root")
    assert execspec.desktop_cwd(root, "a/b/fig.py", execspec.CWD_SANDBOX) == os.path.join(
        root, "a", "b"
    )
    assert execspec.desktop_cwd(root, "a/b/fig.py", execspec.CWD_PROJECT) == os.path.join(
        root, "a", "b"
    )
    assert execspec.desktop_cwd(root, "a/b/fig.py", execspec.CWD_PROJECT_ROOT) == root


# ---------------------------------------------------------------------------
# Codex P1 ×2：计划选定的执行语义不得悄悄回落成 safe
# ---------------------------------------------------------------------------
def _native_plan(tmp_path):
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    (root / "fig.py").write_text("print('x')\n", encoding="utf-8")
    return preparation.plan_for(
        project_id="p",
        project_root=str(root),
        asset_id="",
        stem="",
        script="fig.py",
        entry="__main__",
        original_artifact=None,
        target=preparation.TARGET_SCRIPT,
        execution=execspec.PROFILE_NATIVE,
    )


@nativekit.needs_user_python
def test_replanning_in_place_keeps_the_chosen_execution(tmp_path):
    from types import SimpleNamespace

    plan = _native_plan(tmp_path)
    assert plan.execution == execspec.PROFILE_NATIVE
    entry = SimpleNamespace(plan=plan)
    fresh = preparation.PreparationService._replan_in_place(entry, plan)
    assert fresh is not None
    assert fresh.execution == execspec.PROFILE_NATIVE
    assert fresh.launch_context["owner"] == "desktop", "环境重算把 native 计划改回了 safe"


@nativekit.needs_user_python
def test_a_warm_safe_worker_is_never_reused_for_a_native_plan(tmp_path):
    from types import SimpleNamespace

    plan = _native_plan(tmp_path)
    safe_spec = execspec.safe_spec(
        "fig.py", plan.project_root, "__main__", interpreter=PY, sandbox="/sb"
    )
    warm = SimpleNamespace(
        built=True,
        spec=safe_spec,
        entry="__main__",
        python=plan.interpreter,
        last_build_descriptors=[],
        last_build_runtime=None,
    )
    assert preparation.PreparationService._reusable(warm, plan) is None
    # 正对照：同一个 worker 对 safe 计划仍可复用
    safe_plan = preparation.plan_for(
        project_id="p",
        project_root=plan.project_root,
        asset_id="",
        stem="",
        script="fig.py",
        entry="__main__",
        original_artifact=None,
        target=preparation.TARGET_SCRIPT,
    )
    assert safe_plan.execution == execspec.PROFILE_SAFE
    assert preparation.PreparationService._reusable(warm, safe_plan) is not None


# ---------------------------------------------------------------------------
# Codex r4254738580：冻结的 spec 里不留调用方还拿得到的可变对象
# ---------------------------------------------------------------------------
def test_spec_copies_env_so_later_caller_mutation_cannot_change_it_or_the_receipt():
    env = {"A": "1"}
    argv = ["--x"]
    spec = desktop(env=env, argv=tuple(argv))
    before = (spec.env_keys if hasattr(spec, "env_keys") else None, execspec.launch_context(spec))
    env["LEAK"] = "2"
    env["A"] = "changed"
    assert dict(spec.env) == {"A": "1"}, "调用方事后改 env，冻结的 spec 跟着变了"
    assert execspec.launch_context(spec)["env_keys"] == ["A"]
    assert before[1] == execspec.launch_context(spec)
    with pytest.raises(TypeError):
        spec.env["B"] = "x"  # spec 自己的映射也不可原地改
    with pytest.raises(TypeError):
        spec.env.update(B="x")
    # 仍然是 dict 的行为：序列化 / 展开 / 往返相等 / deepcopy 都照常
    import copy
    import json

    assert json.dumps(spec.env) == '{"A": "1"}' and {**spec.env} == {"A": "1"}
    assert copy.deepcopy(spec) == spec
    assert execspec.spec_from_payload(spec.to_payload()) == spec


def test_spec_holds_no_mutable_container_the_caller_can_still_reach():
    rule = {"kind": "prefix", "from": "", "to": os.path.abspath("/data")}
    rules = [rule]
    safe = execspec.safe_spec(
        "fig.py", ROOT, "__main__", interpreter=PY, sandbox="/sb", input_remap=rules
    )
    rule["to"] = os.path.abspath("/other")
    assert safe.input_remap[0]["to"] == os.path.abspath("/data")
    with pytest.raises(TypeError):
        safe.input_remap[0]["to"] = "x"
    assert isinstance(desktop().argv, tuple) and all(isinstance(a, str) for a in desktop().argv)
    caller_argv = ["--a"]
    assert desktop(argv=caller_argv).argv == ("--a",)  # 构造函数复制成元组：事后改列表无关
    caller_argv.append("--b")
    assert desktop(argv=tuple(caller_argv)).argv == ("--a", "--b")


# ---------------------------------------------------------------------------
# Codex r4254876583：执行语义进会话 key 与语义指纹（safe 的两者保持不变）
# ---------------------------------------------------------------------------
def test_session_key_and_fingerprint_separate_safe_from_native_but_keep_safe_unchanged(tmp_path):
    from tavotto.engine import prepsession

    safe_t = {"kind": "script", "script": "fig.py", "entry": "__main__"}
    native_t = {**safe_t, "execution": execspec.PROFILE_NATIVE}
    assert prepsession.target_key(safe_t) == "script:fig.py"  # 与 main 逐字节相同
    assert prepsession.target_key({**safe_t, "execution": "safe"}) == "script:fig.py"
    assert prepsession.target_key(native_t) != prepsession.target_key(safe_t)
    assert prepsession.target_key(native_t) == "native|script:fig.py"

    plan_native = _native_plan(tmp_path)
    plan_safe = preparation.plan_for(
        project_id="p",
        project_root=plan_native.project_root,
        asset_id="",
        stem="",
        script="fig.py",
        entry="__main__",
        original_artifact=None,
        target=preparation.TARGET_SCRIPT,
    )
    assert '"execution"' not in prepsession._fingerprint(plan_safe)  # noqa: SLF001 — safe 指纹不变
    assert prepsession._fingerprint(plan_native) != prepsession._fingerprint(plan_safe)  # noqa: SLF001


@nativekit.needs_user_python
def test_a_native_check_never_reuses_the_safe_session_for_the_same_script(tmp_path):
    from tavotto.engine import prepsession

    plan = _native_plan(tmp_path)
    service = prepsession.SessionService()
    base = {"kind": "script", "script": "fig.py", "entry": "__main__"}
    safe_sess, _ = service.check(project_id="p", project_root=plan.project_root, target=base)
    native_sess, created = service.check(
        project_id="p",
        project_root=plan.project_root,
        target={**base, "execution": execspec.PROFILE_NATIVE},
    )
    assert created and native_sess is not safe_sess
    assert safe_sess.plan.execution == execspec.PROFILE_SAFE
    assert native_sess.plan.execution == execspec.PROFILE_NATIVE
    assert native_sess.plan.launch_context["owner"] == "desktop"


def test_asset_session_key_also_separates_safe_from_native():
    from tavotto.engine import prepsession

    safe_t = {"kind": "asset", "asset_id": "Fig1.pdf", "stem": "Fig1", "script": "fig.py"}
    native_t = {**safe_t, "execution": execspec.PROFILE_NATIVE}
    assert prepsession.target_key(safe_t) == "asset:Fig1.pdf"  # 与 main 逐字节相同
    assert prepsession.target_key(native_t) == "native|asset:Fig1.pdf"
    # 带 argv 引用的 script 目标：后缀在引用之后
    run = execspec.RunSelection(config_id="rc_1", argv=("--x",))
    script_t = {"kind": "script", "script": "fig.py", "run": run}
    assert prepsession.target_key(script_t) == "script:fig.py~rc_1"
    assert (
        prepsession.target_key({**script_t, "execution": "native"}) == "native|script:fig.py~rc_1"
    )


@nativekit.needs_user_python
def test_a_native_asset_check_never_reuses_the_safe_asset_session(tmp_path):
    from tavotto.engine import prepsession

    plan = _native_plan(tmp_path)
    service = prepsession.SessionService()
    base = {
        "kind": "asset",
        "asset_id": "Fig1.pdf",
        "stem": "Fig1",
        "script": "fig.py",
        "entry": "__main__",
        "original_artifact": None,
    }
    safe_sess, _ = service.check(project_id="p", project_root=plan.project_root, target=base)
    native_sess, created = service.check(
        project_id="p",
        project_root=plan.project_root,
        target={**base, "execution": execspec.PROFILE_NATIVE},
    )
    assert created and native_sess is not safe_sess
    assert safe_sess.plan.execution == execspec.PROFILE_SAFE
    assert native_sess.plan.execution == execspec.PROFILE_NATIVE


@nativekit.needs_user_python
def test_the_execution_dimension_is_injective_even_for_ids_that_end_in_the_old_suffix(tmp_path):
    """asset id 不透明：safe 的 `...@native` 与 native 的 `...` 曾得到同一个 key。"""
    from tavotto.engine import prepsession

    safe_t = {
        "kind": "asset",
        "asset_id": "runtime:s.py#plot@native",
        "stem": "plot",
        "script": "fig.py",
    }
    native_t = {**safe_t, "asset_id": "runtime:s.py#plot", "execution": execspec.PROFILE_NATIVE}
    assert prepsession.target_key(safe_t) != prepsession.target_key(native_t)
    assert prepsession.target_key(safe_t) == "asset:runtime:s.py#plot@native"  # safe 与 main 一致

    plan = _native_plan(tmp_path)
    service = prepsession.SessionService()
    base = {"entry": "__main__", "original_artifact": None}
    s1, _ = service.check(project_id="p", project_root=plan.project_root, target={**safe_t, **base})
    s2, created = service.check(
        project_id="p", project_root=plan.project_root, target={**native_t, **base}
    )
    assert created and s1 is not s2
    assert s1.plan.execution == "safe" and s2.plan.execution == "native"


def test_receipt_id_is_per_launch_instance_and_unchanged_without_one():
    from tavotto.engine import receipt

    def make(instance=""):
        return receipt.ExecutionReceipt(
            profile="native",
            control_plane=receipt.CONTROL_PLANE_NATIVE,
            python_source="",
            generation=1,
            source_revision="",
            spec_stable=desktop().stable_payload(),
            launch_context=execspec.launch_context(desktop()),
            interpreter=PY,
            project_root=ROOT,
            runtime=None,
            **({"instance": instance} if instance else {}),
        )

    a, b, c = make("native-owned-x-1"), make("native-owned-x-2"), make()
    assert a.receipt_id != b.receipt_id, "两次参数相同的启动得到了同一个 receipt_id"
    assert a.to_payload(include_private=True)["instance"] == "native-owned-x-1"
    assert "instance" not in a.to_payload() and "instance" not in c.to_payload(include_private=True)
    # 没有实例键的回执（safe / terminal native）与此前的算法逐字节相同
    legacy = receipt._sha(  # noqa: SLF001
        {
            "public": c.public_identity(),
            "private": c.private_invalidation_key(),
            "generation": c.generation,
        }
    )
    assert c.receipt_id == legacy
