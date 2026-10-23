"""智能修复路由（ADR 0148）：失败类型 -> 唯一修法。纯函数，闭集，顺序即优先级，一次运行最多 3 种。"""

from __future__ import annotations

import pytest

from tavotto.engine import fixrouter
from tavotto.engine.fixrouter import Ctx, choose


def _hist(*changed: tuple[str, ...], run: int = 1) -> tuple[dict, ...]:
    return tuple({"run": run, "seq": i + 1, "changed": list(c)} for i, c in enumerate(changed))


def test_safe_guard_block_is_switch_native_only_when_native_is_allowed():
    assert choose("safe_file_operation_blocked", Ctx(native_allowed=True)) == {
        "kind": "switch_native",
        "silent": True,
    }
    # 不允许 native（只用安全运行 / 运维关了回退）：不标注，落到后面的 AI 修法
    assert choose("safe_file_operation_blocked", Ctx(native_allowed=False))["kind"] == "connect_ai"


def test_missing_dependency_installs_the_named_packages():
    ctx = Ctx(missing_installable=True, packages=("scipy",), auto_install_allowed=True)
    assert choose("missing_dependency", ctx) == {
        "kind": "install_packages",
        "packages": ["scipy"],
        "ask": False,
    }


def test_listed_package_in_an_allowed_environment_is_not_asked_but_everything_else_is():
    def ask(packages, allowed):
        ctx = Ctx(missing_installable=True, packages=packages, auto_install_allowed=allowed)
        return choose("missing_dependency", ctx)["ask"]

    assert ask(("scipy",), True) is False  # 名单内 + 环境允许 -> 不问
    assert ask(("scipy",), False) is True  # 名单内 + 环境不允许（第一次要问）-> 问
    assert ask(("totally-unlisted-pkg",), True) is True  # 名单外 -> 仍然点名确认
    assert ask(("scipy", "totally-unlisted-pkg"), True) is True  # 多个里有一个在名单外 -> 问


def test_missing_dependency_that_cannot_be_installed_falls_through_to_ai():
    assert choose("missing_dependency", Ctx(missing_installable=False, ai_connected=True)) == {
        "kind": "ai_fix"
    }
    # 可装但认不出包名：不拿 import 名去装
    assert choose("missing_dependency", Ctx(missing_installable=True, packages=()))["kind"] == (
        "connect_ai"
    )


@pytest.mark.parametrize("code", sorted(fixrouter.PYTHON_VERSION_CODES))
def test_python_version_failures_route_to_change_python(code):
    got = choose(code, Ctx(python_from="3.9.1", python_to="3.12"))
    assert got == {"kind": "change_python", "from": "3.9.1", "to": "3.12"}
    assert choose(code, Ctx(python_from="3.9.1"))["to"] is None


def test_python_version_codes_include_the_known_stable_codes():
    assert {
        "project_env_unsupported_python",
        "root_python_mismatch",
    } <= fixrouter.PYTHON_VERSION_CODES


def test_raster_script_and_foreign_sources_convert():
    raster = {"kind": "raster_script", "library": "pillow"}
    assert choose("no_figures_captured", Ctx(no_figure_hint=raster)) == {
        "kind": "convert_to_matplotlib",
        "source": "python",
    }
    assert choose("anything", Ctx(source_language="r")) == {
        "kind": "convert_to_matplotlib",
        "source": "r",
    }
    assert choose("anything", Ctx(source_language="matlab"))["source"] == "matlab"
    # python 来源且没有位图提示：不转写
    assert choose("anything", Ctx(source_language="python"))["kind"] == "connect_ai"


def test_everything_else_is_ai_fix_when_connected_and_connect_ai_when_not():
    assert choose("script_error", Ctx(ai_connected=True)) == {"kind": "ai_fix"}
    assert choose("script_error", Ctx(ai_connected=False)) == {"kind": "connect_ai"}
    assert choose("", Ctx()) == {"kind": "connect_ai"}


def test_ai_rounds_exhausted_is_feedback():
    assert choose("script_error", Ctx(ai_connected=True, ai_rounds_exhausted=True)) == {
        "kind": "feedback",
        "tried": [],
    }


def test_priority_order_first_matching_rule_wins():
    everything = dict(
        native_allowed=True,
        missing_installable=True,
        packages=("scipy",),
        no_figure_hint={"kind": "raster_script"},
        source_language="r",
        ai_connected=True,
    )
    # 同一份事实、不同失败码：每个码只被它自己的规则接走
    assert choose("safe_file_operation_blocked", Ctx(**everything))["kind"] == "switch_native"
    assert choose("missing_dependency", Ctx(**everything))["kind"] == "install_packages"
    assert choose("project_env_unsupported_python", Ctx(**everything))["kind"] == "change_python"
    # 转写在 AI 之前
    assert choose("script_error", Ctx(**everything))["kind"] == "convert_to_matplotlib"


def test_a_remedy_is_not_repeated_within_one_run():
    # 本次运行里已经 safe -> native 过：同一个码不再标 switch_native
    ctx = Ctx(native_allowed=True, history=_hist((), ("profile",)), ai_connected=True)
    assert fixrouter.tried_in_run(ctx) == ("switch_native",)
    assert choose("safe_file_operation_blocked", ctx) == {"kind": "ai_fix"}
    # 装过包之后又缺：不再装第二遍
    ctx2 = Ctx(
        missing_installable=True, packages=("scipy",), installed_this_run=True, ai_connected=True
    )
    assert choose("missing_dependency", ctx2) == {"kind": "ai_fix"}


def test_fourth_remedy_in_one_run_is_feedback():
    ctx = Ctx(
        history=_hist((), ("profile",), ("interpreter",)),
        installed_this_run=True,
        ai_connected=True,
        missing_installable=True,
        packages=("scipy",),
    )
    tried = ["install_packages", "switch_native", "change_python"]
    assert list(fixrouter.tried_in_run(ctx)) == tried
    assert choose("missing_dependency", ctx) == {"kind": "feedback", "tried": tried}
    # 两种修法还没到上限
    two = Ctx(history=_hist((), ("profile",)), installed_this_run=True, ai_connected=True)
    assert choose("script_error", two) == {"kind": "ai_fix"}


def test_only_the_current_run_counts_toward_the_cap():
    old = tuple({"run": 1, "seq": i, "changed": ["profile"]} for i in (1, 2, 3))
    new = ({"run": 2, "seq": 1, "changed": []},)
    ctx = Ctx(history=old + new, ai_connected=True)
    assert fixrouter.tried_in_run(ctx) == ()
    assert choose("script_error", ctx) == {"kind": "ai_fix"}


def test_every_remedy_kind_is_reachable_and_in_the_closed_set():
    seen = {
        choose("safe_file_operation_blocked", Ctx(native_allowed=True))["kind"],
        choose("missing_dependency", Ctx(missing_installable=True, packages=("scipy",)))["kind"],
        choose("root_python_mismatch", Ctx())["kind"],
        choose("x", Ctx(source_language="r"))["kind"],
        choose("x", Ctx(ai_connected=True))["kind"],
        choose("x", Ctx())["kind"],
        choose("x", Ctx(ai_rounds_exhausted=True))["kind"],
    }
    assert seen == set(fixrouter.REMEDY_KINDS)


def test_dist_name_reads_the_distribution_from_a_requirement():
    assert fixrouter.dist_name("scipy>=1.2") == "scipy"
    assert fixrouter.dist_name("  scikit-learn[extra]==1") == "scikit-learn"
    assert fixrouter.dist_name("") == ""
