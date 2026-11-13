"""外来配置保留引用恢复：精确 argv、本机证据隔离与共同写锁。"""

from __future__ import annotations

import errno
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tavotto.engine import atomicio, execspec, layoutsession, runconfig

CID = "rc_0123456789ab"
TOKENS = ["", " ", "中文", "--", "-3", "--opt=value", '"quoted"', "--opt", "--opt"]
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "数据目录"))
    path = tmp_path / "搬迁后 中文项目"
    path.mkdir()
    runconfig.forget_secrets()
    return path


def record(cid=CID, *, script="科学/原脚本.py", argv=None):
    return {"id": cid, "script": script, "argv": list(TOKENS) if argv is None else argv}


def test_import_preserves_reference_exact_tokens_without_reading_or_executing_script(
    project, monkeypatch
):
    script = project / "科学" / "原脚本.py"
    script.parent.mkdir()
    script.write_bytes(b"raise RuntimeError('must never execute')")
    original_open = Path.open

    def forbidden_read(path, *args, **kwargs):
        if path == script:
            pytest.fail("导入配置不得读取脚本")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", forbidden_read)
    result = runconfig.import_portable(project, [record()])
    assert isinstance(result, tuple) and len(result) == 1
    cfg = result[0]
    assert cfg.id == CID and cfg.script == "科学/原脚本.py"
    assert cfg.argv == tuple(TOKENS) and cfg.sensitive is False
    assert runconfig.selection(project, CID, script=cfg.script).argv == tuple(TOKENS)
    assert runconfig.default_selection(project, cfg.script) is None
    data = json.loads(runconfig.store_path(project).read_text(encoding="utf-8"))
    assert data["configs"][CID]["argv"] == TOKENS
    assert data.get("defaults", {}) == {} and data.get("launches", {}) == {}
    assert data.get("captures", {}) == {}


def test_whole_batch_id_conflict_publishes_none_and_preserves_original_bytes(project):
    original = runconfig.put(project, "existing.py", ["original"])
    path = runconfig.store_path(project)
    before = path.read_bytes()
    incoming = [record(), record(original.id, script="other.py", argv=["changed"])]
    with pytest.raises(runconfig.RunConfigError):
        runconfig.import_portable(project, incoming)
    assert path.read_bytes() == before
    with pytest.raises(runconfig.RunConfigMissing):
        runconfig.get(project, CID)


def launch_record(cid):
    return {
        "interpreter": {"path": "not-an-executable", "source": "system", "generation": "old"},
        "launcher": {"kind": "direct"},
        "env_state": "old-environment",
        "cwd_mode": "sandbox",
        "run_config": cid,
        "run_sensitive": False,
        "recorded_at": 1,
        "source": {"script_sha1": "a" * 40, "basis": {}},
    }


def test_import_only_changes_configs_and_preserves_all_other_original_values(project):
    old = runconfig.put(project, "existing.py", ["original"], source="mcp")
    path = runconfig.store_path(project)
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(
        defaults={"existing.py": old.id},
        launches={"existing.py": launch_record(old.id)},
        captures={"existing.py": {"runs": {old.id: ["figure"]}, "legacy_stems": ["legacy"]}},
        unknown_metadata={"kept": [1, "unchanged"]},
    )
    atomicio.write_json(path, data)
    runconfig.import_portable(project, [record()])
    after = json.loads(path.read_text(encoding="utf-8"))
    assert {k: v for k, v in after.items() if k != "configs"} == {
        k: v for k, v in data.items() if k != "configs"
    }
    assert after["configs"][old.id] == data["configs"][old.id]
    assert runconfig.default_selection(project, "existing.py").config_id == old.id
    assert runconfig.launch_record(project, "existing.py") == data["launches"]["existing.py"]
    assert sorted(after["configs"]) == sorted([old.id, CID])
    assert list(project.iterdir()) == [], "配置恢复不写用户项目或授予执行环境"


