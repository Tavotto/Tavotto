"""U02 render_spike · 最小应用 render child 的控制流看护（`scripts/dev/u02_spikes/render_child.py`）。

主语分两层，别混：

* **客户端的控制流**（串行 / 超时 → kill → 重启 / 崩溃 → 重启 / 像素预算父侧先拒 / close）——
  用一个**假 child**（纯标准库、说同一套行协议、能按指令挂起或自杀）验证，任何机器都跑。
  它证明的是「父进程这一侧的纪律」，不证明 PDFium 本身。
* **真 child**（pypdfium2 在子进程主线程里渲染 evidence 里的 spike.pdf）——候选包不在时 **skip
  并写明理由**（skip 是 not_run，不是绿）；在 spike venv / `foundation-u02-spikes.yml` 里真跑。

这是 spike 的看护，不是产品用例：产品里的 render child 由 U07 在 `engine/` 里落地时另写。
"""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from dev.u02_spikes import render_child as rc  # noqa: E402

EVIDENCE = ROOT / "docs" / "implementation" / "tavotto-foundation" / "evidence" / "u02" / "render"
SPIKE_PDF = EVIDENCE / "spike.pdf"

HAS_PDFIUM = importlib.util.find_spec("pypdfium2") is not None

#: 假 child：同一套行协议；`hang` 睡到被杀，`crash` 直接 _exit，`render` 写一个 1×1 PNG。
FAKE_CHILD = r"""
import json, os, struct, sys, time, zlib
seq = 0
def png1():
    raw = b"\x00" + bytes([0, 0, 0, 255])
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
out = sys.stdout.buffer
for raw in sys.stdin.buffer:
    req = json.loads(raw)
    seq += 1
    op = req["op"]
    if op == "ping":
        resp = {"id": req["id"], "ok": True, "seq": seq, "pid": os.getpid()}
    elif op == "close":
        out.write((json.dumps({"id": req["id"], "ok": True, "seq": seq}) + "\n").encode()); out.flush()
        break
    elif op == "render":
        if req["pdf"].endswith("chatty.pdf"):
            # 应答前吐 256 KiB 的「日志」：父进程不并发排空的话，这里就会堵在 64 KiB 的管道上
            for _ in range(256):
                out.write(json.dumps({"id": None, "log": "x" * 1000}).encode() + b"\n")
            out.flush()
        if req["width_px"] * req["width_px"] > req["max_pixels"]:
            resp = {"id": req["id"], "ok": False, "seq": seq, "error": {"code": "pixel_budget_exceeded", "message": "child side"}}
        else:
            if req["pdf"].endswith("hang.pdf"):
                time.sleep(30)
            if req["pdf"].endswith("crash.pdf"):
                os._exit(3)
            with open(req["out"], "wb") as fh:
                fh.write(png1())
            time.sleep(0.02)
            resp = {"id": req["id"], "ok": True, "seq": seq, "width": 1, "height": 1, "ms": 0}
    else:
        resp = {"id": req["id"], "ok": False, "seq": seq, "error": {"code": "bad_request", "message": op}}
    out.write((json.dumps(resp) + "\n").encode()); out.flush()
"""


@pytest.fixture
def fake_client(tmp_path):
    script = tmp_path / "fake_child.py"
    script.write_text(FAKE_CHILD, encoding="utf-8")
    client = rc.RenderChildClient(
        [sys.executable, str(script)], max_pixels=10_000, default_timeout=5
    )
    yield client
    client.close()


def _read_png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data[12:16] == b"IHDR"
    w, h = struct.unpack(">II", data[16:24])
    return w, h


# ---------------------------------------------------------------------------
# 客户端控制流（假 child）
# ---------------------------------------------------------------------------
def test_requests_are_serialized_and_each_thread_gets_its_own_answer(fake_client, tmp_path):
    """8 个线程各发 5 次 render：每个响应的 id 对得上（无串包），child 的 seq 严格递增且
    恰好等于总请求数（所有请求都经同一个 child 顺序处理，没有第二个 child）。"""
    seqs: list[int] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker(k: int) -> None:
        try:
            for j in range(5):
                out = tmp_path / f"t{k}_{j}.png"
                resp = fake_client.render(tmp_path / "ok.pdf", out, 10, page_size_pt=(100, 100))
                assert resp["ok"] and _read_png_size(out) == (1, 1)
                with lock:
                    seqs.append(resp["seq"])
        except BaseException as exc:  # noqa: BLE001 - 线程里的任何失败都要浮到主线程
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert sorted(seqs) == list(range(1, 41)), sorted(seqs)
    assert fake_client.restarts == 0


