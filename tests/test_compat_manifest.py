"""CompatBench 语料层的看护：清单 / 版本矩阵 / 基线的结构与纪律。

每条用例钉的都是「坏掉之后会怎样」，而不是「正常时能跑通」：

* case id 重复 → 报告里两条都在、基线只认一条，「明明修好了却还是红」；
* 非 full_support 却没写理由 → 下一个人无法判断这条例外还该不该存在，
  于是被无限期沿用；
* 基线缺失自动当成空 → 第一次跑永远通过，什么都没验证；
* CI 能自己更新基线 → 「红了就把期望改掉」，门禁从此只证明我们接受现状；
* `expected.execute=false` → 把门禁关掉最省事的办法；
* 版本矩阵里复制版本号 → 某次升级之后 CI 验的和用户拿到的不是同一个。

纯标准库，不需要 matplotlib——语料层本来就要在还没装科学栈的机器上跑得起来。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

CI_DIR = Path(__file__).resolve().parents[1] / "scripts" / "ci"
sys.path.insert(0, str(CI_DIR))

import compat_corpus as CC  # noqa: E402


@pytest.fixture(scope="module")
def manifest() -> dict:
    return CC.load_manifest()


@pytest.fixture
def one_case(manifest) -> dict:
    return copy.deepcopy(manifest["cases"][0])


def _doc(cases: list[dict]) -> dict:
    return {"schema": 1, "cases": cases}


# ============================================================ 清单结构
class TestManifestShape:
    def test_the_real_manifest_loads_and_validates(self, manifest):
        assert len(manifest["cases"]) >= 60, "CompatBench 的第一版就不该只有几个冒烟"

    def test_every_script_and_asset_actually_exists(self, manifest):
        for c in manifest["cases"]:
            assert (CC.COMPAT_DIR / c["script"]).is_file(), c["id"]
            for a in c.get("assets") or []:
                assert (CC.ASSETS_DIR / a).is_file(), f"{c['id']}: {a}"

    def test_duplicate_case_id_is_rejected(self, one_case):
        dup = copy.deepcopy(one_case)
        with pytest.raises(CC.CorpusError) as exc:
            CC.validate_manifest(_doc([one_case, dup]))
        assert exc.value.code == "duplicate_case_id"

    def test_missing_script_is_rejected(self, one_case):
        one_case["script"] = "cases/script_shapes/does_not_exist.py"
        with pytest.raises(CC.CorpusError) as exc:
            CC.validate_manifest(_doc([one_case]))
        assert exc.value.code == "script_not_found"

    def test_unknown_tier_is_rejected(self, one_case):
        one_case["tier"] = "critical"
        with pytest.raises(CC.CorpusError) as exc:
            CC.validate_manifest(_doc([one_case]))
        assert exc.value.code == "unknown_tier"

    def test_unknown_classification_is_rejected(self, one_case):
        one_case["classification"] = "mostly_fine"
        with pytest.raises(CC.CorpusError) as exc:
            CC.validate_manifest(_doc([one_case]))
        assert exc.value.code == "unknown_classification"

    def test_unknown_stage_is_rejected(self, one_case):
        one_case["expected"] = {"teleport": True}
        with pytest.raises(CC.CorpusError) as exc:
            CC.validate_manifest(_doc([one_case]))
        assert exc.value.code == "unknown_stage"

    def test_unknown_discovery_mode_is_rejected(self, one_case):
        one_case["discovery"] = "magic"
        with pytest.raises(CC.CorpusError) as exc:
            CC.validate_manifest(_doc([one_case]))
        assert exc.value.code == "unknown_discovery"

    def test_too_many_mutation_targets_is_rejected(self, one_case):
        one_case["mutations"] = [{"gid": "g", "prop": f"p{i}", "value": 1}
                                 for i in range(6)]
        with pytest.raises(CC.CorpusError) as exc:
            CC.validate_manifest(_doc([one_case]))
        assert exc.value.code == "too_many_mutations"


# ============================================================ 例外必须有理由
class TestExceptionsNeedReasons:
    @pytest.mark.parametrize("cls", CC.NEEDS_REASON)
    def test_every_non_full_classification_needs_a_reason(self, one_case, cls):
        one_case["classification"] = cls
        one_case["tier"] = "expected"          # must 不许有 product_bug
        one_case.pop("reason", None)
        one_case.pop("follow_up", None)
        with pytest.raises(CC.CorpusError) as exc:
            CC.validate_manifest(_doc([one_case]))
        assert exc.value.code == "reason_required"

    def test_blank_reason_does_not_count(self, one_case):
        one_case["classification"] = "partial_support"
        one_case["reason"] = "   \n  "
        with pytest.raises(CC.CorpusError) as exc:
            CC.validate_manifest(_doc([one_case]))
        assert exc.value.code == "reason_required"

    def test_product_bug_additionally_needs_a_follow_up(self, one_case):
        """`product_bug` 是**待修缺陷**，不是可以长期接受的状态。"""
        one_case["classification"] = "product_bug"
        one_case["tier"] = "expected"
        one_case["reason"] = "撤销回不到原值"
        one_case.pop("follow_up", None)
        with pytest.raises(CC.CorpusError) as exc:
            CC.validate_manifest(_doc([one_case]))
        assert exc.value.code == "follow_up_required"

    def test_tier1_may_not_declare_a_product_bug(self, one_case):
        """Tier 1 是标准 matplotlib 的高频路径，有 bug 就是发不了版。"""
        one_case["tier"] = "must"
        one_case["classification"] = "product_bug"
        one_case["reason"] = "x"
        one_case["follow_up"] = "y"
        with pytest.raises(CC.CorpusError) as exc:
            CC.validate_manifest(_doc([one_case]))
        assert exc.value.code == "tier1_product_bug"

    def test_declaring_a_stage_false_needs_a_reason(self, one_case):
        one_case["expected"] = {"fidelity": False}
        one_case.pop("expected_false_reasons", None)
        with pytest.raises(CC.CorpusError) as exc:
            CC.validate_manifest(_doc([one_case]))
        assert exc.value.code == "expected_false_reason_required"

    @pytest.mark.parametrize("stage", CC.NON_NEGOTIABLE_STAGES)
    def test_execute_capture_open_can_never_be_declared_false(self, one_case, stage):
        """把门禁关掉最省事的办法就是声明「本来就没期望它过」。

        这三级任何 tier 都不许关：跑不起来 / 捕获不到 / 打不开就是不兼容，
        理由再充分也得记成 classification，让它出现在报告里。
        """
        one_case["expected"] = {stage: False}
        one_case["expected_false_reasons"] = {stage: "我们不想验这个"}
        with pytest.raises(CC.CorpusError) as exc:
            CC.validate_manifest(_doc([one_case]))
        assert exc.value.code == "stage_not_negotiable"

    def test_real_manifest_exceptions_all_carry_reasons(self, manifest):
        """真实清单里的每一条例外都读得懂。"""
        for c in manifest["cases"]:
            cls = c.get("classification", "full_support")
            if cls in CC.NEEDS_REASON:
                assert len(str(c.get("reason", "")).strip()) >= 20, (
                    f"{c['id']} 的 reason 太短，说不清为什么")
            if cls in CC.NEEDS_FOLLOW_UP:
                assert len(str(c.get("follow_up", "")).strip()) >= 20, c["id"]
            for stage, want in (c.get("expected") or {}).items():
                if not want:
                    reason = (c.get("expected_false_reasons") or {}).get(stage, "")
                    assert len(str(reason).strip()) >= 20, f"{c['id']}.{stage}"


# ============================================================ 覆盖面
class TestCoverage:
    def test_smoke_subset_is_not_empty_and_is_pr_sized(self, manifest):
        smoke = CC.select(manifest["cases"], smoke=True)
        assert smoke, "smoke 子集空 = PR 上没有兼容门禁"
        assert len(smoke) <= 30, (
            f"smoke 子集 {len(smoke)} 个太大——PR 档的目标是几分钟内跑完，"
            f"塞进整套 corpus 只会让人开始跳过它")

    def test_smoke_covers_every_tier1_category(self, manifest):
        must_cats = {c["category"] for c in manifest["cases"] if c["tier"] == "must"}
        smoke_cats = {c["category"] for c in manifest["cases"] if c.get("smoke")}
        assert must_cats <= smoke_cats

    def test_required_tier1_dimensions_exist(self, manifest):
        """Tier 1 必须真的盖住标准 matplotlib 的高频路径。

        这条防的是「把碍事的 case 降级成 exploratory 让门禁松一档」。
        """
        must = {c["id"] for c in manifest["cases"] if c["tier"] == "must"}
        for needed in ("art_plot", "art_scatter", "art_bar", "art_hist",
                       "art_imshow", "art_legend", "art_colorbar",
                       "art_text_annotate", "ax_subplots", "ax_tight_layout",
                       "shape_pyplot_show_only", "shape_oo_savefig",
                       "shape_no_savefig_multi"):
            assert needed in must, f"{needed} 不在 Tier 1 里"

    def test_browser_subset_is_not_empty(self, manifest):
        assert len(CC.select(manifest["cases"], browser_only=True)) >= 10

    def test_browser_subset_only_holds_scripts_the_playground_can_run(self, manifest):
        """浏览器 playground 没有注册表、也只收单个文件。

        它把上传的 .py 按 `python figure.py` 跑一遍，所以：
        * 只有 `def main():` 而没人调用的脚本在**原生 Python 下也不画图**，
          那边捕获不到是对的（桌面的 entry 机制是超集）；
        * 需要数据文件 / 本地 helper 的脚本在那边根本没有那些文件。

        标错的后果是对拍恒红，然后这条门禁被当成噪音关掉。
        """
        for c in manifest["cases"]:
            if not c.get("browser_eligible"):
                continue
            src = (CC.COMPAT_DIR / c["script"]).read_text(encoding="utf-8")
            assert c.get("entry", "main") == "__main__" or "if __name__ ==" in src, \
                f"{c['id']} 作为脚本跑不出图，不该进浏览器对拍"
            assert not c.get("assets"), f"{c['id']} 要数据文件，playground 没有"
            assert not c.get("extra_files"), f"{c['id']} 要本地 helper"

    def test_browser_subset_covers_the_no_savefig_shape(self, manifest):
        """§20 那条硬要求：`plt.plot + plt.show`（无 savefig）必须在两个入口
        都能捕获，所以它必须真的在对拍子集里。"""
        ids = {c["id"] for c in CC.select(manifest["cases"], browser_only=True)}
        assert {"shape_pyplot_show_only", "shape_no_savefig_multi"} <= ids

    def test_every_category_is_represented(self, manifest):
        cats = {c["category"] for c in manifest["cases"]}
        assert cats == {"script_shapes", "core_artists", "axes_layout",
                        "scientific_stack", "metamorphic"}

    def test_metamorphic_families_have_several_variants(self, manifest):
        """同一张视觉结果 × 不同写法：家族数与每族的变体数都要够。"""
        fams: dict[str, set[str]] = {}
        for c in manifest["cases"]:
            if c["category"] != "metamorphic":
                continue
            _mm, fam, variant = c["id"].split("_", 2)
            fams.setdefault(fam, set()).add(variant)
        assert len(fams) >= 8, f"语义家族只有 {len(fams)} 个"
        for fam, variants in fams.items():
            assert len(variants) >= 3, f"{fam} 只有 {len(variants)} 个变体"

    def test_no_case_is_a_pure_duplicate(self, manifest):
        """不许为了凑数字造没有区别的复制品。"""
        seen: dict[tuple, str] = {}
        for c in manifest["cases"]:
            key = (c["script"], c["stem"], json.dumps(c["mutations"], sort_keys=True))
            assert key not in seen, f"{c['id']} 与 {seen[key]} 完全一样"
            seen[key] = c["id"]


# ============================================================ 版本矩阵
class TestVersionMatrix:
    def test_matrix_loads(self):
        assert CC.load_matrix()["targets"]

    def test_every_source_points_at_a_real_lock_file(self):
        for name, spec in CC.load_matrix()["targets"].items():
            if spec.get("source"):
                assert (CC.REPO / spec["source"]).is_file(), name

    def test_versions_are_read_from_the_lock_not_copied(self):
        """带 source 的 target **不许**再写死版本号——版本真相只能有一份。"""
        m = CC.load_matrix()
        with pytest.raises(CC.CorpusError) as exc:
            bad = copy.deepcopy(m)
            bad["targets"]["bundled"]["matplotlib"] = "3.11.1"
            CC.validate_matrix(bad)
        assert exc.value.code == "target_duplicates_version"

    def test_bundled_target_resolves_to_the_runtime_lock(self):
        m = CC.load_matrix()
        got = CC.resolve_target(m, "bundled")
        lock = json.loads(
            (CC.REPO / "packaging" / "runtime-lock.json").read_text(encoding="utf-8"))
        target = next(iter(lock["targets"].values()))
        raw = target["packages"]
        # 锁文件的 packages 两种形状都合法（dict 或 [{name, version}]），
        # `_versions_from_lock` 两种都认——这里照样两种都接，免得这条用例
        # 在一次纯格式调整里变红。
        want = ({k.lower(): v for k, v in raw.items()} if isinstance(raw, dict)
                else {p["name"].lower(): p["version"] for p in raw})
        assert got["matplotlib"] == want["matplotlib"]

    def test_browser_target_resolves_to_the_playground_lock(self):
        got = CC.resolve_target(CC.load_matrix(), "browser")
        lock = json.loads((CC.REPO / "packaging" / "playground-runtime.json")
                          .read_text(encoding="utf-8"))
        assert got["matplotlib"] == lock["packages"]["matplotlib"]
        assert got["pyodide"] == lock["pyodide_version"]

    def test_minimum_target_pins_an_exact_version(self):
        """`pip install matplotlib>=3.8` 在 CI 上等于装 latest，那一档什么都没验。"""
        spec = CC.resolve_target(CC.load_matrix(), "minimum")
        assert spec["matplotlib"][0].isdigit()
        assert ">" not in spec["matplotlib"] and "*" not in spec["matplotlib"]

    def test_a_required_target_without_versions_is_rejected(self):
        m = copy.deepcopy(CC.load_matrix())
        m["targets"]["sloppy"] = {"required": True}
        with pytest.raises(CC.CorpusError) as exc:
            CC.validate_matrix(m)
        assert exc.value.code == "target_no_version"

    def test_a_matrix_that_requires_nothing_is_rejected(self):
        m = copy.deepcopy(CC.load_matrix())
        for spec in m["targets"].values():
            spec["required"] = False
        with pytest.raises(CC.CorpusError) as exc:
            CC.validate_matrix(m)
        assert exc.value.code == "matrix_nothing_required"

    def test_minimum_target_matches_what_the_package_claims(self):
        """宣称的下界与矩阵里验的那一档必须对得上。

        pyproject 说 `matplotlib>=3.8`，矩阵就得真有一档 3.8.x 在跑——
        否则「支持 3.8」只是一句没人验过的话。
        """
        pyproject = (CC.REPO / "pyproject.toml").read_text(encoding="utf-8")
        assert "matplotlib>=3.8" in pyproject, (
            "pyproject 的下界改过了，tests/compat/matrix.json 的 minimum "
            "档要跟着改（改之前先确认新下界真的能装到）")
        assert CC.resolve_target(CC.load_matrix(), "minimum")[
            "matplotlib"].startswith("3.8.")


# ============================================================ 基线纪律
class TestBaselineDiscipline:
    def test_missing_baseline_is_a_failure_not_an_empty_baseline(self, tmp_path):
        with pytest.raises(CC.CorpusError) as exc:
            CC.load_baseline(tmp_path / "nope.json")
        assert exc.value.code == "baseline_missing"

    def test_baseline_entry_without_reason_is_rejected(self):
        with pytest.raises(CC.CorpusError) as exc:
            CC.validate_baseline({"schema": 1, "cases": {
                "x": {"classification": "partial_support", "stages": {}}}})
        assert exc.value.code == "reason_required"

    def test_baseline_product_bug_without_follow_up_is_rejected(self):
        """把已知缺陷记进基线是为了**看住**它，不是为了接受它。"""
        with pytest.raises(CC.CorpusError) as exc:
            CC.validate_baseline({"schema": 1, "cases": {
                "x": {"classification": "product_bug", "stages": {},
                      "reason": "撤销回不去"}}})
        assert exc.value.code == "follow_up_required"

    def test_baseline_may_not_carry_timestamps(self):
        """时间戳每次都变，会把真正的分类变化淹没在 diff 噪音里。"""
        with pytest.raises(CC.CorpusError) as exc:
            CC.validate_baseline({"schema": 1, "generated_at": "2026-08-21",
                                  "cases": {"x": {"classification": "full_support",
                                                  "stages": {}}}})
        assert exc.value.code == "baseline_has_timestamp"

    def test_baseline_payload_is_deterministic(self):
        results = {
            "b": {"classification": "full_support", "stages": {"execute": True}},
            "a": {"classification": "partial_support", "stages": {"open": True},
                  "reason": "只认得一半"},
        }
        first = CC.baseline_payload(results)
        second = CC.baseline_payload(dict(reversed(list(results.items()))))
        assert json.dumps(first, sort_keys=False) == json.dumps(second, sort_keys=False)
        assert list(first["cases"]) == ["a", "b"], "基线的 case 必须按 id 排序"

    def test_baseline_payload_keeps_stage_order_stable(self):
        payload = CC.baseline_payload({"x": {
            "classification": "full_support",
            "stages": {"export": True, "execute": True, "open": False}}})
        assert list(payload["cases"]["x"]["stages"]) == ["execute", "open", "export"]

    def test_diff_reports_new_missing_and_changed(self):
        base = {"cases": {"a": {"classification": "full_support", "stages": {}},
                          "gone": {"classification": "full_support", "stages": {}}}}
        results = {"a": {"classification": "product_bug", "stages": {}},
                   "fresh": {"classification": "full_support", "stages": {}}}
        d = CC.diff_baseline(base, results)
        assert d["new"] == ["fresh"]
        assert d["missing"] == ["gone"]
        assert [c["id"] for c in d["changed"]] == ["a"]

    def test_committed_baseline_is_valid_and_covers_the_corpus(self, manifest):
        """仓库里那份基线必须与清单对得上——两边漂开的表现是「新加的 case
        永远报 new」或者「删掉的 case 永远报 missing」，然后没人再看这条差异。"""
        baseline = CC.load_baseline()
        ids = {c["id"] for c in manifest["cases"]}
        extra = set(baseline["cases"]) - ids
        assert not extra, f"基线里有清单里不存在的 case：{sorted(extra)}"
        missing = ids - set(baseline["cases"])
        assert not missing, (
            f"清单里这些 case 不在基线里：{sorted(missing)[:10]}。"
            f"本地跑 --all --update-baseline，逐条读过再提交")


# ============================================================ CI 接线
class TestCiWiring:
    """门禁接上了没有——**没接上的门禁与空转的门禁一样坏**，而且更安静。"""

    def _wf(self, name: str) -> str:
        return (CC.REPO / ".github" / "workflows" / name).read_text(encoding="utf-8")

    def test_pr_ci_runs_the_smoke_subset(self):
        wf = self._wf("ci.yml")
        assert "compat_matrix.py --smoke" in wf.replace("\\\n", "").replace("  ", " ")
        assert "--gate pr" in wf

    def test_pr_ci_pins_the_scientific_stack_from_the_lock(self):
        """PR 档也必须用锁文件的版本。`pip install matplotlib` 在这里等于
        「每天换一个 matplotlib 验兼容性」，零 patch 保真度会随机飘红。"""
        wf = self._wf("ci.yml")
        assert "runtime_pins.py" in wf

    def test_lab_ci_runs_compat_at_every_depth(self):
        wf = self._wf("lab-ci.yml")
        assert "compat_matrix.py" in wf
        for gate in ("--gate main", "--gate release", "--gate nightly"):
            assert gate in wf, gate

    def test_release_gate_uses_the_strictest_setting(self):
        """发行档是唯一连基线里已知的 product_bug 都不放过的一档。"""
        wf = self._wf("release.yml")
        assert "compat_matrix.py" in wf and "--gate release" in wf

    def test_nightly_runs_the_version_matrix(self):
        wf = self._wf("nightly.yml")
        assert "compat-version-matrix" in wf
        for target in ("minimum", "bundled", "browser"):
            assert f"target: {target}" in wf, target

    def test_ci_never_passes_update_baseline(self):
        """workflow 里出现 `--update-baseline` 就等于「红了自动改期望」。

        runner 自己还有一道 `CI=true` 硬拒，但那是第二道闸——第一道是
        这些文件里根本不该有这个参数。
        """
        for name in ("ci.yml", "lab-ci.yml", "nightly.yml", "release.yml"):
            assert "--update-baseline" not in self._wf(name), name


class TestReasonsAreReviewable:
    """基线是**给人读的**。它的价值全部来自「有人真的读过」。"""

    def test_no_reason_points_at_another_entry(self, manifest):
        """不许写「同上」。

        基线按 id 排序，manifest 与 baseline 的顺序都不是写作顺序——
        「同上」在成品里指向的是随机的另一条，读的人只能放弃。
        """
        for c in manifest["cases"]:
            assert "同上" not in str(c.get("reason", "")), c["id"]

    def test_baseline_stays_small_enough_to_read(self):
        """基线里不许塞整轮诊断原文。

        `detail` 是每个 case 几 KB 的 manifest 摘要 / 像素指标 / 导出字节数，
        它属于 compat-report.json。塞进基线的后果是一份没人愿意在 review 里
        打开的文件——而那等于基线纪律失效。
        """
        size = CC.BASELINE_PATH.stat().st_size
        assert size < 200_000, f"基线 {size} 字节，太大了没人会读"
        entry = next(iter(CC.load_baseline()["cases"].values()))
        assert set(entry) <= {"classification", "stages", "reason", "follow_up",
                              "note", "stage"}, entry.keys()

    def test_baseline_records_the_environment_it_was_taken_on(self):
        """分类会随 matplotlib 版本变。不写下来的话「基线对不上」永远查不出
        是产品变了还是环境变了。"""
        gen = CC.load_baseline().get("generated_for") or {}
        assert gen.get("target"), "基线没写它是在哪个 target 上采的"
        assert gen.get("matplotlib"), "基线没写 matplotlib 版本"

    def test_baseline_target_is_the_one_users_get(self):
        """基线应当描述**用户拿到的那套环境**（内置 runtime），不是某台开发机
        上碰巧装着的版本。"""
        gen = CC.load_baseline()["generated_for"]
        assert gen["target"] == "bundled"
        assert gen["matplotlib"] == CC.resolve_target(
            CC.load_matrix(), "bundled")["matplotlib"]


def test_no_case_asserts_a_limitation_that_no_longer_exists(manifest):
    """清单里的 reason 不许描述**已经被修掉**的限制。

    这条是 codex 审查抓到的：`ax_secondary_x` 的 reason 一直写着
    「`_cls_key()` 对 SecondaryAxis 回 None，容器字段一个都出不来」，而同一个
    PR 里那一行已经放宽成 `_AxesBase` 了。报告因此在**发布一条假的限制**——
    对一个存在意义就是「让 Tavotto 说真话」的基准来说，这是最不能有的失误。

    静态判据：reason 里如果点名了某个函数「返回 None / 不认得」，那个说法必须
    在源码里仍然成立。这里只查这一条已知的，加新说法时照此扩充。
    """
    src = (CC.REPO / "src" / "tavotto" / "engine" / "overrides.py").read_text(
        encoding="utf-8")
    cls_key_takes_axesbase = "isinstance(artist, _AxesBase)" in src
    for c in manifest["cases"]:
        reason = str(c.get("reason", ""))
        if "_cls_key" in reason and "回 None" in reason:
            assert not cls_key_takes_axesbase, (
                f"{c['id']} 的 reason 说 _cls_key 回 None，但源码里它已经认"
                f"_AxesBase 了——这条限制不存在了，reason 要跟着改")


def test_child_axes_cases_assert_on_the_child_itself(manifest):
    """子 axes 的 case 必须在**子 axes 上**有断言。

    只盯宿主轴的元素（`axes_0.lines_0`）的话，子 axes 那部分支持悄悄回退了，
    这条 case 照样全绿——它一度就是这样，而 reason 里还写着一条早已不成立的
    限制。断言要落在这条 case 声称自己在验的那个东西上。
    """
    for cid in ("ax_secondary_x", "ax_secondary_y", "ax_inset"):
        c = next(x for x in manifest["cases"] if x["id"] == cid)
        targets = {gid for gid, _p in c["semantic_expectations"]["editable"]}
        targets |= {m["gid"] for m in c.get("mutations") or []}
        assert any(g.startswith("axes_1") for g in targets), (
            f"{cid} 的断言全落在宿主轴上，子 axes 回退了它也不会红：{sorted(targets)}")


def test_fallback_only_cases_are_not_claimed_as_full_support(manifest):
    """**没有磁盘产物的 stem 不许报「完全支持」。**

    `plt.show()` 出来的图在桌面界面上够不着：面板列表按文件扫、
    `analyze_script()` 对没有存图调用的脚本回 None、注册表对话框只给候选与
    已登记的脚本「试运行」按钮，没有「任选一个脚本」的入口。而 CompatBench
    走的是 `probe_and_register()` + worker——**它绕过了那条产品入口**。

    据此报「完全支持」就是拿基准替产品打掩护，而这个基准存在的全部意义是
    让 Tavotto 说真话。这一条是结构性的：判据不是某个 case 的名字，而是
    「这个脚本有没有存图调用」——将来加同类 case 时它自动生效。

    引擎阶段照常验（它们是真的），只有 `classification` 受这条约束。
    """
    import ast
    offenders = []
    for c in manifest["cases"]:
        src = (CC.COMPAT_DIR / c["script"]).read_text(encoding="utf-8")
        tree = ast.parse(src)
        saves = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and getattr(n.func, "attr", "") in ("savefig", "save")]
        if saves:
            continue                       # 有存图调用 → 磁盘上会有产物
        if c.get("classification", "full_support") == "full_support":
            offenders.append(c["id"])
    assert not offenders, (
        "这些 case 的脚本一次都不存盘，桌面界面上打不开它们，却被记成"
        f"「完全支持」：{offenders}")
