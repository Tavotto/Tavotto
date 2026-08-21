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
import subprocess
import sys
from pathlib import Path

import pytest

CI_DIR = Path(__file__).resolve().parents[1] / "scripts" / "ci"
sys.path.insert(0, str(CI_DIR))

import compat_corpus as CC  # noqa: E402
import compat_matrix as CM  # noqa: E402


def case(cid="c", tier="expected", expected=None, cls=None, **extra) -> dict:
    out = {"id": cid, "category": "core_artists", "tier": tier,
           "expected": expected or {}, "stem": "s", "expected_figures": 1,
           "script": "cases/core_artists/ca_basic_series.py",
           "discovery": "discoverable", **extra}
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
        cls, _r, detail = CM.classify(
            case(), stages(execute=True, capture=True, edit=False), {})
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
        c = case(cls="unsupported_by_design", reason="3D 数据属于代码，不在编辑器里改",
                 expected={"edit": False},
                 expected_false_reasons={"edit": "Line3D 刻意不出可编辑字段"})
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
        assert CC.NEEDS_REASON == tuple(
            c for c in CC.CLASSIFICATIONS if c != "full_support")

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
        results = {"t1": {"id": "t1", "tier": "must", "classification": "product_bug",
                          "stages": stages(execute=False)}}
        for gate in CM.GATES:
            ok, fails = self._run(gate, cases, results, {"cases": {}})
            assert not ok, gate
            assert any("Tier 1" in f for f in fails)

    def test_a_new_product_bug_always_fails(self):
        cases = [case("x")]
        results = {"x": {"id": "x", "tier": "expected",
                         "classification": "product_bug", "stages": stages(edit=False)}}
        ok, fails = self._run("pr", cases, results, {"cases": {}})
        assert not ok and any("新出现的 product_bug" in f for f in fails)

    def test_a_baseline_known_bug_passes_pr_but_not_release(self):
        """已知缺陷在基线里属于「看住」，但 1.0 的 exit rule 是 P0 = 0。"""
        cases = [case("x")]
        results = {"x": {"id": "x", "tier": "expected",
                         "classification": "product_bug", "stages": stages(edit=False)}}
        baseline = {"cases": {"x": {"classification": "product_bug",
                                    "reason": "撤销回不去",
                                    "follow_up": "别名组", "stages": {}}}}
        assert self._run("pr", cases, results, baseline)[0]
        ok, fails = self._run("release", cases, results, baseline)
        assert not ok and any("release" in f for f in fails)

    def test_regressing_against_the_baseline_fails(self):
        """把 case 从 full_support 改成 unsupported_by_design 让 CI 变绿，
        是这里唯一真正想拦的作弊。"""
        cases = [case("x")]
        results = {"x": {"id": "x", "tier": "expected",
                         "classification": "unsupported_by_design",
                         "stages": stages(edit=False)}}
        baseline = {"cases": {"x": {"classification": "full_support", "stages": {}}}}
        ok, fails = self._run("nightly", cases, results, baseline)
        assert not ok and any("退步" in f for f in fails)

    def test_improving_against_the_baseline_passes(self):
        cases = [case("x")]
        results = {"x": {"id": "x", "tier": "expected",
                         "classification": "full_support", "stages": {}}}
        baseline = {"cases": {"x": {"classification": "partial_support",
                                    "reason": "曾经只认一半", "stages": {}}}}
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
            "a": {"id": "a", "category": "core_artists", "tier": "expected",
                  "classification": "partial_support", "stages":
                      stages(discover=True, execute=True, capture=True, open=True,
                             semantic=False),
                  "detail": {}, "skipped": {}, "census": {}, "browser": None,
                  "reason": "只认一半"},
            "b": {"id": "b", "category": "core_artists", "tier": "must",
                  "classification": "full_support", "stages":
                      stages(discover=True, execute=True, capture=True, open=True,
                             semantic=True),
                  "detail": {}, "skipped": {}, "census": {}, "browser": None},
        }
        return cases, results

    def test_cases_are_sorted_by_id(self):
        cases, results = self._fixture()
        rep = CM.build_report(cases, results, {}, "current", "all")
        assert [c["id"] for c in rep["cases"]] == ["a", "b"]

    def test_report_json_is_deterministic(self):
        cases, results = self._fixture()
        a = CM.build_report(cases, results, {}, "current", "all")
        b = CM.build_report(list(reversed(cases)),
                            dict(reversed(list(results.items()))), {},
                            "current", "all")
        a.pop("generated_at"), b.pop("generated_at")
        assert json.dumps(a, sort_keys=False) == json.dumps(b, sort_keys=False)

    def test_funnel_denominator_only_counts_cases_that_got_there(self):
        """execute 就崩了的 case 不该进 export 的分母——否则后面几级互相污染，
        看报告的人分不清「没跑」和「跑了没过」。"""
        cases = [case("a"), case("b")]
        results = {
            "a": {"id": "a", "tier": "expected", "classification": "product_bug",
                  "stages": {"discover": True, "execute": False},
                  "detail": {}, "skipped": {}, "census": {}, "browser": None},
            "b": {"id": "b", "tier": "expected", "classification": "full_support",
                  "stages": {"discover": True, "execute": True, "export": True},
                  "detail": {}, "skipped": {}, "census": {}, "browser": None},
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
        results = {"x": {"id": "x", "tier": "expected", "classification": "product_bug",
                         "stage": "edit", "stages": stages(edit=False), "detail": {},
                         "skipped": {}, "census": {}, "browser": None,
                         "detail_note": "未通过：['edit']"}}
        text = CM.render_summary(CM.build_report(cases, results, {}, "current", "all"))
        assert "Product bugs" in text and "`x`" in text

    def test_artist_census_ranks_the_biggest_gaps_first(self):
        results = {
            "a": {"census": {"total": {"QuadMesh": 1, "Line2D": 5},
                             "recognized": {"Line2D": 5}}},
            "b": {"census": {"total": {"QuadMesh": 3, "LineCollection": 9},
                             "recognized": {}}},
        }
        rows = CM.artist_census(results)
        assert [r["artist"] for r in rows] == ["LineCollection", "QuadMesh"]
        # 全认得的类不是缺口，不该出现在榜上
        assert "Line2D" not in [r["artist"] for r in rows]

    def test_fidelity_tolerance_is_a_reviewable_constant(self):
        """阈值必须是一张能被 review 的表，不许散在判定逻辑里。"""
        assert set(CM.FIDELITY_TOLERANCE) == {
            "changed_pixel_ratio", "mean_abs_diff", "max_abs_diff"}
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
            "visual_regression 又自己实现了一份 compare")
        assert VR.verdict({"ok": True, "changed_pixel_ratio": 1.0,
                           "mean_abs_diff": 9.0, "max_abs_diff": 200},
                          CM.FIDELITY_TOLERANCE) == pixelcompare.verdict(
            {"ok": True, "changed_pixel_ratio": 1.0, "mean_abs_diff": 9.0,
             "max_abs_diff": 200}, CM.FIDELITY_TOLERANCE)


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
            capture_output=True, text=True, timeout=180, **self._DECODE,
            env={**os.environ, **(env or {})})

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
            capture_output=True, text=True, timeout=120, **self._DECODE)
        assert out.returncode == 0
        for mode in ("native", "census", "browser"):
            assert mode in out.stdout


