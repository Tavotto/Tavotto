"""TeX 工具链失败的分类与错误转换（`engine/texdiag.py`，接在 `pool` 的两条错误收口上）。

分类读的是 matplotlib 自己的固定英文模板（3.8–3.11 同文）与 LaTeX 的 `! …` 行；这里用
**逐字摘自真实失败**的文本做可控 stub，钉住：

1. 缺命令 / 缺宏包或字体 / 其他编译错误 / 认得是 TeX 但原因不明 四类各归各位，不是 TeX 的回 None；
2. 两条控制面（Python 池 `_error_of`、workerd `_worker_error`）同一判据，且**先于缺依赖**——
   LaTeX 日志里出现 `No module named` 字样也不会落成 `missing_dependency` 的一键 pip 出口；
3. 参数经错误转换合同 `pool.recovery_params` 带出：渲染端点 / 试运行探测 / 准备会话同一份；
4. 失败出现在 build 之后（导出那一步）也走同一个收口——真 worker 用例在末节。

真实 TeX 的 smoke 单列在 `tests/test_tex_toolchain_real.py`（没有 TeX 时那边记 environment-blocked）。
"""

from __future__ import annotations

import pytest

from tavotto.engine import pool, probe, texdiag

# --- 逐字取自真实失败（matplotlib 3.11.2 / TeX Live 2025）-----------------------------------
CMD_MISSING = "RuntimeError: Failed to process string with tex because latex could not be found"
DVIPNG_MISSING = "RuntimeError: Failed to process string with tex because dvipng could not be found"
STY_MISSING = """RuntimeError: latex was not able to process the following string:
b'$\\\\alpha$'

Here is the full command invocation and its output:

latex -interaction=nonstopmode --halt-on-error --output-directory=tmpq3b7 a2f1.tex

This is pdfTeX, Version 3.141592653-2.6-1.40.27 (TeX Live 2025) (preloaded format=latex)
(./a2f1.tex
LaTeX2e <2024-11-01> patch level 2

! LaTeX Error: File `type1ec.sty' not found.

Type X to quit or <RETURN> to proceed,
or enter new name. (Default extension: sty)

Enter file name:
! Emergency stop.
<read *>

No pages of output.
"""
TFM_MISSING = """RuntimeError: latex was not able to process the following string:
b'Signal'

Here is the full command invocation and its output:

latex -interaction=nonstopmode --halt-on-error a2f1.tex

kpathsea: Running mktextfm ecrm1000
! Font T1/cmr/m/n/10=ecrm1000 at 10.0pt not loadable: Metric (TFM) file not found.
<to be read again>
"""
COMPILE = """RuntimeError: latex was not able to process the following string:
b'$x^$'

Here is the full command invocation and its output:

latex -interaction=nonstopmode --halt-on-error a2f1.tex

! Missing { inserted.
<to be read again>
"""
TEXMF = (
    "FileNotFoundError: Matplotlib's TeX implementation searched for a file named "
    "'phvr8r.tfm' in your texmf tree, but could not find it"
)
TEX_FRAME_ONLY = """Traceback (most recent call last):
  File "/x/site-packages/matplotlib/texmanager.py", line 301, in make_dvi
    raise ValueError("something unexpected")
ValueError: something unexpected
"""


@pytest.mark.parametrize(
    "text,kind,fact",
    [
        (CMD_MISSING, "command_missing", {"command": "latex"}),
        (DVIPNG_MISSING, "command_missing", {"command": "dvipng"}),
        (STY_MISSING, "package_missing", {"command": "latex", "file": "type1ec.sty"}),
        (TFM_MISSING, "package_missing", {"command": "latex", "file": "ecrm1000.tfm"}),
        # kpsewhich 不在与文件不在是同一句：证据不足，不判缺宏包
        (TEXMF, "unknown", {"file": "phvr8r.tfm"}),
        (COMPILE, "compile_error", {"command": "latex", "line": "Missing { inserted."}),
        (TEX_FRAME_ONLY, "unknown", {}),
    ],
)
def test_each_failure_lands_in_its_own_kind(text, kind, fact):
    got = texdiag.classify(text)
    assert got is not None
    assert got.as_fact() == {"kind": kind, **fact}


