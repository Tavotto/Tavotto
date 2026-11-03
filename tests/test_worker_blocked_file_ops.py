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


#: P1（#952 第四轮）：被拦删除之后 `x` 独占创建——直接运行时删除生效、`x` 成功；这里撞上还在的旧文件。
DELETE_THEN_EXCLUSIVE_CREATE_SWALLOWED = """\
import os
import matplotlib.pyplot as plt

n = 0
os.remove("cache.csv")
try:
    with open("cache.csv", "x") as f:
        f.write("2")
except FileExistsError:
    pass
with open("cache.csv") as f:
    n = int(f.read())
fig, ax = plt.subplots()
ax.plot([0, n])
"""

#: 同上但脚本捕获失败后**不再读**：失败本身就是与直接运行不同的结果（x 本该成功），仍判失败。
DELETE_THEN_EXCLUSIVE_CREATE_NO_READ = """\
import os
import matplotlib.pyplot as plt

os.remove("cache.csv")
try:
    open("cache.csv", "x").close()
    created = True
except FileExistsError:
    created = False
fig, ax = plt.subplots()
ax.plot([0, 1 if created else 2])
"""

#: P2（#952 第四轮）：移进已存在目录，只有 `archive/seed.txt` 受影响；目录里无关的文件读了照常成功。
MOVE_INTO_DIR_READ_UNRELATED = """\
import shutil
import matplotlib.pyplot as plt

shutil.move("seed.txt", "archive")
with open("archive/summary.csv") as f:
    n = len(f.read())
fig, ax = plt.subplots()
ax.plot([0, n])
"""

MOVE_INTO_DIR_READ_MOVED = MOVE_INTO_DIR_READ_UNRELATED.replace(
    "archive/summary.csv", "archive/seed.txt"
)
MOVE_INTO_DIR_READ_SOURCE = MOVE_INTO_DIR_READ_UNRELATED.replace("archive/summary.csv", "seed.txt")


@pytest.fixture
def figs(tmp_path):
    root = tmp_path / "figs"
    root.mkdir()
    (root / "cache.csv").write_text("1", encoding="utf-8")
    (root / "seed.txt").write_text("abc", encoding="utf-8")
    (root / "stale.csv").write_text("9", encoding="utf-8")
    (root / "archive").mkdir()
    (root / "archive" / "summary.csv").write_text("12345", encoding="utf-8")
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


MISSING_OK_THEN_EXISTS = """\
from pathlib import Path
import matplotlib.pyplot as plt

here = Path(__file__).parent
(here / "never_there.csv").unlink(missing_ok=True)
gone = not (here / "never_there.csv").exists()
fig, ax = plt.subplots()
ax.set_title(f"{gone}")
ax.plot([0, 1])
"""


@needs_worker
def test_deleting_a_missing_path_is_not_a_blocked_operation(figs):
    """`unlink(missing_ok=True)` 对不存在的路径：直跑要么删掉、要么 no-op，随后 exists() 两边都是 False——
    不记账，不是分叉（Codex #952）。不带 missing_ok 的删除不在此列（沙盒档里项目里没有≠直跑时没有）。"""
    worker, resp = _build(figs, "missing_ok.py", MISSING_OK_THEN_EXISTS, project_mode=True)
    assert resp["stems"], resp
    assert "[guard]" not in worker._log_tail()


#: 3.12 的非 strict `Path.resolve()` 末尾多 `self.stat()` 一次（3.13 去掉了）。脚本自己把它装回去，
#: 3.13 上也能复现：守卫若用 `resolve()` 判「在不在真实图库」，第二次删除时的那一下 stat
#: 就被账本记成「脚本回读了刚被拦下的路径」——发行档 lab（3.12）上 test_workdir_mode 三条红过。
RESOLVE_STATS_LIKE_312 = """\
import os
import pathlib
from pathlib import Path
import matplotlib.pyplot as plt

_resolve = pathlib.Path.resolve


def resolve(self, strict=False):
    out = _resolve(self, strict)
    if not strict:
        try:
            out.stat()
        except OSError:
            pass
    return out


pathlib.Path.resolve = resolve
Path("stale.csv").unlink()
os.remove("stale.csv")
fig, ax = plt.subplots()
ax.plot([0, 1])
"""


@needs_worker
def test_the_guards_own_path_check_is_not_a_readback_on_312(figs):
    worker, resp = _build(figs, "resolve312.py", RESOLVE_STATS_LIKE_312, project_mode=True)
    assert resp["stems"], resp
    assert worker._log_tail().count("[guard]") == 2  # 两次删除都被拦下
    assert (figs / "stale.csv").is_file()


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
    ledger.observe(str(f), "w")
    ledger.opened(str(f), "w")  # 脚本自己重写（打开成功）了：此后读到的是它写的
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
    ledger.opened(str(tmp_path / f"{cap + 1}.txt"), "w")  # 脚本自己重写（打开成功）
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
    # 被拦的操作可能只在某个入口里：换下一个入口照旧要试（都失败时报第一次的这个码）
    assert engine_probe.entry_retry_allowed(_worker_error()) is True


