"""诊断包要答得出「渲染进程死在哪一句」（#435）。

#435 的诊断报告里有 24 条 `Traceback (most recent call last):`——只有这一行，异常
本身一个字都没带；而 worker 自己的日志（worker.log）根本不在包里。于是维护者
拿到的是一份长了一屏、信息量为零的报告，来回还得问一次。两处各一组用例：

* `recent_errors`：每段 traceback 与它收尾的那句异常配成一条；ERROR 行照旧。
* `render.worker_logs`：最近几份 worker.log 尾巴里的**证据块**进报告（按 mtime 取
  最近的，空的要标出来），且**过同一道脱敏**（主目录、密钥）。README 承诺包里
  不含脚本源码与数据——所以只认两种结构块：Python traceback（头 + `File "…", line N`
  帧行 + 收尾的异常行）与 faulthandler 的崩溃栈；块外的一切——脚本 print 的（哪怕
  长得像异常行）、帧下面那行源码、引擎自己的 `[guard]` 标记——都略去，只留计数。
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
#: 用例里的「当前项目」与「别的项目」：目录名前缀 = `pool.cache_digest(项目)`。
PROJECT = "/projects/this-one"
OTHER = "/projects/someone-elses"


def _session(root, script: str, text: str, age_s: float, *, project: str = PROJECT) -> None:
    d = root / f"{pool.cache_digest(project)}-{script}"
    d.mkdir()
    log = d / "worker.log"
    log.write_text(text, encoding="utf-8")
    stamp = time.time() - age_s
    os.utime(log, (stamp, stamp))


def test_worker_log_tails_take_the_most_recent_sessions_and_flag_empty_ones(tmp_path):
    _session(tmp_path, "old.py", "old stuff\n", age_s=3600)
    _session(tmp_path, "crashed.py", "Fatal Python error: Segmentation fault\n", age_s=10)
    _session(tmp_path, "silent.py", "", age_s=5)
    _session(tmp_path, "mid.py", "line1\nline2\n", age_s=60)
    got = diagnostics.worker_log_tails(tmp_path, project_dir=PROJECT, files=3, lines=60)
    d = pool.cache_digest(PROJECT)
    assert [g["session"] for g in got] == [f"{d}-silent.py", f"{d}-crashed.py", f"{d}-mid.py"]
    assert got[0]["empty"] is True and got[0]["tail"] == ""
    assert got[1]["empty"] is False
    assert "Segmentation fault" in got[1]["tail"]
    # 只有用户输出的那份：非空（进程留了话）、但一条证据都没有、略去两行
    assert got[2]["empty"] is False and got[2]["tail"] == "" and got[2]["omitted"] == 2
    assert all(g["modified"] for g in got)


def test_worker_log_tails_only_take_the_current_projects_sessions(tmp_path):
    """评审 #443 P1：诊断包是「当前这个项目」的，别的项目的脚本名与报错不出门。

    一次性重放目录（`_replay-<nonce>-<哈希>-<脚本>`）算本项目的；没打开项目时
    一份都不带——没有项目就没有「属于谁」这个判据。
    """
    tb = 'Traceback (most recent call last):\n  File "/p/a.py", line 1, in <module>\nKeyError: {}\n'
    _session(tmp_path, "mine.py", tb.format("'mine'"), age_s=1)
    _session(tmp_path, "theirs.py", tb.format("'SECRET_OTHER_PROJECT'"), age_s=0.5, project=OTHER)
    replay = tmp_path / f"_replay-0123abcd-{pool.cache_digest(PROJECT)}-mine.py"
    replay.mkdir()
    (replay / "worker.log").write_text(tb.format("'replay'"), encoding="utf-8")
    got = diagnostics.worker_log_tails(tmp_path, project_dir=PROJECT)
    names = {g["session"] for g in got}
    assert names == {f"{pool.cache_digest(PROJECT)}-mine.py", replay.name}, names
    assert "SECRET_OTHER_PROJECT" not in json.dumps(got)
    assert diagnostics.worker_log_tails(tmp_path, project_dir=None) == []
    assert diagnostics.worker_log_tails(tmp_path, project_dir="") == []


def test_worker_log_tails_keep_only_the_last_n_lines(tmp_path):
    """尾巴先按行数截、再抽证据：截掉了 traceback 头，剩下的帧行不成块。"""
    frames = [f'  File "/p/m{i}.py", line {i}, in f{i}' for i in range(199)]
    body = "Traceback (most recent call last):\n" + "\n".join(frames) + "\nKeyError: 'k'\n"
    _session(tmp_path, "long.py", body, age_s=1)
    (got,) = diagnostics.worker_log_tails(tmp_path, project_dir=PROJECT, files=3, lines=5)
    # 最后 5 行里没有头：4 条帧行 + 收尾句都没有来历，一条都不算
    assert got["tail"] == "" and got["omitted"] == 5
    (whole,) = diagnostics.worker_log_tails(tmp_path, project_dir=PROJECT, files=3, lines=500)
    lines = whole["tail"].splitlines()
    assert lines[0] == "Traceback (most recent call last):" and lines[-1] == "KeyError: 'k'"
    assert len(lines) == 201


WORKER_LOG = """\
loading E:/data/run7/temperature.csv
T = [301.2, 302.9, 305.1]     # 脚本自己 print 的数据
RuntimeError: patient-123     # 脚本 print 出来的、长得像异常行的用户数据
[guard] study results         # 脚本 print 出来的、长得像引擎标记的用户数据
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
usage: x.py [-h] --gff GFF --genome GENOME
"""

#: WORKER_LOG 里两个结构块之外的行数：脚本 print 的四行、matplotlib 噪音两行、
#: 引擎标记一行、帧下面的源码两行、usage 一行
WORKER_LOG_OMITTED = 10


def test_evidence_lines_keep_only_traceback_and_faulthandler_blocks():
    """README 承诺：不含脚本源码、不含数据。证据是**结构块**，不是行首长相。

    评审 #443 第二轮 P1：用户脚本的 stdout 也在这份日志里，`print("RuntimeError:
    patient-123")` / `print("[guard] …")` 与引擎的证据一模一样——按前缀放行就是把
    用户数据当证据带出门。异常行只在它收尾一段 traceback 时算数。
    """
    kept, omitted = diagnostics.evidence_lines(WORKER_LOG.splitlines())
    text = "\n".join(kept)
    # 留下的：两个结构块（帧里的绝对路径缩成 `…/文件名` / `…/site-packages/包/模块.py`）
    assert kept[:4] == [
        "Traceback (most recent call last):",
        '  File "…/fig.py", line 12, in <module>',
        '  File "…/site-packages/pandas/io/parsers.py", line 900, in read_csv',
        "FileNotFoundError: [Errno 2] No such file or directory: '…/missing.csv'",
    ], kept
    assert kept[4:] == [
        "Fatal Python error: Segmentation fault",
        "Current thread 0x00001234 (most recent call first):",
        '  File "…/site-packages/matplotlib/ft2font.py", line 40 in load',
        '  File "…/fig.py", line 20 in <module>',
        "Extension modules: numpy._core._multiarray_umath (total: 12)",
    ], kept
    # 略去的：脚本 print 的一切（含长得像异常行 / 标记行的）、帧下面的源码行、噪音、usage
    for absent in (
        "temperature.csv",
        "301.2",
        "patient-123",
        "study results",
        "[guard]",
        "df = pd.read_csv(secret_path)",
        "return _read(",
        "UserWarning",
        "plt.show()",
        "usage:",
        "E:/data",
        "/env/lib",
    ):
        assert absent not in text, absent
    assert omitted == WORKER_LOG_OMITTED


def test_a_lone_exception_looking_line_without_a_traceback_is_not_evidence():
    """没有 traceback 头在前面的「异常行」只是用户的一行输出。"""
    kept, omitted = diagnostics.evidence_lines(
        ["RuntimeError: patient-123", "ValueError: sample A failed", "SystemExit: 2"]
    )
    assert kept == [] and omitted == 3


def test_a_traceback_block_ends_at_its_exception_line():
    """收尾的异常行之后的东西不再算这个块的：用户接着 print 的数据不能搭车。"""
    kept, omitted = diagnostics.evidence_lines(
        [
            "Traceback (most recent call last):",
            '  File "/x/y.py", line 1, in <module>',
            "    run()",
            "KeyError: 'temperature'",
            "next sample: patient-124",
        ]
    )
    assert kept == [
        "Traceback (most recent call last):",
        '  File "…/y.py", line 1, in <module>',
        "KeyError: 'temperature'",
    ]
    assert omitted == 2


@pytest.mark.parametrize(
    "line, expect",
    [
        (
            r'  File "D:\ConfidentialStudy\fig.py", line 12, in <module>',
            '  File "…/fig.py", line 12, in <module>',
        ),
        (
            r'  File "\\wsl.localhost\Ubuntu\home\u\motif\plot.py", line 20 in <module>',
            '  File "…/plot.py", line 20 in <module>',
        ),
        (
            r"OSError: [WinError 5] 拒绝访问。: 'C:\Users\someone\x.png'",
            "OSError: [WinError 5] 拒绝访问。: '…/x.png'",
        ),
        ("error: the following arguments are required: -c/--clstr, -x/--metadata", None),
        ('  File "<frozen runpy>", line 88, in _run_code', None),
    ],
)
def test_absolute_paths_outside_home_are_shortened_too(line, expect):
    assert diagnostics.shorten_paths(line) == (expect if expect is not None else line)


def test_worker_log_tails_report_how_many_lines_were_left_out(tmp_path):
    _session(tmp_path, "fig.py", WORKER_LOG, age_s=1)
    (got,) = diagnostics.worker_log_tails(tmp_path, project_dir=PROJECT)
    assert got["omitted"] == WORKER_LOG_OMITTED
    assert got["empty"] is False
    assert "301.2" not in got["tail"] and "patient-123" not in got["tail"]


def test_worker_log_tails_survive_a_missing_cache_dir(tmp_path):
    assert diagnostics.worker_log_tails(tmp_path / "nowhere", project_dir=PROJECT) == []


def test_the_report_carries_redacted_worker_logs(client, tmp_path, monkeypatch):
    """报告里那一段过的是**同一道**脱敏：主目录换成 `~`、密钥抹掉；且只带当前项目的。"""
    monkeypatch.setattr(pool, "ENGINE_CACHE", tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    m.open_project(str(project))
    _session(
        tmp_path,
        "fig.py",
        f"loading {os.path.join(REAL_HOME, 'data.csv')}\n"
        f"Traceback (most recent call last):\n"
        f'  File "{os.path.join(REAL_HOME, "fig.py")}", line 3, in <module>\n'
        "    token = 'sk-abcdefghijklmnop'\n"
        "RuntimeError: bad key sk-abcdefghijklmnop\n"
        "Fatal Python error: Segmentation fault\n",
        age_s=1,
        project=str(project),
    )
    _session(tmp_path, "other.py", "RuntimeError: OTHER_PROJECT_SECRET\n", age_s=0.5, project=OTHER)
    z = zipfile.ZipFile(BytesIO(client.get("/api/diagnostics/bundle").data))
    report = json.loads(z.read("report.json"))
    logs = report["render"]["worker_logs"]
    assert [g["session"] for g in logs] == [f"{pool.cache_digest(str(project))}-fig.py"], logs
    assert "OTHER_PROJECT_SECRET" not in json.dumps(report)
    tail = logs[0]["tail"]
    assert "Segmentation fault" in tail
    assert REAL_HOME not in tail
    # 帧路径缩到文件名：主目录连出现的机会都没有（不靠 `~` 那道替换）
    assert 'File "…/fig.py", line 3' in tail
    assert "sk-abcdefghijklmnop" not in tail and "***" in tail
    assert "loading" not in tail, "脚本自己 print 的那行不进包"
    assert "token = " not in tail, "帧下面的源码行不进包（README：不含 Python 源代码）"
    assert logs[0]["omitted"] == 2
    # 「复制诊断」的文本形态也带着它（多行值按缩进展开）
    text = diagnostics.render_text(report)
    assert "render.worker_logs[0].tail:" in text
    assert "    Fatal Python error: Segmentation fault" in text
