"""`.ipynb` → 同名 `.py`（ADR 0179）：抽取规则、落盘规则、端点、脚本库清单。

守的不变式：笔记本一个字节不动、用户的同名脚本绝不覆盖、只写笔记本旁边、IPython 专有语法不让脚本当场 SyntaxError、
语法核验不过不落盘、不执行任何东西、会话认证不被绕过。
"""

from __future__ import annotations

import ast
import errno
import json
import os
import threading
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import assetimport, atomicio, discover, notebookscript, pool, project_watch
from tavotto.engine.brand import DIST_NAME, PRODUCT_NAME


def _nb(*cells, version=4, **extra) -> bytes:
    """造一个 nbformat 4 笔记本；cells 里 `("md", 文本)` / `("code", 文本)` / 字典"""
    out = []
    for c in cells:
        if isinstance(c, dict):
            out.append(c)
        else:
            kind, src = c
            out.append(
                {
                    "cell_type": {"md": "markdown", "code": "code", "raw": "raw"}[kind],
                    "metadata": {},
                    "source": src,
                    **(
                        {
                            "outputs": [{"output_type": "stream", "text": "OUT"}],
                            "execution_count": 7,
                        }
                        if kind == "code"
                        else {}
                    ),
                }
            )
    doc = {
        "nbformat": version,
        "nbformat_minor": 5,
        "metadata": {"kernelspec": {"name": "python3"}},
        "cells": out,
        **extra,
    }
    return json.dumps(doc).encode("utf-8")


PLOT = "import matplotlib.pyplot as plt\nplt.plot([1, 2, 3])\nplt.savefig('fig.png')"


def _body(*cells) -> str:
    return notebookscript.extract(_nb(*cells)).body


# ---------------------------------------------------------------- 抽取规则


def test_code_cells_are_joined_in_order_with_percent_separators():
    body = _body(("code", "a = 1"), ("code", ["b = 2\n", "c = 3\n"]), ("code", "d = 4"))
    assert body == "# %%\na = 1\n\n# %%\nb = 2\nc = 3\n\n# %%\nd = 4\n"


def test_markdown_becomes_comments_and_raw_cells_are_dropped():
    body = _body(("md", "# 标题\n\n说明文字"), ("raw", "RAW"), ("code", "x = 1"))
    assert body.startswith("# %% [markdown]\n# # 标题\n#\n# 说明文字\n\n# %%\nx = 1\n")
    assert "RAW" not in body


def test_outputs_counts_and_metadata_never_come_along():
    body = _body(("code", "x = 1"))
    for leaked in ("OUT", "kernelspec", "execution_count", "7"):
        assert leaked not in body.replace("x = 1", "")


@pytest.mark.parametrize(
    "line",
    [
        "%matplotlib inline",
        "%load_ext autoreload",
        "%autoreload 2",
        "!pip install -q seaborn",
        "!ls -la",
        "  %timeit f()",
        "plt.plot?",
        "plt.plot??",
        "?plt.plot",
        "files = !ls",
        "out = %timeit -o f()",
    ],
)
def test_ipython_syntax_lines_become_comments_keeping_the_original(line):
    body = _body(("code", f"import os\n{line}\nx = 1"))
    assert f"{_NOTE_PREFIX}{line.strip()}" in body
    ast.parse(body)  # 抽出来的是合法 Python


_NOTE_PREFIX = "# [notebook] "


def test_a_percent_at_the_start_of_a_continuation_line_is_modulo_not_a_magic():
    body = _body(("code", "x = (7\n     % 3)\nprint(x)"))
    assert "# [notebook]" not in body and "% 3)" in body


def test_cell_magics_that_wrap_python_keep_the_code():
    for magic in ("%%time", "%%timeit -n 1", "%%capture out", "%%prun"):
        body = _body(("code", f"{magic}\n{PLOT}"))
        assert f"# [notebook] {magic}" in body
        assert "\nplt.plot([1, 2, 3])\n" in body  # 绘图代码一行没丢


@pytest.mark.parametrize(
    "magic", ["%%bash", "%%html", "%%writefile a.txt", "%%javascript", "%%unknown_magic"]
)
def test_cell_magics_that_are_not_python_comment_out_the_whole_cell(magic):
    body = _body(("code", "x = 1"), ("code", f"{magic}\n<b>not python</b>\nrm -rf /"))
    assert "# [notebook] <b>not python</b>" in body and "# [notebook] rm -rf /" in body
    assert "\n<b>" not in body and "\nrm -rf" not in body
    ast.parse(body)


def test_shell_escape_continuation_lines_go_together():
    body = _body(("code", "!pip install \\\n    foo \\\n    bar\nx = 1"))
    assert body.count("# [notebook]") == 3 and "\nx = 1" in body


