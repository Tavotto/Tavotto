"""safe worker 阻断关键文件操作之后，不许把读到的结果记成完整成功（P01a，AC-02 / AC-03）。

审计反例 EX03：脚本写临时缓存，`os.replace` 覆盖旧缓存，再读缓存算值画图。直接 Python 与
native 得到 2；safe worker 的删除 / 改名守卫把 replace **静默跳过**，脚本读到旧缓存的 1，
build 仍返回 ok。EX02：`savefig('stage.png')` 被拦截不落盘，随后 `Image.open('stage.png')`
读不到——脚本若 `except Exception` 吞掉，同样得到一个「成功」的错值。

守卫本身一字不动（项目里的文件仍然原样），变的是**终局事实**：

* 被守卫拦下的改名 / 删除 / 写入，与被拦截的 savefig 目标，记进一本账（`figcapture.BlockedFileOps`）；
* 脚本**随后又去读**那个受影响的路径（open / stat），才算「结果可能不同于直接 Python」——
  光是被拦下一次不算（`test_workdir_mode` 里那个脚本拦了四次、从没回读，照旧成功）；
* 事实记在带外账本里，不靠异常——用户脚本 `except Exception` 吞掉什么都洗不掉它；
* 终局是稳定错误码 `safe_file_operation_blocked`，不自动换环境重跑、不改脚本、不原地放行。

每条反例都配正对照：沙盒里真实发生的 replace、没有回读的拦截、普通 savefig、BytesIO 往返、
脚本自己改 `plt.show` 之类的普通 monkeypatch，全部照常成功。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tavotto import app as app_module
from tavotto.engine import (
    figcapture,
    pool as engine_pool,
    prepsession,
    probe as engine_probe,
    workdir,
)

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

CODE = "safe_file_operation_blocked"
VECTORS = Path(__file__).parent / "golden" / "preparation_session_vectors.json"

#: 审计 EX03 的原样形状：相对路径、脚本目录里运行（project 模式）。
EX03 = """\
import json
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

with open("cache.tmp", "w") as f:
    f.write("2")
os.replace("cache.tmp", "cache.csv")
with open("cache.csv") as f:
    value = int(f.read())
with open("observed.json", "w") as f:
    json.dump({"computed_value": value}, f)
fig, ax = plt.subplots()
ax.plot([0, 1], [value, value])
fig.savefig("computed.png")
"""

#: 同一个反例，绝对路径：不依赖 project 模式（沙盒 cwd 下也指向真实图库）。
EX03_ABSOLUTE = """\
import os
from pathlib import Path
import matplotlib.pyplot as plt

here = Path(__file__).resolve().parent
tmp, final = here / "cache.tmp", here / "cache.csv"
tmp.write_text("2")
os.replace(tmp, final)
value = int(final.read_text())
fig, ax = plt.subplots()
ax.plot([0, 1], [value, value])
"""

#: 用户把阻断的那一步和回读都包进 `except Exception`：异常（若有）被吞掉，事实仍在。
EX03_SWALLOWED = """\
import os
import matplotlib.pyplot as plt

value = 1
try:
    with open("cache.tmp", "w") as f:
        f.write("2")
    os.replace("cache.tmp", "cache.csv")
except Exception:
    pass
try:
    with open("cache.csv") as f:
        value = int(f.read())
except Exception:
    value = 1
fig, ax = plt.subplots()
ax.plot([0, 1], [value, value])
"""

#: `shutil.move` 与 `os.rename` 是同一类（改名 / 移动一个项目里的文件）。
MOVE_THEN_READ = """\
import shutil
import matplotlib.pyplot as plt

shutil.move("seed.txt", "moved.txt")
with open("moved.txt") as f:
    n = len(f.read())
fig, ax = plt.subplots()
ax.plot([0, n])
"""

#: 拦下删除之后用 exists 做分支：脚本以为它删掉了。
DELETE_THEN_EXISTS = """\
import os
import matplotlib.pyplot as plt

os.remove("stale.csv")
n = 0 if os.path.exists("stale.csv") else 3
fig, ax = plt.subplots()
ax.plot([0, n])
"""

#: EX02：savefig 被拦截不落盘，随后读它。
EX02 = """\
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image

fig, ax = plt.subplots()
ax.plot([0, 1], [1, 2])
fig.savefig("stage.png")
with Image.open("stage.png") as im:
    size = list(im.size)