ENTRY_SPECIFIC_BLOCK = """\
import os
from pathlib import Path
import matplotlib.pyplot as plt

HERE = Path(__file__).parent


def main():
    os.replace(HERE / "cache.tmp", HERE / "cache.csv")
    value = (HERE / "cache.csv").read_text()
    plt.plot([0, int(value)])


def render():
    plt.plot([0, 1, 2])
"""


@needs_worker
def test_a_blocked_operation_inside_one_entry_does_not_stop_probing_the_next(figs):
    """`main()` 里被拦 + 回读（该入口失败），`render()` 是干净的图入口：探测要接着试到 render 并成功，
    不能因为第一个入口的被拦失败就判整份脚本无法渲染（Codex #952）。"""
    (figs / "cache.tmp").write_text("2", encoding="utf-8")
    (figs / "entry_specific.py").write_text(ENTRY_SPECIFIC_BLOCK, encoding="utf-8")
    workdir.set_mode(figs, workdir.MODE_PROJECT)
    result = engine_probe.probe(figs, "entry_specific.py", entries=["main", "render"])
    assert result["error"] is None, result
    assert result["entry"] == "render" and result["tried"] == ["main", "render"]


@needs_worker
def test_when_every_entry_fails_the_blocked_code_is_still_reported(figs):
    (figs / "cache.tmp").write_text("2", encoding="utf-8")
    (figs / "only_main.py").write_text(
        ENTRY_SPECIFIC_BLOCK.split("\n\ndef render")[0] + "\n", encoding="utf-8"
    )
    workdir.set_mode(figs, workdir.MODE_PROJECT)
    result = engine_probe.probe(figs, "only_main.py", entries=["main", "render"])
    assert result["error"]["code"] == CODE, result


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


# ============================================ 第三轮 Codex P1：账本看到的路径要是「脚本交出的那条」
def _savefig_then_read(call: str, readback: str, *, pre: str = "") -> str:
    return f"""\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path
from PIL import Image
{pre}
fig, ax = plt.subplots()
ax.plot([0, 1], [1, 2])
{call}
try:
    with Image.open({readback}) as im:
        size = list(im.size)
except Exception:
    size = [0, 0]
"""


@needs_worker
def test_p1_default_sandbox_ledger_sees_the_requested_path_not_the_fallback_rewrite(figs):
    """沙盒默认档：项目里已有旧 stage.png，相对读回退会把 `open("stage.png")` 改指到项目里那份——
    账本若装在回退之下，只看到改写后的路径，与 savefig 记下的沙盒路径永不相等，旧图被静默读走。"""
    e = _blocked(figs, "ex02_sandbox.py", EX02, project_mode=False)
    assert e.code == CODE, (e.code, str(e))
    assert [o["op"] for o in e.extra["operations"]] == ["savefig"]
    assert (figs / "stage.png").read_bytes() == b"stale-old-image"


@needs_worker
@pytest.mark.parametrize("project_mode", [False, True], ids=["sandbox", "project"])
@pytest.mark.parametrize(
    ("call", "readback"),
    [
        ('fig.savefig("stage")', '"stage.png"'),
        ('fig.savefig(Path("stage"))', '"stage.png"'),
        ('fig.savefig("stage.")', '"stage.png"'),
        ('fig.savefig(Path("stage.png"))', '"stage.png"'),
        ('fig.savefig("stage.png")', 'Path("stage.png")'),
        ('fig.savefig("stage.png")', '"./stage.png"'),
        ('fig.savefig(str(Path.cwd() / "stage.png"))', '"stage.png"'),
        ('fig.savefig("stage.png")', 'str(Path.cwd() / "stage.png")'),
    ],
)
def test_p1_savefig_target_forms_all_meet_the_readback(figs, project_mode, call, readback):
    """无扩展名补默认后缀、pathlib、`./`、绝对路径：写与读指向同一处就必须对上。"""
    e = _blocked(figs, "forms.py", _savefig_then_read(call, readback), project_mode=project_mode)
    assert e.code == CODE, (e.code, str(e))


@needs_worker
def test_p1_default_format_comes_from_rcparams(figs):
    (figs / "stage.svg").write_text("<svg/>", encoding="utf-8")
    src = _savefig_then_read(
        'fig.savefig("stage")', '"stage.svg"', pre='plt.rcParams["savefig.format"] = "svg"'
    ).replace("Image.open(", "open(")
    e = _blocked(figs, "rcfmt.py", src, project_mode=False)
    assert e.code == CODE, (e.code, str(e))


