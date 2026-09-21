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
Traceback (most recent call last):
  File "/app/a.py", line 2, in <module>
    raise Patient_123Error("cohort B")
Patient_123Error: cohort B
""".splitlines()


def test_each_traceback_is_paired_with_its_exception_line():
    """收尾那句与 worker 证据同一条规则：只留异常类型，ImportError 家族只留加载器形状
    （评审 #443 第七轮 P1：`ValueError: patient-123` 这种 message 是用户数据）。"""
    got = diagnostics.recent_errors(APP_LOG)
    assert got == [
        "2026-09-20 10:47:02,622 ERROR tavotto: 引擎渲染失败: Figure 1: 渲染进程退出了",
        "Traceback (most recent call last): → OSError: …",
        "Traceback (most recent call last): → ImportError: DLL load failed while importing mod:c2efbf3758",
        "Traceback (most recent call last): → exc:a7bb77bcf2: …",  # 用户定义的异常类名也不出门
    ]


def test_the_paired_exception_line_drops_its_message_and_error_lines_lose_their_paths():
    """评审 #443 第四轮 P1 → 第七轮 P1：收尾句里的路径先是与 worker 证据同一道缩写，
    第七轮起整句 message 都不带（`ValueError: patient-123` 缩路径也救不了）。ERROR 行是
    应用自己的日志语句，路径缩写、其余照旧。"""
    got = diagnostics.recent_errors(
        [
            "2026-09-20 10:00:00,000 ERROR tavotto: 导出失败: D:\\Study\\fig1.pdf 写不进去",
            "Traceback (most recent call last):",
            '  File "/app/x.py", line 1, in <module>',
            "OSError: [Errno 13] Permission denied: '/mnt/private-study/patient-a/data.csv'",
            "Traceback (most recent call last):",
            '  File "/app/x.py", line 2, in <module>',
            "ValueError: patient-123",
        ]
    )
    assert got == [
        "2026-09-20 10:00:00,000 ERROR tavotto: 导出失败: …/file:6fe886ec6b.pdf 写不进去",
        "Traceback (most recent call last): → OSError: …",
        "Traceback (most recent call last): → ValueError: …",
    ]
    assert "patient" not in "\n".join(got)


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
#: 一段**完整**的 faulthandler 块：头 + 线程行 + 帧 + 收尾。
FH_BLOCK = (
    "Fatal Python error: Segmentation fault\n\n"
    "Current thread 0x0000000201ac3f80 (most recent call first):\n"
    '  File "/p/crash.py", line 6 in <module>\n'
    "Extension modules: numpy._core._multiarray_umath (total: 4)\n"
)

#: 用例里的「当前项目」与「别的项目」：目录名前缀 = `pool.cache_digest(项目)`。
PROJECT = "/projects/this-one"
OTHER = "/projects/someone-elses"


def _sid(script: str, *, raw: bool = False) -> str:
    """报告里的会话 id：目录名的 sha1 前 12 位（`raw=True` 时 `script` 就是目录名）。"""
    import hashlib

    name = script if raw else f"{pool.cache_digest(PROJECT)}-{script}"
    return "session:" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]


def _session(root, script: str, text: str, age_s: float, *, project: str = PROJECT) -> None:
    d = root / f"{pool.cache_digest(project)}-{script}"
    d.mkdir()
    log = d / "worker.log"
    log.write_text(text, encoding="utf-8")
    stamp = time.time() - age_s
    os.utime(log, (stamp, stamp))