with open("observed.json", "w") as f:
    json.dump({"image_size": size}, f)
"""

EX02_SWALLOWED = """\
import matplotlib.pyplot as plt
from PIL import Image

fig, ax = plt.subplots()
ax.plot([0, 1], [1, 2])
fig.savefig("stage.png")
try:
    with Image.open("stage.png") as im:
        size = list(im.size)
except Exception:
    size = [0, 0]
"""

# ----------------------------- 正对照：这些都必须照常成功
#: 沙盒里（默认 cwd）的 replace 是真实发生的，值是 2——保护的是真实图库，不是所有 replace。
SANDBOX_REPLACE = """\
import os
import matplotlib.pyplot as plt

with open("cache.tmp", "w") as f:
    f.write("2")
os.replace("cache.tmp", "cache.csv")
with open("cache.csv") as f:
    value = int(f.read())
assert value == 2, value
fig, ax = plt.subplots()
ax.plot([0, 1], [value, value])
fig.savefig("computed.png")
"""

#: 项目里的文件被拦下，但脚本此后再没碰过那些路径——与 test_workdir_mode 同形状。
BLOCKED_NEVER_READ = """\
import os
import shutil
import matplotlib.pyplot as plt

os.remove("stale.csv")
os.rename("seed.txt", "seed_old.txt")
shutil.rmtree("old_dir")
fig, ax = plt.subplots()
ax.plot([1, 2], [3, 4])
fig.savefig("plain.png")
"""

#: 拦下之后把路径重新写一遍再读：读到的是脚本自己刚写的，和直接 Python 一致。
BLOCKED_THEN_REWRITTEN = """\
import os
import matplotlib.pyplot as plt

os.remove("stale.csv")
with open("stale.csv", "w") as f:
    f.write("5")
with open("stale.csv") as f:
    n = int(f.read())
fig, ax = plt.subplots()
ax.plot([0, n])
"""

#: 普通 savefig（不回读）、BytesIO 往返、普通 monkeypatch：不是阻断，不能被当成失败。
PLAIN_SAVEFIG = """\
import io
import matplotlib.pyplot as plt
from PIL import Image

plt.show = lambda *a, **k: None        # 普通 monkeypatch
fig, ax = plt.subplots()
ax.plot([1, 2], [3, 4])
fig.savefig("plain.png")
buf = io.BytesIO()
fig.savefig(buf, format="png")
buf.seek(0)
with Image.open(buf) as im:
    assert im.size[0] > 0
plt.show()
"""

#: 脚本在 savefig 之前读过同名旧文件，之后不再读：顺序决定一切。
READ_BEFORE_SAVEFIG = """\
import matplotlib.pyplot as plt

with open("stage.png", "rb") as f:
    old = len(f.read())