@needs_worker
def test_p1_explicit_format_does_not_get_a_suffix_but_the_literal_name_is_tracked(figs):
    """`format="png"` 配无后缀文件名：matplotlib 写的就是字面名 `stage`（不补后缀）。"""
    (figs / "stage").write_bytes(b"stale-literal")
    src = _savefig_then_read('fig.savefig("stage", format="png")', '"stage"').replace(
        "Image.open(", "open("
    )
    e = _blocked(figs, "explicit_fmt.py", src, project_mode=False)
    assert e.code == CODE, (e.code, str(e))


@needs_worker
def test_p1_bytesio_savefig_is_never_recorded(figs):
    src = _savefig_then_read(
        'buf = io.BytesIO(); fig.savefig(buf, format="png"); buf.seek(0)',
        "buf",
        pre="import io",
    )
    _build(figs, "bio.py", src, project_mode=False)  # 不抛即成功
    assert (figs / "stage.png").read_bytes() == b"stale-old-image"


@needs_worker
def test_p1_a_readback_of_an_unrelated_name_still_succeeds(figs):
    src = _savefig_then_read('fig.savefig("stage")', '"seed.txt"').replace("Image.open(", "open(")
    _build(figs, "unrelated.py", src, project_mode=False)


@pytest.mark.parametrize(
    ("target", "kwargs", "rc_format"),
    [
        ("stage", {}, None),
        ("stage.", {}, None),
        ("stage.png", {}, None),
        ("stage", {}, "svg"),
        ("stage", {"format": "png"}, None),
        ("stage.dat", {"format": "png"}, None),
        ("a.b/stage", {}, None),
    ],
)
@pytest.mark.parametrize("kind", [str, Path])
def test_savefig_effective_names_cover_what_matplotlib_really_writes(
    tmp_path, monkeypatch, target, kwargs, rc_format, kind
):
    """记账用的有效目标名与 matplotlib 真写出的文件逐个对照（不是照着注释猜）。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.b").mkdir()
    fig = plt.figure()
    try:
        with matplotlib.rc_context({"savefig.format": rc_format} if rc_format else {}):
            fig.savefig(kind(target), **kwargs)
            names = figcapture.savefig_effective_names(kind(target), kwargs, fig.canvas)
    finally:
        plt.close(fig)
    written = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()}
    assert len(written) == 1
    assert {Path(n).as_posix() for n in names} >= written, (names, written)


def test_savefig_effective_names_ignore_file_objects_and_keep_bytes_literal():
    import io

    assert figcapture.savefig_effective_names(io.BytesIO(), {}) == []
    assert figcapture.savefig_effective_names(b"stage", {}) == [b"stage"]
    assert figcapture.savefig_effective_names("stage", {}) == []  # 拿不到 canvas：一个候选都不记


# ====================================================== #952 第四轮
@needs_worker
def test_p1_failed_exclusive_create_after_a_blocked_delete_is_a_divergence(figs):
    """`x` 撞上被我们留下的旧文件而失败（直接运行里删除生效、`x` 成功）：清嫌疑要等 open 成功，所以回读被报告。"""
    e = _blocked(figs, "x_swallowed.py", DELETE_THEN_EXCLUSIVE_CREATE_SWALLOWED)
    assert e.code == CODE, (e.code, str(e))
    assert [o["op"] for o in e.extra["operations"]] == ["os.remove"]


@needs_worker
def test_p1_failed_exclusive_create_is_itself_an_observation_even_without_readback(figs):
    e = _blocked(figs, "x_noread.py", DELETE_THEN_EXCLUSIVE_CREATE_NO_READ)
    assert e.code == CODE, (e.code, str(e))


@needs_worker
def test_p1_successful_rewrite_after_a_blocked_delete_still_clears_the_taint(figs):
    worker, resp = _build(figs, "rewritten_ok.py", BLOCKED_THEN_REWRITTEN, project_mode=True)
    assert resp["stems"], resp


@needs_worker
def test_p2_move_into_existing_directory_only_taints_the_moved_child(figs):
    worker, resp = _build(figs, "mv_dir_ok.py", MOVE_INTO_DIR_READ_UNRELATED, project_mode=True)
    assert resp["stems"], resp
    assert worker._log_tail().count("[guard]") >= 1  # 守卫照旧在拦


@needs_worker
@pytest.mark.parametrize(
    ("name", "source"),
    [("mv_dir_moved.py", MOVE_INTO_DIR_READ_MOVED), ("mv_dir_src.py", MOVE_INTO_DIR_READ_SOURCE)],
)
def test_p2_reading_the_moved_child_or_the_source_still_fails(figs, name, source):
    e = _blocked(figs, name, source)
    assert e.code == CODE, (e.code, str(e))
    assert [o["op"] for o in e.extra["operations"]] == ["shutil.move"]


def test_ledger_failed_write_open_keeps_the_taint_and_x_failure_observes(tmp_path):
    f = tmp_path / "a.txt"
    ledger = _ledger(tmp_path)
    ledger.blocked("os.remove", "delete", str(f))
    ledger.observe(str(f), "w")  # 只是准备打开：不清
    ledger.observe(str(f), "r")
    assert ledger.material() != []
    x = _ledger(tmp_path)
    x.blocked("os.remove", "delete", str(f))
    x.exclusive_create_failed(str(f))
    assert [o["op"] for o in x.material()] == ["os.remove"]
    unrelated = _ledger(tmp_path)
    unrelated.blocked("os.remove", "delete", str(f))
    unrelated.exclusive_create_failed(str(tmp_path / "b.txt"))
    assert unrelated.material() == []


def test_ledger_mkdir_collision_only_counts_after_a_destructive_block(tmp_path):
    """`os.mkdir` 撞名与 `x` 打开同一判据：写入被拦（直跑后路径同样存在）之后的撞名两边一样失败，不是分叉；
    删除 / 删除目录被拦之后撞名才是（直跑里路径已经没了，Codex #952）。"""
    f = tmp_path / "cache.csv"
    write = _ledger(tmp_path)
    write.blocked("Path.write_text", "write", str(f))
    write.mkdir_collided(str(f))
    assert write.material() == []
    gone = _ledger(tmp_path)
    gone.blocked("shutil.rmtree", "delete_dir", str(tmp_path / "old_dir"))
    gone.mkdir_collided(str(tmp_path / "old_dir"))
    assert [o["op"] for o in gone.material()] == ["shutil.rmtree"]


