"""Import Origin PR6：前端展示要的两个后端事实（最小后端改动，不新增已发布错误码、不新增 BLOCK_REASONS / ORIGIN_REASONS）。

* `prepsession.origin_facts` 的 `confirmable`：来源未定的 import 里带预填（`suggested_distribution`）的个数——界面据此区分
  「需要确认」（有输入框可给）与「尚无法确定」（只解释），不必为了一个计数再取整份计划。没有就不出这个键（检查项形状不变）。
* 进度终态的 `dist_reasons`：装后「发行包与版本」核验没过时的原因码（`deprepair.DIST_REASONS` 闭集，去重、只有码没有名字）——
  界面据此按原因分别说人话，不解析给人读的 `error` 文案。普通的 import 失败（异常原文）不在闭集里，不出。

看护的是主语：计数只数闭集里的原因、预填只认非空；原因码只来自核验的闭集；前端镜像（`api.ts`）与后端同序同长。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tavotto.engine import depplan, deprepair, prepsession

ROOT = Path(__file__).resolve().parents[1]
API_TS = ROOT / "web" / "src" / "lib" / "api.ts"


def _origins(*rows: dict) -> dict:
    return {"plan": {"status": "nothing_needed", "origins": list(rows)}}


class TestConfirmableCount:
    def test_counts_only_origins_that_carry_a_prefill(self):
        facts = prepsession.origin_facts(
            _origins(
                {
                    "import_name": "lab_tools",
                    "reason": "distribution_unresolved",
                    "suggested_distribution": "lab-tools",
                },
                {
                    "import_name": "peaks",
                    "reason": "distribution_unresolved",
                    "suggested_distribution": "peaks",
                },
                {"import_name": "cv2", "reason": "module_origin_ambiguous"},
            )
        )
        assert facts == {
            "origins": {
                "count": 3,
                "by_reason": {"distribution_unresolved": 2, "module_origin_ambiguous": 1},
                "confirmable": 2,
            }
        }

    def test_the_key_is_absent_when_nothing_is_confirmable(self):
        facts = prepsession.origin_facts(
            _origins(
                {"import_name": "cv2", "reason": "module_origin_ambiguous"},
                {"import_name": "x", "reason": "unverified", "suggested_distribution": ""},
            )
        )
        assert facts == {
            "origins": {"count": 2, "by_reason": {"module_origin_ambiguous": 1, "unverified": 1}}
        }
        assert "confirmable" not in facts["origins"]

    def test_a_reason_outside_the_closed_set_is_not_counted_even_with_a_prefill(self):
        facts = prepsession.origin_facts(
            _origins(
                {"import_name": "a", "reason": "something_new", "suggested_distribution": "a"},
                {"import_name": "b", "reason": "unverified"},
            )
        )
        assert facts == {"origins": {"count": 1, "by_reason": {"unverified": 1}}}

    def test_no_origins_means_the_check_is_exactly_what_it_was(self):
        assert prepsession.origin_facts(_origins()) == {}
        assert prepsession.origin_facts(None) == {}

    def test_it_never_carries_names_or_the_prefill_text(self):
        facts = prepsession.origin_facts(
            _origins(
                {
                    "import_name": "secret_lab_pkg",
                    "reason": "distribution_unresolved",
                    "suggested_distribution": "secret-lab-pkg",
                }
            )
        )
        assert "secret" not in repr(facts)

    def test_it_agrees_with_what_the_real_plan_offers(self, tmp_path):
        """对照真计划：`depplan.plan` 给预填的那个，计数就数它；撞表名没有预填就不数。"""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "fig.py").write_text("import lab_tools_zz\nimport Yaml\n", encoding="utf-8")
        facts_ = depplan.TargetFacts(
            python="/fake/python",
            marker_env={"python_version": "3.12", "sys_platform": "linux"},
            stdlib=frozenset(),
            installed={},
        )
        payload = depplan.plan(
            proj, "fig.py", facts=facts_, target_kind="project_venv"
        ).to_payload()
        suggested = [
            o["import_name"] for o in payload["origins"] if o.get("suggested_distribution")
        ]
        got = prepsession.origin_facts({"plan": payload})
        assert got["origins"].get("confirmable", 0) == len(suggested)
        assert "lab_tools_zz" in suggested  # 对照：表外的纯名字确实带预填


class TestDistReasons:
    def _err(self, code, imports):
        return deprepair.RepairError(code, "x", imports=imports)

    def test_only_closed_set_codes_come_out_deduplicated_in_closed_set_order(self):
        err = self._err(
            deprepair.ERROR_IMPORT_STILL_FAILED,
            {
                "a": deprepair.DIST_VERSION_MISMATCH,
                "b": deprepair.DIST_NOT_INSTALLED,
                "c": deprepair.DIST_VERSION_MISMATCH,
                "d": "ModuleNotFoundError: No module named 'secret_name'",
            },
        )
        assert deprepair.dist_reasons_of(err) == [
            deprepair.DIST_NOT_INSTALLED,
            deprepair.DIST_VERSION_MISMATCH,
        ]

    @pytest.mark.parametrize("reason", deprepair.DIST_REASONS)
    def test_each_closed_set_code_passes(self, reason):
        assert deprepair.dist_reasons_of(
            self._err(deprepair.ERROR_IMPORT_STILL_FAILED, {"x": reason})
        ) == [reason]

    def test_an_ordinary_import_failure_carries_none(self):
        err = self._err(deprepair.ERROR_IMPORT_STILL_FAILED, {"x": "ImportError: boom"})
        assert deprepair.dist_reasons_of(err) == []

    def test_other_error_codes_carry_none_even_if_they_have_the_same_extra(self):
        err = self._err(deprepair.ERROR_FAILED, {"x": deprepair.DIST_NOT_INSTALLED})
        assert deprepair.dist_reasons_of(err) == []

    def test_malformed_extra_is_ignored(self):
        assert (
            deprepair.dist_reasons_of(
                deprepair.RepairError(deprepair.ERROR_IMPORT_STILL_FAILED, "x")
            )
            == []
        )
        assert (
            deprepair.dist_reasons_of(self._err(deprepair.ERROR_IMPORT_STILL_FAILED, ["list"]))
            == []
        )
        assert deprepair.dist_reasons_of(ValueError("not a repair error")) == []


class TestProgressTerminalState:
    @pytest.fixture(autouse=True)
    def _isolate(self):
        yield
        deprepair._progress.pop("plan-ui-1", None)

    def _fail_with(self, monkeypatch, err):
        def boom(*a, **k):
            raise err

        monkeypatch.setattr(deprepair, "prepare", boom)
        return deprepair._prepare_guarded("plan-ui-1", None, confirmed_impact="x")

    def test_the_failed_record_carries_the_reason_codes_but_no_names(self, monkeypatch):
        rec = self._fail_with(
            monkeypatch,
            deprepair.RepairError(
                deprepair.ERROR_IMPORT_STILL_FAILED,
                "装上的发行包与计划不符: lab_tools: distribution_does_not_provide_import",
                imports={"lab_tools": deprepair.DIST_DOES_NOT_PROVIDE},
            ),
        )
        assert rec["state"] == deprepair.STATE_FAILED
        assert rec["code"] == deprepair.ERROR_IMPORT_STILL_FAILED
        assert rec["dist_reasons"] == [deprepair.DIST_DOES_NOT_PROVIDE]
        assert deprepair.progress("plan-ui-1")["dist_reasons"] == [deprepair.DIST_DOES_NOT_PROVIDE]
        assert "lab_tools" not in repr(rec["dist_reasons"])

    def test_an_ordinary_import_failure_has_no_such_key(self, monkeypatch):
        rec = self._fail_with(
            monkeypatch,
            deprepair.RepairError(
                deprepair.ERROR_IMPORT_STILL_FAILED, "x: boom", imports={"x": "ImportError: boom"}
            ),
        )
        assert rec["code"] == deprepair.ERROR_IMPORT_STILL_FAILED
        assert "dist_reasons" not in rec

    def test_other_failures_have_no_such_key(self, monkeypatch):
        rec = self._fail_with(monkeypatch, deprepair.RepairError(deprepair.ERROR_FAILED, "nope"))
        assert "dist_reasons" not in rec

    def test_the_task_diagnostic_projection_does_not_carry_it(self, monkeypatch):
        """诊断白名单另算：新字段不会因为进了进度记录就被带进诊断快照。"""
        self._fail_with(
            monkeypatch,
            deprepair.RepairError(
                deprepair.ERROR_IMPORT_STILL_FAILED,
                "x",
                imports={"lab": deprepair.DIST_NOT_INSTALLED},
            ),
        )
        projected = deprepair.diagnostic_projection(deprepair.progress("plan-ui-1"))
        assert "dist_reasons" not in repr(projected)


class TestFrontendMirror:
    def _api(self) -> str:
        return API_TS.read_text(encoding="utf-8")

    def _ts_tuple(self, name: str) -> list[str]:
        m = re.search(rf"export const {name} = \[(.*?)\] as const", self._api(), re.S)
        assert m, name
        return re.findall(r"'([^']+)'", m.group(1))

    def test_dist_failure_reasons_are_the_same_closed_set_in_the_same_order(self):
        assert tuple(self._ts_tuple("DIST_FAILURE_REASONS")) == deprepair.DIST_REASONS

    def test_the_guard_notices_a_drifted_mirror(self):
        drifted = self._api().replace("'distribution_unverifiable'", "'distribution_unreadable'")
        assert "'distribution_unverifiable'" not in drifted
        # 同样的切法在漂移后的文本上会得到不同的元组
        m = re.search(r"export const DIST_FAILURE_REASONS = \[(.*?)\] as const", drifted, re.S)
        assert tuple(re.findall(r"'([^']+)'", m.group(1))) != deprepair.DIST_REASONS

    def test_the_payload_keys_are_spelled_the_same_on_both_sides(self):
        api = self._api()
        assert "dist_reasons?: DistFailureReason[]" in api
        assert "confirmable?: number" in api
        src = (ROOT / "src" / "tavotto" / "engine" / "prepsession.py").read_text(encoding="utf-8")
        assert '"confirmable"' in src
        assert 'rec["dist_reasons"]' in (
            ROOT / "src" / "tavotto" / "engine" / "deprepair.py"
        ).read_text(encoding="utf-8")

    def test_origin_reasons_mirror_is_unchanged_by_this_pr(self):
        assert len(depplan.ORIGIN_REASONS) == 7