def test_worker_log_tails_take_the_most_recent_sessions_and_flag_empty_ones(tmp_path):
    _session(tmp_path, "old.py", "old stuff\n", age_s=3600)
    _session(tmp_path, "crashed.py", FH_BLOCK, age_s=10)
    _session(tmp_path, "silent.py", "", age_s=5)
    _session(tmp_path, "mid.py", "line1\nline2\n", age_s=60)
    got = diagnostics.worker_log_tails(tmp_path, project_dir=PROJECT, files=3, lines=60)
    assert [g["session"] for g in got] == [_sid("silent.py"), _sid("crashed.py"), _sid("mid.py")]
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
    assert names == {_sid("mine.py"), _sid(replay.name, raw=True)}, names
    assert {g["replay"] for g in got} == {True, False}
    assert "SECRET_OTHER_PROJECT" not in json.dumps(got)
    # README 承诺文件名一律换成不可逆哈希：脚本名不出现在任何字段里
    assert "mine.py" not in json.dumps(got)
    assert diagnostics.worker_log_tails(tmp_path, project_dir=None) == []
    assert diagnostics.worker_log_tails(tmp_path, project_dir="") == []


def test_worker_log_tails_take_whole_blocks_from_the_end_within_the_budget(tmp_path):
    """按块截尾（评审 #443）：预算装得下几块就取几块，最后那一块再长也整块要。

    先按行数截、再抽证据的老做法会把一段 80 行的崩溃栈截成没有头的帧行——
    状态机一条都不认，报告里就没有崩溃位置。
    """
    tb = lambda tag, n: (  # noqa: E731 —— 一段 n 帧的 traceback
        "Traceback (most recent call last):\n"
        + "".join(f'  File "/p/{tag}{i}.py", line {i}, in f{i}\n' for i in range(n))
        + f"KeyError: '{tag}'\n"
    )
    _session(tmp_path, "long.py", tb("old", 3) + tb("mid", 3) + tb("new", 199), age_s=1)
    (got,) = diagnostics.worker_log_tails(tmp_path, project_dir=PROJECT, files=3, lines=5)
    lines = got["tail"].splitlines()
    # 预算 5 行装不下 201 行的那块，但它是最新的一块：整块都要，前面两块不要
    # 收尾行只留类型（message 是自由文本，不出门）；哪一块靠帧数分辨
    assert lines[0] == "Traceback (most recent call last):" and lines[-1] == "KeyError: …"
    assert len(lines) == 201 and "file:dfa9ef2ab3.py" in lines[-2]
    (mid,) = diagnostics.worker_log_tails(tmp_path, project_dir=PROJECT, files=3, lines=206)
    assert mid["tail"].count("Traceback (most recent call last):") == 2  # new + mid，old 装不下
    assert "file:9153e9b2f1.py" in mid["tail"] and "file:bb6748c18d.py" not in mid["tail"]


def test_a_crash_stack_longer_than_the_line_budget_still_keeps_its_header(tmp_path):
    """faulthandler 一段 80 帧的栈：报告里必须还是从 `Fatal Python error` 开头。"""
    frames = "".join(f'  File "/env/site-packages/mpl/m{i}.py", line {i} in f\n' for i in range(80))
    body = (
        "noise line from the script\n"
        "Fatal Python error: Segmentation fault\n\n"
        "Current thread 0x1 (most recent call first):\n"
        + frames
        + "Extension modules: x (total: 1)\n"
    )
    _session(tmp_path, "deep.py", body, age_s=1)
    (got,) = diagnostics.worker_log_tails(tmp_path, project_dir=PROJECT)
    lines = got["tail"].splitlines()
    assert lines[0] == "Fatal Python error: Segmentation fault"
    assert lines[-1] == "Extension modules: … (total: 1)"
    assert len(lines) == 83 and got["omitted"] == 1


def test_a_crash_block_longer_than_the_scan_window_still_starts_at_its_header(tmp_path):
    """评审 #443 第六轮 P2：`all_threads=True` 遇上几条深栈线程，一段崩溃栈能超过 400 行；
    扫描窗至少回溯到最近一个崩溃头，不然唯一的头被切掉、后面的帧一条都不算。"""
    frames = "".join(
        f'  File "/env/site-packages/mpl/m{i}.py", line {i} in f\n' for i in range(450)
    )
    body = "noise\n" * 50 + "Fatal Python error: Segmentation fault\n\n"
    body += "Current thread 0x1 (most recent call first):\n" + frames
    body += "Extension modules: x (total: 1)\n"
    _session(tmp_path, "deep.py", body, age_s=1)
    (got,) = diagnostics.worker_log_tails(tmp_path, project_dir=PROJECT)
    lines = got["tail"].splitlines()
    assert lines[0] == "Fatal Python error: Segmentation fault"
    assert lines[-1] == "Extension modules: … (total: 1)"
    assert len(lines) == 453