def test_ledger_move_destination_resolves_like_shutil_move(tmp_path):
    ledger = _ledger(tmp_path)
    d = tmp_path / "archive"
    d.mkdir()
    assert ledger.move_destination(str(tmp_path / "seed.txt"), str(d)) == str(d / "seed.txt")
    assert ledger.move_destination(str(tmp_path / "seed.txt") + "/", str(d)) == str(d / "seed.txt")
    assert ledger.move_destination(str(tmp_path / "seed.txt"), str(tmp_path / "new.txt")) == str(
        tmp_path / "new.txt"
    )
    ledger.blocked("shutil.move", "rename", str(tmp_path / "seed.txt"), str(d / "seed.txt"))
    ledger.observe(str(d / "summary.csv"), "r")
    assert ledger.material() == []
    ledger.observe(str(d / "seed.txt"), "r")
    assert [o["op"] for o in ledger.material()] == ["shutil.move"]


# ====================================================== #952 第五轮
_PLOT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
"""

#: 绝对路径的阻断操作 + 相对路径的回读：沙盒档里读被回退改指到项目里那份，账本得同时认「请求的」与「实际读的」。
ABS_UNLINK_THEN_RELATIVE_READ = """\
from pathlib import Path
import os
{plot}
Path(__file__).parent.joinpath("cache.csv").unlink()
with open("{read}") as f:
    value = f.read()
"""


@needs_worker
def test_p1_absolute_block_then_relative_read_is_caught_in_the_default_sandbox(figs):
    src = ABS_UNLINK_THEN_RELATIVE_READ.format(plot=_PLOT, read="cache.csv")
    e = _blocked(figs, "abs_rel.py", src, project_mode=False)
    assert e.code == CODE, (e.code, str(e))
    assert [o["op"] for o in e.extra["operations"]] == ["Path.unlink"]


@needs_worker
def test_p1_absolute_savefig_target_then_relative_read_is_caught_in_the_default_sandbox(figs):
    """savefig 目标的 stat 不算观察（只认读内容），所以回退内部对有效路径的 `isfile` 探测救不了这条：
    账本必须自己把相对读解析到回退实际会读的项目路径。"""
    src = (
        _PLOT
        + 'from pathlib import Path\nfig.savefig(str(Path(__file__).resolve().parent / "stage.png"))\n'
        + 'with open("stage.png", "rb") as f:\n    data = f.read()\n'
    )
    e = _blocked(figs, "abs_savefig_rel.py", src, project_mode=False)
    assert e.code == CODE, (e.code, str(e))
    assert [o["op"] for o in e.extra["operations"]] == ["savefig"]


@needs_worker
def test_p1_absolute_block_then_relative_read_of_something_else_still_succeeds(figs):
    src = ABS_UNLINK_THEN_RELATIVE_READ.format(plot=_PLOT, read="stale.csv")
    worker, _ = _build(figs, "abs_rel_ok.py", src, project_mode=False)
    assert worker._log_tail().count("[guard]") >= 1  # 守卫照旧在拦


KW_REPLACE_THEN_READ = """\
import os
import shutil
{plot}
with open("cache.tmp", "w") as f:
    f.write("2")
{call}
with open("{read}") as f:
    value = f.read()
