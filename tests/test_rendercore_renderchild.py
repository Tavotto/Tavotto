"""render child 与 RenderHost（统一实施包 U07，ADR 0066）——U02 的 `test_foundation_u02_render_child.py` 收编。

两层：

* **假 child**（任何机器，主仓库 .venv 就能跑）：同一套行协议的小脚本，验父进程的控制流——串行 + 无串包、
  有界队列背压、超时 kill + reap + 下一次恢复、崩溃 / 外杀恢复、父侧像素预算、close、256 KiB 的 chatty
  输出被并发排空（`tests/test_source_hygiene.py` 对 `stdout=PIPE` 的动态前提）。
* **真 child**（pypdfium2 在子进程主线程里渲染 U00 夹具 / U06 evidence）：候选包不在时 **skip 并写明理由**
  （skip 是 not_run，不是绿）；在 rc-venv / `foundation-u06-rendercore.yml` 里真跑——probe 含 /UserUnit、
  render 的尺寸规则与 straight alpha、child 侧预算、坏 PDF 结构化错误且 child 不死、超时 → kill → reap → 恢复、
  并发 preview / probe / inspect 全部经同一个 child 顺序处理（RC-047）。
"""

from __future__ import annotations

import importlib.util
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from tavotto.rendercore import renderchild as rc, renderhost as rh

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "foundation" / "pdf_png_assets"
U06_PDF = ROOT / "docs" / "implementation" / "tavotto-foundation" / "evidence" / "u06" / "u06.pdf"

HAS_PDFIUM = importlib.util.find_spec("pypdfium2") is not None
HAS_PIKEPDF = importlib.util.find_spec("pikepdf") is not None

#: 假 child：同一套行协议；`hang` 睡到被杀，`crash` 直接 _exit，`render` 写一个 2×1 RGBA 原样文件。
FAKE_CHILD = r"""
import json, os, sys, time
seq = 0
out = sys.stdout.buffer
for raw in sys.stdin.buffer:
    req = json.loads(raw)
    seq += 1
    op = req["op"]
    if op == "ping":
        resp = {"id": req["id"], "ok": True, "seq": seq, "pid": os.getpid(), "pdfium": "fake"}
    elif op == "close":
        out.write((json.dumps({"id": req["id"], "ok": True, "seq": seq}) + "\n").encode()); out.flush()
        break
    elif op == "probe":
        resp = {"id": req["id"], "ok": True, "seq": seq, "pages": 1, "page": 0, "width_pt": 200.0, "height_pt": 100.0,
                "raw_width_pt": 200.0, "raw_height_pt": 100.0, "user_unit": 1.0, "rotation": 0,
                "media_box": [0, 0, 200, 100], "crop_box": [0, 0, 200, 100]}
    elif op == "render":
        if req["pdf"].endswith("chatty.pdf"):
            # 应答前吐 256 KiB 的「日志」：父进程不并发排空的话，这里就会堵在 64 KiB 的管道上
            for _ in range(256):
                out.write(json.dumps({"id": None, "log": "x" * 1000}).encode() + b"\n")
            out.flush()
        w = int(req.get("width_px") or 2)
        if w * w > req["max_pixels"]:
            resp = {"id": req["id"], "ok": False, "seq": seq, "error": {"code": "pixel_budget_exceeded", "message": "child side"}}
        else:
            if req["pdf"].endswith("hangpart.pdf"):
                with open(req["out"] + ".part", "wb") as fh:  # 写到一半被杀：留下 .part
                    fh.write(b"\x00" * 16)
                time.sleep(30)
            if req["pdf"].endswith("hang.pdf"):
                time.sleep(30)
            if req["pdf"].endswith("crash.pdf"):
                os._exit(3)
            if req["pdf"].endswith("slow.pdf"):
                time.sleep(0.3)
            if req["pdf"].endswith("slower.pdf"):
                time.sleep(1.0)
            data = bytes([255, 0, 0, 255, 0, 0, 255, 128])
            with open(req["out"], "wb") as fh:
                fh.write(data)
            nbytes = 999 if req["pdf"].endswith("shortbytes.pdf") else 8  # 说的与写的对不上
            resp = {"id": req["id"], "ok": True, "seq": seq, "width": 2, "height": 1, "stride": 8, "channels": 4, "bytes": nbytes, "ms": 0}
    else:
        resp = {"id": req["id"], "ok": False, "seq": seq, "error": {"code": "bad_request", "message": op}}
    out.write((json.dumps(resp) + "\n").encode()); out.flush()
"""


