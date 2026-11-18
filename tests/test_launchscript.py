"""启动脚本（ADR 0194 §二）：AI 修复写的胶水，Tavotto 执行它的字节、身份仍是原脚本，图挂回原脚本；原脚本一个字节不动。"""

from __future__ import annotations

import os

import pytest

from tavotto.engine import config, execspec, launchscript, preparation
from tests.test_fixtools import call, put
from tests.test_fixtools_change import box, change_ctx  # noqa: F401 — 夹具

LIB = "def plot(rows):\n    pass\n"


def test_store_dirname_matches_config():
    assert launchscript.STORE_DIRNAME == config.PROJECT_STORE_DIRNAME


def test_path_is_inside_tavottos_folder_and_deterministic(tmp_path):
    a = launchscript.path_for(tmp_path, "code/fig.py")
    assert a == launchscript.path_for(tmp_path, "code/fig.py")
    assert a.parent == tmp_path / "tavottofile" / "launchers" and a.suffix == ".py"
    assert a != launchscript.path_for(tmp_path, "other/fig.py")  # 同名不同目录不撞


def test_write_read_revision_remove_roundtrip(tmp_path):
    assert launchscript.source_for(tmp_path, "fig.py") is None
    assert launchscript.revision_of(tmp_path, "fig.py") == ""
    path, prev = launchscript.write(tmp_path, "fig.py", "x = 1\n")
    assert prev is None and launchscript.source_for(tmp_path, "fig.py") == os.fspath(path)
    rev1 = launchscript.revision_of(tmp_path, "fig.py")
    _, prev = launchscript.write(tmp_path, "fig.py", "x = 2\n")
    assert prev == "x = 1\n" and launchscript.revision_of(tmp_path, "fig.py") != rev1
    assert (
        launchscript.remove(tmp_path, "fig.py")
        and launchscript.source_for(tmp_path, "fig.py") is None
    )


@pytest.mark.parametrize(
    ("code", "want"),
    [("def (:\n", "syntax_error"), ("x = 1\x00", "not_text"), ("#" * (65 * 1024), "too_large")],
)
def test_validate_rejects(code, want):
    with pytest.raises(launchscript.LauncherError) as exc:
        launchscript.validate(code)
    assert exc.value.code == want


