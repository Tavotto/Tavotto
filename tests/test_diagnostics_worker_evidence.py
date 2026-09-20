"""诊断包要答得出「渲染进程死在哪一句」（#435）。

#435 的诊断报告里有 24 条 `Traceback (most recent call last):`——只有这一行，异常
本身一个字都没带；而 worker 自己的日志（worker.log）根本不在包里。于是维护者
拿到的是一份长了一屏、信息量为零的报告，来回还得问一次。两处各一组用例：

* `recent_errors`：每段 traceback 与它收尾的那句异常配成一条；ERROR 行照旧。
* `render.worker_logs`：最近几份 worker.log 尾巴里的**证据行**进报告（按 mtime 取
  最近的，空的要标出来），且**过同一道脱敏**（主目录、密钥）。README 承诺包里
  不含脚本源码与数据——所以只留 traceback 头 / `File "…", line N` 帧行 / 异常行 /
  faulthandler 的崩溃栈 / worker 自己的 `[guard]` 标记，脚本 print 的一切与帧下面
  那行源码都略去，只留计数。
"""

from __future__ import annotations

import json
import os
import time
import zipfile
from io import BytesIO

import pytest

from tavotto import app as m
from tavotto.engine import diagnostics, pool

REAL_HOME = os.path.expanduser("~")


@pytest.fixture
def client():
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()


# ------------------------------------------------------------ recent_errors
APP_LOG = """\
2026-09-20 10:47:02,622 ERROR tavotto: 引擎渲染失败: Figure 1: 渲染进程退出了
2026-09-20 10:49:00,000 WARNING tavotto: 素材扫描跳过 x.png（probe 失败）
Traceback (most recent call last):
  File "/app/tavotto/app.py", line 588, in _panels
    spec = engine_originalspec.asset_spec(p, kind, probe)
  File "/app/tavotto/engine/originalspec.py", line 40, in asset_spec
    return probe(path)
OSError: [WinError 5] 拒绝访问。: 'x.png'
2026-09-20 10:49:01,000 INFO tavotto: 继续
Traceback (most recent call last):
  File "/app/a.py", line 1, in <module>
    import PIL
ImportError: DLL load failed while importing _imaging: 找不到指定的模块。
""".splitlines()


def test_each_traceback_is_paired_with_its_exception_line():
    got = diagnostics.recent_errors(APP_LOG)
    assert got == [
        "2026-09-20 10:47:02,622 ERROR tavotto: 引擎渲染失败: Figure 1: 渲染进程退出了",
        "Traceback (most recent call last): → OSError: [WinError 5] 拒绝访问。: 'x.png'",
        "Traceback (most recent call last): → ImportError: DLL load failed while importing _imaging: "
        "找不到指定的模块。",
    ]


def test_frames_stay_out_of_the_error_list():
    """帧行（文件路径 + 源码）不进报告：读的人要的是那一句，脱敏面也更小。"""
    got = "\n".join(diagnostics.recent_errors(APP_LOG))
    assert "/app/tavotto/app.py" not in got
    assert "asset_spec" not in got


def test_a_traceback_cut_off_at_the_end_of_the_tail_is_kept_as_is():
    """尾巴正好截在 traceback 中间：没有收尾句就原样留头，不编一句。"""
    lines = APP_LOG[:6]  # 到第二个帧行为止，异常行还没到
    got = diagnostics.recent_errors(lines)
    assert got[-1] == "Traceback (most recent call last):"


def test_the_limit_keeps_the_most_recent_entries():
    lines = [f"2026-09-20 10:00:{i:02d},000 ERROR tavotto: e{i}" for i in range(40)]
    got = diagnostics.recent_errors(lines, limit=5)
    assert [ln.rsplit(" ", 1)[1] for ln in got] == ["e35", "e36", "e37", "e38", "e39"]


# ------------------------------------------------------------ worker.log 尾巴
def _session(root, name: str, text: str, age_s: float) -> None:
    d = root / name
    d.mkdir()
    log = d / "worker.log"
    log.write_text(text, encoding="utf-8")
    stamp = time.time() - age_s
    os.utime(log, (stamp, stamp))


def test_worker_log_tails_take_the_most_recent_sessions_and_flag_empty_ones(tmp_path):
    _session(tmp_path, "aaaa-old.py", "old stuff\n", age_s=3600)
    _session(tmp_path, "bbbb-crashed.py", "Fatal Python error: Segmentation fault\n", age_s=10)
    _session(tmp_path, "cccc-silent.py", "", age_s=5)
    _session(tmp_path, "dddd-mid.py", "line1\nline2\n", age_s=60)
    got = diagnostics.worker_log_tails(tmp_path, files=3, lines=60)
    assert [g["session"] for g in got] == ["cccc-silent.py", "bbbb-crashed.py", "dddd-mid.py"]
    assert got[0]["empty"] is True and got[0]["tail"] == ""
    assert got[1]["empty"] is False
    assert "Segmentation fault" in got[1]["tail"]
    assert all(g["modified"] for g in got)