def test_chain_separators_count_only_between_two_traceback_blocks():
    """链接语要逐字相同、且夹在两段 traceback 之间；用户 print 的变体不算。"""
    chained = [
        "Traceback (most recent call last):",
        '  File "/x/a.py", line 1, in <module>',
        "KeyError: 'k'",
        "",
        "During handling of the above exception, another exception occurred:",
        "",
        "Traceback (most recent call last):",
        '  File "/x/a.py", line 3, in <module>',
        "RuntimeError: second",
    ]
    kept, omitted = diagnostics.evidence_lines(chained)
    assert kept == [
        "Traceback (most recent call last):",
        '  File "…/file:bb88d7506c.py", line 1',
        "KeyError: …",
        "During handling of the above exception, another exception occurred:",
        "Traceback (most recent call last):",
        '  File "…/file:bb88d7506c.py", line 3',
        "RuntimeError: …",
    ]
    assert omitted == 0
    # 三种不算：没有 traceback 在前 / 带尾巴的变体 / 后面没有跟着第二段
    assert diagnostics.evidence_lines(
        ["During handling of the above exception, another exception occurred:"]
    ) == ([], 1)
    assert diagnostics.evidence_lines(
        chained[:3] + ["During handling of the above exception: patient-123"]
    ) == (kept[:3], 1)
    assert diagnostics.evidence_lines(chained[:5]) == (kept[:3], 1)


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
    # 留下的：两个结构块。帧只留「路径 + 行号」（函数名是用户的标识符，评审 #443 第七轮），
    # 路径缩成 `…/file:<哈希>.py`，已知第三方包只多留一个包名 `…/site-packages/包/file:<哈希>.py`
    # （包名之后的每一段都可能是用户起的，评审 #443 第八轮）；崩溃头只放行 CPython 自己的
    # 故障名；`Extension modules` 只留计数（模块名可能是用户的包）。
    assert kept[:4] == [
        "Traceback (most recent call last):",
        '  File "…/file:07dcc94317.py", line 12',
        '  File "…/site-packages/pandas/file:c28586813a.py", line 900',
        "FileNotFoundError: …",  # message 是自由文本（可能带数据），只留类型
    ], kept
    assert kept[4:] == [
        "Fatal Python error: Segmentation fault",
        "Current thread 0x00001234 (most recent call first):",
        '  File "…/site-packages/matplotlib/file:6ad788fb3e.py", line 40',
        '  File "…/file:07dcc94317.py", line 20',
        "Extension modules: … (total: 12)",
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
        "missing.csv",
        "read_csv",  # 帧里的函数名
        "in load",
        "<module>",
        "_multiarray_umath",  # Extension modules 的名单
        "parsers.py",  # 库里的文件名也哈希：包名之后的每一段都可能是用户起的
        "ft2font",
    ):
        assert absent not in text, absent
    assert omitted == WORKER_LOG_OMITTED


def test_a_lone_exception_looking_line_without_a_traceback_is_not_evidence():
    """没有 traceback 头在前面的「异常行」只是用户的一行输出。"""
    kept, omitted = diagnostics.evidence_lines(
        ["RuntimeError: patient-123", "ValueError: sample A failed", "SystemExit: 2"]
    )
    assert kept == [] and omitted == 3


