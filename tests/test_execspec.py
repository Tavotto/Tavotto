"""ExecutionSpec 与 CapturedFigureDescriptor 的模型契约（Session 2，ADR 0013/0014）。

这里不需要 matplotlib——验的是**模型本身**：safe 档默认值只有一个权威出处、
JSON 序列化可逆且坏数据在边界上死、asset id / fingerprint 不含任何机器相关
路径、writeback 能力只能派生不能指定、worker argv 的形状与两条控制面历史上
手拼的逐字节一致（golden）。真跑 worker 的对拍在
`test_compat_capture_parity.py`。
"""
import dataclasses
from pathlib import Path

import pytest

from tavotto.engine import execspec, figcapture, pool


def _spec(**overrides):
    kw = dict(script="fig.py", figures_dir="/proj", entry="main",
              interpreter="/usr/bin/python3", sandbox="/proj/.box")
    kw.update(overrides)
    return execspec.safe_spec(kw.pop("script"), kw.pop("figures_dir"),
                              kw.pop("entry"), **kw)


# ===========================================================================
# ExecutionSpec：safe 默认值 / 序列化 / 校验
# ===========================================================================
class TestSafeDefaults:
    def test_safe_spec_is_the_single_authority_for_defaults(self):
        """safe 档的运行时默认值只写在 `safe_spec()` 里——逐项钉住。"""
        s = _spec()
        assert s.profile == execspec.PROFILE_SAFE
        assert s.target_kind == execspec.TARGET_SCRIPT
        assert s.argv == ()                      # 脚本的 sys.argv[1:] 为空
        assert s.passthrough_savefig is False    # savefig 吞掉捕获
        assert s.env is None                     # 默认原样继承，不复制父环境
        assert s.entry == "main"
        assert s.cwd == "/proj/.box"

    def test_profile_constants_are_shared_with_figcapture(self):
        """profile 常量的唯一出处在 figcapture（worker/browser 平铺 import
        够得着的那一层），execspec 只是 re-export。"""
        assert execspec.PROFILE_SAFE is figcapture.PROFILE_SAFE
        assert execspec.PROFILE_NATIVE is figcapture.PROFILE_NATIVE


class TestSerialization:
    def test_json_roundtrip_is_lossless(self):
        s = _spec(env={"MPLCONFIGDIR": "/data/mpl"})
        assert execspec.spec_from_payload(s.to_payload()) == s

    def test_roundtrip_survives_json_encoding(self):
        import json
        s = _spec()
        wire = json.dumps(s.to_payload(), ensure_ascii=False)
        assert execspec.spec_from_payload(json.loads(wire)) == s

    def test_stable_payload_has_no_machine_specific_fields(self):
        """fingerprint / 持久化只准用 stable_payload——机器相关路径一个不进。

        interpreter / cwd / project_root 是绝对路径，env 增量里有 MPLCONFIGDIR
        这类本机路径：任何一个混进去，同一个项目换台机器指纹就变。
        """
        stable = _spec(env={"MPLCONFIGDIR": "/data/mpl"}).stable_payload()
        assert set(stable) == {*execspec.STABLE_FIELDS, "spec_version"}
        for banned in ("interpreter", "cwd", "project_root", "env"):
            assert banned not in stable

    def test_unknown_spec_version_is_rejected(self):
        payload = _spec().to_payload()
        payload["spec_version"] = 99
        with pytest.raises(ValueError, match="spec_version"):
            execspec.spec_from_payload(payload)


class TestValidation:
    def test_invalid_profile_is_rejected(self):
        with pytest.raises(ValueError, match="profile"):
            dataclasses.replace(_spec(), profile="yolo")

    def test_invalid_target_kind_is_rejected(self):
        with pytest.raises(ValueError, match="target_kind"):
            dataclasses.replace(_spec(), target_kind="notebook")

    def test_invalid_payloads_are_rejected(self):
        base = _spec().to_payload()
        for key, bad in [("profile", "trusted"), ("target_kind", "shell"),
                         ("interpreter", ""), ("argv", "not-a-list"),
                         ("target", "/abs/fig.py")]:
            payload = {**base, key: bad}
            with pytest.raises(ValueError):
                execspec.spec_from_payload(payload)

    def test_safe_profile_requires_an_entry(self):
        with pytest.raises(ValueError, match="entry"):
            _spec(entry=None)

    def test_module_target_must_be_a_dotted_identifier(self):
        with pytest.raises(ValueError, match="模块名"):
            dataclasses.replace(_spec(), target_kind=execspec.TARGET_MODULE,
                                target="not a module!")


class TestScriptNormalization:
    def test_backslashes_are_normalized_to_posix(self):
        """注册表键是 POSIX 相对路径；Windows 侧传进反斜杠也得归一。"""
        assert _spec(script="panels\\fig.py").target == "panels/fig.py"

    def test_absolute_script_targets_are_rejected(self):
        """target 要进 fingerprint / 未来进文档，绝对路径 = 跨不了机器。"""
        with pytest.raises(ValueError, match="相对"):
            _spec(script="/abs/fig.py")
        with pytest.raises(ValueError, match="相对"):
            _spec(script="C:\\proj\\fig.py")


# ===========================================================================
# worker argv：两条控制面共用的唯一出处
# ===========================================================================
class TestWorkerArgv:
    def test_the_argv_shape_is_frozen(self):
        """golden：与 2026-08-25 之前 `EngineWorker.__init__` / `_spawn_spec`
        两处手拼的命令行**逐字节一致**——这次是重构不是改语义。
        （两条控制面的互相对拍在 `test_workerd_pool.py`。）

        `--script` 的期望用 `Path` 拼：旧代码就是 `str(Path(figures_dir) /
        script_name)`，Windows 上产出反斜杠**是被冻结的旧语义**，不是漂移；
        其余元素全部逐字面透传。"""
        s = _spec()
        argv = execspec.worker_argv(s, worker_py="/eng/worker.py",
                                    out_dir="/cache/out", runtime_args=["-B"])
        assert argv == ["/usr/bin/python3", "-B", "/eng/worker.py",
                        "--script", str(Path("/proj") / "fig.py"),
                        "--figures-dir", "/proj",
                        "--out-dir", "/cache/out",
                        "--sandbox", "/proj/.box",
                        "--entry", "main"]

    def test_engine_worker_carries_the_spec_it_spawned_from(self, monkeypatch, tmp_path):
        """EngineWorker 的 Popen argv 必须就是它自己那份 spec 的 worker_argv。"""
        box = {}

        class _Rec:
            def __init__(self, argv, **kw):
                box["argv"] = argv
                self.pid = 1

            def poll(self):
                return None

        monkeypatch.setattr(pool.subprocess, "Popen", _Rec)
        monkeypatch.setattr(pool, "select_worker_python",
                            lambda: ("/usr/bin/python3", pool.SOURCE_SYSTEM))
        w = pool.EngineWorker("fig.py", str(tmp_path), "draw")
        assert w.spec.profile == execspec.PROFILE_SAFE
        assert w.spec.target == "fig.py"
        assert w.spec.entry == "draw"
        assert box["argv"] == execspec.worker_argv(
            w.spec, worker_py=pool.WORKER_PY, out_dir=w.out_dir)

    def test_native_is_not_served_yet(self):
        native = execspec.ExecutionSpec(
            profile=execspec.PROFILE_NATIVE, interpreter="/usr/bin/python3",
            target_kind=execspec.TARGET_SCRIPT, target="fig.py", entry=None,
            argv=("--dataset", "run7"), cwd="/home/u/proj", env=None,
            project_root="/home/u/proj", passthrough_savefig=True)
        with pytest.raises(ValueError, match="native"):
            execspec.worker_argv(native, worker_py="/w.py", out_dir="/o")


# ===========================================================================
# CapturedFigureDescriptor：id / fingerprint / 能力派生
# ===========================================================================
def _descriptor(**overrides):
    kw = dict(script="fig.py", entry="main", stem="Fig1",
              capture_source=figcapture.SOURCE_SAVEFIG,
              execution_profile=figcapture.PROFILE_SAFE,
              size_mm=(80.0, 60.0), source_fingerprint="sha256:feed")
    kw.update(overrides)
    return figcapture.build_descriptor(**kw)


