"""T03：用户给的**精确 argv**从 token 输入一路贯通到真实 worker，并绑定产物（模型 + 真 worker + 池）。

判据的主语：**脚本自己看到的**（真 worker 子进程里的 `sys.argv` / 曲线数组 / 解释器），与同一个解释器
**原生**跑同一份脚本的结果逐项对拍——不是请求回显、不是 store 里的期望值。HTTP 全链路（探测 / 编辑 /
冷重放 / 导出 / 重开）在 `test_run_argv_e2e.py`。

不碰 matplotlib 的部分（模型、登记、池键、资产 id）先于需要科学栈的部分；后者在找不到解释器时 skip
（skip 会在 pytest 汇总里如实显示，不算通过）。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import (
    execspec,
    figcapture,
    pool,
    preparation,
    registry,
    runconfig,
    runtimeasset,
)

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SENTINEL = "SENTINEL-argv-value-42"


def _spec(**kw):
    base = dict(
        interpreter="/usr/bin/python3",
        sandbox="/proj/.box",
    )
    base.update(kw)
    return execspec.safe_spec("fig.py", "/proj", "main", **base)


# ===========================================================================
# 模型：空 argv 逐字节不变；非空只多配置引用；参数值走私有请求管道
# ===========================================================================
class TestWorkerArgvShape:
    def _argv(self, spec):
        return execspec.worker_argv(spec, worker_py="/w.py", out_dir="/out")

    def test_empty_argv_adds_no_token(self):
        """A04 旁证 / C20：没给参数 = T03 之前的命令行，一个 token 都不多。"""
        argv = self._argv(_spec())
        assert "--script-argv-json" not in argv and "--run-config" not in argv
        assert argv == self._argv(_spec(argv=()))

    def test_nonempty_argv_sends_only_the_config_reference_at_spawn(self):
        tokens = ["", " ", "中文", "--", "-3", "--", '"q"', "--x", "--x"]
        argv = self._argv(_spec(argv=tokens, run_config="rc_0123456789ab"))
        assert argv == [*self._argv(_spec()), "--run-config", "rc_0123456789ab"]
        run = execspec.RunSelection("rc_0123456789ab", tuple(tokens))
        assert pool._run_payload(run)["run"]["argv"] == tokens  # 所有 token 经私有请求无损传入

    def test_argv_requires_a_config_reference(self):
        with pytest.raises(ValueError, match="run_config"):
            _spec(argv=("--a",))

    def test_argv_is_bounded_and_rejects_nul(self):
        with pytest.raises(ValueError):
            _spec(argv=("x",) * (execspec.MAX_ARGV_TOKENS + 1), run_config="rc_x")
        with pytest.raises(ValueError, match="NUL"):
            _spec(argv=("a\x00b",), run_config="rc_x")

    def test_payload_roundtrip_keeps_the_exact_tokens(self):
        spec = _spec(argv=("", "中文", "--"), run_config="rc_0123456789ab")
        assert execspec.spec_from_payload(json.loads(json.dumps(spec.to_payload()))) == spec


class TestPublicProjectionsCarryCountsNotValues:
    def test_stable_payload_and_launch_context(self):
        spec = _spec(argv=(SENTINEL, "--k"), run_config="rc_0123456789ab")
        stable, ctx = spec.stable_payload(), execspec.launch_context(spec)
        for payload in (stable, ctx):
            assert SENTINEL not in json.dumps(payload, ensure_ascii=False)
            assert payload["argv"] == []
            assert payload["argv_count"] == 2 and payload["run_config"] == "rc_0123456789ab"

    def test_empty_argv_keeps_the_old_shape(self):
        stable = _spec().stable_payload()
        assert set(stable) == {*execspec.STABLE_FIELDS, "spec_version"} and stable["argv"] == []

    def test_native_keeps_its_own_argv_untouched(self):
        native = execspec.native_spec(
            "fig.py", interpreter="/usr/bin/python3", cwd="/c", project_root="/c", argv=("a", "b")
        )
        assert native.stable_payload()["argv"] == ["a", "b"]

    def test_receipt_identity_differs_per_configuration_without_leaking(self):
        a = _spec(argv=(SENTINEL,), run_config="rc_aaaaaaaaaaaa").stable_payload()
        b = _spec(argv=(SENTINEL, "x"), run_config="rc_bbbbbbbbbbbb").stable_payload()
        assert a != b and SENTINEL not in json.dumps([a, b])


# ===========================================================================
# 登记：校验 / 复用 / 修订 / 敏感 / 降级
# ===========================================================================
class TestRunConfigStore:
    def test_validation_rejects_instead_of_repairing(self, tmp_path):
        wire_heavy = ["中" * 3000]  # 原文才 3000 字符，转义成 \\uXXXX 后装不进 Windows 命令行
        for bad in (
            "--freq 2",
            [1],
            ["a", None],
            ["a\x00"],
            ["x"] * 257,
            ["x" * 40000],
            wire_heavy,
        ):
            with pytest.raises(runconfig.InvalidArgv):
                runconfig.validate_argv(bad)
        assert runconfig.validate_argv(None) == () and runconfig.validate_argv([]) == ()

    def test_same_tokens_reuse_the_reference_and_any_change_makes_a_new_one(self, tmp_path):
        a = runconfig.put(tmp_path, "s.py", ["--k", "1"])
        assert runconfig.put(tmp_path, "s.py", ["--k", "1"]).id == a.id
        assert runconfig.put(tmp_path, "s.py", ["--k", "2"]).id != a.id  # 编辑 = 新修订，旧的不变
        assert runconfig.put(tmp_path, "s.py", ["--k", " 1"]).id != a.id  # 空格也算
        assert runconfig.put(tmp_path, "t.py", ["--k", "1"]).id != a.id  # 脚本是身份的一部分
        assert runconfig.get(tmp_path, a.id).argv == ("--k", "1")  # 旧引用仍取到当时的 token
        assert runconfig.put(tmp_path, "s.py", []) is None  # 空 = 没有配置（旧行为）

    def test_the_reference_survives_a_restart_and_is_not_the_arguments(self, tmp_path):
        cfg = runconfig.put(tmp_path, "s.py", [SENTINEL])
        assert SENTINEL not in cfg.id and SENTINEL not in json.dumps(cfg.public())
        runconfig.forget_secrets()  # 普通配置落盘，不受影响
        assert runconfig.get(tmp_path, cfg.id, script="s.py").argv == (SENTINEL,)
        assert runconfig.store_path(tmp_path).is_relative_to(Path(os.environ["TAVOTTO_DATA_DIR"]))
        assert not any(tmp_path.iterdir())  # 不写用户项目（只读项目照样能用）

    def test_a_sensitive_configuration_is_never_persisted(self, tmp_path):
        cfg = runconfig.put(tmp_path, "s.py", ["--token", SENTINEL], sensitive=True)
        assert runconfig.get(tmp_path, cfg.id).argv == ("--token", SENTINEL)  # 本进程内可用
        assert SENTINEL not in runconfig.store_path(tmp_path).read_text(encoding="utf-8")
        runconfig.forget_secrets()  # 模拟重启
        with pytest.raises(runconfig.RunConfigSecretMissing):  # 显式拒绝，不是空 argv
            runconfig.get(tmp_path, cfg.id)
        with pytest.raises(runconfig.RunConfigSecretMissing):
            runtimeasset.run_selection(
                tmp_path, {"script": "s.py", "run_config": cfg.id, "stem": "x"}
            )

    def test_unknown_or_foreign_references_are_refused_not_defaulted(self, tmp_path):
        cfg = runconfig.put(tmp_path, "s.py", ["a"])
        with pytest.raises(runconfig.RunConfigMissing):
            runconfig.get(tmp_path, "rc_000000000000")
        with pytest.raises(runconfig.RunConfigMissing):
            runconfig.get(tmp_path, cfg.id, script="other.py")
        with pytest.raises(runconfig.RunConfigMissing):  # 别的项目（另一台机器保存的文档）
            runconfig.get(tmp_path / "elsewhere", cfg.id)

    def test_eviction_never_drops_a_config_a_panel_default_points_at(self, tmp_path, monkeypatch):
        # Codex r4214001208：淘汰最老配置会连带删掉引用它的磁盘面板默认 -> default_selection() 变 None
        # -> 面板静默用空 argv 重跑。被默认引用的配置必须留着。
        monkeypatch.setattr(runconfig, "MAX_CONFIGS", 3)
        pinned = runconfig.put(tmp_path, "s.py", ["--pinned"])
        runconfig.set_default(tmp_path, "s.py", pinned.id)
        for i in range(6):
            runconfig.put(tmp_path, "s.py", [f"--n{i}"])
        sel = runconfig.default_selection(tmp_path, "s.py")
        assert sel is not None and sel.argv == ("--pinned",)
        assert len(runconfig._load(tmp_path)) <= 3 + 1  # 受上限约束（默认引用的那条可多占一格）

    def test_project_identity_follows_the_volume_not_normcase(self, tmp_path, monkeypatch):
        # Codex r4214001213：macOS 卷大小写不敏感但 os.path.normcase 是 no-op；同一项目换大小写打开
        # 必须指向同一份登记。
        from tavotto.engine import config as engine_config

        monkeypatch.setattr(engine_config, "path_is_case_insensitive", lambda p: True)
        upper, lower = tmp_path / "Plots", tmp_path / "plots"
        cfg = runconfig.put(upper, "s.py", ["--k", "1"])
        runconfig.set_default(upper, "s.py", cfg.id)
        assert runconfig.store_path(upper) == runconfig.store_path(lower)
        assert runconfig.get(lower, cfg.id, script="s.py").argv == ("--k", "1")
        assert runconfig.default_selection(lower, "s.py").config_id == cfg.id

    def test_a_registry_written_under_the_legacy_digest_is_still_read(self, tmp_path, monkeypatch):
        # 兼容：首版按 normcase 命名的登记文件（POSIX 上 = 原样大小写）在新判据下仍读得到。
        from tavotto.engine import config as engine_config

        root = tmp_path / "Plots"
        monkeypatch.setattr(engine_config, "path_is_case_insensitive", lambda p: False)
        cfg = runconfig.put(root, "s.py", ["--k", "1"])
        legacy = runconfig.store_path(root)
        monkeypatch.setattr(engine_config, "path_is_case_insensitive", lambda p: True)
        assert runconfig.store_path(root) != legacy and legacy.exists()
        assert runconfig._legacy_store_path(root) == legacy or os.name == "nt"
        assert runconfig.get(root, cfg.id).argv == ("--k", "1")

    def test_a_legacy_registry_is_found_through_a_case_alias_of_the_root(
        self, tmp_path, monkeypatch
    ):
        # Codex r4214097447：旧登记按当初的拼写（Plots）算哈希；现在以别名（plots）打开，
        # 回落候选要含磁盘真实拼写。模拟版：所有平台都跑得到。
        from tavotto.engine import config as engine_config

        real, alias = tmp_path / "Plots", tmp_path / "plots"
        monkeypatch.setattr(engine_config, "path_is_case_insensitive", lambda p: False)
        cfg = runconfig.put(real, "s.py", ["--k", "1"])
        runconfig.set_default(real, "s.py", cfg.id)
        legacy = runconfig.store_path(real)
        monkeypatch.setattr(engine_config, "path_is_case_insensitive", lambda p: True)
        monkeypatch.setattr(runconfig, "_on_disk_spelling", lambda p: p.replace("plots", "Plots"))
        assert legacy.exists() and runconfig.store_path(alias) != legacy
        assert runconfig.get(alias, cfg.id).argv == ("--k", "1")
        assert runconfig.default_selection(alias, "s.py").config_id == cfg.id
        runconfig.put(alias, "s.py", ["--k", "2"])  # 下次写入落新名
        assert runconfig.store_path(alias).exists()

    def test_on_disk_spelling_recovers_real_case(self, tmp_path):
        (tmp_path / "Plots" / "Sub").mkdir(parents=True)
        assert runconfig._on_disk_spelling(str(tmp_path / "missing" / "X")) == str(
            tmp_path / "missing" / "X"
        )
        if not (tmp_path / "plots").exists():
            pytest.skip("大小写敏感的卷：别名不存在，模拟版用例已覆盖")
        got = runconfig._on_disk_spelling(str(tmp_path / "plots" / "SUB"))
        assert got == str(tmp_path / "Plots" / "Sub")

    def test_a_newer_format_is_refused_by_this_reader(self, tmp_path):
        cfg = runconfig.put(tmp_path, "s.py", ["a"])
        path = runconfig.store_path(tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["version"] = runconfig.FORMAT_VERSION + 1
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(runconfig.RunConfigUnsupported):
            runconfig.get(tmp_path, cfg.id)
        with pytest.raises(runconfig.RunConfigUnsupported):  # 也不许"顺手覆盖"它
            runconfig.put(tmp_path, "s.py", ["b"])

    def test_the_disk_panel_default_follows_the_last_explicit_run(self, tmp_path):
        a = runconfig.put(tmp_path, "s.py", ["a"])
        assert runconfig.default_selection(tmp_path, "s.py") is None
        runconfig.set_default(tmp_path, "s.py", a.id)
        assert runconfig.default_selection(tmp_path, "s.py").argv == ("a",)
        runconfig.set_default(tmp_path, "s.py", None)  # 之后无参数运行 → 清掉
        assert runconfig.default_selection(tmp_path, "s.py") is None


# ===========================================================================
# 池键 / 资产 id / 描述符
# ===========================================================================
class FakeWorker:
    def __init__(self, name):
        self.script_name = name
        self.shutdown_calls = 0

    def alive(self):
        return True

    def shutdown(self):
        self.shutdown_calls += 1


class TestIdentity:
    def test_pool_keys_separate_argv_and_share_the_script_prefix(self, tmp_path):
        a = execspec.RunSelection("rc_a", ("--k", "1"))
        b = execspec.RunSelection("rc_b", ("--k", "2"))
        keys = {pool._worker_key(tmp_path, "s.py", None, r) for r in (None, a, b)}
        assert len(keys) == 3
        assert pool._worker_key(tmp_path, "s.py", None, a) == pool._worker_key(
            tmp_path, "s.py", None, execspec.RunSelection("rc_a", ("--k", "1"))
        )
        assert all(k[:2] == pool._worker_key(tmp_path, "s.py")[:2] for k in keys)
        assert "--k" not in json.dumps(sorted(keys))  # 键里没有 argv 原文
        assert pool._worker_base(tmp_path, "s.py", None, a) != pool._worker_base(
            tmp_path, "s.py", None, b
        )

    def test_invalidate_by_script_drops_every_variant_but_by_run_only_that_one(
        self, tmp_path, monkeypatch
    ):
        a = execspec.RunSelection("rc_a", ("1",))
        b = execspec.RunSelection("rc_b", ("2",))
        workers = {r: FakeWorker("s.py") for r in (None, a, b)}
        monkeypatch.setattr(
            pool,
            "_workers",
            {pool._worker_key(tmp_path, "s.py", None, r): w for r, w in workers.items()},
        )
        pool.invalidate("s.py", str(tmp_path), run=a)
        survivors = {
            r
            for r, w in workers.items()
            if pool._worker_key(tmp_path, "s.py", None, r) in pool._workers
        }
        assert survivors == {None, b}
        pool.invalidate("s.py", str(tmp_path))  # 脚本变了：全部变体过期
        assert not pool._workers

    def test_asset_id_is_unchanged_without_a_configuration_and_distinct_with_one(self):
        base = figcapture.runtime_asset_id("a/s.py", "result")
        assert base == "runtime:a/s.py#result"  # 保存的文档 / override 的身份一个字节不变
        cfg = figcapture.runtime_asset_id("a/s.py", "result", "rc_0123456789ab")
        assert cfg == base + "~rc_0123456789ab" and cfg != base

    def test_resolve_matches_forward_and_rejects_foreign_suffixes(self):
        reg = registry.Registry()
        reg.load_data({"scripts": {"s.py": {"entry": "__main__", "stems": ["result"]}}})
        base = "runtime:s.py#result"
        assert "run_config" not in runtimeasset.resolve(base, reg)  # 无配置的形状与之前逐键相同
        hit = runtimeasset.resolve(base + "~rc_0123456789ab", reg)
        assert hit["script"] == "s.py" and hit["run_config"] == "rc_0123456789ab"
        for bad in (base + "~rc_xyz", base + "~", base + "~rc_0123456789abc", base + "~other"):
            assert runtimeasset.resolve(bad, reg) is None

    def test_descriptor_payload_has_no_run_config_key_without_one(self):
        kw = dict(
            script="s.py",
            entry="main",
            stem="result",
            capture_source=figcapture.SOURCE_SAVEFIG,
            execution_profile=figcapture.PROFILE_SAFE,
            size_mm=(10.0, 8.0),
            source_fingerprint="sha256:x",
        )
        plain = figcapture.build_descriptor(**kw)
        assert "run_config" not in plain.to_payload()
        cfg = figcapture.build_descriptor(**kw, run_config="rc_0123456789ab")
        assert cfg.asset_id.endswith("~rc_0123456789ab")
        assert figcapture.descriptor_from_payload(cfg.to_payload()) == cfg
        assert figcapture.descriptor_from_payload(plain.to_payload()) == plain


def test_the_workerd_spawn_spec_carries_the_same_tokens_as_the_python_pool(tmp_path):
    """两条控制面 spawn 同一份 argv（workerd 的 spec 哈希因此按 argv 分会话）。workerd 产物本机未构建，
    这里量的是**交给 workerd 的规格**，不是 Rust 侧的行为（未执行）。"""
    tokens = ("", "中文", "--", "-3")
    run = execspec.RunSelection("rc_0123456789ab", tokens)
    args = ("s.py", str(tmp_path), "main", tmp_path / "out", tmp_path / "sb", tmp_path / "w.log")
    plain = pool._spawn_spec(*args, "/usr/bin/python3", pool.SOURCE_CURRENT)
    with_run = pool._spawn_spec(*args, "/usr/bin/python3", pool.SOURCE_CURRENT, run=run)
    assert "--script-argv-json" not in plain["argv"]
    assert with_run["argv"] == [*plain["argv"], "--run-config", run.config_id]
    assert pool._run_payload(run)["run"]["argv"] == list(tokens)
    assert with_run["argv"] != plain["argv"]  # → workerd 的 spec 哈希随配置引用而变，不会复用会话


def test_the_semantic_revision_follows_the_configuration_and_nothing_else(tmp_path):
    """C03：换任何一个 token = 新的执行意图（`_fingerprint` 变，`config_revision` 会 +1）；同一份配置重做计划不变。"""
    from tavotto.engine import prepsession

    (tmp_path / "s.py").write_text("print(1)\n", encoding="utf-8")

    def plan(run):
        return preparation.plan_for(
            project_id="p",
            project_root=str(tmp_path),
            asset_id="",
            stem="",
            script="s.py",
            entry="__main__",
            original_artifact=None,
            target=preparation.TARGET_SCRIPT,
            run=run,
        )

    a = execspec.RunSelection("rc_aaaaaaaaaaaa", ("--k", "1"))
    b = execspec.RunSelection("rc_bbbbbbbbbbbb", ("--k", "2"))
    fp = prepsession._fingerprint
    assert fp(plan(a)) == fp(plan(execspec.RunSelection("rc_aaaaaaaaaaaa", ("--k", "1"))))
    assert fp(plan(a)) != fp(plan(b)) != fp(plan(None))


def test_a_plan_publishes_the_reference_and_count_but_never_the_values(tmp_path):
    run = execspec.RunSelection("rc_0123456789ab", (SENTINEL, "x"))
    (tmp_path / "s.py").write_text("print(1)\n", encoding="utf-8")
    plan = preparation.plan_for(
        project_id="p",
        project_root=str(tmp_path),
        asset_id="",
        stem="",
        script="s.py",
        entry="__main__",
        original_artifact=None,
        target=preparation.TARGET_SCRIPT,
        run=run,
    )
    wire = json.dumps(plan.to_payload(), ensure_ascii=False, default=str)
    assert SENTINEL not in wire
    assert plan.to_payload()["run_config"] == "rc_0123456789ab"
    assert plan.to_payload()["argv_count"] == 2


# ===========================================================================
# 真 worker：脚本自己看到的，与原生逐项相同
# ===========================================================================
DUMP_SCRIPT = """\
import json, os, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