@pytest.mark.parametrize(
    "lines",
    [
        # 只有一个头：用户 print 出来的
        ["Fatal Python error: patient-123"],
        ["Fatal Python error: Segmentation fault"],
        # 头 + 线程行但没有帧
        ["Fatal Python error: Segmentation fault", "Current thread 0x1 (most recent call first):"],
        # traceback 头后面跟一行数据（不是合法的异常行）
        ["Traceback (most recent call last):", "next sample: patient-124"],
        # traceback 头 + 合法收尾但一帧都没有
        ["Traceback (most recent call last):", "KeyError: 'k'"],
        # 头 + 帧但没有收尾
        ["Traceback (most recent call last):", '  File "/x/a.py", line 1, in <module>'],
    ],
)
def test_incomplete_blocks_are_not_evidence(lines):
    """评审 #443 第四轮 P1：块要凑齐「头 + 帧 + 收尾」/「头 + 线程 + 帧」才算，
    否则整块按用户输出略去——README 承诺不含脚本输出，一个头不能当通行证。"""
    kept, omitted = diagnostics.evidence_lines(lines)
    assert kept == [], kept
    assert omitted == len(lines)


def test_user_print_exc_blocks_lose_their_free_text_message():
    """评审 #443 第五轮 P1：`except: traceback.print_exc()` 打出来的块结构与引擎的
    一模一样，来历分不出来——能保证的只有 message 不出门。ImportError 家族例外：
    那句是加载器说的（缺哪个模块 / 哪个 DLL 加载失败），正是排障要的。
    第九轮 P1：类型名也是用户能起的（`class Patient_123Error(Exception)`），只放行
    本进程 builtins 里的异常类，其余 `exc:<哈希>`；第十一轮：点分名的 `__module__` 也是用户
    能改的，只留来自闭集的包名（`numpy.exc:<哈希>`）。"""
    kept, _ = diagnostics.evidence_lines(
        [
            "Traceback (most recent call last):",
            '  File "/x/a.py", line 1, in <module>',
            "KeyError: 'patient-123 / 2026-09-20 / cohort B'",
            "Traceback (most recent call last):",
            '  File "/x/a.py", line 2, in <module>',
            "ModuleNotFoundError: No module named 'Bio'",
            "Traceback (most recent call last):",
            '  File "/x/a.py", line 3, in <module>',
            "ImportError: DLL load failed while importing _imaging: 找不到指定的模块。",
            "Traceback (most recent call last):",
            '  File "/x/a.py", line 4, in <module>',
            "matplotlib.units.ConversionError: Failed to convert value(s) to axis units: 'patient'",
            "Traceback (most recent call last):",
            '  File "/x/a.py", line 5, in <module>',
            "ImportError: patient-123 confidential cohort",  # 用户自己 raise 的
            "Traceback (most recent call last):",
            '  File "/x/a.py", line 6, in <module>',
            "ImportError: cannot import name 'foo' from 'pkg.mod' (/env/pkg/mod.py)",
            # 用户自己定义的异常类：`class Patient_123Error(Exception)`，在 __main__ 里定义的
            # 与 builtins 一样不带模块前缀（评审 #443 第九轮）；用户模块里的带前缀
            "Traceback (most recent call last):",
            '  File "/x/a.py", line 7, in <module>',
            "Patient_123Error: cohort B failed",
            "Traceback (most recent call last):",
            '  File "/x/a.py", line 8, in <module>',
            "mystudy.CohortError: patient 123",
            "Traceback (most recent call last):",
            '  File "/x/a.py", line 9, in <module>',
            "Patient_123Error",
            "Traceback (most recent call last):",
            '  File "/x/a.py", line 10, in <module>',
            "KeyboardInterrupt",
            "Traceback (most recent call last):",
            '  File "/x/a.py", line 11, in <module>',
            "numpy.exceptions.AxisError: axis 2 is out of bounds",
            # `__module__` 是用户能改的：`Patient_123Error.__module__ = "numpy"`（评审 #443 第十一轮）
            "Traceback (most recent call last):",
            '  File "/x/a.py", line 12, in <module>',
            "numpy.Patient_123Error: cohort B",
        ]
    )
    closers = [ln for ln in kept if not ln.startswith(("Traceback", "  File"))]
    assert closers == [
        "KeyError: …",
        # 加载器形状保留，里面的名字按闭集放行（标准库 / 已知包的顶层名）或哈希：
        # `Bio` 不在闭集里（第十二轮：`raise ModuleNotFoundError("No module named 'patient_123'")`
        # 与真缺包的形状一模一样），读的人拿候选名哈希就能对上
        "ModuleNotFoundError: No module named 'mod:b31fc969b4'",
        # 只保留到模块名，后面的操作系统文案不带；扩展模块名同样哈希
        "ImportError: DLL load failed while importing mod:c2efbf3758",
        # 点分名只留来自闭集的包名，其余哈希（库的异常类可枚举，读的人对得上）
        "matplotlib.exc:c9a2cd5a8f: …",
        "ImportError: …",  # 形状对不上加载器的：只留类型（评审 #443 第六轮）
        "ImportError: cannot import name 'mod:0beec7b5ea' from 'mod:a71f3f77bd'",
        # 类型名不是 builtins 的异常类：哈希
        "exc:a7bb77bcf2: …",
        "exc:3b0b364cbf: …",
        "exc:a7bb77bcf2",
        "KeyboardInterrupt",
        "numpy.exc:f3a4e1053c: …",
        "numpy.exc:ee491de013: …",  # 借了 numpy 的名字也只剩包名
    ], closers
    assert "Patient" not in "\n".join(kept) and "mystudy" not in "\n".join(kept)
    assert "ConversionError" not in "\n".join(kept)
    assert "Bio" not in "\n".join(kept) and "_imaging" not in "\n".join(kept)