class TestAssetId:
    def test_the_id_shape_is_the_adr_0013_one(self):
        assert figcapture.runtime_asset_id("panels/myplot.py", "myplot") == \
            "runtime:panels/myplot.py#myplot"

    def test_the_id_has_no_project_path_dimension(self):
        """id 只由 (脚本相对路径, stem) 决定——**没有任何参数能把绝对项目
        路径混进来**。跨机器/跨挂载点的稳定性由这条签名保证；真 worker 在
        两个不同项目根下产出同一 id 的对拍在 parity 测试里。"""
        import inspect
        params = inspect.signature(figcapture.runtime_asset_id).parameters
        assert list(params) == ["script", "stem"]

    def test_absolute_script_paths_are_rejected(self):
        with pytest.raises(ValueError, match="相对"):
            figcapture.runtime_asset_id("/abs/fig.py", "Fig1")

    def test_different_stems_and_scripts_do_not_collide(self):
        ids = {figcapture.runtime_asset_id("a.py", "fig"),
               figcapture.runtime_asset_id("a.py", "fig-2"),
               figcapture.runtime_asset_id("b.py", "fig"),
               figcapture.runtime_asset_id("sub/a.py", "fig")}
        assert len(ids) == 4

    def test_entry_deliberately_does_not_enter_the_id(self):
        """ADR 0013 决策 2：注册表里一个脚本只有一个 entry，(script, stem)
        已唯一；entry 编进 id 的话，用户改脚本换入口重新探测后，同一张图
        变成新身份，历史 override 全部成孤儿。entry 的差异由 descriptor 的
        独立字段与 fingerprint 承担。"""
        a = _descriptor(entry="main")
        b = _descriptor(entry="__main__",
                        source_fingerprint="sha256:other")
        assert a.asset_id == b.asset_id
        assert a.entry != b.entry
        assert a.source_fingerprint != b.source_fingerprint


class TestWritebackCapabilityIsDerived:
    def test_the_factory_has_no_way_to_dictate_capability(self):
        """`can_writeback_*` 不在工厂参数里——前端与上游**没有**渠道去猜/钦定。"""
        import inspect
        params = inspect.signature(figcapture.build_descriptor).parameters
        assert "can_writeback_artifact" not in params
        assert "can_writeback_source" not in params

    def test_pyplot_capture_never_carries_an_artifact(self):
        """pyplot 捕获的图从没存过盘：磁盘上碰巧同名的文件不是它的原件，
        写回它 = 覆盖一个不相干的文件。工厂对这种组合直接抛。"""
        with pytest.raises(ValueError, match="pyplot"):
            _descriptor(capture_source=figcapture.SOURCE_PYPLOT,
                        original_artifact="Fig1.pdf")

    def test_pyplot_capture_cannot_write_back(self):
        d = _descriptor(capture_source=figcapture.SOURCE_PYPLOT)
        assert d.original_artifact is None
        assert d.can_writeback_artifact is False

    def test_savefig_with_an_artifact_on_disk_can(self):
        d = _descriptor(original_artifact="Fig1.pdf")
        assert d.can_writeback_artifact is True

    def test_savefig_without_an_artifact_cannot(self):
        assert _descriptor().can_writeback_artifact is False

    def test_source_writeback_is_always_false_in_v1(self):
        """改写用户脚本（Pylustrator 的默认行为）v1 明确不做（ADR 0013 §7）。"""
        assert _descriptor(original_artifact="Fig1.pdf").can_writeback_source is False

    def test_payloads_lying_about_capability_are_rejected(self):
        payload = _descriptor(capture_source=figcapture.SOURCE_PYPLOT).to_payload()
        payload["can_writeback_artifact"] = True
        with pytest.raises(ValueError, match="can_writeback_artifact"):
            figcapture.descriptor_from_payload(payload)


