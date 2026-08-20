"""实验室 CI 工具自身的看护。

「这是 CI 脚本所以不用测」正是本仓库反复否定的那种想法：这些脚本是**门禁**，
门禁失灵的表现是安静地放行，不是报错。所以每条用例都尽量做成「把实现改坏就
会红」的形状，而不是只断言 happy path 跑得通。

**全部平台无关**——ci.yml 的 backend job 是三平台矩阵，这里不能引入
`/proc`、`resource`、POSIX 权限之类的假设；确实只在 Linux 有意义的分支
（进程扫描）用「能力探测 + 跳过」而不是硬跳 sys.platform。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

CI_DIR = Path(__file__).resolve().parents[1] / "scripts" / "ci"
sys.path.insert(0, str(CI_DIR))

import _common  # noqa: E402
import cleanup  # noqa: E402
import lab_preflight  # noqa: E402


# ---------------------------------------------------------------- 路径安全
class TestPathSafety:
    """`assert_within` 是所有删除动作的唯一闸门，先把它钉死。"""

    def test_accepts_path_inside_root(self, tmp_path):
        root = tmp_path / "state"
        (root / "tmp" / "x").mkdir(parents=True)
        assert _common.assert_within(root / "tmp" / "x", root)

    def test_rejects_path_outside_root(self, tmp_path):
        root = tmp_path / "state"
        root.mkdir()
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        with pytest.raises(_common.CiError) as exc:
            _common.assert_within(outside, root)
        assert exc.value.code == "unsafe_path"

    def test_rejects_dotdot_escape(self, tmp_path):
        """`state/tmp/../../etc` 必须被挡住——字符串前缀比较会放它过去。"""
        root = tmp_path / "state"
        (root / "tmp").mkdir(parents=True)
        with pytest.raises(_common.CiError):
            _common.assert_within(root / "tmp" / ".." / ".." / "etc", root)

    def test_rejects_the_root_itself(self, tmp_path):
        """一句「清理这个目录」不能把整个持久化根删掉。"""
        root = tmp_path / "state"
        root.mkdir()
        with pytest.raises(_common.CiError):
            _common.assert_within(root, root)

    @pytest.mark.skipif(not hasattr(os, "symlink"), reason="本平台不支持符号链接")
    def test_rejects_symlink_pointing_outside(self, tmp_path):
        """符号链接必须被解开再判——否则 tmp/evil → / 就是一次灾难。"""
        root = tmp_path / "state"
        (root / "tmp").mkdir(parents=True)
        outside = tmp_path / "outside"
        outside.mkdir()
        link = root / "tmp" / "evil"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("这台机器上建不了符号链接（Windows 需要开发者模式）")
        with pytest.raises(_common.CiError):
            _common.assert_within(link, root)

    def test_safe_rmtree_refuses_outside_and_keeps_file(self, tmp_path):
        """越界时不仅要抛错，更要确认**文件还在**。"""
        root = tmp_path / "state"
        root.mkdir()
        victim = tmp_path / "precious.txt"
        victim.write_text("不该被删", encoding="utf-8")
        with pytest.raises(_common.CiError):
            _common.safe_rmtree(victim, root)
        assert victim.exists(), "越界检查通过了却还是把文件删了"

    def test_safe_rmtree_removes_inside(self, tmp_path):
        root = tmp_path / "state"
        target = root / "tmp" / "junk"
        target.mkdir(parents=True)
        (target / "a.txt").write_text("x", encoding="utf-8")
        assert _common.safe_rmtree(target, root) is True
        assert not target.exists()


# ---------------------------------------------------------------- state root
class TestStateRoot:
    def test_env_override_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TAVOTTO_CI_STATE_ROOT", str(tmp_path / "custom"))
        assert _common.state_root() == tmp_path / "custom"

    def test_default_is_not_runner_temp(self, monkeypatch):
        """默认值绝不能落在一次性目录里。

        baseline 落在 `$RUNNER_TEMP` 的话，「和基线比」实际是「和自己刚生成的
        那份比」，永远不会红——正是这套 CI 想消灭的那种空转门禁。
        """
        monkeypatch.delenv("TAVOTTO_CI_STATE_ROOT", raising=False)
        monkeypatch.setenv("RUNNER_TEMP", "/some/ephemeral/path")
        root = str(_common.state_root())
        assert "ephemeral" not in root
        assert root == _common.DEFAULT_STATE_ROOT

    def test_ensure_layout_creates_all_and_is_idempotent(self, tmp_path):
        root = tmp_path / "state"
        _common.ensure_layout(root)
        for rel in _common.LAYOUT:
            assert (root / rel).is_dir(), f"{rel} 没建出来"
        _common.ensure_layout(root)          # 再来一次不该炸
        assert (root / "baselines" / "perf").is_dir()

    def test_ensure_layout_reports_unwritable(self, tmp_path):
        """根目录建不出来时要给出稳定 code，而不是裸 OSError。"""
        blocker = tmp_path / "blocked"
        blocker.write_text("我是文件不是目录", encoding="utf-8")
        with pytest.raises(_common.CiError) as exc:
            _common.ensure_layout(blocker)
        assert exc.value.code == "state_root_unwritable"


# ---------------------------------------------------------------- 报告
class TestReports:
    def test_write_report_is_atomic_and_parseable(self, tmp_path):
        _common.ensure_layout(tmp_path)
        dest = _common.write_report("x.json", {"ok": True, "中文": "值"}, tmp_path)
        assert json.loads(dest.read_text(encoding="utf-8"))["中文"] == "值"
        # 临时文件不能留下——它会被 upload-artifact 一起收走，看报告的人
        # 就会同时看到 x.json 和 x.json.tmp 两份，不知道该信哪个。
        assert not list(tmp_path.glob("reports/*.tmp"))

    def test_run_metadata_carries_fields_that_make_baselines_comparable(self):
        meta = _common.run_metadata("nightly")
        for key in ("sha", "mode", "timestamp", "python", "os", "cpu_count"):
            assert key in meta, f"缺 {key}——没有它，历史 benchmark 无法判断可比性"
        assert meta["mode"] == "nightly"

    def test_summary_appends_to_github_step_summary(self, tmp_path, monkeypatch):
        f = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(f))
        _common.summary("第一行")
        _common.summary("第二行")
        assert f.read_text(encoding="utf-8").splitlines() == ["第一行", "第二行"]

    def test_summary_falls_back_to_stdout(self, capsys, monkeypatch):
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        _common.summary("本地跑")
        assert "本地跑" in capsys.readouterr().out


# ---------------------------------------------------------------- cleanup
class TestCleanup:
    def _aged(self, path: Path, days: float) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("x", encoding="utf-8")
        old = time.time() - days * 86400
        os.utime(path, (old, old))

    def test_removes_only_expired(self, tmp_path):
        root = tmp_path / "state"
        _common.ensure_layout(root)
        fresh = root / "tmp" / "fresh.txt"
        stale = root / "tmp" / "stale.txt"
        self._aged(fresh, 0.1)
        self._aged(stale, 10)
        cleanup.sweep(root)
        assert fresh.exists(), "还没过期的被删了"
        assert not stale.exists(), "过期的没被删——保留期判断失效了"

    def test_never_touches_protected_subtrees(self, tmp_path):
        """baselines / cache / upgrade 是持久化的意义所在，多老都不能删。"""
        root = tmp_path / "state"
        _common.ensure_layout(root)
        keepers = [
            root / "baselines" / "perf" / "main.json",
            root / "cache" / "wheels" / "x.whl",
            root / "upgrade" / "state" / "config.json",
        ]
        for k in keepers:
            self._aged(k, 365)
        cleanup.sweep(root)
        cleanup.sweep_workspace_leftovers(root)
        for k in keepers:
            assert k.exists(), f"{k} 被清理掉了——跨 run 状态丢失，基线比较会失去意义"

    def test_dry_run_changes_nothing(self, tmp_path):
        root = tmp_path / "state"
        _common.ensure_layout(root)
        stale = root / "tmp" / "stale.txt"
        self._aged(stale, 30)
        actions = cleanup.sweep(root, dry_run=True)
        assert stale.exists(), "--dry-run 居然真删了"
        assert actions and all(not a["removed"] for a in actions)

    def test_sweeps_disposable_venvs(self, tmp_path):
        root = tmp_path / "state"
        _common.ensure_layout(root)
        venv = root / "tmp" / "venv-abc"
        (venv / "bin").mkdir(parents=True)
        art = root / "tmp" / "artifact-xyz"
        art.mkdir(parents=True)
        cleanup.sweep_workspace_leftovers(root)
        assert not venv.exists() and not art.exists()

    def test_kill_stale_only_matches_this_state_root(self, tmp_path):
        """扫描必须以持久化根为归属依据，不能按进程名。

        这里不真起进程，只确认「另一个根」下的进程绝不会被本根的清理选中——
        误杀维护者自己开着的实例，会让这个开关永远没人敢用。
        """
        if not Path("/proc").is_dir():
            pytest.skip("非 Linux，无 /proc 进程扫描")
        other_root = tmp_path / "someone-elses-root"
        other_root.mkdir()
        found = cleanup.kill_stale_processes(other_root, dry_run=True)
        assert found == [], f"不该匹配到任何进程，却选中了 {found}"


# ---------------------------------------------------------------- preflight
class TestPreflight:
    def test_blocks_when_state_root_unwritable(self, tmp_path, monkeypatch):
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setenv("TAVOTTO_CI_STATE_ROOT", str(blocker))
        checks = lab_preflight.check_state_root("main")
        blocking = [c for c in checks if not c.ok and not c.warn]
        assert blocking, "根目录不可写居然放行了"
        assert "持久化根目录" in blocking[0].name

    def test_unknown_memory_is_not_treated_as_insufficient(self, monkeypatch):
        """读不到内存 ≠ 内存不足。

        把未知当不足，会让这条门禁在任何非 Linux 开发机上恒红，
        而恒红的门禁很快就会被加进忽略列表。
        """
        monkeypatch.setattr(_common, "_mem_total_gib", lambda: 0.0)
        monkeypatch.setattr(lab_preflight, "run_metadata",
                            lambda *a, **k: {"cpu_count": 16, "ram_gib": 0.0})
        checks = lab_preflight.check_hardware()
        mem = [c for c in checks if c.name == "内存"][0]
        assert mem.ok is True and mem.warn is True

    def test_insufficient_memory_does_block(self, monkeypatch):
        """但真读到了且真不够时，必须拦下来——否则上一条就成了万能借口。"""
        monkeypatch.setattr(lab_preflight, "run_metadata",
                            lambda *a, **k: {"cpu_count": 16, "ram_gib": 2.0})
        checks = lab_preflight.check_hardware()
        mem = [c for c in checks if c.name == "内存"][0]
        assert mem.ok is False and mem.warn is False

    def test_low_cpu_blocks(self, monkeypatch):
        monkeypatch.setattr(lab_preflight, "run_metadata",
                            lambda *a, **k: {"cpu_count": 1, "ram_gib": 32.0})
        cpu = [c for c in lab_preflight.check_hardware() if c.name == "CPU 核数"][0]
        assert cpu.ok is False

    def test_rust_only_required_for_deep_modes(self, monkeypatch):
        """main 模式不跑 Rust，就不该拿 cargo 缺席去拦它。"""
        monkeypatch.setattr(lab_preflight.shutil, "which",
                            lambda exe: None if exe == "cargo" else f"/usr/bin/{exe}")
        names_main = {c.name for c in lab_preflight.check_toolchain("main")}
        names_nightly = {c.name for c in lab_preflight.check_toolchain("nightly")}
        assert "Rust cargo" not in names_main
        assert "Rust cargo" in names_nightly

    def test_missing_executable_blocks_with_remedy(self, monkeypatch):
        monkeypatch.setattr(lab_preflight.shutil, "which", lambda exe: None)
        bad = [c for c in lab_preflight.check_toolchain("main") if not c.ok]
        assert bad, "所有可执行文件都找不到却全部放行"
        assert all(c.remedy for c in bad), "失败项没给处置建议，等于只说了『坏了』"

    def test_disk_threshold_scales_with_mode(self):
        """深模式要的磁盘更多——golden + upgrade fixture 会占不少。"""
        assert lab_preflight.MODE_DISK_GIB["weekly"] > lab_preflight.MODE_DISK_GIB["main"]

    def test_cli_exits_nonzero_when_blocked(self, tmp_path, monkeypatch):
        """整条 CLI 路径必须把阻断变成非零退出码，否则 workflow 不会红。"""
        blocker = tmp_path / "blocked"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setenv("TAVOTTO_CI_STATE_ROOT", str(blocker))
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        assert lab_preflight.main(["--mode", "main", "--no-report"]) == 1

    def test_cli_passes_on_a_healthy_root(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TAVOTTO_CI_STATE_ROOT", str(tmp_path / "state"))
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        monkeypatch.setattr(lab_preflight, "run_metadata",
                            lambda *a, **k: {"cpu_count": 16, "ram_gib": 32.0})
        monkeypatch.setattr(lab_preflight, "check_state_root",
                            lambda mode: [lab_preflight.Check("持久化根目录", True, "ok")])
        monkeypatch.setattr(lab_preflight.shutil, "which", lambda exe: f"/usr/bin/{exe}")
        assert lab_preflight.main(["--mode", "main", "--no-report"]) == 0


# ---------------------------------------------------------------- 冒烟
def test_scripts_run_without_the_product_installed():
    """preflight 与 cleanup 必须在产品没装好时也能跑。

    诊断工具因为「被诊断的东西没装好」而自己崩掉，是最没用的失败方式——
    与 `tavotto open`/`doctor` 要在 Flask import 失败时仍可用是同一条纪律。
    """
    for script in ("lab_preflight.py", "cleanup.py"):
        out = subprocess.run(
            [sys.executable, str(CI_DIR / script), "--help"],
            capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, f"{script} --help 都跑不了：{out.stderr}"
