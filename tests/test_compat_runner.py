"""CompatBench runner 的看护：分类、门禁、报告确定性、CLI。

**不跑真 worker**（那是 runner 自己的活，耗时以分钟计）——这里验的是它把
阶段结果折成结论的那一层。每条用例钉的仍然是「坏掉之后会怎样」：

* 分类把「我们的 bug」记成「产品边界」 → benchmark 从此只证明我们接受现状；
* 门禁只看总分 → Tier 1 上的缺陷被长尾的绿色稀释掉；
* CI 能自己改基线 → 红了就改期望；
* 报告顺序不确定 → 每次 diff 都是噪音，没人再读。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

CI_DIR = Path(__file__).resolve().parents[1] / "scripts" / "ci"
sys.path.insert(0, str(CI_DIR))

import compat_corpus as CC  # noqa: E402
import compat_matrix as CM  # noqa: E402


def case(cid="c", tier="expected", expected=None, cls=None, **extra) -> dict:
    out = {
        "id": cid,
        "category": "core_artists",
        "tier": tier,
        "expected": expected or {},
        "stem": "s",
        "expected_figures": 1,
        "script": "cases/core_artists/ca_basic_series.py",
        "discovery": "discoverable",
        **extra,
    }
    if cls:
        out["classification"] = cls
    return out


def stages(**kw) -> dict:
    return dict(kw)


# ============================================================ 分类
class TestClassification:
    def test_all_green_is_full_support(self):
        cls, _r, _d = CM.classify(case(), stages(execute=True, capture=True), {})
        assert cls == "full_support"

    def test_an_undeclared_failure_is_a_product_bug(self):
        """**没有被清单声明过的失败一律是我们的 bug。**

        否则「我们的 bug」会被悄悄记成「产品边界」，而那正是这套 benchmark
        要消灭的自欺。
        """
        cls, _r, detail = CM.classify(case(), stages(execute=True, capture=True, edit=False), {})
        assert cls == "product_bug"
        assert "edit" in detail

    def test_a_declared_boundary_must_name_the_stage_it_gives_up(self):
        """**光写 classification 不够，还得写清楚是哪一级不过。**

        否则「标成 unsupported_by_design」就成了万能挡箭牌：这个 case 从此
        无论哪里坏掉都是绿的。边界要具体到阶段（`expected.<stage>=false` +
        `expected_false_reasons`），classification 只说明这是哪一类边界。
        """
        c = case(cls="unsupported_by_design", reason="3D 只开放文字与视角")
        cls, _r, detail = CM.classify(c, stages(execute=True, edit=False), {})
        assert cls == "product_bug", "没声明是 edit 那一级，就得按缺陷算"
        assert "edit" in detail

    def test_a_boundary_declared_down_to_the_stage_is_honoured(self):
        c = case(
            cls="unsupported_by_design",
            reason="3D 数据属于代码，不在编辑器里改",
            expected={"edit": False},
            expected_false_reasons={"edit": "Line3D 刻意不出可编辑字段"},
        )
        cls, reason, _d = CM.classify(c, stages(execute=True, edit=False), {})
        assert cls == "unsupported_by_design"
        assert reason

    def test_by_design_and_partial_are_different_answers(self):
        """两个词回答的不是同一个问题，报告与路线图都靠这个区别：

        * `partial_support`         —— 有缺口，将来可能补（LineCollection…）；
        * `unsupported_by_design`   —— 缺口是产品决定，不打算补（3D 数据、
          改数据 / 改结构 → 回代码）。

        混成一个数字的话，「值得补的缺口有多少」这个问题就再也答不了。
        """
        assert "partial_support" in CC.CLASSIFICATIONS
        assert "unsupported_by_design" in CC.CLASSIFICATIONS
        assert CC.NEEDS_REASON == tuple(c for c in CC.CLASSIFICATIONS if c != "full_support")

    def test_declared_environment_dependency_absorbs_real_failures(self):
        c = case(cls="environment_dependency", reason="缺 seaborn")
        cls, _r, _d = CM.classify(c, stages(execute=False), {})
        assert cls == "environment_dependency"

    def test_expected_false_stage_is_a_declared_boundary(self):
        c = case(expected={"edit": False})
        cls, _r, detail = CM.classify(c, stages(execute=True, edit=False), {})
        assert cls == "partial_support"
        assert "edit" in detail

    def test_declared_partial_stays_partial_even_when_everything_passes(self):
        """声明过是部分支持的 case，全绿也不许升成 full_support。

        升上去的话「这条为什么被标成 partial」的记录就丢了，下一个人会以为
        缺口已经补上。
        """
        c = case(cls="partial_support", reason="LineCollection 不认")
        cls, _r, _d = CM.classify(c, stages(execute=True, capture=True), {})
        assert cls == "partial_support"

    def test_product_bug_is_pinned_to_the_first_failing_stage(self):
        """「execute 崩了所以 export 也失败」重复记账会让报告读起来像塌方。"""
        st = stages(discover=True, execute=False, capture=False, export=False)
        assert CM.product_bug_stage(st, {}) == "execute"


# ============================================================ 门禁
class TestGate:
    def _run(self, gate, cases, results, baseline=None):
        return CM.evaluate_gate(gate, cases, results, baseline)

    def test_tier1_failure_fails_every_gate(self):
        cases = [case("t1", tier="must")]
        results = {
            "t1": {
                "id": "t1",
                "tier": "must",
                "classification": "product_bug",
                "stages": stages(execute=False),
            }
        }
        for gate in CM.GATES:
            ok, fails = self._run(gate, cases, results, {"cases": {}})
            assert not ok, gate
            assert any("Tier 1" in f for f in fails)

    def test_a_new_product_bug_always_fails(self):
        cases = [case("x")]
        results = {
            "x": {
                "id": "x",
                "tier": "expected",
                "classification": "product_bug",
                "stages": stages(edit=False),
            }
        }
        ok, fails = self._run("pr", cases, results, {"cases": {}})
        assert not ok and any("新出现的 product_bug" in f for f in fails)

    def test_a_baseline_known_bug_passes_pr_but_not_release(self):
        """已知缺陷在基线里属于「看住」，但 1.0 的 exit rule 是 P0 = 0。"""
        cases = [case("x")]
        results = {
            "x": {
                "id": "x",
                "tier": "expected",
                "classification": "product_bug",
                "stages": stages(edit=False),
            }
        }
        baseline = {
            "cases": {
                "x": {
                    "classification": "product_bug",
                    "reason": "撤销回不去",
                    "follow_up": "别名组",
                    "stages": {},
                }
            }
        }
        assert self._run("pr", cases, results, baseline)[0]
        ok, fails = self._run("release", cases, results, baseline)
        assert not ok and any("release" in f for f in fails)

    def test_regressing_against_the_baseline_fails(self):
        """把 case 从 full_support 改成 unsupported_by_design 让 CI 变绿，
        是这里唯一真正想拦的作弊。"""
        cases = [case("x")]
        results = {
            "x": {
                "id": "x",
                "tier": "expected",
                "classification": "unsupported_by_design",
                "stages": stages(edit=False),
            }
        }
        baseline = {"cases": {"x": {"classification": "full_support", "stages": {}}}}
        ok, fails = self._run("nightly", cases, results, baseline)
        assert not ok and any("退步" in f for f in fails)

    def test_improving_against_the_baseline_passes(self):
        cases = [case("x")]
        results = {
            "x": {"id": "x", "tier": "expected", "classification": "full_support", "stages": {}}
        }
        baseline = {
            "cases": {
                "x": {"classification": "partial_support", "reason": "曾经只认一半", "stages": {}}
            }
        }
        assert self._run("nightly", cases, results, baseline)[0]

    def test_gates_get_progressively_stricter(self):
        """pr ⊆ main ⊆ nightly ⊆ release，缺一档就说明有人放松了顺序。"""
        prev: set = set()
        for gate in ("pr", "main", "nightly", "release"):
            now = set(CM.GATES[gate]["tier1_stages"])
            assert prev <= now, f"{gate} 比上一档更松"
            prev = now
        assert "fidelity" in CM.GATES["release"]["tier1_stages"]


# ============================================================ 报告
class TestReport:
    def _fixture(self):
        cases = [case("b", tier="must"), case("a")]
        results = {
            "a": {
                "id": "a",
                "category": "core_artists",
                "tier": "expected",
                "classification": "partial_support",
                "stages": stages(
                    discover=True, execute=True, capture=True, open=True, semantic=False
                ),
                "detail": {},
                "skipped": {},
                "census": {},
                "browser": None,
                "reason": "只认一半",
            },
            "b": {
                "id": "b",
                "category": "core_artists",
                "tier": "must",
                "classification": "full_support",
                "stages": stages(
                    discover=True, execute=True, capture=True, open=True, semantic=True
                ),
                "detail": {},
                "skipped": {},
                "census": {},
                "browser": None,
            },
        }
        return cases, results

    def test_cases_are_sorted_by_id(self):
        cases, results = self._fixture()
        rep = CM.build_report(cases, results, {}, "current", "all")
        assert [c["id"] for c in rep["cases"]] == ["a", "b"]

    def test_v2_observation_is_engine_layer_and_unobserved_metrics_are_null(self):
        """V2 基线指标：首次有效出图是引擎层口径（execute+capture+open）；回退率 / 干预次数 / 执行次数 /
        端到端 p50 p95 在这一层拿不到——必须是 null 并写明原因，不是 0（05_TEST_STRATEGY §7）。"""
        cases, results = self._fixture()
        obs = CM.build_report(cases, results, {}, "current", "all")["v2_observation"]
        assert obs["layer"] == "engine_one_shot"
        ff = obs["first_valid_figure"]
        assert (ff["passed"], ff["total"]) == (2, 2) and ff["rate"] == 1.0
        for key in (
            "exec_count",
            "fallback_rate",
            "intervention_count",
            "e2e_first_figure_ms_p50_p95",
        ):
            assert obs[key] is None, key
            assert obs["unobserved"].get(key), key

    def test_v2_case_duration_counts_each_measured_group_once(self):
        """`_finish()` 把整组的耗时抄给组内每个 case；同一脚本三个 stem 不能给 p50/p95 投三票。"""
        cases = [dict(case(f"g{i}"), script="s.py") for i in range(3)] + [
            dict(case("h"), script="t.py")
        ]
        results = {}
        for c in cases:
            results[c["id"]] = {
                "id": c["id"],
                "stages": {"execute": True, "capture": True, "open": True},
                "duration_ms": 9000.0 if c["script"] == "s.py" else 1000.0,
            }
        dur = CM.v2_observation(cases, results)["case_duration_ms"]
        assert dur["groups"] == 2
        assert dur["p50"] == 5000.0  # {1000, 9000}；逐 case 计则是 9000

    def test_report_json_is_deterministic(self):
        cases, results = self._fixture()
        a = CM.build_report(cases, results, {}, "current", "all")
        b = CM.build_report(
            list(reversed(cases)), dict(reversed(list(results.items()))), {}, "current", "all"
        )
        # generated_at 与 metadata 都是**随这一次运行变化**的身份字段，
        # 本来就不该参与「同样的输入是否产出同样的报告」。metadata 是本 PR
        # 加的：汇总要靠它认出「这份是本轮的」。
        for d in (a, b):
            d.pop("generated_at", None)
            d.pop("metadata", None)
        assert json.dumps(a, sort_keys=False) == json.dumps(b, sort_keys=False)

    def test_funnel_denominator_only_counts_cases_that_got_there(self):
        """execute 就崩了的 case 不该进 export 的分母——否则后面几级互相污染，
        看报告的人分不清「没跑」和「跑了没过」。"""
        cases = [case("a"), case("b")]
        results = {
            "a": {
                "id": "a",
                "tier": "expected",
                "classification": "product_bug",
                "stages": {"discover": True, "execute": False},
                "detail": {},
                "skipped": {},
                "census": {},
                "browser": None,
            },
            "b": {
                "id": "b",
                "tier": "expected",
                "classification": "full_support",
                "stages": {"discover": True, "execute": True, "export": True},
                "detail": {},
                "skipped": {},
                "census": {},
                "browser": None,
            },
        }
        rows = {r["stage"]: r for r in CM.funnel(cases, results)}
        assert rows["export"]["total"] == 1 and rows["export"]["passed"] == 1
        assert rows["execute"]["total"] == 2 and rows["execute"]["passed"] == 1

    def test_summary_shows_the_whole_funnel_not_one_percentage(self):
        cases, results = self._fixture()
        text = CM.render_summary(CM.build_report(cases, results, {}, "current", "all"))
        for label in CC.STAGE_LABELS.values():
            assert label in text

    def test_summary_calls_out_product_bugs_loudly(self):
        cases = [case("x")]
        results = {
            "x": {
                "id": "x",
                "tier": "expected",
                "classification": "product_bug",
                "stage": "edit",
                "stages": stages(edit=False),
                "detail": {},
                "skipped": {},
                "census": {},
                "browser": None,
                "detail_note": "未通过：['edit']",
            }
        }
        text = CM.render_summary(CM.build_report(cases, results, {}, "current", "all"))
        assert "Product bugs" in text and "`x`" in text

    def test_artist_census_ranks_the_biggest_gaps_first(self):
        results = {
            "a": {"census": {"total": {"QuadMesh": 1, "Line2D": 5}, "recognized": {"Line2D": 5}}},
            "b": {"census": {"total": {"QuadMesh": 3, "LineCollection": 9}, "recognized": {}}},
        }
        rows = CM.artist_census(results)
        assert [r["artist"] for r in rows] == ["LineCollection", "QuadMesh"]
        # 全认得的类不是缺口，不该出现在榜上
        assert "Line2D" not in [r["artist"] for r in rows]

    def test_fidelity_tolerance_is_a_reviewable_constant(self):
        """阈值必须是一张能被 review 的表，不许散在判定逻辑里。"""
        assert set(CM.FIDELITY_TOLERANCE) == {
            "changed_pixel_ratio",
            "mean_abs_diff",
            "max_abs_diff",
        }
        for v in CM.FIDELITY_TOLERANCE.values():
            assert isinstance(v, (int, float)) and v > 0
        # 保真度比 golden 视觉回归松一档是**有理由的**（两个进程各自编码），
        # 但不许松到失去意义。
        import pixelcompare  # noqa: PLC0415

        assert CM.FIDELITY_TOLERANCE["changed_pixel_ratio"] < 0.02
        assert pixelcompare.NOISE_FLOOR == 3

    def test_pixel_comparator_has_exactly_one_implementation(self):
        """`visual_regression` 与 CompatBench 必须用同一份算法。

        各写各的最直接的后果是两条门禁对同一张图给出相反结论；更隐蔽的是
        阈值悄悄漂开，某一侧变成永远不会红的摆设。
        """
        import pixelcompare
        import visual_regression as VR

        src = (CI_DIR / "visual_regression.py").read_text(encoding="utf-8")
        assert "def compare(" in src and "pixelcompare.compare" in src, (
            "visual_regression 又自己实现了一份 compare"
        )
        assert VR.verdict(
            {"ok": True, "changed_pixel_ratio": 1.0, "mean_abs_diff": 9.0, "max_abs_diff": 200},
            CM.FIDELITY_TOLERANCE,
        ) == pixelcompare.verdict(
            {"ok": True, "changed_pixel_ratio": 1.0, "mean_abs_diff": 9.0, "max_abs_diff": 200},
            CM.FIDELITY_TOLERANCE,
        )


# ============================================================ CLI
class TestCli:
    #: **读取侧也要钉 UTF-8。** 两个 CLI 自己把 stdout 钉成了 UTF-8
    #: （`_common.use_utf8_streams`），而 `text=True` 让**父进程**按本地区域
    #: 解码——Windows 上是 cp1252，中文 help 里的 `0x81/0x8D/0x8F/0x9D` 在
    #: 那张码表里根本没定义，于是 `out.stdout` 变成 None，断言报
    #: 「argument of type 'NoneType' is not iterable」，与真实原因毫不相干。
    #: 写的一侧钉了、读的一侧没钉，等于没钉——这是同一条不变式的两端。
    _DECODE = {"encoding": "utf-8", "errors": "replace"}

    def _run(self, args, env=None):
        import os

        return subprocess.run(
            [sys.executable, str(CI_DIR / "compat_matrix.py"), *args],
            capture_output=True,
            text=True,
            timeout=180,
            **self._DECODE,
            env={**os.environ, **(env or {})},
        )

    def test_help_works(self):
        out = self._run(["--help"])
        assert out.returncode == 0
        for flag in ("--smoke", "--all", "--case", "--target", "--update-baseline"):
            assert flag in out.stdout

    def test_list_does_not_render_anything(self):
        out = self._run(["--smoke", "--list"])
        assert out.returncode == 0
        assert "shape_pyplot_show_only" in out.stdout

    def test_ci_may_not_update_the_baseline(self):
        """**红了就更新基线**是这套东西唯一致命的退化方式，而且它一直报平安。"""
        out = self._run(["--all", "--update-baseline"], env={"CI": "true"})
        assert out.returncode == 2
        assert "不允许在 CI 环境使用" in out.stderr

    def test_unknown_case_id_is_an_error_not_an_empty_run(self):
        out = self._run(["--case", "no_such_case", "--list"])
        assert out.returncode == 2
        assert "no_such_case" in out.stderr

    def test_unknown_target_is_an_error(self):
        out = self._run(["--target", "moon", "--list"])
        assert out.returncode == 2

    def test_driver_help_works(self):
        out = subprocess.run(
            [sys.executable, str(CI_DIR / "compat_driver.py"), "--help"],
            capture_output=True,
            text=True,
            timeout=120,
            **self._DECODE,
        )
        assert out.returncode == 0
        for mode in ("native", "census", "browser"):
            assert mode in out.stdout


# ============================================================ 与既有 corpus 的边界
def test_compat_corpus_is_separate_from_the_acceptance_corpus():
    """`tests/acceptance/` 问「已支持的行为有没有退化」，`tests/compat/` 问
    「外部 matplotlib 世界我们兼容多少」。两者共享工具，语义必须分开——
    合并之后就再也分不清「我们退步了」和「我们本来就不支持」。"""
    acceptance = json.loads(
        (CC.REPO / "tests" / "acceptance" / "manifest.json").read_text(encoding="utf-8")
    )
    compat_stems = {c["stem"] for c in CC.load_manifest()["cases"]}
    assert not (set(acceptance["cases"]) & compat_stems), (
        "两套 corpus 的 stem 撞上了——它们各自回答不同的问题，别混"
    )
    assert not list((CC.CASES_DIR).glob("**/c0[123]_*.py")), "验收 corpus 的脚本被复制进 compat 了"


# ============================================================ 汇总集成
def test_summarize_surfaces_compat_and_never_hides_product_bugs():
    """实验室汇总表里必须有兼容性这一行，而且 product_bug 要出现在**细节列**。

    把它折进「部分支持」的数字里，扫读的人就永远看不到「有几个是我们自己的
    缺陷」——那正是这套 benchmark 最不能被稀释掉的那个数。
    """
    import summarize as SUM

    assert any(f == "compat.json" for f, _l, _k in SUM.SECTIONS)
    assert dict((f, k) for f, _l, k in SUM.SECTIONS)["compat.json"] == "correctness"
    detail = SUM._detail(
        "compat.json",
        {
            "target": "bundled",
            "summary": {
                "cases": 149,
                "funnel": [{"stage": "capture", "passed": 143, "total": 143}],
                "classification": {"full_support": 120},
                "product_bugs": [{"id": "art_legend_overlapping_fontsize", "stage": "edit"}],
            },
        },
    )
    assert "art_legend_overlapping_fontsize:edit" in detail
    assert "bundled" in detail
    clean = SUM._detail(
        "compat.json",
        {
            "target": "bundled",
            "summary": {"cases": 1, "funnel": [], "classification": {}, "product_bugs": []},
        },
    )
    assert "产品缺陷 0" in clean


def test_target_version_mismatch_refuses_to_produce_a_report():
    """一份标着 `target: bundled` 却跑在别的 matplotlib 上的报告，比没有报告
    更坏——它会被当成「内置 runtime 上验过了」。"""
    target = {"matplotlib": "3.11.1", "numpy": "2.5.2"}
    assert CM.check_target_versions(target, {"matplotlib": "3.11.1", "numpy": "2.5.2"}) == []
    bad = CM.check_target_versions(target, {"matplotlib": "3.10.8", "numpy": "2.5.2"})
    assert len(bad) == 1 and "3.10.8" in bad[0]


def test_missing_optional_package_is_not_a_version_mismatch():
    """缺包不算版本不符——那由 case 的 environment_dependency 分类如实记账，
    在这里报错只会让「本机没装 seaborn」变成「整轮跑不了」。"""
    assert CM.check_target_versions({"seaborn": "0.13.2"}, {}) == []


def test_worker_python_override_is_actually_applied(monkeypatch):
    """`--python` 必须让**渲染池**也用它，不能只喂给旁路驱动。

    只传给驱动的话，`--target bundled --python <bundled venv>` 会变成
    「对照组跑 3.11.1、Tavotto 跑机器上碰巧装着的 3.10.8」——保真度全线飘红，
    而报告标着 target=bundled。整轮 149 个 case 只有文字部分对不上（同一套
    矢量、不同版本的字体度量），撞过一次。
    """
    from tavotto.engine import pool

    monkeypatch.delenv("TAVOTTO_WORKER_PYTHON", raising=False)
    try:
        real = pool.find_worker_python()
    except pool.WorkerError:
        pytest.skip("本机没有装着 matplotlib 的解释器")
    chosen = CM._worker_python(real)
    assert chosen == real
    assert os.environ["TAVOTTO_WORKER_PYTHON"] == real
    picked, _src = pool.select_worker_python()
    assert pool.same_python(picked, real)


def test_worker_python_override_refuses_when_the_pool_disagrees(monkeypatch):
    """池没采纳指定的解释器时必须**报错**，不许悄悄用别的跑完。"""
    from tavotto.engine import pool

    # `_worker_python()` 直接写 `os.environ`（它是 CI 驱动，不是被测函数），
    # 而这里它在写完之后才抛错——不经 monkeypatch 记账的话，这条指向
    # 不存在路径的 TAVOTTO_WORKER_PYTHON 会一直漏给同一进程里后面的所有用例。
    monkeypatch.delenv("TAVOTTO_WORKER_PYTHON", raising=False)
    monkeypatch.setattr(pool, "select_worker_python", lambda: ("/somewhere/else/python", "system"))
    monkeypatch.setattr(pool, "reset_worker_python", lambda: None)
    with pytest.raises(RuntimeError, match="没有采纳"):
        CM._worker_python("/tmp/wanted/python")


def test_environment_dependency_says_whether_the_dep_was_present():
    """环境依赖这一档最容易被误读成「跳过了」。依赖在的时候要说出来。"""
    c = case(cls="environment_dependency", reason="seaborn 是可选依赖")
    _cls, _r, note = CM.classify(c, stages(execute=True, capture=True), {})
    assert "环境满足依赖" in note
    _cls2, _r2, note2 = CM.classify(c, stages(execute=False), {})
    assert "execute" in note2


def test_environment_dependency_only_absorbs_a_missing_dependency():
    """依赖**在**的时候，后面任何一级栽了都是真失败。

    否则「环境依赖」就成了一张永久免检证。实测撞到过：装了 seaborn 的目标上
    `sci_sns_bar` 的 replay 分歧被这一档吸收掉，而真实原因是那条 case 自己
    用了 bootstrap 置信区间（随机）——两件事都该被看见。
    """
    c = case(cls="environment_dependency", reason="seaborn 是可选依赖")
    # 依赖缺失：execute 就没过 → 吸收
    cls, _r, note = CM.classify(c, stages(execute=False), {})
    assert cls == "environment_dependency" and "依赖缺失" in note
    # 依赖在、跑起来了，replay 却分歧 → 真失败
    cls2, _r2, _n2 = CM.classify(c, stages(execute=True, capture=True, replay=False), {})
    assert cls2 == "product_bug"


def test_both_clis_pin_utf8_streams():
    """两个 CLI 都必须钉住 UTF-8 输出。

    Windows 上 stdout 被 CI/测试捕获时退回 cp1252，中文 help 与中文进度行
    直接 UnicodeEncodeError 打死进程——而这两个脚本的 `--help` 正好跑在
    ci.yml backend 矩阵的 windows-latest 那一档。`compat_driver` 更要紧：
    它的 **stdout 是协议通道**，末行 JSON 带 ensure_ascii=False。

    实现只有 `_common.use_utf8_streams` 一份；这条钉的是「两个入口都真的
    调了它」——漏掉的症状只在 Windows 上出现，本机怎么跑都是绿的。
    """
    for name in ("compat_matrix.py", "compat_driver.py"):
        src = (CI_DIR / name).read_text(encoding="utf-8")
        assert "use_utf8_streams()" in src, f"{name} 没调 use_utf8_streams"
        # 钉的是**从 _common 导入了这个名字**，不是某一种写法——
        # `from _common import run_metadata, use_utf8_streams` 一样成立。
        # 上一版把字面写法钉死，于是 compat_matrix 多导一个符号就红了，
        # 而「用的是不是唯一那份实现」完全没变。
        assert re.search(r"from _common import [^\n]*\buse_utf8_streams\b", src), (
            f"{name} 自己抄了一份，而不是用 _common 里那唯一的实现"
        )


def test_replay_stage_also_compares_property_values():
    """replay 阶段不能只比几何——那样它会替产品盖住产品自己的盲区。

    `app._compare_manifests` 的 docstring 写着「只比几何」（gid 集合 /
    bbox / anchor / size_mm）。实测过一个纯属性分歧：「广播改柱色 → 单柱
    改色 → 全撤」之后热态停在 `#775599`、全新重放是 `#1f77b4`，那个比较器
    比过 18 个元素、报 **0 处分歧**。

    一个自称在验等价性、却看不见颜色的基准，比没有基准更坏——它会给出
    「四路一致」的结论，而四路里有一路的颜色是错的。
    """
    same = {
        "elements": [{"gid": "axes_0.lines_0", "editable": [{"prop": "color", "value": "#123456"}]}]
    }
    other = {
        "elements": [{"gid": "axes_0.lines_0", "editable": [{"prop": "color", "value": "#654321"}]}]
    }
    assert CM._prop_diffs(same, same) == []
    got = CM._prop_diffs(same, other)
    assert len(got) == 1 and "axes_0.lines_0.color" in got[0]


def test_prop_diff_uses_the_same_tolerance_as_the_edit_stage():
    """数值比较不许另起一套容差——与 `stage_edit` 用的是同一个 `_same_value`。"""
    a = {"elements": [{"gid": "g", "editable": [{"prop": "lw", "value": 1.000}]}]}
    b = {"elements": [{"gid": "g", "editable": [{"prop": "lw", "value": 1.001}]}]}
    assert CM._prop_diffs(a, b) == [], "微小浮点差被当成分歧了"
    c = {"elements": [{"gid": "g", "editable": [{"prop": "lw", "value": 3.0}]}]}
    assert CM._prop_diffs(a, c), "真实差异没被抓到"


def test_target_python_version_is_checked_too():
    """**包版本对上不等于 Python 版本对上。**

    我自己栽过这条：`matrix.json` 的 minimum 档钉着 `python: "3.10"`，而我拿
    一个 3.11 的 venv 跑完整档、报告标着 `target: minimum` 交了出去。
    matplotlib 3.8.4 装对了，所以包版本核对一路绿。

    代价实打实：3.10 的 `pathlib` 在**类定义时**就把 `io.open` 绑进了
    `_NormalAccessor`，`Path.read_text()` 因此绕过 monkeypatch——这个只在
    3.10 上张开的缺口，正因为我跑的是 3.11，一直绿到 CI 的 ubuntu-3.10 才红。
    """
    t = {"python": "3.10", "matplotlib": "3.8.4"}
    assert CM.check_python_version(t, "3.10.20") == []
    bad = CM.check_python_version(t, "3.11.14")
    assert len(bad) == 1 and "3.11.14" in bad[0]
    # 没钉版本的 target（current）不该被这条挡住
    assert CM.check_python_version({}, "3.13.0") == []


def test_minimum_target_pins_the_python_the_package_claims():
    """矩阵里 minimum 档的 Python 必须就是 pyproject 的 requires-python 下界。

    那条下界本来就是「我们宣称支持的最老 Python」，矩阵却验另一个版本的话，
    宣称与验证之间就有一段没人走过的路——上面那个 pathlib 缺口正好落在那段里。
    """
    import re

    spec = CC.resolve_target(CC.load_matrix(), "minimum")
    pyproject = (CC.REPO / "pyproject.toml").read_text(encoding="utf-8")
    # 只比**下界**，不比整串：上界（`,<3.14`）会随支持范围变，把它写死会让
    # 这条用例在一次与本议题无关的调整里变红。
    m = re.search(r'requires-python\s*=\s*"[^"]*?>=\s*(\d+\.\d+)', pyproject)
    assert m, "读不出 pyproject 的 requires-python 下界"
    assert str(spec["python"]).startswith(m.group(1)), (
        f"pyproject 宣称的下界是 {m.group(1)}，而 matrix.json 的 minimum 档"
        f"钉的是 {spec['python']}——宣称与验证之间会留下一段没人走过的路"
    )


class TestParityGate:
    """桌面/浏览器语义分叉必须让门禁红——**任何档位**。

    这条曾经是空转的：对拍结果只写进 `results[cid]["browser"]`，报告里打出
    一节「Browser / Desktop semantic divergence」，然后门禁照常放行。
    `_finish()` 只从 `stages` 分类、`evaluate_gate()` 只看 stages 与
    classification，两处都够不着它。**一个把分叉打印出来、然后说「通过」的
    门禁，比不检查更坏**——它让人以为这件事有人看着。
    """

    def _case_and_result(self, parity_ok):
        c = case("x", tier="expected")
        r = {
            "x": {
                "id": "x",
                "tier": "expected",
                "classification": "full_support",
                "stages": {s: True for s in CC.STAGES},
                "browser": {"ok": parity_ok, "reason": "角色不一致：['line']"},
            }
        }
        return [c], r

    @pytest.mark.parametrize("gate", sorted(CM.GATES))
    def test_divergence_fails_every_gate(self, gate):
        cases, results = self._case_and_result(False)
        ok, fails = CM.evaluate_gate(gate, cases, results, {"cases": {}})
        assert not ok, f"{gate} 档放过了语义分叉"
        assert any("语义分叉" in f for f in fails), fails

    def test_agreement_passes(self):
        cases, results = self._case_and_result(True)
        assert CM.evaluate_gate("release", cases, results, {"cases": {}})[0]

    def test_cases_without_parity_data_are_not_penalised(self):
        """没跑对拍的 case（不是 browser_eligible，或本次没开 --browser）
        不该被这条当成分叉。"""
        c = case("x")
        r = {
            "x": {
                "id": "x",
                "tier": "expected",
                "classification": "full_support",
                "stages": {s: True for s in CC.STAGES},
                "browser": None,
            }
        }
        assert CM.evaluate_gate("release", [c], r, {"cases": {}})[0]


def test_browser_verdict_compares_editable_sets():
    """对拍要比可编辑属性集合——文档从第一版起就是这么写的。

    只比角色的话，浏览器侧多出或少掉任何一个属性、只要角色不变，这条就报
    成功。**文档说的和代码做的不是一回事，比两边都不做更坏。**
    """
    c = case("x")
    c["mutations"] = []
    desktop = {
        "detail": {
            "semantic": {
                "roles": ["axes", "line"],
                "editable_all": ["axes_0.lines_0.color", "axes_0.lines_0.linewidth"],
            }
        }
    }
    same = {
        "ok": True,
        "figures": ["s"],
        "semantics": {
            "s": {
                "roles": ["axes", "line"],
                "editable": ["axes_0.lines_0.color", "axes_0.lines_0.linewidth"],
            }
        },
    }
    assert CM._browser_verdict(c, same, desktop)["ok"]

    fewer = {
        "ok": True,
        "figures": ["s"],
        "semantics": {"s": {"roles": ["axes", "line"], "editable": ["axes_0.lines_0.color"]}},
    }
    v = CM._browser_verdict(c, fewer, desktop)
    assert not v["ok"] and "可编辑属性不一致" in v["reason"]
    assert v["editable_only_desktop"] == ["axes_0.lines_0.linewidth"]


def test_browser_verdict_compares_capture_counts():
    """**捕获到几张也要比。**

    `MAX_FIGURES` 在两侧作用的对象不同：桌面 `collect_pyplot_figures(limit=8)`
    只截待补的 pyplot 兜底（savefig 认领的那些不受限），浏览器 `browser.py`
    截的是**总数**。8 张 savefig + 1 张 show-only 于是桌面 9、浏览器 8——而
    保留下来的那张显式 stem 角色与可编辑属性完全一致，旧判据照报成功。

    `browser.py` 第 71 行写着「两个入口捕获到的图数因此不会分叉」，这条用例
    就是那句话的反例。
    """
    c = case("x", expected_figures=9)
    c["mutations"] = []
    sem = {"roles": ["axes", "line"], "editable": ["axes_0.lines_0.color"]}
    desktop = {
        "detail": {
            "semantic": {"roles": ["axes", "line"], "editable_all": ["axes_0.lines_0.color"]},
            "capture": {"stems": [f"s{i}" for i in range(9)], "got_figures": 9},
        }
    }

    # 两侧都是 9 张 → 通过。
    ok = {
        "ok": True,
        "figures": [f"s{i}" for i in range(9)],
        "truncated": 0,
        "semantics": {"s": sem},
    }
    assert CM._browser_verdict(c, ok, desktop)["ok"]

    # 浏览器只剩 8 张、并如实报了截断 → 必须红，且两条理由分开说。
    short = {
        "ok": True,
        "figures": [f"s{i}" for i in range(8)],
        "truncated": 1,
        "semantics": {"s": sem},
    }
    v = CM._browser_verdict(c, short, desktop)
    assert not v["ok"], "少捕获一张却报成功——这正是要挡的"
    assert "捕获张数不一致" in v["reason"]
    assert "截断了 1 张" in v["reason"]
    assert v["truncated"] == 1 and len(v["desktop_stems"]) == 9


def test_browser_verdict_flags_extra_figures_too():
    """多捕获也是分叉——判据是「不一致」，不是「少了」。"""
    c = case("x", expected_figures=1)
    c["mutations"] = []
    sem = {"roles": ["axes"], "editable": []}
    desktop = {
        "detail": {
            "semantic": {"roles": ["axes"], "editable_all": []},
            "capture": {"stems": ["s"], "got_figures": 1},
        }
    }
    more = {"ok": True, "figures": ["s", "s-2"], "truncated": 0, "semantics": {"s": sem}}
    v = CM._browser_verdict(c, more, desktop)
    assert not v["ok"] and "捕获张数不一致" in v["reason"]


def test_browser_verdict_without_desktop_capture_detail_is_not_penalised():
    """桌面侧没有 capture 明细时不拿张数说事——那是缺数据，不是分叉。"""
    c = case("x", expected_figures=1)
    c["mutations"] = []
    desktop = {"detail": {"semantic": {"roles": ["axes"], "editable_all": []}}}
    ok = {
        "ok": True,
        "figures": ["s"],
        "truncated": 0,
        "semantics": {"s": {"roles": ["axes"], "editable": []}},
    }
    assert CM._browser_verdict(c, ok, desktop)["ok"]


# ============================================================ runner 自身崩溃（#1133）
class TestRunnerError:
    """runner 自己崩了 ≠ 产品缺陷：独立分类 `runner_error`，报告可见、门禁不当产品回归。"""

    def _crashed(self, cid="x"):
        return CM.runner_crash_result(case(cid), "Traceback: boom")

    def test_runner_crash_is_runner_error_not_product_bug(self):
        r = self._crashed()
        assert r["classification"] == "runner_error"
        assert r["classification"] != "product_bug"
        assert "boom" in r["detail"]["runner_error"]

    def test_runner_error_is_not_a_declarable_classification(self):
        """清单/基线只能声明闭集里的状态，runner_error 只是一次运行的观测。"""
        assert "runner_error" not in CC.CLASSIFICATIONS
        assert "runner_error" in CC.RESULT_CLASSIFICATIONS

    def test_real_product_error_is_still_product_bug(self):
        cls, _r, _d = CM.classify(case("p"), stages(execute=False), {})
        assert cls == "product_bug"

    def test_crashed_route_runner_is_runner_error(self):
        routes = {
            "r": {
                "status": "fail",
                "code": "route_runner_crashed",
                "classification": "runner_error",
            }
        }
        cls, _r, _d = CM.classify(case(), stages(execute=True), {}, routes)
        assert cls == "runner_error"
        # 真正走不通的路由仍是 product_bug
        routes = {"r": {"status": "fail", "code": "x", "classification": "product_bug"}}
        assert CM.classify(case(), stages(execute=True), {}, routes)[0] == "product_bug"

    def _crashed_routes(self):
        return {
            "runner_error": {
                "status": "fail",
                "code": "route_runner_crashed",
                "classification": "runner_error",
                "detail": "Traceback: route boom",
            }
        }

    def test_stage_failure_plus_route_runner_crash_stays_product_bug(self):
        """引擎阶段已独立失败，随后路由 runner 崩溃：真回归不能被抹成 runner_error。"""
        routes = self._crashed_routes()
        cls, _r, detail = CM.classify(case(), stages(execute=True, replay=False), {}, routes)
        assert cls == "product_bug"
        assert "replay" in detail and "runner_error" in detail  # 崩溃信息仍可见
        # 经 _finish 落到结果里：分类 product_bug、stage 为失败阶段
        res = CM._blank(case("z"))
        res["stages"].update(execute=True, replay=False)
        res["routes"] = routes
        out = CM._finish({"z": res}, [case("z")], 0.0)["z"]
        assert out["classification"] == "product_bug"
        assert out["stage"] == "replay"
        assert "runner_error" in out["detail_note"]
        assert out["routes"]["runner_error"]["detail"] == "Traceback: route boom"

    def test_other_failed_route_plus_route_runner_crash_stays_product_bug(self):
        routes = {
            "bad": {"status": "fail", "code": "x", "classification": "product_bug"},
            **self._crashed_routes(),
        }
        cls, _r, detail = CM.classify(case(), stages(execute=True), {}, routes)
        assert cls == "product_bug" and "bad" in detail

    def test_route_runner_crash_detail_reaches_summary_at_documented_path(self):
        c = case("x")
        res = CM._blank(c)
        res["stages"].update(execute=True)
        res["routes"] = self._crashed_routes()
        results = CM._finish({"x": res}, [c], 0.0)
        assert results["x"]["classification"] == "runner_error"
        report = CM.build_report([c], results, {}, "current", "all")
        (e,) = report["summary"]["runner_errors"]
        assert e["detail"] and e["detail"] == results["x"]["routes"]["runner_error"]["detail"]
        text = CM.render_summary(report)
        assert "routes.runner_error.detail" in text
        # run_group 崩溃的位置同样存在
        crashed = self._crashed("y")
        assert crashed["detail"]["runner_error"]
        report = CM.build_report([case("y")], {"y": crashed}, {}, "current", "all")
        assert report["summary"]["runner_errors"][0]["detail"] == "Traceback: boom"

    def test_crash_alongside_product_bug_still_listed_in_runner_errors(self):
        """stage 失败 + 路由崩溃 → 仍是 product_bug，但崩溃按事实进 runner_errors。"""
        c = case("z")
        res = CM._blank(c)
        res["stages"].update(execute=True, replay=False)
        res["routes"] = self._crashed_routes()
        results = CM._finish({"z": res}, [c], 0.0)
        assert results["z"]["classification"] == "product_bug"
        report = CM.build_report([c], results, {}, "current", "all")
        s = report["summary"]
        assert s["classification"].get("runner_error", 0) == 0  # 分类计数不重复
        assert [b["id"] for b in s["product_bugs"]] == ["z"]
        (e,) = s["runner_errors"]
        assert e["id"] == "z" and e["classification"] == "product_bug"
        assert e["detail"] == "Traceback: route boom"
        text = CM.render_summary(report)
        assert "Runner errors" in text and "`z`" in text
        import summarize as SUM

        detail = SUM._detail("compat.json", {"target": "bundled", "summary": report["summary"]})
        assert "runner 崩溃 1" in detail
        warns: list[str] = []
        CM.evaluate_gate("pr", [c], results, {"cases": {}}, warns)
        wtext = "\n".join(warns)
        # 混合 case：走「也崩溃了」那条，不再说它没有裁决
        assert "也崩溃了" in wtext and "z" in wtext and "没有裁决" not in wtext

    def test_gate_warns_but_does_not_fail_on_runner_error(self):
        """维护者裁决（#1178）：runner 崩溃门禁只告警不致红，但告警里逐条可见。"""
        cases = [case("x")]
        results = {"x": self._crashed("x")}
        baseline = {"cases": {"x": {"classification": "full_support", "stages": {}}}}
        for gate in ("pr", "release"):
            warns: list[str] = []
            ok, fails = CM.evaluate_gate(gate, cases, results, baseline, warns)
            text = "\n".join(fails)
            assert "新出现的 product_bug" not in text
            assert "product_bug（含基线" not in text
            assert "退步" not in text
            assert ok and fails == []
            # 不能静默：没有裁决的 case 以基础设施理由出现在告警里
            wtext = "\n".join(warns)
            assert "runner 自身崩溃" in wtext and "非产品回归" in wtext and "x" in wtext

    def test_crash_before_any_stage_records_no_stage_and_skips_funnel(self):
        """discover 前崩溃：stages 留空，funnel 的 Discovery 分母/失败都不含它（Codex r4310627547）。"""
        crashed = self._crashed("x")
        assert crashed["stages"] == {}
        ok = CM._blank(case("y"))
        ok["stages"]["discover"] = True
        rows = CM.funnel([case("x"), case("y")], {"x": crashed, "y": ok})
        disc = next(r for r in rows if r["stage"] == "discover")
        assert disc["total"] == 1 and disc["passed"] == 1 and disc["rate"] == 1.0

    def test_gate_warning_splits_pure_crash_from_mixed_product_failure(self):
        """纯 runner_error 与「已有产品失败 + 崩溃」各一条告警，措辞各自正确（Codex r4310627554）。"""
        pure_case, mixed_case = case("pure"), case("mixed")
        res = CM._blank(mixed_case)
        res["stages"].update(execute=True, replay=False)
        res["routes"] = self._crashed_routes()
        results = CM._finish({"mixed": res}, [mixed_case], 0.0)
        results["pure"] = self._crashed("pure")
        assert results["mixed"]["classification"] == "product_bug"
        warns: list[str] = []
        CM.evaluate_gate("pr", [pure_case, mixed_case], results, {"cases": {}}, warns)
        assert len(warns) == 2
        (pure_w,) = [w for w in warns if "没有裁决" in w]
        (mixed_w,) = [w for w in warns if "也崩溃了" in w]
        assert "pure" in pure_w and "mixed" not in pure_w
        assert "mixed" in mixed_w and "pure" not in mixed_w
        assert "按 product_bug 处理" in mixed_w

    def test_runner_error_never_counts_as_a_product_bug_in_the_report(self):
        cases = [case("x"), case("y")]
        results = {"x": self._crashed("x")}
        results["y"] = {
            "id": "y",
            "category": "core_artists",
            "tier": "expected",
            "classification": "full_support",
            "stages": stages(discover=True, execute=True),
            "detail": {},
            "skipped": {},
            "census": {},
            "browser": None,
        }
        report = CM.build_report(cases, results, {}, "current", "all")
        s = report["summary"]
        assert s["product_bugs"] == []
        assert s["classification"] == {"full_support": 1, "runner_error": 1}
        assert [e["id"] for e in s["runner_errors"]] == ["x"]
        text = CM.render_summary(report)
        assert "runner_error | 1" in text and "Runner errors" in text
        assert "Product bugs: 0" in text

    def test_baseline_refuses_to_freeze_a_runner_error(self):
        with pytest.raises(CC.CorpusError) as ei:
            CC.baseline_payload({"x": self._crashed("x")})
        assert ei.value.code == "baseline_runner_error"

    # ---- #1178 r4310553654：基线按崩溃事实拒绝，统一判据 ----
    def _stage_fail_plus_route_crash(self, cid="z"):
        res = CM._blank(case(cid))
        res["stages"].update(execute=True, replay=False)
        res["routes"] = self._crashed_routes()
        return CM._finish({cid: res}, [case(cid)], 0.0)[cid]

    def test_baseline_refuses_when_route_crash_hidden_behind_product_bug(self):
        out = self._stage_fail_plus_route_crash("z")
        assert out["classification"] == "product_bug"  # 最终分类不是 runner_error
        with pytest.raises(CC.CorpusError) as ei:
            CC.baseline_payload({"z": out})
        assert ei.value.code == "baseline_runner_error"
        assert "z" in str(ei.value)

    def test_has_runner_crash_is_fact_based(self):
        assert CC.has_runner_crash(self._crashed("x"))
        assert CC.has_runner_crash(self._stage_fail_plus_route_crash("z"))
        clean = CM._blank(case("c"))
        assert not CC.has_runner_crash(clean)
        # 路由条目只有 classification、没有 detail 也算崩溃事实
        clean["routes"] = {"r": {"status": "fail", "classification": "runner_error"}}
        assert CC.has_runner_crash(clean)

    def test_report_lists_crash_hidden_behind_product_bug_without_detail(self):
        cases = [case("z")]
        res = CM._blank(case("z"))
        res["stages"].update(execute=True, replay=False)
        res["routes"] = {"r": {"status": "fail", "classification": "runner_error"}}
        results = CM._finish({"z": res}, cases, 0.0)
        report = CM.build_report(cases, results, {}, "current", "all")
        assert [e["id"] for e in report["summary"]["runner_errors"]] == ["z"]


