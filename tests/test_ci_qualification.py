"""实验室 qualification 脚本的看护：视觉回归 / 性能门禁 / soak 泄漏判定 / 升级。

这几条是整套 lab CI 里**最容易悄悄退化成摆设**的部分，所以每条用例都尽量
钉住「坏掉之后会怎样」而不是「正常时能跑通」：

* 基线缺失自动补一份 → 门禁永远绿；
* 阈值把噪声也算成回归 → 门禁天天红，然后被人忽略；
* 候选版把基线覆盖掉 → 「和基线比」变成「和自己比」；
* 泄漏判定拿终值而不是斜率 → Python 分配器的高水位被误报成泄漏。

需要 numpy/Pillow 的用例在缺依赖时**跳过并注明理由**（它们在 `[ci]` extras 里，
普通开发环境不装）；不需要的那些一律平台无关。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

CI_DIR = Path(__file__).resolve().parents[1] / "scripts" / "ci"
sys.path.insert(0, str(CI_DIR))
sys.path.insert(0, str(CI_DIR.parent))

import _common  # noqa: E402
import benchmark as BM  # noqa: E402
import release_blockers as RB  # noqa: E402
import soak as SK  # noqa: E402
import upgrade_acceptance as UA  # noqa: E402
import visual_regression as VR  # noqa: E402

try:
    import numpy  # noqa: F401
    from PIL import Image  # noqa: F401

    HAS_IMAGING = True
except ImportError:
    HAS_IMAGING = False

needs_imaging = pytest.mark.skipif(
    not HAS_IMAGING, reason="视觉回归需要 numpy 与 Pillow（pip install -e '.[ci]'）"
)


# ============================================================ 视觉回归
class TestVisualManifest:
    def test_manifest_parses_and_covers_real_corpus(self):
        """清单里的每个 case 都必须对应一个真实存在的 corpus 脚本。"""
        m = VR.load_manifest()
        assert m["cases"], "验收清单是空的"
        for stem, case in m["cases"].items():
            script = VR.CORPUS / case["script"]
            assert script.is_file(), f"{stem} 指向的 {script} 不存在"

    def test_registry_stems_match_manifest(self):
        """注册表与验收清单必须逐条对齐。

        对不上的表现极其误导：注册表里没有的 stem 根本不会被扫出来，
        视觉回归会报「corpus_stem_missing」，而人第一反应是去查渲染。
        """
        reg = json.loads((VR.CORPUS / "tavotto_registry.json").read_text(encoding="utf-8"))
        reg_stems = {s for spec in reg["scripts"].values() for s in spec["stems"]}
        manifest_stems = set(VR.load_manifest()["cases"])
        assert reg_stems == manifest_stems, (
            f"注册表独有 {reg_stems - manifest_stems}；清单独有 {manifest_stems - reg_stems}"
        )

    def test_every_visual_exception_states_a_reason(self):
        """任何放宽或跳过都必须写明理由。

        没有理由的例外，下一个人无法判断它还该不该存在，最终只会被无限期沿用。
        """
        m = VR.load_manifest()
        for stem, case in m["cases"].items():
            if case.get("visual") is False:
                assert case.get("visual_skip_reason", "").strip(), f"{stem} 跳过像素比对却没写理由"
            if "tolerance" in case:
                assert case.get("tolerance_reason", "").strip(), f"{stem} 放宽阈值却没写理由"

    def test_render_width_is_a_real_bucket(self):
        """渲染宽度必须命中服务端的 bucket。

        否则服务端会向上取整到另一档，基线与候选在不同分辨率下比较，
        每次都是 size_mismatch，而原因完全看不出来。
        """
        buckets = [200, 400, 800, 1600, 3200]  # 与 app.RENDER_BUCKETS 同源
        assert VR.RENDER_WIDTH in buckets

    def test_tolerance_merge_keeps_case_override(self):
        m = VR.load_manifest()
        default = VR.case_tolerance(m, "c01_line")
        relaxed = VR.case_tolerance(m, "c02_constrained")
        assert relaxed["changed_pixel_ratio"] > default["changed_pixel_ratio"]
        assert "_comment" not in default, "注释字段混进了判定阈值"


@needs_imaging
class TestVisualComparison:
    def _png(self, tmp_path, arr, name):
        import numpy as np
        from PIL import Image

        p = tmp_path / name
        Image.fromarray(np.clip(arr, 0, 255).astype("uint8"), mode="L").save(p)
        return p

    def _base(self):
        import numpy as np

        a = np.full((160, 240), 240, dtype="uint8")
        a[40:120, 50:190] = 30
        return a

    def test_identical_images_pass(self, tmp_path):
        b = self._base()
        m = VR.compare(self._png(tmp_path, b, "a.png"), self._png(tmp_path, b, "b.png"), None)
        assert m["changed_pixel_ratio"] == 0.0 and m["max_abs_diff"] == 0

    def test_antialias_noise_does_not_trip(self, tmp_path):
        """±2 的逐像素抖动是抗锯齿与 PNG 量化的正常产物，不能算回归。

        这条曾经真的红过：`mean_abs_diff` 当时在全图上算，遍布全图的底噪
        就足以顶穿阈值，而画面一模一样。
        """
        import numpy as np

        b = self._base()
        noisy = b.astype("int16") + np.tile([0, 2, -2, 1, -1], (160, 48))
        m = VR.compare(self._png(tmp_path, b, "a.png"), self._png(tmp_path, noisy, "n.png"), None)
        tol = VR.case_tolerance(VR.load_manifest(), "c01_line")
        ok, why = VR.verdict(m, tol)
        assert ok, f"抗锯齿噪声被误判为回归：{why}"

    def test_moved_element_is_caught(self, tmp_path):
        """元素挪了几个像素必须变红——这正是要抓的那类回归。"""
        import numpy as np

        b = self._base()
        moved = np.full((160, 240), 240, dtype="uint8")
        moved[46:126, 50:190] = 30
        m = VR.compare(self._png(tmp_path, b, "a.png"), self._png(tmp_path, moved, "m.png"), None)
        ok, why = VR.verdict(m, VR.case_tolerance(VR.load_manifest(), "c01_line"))
        assert not ok and why

    def test_colour_shift_is_caught(self, tmp_path):
        """整体亮度偏移（改了配色）也必须抓到。"""
        b = self._base()
        m = VR.compare(
            self._png(tmp_path, b, "a.png"),
            self._png(tmp_path, b.astype("int16") + 10, "c.png"),
            None,
        )
        ok, _ = VR.verdict(m, VR.case_tolerance(VR.load_manifest(), "c01_line"))
        assert not ok

    def test_size_mismatch_is_a_regression_not_a_crash(self, tmp_path):
        import numpy as np

        b = self._base()
        small = np.full((140, 240), 240, dtype="uint8")
        m = VR.compare(self._png(tmp_path, b, "a.png"), self._png(tmp_path, small, "s.png"), None)
        ok, why = VR.verdict(m, VR.case_tolerance(VR.load_manifest(), "c01_line"))
        assert not ok and "尺寸" in why[0]

    def test_diff_image_is_written_on_change(self, tmp_path):
        """失败时必须给出 diff 图——只报一个数字，开发者只会重跑一次了事。"""
        import numpy as np

        b = self._base()
        moved = np.full((160, 240), 240, dtype="uint8")
        moved[46:126, 50:190] = 30
        out = tmp_path / "d.png"
        VR.compare(self._png(tmp_path, b, "a.png"), self._png(tmp_path, moved, "m.png"), out)
        assert out.is_file() and out.stat().st_size > 100


class TestVisualBaselinePolicy:
    def test_update_baselines_is_refused_in_ci(self, monkeypatch, capsys):
        """CI 里绝不允许重建基线。

        「基线不存在 → 自动创建 → 报绿」是这套门禁最容易退化成的样子，
        所以即使有人在 workflow 里手滑加了这个参数，也要在入口拦下。
        """
        monkeypatch.setenv("CI", "true")
        assert VR.main(["--update-baselines"]) == 2
        assert "不允许在 CI 环境使用" in capsys.readouterr().err

    def test_baseline_dir_lives_in_repo_not_state_root(self):
        """基线是需要 review 的资产，必须随代码走。

        放进持久化根的话，它就永远不会出现在任何一次 code review 里——
        谁改了基线、为什么改，全都无从追溯。
        """
        assert VR.BASELINE_DIR.is_relative_to(Path(__file__).resolve().parents[1])
        assert "acceptance" in VR.BASELINE_DIR.parts


# ============================================================ 性能门禁
class TestBenchmarkGate:
    def _baseline(self, metrics):
        return {"metrics": metrics, "metadata": {"sha": "deadbeef", "timestamp": "t"}}

    def test_regression_beyond_threshold_is_flagged(self):
        base = self._baseline({"p::hot_total_ms": 100.0})
        ok, findings = BM.compare(
            {"p::hot_total_ms": 100.0 * (1 + (BM.REGRESSION_PCT + 10) / 100)}, base
        )
        assert not ok
        assert findings[0]["verdict"] == "regression"

    def test_noise_below_threshold_passes(self):
        """阈值以内的波动不能报红，否则这条门禁很快就会被忽略。"""
        base = self._baseline({"p::hot_total_ms": 100.0})
        ok, findings = BM.compare({"p::hot_total_ms": 110.0}, base)  # +10%，低于 25%
        assert ok and findings[0]["verdict"] == "ok"

    def test_improvement_is_reported_not_punished(self):
        base = self._baseline({"p::hot_total_ms": 100.0})
        ok, findings = BM.compare({"p::hot_total_ms": 60.0}, base)
        assert ok and findings[0]["verdict"] == "faster"

    def test_new_metric_does_not_fail_the_build(self):
        """新增面板会带来新指标，它没有历史可比，不该因此变红。"""
        ok, findings = BM.compare({"new::hot_total_ms": 50.0}, self._baseline({"old::x": 1.0}))
        assert ok and findings[0]["verdict"] == "new"

    def test_missing_baseline_is_not_a_pass_disguised_as_comparison(self):
        """没有基线时返回空 findings，调用方据此走「只记录」分支。"""
        ok, findings = BM.compare({"p::x": 1.0}, None)
        assert ok and findings == []

    def test_save_baseline_is_atomic_and_keeps_previous(self, tmp_path):
        _common.ensure_layout(tmp_path)
        BM.save_baseline(tmp_path, {"metrics": {"a": 1.0}, "metadata": {"sha": "v1"}})
        BM.save_baseline(tmp_path, {"metrics": {"a": 2.0}, "metadata": {"sha": "v2"}})
        cur = json.loads(BM.baseline_path(tmp_path).read_text(encoding="utf-8"))
        prev = json.loads(
            BM.baseline_path(tmp_path).with_suffix(".previous.json").read_text(encoding="utf-8")
        )
        assert cur["metrics"]["a"] == 2.0 and prev["metrics"]["a"] == 1.0
        assert not list((tmp_path / "baselines" / "perf").glob("*.tmp"))

    def test_baseline_records_metadata_that_makes_it_comparable(self, tmp_path):
        """没有 SHA / CPU / Python 的历史数字没有长期价值。"""
        _common.ensure_layout(tmp_path)
        BM.save_baseline(
            tmp_path, {"metrics": {"a": 1.0}, "metadata": _common.run_metadata("main")}
        )
        meta = json.loads(BM.baseline_path(tmp_path).read_text(encoding="utf-8"))["metadata"]
        for key in ("sha", "python", "cpu_count", "timestamp", "os"):
            assert key in meta

    def test_contaminated_environment_is_detected(self, monkeypatch):
        """机器忙时必须明确报污染，而不是交出一份没意义的数字。"""
        monkeypatch.setattr(BM, "_load_avg", lambda: 999.0)
        monkeypatch.setattr(BM, "_free_ram_gib", lambda: 64.0)
        clean, facts = BM.check_environment()
        assert not clean and facts["reasons"]

    def test_load_is_judged_per_cpu_not_absolute(self, monkeypatch):
        """16 核上 load=4 是空闲，4 核上 load=4 已经满了。"""
        monkeypatch.setattr(BM, "_free_ram_gib", lambda: 64.0)
        monkeypatch.setattr(BM, "_load_avg", lambda: 4.0)
        monkeypatch.setattr(BM.os, "cpu_count", lambda: 64)
        assert BM.check_environment()[0] is True
        monkeypatch.setattr(BM.os, "cpu_count", lambda: 4)
        assert BM.check_environment()[0] is False

    def test_extract_metrics_skips_warm_cold_and_failed_exports(self):
        """一脚本多产物时，第二个 stem 的『第一次』其实已经是热的。

        把它当冷启动记进基线，会让基线里混进两个数量级不同的数字。
        """
        raw = {
            "rows": [
                {
                    "id": "a.pdf",
                    "really_cold": True,
                    "cold": {"total_ms": 9000},
                    "hot": {"total_ms": 25},
                    "export_wall_ms": 300,
                    "export_ok": True,
                },
                {
                    "id": "b.pdf",
                    "really_cold": False,
                    "cold": {"total_ms": 30},
                    "hot": {"total_ms": 22},
                    "export_wall_ms": 280,
                    "export_ok": False,
                },
            ]
        }
        m = BM.extract_metrics(raw)
        assert "a.pdf::cold_total_ms" in m
        assert "b.pdf::cold_total_ms" not in m, "把热态当成冷启动记进了基线"
        assert "b.pdf::export_ms" not in m, "失败的导出被当成有效测量"


# ============================================================ soak 泄漏判定
class TestSoakLeakDetection:
    def _series(self, n=50, fd=lambda i: 100, rss=lambda i: 500_000, proc=lambda i: 4):
        return [
            {"iteration": i, "fds": fd(i), "rss_kib": rss(i), "processes": proc(i)}
            for i in range(n)
        ]

    def test_stable_run_is_clean(self):
        assert SK.analyse(self._series(fd=lambda i: 100 + i % 3), 5)["verdict"] == "ok"

    def test_linear_fd_growth_is_a_leak(self):
        r = SK.analyse(self._series(fd=lambda i: 100 + i), 5)
        assert r["verdict"] == "leak" and any("FD" in f for f in r["findings"])

    def test_linear_rss_growth_is_a_leak(self):
        r = SK.analyse(self._series(rss=lambda i: 500_000 + i * 10_000), 5)
        assert r["verdict"] == "leak" and any("RSS" in f for f in r["findings"])

    def test_allocator_high_water_mark_is_not_a_leak(self):
        """头几轮 import 科学栈把 RSS 顶上去之后走平——那不是泄漏。

        要求「结束 RSS == 初始 RSS」只会得到一条恒红的门禁。
        """
        r = SK.analyse(self._series(rss=lambda i: 300_000 if i < 5 else 800_000), 5)
        assert r["verdict"] == "ok", f"高水位被误判成泄漏：{r.get('findings')}"

    def test_worker_churn_is_flagged(self):
        r = SK.analyse(self._series(proc=lambda i: 4 + (i % 20)), 5)
        assert r["verdict"] == "leak"

    def test_too_few_samples_is_inconclusive_not_pass(self):
        """样本不足时要如实说『判不了』，不能悄悄算通过。"""
        assert SK.analyse(self._series(n=6), 5)["verdict"] == "inconclusive"

    def test_warmup_samples_are_excluded(self):
        r = SK.analyse(self._series(fd=lambda i: 100 + i), 5)
        assert r["warmup_skipped"] == 5 and r["samples_used"] == 45


# ============================================================ 升级验收
class TestUpgradeAcceptance:
    def test_version_ordering(self):
        assert UA._version_key("v0.10.0") > UA._version_key("v0.9.9")
        assert UA._version_key("0.8.0") == (0, 8, 0)

    def test_prereleases_are_never_chosen_as_baseline(self, monkeypatch):
        """用户不会从一个 rc 升上来，拿它当基线是在验没人走过的路径。"""
        monkeypatch.setattr(
            UA,
            "_api_json",
            lambda url, timeout=60: [
                {"tag_name": "v0.9.0-rc1", "prerelease": True, "draft": False},
                {"tag_name": "v0.7.0", "prerelease": False, "draft": False},
                {"tag_name": "v0.6.0", "prerelease": False, "draft": False},
            ],
        )
        assert UA.resolve_baseline("0.8.0", None) == "v0.7.0"

    def test_baseline_must_be_older_than_candidate(self, monkeypatch):
        monkeypatch.setattr(
            UA,
            "_api_json",
            lambda url, timeout=60: [
                {"tag_name": "v0.9.0", "prerelease": False, "draft": False},
            ],
        )
        with pytest.raises(_common.CiError) as exc:
            UA.resolve_baseline("0.8.0", None)
        assert exc.value.code == "no_baseline_release"

    def test_explicit_tag_wins_without_network(self):
        """显式指定时不该联网——否则离线环境下连覆盖都用不了。"""
        assert UA.resolve_baseline("0.8.0", "v0.5.0") == "v0.5.0"

    def test_project_path_exercises_cjk_and_space(self):
        """中文与空格必须在**主路径**上，而不是单开一个 case。"""
        assert " " in UA.PROJECT_DIRNAME
        assert any("一" <= ch <= "鿿" for ch in UA.PROJECT_DIRNAME)

    def test_traceback_detection_catches_real_shapes(self):
        log = (
            "INFO 启动完成\n"
            "Traceback (most recent call last):\n"
            '  File "x.py", line 1\n'
            "KeyError: 'schema'\n"
        )
        found = UA._tracebacks(log)
        assert len(found) >= 2

    def test_traceback_detection_ignores_ordinary_lines(self):
        """普通日志里出现 error 字样不能算 traceback，否则这条恒红。"""
        assert UA._tracebacks("INFO 渲染完成\nWARN 未找到可选字体\n") == []


# ============================================================ 冒烟
@pytest.mark.parametrize(
    "script",
    [
        "lab_acceptance.py",
        "soak.py",
        "visual_regression.py",
        "benchmark.py",
        "upgrade_acceptance.py",
        "compat_matrix.py",
        "compat_driver.py",
    ],
)
def test_every_lab_script_has_a_working_cli(script):
    """每个脚本都要能 `--help`。

    argparse 写错、顶层 import 崩掉这类问题，等到 nightly 跑起来才发现的话，
    要白等一整轮排队。
    """
    import subprocess

    # 读取侧钉 UTF-8：这些脚本的 help 是中文，而 `text=True` 让父进程按本地
    # 区域解码——Windows 的 cp1252 里 0x81/0x8D/0x8F/0x9D 没有定义，撞上就
    # 把「--help 能不能跑」变成一个解码错误。写的一侧钉了、读的一侧没钉，
    # 等于没钉。
    out = subprocess.run(
        [sys.executable, str(CI_DIR / script), "--help"],
        capture_output=True,
        text=True,
        timeout=120,
        encoding="utf-8",
        errors="replace",
    )
    assert out.returncode == 0, f"{script} --help 失败：{out.stderr[-500:]}"


class TestUpgradeRenameBoundary:
    """跨产品改名边界时的行为。

    2026-08-20 从 Magplot 改名到 Tavotto 选的是**干净断裂**（见
    `src/tavotto/engine/brand.py`）：包名、数据目录、格式标识全换且不做兼容。
    跨越那条边界的「升级」在产品语义上不存在，所以升级验收必须识别出来并
    如实标注——既不能伪装成通过（假绿），也不能报成失败（会让人去修一条
    产品刻意不支持的路径）。
    """

    def test_detects_package_rename(self, tmp_path):
        old = tmp_path / "magplot-0.7.0-py3-none-any.whl"
        new = tmp_path / "tavotto-0.8.0-py3-none-any.whl"
        crossed, why = UA.crosses_rename_boundary(old, new)
        assert crossed
        assert "magplot" in why and "tavotto" in why
        assert "brand.py" in why, "没指出这条决策记录在哪，读的人无从判断该不该修"

    def test_same_package_is_not_a_boundary(self, tmp_path):
        old = tmp_path / "tavotto-0.8.0-py3-none-any.whl"
        new = tmp_path / "tavotto-0.9.0-py3-none-any.whl"
        assert UA.crosses_rename_boundary(old, new)[0] is False

    def test_dist_name_parsing(self, tmp_path):
        assert UA.wheel_dist_name(tmp_path / "tavotto-0.8.0-py3-none-any.whl") == "tavotto"
        # PEP 427 允许分发名里的 '-' 写成 '_'
        assert UA.wheel_dist_name(tmp_path / "some_pkg-1.2.3-py3-none-any.whl") == "some-pkg"

    def test_skip_is_rendered_as_skip_not_pass(self, tmp_path, monkeypatch):
        """汇总里跳过必须显示成跳过。

        渲染成 PASS 会让人以为升级路径验过了，而实际上一次都没跑。
        """
        import summarize as SM

        _common.ensure_layout(tmp_path)
        # **报告要盖上本轮身份**，否则汇总会先把它按「上一轮的陈旧报告」拒掉，
        # 根本走不到「跳过怎么渲染」这一支——而这条用例验的正是后者。
        monkeypatch.setenv("GITHUB_RUN_ID", "777")
        monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
        _common.write_report(
            "upgrade.json",
            {
                "ok": True,
                "skipped": True,
                "reason": "rename_boundary",
                "detail": "跨越了产品改名边界",
                "metadata": {"run_id": "777", "run_attempt": "1"},
            },
            tmp_path,
        )
        monkeypatch.setenv("TAVOTTO_CI_STATE_ROOT", str(tmp_path))
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        text = _detail_for(SM, "upgrade.json", tmp_path)
        assert "跳过" in text and "PASS" not in text

        # 更要紧的是**结果列**：扫读的人先看那一列，一个 ✅ PASS 会让他
        # 以为这项验过了，而实际上一次都没跑。
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            SM.main(["--mode", "release"])
        line = [ln for ln in buf.getvalue().splitlines() if "升级" in ln][0]
        assert "⏭️" in line, f"跳过没有出现在结果列：{line}"
        assert "PASS" not in line, f"跳过被渲染成了 PASS：{line}"


def _detail_for(SM, name, root):
    import json as _json

    data = _json.loads((root / "reports" / name).read_text(encoding="utf-8"))
    return SM._detail(name, data)


# ---------------- slow 门禁的判据 ---------------------------------------------
WORKFLOWS = CI_DIR.parents[1] / ".github" / "workflows"


def _slow_step(name: str = "_lab-qualification.yml") -> str:
    """slow 门禁那一步的正文。

    **默认参数从 `release.yml` 改成了 `_lab-qualification.yml`**：
    发行资格验证已经收敛成唯一一份可复用 workflow（见
    `tests/test_release_workflow_contract.py::test_qualification_is_defined_exactly_once`），
    release.yml 与 lab-ci.yml 现在都只是调用它。
    """
    src = (WORKFLOWS / name).read_text(encoding="utf-8")
    assert "slow / 集成用例" in src, f"{name} 里没有 slow 门禁了"
    step = src.split("slow / 集成用例", 1)[1].split("\n      - name:", 1)[0]
    # 注释与 echo 都要剥掉：解释这段历史、以及打给用户看的报错文案里，必然
    # 出现 `2>/dev/null` 和 `::` 这些字面量。连它们一起判的话，「把原因写清楚」
    # 反而会让用例红——仓库里 test_orphan_check_does_not_rely_on_a_dead_parent_pid
    # 早就踩过一次，这里是第二次。
    keep = []
    for ln in step.splitlines():
        t = ln.lstrip()
        if t.startswith("#") or t.startswith("echo ") or t.startswith("tail "):
            continue
        keep.append(ln)
    return "\n".join(keep)


def test_slow_gate_reads_pytest_exit_code_not_its_human_output():
    """判据不许再是「`--collect-only -q` 里有几行含 `::`」。

    pytest 9 把那段输出改成了按文件汇总（`tests/test_bootstrap.py: 1`，一个
    `::` 都没有）。于是 dependabot 的 8.4.2 → 9.0.3 **静默打断了这道门禁**，
    而症状与真实原因毫不相干：它报「标记被删或 pytest.ini 变了」，可标记好好
    地在 tests/test_bootstrap.py 里。没人发现，是因为当时没有 runner 领得走
    实验室这条通道——v0.9.0 发版时才第一次撞上。

    退出码是 pytest 的稳定契约（5 = EXIT_NOTESTSCOLLECTED，4 = 收集出错），
    面向人的那段文字不是。
    """
    # 只剩一份定义了（见 test_there_is_only_one_copy_of_the_slow_gate）。
    for wf in ("_lab-qualification.yml",):
        step = _slow_step(wf)
        assert 'grep -c "::"' not in step, (
            f"{wf}：又回去数 `::` 了——那是 pytest 打给人看的格式，会随版本变"
        )
        assert "--collect-only" in step, f"{wf}：还是要先确认真的选得中"
        assert "rc=$?" in step, f"{wf}：要按退出码分诊"
        assert "5)" in step, f"{wf}：EXIT_NOTESTSCOLLECTED 那一支不见了"
        # 正面判据：收集的输出要**留下来**。写成「不许出现 2>/dev/null」的
        # 否定形式会被自己的报错文案咬到（那句话里就有这个字面量），而按
        # 「留没留日志」判既准确又不受措辞影响。
        assert "> slow-collect.log 2>&1" in step, (
            f"{wf}：收集的输出没留下来——出错原因会像这次一样整个消失"
        )


def test_there_is_only_one_copy_of_the_slow_gate():
    """**这条用例换过一次判据，理由值得记下来。**

    从前它叫 `test_both_copies_of_the_slow_gate_agree`，逐 token 对拍
    release.yml 与 lab-ci.yml 里那两份手抄的 slow 门禁——因为它们**已经
    漂开过一次**（lab-ci 那份补了「空转的门禁比没有门禁更坏」，release 那份
    没有）。那条用例的原话是：「合成一份 composite action 是 post-1.0 的活；
    在那之前用一条对拍挡住继续分叉。」

    现在那件事做完了：资格验证收敛成 `_lab-qualification.yml` 一份，
    两个调用方都只是 `uses:` 它。**对拍的前提消失了**——没有第二份可拍。
    于是判据从「两份要一致」换成「不许有第二份」：这不是放松，是把
    「为什么需要对拍」那个根因直接拿掉，而拿掉之后仍然留一道门看着它别回来。

    结构性的那半（两个调用方都走同一个文件）由
    `tests/test_release_workflow_contract.py::test_qualification_is_defined_exactly_once`
    看护，这里只钉「别处不许再出现一份」。
    """
    others = []
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        if wf.name == "_lab-qualification.yml":
            continue
        if "slow / 集成用例" in wf.read_text(encoding="utf-8"):
            others.append(wf.name)
    assert not others, (
        f"这些文件里又出现了一份 slow 门禁：{others}。"
        f"资格验证只能有一份定义——两份必然漂开，而漂开的代价是"
        f"发行链上跑的判据与 nightly 上验过的不是同一个"
    )


# ---------------- 升级验收与会话认证 -------------------------------------------


def test_upgrade_acceptance_carries_session_credentials():
    """0.9.0 起浏览器模式也要认证（ADR 0008），这个脚本当时没跟上。

    症状极具迷惑性：`_wait_ready` 打的 `/api/version` 是**公共端点**，所以
    「就绪」永远成立，随后每一个 API 调用 401。v0.9.0 发版时阶段一（0.8.0，
    无认证）一路绿、阶段二（候选）当场 401——而这个脚本在会话认证合并之后
    一次都没跑过，因为那时没有 runner 领得走实验室这条通道。

    **实现不在这里**：装载凭据的判据只有 `smoke_app.adopt_session_credentials`
    一处（`visual_regression` / `soak` 是同一批受害者，见
    `test_every_app_launcher_adopts_credentials`）。这条只钉两件事：起完实例
    真的调了它，以及「取不到凭据」是**继续裸走**而不是失败——`--baseline`
    可以指定任意历史版本，其中大多数早于这道边界。
    """
    src = (CI_DIR / "upgrade_acceptance.py").read_text(encoding="utf-8")
    # 盯**调用点**而不是「文件里出现过这个名字」：把方法改名成
    # `_unused_adopt_credentials` 之类，子串匹配照样成立，而实例起来之后
    # 一次都不会被调用——那正是这条用例要挡的失效形态。
    assert "self._adopt_credentials(port)" in src, "起完实例必须真的调用它，光定义在那儿不算"
    assert "SA.adopt_session_credentials(" in src, "必须走唯一实现，别在这里再写一份"
    body = src.split("def _adopt_credentials", 1)[1].split("\n    def ", 1)[0]
    code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
    assert "else:" in code and "裸走" in code, (
        "取不到凭据要继续裸走（N-1 基线可能早于 ADR 0008），不是失败"
    )


def test_upgrade_acceptance_writes_the_document_shape_the_product_writes():
    """R-18 ①：自动保存端点从 v0.12.0 起就要求顶层 `schema`。

    以前这里 PUT 的是 `{"doc": …, "updatedAt": …}` 包一层的形状——N-1 一开始
    就 400，异常被 except 吞成 `autosave_saved=False`，「自动保存读得回来」
    这条检查**从来没跑过**。钉的是发出去的字节：`json.dumps(doc)`，不许再包。
    """
    src = (CI_DIR / "upgrade_acceptance.py").read_text(encoding="utf-8")
    body = src.split("def write_state_with_old", 1)[1].split("\ndef ", 1)[0]
    code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
    assert "data=json.dumps(doc).encode()" in code, "自动保存要发文档本身，不是 {'doc': …}"
    assert '{"doc": doc}' not in code, "另存为也要发文档本身（前端 saveLayout 就是这么发的）"
    assert '"schema": 2' in code, "文档顶层必须带 schema"


class TestUpgradeAcceptanceReadback:
    """R-18 ②：`/api/layouts` 返回的是字符串列表；读回的文档要真是文档。"""

    def test_layout_names_reads_the_string_list_the_product_returns(self):
        assert UA.layout_names({"layouts": ["升级布局", "另一张"]}) == ["升级布局", "另一张"]

    def test_layout_names_also_accepts_named_entries(self):
        assert UA.layout_names({"layouts": [{"name": "a"}, "b"]}) == ["a", "b"]

    def test_layout_names_refuses_a_shape_it_cannot_read(self):
        with pytest.raises(UA.CiError):
            UA.layout_names({"layouts": "升级布局"})

    def test_document_readback_wants_a_document_that_still_points_at_the_panel(self):
        doc = {"schema": 2, "objects": [{"type": "panel", "id": "Fig1.pdf"}]}
        ok, _ = UA.document_readback(doc, "Fig1.pdf")
        assert ok
        assert not UA.document_readback({"doc": doc}, "Fig1.pdf")[0], "包一层的不是文档"
        assert not UA.document_readback(doc, "Fig9.pdf")[0], "指错图的不算读回"
        assert not UA.document_readback(None, "Fig1.pdf")[0]

    def test_state_the_old_version_failed_to_write_is_a_failed_check_not_a_skip(self):
        """验收问的是「上一版写的这一版读得回来吗」；上一版没写成，问题就没被问到。
        以前 `if facts.get("layout_saved"):` 让整条检查静静消失、报告照旧全绿。"""
        failed = UA.missing_state_checks({"layout_saved": False, "layout_error": "HTTP 400"})
        assert [(n, ok) for n, ok, _ in failed] == [
            ("N-1 写出命名布局", False),
            ("N-1 写出自动保存", False),
        ]
        assert "HTTP 400" in failed[0][2]
        assert UA.missing_state_checks({"layout_saved": True, "autosave_saved": True}) == []


def test_no_app_request_anywhere_skips_auth():
    """**任何**起实例的脚本里，打到应用的请求都必须带上会话凭据。

    这条用例是本轮反复的教训本身。它被 Codex 连着破了三次：

    1. 范围只覆盖 `upgrade_acceptance.py` 一个文件 → `visual_regression`
       的 `_post_png` 漏掉的 `SA._AUTH` 它一点都挡不住；
    2. 判据是裸子串 → 注释里提一句函数名就能满足它；
    3. 目标识别靠 URL 字面量 → `smoke_app._get/_post` 传的是变量 `url`，
       从中心助手里摘掉 `_AUTH` 它照样绿，而下游三个脚本全部 401。

    三次都是同一个病根：**拿文本启发式去判源码结构**。所以改成 AST：
    找出真实的 `urllib.request.Request(...)` 调用，按目标分类，并且**中心
    助手单独钉死**——它们是所有调用方共用的那一层，破了下游全塌。
    """
    import ast

    offenders = []
    for name, src in _app_launchers().items():
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and _is_urllib_request(node.func)):
                continue
            text = ast.unparse(node)
            # **按目标表达式引用了谁分类，不按 URL 里有没有某个子串。**
            # 「URL 含 api.github.com」这种判据 CodeQL 会告（子串可以出现在
            # 任意位置），而且本来就脆——真正要问的是「这个请求打的是本机
            # 实例还是 GitHub」，那是源码结构问题：本机实例的 URL 一律由
            # `base` / `s.base` / `self.base` 拼出来。
            if not _references_local_base(node):
                continue
            if "_AUTH" not in text:
                offenders.append(f"{name}: {text[:150]}")
    assert not offenders, (
        "这些打到应用的请求没带会话凭据，401 的症状会出现在很远的地方：\n" + "\n".join(offenders)
    )


def test_the_central_request_helpers_carry_auth():
    """`smoke_app._get` / `_post` 是所有调用方共用的那一层。

    它们把 URL 当变量收，所以按 URL 文本分类的扫描**看不见**它们——从这里
    摘掉 `_AUTH`，上面那条用例照样绿，而 `visual_regression` / `soak` /
    `upgrade_acceptance` 三个脚本会全部 401。Codex 在 #56 上第三次破防打的
    就是这个点，实测确认属实。

    共用层塌了下游全塌，所以单独钉死，不靠通用启发式覆盖。
    """
    import ast

    src = (SCRIPTS / "smoke_app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    checked = set()
    for fn in ast.walk(tree):
        if not (isinstance(fn, ast.FunctionDef) and fn.name in ("_get", "_post", "_req")):
            continue
        body = ast.unparse(fn)
        assert "_AUTH" in body, f"smoke_app.{fn.name} 不再带会话凭据——所有调用方会一起 401"
        checked.add(fn.name)
    assert {"_get", "_post"} <= checked, (
        f"没找到中心助手（只找到 {sorted(checked)}）——这条用例本身失效了"
    )


SCRIPTS = CI_DIR.parent


def _app_launchers() -> dict[str, str]:
    """会自己起一个 Tavotto 实例的脚本 = 源码里给子进程塞了 TAVOTTO_DATA_DIR。

    按**行为**枚举而不是写死一张名单：名单会在下一个脚本加进来时悄悄漏掉它，
    而漏掉的表现正是本轮那种——发行链跑到那一步才 401。

    **枚举本身也不能靠一种拼写。** 上一版判据是 `'"TAVOTTO_DATA_DIR"' in src`，
    只认 dict 字面量的字符串键；`recover_frac_positions.py` 用的是
    `dict(os.environ, TAVOTTO_DATA_DIR=...)` 关键字形式，于是整个文件都没进
    枚举——两条认证扫描对它一路绿，而它在 0.9.0 上会空转 120 次然后报
    「隔离实例没起来」。Codex 在 #56 上第四次破这条用例打的就是这个点。
    """
    found = {}
    for path in sorted(SCRIPTS.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        src = path.read_text(encoding="utf-8")
        if _sets_env(src, "TAVOTTO_DATA_DIR") and "subprocess.Popen" in src:
            found[path.name] = src
    return found


def test_every_app_launcher_adopts_credentials():
    """ADR 0008 之后，**每一个**起实例的脚本都要处理认证，三选一。

    v0.9.0 的教训：`upgrade_acceptance` / `visual_regression` / `soak` 三个脚本
    在会话认证合并之后全都还在裸调 API，而它们**一个都没跑过**。发行链第一次
    真跑时，三步各 401 一次——我修了第一个却没扫另外两个，于是同一个 401 在
    两轮 CI 里又出现了两次（`fix-the-predicate-sweep-the-consumers`）。

    所以判据放在这里，按行为枚举调用方，而不是逐个脚本各写一条用例。
    """
    launchers = _app_launchers()
    assert len(launchers) >= 4, (
        f"只枚举到 {sorted(launchers)}——枚举判据失效了，这条用例挡不住任何东西"
    )
    for name, src in launchers.items():
        # **用 AST 找真实的调用，不再做子串启发式。** 这条判据被 Codex 连破
        # 三次，每次都是同一类漏洞：注释满足它、函数**定义**满足它、目标写成
        # 变量就匹配不到。子串补丁打三次还漏，说明方法本身不对——源码结构的
        # 问题要用解析源码结构来判。
        adopts = _launch_reaches(src, "adopt_session_credentials")
        bypass = _sets_env(src, "TAVOTTO_INSECURE_NO_AUTH")
        desktop = "/api/desktop/bootstrap" in src or "TAVOTTO_DESKTOP_HANDSHAKE" in src
        assert adopts or bypass or desktop, (
            f"{name} 起了实例却既不取凭据、也没显式旁路、也不是桌面握手——"
            "ADR 0008 之后它的每个 API 调用都会 401，而症状会出现在很远的地方"
        )


def test_session_credential_logic_has_a_single_implementation():
    """凭据装载只能有一处，否则修一个漏一个——本轮已经付过这笔学费。"""
    hits = [
        n
        for n, src in _app_launchers().items()
        if 'session" / f"port-' in src or "['secret']" in src or '["secret"]' in src
    ]
    assert hits == ["smoke_app.py"], f"除 smoke_app 外还有人自己解析凭据文件：{hits}"


def test_ci_credential_path_matches_session_client():
    """CI 侧的路径公式必须与产品那份逐字一致。

    产品那份（`engine/session_client.session_file_path`）从**当前进程**的
    `config.data_dir()` 推路径，而 CI 是把 `TAVOTTO_DATA_DIR` 塞进**子进程**
    env 的，父进程用不了它——所以这里必然是第二份表达。两份就要对拍
    （与 patchspec ↔ Rust、preflight 双求值器同一套纪律）。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("_smoke_app_probe", SCRIPTS / "smoke_app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    from tavotto.engine import session_client

    root = Path("/tmp/whatever-data-dir")
    monkey = os.environ.get("TAVOTTO_DATA_DIR")
    os.environ["TAVOTTO_DATA_DIR"] = str(root)
    try:
        from tavotto.engine import config

        config.data_dir.cache_clear() if hasattr(config.data_dir, "cache_clear") else None
        product = Path(session_client.session_file_path(5089))
    finally:
        if monkey is None:
            os.environ.pop("TAVOTTO_DATA_DIR", None)
        else:
            os.environ["TAVOTTO_DATA_DIR"] = monkey
    ours = mod.session_credential_path(root, 5089)
    assert ours == product, f"路径公式分叉：CI={ours} 产品={product}"


def _is_urllib_request(func) -> bool:
    """`urllib.request.Request` / `Request` 的调用目标。"""
    import ast

    if isinstance(func, ast.Attribute) and func.attr == "Request":
        return True
    return isinstance(func, ast.Name) and func.id == "Request"


def _calls_named(src: str, name: str) -> list:
    """源码里对 `name` 的**真实调用**（不含函数定义、不含注释、不含字符串）。

    这三样正是子串判据接连失守的地方：`def adopt_session_credentials(...)`
    这行定义、以及任何一句提到它的注释，都能满足 `name in src`。
    """
    import ast

    out = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        hit = (isinstance(f, ast.Name) and f.id == name) or (
            isinstance(f, ast.Attribute) and f.attr == name
        )
        if hit:
            out.append(ast.unparse(node))
    return out


def _sets_env(src: str, key: str) -> bool:
    """`key` 真的被设成了环境变量——**两种拼法都要认**。

        env = {"TAVOTTO_DATA_DIR": ...}          # dict 字面量的字符串键
        env = dict(os.environ, TAVOTTO_DATA_DIR=...)   # 关键字实参

    只认前一种的代价是真实的：`recover_frac_positions.py` 用的是后一种，
    于是它整个躲开了认证扫描（#56 的第四条 review）。注释里提一句不算。
    """
    import ast

    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and k.value == key:
                    return True
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == key:
                    return True
    return False


def _references_local_base(call) -> bool:
    """这个 Request 打的是本机实例吗——看它的目标表达式引用了谁。

    本机实例的 URL 一律从 `base` / `s.base` / `self.base` 拼出来；GitHub API
    那几处引用的是模块级的 `API` / `REPO_SLUG`。按**引用的名字**判，而不是按
    URL 文本里有没有某个域名子串（后者 CodeQL 会告，且子串可以出现在任意位置）。
    """
    import ast

    if not call.args:
        return False
    for node in ast.walk(call.args[0]):
        if isinstance(node, ast.Name) and node.id == "base":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "base":
            return True
    return False


def _launch_reaches(src: str, target: str) -> bool:
    """「起实例」这条路上真的会走到 `target` 吗——一层可达性。

    只问「文件里有没有对 target 的调用」是不够的：把调用包进一个**没人调**的
    helper（`def _adopt(...): return _SA.adopt_session_credentials(...)`）照样
    满足它，而实例起来之后一次都不会执行。本轮反证时亲手撞到过，它和
    「函数定义满足子串」是同一类洞的不同深度。

    所以从**含 `subprocess.Popen` 的那个函数**出发，把它自己 + 它直接调用的
    函数体合起来找 target，并剪掉静态就走不到的分支（`if False:` 这种调试
    开关忘了删的情形）。一层足够覆盖真实写法（直接调、或经一个薄包装），
    再深就该用真正的调用图了——那时更该问的是「为什么这条路这么绕」。

    **边界写在明处：这条判据查的是意外，不是防蓄意。** 静态分析永远绕得过
    （`if some_always_false_flag:`、藏进一个永不为真的条件……），追下去是
    赢不了的军备竞赛——这个仓库对 playground 完整性校验早就下过同样的裁决。
    行为上的真保证是 **lab gate 本身**：`visual_regression` 一旦掉了凭据，
    发行链当场红（v0.9.0 就是这么暴露的）。这条静态判据的职责只是**更早、
    更便宜**地发现同一件事，不是取代它。
    """
    import ast

    tree = ast.parse(src)
    funcs = {
        n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def dead(node) -> bool:
        """静态就走不到的分支：`if False:` / `if 0:` / `while False:`。

        现实里这不是蓄意伪装，是**调试开关忘了删**——把一行临时关掉、验完
        忘了打开。真会发生，也真的会让门禁安静地报绿，所以剪掉。
        """
        test = getattr(node, "test", None)
        return isinstance(test, ast.Constant) and not test.value

    def calls_in(node) -> set:
        out = set()
        stack = [node]
        while stack:
            cur = stack.pop()
            for child in ast.iter_child_nodes(cur):
                if isinstance(child, (ast.If, ast.While)) and dead(child):
                    # 只剪 body；orelse 照走（`if False: A else: B` 走的是 B）
                    stack.extend(child.orelse)
                    continue
                stack.append(child)
            if isinstance(cur, ast.Call):
                f = cur.func
                if isinstance(f, ast.Name):
                    out.add(f.id)
                elif isinstance(f, ast.Attribute):
                    out.add(f.attr)
        return out

    launchers = [fn for fn in funcs.values() if "Popen" in calls_in(fn)]
    if not launchers:  # 模块级 Popen：退回全文件
        launchers = [tree]
    for fn in launchers:
        reachable = calls_in(fn)
        if target in reachable:
            return True
        for name in list(reachable):
            inner = funcs.get(name)
            if inner is not None and target in calls_in(inner):
                return True
    return False


def test_single_path_action_inputs_are_not_globs():
    """只收**一个路径**的 action 输入，不许喂 glob。

    2026-08-22 v0.9.1 发版实测：`anchore/sbom-action` 的 `file:` 写成
    `dist/*.whl`，syft 把它原样当文件名，报
    `no source providers were able to resolve the input`。那一步是 #45 加的，
    而 `github_release` 这个 job 在那之后**从来没成功跑到过**（几轮都卡在
    lab gate 之前），所以整整没人发现——又一处「从没执行过所以烂着」。

    `subject-path` / `files` 这类**明确支持多值**的输入不在此列：
    `actions/attest-build-provenance` 与 `softprops/action-gh-release` 都按
    多行 glob 收，写 glob 是对的。判据只盯单值输入，别把正当写法也判红。
    """
    import re

    SINGLE_VALUE = ("file", "image", "artifact-name", "output-file")
    offenders = []
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        for i, line in enumerate(wf.read_text(encoding="utf-8").splitlines(), 1):
            m = re.match(r"\s*(" + "|".join(SINGLE_VALUE) + r"):\s*(\S.*)$", line)
            if not m:
                continue
            val = m.group(2).strip()
            # **表达式前缀不等于没有通配符。** `${{ github.workspace }}/dist/*.whl`
            # 这种常见写法里，GitHub 只替换表达式、**不做 shell 展开**，剩下的
            # `*` 会原样交给 syft——和裸 glob 一样坏。所以剥掉表达式之后再看，
            # 别按开头是不是 `${{` 一刀放行（#63 的 review 逮到）。
            bare = re.sub(r"\$\{\{[^}]*\}\}", "", val)
            if not bare.strip():  # 整个值就是一个表达式：由前一步解析出的具体路径
                continue
            if "*" in bare or "?" in bare:
                offenders.append(f"{wf.name}:{i} {m.group(1)}: {val}")
    assert not offenders, "这些输入只收一个路径，喂 glob 会被原样当成文件名：\n  " + "\n  ".join(
        offenders
    )


def test_always_steps_do_not_depend_on_a_step_that_may_not_have_run():
    """`always()` 的收尾步骤不能依赖某个**可能没跑过**的步骤的输出。

    2026-08-22（v0.9.1 发版）实测：`lab_release_gate` 的「汇总」写的是
    `${{ steps.venv.outputs.python }} scripts/ci/summarize.py`，而
    `steps.venv` 来自「建验证环境」——体检先失败时它根本没跑，变量是空串，
    命令退化成**直接执行**那个脚本；它是 100644（没有执行位），于是
    `Permission denied` / 退出码 126。

    后果是这一步最不该有的那一种：**它「总是要跑」，却恰恰在真的有失败要汇总
    时自己挂掉**——体检报出了遗留进程，而读的人在 job summary 里只看到 126。
    与本轮反复出现的「诊断在最需要它时失灵」是同一个形状。

    判据只盯**这一处已知的依赖**（`steps.venv.outputs.python`）：泛化成
    「扫所有 always() 步骤里的所有 steps.* 引用」会把大量正当写法也判红
    （很多 always() 步骤本来就只在前序跑过时才有意义），那种噪音门禁活不过
    两周。要扩就等下一次真出事，按真实案例扩。
    """
    # **不用 PyYAML。** 它不在 `.venv` 里（Flask 那侧刻意只有 flask+pymupdf），
    # 而 `importorskip` 会让这条在本地开发环境静默跳过——那正是空门禁。
    # 这里只需要「按步骤切开、看它的 if 与 run」，标准库够用。
    import re

    offenders = []
    # **扫全部 workflow。** 原来只扫 release.yml 与 lab-ci.yml，而那两处的
    # 步骤已经搬进 `_lab-qualification.yml`——只扫老地方的话，这条判据会在
    # 搬家那天悄悄变成「什么都没扫」。名单要跟着结构走，不能写死。
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        name = wf.name
        src = wf.read_text(encoding="utf-8")
        # 以 `      - name:` 切步骤（本仓库两个 workflow 的缩进是一致的）
        steps = re.split(r"\n(?=      - name:)", src)
        # 切分自检**按文件给下限**：codeql.yml 这类小文件本来就只有两三步，
        # 一刀切 `> 10` 会在扫描范围扩大到全部 workflow 时把它判红——
        # 而那说明的不是切分失效，是我把「资格验证那种大文件」的常识
        # 套到了所有文件上。判据的主语要说清楚：这里问的是
        # 「这个文件里以 `- name:` 起头的步骤有没有被切出来」。
        if "      - name:" in src:
            assert len(steps) > 1, f"{name}: 有步骤却一段都没切出来，切分判据失效了"
        for step in steps:
            # **只判会在前序失败后照跑的那些。** 普通顺序步骤引用它是正当的
            # ——venv 没建起来时它们根本不会执行。第一版没区分，把十几处正当
            # 写法一起判红了：判据的主语又窄了一圈（该问「这一步会不会在 venv
            # 没跑时执行」，我问的是「有没有引用这个变量」）。
            if not re.search(r"^\s+if:.*(always\(\)|failure\(\))", step, re.M):
                continue
            for m in re.finditer(r"steps\.venv\.outputs\.python(.*?)\}\}", step, re.S):
                if "||" not in m.group(1):
                    label = re.search(r"- name: (.*)", step)
                    offenders.append(f"{name}: 步骤「{label.group(1).strip() if label else '?'}」")
    assert not offenders, (
        "这些步骤在前序失败时照跑，却依赖「建验证环境」的输出——那时它是空串，"
        "命令退化成直接执行脚本（100644 → Permission denied）：\n  " + "\n  ".join(offenders)
    )


def test_summary_refuses_reports_from_another_run(tmp_path, monkeypatch):
    """汇总只认本轮的报告，否则它会把没跑过的阶段标成 PASS。

    `reports/` 在**持久**状态根里、保留 30 天，而 `cleanup.py` 排在体检
    **之后**——体检早早失败时，上一轮的 `soak.json` / `visual.json` 还原样
    躺在那儿。不核对 `metadata.run_id` 的话，汇总会报告那些阶段通过，而它们
    这一轮根本没跑过。

    **这是最坏的一种诊断失效：不是缺席，是说谎。** 而且它偏偏发生在体检失败、
    最需要看清「究竟跑到哪一步」的时候。2026-08-22 v0.9.1 发版时，
    汇总因为另一个 bug 直接崩了（#61），反而没来得及说这个谎——修好解释器
    却不修这一条，等于把「崩掉」换成「说谎」。
    """
    import importlib.util
    import json as _json

    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "soak.json").write_text(
        _json.dumps({"ok": True, "metadata": {"run_id": "1111"}}), encoding="utf-8"
    )
    monkeypatch.setenv("TAVOTTO_CI_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("GITHUB_RUN_ID", "2222")  # 本轮 ≠ 报告那轮
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    spec = importlib.util.spec_from_file_location("_sm", CI_DIR / "summarize.py")
    mod = importlib.util.module_from_spec(spec)
    import sys as _sys

    _sys.path.insert(0, str(CI_DIR))
    spec.loader.exec_module(mod)

    import contextlib
    import io as _io

    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        mod.main(["--mode", "release"])
    out = buf.getvalue()
    soak_line = [ln for ln in out.splitlines() if "soak" in ln.lower()]
    assert soak_line, f"输出里找不到 soak 那一行：\n{out[:600]}"
    assert "PASS" not in soak_line[0], f"上一轮的报告被当成本轮的结果了：{soak_line[0]}"
    assert "未运行" in soak_line[0], f"该标成未运行：{soak_line[0]}"


def test_summary_keeps_this_runs_report_even_across_a_rerun(tmp_path, monkeypatch):
    """身份要带 attempt，而且**每个写报告的脚本都得真的写 metadata**。

    两条都是 #61 的 review 逮到的，方向相反、后果一样坏：

    * `GITHUB_RUN_ID` 在「Re-run jobs」时**复用**，只有 `GITHUB_RUN_ATTEMPT`
      递增——只比 run_id 的话，上一次尝试留下的报告会被当成本次的（说谎）。
    * `compat_matrix` 当时**根本没写 metadata**，于是本轮真跑出来的
      CompatBench 报告会被一律拒收（误报未运行）。**我修「说谎」的时候造出了
      「误报」**，两头都是诊断失真。
    """
    import contextlib
    import importlib.util
    import io as _io
    import json as _json
    import sys as _sys

    (tmp_path / "reports").mkdir()
    # 本轮 attempt=2；报告来自 attempt=1（同一个 run_id）
    (tmp_path / "reports" / "soak.json").write_text(
        _json.dumps({"ok": True, "metadata": {"run_id": "42", "run_attempt": "1"}}),
        encoding="utf-8",
    )
    # 本轮自己的 CompatBench 报告，必须留下
    (tmp_path / "reports" / "compat.json").write_text(
        _json.dumps({"ok": True, "metadata": {"run_id": "42", "run_attempt": "2"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("TAVOTTO_CI_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    spec = importlib.util.spec_from_file_location("_sm2", CI_DIR / "summarize.py")
    mod = importlib.util.module_from_spec(spec)
    _sys.path.insert(0, str(CI_DIR))
    spec.loader.exec_module(mod)
    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        mod.main(["--mode", "release"])
    out = buf.getvalue()

    soak = [ln for ln in out.splitlines() if "soak" in ln.lower()][0]
    assert "PASS" not in soak, f"上一次 attempt 的报告被当成本次的了：{soak}"
    compat = [ln for ln in out.splitlines() if "兼容" in ln or "compat" in ln.lower()][0]
    assert "未运行" not in compat, f"本轮自己的报告被误判成未运行了：{compat}"


def test_every_report_writer_stamps_its_identity():
    """写报告的脚本都要 `run_metadata()`，否则汇总认不出它是本轮的。

    `compat_matrix` 当时就漏了——而漏掉的表现不是报错，是那一行永远显示
    「未运行」。判据按**调用点**扫，别只看文件里出现过这个名字。
    """
    # **主语第三次才对。** 依次错过：只扫 `write_report()` 的调用点
    # （compat_matrix 自己 json.dump，不走那个助手）、按「源码里出现哪个报告名」
    # 扫（compat.json 这个名字压根不在它源码里，是 workflow 用 `--json` 传进去的）。
    # 正确的问法是：**workflow 里哪个脚本产出 SECTIONS 里的那份报告。**
    import ast
    import re

    spec_src = (CI_DIR / "summarize.py").read_text(encoding="utf-8")
    wanted = set(re.findall(r'"(\w+\.json)"', spec_src.split("SECTIONS", 1)[1][:800]))
    assert len(wanted) >= 4, f"只解析出 {wanted}——SECTIONS 的形状变了，判据失效"

    # 报告有两种产出方式，都要认：
    #   ① 脚本自己 `write_report("x.json", ...)`（多数）
    #   ② workflow 用 `--json .../x.json` 把路径传进去（compat_matrix 是这种，
    #      所以它源码里根本没有 "compat.json" 这个字面量）
    producers: dict[str, str] = {}
    for path in sorted(CI_DIR.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        for rep in wanted:
            if f'"{rep}"' in src:
                producers[rep] = path.name
    wf = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    for step in re.split(r"\n(?=      - name:)", wf):
        m = re.search(r"scripts/ci/(\w+)\.py", step)
        if not m:
            continue
        for rep in wanted:
            if rep in step:
                producers[rep] = m.group(1) + ".py"
    assert len(producers) >= 5, f"只对上 {producers}——产出关系解析失效了"

    missing = []
    for rep, script in sorted(producers.items()):
        src = (CI_DIR / script).read_text(encoding="utf-8")
        calls = {
            getattr(n.func, "attr", getattr(n.func, "id", ""))
            for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Call)
        }
        if "run_metadata" not in calls:
            missing.append(f"{script}（产出 {rep}）")
    assert not missing, (
        f"这些脚本产出了汇总要读的报告，却一次都没调 run_metadata()——"
        f"汇总会把它们当成上一轮的、标成「未运行」：{missing}"
    )

    # **精度写在明处**：判的是「这个脚本调没调过 run_metadata()」，不是
    # 「写这份报告时盖上了没有」。后者要跟着数据流走，静态做不可靠。
    # 所以它逮得住「整个脚本都忘了盖」（compat_matrix 当时就是），逮不住
    # 「调了但没放进这份 payload」。够用，但别当成更强的保证。


def test_the_desktop_leg_no_longer_waits_for_anything():
    """**这条用例的前身消失了，理由值得记下来。**

    从前它叫 `test_desktop_outwaits_the_release_qualification_gate`，
    钉的是「桌面链等 Release 的上限 ≥ 发行资格验证的 job 超时」——因为
    2026-08-22 v0.9.1 发版时两条腿**全程构建成功**（含 macOS 的签名与公证），
    只栽在等待那一步：上限是 10 分钟，而那个数字是 `lab_release_gate` 存在
    **之前**定的。

    那条判据是对的，但它守的是一个**不该存在的机制**。#62 把上限调到
    190 分钟，注释里自己写着「没有任何固定上限是够的」——因为 lab gate 的
    **排队**时间本身没有上界。

    现在整段轮询删掉了：桌面链只把产物传成 artifact，挂 Release 归
    release.yml 的 publish job 统一做。**「等多久才够」这个问题不再存在**，
    所以钉那个不等式的用例也不再存在。取而代之的是更强的一条：
    这条链里不许有任何等待。

    结构性的那半（没有轮询、没有自挂 Release、tag 只有一个入口）由
    `tests/test_release_workflow_contract.py` 看护；这里留一条最小的哨兵，
    免得有人在 desktop-tauri.yml 里把它加回来而两个文件的用例都没红。
    """
    src = (WORKFLOWS / "desktop-tauri.yml").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    assert "seq 1" not in code, "桌面链里又出现了轮询循环"
    assert "gh release view" not in code, "桌面链又在等 Release 出现"
    assert "action-gh-release" not in code, "桌面链又在自己挂 Release"


def test_the_ownership_predicate_has_exactly_one_implementation():
    """「这个进程是不是本 CI 漏下的」只能有一份判据。

    从前有两份：体检认「argv 像 Tavotto 且命令行里有 CI 根**或 runner 工作
    目录**」，`cleanup.kill_stale_processes` 认「有 tavotto 且有 CI 根」。
    不一致的后果不是多杀少杀，是**自愈报告成功却什么都没做**——体检判成
    遗留、kill 那份不认、复检照旧失败。「共享判据修一处不算修完」。
    """
    import ast

    import cleanup as CU
    import lab_preflight as PF

    assert PF.find_ci_owned_tavotto is _common.find_ci_owned_tavotto
    assert CU.find_ci_owned_tavotto is _common.find_ci_owned_tavotto

    # **import 了不等于用了。** 第一版只比这两个绑定，于是把 cleanup 里那句
    # 调用换成空列表，用例照样绿——判据少了一维（问「有没有 import」，
    # 该问「kill 那条路径走不走它」）。所以再按**调用点**判一次。
    for mod_path, func in (
        (CI_DIR / "cleanup.py", "kill_stale_processes"),
        (CI_DIR / "lab_preflight.py", "check_stale_processes"),
    ):
        tree = ast.parse(mod_path.read_text(encoding="utf-8"))
        fn = next(
            (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func), None
        )
        assert fn is not None, f"{mod_path.name} 里没有 {func}"
        called = {
            n.func.id
            for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "find_ci_owned_tavotto" in called, (
            f"{mod_path.name}::{func} 没有调 find_ci_owned_tavotto——"
            f"判据又分叉了，而分叉的表现是「自愈报告成功却什么都没做」"
        )

    # 两个消费方都不许再自己扫 /proc
    for mod_path in (CI_DIR / "lab_preflight.py", CI_DIR / "cleanup.py"):
        tree = ast.parse(mod_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "/proc":
                raise AssertionError(
                    f"{mod_path.name} 又自己扫 /proc 了——判据必须只留 _common 一份"
                )


def test_kill_stale_actually_reaches_a_runner_workspace_process(monkeypatch):
    """行为级：runner 工作目录下的进程，`kill_stale_processes` 也要收。

    这正是从前两份判据分叉的那一格——体检认 runner 工作目录，kill 不认。
    静态判据（上一条）能挡住「不调共享函数」，挡不住「调了但过滤掉了」，
    所以这一条按**真的返回了哪些 pid** 判。
    """
    import cleanup as CU

    work = "/home/runner/actions-runner/_work/Tavotto"
    monkeypatch.setenv("RUNNER_WORKSPACE", work)
    monkeypatch.setattr(
        "cleanup.find_ci_owned_tavotto",
        lambda extra_markers=None: [(4242, f"{work}/dist/tavotto --figures x")],
    )
    got = CU.kill_stale_processes(Path("/srv/tavotto-ci"), dry_run=True)
    assert [r["pid"] for r in got] == [4242], (
        "runner 工作目录下的遗留进程没被收——体检会判它遗留，而自愈收不到它"
    )


def test_preflight_reports_stale_processes_when_not_reaping(monkeypatch):
    fake = [(4242, "/srv/x/venv/bin/python -m tavotto --port 1 --figures /srv/x/f")]
    monkeypatch.setattr("lab_preflight.find_ci_owned_tavotto", lambda: fake)
    import lab_preflight as PF

    (check,) = PF.check_stale_processes(reap=False)
    assert not check.ok and not check.warn, "不自愈时必须阻断"
    assert "4242" in check.detail


def test_preflight_self_heals_stale_processes(monkeypatch):
    """`--reap-stale` 真的把它们清掉，并**复检**确认。"""
    import lab_preflight as PF

    alive = {4242, 4243}
    fake = [(p, f"/srv/x/venv/bin/python -m tavotto --port {p}") for p in sorted(alive)]

    def _find():
        return [(p, c) for p, c in fake if p in alive]

    def _kill(pid, sig):
        alive.discard(pid)

    monkeypatch.setattr("lab_preflight.find_ci_owned_tavotto", _find)
    monkeypatch.setattr(PF.os, "kill", _kill)
    (check,) = PF.check_stale_processes(reap=True)
    assert check.ok, f"清干净了却没通过：{check.detail}"
    assert check.warn, "通过了也必须在 summary 里看得见——静默自愈会掩盖真实的退出路径缺陷"
    assert "2" in check.detail


def test_self_heal_still_blocks_when_a_process_survives(monkeypatch):
    """**清不掉就照旧阻断。**

    「发了信号就当它死了」是最坏的写法：SIGTERM 对卡在 C 扩展里的 worker
    可能毫无作用，而「报告已清理、其实还在」会让 soak 的泄漏判定与 benchmark
    拿着被污染的机器继续跑——比直接失败糟糕得多。
    """
    import lab_preflight as PF

    fake = [(4242, "/srv/x/venv/bin/python -m tavotto --port 4242")]
    monkeypatch.setattr("lab_preflight.find_ci_owned_tavotto", lambda: fake)
    monkeypatch.setattr(PF.os, "kill", lambda pid, sig: None)  # 杀不动
    monkeypatch.setattr(PF.time, "monotonic", iter([0.0, 999.0, 999.0, 999.0]).__next__)
    (check,) = PF.check_stale_processes(reap=True)
    assert not check.ok and not check.warn
    assert "没死" in check.detail


def test_ownership_predicate_never_matches_a_maintainers_own_instance():
    """维护者自己开的实例不归 CI 管——误杀一次就再没人敢开自愈。"""
    markers = ["/srv/tavotto-ci", "/home/runner/actions-runner/_work/Tavotto"]
    assert not _common.is_ci_owned_tavotto(
        "/home/alice/.venv/bin/python -m tavotto --figures /home/alice/figs", markers
    )
    assert not _common.is_ci_owned_tavotto("/usr/bin/python3 -m http.server", markers)
    # 名字里带 tavotto 但不是启动形态的也不算
    assert not _common.is_ci_owned_tavotto("less /srv/tavotto-ci/reports/x.json", markers)
    # 真正归属 CI 的四种形态
    for cmd in (
        "/srv/tavotto-ci/tmp/v/bin/python -m tavotto --port 1",
        "/srv/tavotto-ci/tmp/v/bin/python /x/engine/worker.py --script a",
        "/srv/tavotto-ci/rt/tavotto-workerd --spec x",
        "/home/runner/actions-runner/_work/Tavotto/dist/tavotto --figures x",
    ):
        assert _common.is_ci_owned_tavotto(cmd, markers), cmd


def test_the_lab_workflows_actually_pass_reap_stale():
    """自愈写好了却没接上去，等于没写。"""
    # 体检现在只有一处调用点（资格验证收敛成 `_lab-qualification.yml`），
    # 但判据仍按「凡是调它的地方」扫——写死一个文件名，会在下次搬家时
    # 悄悄变成空判据。
    found = 0
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        for ln in wf.read_text(encoding="utf-8").splitlines():
            if "lab_preflight.py" not in ln or ln.lstrip().startswith("#"):
                continue
            found += 1
            assert "--reap-stale" in ln, f"{wf.name}: 体检没有开自愈：{ln.strip()}"
    assert found, "一处调用 lab_preflight.py 的地方都没有——这条判据空转了"


def test_a_replacement_process_cannot_slip_through_the_grace_period(monkeypatch):
    """**判据是「机器上现在还有没有」，不是「原来那批死了没」。**

    只盯最初那组 pid 会漏掉**替补**：宽限期里 supervisor 完全可能重启一个
    worker，新进程同样归属本 CI、同样会污染 soak 的泄漏判定与 benchmark，
    而它不在 `targets` 里 —— 于是复检说「清干净了」，机器上却还有。

    体检跑在本轮 CI 开跑**之前**，那一刻不该有任何归属本 CI 的 Tavotto
    进程，所以「还有没有」既是更严的判据，也是更对的那个。
    """
    import lab_preflight as PF

    alive = {4242}
    seq = iter([1])  # 第一次 kill 之后冒出一个替补

    def _find():
        return [(p, f"/srv/x/venv/bin/python -m tavotto --port {p}") for p in sorted(alive)]

    def _kill(pid, sig):
        alive.discard(pid)
        if next(seq, None):  # 只在第一次 kill 后放一个替补进来
            alive.add(5353)

    monkeypatch.setattr("lab_preflight.find_ci_owned_tavotto", _find)
    monkeypatch.setattr(PF.os, "kill", _kill)
    (check,) = PF.check_stale_processes(reap=True)
    assert check.ok, f"替补被漏掉了吗：{check.detail}"
    assert not alive, f"机器上还剩 {alive}，而体检说通过了"


def test_an_explicit_root_is_actually_searched(monkeypatch, tmp_path):
    """`--kill-stale --root X` 要真的收 X 下面的进程。

    候选集由 `find_ci_owned_tavotto()` 按**默认** marker（持久化根 +
    runner 工作目录）筛出来，显式 root 下的进程在进入 `marker not in cmd`
    这句之前就已经被丢掉了 —— 于是它一个都不收，而且不报错。
    """
    import cleanup as CU

    # **命令行要按 `resolve()` 之后的 root 拼**：判据比的是
    # `str(Path(root).resolve())`，写死 POSIX 字面量的话 Windows 上
    # `/mnt/x` 会被解析成 `D:\\mnt\\x`，与命令行里的字面量对不上——
    # 于是这条用例量的不再是「extra_markers 有没有传下去」，而是
    # 「它跑在哪个平台上」。CI 的 windows 腿逮到过一次。
    other = str((tmp_path / "other-ci-root").resolve())
    cmd = f"{other}/venv/bin/python -m tavotto --port 7070"
    monkeypatch.setenv("TAVOTTO_CI_STATE_ROOT", str((tmp_path / "state").resolve()))
    monkeypatch.delenv("RUNNER_WORKSPACE", raising=False)
    monkeypatch.delenv("GITHUB_WORKSPACE", raising=False)
    monkeypatch.setattr("cleanup.proc_cmdlines", lambda: [(7070, cmd)], raising=False)
    # 真的走 _common 的筛选，只是把 /proc 换掉
    monkeypatch.setattr(_common, "proc_cmdlines", lambda: [(7070, cmd)])
    got = CU.kill_stale_processes(Path(other), dry_run=True)
    assert [r["pid"] for r in got] == [7070], (
        f"显式 root 下的进程没被收到：{got} —— extra_markers 没传下去"
    )


def test_a_replacement_that_survives_sigkill_is_reported(monkeypatch):
    """**复检那一维单独钉一次。**

    上一条（`…cannot_slip_through_the_grace_period`）实际覆盖的是 **SIGKILL
    扫全量**那一步：把 `survivors` 改回「只看原来那组 pid」，替补早已被
    SIGKILL 收掉，用例照样绿 —— 一处改动被另一处的行为掩盖了。

    这一条构造一个**连 SIGKILL 都打不死**的替补（D 状态，卡在内核里），
    于是只有「复检问的是机器上现在还有没有」这一维能把它报出来。
    """
    import lab_preflight as PF

    alive = {4242}
    spawned = iter([1])

    def _find():
        return [(p, f"/srv/x/venv/bin/python -m tavotto --port {p}") for p in sorted(alive)]

    def _kill(pid, sig):
        if pid == 5353:
            return  # 替补打不死
        alive.discard(pid)
        if next(spawned, None):
            alive.add(5353)

    monkeypatch.setattr("lab_preflight.find_ci_owned_tavotto", _find)
    monkeypatch.setattr(PF.os, "kill", _kill)
    monkeypatch.setattr(PF.time, "monotonic", iter([0.0] + [999.0] * 8).__next__)
    (check,) = PF.check_stale_processes(reap=True)
    assert not check.ok, "打不死的替补被当成清干净了"
    assert "5353" in check.detail, f"没报出是哪个进程：{check.detail}"


# ============================================================ Release-blocker 门禁
#
# issue #35 把「真实 N-1 更新」列为退出条件后，带着这个已知未验证的洞又发了
# 多个 0.x 版本——没有任何机制在发版时把它摆到眼前。与 #78（「声明了却从未
# 执行的 job」）同族：洞活在 issue 里而不是 YAML 里。判定逻辑集中在
# scripts/ci/release_blockers.py，这里逐条钉「坏掉之后会怎样」。


def _issue(num: int, title: str = "x", state: str = "open", pr: bool = False) -> dict:
    d = {"number": num, "title": title, "state": state}
    if pr:
        d["pull_request"] = {"url": "…"}
    return d


def test_no_open_blockers_and_no_ack_passes():
    ok, _ = RB.check([], "", "workflow_dispatch")
    assert ok


def test_a_stale_ack_with_no_open_blockers_is_refused():
    """残留的 ack 是上一次发版复制来的陈词。

    放行它的话，下一个 blocker 出现时那串旧编号可能正好把它「签」掉——
    一次 ack 永久生效，门禁名存实亡。
    """
    ok, lines = RB.check([], "35", "workflow_dispatch")
    assert not ok
    assert any("陈旧" in ln for ln in lines)


def test_an_unacked_blocker_blocks_and_is_listed():
    ok, lines = RB.check([_issue(35, "N-1 真更新"), _issue(83)], "", "workflow_dispatch")
    assert not ok
    text = "\n".join(lines)
    assert "#35" in text and "N-1 真更新" in text, "清单必须摆到发版人眼前，不是光说有"


def test_an_exact_ack_passes_and_names_the_responsibility():
    ok, lines = RB.check([_issue(35), _issue(83)], "35,83", "workflow_dispatch")
    assert ok
    assert any("明知" in ln for ln in lines), "放行时要写明签字的含义"


def test_a_partial_ack_is_refused():
    """两个 blocker 只签一个 = 没签。"""
    ok, lines = RB.check([_issue(35), _issue(83)], "35", "workflow_dispatch")
    assert not ok
    assert any("#83" in ln for ln in lines)


def test_an_ack_naming_a_closed_issue_is_refused():
    """签了不在 open 清单里的编号（已关闭 / 写错）也不放行。

    「逐条对得上」是双向的——单向包含会让「多签几个万能编号」永久生效。
    """
    ok, lines = RB.check([_issue(35)], "35,999", "workflow_dispatch")
    assert not ok
    assert any("#999" in ln for ln in lines)


def test_prs_are_not_blockers():
    """GitHub 的 issues 端点会把 PR 混进来——PR 不是「已知未修的洞」。"""
    ok, _ = RB.check([_issue(41, pr=True)], "", "workflow_dispatch")
    assert ok


def test_tag_push_gets_told_how_to_proceed():
    """tag 触发带不了输入：红灯必须告诉人两条出路，而不是只说不行。"""
    ok, lines = RB.check([_issue(35)], "", "push")
    assert not ok
    text = "\n".join(lines)
    assert "workflow_dispatch" in text and "ack_open_blockers" in text


def test_ack_parsing_tolerates_human_input():
    assert RB.parse_ack(" #35 ，83, ") == {35, 83}
    assert RB.parse_ack("") == set()
    with pytest.raises(SystemExit):
        RB.parse_ack("35,abc")


def test_release_yml_wires_the_blocker_gate():
    """脚本写好了却没接上去，等于没写（gate-never-executed-rots）。"""
    src = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    assert "ack_open_blockers:" in src.split("\njobs:")[0], (
        "workflow_dispatch 少了 ack_open_blockers 输入"
    )
    trust = src.split("\n  trust:", 1)[1].split("\n  build:", 1)[0]
    assert "release_blockers.py" in trust, "trust 里没有 blocker 门禁那一步"
    assert "labels=release:blocker" in trust, "查询的不是 release:blocker 这个 label"
    assert "state=open" in trust
    assert "issues: read" in trust, "trust 没有 issues: read——gh api 查不了 label"
    # 门禁必须在 dispatch 与 tag push 两条路上都跑：不许挂 if 只在一条路执行
    step = trust.split("Release-blocker", 1)[1].split("- id: resolve", 1)[0]
    assert "\n        if:" not in step, (
        "blocker 门禁被 if 限定到了某一条触发路径——tag push 会无声绕过它"
    )


# ---------------------------------------------------------------- 字体包清单
# `bootstrap_lab_runner.sh` 的 `APT_PACKAGES` ↔ `docs/ci/self-hosted-runner.md`
# 的工具链表。两份说的是同一件事——**这台机器上装了什么**——而它们各自会被
# 不同的理由改动（脚本因为「重建时要装上」，文档因为「机器现在有什么」）。
# issue #229 就是这条缝：字体在机器上手工装好了，脚本没补，按脚本重建会复发。
#
# 字体缺失**没有任何一条信息指向「机器少装了东西」**：matplotlib 找不到
# `DejaVuSans-Oblique.ttf` 时不 warn，`style=italic` 静默退回 regular，画出来
# 与 regular 逐字节相同，红的是渲染用例。所以这条缝只能靠判据看住。

BOOTSTRAP = CI_DIR / "bootstrap_lab_runner.sh"
RUNNER_DOC = CI_DIR.parents[1] / "docs" / "ci" / "self-hosted-runner.md"


def _font_packages_in_script() -> set[str]:
    """`APT_PACKAGES` 数组里的 `fonts-*` 包名。

    **先去注释再取词**：注释里也会出现包名（`fonts-dejavu-core` 是「为什么
    要 extra」的一部分，恰恰是**不装**的那个），连注释一起 grep 会把它读成
    要装的包，判据于是永远对不上。
    """
    src = BOOTSTRAP.read_text(encoding="utf-8")
    block = src.split("APT_PACKAGES=(", 1)[1].split("\n)", 1)[0]
    return {
        word
        for line in block.splitlines()
        for word in line.split("#", 1)[0].split()
        if word.startswith("fonts-")
    }


def _font_packages_in_doc() -> set[str]:
    """工具链表第一列里的 `fonts-*` 包名。"""
    src = RUNNER_DOC.read_text(encoding="utf-8")
    table = src.split("## 4. 工具链", 1)[1].split("\n## ", 1)[0]
    return {
        first
        for line in table.splitlines()
        if line.startswith("|") and (first := line.split("|")[1].strip()).startswith("fonts-")
    }


def test_the_font_packages_are_one_list_on_both_sides():
    """脚本装的字体包与文档那张表必须逐个对上（issue #229）。"""
    script, doc = _font_packages_in_script(), _font_packages_in_doc()
    # 两侧同时读成空集也是「相等」——那种绿是解析坏了，不是清单对上了。
    assert script, "没从 APT_PACKAGES 里读到任何 fonts-* 包：解析坏了，下面那条断言恒真"
    assert doc, "没从工具链表里读到任何 fonts-* 包：解析坏了，下面那条断言恒真"
    assert script == doc, (
        f"字体包清单两侧不一致：脚本装了 {sorted(script - doc)} 而文档没写，"
        f"文档写了 {sorted(doc - script)} 而脚本不装。"
        "两份都是「这台机器上有什么」，漂了就会在下一次重建时少装一个包。"
    )


# ---------------------------------------------------------------- 字体体检
# `lab_preflight.check_fonts()`。执行位置查过了：`_lab-qualification.yml` 的
# 「开跑前体检」是 checkout 之后的第一步、**没有 if**，抽查 6 次有结论的
# lab-ci run（最近一次 2026-09-03），那一步的 conclusion 都是 success ——
# 它是真的每轮都跑，不是「声明了从没执行过」。
#
# 判据的主语要说死：它量的是**这台机器上有没有那张脸**，不是「italic 有没有
# 退回 regular」。所以下面第三条用例钉的是**失败信息本身**——判据说的话不许
# 比它量的东西强。


def _fc(monkeypatch, listing: str | None):
    """把 fc-list 换成给定输出；`None` 表示这台机器上没有 fc-list。

    输出形状取自本机真跑的 `fc-list : file`（每行 `<路径>: `），不是凭空捏的
    ——捏出来的形状会产生假红，而假红比假绿隐蔽。
    """
    import subprocess as sp

    import lab_preflight as PF

    monkeypatch.setattr(
        PF.shutil, "which", lambda exe: None if listing is None else "/usr/bin/fc-list"
    )
    monkeypatch.setattr(
        PF.subprocess, "run", lambda *a, **k: sp.CompletedProcess(a[0], 0, listing or "", "")
    )
    return PF


def test_preflight_blocks_when_the_oblique_face_is_missing(monkeypatch):
    """缺那张脸 = 阻断（issue #229：脚本没补包，重建就会复发）。"""
    PF = _fc(monkeypatch, "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf: \n")
    (check,) = PF.check_fonts()
    assert not check.ok and not check.warn, "缺字体必须阻断，不是警告"
    assert "fonts-dejavu-extra" in check.remedy
    # 判据必须进控制流：没挂进 run_all 的体检项一次都不会执行。
    assert "check_fonts" in PF.run_all.__code__.co_names, "check_fonts 没挂进 run_all"


def test_preflight_passes_when_the_oblique_face_is_installed(monkeypatch):
    """装上了就得放行——否则这条判据在健康的机器上是一堵墙。"""
    PF = _fc(
        monkeypatch,
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf: \n"
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf: \n",
    )
    (check,) = PF.check_fonts()
    assert check.ok, check.detail
    assert check.detail.endswith("DejaVuSans-Oblique.ttf")


def test_preflight_treats_unmeasurable_as_its_own_answer(monkeypatch):
    """没有 fc-list 是第三档：记录但不阻断。「量不到」不是「不在」。"""
    PF = _fc(monkeypatch, None)
    (check,) = PF.check_fonts()
    assert not check.ok and check.warn, "量不到时既不能判通过，也不能阻断"


def test_the_font_check_does_not_claim_more_than_it_measures(monkeypatch):
    """失败信息本身就是一条断言，它说的话必须和判据量的东西一致。

    这条判据量的是**文件在不在**；它证明不了「italic 没退回 regular」——那取决于
    跑图的解释器解析到哪一份 DejaVu（matplotlib 自带的 mpl-data 里也有一张
    Oblique）。措辞一旦说过了头，下一个人会拿它当那一维已经被看住的证据。
    """
    PF = _fc(monkeypatch, "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf: \n")
    assert "不证明" in (PF.check_fonts.__doc__ or ""), "docstring 没写死它证明不了什么"
    for check in (*PF.check_fonts(), *_fc(monkeypatch, None).check_fonts()):
        said = check.name + check.detail + check.remedy
        assert "退回" not in said and "回退" not in said, (
            f"「{check.name}」的措辞里出现了「退回/回退」——那是它量不到的那一维，"
            "不许写进判据说出口的话"
        )