"""


@needs_worker
@pytest.mark.parametrize(
    ("call", "read", "op"),
    [
        ('os.replace("cache.tmp", dst="cache.csv")', "cache.csv", "os.replace"),
        ('os.replace(src="cache.tmp", dst="cache.csv")', "cache.csv", "os.replace"),
        ('os.rename("cache.tmp", dst="cache.csv")', "cache.csv", "os.rename"),
        ('shutil.move("seed.txt", dst="moved.txt")', "moved.txt", "shutil.move"),
        ('shutil.move(src="seed.txt", dst="moved.txt")', "seed.txt", "shutil.move"),
    ],
)
def test_p1_destination_given_by_keyword_is_recorded_and_read_back(figs, call, read, op):
    src = KW_REPLACE_THEN_READ.format(plot=_PLOT, call=call, read=read)
    e = _blocked(figs, "kw_dst.py", src)
    assert e.code == CODE, (e.code, str(e))
    assert [o["op"] for o in e.extra["operations"]] == [op]


@needs_worker
def test_p1_keyword_destination_with_an_unrelated_read_still_succeeds(figs):
    src = KW_REPLACE_THEN_READ.format(
        plot=_PLOT, call='os.replace("cache.tmp", dst="cache.csv")', read="stale.csv"
    )
    _build(figs, "kw_dst_ok.py", src, project_mode=True)


@needs_worker
def test_p2_extensionless_savefig_does_not_taint_an_unrelated_file_of_that_name(figs):
    (figs / "stage").write_text("unrelated", encoding="utf-8")
    src = _PLOT + 'fig.savefig("stage")\nwith open("stage") as f:\n    data = f.read()\n'
    _build(figs, "noext_ok.py", src, project_mode=False)
    # 补出来的后缀名仍然算：读 stage.png 才是读到了被拦的那份
    src2 = _PLOT + 'fig.savefig("stage")\nwith open("stage.png", "rb") as f:\n    data = f.read()\n'
    e = _blocked(figs, "noext_bad.py", src2, project_mode=False)
    assert e.code == CODE, (e.code, str(e))


SVG_DEFAULT_PLOT = _PLOT + "matplotlib.rcParams['savefig.format'] = 'svg'\n"


@needs_worker
def test_retro_open_of_the_non_default_png_name_is_not_blamed(figs):
    """#1076 Codex P2：SVG 默认下 `savefig('out'); open('out.png','rb')` 直接运行同样 FileNotFoundError，不能归因。"""
    src = (
        SVG_DEFAULT_PLOT
        + 'fig.savefig("out")\ntry:\n    open("out.png", "rb")\nexcept FileNotFoundError:\n    pass\n'
    )
    _build(figs, "open_spec.py", src, project_mode=False)


@needs_worker
def test_retro_ambiguous_png_candidate_is_not_blamed_when_the_default_format_is_svg(figs):
    """#1065 Codex P2：默认格式 svg 时 `savefig('out')` 写的是 out.svg；读 out.png 直接运行也 FileNotFoundError，
    推测的 png 候选不能被归因成守卫造成的 `safe_file_operation_blocked`。"""
    src = SVG_DEFAULT_PLOT + 'fig.savefig("out")\nimport os\nos.path.getsize("out.png")\n'
    with pytest.raises(engine_pool.WorkerError) as err:
        _build(figs, "amb_png.py", src, project_mode=False)
    assert err.value.code != CODE, (err.value.code, str(err.value))


@needs_worker
def test_retro_default_canvas_follows_rc_so_reading_the_rc_format_is_blamed(figs):
    src = SVG_DEFAULT_PLOT + 'fig.savefig("out")\nimport os\nos.path.getsize("out.svg")\n'
    e = _blocked(figs, "rc_svg.py", src, project_mode=False)
    assert e.code == CODE, (e.code, str(e))


CUSTOM_CANVAS_PNG = (
    SVG_DEFAULT_PLOT
    + "from matplotlib.backends.backend_agg import FigureCanvasAgg\n"
    + "class C(FigureCanvasAgg):\n"
    + "    def get_default_filetype(self):\n"
    + '        return "png"\n'
    + "C(fig)\n"
    + 'fig.savefig("out")\n'
    + "import os\n"
)