@pytest.fixture
def fake_host(tmp_path):
    script = tmp_path / "fake_child.py"
    script.write_text(FAKE_CHILD, encoding="utf-8")
    host = rh.RenderHost(
        [sys.executable, str(script)],
        max_pixels=10_000,
        default_timeout=5,
        max_waiting=4,
        scratch_dir=tmp_path,
    )
    yield host
    host.close()


def _assert_not_alive(pid: int) -> None:
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except OSError:
            return
        # 已 reap 的 pid 在 POSIX 上 kill(pid, 0) 会 ESRCH；Windows 上 OpenProcess 失败同样抛
        time.sleep(0.05)
    raise AssertionError(f"pid {pid} 仍然活着（没 kill 或没 reap）")


# ---------------------------------------------------------------------------
# 假 child：父进程控制流
# ---------------------------------------------------------------------------
def test_requests_are_serialized_and_each_thread_gets_its_own_answer(fake_host, tmp_path):
    """8 个线程各发 5 次 render：每个响应的 id 对得上（无串包），child 的 seq 严格递增且恰好等于总请求数
    （所有请求都经同一个 child 顺序处理，没有第二个 child）。"""
    fake_host.max_waiting = 64
    fake_host._slots = threading.BoundedSemaphore(65)
    pdf = tmp_path / "ok.pdf"
    pdf.write_bytes(b"%PDF-")
    seqs: list[int] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def work() -> None:
        try:
            for _ in range(5):
                buf = fake_host.render(pdf, width_px=2)
                assert (buf.width, buf.height, buf.channels) == (2, 1, 4)
                assert buf.pixel(1, 0) == (0, 0, 255, 128)
                resp = fake_host.ping()
                with lock:
                    seqs.append(resp["seq"])
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert fake_host.starts == 1 and fake_host.pid is not None
    assert len(seqs) == 40 and sorted(seqs) == seqs  # ping 的 seq 递增：只有一个 child、一条队


def test_a_bounded_queue_pushes_back_instead_of_piling_up(fake_host, tmp_path):
    """max_waiting=4：一个慢请求占着锁，再来 4 个在等，第 6 个立刻 `render_queue_full`（不排队、不起第二个 child）。"""
    slow = tmp_path / "slow.pdf"
    slow.write_bytes(b"%PDF-")
    started = threading.Barrier(6)
    results: list[str] = []
    lock = threading.Lock()

    def work(i: int) -> None:
        started.wait()
        time.sleep(0.02 * i)  # 错开一点，让第一个先拿到锁
        try:
            fake_host.render(slow, width_px=2)
            outcome = "ok"
        except rh.RenderChildError as exc:
            outcome = exc.code
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("render_queue_full") >= 1, results
    assert results.count("ok") == 6 - results.count("render_queue_full")
    assert fake_host.starts == 1