def test_timeout_kills_the_child_and_the_next_request_recovers(fake_client, tmp_path):
    """挂住的请求在 deadline 到点时报 `render_child_timeout`，child 被 kill + reap（pid 不再存活），
    下一次请求自动起一个新 child 并成功。"""
    pid_before = fake_client.ping()["pid"]
    t0 = time.monotonic()
    with pytest.raises(rc.RenderChildError) as ei:
        fake_client.render(
            tmp_path / "hang.pdf", tmp_path / "x.png", 10, page_size_pt=(1, 1), timeout=0.5
        )
    assert ei.value.code == "render_child_timeout"
    assert time.monotonic() - t0 < 5, "deadline 没生效：等了远超 timeout 的时间"
    assert fake_client.pid is None, "超时后 child 必须已经被 kill 并 reap"
    assert fake_client.last_exit is not None, "kill 之后没有 wait()：退出码没收回来 = 没 reap"
    _assert_not_alive(pid_before)
    resp = fake_client.ping()
    assert resp["pid"] != pid_before and resp["seq"] == 1
    assert fake_client.restarts == 1


def test_child_crash_reports_died_and_the_next_request_recovers(fake_client, tmp_path):
    pid_before = fake_client.ping()["pid"]
    with pytest.raises(rc.RenderChildError) as ei:
        fake_client.render(tmp_path / "crash.pdf", tmp_path / "x.png", 10, page_size_pt=(1, 1))
    assert ei.value.code == "render_child_died"
    assert fake_client.last_exit == 3, "崩溃的退出码要经 wait() 收回（reap），不是猜"
    _assert_not_alive(pid_before)
    out = tmp_path / "after.png"
    resp = fake_client.render(tmp_path / "ok.pdf", out, 10, page_size_pt=(1, 1))
    assert resp["ok"] and resp["seq"] == 1 and _read_png_size(out) == (1, 1)
    assert fake_client.restarts == 1