def test_chinese_bom_and_crlf_survive():
    raw = _nb(("code", "标签 = '温度 (℃)'\r\nprint(标签)\r\n"))
    body = notebookscript.extract(b"\xef\xbb\xbf" + raw).body
    assert "标签 = '温度 (℃)'\nprint(标签)\n" in body and "\r" not in body


def test_a_cell_that_is_only_magics_does_not_count_as_code():
    with pytest.raises(notebookscript.NotebookError) as e:
        notebookscript.extract(_nb(("code", "%matplotlib inline"), ("md", "text")))
    assert e.value.code == "notebook_no_code"


@pytest.mark.parametrize(
    "raw, code",
    [
        (b"", "notebook_empty"),
        (b"   \n", "notebook_empty"),
        (b"not json at all", "notebook_unreadable"),
        (b"\xff\xfe\x00bad", "notebook_unreadable"),
        (b"[1, 2]", "notebook_unreadable"),
        (b'{"cells": []}', "notebook_unreadable"),
        (b'{"nbformat": 4}', "notebook_unreadable"),
        (b'{"nbformat": "4", "cells": []}', "notebook_unreadable"),
        (_nb(version=3), "notebook_unsupported_version"),
        (b'{"nbformat": 3, "worksheets": [{"cells": []}]}', "notebook_unsupported_version"),
        (b'{"nbformat": 5, "cells": []}', "notebook_unsupported_version"),
        (_nb(), "notebook_no_code"),
        (_nb(("md", "only text"), ("code", "   \n")), "notebook_no_code"),
    ],
)
def test_unreadable_old_or_codeless_notebooks_get_their_own_code(raw, code):
    with pytest.raises(notebookscript.NotebookError) as e:
        notebookscript.extract(raw)
    assert e.value.code == code


def test_the_old_version_error_says_which_version():
    with pytest.raises(notebookscript.NotebookError) as e:
        notebookscript.extract(_nb(version=3))
    assert e.value.params == {"version": 3}


def test_a_real_syntax_error_is_refused_with_cell_and_line():
    with pytest.raises(notebookscript.NotebookError) as e:
        notebookscript.extract(_nb(("code", "x = 1"), ("code", "y = 2\nif True print(1)")))
    assert e.value.code == "notebook_syntax_error"
    assert e.value.params == {"cell": 2, "line": 2}
    # 诊断参数里没有笔记本内容
    assert "print" not in json.dumps(e.value.params)


def test_a_cell_the_parser_cannot_verify_is_refused_not_crashed():
    deep = "x = " + "(" * 400 + "1" + ")" * 400
    for src in ("x = 1\x00", deep):
        with pytest.raises(notebookscript.NotebookError) as e:
            notebookscript.extract(_nb(("code", "a = 1"), ("code", src)))
        assert e.value.code == "notebook_syntax_error" and e.value.params["cell"] == 2


def test_too_large_is_refused_before_parsing(monkeypatch):
    monkeypatch.setattr(notebookscript, "MAX_BYTES", 100)
    with pytest.raises(notebookscript.NotebookError) as e:
        notebookscript.extract(_nb(("code", "x = " + "1" * 200)))
    assert e.value.code == "notebook_too_large" and e.value.params == {"limit_mb": 0}


def test_extract_is_pure_it_executes_nothing(tmp_path, monkeypatch):
    marker = tmp_path / "ran"
    src = f"open({str(marker)!r}, 'w').write('x')\n!touch {marker}"
    notebookscript.extract(_nb(("code", src)))
    assert not marker.exists()


def test_header_names_the_notebook_uses_the_brand_and_carries_the_marker():
    ex = notebookscript.extract(_nb(("code", "x = 1")))
    text = notebookscript.render("论文 图1.ipynb", ex)
    first = text.splitlines()[:3]
    assert (
        PRODUCT_NAME in first[0] and "论文 图1.ipynb" in first[0] and "不会改动笔记本" in first[0]
    )
    assert "does not change the notebook" in first[1]
    assert first[2].startswith(f"# {DIST_NAME}-notebook: 论文 图1.ipynb sha256=")
    assert notebookscript._split_header(text)[2] == ex.body
    ast.parse(text)


def test_a_newline_in_the_notebook_name_cannot_break_the_header():
    ex = notebookscript.extract(_nb(("code", "x = 1")))
    text = notebookscript.render("a\nb.ipynb", ex)
    assert len(text.split("\n# %%")[0].splitlines()) == 3
    ast.parse(text)


# ---------------------------------------------------------------- 落盘规则


def _ex(src="x = 1"):
    return notebookscript.extract(_nb(("code", src)))


def _place(d: Path, name="fig.ipynb", src="x = 1"):
    with assetimport.LOCK:
        return notebookscript.place(d, name, _ex(src))


