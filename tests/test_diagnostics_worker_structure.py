"""worker.log 里被整行略去的内容，要留下「结构信息」而不是一个空 tail。

真实 Windows beta 诊断包：`worker_logs[0].omitted: 4`、`tail:` 为空——日志有内容，但没有一段
是完整的 `Traceback` 块（头 + 帧 + 收尾），按白名单整行略去后，包里看不出脚本死于什么
（实际是 `ModuleNotFoundError: No module named 'lxml'`）。

**判据的主语**：被判的是 *worker 子进程* 写进 `worker.log` 的行（含用户脚本的 stdout/stderr），
在*导出那一刻*、*按行的形状*判；输出的每一段要么是闭集成员（builtins 异常名、已知包名、
帧分类）、要么是哈希、要么是个数。用户 print 出同样的形状只会得到同样的闭集摘要。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import zipfile
from io import BytesIO

import pytest

from tavotto import app as m
from tavotto.engine import diagnostics, pool, worker

PROJECT = "/projects/this-one"


def _session(root, script: str, text: str, *, project: str = PROJECT) -> None:
    d = root / f"{pool.cache_digest(project)}-{script}"
    d.mkdir()
    log = d / "worker.log"
    log.write_text(text, encoding="utf-8")
    stamp = time.time() - 1
    os.utime(log, (stamp, stamp))


def _tail(tmp_path, text: str) -> dict:
    _session(tmp_path, "fig.py", text)
    (got,) = diagnostics.worker_log_tails(tmp_path, project_dir=PROJECT)
    return got


def _digest(name: str) -> str:
    return hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]


@pytest.fixture
def client():
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()


def test_bare_missing_module_line_leaves_its_type_and_module(tmp_path):
    """真实形状：没有完整 traceback 块，只剩一句 `ModuleNotFoundError`（lxml 是已知包，原样）。"""
    got = _tail(tmp_path, "loading data\nModuleNotFoundError: No module named 'lxml'\n")
    assert got["empty"] is False
    assert got["omitted"] == 2, "原行仍然是被略去的，计数不变"
    assert got["tail"] == "[structure] ModuleNotFoundError module=lxml"


def test_headless_frames_are_classified_never_named(tmp_path):
    """帧行丢了 `Traceback` 头（被截在这一代起点之前）：只出帧数与分类，路径 / 文件名 / 源码不出。"""
    log = (
        '  File "C:\\Users\\Zhang Wei\\Clinical_Study_X\\analysis_secret.py", line 4, in <module>\n'
        "    import docx_secret_src\n"
        '  File "<frozen importlib._bootstrap>", line 1204, in _gcd_import\n'
        '  File "C:\\Py\\Lib\\site-packages\\docx\\__init__.py", line 2, in <module>\n'
        '  File "C:\\Users\\Zhang Wei\\Clinical_Study_X\\helpers_secret.py", line 9, in f\n'
        "ModuleNotFoundError: No module named 'docx.mod_x'\n"
    )
    tail = _tail(tmp_path, log)["tail"]
    assert tail.startswith(f"[structure] ModuleNotFoundError module=mod:{_digest('docx.mod_x')} ")
    assert "frames=4 (user=2, runtime=1, site:docx=1)" in tail
    for leak in (
        "Zhang",
        "Clinical",
        "analysis_secret",
        "helpers_secret",
        "docx_secret_src",
        "_gcd",
    ):
        assert leak not in tail


def test_private_module_name_is_hashed_like_missing_dependencies(tmp_path):
    tail = _tail(tmp_path, "ModuleNotFoundError: No module named 'patient_123_utils'\n")["tail"]
    assert tail == f"[structure] ModuleNotFoundError module=mod:{_digest('patient_123_utils')}"
    assert "patient_123" not in tail
    # 与既有 `missing_dependencies` 同一个函数出的口径
    assert (
        diagnostics._import_name_for_export("patient_123_utils")
        == f"mod:{_digest('patient_123_utils')}"
    )


def test_stdlib_and_curated_modules_are_shown(tmp_path):
    assert "module=json" in _tail(tmp_path, "ImportError: No module named 'json'\n")["tail"]


def test_user_defined_exception_name_never_leaves(tmp_path):
    log = 'Patient_123Error: cohort B\n  File "/x/y.py", line 3, in f\nCohortFailure\n'
    got = _tail(tmp_path, log)
    assert "Patient_123" not in got["tail"] and "Cohort" not in got["tail"]
    # 帧还在 -> 只出一条无异常行的帧摘要
    assert got["tail"] == "[structure] no-exception-line frames=1 (user=1)"


def test_builtin_exception_message_is_never_copied(tmp_path):
    tail = _tail(
        tmp_path, "KeyError: 'patient-123'\nmodule.path.FileNotFoundError: C:\\Users\\a\\b.csv\n"
    )["tail"]
    assert tail.splitlines() == ["[structure] KeyError", "[structure] FileNotFoundError"]
    assert "patient" not in tail and "b.csv" not in tail


def test_warnings_and_prose_are_not_failures(tmp_path):
    log = (
        "/x/y.py:3: UserWarning: boom\n"
        "DeprecationWarning: x\n"
        "next sample: patient-124\n"
        "ValueError was raised by me\n"
    )
    got = _tail(tmp_path, log)
    assert got["tail"] == "" and got["omitted"] == 4


def test_sensitive_run_keeps_only_the_exception_type(tmp_path):
    log = (
        "[sensitive run: script output omitted]\n"
        '  File "/Users/a/secret_study.py", line 4, in <module>\n'
        "ModuleNotFoundError: No module named 'lxml'\n"
    )
    tail = _tail(tmp_path, log)["tail"]
    assert tail == "[structure] ModuleNotFoundError (sensitive run: details withheld)"
    assert "lxml" not in tail and "frames" not in tail


def test_sensitive_notice_is_the_same_pair_as_the_worker():
    assert diagnostics._SENSITIVE_NOTICE == worker._PRIVATE_OUTPUT_NOTICE.strip()


def test_complete_traceback_blocks_do_not_get_a_second_summary(tmp_path):
    log = (
        "Traceback (most recent call last):\n"
        '  File "/app/a.py", line 1, in <module>\n'
        "    import lxml\n"
        "ModuleNotFoundError: No module named 'lxml'\n"
    )
    tail = _tail(tmp_path, log)["tail"]
    assert "[structure]" not in tail
    assert "ModuleNotFoundError: No module named 'lxml'" in tail


def test_at_most_three_summaries_keep_the_latest(tmp_path):
    log = "".join(
        f"{n}\n" for n in ("KeyError", "ValueError", "OSError", "TypeError", "IndexError")
    )
    assert _tail(tmp_path, log)["tail"].splitlines() == [
        "[structure] OSError",
        "[structure] TypeError",
        "[structure] IndexError",
    ]


def test_canaries_never_reach_any_bundle_file_or_the_copy_text(client, tmp_path, monkeypatch):
    """金丝雀全文搜索：脚本名、路径、项目名、用户异常消息里的标记串，在 zip 每个文件与「复制诊断」
    文本里都搜不到；而结构（类型 / lxml / 帧数）在。"""
    monkeypatch.setattr(pool, "ENGINE_CACHE", tmp_path)
    project = tmp_path / "CANARY_PROJECT_NAME"
    project.mkdir()
    m.open_project(str(project))
    log = (
        "CANARY_STDOUT_LINE loaded CANARY_DATA.csv\n"
        '  File "D:\\CANARY_DIR\\CANARY_SCRIPT.py", line 7, in canary_func\n'
        "    CANARY_SOURCE_LINE()\n"
        "KeyError: 'CANARY_KEY'\n"
        "ImportError: No module named 'CANARY_private_pkg'\n"
        "ModuleNotFoundError: No module named 'lxml'\n"
    )
    _session(tmp_path, "CANARY_SCRIPT.py", log, project=str(project))
    # 第二份会话：用户自定义异常名与消息（每份日志的摘要各自封顶，分开放才都在窗口里）
    _session(
        tmp_path,
        "CANARY_OTHER.py",
        "CANARY_UserError: CANARY_MESSAGE\nmodule.CANARY_Error\n",
        project=str(project),
    )
    z = zipfile.ZipFile(BytesIO(client.get("/api/diagnostics/bundle").data))
    report = json.loads(z.read("report.json"))
    tails = [e["tail"] for e in report["render"]["worker_logs"]]
    assert len(tails) == 2
    (mine,) = [t for t in tails if "lxml" in t]
    assert "[structure] ModuleNotFoundError module=lxml" in mine
    assert "[structure] KeyError frames=1 (user=1)" in mine
    assert f"module=mod:{_digest('CANARY_private_pkg')}" in mine
    assert [t for t in tails if t != mine] == [""], "用户自定义异常名一个字都不出"
    texts = [diagnostics.render_text(report)] + [
        z.read(n).decode("utf-8", errors="replace") for n in z.namelist()
    ]
    for body in texts:
        assert "CANARY" not in body, "金丝雀泄漏"
        assert "canary_func" not in body