def test_the_timeout_covers_waiting_for_the_lock_and_does_not_kill_a_busy_child(
    fake_host, tmp_path
):
    """Codex #471 P2：一个 deadline 管到底。A 拿着锁渲一个 1 s 的慢请求；B 带 0.05 s 超时进来，必须在 A 做完
    **之前**拿到 `render_child_timeout`（不是等 A 做完才开始计时），而且 **child 不被打断**——A 照常成功、没有重启。
    判据的主语是「B 回来时 A 还没完」（`"a" not in results`）+ 一个余量 0.5 s 的上界：第一版写的是 0.3 s 慢请求
    + `elapsed < 0.25`，Windows runner 上 `Lock.acquire(timeout=0.05)` 回来花了 0.28 s，贴边的定时判据量的是
    调度抖动，不是被测行为（2026-09-21 #471 run 35590586039 windows 腿）。"""
    slow = tmp_path / "slower.pdf"
    slow.write_bytes(b"%PDF-")
    ok = tmp_path / "ok.pdf"
    ok.write_bytes(b"%PDF-")
    fake_host.ping()
    pid = fake_host.pid
    results: dict[str, object] = {}
    started = threading.Event()

    def slow_work() -> None:
        started.set()
        results["a"] = fake_host.render(slow, width_px=2)

    t = threading.Thread(target=slow_work)
    t.start()
    started.wait()
    time.sleep(0.05)  # 让 A 先拿到锁
    t0 = time.monotonic()
    with pytest.raises(rh.RenderChildError) as ei:
        fake_host.render(ok, width_px=2, timeout=0.05)
    elapsed = time.monotonic() - t0
    a_done_when_b_returned = "a" in results
    assert ei.value.code == "render_child_timeout"
    assert not a_done_when_b_returned and elapsed < 0.5, (elapsed, a_done_when_b_returned)
    t.join()
    assert results["a"].width == 2  # A 没被打断
    assert fake_host.pid == pid and fake_host.restarts == 0  # child 没被 kill、没重启
    assert fake_host.render(ok, width_px=2).width == 2


def test_timeout_kills_the_child_and_the_next_request_recovers(fake_host, tmp_path):
    hang = tmp_path / "hang.pdf"
    hang.write_bytes(b"%PDF-")
    fake_host.ping()
    pid = fake_host.pid
    assert pid is not None
    with pytest.raises(rh.RenderChildError) as ei:
        fake_host.render(hang, width_px=2, timeout=0.5)
    assert ei.value.code == "render_child_timeout"
    _assert_not_alive(pid)
    assert fake_host.last_exit is not None, "kill 之后必须 wait() reap"
    assert fake_host.pid is None
    ok = tmp_path / "ok.pdf"
    ok.write_bytes(b"%PDF-")
    buf = fake_host.render(ok, width_px=2)
    assert buf.width == 2 and fake_host.restarts == 1 and fake_host.pid not in (None, pid)


def test_child_crash_reports_died_and_the_next_request_recovers(fake_host, tmp_path):
    crash = tmp_path / "crash.pdf"
    crash.write_bytes(b"%PDF-")
    with pytest.raises(rh.RenderChildError) as ei:
        fake_host.render(crash, width_px=2)
    assert ei.value.code == "render_child_died"
    assert fake_host.last_exit == 3
    assert fake_host.ping()["ok"] and fake_host.restarts == 1


def test_external_kill_is_recovered_within_one_request(fake_host):
    fake_host.ping()
    pid = fake_host.pid
    assert pid is not None
    os.kill(pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
    deadline = time.time() + 5
    while fake_host._proc is not None and fake_host._proc.poll() is None:
        assert time.time() < deadline
        time.sleep(0.02)
    resp = fake_host.ping()  # 请求前 poll() 发现已死 → reap → 重启
    assert resp["ok"] and fake_host.restarts == 1 and fake_host.pid != pid


def test_pixel_budget_is_rejected_by_the_parent_before_any_child_call(fake_host, tmp_path):
    pdf = tmp_path / "ok.pdf"
    pdf.write_bytes(b"%PDF-")
    with pytest.raises(rh.RenderChildError) as ei:
        fake_host.render(pdf, width_px=200, page_size_pt=(100, 100))  # 200×200 > 10 000
    assert ei.value.code == "pixel_budget_exceeded"
    with pytest.raises(rh.RenderChildError) as ei:
        fake_host.render(pdf, dpi=1000, page_size_pt=(100, 100))  # 1389² > 10 000
    assert ei.value.code == "pixel_budget_exceeded"
    assert fake_host.starts == 0, "父侧预算先拒，一个 child 都不该起"
    # 父侧粗判（没有 page_size_pt）过了、child 侧按真实尺寸拒：结构化错误，child 不死
    with pytest.raises(rh.RenderChildError) as ei:
        fake_host.render(pdf, width_px=150)  # 150² = 22 500 > 10 000（假 child 按宽平方判）
    assert ei.value.code == "pixel_budget_exceeded" and fake_host.pid is not None


def test_width_and_dpi_are_mutually_exclusive_and_positive(fake_host, tmp_path):
    pdf = tmp_path / "ok.pdf"
    pdf.write_bytes(b"%PDF-")
    for kwargs in ({}, {"width_px": 2, "dpi": 72}, {"width_px": 0}, {"dpi": -1}):
        with pytest.raises(rh.RenderChildError) as ei:
            fake_host.render(pdf, **kwargs)
        assert ei.value.code == "bad_request", kwargs


def test_reader_drains_more_than_a_pipe_buffer_while_the_child_runs(fake_host, tmp_path):
    """child 在应答前吐 256 KiB：读线程必须在 child 运行期间持续排空（`test_source_hygiene` 那条判据的动态前提）。"""
    chatty = tmp_path / "chatty.pdf"
    chatty.write_bytes(b"%PDF-")
    buf = fake_host.render(chatty, width_px=2, timeout=10)
    assert buf.width == 2


def test_close_lets_the_child_exit_cleanly_and_reaps_it(fake_host):
    fake_host.ping()
    pid = fake_host.pid
    fake_host.close()
    assert fake_host.last_exit == 0 and fake_host.pid is None
    _assert_not_alive(pid)


def test_the_pixel_file_is_removed_after_reading(fake_host, tmp_path):
    pdf = tmp_path / "ok.pdf"
    pdf.write_bytes(b"%PDF-")
    fake_host.render(pdf, width_px=2)
    assert not list(tmp_path.glob("render-*.rgba"))


def test_a_stale_part_file_is_removed_when_the_render_fails(fake_host, tmp_path):
    """Codex #471 P2：child 写到一半被 kill 留下的 `.part` 也要清，不然每次 mkstemp 新名字、失败的高分辨率
    渲染会攒成一堆孤儿。"""
    pdf = tmp_path / "hangpart.pdf"
    pdf.write_bytes(b"%PDF-")
    with pytest.raises(rh.RenderChildError) as ei:
        fake_host.render(pdf, width_px=2, timeout=0.5)
    assert ei.value.code == "render_child_timeout"
    assert not list(tmp_path.glob("render-*.rgba*")), list(tmp_path.glob("render-*"))


def test_a_pixel_file_that_disagrees_with_the_response_reaps_the_child(fake_host, tmp_path):
    """Codex #471 P2：child 说 999 字节、写了 8 字节——协议不可信，与 request() 里的 protocol 失败同一处置：
    kill + reap，下一次请求换一个 child。"""
    pdf = tmp_path / "shortbytes.pdf"
    pdf.write_bytes(b"%PDF-")
    fake_host.ping()
    pid = fake_host.pid
    with pytest.raises(rh.RenderChildError) as ei:
        fake_host.render(pdf, width_px=2)
    assert ei.value.code == "render_child_protocol"
    _assert_not_alive(pid)
    assert fake_host.last_exit is not None and fake_host.pid is None
    ok = tmp_path / "ok.pdf"
    ok.write_bytes(b"%PDF-")
    assert fake_host.render(ok, width_px=2).width == 2 and fake_host.restarts == 1


def test_a_child_that_cannot_be_spawned_is_a_structured_failure_not_an_oserror(tmp_path):
    """Codex #471 第三轮 P2：exe 不在 / 没权限（冻结产物命令写错）时 `Popen` 抛 OSError——那不是 `RenderChildError`，
    `job.produce` 的 `except RenderChildError` 接不住，整个作业会炸而不是该格式 `format_failed`。现在它是
    `render_child_spawn_failed`；锁与队列槽都释放（第二次请求照样得到同一个结构化错误，不是卡死）。"""
    host = rh.RenderHost(
        [str(tmp_path / "no-such-render-child")], default_timeout=5, scratch_dir=tmp_path
    )
    try:
        for _ in range(2):
            with pytest.raises(rh.RenderChildError) as ei:
                host.ping(timeout=2)
            assert ei.value.code == "render_child_spawn_failed"
            assert "no-such-render-child" in ei.value.message
        assert host.pid is None and host.starts == 2
        pdf = tmp_path / "ok.pdf"
        pdf.write_bytes(b"%PDF-")
        with pytest.raises(rh.RenderChildError) as ei:
            host.render(pdf, width_px=2, timeout=2)
        assert ei.value.code == "render_child_spawn_failed"
        assert not list(tmp_path.glob("render-*.rgba*"))  # 像素临时文件照常清
    finally:
        host.close()


def test_pixel_validation_runs_inside_the_request_lock(fake_host, tmp_path):
    """Codex #471 第三轮 P2：像素文件长度核对若在 `request()` 释放锁之后才做，排在后面的请求会在「释放锁 →
    发现说谎 → kill」之间挤进去，和那个 child 说话。判据：render 那次请求**释放锁的那一刻** child 已经被 reap。"""
    pdf = tmp_path / "shortbytes.pdf"
    pdf.write_bytes(b"%PDF-")
    fake_host.ping()
    inner = fake_host._lock
    reaped_at_release: list[bool] = []

    class SpyLock:
        def acquire(self, *a, **k):
            return inner.acquire(*a, **k)

        def release(self):
            reaped_at_release.append(fake_host._proc is None)
            inner.release()

        def __enter__(self):
            inner.acquire()
            return self

        def __exit__(self, *a):
            self.release()
            return False

    fake_host._lock = SpyLock()
    with pytest.raises(rh.RenderChildError) as ei:
        fake_host.render(pdf, width_px=2)
    assert ei.value.code == "render_child_protocol"
    assert reaped_at_release == [True], reaped_at_release


def test_error_codes_are_a_closed_set():
    assert set(rc.ERROR_CODES) == {
        "render_child_timeout",
        "render_child_died",
        "render_child_spawn_failed",
        "render_child_protocol",
        "render_queue_full",
        "pixel_budget_exceeded",
        "render_failed",
        "bad_request",
    }
    with pytest.raises(AssertionError):
        rc.RenderChildError("not_a_code", "x")


def test_child_argv_is_this_interpreter_running_the_module_when_not_frozen():
    assert rc.child_argv() == [sys.executable, "-m", "tavotto.rendercore.renderchild"]


def test_importing_the_host_does_not_import_the_candidate_packages():
    """父进程 import renderhost / renderchild 不该拉起 pypdfium2 / pikepdf（native 只在 child 进程里）。"""
    code = (
        "import sys\n"
        "import tavotto.rendercore.renderhost, tavotto.rendercore.renderchild\n"
        "bad = [m for m in ('pypdfium2', 'pikepdf', 'PIL') if m in sys.modules]\n"
        "print(bad)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), os.environ.get("PYTHONPATH", "")]),
        },
    )
    assert proc.stdout.strip() == "[]", proc.stdout