@pytest.mark.parametrize(
    "message, expect",
    [
        ("No module named 'numpy'", "No module named 'numpy'"),  # 已知包的顶层名
        ("No module named 'os'", "No module named 'os'"),  # 标准库
        ("No module named 'patient_123'", "No module named 'mod:0be612298b'"),
        # 顶层名在闭集里，后面的段仍是用户能起的：`os.patient`
        ("No module named 'os.patient'", "No module named 'os.mod:e87388d0a7'"),
        (
            "No module named 'json.patient'; 'json' is not a package",
            "No module named 'json.mod:a50cee6e34'; 'json' is not a package",
        ),
        (
            "cannot import name 'axes' from 'matplotlib' (x)",
            "cannot import name 'mod:495f723e9f' from 'matplotlib'",
        ),
        (
            "DLL load failed while importing _imaging: 找不到指定的模块。",
            "DLL load failed while importing mod:c2efbf3758",
        ),
    ],
)
def test_loader_messages_keep_their_shape_but_only_closed_set_names(message, expect):
    """评审 #443 第十二轮 P1：加载器文案的形状是闭集，里面的名字不是——用户 `raise
    ModuleNotFoundError("No module named 'patient_123'")` 再 `print_exc()` 一模一样。名字只在是
    标准库 / 已知第三方包的**顶层名**时原样，点分名的余下部分与不认识的名字 `mod:<哈希>`。"""
    kept, _ = diagnostics.evidence_lines(
        [
            "Traceback (most recent call last):",
            '  File "/x/a.py", line 1, in <module>',
            f"ImportError: {message}",
        ]
    )
    assert kept[-1] == f"ImportError: {expect}", kept


@pytest.mark.parametrize(
    "path, expect",
    [
        ("/mnt/private/file.patient", "…/file:3938b8c339"),  # 扩展名也是用户起的：不在闭集就不带
        ("/mnt/x/a.PY", "…/file:41a21914c8.py"),  # 闭集里的按小写带出
        ("/x/data.csv", "…/file:1aa5784d52.csv"),
        ("/x/noext", "…/file:6bdce06546"),
        ("/x/.hidden", "…/file:92f73832be"),  # 没有词干：不算扩展名
    ],
)
def test_file_extensions_are_kept_only_from_a_closed_set(path, expect):
    """评审 #443 第十二轮 P1：`len(ext) <= 8` 放行了 `.patient` 这种用户起的后缀。"""
    assert diagnostics.shorten_paths(f'"{path}"') == f'"{expect}"'


