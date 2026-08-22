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
import subprocess
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


# ---------------- bootstrap_lab_runner.sh ------------------------------------
# 这三条都来自 2026-08-22 配置实验室 runner 时踩到的真实故障：脚本自称
# 「准备完成」，而那台机器一个 lab job 都跑不起来。

BOOTSTRAP = CI_DIR / "bootstrap_lab_runner.sh"


def _bootstrap() -> str:
    return BOOTSTRAP.read_text(encoding="utf-8")


def test_apt_list_only_contains_real_debian_packages():
    """`flock` 不是 Debian/Ubuntu 的包名——那个二进制来自 util-linux。

    写进 APT_PACKAGES 的后果不是「多装一个包」，是 `apt-get install` 整条命令
    以 `E: Unable to locate package flock` 失败，而它是安装路径的**第一步**。
    于是这个脚本在 Ubuntu 上从来没跑完过一次，实验室 runner 也就一直没配起来。

    偏偏 `--check` 查的是**二进制**（`command -v flock`，util-linux 永远在），
    所以检查一路绿灯、安装当场就死——两边看的不是同一个东西。这条用例只钉
    APT 表；检查表里保留 flock 是对的，那正是要确认的事。
    """
    src = _bootstrap()
    apt = src.split("APT_PACKAGES=(", 1)[1].split(")", 1)[0]
    pkgs = [w for ln in apt.splitlines() for w in ln.split("#", 1)[0].split()]
    assert pkgs, "APT_PACKAGES 解析成了空表——这条用例本身失效了"
    for phantom in ("flock", "ulimit", "systemctl"):
        assert phantom not in pkgs, \
            f"{phantom} 不是 Debian 包名，写进去会让 apt-get install 整条失败"
    assert "flock" in src, "检查表里仍应确认 flock 这个命令在不在"


