"""诊断包「脚本源码」附带脚本运行时从项目外加载的本地模块（ADR 0118 修订 2026-11-15）。

起因：组图脚本先 `sys.path.insert(0, 项目外的公共目录)` 再 `from shared_plot import draw`，出错的 `shared_plot.py` 在项目外，
包里没有它。判据的主语：**脚本这一次运行里、脚本自己加进 `sys.path` 的项目外目录里实际加载过的本地 `.py`**——
stdlib / 第三方包 / 运行前就在 `sys.path` 上的目录 / 项目内的都不是。分四层：渲染进程的纯函数、真 worker 的成功与失败两条路、
父进程的持久记录、诊断包收集（勾了收、没勾零字节、符号链接 / 敏感目录 / 项目内 / 脚本本身在项目外一律不收）。
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import threading
import types
import zipfile
from pathlib import Path

import pytest

from support.pipedrain import StderrDrain
from tavotto import app as m
from tavotto.engine import diagdetail, diagexternal, diagsend, figcapture, pool

EXT_CANARY = "EXT_MODULE_CANARY_q7x2"
EXT_SECRET = "EXT_SECRET_VALUE_zz91"


def _mod(path: Path) -> types.ModuleType:
    mod = types.ModuleType("m")
    mod.__file__ = str(path)
    return mod


def _write(path: Path, text: str = "X = 1\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _observe(project, modules, baseline, sys_path, **kw):
    return figcapture.observed_external_modules(
        str(project), modules, baseline, kw.pop("exclude_dirs", ()), sys_path=sys_path, **kw
    )


# ---------------------------------------------------------------------------
# 一、渲染进程的纯函数
# ---------------------------------------------------------------------------
class TestObservedExternalModules:
    def test_module_from_a_directory_the_script_added_is_recognised(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        ext = tmp_path / "公共代码"
        helper = _write(ext / "shared_plot.py")
        got = _observe(
            proj,
            {"shared_plot": _mod(helper)},
            baseline=[str(proj)],
            sys_path=[str(ext), str(proj)],
        )
        assert got == [os.path.realpath(helper)]

    def test_package_submodule_is_recognised(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        ext = tmp_path / "lib"
        sub = _write(ext / "pkg" / "sub.py")
        got = _observe(proj, {"pkg.sub": _mod(sub)}, [str(proj)], [str(ext), str(proj)])
        assert got == [os.path.realpath(sub)]

    def test_directories_present_before_the_script_ran_are_not_recognised(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        ext = tmp_path / "preexisting"
        helper = _write(ext / "h.py")
        got = _observe(proj, {"h": _mod(helper)}, [str(proj), str(ext)], [str(ext), str(proj)])
        assert got == []

    def test_project_stdlib_site_packages_and_engine_are_not_recognised(self, tmp_path):
        proj = tmp_path / "proj"
        inside = _write(proj / "inner.py")
        engine = tmp_path / "engine"
        eng = _write(engine / "figcapture_like.py")
        third = _write(tmp_path / "venv" / "lib" / "site-packages" / "thirdparty.py")
        dist = _write(tmp_path / "ext" / "dist-packages" / "other.py")
        paths = [str(proj), str(engine), str(third.parent), str(dist.parent)]
        mods = {
            "inner": _mod(inside),
            "eng": _mod(eng),
            "thirdparty": _mod(third),
            "other": _mod(dist),
            "os": _mod(Path(os.__file__)),
            "json": _mod(Path(json.__file__)),
        }
        got = _observe(
            proj, mods, [], paths + [os.path.dirname(os.__file__)], exclude_dirs=(str(engine),)
        )
        assert got == []

    def test_non_py_not_on_any_sys_path_and_missing_files_are_ignored(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        ext = tmp_path / "ext"
        so = _write(ext / "native.so", "binary")
        stray = _write(tmp_path / "stray" / "stray.py")  # 不在任何 sys.path 根下
        gone = ext / "gone.py"  # 文件已经不存在
        mods = {
            "native": _mod(so),
            "stray": _mod(stray),
            "gone": _mod(gone),
            "nofile": types.ModuleType("x"),
        }
        assert _observe(proj, mods, [], [str(ext)]) == []

    def test_module_removed_from_sys_modules_is_found_through_traceback_frames(self, tmp_path):
        """导入到一半抛错的模块会被解释器从 `sys.modules` 摘掉——它正是要查的那一个。"""
        proj = tmp_path / "proj"
        proj.mkdir()
        ext = tmp_path / "ext"
        helper = _write(ext / "broken.py")
        got = _observe(proj, {}, [str(proj)], [str(ext), str(proj)], extra_files=[str(helper)])
        assert got == [os.path.realpath(helper)]

    def test_result_is_bounded_and_deduplicated(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        ext = tmp_path / "ext"
        mods = {f"mod{i:03d}": _mod(_write(ext / f"mod{i:03d}.py")) for i in range(40)}
        got = _observe(proj, mods, [str(proj)], [str(ext)], extra_files=[mods["mod000"].__file__])
        assert len(got) == figcapture.EXTERNAL_MODULES_MAX == 20
        assert len(set(got)) == len(got)


# ---------------------------------------------------------------------------
# 二、真 worker：成功与失败两条路都把它带回父进程
# ---------------------------------------------------------------------------
try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None


def _spawn(script: Path, figs: Path, tmp_path: Path):
    proc = subprocess.Popen(
        [
            WORKER_PY,
            str(pool.WORKER_PY),
            "--script",
            str(script),
            "--figures-dir",
            str(figs),
            "--out-dir",
            str(tmp_path / "out"),
            "--sandbox",
            str(tmp_path / "sandbox"),
            "--entry",
            "__main__",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
    )
    proc.stderr_drain = StderrDrain(proc)
    return proc


def _build(proc) -> dict:
    proc.stdin.write(
        json.dumps({"protocol_version": 1, "request_id": "r1", "cmd": "build", "payload": {}})
        + "\n"
    )
    proc.stdin.flush()
    box: list = []
    t = threading.Thread(target=lambda: box.append(proc.stdout.readline()), daemon=True)
    t.start()
    t.join(120)
    assert box and box[0], f"worker 无响应\n{proc.stderr_drain.tail(4000, wait=5)}"
    return json.loads(box[0])


def _script_with_helper(tmp_path: Path, tail: str) -> tuple[Path, Path, Path]:
    figs = tmp_path / "figures"
    figs.mkdir()
    ext = tmp_path / "公共代码"
    helper = _write(ext / "shared_plot.py", "def draw():\n    return 1\n")
    script = _write(
        figs / "make_panels.py",
        "import sys\n"
        f"sys.path.insert(0, {str(ext)!r})\n"
        "from shared_plot import draw\n"
        "import matplotlib.pyplot as plt\n"
        f"{tail}",
    )
    return figs, script, helper


@pytest.mark.skipif(WORKER_PY is None, reason="找不到装有 matplotlib 的解释器")
class TestRealWorker:
    def _run(self, tmp_path, tail):
        figs, script, helper = _script_with_helper(tmp_path, tail)
        proc = _spawn(script, figs, tmp_path)
        try:
            return _build(proc), helper
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)

    def test_success_build_reports_the_helper(self, tmp_path):
        resp, helper = self._run(
            tmp_path,
            "draw()\nfig, ax = plt.subplots()\nax.plot([0, 1], [1, 0])\nfig.savefig('a.pdf')\n",
        )
        assert resp["ok"], resp
        assert resp["external_modules"] == [os.path.realpath(helper)]

    def test_failure_after_import_reports_the_helper(self, tmp_path):
        resp, helper = self._run(tmp_path, "draw()\nraise RuntimeError('boom after import')\n")
        assert resp["ok"] is False
        assert resp["error"]["code"] == "script_error"
        assert resp["error"]["external_modules"] == [os.path.realpath(helper)]

    def test_nonzero_system_exit_reports_the_helper(self, tmp_path):
        resp, helper = self._run(tmp_path, "draw()\nsys.exit(3)\n")
        assert resp["ok"] is False
        assert resp["error"]["external_modules"] == [os.path.realpath(helper)]

    def test_import_time_failure_reports_the_helper(self, tmp_path):
        """出错的 helper 在导入时就抛：它被解释器从 `sys.modules` 摘掉了，只剩 traceback 帧还指着它。"""
        figs = tmp_path / "figures"
        figs.mkdir()
        ext = tmp_path / "pub"
        helper = _write(ext / "shared_plot.py", "raise ValueError('bad at import time')\n")
        script = _write(
            figs / "make_panels.py",
            f"import sys\nsys.path.insert(0, {str(ext)!r})\nimport shared_plot\n",
        )
        proc = _spawn(script, figs, tmp_path)
        try:
            resp = _build(proc)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
        assert resp["ok"] is False
        assert resp["error"]["external_modules"] == [os.path.realpath(helper)]

    def test_script_without_external_modules_has_unchanged_response_shape(self, tmp_path):
        figs = tmp_path / "figures"
        figs.mkdir()
        script = _write(
            figs / "p.py",
            "import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nfig.savefig('a.pdf')\n",
        )
        proc = _spawn(script, figs, tmp_path)
        try:
            resp = _build(proc)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
        assert resp["ok"] and "external_modules" not in resp


# ---------------------------------------------------------------------------
# 三、父进程的持久记录
# ---------------------------------------------------------------------------
@pytest.fixture
def datadir(tmp_path, monkeypatch):
    d = tmp_path / "appdata"
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(d))
    return d


class TestPersistence:
    def test_round_trip_and_store_lives_in_the_app_data_dir_not_the_project(
        self, tmp_path, datadir
    ):
        proj = tmp_path / "proj"
        proj.mkdir()
        diagexternal.note(proj, "make_panels.py", ["/x/shared_plot.py"])
        assert diagexternal.modules_for(proj, "make_panels.py") == ["/x/shared_plot.py"]
        assert diagexternal.store_path().parent == datadir
        assert list(proj.iterdir()) == []  # 项目文件夹里一个字节都没多
        assert [p.name for p in datadir.iterdir()] == [diagexternal.STORE_NAME]  # 无遗留临时文件

    def test_absolute_script_path_is_stored_project_relative(self, tmp_path, datadir):
        proj = tmp_path / "proj"
        (proj / "sub").mkdir(parents=True)
        diagexternal.note(proj, str(proj / "sub" / "a.py"), ["/x/h.py"])
        assert diagexternal.modules_for(proj, "sub/a.py") == ["/x/h.py"]
        assert diagexternal.modules_for(proj, "other.py") == []

    def test_latest_run_wins_and_empty_clears(self, tmp_path, datadir):
        diagexternal.note(tmp_path, "a.py", ["/x/one.py"])
        diagexternal.note(tmp_path, "a.py", ["/x/two.py"])
        assert diagexternal.modules_for(tmp_path, "a.py") == ["/x/two.py"]
        diagexternal.note(tmp_path, "a.py", [])
        assert diagexternal.modules_for(tmp_path, "a.py") == []

    def test_nothing_to_record_never_touches_the_disk(self, tmp_path, datadir):
        diagexternal.note(tmp_path, "a.py", [])
        diagexternal.note(tmp_path, "a.py", None)
        assert not datadir.exists()

    def test_entries_expire_after_30_days(self, tmp_path, datadir):
        diagexternal.note(tmp_path, "a.py", ["/x/h.py"], now=1000.0)
        assert diagexternal.modules_for(tmp_path, "a.py", now=1000.0 + diagexternal.RETAIN_S - 1)
        assert (
            diagexternal.modules_for(tmp_path, "a.py", now=1000.0 + diagexternal.RETAIN_S + 1) == []
        )
        # 过期的在下一次写入时被丢掉
        diagexternal.note(tmp_path, "b.py", ["/x/k.py"], now=1000.0 + diagexternal.RETAIN_S + 5)
        raw = json.loads(diagexternal.store_path().read_text(encoding="utf-8"))
        assert "a.py" not in json.dumps(raw)

    def test_bounds_scripts_per_project_and_modules_per_script(self, tmp_path, datadir):
        for i in range(diagexternal.MAX_SCRIPTS_PER_PROJECT + 10):
            diagexternal.note(tmp_path, f"s{i:03d}.py", ["/x/h.py"], now=1000.0 + i)
        raw = json.loads(diagexternal.store_path().read_text(encoding="utf-8"))
        (scripts,) = raw["projects"].values()
        assert len(scripts) == diagexternal.MAX_SCRIPTS_PER_PROJECT
        assert (
            "s000.py" not in scripts
            and f"s{diagexternal.MAX_SCRIPTS_PER_PROJECT + 9:03d}.py" in scripts
        )
        diagexternal.note(tmp_path, "many.py", [f"/x/m{i}.py" for i in range(100)])
        assert (
            len(diagexternal.modules_for(tmp_path, "many.py"))
            == diagexternal.MAX_MODULES_PER_SCRIPT
        )

    @pytest.mark.parametrize(
        "junk", ["", "{", "[]", '{"schema": 99, "projects": {}}', '{"schema": 1, "projects": 5}']
    )
    def test_corrupt_store_reads_as_empty_and_does_not_break_writing(self, tmp_path, datadir, junk):
        datadir.mkdir(parents=True)
        diagexternal.store_path().write_text(junk, encoding="utf-8")
        assert diagexternal.modules_for(tmp_path, "a.py") == []
        diagexternal.note(tmp_path, "a.py", ["/x/h.py"])
        assert diagexternal.modules_for(tmp_path, "a.py") == ["/x/h.py"]

    def test_note_never_raises_even_when_the_data_dir_is_unwritable(self, tmp_path, monkeypatch):
        blocker = tmp_path / "file"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setenv("TAVOTTO_DATA_DIR", str(blocker / "sub"))  # 是文件，建不了目录
        diagexternal.note(tmp_path, "a.py", ["/x/h.py"])
        assert diagexternal.modules_for(tmp_path, "a.py") == []

    def test_paths_are_consumed_by_the_parent_and_never_stay_in_the_error_or_response(
        self, tmp_path, datadir
    ):
        """err.extra / build 响应是诊断、投影、遥测读得到的那一份：项目外的绝对路径只进持久记录，不留在里面。"""
        helper = "/somewhere/公共代码/shared_plot.py"
        w = types.SimpleNamespace(
            figures_dir=str(tmp_path),
            script_name="a.py",
            python="",
            python_source="",
            _log_tail=lambda *a, **k: "",
        )
        resp = {
            "ok": False,
            "error": {
                "code": "script_error",
                "message": "boom",
                "traceback": "",
                "external_modules": [helper],
            },
        }
        err = pool.EngineWorker._error_of(w, resp)
        assert "external_modules" not in (err.extra or {})
        assert helper not in repr(vars(err))
        assert diagexternal.modules_for(tmp_path, "a.py") == [helper]
        ok = {"ok": True, "external_modules": [helper]}
        pool._note_external_modules(w, ok, failed=False)
        assert "external_modules" not in ok
        wd = types.SimpleNamespace(extra={"external_modules": [helper], "known": ["x"]})
        pool._note_external_modules(w, wd.extra, failed=True)
        assert wd.extra == {"known": ["x"]}

    def test_pool_hook_records_on_success_and_only_on_present_failure_facts(
        self, tmp_path, datadir
    ):
        w = types.SimpleNamespace(figures_dir=str(tmp_path), script_name="a.py")
        pool._note_external_modules(w, {"external_modules": ["/x/h.py"]}, failed=False)
        assert diagexternal.modules_for(tmp_path, "a.py") == ["/x/h.py"]
        pool._note_external_modules(
            w, {"code": "script_error"}, failed=True
        )  # 失败没带：不当作「没有」
        assert diagexternal.modules_for(tmp_path, "a.py") == ["/x/h.py"]
        pool._note_external_modules(w, {}, failed=False)  # 成功且没有：清掉
        assert diagexternal.modules_for(tmp_path, "a.py") == []


# ---------------------------------------------------------------------------
# 四、诊断包收集
# ---------------------------------------------------------------------------
def _collect(proj, lookup, scripts=("fig.py",), **kw):
    src = diagdetail.Sources(
        project={"figures_dir": str(proj)},
        scripts=list(scripts),
        external_lookup=lookup,
        **kw,
    )
    items = diagdetail.collect_source(
        src, diagdetail.DetailOptions(source=True), diagdetail.Scrubber()
    )
    index = json.loads(next(i.data for i in items if i.path == "index.json"))
    return items, index


def _external_rows(index):
    return [r for r in index["scripts"] if r.get("reason") == "imported_external"]


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "proj"
    _write(p / "fig.py", "FIG = 1\n")
    return p


class TestCollect:
    def test_ticked_collects_the_imported_external_module(self, tmp_path, proj):
        helper = _write(tmp_path / "公共代码" / "shared_plot.py", f"{EXT_CANARY} = 1\n")
        items, index = _collect(proj, lambda rel: [str(helper)] if rel == "fig.py" else [])
        (row,) = _external_rows(index)
        fig_row = next(r for r in index["scripts"] if r["script"] == "fig.py")
        assert row["imported_by"] == fig_row["id"]
        assert "imported_external" in index["criterion"]
        member = next(i for i in items if i.path == row["file"])
        assert EXT_CANARY.encode() in member.data
        # 成员名是虚拟写法，不带绝对路径
        assert row["file"].startswith("ext-01-external__") and str(tmp_path) not in row["file"]

    def test_index_never_carries_the_unscrubbed_absolute_path(self, tmp_path, proj, monkeypatch):
        home = tmp_path / "canaryhome9"
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        helper = _write(home / "共享" / "shared_plot.py", "X = 1\n")
        scrub = diagdetail.Scrubber()
        assert scrub.text(str(home)) != str(home)  # 反证：家目录替换确实在起作用
        src = diagdetail.Sources(
            project={"figures_dir": str(proj)},
            scripts=["fig.py"],
            external_lookup=lambda r: [str(helper)],
        )
        items = diagdetail.collect_source(src, diagdetail.DetailOptions(source=True), scrub)
        blob = b"".join(i.path.encode() + i.data for i in items).decode()
        assert str(home) not in blob and "canaryhome9" not in blob
        (row,) = _external_rows(json.loads(next(i.data for i in items if i.path == "index.json")))
        assert row["script"].startswith("~")

    def test_non_ascii_directory_and_file_names(self, tmp_path, proj):
        helper = _write(tmp_path / "公共代码 二期" / "装配.py", "Y = 2\n")
        items, index = _collect(proj, lambda rel: [str(helper)])
        (row,) = _external_rows(index)
        assert next(i for i in items if i.path == row["file"]).data == b"Y = 2\n"

    def test_secrets_in_the_module_are_scrubbed_like_any_script(self, tmp_path, proj):
        helper = _write(
            tmp_path / "pub" / "shared_plot.py", f'api_key = "{EXT_SECRET}"\n# token={EXT_SECRET}\n'
        )
        items, _index = _collect(proj, lambda rel: [str(helper)])
        blob = b"".join(i.data for i in items).decode()
        assert EXT_SECRET not in blob

    @pytest.mark.skipif(os.name == "nt", reason="符号链接需要权限")
    def test_symlinked_file_or_directory_is_refused(self, tmp_path, proj):
        real = _write(tmp_path / "real" / "shared_plot.py", f"{EXT_CANARY} = 1\n")
        link_file = tmp_path / "pub" / "link.py"
        link_file.parent.mkdir()
        link_file.symlink_to(real)
        link_dir = tmp_path / "linkdir"
        link_dir.symlink_to(real.parent, target_is_directory=True)
        items, index = _collect(
            proj, lambda rel: [str(link_file), str(link_dir / "shared_plot.py")]
        )
        assert _external_rows(index) == []
        assert EXT_CANARY.encode() not in b"".join(i.data for i in items)

    def test_project_internal_sensitive_nonpy_relative_and_missing_are_refused(
        self, tmp_path, proj, monkeypatch
    ):
        home = tmp_path / "home"
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        inside = _write(proj / "inner.py", f"{EXT_CANARY} = 1\n")
        sensitive = _write(home / ".ssh" / "keys.py", f"{EXT_CANARY} = 2\n")
        notpy = _write(tmp_path / "pub" / "data.txt", f"{EXT_CANARY} = 3\n")
        cands = [
            str(inside),
            str(sensitive),
            str(notpy),
            "relative/h.py",
            str(tmp_path / "gone.py"),
        ]
        items, index = _collect(proj, lambda rel: cands)
        assert _external_rows(index) == []
        assert EXT_CANARY.encode() not in b"".join(i.data for i in items)

    def test_the_same_file_is_collected_once_and_total_count_cap_holds(self, tmp_path, proj):
        _write(proj / "fig2.py", "FIG2 = 1\n")
        helper = _write(tmp_path / "pub" / "shared_plot.py", "Z = 1\n")
        _items, index = _collect(
            proj, lambda rel: [str(helper), str(helper)], scripts=("fig.py", "fig2.py")
        )
        assert len(_external_rows(index)) == 1

    def test_over_the_shared_count_cap_is_recorded_not_silently_dropped(
        self, tmp_path, proj, monkeypatch
    ):
        monkeypatch.setattr(diagdetail, "SOURCE_FILES", 3)
        _write(proj / "fig2.py", "FIG2 = 1\n")
        helpers = [str(_write(tmp_path / "pub" / f"h{i}.py", f"H{i} = 1\n")) for i in range(4)]
        _items, index = _collect(
            proj,
            lambda rel: helpers[:2] if rel == "fig.py" else helpers[2:],
            scripts=("fig.py", "fig2.py"),
        )
        rows = _external_rows(index)
        assert len(rows) == 4
        assert (
            sum(1 for r in rows if r.get("skipped_over_count_cap")) == 3
        )  # 3 - 2 个脚本 = 只剩 1 个名额

    def test_script_itself_outside_the_project_is_still_refused(self, tmp_path, proj):
        outside = _write(tmp_path / "outside.py", f"{EXT_CANARY} = 1\n")
        items, index = _collect(proj, lambda rel: [], scripts=("../outside.py", "fig.py"))
        assert [r["script"] for r in index["scripts"]] == ["fig.py"]
        assert EXT_CANARY.encode() not in b"".join(i.data for i in items)
        assert outside.exists()

    def test_oversized_module_is_truncated_with_the_same_per_file_cap(self, tmp_path, proj):
        big = _write(
            tmp_path / "pub" / "big.py", "A = 1\n" * (diagdetail.SOURCE_FILE_CAP // 6 + 10)
        )
        items, index = _collect(proj, lambda rel: [str(big)])
        (row,) = _external_rows(index)
        assert row["bytes"] > diagdetail.SOURCE_FILE_CAP
        assert b"[truncated" in next(i for i in items if i.path == row["file"]).data


# ---------------------------------------------------------------------------
# 五、整条链路（发送包）：没勾「脚本源码」= 零字节
# ---------------------------------------------------------------------------
@pytest.fixture
def client(monkeypatch):
    for name in (
        diagsend.ENV_ENABLE,
        diagsend.ENV_HARD_OFF,
        diagsend.ENV_ENDPOINT,
        diagsend.ENV_DEV_LOOPBACK,
    ):
        monkeypatch.delenv(name, raising=False)
    m.app.config["TESTING"] = True
    m.reset_projects()
    diagsend.reset_for_tests()
    diagdetail.REQUESTS.clear()
    yield m.app.test_client()
    diagsend.reset_for_tests()
    m.reset_projects()


def _prepare(client, **opts):
    body = {"detail_options": opts} if opts else {}
    r = client.post("/api/diagnostics/send/prepare", json=body)
    assert r.status_code == 200, r.get_json()
    prep = r.get_json()
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/diagnostics/send/{prep['id']}/bundle").data))
    return z


def _all_bytes(z: zipfile.ZipFile) -> bytes:
    out = []
    for n in z.namelist():
        raw = z.read(n)
        out.append(n.encode() + raw)
        if n.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(raw)) as inner:
                out.extend(i.encode() + inner.read(i) for i in inner.namelist())
    return b"\n".join(out)


class TestBundleEndToEnd:
    @pytest.fixture
    def wired(self, client, tmp_path, datadir, monkeypatch):
        proj = tmp_path / "proj"
        _write(
            proj / "fig_one.py",
            "import matplotlib.pyplot as plt\n\n\ndef main():\n    fig, ax = plt.subplots()\n"
            "    fig.savefig('fig_one.pdf')\n    return fig\n",
        )
        assert client.post("/api/projects/open", json={"path": str(proj)}).status_code == 200
        helper = _write(tmp_path / "公共代码" / "ext_shared_helper.py", f"{EXT_CANARY} = 1\n")
        diagexternal.note(proj, "fig_one.py", [os.path.realpath(helper)])
        return proj, helper

    def test_unticked_is_zero_bytes_of_the_external_module(self, client, wired):
        _proj, helper = wired
        z = _prepare(client)  # 没勾任何额外内容
        blob = _all_bytes(z)
        assert EXT_CANARY.encode() not in blob
        assert b"ext_shared_helper" not in blob and "公共代码".encode() not in blob
        assert "optional-source.zip" not in z.namelist()

    def test_ticked_attaches_it(self, client, wired):
        _proj, helper = wired
        z = _prepare(client, source=True)
        inner = zipfile.ZipFile(io.BytesIO(z.read("optional-source.zip")))
        index = json.loads(inner.read("index.json"))
        (row,) = _external_rows(index)
        assert EXT_CANARY.encode() in inner.read(row["file"])
        assert "ext_shared_helper.py" in row["script"]
