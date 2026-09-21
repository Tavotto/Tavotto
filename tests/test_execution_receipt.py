"""U01 共同合同（ADR 0053）的**模型**契约：LaunchContext / grant / DependencyIntent /
ExecutionReceipt / SourceArtifact / RenderPlan 引用。

这里不需要 matplotlib，也不起任何子进程——验的是模型本身：三分的 cwd 来源与写入模式
只由 spec 派生、授权只由 `workdir` 记账、依赖意图无损且 unknown 不是空、回执的三种身份
各答各的问题、字节 hash 不回写进语义身份。真 worker 自报的对拍在
`test_worker_runtime_report.py`，穿过公共入口的在 `test_foundation_harness.py`。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import (
    config as engine_config,
    depresolve,
    execspec,
    exportreq,
    figcapture,
    receipt,
    workdir,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "foundation"


def _safe(cwd_mode=execspec.CWD_SANDBOX, **kw):
    return execspec.safe_spec(
        "figs/fig.py",
        "/proj",
        "main",
        interpreter=kw.get("interpreter", "/envs/a/bin/python"),
        sandbox="/proj/.box",
        cwd_mode=cwd_mode,
    )


def _native():
    return execspec.native_spec(
        "fig.py",
        interpreter="/home/u/.venv/bin/python",
        cwd="/home/u/lab",
        project_root="/home/u/lab",
        argv=("--fast",),
    )


# ===========================================================================
# LaunchContext：cwd 三分 + 写入模式，只由 spec 派生
# ===========================================================================
class TestLaunchContext:
    def test_safe_sandbox_is_the_sandbox_origin_with_sandboxed_writes(self):
        ctx = execspec.launch_context(_safe())
        assert ctx["cwd_origin"] == execspec.CWD_ORIGIN_SANDBOX
        assert ctx["write_mode"] == execspec.WRITE_MODE_SANDBOXED
        assert ctx["cwd_mode"] == execspec.CWD_SANDBOX
        assert ctx["grant"] is None  # 没给 = 没附带授权信息，不是「没授权」

    def test_safe_project_mode_is_script_parent_not_project_root(self):
        """FO-041 的三分要分得开：ADR 0047 的 project 档 cwd 是**脚本目录**，语义不变。"""
        ctx = execspec.launch_context(_safe(execspec.CWD_PROJECT))
        assert ctx["cwd_origin"] == execspec.CWD_ORIGIN_SCRIPT_PARENT
        assert ctx["cwd_origin"] != execspec.CWD_ORIGIN_PROJECT_ROOT
        assert ctx["write_mode"] == execspec.WRITE_MODE_PROJECT_DIR

    def test_native_is_the_invocation_cwd_with_no_guards(self):
        ctx = execspec.launch_context(_native())
        assert ctx["cwd_origin"] == execspec.CWD_ORIGIN_INVOCATION
        assert ctx["write_mode"] == execspec.WRITE_MODE_UNRESTRICTED
        assert ctx["argv"] == ["--fast"]
        assert ctx["entry"] is None

    def test_every_cwd_origin_has_exactly_one_producer(self):
        """U01 时 `project.root` 只是占位（没有 spec 组合派生出它）；U03 加了 `cwd_mode=project_root`
        这一档之后四个来源各有且只有一个生产者。`project` 档**没有**被改成项目根（ADR 0047
        的语义不变），而是多了第三档——这条钉住这两件事。"""
        by_origin = {
            execspec.launch_context(spec)["cwd_origin"]: spec.cwd_mode
            for spec in (
                _safe(),
                _safe(execspec.CWD_PROJECT),
                _safe(execspec.CWD_PROJECT_ROOT),
                _native(),
            )
        }
        assert set(by_origin) == set(execspec.CWD_ORIGINS)
        assert by_origin[execspec.CWD_ORIGIN_SCRIPT_PARENT] == execspec.CWD_PROJECT
        assert by_origin[execspec.CWD_ORIGIN_PROJECT_ROOT] == execspec.CWD_PROJECT_ROOT

    def test_context_carries_no_machine_paths(self):
        ctx = execspec.launch_context(_safe(interpreter="/envs/secret-venv/bin/python"))
        text = json.dumps(ctx, ensure_ascii=False)
        for needle in ("/envs/", "/proj", ".box"):
            assert needle not in text, f"LaunchContext 里带了机器路径: {needle}"

    def test_grant_is_passed_through_verbatim_and_must_be_a_dict(self):
        grant = {"cwd_write": {"granted": True, "granted_at": 1.5}}
        assert execspec.launch_context(_safe(), grant=grant)["grant"] == grant
        with pytest.raises(ValueError, match="grant"):
            execspec.launch_context(_safe(), grant="yes")  # type: ignore[arg-type]

    def test_worker_argv_is_untouched_by_the_context(self):
        """LaunchContext 是派生视图：spec 与 argv 的 golden 一个字节不变。"""
        spec = _safe()
        argv = execspec.worker_argv(spec, worker_py="/w.py", out_dir="/o")
        execspec.launch_context(spec)
        assert execspec.worker_argv(spec, worker_py="/w.py", out_dir="/o") == argv
        assert "cwd_origin" not in spec.stable_payload()


# ===========================================================================
# grant：只由 workdir 记账（FO-047）
# ===========================================================================
class TestGrant:
    def test_switching_to_project_records_the_moment_and_back_erases_it(self, tmp_path):
        root = tmp_path / "p"
        root.mkdir()
        assert workdir.grant_for(root) == {
            "cwd_write": {"granted": False, "granted_at": None, "mode": None},
            "decided": False,
        }
        state = workdir.set_mode(root, workdir.MODE_PROJECT)
        grant = workdir.grant_for(root)["cwd_write"]
        assert grant["granted"] is True and grant["mode"] == workdir.MODE_PROJECT
        assert isinstance(grant["granted_at"], float)
        assert state["grant"]["cwd_write"] == grant  # state() 是同一份记录的投影
        # 再设一次不刷新时刻：授权是那一次点头，不是每次保存
        again = workdir.set_mode(root, workdir.MODE_PROJECT)
        assert again["grant"]["cwd_write"]["granted_at"] == grant["granted_at"]
        # 换到另一档授权模式（脚本目录 → 项目根）是新的一次点头：时刻重记、mode 跟着变
        rooted = workdir.set_mode(root, workdir.MODE_PROJECT_ROOT)["grant"]["cwd_write"]
        assert rooted["granted"] is True and rooted["mode"] == workdir.MODE_PROJECT_ROOT
        assert rooted["granted_at"] >= grant["granted_at"]
        # 切回沙盒 = 撤销授权；但「决定过」留着（U03：首开的确认框不再问）
        workdir.set_mode(root, workdir.MODE_SANDBOX)
        assert workdir.grant_for(root) == {
            "cwd_write": {"granted": False, "granted_at": None, "mode": None},
            "decided": True,
        }

    def test_legacy_setting_without_a_moment_is_granted_but_undated(self, tmp_path):
        """老设置只有 `{"mode": "project"}`：授予过但没记时刻——两个答案不许压成一个。"""
        root = tmp_path / "p"
        root.mkdir()
        engine_config.set_project_settings(str(root), {workdir.SETTINGS_KEY: {"mode": "project"}})
        assert workdir.grant_for(root) == {
            "cwd_write": {"granted": True, "granted_at": None, "mode": workdir.MODE_PROJECT},
            "decided": True,
        }
        assert workdir.mode_for(root) == workdir.MODE_PROJECT

    def test_grant_is_project_scoped(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir(), b.mkdir()
        workdir.set_mode(a, workdir.MODE_PROJECT)
        assert workdir.grant_for(b)["cwd_write"]["granted"] is False


# ===========================================================================
# DependencyIntent：无损，unknown 不是空依赖
# ===========================================================================
class TestDependencyIntent:
    @pytest.mark.parametrize(
        "line,expect",
        [
            ("tabulate[widechars]==0.9.0", ("tabulate", "==0.9.0", ("widechars",), "")),
            (
                'six==1.17.0; python_version < "3.0"',
                ("six", "==1.17.0", (), 'python_version < "3.0"'),
            ),
            ("six>=1.16,<2", ("six", ">=1.16,<2", (), "")),
            ("Sorted_Containers", ("sorted-containers", "", (), "")),
        ],
    )
    def test_extras_and_markers_survive(self, line, expect):
        it = depresolve.parse_intent(line)
        assert (it.name, it.specifier, it.extras, it.marker) == expect
        assert it.kind == depresolve.INTENT_KIND_REQUIREMENT
        assert it.raw == line

    @pytest.mark.parametrize(
        "line",
        [
            "-r base.txt",
            "--index-url https://x",
            "pkg @ https://x/y.whl",
            "../local",
            "tabulate[]==1",
        ],
    )
    def test_unparseable_lines_become_unknown_intents_not_nothing(self, line):
        it = depresolve.parse_intent(line)
        assert it is not None
        assert it.kind == depresolve.INTENT_KIND_UNKNOWN
        assert it.name == ""
        assert it.raw == line

    def test_blank_and_comment_lines_are_nothing(self):
        assert depresolve.parse_intent("") is None
        assert depresolve.parse_intent("   # only a comment") is None

    def test_the_install_path_grammar_is_untouched(self):
        """安全边界不动：安装路径仍只认窄语法（extras / marker 一律 None）。"""
        assert depresolve.parse_requirement("tabulate[widechars]==0.9.0") is None
        assert depresolve.parse_requirement('six==1.17.0; python_version < "3.0"') is None
        assert depresolve.parse_requirement("six==1.17.0") == ("six", "==1.17.0")

    def test_declared_intents_match_the_fixture_truth_file_by_file(self):
        """U00 夹具 ⑤ 的 truth.json 逐条对：名字 / 版本 / extras / marker 一个都不丢。"""
        truth = json.loads((FIXTURES / "dependency_declarations" / "truth.json").read_text("utf-8"))
        intents = depresolve.declared_intents(FIXTURES / "dependency_declarations")
        by_file: dict[str, list] = {}
        for it in intents:
            by_file.setdefault(it.source, []).append(it)
        for fname, rows in truth["files"].items():
            got = [
                (it.name, it.specifier, list(it.extras), it.marker or None) for it in by_file[fname]
            ]
            want = [(r["name"], r["spec"], r["extras"], r["marker"]) for r in rows]
            assert got == want, fname
        kinds = {it.source: it.kind for it in intents}
        assert kinds["constraints.txt"] == depresolve.INTENT_KIND_CONSTRAINT
        assert all(
            k == depresolve.INTENT_KIND_REQUIREMENT
            for f, k in kinds.items()
            if f != "constraints.txt"
        )
        groups = {it.group for it in intents if it.source == "pyproject.toml"}
        if sys.version_info >= (3, 11):  # tomllib 在：按 project / optional 分组
            assert "pyproject:project.dependencies" in groups
            assert "pyproject:optional-dependencies.report" in groups
        else:  # 3.10 退化路径只认「名字带 dependencies 的数组」，组名统一 pyproject
            assert groups == {"pyproject"}

    def test_conflicts_are_listed_not_resolved(self):
        """同名声明的 specifier 不一致就列出——**含 constraints.txt**（Codex #451 P2：
        夹具里 `tabulate==0.9.0` 与约束 `tabulate<0.9` 互相矛盾，之前被丢掉）。这里不做
        PEP 440 求值，只把「不一致」摆出来，裁决归 U04。"""
        intents = depresolve.declared_intents(FIXTURES / "dependency_declarations")
        found = {c["name"]: c for c in depresolve.conflicts(intents)}
        assert set(found) == {"six", "tabulate"}
        assert found["six"]["specifiers"] == ["==1.16.0", "==1.17.0"]
        assert found["tabulate"]["specifiers"] == ["<0.9", "==0.9.0"]
        assert depresolve.INTENT_KIND_CONSTRAINT in found["tabulate"]["kinds"]
        assert found["six"]["kinds"] == [depresolve.INTENT_KIND_REQUIREMENT]
        # 旧的安装路径解析器仍是「第一条静默胜出」——这里不改它，只让意图层看见冲突
        assert depresolve.parse_requirements_text("six==1.16.0\nsix==1.17.0") == {"six": "==1.16.0"}

    def test_an_unknown_line_in_a_requirements_file_is_kept(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("-r base.txt\nsix==1.17.0\n", encoding="utf-8")
        intents = depresolve.declared_intents(tmp_path)
        assert [(i.kind, i.raw) for i in intents] == [
            (depresolve.INTENT_KIND_UNKNOWN, "-r base.txt"),
            (depresolve.INTENT_KIND_REQUIREMENT, "six==1.17.0"),
        ]

    @pytest.mark.skipif(
        sys.version_info < (3, 11), reason="Poetry 表要 tomllib（3.10 退化路径不认表）"
    )
    def test_poetry_caret_and_table_values_are_unknown_not_stripped(self, tmp_path):
        """Codex（#455 转办，P2）：`requests = "^2.31"` 之前只留下名字——版本没了、raw 也是截断值，
        等于把一条约束偷偷放宽成「任意版本」。D14：未知约束必须明确停止。Poetry 自己的语法
        （`^` / `~` / 表值）在这里是 `unknown`，raw 保留原文；纯数字版本仍映射成 `==`，raw 也是原文。
        完整转换归 U04 / X01。"""
        (tmp_path / "pyproject.toml").write_text(
            "[tool.poetry.dependencies]\n"
            'python = "^3.10"\n'
            'requests = "^2.31"\n'
            'six = "1.17.0"\n'
            'numpy = {version = "~1.26", extras = ["all"]}\n',
            encoding="utf-8",
        )
        by_raw = {it.raw: it for it in depresolve.declared_intents(tmp_path)}
        assert "python" not in " ".join(by_raw)  # 解释器版本不是依赖
        caret = by_raw['requests = "^2.31"']
        assert caret.kind == depresolve.INTENT_KIND_UNKNOWN and caret.name == ""
        table = by_raw['numpy = {version = "~1.26", extras = ["all"]}']
        assert table.kind == depresolve.INTENT_KIND_UNKNOWN
        plain = by_raw['six = "1.17.0"']
        assert (plain.name, plain.specifier, plain.kind) == ("six", "==1.17.0", "requirement")
        assert all(it.group == "pyproject:tool.poetry.dependencies" for it in by_raw.values())

    def test_intent_payload_round_trips_every_field(self):
        it = depresolve.parse_intent("a[b]>=1; os_name == 'nt'", group="g", source="s")
        assert it.to_payload() == {
            "name": "a",
            "specifier": ">=1",
            "extras": ["b"],
            "marker": "os_name == 'nt'",
            "group": "g",
            "source": "s",
            "kind": "requirement",
            "raw": "a[b]>=1; os_name == 'nt'",
        }


# ===========================================================================
# ExecutionReceipt：worker 自报 + 控制面关联；身份三分
# ===========================================================================
@dataclasses.dataclass
class _FakeWorker:
    spec: execspec.ExecutionSpec
    generation: int = 1
    script_sha1: str = "abc123"
    python_source: str = "project_venv"
    python: str = "/envs/a/bin/python"


def _runtime(prefix="/envs/a", **over):
    rt = {
        "runtime_report_version": 1,
        "python_version": "3.13.11",
        "python_implementation": "CPython",
        "executable": f"{prefix}/bin/python",
        "prefix": prefix,
        "base_prefix": "/opt/python3.13",
        "platform": "darwin",
        "machine": "arm64",
        "cwd": "/proj/.box",
        "argv0": "/proj/figs/fig.py",
        "packages": {"matplotlib": "3.10.8", "numpy": "2.4.3"},
    }
    rt.update(over)
    return rt


def _receipt(prefix="/envs/a", runtime=True, **over):
    worker = _FakeWorker(spec=_safe(interpreter=f"{prefix}/bin/python"))
    for k, v in over.items():
        setattr(worker, k, v)
    resp = {"stems": {}, "descriptors": [{"stem": "fig"}]}
    if runtime:
        resp["runtime"] = _runtime(prefix)
    return receipt.from_worker(
        worker, resp, control_plane=receipt.CONTROL_PLANE_PYTHON, grant=workdir.grant_for("/nope")
    )


class TestExecutionReceipt:
    def test_worker_report_makes_it_complete_and_its_absence_is_partial_not_zero(self):
        full = _receipt()
        assert full.completeness == receipt.COMPLETENESS_COMPLETE
        assert full.runtime["prefix"] == "/envs/a"
        old = _receipt(runtime=False)
        assert old.completeness == receipt.COMPLETENESS_PARTIAL
        assert old.runtime is None  # 没量，不是量到了空
        assert old.to_payload()["runtime"] is None

    def test_public_identity_has_no_paths_and_survives_a_prefix_change(self):
        """两个 venv、同样的意图与版本：公开身份相同，私有失效键不同。"""
        a, b = _receipt("/envs/a"), _receipt("/envs/b")
        assert a.public_identity() == b.public_identity()
        assert a.private_invalidation_key() != b.private_invalidation_key()
        assert a.receipt_id != b.receipt_id
        public = json.dumps(a.to_payload(), ensure_ascii=False)
        for needle in ("/envs/", "/opt/python", "/proj"):
            assert needle not in public, f"默认投影带了机器路径: {needle}"
        private = json.dumps(a.to_payload(include_private=True), ensure_ascii=False)
        assert "/envs/a" in private and "/opt/python3.13" in private

    def test_package_versions_are_public_identity(self):
        a = _receipt()
        worker = _FakeWorker(spec=_safe())
        resp = {"descriptors": [], "runtime": _runtime(packages={"matplotlib": "3.11.1"})}
        b = receipt.from_worker(worker, resp, control_plane="python_pool", grant=None)
        assert a.public_identity() != b.public_identity()

    def test_generation_and_project_change_the_receipt_id(self):
        base = _receipt()
        assert _receipt(generation=2).receipt_id != base.receipt_id
        other_project = receipt.ExecutionReceipt(
            **{**dataclasses.asdict(base), "project_root": "/other"}
        )
        assert other_project.receipt_id != base.receipt_id
        assert other_project.public_identity() == base.public_identity()  # 意图没变

    def test_source_revision_is_part_of_the_public_identity(self):
        assert _receipt(script_sha1="fff").public_identity() != _receipt().public_identity()

    def test_launch_context_grant_is_not_identity(self):
        worker = _FakeWorker(spec=_safe())
        resp = {"descriptors": [], "runtime": _runtime()}
        g1 = receipt.from_worker(
            worker,
            resp,
            control_plane="python_pool",
            grant={"cwd_write": {"granted": False, "granted_at": None}},
        )
        g2 = receipt.from_worker(
            worker,
            resp,
            control_plane="python_pool",
            grant={"cwd_write": {"granted": True, "granted_at": 9.0}},
        )
        assert g1.public_identity() == g2.public_identity()
        assert g2.launch_context["grant"]["cwd_write"]["granted"] is True

    @pytest.mark.parametrize(
        "field,value,match",
        [
            ("generation", 0, "generation"),
            ("generation", True, "generation"),
            ("control_plane", "cloud", "control_plane"),
            ("profile", "turbo", "profile"),
            ("runtime", "3.13", "runtime"),
        ],
    )
    def test_bad_values_die_at_the_boundary(self, field, value, match):
        base = dataclasses.asdict(_receipt())
        base[field] = value
        with pytest.raises(ValueError, match=match):
            receipt.ExecutionReceipt(**base)

    def test_a_malformed_runtime_report_is_treated_as_absent(self):
        worker = _FakeWorker(spec=_safe())
        r = receipt.from_worker(worker, {"runtime": "yes"}, control_plane="python_pool", grant=None)
        assert r.completeness == receipt.COMPLETENESS_PARTIAL


# ===========================================================================
# SourceArtifact：字节 hash 与语义身份是两个字段
# ===========================================================================
class TestSourceArtifact:
    def test_static_artifact_from_the_fixture_pdf(self):
        pdf = FIXTURES / "pdf_png_assets" / "page.pdf"
        art = figcapture.source_artifact_from_file(
            pdf, source_id="page.pdf", origin=figcapture.ORIGIN_STATIC
        )
        assert art.kind == "pdf"
        assert art.bytes_sha256 == hashlib.sha256(pdf.read_bytes()).hexdigest()
        assert art.size_bytes == pdf.stat().st_size
        assert art.receipt_id is None and art.generation is None and art.patch_hash is None
        assert art.to_payload()["source_artifact_version"] == figcapture.SOURCE_ARTIFACT_VERSION

    def test_execution_artifact_needs_a_receipt_and_static_must_not_have_one(self, tmp_path):
        f = tmp_path / "x.png"
        f.write_bytes(b"\x89PNG not really but bytes")
        with pytest.raises(ValueError, match="receipt_id"):
            figcapture.source_artifact_from_file(
                f, source_id="runtime:s.py#x", origin=figcapture.ORIGIN_EXECUTION
            )
        with pytest.raises(ValueError, match="static"):
            figcapture.source_artifact_from_file(
                f, source_id="x.png", origin=figcapture.ORIGIN_STATIC, generation=1
            )

    def test_semantic_identity_ignores_the_bytes_but_not_the_receipt(self, tmp_path):
        """语义身份跟着回执的**公开**身份走（Codex #451 P2）：`receipt_id` 含私有键与
        generation，同一台机器上重建一代、或换一台机器，语义没变、id 却变了——它只是
        实例元数据。"""
        f1, f2 = tmp_path / "a.pdf", tmp_path / "b.pdf"
        f1.write_bytes(b"%PDF-1.4 a"), f2.write_bytes(b"%PDF-1.4 bb")
        kw = dict(
            source_id="runtime:s.py#fig",
            origin=figcapture.ORIGIN_EXECUTION,
            receipt_id="sha256:r1",
            receipt_identity="sha256:pub1",
        )
        a = figcapture.source_artifact_from_file(f1, **kw)
        b = figcapture.source_artifact_from_file(f2, **kw)
        assert a.bytes_sha256 != b.bytes_sha256
        assert a.semantic_identity() == b.semantic_identity()  # 同一张图的同一版
        same_public_other_instance = figcapture.source_artifact_from_file(
            f1, **{**kw, "receipt_id": "sha256:r2", "generation": 2}
        )
        assert same_public_other_instance.semantic_identity() == a.semantic_identity()
        other_public = figcapture.source_artifact_from_file(
            f1, **{**kw, "receipt_identity": "sha256:pub2"}
        )
        assert other_public.semantic_identity() != a.semantic_identity()
        with pytest.raises(ValueError, match="receipt_identity"):
            figcapture.source_artifact_from_file(
                f1, source_id="runtime:s.py#fig", origin="execution", receipt_id="sha256:r1"
            )

    def test_zero_byte_file_is_not_an_artifact(self, tmp_path):
        f = tmp_path / "empty.pdf"
        f.write_bytes(b"")
        with pytest.raises(ValueError, match="零字节"):
            figcapture.source_artifact_from_file(f, source_id="empty.pdf", origin="static")


# ===========================================================================
# RenderPlan 引用：规范化请求 + 资源身份，不做渲染
# ===========================================================================
class TestRenderPlanRef:
    def _req(self, **over):
        spec = {
            "scope": "original",
            "formats": ["pdf", "png"],
            "filename": "Fig1",
            "ppi": 300,
            "original": {"figure_id": "Fig1.pdf", "source_kind": "figure"},
        }
        spec.update(over)
        return exportreq.normalize(spec)

    def _res(self, receipt_id="sha256:r1", sha="a" * 64, receipt_identity="sha256:pub1"):
        return {
            "source_id": "runtime:s.py#Fig1",
            "origin": "execution",
            "kind": "pdf",
            "bytes_sha256": sha,
            "receipt_id": receipt_id,
            "receipt_identity": receipt_identity,
            "patch_hash": "sha256:p",
        }

    def test_identity_follows_render_semantics_not_delivery_details(self):
        a = exportreq.render_plan_ref(self._req(), [self._res()])
        b = exportreq.render_plan_ref(
            self._req(filename="Other", overwrite="replace"), [self._res()]
        )
        assert a["plan_identity"] == b["plan_identity"]
        c = exportreq.render_plan_ref(self._req(ppi=600), [self._res()])
        assert c["plan_identity"] != a["plan_identity"]

    def test_identity_follows_the_resource_receipt_but_not_its_bytes(self):
        a = exportreq.render_plan_ref(self._req(), [self._res()])
        same_receipt_other_bytes = exportreq.render_plan_ref(self._req(), [self._res(sha="b" * 64)])
        assert a["plan_identity"] == same_receipt_other_bytes["plan_identity"]
        assert a["input_bytes"] != same_receipt_other_bytes["input_bytes"]
        # 换一个实例（receipt_id / generation 变、公开身份不变）：计划身份不变（Codex #451 P2）
        other_instance = exportreq.render_plan_ref(self._req(), [self._res(receipt_id="sha256:r2")])
        assert other_instance["plan_identity"] == a["plan_identity"]
        assert other_instance["resources"][0]["receipt_id"] == "sha256:r2"  # 实例元数据仍在
        other_public = exportreq.render_plan_ref(
            self._req(), [self._res(receipt_identity="sha256:pub2")]
        )
        assert other_public["plan_identity"] != a["plan_identity"]

    def test_resources_must_be_real_artifact_payloads(self):
        with pytest.raises(ValueError, match="bytes_sha256"):
            exportreq.render_plan_ref(
                self._req(), [{"source_id": "x", "origin": "static", "kind": "pdf"}]
            )
        with pytest.raises(ValueError, match="resources"):
            exportreq.render_plan_ref(self._req(), "nope")  # type: ignore[arg-type]


# ===========================================================================
# 纯模型不拉科学栈 / native 对象
# ===========================================================================
PURE_MODULES = (
    "tavotto.engine.execspec",
    "tavotto.engine.workdir",
    "tavotto.engine.depresolve",
    "tavotto.engine.figcapture",
    "tavotto.engine.receipt",
    "tavotto.engine.exportreq",
    "tavotto.engine.preparation",
)


def test_pure_models_import_without_the_scientific_stack_or_the_pdf_library():
    """另起一个解释器 import 全部纯模型，然后看 `sys.modules`：matplotlib / numpy /
    pymupdf / fitz 一个都不许出现。在本进程里判是空门禁——pytest 早就 import 过它们。"""
    code = (
        "import sys, json\n"
        f"for m in {PURE_MODULES!r}:\n"
        "    __import__(m)\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in "
        "{'matplotlib', 'numpy', 'pymupdf', 'fitz', 'PIL', 'scipy', 'pandas'})\n"
        "print(json.dumps(bad))\n"
    )
    # 继承环境而不是给空 env：`pool` 在 import 时就算 `config.data_dir()`，Windows 上
    # `Path.home()` 要 USERPROFILE，空 env 会在这一步 RuntimeError（#451 windows 片 1）。
    # 数据目录仍由 conftest 的 TAVOTTO_DATA_DIR 隔离（随环境继承进去）。
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "TAVOTTO_NO_TELEMETRY": "1"},
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == []
