"""build 的静默看门狗：卡死与「慢但在跑」用输出分得开（ADR 0050）。

判据只有一条，两条控制面共用：**`worker.log` 还在长就是还在跑**。worker 的
stderr 与用户脚本的 stdout 都落进那个文件，所以「有没有新输出」就是「有没有
进展」。用户不必先把脚本归类——那正是这一版要去掉的认知成本。

四层看护：

* **判据本身**：日志长 → 静默计时清零；不长 → 到点判死；兜底上限仍在。
* **只对 build**：热态操作（override / export / 预览）仍是平坦上限。
* **真 worker**：会打进度的慢脚本活过静默阈值；完全静默的照样被判死。
* **同源**：workerd 那侧收到同一个 `idle_timeout_ms`。
"""

import json
import os
import threading
import time

import pytest

from tavotto.engine import pool


class _Proc:
    """活着、但永远不回应的 worker。stdout 读起来会一直阻塞。"""

    def __init__(self, *_a, **_kw):
        self.pid = 1
        self.stdin = self
        self.stdout = self

    def poll(self):
        return None

    def readline(self):
        time.sleep(30)
        return ""

    def write(self, *_a):
        return None

    def flush(self):
        return None

    def kill(self):
        return None

    def wait(self, timeout=None):
        return 0


@pytest.fixture
def worker(monkeypatch, tmp_path):
    monkeypatch.setattr(pool.subprocess, "Popen", _Proc)
    monkeypatch.setattr(
        pool, "select_worker_python", lambda: ("/usr/bin/python3", pool.SOURCE_SYSTEM)
    )
    w = pool.EngineWorker("fig.py", str(tmp_path), "main")
    w.log_path.parent.mkdir(parents=True, exist_ok=True)
    w.log_path.write_bytes(b"")
    return w


# --------------------------------------------------------------- 判据本身
def test_output_resets_the_silence_clock(worker, monkeypatch):
    """脚本一直在打进度 → 一直等下去；停了才开始计时判死。

    这是整件事的产品结论：**会说话的慢脚本不再需要任何配置**。
    """
    monkeypatch.setattr(pool, "_PROGRESS_POLL", 0.02)
    stop = threading.Event()

    def chatter():
        # 「每 50 帧打一行」的最小复现：静默阈值的 5 倍时长里持续输出
        while not stop.is_set():
            with worker.log_path.open("ab") as f:
                f.write("[INFO] ...已处理 50 帧\n".encode())
            time.sleep(0.02)

    t = threading.Thread(target=chatter, daemon=True)
    t.start()
    try:
        alive = threading.Thread(target=lambda: time.sleep(0.5), daemon=True)
        alive.start()
        waited, silent = worker._wait_with_watchdog(alive, timeout=30.0, idle=0.1)
    finally:
        stop.set()
    assert not alive.is_alive(), "读线程自己结束了，不该被看门狗判死"
    assert waited >= 0.4, "有输出时必须一直等下去，而不是在静默阈值处返回"
    assert silent < 0.1


def test_silence_ends_it(worker, monkeypatch):
    """一个字节都不多出来 → 到点判死，而且报的是「静默了多久」。"""
    monkeypatch.setattr(pool, "_PROGRESS_POLL", 0.02)
    blocked = threading.Thread(target=lambda: time.sleep(30), daemon=True)
    blocked.start()
    waited, silent = worker._wait_with_watchdog(blocked, timeout=30.0, idle=0.15)
    assert blocked.is_alive()
    assert 0.15 <= silent < 1.0
    assert waited < 1.0, "静默判死不该等到兜底上限"


def test_the_hard_ceiling_still_catches_a_printing_loop(worker, monkeypatch):
    """`while True: print(i)` 永远等不到静默——兜底上限必须仍然收得住。"""
    monkeypatch.setattr(pool, "_PROGRESS_POLL", 0.02)
    stop = threading.Event()

    def chatter():
        while not stop.is_set():
            with worker.log_path.open("ab") as f:
                f.write(b"x\n")
            time.sleep(0.01)

    threading.Thread(target=chatter, daemon=True).start()
    try:
        blocked = threading.Thread(target=lambda: time.sleep(30), daemon=True)
        blocked.start()
        waited, silent = worker._wait_with_watchdog(blocked, timeout=0.3, idle=30.0)
    finally:
        stop.set()
    assert blocked.is_alive()
    assert 0.3 <= waited < 2.0
    assert silent < 0.3, "它一直在输出，判死的该是兜底上限而不是静默"


def test_without_a_watchdog_the_behaviour_is_the_old_flat_one(worker):
    """热态操作不传 idle → 一次 join，与看门狗登场之前逐字节相同。"""
    blocked = threading.Thread(target=lambda: time.sleep(30), daemon=True)
    blocked.start()
    started = time.monotonic()
    waited, silent = worker._wait_with_watchdog(blocked, timeout=0.2, idle=None)
    assert 0.2 <= time.monotonic() - started < 1.5
    assert waited >= 0.2 and silent == 0.0


# ------------------------------------------------- 看门狗看得见真实的那个日志
def test_the_watchdog_can_see_the_real_log(worker):
    """`_log_size` 对**真实 worker 的 log_path、走默认根**必须量得到、且会变大。

    这是本判据唯一会**静默**坏掉的地方。`_log_size` 走 `_contained_log`：先
    `realpath` 再按前缀把路径钉在 `ENGINE_CACHE` 内（CodeQL 那条路径注入的解药）。
    如果哪天归一化行为变了——Windows 的盘符大小写、8.3 短名、`RUNNER_TEMP` 那种
    junction、UNC——它会开始**误拒真实路径**，于是 `_log_size` 恒回 0，看门狗
    再也看不见进展，`build` 静默退化成 4 小时的平坦上限。

    **那一刻不会有任何东西变红**：没有异常、没有报错，只是用户从等 20 分钟变成等
    4 小时。端到端冒烟也发现不了——它跑得快，根本不会等到上限，所以它没有机会
    区分「看门狗在工作」和「看门狗瞎了但兜底还在」。

    此前的用例只钉了**拒绝**方向（越界路径回 0），接受方向一条都没有——
    「判据只钉了一条边」，反方向坏掉时它不响。这一条补的就是那另一条边。
    """
    assert pool._log_size(worker.log_path) == 0  # 空文件
    worker.log_path.write_bytes(b"[INFO] 1\n")
    first = pool._log_size(worker.log_path)
    assert first > 0, (
        "默认根下量不到真实 worker 的日志——看门狗此刻已经瞎了，而 build 会静默退化成平坦上限"
    )
    with worker.log_path.open("ab") as f:
        f.write(b"[INFO] 2\n")
    assert pool._log_size(worker.log_path) > first, "日志长大了却量不出变化"


@pytest.mark.skipif(os.name == "nt", reason="符号链接根是 POSIX 上 junction 的等价物")
def test_a_symlinked_cache_root_still_resolves(tmp_path, monkeypatch):
    """缓存根本身是个符号链接时也要量得到。

    CI runner 的临时目录常是 junction / symlink（macOS 上 `/tmp` → `/private/tmp`
    就是），而包含判断两侧都过 `realpath`——**只有两侧都过才成立**。这条钉住
    「根是链接」这个真实形状，而不是只在规整目录上验过就宣布安全。
    """
    real = tmp_path / "real-cache"
    real.mkdir()
    link = tmp_path / "linked-cache"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(pool, "ENGINE_CACHE", link)
    log = real / "s1" / "worker.log"
    log.parent.mkdir(parents=True)
    log.write_bytes(b"x" * 7)
    # 通过链接那条路径去问，答案必须与实体一致
    assert pool._log_size(link / "s1" / "worker.log") == 7
    assert pool._log_tail_from(link / "s1" / "worker.log", 0) == "xxxxxxx"