@pytest.mark.parametrize(
    "text",
    [
        "",
        "ValueError: x and y must have same first dimension",
        # 用户自己的异常文字里出现 latex 这个词，不足以判成 TeX 的失败
        "RuntimeError: my latex export of the table failed",
        "ModuleNotFoundError: No module named 'seaborn'",
    ],
)
def test_not_tex_is_none(text):
    assert texdiag.classify(text) is None


def test_names_outside_the_closed_sets_are_not_carried_out():
    weird = "Failed to process string with tex because /opt/evil;rm could not be found"
    assert texdiag.classify(weird).command == "other"
    odd_file = STY_MISSING.replace("type1ec.sty", "../../etc/pa ss.sty")
    fact = texdiag.classify(odd_file)
    assert fact.file == ""
    # 文案要 `{{file}}`：给不出就降级成 `tex_failed`，不发露占位符的 `tex_package_missing`
    assert fact.code == "tex_failed"
    assert texdiag.code_for(fact.as_fact()) == "tex_failed"


@pytest.mark.parametrize("name", ["中文 pkg.sty", "my pkg.sty"])
def test_unsafe_missing_file_name_degrades_to_tex_failed(name):
    text = STY_MISSING.replace("type1ec.sty", name)
    assert texdiag.classify(text).code == "tex_failed"
    assert pool._worker_error("m", "script_error", text).code == "tex_failed"


def test_compile_failure_without_a_bang_line_degrades_to_tex_failed():
    text = COMPILE.replace("! Missing { inserted.", "no bang line here")
    fact = texdiag.classify(text)
    assert fact.line == ""
    assert fact.code == "tex_failed"
    assert pool._worker_error("m", "script_error", text).code == "tex_failed"


def test_code_for_is_the_single_rule_for_fact_dicts():
    assert texdiag.code_for({"kind": "package_missing", "file": "a.sty"}) == "tex_package_missing"
    assert texdiag.code_for({"kind": "package_missing"}) == "tex_failed"
    assert texdiag.code_for({"kind": "compile_error", "line": ""}) == "tex_failed"
    assert (
        texdiag.code_for({"kind": "command_missing", "command": "other"}) == "tex_command_missing"
    )
    assert texdiag.code_for(None) == "tex_failed"


def test_every_code_gets_all_the_placeholders_its_text_needs():
    import json
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "web" / "src" / "i18n" / "locales"
    inputs = [
        CMD_MISSING,
        DVIPNG_MISSING,
        STY_MISSING,
        TFM_MISSING,
        COMPILE,
        COMPILE.replace("! Missing { inserted.", "no bang line"),
        STY_MISSING.replace("type1ec.sty", "中文 pkg.sty"),
        TEXMF,
        TEX_FRAME_ONLY,
    ]
    seen = set()
    for text in inputs:
        fact = texdiag.classify(text)
        code = fact.code
        seen.add(code)
        given = set(texdiag.params_of(fact.as_fact()))
        for locale in ("zh-CN", "en-US"):
            msgs = json.loads((root / locale / "errors.json").read_text(encoding="utf-8"))[
                "backend"
            ]
            need = set(re.findall(r"{{\s*(\w+)\s*}}", msgs[code])) - {"error"}
            assert need <= given, (locale, code, need, given)
    assert seen == set(texdiag.ERROR_CODES)


@pytest.mark.parametrize(
    "text,code",
    [
        (CMD_MISSING, "tex_command_missing"),
        (STY_MISSING, "tex_package_missing"),
        (COMPILE, "tex_compile_failed"),
        (TEX_FRAME_ONLY, "tex_failed"),
    ],
)
def test_workerd_funnel_gives_the_tex_code_and_never_missing_dependency(text, code):
    poisoned = text + "\n(texmf note) No module named 'type1ec'\n"
    err = pool._worker_error("脚本执行失败: …", "script_error", poisoned)
    assert err.code == code
    assert err.module == ""
    assert pool.recovery_params(err) == texdiag.params_of(texdiag.classify(poisoned).as_fact())