@pytest.mark.parametrize(
    "line, expect",
    [
        (
            r'  File "C:\Clinical Trial\patient-a\plot.py", line 3, in <module>',
            '  File "…/file:a19de1d7c3.py", line 3, in <module>',
        ),
        (
            "ImportError: cannot load '/mnt/clinical trial/patient a/lib.so'",
            "ImportError: cannot load '…/file:68de22c57c.so'",
        ),
        (r"OSError: D:\Study Data\a.pdf 写不进去", "OSError: …/file:0e6c2a272c.pdf 写不进去"),
        (
            '  File "/env/lib/python3.11/site-packages/matplotlib/ft2font.py", line 40 in load',
            '  File "…/site-packages/matplotlib/file:6ad788fb3e.py", line 40 in load',
        ),
    ],
)
def test_paths_with_spaces_are_shortened_as_a_whole(line, expect):
    """评审 #443 第五轮 P1：带空格的路径要整体缩，不能在第一个空格处断掉留下后半截。"""
    assert diagnostics.shorten_paths(line) == expect


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
        '  File "…/file:8332f20adb.py", line 1',
        "KeyError: …",
    ]
    assert omitted == 2


@pytest.mark.parametrize(
    "line, expect",
    [
        # traceback 帧：`, in 函数名` 不带
        (
            '  File "/mnt/study/plot.py", line 12, in analyze_patient_123',
            '  File "…/file:a19de1d7c3.py", line 12',
        ),
        # faulthandler 帧：`in 函数名` 不带；两种写法归成一种
        (
            '  File "/mnt/study/plot.py", line 12 in analyze_patient_123',
            '  File "…/file:a19de1d7c3.py", line 12',
        ),
        # 相对名 / 虚拟名也是用户能起的字符串（`exec(compile(src, "patient_123.py", "exec"))`，
        # 评审 #443 第十轮）：与绝对路径同一条规则；CPython 的伪文件名可枚举，哈希了也对得上
        ('  File "patient_123.py", line 3, in <module>', '  File "…/file:b86e17862c.py", line 3'),
        ('  File "<string>", line 1, in <module>', '  File "…/file:b851f3d271", line 1'),
        ('  File "<frozen runpy>", line 88, in _run_code', '  File "…/file:19f15fd060", line 88'),
        # 已知第三方包：多留一个包名，文件名照样哈希，函数名同样不带
        (
            '  File "/env/lib/python3.11/site-packages/matplotlib/ft2font.py", line 40 in load',
            '  File "…/site-packages/matplotlib/file:6ad788fb3e.py", line 40',
        ),
    ],
)
def test_exported_frames_are_rebuilt_from_path_and_line_only(line, expect):
    """评审 #443 第七轮 P1：帧行整行过 `shorten_paths` 只换掉了文件名，`in analyze_patient_123`
    原样出门；改成从解析出来的路径与行号**重建**一行，正则没认的后半截一律不带。
    第十轮 P1：文件名不经 `shorten_paths`（它只认绝对路径），相对名 / 虚拟名一样哈希。"""
    kept, _ = diagnostics.evidence_lines(
        ["Traceback (most recent call last):", line, "KeyError: 'k'"]
    )
    assert kept == ["Traceback (most recent call last):", expect, "KeyError: …"], kept
    assert "patient" not in expect and "_run_code" not in expect


