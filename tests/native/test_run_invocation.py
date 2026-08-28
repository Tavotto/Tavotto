"""`tavotto run` 的**语法契约**（ADR 0021 §2）。

这一批全是纯函数，跑得飞快——它们挡的是"敲错一条命令，Tavotto 猜了一下然后
跑起来了"这一类：那种失败没有任何信号，用户只能靠结果不对去发现它。
"""

from __future__ import annotations

import os
import sys

import pytest

from tavotto.engine import runcodes, runspec
from tavotto.engine.runcodes import RunError

ME = sys.executable


def opts(**kw) -> runspec.RunOptions:
    return runspec.RunOptions(**kw)


def build(invocation, *, cwd, **kw):
    return runspec.build_request(opts(**kw), list(invocation), cwd=str(cwd))


# --------------------------------------------------------------------------
# `--` 是强制的
# --------------------------------------------------------------------------
def test_run_requires_delimiter():
    """没有 `--` 就没有 invocation——**不猜**。

    猜的代价很具体：用户脚本可以有自己的 `--project` / `--quiet`。猜错时
    Tavotto 吃掉了他的参数，而脚本照样跑完、照样出图，只是用的是一个他没
    要求的配置。
    """
    mine, invocation = runspec.split_argv(["--quiet", "python", "fig.py"])
    assert invocation == []
    assert mine == ["--quiet", "python", "fig.py"]
    with pytest.raises(RunError) as exc:
        runspec.parse_invocation(invocation)
    assert exc.value.code == runcodes.RUN_COMMAND_MISSING


def test_only_the_first_delimiter_is_ours():
    """`--` 之后再出现的 `--` 属于用户的脚本。"""
    mine, invocation = runspec.split_argv(["--quiet", "--", "python", "fig.py", "--", "extra"])
    assert mine == ["--quiet"]
    assert invocation == ["python", "fig.py", "--", "extra"]
    _, kind, target, argv = runspec.parse_invocation(invocation)
    assert (kind, target, argv) == (runspec.TARGET_SCRIPT, "fig.py", ("--", "extra"))


# --------------------------------------------------------------------------
# 两种正式形态
# --------------------------------------------------------------------------
def test_run_script_parse(tmp_path):
    script = tmp_path / "figure.py"
    script.write_text("x = 1\n", encoding="utf-8")
    req = build([ME, "figure.py", "--sample", "A"], cwd=tmp_path)
    assert req.target_kind == runspec.TARGET_SCRIPT
    assert req.raw_target == "figure.py"  # **用户原样敲的那串**（argv[0] 的口径）
    assert req.user_argv == ("--sample", "A")
    assert req.cwd == str(tmp_path)
    assert req.interpreter == os.path.abspath(ME)


def test_run_module_parse(tmp_path):
    req = build([ME, "-m", "paper.figures.xps", "--sample", "A"], cwd=tmp_path)
    assert req.target_kind == runspec.TARGET_MODULE
    assert req.raw_target == "paper.figures.xps"
    assert req.user_argv == ("--sample", "A")
    assert req.target_display == "python -m paper.figures.xps"


def test_run_absolute_python(tmp_path):
    script = tmp_path / "figure.py"
    script.write_text("x = 1\n", encoding="utf-8")
    req = build([os.path.abspath(ME), "figure.py"], cwd=tmp_path)
    assert req.interpreter == os.path.abspath(ME)


@pytest.mark.parametrize(
    "word", ["python", "python3", "python3.13", "python.exe", "PYTHON.EXE", "python3.11.exe"]
)
def test_interpreter_words_we_accept(word):
    assert runspec.looks_like_interpreter(word)
    assert runspec.looks_like_interpreter(f"/opt/some/place/{word}")


# --------------------------------------------------------------------------
# 明确拒绝的形态（**绝不静默丢**）
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "command", ["make", "bash", "sh", "poetry", "uv", "conda", "Rscript", "py", "pythonw"]
)
def test_run_unsupported_command(command):
    with pytest.raises(RunError) as exc:
        runspec.parse_invocation([command, "all"])
    assert exc.value.code == runcodes.UNSUPPORTED_RUN_COMMAND
    assert command in str(exc.value)  # 文案要指得出是哪一个