fig, ax = plt.subplots()
ax.plot([0, old])
fig.savefig("stage.png")
"""


@pytest.fixture
def figs(tmp_path):
    root = tmp_path / "figs"
    root.mkdir()
    (root / "cache.csv").write_text("1", encoding="utf-8")
    (root / "seed.txt").write_text("abc", encoding="utf-8")
    (root / "stale.csv").write_text("9", encoding="utf-8")
    (root / "old_dir").mkdir()
    (root / "old_dir" / "x.txt").write_text("x", encoding="utf-8")
    (root / "stage.png").write_bytes(b"stale-old-image")
    yield root
    engine_pool.shutdown_all(str(root), wait=True)
    workdir.set_mode(root, workdir.MODE_SANDBOX)


def _build(figs: Path, name: str, source: str, *, project_mode: bool):
    (figs / name).write_text(source, encoding="utf-8")
    workdir.set_mode(figs, workdir.MODE_PROJECT if project_mode else workdir.MODE_SANDBOX)
    return engine_pool.build(name, str(figs), "__main__")


def _blocked(figs, name, source, *, project_mode=True) -> engine_pool.WorkerError:
    with pytest.raises(engine_pool.WorkerError) as err:
        _build(figs, name, source, project_mode=project_mode)
    return err.value


# ====================================================== 反例：不许记成完整成功
@needs_worker
def test_ex03_replace_then_read_is_not_a_complete_success(figs):
    """审计 EX03：以前这里 `ok=true`、脚本读到 1（直接 Python 是 2）。"""
    e = _blocked(figs, "ex03.py", EX03)
    assert e.code == CODE, (e.code, str(e))
    # 守卫一字不动：真实图库里的缓存仍是旧的 1，脚本的临时文件也没被改名
    assert (figs / "cache.csv").read_text(encoding="utf-8") == "1"
    assert (figs / "cache.tmp").is_file()
    # 说得出是哪一类操作，不带路径（message 会进 app.log 与诊断包）
    ops = e.extra["operations"]
    assert [(o["op"], o["count"]) for o in ops] == [("os.replace", 1)]
    assert "cache" not in str(e) and "cache" not in json.dumps(e.extra)
    # 接得上后续「保留文件语义」执行路径的依据，且如实说当前没有可用的 adapter
    assert e.extra["required_execution"] == "preserve_file_semantics"
    assert e.extra["adapter_available"] is False
    assert e.retryable is False if hasattr(e, "retryable") else True


@needs_worker
def test_ex03_with_absolute_paths_in_the_default_sandbox_mode(figs):
    e = _blocked(figs, "ex03_abs.py", EX03_ABSOLUTE, project_mode=False)
    assert e.code == CODE, (e.code, str(e))
    assert (figs / "cache.csv").read_text(encoding="utf-8") == "1"


@needs_worker
def test_user_except_exception_cannot_wash_the_blocked_fact(figs):
    """脚本把阻断那步与回读都包进 `except Exception`，值落回 1 并画图——仍不是完整成功。"""
    e = _blocked(figs, "swallowed.py", EX03_SWALLOWED)
    assert e.code == CODE, (e.code, str(e))
    assert (figs / "cache.csv").read_text(encoding="utf-8") == "1"


@needs_worker
def test_a_script_that_catches_the_readback_failure_is_still_reported(figs):
    """目标文件根本不存在：回读抛 FileNotFoundError 被脚本吞掉，回落到默认值。"""
    (figs / "cache.csv").unlink()
    e = _blocked(figs, "swallowed2.py", EX03_SWALLOWED)
    assert e.code == CODE, (e.code, str(e))


@needs_worker
def test_a_failing_readback_is_attributed_to_the_blocked_operation(figs):
    """目标缓存原本不存在，replace 被拦下，回读 FileNotFoundError——原因是守卫，不是「数据找不到」。

    以前这里是 `script_error`（或带弹窗的 `missing_input`，请用户去指认一个其实被我们拦下的路径）。"""
    (figs / "cache.csv").unlink()
    e = _blocked(figs, "ex03_gone.py", EX03)
    assert e.code == CODE, (e.code, str(e))
    assert getattr(e, "missing_input", None) is None


@needs_worker
@pytest.mark.parametrize(
    ("name", "source", "op"),
    [
        ("mv.py", MOVE_THEN_READ, "shutil.move"),
        ("rm.py", DELETE_THEN_EXISTS, "os.remove"),
    ],
)
def test_other_blocked_operations_followed_by_an_observation(figs, name, source, op):
    e = _blocked(figs, name, source)
    assert e.code == CODE, (e.code, str(e))
    assert [o["op"] for o in e.extra["operations"]] == [op]


@needs_worker
def test_ex02_savefig_then_reading_the_file_back_is_attributed(figs):
    """EX02：savefig 被拦截不落盘，`Image.open('stage.png')` 读不到（或读到图库里的旧图）。"""
    (figs / "stage.png").unlink()
    e = _blocked(figs, "ex02.py", EX02)
    assert e.code == CODE, (e.code, str(e))
    assert [o["op"] for o in e.extra["operations"]] == ["savefig"]
    assert not (figs / "stage.png").exists(), "拦截仍然不落盘"


@needs_worker
def test_ex02_a_stale_file_of_the_same_name_is_not_mistaken_for_the_saved_one(figs):
    """project 模式下图库里碰巧有个旧的 stage.png：读到的不是脚本这次存的，不能算成功。"""
    e = _blocked(figs, "ex02_stale.py", EX02)
    assert e.code == CODE, (e.code, str(e))
    assert (figs / "stage.png").read_bytes() == b"stale-old-image"


@needs_worker
def test_ex02_swallowed_readback_is_still_reported(figs):
    e = _blocked(figs, "ex02_swallowed.py", EX02_SWALLOWED)
    assert e.code == CODE, (e.code, str(e))


# ============================================================ 正对照
@needs_worker
def test_a_real_replace_inside_the_sandbox_is_untouched(figs):
    worker, resp = _build(figs, "sandbox_replace.py", SANDBOX_REPLACE, project_mode=False)
    assert "computed" in resp["stems"], resp
    assert "[guard]" not in worker._log_tail()


@needs_worker
def test_blocked_operations_that_are_never_read_back_still_succeed(figs):
    worker, resp = _build(figs, "never_read.py", BLOCKED_NEVER_READ, project_mode=True)
    assert "plain" in resp["stems"], resp
    assert worker._log_tail().count("[guard]") >= 3  # 守卫照旧在拦
    assert (figs / "stale.csv").is_file() and (figs / "seed.txt").is_file()


@needs_worker
def test_a_blocked_path_the_script_rewrote_itself_is_not_a_divergence(figs):
    worker, resp = _build(figs, "rewritten.py", BLOCKED_THEN_REWRITTEN, project_mode=True)
    assert resp["stems"], resp


@needs_worker
def test_plain_savefig_bytesio_roundtrip_and_ordinary_monkeypatching_succeed(figs):
    worker, resp = _build(figs, "plain.py", PLAIN_SAVEFIG, project_mode=True)
    assert "plain" in resp["stems"], resp
    assert not (figs / "plain.png").exists(), "savefig 仍是捕获、不落盘"


@needs_worker
def test_a_savefig_followed_by_an_existence_print_still_succeeds(figs):
    source = PLAIN_SAVEFIG.replace("plt.show()", "import os; print(os.path.exists('plain.png'))")
    worker, resp = _build(figs, "exists_print.py", source, project_mode=True)
    assert "plain" in resp["stems"], resp


@needs_worker
def test_reading_a_path_before_the_savefig_is_not_a_divergence(figs):
    worker, resp = _build(figs, "before.py", READ_BEFORE_SAVEFIG, project_mode=True)
    assert "stage" in resp["stems"], resp


# ====================================================== 账本本身（进程内，不起 worker）
def _ledger(tmp_path):
    return figcapture.BlockedFileOps()


def test_ledger_needs_an_observation_after_the_block(tmp_path):
    f = tmp_path / "a.txt"
    ledger = _ledger(tmp_path)
    ledger.observe(str(f), "r")  # 先于阻断的读不算
    ledger.blocked("os.replace", "rename", str(tmp_path / "a.tmp"), str(f))
    assert ledger.material() == []
    ledger.observe(str(tmp_path / "other.txt"), "r")
    assert ledger.material() == []
    ledger.observe(str(f), "rb")
    assert [(o["op"], o["count"]) for o in ledger.material()] == [("os.replace", 1)]


def test_ledger_write_open_clears_the_taint_but_append_does_not(tmp_path):
    f = tmp_path / "a.txt"
    ledger = _ledger(tmp_path)
    ledger.blocked("os.remove", "delete", str(f))
    ledger.observe(str(f), "a")
    ledger.observe(str(f), "w")  # 脚本自己重写了：此后读到的是它写的
    ledger.observe(str(f), "r")
    assert ledger.material() == []
    ledger.blocked("os.remove", "delete", str(f))
    ledger.observe(str(f), "r+")
    assert ledger.material() != []


def test_ledger_directory_taint_covers_the_files_under_it(tmp_path):
    d = tmp_path / "d"
    ledger = _ledger(tmp_path)
    ledger.blocked("shutil.rmtree", "delete_dir", str(d))
    ledger.observe(str(tmp_path / "dd" / "x"), "r")  # 同前缀的兄弟目录不算
    assert ledger.material() == []
    ledger.observe(str(d / "x" / "y.txt"), "r")
    assert [o["op"] for o in ledger.material()] == ["shutil.rmtree"]


def test_a_stat_of_an_intercepted_savefig_target_is_not_a_divergence_but_a_read_is(tmp_path):
    f = tmp_path / "a.png"
    ledger = _ledger(tmp_path)
    ledger.blocked("savefig", "savefig", str(f))
    ledger.observe_stat(str(f))  # `savefig(p); print(os.path.exists(p))`：校验打印，照旧成功
    assert ledger.material() == []
    ledger.observe(str(f), "rb")  # 读内容才是错的数据
    assert [o["op"] for o in ledger.material()] == ["savefig"]
    # 对照：改名 / 删除之后的 stat 就是分叉（existence 本身就是被改变的状态）
    other = _ledger(tmp_path)
    other.blocked("os.remove", "delete", str(f))
    other.observe_stat(str(f))
    assert [o["op"] for o in other.material()] == ["os.remove"]


def test_ledger_is_bounded_and_a_stopped_ledger_is_inert(tmp_path):
    ledger = _ledger(tmp_path)
    for i in range(figcapture.BlockedFileOps.MAX_EVENTS + 50):
        ledger.blocked("os.remove", "delete", str(tmp_path / f"{i}.txt"))
    assert len(ledger._events) == figcapture.BlockedFileOps.MAX_EVENTS
    ledger.stop()
    ledger.observe(str(tmp_path / "0.txt"), "r")
    assert ledger.material() == []


def test_ledger_overflow_beyond_max_events_still_catches_a_later_readback(tmp_path):
    """Codex P1：第 201+ 个被拦操作的路径不能被静默丢掉，否则回读它仍记成完整成功。"""
    cap = figcapture.BlockedFileOps.MAX_EVENTS
    ledger = _ledger(tmp_path)
    for i in range(cap + 50):
        ledger.blocked("os.remove", "delete", str(tmp_path / f"{i}.txt"))
    ledger.observe(str(tmp_path / "0.txt"), "r")  # 前 200 个之内：原本就判得出
    assert [o["op"] for o in ledger.material()] == ["os.remove"]
    late = _ledger(tmp_path)
    for i in range(cap + 50):
        late.blocked("os.remove", "delete", str(tmp_path / f"{i}.txt"))
    late.observe(str(tmp_path / "unrelated.txt"), "r")  # 正对照：读不相干的路径不算
    assert late.material() == []
    late.observe(str(tmp_path / f"{cap + 10}.txt"), "r")  # 第 210 个
    assert [(o["op"], o["count"]) for o in late.material()] == [("os.remove", 1)]


def test_ledger_overflow_rewrite_clears_and_savefig_stat_stays_exempt(tmp_path):
    cap = figcapture.BlockedFileOps.MAX_EVENTS
    ledger = _ledger(tmp_path)
    for i in range(cap + 5):
        ledger.blocked("os.remove", "delete", str(tmp_path / f"{i}.txt"))
    ledger.observe(str(tmp_path / f"{cap + 1}.txt"), "w")  # 脚本自己重写
    ledger.observe(str(tmp_path / f"{cap + 1}.txt"), "r")
    assert ledger.material() == []
    sv = _ledger(tmp_path)
    for i in range(cap + 5):
        sv.blocked("savefig", "savefig", str(tmp_path / f"{i}.png"))
    sv.observe_stat(str(tmp_path / f"{cap + 1}.png"))  # 溢出的 savefig 同样只认读内容
    assert sv.material() == []
    sv.observe(str(tmp_path / f"{cap + 1}.png"), "rb")
    assert [o["op"] for o in sv.material()] == ["savefig"]


def test_ledger_overflow_set_is_bounded_coarsens_to_directories_then_marks_lost(tmp_path):
    cap = figcapture.BlockedFileOps.MAX_EVENTS
    spill_cap = figcapture.BlockedFileOps.MAX_SPILL_PATHS
    ledger = _ledger(tmp_path)
    for i in range(cap):
        ledger.blocked("os.remove", "delete", str(tmp_path / "fill" / f"{i}.txt"))
    d = tmp_path / "bulk"
    for i in range(spill_cap + 10):
        ledger.blocked("os.remove", "delete", str(d / f"{i}.txt"))
    (ev,) = ledger._spill.values()
    assert ev["coarse"] and len(ev["paths"]) <= spill_cap and not ev["lost"]
    ledger.observe(str(tmp_path / "elsewhere" / "a.txt"), "r")
    assert ledger.material() == []
    ledger.observe(str(d / "never_removed.txt"), "r")  # 粗化：同目录的读也算（宁可误报）
    assert [o["op"] for o in ledger.material()] == ["os.remove"]
    # 目录数也超限 -> 丢失：此后任何观察都算命中
    lost = _ledger(tmp_path)
    for i in range(cap):
        lost.blocked("os.remove", "delete", str(tmp_path / "fill" / f"{i}.txt"))
    for i in range(spill_cap + 10):
        lost.blocked("os.remove", "delete", str(tmp_path / f"dir{i}" / "x.txt"))
    (ev,) = lost._spill.values()
    assert ev["lost"] and len(ev["paths"]) <= spill_cap
    lost.observe(str(tmp_path / "anything.txt"), "r")
    assert [o["op"] for o in lost.material()] == ["os.remove"]


def test_ledger_sees_os_path_exists_even_when_it_bypasses_os_stat(tmp_path, monkeypatch):
    """Windows 3.12+：`os.path.exists` 是 C 实现、不经 `os.stat`。这里模拟那个条件（POSIX 上也成立）。"""
    import os

    real_stat = os.stat

    def exists_without_os_stat(path):  # 不调 `os.stat` 名字，只用装钩之前捕获的原函数
        try:
            real_stat(path)
        except OSError:
            return False
        return True

    for name in ("exists", "isfile", "isdir", "lexists", "islink"):
        monkeypatch.setattr(os.path, name, exists_without_os_stat)
    f = tmp_path / "stale.csv"
    f.write_text("9")
    for name in ("exists", "isfile", "isdir", "lexists", "islink"):
        ledger = _ledger(tmp_path)
        uninstall = ledger.install()
        try:
            ledger.blocked("os.remove", "delete", str(f))
            assert ledger.material() == []
            getattr(os.path, name)(str(f))
            assert [o["op"] for o in ledger.material()] == ["os.remove"], name
        finally:
            uninstall()
        assert getattr(os.path, name) is exists_without_os_stat  # 卸载还原的是安装前那个


def test_ledger_exists_hooks_exempt_savefig_targets_and_restore_originals(tmp_path):
    import os

    originals = {n: getattr(os.path, n) for n in ("exists", "isfile", "isdir", "lexists", "islink")}
    ledger = _ledger(tmp_path)
    uninstall = ledger.install()
    ledger.blocked("savefig", "savefig", str(tmp_path / "a.png"))
    os.path.exists(str(tmp_path / "a.png"))
    assert ledger.material() == []
    uninstall()
    assert {n: getattr(os.path, n) for n in originals} == originals


# ====================================== 事实经过 pool / 探测 / 准备会话 / HTTP 之后仍是失败
def _worker_error() -> engine_pool.WorkerError:
    err = engine_pool.WorkerError("blocked", "tb", code=CODE)
    err.extra = {
        "operations": [{"op": "os.replace", "kind": "rename", "count": 1}],
        "required_execution": "preserve_file_semantics",
        "adapter_available": False,
    }
    return err


def test_http_error_payload_carries_the_code_and_the_basis():
    body = app_module._worker_error_payload(_worker_error())
    assert body["code"] == CODE
    assert body["params"]["required_execution"] == "preserve_file_semantics"
    assert body["params"]["adapter_available"] == "false"
    assert body["params"]["operations"] == "os.replace"


def test_probe_passes_the_code_through_instead_of_a_generic_probe_failure():
    out = engine_probe._error_from_worker(_worker_error(), entry="__main__", script="a.py")
    assert out["code"] == CODE
    assert engine_probe.entry_retry_allowed(_worker_error()) is False


def test_preparation_projection_reports_a_failure_not_a_success():
    """准备会话的派生只认「尝试终局 + 稳定码」：这个码落在 failed，没有任何一档会把它读成成功。"""
    vectors = json.loads(VECTORS.read_text("utf-8"))["vectors"]
    facts = copy.deepcopy(next(v for v in vectors if v["name"] == "script raised")["facts"])
    facts["attempt"]["error"] = {"code": CODE, "message": "blocked"}
    result = prepsession.derive(facts)
    assert result["outcome"] == {"kind": "failed", "code": CODE}
    assert result["figure_captured"] is False and result["execution_finished"] is False


OVERFLOW_READ_LAST = """\
import os

for i in range(260):
    with open(f"c{i}.tmp", "w") as f:
        f.write(str(i))
    os.replace(f"c{i}.tmp", f"c{i}.csv")
with open("c259.csv") as f:
    value = int(f.read())
"""

OVERFLOW_NEVER_READ = """\
import os

for i in range(260):
    with open(f"o{i}.tmp", "w") as f:
        f.write(str(i))
    os.remove(f"o{i}.tmp")
with open("seed.txt") as f:
    value = f.read()
"""


@needs_worker
def test_p1_overflow_operation_201_plus_then_readback_fails(figs):
    e = _blocked(figs, "ovf.py", OVERFLOW_READ_LAST)
    assert e.code == CODE, (e.code, str(e))
    assert [o["op"] for o in e.extra["operations"]] == ["os.replace"]


@needs_worker
def test_p1_hundreds_of_blocked_operations_never_read_back_still_succeed(figs):
    _build(figs, "ovn.py", OVERFLOW_NEVER_READ, project_mode=True)