# ---------------------------------------------------------------------------
# 真 child（需要 pypdfium2 + pikepdf；没有就 skip 并写明）
# ---------------------------------------------------------------------------
real = pytest.mark.skipif(
    not (HAS_PDFIUM and HAS_PIKEPDF),
    reason="本解释器里没有 pypdfium2 / pikepdf（候选包只装在 rc-venv）——这里是 not_run，不是绿",
)


@pytest.fixture
def real_host(tmp_path):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), env.get("PYTHONPATH", "")])
    host = rh.RenderHost(env=env, default_timeout=60, scratch_dir=tmp_path)
    yield host
    host.close()


@real
def test_real_child_pings_with_its_pdfium_version_and_memory_limit(real_host):
    info = real_host.ping()
    assert info["pdfium"].count(".") >= 2 and info["frozen"] is False
    assert info["memory_limit"] in ("set", "set-but-not-enforced-by-kernel", "unsupported") or info[
        "memory_limit"
    ].startswith("failed")


@real
def test_real_child_probe_reports_the_visible_size_with_rotation_and_userunit(real_host, tmp_path):
    """U00 夹具：CropBox [15 10 285 170] → 270 × 160；合成一页 /Rotate 90 + /UserUnit 2 → 320 × 540
    （PDFium 自己的 get_size() 忽略 UserUnit：child 补上那一次乘）。"""
    p = real_host.probe(FIXTURE / "page.pdf")
    assert (p["pages"], p["width_pt"], p["height_pt"], p["user_unit"], p["rotation"]) == (
        1,
        270.0,
        160.0,
        1.0,
        0,
    )
    assert p["crop_box"] == [15.0, 10.0, 285.0, 170.0] and p["media_box"] == [
        0.0,
        0.0,
        300.0,
        200.0,
    ]
    p = real_host.probe(_rotated_userunit_pdf(tmp_path))
    assert (p["width_pt"], p["height_pt"], p["user_unit"], p["rotation"]) == (320.0, 540.0, 2.0, 90)
    assert (p["raw_width_pt"], p["raw_height_pt"]) == (160.0, 270.0)


