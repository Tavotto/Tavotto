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
    not HAS_IMAGING, reason="视觉回归需要 numpy 与 Pillow（pip install -e '.[ci]'）")


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
            f"注册表独有 {reg_stems - manifest_stems}；清单独有 {manifest_stems - reg_stems}")

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
        buckets = [200, 400, 800, 1600, 3200]     # 与 app.RENDER_BUCKETS 同源
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
        m = VR.compare(self._png(tmp_path, b, "a.png"),
                       self._png(tmp_path, b.astype("int16") + 10, "c.png"), None)
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
        ok, findings = BM.compare({"p::hot_total_ms": 100.0 * (1 + (BM.REGRESSION_PCT + 10) / 100)}, base)
        assert not ok
        assert findings[0]["verdict"] == "regression"

    def test_noise_below_threshold_passes(self):
        """阈值以内的波动不能报红，否则这条门禁很快就会被忽略。"""
        base = self._baseline({"p::hot_total_ms": 100.0})
        ok, findings = BM.compare({"p::hot_total_ms": 110.0}, base)   # +10%，低于 25%
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
        prev = json.loads(BM.baseline_path(tmp_path).with_suffix(".previous.json").read_text(encoding="utf-8"))
        assert cur["metrics"]["a"] == 2.0 and prev["metrics"]["a"] == 1.0
        assert not list((tmp_path / "baselines" / "perf").glob("*.tmp"))

    def test_baseline_records_metadata_that_makes_it_comparable(self, tmp_path):
        """没有 SHA / CPU / Python 的历史数字没有长期价值。"""
        _common.ensure_layout(tmp_path)
        BM.save_baseline(tmp_path, {"metrics": {"a": 1.0},
                                    "metadata": _common.run_metadata("main")})
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
        raw = {"rows": [
            {"id": "a.pdf", "really_cold": True, "cold": {"total_ms": 9000},
             "hot": {"total_ms": 25}, "export_wall_ms": 300, "export_ok": True},
            {"id": "b.pdf", "really_cold": False, "cold": {"total_ms": 30},
             "hot": {"total_ms": 22}, "export_wall_ms": 280, "export_ok": False},
        ]}
        m = BM.extract_metrics(raw)
        assert "a.pdf::cold_total_ms" in m
        assert "b.pdf::cold_total_ms" not in m, "把热态当成冷启动记进了基线"
        assert "b.pdf::export_ms" not in m, "失败的导出被当成有效测量"


# ============================================================ soak 泄漏判定
class TestSoakLeakDetection:
    def _series(self, n=50, fd=lambda i: 100, rss=lambda i: 500_000, proc=lambda i: 4):
        return [{"iteration": i, "fds": fd(i), "rss_kib": rss(i), "processes": proc(i)}
                for i in range(n)]

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
        monkeypatch.setattr(UA, "_api_json", lambda url, timeout=60: [
            {"tag_name": "v0.9.0-rc1", "prerelease": True, "draft": False},
            {"tag_name": "v0.7.0", "prerelease": False, "draft": False},
            {"tag_name": "v0.6.0", "prerelease": False, "draft": False},
        ])
        assert UA.resolve_baseline("0.8.0", None) == "v0.7.0"

    def test_baseline_must_be_older_than_candidate(self, monkeypatch):
        monkeypatch.setattr(UA, "_api_json", lambda url, timeout=60: [
            {"tag_name": "v0.9.0", "prerelease": False, "draft": False},
        ])
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
        log = ("INFO 启动完成\n"
               "Traceback (most recent call last):\n"
               '  File "x.py", line 1\n'
               "KeyError: 'schema'\n")
        found = UA._tracebacks(log)
        assert len(found) >= 2

    def test_traceback_detection_ignores_ordinary_lines(self):
        """普通日志里出现 error 字样不能算 traceback，否则这条恒红。"""
        assert UA._tracebacks("INFO 渲染完成\nWARN 未找到可选字体\n") == []


# ============================================================ 冒烟
@pytest.mark.parametrize("script", [
    "lab_acceptance.py", "soak.py", "visual_regression.py",
    "benchmark.py", "upgrade_acceptance.py",
    "compat_matrix.py", "compat_driver.py",
])
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
    out = subprocess.run([sys.executable, str(CI_DIR / script), "--help"],
                         capture_output=True, text=True, timeout=120,
                         encoding="utf-8", errors="replace")
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
        _common.write_report("upgrade.json",
                             {"ok": True, "skipped": True, "reason": "rename_boundary",
                              "detail": "跨越了产品改名边界"}, tmp_path)
        monkeypatch.setenv("TAVOTTO_CI_STATE_ROOT", str(tmp_path))
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        text = _detail_for(SM, "upgrade.json", tmp_path)
        assert "跳过" in text and "PASS" not in text

        # 更要紧的是**结果列**：扫读的人先看那一列，一个 ✅ PASS 会让他
        # 以为这项验过了，而实际上一次都没跑。
        import io, contextlib
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


