"""`POST /api/ai/convert` 的 R / MATLAB 来源入口（ADR 0143 修订，2026-10-23）：`source_language` 缺省 python（规则不变）；
r / matlab 时 `library` 必须缺省、后端用 `sourcelang.detect` 复核文件内容与声明一致（不一致 400）、`contained_path` 等原有检查不变。

只验入口的校验与传参：`ai_bridge.run` 被换成记录器（不起 Agent、不跑任何东西、不碰解释器）。
"""

from __future__ import annotations

import pytest

from tavotto.engine import ai_convert, ai_tasks, config, convertdeps, project_watch

R_SCRIPT = 'library(ggplot2)\ndf <- read.csv("data.csv")\nggplot(df, aes(x, y)) + geom_point()\n'
M_SCRIPT = "% 曲线\nx = linspace(0, 1, 20);\nfigure;\nplot(x, x.^2);\n"
OBJC = (
    "#import <Foundation/Foundation.h>\n\nint main(int argc, const char * argv[]) { return 0; }\n"
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    from tavotto import app as m

    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(config, "ai_settings", lambda: {})
    figs = tmp_path / "figures"
    (figs / "sub").mkdir(parents=True)
    (figs / "fig.R").write_bytes(R_SCRIPT.encode())
    (figs / "fig.m").write_bytes(M_SCRIPT.encode())
    (figs / "objc.m").write_bytes(OBJC.encode())
    (figs / "fig.py").write_bytes(b"open('out.png', 'wb').write(b'x')\n")
    (figs / "sub" / "deep.R").write_bytes(R_SCRIPT.encode())
    (figs / "out.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    m.open_project(str(figs))
    calls: list = []

    def fake_run(agent, script, prompt, figures_dir, **kw):
        calls.append({"agent": agent, "script": script, "prompt": prompt, **kw})
        return "abc123def456"

    monkeypatch.setattr(m.engine_ai, "run", fake_run)
    try:
        yield m.app.test_client(), figs, calls
    finally:
        m.reset_projects()
        project_watch.stop()
        ai_tasks.unregister(ai_convert.TASK_TYPE)


def _post(client, **body):
    return client.post("/api/ai/convert", json={"agent": "codex", **body})


@pytest.mark.parametrize(
    ("script", "language", "label"),
    [("fig.R", "r", "R"), ("fig.m", "matlab", "MATLAB"), ("sub/deep.R", "r", "R")],
)
def test_r_and_matlab_scripts_start_a_convert_session_without_a_library(
    project, script, language, label
):
    client, figs, calls = project
    r = _post(client, script=script, source_language=language, path="out.png")
    assert r.status_code == 200, r.get_json()
    assert r.get_json() == {"session": "abc123def456", "script": script}
    call = calls[0]
    assert call["convert"] == {
        "library": None,
        "source_language": language,
        "original_rel": "out.png",
    }
    assert call["task_hooks"]["deps"] is convertdeps.ensure, "R / MATLAB 才接自动安装钩子"
    assert call["task_hooks"]["runner"] is not None
    assert label in call["prompt"]


def test_python_default_is_unchanged_and_needs_a_library_and_gets_no_deps_hook(project):
    client, figs, calls = project
    r = _post(client, script="fig.py", library="pillow", path="out.png")
    assert r.status_code == 200
    call = calls[0]
    assert call["convert"] == {
        "library": "pillow",
        "source_language": "python",
        "original_rel": "out.png",
    }
    assert "deps" not in call["task_hooks"], "Python 来源的转换不接自动安装"
    assert _post(client, script="fig.py").status_code == 400, "python 缺 library 仍是 400"
    assert _post(client, script="fig.py", library="nope").status_code == 400
    assert _post(client, script="fig.R", library="pillow").status_code == 400, (
        "python 缺省时 .R 不收"
    )
    assert (
        _post(client, script="fig.py", source_language="python", library="pillow").status_code
        == 200
    )


@pytest.mark.parametrize(
    "body",
    [
        {"script": "fig.R", "source_language": "r", "library": "pillow"},  # 库必须缺省
        {"script": "fig.R", "source_language": "julia"},
        {"script": "fig.R", "source_language": None},
        {"script": 3, "source_language": "r"},
        {"script": "fig.R", "source_language": "r", "client_request_id": "bad id!"},
    ],
)
def test_malformed_requests_are_400(project, body):
    client, _figs, calls = project
    assert _post(client, **body).status_code == 400
    assert not calls


def test_declared_language_must_match_the_files_content(project):
    client, figs, calls = project
    # .m 声明成 r / 声明成 matlab 但其实是 Objective-C / .R 声明成 matlab
    for script, language in [
        ("fig.m", "r"),
        ("objc.m", "matlab"),
        ("fig.R", "matlab"),
        ("fig.py", "r"),
    ]:
        r = _post(client, script=script, source_language=language)
        assert r.status_code == 400, (script, language)
        assert r.get_json()["code"] == "convert_source_language_mismatch"
        assert r.get_json()["params"] == {"script": script}
    assert not calls


def test_oversized_or_non_utf8_foreign_scripts_are_refused(project):
    client, figs, calls = project
    (figs / "big.R").write_bytes(b"x <- 1\n" + b"#" * (1024 * 1024))
    (figs / "latin.R").write_bytes("x <- 'é'\n".encode("latin-1"))
    for script in ("big.R", "latin.R"):
        assert _post(client, script=script, source_language="r").status_code == 400
    assert not calls


def test_missing_and_escaping_paths_keep_the_original_checks(project, tmp_path):
    client, figs, calls = project
    (tmp_path / "outside.R").write_bytes(R_SCRIPT.encode())
    assert _post(client, script="ghost.R", source_language="r").status_code == 404
    assert _post(client, script="../outside.R", source_language="r").status_code == 404
    assert not calls


def test_the_original_image_path_is_still_contained(project, tmp_path):
    client, figs, calls = project
    (tmp_path / "secret.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    assert (
        _post(client, script="fig.R", source_language="r", path="../secret.png").status_code == 200
    )
    assert calls[0]["convert"]["original_rel"] is None, "项目外的“原图”路径一律丢掉，不当参考"


def test_the_escape_hatch_still_applies(project, monkeypatch):
    client, figs, calls = project
    monkeypatch.setenv("TAVOTTO_NO_CONVERT_TO_EDITABLE", "1")
    r = _post(client, script="fig.R", source_language="r")
    assert r.status_code == 404 and r.get_json()["code"] == "convert_disabled" and not calls


def test_without_a_path_the_same_named_image_beside_the_script_is_the_reference(project):
    client, figs, calls = project
    (figs / "fig.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    assert _post(client, script="fig.R", source_language="r").status_code == 200
    assert calls[0]["convert"]["original_rel"] == "fig.png"
    # 显式给了 path 就用 path，不再猜
    assert _post(client, script="fig.R", source_language="r", path="out.png").status_code == 200
    assert calls[1]["convert"]["original_rel"] == "out.png"


def test_no_same_named_image_means_no_reference(project):
    client, figs, calls = project
    assert _post(client, script="fig.R", source_language="r").status_code == 200
    assert calls[0]["convert"]["original_rel"] is None


def test_python_conversions_never_guess_a_sibling_image(project):
    client, figs, calls = project
    (figs / "fig.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    assert _post(client, script="fig.py", library="pillow").status_code == 200
    assert calls[0]["convert"]["original_rel"] is None


def test_a_tiff_beside_the_script_is_not_a_displayable_reference(project):
    """Codex #1330 P2：浏览器显示不了 TIFF——只有 fig.tif 时不当对照图；同时有 fig.png 用 png。"""
    client, figs, calls = project
    (figs / "fig.tif").write_bytes(b"II*\x00")
    assert _post(client, script="fig.R", source_language="r").status_code == 200
    assert calls[0]["convert"]["original_rel"] is None
    (figs / "fig.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    assert _post(client, script="fig.R", source_language="r").status_code == 200
    assert calls[1]["convert"]["original_rel"] == "fig.png"
