"""Import Origin 的来源 / 覆盖度 / 核验字段与诊断：**只出 reason code、计数、类别，不出模块名 / 包名 / 路径 / 源码 / 环境变量**。

新字段落在哪些载荷里（PR1–PR6）：

* 联合计划 `origins[]`（`import_name` / `distribution` / `suggested_distribution` / `candidates` / `coverage`）与
  `user_specified[]`、影响摘要 `impact.user_specified[]`——都带着用户脚本里的 import 名或用户敲的包名；
* 准备会话检查项 `dependencies.detail.origins`（计数与闭集原因码，给卡片用）；
* 联合安装终态 `dist_reasons`（装后核验的闭集原因码）。

这些**不许**从任何诊断出口原样出去。出口一共四个：依赖任务的白名单投影（`deprepair.diagnostic_projection`）、
准备任务的白名单投影（`preparation.diagnostic_projection`，经任务诊断端点与全局诊断包）、缺依赖现场
（`deprepair.note_missing_dependency`）、全局诊断包。四个出口今天都是「逐字段挑」——**本文件钉住的是这个事实，
以及新字段进不去**：

1. 金丝雀：把带标记的名字 / 路径塞进每一个新字段的位置，出口全文搜索（原样、小写、hex、base64、url 编码、常见摘要）找不到；
2. 形状：投影出来的键是一个写死的闭集，新字段名一个都不在里面——哪天有人想把它们放进诊断，这里先红，
   迫使他回来读这一段并决定出什么形状（只许闭集码与计数）；
3. 结构：投影函数的源码里没有这些键名（AST，不是子串）。

反证见 PR 正文：在投影里加一行 `"origins": rec.get("origins")`，上面三类各自变红。
"""

# ruff: noqa: F811 — 夹具从兄弟测试文件导入复用，参数名与导入名相同
from __future__ import annotations

import ast
import inspect
import json
import textwrap

import pytest

from tavotto.engine import depplan, deprepair, prepsession
from test_preparation_api import (  # noqa: F401
    _open,
    _project,
    client as prep_client,
    fake_pool,
)

# 夹具与助手沿用任务诊断那一组（同一套假 pool / 会话 / 导出包；搬进来是 import，不是复制）
from test_task_diagnostics import (  # noqa: F401
    _bundle_files,
    _get,
    _run_session,
    assert_no_secret,
    run_aware_pool,
    sessions,
)

#: 带标记的名字：像用户课题里会有的 import 名 / 包名 / 路径片段。不是 curated、不是标准库、不是闭集成员。
IMPORT = "zq9patient7731lab"
DIST = "zq9-patient7731-lab-tools"
PATH = "/Users/zq9patient7731/课题/secret_site"
SPECIFIED = "zq9-patient7731-lab-tools>=2.1"
ALL_CANARIES = (IMPORT, DIST, PATH, "zq9patient7731")


def _rich_impact() -> dict:
    return deprepair.impact_of(
        target_kind="tavotto_managed",
        requirements=(SPECIFIED, "numpy"),
        constraints=(f"{DIST}<3",),
        require_hashes=False,
        adapter=(),
        groups=(),
        creates_environment=True,
        private_python=None,
        env_fingerprint="x",
        user_specified=(
            {"import_name": IMPORT, "requirement": SPECIFIED},
            {"import_name": IMPORT + "2", "requirement": DIST + "2"},
        ),
    )


def _rich_record(state: str = "failed") -> dict:
    """一条带着**每一个**新字段的联合安装终态——比真实的终态更"满"，用来探出口。"""
    impact = _rich_impact()
    return {
        "plan_id": "dp-privacy-1",
        "state": state,
        "code": deprepair.ERROR_IMPORT_STILL_FAILED if state == "failed" else "",
        "target_kind": "tavotto_managed",
        "flow": deprepair.FLOW_JOINT,
        "script": "scripts/zq9patient7731_figure.py",
        "import_name": IMPORT,
        "distribution": DIST,
        "impact": impact,
        "impact_digest": deprepair.impact_digest(impact),
        "error": f"装后核对：{IMPORT}: distribution_not_installed（{PATH}）",
        "dist_reasons": [deprepair.DIST_NOT_INSTALLED],
        "imports": {IMPORT: deprepair.DIST_NOT_INSTALLED},
        "origins": [
            {
                "import_name": IMPORT,
                "suggested_distribution": DIST,
                "candidates": [DIST, DIST + "-headless"],
                "coverage": {"detail": [{"code": "module_not_found_in_user_environment"}]},
            }
        ],
        "user_specified": [{"import_name": IMPORT, "requirement": SPECIFIED}],
        "result": {
            "activated": False,
            "health_ok": False,
            "python": PATH,
            "installed": {DIST: "2.1"},
        },
        "pypi_source": "default",
        "pypi_mirror": "",
        "retryable": True,
        "stage": "verifying",
    }