@needs_worker
def test_retro_custom_canvas_default_png_with_rc_svg_reading_svg_is_not_blamed(figs):
    """#1076 Codex P2：自定义 canvas 的缺省格式（png）≠ rcParams（svg）：直接运行写的是 out.png。"""
    with pytest.raises(engine_pool.WorkerError) as err:
        _build(
            figs,
            "cc_svg.py",
            CUSTOM_CANVAS_PNG + 'os.path.getsize("out.svg")\n',
            project_mode=False,
        )
    assert err.value.code != CODE, (err.value.code, str(err.value))


@needs_worker
def test_retro_custom_canvas_default_png_with_rc_svg_reading_png_is_blamed(figs):
    e = _blocked(
        figs, "cc_png.py", CUSTOM_CANVAS_PNG + 'os.path.getsize("out.png")\n', project_mode=False
    )
    assert e.code == CODE, (e.code, str(e))


@needs_worker
def test_retro_explicit_suffix_target_is_still_blamed(figs):
    src = SVG_DEFAULT_PLOT + 'fig.savefig("out.svg")\nimport os\nos.path.getsize("out.svg")\n'
    e = _blocked(figs, "def_svg.py", src, project_mode=False)
    assert e.code == CODE, (e.code, str(e))


@needs_worker
def test_retro_explicit_format_target_is_still_blamed(figs):
    src = _PLOT + 'fig.savefig("out", format="png")\nimport os\nos.path.getsize("out")\n'
    e = _blocked(figs, "def_fmt.py", src, project_mode=False)
    assert e.code == CODE, (e.code, str(e))


_OVERFLOW_PREFIX = (
    "import os, sys\n"
    "for i in range(260):\n"
    '    with open(f"q{i}.tmp", "w") as f:\n'
    "        f.write(str(i))\n"
    '    os.replace(f"q{i}.tmp", f"q{i}.csv")\n'
)


@needs_worker
@pytest.mark.parametrize("overflow", [False, True], ids=["inline", "after_overflow"])
def test_retro_speculative_png_is_not_blamed_for_a_failed_getsize(figs, overflow):
    """#1076 Codex P2：溢出 MAX_EVENTS 之后合并进溢出集合的事件，同样不能把推测候选归给守卫。"""
    head = (_OVERFLOW_PREFIX if overflow else "import os, sys\n") + SVG_DEFAULT_PLOT
    src = head + 'fig.savefig("out")\nos.path.getsize("out.png")\n'
    with pytest.raises(engine_pool.WorkerError) as err:
        _build(figs, "amb_all.py", src, project_mode=True)
    assert err.value.code != CODE, (err.value.code, str(err.value))


@needs_worker
def test_retro_definitive_candidate_is_still_blamed_after_overflow(figs):
    src = (
        _OVERFLOW_PREFIX + SVG_DEFAULT_PLOT + 'fig.savefig("out.svg")\nos.path.getsize("out.svg")\n'
    )
    e = _blocked(figs, "def_ovf.py", src, project_mode=True)
    assert e.code == CODE, (e.code, str(e))


RENAME_SPECULATIVE_SRC = (
    "import os\n"
    + SVG_DEFAULT_PLOT
    + 'fig.savefig("out")\n'
    + "try:\n"
    + '    os.rename("out.png", "moved.png")\n'
    + "except FileNotFoundError:\n"
    + "    pass\n"
    + "try:\n"
    + '    open("moved.png").close()\n'
    + "except FileNotFoundError:\n"
    + "    pass\n"
)


@needs_worker
def test_retro_rename_of_an_independently_existing_source_keeps_the_ordinary_event(figs):
    """#1076 Codex P2：项目里本来就有 out.png，SVG 默认的 savefig('out') 之后改名它——直接运行真的移走了文件，
    守卫拦下后读 moved.png 就是分叉，普通改名事件不能因为源名恰是推测候选而丢掉。"""
    (figs / "out.png").write_bytes(b"pre-existing")
    e = _blocked(figs, "ren_exist.py", RENAME_SPECULATIVE_SRC, project_mode=True)
    assert e.code == CODE, (e.code, str(e))
    assert [o["op"] for o in e.extra["operations"]] == ["os.rename"]


@needs_worker
def test_retro_rename_of_a_definitive_savefig_target_is_still_blamed(figs):
    src = RENAME_SPECULATIVE_SRC.replace('fig.savefig("out")', 'fig.savefig("out.png")')
    e = _blocked(figs, "ren_def.py", src, project_mode=True)
    assert e.code == CODE, (e.code, str(e))


def test_savefig_effective_names_skip_the_literal_name_when_matplotlib_appends_a_suffix():
    class Canvas:
        def get_default_filetype(self):
            return "png"

    class Broken:
        def get_default_filetype(self):
            raise RuntimeError("boom")

    class NotText:
        def get_default_filetype(self):
            return None

    c = Canvas()
    assert "stage" not in figcapture.savefig_effective_names("stage", {}, c)
    assert "stage" not in figcapture.savefig_effective_names("stage.", {}, c)
    assert "stage" in figcapture.savefig_effective_names("stage", {"format": "png"}, c)
    assert figcapture.savefig_effective_names("stage", {}, c) == ["stage.png"]
    assert figcapture.savefig_effective_names("stage.", {}, c) == ["stage.png"]
    for bad in (None, Broken(), NotText()):
        assert figcapture.savefig_effective_names("stage", {}, bad) == []