@pytest.mark.parametrize("optional", [{}, {"launches": {}, "captures": {}}])
def test_absent_and_empty_evidence_fields_remain_distinct(project, optional):
    path = runconfig.store_path(project)
    data = {"version": 1, "configs": {}, **optional}
    atomicio.write_json(path, data)
    runconfig.import_portable(project, [record()])
    after = json.loads(path.read_text(encoding="utf-8"))
    assert {k: v for k, v in after.items() if k != "configs"} == {
        k: v for k, v in data.items() if k != "configs"
    }


def test_identical_reimport_is_a_byte_exact_no_write_even_at_capacity(project, monkeypatch):
    first = runconfig.import_portable(project, [record()])[0]
    path = runconfig.store_path(project)
    before = path.read_bytes()
    monkeypatch.setattr(runconfig, "MAX_CONFIGS", 0)

    def forbidden_write(*_args, **_kwargs):
        pytest.fail("幂等复用不得重写登记")

    monkeypatch.setattr(runconfig, "_write", forbidden_write)
    again = runconfig.import_portable(project, [record(), record()])
    assert again == (first,) and path.read_bytes() == before


def test_matching_existing_non_sensitive_record_keeps_original_metadata(project):
    cfg = runconfig.put(project, "scientific.py", TOKENS, source="mcp")
    path = runconfig.store_path(project)
    before = path.read_bytes()
    imported = runconfig.import_portable(project, [record(cfg.id, script=cfg.script)])
    assert imported == (cfg,) and imported[0].source == "mcp"
    assert path.read_bytes() == before


def test_equal_argv_with_different_ids_preserves_both_explicit_references(project):
    first = runconfig.put(project, "scientific.py", TOKENS)
    imported = runconfig.import_portable(project, [record(script=first.script)])[0]
    assert imported.id == CID != first.id
    assert runconfig.get(project, first.id).argv == imported.argv
    assert len(runconfig._read(project)["configs"]) == 2


@pytest.mark.parametrize("forget", [False, True])
def test_sensitive_existing_id_never_becomes_a_persistent_plaintext_record(project, forget):
    secret = runconfig.put(project, "secret.py", TOKENS, sensitive=True)
    path = runconfig.store_path(project)
    before = path.read_bytes()
    if forget:
        runconfig.forget_secrets()
    with pytest.raises(runconfig.RunConfigSecretMissing) as error:
        runconfig.import_portable(project, [record(secret.id, script=secret.script)])
    assert error.value.reason == "portable_secret_conflict"
    assert path.read_bytes() == before
    if not forget:
        assert runconfig.get(project, secret.id).argv == tuple(TOKENS)


def test_capacity_refuses_entire_batch_without_pruning_existing_or_new_ids(project, monkeypatch):
    old = runconfig.put(project, "existing.py", ["original"])
    path = runconfig.store_path(project)
    before = path.read_bytes()
    monkeypatch.setattr(runconfig, "MAX_CONFIGS", 2)
    with pytest.raises(runconfig.RunConfigUnsupported) as error:
        runconfig.import_portable(project, [record(), record("rc_ffffffffffff")])
    assert error.value.reason == "portable_capacity"
    assert path.read_bytes() == before and runconfig.get(project, old.id)


@pytest.mark.parametrize(
    "records",
    [
        None,
        {},
        "records",
        [None],
        [{}],
        [{"id": CID, "script": "s.py", "argv": ["ok"], "sensitive": False}],
        [{"id": "rc_bad", "script": "s.py", "argv": ["ok"]}],
        [{"id": "rc_0123456789AB", "script": "s.py", "argv": ["ok"]}],
        [{"id": 42, "script": "s.py", "argv": ["ok"]}],
        [{"id": CID, "script": None, "argv": ["ok"]}],
    ],
)
def test_closed_invalid_records_create_no_registry_or_lock(project, records):
    path = runconfig.store_path(project)
    with pytest.raises(runconfig.RunConfigError):
        runconfig.import_portable(project, records)
    assert not path.exists() and not path.parent.exists()