def _walk_items(obj, out: dict | None = None) -> dict:
    out = {} if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out[k] = v
            _walk_items(v, out)
    return out


def _walk_keys(obj, out: set[str]) -> set[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            _walk_keys(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _walk_keys(v, out)
    return out


# ---------------------------------------------------------------- 出口 1：依赖任务的白名单投影
class TestDependencyTaskProjection:
    @pytest.mark.parametrize("state", ["failed", "done"])
    def test_canaries_in_every_new_field_never_reach_the_snapshot(self, state):
        snap = deprepair.diagnostic_projection(_rich_record(state))
        text = json.dumps(snap, ensure_ascii=False)
        for canary in ALL_CANARIES:
            assert_no_secret(text, canary)
        # 空转反证：投影确实有内容（只有个数）
        assert snap["impact"]["installs"] == 2 and snap["impact"]["constraints"] == 1

    def test_the_projection_has_exactly_the_documented_keys_and_none_of_the_new_ones(self):
        snap = deprepair.diagnostic_projection(_rich_record())
        keys = _walk_keys(snap, set())
        # 写死的闭集（值缺席时键会缺席，所以是 ⊆）：今天的投影最多长这样。新增任何一个键都要回来改这里，并说明它为什么不泄漏
        assert keys <= {
            "snapshot_version",
            "kind",
            "attempt_id",
            "outcome",
            "target",
            "kind",
            "scope",
            "flow",
            "impact",
            "version",
            "installs",
            "constraints",
            "require_hashes",
            "creates_environment",
            "modifies_user_environment",
            "private_python_download",
            "private_python_bytes",
            "rollback",
            "digest",
            "error",
            "code",
            "stage",
            "last",
            "committed",
            "result",
            "activated",
            "health_ok",
            "package_source",
            "source",
            "mirror_used",
            "retryable",
        }
        # `installs` / `constraints` 只是个数；`error` 只有 `code`（闭集）
        assert isinstance(snap["impact"]["installs"], int)
        assert isinstance(snap["impact"]["constraints"], int)
        assert set(snap["error"]) == {"code"}
        # 新字段名一个都不在
        assert not (
            keys
            & {
                "origins",
                "user_specified",
                "suggested_distribution",
                "candidates",
                "dist_reasons",
                "coverage",
                "import_name",
                "distribution",
                "requirement",
                "imports",
            }
        )

    def test_the_one_value_derived_from_package_names_is_the_impact_digest_and_that_is_issue_918(
        self,
    ):
        """**已知差距，钉着现状**：投影里唯一一个由包名算出来的值是 `impact.digest`（影响摘要的无密钥 sha256；
        `IMPACT_VERSION` 2 起摘要含用户指定的包名）。名字不以明文出门，但对常见包名集合可以离线复算——与 #918 第 1 条同形，
        该 issue 仍开着（修法：诊断导出里改成带本机密钥的摘要或不导出）。本用例不声称它安全，只钉住「它是唯一一个」：
        哪天有人再往投影里加一个由名字派生的值，这里先红；哪天 #918 修了（摘要不再可复算），把这条改成断言新形状。"""
        a = deprepair.diagnostic_projection(_rich_record())
        other = _rich_record()
        other["impact"] = deprepair.impact_of(
            target_kind="tavotto_managed",
            requirements=("some-other-package",),
            constraints=(),
            require_hashes=False,
            adapter=(),
            groups=(),
            creates_environment=True,
            private_python=None,
            env_fingerprint="x",
            user_specified=({"import_name": IMPORT, "requirement": "some-other-package"},),
        )
        other["impact_digest"] = deprepair.impact_digest(other["impact"])
        b = deprepair.diagnostic_projection(other)
        assert a["impact"]["digest"] != b["impact"]["digest"]  # 它确实随包名变
        hexish = {
            k
            for k, v in _walk_items(a).items()
            if isinstance(v, str) and len(v) >= 32 and all(c in "0123456789abcdef" for c in v)
        }
        assert hexish == {"digest"}

    def test_the_error_code_is_a_published_code_or_absent(self):
        rec = _rich_record()
        rec["code"] = f"custom_{IMPORT}"  # 不在已发布闭集里 → 丢掉，不哈希
        snap = deprepair.diagnostic_projection(rec)
        assert "error" not in snap or "code" not in snap["error"]
        assert_no_secret(json.dumps(snap), IMPORT)

    def test_the_projection_source_never_names_the_new_fields(self):
        """结构：投影函数里 `rec.get(...)` / 下标的键名只许是下面这些（AST，不是子串）。"""
        tree = ast.parse(textwrap.dedent(inspect.getsource(deprepair.diagnostic_projection)))
        read: set[str] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                read.add(node.args[0].value)
        assert read == {
            "impact",
            "private_python",
            "result",
            "plan_id",
            "state",
            "target_kind",
            "scope",
            "flow",
            "impact_version",
            "installs",
            "constraints",
            "require_hashes",
            "creates_environment",
            "modifies_user_environment",
            "download_bytes",
            "rollback",
            "impact_digest",
            "code",
            "stage",
            "committed",
            "activated",
            "health_ok",
            "pypi_source",
            "pypi_mirror",
            "retryable",
        }
        assert not (read & {"origins", "user_specified", "dist_reasons", "imports", "coverage"})

    def test_dist_reasons_are_a_closed_code_set_that_the_snapshot_does_not_carry_today(self):
        """装后核验的闭集原因码（`dist_reasons`）进度记录里有、任务诊断里没有：这是**今天的事实**，不是疏漏——
        要不要让维护者在诊断里看到「为什么核验没过」是 PR 正文里留给维护者的决定（出的话只能是这个闭集的码，不能带名字）。"""
        snap = deprepair.diagnostic_projection(_rich_record())
        blob = json.dumps(snap)
        assert "dist_reasons" not in blob
        for code in deprepair.DIST_REASONS:
            assert code not in blob


# ---------------------------------------------------------------- 出口 3：缺依赖现场
class TestMissingDependencyEvidence:
    def test_a_rich_offer_leaves_no_origin_or_candidate_in_the_evidence(self, tmp_path):
        deprepair.clear_missing_dependency_evidence()
        offer = {
            "code": "dependency_preparation_required",
            "requirement": {
                "resolution_source": f"source_{IMPORT}",  # 不在 INSTALLABLE_SOURCES 里 → 哈希
                "installable": False,
                "distribution": DIST,
                "import_name": IMPORT,
            },
            "plan": {
                "origins": [
                    {"import_name": IMPORT, "suggested_distribution": DIST, "candidates": [DIST]}
                ],
                "user_specified": [{"import_name": IMPORT, "requirement": SPECIFIED}],
            },
            "user_environments": [{"id": "ue-1", "python": PATH + "/bin/python"}],
            "targets": [
                {
                    "kind": "tavotto_managed",
                    "available": False,
                    "reason": f"reason_{IMPORT}",
                    "python": PATH,
                    "modifies_user_environment": False,
                    "creates_environment": True,
                }
            ],
        }
        deprepair.note_missing_dependency(
            tmp_path,
            script="s.py",
            module="numpy",
            python_source="bundled",
            project_env=None,
            offer=offer,
        )
        (record,) = deprepair.recent_missing_dependencies(tmp_path)
        text = json.dumps(record, ensure_ascii=False)
        for canary in ALL_CANARIES:
            assert_no_secret(text, canary)
        keys = _walk_keys(record, set())
        assert not (
            keys & {"origins", "user_specified", "suggested_distribution", "candidates", "plan"}
        )
        # 反证：记录不是空的——不在闭集里的值被哈希而不是丢掉
        assert record["offer"]["resolution_source"].startswith("str:")
        assert record["offer"]["targets"][0]["reason"].startswith("str:")
        deprepair.clear_missing_dependency_evidence()


# ---------------------------------------------------------------- 会话检查项的来源事实：只有计数与闭集原因码
class TestOriginFactsAreCountsAndClosedCodes:
    def _rows(self):
        reasons = sorted(depplan.ORIGIN_REASONS)
        rows = [
            {
                "import_name": IMPORT,
                "distribution": DIST,
                "suggested_distribution": DIST,
                "reason": reasons[0],
                "candidates": [DIST],
                "coverage": {"path": PATH},
            },
            {"import_name": IMPORT + "b", "reason": reasons[-1], "path": PATH},
            # 不在闭集里的原因码：丢掉，不原样回显（回显就是把任意字符串送进了检查项）
            {"import_name": IMPORT + "c", "reason": f"reason_{IMPORT}"},
        ]
        return rows

    def test_only_counts_and_closed_reason_codes_come_out(self):
        facts = prepsession.origin_facts({"plan": {"origins": self._rows()}})
        text = json.dumps(facts, ensure_ascii=False)
        for canary in ALL_CANARIES:
            assert_no_secret(text, canary)
        origins = facts["origins"]
        assert set(origins) <= {"count", "by_reason", "confirmable"}
        assert isinstance(origins["count"], int) and origins["count"] == 2
        assert set(origins["by_reason"]) <= set(depplan.ORIGIN_REASONS)
        assert all(isinstance(n, int) for n in origins["by_reason"].values())
        assert origins["confirmable"] == 1

    def test_nothing_unknown_means_no_key_at_all(self):
        assert prepsession.origin_facts({"plan": {"origins": []}}) == {}
        assert prepsession.origin_facts(None) == {}


# ---------------------------------------------------------------- 出口 2 + 4：真走一遍会话，任务诊断与全局诊断包
def _rich_offer() -> dict:
    reasons = sorted(depplan.ORIGIN_REASONS)
    return {
        "plan": {
            "status": "nothing_needed",
            "origins": [
                {
                    "import_name": IMPORT,
                    "distribution": DIST,
                    "suggested_distribution": DIST,
                    "reason": reasons[0],
                    "candidates": [DIST],
                    "coverage": {
                        "detail": [{"code": "module_not_found_in_user_environment"}],
                        "path": PATH,
                    },
                },
                {"import_name": IMPORT + "b", "reason": reasons[-1]},
            ],
            "user_specified": [{"import_name": IMPORT, "requirement": SPECIFIED}],
        },
        "user_environments": [{"id": "ue-1", "python": PATH + "/bin/python", "label": DIST}],
    }


def test_a_session_whose_plan_carries_origins_exports_no_name_through_the_task_snapshot_or_the_bundle(
    prep_client,
    tmp_path,
    run_aware_pool,
    sessions,
    monkeypatch,
):
    client = prep_client
    monkeypatch.setattr(deprepair, "preparation_offer", lambda root, script: _rich_offer())
    root = _project(tmp_path, "p")
    (root / "tavotto_registry.json").unlink()
    (root / "fig.pdf").unlink()
    pj = _open(client, root)
    report = _run_session(client, pj, {"script": "fig.py"})
    ref = report["provider"]["attempt_id"]

    # 检查项里的来源事实：只有计数与闭集原因码（卡片用；名字在计划载荷里，不在检查项里）
    dep = next(c for c in report["checks"] if c["id"] == "dependencies")
    assert set(dep["detail"]["origins"]) <= {"count", "by_reason", "confirmable"}
    assert dep["detail"]["origins"]["count"] == 2 and dep["detail"]["origins"]["confirmable"] == 1
    assert not (
        _walk_keys(dep["detail"], set()) & {"import_name", "distribution", "candidates", "coverage"}
    )

    res = _get(client, "preparation", ref, pj)
    assert res.status_code == 200, res.get_json()
    text = res.get_data(as_text=True)
    for canary in ALL_CANARIES:
        assert_no_secret(text, canary)
    snap = res.get_json()["snapshot"]
    assert not (
        _walk_keys(snap, set())
        & {"origins", "user_specified", "suggested_distribution", "candidates"}
    )
    # 空转反证：快照不是空壳，依赖那一段确实被投影了（只有布尔与个数）
    assert snap["config"]["dependency_preparation"] is True

    files = _bundle_files(client, pj)
    assert files, "诊断包是空的"
    for name, data in files.items():
        blob = data.decode("utf-8", errors="replace")
        for canary in ALL_CANARIES:
            assert_no_secret(blob, canary)


def test_the_preparation_projection_reads_only_a_fixed_set_of_dependency_keys():
    """结构：`preparation.diagnostic_projection` 对 `dependency_preparation`（`dep`）只读 `blocked`；对计划只读个数与布尔。"""
    from tavotto.engine import preparation

    tree = ast.parse(textwrap.dedent(inspect.getsource(preparation.diagnostic_projection)))
    dep_reads = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "dep"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert dep_reads == {"blocked"}
    source = inspect.getsource(preparation.diagnostic_projection)
    for name in ("origins", "user_specified", "suggested_distribution", "candidates", "coverage"):
        assert name not in source, name