@pytest.mark.parametrize("flag", ["-c", "-", "-S", "-I", "-E", "-O", "-X", "-W", "-B", "--version"])
def test_run_unsupported_flag(flag):
    """`python -O fig.py` 与 `python fig.py` 不是同一个语义（assert 被去掉）。

    丢掉那个标志跑出来的图可能真的不一样，而用户以为 Tavotto 跑的是他敲的
    那条命令。
    """
    with pytest.raises(RunError) as exc:
        runspec.parse_invocation(["python", flag, "fig.py"])
    assert exc.value.code == runcodes.UNSUPPORTED_PYTHON_OPTION
    assert flag in str(exc.value)


def test_run_missing_target():
    with pytest.raises(RunError) as exc:
        runspec.parse_invocation(["python"])
    assert exc.value.code == runcodes.RUN_COMMAND_MISSING


def test_run_invalid_module(tmp_path):
    for bad in ["", "paper-figures", "paper..fig", "1paper", "paper/fig"]:
        with pytest.raises(RunError) as exc:
            build([ME, "-m", bad], cwd=tmp_path)
        assert exc.value.code == runcodes.INVALID_MODULE_NAME, bad


def test_a_missing_script_is_an_invocation_error_not_a_script_error(tmp_path):
    """**Session 8 的遗留**：目标不存在曾经落成 `script_error` + 一段
    FileNotFoundError traceback。那是 invocation 层的错，不是脚本的错——
    分类不准的代价是用户去脚本里找一个根本不存在的 bug。"""
    with pytest.raises(RunError) as exc:
        build([ME, "nope.py"], cwd=tmp_path)
    assert exc.value.code == runcodes.SCRIPT_TARGET_MISSING


def test_a_directory_target_is_not_a_script(tmp_path):
    (tmp_path / "sub").mkdir()
    with pytest.raises(RunError) as exc:
        build([ME, "sub"], cwd=tmp_path)
    assert exc.value.code == runcodes.SCRIPT_TARGET_NOT_FILE


def test_interpreter_not_found(tmp_path):
    with pytest.raises(RunError) as exc:
        runspec.resolve_interpreter("python-that-does-not-exist-9x", cwd=str(tmp_path))
    assert exc.value.code == runcodes.INTERPRETER_NOT_FOUND


def test_interpreter_path_that_is_not_a_file(tmp_path):
    (tmp_path / "bin").mkdir()
    with pytest.raises(RunError) as exc:
        runspec.resolve_interpreter(str(tmp_path / "bin"), cwd=str(tmp_path))
    assert exc.value.code == runcodes.INTERPRETER_NOT_EXECUTABLE


# --------------------------------------------------------------------------
# 体检
# --------------------------------------------------------------------------
def test_probe_reads_the_real_interpreter():
    info = runspec.probe_interpreter(ME)
    assert info["implementation"] == "cpython"
    assert tuple(info["version"]) >= runspec.PYTHON_MIN


def test_probe_rejects_an_old_python(monkeypatch):
    """版本判据要真的判——**不能只是把探针跑通了就算数**。"""

    class _Proc:
        returncode = 0
        stdout = '{"version": [3, 8, 10], "implementation": "cpython", "prefix": "/x"}'
        stderr = ""

    with pytest.raises(RunError) as exc:
        runspec.probe_interpreter("/fake/python", run=lambda *a, **k: _Proc())
    assert exc.value.code == runcodes.UNSUPPORTED_PYTHON_VERSION
    assert "3.8.10" in str(exc.value)


def test_probe_rejects_a_non_cpython():
    class _Proc:
        returncode = 0
        stdout = '{"version": [3, 12, 0], "implementation": "pypy", "prefix": "/x"}'
        stderr = ""

    with pytest.raises(RunError) as exc:
        runspec.probe_interpreter("/fake/python", run=lambda *a, **k: _Proc())
    assert exc.value.code == runcodes.UNSUPPORTED_PYTHON_IMPLEMENTATION


def test_a_thing_that_runs_but_is_not_python_is_an_implementation_error():
    """能跑、但吐不出那行 JSON——报"实现不支持"，不报"不可执行"。

    后者会把用户送去检查文件权限，而问题是"这压根不是 CPython"。
    """

    class _Proc:
        returncode = 0
        stdout = "hello\n"
        stderr = ""

    with pytest.raises(RunError) as exc:
        runspec.probe_interpreter("/fake/python", run=lambda *a, **k: _Proc())
    assert exc.value.code == runcodes.UNSUPPORTED_PYTHON_IMPLEMENTATION