def test_a_fresh_script_is_created_beside_the_notebook(tmp_path):
    placed = _place(tmp_path)
    assert placed.path == tmp_path / "fig.py" and placed.status == "created"
    assert ast.parse(placed.path.read_text(encoding="utf-8"))


def test_same_content_is_reused_without_rewriting(tmp_path):
    first = _place(tmp_path)
    before = first.path.stat().st_mtime_ns
    again = _place(tmp_path)
    assert again.status == "reused" and again.path == first.path
    assert first.path.stat().st_mtime_ns == before


def test_an_untouched_generated_copy_is_updated_in_place_when_the_notebook_changes(tmp_path):
    _place(tmp_path, src="x = 1")
    placed = _place(tmp_path, src="x = 2")
    assert placed.status == "updated" and placed.path.name == "fig.py"
    assert "x = 2" in placed.path.read_text(encoding="utf-8")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["fig.py"]  # 没有留下临时文件、没有 (2)


def test_an_edited_generated_copy_is_never_overwritten(tmp_path):
    first = _place(tmp_path, src="x = 1")
    text = first.path.read_text(encoding="utf-8")
    first.path.write_text(text + "\nprint('我改过')\n", encoding="utf-8")
    edited = first.path.read_bytes()
    placed = _place(tmp_path, src="x = 2")
    assert placed.status == "renamed" and placed.path.name == "fig (notebook).py"
    assert first.path.read_bytes() == edited


def test_a_users_own_script_is_never_overwritten(tmp_path):
    (tmp_path / "fig.py").write_bytes(b"print('mine')\n")
    placed = _place(tmp_path)
    assert placed.status == "renamed" and placed.path.name == "fig (notebook).py"
    assert (tmp_path / "fig.py").read_bytes() == b"print('mine')\n"


def test_a_generated_copy_of_another_notebook_counts_as_not_ours(tmp_path):
    with assetimport.LOCK:
        notebookscript.place(tmp_path, "other.ipynb", _ex("y = 1"))
    (tmp_path / "other.py").rename(tmp_path / "fig.py")  # 标记里写的是 other.ipynb
    placed = _place(tmp_path, "fig.ipynb", "x = 1")
    assert placed.path.name == "fig (notebook).py"


def test_candidates_keep_counting_and_reuse_finds_an_earlier_identical_one(tmp_path):
    (tmp_path / "fig.py").write_text("mine\n", encoding="utf-8")
    (tmp_path / "fig (notebook).py").write_text("mine too\n", encoding="utf-8")
    assert _place(tmp_path).path.name == "fig (notebook 2).py"
    again = _place(tmp_path)
    assert again.status == "reused" and again.path.name == "fig (notebook 2).py"


def test_name_clash_is_case_insensitive(tmp_path):
    (tmp_path / "FIG.PY").write_text("mine\n", encoding="utf-8")
    placed = _place(tmp_path)
    assert placed.path.name == "fig (notebook).py"
    assert (tmp_path / "FIG.PY").read_text(encoding="utf-8") == "mine\n"


def test_a_directory_or_symlink_in_the_way_is_skipped(tmp_path):
    (tmp_path / "fig.py").mkdir()
    assert _place(tmp_path).path.name == "fig (notebook).py"
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (tmp_path / "fig (notebook).py").unlink()
    try:
        os.symlink(outside, tmp_path / "fig (notebook).py")
    except OSError:
        pytest.skip("symlinks need privileges here")
    placed = _place(tmp_path)
    assert placed.path.name == "fig (notebook 2).py"
    assert outside.read_text(encoding="utf-8") == "secret"


def test_candidates_run_out_with_a_stable_code(tmp_path, monkeypatch):
    monkeypatch.setattr(notebookscript, "_MAX_CANDIDATES", 3)
    for name in notebookscript.candidate_names("fig"):
        (tmp_path / name).write_text("mine\n", encoding="utf-8")
    with pytest.raises(assetimport.ImportRefused) as e:
        _place(tmp_path)
    assert e.value.code == "asset_import_name_exhausted"


def test_the_stem_goes_through_the_shared_name_cleaner():
    assert notebookscript.script_stem("a<b>:c.ipynb") == "a_b__c"
    assert notebookscript.script_stem("CON.ipynb") == "_CON"
    assert notebookscript.script_stem("...ipynb") == "image"
    assert notebookscript.script_stem("论文 图 1.ipynb") == "论文 图 1"


def test_concurrent_conversions_never_clobber(tmp_path):
    results: list[str] = []

    def go():
        results.append(_place(tmp_path).status)

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ["created"] + ["reused"] * 7
    assert [p.name for p in tmp_path.iterdir()] == ["fig.py"]