# ============================================================ 与既有 corpus 的边界
def test_compat_corpus_is_separate_from_the_acceptance_corpus():
    """`tests/acceptance/` 问「已支持的行为有没有退化」，`tests/compat/` 问
    「外部 matplotlib 世界我们兼容多少」。两者共享工具，语义必须分开——
    合并之后就再也分不清「我们退步了」和「我们本来就不支持」。"""
    acceptance = json.loads(
        (CC.REPO / "tests" / "acceptance" / "manifest.json").read_text(encoding="utf-8"))
    compat_stems = {c["stem"] for c in CC.load_manifest()["cases"]}
    assert not (set(acceptance["cases"]) & compat_stems), (
        "两套 corpus 的 stem 撞上了——它们各自回答不同的问题，别混")
    assert not list((CC.CASES_DIR).glob("**/c0[123]_*.py")), \
        "验收 corpus 的脚本被复制进 compat 了"


# ============================================================ 汇总集成
def test_summarize_surfaces_compat_and_never_hides_product_bugs():
    """实验室汇总表里必须有兼容性这一行，而且 product_bug 要出现在**细节列**。

    把它折进「部分支持」的数字里，扫读的人就永远看不到「有几个是我们自己的
    缺陷」——那正是这套 benchmark 最不能被稀释掉的那个数。
    """
    import summarize as SUM
    assert any(f == "compat.json" for f, _l, _k in SUM.SECTIONS)
    assert dict((f, k) for f, _l, k in SUM.SECTIONS)["compat.json"] == "correctness"
    detail = SUM._detail("compat.json", {
        "target": "bundled",
        "summary": {"cases": 149,
                    "funnel": [{"stage": "capture", "passed": 143, "total": 143}],
                    "classification": {"full_support": 120},
                    "product_bugs": [{"id": "art_legend_overlapping_fontsize",
                                      "stage": "edit"}]}})
    assert "art_legend_overlapping_fontsize:edit" in detail
    assert "bundled" in detail
    clean = SUM._detail("compat.json", {
        "target": "bundled",
        "summary": {"cases": 1, "funnel": [], "classification": {},
                    "product_bugs": []}})
    assert "产品缺陷 0" in clean