def _slow_step(name: str) -> str:
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
    for wf in ("release.yml", "lab-ci.yml"):
        step = _slow_step(wf)
        assert 'grep -c "::"' not in step, \
            f"{wf}：又回去数 `::` 了——那是 pytest 打给人看的格式，会随版本变"
        assert "--collect-only" in step, f"{wf}：还是要先确认真的选得中"
        assert "rc=$?" in step, f"{wf}：要按退出码分诊"
        assert "5)" in step, f"{wf}：EXIT_NOTESTSCOLLECTED 那一支不见了"
        # 正面判据：收集的输出要**留下来**。写成「不许出现 2>/dev/null」的
        # 否定形式会被自己的报错文案咬到（那句话里就有这个字面量），而按
        # 「留没留日志」判既准确又不受措辞影响。
        assert "> slow-collect.log 2>&1" in step, \
            f"{wf}：收集的输出没留下来——出错原因会像这次一样整个消失"


def test_both_copies_of_the_slow_gate_agree():
    """release.yml 与 lab-ci.yml 各有一份，而且**已经漂开过一次**。

    RELEASING.md 写着 lab_release_gate「复用同一套 job」，实际是两份拷贝：
    lab-ci.yml 那份后来补了「空转的门禁比没有门禁更坏」，release.yml 那份没有。
    漂开的代价是发行链上跑的判据与 nightly 上验过的不是同一个。合成一份
    composite action 是 post-1.0 的活；在那之前用一条对拍挡住继续分叉。
    """
    a, b = _slow_step("release.yml"), _slow_step("lab-ci.yml")
    for token in ("--collect-only", "rc=$?", "5)", "set +e", "slow-collect.log"):
        assert (token in a) == (token in b), \
            f"两份 slow 门禁在 {token!r} 上不一致——判据分叉了"


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
    assert "self._adopt_credentials(port)" in src, \
        "起完实例必须真的调用它，光定义在那儿不算"
    assert "SA.adopt_session_credentials(" in src, \
        "必须走唯一实现，别在这里再写一份"
    body = src.split("def _adopt_credentials", 1)[1].split("\n    def ", 1)[0]
    code = "\n".join(ln for ln in body.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "else:" in code and "裸走" in code, \
        "取不到凭据要继续裸走（N-1 基线可能早于 ADR 0008），不是失败"


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
        "这些打到应用的请求没带会话凭据，401 的症状会出现在很远的地方：\n"
        + "\n".join(offenders))


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
        assert "_AUTH" in body, \
            f"smoke_app.{fn.name} 不再带会话凭据——所有调用方会一起 401"
        checked.add(fn.name)
    assert {"_get", "_post"} <= checked, \
        f"没找到中心助手（只找到 {sorted(checked)}）——这条用例本身失效了"


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
    assert len(launchers) >= 4, \
        f"只枚举到 {sorted(launchers)}——枚举判据失效了，这条用例挡不住任何东西"
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
            "ADR 0008 之后它的每个 API 调用都会 401，而症状会出现在很远的地方")


def test_session_credential_logic_has_a_single_implementation():
    """凭据装载只能有一处，否则修一个漏一个——本轮已经付过这笔学费。"""
    hits = [n for n, src in _app_launchers().items()
            if 'session" / f"port-' in src or "['secret']" in src
            or '["secret"]' in src]
    assert hits == ["smoke_app.py"], \
        f"除 smoke_app 外还有人自己解析凭据文件：{hits}"


def test_ci_credential_path_matches_session_client():
    """CI 侧的路径公式必须与产品那份逐字一致。

    产品那份（`engine/session_client.session_file_path`）从**当前进程**的
    `config.data_dir()` 推路径，而 CI 是把 `TAVOTTO_DATA_DIR` 塞进**子进程**
    env 的，父进程用不了它——所以这里必然是第二份表达。两份就要对拍
    （与 patchspec ↔ Rust、preflight 双求值器同一套纪律）。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_smoke_app_probe", SCRIPTS / "smoke_app.py")
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
        hit = (isinstance(f, ast.Name) and f.id == name) or \
              (isinstance(f, ast.Attribute) and f.attr == name)
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
    funcs = {n.name: n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

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

    launchers = [fn for fn in funcs.values()
                 if "Popen" in calls_in(fn)]
    if not launchers:                      # 模块级 Popen：退回全文件
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
            if not bare.strip():          # 整个值就是一个表达式：由前一步解析出的具体路径
                continue
            if "*" in bare or "?" in bare:
                offenders.append(f"{wf.name}:{i} {m.group(1)}: {val}")
    assert not offenders, (
        "这些输入只收一个路径，喂 glob 会被原样当成文件名：\n  " + "\n  ".join(offenders))