def test_worker_log_tails_keep_only_the_last_n_lines(tmp_path):
    body = "\n".join(f"[guard] L{i}" for i in range(200)) + "\n"
    _session(tmp_path, "aaaa-long.py", body, age_s=1)
    (got,) = diagnostics.worker_log_tails(tmp_path, files=3, lines=5)
    assert got["tail"].splitlines() == [f"[guard] L{i}" for i in range(195, 200)]


WORKER_LOG = """\
loading E:/data/run7/temperature.csv
T = [301.2, 302.9, 305.1]     # 脚本自己 print 的数据
/env/lib/site-packages/matplotlib/pyplot.py:100: UserWarning: FigureCanvasAgg is non-interactive
  plt.show()
[guard] 跳过删除真实图库文件: E:/data/old.png
Traceback (most recent call last):
  File "E:/data/fig.py", line 12, in <module>
    df = pd.read_csv(secret_path)
  File "/env/lib/site-packages/pandas/io/parsers.py", line 900, in read_csv
    return _read(filepath_or_buffer, kwds)
FileNotFoundError: [Errno 2] No such file or directory: 'E:/data/missing.csv'
Fatal Python error: Segmentation fault

Current thread 0x00001234 (most recent call first):
  File "/env/lib/site-packages/matplotlib/ft2font.py", line 40 in load
  File "E:/data/fig.py", line 20 in <module>
Extension modules: numpy._core._multiarray_umath (total: 12)
"""


def test_evidence_lines_keep_errors_and_frames_but_not_prints_or_source():
    """README 承诺：不含脚本源码、不含数据。证据行是结构性的：帧、异常、崩溃栈。"""
    kept, omitted = diagnostics.evidence_lines(WORKER_LOG.splitlines())
    text = "\n".join(kept)
    # 留下的
    assert "Traceback (most recent call last):" in text
    assert 'File "E:/data/fig.py", line 12, in <module>' in text
    assert "FileNotFoundError: [Errno 2]" in text
    assert "Fatal Python error: Segmentation fault" in text
    assert "Current thread 0x00001234" in text
    assert 'File "E:/data/fig.py", line 20 in <module>' in text
    assert "[guard] 跳过删除真实图库文件" in text
    assert "Extension modules:" in text
    # 略去的：脚本 print 的数据、帧下面的源码行、matplotlib 的噪音
    assert "temperature.csv" not in text
    assert "301.2" not in text
    assert "df = pd.read_csv(secret_path)" not in text
    assert "return _read(" not in text
    assert "UserWarning" not in text
    assert "plt.show()" not in text
    assert omitted == 6


def test_worker_log_tails_report_how_many_lines_were_left_out(tmp_path):
    _session(tmp_path, "aaaa-fig.py", WORKER_LOG, age_s=1)
    (got,) = diagnostics.worker_log_tails(tmp_path)
    assert got["omitted"] == 6
    assert got["empty"] is False
    assert "301.2" not in got["tail"]


def test_worker_log_tails_survive_a_missing_cache_dir(tmp_path):
    assert diagnostics.worker_log_tails(tmp_path / "nowhere") == []


def test_the_report_carries_redacted_worker_logs(client, tmp_path, monkeypatch):
    """报告里那一段过的是**同一道**脱敏：主目录换成 `~`、密钥抹掉。"""
    monkeypatch.setattr(pool, "ENGINE_CACHE", tmp_path)
    _session(
        tmp_path,
        "eeee-fig.py",
        f"loading {os.path.join(REAL_HOME, 'data.csv')}\n"
        f"Traceback (most recent call last):\n"
        f'  File "{os.path.join(REAL_HOME, "fig.py")}", line 3, in <module>\n'
        "    token = 'sk-abcdefghijklmnop'\n"
        "RuntimeError: bad key sk-abcdefghijklmnop\n"
        "Fatal Python error: Segmentation fault\n",
        age_s=1,
    )
    z = zipfile.ZipFile(BytesIO(client.get("/api/diagnostics/bundle").data))
    report = json.loads(z.read("report.json"))
    logs = report["render"]["worker_logs"]
    assert logs and logs[0]["session"] == "eeee-fig.py"
    tail = logs[0]["tail"]
    assert "Segmentation fault" in tail
    assert REAL_HOME not in tail and "~" in tail
    assert "sk-abcdefghijklmnop" not in tail and "***" in tail
    assert "loading" not in tail, "脚本自己 print 的那行不进包"
    assert "token = " not in tail, "帧下面的源码行不进包（README：不含 Python 源代码）"
    assert logs[0]["omitted"] == 2
    # 「复制诊断」的文本形态也带着它（多行值按缩进展开）
    text = diagnostics.render_text(report)
    assert "render.worker_logs[0].tail:" in text
    assert "    Fatal Python error: Segmentation fault" in text