# ---------------------------------------------------------------- 端点


@pytest.fixture
def project(tmp_path, monkeypatch):
    m.reset_projects()
    root = tmp_path / "project"
    root.mkdir()
    m.open_project(str(root))
    monkeypatch.setattr(
        pool, "get", lambda *a, **k: pytest.fail("notebook conversion executed a script")
    )
    yield m.app.test_client(), root
    m.reset_projects()
    project_watch.stop()


_OWN = {"tavotto_registry.json", "tavottofile"}


def _files(root: Path) -> list[str]:
    return sorted(p.name for p in root.iterdir() if p.name not in _OWN)


def _extract(client, notebook, **kw):
    return client.post("/api/notebook/extract", json={"notebook": notebook}, **kw)


def test_extract_writes_the_script_beside_the_notebook_and_leaves_it_untouched(project):
    client, root = project
    (root / "sub").mkdir()
    nb = root / "sub" / "my fig.ipynb"
    nb.write_bytes(_nb(("md", "t"), ("code", "%matplotlib inline\n" + PLOT)))
    before = nb.read_bytes()
    mtime = nb.stat().st_mtime_ns
    resp = _extract(client, "sub/my fig.ipynb")
    assert resp.status_code == 200
    assert resp.json == {
        "notebook": "sub/my fig.ipynb",
        "script": "sub/my fig.py",
        "status": "created",
    }
    assert nb.read_bytes() == before and nb.stat().st_mtime_ns == mtime
    assert "# [notebook] %matplotlib inline" in (root / "sub" / "my fig.py").read_text(
        encoding="utf-8"
    )
    assert _files(root) == ["sub"] and _files(root / "sub") == ["my fig.ipynb", "my fig.py"]


def test_extract_twice_reuses_and_a_changed_notebook_updates(project):
    client, root = project
    (root / "a.ipynb").write_bytes(_nb(("code", "x = 1")))
    assert _extract(client, "a.ipynb").json["status"] == "created"
    assert _extract(client, "a.ipynb").json["status"] == "reused"
    (root / "a.ipynb").write_bytes(_nb(("code", "x = 2")))
    resp = _extract(client, "a.ipynb")
    assert resp.json["status"] == "updated" and resp.json["script"] == "a.py"


def test_extract_does_not_overwrite_the_users_own_script(project):
    client, root = project
    (root / "a.ipynb").write_bytes(_nb(("code", "x = 1")))
    (root / "a.py").write_text("print('mine')\n", encoding="utf-8")
    resp = _extract(client, "a.ipynb")
    assert resp.json["script"] == "a (notebook).py" and resp.json["status"] == "renamed"
    assert (root / "a.py").read_text(encoding="utf-8") == "print('mine')\n"


@pytest.mark.parametrize(
    "raw, status, code",
    [
        ("../outside.ipynb", 400, "notebook_path_outside_project"),
        ("nope.ipynb", 404, "notebook_not_found"),
        ("", 404, "notebook_not_found"),
        ("plain.py", 400, "notebook_unsupported_type"),
        ("folder.ipynb", 400, "notebook_unsupported_type"),
    ],
)
def test_extract_refuses_bad_paths_with_stable_codes(project, tmp_path, raw, status, code):
    client, root = project
    (root / "plain.py").write_text("x = 1\n", encoding="utf-8")
    (root / "folder.ipynb").mkdir()
    (tmp_path / "outside.ipynb").write_bytes(_nb(("code", "x = 1")))
    resp = _extract(client, raw)
    assert resp.status_code == status and resp.json["code"] == code
    assert _files(root) == ["folder.ipynb", "plain.py"] and not (tmp_path / "outside.py").exists()


def test_extract_refuses_an_absolute_path_outside_and_a_symlink_out(project, tmp_path):
    client, root = project
    outside = tmp_path / "out"
    outside.mkdir()
    (outside / "o.ipynb").write_bytes(_nb(("code", "x = 1")))
    assert (
        _extract(client, str(outside / "o.ipynb")).json["code"] == "notebook_path_outside_project"
    )
    try:
        os.symlink(outside, root / "link")
    except OSError:
        pytest.skip("symlinks need privileges here")
    resp = _extract(client, "link/o.ipynb")
    assert resp.json["code"] == "notebook_path_outside_project"
    assert not (outside / "o.py").exists()


@pytest.mark.parametrize(
    "raw, status, code",
    [
        (b"{}", 422, "notebook_unreadable"),
        (b"nope", 422, "notebook_unreadable"),
        (_nb(version=3), 422, "notebook_unsupported_version"),
        (_nb(("md", "x")), 422, "notebook_no_code"),
        (_nb(("code", "if True print(1)")), 422, "notebook_syntax_error"),
        (b"", 400, "notebook_empty"),
    ],
)
def test_extract_failures_write_nothing(project, raw, status, code):
    client, root = project
    (root / "bad.ipynb").write_bytes(raw)
    resp = _extract(client, "bad.ipynb")
    assert resp.status_code == status and resp.json["code"] == code and resp.json["error"]
    assert _files(root) == ["bad.ipynb"]
    assert (root / "bad.ipynb").read_bytes() == raw