def test_the_probe_never_imports_matplotlib():
    """体检是**只读**的：不 import matplotlib、不碰用户目录、不写东西。

    抢着报一个我们编的 `ModuleNotFoundError` 反而挡住了那条诚实的错误——
    用户自己敲那条命令时看到的就该是它。
    """
    src = runspec._PROBE_SRC  # noqa: SLF001 — 判据就是这段源码本身
    for forbidden in ("matplotlib", "open(", "write", "os.", "subprocess"):
        assert forbidden not in src, f"体检探针里不该出现 {forbidden!r}: {src}"


# --------------------------------------------------------------------------
# cwd 与 project_root 是两个概念
# --------------------------------------------------------------------------
def test_project_root_defaults_to_the_script_folder(tmp_path):
    sub = tmp_path / "figs"
    sub.mkdir()
    (sub / "figure.py").write_text("x=1\n", encoding="utf-8")
    req = build([ME, "figs/figure.py"], cwd=tmp_path)
    assert req.cwd == str(tmp_path)  # **cwd 一个字节不动**
    assert req.project_root == str(sub)


def test_project_root_climbs_to_an_existing_registry(tmp_path):
    root = tmp_path / "paper"
    deep = root / "a" / "b"
    deep.mkdir(parents=True)
    (root / "tavotto_registry.json").write_text("{}", encoding="utf-8")
    (deep / "figure.py").write_text("x=1\n", encoding="utf-8")
    req = build([ME, "a/b/figure.py"], cwd=root)
    assert req.project_root == str(root)


def test_project_root_never_climbs_past_max_parents(tmp_path):
    """**绝不静默越到用户 home**：那会把一整棵源码树当图库扫一遍。"""
    root = tmp_path / "paper"
    deep = root / "a" / "b" / "c" / "d" / "e"
    deep.mkdir(parents=True)
    (root / "tavotto_registry.json").write_text("{}", encoding="utf-8")
    (deep / "figure.py").write_text("x=1\n", encoding="utf-8")
    req = build([ME, str(deep / "figure.py")], cwd=root)
    assert req.project_root == str(deep), "越过 MAX_PARENTS 层找到了注册表"


def test_module_target_uses_cwd_as_the_project_root(tmp_path):
    req = build([ME, "-m", "paper.fig"], cwd=tmp_path)
    assert req.project_root == str(tmp_path)


def test_explicit_project_does_not_change_cwd(tmp_path):
    where = tmp_path / "run-here"
    proj = tmp_path / "library"
    where.mkdir()
    proj.mkdir()
    (where / "figure.py").write_text("x=1\n", encoding="utf-8")
    req = build([ME, "figure.py"], cwd=where, project=str(proj))
    assert req.project_root == str(proj)
    assert req.cwd == str(where), "--project 只改项目根，**不改工作目录**"


def test_bad_project_is_rejected(tmp_path):
    (tmp_path / "figure.py").write_text("x=1\n", encoding="utf-8")
    with pytest.raises(RunError) as exc:
        build([ME, "figure.py"], cwd=tmp_path, project=str(tmp_path / "nope"))
    assert exc.value.code == runcodes.PROJECT_ROOT_INVALID


# --------------------------------------------------------------------------
# 指纹与许可键
# --------------------------------------------------------------------------
def test_the_command_fingerprint_never_contains_argument_values(tmp_path):
    """指纹只记 argv 的**数量**。参数值里可能有样本名、路径甚至凭据。"""
    (tmp_path / "figure.py").write_text("x=1\n", encoding="utf-8")
    a = build([ME, "figure.py", "--token", "s3cr3t"], cwd=tmp_path)
    b = build([ME, "figure.py", "--token", "other"], cwd=tmp_path)
    assert a.command_fingerprint() == b.command_fingerprint()
    c = build([ME, "figure.py", "--token", "x", "--more", "y"], cwd=tmp_path)
    assert c.command_fingerprint() != a.command_fingerprint()