RMTREE_RECREATE = """\
import os
import shutil
from pathlib import Path
{plot}
shutil.rmtree("old_dir")
{mk}
with open("old_dir/result.csv", "w") as f:
    f.write("new")
with open("old_dir/{read}") as f:
    value = f.read()
"""


@needs_worker
@pytest.mark.parametrize(
    "mk",
    ['os.makedirs("old_dir", exist_ok=True)', 'Path("old_dir").mkdir(exist_ok=True)'],
    ids=["makedirs", "path_mkdir"],
)
def test_p2_reading_a_file_the_script_rewrote_under_a_blocked_directory_succeeds(figs, mk):
    src = RMTREE_RECREATE.format(plot=_PLOT, mk=mk, read="result.csv")
    _build(figs, "rmtree_ok.py", src, project_mode=True)


@needs_worker
def test_p2_an_unrewritten_file_under_the_blocked_directory_still_fails(figs):
    src = RMTREE_RECREATE.format(
        plot=_PLOT, mk='os.makedirs("old_dir", exist_ok=True)', read="x.txt"
    )
    e = _blocked(figs, "rmtree_bad.py", src)
    assert e.code == CODE, (e.code, str(e))
    assert [o["op"] for o in e.extra["operations"]] == ["shutil.rmtree"]


def test_ledger_rewritten_descendant_is_exempt_from_a_directory_taint(tmp_path):
    d = tmp_path / "out"
    ledger = _ledger(tmp_path)
    ledger.blocked("shutil.rmtree", "delete_dir", str(d))
    ledger.recreating_directory(str(d))
    ledger.opened(str(d / "result.csv"), "w")
    ledger.observe(str(d / "result.csv"), "r")
    assert ledger.material() == []
    ledger.observe(str(d / "other.csv"), "r")  # 没重写的后代照旧
    assert [o["op"] for o in ledger.material()] == ["shutil.rmtree"]


def test_ledger_a_later_block_revokes_the_rewrite_exemption(tmp_path):
    d = tmp_path / "out"
    ledger = _ledger(tmp_path)
    ledger.blocked("shutil.rmtree", "delete_dir", str(d))
    ledger.recreating_directory(str(d))
    ledger.opened(str(d / "result.csv"), "w")
    ledger.blocked("os.remove", "delete", str(d / "result.csv"))
    ledger.observe(str(d / "result.csv"), "r")
    assert "os.remove" in [o["op"] for o in ledger.material()]


def test_ledger_read_redirect_marks_the_effective_path_too(tmp_path):
    f = tmp_path / "proj" / "cache.csv"
    ledger = _ledger(tmp_path)
    ledger.blocked("Path.unlink", "delete", str(f))
    ledger.read_redirect = lambda p: str(f) if p == "cache.csv" else None
    ledger.observe("cache.csv", "w")  # 写模式不回退
    assert ledger.material() == []
    ledger.observe("cache.csv", "r+")  # 回退只认只读
    assert ledger.material() == []
    ledger.observe("cache.csv", "r")
    assert [o["op"] for o in ledger.material()] == ["Path.unlink"]


def test_ledger_recreating_a_blocked_directory_exempts_only_its_own_state_query(tmp_path):
    d = tmp_path / "out"
    ledger = _ledger(tmp_path)
    ledger.blocked("shutil.rmtree", "delete_dir", str(d))
    ledger.recreating_directory(str(d))
    ledger.observe_stat(str(d))
    assert ledger.material() == []
    ledger.observe(str(d / "old.txt"), "r")
    assert [o["op"] for o in ledger.material()] == ["shutil.rmtree"]
    other = _ledger(tmp_path)
    other.blocked("os.remove", "delete", str(d))  # 被拦的是文件：mkdir 不是重建
    other.recreating_directory(str(d))
    other.observe_stat(str(d))
    assert other.material() != []


# ====================================================== #952 第八轮
RMTREE_WRITE_CHILD = """\
import os
import shutil
from pathlib import Path
{plot}
shutil.rmtree("old_dir")
{mk}
with open("old_dir/result.csv", "w") as f:
    f.write("new")
with open("old_dir/result.csv") as f:
    value = f.read()
"""