def test_extract_too_large(project, monkeypatch):
    client, root = project
    monkeypatch.setattr(notebookscript, "MAX_BYTES", 50)
    (root / "big.ipynb").write_bytes(_nb(("code", "x = 1")))
    resp = _extract(client, "big.ipynb")
    assert resp.status_code == 413 and resp.json["code"] == "notebook_too_large"
    assert resp.json["params"] == {"limit_mb": 0} and _files(root) == ["big.ipynb"]


def _break_writes(monkeypatch, err):
    def boom(path, *a, **k):
        raise atomicio.AtomicWriteError("write_failed", "x", Path(path)) from err

    monkeypatch.setattr(atomicio, "write_bytes", boom)


@pytest.mark.parametrize(
    "num, status, code",
    [
        (errno.EACCES, 403, "asset_import_not_writable"),
        (errno.EROFS, 403, "asset_import_not_writable"),
        (errno.ENOSPC, 507, "asset_import_disk_full"),
        (errno.EIO, 500, "asset_import_write_failed"),
    ],
)
def test_write_failures_have_their_own_codes_and_leave_nothing(
    project, monkeypatch, num, status, code
):
    client, root = project
    (root / "a.ipynb").write_bytes(_nb(("code", "x = 1")))
    _break_writes(monkeypatch, OSError(num, "nope"))
    resp = _extract(client, "a.ipynb")
    assert resp.status_code == status and resp.json["code"] == code
    assert _files(root) == ["a.ipynb"]


def test_import_bytes_writes_a_script_in_the_project_root_without_saving_the_notebook(project):
    client, root = project
    resp = client.post(
        "/api/notebook/import",
        data=_nb(("code", "%matplotlib inline\n" + PLOT)),
        content_type="application/octet-stream",
        query_string={"name": "论文 图.ipynb"},
    )
    assert resp.status_code == 200
    assert resp.json["script"] == "论文 图.py" and resp.json["status"] == "created"
    assert _files(root) == ["论文 图.py"]
    # 同样的字节再来一次：复用
    again = client.post(
        "/api/notebook/import",
        data=_nb(("code", "%matplotlib inline\n" + PLOT)),
        content_type="application/octet-stream",
        query_string={"name": "论文 图.ipynb"},
    )
    assert again.json["status"] == "reused"


def test_import_ignores_directories_in_the_name(project):
    client, root = project
    resp = client.post(
        "/api/notebook/import",
        data=_nb(("code", "x = 1")),
        content_type="application/octet-stream",
        query_string={"name": "../../etc/evil.ipynb"},
    )
    assert resp.json["script"] == "evil.py" and _files(root) == ["evil.py"]


def test_import_refuses_bad_bytes_and_oversize(project, monkeypatch):
    client, root = project
    post = lambda data: client.post(  # noqa: E731
        "/api/notebook/import",
        data=data,
        content_type="application/octet-stream",
        query_string={"name": "a.ipynb"},
    )
    assert post(b"").json["code"] == "notebook_empty"
    assert post(b"zzz").json["code"] == "notebook_unreadable"
    monkeypatch.setattr(notebookscript, "MAX_BYTES", 20)
    big = post(_nb(("code", "x = 1")))
    assert big.status_code == 413 and big.json["code"] == "notebook_too_large"
    assert _files(root) == []


def test_import_without_a_project_is_a_stable_409(tmp_path):
    m.reset_projects()
    try:
        resp = m.app.test_client().post(
            "/api/notebook/import", data=b"{}", content_type="application/octet-stream"
        )
        assert resp.status_code == 409
        resp = m.app.test_client().post("/api/notebook/extract", json={"notebook": "a.ipynb"})
        assert resp.status_code == 409
    finally:
        m.reset_projects()


def test_endpoints_are_post_only(project):
    client, _ = project
    for path in ("/api/notebook/extract", "/api/notebook/import"):
        assert client.get(path).status_code in (404, 405)