def test_external_kill_is_recovered_within_one_request(fake_client, tmp_path):
    """被外力（信号）在两次请求之间杀掉的 child：下一次请求要么在发出前就被 `_ensure()` 发现
    （`poll()` 已非 None → reap + 重启，调用方看到成功），要么信号还没落地、请求发出后读到 EOF
    （报 `render_child_died`），再下一次必成功。两条路都合法，合同是**最多失败一次、随后恢复、
    旧 pid 被 reap、restarts 恰为 1**——判据不赌信号落地的时序。"""
    pid = fake_client.ping()["pid"]
    os.kill(pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
    failures = 0
    try:
        resp = fake_client.ping()
    except rc.RenderChildError as exc:
        assert exc.code == "render_child_died"
        failures = 1
        resp = fake_client.ping()
    assert resp["pid"] != pid and resp["seq"] == 1
    assert fake_client.restarts == 1 and failures <= 1
    _assert_not_alive(pid)


def test_pixel_budget_is_rejected_by_the_parent_before_any_child_call(fake_client, tmp_path):
    """父侧先拒：预算超了根本不起 child（pid 仍为 None）；child 侧的第二道用 width² 绕过父侧的
    精确判据来触发（父侧按 page_size_pt 算出的高很小、child 按自己的规则算超）。"""
    with pytest.raises(rc.RenderChildError) as ei:
        fake_client.render(tmp_path / "ok.pdf", tmp_path / "x.png", 200, page_size_pt=(100, 100))
    assert ei.value.code == "pixel_budget_exceeded"
    assert fake_client.pid is None, "父侧就该拒掉，不该起 child"
    with pytest.raises(rc.RenderChildError) as ei:
        fake_client.render(tmp_path / "ok.pdf", tmp_path / "x.png", 200, page_size_pt=(1000, 1))
    assert ei.value.code == "pixel_budget_exceeded" and ei.value.message == "child side"
    assert fake_client.pid is not None, "这一条是 child 侧拒的，child 活着"


def test_reader_drains_more_than_a_pipe_buffer_while_the_child_runs(fake_client, tmp_path):
    """`tests/test_source_hygiene.py` 对 scripts/ 里的 `stdout=PIPE` 一律说不，只给本客户端一条例外——
    例外的前提是「读线程在子进程运行期间持续排空」。这里让假 child 在应答前吐 256 KiB（远超
    64 KiB 的管道缓冲）：不并发排空，child 堵在写上，父进程等不到应答，这条就超时红。"""
    out = tmp_path / "chatty.png"
    resp = fake_client.render(tmp_path / "chatty.pdf", out, 10, page_size_pt=(1, 1), timeout=10)
    assert resp["ok"] and _read_png_size(out) == (1, 1)


def test_close_lets_the_child_exit_cleanly_and_reaps_it(fake_client):
    pid = fake_client.ping()["pid"]
    fake_client.close()
    assert fake_client.pid is None
    _assert_not_alive(pid)


def test_error_codes_are_a_closed_set():
    with pytest.raises(AssertionError):
        rc.RenderChildError("something_else", "x")


def _assert_not_alive(pid: int) -> None:
    if os.name == "nt":
        for _ in range(50):
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if str(pid) not in out.stdout:
                return
            time.sleep(0.05)
        raise AssertionError(f"pid {pid} 仍在 tasklist 里（没 reap 或没 kill）")
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:  # pragma: no cover - 别人的进程；不该发生
            raise
        time.sleep(0.05)
    raise AssertionError(f"pid {pid} 仍存活（没 reap 或没 kill）")


# ---------------------------------------------------------------------------
# 真 child（需要 pypdfium2；没有就 skip 并写明）
# ---------------------------------------------------------------------------
pytestmark_real = pytest.mark.skipif(
    not HAS_PDFIUM,
    reason="本解释器里没有 pypdfium2（候选包只装在 U02 的 spike venv 里）——这里是 not_run，不是绿",
)


@pytestmark_real
def test_real_child_renders_the_evidence_pdf_to_the_requested_width(tmp_path):
    env = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join([str(ROOT / "scripts"), os.environ.get("PYTHONPATH", "")]),
    )
    client = rc.RenderChildClient(
        [sys.executable, "-m", "dev.u02_spikes.render_child"], env=env, default_timeout=60
    )
    try:
        info = client.ping()
        assert info["memory_limit"] in (
            "set",
            "set-but-not-enforced-by-kernel",
            "unsupported",
        ) or info["memory_limit"].startswith("failed")
        out = tmp_path / "spike.png"
        resp = client.render(SPIKE_PDF, out, 400, page_size_pt=(400, 320))
        assert (resp["width"], resp["height"]) == (400, 320)
        assert _read_png_size(out) == (400, 320)
        # 像素预算 child 侧：绕过父侧精确判据（给错的 page_size_pt），child 打开页面后按真实高度拒
        client.max_pixels = 400 * 320 - 1
        with pytest.raises(rc.RenderChildError) as ei:
            client.render(SPIKE_PDF, tmp_path / "too_big.png", 400, page_size_pt=(400, 1))
        assert ei.value.code == "pixel_budget_exceeded"
        assert not (tmp_path / "too_big.png").exists()
        # 坏输入：结构化错误而不是 child 死掉
        bad = tmp_path / "bad.pdf"
        bad.write_bytes(b"%PDF-1.4 not really")
        client.max_pixels = rc.DEFAULT_MAX_PIXELS
        with pytest.raises(rc.RenderChildError) as ei:
            client.render(bad, tmp_path / "bad.png", 100, page_size_pt=(100, 100))
        assert ei.value.code == "render_failed"
        assert client.pid is not None, "坏输入不该杀 child"
        # 极短超时 → kill → 下一次恢复（真 child、真 kill、真 reap）
        pid = client.pid
        with pytest.raises(rc.RenderChildError) as ei:
            client.render(
                SPIKE_PDF, tmp_path / "t.png", 3000, page_size_pt=(400, 320), timeout=0.0001
            )
        assert ei.value.code == "render_child_timeout"
        _assert_not_alive(pid)
        resp = client.render(SPIKE_PDF, out, 200, page_size_pt=(400, 320))
        assert (resp["width"], resp["height"]) == (200, 160) and client.restarts == 1
    finally:
        client.close()


def test_fixture_evidence_pdf_is_present_and_small():
    """evidence 里的 spike.pdf 是真 child 用例的输入；它必须在、且是 KB 级（进 git 的理由）。"""
    assert SPIKE_PDF.is_file()
    assert SPIKE_PDF.stat().st_size < 200_000
    report = json.loads((EVIDENCE / "report.json").read_text(encoding="utf-8"))
    assert report["all_ok"] is True
    assert report["outputs"]["spike.pdf"]["sha256"] == _sha256(SPIKE_PDF), (
        "evidence 里的 spike.pdf 与 report.json 记的 hash 不同：两者要一起重生成"
    )


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()
