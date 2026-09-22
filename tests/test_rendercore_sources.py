"""执行侧源解析器与 native 回执（统一实施包 U08，ADR 0067；纯模型，任何机器都跑）。

* `ExecutionSourceResolver`：带 override / runtime 素材的面板交给 `execute`，其余交给静态解析器（磁盘原件不跑
  脚本，RC-019）；`execute` 交回的不是 `origin=execution` 的产物就拒（RC-017 must_fail：拿 materialized cache 冒充）。
* `receipt.from_native_session`：native 会话没有 spec 账本，回执按描述符元数据重建；argv 只进数量不进值
  （ADR 0021 §4），`source_revision` 空串（不猜「当时」的脚本）。
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from tavotto.engine import execspec, figcapture, receipt
from tavotto.rendercore import sources

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "foundation" / "pdf_png_assets"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    shutil.copy(FIXTURE / "page.pdf", root / "Fig1.pdf")
    return root


def _executed(path: Path, source_id: str) -> sources.FrozenSource:
    data = path.read_bytes()
    art = figcapture.SourceArtifact(
        source_id=source_id,
        origin=figcapture.ORIGIN_EXECUTION,
        kind="pdf",
        bytes_sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        receipt_id="sha256:" + "1" * 64,
        generation=1,
        patch_hash="sha1:abc",
        receipt_identity="sha256:" + "2" * 64,
    )
    return sources.FrozenSource(artifact=art, path=path)


def test_static_panels_go_to_the_static_resolver_and_overrides_go_to_execute(project):
    calls: list[str] = []

    def execute(obj: dict) -> sources.FrozenSource:
        calls.append(str(obj["id"]))
        return _executed(project / "Fig1.pdf", str(obj["id"]))

    r = sources.ExecutionSourceResolver(
        static=sources.StaticSourceResolver(project), execute=execute
    )
    plain = r.resolve({"type": "panel", "id": "Fig1.pdf"})
    assert plain.artifact.origin == "static" and calls == []  # 磁盘原件不跑脚本（RC-019）
    over = r.resolve({"type": "panel", "id": "Fig1.pdf", "overrides": [{"gid": "a", "prop": "p"}]})
    assert over.artifact.origin == "execution" and calls == ["Fig1.pdf"]
    rt = r.resolve({"type": "panel", "id": "runtime:fig1.py#Fig1"})
    assert rt.artifact.origin == "execution" and calls == ["Fig1.pdf", "runtime:fig1.py#Fig1"]


def test_execute_must_return_an_execution_artifact_not_a_disguised_static_one(project):
    """RC-017：`execute` 回调把磁盘原件（static）当执行产物交回来——拒，不放行。"""

    def sneaky(obj: dict) -> sources.FrozenSource:
        return sources.StaticSourceResolver(project).resolve({"id": "Fig1.pdf"})

    r = sources.ExecutionSourceResolver(
        static=sources.StaticSourceResolver(project), execute=sneaky
    )
    with pytest.raises(sources.SourceError) as exc:
        r.resolve({"type": "panel", "id": "Fig1.pdf", "overrides": [{"gid": "a", "prop": "p"}]})
    assert exc.value.code == "source_needs_execution"


class _Session:
    """`nativesession.NativeSession` 的最小形状（回执只读这些属性）。"""

    target_kind = execspec.TARGET_SCRIPT
    interpreter = "/opt/venv/bin/python"
    cwd = "/data/run"
    arg_count = 2
    generation = 3
    descriptors = [{"stem": "Fig1"}]
    last_build_runtime = {"python_version": "3.12.1", "packages": {"matplotlib": "3.9"}}

    def __init__(self, root: str) -> None:
        self.project_root = root


def test_native_receipts_record_argv_count_not_values_and_do_not_guess_the_revision(tmp_path):
    session = _Session(str(tmp_path))
    rcpt = receipt.from_native_session(session, "fig1.py")
    assert rcpt.profile == execspec.PROFILE_NATIVE
    assert rcpt.control_plane == receipt.CONTROL_PLANE_NATIVE and rcpt.generation == 3
    assert rcpt.spec_stable["argv"] == [receipt.NATIVE_ARGV_PLACEHOLDER] * 2
    assert rcpt.spec_stable["target"] == "fig1.py" and rcpt.source_revision == ""
    assert rcpt.completeness == receipt.COMPLETENESS_COMPLETE
    assert rcpt.launch_context["profile"] == "native"
    public = rcpt.public_identity()
    assert "/opt/venv" not in public and "/data/run" not in public
    # 同一会话再来一次身份不变；换 argv 数量身份就变（数量进身份，值不进）
    assert receipt.from_native_session(session, "fig1.py").public_identity() == public
    other = _Session(str(tmp_path))
    other.arg_count = 3
    assert receipt.from_native_session(other, "fig1.py").public_identity() != public
    # 没自报 runtime 的会话是 partial，不补不猜
    bare = _Session(str(tmp_path))
    bare.last_build_runtime = None
    assert receipt.from_native_session(bare, "fig1.py").completeness == receipt.COMPLETENESS_PARTIAL
    art = receipt.source_artifact_for(
        rcpt, FIXTURE / "page.pdf", source_id="runtime:fig1.py#Fig1", patch_hash="sha1:x"
    )
    assert art.origin == "execution" and art.receipt_identity == public