@pytest.mark.parametrize(
    "script",
    [
        "",
        "../escape.py",
        "/absolute.py",
        "a//b.py",
        "a/./b.py",
        "C:/s.py",
        "a\\s.py",
        "CON.py",
        "s.py ",
        "a\x00b",
    ],
)
def test_script_identity_is_portable_relative_and_not_repaired(project, script):
    with pytest.raises(runconfig.RunConfigError) as error:
        runconfig.import_portable(project, [record(script=script)])
    assert error.value.reason == "portable_script_invalid"
    assert not runconfig.store_path(project).parent.exists()


@pytest.mark.parametrize(
    "argv",
    [None, [], "--shell text", [1], ["a\x00b"], ["x"] * (execspec.MAX_ARGV_TOKENS + 1)],
)
def test_argv_uses_existing_exact_validation_and_empty_argv_has_no_reference(project, argv):
    row = record()
    row["argv"] = argv
    with pytest.raises(runconfig.InvalidArgv):
        runconfig.import_portable(project, [row])
    assert not runconfig.store_path(project).parent.exists()


def test_batch_duplicate_conflict_is_validated_before_any_registry_write(project):
    with pytest.raises(runconfig.RunConfigError):
        runconfig.import_portable(project, [record(), record(argv=["different"])])
    assert not runconfig.store_path(project).parent.exists()


@pytest.mark.parametrize("field,value", [("script", "bad\ud800.py"), ("argv", ["bad\ud800"])])
def test_escaped_json_surrogates_fail_as_runconfig_errors_before_any_store_write(
    project, field, value
):
    row = record()
    row[field] = value
    incoming = json.loads(json.dumps([row]))
    with pytest.raises(runconfig.RunConfigError):
        runconfig.import_portable(project, incoming)
    assert not runconfig.store_path(project).parent.exists()


def test_unserializable_existing_unknown_value_is_not_dropped_or_replaced(project):
    path = runconfig.store_path(project)
    path.parent.mkdir(parents=True)
    raw = b'{"version":1,"configs":{},"unknown":"\\ud800"}'
    path.write_bytes(raw)
    with pytest.raises(runconfig.RunConfigUnreadable) as error:
        runconfig.import_portable(project, [record()])
    assert error.value.reason == "portable_write_failed"
    assert path.read_bytes() == raw


@pytest.mark.parametrize(
    "raw,error_class",
    [
        (b"truncated{", runconfig.RunConfigUnreadable),
        (b'{"version":1,"configs":[]}', runconfig.RunConfigUnreadable),
        (b'{"version":1,"configs":{},"defaults":[]}', runconfig.RunConfigUnreadable),
        (b'{"version":1,"configs":{},"launches":{"s.py":{}}}', runconfig.RunConfigUnreadable),
        (b'{"version":1,"configs":{},"captures":{"s.py":{}}}', runconfig.RunConfigUnreadable),
        (b'{"version":1,"configs":{},"launches":[]}', runconfig.RunConfigUnreadable),
        (b'{"version":1,"configs":{},"captures":[]}', runconfig.RunConfigUnreadable),
        (b'{"version":2,"configs":{}}', runconfig.RunConfigUnsupported),
    ],
)
def test_existing_bad_or_future_store_is_never_repaired_or_replaced(project, raw, error_class):
    path = runconfig.store_path(project)
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    with pytest.raises(error_class):
        runconfig.import_portable(project, [record()])
    assert path.read_bytes() == raw


def test_empty_batch_still_validates_store_but_creates_no_json_when_absent(project):
    assert runconfig.import_portable(project, []) == ()
    path = runconfig.store_path(project)
    assert not path.exists()
    path.write_bytes(b"broken")
    with pytest.raises(runconfig.RunConfigUnreadable):
        runconfig.import_portable(project, [])
    assert path.read_bytes() == b"broken"