def test_target_version_mismatch_refuses_to_produce_a_report():
    """一份标着 `target: bundled` 却跑在别的 matplotlib 上的报告，比没有报告
    更坏——它会被当成「内置 runtime 上验过了」。"""
    target = {"matplotlib": "3.11.1", "numpy": "2.5.2"}
    assert CM.check_target_versions(target, {"matplotlib": "3.11.1",
                                             "numpy": "2.5.2"}) == []
    bad = CM.check_target_versions(target, {"matplotlib": "3.10.8",
                                            "numpy": "2.5.2"})
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
    monkeypatch.setattr(pool, "select_worker_python",
                        lambda: ("/somewhere/else/python", "system"))
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
    cls2, _r2, _n2 = CM.classify(
        c, stages(execute=True, capture=True, replay=False), {})
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
        assert "from _common import use_utf8_streams" in src, (
            f"{name} 自己抄了一份，而不是用 _common 里那唯一的实现")


def test_replay_stage_also_compares_property_values():
    """replay 阶段不能只比几何——那样它会替产品盖住产品自己的盲区。

    `app._compare_manifests` 的 docstring 写着「只比几何」（gid 集合 /
    bbox / anchor / size_mm）。实测过一个纯属性分歧：「广播改柱色 → 单柱
    改色 → 全撤」之后热态停在 `#775599`、全新重放是 `#1f77b4`，那个比较器
    比过 18 个元素、报 **0 处分歧**。

    一个自称在验等价性、却看不见颜色的基准，比没有基准更坏——它会给出
    「四路一致」的结论，而四路里有一路的颜色是错的。
    """
    same = {"elements": [{"gid": "axes_0.lines_0",
                          "editable": [{"prop": "color", "value": "#123456"}]}]}
    other = {"elements": [{"gid": "axes_0.lines_0",
                           "editable": [{"prop": "color", "value": "#654321"}]}]}
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
        f"钉的是 {spec['python']}——宣称与验证之间会留下一段没人走过的路")


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
        r = {"x": {"id": "x", "tier": "expected", "classification": "full_support",
                   "stages": {s: True for s in CC.STAGES},
                   "browser": {"ok": parity_ok, "reason": "角色不一致：['line']"}}}
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
        r = {"x": {"id": "x", "tier": "expected", "classification": "full_support",
                   "stages": {s: True for s in CC.STAGES}, "browser": None}}
        assert CM.evaluate_gate("release", [c], r, {"cases": {}})[0]


def test_browser_verdict_compares_editable_sets():
    """对拍要比可编辑属性集合——文档从第一版起就是这么写的。

    只比角色的话，浏览器侧多出或少掉任何一个属性、只要角色不变，这条就报
    成功。**文档说的和代码做的不是一回事，比两边都不做更坏。**
    """
    c = case("x")
    c["mutations"] = []
    desktop = {"detail": {"semantic": {"roles": ["axes", "line"],
                                       "editable_all": ["axes_0.lines_0.color",
                                                        "axes_0.lines_0.linewidth"]}}}
    same = {"ok": True, "figures": ["s"], "semantics": {"s": {
        "roles": ["axes", "line"],
        "editable": ["axes_0.lines_0.color", "axes_0.lines_0.linewidth"]}}}
    assert CM._browser_verdict(c, same, desktop)["ok"]

    fewer = {"ok": True, "figures": ["s"], "semantics": {"s": {
        "roles": ["axes", "line"], "editable": ["axes_0.lines_0.color"]}}}
    v = CM._browser_verdict(c, fewer, desktop)
    assert not v["ok"] and "可编辑属性不一致" in v["reason"]
    assert v["editable_only_desktop"] == ["axes_0.lines_0.linewidth"]