def test_fd_limit_is_measured_on_the_runner_service_not_the_bootstrap_shell():
    """旧实现读 `ulimit -n`，量的是 sudo 起的 root shell，与 job 无关。

    两个数可以一个 65536 一个 1024，方向还任意。而 lab_preflight 的
    「文件描述符上限」是**硬阻断**，所以量错对象的代价不是提示不准，是脚本
    说完「准备完成」之后那台机器一个 lab job 都跑不起来——正是本次实测。
    """
    src = _bootstrap()
    # 只读的 FD 判据现在定义在 --check **之前**（两条路共用），所以不能再按
    # 「say 文件描述符上限 → say 准备完成」这一段来切；那样切会漏掉判据本体。
    fd = src.split("NOFILE_MIN=", 1)[1]
    # 只看**可执行的那几行**：解释这段历史的注释里必然出现 `ulimit -n`，
    # 连注释一起判的话，写清楚为什么反而会让用例红（仓库里
    # test_orphan_check_does_not_rely_on_a_dead_parent_pid 踩过同一个坑）。
    code = "\n".join(ln for ln in fd.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "ulimit -n" not in code, \
        "又回去量 bootstrap 自己那个 shell 了——那不是 job 跑在里面的进程"
    assert "/proc/" in fd and "limits" in fd, "要读 runner 服务真实进程的 limits"
    assert "systemctl restart" in fd, "写完 drop-in 必须告诉人要重启才生效"
    # unit 的发现只有一处（工具探测那边解析服务 PATH 用的是同一张表）。
    # 两处各写一份的话，会出现「工具按 A 实例的配置查、FD 按 B 实例的进程量」。
    assert "discover_runner_units" in code, "FD 那段要走共用的 unit 发现"
    disc = src.split("\ndiscover_runner_units() {", 1)[1].split("\n}\n", 1)[0]
    assert "actions.runner" in disc, "要按 runner 的 systemd unit 名去找"


def test_bootstrap_and_preflight_agree_on_the_fd_threshold():
    """阈值写在两个文件里（一个 shell 一个 Python，没法共享常量）。

    分叉的表现最难查：bootstrap 说配好了，preflight 说不够——两边都「按自己的
    标准」是对的。所以按 patchspec ↔ Rust 那套纪律，用一条用例把它们对拍。
    """
    import re
    boot = re.search(r"NOFILE_MIN=(\d+)", _bootstrap())
    assert boot, "bootstrap 里找不到 NOFILE_MIN"
    pf = (CI_DIR / "lab_preflight.py").read_text(encoding="utf-8")
    pre = re.search(r"soft >= (\d+)", pf)
    assert pre, "lab_preflight 里找不到 FD 阈值"
    assert boot.group(1) == pre.group(1), (
        f"阈值分叉：bootstrap={boot.group(1)} preflight={pre.group(1)}——"
        "bootstrap 会说配好了，而 preflight 当场拦下整个 lab job")


def test_tools_are_never_probed_with_the_callers_own_path():
    """判据不许来自「谁在跑这个脚本」——root 的 PATH 和管理员的 PATH 都不算。

    最初的错是以 root 查：脚本**自己**给的装法把 cargo 装进
    `~$RUNNER_USER/.cargo/bin`，root 的 PATH 里根本没有，于是一台配置正确的
    机器被判成「1 项未就绪」，而提示写着「去掉 --check 重跑以安装」——重跑也
    不会装（cargo 那一支只是 warn）。第二个错是改用 $RUNNER_USER 的**登录**
    shell：那读 ~/.profile，`.env` 没配的机器照样报 ✓，而 job 一起来就
    command not found。两个错同一个形状：量错了对象。

    对的对象只有一个——runner 服务真正生效的 PATH。所以探测必须 `env -i`
    清干净再显式喂 `$SERVICE_PATH`，绝不继承调用方的环境。
    """
    src = _bootstrap()
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    body = code.split("\nrun_in_service_path() {", 1)[1].split("\n}\n", 1)[0]
    assert "env -i" in body, \
        "没清环境——继承调用方的 PATH 等于又把「谁在跑脚本」混进判据"
    assert 'PATH="$SERVICE_PATH"' in body, "要显式喂 runner 服务的 PATH"
    assert '-i sh -lc' not in body, "又走回登录 shell 了（那读的是 ~/.profile）"
    assert 'sudo -u "$RUNNER_USER"' in body, \
        "有 root 时要以 $RUNNER_USER 执行——光有路径不够，还得那个账号跑得起来"


# ---- 服务 PATH：判据必须与 job 真实生效的环境一致 -----------------------------
# 下面这几条**不是文本断言**：它们把脚本里的函数原样抠出来，喂真实 fixture
# 跑一遍。文本断言只能证明「某几个字还在」，证明不了解析对不对——而本轮两条
# 意见指的恰恰是「量的对象错了」，那只有真跑一遍才看得见。


def _bootstrap_funcs(*names: str) -> str:
    """把脚本里指定的几个 shell 函数原样抠出来（含函数体）。

    脚本开头就 `die` 在非 Linux 上，整份 source 不进来；而这几个函数不碰任何
    全局，抠出来单独跑与它们在脚本里跑是同一份代码——**不是复制一份**。
    """
    src = _bootstrap()
    out = []
    for name in names:
        head = f"\n{name}() {{\n"
        assert head in src, f"脚本里找不到函数 {name}()"
        body = src.split(head, 1)[1].split("\n}\n", 1)[0]
        out.append(f"{name}() {{\n{body}\n}}")
    return "\n".join(out)


def _probe_posix_bash() -> tuple[bool, str]:
    """有没有一个**能跑 POSIX 脚本的** bash——不是「PATH 上有没有叫 bash 的东西」。

    windows-latest 上 `bash` 解析到的是 **WSL 的 `bash.exe`**：它确实在 PATH 上、
    `shutil.which("bash")` 也返回真，但没装发行版，于是往 stdout 打一段 **UTF-16**
    的 "Windows Subsystem for Linux has no installed distributions" 并 rc=1。
    朴素的「有没有 bash」守卫一条都挡不住——判据的主语错了，与这个 PR 修的那批
    缺陷是同一个形状。所以这里**真跑一次**，并按字节比对（不解码：那段 UTF-16
    用 text=True 读会当场 UnicodeDecodeError，探测本身就该扛得住）。

    Windows 直接判定不可用：被测对象是给 Linux runner 写的 bash 脚本，脚本自己
    第一件事就是 `uname -s` 不是 Linux 就 die。就算 PATH 上排在前面的是 Git Bash
    （探测能过），它的 MSYS 路径改写也会让 `/usr/bin` 这类断言变成另一回事——
    那时红的不是被测代码，是测试环境。
    """
    if os.name == "nt":
        return False, "Windows：被测脚本本身只支持 Linux（脚本开头 uname 不符即 die）"
    try:
        r = subprocess.run(["bash", "-c", "echo ok"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"起不来 bash：{type(exc).__name__}: {exc}"
    if r.returncode != 0:
        return False, f"bash -c 'echo ok' 回了 {r.returncode}，stdout={r.stdout[:120]!r}"
    if r.stdout.strip() != b"ok":
        return False, f"bash -c 'echo ok' 的 stdout 是 {r.stdout[:120]!r}，不是 b'ok'"
    return True, "ok"


_POSIX_BASH_OK, _POSIX_BASH_DETAIL = _probe_posix_bash()
requires_posix_bash = pytest.mark.skipif(
    not _POSIX_BASH_OK,
    reason=f"没有能跑 POSIX 脚本的 bash，跳过这批 shell 用例（{_POSIX_BASH_DETAIL}）")


def _run_sh(script: str, *args: str) -> subprocess.CompletedProcess:
    # 显式 utf-8：跟着 locale 走的话，同一段脚本在别的机器上会解出不同的字节，
    # 而本文件里正好有一条用例是靠「输出不是合法 UTF-8」抓到吞字节的
    # （test_no_bare_variable_is_glued_to_a_cjk_character），所以**不设**
    # errors="replace"——那会把那个信号一起抹掉。
    return subprocess.run(["bash", "-c", script, "sh"] + list(args),
                          capture_output=True, text=True, encoding="utf-8")


@requires_posix_bash
def test_service_path_prefers_env_over_dot_path(tmp_path):
    """`.env` 的 PATH= 覆盖 `.path`——因为 Listener 读 .env 在 runsvc.sh 之后。

    实验室 runner（tavotto-ci-01）上实测过这条优先级：`.path` 里没有
    `~/.cargo/bin`、`.env` 里有，而 job 里的 `cargo build` 是成功的。
    """
    root = tmp_path / "actions-runner"
    root.mkdir()
    (root / ".path").write_text("/usr/bin:/bin\n", encoding="utf-8")
    (root / ".env").write_text(
        "PATH=/home/runner/.cargo/bin:/usr/bin:/bin\n"
        "RUNNER_TOOL_CACHE=/opt/hostedtoolcache\n", encoding="utf-8")
    script = _bootstrap_funcs("env_path_of_root", "service_path_of_root") + \
        '\nservice_path_of_root "$1"\n'
    r = _run_sh(script, str(root))
    assert r.returncode == 0, r.stderr
    value, source = r.stdout.rstrip("\n").split("\t")
    assert value == "/home/runner/.cargo/bin:/usr/bin:/bin", \
        f"没取 .env 的 PATH，取到的是 {value!r}——那是 runsvc.sh 之前的那一层"
    assert source.endswith("/.env")


@requires_posix_bash
def test_service_path_falls_back_to_dot_path_when_env_has_none(tmp_path):
    """只认 `.env` 是不够的：从登录 shell 跑过 config.sh 的机器 cargo 在 `.path` 里。

    `.path` 存的是 `echo $PATH>.path`，即配置那一刻那个 shell 的 PATH。那种机器
    不配 `.env` 也跑得起来，只认 `.env` 会把它**误报成红**——与本条要修的假绿
    是同一个错的两个方向。
    """
    root = tmp_path / "actions-runner"
    root.mkdir()
    (root / ".path").write_text("/home/runner/.cargo/bin:/usr/bin:/bin\n",
                                encoding="utf-8")
    (root / ".env").write_text("RUNNER_TOOL_CACHE=/opt/hostedtoolcache\n",
                               encoding="utf-8")
    script = _bootstrap_funcs("env_path_of_root", "service_path_of_root") + \
        '\nservice_path_of_root "$1"\n'
    r = _run_sh(script, str(root))
    assert r.returncode == 0, r.stderr
    value, source = r.stdout.rstrip("\n").split("\t")
    assert value == "/home/runner/.cargo/bin:/usr/bin:/bin"
    assert source.endswith("/.path")


@requires_posix_bash
def test_env_parsing_matches_the_runners_own_loader(tmp_path):
    """逐字复现 runner 的 `LoadAndSetEnv`：第一个 `=` 切开、后面的覆盖前面的。

    它**不认注释**——`# PATH=/x` 只是键叫 `# PATH` 而已。所以判据必须是
    「`=` 之前那段整体等于 PATH」：拿子串去匹配的话，一条被注释掉的旧 PATH
    会被当成生效的那条，而那正是运维为了排障顺手注释掉的那一行。
    """
    root = tmp_path / "actions-runner"
    root.mkdir()
    (root / ".env").write_text(
        "# 这一行有 = 号但键不是 PATH\n"
        "# PATH=/decoy/commented-out\n"
        "\n"
        "PATH=/first/wins-not\n"
        "PATHX=/decoy/prefix\n"
        "PATH=/real/last-wins\n"
        # **诱饵必须排在真值之后**：全放前面的话，子串匹配（`$0 ~ /PATH=/`）
        # 取「最后一条」照样能撞对答案，这条用例就什么都没证明。
        "MY_PATH=/decoy/suffix-afterwards\n"
        "# PATH=/decoy/commented-out-afterwards\n", encoding="utf-8")
    script = _bootstrap_funcs("env_path_of_root") + '\nenv_path_of_root "$1"\n'
    r = _run_sh(script, str(root))
    assert r.returncode == 0, r.stderr
    assert r.stdout.rstrip("\n") == "/real/last-wins", \
        f"解析与 runner 不一致：{r.stdout!r}"


@requires_posix_bash
def test_unreadable_service_config_fails_instead_of_answering(tmp_path):
    """两个文件都没有时必须**失败**，不许回一个「差不多的」PATH。

    这是 P2 的根：读不到就没有可信答案，退回去查调用方自己的 PATH 只会把假绿
    印成 ✓。失败才能让上层把它算进「未就绪」。
    """
    root = tmp_path / "actions-runner"
    root.mkdir()
    script = _bootstrap_funcs("env_path_of_root", "service_path_of_root") + \
        '\nservice_path_of_root "$1"\n'
    r = _run_sh(script, str(root))
    assert r.returncode != 0, f"读不到配置却成功返回了：{r.stdout!r}"
    assert r.stdout.strip() == "", f"读不到配置却输出了 PATH：{r.stdout!r}"


@requires_posix_bash
def test_probe_label_never_claims_an_account_it_did_not_use():
    """标签由决定探测方式的那个变量算出来，不另写一份文案。

    P2 的表现就是这个：非 root、且不是 $RUNNER_USER 的管理员跑 `--check`，
    探测以**他自己**的身份执行，输出却写着「按 $RUNNER_USER 的 PATH 查」。
    管理员装了而 runner 没装是假绿，反过来是假红，两个方向都错。
    """
    funcs = _bootstrap_funcs("probe_label")
    base = 'RUNNER_USER=github-runner\nSERVICE_SRC=/srv/r/.env\n' + funcs + "\n"

    r = _run_sh(base + 'PROBE_MODE=sudo PROBE_KIND=service probe_label')
    assert "github-runner" in r.stdout and "服务的 PATH" in r.stdout, r.stdout

    r = _run_sh(base + 'PROBE_MODE=foreign PROBE_KIND=service probe_label')
    me = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
    assert me in r.stdout, \
        f"没说出真正执行探测的账号：{r.stdout!r}"
    assert "验不了" in r.stdout, \
        f"以别人的身份查完却没说这一点：{r.stdout!r}"

    # 「登录 PATH」这一档（runner 还没注册）必须自己说清它不是服务 PATH。
    r = _run_sh(base + 'PROBE_MODE=sudo PROBE_KIND=login probe_label')
    assert "不是服务 PATH" in r.stdout, r.stdout

    # 点名一个 .env、而那份配置由 N 个实例共用（实验室那台有四个），会让运维
    # 只改其中一个，剩下几个照旧坏着——而 job 落在哪个实例上是调度决定的。
    r = _run_sh(base + 'SERVICE_COUNT=4 PROBE_MODE=sudo PROBE_KIND=service probe_label')
    assert "另 3 个实例同配置" in r.stdout, \
        f"没说清这份 PATH 覆盖几个实例：{r.stdout!r}"
    r = _run_sh(base + 'SERVICE_COUNT=1 PROBE_MODE=sudo PROBE_KIND=service probe_label')
    assert "实例同配置" not in r.stdout, f"只有一个实例却说有别的：{r.stdout!r}"


def test_check_mode_probes_the_service_path_not_a_login_shell():
    """`--check` 的 ✓ 只能由服务 PATH 给出，登录 shell 只配当 ✗ 的补充说明。

    上一版用 `sudo -u … -i`（login shell，读 ~/.profile）查 cargo：`.env` 没配
    的机器照样报 ✓，而 job 一起来就 command not found——正是这道检查本该逮住的
    那种错配。所以 `check_cmd` 的**通过判据**必须走 probe_cmd/服务 PATH。
    """
    src = _bootstrap()
    body = src.split("\ncheck_cmd() {", 1)[1].split("\n}\n", 1)[0]
    code = "\n".join(ln for ln in body.splitlines()
                     if not ln.lstrip().startswith("#"))
    ok = code.split("printf", 1)[0]          # ✓ 之前，即判据本身
    assert "probe_cmd" in ok, "✓ 的判据没走服务 PATH"
    assert "as_runner_login" not in ok, \
        "✓ 的判据又回到登录 shell 了——.env 没配的机器会报绿"
    # 登录 shell 仍在，但只出现在 ✗ 之后（区分「没装」与「装了没接上」）
    assert "as_runner_login" in code, "丢掉了「装了但不在服务 PATH 上」这条提示"


@requires_posix_bash
def test_the_pass_criterion_never_reaches_a_login_shell(tmp_path):
    """✓ 只能由服务 PATH 给出——沿整条调用链验，不是只看最外面那一层。

    上一版用 `sudo -u … -i`（login shell，读 ~/.profile）查 cargo：`.env` 没配
    的机器照样报 ✓，而 job 一起来就 command not found，正是这道检查本该逮住的
    那种错配。**只断言 `check_cmd` 调了 `probe_cmd` 是不够的**：把登录 shell
    塞回 `probe_cmd` 内部，那种断言一样绿（实测过）。所以这里把登录 shell 换成
    哨兵，判据只要碰它一下就会拿到假路径。
    """
    svcbin = tmp_path / "svcbin"
    svcbin.mkdir()
    tool = svcbin / "onlyinservicepath"
    tool.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    tool.chmod(0o755)

    stub = 'as_runner_login() { printf "/DECOY/FROM-LOGIN-SHELL\\n"; }\n'
    script = ("RUNNER_USER=nobody\nPROBE_MODE=foreign\nPROBE_KIND=service\n"
              # 服务 PATH 里得有 sh 本身：`env -i PATH=… sh -c` 是拿这个
              # PATH 去找 sh 的。真机上的服务 PATH 当然含 /usr/bin。
              f'SERVICE_PATH={svcbin}:/usr/bin:/bin\n' + stub
              + _bootstrap_funcs("run_in_service_path", "probe_cmd")
              + '\nprobe_cmd "$1"\n')

    r = _run_sh(script, "command -v -- onlyinservicepath")
    assert r.stdout.strip() == str(tool), \
        f"服务 PATH 上的工具没查出来（拿到 {r.stdout.strip()!r}）"

    # 服务 PATH 上没有的东西，判据必须回空——绝不能靠登录 shell 补上一个 ✓
    r = _run_sh(script, "command -v -- notonanypathatall")
    assert "DECOY" not in r.stdout, \
        "判据回落到登录 shell 了——.env 没配的机器会报绿"
    assert r.stdout.strip() == "", f"凭空查出了 {r.stdout.strip()!r}"


def _bootstrap_dispatch() -> str:
    """解析结果 → PROBE_KIND 的那段分派（顶层代码，不是函数，只能按区间抠）。"""
    src = _bootstrap()
    head = 'SERVICE_PATHS="$('
    assert head in src
    return head + src.split(head, 1)[1].split("\ncheck_cmd() {", 1)[0]


@requires_posix_bash
def test_a_third_party_caller_gets_no_answer_rather_than_the_wrong_one():
    """既不是 root 也不是 $RUNNER_USER、又读不到 runner 配置 → **无解**，不是降级。

    `--check` 明确允许无 root 跑（前置检查那一行），所以调用方完全可能是第三个
    账号。这时按**他自己**的 PATH 查完再当成结论，管理员装了而 runner 没装是
    假绿，反过来是假红——两个方向都错，而假绿更坏：它会让人以为这台机器可以
    接活。所以这一档必须落到 `unresolved`（下面另一条钉它算进「未就绪」），
    而不是悄悄降级成 `login` 去查别人的 PATH。

    有 root（或本来就是 runner）时降级成 `login` 才是对的：那是「runner 还没
    注册」的正常状态，与 FD 那段的处理一致。
    """
    stubs = ("discover_runner_units() { :; }\n"      # 一个 unit 都没有
             "root_of_unit() { return 1; }\n"
             "service_path_of_root() { return 1; }\n"
             "warn() { :; }\n"
             "RUNNER_USER=github-runner\n")
    tail = '\nprintf "%s" "$PROBE_KIND"\n'

    r = _run_sh(stubs + "PROBE_MODE=foreign\n" + _bootstrap_dispatch() + tail)
    assert r.stdout.strip() == "unresolved", \
        f"第三方账号读不到配置却给了结论：PROBE_KIND={r.stdout.strip()!r}"

    r = _run_sh(stubs + "PROBE_MODE=sudo\n" + _bootstrap_dispatch() + tail)
    assert r.stdout.strip() == "login", \
        f"有 root 时该降级查「装没装」，却成了 {r.stdout.strip()!r}"


def test_an_unresolved_service_path_counts_as_not_ready():
    """`unresolved` 必须算进 MISSING，且**不许**再去跑工具表。

    「检查通过」而其实一项都没验，比没有这道检查更坏——它还在报平安。
    """
    src = _bootstrap()
    branch = src.split('if [ "$PROBE_KIND" = unresolved ]; then', 1)[1] \
                .split('elif [ "$PROBE_KIND" = login ]', 1)[0]
    assert "MISSING=$((MISSING + 1))" in branch, \
        "读不到服务 PATH 却没算进未就绪——那句「检查通过」是假的"
    assert "check_tools_on" not in branch, \
        "没有可信 PATH 还是把工具表跑了一遍，等于按调用方的环境出结论"
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


def _bootstrap_rows() -> str:
    """SERVICE_ROWS / SERVICE_BAD / SERVICE_PATHS 那一段（顶层代码，按区间抠）。"""
    src = _bootstrap()
    head = 'SERVICE_ROWS="$('
    assert head in src
    return head + src.split(head, 1)[1].split("\nif [ -n \"$SERVICE_PATHS\" ]", 1)[0]


@requires_posix_bash
def test_an_unreadable_instance_is_reported_not_silently_dropped(tmp_path):
    """一个实例解析不了，不许从表里悄悄消失。

    早先这里是 `continue`：四个实例里坏一个，另外三个读得到且通过，`--check`
    就报「检查通过」——而 job 落在哪个实例上是调度决定的。沉默地少验一个，与
    按错的账号验是同一类错：**答案的覆盖面与它宣称的不一致**。
    """
    good = tmp_path / "good"
    good.mkdir()
    (good / ".env").write_text("PATH=/usr/bin:/bin\n", encoding="utf-8")
    bad = tmp_path / "bad"
    bad.mkdir()          # .env / .path 都没有

    stubs = (
        'discover_runner_units() { printf "u-good\\nu-bad\\n"; }\n'
        f'root_of_unit() {{ [ "$1" = u-good ] && printf "{good}" || printf "{bad}"; }}\n'
        'stale_config_of_root() { return 1; }\n'
    )
    script = (stubs + _bootstrap_funcs("env_path_of_root", "service_path_of_root")
              + "\n" + _bootstrap_rows()
              + '\nprintf "BAD<%s>\\nPATHS<%s>\\n" "$SERVICE_BAD" "$SERVICE_PATHS"\n')
    r = _run_sh(script)
    assert "u-bad" in r.stdout, \
        f"读不到配置的实例被静默丢掉了：{r.stdout!r}"
    assert "u-good" not in r.stdout.split("PATHS<")[0], "好实例被误判成坏的"
    assert "/usr/bin:/bin" in r.stdout.split("PATHS<")[1], "好实例没进待查表"


@requires_posix_bash
def test_config_edited_after_the_service_started_is_not_trusted(tmp_path):
    """改了 `.env` 却没重启 = 跑着的 listener 还是旧环境，不能算配好了。

    `.env` 的 PATH 是 `Runner.Listener` 启动时一次性读进内存的，改文件不会传导
    到已经在跑的进程（`/proc/<pid>/environ` 是 exec 快照，只反映 `.path` 那层，
    实测确认过，所以「去读活进程的 PATH」这条路对 `.env` 层根本走不通）。
    与最初那个假绿同一个形状，只是错在时间维度上。
    """
    root = tmp_path / "r"
    root.mkdir()
    (root / ".env").write_text("PATH=/usr/bin:/bin\n", encoding="utf-8")

    stubs = ('discover_runner_units() { printf "u1\\n"; }\n'
             f'root_of_unit() {{ printf "{root}"; }}\n')
    body = (_bootstrap_funcs("env_path_of_root", "service_path_of_root") + "\n"
            + _bootstrap_rows()
            + '\nprintf "BAD<%s>\\nPATHS<%s>\\n" "$SERVICE_BAD" "$SERVICE_PATHS"\n')

    # 配置比服务旧 —— 正常，进待查表
    r = _run_sh(stubs + 'stale_config_of_root() { return 1; }\n' + body)
    assert "/usr/bin:/bin" in r.stdout.split("PATHS<")[1], r.stdout
    assert r.stdout.split("BAD<")[1].startswith(">"), f"误报成过期：{r.stdout!r}"

    # 配置比服务新 —— 必须报出来，且**不进**待查表（否则照旧报绿）
    r = _run_sh(stubs + 'stale_config_of_root() { printf "%s/.env\\n" "$2"; }\n' + body)
    assert "没重启" in r.stdout, f"改了配置没重启，却没报出来：{r.stdout!r}"
    assert r.stdout.split("PATHS<")[1].startswith(">"), \
        f"过期的配置还是进了待查表：{r.stdout!r}"


@requires_posix_bash
def test_all_instances_unreadable_is_not_reported_as_not_registered():
    """全都读不出来 ≠ 还没注册。降级查登录 PATH 会把坏机器报成「只差注册」。"""
    stubs = ("discover_runner_units() { :; }\nroot_of_unit() { return 1; }\n"
             "service_path_of_root() { return 1; }\nwarn() { :; }\n"
             "RUNNER_USER=github-runner\nPROBE_MODE=sudo\n"
             'SERVICE_BAD="u1\tu1 的 .env 读不到"\nSERVICE_PATHS=""\n')
    r = _run_sh(stubs + _bootstrap_dispatch() + '\nprintf "%s" "$PROBE_KIND"\n')
    assert r.stdout.strip() == "unresolved", \
        f"实例坏着却当成「还没注册」降级了：{r.stdout.strip()!r}"


def test_a_reported_bad_instance_also_counts_as_not_ready():
    """报出来还不够，必须算进 MISSING —— 否则末尾照旧是一句「检查通过」。

    ✗ 打在屏幕上而退出码是 0，CI 与脚本调用方看到的仍然是通过；扫读的人也只会
    记住最后那一行。「报了但不阻断」在这里等于没报。
    """
    src = _bootstrap()
    branch = src.split('if [ -n "$SERVICE_BAD" ]; then', 1)[1] \
                .split('if [ "$PROBE_KIND" = unresolved ]', 1)[0]
    assert "MISSING=$((MISSING + 1))" in branch, \
        "坏实例报出来了却没算进未就绪——末尾还是「检查通过」，等于没报"


def test_a_foreign_caller_is_warned_not_just_footnoted():
    """残差不能只写在那行标签的括号里——末尾一句「检查通过」会盖过它。

    非 root、且不是 $RUNNER_USER 的调用方：PATH 判断是准的（`.env`/`.path` 谁读
    都一样），没验的是「这些二进制 $RUNNER_USER 有没有权限执行」。这一项值得
    一条 warn，而不是一个括号。
    """
    src = _bootstrap()
    block = src.split('say "检查模式：不修改任何东西"', 1)[1] \
               .split('if [ "$PROBE_KIND" = unresolved ]', 1)[0]
    assert '"$PROBE_MODE" = foreign' in block, "第三方调用方那一档没有单独提示"
    assert "warn " in block, "残差只留在标签里，末尾的「检查通过」会盖过它"


def test_install_mode_checks_cargo_against_every_service_path():
    """安装路径的 cargo 也要逐份服务 PATH 查，不能只取第一份。

    原先这里是 `head -1`，理由写的是「安装路径只是给人看的提示」——可它末尾照样
    打「准备完成」。实例之间配置不同时，第一份找得到 cargo 就报已装，而 job 落到
    另一份上照旧 command not found。与 --check 那边同一条纪律：**答案的覆盖面
    必须对得上它宣称的**。
    """
    src = _bootstrap()
    rust = src.split('say "Rust"', 1)[1].split('say "文件描述符上限"', 1)[0]
    code = "\n".join(ln for ln in rust.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "$SERVICE_PATHS" in code and "while IFS=" in code, \
        "安装路径没有逐份服务 PATH 查 cargo"
    assert "head -1" not in code, "又只取第一份服务 PATH 了"


@requires_posix_bash
def test_an_unlimited_descriptor_limit_is_not_treated_as_too_low():
    """`LimitNOFILE=infinity` → /proc 写 `unlimited`、systemctl 写 `infinity`。

    `[ unlimited -ge 4096 ]` 报 "integer expression expected" 并走进失败分支，
    于是脚本用一个 65536 的 drop-in 去**降低**一个本来就够用的设置，而且要等
    重启才发作。判据收敛成一个 `_nofile_ge`，这里直接跑它。
    """
    script = ("NOFILE_MIN=4096\n" + _bootstrap_funcs("_nofile_ge")
              + '\nif _nofile_ge "$1"; then echo YES; else echo NO; fi\n')
    for value, want in [("unlimited", "YES"), ("infinity", "YES"),
                        ("65536", "YES"), ("4096", "YES"),
                        ("1024", "NO"), ("", "NO"), ("n/a", "NO")]:
        r = _run_sh(script, value)
        assert r.stdout.strip() == want, \
            f"_nofile_ge({value!r}) 回了 {r.stdout.strip()!r}，应是 {want}"
        assert "integer expression" not in r.stderr, \
            f"{value!r} 被丢进数值比较里炸了：{r.stderr!r}"


@requires_posix_bash
def test_a_configured_but_unrestarted_limit_is_not_overridden():
    """「跑着的不够」≠「没配」——后者才该写 drop-in。

    管理员刚把 LimitNOFILE 调高、还没重启时，/proc 里还是旧值而配置里已是新值。
    这时写我们自己的 drop-in 会按字典序排在他后面**把他刚配好的值顶掉**
    （systemd.unit(5)：.d 下按文件名字典序加载，后面的赋值赢），而且同样要等
    重启才发作——用一个待生效的配置换掉另一个，纯属帮倒忙。
    """
    script = ("NOFILE_MIN=4096\n"
              + _bootstrap_funcs("_nofile_ge", "nofile_state")
              + '\nnofile_state "$1" "$2"\n')
    cases = [
        ("65536", "65536",   "ok"),            # 两边都够
        # **配置读不到不算「无所谓」**：实测 `systemctl show -p LimitNOFILESoft`
        # 连不存在的 unit 都回系统默认值（1048576），从不回空；真回空/回 0 说明
        # 这台机器上问不出配置态，那就不能宣称 ok（后面 verify 也会失败并让
        # 脚本非零退出）。这条原先写着 ok，编码的正是复审 :531 指出的那个 bug。
        ("unlimited", "0",   "needs_dropin"),
        ("1024", "200000",   "pending"),       # 管理员配好了，只差重启
        ("1024", "infinity", "pending"),
        ("1024", "1024",     "needs_dropin"),  # 真没配
        ("1024", "",         "needs_dropin"),  # systemctl 读不到 → 当没配
    ]
    for running, configured, want in cases:
        r = _run_sh(script, running, configured)
        assert r.stdout.strip() == want, (
            f"nofile_state(running={running!r}, configured={configured!r}) "
            f"回了 {r.stdout.strip()!r}，应是 {want}")


def test_preflight_also_accepts_an_unlimited_descriptor_limit(monkeypatch):
    """preflight 那边有同形状的一条：RLIM_INFINITY 是 -1，比大小直接判成不够。

    两边必须一起放行——否则一台设了 `infinity` 的机器会被 bootstrap 说「够了」、
    被 preflight 当场拦下，而两边都「按自己的标准」是对的。这与
    test_bootstrap_and_preflight_agree_on_the_fd_threshold 是同一条纪律。
    """
    import importlib
    # POSIX-only 模块；Windows 上 lab_preflight 自己也把 resource 设成 None，
    # 并让「文件描述符上限」这项直接跳过——那条路径本来就没有 rlimit 可判。
    resource = pytest.importorskip(
        "resource", reason="resource 是 POSIX-only；Windows 上没有 rlimit 可判")
    pf = importlib.import_module("lab_preflight")

    monkeypatch.setattr(pf.resource, "getrlimit",
                        lambda _what: (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
    fd = [c for c in pf.check_environment() if c.name == "文件描述符上限"]
    assert fd, "preflight 里找不到「文件描述符上限」这项"
    assert fd[0].ok, f"无上限被判成了不够：{fd[0].detail}"
    assert "unlimited" in fd[0].detail, f"没说清是无上限：{fd[0].detail}"

    # 真的不够时照旧要拦下来
    monkeypatch.setattr(pf.resource, "getrlimit", lambda _what: (1024, 4096))
    fd = [c for c in pf.check_environment() if c.name == "文件描述符上限"]
    assert not fd[0].ok, "1024 应当判成不够"


@requires_posix_bash
def test_the_dropin_never_clobbers_a_file_it_did_not_write(tmp_path):
    """脚本开头写着「不删任何未知文件」——`cat > limits.conf` 违背了它。

    `limits.conf` 正是管理员给 drop-in 起名时最顺手的那个词（限额之外的加固、
    环境变量都常放在里面）。截断掉的后果要等下次重启才发作，那时谁也想不到是
    bootstrap 干的。所以：文件名带自己的名字，且只覆盖确认是自己写的那一份。
    """
    funcs = _bootstrap_funcs("write_nofile_dropin")
    src = _bootstrap()
    marker = src.split('NOFILE_DROPIN_MARKER="', 1)[1].split('"', 1)[0]
    name = src.split('NOFILE_DROPIN_NAME="', 1)[1].split('"', 1)[0]
    assert name != "limits.conf", "又用回那个管理员最可能占用的名字了"
    base = (f'NOFILE_DROPIN_MARKER="{marker}"\nNOFILE_DROPIN_NAME="{name}"\n'
            'NOFILE_MIN=4096\nwarn() { printf "WARN:%s\\n" "$*"; }\n'
            + funcs + '\nwrite_nofile_dropin "$1"; echo "rc=$?"\n')

    # ① 管理员自己的 limits.conf 必须一个字节不动
    d = tmp_path / "a.d"
    d.mkdir()
    admin = d / "limits.conf"
    admin.write_text("[Service]\nEnvironment=FOO=bar\n", encoding="utf-8")
    r = _run_sh(base, str(d))
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    assert admin.read_text(encoding="utf-8") == "[Service]\nEnvironment=FOO=bar\n", \
        "管理员的 limits.conf 被截断了"
    assert (d / name).exists(), "自己那份 drop-in 没写出来"
    assert "LimitNOFILE=65536" in (d / name).read_text(encoding="utf-8")

    # ② 同名文件已存在但不是我们写的 → 拒绝覆盖，并且报失败
    d2 = tmp_path / "b.d"
    d2.mkdir()
    theirs = d2 / name
    theirs.write_text("[Service]\nLimitNOFILE=1000000\n", encoding="utf-8")
    r = _run_sh(base, str(d2))
    assert "rc=1" in r.stdout, f"覆盖了别人的同名文件：{r.stdout!r}"
    assert theirs.read_text(encoding="utf-8") == "[Service]\nLimitNOFILE=1000000\n"
    assert "WARN:" in r.stdout, "拒绝覆盖却没说一声"

    # ③ 自己上一次写的那份可以照旧覆盖（否则升级脚本后永远改不动它）
    d3 = tmp_path / "c.d"
    d3.mkdir()
    mine = d3 / name
    mine.write_text(f"# {marker}\n[Service]\nLimitNOFILE=8192\n", encoding="utf-8")
    r = _run_sh(base, str(d3))
    assert "rc=0" in r.stdout, r.stdout
    assert "LimitNOFILE=65536" in mine.read_text(encoding="utf-8"), "自己写的那份没更新"


def test_no_bare_variable_is_glued_to_a_cjk_character():
    """`$VAR中文` 必须写成 `${VAR}中文`——否则在别人的机器上会吞掉一个字节。

    bash 用 `isalnum()` 逐**字节**扫变量名。UTF-8 locale 下高位字节不算字母，
    到此为止；但在 C/Latin-1 一类的 locale 里 0xEF 就是字母 `ï`，于是
    `$NOFILE_MIN，` 被当成变量 `NOFILE_MIN\\xef`——未定义、展开成空，还顺手
    把那个中文字的第一个字节吃掉，输出从此不是合法 UTF-8。

    发现方式很典型：本机 pytest 跑得好好的，换个 locale 的子进程里就
    `UnicodeDecodeError`。**这正是「只在别人电脑上发生」的那一类**，与
    tests/test_windows_regressions.py 是同一条纪律，所以钉成结构性判据。
    """
    import re
    src = _bootstrap()
    bad = []
    for i, line in enumerate(src.splitlines(), 1):
        if line.lstrip().startswith("#"):      # 注释不展开，不判
            continue
        for m in re.finditer(r"\$[A-Za-z_][A-Za-z0-9_]*(?=[^\x00-\x7f])", line):
            bad.append(f"L{i}: {m.group(0)} 紧跟非 ASCII 字符")
    assert not bad, "变量名与中文粘在一起，换个 locale 就会吞字节：\n" + "\n".join(bad)


def test_the_pending_branch_actually_skips_writing_the_dropin():
    """判对了状态还不够——`pending` 那一支必须真的不写。

    `nofile_state` 回 `pending` 而循环照旧往下走到 `write_nofile_dropin`，
    结果与没判一模一样：管理员刚配好的值被我们排在后面的文件顶掉。
    """
    src = _bootstrap()
    # `pending)` 现在有两处（--check 只读那份、安装路径写 drop-in 那份），
    # 这条钉的是**安装路径**那份：先切到 say "文件描述符上限" 之后再找。
    install = src.split('say "文件描述符上限"', 1)[1]
    branch = install.split("        pending)", 1)[1].split("        esac", 1)[0]
    assert "continue" in branch, "pending 之后没有 continue，会继续往下写 drop-in"
    assert "write_nofile_dropin" not in branch, "pending 那一支还是写了 drop-in"
    assert "NOFILE_PENDING=$((NOFILE_PENDING + 1))" in branch, \
        "没计数——末尾就不会提醒去重启，那这台机器永远差一次重启没人知道"


def test_the_posix_bash_guard_does_not_skip_where_bash_works():
    """守卫只该挡住「没有能跑 POSIX 脚本的 bash」的平台，不能一跳跳全部。

    做成宁跳勿错的守卫，等于把上面那十几条 shell 用例整个关掉——而且全绿。
    那是空门禁的又一种形态，还比普通空门禁更难发现：`-q` 下跳过只是一个点。
    """
    if os.name == "nt":
        pytest.skip("Windows 上本就没有 POSIX bash，这条只在 POSIX 平台有意义")
    assert _POSIX_BASH_OK, (
        f"POSIX 平台上守卫却判定 bash 不可用（{_POSIX_BASH_DETAIL}）——"
        "这批 shell 用例会被整体跳过而 CI 全绿")


def test_the_posix_bash_guard_rejects_a_wsl_style_stub(tmp_path, monkeypatch):
    """WSL 的 bash.exe **在 PATH 上**，`which` 说有——它只是跑不了 POSIX 脚本。

    windows-latest 上它往 stdout 打一段 UTF-16 的
    "Windows Subsystem for Linux has no installed distributions" 并 rc=1，
    于是断言拿到的是那段乱码而不是 `unresolved` / `ok` / `YES`（本轮 14 条红）。
    所以守卫必须**真跑一次**并核对输出，不能只问「有没有 bash」。

    这里把那个 stub 原样重放（含 UTF-16 与 rc=1），确认探测判它不可用；顺带钉住
    「探测自己不会被那段字节噎死」——按字节比对就是为了这个。
    """
    if os.name == "nt":
        pytest.skip("这条模拟的就是 Windows 的行为，在 POSIX 上跑才有意义")

    stub = tmp_path / "bash"
    stub.write_text(
        "#!/bin/sh\n"
        # printf 的八进制转义写出 UTF-16LE：'W\0i\0n\0...'，与 WSL 实际输出同形
        "printf 'W\\0i\\0n\\0d\\0o\\0w\\0s\\0 \\0S\\0u\\0b\\0s\\0y\\0s\\0t\\0e\\0m\\0'\n"
        "exit 1\n", encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path), prepend=False)

    ok, detail = _probe_posix_bash()
    assert not ok, "把 WSL stub 当成可用的 bash 了——正是本轮 CI 红的那 14 条"
    assert "1" in detail, f"没说清失败原因：{detail!r}"

    # 反过来：一个正常的 bash 必须判成可用，否则守卫就是「一跳跳全部」
    good = tmp_path / "bash"
    good.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    good.chmod(0o755)
    ok, detail = _probe_posix_bash()
    assert ok, f"正常的 shell 被判成不可用：{detail!r}"
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


# ---- 第四轮复审的五条：都在「以再问一次系统为准」这条线上 --------------------


@requires_posix_bash
def test_ok_requires_both_the_running_and_the_configured_limit():
    """只看运行态的 `ok` 会放过「已 daemon-reload 的低配置」。

    那时跑着的进程仍然够 → 报 ok，而下一次**普通重启**就掉到低值，随后
    lab_preflight 拦下每个 job——没人会想到是 bootstrap 说过 ok。
    """
    script = ("NOFILE_MIN=4096\n" + _bootstrap_funcs("_nofile_ge", "nofile_state")
              + '\nnofile_state "$1" "$2"\n')
    cases = [
        ("65536", "65536",   "ok"),
        ("65536", "1024",    "needs_dropin"),   # ← 本条要修的：配置已降，重启就掉
        ("unlimited", "1024", "needs_dropin"),
        ("1024", "200000",   "pending"),
        ("1024", "1024",     "needs_dropin"),
    ]
    for running, configured, want in cases:
        r = _run_sh(script, running, configured)
        assert r.stdout.strip() == want, (
            f"nofile_state(running={running!r}, configured={configured!r}) "
            f"回了 {r.stdout.strip()!r}，应是 {want}")


@requires_posix_bash
def test_a_failed_dropin_write_is_reported_as_failure(tmp_path):
    """重定向失败不会让 `set -e` 生效——函数被当作 `if` 的条件调用。

    目标 immutable、只读文件系统、磁盘满，`cat >` 都会失败而函数照旧走到
    `return 0`，调用方于是打印「已写 drop-in」。
    """
    src = _bootstrap()
    marker = src.split('NOFILE_DROPIN_MARKER="', 1)[1].split('"', 1)[0]
    name = src.split('NOFILE_DROPIN_NAME="', 1)[1].split('"', 1)[0]
    base = (f'NOFILE_DROPIN_MARKER="{marker}"\nNOFILE_DROPIN_NAME="{name}"\n'
            'NOFILE_MIN=4096\nwarn() { printf "WARN\\n"; }\n'
            + _bootstrap_funcs("write_nofile_dropin")
            + '\nwrite_nofile_dropin "$1"; echo "rc=$?"\n')

    # 正常路径仍然成功
    good = tmp_path / "good.d"
    good.mkdir()
    r = _run_sh(base, str(good))
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    assert (good / name).exists()

    # 目标是个**目录** → 重定向必然失败（不依赖 root，也不用真设 immutable）
    bad = tmp_path / "bad.d"
    bad.mkdir()
    (bad / name).mkdir()
    r = _run_sh(base, str(bad))
    assert "rc=1" in r.stdout, \
        f"写不进去却报了成功——调用方会打印「已写 drop-in」：{r.stdout!r}"


@requires_posix_bash
def test_a_refused_or_ineffective_dropin_makes_the_script_exit_nonzero():
    """报出来还不够：照旧走到「准备完成」且退出码 0，自动化就会当成配好了。

    而那台机器的 FD 上限仍在 preflight 阈值之下，每个 lab job 都会被拦下。
    """
    src = _bootstrap()
    branch = src.split("            NOFILE_FAILED=$((NOFILE_FAILED + 1))", 1)[0]
    assert "NOFILE_FAILED=$((NOFILE_FAILED + 1))" in src, "失败没有计数"
    # 计数变量必须先初始化，否则 set -u 下末尾那句自己就炸
    assert "NOFILE_FAILED=0" in src, "NOFILE_FAILED 没初始化"
    # **把末尾那段真跑一遍。** 只断言「tail 里有 NOFILE_FAILED 和 die」是钉在
    # 错的层上：把条件改成 `if false` 两个字符串都还在，用例照样绿（实测）。
    marker = 'if [ "${NOFILE_FAILED:-0}"'
    tail = marker + src.split(marker, 1)[1]
    harness = 'die() { echo "DIE:$*"; exit 1; }\nNOFILE_FAILED="$1"\n' + tail
    r = _run_sh(harness, "2")
    assert r.returncode != 0 and "DIE:" in r.stdout, \
        f"有失败却以 0 结束——自动化会当成配好了：rc={r.returncode} out={r.stdout!r}"
    r = _run_sh(harness, "0")
    assert r.returncode == 0 and "DIE:" not in r.stdout, \
        f"没有失败却也退非零：rc={r.returncode} out={r.stdout!r}"


def test_the_dropin_is_verified_against_systemd_not_assumed():
    """写了文件 ≠ 值生效了：.d 下按字典序加载，排在后面的 99-local.conf 赢。

    把名字排得更靠后是军备竞赛（别人还能再往后一格），正解是写完 daemon-reload
    再问一次 systemd，不生效就如实报出来并指出是哪个文件在覆盖。
    """
    src = _bootstrap()
    fn = src.split("\nverify_nofile_effective() {", 1)[1].split("\n}\n", 1)[0]
    assert "daemon-reload" in fn, "没让 systemd 重读就去问它，问到的还是旧值"
    assert "configured_nofile" in fn, "没有回头再问一次系统"
    assert "systemctl cat" in fn, "不生效时没指出是哪个文件在覆盖"
    # 调用点：写成功之后必须接着验，不能只写不验
    call = src.split("if write_nofile_dropin ", 1)[1].split("then", 1)[0]
    assert "verify_nofile_effective" in call, \
        "写完没验证就报「已写 drop-in」——排在后面的 drop-in 照样赢"



@requires_posix_bash
def test_the_offender_listing_only_treats_file_headers_as_filenames():
    """`systemctl cat` 里 `# /path` 是文件头，而 drop-in **内部**的注释也以 `# ` 开头。

    真机上撞到过：我们自己写的 drop-in 里有三行中文注释，`/^# /{f=$2}` 把最后
    一行的第二个词当成了文件名，于是清单里冒出一行「表现是随机的: LimitNOFILE=…」
    ——而真正该被点名的那个文件反倒没出现。诊断输出里指错文件，比不输出更坏。
    """
    src = _bootstrap()
    fn = src.split("\nverify_nofile_effective() {", 1)[1].split("\n}\n", 1)[0]
    prog = fn.split("awk '", 1)[1].split("'", 1)[0]
    assert prog.startswith("/^# \\//"), \
        f"文件头的判据不是「# 加绝对路径」：{prog!r}"

    # 真跑一遍 awk：喂一段与 systemctl cat 同形的输入，内部注释必须不被当文件头
    sample = (
        "# /etc/systemd/system/x.service\n"
        "[Service]\n"
        "LimitNOFILE=65536\n"
        "\n"
        "# /etc/systemd/system/x.service.d/90-tavotto-nofile.conf\n"
        "# 由 scripts/ci/bootstrap_lab_runner.sh 写入\n"
        "# 表现是随机的 \"Too many open files\"，与真实的句柄泄漏几乎分不开。\n"
        "[Service]\n"
        "LimitNOFILE=4096\n")
    r = subprocess.run(["bash", "-c", "awk '" + prog + "'"], input=sample,
                       capture_output=True, text=True, encoding="utf-8")
    out = r.stdout
    assert "表现是随机的" not in out, f"内部注释又被当成文件名了：{out!r}"
    assert out.count("/etc/systemd/system/") == 2, f"文件名没认全：{out!r}"
    assert "90-tavotto-nofile.conf: " in out.replace("        ", ""), \
        f"该点名的那个文件没出现：{out!r}"


def test_the_legacy_dropin_note_does_not_claim_it_was_superseded():
    """`limits.conf` 排在 `90-…` **之后**，它压过新文件，不是被新文件取代。

    排序按字节走，字母在数字之后（'l'=0x6C > '9'=0x39）——真机 `systemctl cat`
    的加载顺序实测确认。原先那句提示写着「已被取代，可自行删除」，方向正好反了：
    照着删反而会把当时唯一生效的那份设定删掉。
    """
    src = _bootstrap()
    fn = src.split("\nwrite_nofile_dropin() {", 1)[1].split("\n}\n", 1)[0]
    note = fn.split("limits.conf", 1)[1]
    assert "取代" not in note.split("echo", 1)[0] or "之后" in note, \
        "提示仍在说旧文件被取代"
    for word in ("已被 $NOFILE_DROPIN_NAME 取代", "可自行删除"):
        assert word not in fn, f"提示里还留着反的说法：{word}"


# ---- 第五轮复审：/proc 目录 mtime 不是进程启动时刻 ---------------------------


def test_process_start_time_is_never_taken_from_proc_dir_mtime():
    """`/proc/<pid>` 的 mtime 是 **inode 被实例化**的时刻，不是进程起来的时刻。

    真机实测（Ubuntu 24.04 / kernel 6.8）：起一个进程、**等 5 秒再第一次** stat
    它的 /proc 目录——

        T0（进程创建）  = 1787400324.471660887
        5 秒后首次 stat = 1787400329.477145631   ← 与当时的 now 只差 4ms

    它一旦实例化就不再漂（连采三次一模一样），所以长期运行的进程看上去
    「碰巧对」。上一版正是被这个碰巧骗过去的：拿它当亚秒精度的服务启动时刻，
    真机验证还「通过」了。**一个语义错的精确值，比一个诚实的粗略值坏得多。**
    """
    src = _bootstrap()
    for fn_name in ("config_is_newer_than_service", "service_start_epoch"):
        fn = src.split(f"\n{fn_name}() {{", 1)[1].split("\n}\n", 1)[0]
        code = "\n".join(ln for ln in fn.splitlines()
                         if not ln.lstrip().startswith("#"))
        assert "/proc/" not in code, \
            f"{fn_name} 又去读 /proc 了——那里没有「进程什么时候起来的」这个量"
    fn = src.split("\nservice_start_epoch() {", 1)[1].split("\n}\n", 1)[0]
    assert "ExecMainStartTimestamp" in fn, \
        "服务启动时刻要问 systemd（语义正确，代价是只有整秒）"


@requires_posix_bash
def test_the_staleness_check_is_conservative_at_second_granularity():
    """只有整秒可用时，判据必须偏向「可能更新」——假红不假绿。

    不同来源之间还能差出约一秒（systemd 记的是它 exec 的时刻，内核 starttime
    记的是进程创建，实测这台机器上两者差 0.62s），所以留一秒余量。
    """
    src = _bootstrap()
    slack = int(src.split("STALE_SLACK_SEC=", 1)[1].split()[0])
    assert slack >= 1, "没有留余量，同秒或差一秒内的修改会被放过"

    script = (f"STALE_SLACK_SEC={slack}\n" + _bootstrap_funcs("_file_is_stale")
              + '\nif _file_is_stale "$1" "$2"; then echo STALE; else echo FRESH; fi\n')
    for ft, st, want in [
        ("1000", "1000", "STALE"),   # 同一秒 → 不能证明更旧
        ("1000", "1001", "STALE"),   # 落在余量之内
        ("1000", "1005", "FRESH"),   # 明显早于服务启动
        ("1010", "1000", "STALE"),   # 明显更新
        ("", "1000", "FRESH"),       # 读不出来就别乱报（上层另有 bad 一档）
        ("abc", "1000", "FRESH"),
    ]:
        r = _run_sh(script, ft, st)
        assert r.stdout.strip() == want, \
            f"_file_is_stale({ft!r}, {st!r}) 回了 {r.stdout.strip()!r}，应是 {want}"
        assert not r.stderr.strip(), f"比较 {ft!r} {st!r} 报错了：{r.stderr!r}"


def test_the_staleness_check_routes_through_the_shared_comparator():
    """真实调用点必须走同一个比较器，否则上面那条只验了个孤立函数。"""
    src = _bootstrap()
    fn = src.split("\nconfig_is_newer_than_service() {", 1)[1].split("\n}\n", 1)[0]
    assert "_file_is_stale" in fn, "没走共用比较器——余量与相等语义会分叉"


@requires_posix_bash
def test_both_config_files_are_checked_for_freshness(tmp_path):
    """回退到 `.path` 时也要查 `.env` 的新鲜度。

    管理员把 `.env` 里的 PATH= 删掉/注释掉之后，解析会回退到 `.path`——可「删掉
    那一行」本身就是一次修改，而跑着的 listener 手里还是删之前的 `.env` PATH。
    只查选中的那个文件，这种情况会被判成「没改过」。
    """
    root = tmp_path / "r"
    root.mkdir()
    (root / ".env").write_text("RUNNER_TOOL_CACHE=/opt\n", encoding="utf-8")  # 没有 PATH=
    (root / ".path").write_text("/usr/bin:/bin\n", encoding="utf-8")

    script = ('config_is_newer_than_service() { [ "$2" = "$TARGET" ]; }\n'
              + _bootstrap_funcs("stale_config_of_root")
              + '\nstale_config_of_root u "$1" || echo NONE\n')

    import os as _os
    for target, expect in [(str(root / ".env"), str(root / ".env")),
                           (str(root / ".path"), str(root / ".path")),
                           ("/nothing", "NONE")]:
        r = subprocess.run(["bash", "-c", script, "sh", str(root)],
                           capture_output=True, text=True, encoding="utf-8",
                           env={**_os.environ, "TARGET": target})
        assert r.stdout.strip() == expect, \
            f"TARGET={target} 时回了 {r.stdout.strip()!r}，应是 {expect}"


# ---- 第六轮复审：两条都是「--check 报了绿而 job 跑不起来」-------------------


def test_check_mode_validates_the_descriptor_limit_too():
    """`--check` 也要验 FD 上限——它是 lab_preflight 的**硬阻断**项。

    原先只读的 FD 判据长在安装路径里，而 `--check` 分支在那之前就 `exit 0` 了。
    于是按文档跑 `--check` 的运维会在一台 soft=1024 的机器上看到「检查通过」，
    随后每一个 lab job 都被 preflight 拦下。
    """
    src = _bootstrap()
    # 用 `say "检查通过"` 这个**代码**形态当终点：注释里也写着「检查通过」，
    # 拿裸词切会在第一条注释处就截断（本轮踩过一次）。
    check = src.split('if [ "$CHECK_ONLY" -eq 1 ]; then', 1)[1] \
               .split('say "检查通过"', 1)[0]
    code = "\n".join(ln for ln in check.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "nofile_state" in code, "--check 没验 FD 上限"
    assert "nofile_of_service" in code and "configured_nofile" in code, \
        "--check 的 FD 判据没同时看运行态与配置态"
    # 必须**只读**：check 模式一个 drop-in 都不许写
    assert "write_nofile_dropin" not in code, "--check 写了 drop-in——它该是只读的"
    # 不达标要算进未就绪，否则又是「报了但不阻断」
    fd_part = code.split("nofile_state", 1)[1]
    assert fd_part.count("MISSING=$((MISSING + 1))") >= 2, \
        "FD 不达标 / 只差重启 没有都算进未就绪"
    # 判据定义必须排在 --check 分支之前，否则 check 里根本调不到
    assert src.index("nofile_state() {") < src.index('if [ "$CHECK_ONLY" -eq 1 ]; then'), \
        "只读判据仍定义在 --check 之后"


@requires_posix_bash
def test_an_explicitly_empty_service_path_is_a_bad_configuration(tmp_path):
    """`.env` 里的 `PATH=`（值为空）会让 runner 把 PATH 整个删掉。

    它是合法的一行，`env_path_of_root` 会成功返回空串；而空串会被下游的
    `[ -n "$spath" ] || continue` 整行跳过——于是工具表**一个都没查**，
    `--check` 照样打「检查通过」，而 job 一个命令都找不到。
    """
    root = tmp_path / "r"
    root.mkdir()
    (root / ".env").write_text("PATH=\nRUNNER_TOOL_CACHE=/opt\n", encoding="utf-8")
    (root / ".path").write_text("/usr/bin:/bin\n", encoding="utf-8")

    stubs = ('discover_runner_units() { printf "u1\\n"; }\n'
             f'root_of_unit() {{ printf "{root}"; }}\n'
             'stale_config_of_root() { return 1; }\n')
    script = (stubs + _bootstrap_funcs("env_path_of_root", "service_path_of_root")
              + "\n" + _bootstrap_rows()
              + '\nprintf "BAD<%s>\\nPATHS<%s>\\n" "$SERVICE_BAD" "$SERVICE_PATHS"\n')
    r = _run_sh(script)
    assert "PATH 是空的" in r.stdout, \
        f"空 PATH 被当成了正常配置：{r.stdout!r}"
    assert r.stdout.split("PATHS<")[1].startswith(">"), \
        f"空 PATH 还是进了待查表：{r.stdout!r}"