def test_write_failure_preserves_bytes_does_not_leak_argv_and_releases_ownership(
    project, monkeypatch
):
    runconfig.put(project, "old.py", ["old"])
    path = runconfig.store_path(project)
    before = path.read_bytes()
    real_replace = atomicio.os.replace

    def fail(source, target):
        if Path(target) == path:
            raise OSError(errno.ENOSPC, "disk full")
        return real_replace(source, target)

    with monkeypatch.context() as context:
        context.setattr(atomicio.os, "replace", fail)
        with pytest.raises(runconfig.RunConfigUnreadable) as error:
            runconfig.import_portable(project, [record(argv=["ARGV_CANARY"])])
        assert "ARGV_CANARY" not in json.dumps(error.value.to_payload())
        assert error.value.reason == "portable_write_failed"
    assert path.read_bytes() == before
    assert not getattr(runconfig._STORE_LOCKS, "held", ())
    assert runconfig.import_portable(project, [record()])[0].id == CID


def test_lock_permission_failure_cannot_fall_back_to_thread_only_writer(project, monkeypatch):
    runconfig.put(project, "old.py", ["old"])
    path = runconfig.store_path(project)
    before = path.read_bytes()

    def denied(*_args, **_kwargs):
        raise PermissionError(errno.EACCES, "no lock permission")

    monkeypatch.setattr(layoutsession, "acquire_file_lock", denied)
    with pytest.raises(runconfig.RunConfigUnreadable) as error:
        runconfig.import_portable(project, [record()])
    assert error.value.reason == "store_lock_unavailable"
    assert path.read_bytes() == before
    assert not getattr(runconfig._STORE_LOCKS, "held", ())


@pytest.mark.skipif(os.name != "nt", reason="Windows 真实只读文件替换权限边界")
def test_real_windows_readonly_registry_rejects_import_without_replacing_bytes(project):
    runconfig.put(project, "old.py", ["old"])
    path = runconfig.store_path(project)
    before = path.read_bytes()
    path.chmod(stat.S_IREAD)
    try:
        with pytest.raises(runconfig.RunConfigUnreadable):
            runconfig.import_portable(project, [record()])
        assert path.read_bytes() == before
    finally:
        path.chmod(stat.S_IREAD | stat.S_IWRITE)


def test_common_store_guard_remains_reentrant_and_releases_one_file_lock(project, monkeypatch):
    acquire, release = layoutsession.acquire_file_lock, layoutsession.release_file_lock
    events = []

    def taking(*args):
        fd = acquire(*args)
        events.append(("acquire", fd))
        return fd

    def releasing(fd):
        events.append(("release", fd))
        return release(fd)

    monkeypatch.setattr(layoutsession, "acquire_file_lock", taking)
    monkeypatch.setattr(layoutsession, "release_file_lock", releasing)
    with runconfig._store_guard(project):
        runconfig.import_portable(project, [record()])
        assert runconfig.get(project, CID).id == CID
        runconfig.put(project, "other.py", ["other"])
    assert [name for name, _ in events] == ["acquire", "release"]
    assert events[0][1] == events[1][1]


def test_real_cross_process_lock_timeout_is_explicit_and_does_not_write(project, tmp_path):
    runconfig.put(project, "old.py", ["old"])
    path = runconfig.store_path(project)
    before = path.read_bytes()
    script = f"""
import json,sys
sys.path.insert(0,{str(REPO / "src")!r})
from tavotto.engine import layoutsession,runconfig
layoutsession.LOCK_TIMEOUT_S=.08
try:
    runconfig.import_portable({str(project)!r},[{{'id':{CID!r},'script':'portable.py','argv':['portable']}}])
except runconfig.RunConfigUnreadable as exc:
    print(json.dumps({{'code':exc.code,'reason':exc.reason}}))
else:
    raise AssertionError('contending writer bypassed lock')
"""
    with runconfig._store_guard(project):
        child = subprocess.run(
            [sys.executable, "-I", "-c", script],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        )
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout) == {
        "code": "run_config_unreadable",
        "reason": "store_lock_unavailable",
    }
    assert path.read_bytes() == before
    assert runconfig.import_portable(project, [record()])[0].id == CID