tokens = sys.argv[1:]
ys = [float(len(t)) for t in tokens] or [0.5, 1.5]
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot(list(range(len(ys))), ys)
fig.savefig("result.pdf")
with open(os.environ["REC_FILE"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({
        "argv": tokens,
        "argc": len(sys.argv),
        "argv0": os.path.basename(sys.argv[0]),
        "exe": sys.executable,
        "prefix": sys.prefix,
        "ydata": [float(v) for v in ax.lines[0].get_ydata()],
    }, ensure_ascii=False) + "\\n")
"""

#: 六个必填参数的 FFT 等价脚本（A01）：不给参数时 argparse 以 `sys.exit(2)` 结束，给了就按参数画曲线
FFT_SCRIPT = """\
import argparse, json, os, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

with open(os.environ["RUNS_FILE"], "a", encoding="utf-8") as fh:
    fh.write("x")
p = argparse.ArgumentParser()
for name, typ in (("freq", float), ("amp", float), ("phase", float), ("n", int), ("tag", str), ("mode", str)):
    p.add_argument("--" + name, type=typ, required=True)
a = p.parse_args()
x = np.arange(a.n)
y = a.amp * np.sin(2 * np.pi * a.freq * x / a.n + a.phase)
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot(x, y)
ax.set_title(a.tag + "/" + a.mode)
fig.savefig("fft.pdf")
with open(os.environ["REC_FILE"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({
        "argv": sys.argv[1:],
        "ydata": [round(float(v), 9) for v in ax.lines[0].get_ydata()],
        "title": ax.get_title(),
    }, ensure_ascii=False) + "\\n")
"""

#: 导入期就解析参数（A07）：`parse_args()` 在模块顶层，第一行用户代码之前 argv 就得对
IMPORT_TIME_SCRIPT = """\
import argparse, json, os, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

_parser = argparse.ArgumentParser()
_parser.add_argument("--n", type=int, required=True)
ARGS = _parser.parse_args()

def main():
    fig, ax = plt.subplots(figsize=(3, 2))
    ax.plot(list(range(ARGS.n)), [float(i * i) for i in range(ARGS.n)])
    fig.savefig("imp.pdf")
    with open(os.environ["REC_FILE"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"argv": sys.argv[1:], "n": ARGS.n}) + "\\n")
"""

TOKEN_CASES = {
    "plain_flags": ["--freq", "2", "--tag", "a"],
    "empty_space_chinese_quotes": ["", " ", "a b", "中文", "引号\"双\"'单'"],
    "negative_double_dash_repeat": ["-3", "-1.5e3", "--", "-x", "--", "-x", "sub"],
    "glued_options_duplicates": ["--k=v", "-k5", "--k=v", "--k=v"],
    "subcommand": ["train", "--lr", "0.1", "--", "eval", "--split", "test"],
}


@pytest.fixture
def figs(tmp_path, monkeypatch):
    root = tmp_path / "figs"
    root.mkdir()
    monkeypatch.setenv("REC_FILE", str(tmp_path / "hot.jsonl"))
    monkeypatch.setenv("RUNS_FILE", str(tmp_path / "runs.txt"))
    yield root
    pool.shutdown_all(str(root), wait=True)


def _records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _native(python: str, script: Path, tokens: list[str], rec: Path, tmp: Path) -> dict:
    """同一个解释器原生跑同一份脚本：参照物（不经过 Tavotto 的任何一行）。"""
    cwd = tmp / "native_cwd"
    cwd.mkdir(exist_ok=True)
    env = {**os.environ, "REC_FILE": str(rec), "MPLBACKEND": "Agg"}
    done = subprocess.run(
        [python, str(script), *tokens],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert done.returncode == 0, done.stderr
    return _records(rec)[-1]


def _build(figs, name, script_text, tokens, entry="__main__"):
    (figs / name).write_text(script_text, encoding="utf-8")
    run = runconfig.selection_for(figs, name, tokens)
    context = {"run": run} if run is not None else {}
    worker, resp = pool.build(name, str(figs), entry, **context)
    return worker, resp, run


@needs_worker
@pytest.mark.parametrize("name", sorted(TOKEN_CASES))
def test_the_script_sees_exactly_the_tokens_native_would(figs, tmp_path, name):
    """A02/A03：sys.argv 的内容、顺序、数量，曲线数组，解释器——逐项等于原生。"""
    tokens = TOKEN_CASES[name]
    worker, resp, run = _build(figs, "dump.py", DUMP_SCRIPT, tokens)
    hot = _records(tmp_path / "hot.jsonl")[-1]
    native = _native(worker.python, figs / "dump.py", tokens, tmp_path / "native.jsonl", tmp_path)
    assert hot["argv"] == tokens == native["argv"]
    assert hot["argc"] == native["argc"] == len(tokens) + 1
    assert hot["argv0"] == native["argv0"] == "dump.py"
    assert hot["ydata"] == native["ydata"] == [float(len(t)) for t in tokens]
    assert (hot["exe"], hot["prefix"]) == (native["exe"], native["prefix"])
    # 描述符里的资产身份带着这份配置，参数值不在里面
    ids = [d["asset_id"] for d in resp["descriptors"]]
    assert ids == [figcapture.runtime_asset_id("dump.py", "result", run.config_id)]
    assert all(token not in json.dumps(resp["descriptors"]) for token in tokens if len(token) > 3)


@needs_worker
def test_without_arguments_the_script_sees_only_itself(figs, tmp_path):
    """C20：没给参数 = 旧行为；资产 id 与 T03 之前逐字节相同。"""
    worker, resp, run = _build(figs, "dump.py", DUMP_SCRIPT, None)
    hot = _records(tmp_path / "hot.jsonl")[-1]
    assert run is None and hot["argv"] == [] and hot["argc"] == 1
    assert [d["asset_id"] for d in resp["descriptors"]] == ["runtime:dump.py#result"]
    assert not any("run_config" in d for d in resp["descriptors"])


FFT_ARGS = [
    "--freq",
    "3",
    "--amp",
    "2.5",
    "--phase",
    "0.25",
    "--n",
    "16",
    "--tag",
    "T",
    "--mode",
    "m",
]


@needs_worker
def test_six_required_arguments_run_and_match_native_while_the_missing_case_is_classified(
    figs, tmp_path
):
    """A01/A06：六必填 FFT 等价 fixture——能填、能传、曲线与原生逐点相同；缺参仍是 `script_needs_arguments`，
    且只有 argparse 的实际证据才给 `parse_kind`。"""
    # 缺参：旧行为（没配置）——分类准确，执行恰好一次（不盲试别的入口、不反复解析）
    (figs / "fft.py").write_text(FFT_SCRIPT, encoding="utf-8")
    with pytest.raises(pool.WorkerError) as missing:
        pool.build("fft.py", str(figs), "__main__")
    assert missing.value.code == "script_needs_arguments"
    assert missing.value.extra["parse_kind"] == "missing_required"
    assert "argv_count" not in missing.value.extra
    assert (tmp_path / "runs.txt").read_text(encoding="utf-8") == "x"
    pool.invalidate("fft.py", str(figs))

    worker, resp, run = _build(figs, "fft.py", FFT_SCRIPT, FFT_ARGS)
    hot = _records(tmp_path / "hot.jsonl")[-1]
    native = _native(worker.python, figs / "fft.py", FFT_ARGS, tmp_path / "n.jsonl", tmp_path)
    assert hot["argv"] == FFT_ARGS
    assert hot["ydata"] == native["ydata"] and len(hot["ydata"]) == 16
    assert hot["title"] == native["title"] == "T/m"
    assert resp["stems"] and run is not None


@needs_worker
def test_a_value_the_script_parser_rejects_is_reported_as_such_without_leaking_it(figs, tmp_path):
    """给了精确 token、脚本自己的解析器仍拒绝：同一个稳定码，额外带个数与（有证据的）类别；
    参数值不进错误的 message / extra。"""
    bad = ["--freq", "oops-" + SENTINEL, *FFT_ARGS[2:]]
    (figs / "fft.py").write_text(FFT_SCRIPT, encoding="utf-8")
    run = runconfig.selection_for(figs, "fft.py", bad)
    with pytest.raises(pool.WorkerError) as rejected:
        pool.build("fft.py", str(figs), "__main__", run=run)
    exc = rejected.value
    assert exc.code == "script_needs_arguments"
    assert exc.extra["argv_count"] == len(bad) and exc.extra["parse_kind"] == "invalid_value"
    assert SENTINEL not in str(exc) and SENTINEL not in json.dumps(exc.extra)


PLAIN_EXIT_SCRIPT = """\
import sys
sys.exit(2)
"""

#: 脚本自己的子类把 `error` 的文字换了：抛出点仍在 argparse（`parser.exit`），但 message 不是 argparse 的固定句式
CUSTOM_ERROR_SCRIPT = """\
import argparse

class P(argparse.ArgumentParser):
    def error(self, message):
        super().error("custom text, nothing like the stock argparse sentences")

P().parse_args()
"""


@needs_worker
def test_a_plain_exit_2_is_never_guessed_to_be_a_parse_failure(figs):
    """A06：普通 `sys.exit(2)` 是 `script_exited`，没有 `parse_kind`；argparse 帧在但句式对不上也不猜。"""
    (figs / "plain.py").write_text(PLAIN_EXIT_SCRIPT, encoding="utf-8")
    run = runconfig.selection_for(figs, "plain.py", ["--x", "1"])
    with pytest.raises(pool.WorkerError) as plain:
        pool.build("plain.py", str(figs), "__main__", run=run)
    assert plain.value.code == "script_exited"
    assert "parse_kind" not in (plain.value.extra or {})

    (figs / "custom.py").write_text(CUSTOM_ERROR_SCRIPT, encoding="utf-8")
    run = runconfig.selection_for(figs, "custom.py", ["--x", "1"])
    with pytest.raises(pool.WorkerError) as custom:
        pool.build("custom.py", str(figs), "__main__", run=run)
    assert custom.value.code == "script_needs_arguments"  # 参数解析库自己抛的（抛出点证据）
    assert "parse_kind" not in custom.value.extra  # 但句式不是 argparse 的固定句式：不猜


@needs_worker
def test_import_time_parsing_sees_the_arguments_before_any_user_code(figs, tmp_path):
    """A07：模块顶层的 `parse_args()` 在第一行用户代码之前就看得到 argv（排在 paper_style / runpy 之前）。"""
    worker, resp, run = _build(figs, "imp.py", IMPORT_TIME_SCRIPT, ["--n", "4"], entry="main")
    assert _records(tmp_path / "hot.jsonl")[-1] == {"argv": ["--n", "4"], "n": 4}


@needs_worker
def test_two_configurations_of_one_script_never_share_a_session_or_an_identity(figs, tmp_path):
    a_tokens, b_tokens = ["aa", "bbbb"], ["c", "ddd", "eeeee"]
    wa, ra, run_a = _build(figs, "dump.py", DUMP_SCRIPT, a_tokens)
    wb, rb, run_b = _build(figs, "dump.py", DUMP_SCRIPT, b_tokens)
    assert wa is not wb and wa.out_dir != wb.out_dir
    assert run_a.config_id != run_b.config_id
    id_a, id_b = ra["descriptors"][0]["asset_id"], rb["descriptors"][0]["asset_id"]
    assert id_a != id_b and id_a.endswith(run_a.config_id) and id_b.endswith(run_b.config_id)
    # 再取各自的会话：热态，不再执行（记录行数不增）
    rows = len(_records(tmp_path / "hot.jsonl"))
    assert pool.get("dump.py", str(figs), "__main__", run=run_a) is wa
    assert pool.get("dump.py", str(figs), "__main__", run=run_b) is wb
    assert len(_records(tmp_path / "hot.jsonl")) == rows == 2
    # 只作废一份配置：另一份的热会话还在
    pool.invalidate("dump.py", str(figs), run=run_a)
    assert pool.peek("dump.py", str(figs), run=run_b) is wb
    assert pool.peek("dump.py", str(figs), run=run_a) is None


@needs_worker
def test_a_cold_replay_uses_the_frozen_configuration_not_the_latest_one(figs, tmp_path):
    """R03：冷重放（写回验证用的 `one_shot`）跑的是热会话冻结的 argv；换了别的配置也不影响它。"""
    first = ["aa", "bbbb"]
    worker, _resp, run = _build(figs, "dump.py", DUMP_SCRIPT, first)
    # 之后用户又跑了另一份配置（"项目最新配置"）
    _build(figs, "dump.py", DUMP_SCRIPT, ["zzzzzz"])
    rows = len(_records(tmp_path / "hot.jsonl"))
    replay = pool.one_shot("dump.py", str(figs), "__main__", run=worker.run)
    try:
        replay.ensure_built()
    finally:
        pool.discard(replay)
    cold = _records(tmp_path / "hot.jsonl")
    assert len(cold) == rows + 1  # 真实新尝试各算一次
    assert cold[-1]["argv"] == first and cold[-1]["ydata"] == [2.0, 4.0]


@needs_worker
def test_missing_or_bad_private_argv_payload_never_executes_empty_arguments(figs):
    """缺失 / 坏载荷必须在用户脚本之前被拒绝；同一进程仍能响应，错误中不回显 token。"""
    (figs / "dump.py").write_text(
        "raise AssertionError('user script executed')\n", encoding="utf-8"
    )
    spec = execspec.safe_spec(
        "dump.py",
        str(figs),
        "__main__",
        interpreter=WORKER_PY,
        sandbox=str(figs.parent / "sb"),
        argv=["placeholder"],
        run_config="rc_test",
    )
    base = execspec.worker_argv(spec, worker_py=pool.WORKER_PY, out_dir=figs.parent / "o")
    requests = [
        pool.build_envelope({"cmd": "build", **payload})
        for payload in (
            {},
            {"run": SENTINEL},
            {"run": {"config_id": "wrong", "argv": [SENTINEL], "sensitive": True}},
            {"run": {"config_id": "rc_test", "argv": [1, 2], "sensitive": False}},
            {"run": {"config_id": "rc_test", "argv": [SENTINEL], "sensitive": "false"}},
        )
    ]
    done = subprocess.run(
        base,
        input="".join(json.dumps(r) + "\n" for r in [*requests, {"cmd": "build"}]),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    responses = [json.loads(line) for line in done.stdout.splitlines()]
    assert len(responses) == len(requests) + 1
    assert all(r["error"]["code"] == "bad_request" for r in responses[:-1])
    assert not responses[-1]["ok"] and "required" in responses[-1]["error"]
    assert "user script executed" not in done.stdout
    assert SENTINEL not in done.stdout + done.stderr