def test_endpoints_sit_behind_the_session_guard(tmp_path):
    """真服务器：没有会话 cookie 是 401 且不写任何东西；带 bootstrap 出来的 cookie 才写得进去（ADR 0008，无旁路）"""
    import urllib.error
    import urllib.request

    from werkzeug.serving import make_server

    from tavotto import security
    from tavotto.engine import session_client

    m.reset_projects()
    root = tmp_path / "p"
    root.mkdir()
    (root / "a.ipynb").write_bytes(_nb(("code", "x = 1")))
    m.open_project(str(root))
    srv = make_server("127.0.0.1", 0, m.app, threaded=True)
    port = srv.server_port
    state, nonce = security.new_browser_state(port)
    m.app.config[security.STATE_KEY] = state
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()

    def post(path, body, headers):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers)

    try:
        js = {"Content-Type": "application/json"}
        octet = {"Content-Type": "application/octet-stream"}
        body = json.dumps({"notebook": "a.ipynb"}).encode()
        assert post("/api/notebook/extract", body, js)[0] == 401
        assert post("/api/notebook/import?name=b.ipynb", _nb(("code", "x = 1")), octet)[0] == 401
        assert not (root / "a.py").exists() and not (root / "b.py").exists()
        status, headers = post(security.BOOTSTRAP_PATH, json.dumps({"nonce": nonce}).encode(), js)
        assert status == 200
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        assert post("/api/notebook/extract", body, {**js, "Cookie": cookie})[0] == 200
        assert (root / "a.py").is_file()
    finally:
        srv.shutdown()
        thread.join(timeout=5)
        m.app.config.pop(security.STATE_KEY, None)
        session_client.remove_secret(port)
        m.reset_projects()
        project_watch.stop()


# ---------------------------------------------------------------- 脚本库清单


def test_registry_lists_notebooks_that_still_need_converting(project):
    client, root = project
    (root / "sub").mkdir()
    (root / "a.ipynb").write_bytes(_nb(("code", "x = 1")))
    (root / "sub" / "b.ipynb").write_bytes(b"not even json")  # 坏的也列：转换时如实说清
    (root / ".ipynb_checkpoints").mkdir()
    (root / ".ipynb_checkpoints" / "a-checkpoint.ipynb").write_bytes(_nb(("code", "x = 1")))
    (root / "plain.py").write_text("x = 1\n", encoding="utf-8")
    view = client.get("/api/registry").json
    assert view["notebook_scripts"] == [{"notebook": "a.ipynb"}, {"notebook": "sub/b.ipynb"}]
    # 笔记本不是脚本：不进可运行脚本清单
    assert [s["script"] for s in view["all_scripts"]] == ["plain.py"]


def test_a_converted_notebook_is_not_listed_again_until_it_changes(project):
    client, root = project
    nb = root / "a.ipynb"
    nb.write_bytes(_nb(("code", "x = 1")))
    assert _extract(client, "a.ipynb").status_code == 200
    view = client.get("/api/registry").json
    assert view["notebook_scripts"] == []
    assert "a.py" in [s["script"] for s in view["all_scripts"]]
    # 笔记本之后又被改过（比脚本新）：再列出来，点一下更新那份
    py_ns = (root / "a.py").stat().st_mtime_ns
    os.utime(nb, ns=(py_ns + 5_000_000_000, py_ns + 5_000_000_000))
    assert client.get("/api/registry").json["notebook_scripts"] == [{"notebook": "a.ipynb"}]


def test_a_users_own_same_named_script_does_not_hide_the_notebook(project):
    client, root = project
    (root / "a.ipynb").write_bytes(_nb(("code", "x = 1")))
    (root / "a.py").write_text("print('mine')\n", encoding="utf-8")
    assert client.get("/api/registry").json["notebook_scripts"] == [{"notebook": "a.ipynb"}]