@pytest.mark.parametrize(
    "path, expect",
    [
        # 一段叫 tavotto / site-packages 证明不了什么：后面跟的不是已知包 / 引擎文件就整个哈希
        ("/mnt/tavotto/private-study/patient.py", "…/file:e85d6c19a0.py"),
        ("/mnt/site-packages/cohort/patient.py", "…/file:e85d6c19a0.py"),
        ("/env/site-packages/my_private_pkg/patient.py", "…/file:e85d6c19a0.py"),
        ("/x/tavotto/engine/patient.py", "…/file:e85d6c19a0.py"),
        # 已知包：只多留包名这一位来自闭集的信息，包名之后的每一段（子目录、文件名）都哈希——
        # 用户完全可以把项目放在 `…/site-packages/numpy/private-study/`（评审 #443 第八轮）
        (
            "/mnt/site-packages/numpy/private-study/patient.py",
            "…/site-packages/numpy/file:e85d6c19a0.py",
        ),
        (
            "/env/lib/site-packages/numpy/core/_methods.py",
            "…/site-packages/numpy/file:e6995c4ddf.py",
        ),
        ("/env/site-packages/numpy/patient a/x.py", "…/site-packages/numpy/file:99a930f602.py"),
        ("/env/site-packages/numpy", "…/site-packages/numpy"),
        # 引擎目录里真实存在的文件：原样
        ("/opt/Tavotto/tavotto/engine/worker.py", "…/tavotto/engine/worker.py"),
    ],
)
def test_package_paths_keep_only_the_package_name_from_a_closed_set(path, expect):
    """评审 #443 第七、八轮 P1：以前路径里有一段叫 `site-packages` / `tavotto` 就把后面整串
    原样保留；第七轮改成查已知包名，第八轮指出 `/mnt/site-packages/numpy/private-study/patient.py`
    照样过——包名之后的每一段都是用户能起的名字。现在出门的只有「属于哪个已知库」这一位
    信息（闭集 `_KNOWN_SITE_PACKAGES`），文件名与用户文件一样哈希：库的文件名公开可枚举，
    读的人拿包里的文件名逐个哈希就能对上；`tavotto/engine/` 之后仍要是引擎目录里真实存在的
    文件名（`_ENGINE_FILES`）。这个进程里未必装着 matplotlib（它在 worker 的解释器里），
    按真实安装根验不可靠。"""
    assert diagnostics.shorten_paths(f'"{path}"') == f'"{expect}"'
    assert "patient" not in diagnostics.shorten_paths(f'"{path}"')


@pytest.mark.parametrize(
    "header, expect",
    [
        ("Fatal Python error: Segmentation fault", "Fatal Python error: Segmentation fault"),
        ("Fatal Python error: Aborted", "Fatal Python error: Aborted"),
        ("Windows fatal exception: access violation", "Windows fatal exception: access violation"),
        ("Windows fatal exception: code 0xc0000409", "Windows fatal exception: code 0xc0000409"),
        # `Py_FatalError("…")` 的自由文本 / 用户 print 出来凑成整块的：只留头
        ("Fatal Python error: patient-123 cohort B", "Fatal Python error: …"),
        ("Fatal Python error: Segmentation fault: patient-123", "Fatal Python error: …"),
    ],
)
def test_crash_headers_pass_only_cpythons_own_fault_names(header, expect):
    """崩溃头与 `Extension modules` 尾也是自由文本的位置：故障名按 CPython faulthandler.c
    的闭集放行，模块名单只留计数（用户自己的 C 扩展名会在里面）。"""
    kept, _ = diagnostics.evidence_lines(
        [
            header,
            "Current thread 0x00001234 (most recent call first):",
            '  File "/x/a.py", line 1 in <module>',
            "Extension modules: numpy._core._multiarray_umath, cohort_secret._ext (total: 2)",
        ]
    )
    assert kept[0] == expect, kept
    assert kept[-1] == "Extension modules: … (total: 2)", kept
    assert "cohort_secret" not in "\n".join(kept)