class TestRunGroupCrashKeepsPartialResults:
    """run_group 中途崩溃：已记录的阶段结果保留，已有产品失败时仍判 product_bug（Codex #1178）。"""

    CRASH = "Traceback: hot.override boom"

    def test_finish_with_crash_keeps_independent_product_failure(self):
        c = case("a")
        res = CM._blank(c)
        res["stages"].update(discover=True, execute=True, replay=False)
        out = CM._finish({"a": res}, [c], 0.0, crash_tb=self.CRASH)["a"]
        assert out["classification"] == "product_bug"
        assert out["stage"] == "replay"
        assert out["stages"]["replay"] is False
        assert out["detail"]["runner_error"] == self.CRASH
        assert "runner_error" in out["detail_note"]
        assert "run_group_crashed" not in json.dumps(out["routes"])  # 合成条目不外泄
        assert CC.has_runner_crash(out)

    def test_finish_with_crash_and_no_failure_is_runner_error(self):
        c = case("a")
        res = CM._blank(c)
        res["stages"].update(discover=True, execute=True)
        out = CM._finish({"a": res}, [c], 0.0, crash_tb=self.CRASH)["a"]
        assert out["classification"] == "runner_error"
        assert out["stages"] == {"discover": True, "execute": True}

    def test_finish_with_crash_and_untouched_case_is_whole_runner_error(self):
        c = case("a")
        out = CM._finish({"a": CM._blank(c)}, [c], 0.0, crash_tb=self.CRASH)["a"]
        assert out["classification"] == "runner_error"
        assert out["detail"]["runner_error"] == self.CRASH

    def _patch_pipeline(self, monkeypatch, tmp_path, *, override_boom):
        from tavotto.engine import pool

        class FakeHot:
            def __init__(self):
                self.calls = 0

            def ensure_built(self):
                return {"stems": {"s": {"source": "x"}}, "timings": {}}

            def override(self, stem, patches):
                self.calls += 1
                if self.calls >= 2 and override_boom:
                    raise RuntimeError("hot.override boom")
                return {"manifest": {"elements": [{"gid": "g", "editable": []}]}, "timings": {}}

        monkeypatch.setattr(CM, "materialize", lambda g, w: (tmp_path, "s.py", "main"))
        monkeypatch.setattr(
            CM, "stage_discover", lambda *a, **k: {"ok": True, "entry": "main", "detail": {}}
        )
        monkeypatch.setattr(CM, "_fresh_worker", lambda *a, **k: FakeHot())
        monkeypatch.setattr(CM, "stage_semantic", lambda *a, **k: {"ok": True, "detail": {}})
        monkeypatch.setattr(
            CM,
            "stage_edit",
            lambda *a, **k: {"ok": True, "detail": {}, "full_patches": [{"gid": "g"}]},
        )
        monkeypatch.setattr(CM, "stage_replay", lambda *a, **k: {"ok": False, "detail": {}})
        monkeypatch.setattr(pool, "discard", lambda w: None)

    def _run(self, group, tmp_path):
        return CM.run_group(
            group,
            python=sys.executable,
            root=tmp_path,
            out_dir=tmp_path,
            want_fidelity=False,
            want_browser=False,
        )

    def test_run_group_crash_after_replay_failure_stays_product_bug(self, monkeypatch, tmp_path):
        self._patch_pipeline(monkeypatch, tmp_path, override_boom=True)
        c = case("a", expected_figures=1)
        results = self._run([c], tmp_path)
        r = results["a"]
        assert r["classification"] == "product_bug"
        assert r["stages"]["replay"] is False
        assert "hot.override boom" in r["detail"]["runner_error"]
        assert CC.has_runner_crash(r)
        report = CM.build_report([c], results, {}, "current", "all")
        assert [b["id"] for b in report["summary"]["product_bugs"]] == ["a"]
        assert [e["id"] for e in report["summary"]["runner_errors"]] == ["a"]
        with pytest.raises(CC.CorpusError) as ei:
            CC.baseline_payload(results)
        assert ei.value.code == "baseline_runner_error"

    def test_run_group_crash_before_any_stage_is_runner_error(self, monkeypatch, tmp_path):
        self._patch_pipeline(monkeypatch, tmp_path, override_boom=False)

        def boom(*a, **k):
            raise RuntimeError("discover boom")

        monkeypatch.setattr(CM, "stage_discover", boom)
        results = self._run([case("a")], tmp_path)
        assert results["a"]["classification"] == "runner_error"
        assert "discover boom" in results["a"]["detail"]["runner_error"]

    def test_route_runner_crash_keeps_completed_routes_and_appends_crash(
        self, monkeypatch, tmp_path
    ):
        """stage_product_routes 抛错：A 已存下的失败路由保留（product_bug），B 只有崩溃 → runner_error。"""
        self._patch_pipeline(monkeypatch, tmp_path, override_boom=False)
        monkeypatch.setattr(CM, "stage_replay", lambda *a, **k: {"ok": True, "detail": {}})
        monkeypatch.setattr(CM, "stage_export", lambda *a, **k: {"ok": True, "detail": {}})
        failed = {
            "status": "fail",
            "via": "http",
            "code": "route_failed",
            "classification": "product_bug",
            "reason": "产品路由未走通（case a）",
            "follow_up": "x",
            "detail": {},
        }

        def routes_then_boom(group, root, results):
            results["a"]["routes"] = {"http": dict(failed), "cli": {"status": "pass"}}
            raise RuntimeError("routes boom")

        monkeypatch.setattr(CM, "stage_product_routes", routes_then_boom)
        # a 还声明了一条没来得及存的 native_run → 路由检查未完成，才会被追加崩溃
        a = case("a", product_routes={"http": True, "cli": True, "native_run": True})
        b = case("b", product_routes={"http": True})
        results = self._run([a, b], tmp_path)

        ra = results["a"]
        assert ra["classification"] == "product_bug"
        assert ra["routes"]["http"] == failed
        assert ra["routes"]["cli"] == {"status": "pass"}
        assert ra["routes"]["runner_error"]["code"] == "route_runner_crashed"
        assert CC.has_runner_crash(ra)

        rb = results["b"]
        assert rb["classification"] == "runner_error"
        assert list(rb["routes"]) == ["runner_error"]

        report = CM.build_report([a, b], results, {}, "current", "all")
        assert [x["id"] for x in report["summary"]["product_bugs"]] == ["a"]
        assert {x["id"] for x in report["summary"]["runner_errors"]} >= {"a", "b"}

    def test_route_runner_crash_does_not_retag_a_case_whose_routes_finished(
        self, monkeypatch, tmp_path
    ):
        """真实异常路径：A 只声明 safe_probe 且已存好；B 的 native_run 崩溃 → 只有 B 记崩溃（r4310699342）。"""
        from tavotto import app as tavotto_app

        self._patch_pipeline(monkeypatch, tmp_path, override_boom=False)
        monkeypatch.setattr(CM, "stage_replay", lambda *a, **k: {"ok": True, "detail": {}})
        monkeypatch.setattr(CM, "stage_export", lambda *a, **k: {"ok": True, "detail": {}})
        monkeypatch.setattr(
            CM, "route_probe_via_app", lambda *a, **k: {"ok": True, "via": "http", "detail": {}}
        )
        monkeypatch.setattr(CM, "route_cli_open", lambda *a, **k: {"ok": True, "via": "cli"})
        monkeypatch.setattr(tavotto_app, "close_project", lambda pj: None)

        def native_boom(*a, **k):
            raise RuntimeError("native boom")

        monkeypatch.setattr(CM, "route_native_run", native_boom)
        a = case("a", product_routes={"safe_probe": True})
        b = case("b", product_routes={"native_run": True})
        results = self._run([a, b], tmp_path)

        ra = results["a"]
        assert ra["routes"] == {"safe_probe": ra["routes"]["safe_probe"]}
        assert ra["routes"]["safe_probe"]["status"] == "pass"
        assert "runner_error" not in ra["routes"]
        assert ra["classification"] != "runner_error"
        assert not CC.has_runner_crash(ra)

        rb = results["b"]
        # 逐路由隔离：崩溃记在 native_run 自己的条目上（不再是汇总键）
        assert rb["routes"]["native_run"]["code"] == "route_runner_crashed"
        assert "native boom" in rb["routes"]["native_run"]["detail"]
        assert "native boom" in CC.runner_error_detail(rb)
        assert rb["classification"] == "runner_error"

        report = CM.build_report([a, b], results, {}, "current", "all")
        assert [e["id"] for e in report["summary"]["runner_errors"]] == ["b"]

    def test_later_route_crash_keeps_earlier_failed_route_of_same_case(self, monkeypatch, tmp_path):
        """真实 stage_product_routes：同一 case 的 desktop_project 已失败、native_run 抛 OSError → 仍是 product_bug（r4310730061）。"""
        from tavotto import app as tavotto_app

        self._patch_pipeline(monkeypatch, tmp_path, override_boom=False)
        monkeypatch.setattr(CM, "stage_replay", lambda *a, **k: {"ok": True, "detail": {}})
        monkeypatch.setattr(CM, "stage_export", lambda *a, **k: {"ok": True, "detail": {}})
        monkeypatch.setattr(
            CM,
            "route_probe_via_app",
            lambda *a, **k: {"ok": True, "via": "http", "pj": object(), "detail": {}},
        )
        monkeypatch.setattr(
            CM,
            "route_desktop_project",
            lambda *a, **k: {"ok": False, "code": "desktop_project_failed", "detail": {}},
        )
        monkeypatch.setattr(CM, "route_cli_open", lambda *a, **k: {"ok": True, "via": "cli"})
        monkeypatch.setattr(tavotto_app, "close_project", lambda pj: None)

        def native_boom(*a, **k):
            raise OSError("spawn native_run boom")

        monkeypatch.setattr(CM, "route_native_run", native_boom)
        a = case("a", product_routes={"desktop_project": True, "native_run": True})
        results = self._run([a], tmp_path)

        ra = results["a"]
        assert ra["routes"]["desktop_project"]["status"] == "fail"
        assert ra["routes"]["desktop_project"]["classification"] == "product_bug"
        assert ra["routes"]["native_run"]["code"] == "route_runner_crashed"
        assert ra["routes"]["native_run"]["classification"] == "runner_error"
        assert "spawn native_run boom" in ra["routes"]["native_run"]["detail"]
        assert ra["classification"] == "product_bug"

        report = CM.build_report([a], results, {}, "current", "all")
        assert [x["id"] for x in report["summary"]["product_bugs"]] == ["a"]
        assert [x["id"] for x in report["summary"]["runner_errors"]] == ["a"]
        ok, fails = CM.evaluate_gate("pr", [a], results, {"cases": {}})
        assert not ok and any("新出现的 product_bug" in f for f in fails)

    def _isolation_pipeline(self, monkeypatch, tmp_path, *, probe, cli, close=None):
        from tavotto import app as tavotto_app

        self._patch_pipeline(monkeypatch, tmp_path, override_boom=False)
        monkeypatch.setattr(CM, "stage_replay", lambda *a, **k: {"ok": True, "detail": {}})
        monkeypatch.setattr(CM, "stage_export", lambda *a, **k: {"ok": True, "detail": {}})
        monkeypatch.setattr(CM, "route_probe_via_app", probe)
        monkeypatch.setattr(CM, "route_cli_open", cli)
        monkeypatch.setattr(tavotto_app, "close_project", close or (lambda pj: None))

    def test_shared_probe_failure_survives_later_cli_crash(self, monkeypatch, tmp_path):
        """safe_probe 已是产品失败，随后 route_cli_open 起不来 → 仍 product_bug，门禁致红（r4310776661）。"""

        def cli_boom(*a, **k):
            raise OSError("cli spawn boom")

        self._isolation_pipeline(
            monkeypatch,
            tmp_path,
            probe=lambda *a, **k: {
                "ok": False,
                "code": "probe_failed",
                "via": "http",
                "pj": "pj1",
                "detail": {},
            },
            cli=cli_boom,
        )
        a = case("a", product_routes={"safe_probe": True, "cli_open": True})
        results = self._run([a], tmp_path)
        ra = results["a"]
        assert ra["routes"]["safe_probe"]["status"] == "fail"
        assert ra["routes"]["safe_probe"]["classification"] == "product_bug"
        assert ra["routes"]["cli_open"]["code"] == "route_runner_crashed"
        assert ra["routes"]["cli_open"]["classification"] == "runner_error"
        assert "cli spawn boom" in ra["routes"]["cli_open"]["detail"]
        assert ra["classification"] == "product_bug"
        assert CC.has_runner_crash(ra)
        report = CM.build_report([a], results, {}, "current", "all")
        assert [x["id"] for x in report["summary"]["runner_errors"]] == ["a"]
        ok, fails = CM.evaluate_gate("pr", [a], results, {"cases": {}})
        assert not ok and any("新出现的 product_bug" in f for f in fails)

    def test_close_project_crash_keeps_route_verdicts_and_records_fact(self, monkeypatch, tmp_path):
        def close_boom(pj):
            raise RuntimeError("close boom")

        self._isolation_pipeline(
            monkeypatch,
            tmp_path,
            probe=lambda *a, **k: {
                "ok": False,
                "code": "probe_failed",
                "via": "http",
                "pj": "pj1",
                "detail": {},
            },
            cli=lambda *a, **k: {"ok": True, "via": "cli", "payload": {"stem": "s"}},
            close=close_boom,
        )
        a = case("a", product_routes={"safe_probe": True, "cli_open": True})
        results = self._run([a], tmp_path)
        ra = results["a"]
        assert ra["routes"]["safe_probe"]["classification"] == "product_bug"
        assert ra["routes"]["cli_open"]["status"] == "pass"
        assert ra["routes"]["runner_error"]["code"] == "route_runner_crashed"
        assert "close boom" in CC.runner_error_detail(ra)
        assert ra["classification"] == "product_bug"
        assert CC.has_runner_crash(ra)

    def test_only_one_route_crashing_is_runner_error(self, monkeypatch, tmp_path):
        def cli_boom(*a, **k):
            raise OSError("cli only boom")

        self._isolation_pipeline(
            monkeypatch,
            tmp_path,
            probe=lambda *a, **k: {"ok": True, "via": "http", "detail": {}},
            cli=cli_boom,
        )
        a = case("a", product_routes={"safe_probe": True, "cli_open": True})
        results = self._run([a], tmp_path)
        ra = results["a"]
        assert ra["routes"]["safe_probe"]["status"] == "pass"
        assert ra["routes"]["cli_open"]["code"] == "route_runner_crashed"
        assert ra["classification"] == "runner_error"
        ok, fails = CM.evaluate_gate("pr", [a], results, {"cases": {}})
        assert not any("新出现的 product_bug" in f for f in fails)