# --------------------------------------------------------------- 只对 build
def test_only_build_gets_the_watchdog(worker, monkeypatch):
    """build 用看门狗、说「多久没有输出」；热态超时仍是平坦上限、说「等了多久」。"""
    seen = []
    monkeypatch.setattr(
        pool.EngineWorker,
        "_readline",
        lambda self, timeout, idle=None: seen.append((timeout, idle)) or "",
    )
    monkeypatch.setattr(pool.EngineWorker, "alive", lambda self: True)
    for cmd in ("build", "render"):
        with pytest.raises(pool.WorkerError):  # 空回应 → 「worker 崩溃」，这里只看参数
            worker.request({"cmd": cmd}, 123.0)
    assert seen[0] == (123.0, pool.BUILD_IDLE_TIMEOUT), "build 必须带看门狗"
    assert seen[1] == (123.0, None), "热态操作不该带看门狗"


def test_the_two_timeout_messages_say_different_things(worker):
    """判据不同，用户的下一步就不同——措辞必须跟着判据走。"""
    silent = worker._timeout_error(1500.0, pool.BUILD_IDLE_TIMEOUT + 1, True)
    assert silent.code == pool.BUILD_TIMEOUT_CODE
    assert "没有任何输出" in str(silent) and "打一行进度输出" in str(silent)

    ceiling = worker._timeout_error(pool.BUILD_HARD_TIMEOUT, 3.0, True)
    assert ceiling.code == pool.BUILD_TIMEOUT_CODE
    assert "一直有输出" in str(ceiling) and "没有任何输出" not in str(ceiling)

    hot = worker._timeout_error(300.0, 0.0, False)
    assert hot.code == "worker_timeout"
    assert "渲染超时" in str(hot)


def test_nobody_has_to_classify_the_script_any_more():
    """注册表的 cost 不再参与超时——那正是这一版要去掉的认知成本（ADR 0050）。

    判据抽掉不红的形状在这里是「pool 还在读注册表」，所以直接钉住：那两个
    按 cost 算超时的出处必须不存在。
    """
    assert not hasattr(pool, "build_timeout_for")
    assert not hasattr(pool, "script_cost")
    assert not hasattr(pool, "BUILD_TIMEOUT_FACTORS")
    src = (pool.__file__,)
    text = open(src[0], encoding="utf-8").read()
    assert "registry" not in text.split("# ---- 单次请求的超时上限")[1][:4000], (
        "超时那一段不该再碰注册表"
    )


# --------------------------------------------------------------- 同源
class _FakeClient:
    def __init__(self):
        self.calls = []

    def call(self, op, **kw):
        self.calls.append((op, kw))
        return {"ok": True, "session_id": "s-1", "stems": {}}


def test_workerd_gets_the_same_predicate(monkeypatch, tmp_path):
    """两条控制面同一条判据：workerd 收到同一个静默阈值与同一个兜底上限。"""
    monkeypatch.setattr(
        pool, "select_worker_python", lambda: ("/usr/bin/python3", pool.SOURCE_SYSTEM)
    )
    client = _FakeClient()
    w = pool.WorkerdWorker("fig.py", str(tmp_path), "main", client=client)
    w.ensure_built()
    build = [kw for op, kw in client.calls if op == "build"][0]
    assert build["timeout"] == pool.BUILD_HARD_TIMEOUT
    assert build["idle_timeout"] == pool.BUILD_IDLE_TIMEOUT
    # 热态操作不带
    w.override("Fig1", [])
    render = [kw for op, kw in client.calls if op == "render"][0]
    assert render["idle_timeout"] is None


def test_the_supervisor_request_carries_the_idle_field(monkeypatch):
    """`idle_timeout_ms` 要真的进到发给 workerd 的那条请求里。"""
    from tavotto.engine import workerd_client

    sent = {}

    class _Proc:
        pass

    c = workerd_client.WorkerdClient.__new__(workerd_client.WorkerdClient)
    c._lock = threading.Lock()
    c._pending = {}
    c._next_id = lambda: "r-1"

    def fake_write(_proc, req):
        sent.update(req)
        slot = c._pending["r-1"]
        slot["resp"] = {"ok": True}
        slot["event"].set()

    c._write = fake_write
    resp = c._call_on(_Proc(), "build", "s-1", None, {}, 100.0, 1.0, 42.0)
    assert resp["ok"]
    assert sent["timeout_ms"] == 100_000
    assert sent["idle_timeout_ms"] == 42_000
    assert json.dumps(sent)  # 信封必须仍是可序列化的普通对象