def test_the_permission_key_ignores_the_target(tmp_path):
    """许可绑的是"这个项目里的这条 Python"，不是"这一条命令"。

    含了 target 的话，同一个项目里换个脚本就要重新确认一次——那会把确认
    训练成一个下意识点掉的对话框，而它恰恰是唯一一次真正的授权。
    """
    (tmp_path / "a.py").write_text("x=1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("x=1\n", encoding="utf-8")
    a = build([ME, "a.py"], cwd=tmp_path)
    b = build([ME, "b.py", "--flag"], cwd=tmp_path)
    assert a.permission_key() == b.permission_key()


def test_the_permission_key_changes_with_the_interpreter(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("x=1\n", encoding="utf-8")
    a = build([ME, "a.py"], cwd=tmp_path)
    other = runspec.RunRequest(**{**a.__dict__, "interpreter": "/some/other/python"})
    assert other.permission_key() != a.permission_key()


# --------------------------------------------------------------------------
# status file
# --------------------------------------------------------------------------
def test_run_status_file_atomic(tmp_path):
    """原子写：调用方会在 CLI 还在跑的时候去读它。"""
    path = tmp_path / "sub" / "status.json"
    runspec.write_status_file(str(path), {"a": 1})
    assert path.is_file()
    leftovers = [p.name for p in path.parent.iterdir() if p.name != "status.json"]
    assert leftovers == [], f"临时文件没清干净: {leftovers}"


def test_run_status_file_no_secrets(tmp_path):
    (tmp_path / "figure.py").write_text("x=1\n", encoding="utf-8")
    req = build([ME, "figure.py", "--token", "s3cr3t-value"], cwd=tmp_path)
    payload = runspec.status_payload(req, script_exit_code=0, figures_captured=1)
    blob = repr(payload)
    assert "s3cr3t-value" not in blob, "status file 里出现了用户 argv 的值"
    assert "PATH" not in payload and "env" not in payload
    assert payload["arg_count"] == 2
    assert payload["script_exit_code"] == 0


def test_status_records_script_exit_and_tavotto_result_separately(tmp_path):
    """脚本 exit 0 但一张图都没有：**两件事分开记**（ADR 0021 §10.3）。"""
    (tmp_path / "figure.py").write_text("x=1\n", encoding="utf-8")
    req = build([ME, "figure.py"], cwd=tmp_path)
    payload = runspec.status_payload(
        req,
        script_exit_code=0,
        figures_captured=0,
        session_result=runcodes.NO_FIGURE_CAPTURED,
        error_code=runcodes.NO_FIGURE_CAPTURED,
    )
    assert payload["script_exit_code"] == 0
    assert payload["session_result"] == runcodes.NO_FIGURE_CAPTURED


# --------------------------------------------------------------------------
# CLI 的依赖边界
# --------------------------------------------------------------------------
def test_the_run_cli_never_pulls_in_flask_or_matplotlib():
    """`tavotto run` 要在用户敲下回车之后**立刻**给出反馈。

    「解释器找不到」「目标不存在」这类判断一行 Flask 都用不上，而装在用户
    机器上的 `tavotto-cli.exe` 每次都要付那份冷启动（`cli_entry` 的模块头
    记的就是这笔账）。这条按**真实 import 结果**判，不按源码里有没有那几个字
    ——间接 import 一样会把它们拖进来。
    """
    import subprocess

    probe = (
        "import sys;"
        "from tavotto.engine import runcli;"
        "print(','.join(sorted(m for m in sys.modules if m.split('.')[0] in "
        "('flask','matplotlib','numpy','pymupdf','fitz'))))"
    )
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        cwd=str(runspec.__file__).rsplit("src", 1)[0],
        env={**os.environ, "PYTHONPATH": "src"},
    )
    assert out.returncode == 0, out.stderr
    leaked = [m for m in out.stdout.strip().split(",") if m]
    assert leaked == [], f"`tavotto run` 的 import 链拖进了重依赖: {leaked}"


def test_dispatch_reaches_run_without_importing_the_app():
    """`run` 在 `cli.dispatch` 的闭集里，而且分派在 argparse **之前**。"""
    from tavotto.engine import cli as engine_cli

    assert "run" in engine_cli.COMMANDS
    assert engine_cli.dispatch(["run", "--help"]) == runcodes.EXIT_OK