class TestMalformedProductResponseIsProductBug:
    """产品返回不合契约的响应 = 路由失败（product_bug），不是 runner 崩溃（Codex #1178 r4310660485）。"""

    class _Resp:
        def __init__(self, status, body):
            self.status_code, self._body = status, body

        def get_json(self):
            return self._body

    def _patch(self, monkeypatch, tmp_path, *, open_body, cli_stdout):
        from tavotto import app as tavotto_app

        outer = self

        class Client:
            def post(self, url, json=None):
                if url == "/api/projects/open":
                    return outer._Resp(200, open_body)
                return outer._Resp(200, {"registered": True, "stems": ["s"]})

            def get(self, url):
                return outer._Resp(200, {})

        monkeypatch.setattr(tavotto_app.app, "test_client", lambda: Client())
        monkeypatch.setattr(tavotto_app, "close_project", lambda pj: None)
        monkeypatch.setattr(CM, "materialize", lambda g, w: (tmp_path, "s.py", "main"))
        monkeypatch.setattr(
            CM.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=cli_stdout, stderr=""),
        )

    def _routes(self, monkeypatch, tmp_path, **kw):
        self._patch(monkeypatch, tmp_path, **kw)
        c = case("a", product_routes={"safe_probe": True, "cli_open": True})
        results = {"a": CM._blank(c)}
        CM.stage_product_routes([c], tmp_path, results)
        return c, results["a"]["routes"]

    GOOD_CLI = json.dumps({"ok": True, "stem": "s"})

    def test_open_200_without_id_is_a_failed_route_not_a_crash(self, monkeypatch, tmp_path):
        c, routes = self._routes(
            monkeypatch, tmp_path, open_body={"name": "x"}, cli_stdout=self.GOOD_CLI
        )
        e = routes["safe_probe"]
        assert e["status"] == "fail"
        assert e["classification"] == "product_bug"
        assert e["code"] == "malformed_product_response"
        assert e["detail"]["field"] == "id"
        assert routes["cli_open"]["status"] == "pass"
        cls, _r, _d = CM.classify(c, stages(execute=True), {}, routes)
        assert cls == "product_bug"

    def test_open_body_not_an_object_is_a_failed_route(self, monkeypatch, tmp_path):
        _c, routes = self._routes(monkeypatch, tmp_path, open_body=["id"], cli_stdout=self.GOOD_CLI)
        assert routes["safe_probe"]["code"] == "malformed_product_response"
        assert routes["safe_probe"]["classification"] == "product_bug"

    def test_cli_output_not_an_object_is_a_failed_route(self, monkeypatch, tmp_path):
        _c, routes = self._routes(
            monkeypatch, tmp_path, open_body={"id": "p1"}, cli_stdout="[1, 2]"
        )
        e = routes["cli_open"]
        assert e["status"] == "fail" and e["classification"] == "product_bug"
        assert e["code"] == "malformed_product_response"

    def test_cli_figures_malformed_is_a_failed_route(self, monkeypatch, tmp_path):
        self._patch(
            monkeypatch,
            tmp_path,
            open_body={"id": "p1"},
            cli_stdout=json.dumps({"ok": False, "code": "multiple_figures_found", "figures": [1]}),
        )
        group = [case("a", product_routes={"cli_open": True}), case("b", stem="t")]
        results = {c["id"]: CM._blank(c) for c in group}
        CM.stage_product_routes(group, tmp_path, results)
        e = results["a"]["routes"]["cli_open"]
        assert e["status"] == "fail" and e["classification"] == "product_bug"

    def test_gate_treats_it_as_a_new_product_bug(self, monkeypatch, tmp_path):
        c, routes = self._routes(monkeypatch, tmp_path, open_body={}, cli_stdout=self.GOOD_CLI)
        res = CM._blank(c)
        res["stages"].update(discover=True, execute=True)
        res["routes"] = routes
        res["classification"], res["reason"], note = CM.classify(c, res["stages"], {}, routes)
        res["detail"] = {"note": note}
        assert res["classification"] == "product_bug"
        ok, fails = CM.evaluate_gate("pr", [c], {"a": res}, {"cases": {}})
        assert not ok and any("新出现的 product_bug" in f for f in fails)

    def test_harness_crash_in_materialize_is_a_route_runner_error(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path, open_body={"id": "p"}, cli_stdout=self.GOOD_CLI)

        def boom(*a, **k):
            raise OSError("materialize boom")

        monkeypatch.setattr(CM, "materialize", boom)
        c = case("a", product_routes={"safe_probe": True})
        results = {"a": CM._blank(c)}
        CM.stage_product_routes([c], tmp_path, results)
        e = results["a"]["routes"]["safe_probe"]
        assert e["code"] == "route_runner_crashed" and e["classification"] == "runner_error"
        assert "materialize boom" in e["detail"]
