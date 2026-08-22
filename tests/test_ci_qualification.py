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