@pytest.mark.parametrize("entry", ["set_default", "commit_success"])
@pytest.mark.parametrize("with_config", [False, True])
def test_success_fallback_on_unsupported_lock_preserves_original_noarg_contract(
    project, monkeypatch, entry, with_config
):
    cfg = runconfig.put(project, "old.py", ["old"])
    runconfig.set_default(project, "old.py", cfg.id)
    runconfig.set_launch_record(project, "old.py", launch_record(cfg.id))
    path = runconfig.store_path(project)
    before = path.read_bytes()

    def unsupported(_fd):
        raise OSError(errno.ENOSYS, "filesystem has no advisory lock")

    monkeypatch.setattr(layoutsession, "_try_file_lock", unsupported)

    def invoke():
        reference = cfg.id if with_config else None
        if entry == "set_default":
            return runconfig.set_default(project, "old.py", reference)
        return runconfig.commit_success(
            project, "old.py", None, default_config_id=reference, update_default=True
        )

    if with_config:
        with pytest.raises(runconfig.RunConfigUnreadable) as error:
            invoke()
        assert error.value.reason == "store_lock_unavailable"
    else:
        assert invoke() is None
    assert path.read_bytes() == before
    assert not getattr(runconfig._STORE_LOCKS, "held", ())


@pytest.mark.parametrize("entry", ["set_default", "commit_success"])
@pytest.mark.parametrize("with_config", [False, True])
def test_success_fallback_on_actual_cross_process_timeout_never_writes_unlocked(
    project, tmp_path, entry, with_config
):
    cfg = runconfig.put(project, "old.py", ["old"])
    runconfig.set_default(project, "old.py", cfg.id)
    runconfig.set_launch_record(project, "old.py", launch_record(cfg.id))
    path = runconfig.store_path(project)
    before = path.read_bytes()
    script = f"""
import sys
sys.path.insert(0,{str(REPO / "src")!r})
from tavotto.engine import layoutsession,runconfig
layoutsession.LOCK_TIMEOUT_S=.08
reference={cfg.id if with_config else None!r}
try:
    if {entry!r}=='set_default':
        runconfig.set_default({str(project)!r},'old.py',reference)
    else:
        runconfig.commit_success({str(project)!r},'old.py',None,default_config_id=reference,update_default=True)
except runconfig.RunConfigUnreadable as exc:
    assert {with_config!r} and exc.reason=='store_lock_unavailable'
else:
    assert not {with_config!r}
print('no unlocked write')
"""
    with runconfig._store_guard(project):
        child = subprocess.run(
            [sys.executable, "-I", "-c", script],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        )
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == "no unlocked write"
    assert path.read_bytes() == before


def wait_for(path, timeout=8):
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            pytest.fail("子进程验证屏障超时")
        time.sleep(0.01)