@pytest.mark.parametrize(
    "line, expect",
    [
        (
            r'  File "D:\ConfidentialStudy\fig.py", line 12, in <module>',
            '  File "…/file:07dcc94317.py", line 12, in <module>',
        ),
        (
            r'  File "\\wsl.localhost\Ubuntu\home\u\motif\plot.py", line 20 in <module>',
            '  File "…/file:a19de1d7c3.py", line 20 in <module>',
        ),
        (
            r"OSError: [WinError 5] 拒绝访问。: 'C:\Users\someone\x.png'",
            "OSError: [WinError 5] 拒绝访问。: '…/file:3fc7759e1c.png'",
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


def test_worker_log_tails_read_only_the_tail_of_a_huge_log(tmp_path, monkeypatch):
    """评审 #443 第十一轮 P1：`read_bytes()[-N:]` 先把整个文件装进内存再切，「扫描上限」
    是空话——一份被脚本刷了几小时的 worker.log 能有几百 MB。改成 seek 到尾巴再读 N 字节：
    这里把 `Path.read_bytes` 钉成炸弹，走到它就是整读。"""
    body = "noise\n" * 5000 + FH_BLOCK
    _session(tmp_path, "huge.py", body, age_s=1)
    log = next(tmp_path.glob("*/worker.log"))
    raw = log.read_bytes()  # 磁盘上的真字节（Windows 的 write_text 写的是 CRLF），装炸弹之前读
    monkeypatch.setattr(diagnostics, "WORKER_LOG_SCAN_BYTES", 4096)

    def boom(self):  # noqa: ARG001
        raise AssertionError("整读了 worker.log（read_bytes）")

    monkeypatch.setattr(type(tmp_path), "read_bytes", boom)
    (got,) = diagnostics.worker_log_tails(tmp_path, project_dir=PROJECT)
    assert got["tail"].startswith("Fatal Python error: Segmentation fault"), got
    # 读进来的只有最后 4096 字节：略去的行数按这一截算，而不是 5000 行噪音
    assert got["omitted"] < 1000, got["omitted"]
    # 而且真的只读了那么多——不是 seek 之后又把整个文件读回来
    assert diagnostics._read_tail_bytes(log, 4096) == raw[-4096:]
    assert diagnostics._read_tail_bytes(log, len(raw) + 10) == raw  # 比文件还大：整份


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
        "RuntimeError: bad key sk-abcdefghijklmnop\n" + FH_BLOCK,
        age_s=1,
        project=str(project),
    )
    _session(tmp_path, "other.py", "RuntimeError: OTHER_PROJECT_SECRET\n", age_s=0.5, project=OTHER)
    z = zipfile.ZipFile(BytesIO(client.get("/api/diagnostics/bundle").data))
    report = json.loads(z.read("report.json"))
    logs = report["render"]["worker_logs"]
    assert len(logs) == 1 and logs[0]["session"].startswith("session:"), logs
    assert "fig.py" not in json.dumps(logs), "脚本名不出门（README：文件名一律换成哈希）"
    assert "crash.py" not in json.dumps(logs)
    assert "OTHER_PROJECT_SECRET" not in json.dumps(report)
    tail = logs[0]["tail"]
    assert "Segmentation fault" in tail
    assert REAL_HOME not in tail
    # 帧路径缩到文件名：主目录连出现的机会都没有（不靠 `~` 那道替换）
    assert 'File "…/file:07dcc94317.py", line 3' in tail
    # 收尾行只留类型：密钥所在的 message 根本不出门（不是被 `***` 替掉，是没带）
    assert "sk-abcdefghijklmnop" not in tail and "RuntimeError: …" in tail
    assert "loading" not in tail, "脚本自己 print 的那行不进包"
    assert "token = " not in tail, "帧下面的源码行不进包（README：不含 Python 源代码）"
    assert logs[0]["omitted"] == 2
    # 「复制诊断」的文本形态也带着它（多行值按缩进展开）
    text = diagnostics.render_text(report)
    assert "render.worker_logs[0].tail:" in text
    assert "    Fatal Python error: Segmentation fault" in text