@pytest.mark.skipif(os.name == "nt", reason="符号链接在 Windows 上要特权")
def test_a_linked_launcher_or_store_is_ignored(tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n")
    proj = tmp_path / "p"
    target = launchscript.path_for(proj, "fig.py")
    target.parent.mkdir(parents=True)
    target.symlink_to(outside)
    assert launchscript.source_for(proj, "fig.py") is None  # 不跟出 Tavotto 的目录
    proj2 = tmp_path / "p2"
    (proj2 / "tavottofile").mkdir(parents=True)
    (proj2 / "tavottofile" / "launchers").symlink_to(tmp_path)
    with pytest.raises(launchscript.LauncherError):
        launchscript.write(proj2, "fig.py", "x = 1\n")


def test_safe_and_native_specs_run_the_launcher_as_the_script(tmp_path):
    (tmp_path / "fig.py").write_text(LIB)
    launchscript.write(tmp_path, "fig.py", "from fig import plot\nplot([1])\n")
    src = launchscript.source_for(tmp_path, "fig.py")
    safe = execspec.safe_spec(
        "fig.py", tmp_path, "__main__", interpreter=str(tmp_path / "py"), sandbox=str(tmp_path)
    )
    assert safe.script_source == src and safe.target == "fig.py"
    native = execspec.desktop_native_spec(
        str(tmp_path / "fig.py"),
        interpreter=str(tmp_path / "py"),
        cwd=str(tmp_path),
        project_root=str(tmp_path),
        script_source=src,
    )
    argv = execspec.bridge_argv(native, runner_py="r.py", out_dir="o")
    assert argv[argv.index("--script-source") + 1] == src
    with pytest.raises(ValueError):
        execspec.desktop_native_spec(
            str(tmp_path / "fig.py"),
            interpreter=str(tmp_path / "py"),
            cwd=str(tmp_path),
            project_root=str(tmp_path),
            script_source=src,
            entry="plot",
        )


def test_the_launcher_is_part_of_the_script_revision(tmp_path):
    (tmp_path / "fig.py").write_text(LIB)
    before = preparation._script_revision(str(tmp_path), "fig.py")
    launchscript.write(tmp_path, "fig.py", "x = 1\n")
    during = preparation._script_revision(str(tmp_path), "fig.py")
    assert during != before and during.split(":", 1)[0] == before.split(":", 1)[0]
    launchscript.remove(tmp_path, "fig.py")
    assert preparation._script_revision(str(tmp_path), "fig.py") == before


# ---------------------------------------------------------------- AI 修复工具
def test_write_launcher_tool_creates_in_tavottos_folder_and_rollback_removes(box):  # noqa: F811
    script = put(box.proj / "code" / "fig.py", LIB)
    original = script.read_bytes()
    ctx = change_ctx(box, script_path=script)
    r = call(ctx, "write_launcher", {"code": "from fig import plot\nplot([1])\n"})
    assert not r.is_error, r.content
    assert launchscript.source_for(box.proj, "code/fig.py") is not None
    assert script.read_bytes() == original  # 原脚本一个字节不动
    assert sorted(p.name for p in script.parent.iterdir()) == ["fig.py"]  # 用户的代码文件夹没多文件
    snap = r.content.split("snapshot: ", 1)[1].split()[0]
    assert not call(ctx, "rollback", {"snapshot": snap}).is_error
    assert launchscript.source_for(box.proj, "code/fig.py") is None


def test_replacing_a_launcher_then_rollback_restores_the_previous_one(box):  # noqa: F811
    script = put(box.proj / "fig.py", LIB)
    ctx = change_ctx(box, script_path=script)
    assert not call(ctx, "write_launcher", {"code": "a = 1\n"}).is_error
    r = call(ctx, "write_launcher", {"code": "a = 2\n"})
    snap = r.content.split("snapshot: ", 1)[1].split()[0]
    call(ctx, "rollback", {"snapshot": snap})
    assert launchscript.read(box.proj, "fig.py") == "a = 1\n"


def test_write_launcher_rejects_bad_code_without_touching_anything(box):  # noqa: F811
    script = put(box.proj / "fig.py", LIB)
    ctx = change_ctx(box, script_path=script)
    assert call(ctx, "write_launcher", {"code": "def (:"}).code == "launcher_syntax_error"
    assert call(ctx, "write_launcher", {"code": ""}).code == "bad_arguments"
    assert launchscript.source_for(box.proj, "fig.py") is None


# ---------------------------------------------------------------- run_original()（safe / native 同一份）
def test_run_original_runs_the_real_script_in_the_callers_globals(tmp_path):
    from tavotto.engine import bridgeboot

    script = tmp_path / "fig.py"
    script.write_text("value = SEED * 2\n", encoding="utf-8")
    g = {"SEED": 21}
    bridgeboot.make_run_original(str(script))(g)
    assert g["value"] == 42


def test_native_run_script_injects_run_original_only_for_a_launcher(tmp_path, monkeypatch):
    """普通脚本的 `__main__` 里不许多出 `run_original`（与 `python fig.py` 一致）；只有带启动脚本时才注入。"""
    import sys

    from tavotto.engine import bridge_runner

    script = tmp_path / "fig.py"
    script.write_text(
        "import sys\nsys.modules[__name__].seen = 'run_original' in globals()\n", encoding="utf-8"
    )
    launcher = tmp_path / "launcher.py"
    launcher.write_text("value = 1\nrun_original()\n", encoding="utf-8")
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    saved_main = sys.modules["__main__"]
    try:
        bridge_runner.run_script(str(script), [])
        assert sys.modules["__main__"].seen is False
        bridge_runner.run_script(str(script), [], source=str(launcher))
        main = sys.modules["__main__"]
        assert main.seen is True and main.value == 1
    finally:
        sys.modules["__main__"] = saved_main
