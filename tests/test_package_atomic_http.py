"""旧项目包的真实 HTTP、资源流与最终目录原子性；故障仅注入 I/O 边界。"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import subprocess
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import fields
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import atomicio, brand, config, documentpackage, packagebudget, pool

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="30" height="20"><path d="M0 0L30 20"/></svg>'
SCRIPT = b'def main():\n    raise RuntimeError("packaging must not execute this script")\n'


@pytest.fixture
def env(tmp_path, monkeypatch):
    m.reset_projects()
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "fallback-exports")
    monkeypatch.setitem(m.app.config, "TESTING", True)
    root = tmp_path / "项目"
    root.mkdir()
    (root / "F.svg").write_bytes(SVG)
    (root / "other.svg").write_bytes(SVG.replace(b"30 20", b"20 10"))
    (root / "plot.py").write_bytes(SCRIPT)
    (root / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "scripts": {
                    "plot.py": {"entry": "main", "cost": "light", "notes": "", "stems": ["F"]}
                },
            }
        ),
        encoding="utf-8",
    )
    m.open_project(str(root))
    with m.app.test_request_context("/api/package"):
        output = m.project_export_dir()
    yield m.app.test_client(), root, output
    pool.shutdown_all(figures_dir=str(root), wait=True)
    m.reset_projects()
    from tavotto.engine import project_watch
    from tavotto.rendercore import renderhost

    project_watch.stop()
    renderhost.shutdown_shared()


def panel(source, number=1):
    return {
        "id": f"p{number}",
        "type": "panel",
        "fileId": source,
        "fileKind": "svg",
        "nativeW": 30,
        "nativeH": 20,
        "x": number,
        "y": 2,
        "w": 30,
        "h": 20,
        "overrides": [],
    }


def document(schema=3, sources=("F.svg", "other.svg", "missing.svg")):
    canvases = [
        {
            "id": f"c{index}",
            "name": f"画布 {index}",
            "page": {"w": 120 + index, "h": 80},
            "objects": [panel(source, index)],
            "guides": [],
        }
        for index, source in enumerate(sources, 1)
    ]
    if schema == 2:
        return {
            "schema": 2,
            "name": "旧排版",
            "page": {"w": 120, "h": 80},
            "objects": [panel(source, index) for index, source in enumerate(sources, 1)],
            "guides": [],
            "user_extra": {"text": "原样保留"},
        }
    return {
        "schema": 3,
        "project": {"id": "working", "name": "旧多画布"},
        "canvases": canvases,
        "activeCanvasId": "c1",
        "createdAt": 11,
        "updatedAt": 22,
        "user_extra": {"text": "原样保留"},
    }


def post(client, doc=None, *, stem="atomic"):
    return client.post(
        "/api/package",
        json={"doc": document() if doc is None else doc, "stem": stem, "settings": {"dpi": 600}},
    )


def unpack(output, response):
    body = response.get_json()
    assert response.status_code == 200, body
    assert set(body) == {"name", "url", "assets", "missing"}
    assert body["name"].startswith("atomic_") and body["name"].endswith(brand.PACKAGE_EXT)
    assert body["url"] == "/exports/" + body["name"]
    with zipfile.ZipFile(output / body["name"]) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert len(names) == len(set(names))
        members = {name: archive.read(name) for name in names}
    return body, members


def directory_bytes(output):
    return {path.name: path.read_bytes() for path in output.iterdir() if path.is_file()}


def assert_failed_unchanged(response, output, before):
    body = response.get_json()
    assert response.status_code == 500, body
    assert body["code"] == "internal_error", body
    assert isinstance(body["error"], str) and body["error"]
    assert directory_bytes(output) == before, "失败请求发布了新包、半包或覆盖了旧包"
    assert {path.name for path in output.iterdir()} == set(before), "失败请求残留暂存项"


def frozen_second(monkeypatch):
    original = time.strftime
    instant = time.localtime(1_700_000_000)
    monkeypatch.setattr(time, "strftime", lambda pattern, *_args: original(pattern, instant))


@pytest.mark.parametrize("schema", [2, 3])
def test_legacy_raw_document_response_and_all_canvas_resources_survive(env, schema):
    client, root, output = env
    doc = document(schema)
    before = {name: (root / name).read_bytes() for name in ("F.svg", "other.svg", "plot.py")}
    body, members = unpack(output, post(client, doc))
    assert body["assets"] == 2 and body["missing"] == ["missing.svg"]
    assert json.loads(members["layout.json"]) == doc
    manifest = json.loads(members["package_manifest.json"])
    assert manifest["kind"] == brand.PACKAGE_KIND and manifest["version"] == 1
    assert "document_format" not in manifest
    assert manifest["page"] == doc.get("page")
    assert manifest["export_settings"] == {"dpi": 600}
    assert manifest["scripts"] == ["plot.py"]
    assert manifest["runtime_assets"] == [] and manifest["figure_snapshots"] == 0
    assert manifest["missing_at_pack_time"] == ["missing.svg"]
    assert {entry["id"] for entry in manifest["assets"]} == {"F.svg", "other.svg"}
    assert set(members) == {
        "layout.json",
        "package_manifest.json",
        "assets/F.svg",
        "assets/other.svg",
        "scripts/plot.py",
    }
    for entry in manifest["assets"]:
        data = members["assets/" + entry["id"]]
        assert data == before[entry["id"]]
        assert entry["bytes"] == len(data)
        assert entry["sha1"] == hashlib.sha1(data).hexdigest()
        assert entry["mtime"] == int((root / entry["id"]).stat().st_mtime)
    assert members["scripts/plot.py"] == SCRIPT
    assert {name: (root / name).read_bytes() for name in before} == before


def test_http_plan_uses_exact_metadata_and_all_six_real_container_budgets(env, monkeypatch):
    client, _root, output = env
    plans, stages = [], []
    original_budget = packagebudget.output_budget
    original_staged = documentpackage.staged_package

    def calculate(members):
        budget = original_budget(members)
        plans.append((dict(members), budget))
        return budget

    @contextmanager
    def stage(destination, **kwargs):
        stages.append((Path(destination), kwargs["budget"]))
        with original_staged(destination, **kwargs) as writer:
            yield writer

    monkeypatch.setattr(packagebudget, "output_budget", calculate)
    monkeypatch.setattr(documentpackage, "staged_package", stage)
    body, members = unpack(output, post(client))
    assert plans and stages, "真实 HTTP 未消费 output_budget/staged_package"
    plan, budget = plans[-1]
    assert plan == {name: len(data) for name, data in members.items()}
    assert any(path == output / body["name"] and value is budget for path, value in stages)
    assert len(fields(budget)) == 6
    assert budget.max_members == len(members)
    assert budget.max_total_bytes == sum(map(len, members.values()))
    assert budget.max_layout_bytes == len(members["layout.json"])
    assert budget.max_manifest_bytes == len(members["package_manifest.json"])
    target = output / body["name"]
    assert target.stat().st_size <= budget.max_archive_bytes
    with zipfile.ZipFile(target) as archive:
        assert target.stat().st_size - archive.start_dir <= budget.max_directory_bytes


def test_real_worker_snapshot_helper_temporary_files_live_until_streamed(env, monkeypatch):
    client, root, output = env
    worker_python = pool.find_worker_python()
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", str(worker_python))
    (root / "plot.py").write_text(
        "def main():\n    import matplotlib.pyplot as plt\n"
        "    fig, ax = plt.subplots(figsize=(2, 1))\n"
        '    ax.plot([0, 1], [1, 0])\n    fig.savefig("F.pdf")\n',
        encoding="utf-8",
    )
    response = client.post("/api/registry/probe", json={"script": "plot.py"})
    assert response.status_code == 200 and response.get_json().get("registered"), (
        response.get_json()
    )
    from tavotto.engine import figcapture

    source = figcapture.runtime_asset_id("plot.py", "F")
    doc = document(sources=(source,))
    roots, copied = [], []
    original_helper = m._package_svg_assets
    original_copy = documentpackage._copy_member

    def helper(value, project_root, archive, **kwargs):
        temporary_root = kwargs.get("temporary_root")
        assert temporary_root is not None, "caller 未持有快照 helper 暂存生命周期"
        roots.append(Path(temporary_root))
        result = original_helper(value, project_root, archive, **kwargs)
        assert roots[-1].is_dir()
        return result

    def copy_member(archive, name, source, *args):
        if not isinstance(source, bytes) and any(Path(source).is_relative_to(p) for p in roots):
            assert Path(source).is_file(), "helper 返回后，尚未流入 ZIP 的真实暂存资源已消失"
            copied.append(name)
        return original_copy(archive, name, source, *args)

    monkeypatch.setattr(m, "_package_svg_assets", helper)
    monkeypatch.setattr(documentpackage, "_copy_member", copy_member)
    body, members = unpack(output, post(client, doc))
    manifest = json.loads(members["package_manifest.json"])
    assert body["assets"] == 0 and body["missing"] == []
    assert manifest["figure_snapshots"] == 1
    assert manifest["runtime_assets"][0]["id"] == source
    assert members["scripts/plot.py"] == (root / "plot.py").read_bytes()
    prefix = "assets/" + config.PROJECT_STORE_DIRNAME + "/figure_snapshots/"
    assert copied and all(name.startswith(prefix) for name in copied)
    assert any(name.endswith(".svg") for name in copied)
    packaged = json.loads(members["layout.json"])
    reference = packaged["canvases"][0]["objects"][0]["figureSnapshot"]
    snapshot = json.loads(members[prefix + "snapshots/" + reference["snapshotId"] + ".json"])
    assert snapshot["payload"]["identity"]["source_id"] == source
    assert snapshot["payload"]["receipt"]["receipt_id"] == reference["receiptId"]
    assert roots and all(not path.exists() for path in roots)


@pytest.mark.parametrize("phase", ["add", "close", "publish"])
@pytest.mark.parametrize("failure", ["enospc", "exception"])
def test_io_failure_never_publishes_half_package_or_changes_older_output(
    env, monkeypatch, phase, failure
):
    client, _root, output = env
    unpack(output, post(client))
    before = directory_bytes(output)
    hits = []

    def fail():
        hits.append(phase)
        if failure == "enospc":
            raise OSError(errno.ENOSPC, "injected package volume full")
        raise RuntimeError("injected package I/O interruption")

    if phase == "add":
        original = zipfile.ZipFile.open

        def open_member(archive, name, mode="r", *args, **kwargs):
            member = name.filename if isinstance(name, zipfile.ZipInfo) else name
            if mode == "w" and member == "assets/other.svg":
                fail()
            return original(archive, name, mode, *args, **kwargs)

        monkeypatch.setattr(zipfile.ZipFile, "open", open_member)
    elif phase == "close":
        original = zipfile.ZipFile.close

        def close(archive):
            writing = archive.mode == "w" and archive.fp is not None
            original(archive)
            if writing:
                fail()

        monkeypatch.setattr(zipfile.ZipFile, "close", close)
    else:

        def publish(*_args, **_kwargs):
            fail()

        monkeypatch.setattr(atomicio, "publish_file", publish)
        monkeypatch.setattr(atomicio, "publish_file_if_absent", publish)
    response = post(client)
    assert hits == [phase], "故障未实际命中所声明的输出阶段"
    assert_failed_unchanged(response, output, before)


def test_source_growth_after_exact_plan_fails_without_publishing(env, monkeypatch):
    client, root, output = env
    unpack(output, post(client))
    before = directory_bytes(output)
    original = packagebudget.output_budget
    hits = []

    def calculate(members):
        result = original(members)
        hits.append(dict(members))
        (root / "other.svg").write_bytes(b"x" * (result.max_total_bytes + 1))
        return result

    monkeypatch.setattr(packagebudget, "output_budget", calculate)
    response = post(client)
    assert len(hits) == 1, "未命中真实最终 plan 之后、流式读取之前"
    assert_failed_unchanged(response, output, before)


def test_same_second_sequential_requests_keep_both_complete_packages(env, monkeypatch):
    client, _root, output = env
    frozen_second(monkeypatch)
    first, first_members = unpack(output, post(client, document(sources=("F.svg",))))
    first_bytes = (output / first["name"]).read_bytes()
    second, second_members = unpack(output, post(client, document(sources=("other.svg",))))
    assert first["name"] != second["name"]
    assert (output / first["name"]).read_bytes() == first_bytes
    assert set(directory_bytes(output)) == {first["name"], second["name"]}
    assert "assets/F.svg" in first_members and "assets/other.svg" not in first_members
    assert "assets/other.svg" in second_members and "assets/F.svg" not in second_members


def test_forced_name_collision_does_not_replace_preexisting_complete_package(env, monkeypatch):
    client, _root, output = env
    frozen_second(monkeypatch)
    original = m.secrets.token_hex
    monkeypatch.setattr(
        m.secrets, "token_hex", lambda size: "00112233" if size == 4 else original(size)
    )
    unpack(output, post(client, document(sources=("F.svg",))))
    before = directory_bytes(output)
    response = post(client, document(sources=("other.svg",)))
    assert_failed_unchanged(response, output, before)


def test_final_directory_exposes_only_complete_packages_while_next_write_is_in_progress(
    env, monkeypatch
):
    client, _root, output = env
    unpack(output, post(client))
    before = directory_bytes(output)
    entered, release = threading.Event(), threading.Event()
    original = zipfile.ZipFile.open

    def open_member(archive, name, mode="r", *args, **kwargs):
        member = name.filename if isinstance(name, zipfile.ZipInfo) else name
        if mode == "w" and member == "assets/other.svg":
            entered.set()
            assert release.wait(timeout=10), "测试未释放其自身 I/O 停点"
        return original(archive, name, mode, *args, **kwargs)

    def generate():
        with m.app.test_client() as client:
            return post(client)

    monkeypatch.setattr(zipfile.ZipFile, "open", open_member)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(generate)
        try:
            assert entered.wait(timeout=10), "未到达真实成员流写入"
            published = {p.name: p.read_bytes() for p in output.glob("*" + brand.PACKAGE_EXT)}
            assert published == before, "ZIP 尚在写入，却已通过最终文件名暴露"
        finally:
            release.set()
        body, _members = unpack(output, pending.result(timeout=10))
    assert body["name"] not in before
    assert all((output / name).read_bytes() == data for name, data in before.items())


def test_same_second_concurrent_requests_keep_every_distinct_document(env, monkeypatch):
    _client, _root, output = env
    frozen_second(monkeypatch)
    barrier = threading.Barrier(3)

    def generate(index):
        doc = document(sources=("F.svg",))
        doc["user_extra"] = {"request": index}
        with m.app.test_client() as client:
            barrier.wait(timeout=10)
            response = post(client, doc)
            return index, unpack(output, response)

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(generate, range(3)))
    names = [body["name"] for _index, (body, _members) in results]
    assert len(set(names)) == 3
    assert set(directory_bytes(output)) == set(names)
    for index, (_body, members) in results:
        assert json.loads(members["layout.json"])["user_extra"] == {"request": index}


def directory_alias(link, target):
    if os.name == "nt":
        result = subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
    else:
        link.symlink_to(target, target_is_directory=True)
    assert link.resolve() == target.resolve()


@pytest.mark.parametrize("retarget", [False, True])
def test_contained_alias_keeps_original_member_name_and_never_reopens_external_alias(
    env, tmp_path, monkeypatch, retarget
):
    client, root, output = env
    internal = root / "真实目录"
    internal.mkdir()
    (internal / "aliased.svg").write_bytes(SVG)
    external = tmp_path / "项目外"
    external.mkdir()
    secret = SVG.replace(b"M0 0L30 20", b"M99 99L88 88")
    (external / "aliased.svg").write_bytes(secret)
    alias = root / "入口"
    directory_alias(alias, internal)
    original = m._sha1_of
    swapped = []

    def hash_then_retarget(path):
        result = original(path)
        if retarget and Path(path) == internal / "aliased.svg" and not swapped:
            alias.rmdir() if os.name == "nt" else alias.unlink()
            directory_alias(alias, external)
            swapped.append(True)
        return result

    monkeypatch.setattr(m, "_sha1_of", hash_then_retarget)
    try:
        body, members = unpack(output, post(client, document(sources=("入口/aliased.svg",))))
        assert body["assets"] == 1 and body["missing"] == []
        assert members["assets/入口/aliased.svg"] == SVG
        assert secret not in members.values(), "计划已确认内部源，写入却重新追随了可变 alias"
        assert "assets/真实目录/aliased.svg" not in members
        manifest = json.loads(members["package_manifest.json"])
        assert manifest["assets"][0]["id"] == "入口/aliased.svg"
        assert manifest["assets"][0]["sha1"] == hashlib.sha1(SVG).hexdigest()
        assert bool(swapped) is retarget
    finally:
        if alias.exists():
            alias.rmdir() if os.name == "nt" else alias.unlink()
    assert (external / "aliased.svg").read_bytes() == secret


def test_legacy_distinct_unicode_normalization_names_both_keep_exact_bytes(env):
    client, root, output = env
    first, second = "é.svg", "e\u0301.svg"
    (root / first).write_bytes(SVG)
    other = SVG.replace(b"30 20", b"11 12")
    (root / second).write_bytes(other)
    assert not (root / first).samefile(root / second), "本资格要求文件系统支持这两个独立合法名称"
    body, members = unpack(output, post(client, document(sources=(first, second))))
    assert body["assets"] == 2 and body["missing"] == []
    assert members["assets/" + first] == SVG
    assert members["assets/" + second] == other
    manifest = json.loads(members["package_manifest.json"])
    assert {entry["id"] for entry in manifest["assets"]} == {first, second}