@pytest.mark.parametrize(
    "writer",
    [
        "put",
        "set_default",
        "set_launch_record",
        "amend_launch_records",
        "commit_success",
        "record_discovered",
        "record_captures",
    ],
)
def test_import_and_every_existing_writer_share_actual_cross_process_transaction(
    project, tmp_path, writer
):
    old = runconfig.put(project, "existing.py", ["old"])
    runconfig.set_launch_record(project, "existing.py", launch_record(old.id))
    script = f"""
import json,sys,time
from pathlib import Path
sys.path.insert(0,{str(REPO / "src")!r})
from tavotto.engine import runconfig
project=Path(sys.argv[1]);base=Path(sys.argv[2]);mode=sys.argv[3];writer=sys.argv[4]
cid={old.id!r}
rec={launch_record(old.id)!r}
if mode=='import':
    original=runconfig._read
    def paused(*a,**kw):
        data=original(*a,**kw)
        (base/'a-read').touch()
        deadline=time.monotonic()+8
        while not (base/'release-a').exists():
            if time.monotonic()>deadline: raise RuntimeError('barrier timeout')
            time.sleep(.01)
        return data
    runconfig._read=paused
    runconfig.import_portable(project,[{{'id':{CID!r},'script':'portable.py','argv':['portable']}}])
else:
    (base/'b-started').touch()
    if writer=='put':
        runconfig.put(project,'new.py',['new'])
    elif writer=='set_default':
        runconfig.set_default(project,'existing.py',cid)
    elif writer=='set_launch_record':
        runconfig.set_launch_record(project,'existing.py',{{**rec,'binding_revision':'new'}})
    elif writer=='amend_launch_records':
        runconfig.amend_launch_records(project,lambda old:{{**old,'binding_revision':'amended'}})
    elif writer=='commit_success':
        runconfig.commit_success(project,'existing.py',{{**rec,'binding_revision':'success'}},default_config_id=cid,update_default=True)
    elif writer=='record_discovered':
        runconfig.record_discovered(project,{{'existing.py':{{'discovered'}}}})
    elif writer=='record_captures':
        runconfig.record_captures(project,'existing.py',['captured'],cid)
    (base/'b-done').touch()
print('ok')
"""
    children = []
    try:
        children.append(
            subprocess.Popen(
                [sys.executable, "-I", "-c", script, str(project), str(tmp_path), "import", writer],
                cwd=tmp_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        )
        wait_for(tmp_path / "a-read")
        children.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    script,
                    str(project),
                    str(tmp_path),
                    "old-writer",
                    writer,
                ],
                cwd=tmp_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        )
        wait_for(tmp_path / "b-started")
        time.sleep(0.15)
        assert not (tmp_path / "b-done").exists(), "旧writer必须等import持有的共同锁"
        (tmp_path / "release-a").touch()
        for child in children:
            stdout, stderr = child.communicate(timeout=12)
            assert child.returncode == 0, stderr.decode(errors="replace")
            assert stdout.strip() == b"ok"
    finally:
        (tmp_path / "release-a").touch()
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
    data = runconfig._read(project)
    assert CID in data["configs"] and old.id in data["configs"]
    if writer == "put":
        assert len(data["configs"]) == 3
    elif writer in {"set_default", "commit_success"}:
        assert data["defaults"]["existing.py"] == old.id
    elif writer == "record_discovered":
        assert runconfig.discovered_stems(project, "existing.py") == {"discovered"}
    elif writer == "record_captures":
        assert runconfig.configs_capturing(project, "existing.py", "captured") == [old.id]
    else:
        assert data["launches"]["existing.py"]["binding_revision"] in {"new", "amended"}


def test_cold_process_selects_original_id_and_tokens_without_scientific_import_or_execution(
    project, tmp_path
):
    runconfig.import_portable(project, [record()])
    script = f"""
import importlib.abc,json,sys
sys.path.insert(0,{str(REPO / "src")!r})
class Reject(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,*args):
        if fullname.split('.')[0] in {{'matplotlib','numpy','pikepdf','pypdfium2','uharfbuzz','fontTools','PIL','fitz','pymupdf'}}:
            raise RuntimeError('scientific/native dependency')
sys.meta_path.insert(0,Reject())
from tavotto.engine import runconfig
runconfig.import_portable({str(project)!r},[{{'id':{CID!r},'script':'科学/原脚本.py','argv':{TOKENS!r}}}])
selection=runconfig.selection({str(project)!r},{CID!r},script='科学/原脚本.py')
assert selection.config_id=={CID!r} and selection.argv=={tuple(TOKENS)!r}
assert runconfig.default_selection({str(project)!r},'科学/原脚本.py') is None
print('cold selection only')
"""
    child = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == "cold selection only"