def test_python_pool_funnel_matches_the_workerd_funnel():
    w = pool.EngineWorker.__new__(pool.EngineWorker)
    w.script_name = "fig.py"
    err = w._error_of(
        {"ok": False, "error": {"code": "internal", "message": "x", "traceback": STY_MISSING}}
    )
    assert err.code == "tex_package_missing"
    assert err.script_name == "fig.py"
    assert pool.recovery_params(err) == {"command": "latex", "file": "type1ec.sty"}


def test_a_real_missing_python_package_is_still_missing_dependency():
    err = pool._worker_error("x", "script_error", "ModuleNotFoundError: No module named 'seaborn'")
    assert err.code == "missing_dependency" and err.module == "seaborn"


def test_probe_keeps_the_tex_code_and_params():
    err = pool._worker_error("x", "script_error", CMD_MISSING)
    out = probe._error_from_worker(err, "main")
    assert out["code"] == "tex_command_missing"
    assert out["params"] == {"command": "latex"}


def test_render_endpoint_payload_carries_the_params():
    from tavotto import app as m

    body = m._worker_error_payload(pool._worker_error("x", "script_error", STY_MISSING))
    assert body["code"] == "tex_package_missing"
    assert body["params"] == {"command": "latex", "file": "type1ec.sty"}
    assert "dependency_repair" not in body and "module" not in body


# --- 真 worker：build 成功、导出那一步才失败也走同一收口 ------------------------------------
try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

LATE = '''\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import RendererPdf
from matplotlib.text import Text


class LateTex(Text):
    """只在 PDF 渲染器（全质量导出）上失败，原文照抄 matplotlib 的模板。"""

    def draw(self, renderer):
        if isinstance(getattr(renderer, "_renderer", renderer), RendererPdf):  # MixedModeRenderer 包着
            raise RuntimeError(
                "Failed to process string with tex because dvipng could not be found"
            )
        return super().draw(renderer)


def main():
    fig, ax = plt.subplots(figsize=(2, 1.5))
    ax.add_artist(LateTex(0.5, 0.5, "late", transform=ax.transAxes))
    fig.savefig("Late.png")
'''


@pytest.mark.skipif(WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器")
def test_a_failure_after_build_is_classified_at_export(tmp_path):
    (tmp_path / "late.py").write_text(LATE, encoding="utf-8")
    w = pool.one_shot("late.py", str(tmp_path), "main")
    try:
        w.ensure_built()
        w.override("Late", [])  # build 与预览照常
        with pytest.raises(pool.WorkerError) as ei:
            w.export("Late", [], str(tmp_path / "late.pdf"), "pdf", 100)
        assert ei.value.code == "tex_command_missing"
        assert pool.recovery_params(ei.value) == {"command": "dvipng"}
    finally:
        pool.discard(w)


@pytest.mark.parametrize(
    "evil",
    [
        "!" * 200_000,
        "! LaTeX Error: File `" * 20_000,
        "! Font " * 30_000,
        "! Font =! at " * 20_000,
        "x" * 200_000 + " was not able to process the following string:",
        "Failed to process string with tex because " + "a" * 200_000,
    ],
    ids=["bangs", "latex-file-prefix", "font-prefix", "font-at", "long-token", "cmd-prefix"],
)
def test_classification_is_linear_on_pathological_logs(evil):
    """LaTeX 日志是外部文本：病态输入上分类必须是线性的（CodeQL py/polynomial-redos 那三条）。"""
    import time

    text = "RuntimeError: latex was not able to process the following string:\n" + evil
    t0 = time.perf_counter()
    texdiag.classify(text)
    texdiag.classify(evil)
    assert time.perf_counter() - t0 < 0.5