class TestDescriptorSerialization:
    def test_payload_roundtrip(self):
        d = _descriptor(original_artifact="Fig1.pdf")
        assert figcapture.descriptor_from_payload(d.to_payload()) == d

    def test_the_wire_shape_is_frozen(self):
        """golden/contract：payload 的键集与取值形态。改这里 = 改协议——
        先想清楚旧调用方（它们必须容忍**新增**，但不许被改名/删除背刺）。"""
        assert _descriptor().to_payload() == {
            "asset_id": "runtime:fig.py#Fig1",
            "script": "fig.py",
            "entry": "main",
            "stem": "Fig1",
            "capture_source": "savefig",
            "execution_profile": "safe",
            "original_artifact": None,
            "size_mm": [80.0, 60.0],
            "source_fingerprint": "sha256:feed",
            "can_writeback_artifact": False,
            "can_writeback_source": False,
        }

    def test_invalid_enums_are_rejected(self):
        with pytest.raises(ValueError, match="capture_source"):
            _descriptor(capture_source="disk")
        with pytest.raises(ValueError, match="execution_profile"):
            _descriptor(execution_profile="chrooted")


class TestSourceFingerprint:
    def _fp(self, **overrides):
        kw = dict(script="fig.py", entry="main",
                  profile=figcapture.PROFILE_SAFE,
                  matplotlib_version="3.11.1")
        body = overrides.pop("script_bytes", b"print('hi')")
        kw.update(overrides)
        return figcapture.source_fingerprint(body, **kw)

    def test_is_deterministic(self):
        assert self._fp() == self._fp()
        assert self._fp().startswith("sha256:")

    def test_changes_with_each_declared_input(self):
        """声称参与指纹的每一维都必须真的参与——少一维就是撒谎的 stale hint。"""
        base = self._fp()
        assert base != self._fp(script_bytes=b"print('bye')")
        assert base != self._fp(entry="__main__")
        assert base != self._fp(matplotlib_version="3.10.8")
        assert base != self._fp(argv=("--fast",))

    def test_line_endings_do_not_split_the_fingerprint(self):
        """CRLF/CR/LF 的同一份源码 = 同一个指纹。

        worker 从磁盘 `read_bytes`（Windows 检出是 CRLF），browser 拿编辑器
        `str`（LF）——行尾不改变 Python 语义，却曾让描述符对拍在 Windows 腿
        上分叉（CI #444）。归一在 `source_fingerprint` 唯一出处内做。"""
        lf = self._fp(script_bytes=b"import x\nprint('hi')\n")
        assert lf == self._fp(script_bytes=b"import x\r\nprint('hi')\r\n")
        assert lf == self._fp(script_bytes=b"import x\rprint('hi')\r")
        # 归一不是钝化：真实内容差异照样分开
        assert lf != self._fp(script_bytes=b"import x\nprint('bye')\n")

    def test_does_not_depend_on_any_absolute_path(self):
        """指纹的输入签名里没有项目根/解释器/cwd——跨机器稳定性的另一半。"""
        import inspect
        params = set(inspect.signature(figcapture.source_fingerprint).parameters)
        assert not params & {"project_root", "figures_dir", "interpreter", "cwd"}


class TestFindOriginalArtifact:
    def test_finds_by_extension_priority(self, tmp_path):
        (tmp_path / "Fig1.png").write_bytes(b"x")
        (tmp_path / "Fig1.pdf").write_bytes(b"x")
        assert figcapture.find_original_artifact(str(tmp_path), "Fig1") == "Fig1.pdf"

    def test_returns_none_when_nothing_is_there(self, tmp_path):
        assert figcapture.find_original_artifact(str(tmp_path), "Fig1") is None

    def test_the_extension_table_is_the_single_authority(self):
        """discover.OUT_EXTS / handoff.OUT_EXTS 是 figcapture.ARTIFACT_EXTS
        的镜像别名——三处认的必须是同一张表。"""
        from tavotto.engine import discover, handoff
        assert discover.OUT_EXTS is figcapture.ARTIFACT_EXTS
        assert handoff.OUT_EXTS is figcapture.ARTIFACT_EXTS