@needs_worker
def test_r8_writing_a_child_of_a_blocked_directory_without_recreating_it_fails(figs):
    """直跑里父目录已被删、这次 open 会 FileNotFoundError；safe 因目录还在而成功——写入本身就是分叉。"""
    src = RMTREE_WRITE_CHILD.format(plot=_PLOT, mk="pass")
    e = _blocked(figs, "r8_nomk.py", src)
    assert e.code == CODE, (e.code, str(e))
    assert [o["op"] for o in e.extra["operations"]] == ["shutil.rmtree"]


@needs_worker
@pytest.mark.parametrize(
    "mk",
    ['os.makedirs("old_dir", exist_ok=True)', 'Path("old_dir").mkdir(parents=True, exist_ok=True)'],
    ids=["makedirs", "path_mkdir"],
)
def test_r8_recreating_the_directory_first_keeps_the_rewrite_exemption(figs, mk):
    _build(figs, "r8_mk_ok.py", RMTREE_WRITE_CHILD.format(plot=_PLOT, mk=mk), project_mode=True)


MKDIR_CAUGHT = """\
import os
import shutil
import matplotlib.pyplot as plt
{plot}
shutil.rmtree("old_dir")
try:
    os.mkdir("old_dir")
    ok = True
except FileExistsError:
    ok = False
fig, ax = plt.subplots()
ax.plot([0, 1 if ok else 2])
"""


@needs_worker
def test_r8_plain_mkdir_that_hits_the_retained_directory_is_a_divergence(figs):
    e = _blocked(figs, "r8_mkdir_bad.py", MKDIR_CAUGHT.format(plot=_PLOT))
    assert e.code == CODE, (e.code, str(e))


@needs_worker
def test_r8_makedirs_exist_ok_after_a_blocked_rmtree_still_succeeds(figs):
    src = MKDIR_CAUGHT.replace("os.mkdir(", "os.makedirs(").replace(
        'os.makedirs("old_dir")', 'os.makedirs("old_dir", exist_ok=True)'
    )
    _build(figs, "r8_makedirs_ok.py", src.format(plot=_PLOT), project_mode=True)


EXCLUSIVE_AFTER_WRITE = """\
import os
from pathlib import Path
import matplotlib.pyplot as plt
{plot}
Path("cache.csv").write_text("2")
try:
    open("cache.csv", "x").close()
except FileExistsError:
    pass
"""


@needs_worker
def test_r8_exclusive_create_failing_after_a_blocked_write_is_not_a_divergence(figs):
    """直跑里 write_text 写成功后文件也存在，`x` 两边都失败。"""
    _build(figs, "r8_x_write.py", EXCLUSIVE_AFTER_WRITE.format(plot=_PLOT), project_mode=True)


@needs_worker
def test_r8_exclusive_create_failing_after_a_blocked_delete_still_fails(figs):
    e = _blocked(figs, "r8_x_delete.py", DELETE_THEN_EXCLUSIVE_CREATE_NO_READ)
    assert e.code == CODE, (e.code, str(e))


def test_r8_ledger_exclusive_failure_shortcut_is_only_for_destructive_sources(tmp_path):
    f, g = tmp_path / "a.txt", tmp_path / "b.txt"
    w = _ledger(tmp_path)
    w.blocked("Path.write_text", "write", str(f))
    w.exclusive_create_failed(str(f))
    assert w.material() == []
    s = _ledger(tmp_path)
    s.blocked("os.rename", "rename", str(f), str(g), dests=[str(g)])
    s.exclusive_create_failed(str(g))  # 目的：直跑后存在，x 同样失败
    assert s.material() == []
    s.exclusive_create_failed(str(f))  # 源：直跑后不在，x 本该成功
    assert [o["op"] for o in s.material()] == ["os.rename"]
    v = _ledger(tmp_path)
    v.blocked("savefig", "savefig", str(f))
    v.exclusive_create_failed(str(f))
    assert v.material() == []


def test_r8_ledger_child_write_needs_a_recreated_directory(tmp_path):
    d = tmp_path / "out"
    bad = _ledger(tmp_path)
    bad.blocked("shutil.rmtree", "delete_dir", str(d))
    bad.opened(str(d / "r.csv"), "w")
    assert [o["op"] for o in bad.material()] == ["shutil.rmtree"]
    sub = _ledger(tmp_path)
    sub.blocked("shutil.rmtree", "delete_dir", str(d))
    sub.recreating_directory(str(d / "sub"))  # 非 parents：直跑里父目录不在
    assert [o["op"] for o in sub.material()] == ["shutil.rmtree"]
    deep = _ledger(tmp_path)
    deep.blocked("shutil.rmtree", "delete_dir", str(d))
    deep.recreating_directory(str(d / "sub"), parents=True)
    deep.opened(str(d / "sub" / "r.csv"), "w")
    deep.observe(str(d / "sub" / "r.csv"), "r")
    assert deep.material() == []