def _rotated_userunit_pdf(tmp_path) -> Path:
    """MediaBox 300×200、CropBox [15 10 285 170]、/Rotate 90、/UserUnit 2：PDFium 说 160 × 270，物理 320 × 540 pt。"""
    import pikepdf

    pdf = pikepdf.new()
    page = pikepdf.Dictionary(
        Type=pikepdf.Name.Page,
        MediaBox=[0, 0, 300, 200],
        CropBox=[15, 10, 285, 170],
        Rotate=90,
        UserUnit=2,
        Contents=pdf.make_stream(b"0 0 1 rg 20 20 40 30 re f"),
    )
    pdf.pages.append(pikepdf.Page(page))
    pdf.save(tmp_path / "rot.pdf")
    return tmp_path / "rot.pdf"


@real
def test_real_child_dpi_render_is_sized_by_the_physical_page_including_userunit(
    real_host, tmp_path
):
    """Codex #471 第三轮 P2：dpi 是物理密度。/UserUnit 2 的页 PDFium 报 160 × 270，物理 320 × 540 pt——72 dpi
    必须出 320 × 540 像素（与 probe 报的尺寸同一次乘），不是 160 × 270；width_px 路径不受影响。"""
    pdf = _rotated_userunit_pdf(tmp_path)
    by_dpi = real_host.render(pdf, dpi=72, page_size_pt=(320, 540))
    assert (by_dpi.width, by_dpi.height, by_dpi.dpi) == (320, 540, 72.0)
    by_w = real_host.render(pdf, width_px=160, page_size_pt=(320, 540))
    assert (by_w.width, by_w.height) == (160, 270)


@real
def test_real_child_renders_by_width_and_by_dpi_with_round_sizes(real_host):
    """page.pdf 可见 270×160 pt：width_px=400 → 400 × round(160·400/270)=237；dpi=150 → round(562.5)=562 × 333
    （Python 的 `round` 是四舍六入五成双，与旧 `app._export_produce_canvas` 报 `width_px` 用的同一个函数——
    报出去的数就是 buffer 的尺寸）。白底是 RGB（3 通道，没有一条全 255 的 alpha）。"""
    by_w = real_host.render(FIXTURE / "page.pdf", width_px=400, page_size_pt=(270, 160))
    assert (by_w.width, by_w.height, by_w.channels, by_w.dpi) == (400, 237, 3, None)
    by_dpi = real_host.render(FIXTURE / "page.pdf", dpi=150, page_size_pt=(270, 160))
    assert (by_dpi.width, by_dpi.height, by_dpi.channels, by_dpi.dpi) == (562, 333, 3, 150.0)
    assert by_dpi.stride >= by_dpi.width * 3
    assert len(by_dpi.samples) >= by_dpi.stride * (by_dpi.height - 1)
    # 源 (60,45) 在蓝矩形里（CropBox 空间 (45,35)）→ 像素 (45·562/270, 333 − 35·333/160)
    px = by_dpi.pixel(int(45 * 562 / 270), int(333 - 35 * 333 / 160))
    assert px == (0, 0, 255), px


