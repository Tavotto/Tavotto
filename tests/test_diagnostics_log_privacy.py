"""诊断包里的日志：报错文字、脚本名、请求的查询串**在写入那一刻**就不进出门版（REL-05）。

QA 2026-09-24 的 REL-05-B1 / B2：用户脚本 `raise ValueError("<病人编号>")` 的文字经
`LOG.error("引擎渲染失败: %s: %s", stem, exc)` 原样进了 report.json 的 `recent_errors`、包里的
app.log 与「复制诊断」；包里的 app.log 还带着脚本文件名（`worker 启动: fig_priv.py`）与 4xx
访问行的查询串。事后在一行文字上分不出哪一段是代码写的、哪一段是用户的，所以修法是结构性的：

* `cache/diagnostics.log` 由 `ExportLogFormatter` 写——日志模板（源码里的字面量）原样，参数按
  类型换形（异常只留类型 + 错误 code，路径 / 文件名哈希，请求行只留路由规则）；诊断包只读它；
* `recent_errors` 按出处放行 ERROR 行：不是出门版写的（`cache/app.log` 形状）只留前缀；
* 模板是字面量这件事本身由 AST 看护（f-string / `%` 拼好的模板会把参数带进模板）。

每条用例都带正向对照：金丝雀确实进了完整的 app.log（证明它流经了日志），出门版确实写了
（模板文字、类型名、路由都在）——包里没有金丝雀才算数。
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import shutil
import zipfile
from io import BytesIO
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from tavotto import app as m, pdfbackend
from tavotto.engine import diagnostics, exportjob, logsafe, pool

REPO = Path(__file__).resolve().parents[1]

ERR_CANARY = "QAERRCANARY_patient_0931"
FILE_CANARY = "QAFILECANARY_z81"
DIR_CANARY = "QAPROJCANARY_Alice"
QUERY_CANARY = "QAQUERYCANARY_p7"
CANARIES = (ERR_CANARY, FILE_CANARY, DIR_CANARY, QUERY_CANARY)

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None


@pytest.fixture
def client():
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()


@pytest.fixture
def wired_logging(tmp_path, monkeypatch):
    """真的走 `app.setup_logging`：cache 目录换到 tmp，诊断包从同一处读。"""
    cache = tmp_path / "cache"
    monkeypatch.setattr(m, "CACHE_DIR", cache)
    monkeypatch.setattr(diagnostics, "_log_path", lambda: cache / diagnostics.EXPORT_LOG_NAME)
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    # setup_logging 见到已有的 RotatingFileHandler 就不重复挂——把别处挂的先摘掉
    root.handlers = [h for h in saved_handlers if not isinstance(h, RotatingFileHandler)]
    m.setup_logging()
    added = [h for h in root.handlers if h not in saved_handlers]
    yield cache
    for h in added:
        root.removeHandler(h)
        h.close()
    root.handlers = saved_handlers
    root.setLevel(saved_level)


def _bundle_texts(client) -> dict[str, str]:
    res = client.get("/api/diagnostics/bundle")
    assert res.status_code == 200
    z = zipfile.ZipFile(BytesIO(res.data))
    texts = {n: z.read(n).decode("utf-8", errors="replace") for n in z.namelist()}
    texts["<复制诊断>"] = client.get("/api/diagnostics/summary").get_data(as_text=True)
    return texts


def _assert_no_canary(texts: dict[str, str]) -> None:
    hits = [f"{c} in {name}" for name, body in texts.items() for c in CANARIES if c in body]
    assert not hits, hits


def _project(tmp_path: Path) -> Path:
    figs = tmp_path / DIR_CANARY / "figures"
    figs.mkdir(parents=True)
    shutil.copy(REPO / "examples" / "figures" / "Fig1_kinetics.pdf", figs / f"{FILE_CANARY}.pdf")
    (figs / f"{FILE_CANARY}.py").write_text(
        f'def main():\n    raise ValueError("{ERR_CANARY}")\n', encoding="utf-8"
    )
    (figs / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "scripts": {
                    f"{FILE_CANARY}.py": {"entry": "main", "cost": "light", "stems": [FILE_CANARY]}
                }
            }
        ),
        encoding="utf-8",
    )
    return figs


# ------------------------------------------------------------------ 接线


def test_setup_logging_writes_the_export_log_where_the_bundle_reads_it(wired_logging):
    """主语：`app.setup_logging` 挂上的出门版 handler 的文件 == 诊断包读的那个文件。"""
    handlers = [
        h
        for h in logging.getLogger().handlers
        if isinstance(getattr(h, "formatter", None), diagnostics.ExportLogFormatter)
    ]
    assert len(handlers) == 1
    assert Path(handlers[0].baseFilename) == diagnostics._log_path()


# ------------------------------------------------------------------ 端到端（桩 worker）


def test_a_script_error_message_and_file_name_never_reach_the_bundle(
    client, tmp_path, monkeypatch, wired_logging
):
    """REL-05-B1：worker 报「脚本执行失败: <用户 raise 的文字>」→ 包里每个文件、复制诊断都没有它。"""
    figs = _project(tmp_path)

    class _Boom:
        built = True
        rev = 1

        def override(self, stem, patches, preview_dpi=None, inline_svg=False):
            raise pool.WorkerError(f"脚本执行失败: {ERR_CANARY}", code="script_error")

    monkeypatch.setattr(m.engine_pool, "get", lambda *a, **kw: _Boom())
    assert client.post("/api/projects/open", json={"path": str(figs)}).status_code == 200
    res = client.post("/api/engine/render", json={"id": f"{FILE_CANARY}.pdf", "patches": []})
    assert res.status_code == 500
    # 一段 LOG.exception：traceback 里带着 message 与脚本文件名
    try:
        raise ValueError(f"{ERR_CANARY} in {figs / (FILE_CANARY + '.py')}")
    except ValueError:
        logging.getLogger("tavotto").exception("面板刷新失败: %s", figs / f"{FILE_CANARY}.pdf")

    raw = (wired_logging / "app.log").read_text(encoding="utf-8")
    assert ERR_CANARY in raw and FILE_CANARY in raw, "对照：金丝雀确实流经了日志"
    assert f"ValueError: {ERR_CANARY}" in raw, "对照：完整 app.log 的 traceback 没被出门版删节"

    texts = _bundle_texts(client)
    _assert_no_canary(texts)

    # 反证落点：出门版确实写了，而且留下的是排障要的那几样
    log = texts["app.log"]
    assert "ERROR tavotto | 引擎渲染失败: " in log
    assert "WorkerError[script_error]" in log
    assert "Traceback (most recent call last):" in log and "ValueError: …" in log
    report = json.loads(texts["report.json"])
    token = report["project"]["figures_dir"]
    assert re.fullmatch(r"<project:[0-9a-f]{10}>", token)
    assert f"项目已打开: {token}" in log, "绝对路径参数出包时换成与 report 同一个项目记号"
    assert f"面板刷新失败: {token}/seg:" in log
    errors = report["recent_errors"]
    assert any("引擎渲染失败" in e and "WorkerError[script_error]" in e for e in errors), errors
    assert any(e.endswith("→ ValueError: …") for e in errors), errors


def test_access_log_keeps_the_route_and_drops_the_query(client, wired_logging):
    """REL-05-B2：werkzeug 访问行只留方法 + 路由规则 + 状态码；查询串与不认识的路径不出门。"""
    wz = logging.getLogger("werkzeug")
    line = '127.0.0.1 - - [25/Sep/2026 09:46:44] "%s" %s %s'
    wz.info(
        line, f"\x1b[31mGET /api/render?id={QUERY_CANARY}.pdf&w=200 HTTP/1.1\x1b[0m", "403", "-"
    )
    wz.info(line, f"GET /api/{QUERY_CANARY}/x HTTP/1.1", "404", "-")
    wz.info(line, f"POST /api/native/sessions/{QUERY_CANARY}/build HTTP/1.1", "409", "-")

    raw = (wired_logging / "app.log").read_text(encoding="utf-8")
    assert raw.count(QUERY_CANARY) == 3, "对照"
    texts = _bundle_texts(client)
    _assert_no_canary(texts)
    log = texts["app.log"]
    assert '"GET /api/render HTTP/1.1" 403 -' in log
    assert '"POST /api/native/sessions/<session_id>/build HTTP/1.1" 409 -' in log
    assert re.search(r'"GET route:[0-9a-f]{10} HTTP/1\.1" 404 -', log)


# ------------------------------------------------------------------ 端到端（真 worker）


@pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)
def test_real_worker_script_error_never_reaches_the_bundle(client, tmp_path, wired_logging):
    """同一件事走真 worker：脚本名进 `worker 启动: …`、异常文字进 WorkerError——包里都没有。"""
    figs = _project(tmp_path)
    assert client.post("/api/projects/open", json={"path": str(figs)}).status_code == 200
    res = client.post("/api/engine/render", json={"id": f"{FILE_CANARY}.pdf", "patches": []})
    assert res.status_code == 500

    raw = (wired_logging / "app.log").read_text(encoding="utf-8")
    assert f"worker 启动: {FILE_CANARY}.py" in raw, "对照：脚本名确实进了完整日志"
    assert ERR_CANARY in raw, "对照：报错文字确实进了完整日志"

    texts = _bundle_texts(client)
    _assert_no_canary(texts)
    log = texts["app.log"]
    assert re.search(r"worker 启动: …/file:[0-9a-f]{10}\.py", log)
    assert "ERROR tavotto | 引擎渲染失败: " in log
    # 有参考意义的闭集值明文（用户拍板：有意义的不哈希）：这里走的是 TAVOTTO_WORKER_PYTHON
    assert "解释器来源=env_override" in log


# ------------------------------------------------------------------ 明文放行（logsafe）
#
# 用户拍板（#601）：有实际参考意义、且结构上不可能含用户内容的参数明文，其余照旧哈希。
# 判据是值的**出处**（源码里的常量集合 / 路由对象 / 版本号形状），每条放行都配一条反证：
# 同一个调用位置换成带金丝雀的自由字符串，必须仍被哈希——放行规则不许变成后门。

_PLAIN_CASES = [
    # (模板, 放行值, 包装)
    (
        "PDF 后端: %s",
        pdfbackend.BACKEND_RENDERCORE,
        lambda v: logsafe.known(v, pdfbackend.BACKENDS),
    ),
    ("来源 %s", pool.SOURCE_ENV, lambda v: logsafe.known(v, pool.SOURCE_LABELS)),
    ("导出[%s]", exportjob.STATUS_DONE, lambda v: logsafe.known(v, exportjob.STATUSES)),
    ("依赖修复成功: numpy %s", "1.26.4", logsafe.version),
    ("私有 Python 就位（%s）", "3.13.1rc2", logsafe.version),
]


@pytest.mark.parametrize(("template", "value", "wrap"), _PLAIN_CASES)
def test_closed_set_values_stay_readable(template, value, wrap):
    out = _format("tavotto", template, wrap(value))
    assert out.endswith(template % value), out


@pytest.mark.parametrize(("template", "value", "wrap"), _PLAIN_CASES)
def test_the_same_slot_with_free_text_is_still_hashed(template, value, wrap):
    """关键反证：放行口认的是出处，不是位置——同一个调用点换成自由字符串照样哈希。"""
    for free in (ERR_CANARY, f"{value} {ERR_CANARY}", f"{value}/{ERR_CANARY}", f"1.2.{ERR_CANARY}"):
        out = _format("tavotto", template, wrap(free))
        assert ERR_CANARY not in out, out


def test_a_list_is_plain_only_when_every_member_is_known():
    from tavotto.engine import exportreq

    ok = _format("tavotto", "格式 %s", logsafe.known_each(["pdf", "png"], exportreq.ENGINE_FORMATS))
    assert ok.endswith("格式 pdf, png")
    bad = _format(
        "tavotto", "格式 %s", logsafe.known_each(["pdf", ERR_CANARY], exportreq.ENGINE_FORMATS)
    )
    assert ERR_CANARY not in bad


def test_only_a_real_flask_rule_passes_as_a_route(client):
    class FakeRule:  # 长得像 Rule，但不是 werkzeug 注册出来的
        rule = f"/api/{ERR_CANARY}"

    assert ERR_CANARY not in _format("tavotto", "路由 %s", logsafe.route(FakeRule()))
    real = next(r for r in m.app.url_map.iter_rules() if r.rule == "/api/render")
    assert _format("tavotto", "路由 %s", logsafe.route(real)).endswith("路由 /api/render")
    assert _format("tavotto", "路由 %s", logsafe.route(None)).endswith("路由 <no-route>")


def test_plain_cannot_be_constructed_outside_logsafe():
    with pytest.raises(TypeError):
        logsafe.Plain(ERR_CANARY, object())


def test_plain_values_reach_the_bundle_and_free_text_does_not(client, wired_logging):
    """端到端：同一条日志语句，一次放行值、一次金丝雀——包里前者明文、后者没有。"""
    log = logging.getLogger("tavotto")
    log.info("渲染解释器来源: %s", logsafe.known(pool.SOURCE_BUNDLED, pool.SOURCE_LABELS))
    log.info("渲染解释器来源: %s", logsafe.known(ERR_CANARY, pool.SOURCE_LABELS))
    raw = (wired_logging / "app.log").read_text(encoding="utf-8")
    assert ERR_CANARY in raw, "对照：完整日志里原值照写"
    texts = _bundle_texts(client)
    _assert_no_canary(texts)
    assert "渲染解释器来源: bundled" in texts["app.log"]
    assert re.search(r"渲染解释器来源: str:[0-9a-f]{10}", texts["app.log"])


# ------------------------------------------------------------------ 格式器本身


def _format(record_logger: str, msg, *args, exc_info=None) -> str:
    rec = logging.LogRecord(record_logger, logging.ERROR, __file__, 1, msg, args, exc_info)
    return diagnostics.ExportLogFormatter().format(rec)


def test_untrusted_loggers_keep_only_level_and_name():
    """第三方 logger 的模板可能是 f-string 拼好的：正文不出门。"""
    out = _format("somelib.io", f"opened {ERR_CANARY}")
    assert out.endswith("ERROR somelib.io | …")


def test_a_non_string_template_is_not_trusted():
    out = _format("tavotto", ValueError(ERR_CANARY))
    assert ERR_CANARY not in out and out.endswith("ERROR tavotto | …")


def test_numbers_and_numeric_json_survive_but_free_text_does_not():
    out = _format(
        "tavotto",
        "引擎渲染: %s %.0fms timings=%s",
        FILE_CANARY,
        1234.5,
        json.dumps({"build_total_ms": 17.4, "cold": True}),
    )
    assert FILE_CANARY not in out
    assert "1234ms" in out and '{"build_total_ms": 17.4, "cold": true}' in out
    leaky = _format("tavotto", "x=%s", json.dumps({"title": ERR_CANARY}))
    assert ERR_CANARY not in leaky


def test_the_full_log_still_gets_the_whole_traceback():
    """出门版不许把删节过的 traceback 缓存到记录上（`record.exc_text`），让 app.log 跟着变短。"""
    try:
        raise ValueError(ERR_CANARY)
    except ValueError:
        import sys

        rec = logging.LogRecord("tavotto", logging.ERROR, __file__, 1, "失败", (), sys.exc_info())
    exported = diagnostics.ExportLogFormatter().format(rec)
    full = logging.Formatter("%(message)s").format(rec)
    assert ERR_CANARY not in exported and "ValueError: …" in exported
    assert f"ValueError: {ERR_CANARY}" in full


def test_error_lines_of_any_other_origin_lose_their_text():
    """`recent_errors` 按出处放行：app.log 形状的 ERROR 行只留前缀；前缀都凑不齐的整行不要。"""
    got = diagnostics.recent_errors(
        [
            f"2026-09-25 02:46:03,688 ERROR tavotto: 引擎渲染失败: boom: 脚本执行失败: {ERR_CANARY}",
            f"stdout: {ERR_CANARY} ERROR x",
            "2026-09-25 02:46:04,000 ERROR tavotto | 引擎渲染失败: str:0123456789: WorkerError",
        ]
    )
    assert got == [
        "2026-09-25 02:46:03,688 ERROR tavotto: …",
        "2026-09-25 02:46:04,000 ERROR tavotto | 引擎渲染失败: str:0123456789: WorkerError",
    ]


# ------------------------------------------------------------------ 模板是字面量（AST）

_LEVELS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}


def _logging_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in _LEVELS:
            continue
        owner = node.func.value
        name = (
            owner.id
            if isinstance(owner, ast.Name)
            else owner.attr
            if isinstance(owner, ast.Attribute)
            else ""
        )
        if "log" in name.lower():
            yield node


def test_every_tavotto_log_template_is_a_string_literal():
    """出门版只信 `tavotto*` logger 的模板——前提是模板里没有运行时的值。f-string、`%` / `.format`
    拼好的串、变量当模板，都会把参数（报错文字、路径）带进「可信」的那一半。"""
    offenders = []
    checked = 0
    for path in sorted((REPO / "src" / "tavotto").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in _logging_calls(tree):
            checked += 1
            args = call.args[1:] if call.func.attr == "log" else call.args
            first = args[0] if args else None
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                offenders.append(f"{path.relative_to(REPO)}:{call.lineno}")
    assert checked > 100, "对照：扫描确实看到了日志调用"
    assert not offenders, offenders


def test_the_ast_scan_catches_an_fstring_template():
    """反证落点：判据认得出 f-string 模板。"""
    tree = ast.parse('LOG.error(f"失败: {exc}")\nLOG.info("ok %s", x)\n')
    calls = list(_logging_calls(tree))
    assert len(calls) == 2
    assert not isinstance(calls[0].args[0], ast.Constant)
    assert isinstance(calls[1].args[0], ast.Constant)


def _logsafe_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        owner = func.value if isinstance(func, ast.Attribute) else None
        owner_name = getattr(owner, "id", "") if owner is not None else ""
        if "logsafe" in owner_name and name in ("known", "known_each", "route", "Plain"):
            yield name, node


def _allowed_is_a_constant(arg: ast.AST) -> bool:
    """`known` 的集合只能是模块常量（`pool.SOURCE_LABELS`）或字符串字面量组成的元组 / 集合。"""
    if isinstance(arg, (ast.Name, ast.Attribute)):
        return True
    if isinstance(arg, (ast.Tuple, ast.Set, ast.List)):
        return all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in arg.elts)
    return False


def test_every_plain_release_names_a_constant_set():
    """放行口不许在调用点现拼集合（`known(x, {x})` / `known(x, set(names))` 就是后门）；
    `Plain` 不许在 logsafe 之外直接构造；`route` 只收 `request.url_rule`。"""
    offenders = []
    seen = 0
    for path in sorted((REPO / "src" / "tavotto").rglob("*.py")):
        if path.name == "logsafe.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name, call in _logsafe_calls(tree):
            seen += 1
            where = f"{path.relative_to(REPO)}:{call.lineno}"
            if name == "Plain":
                offenders.append(f"{where} 直接构造 Plain")
            elif name in ("known", "known_each"):
                if len(call.args) != 2 or not _allowed_is_a_constant(call.args[1]):
                    offenders.append(f"{where} {name} 的集合不是常量")
            elif name == "route":
                arg = call.args[0] if call.args else None
                if not (isinstance(arg, ast.Attribute) and arg.attr == "url_rule"):
                    offenders.append(f"{where} route 收的不是 request.url_rule")
    assert seen >= 15, f"对照：扫描确实看到了放行调用（{seen}）"
    assert not offenders, offenders


def test_the_constant_set_scan_catches_an_inline_set():
    """反证落点：现拼的集合认得出来。"""
    tree = ast.parse("logsafe.known(x, {x})\nlogsafe.known(x, pool.SOURCE_LABELS)\n")
    calls = [c for _n, c in _logsafe_calls(tree)]
    assert [_allowed_is_a_constant(c.args[1]) for c in calls] == [False, True]


def test_the_export_log_is_not_the_full_log():
    assert diagnostics._log_path().name == diagnostics.EXPORT_LOG_NAME != "app.log"
    assert os.path.basename(diagnostics._app_log_path()) == "app.log"