def test_discover_walk_rules_apply_to_notebooks(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.ipynb").write_bytes(b"{}")
    (tmp_path / ".hidden.ipynb").write_bytes(b"{}")
    (tmp_path / "A.IPYNB").write_bytes(b"{}")  # 后缀区分大小写
    (tmp_path / "ok.ipynb").write_bytes(b"{}")
    assert [p.name for p in discover.iter_notebooks(tmp_path)] == ["ok.ipynb"]
    assert discover.iter_scripts(tmp_path) == []


# ---------------------------------------------------------------- IPython 内置名的替身


def _run(text: str, *, block_ipython: bool = True) -> dict:
    """exec 抽出来的脚本；`block_ipython` 时模拟「环境里没装 IPython」（sys.modules 里置 None → import 抛 ImportError）"""
    import sys
    from unittest import mock

    ns: dict = {"__name__": "nbtest"}
    blocked = {"IPython": None, "IPython.display": None} if block_ipython else {}
    with mock.patch.dict(sys.modules, blocked):
        exec(compile(text, "nb.py", "exec"), ns)  # noqa: S102
    return ns


def _text(*cells) -> str:
    return notebookscript.render("n.ipynb", notebookscript.extract(_nb(*cells)))


@pytest.mark.parametrize(
    "src",
    [
        "display(df)",
        "fig = object()  # 传进来的是图（matplotlib Figure）也不做事\ndisplay(fig)",
        "get_ipython().run_line_magic('matplotlib', 'inline')\nget_ipython().system('ls')\nget_ipython().magic('x')",
        "from IPython.display import display, Image, HTML\ndisplay(Image('a.png'))\ndisplay(HTML('<b>x</b>'))",
        "import IPython\nIPython.display.display(1)",
        "import IPython.display as d\nd.display(1)",
        "from IPython import get_ipython\nget_ipython().run_line_magic('x', 'y')",
        "if True:\n    from IPython.display import display\n    display(1)",
        "from IPython.display import *\ndisplay(1)",
    ],
)
def test_notebooks_that_only_fail_for_lack_of_ipython_run_without_it(src):
    df = "df = None\n" if "display(df)" in src else ""
    ns = _run(_text(("code", df + src)))
    assert isinstance(ns, dict)  # exec 没抛


def test_the_shim_is_only_added_when_the_names_are_used_and_otherwise_bytes_are_unchanged():
    plain = notebookscript.extract(_nb(("code", "import os\nx = 1")))
    assert plain.body == "# %%\nimport os\nx = 1\n"
    assert "_tavotto" not in plain.body
    used = notebookscript.extract(_nb(("code", "display(1)"))).body
    assert "def display" in used and "_tavotto_quiet" in used and "def get_ipython" not in used


def test_names_the_notebook_defines_itself_are_not_shadowed():
    body = notebookscript.extract(
        _nb(("code", "def display(x):\n    return 7\nr = display(1)"))
    ).body
    assert "_tavotto" not in body
    assert (
        _run(
            notebookscript.render(
                "n.ipynb",
                notebookscript.extract(
                    _nb(("code", "def display(x):\n    return 7\nr = display(1)"))
                ),
            )
        )["r"]
        == 7
    )


def test_only_ipython_imports_are_wrapped_nothing_else_is_touched():
    body = notebookscript.extract(
        _nb(
            (
                "code",
                "import os\nimport numpy as np\nfrom IPython.display import display\ndisplay(os)",
            )
        )
    ).body
    assert "import os\nimport numpy as np\n" in body
    assert body.count("try:") == 1 and "except ImportError:" in body
    # 只因为没装 IPython 才失败；别的 import 失败照旧失败（不被吞）
    with pytest.raises(ModuleNotFoundError):
        _run(_text(("code", "import definitely_not_installed_pkg_xyz")))


def test_with_ipython_present_the_real_import_wins_over_the_fallback():
    import sys
    import types
    from unittest import mock

    fake = types.ModuleType("IPython")
    fake_display = types.ModuleType("IPython.display")
    fake_display.display = lambda *a, **k: "real"
    fake.display = fake_display
    with mock.patch.dict(sys.modules, {"IPython": fake, "IPython.display": fake_display}):
        ns: dict = {}
        exec(
            compile(
                _text(("code", "from IPython.display import display\nr = display(1)")),
                "n.py",
                "exec",
            ),
            ns,
        )  # noqa: S102
    assert ns["r"] == "real"


def test_shim_is_part_of_the_fingerprint_so_replace_rules_stay_self_consistent(tmp_path):
    with assetimport.LOCK:
        first = notebookscript.place(
            tmp_path, "n.ipynb", notebookscript.extract(_nb(("code", "display(1)")))
        )
        again = notebookscript.place(
            tmp_path, "n.ipynb", notebookscript.extract(_nb(("code", "display(1)")))
        )
        newer = notebookscript.place(
            tmp_path, "n.ipynb", notebookscript.extract(_nb(("code", "display(2)")))
        )
    assert (first.status, again.status, newer.status) == ("created", "reused", "updated")


# ---------------------------------------------------------------- 替身的三个边界（Codex #1838）


def _py(*cells) -> str:
    return _text(*cells)


def test_future_imports_are_hoisted_before_the_shim_and_the_file_compiles():
    text = _py(("code", "from __future__ import annotations\ndisplay(1)"))
    compile(text, "n.py", "exec")
    assert text.index("from __future__ import annotations") < text.index("def display")
    _run(text)


def test_a_future_import_in_a_later_cell_is_hoisted_merged_and_deduplicated():
    text = _py(
        ("code", "x = 1"),
        ("code", "from __future__ import annotations"),
        ("code", "from __future__ import annotations, division\ny = 2"),
    )
    compile(text, "n.py", "exec")
    assert [ln for ln in text.splitlines() if ln.startswith("from __future__")] == [
        "from __future__ import annotations, division"
    ]
    first_code = next(ln for ln in text.splitlines() if ln and not ln.startswith("#"))
    assert first_code == "from __future__ import annotations, division"
    assert "# [notebook] 已把 from __future__" in text


def test_hoisting_future_imports_keeps_their_aliases():
    """`from __future__ import annotations as ann` 提前时别名不能丢：后面读 `ann` 的笔记本照旧能跑；
    同一特性的不同别名、带别名与不带别名各算各的绑定。"""
    text = _py(
        ("code", "x = 1"),
        ("code", "from __future__ import annotations as ann\nflag = ann.compiler_flag"),
        ("code", "from __future__ import annotations, division as dv\nother = dv.compiler_flag"),
    )
    assert [ln for ln in text.splitlines() if ln.startswith("from __future__")] == [
        "from __future__ import annotations as ann, annotations, division as dv"
    ]
    ns: dict = {}
    exec(compile(text, "nb.py", "exec"), ns)  # noqa: S102
    assert ns["flag"] == ns["ann"].compiler_flag and ns["other"] == ns["dv"].compiler_flag


def test_a_future_import_with_no_shim_still_compiles_when_it_was_not_in_the_first_cell():
    compile(
        _py(("code", "import os"), ("code", "from __future__ import annotations")), "n.py", "exec"
    )


def test_final_check_is_compile_so_an_indented_future_import_is_refused():
    with pytest.raises(notebookscript.NotebookError) as e:
        notebookscript.extract(_nb(("code", "if True:\n    from __future__ import annotations")))
    assert e.value.code == "notebook_syntax_error"


def test_top_level_await_is_still_accepted():
    notebookscript.extract(_nb(("code", "import asyncio\nawait asyncio.sleep(0)")))


def test_mixed_imports_only_wrap_the_ipython_names():
    text = _py(("code", "import math, IPython\nr = math.sqrt(4)"))
    assert _run(text)["r"] == 2.0
    text = _py(("code", "import math as m, IPython as ip, os\nr = m.sqrt(9)\nq = ip.display"))
    ns = _run(text)
    assert ns["r"] == 3.0 and ns["os"].__name__ == "os"


def test_a_mixed_import_still_fails_for_the_real_missing_package():
    with pytest.raises(ModuleNotFoundError, match="nosuchpkg_xyz"):
        _run(_py(("code", "import nosuchpkg_xyz, IPython")))


def test_a_function_parameter_named_display_does_not_count_as_a_global_definition():
    text = _py(("code", "def f(display):\n    return display\ndisplay(1)"))
    assert "def display" in text
    _run(text)


def test_a_local_display_inside_a_function_does_not_trigger_the_shim():
    text = _py(("code", "def f():\n    display = 3\n    return display\nr = f()"))
    assert "_tavotto" not in text and _run(text)["r"] == 3


def test_a_nested_function_reading_the_global_display_triggers_the_shim():
    text = _py(
        (
            "code",
            "def outer():\n    def inner():\n        return display(1)\n    return inner()\nr = outer()",
        )
    )
    assert "def display" in text and _run(text)["r"] is None


def test_a_nested_function_reading_an_outer_local_display_does_not():
    text = _py(
        (
            "code",
            "def outer(display):\n    def inner():\n        return display\n    return inner()\nr = outer(5)",
        )
    )
    assert "_tavotto" not in text


def test_a_module_level_binding_or_a_global_declared_assignment_counts():
    assert "_tavotto" not in _py(("code", "for display in [1]:\n    pass\ndisplay"))
    assert "_tavotto" not in _py(
        ("code", "def setup():\n    global display\n    display = print\nsetup()\ndisplay(1)")
    )


def test_a_local_assignment_in_a_function_does_not_define_the_global_display():
    text = _py(("code", "def f():\n    display = 3\n    return display\nf()\ndisplay(1)"))
    assert "def display" in text
    _run(text)


def test_scope_analysis_survives_a_stricter_symtable(monkeypatch):
    """Python 3.14 的 `symtable` 不收顶层 `await` 与不在文件头的 `__future__` 导入（3.13 及以前放行）：分析在等价副本上做，
    副本也走不通时退到保守判断——都不能因为分析工具的限制把笔记本整本拒掉，替身照样按需补上。"""
    cells = (
        ("code", "x = 1"),
        ("code", "from __future__ import annotations"),
        ("code", "async def f():\n    return 1\ny = await f()\ndisplay(y)"),
    )
    assert "def display" in _py(*cells)

    def boom(*_a, **_k):
        raise SyntaxError("stricter symtable")

    monkeypatch.setattr(notebookscript.symtable, "symtable", boom)
    text = _py(*cells)
    assert "def display" in text  # 保守判断：读到了、顶层没定义 → 补
    own = _py(("code", "def display(v):\n    return v\ndisplay(1)"))
    assert "已把" not in own and own.count("def display") == 1  # 笔记本自己定义的不被覆盖