@real
def test_real_child_transparent_render_has_straight_alpha_and_white_render_is_opaque(real_host):
    """透明：没画到的地方 alpha 0；page.pdf 里 α=0.5 的红矩形单独落在透明底上时 alpha≈128 且颜色是**straight**
    的红 (255,0,0)——预乘会给 (128,0,0)。白底：RGB 三通道，同一点是红以 0.5 合成到白 (255,128,128)。"""
    t = real_host.render(
        FIXTURE / "page.pdf", width_px=540, transparent=True, page_size_pt=(270, 160)
    )
    assert t.channels == 4 and t.pixel(5, 5) == (0, 0, 0, 0)
    # 源 (170, 110)：红 α=0.5 单独（蓝矩形 40..140 之外、折线之外）→ CropBox (155, 100) → 像素 (310, 320−200=120)
    px = t.pixel(310, 120)
    assert abs(px[3] - 128) <= 2 and px[0] >= 250 and px[1] <= 4 and px[2] <= 4, px
    w = real_host.render(
        FIXTURE / "page.pdf", width_px=540, transparent=False, page_size_pt=(270, 160)
    )
    assert w.channels == 3 and w.pixel(5, 5) == (255, 255, 255)
    got = w.pixel(310, 120)
    assert all(abs(a - b) <= 2 for a, b in zip(got, (255, 128, 128))), got


@real
def test_real_child_child_side_budget_bad_pdf_and_timeout_recovery(real_host, tmp_path):
    # 像素预算 child 侧：绕过父侧精确判据（给错的 page_size_pt），child 打开页面后按真实高度拒
    real_host.max_pixels = 400 * 237 - 1
    with pytest.raises(rh.RenderChildError) as ei:
        real_host.render(FIXTURE / "page.pdf", width_px=400, page_size_pt=(400, 1))
    assert ei.value.code == "pixel_budget_exceeded"
    assert not list(tmp_path.glob("render-*.rgba"))
    real_host.max_pixels = rc.DEFAULT_MAX_PIXELS
    # 坏输入：结构化错误而不是 child 死掉
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 not really")
    with pytest.raises(rh.RenderChildError) as ei:
        real_host.render(bad, width_px=100, page_size_pt=(100, 100))
    assert ei.value.code == "render_failed"
    assert real_host.pid is not None, "坏输入不该杀 child"
    with pytest.raises(rh.RenderChildError) as ei:
        real_host.probe(bad)
    assert ei.value.code == "render_failed"
    # 极短超时 → kill → 下一次恢复（真 child、真 kill、真 reap）
    pid = real_host.pid
    with pytest.raises(rh.RenderChildError) as ei:
        real_host.render(
            FIXTURE / "page.pdf", width_px=3000, page_size_pt=(270, 160), timeout=0.0001
        )
    assert ei.value.code == "render_child_timeout"
    _assert_not_alive(pid)
    buf = real_host.render(FIXTURE / "page.pdf", width_px=200, page_size_pt=(270, 160))
    assert (buf.width, buf.height) == (200, 119) and real_host.restarts == 1


@real
def test_real_child_inspect_counts_objects_and_extracts_text(real_host):
    info = real_host.inspect(FIXTURE / "page.pdf")
    assert info["pages"] == 1 and info["objects"]["text"] >= 1 and info["objects"]["path"] >= 2
    assert "U00 fixture: y = 3x + 1" in info["text"]


@real
def test_concurrent_probe_render_inspect_all_go_through_one_child_in_order(real_host):
    """RC-047：并发 preview / probe / inspect 没有进程内 native 并发——它们全部经同一个 child、seq 严格递增。"""
    real_host.max_waiting = 64
    real_host._slots = threading.BoundedSemaphore(65)
    errors: list[BaseException] = []
    seqs: list[int] = []
    lock = threading.Lock()

    def work(i: int) -> None:
        try:
            for _ in range(3):
                if i % 3 == 0:
                    real_host.probe(FIXTURE / "page.pdf")
                elif i % 3 == 1:
                    real_host.render(FIXTURE / "page.pdf", width_px=120, page_size_pt=(270, 160))
                else:
                    real_host.inspect(U06_PDF if U06_PDF.is_file() else FIXTURE / "page.pdf")
                with lock:
                    seqs.append(real_host.ping()["seq"])
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert real_host.starts == 1 and sorted(seqs) == seqs and len(seqs) == 18


@real
def test_shared_host_is_one_per_process_and_can_be_shut_down():
    rh.shutdown_shared()
    a = rh.shared()
    b = rh.shared()
    assert a is b
    a.ping()
    pid = a.pid
    rh.shutdown_shared()
    _assert_not_alive(pid)
    assert rh.shared() is not a
    rh.shutdown_shared()
