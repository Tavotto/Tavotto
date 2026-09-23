"""执行侧源解析器与 native 回执（统一实施包 U08，ADR 0067；纯模型，任何机器都跑）。

* `ExecutionSourceResolver`：带 override / runtime 素材的面板交给 `execute`，其余交给静态解析器（磁盘原件不跑
  脚本，RC-019）；`execute` 交回的不是 `origin=execution` 的产物就拒（RC-017 must_fail：拿 materialized cache 冒充）。
* `receipt.from_native_session`：native 会话没有 spec 账本，回执按描述符元数据重建；argv 只进数量不进值
  （ADR 0021 §4），`source_revision` 空串（不猜「当时」的脚本）。
"""

from __future__ import annotations

import hashlib
import os
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
    #: 握手帧里用户进程自报的 pid（`NativeSession.child_pid`）；自报的 `pid` 必须是它（ADR 0070）
    child_pid = 777
    last_build_runtime = {
        "report_origin": "build",
        "pid": 777,
        "python_version": "3.12.1",
        "packages": {"matplotlib": "3.9"},
    }

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


# ================================================================ FingerprintMemo（派生值按文件指纹复用）


@pytest.fixture
def settled(monkeypatch):
    """用例里的文件都是刚写出来的：把「刚改过不记」的窗口关掉，量指纹本身；窗口另有一条用例。"""
    monkeypatch.setattr(sources, "RACY_WINDOW_NS", 0)


def _bump_mtime(p: Path) -> None:
    """同长改写之后把 mtime 显式**往回**拨 1 秒：不让用例依赖「两次写入恰好落在不同的时间戳 tick」（Linux 按 tick）；
    往前拨会落进「刚改过 / 来自未来」的窗口，值就不会被记下，量不到复用。"""
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns - 1_000_000_000))


def test_the_memo_reuses_a_value_while_the_file_is_untouched_and_recomputes_after_a_change(
    tmp_path, settled
):
    p = tmp_path / "a.bin"
    p.write_bytes(b"aaaa")
    memo = sources.FingerprintMemo()
    calls = []

    def compute():
        calls.append(1)
        return p.read_bytes()

    assert memo.get_or_compute(p, compute) == b"aaaa"
    assert memo.get_or_compute(p, compute) == b"aaaa" and len(calls) == 1 and memo.hits == 1
    p.write_bytes(b"bbbb")  # 同长改写
    _bump_mtime(p)
    assert memo.get_or_compute(p, compute) == b"bbbb" and len(calls) == 2
    assert (
        memo.get_or_compute(p, compute) == b"bbbb" and len(calls) == 2
    )  # 改写之后的新值同样被记下
    p.write_bytes(b"cc")  # 长度变了（mtime 不必变）
    assert memo.get_or_compute(p, compute) == b"cc" and len(calls) == 3
    q = tmp_path / "q.bin"
    q.write_bytes(b"dd")
    os.replace(q, p)  # 换成另一个文件（inode 变了）
    assert memo.get_or_compute(p, compute) == b"dd" and len(calls) == 4


def test_the_memo_keys_values_by_kind_and_bounds_its_table(tmp_path, settled):
    files = [tmp_path / f"{i}.bin" for i in range(3)]
    for f in files:
        f.write_bytes(f.name.encode())
    memo = sources.FingerprintMemo(maxsize=2)
    assert memo.get_or_compute(files[0], lambda: "pdf", key="pdf") == "pdf"
    assert memo.get_or_compute(files[0], lambda: "raster", key="raster") == "raster"
    assert memo.get_or_compute(files[0], lambda: "x", key="pdf") == "pdf"  # 同文件不同 key 互不串
    memo.get_or_compute(files[1], lambda: 1)
    memo.get_or_compute(files[2], lambda: 2)  # 表满：丢最久没用的
    assert len(memo._table) == 2


def test_a_value_computed_while_the_file_changed_or_a_failure_is_not_remembered(tmp_path, settled):
    p = tmp_path / "a.bin"
    p.write_bytes(b"aaaa")
    memo = sources.FingerprintMemo()

    def compute_while_rewriting():
        value = p.read_bytes()
        p.write_bytes(b"bbbbbb")  # 算的过程中被改写：旧值不许挂到新指纹上
        return value

    assert memo.get_or_compute(p, compute_while_rewriting) == b"aaaa"
    assert memo.get_or_compute(p, lambda: p.read_bytes()) == b"bbbbbb"

    def boom():
        raise OSError("读不了")

    q = tmp_path / "q.bin"
    q.write_bytes(b"q")
    with pytest.raises(OSError):
        memo.get_or_compute(q, boom)
    assert memo.get_or_compute(q, lambda: "ok") == "ok"  # 失败不缓存
    assert (
        memo.get_or_compute(tmp_path / "missing.bin", lambda: "m") == "m"
    )  # stat 不了：照算、不记
    assert memo.get_or_compute(tmp_path / "missing.bin", lambda: "n") == "n"


def test_a_file_changed_within_the_timestamp_granularity_window_is_not_remembered(
    tmp_path, monkeypatch
):
    """git 的「racy clean」：时间戳是粗粒度的（Linux 按 tick、Windows ~16 ms），刚写完的文件再被等长改写可以
    得到同一个指纹。最后一次改动离现在不到 `RACY_WINDOW_NS` 的，一律不记；过了窗口才记。"""
    import types

    p = tmp_path / "a.bin"
    p.write_bytes(b"aaaa")
    memo = sources.FingerprintMemo()
    calls = []
    memo.get_or_compute(p, lambda: calls.append(1) or "v")
    memo.get_or_compute(p, lambda: calls.append(1) or "v")
    assert len(calls) == 2, "刚写出的文件不许被复用"
    real = sources.time.time_ns
    later = types.SimpleNamespace(time_ns=lambda: real() + sources.RACY_WINDOW_NS + 1)
    monkeypatch.setattr(sources, "time", later)
    memo.get_or_compute(p, lambda: calls.append(1) or "v")
    memo.get_or_compute(p, lambda: calls.append(1) or "v")
    assert len(calls) == 3, "过了窗口：第一次算完就记下，第二次复用"
