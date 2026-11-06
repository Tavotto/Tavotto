"""声明式项目附件：精确字节、不可变发布、原场景身份与显式 schema 边界。"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tavotto import chartbundle
from tavotto.engine import atomicio, chartdata, chartmodel, documentassets as assets, documents

SOURCE_ID = '图 "quoted".svg'
SOURCE = (
    b'<?xml version="1.0" encoding="UTF-8"?>\r\n'
    b'<svg xmlns="http://www.w3.org/2000/svg" width="100pt" height="100pt" '
    b'viewBox="0 0 100 100">\n'
    b'<g id="old_axis"><text x="2" y="98">old "tick"</text></g>\n'
    b'<path id="curve" d="M10 90L90 10" fill="none" stroke="blue"/>\n'
    b'<text x="30" y="8">unbound &amp; title</text></svg>\r\n'
)
SCENE_DIGEST = hashlib.sha256(SOURCE).hexdigest()


def chart(source=SOURCE):
    # 标准 TSV 的 quoted 表头真实经过现有解析器，不另造一份单元格解析逻辑。
    parsed = chartdata.parse_table('"x,\t""quoted""\nheader"\ty\n"0"\t"0"\n"1"\t"1"\n')
    assert not parsed["issues"]
    x_name = 'x,\t"quoted"\nheader'
    assert x_name in parsed["columns"]
    dataset = {"columns": parsed["columns"]}
    model = {
        "schema": chartmodel.MODEL_SCHEMA,
        "rev": 1,
        "axes": [
            {
                "id": "ax1",
                "rect": [10.0, 10.0, 80.0, 80.0],
                "xlim": [0.0, 1.0],
                "ylim": [0.0, 1.0],
                "xscale": "linear",
                "yscale": "linear",
                "invert_x": False,
                "invert_y": False,
            }
        ],
        "series": [
            {
                "id": "s1",
                "kind": "line",
                "axes": "ax1",
                "node": "curve",
                "x": x_name,
                "y": "y",
                "style": {"color": "#064cff"},
            }
        ],
        "dataset": dataset,
        "scene_digest": hashlib.sha256(source).hexdigest(),
        "data_digest": chartmodel.digest_of(dataset),
        "engine": {"matplotlib": "3.11.2", "numpy": "2.1.0", "tavotto": "0.19.2"},
    }
    binding = {
        "model_rev": 1,
        "scene_digest": model["scene_digest"],
        "entries": [
            {"object": "axes:ax1", "node": "old_axis"},
            {"object": "series:s1", "node": "curve"},
        ],
    }
    return {"model": model, "binding": binding}


def reference(data=SOURCE, *, source_id=SOURCE_ID, scene_digest=SCENE_DIGEST):
    return {
        "schema": 1,
        "sourceId": source_id,
        "bytesSha256": hashlib.sha256(data).hexdigest(),
        "sizeBytes": len(data),
        "sceneDigest": scene_digest,
    }


def document(ref=None):
    panel = {"type": "panel", "fileId": SOURCE_ID, "fileKind": "svg", "chart": chart()}
    if ref is not None:
        panel["chartSource"] = ref
    return {"schema": 4, "canvases": [{"objects": [panel]}]}


def freeze(root, data=SOURCE, *, source_id=SOURCE_ID, scene_digest=SCENE_DIGEST):
    return assets.freeze_source(root, source_id, data, scene_digest=scene_digest)


def directory_link(link: Path, target: Path):
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        if sys.platform != "win32" or exc.winerror != 1314:
            raise
        # 无 symlink 特权的本机仍真实验证 NTFS namespace redirect，不能将其当目录放行。
        quoted_link = str(link).replace("'", "''")
        quoted_target = str(target).replace("'", "''")
        script = (
            f"New-Item -ItemType Junction -Path '{quoted_link}' "
            f"-Target '{quoted_target}' | Out-Null"
        )
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            check=True,
            capture_output=True,
        )


def test_freeze_preserves_exact_bytes_and_uses_only_the_digest_for_the_path(tmp_path):
    root = tmp_path / "tavottofile"
    ref = freeze(root)
    assert ref == reference()
    loaded = assets.load_source(root, ref, source_id=SOURCE_ID, scene_digest=SCENE_DIGEST)
    assert loaded.input_svg == loaded.source_svg == SOURCE
    assert loaded.path == root / "chart_sources" / (ref["bytesSha256"] + ".svg")
    assert loaded.path.read_bytes() == SOURCE
    assert sorted(p.name for p in loaded.path.parent.iterdir()) == [loaded.path.name]
    assert not list(root.glob("*.json")), "附件层提前提交了正文或新索引"


def test_existing_identical_source_is_never_replaced(tmp_path, monkeypatch):
    ref = freeze(tmp_path)
    path = assets.source_path(tmp_path, ref)
    before = path.stat()
    real_publish = atomicio._publish_if_absent
    attempts = []

    def publish(src, dest):
        attempts.append(dest)
        return real_publish(src, dest)

    monkeypatch.setattr(atomicio, "_publish_if_absent", publish)
    assert freeze(tmp_path) == ref
    assert attempts == [path]
    assert path.read_bytes() == SOURCE
    after = path.stat()
    assert os.path.samestat(before, after)
    assert before.st_mtime_ns == after.st_mtime_ns


@pytest.mark.parametrize("tampered", [b"truncated", SOURCE.replace(b"blue", b"pink")])
def test_existing_corrupt_source_is_refused_without_repair(tmp_path, tampered):
    ref = freeze(tmp_path)
    path = assets.source_path(tmp_path, ref)
    path.write_bytes(tampered)
    with pytest.raises(assets.DocumentAssetError, match="chart_source_mismatch"):
        freeze(tmp_path)
    assert path.read_bytes() == tampered
    assert not list(path.parent.glob("*.tmp"))


def test_missing_source_never_falls_back_to_the_original_filename(tmp_path):
    source_id = "original.svg"
    (tmp_path / source_id).write_bytes(SOURCE)
    with pytest.raises(assets.DocumentAssetError, match="chart_source_mismatch"):
        assets.load_source(tmp_path, reference(source_id=source_id))
    assert not (tmp_path / "chart_sources").exists(), "读侧创建了附件目录"


def test_source_id_cannot_be_used_as_a_storage_path(tmp_path):
    ref = freeze(tmp_path, source_id="../../outside.svg")
    path = assets.source_path(tmp_path, ref)
    assert path.parent == tmp_path / "chart_sources"
    assert assets.load_source(tmp_path, ref, source_id="../../outside.svg").input_svg == SOURCE
    with pytest.raises(assets.DocumentAssetError, match="chart_source_mismatch"):
        assets.load_source(tmp_path, ref, source_id="other.svg")


@pytest.mark.parametrize("key", sorted(documents.CHART_SOURCE_KEYS))
def test_reference_rejects_every_missing_field(key):
    ref = reference()
    del ref[key]
    with pytest.raises(documents.DocumentError, match="无效的计算图表来源引用"):
        documents.validate_chart_source(ref)


@pytest.mark.parametrize(
    "changes",
    [
        {"schema": True},
        {"schema": 1.0},
        {"schema": 2},
        {"sourceId": ""},
        {"sourceId": "\x00.svg"},
        {"sourceId": 1},
        {"bytesSha256": "A" * 64},
        {"bytesSha256": "../bad"},
        {"bytesSha256": "a" * 63},
        {"sceneDigest": "g" * 64},
        {"sizeBytes": True},
        {"sizeBytes": 1.0},
        {"sizeBytes": 0},
        {"sizeBytes": assets.MAX_SOURCE_BYTES + 1},
        {"path": "../../outside.svg"},
        {"origin": "execution"},
        {"receiptId": "receipt"},
    ],
)
def test_reference_is_a_strict_closed_contract(changes):
    with pytest.raises(documents.DocumentError):
        documents.validate_chart_source({**reference(), **changes})


def test_source_and_scene_are_verified_not_just_reference_syntax(tmp_path):
    with pytest.raises(assets.DocumentAssetError, match="chart_source_mismatch"):
        freeze(tmp_path, scene_digest="0" * 64)
    assert not (tmp_path / "chart_sources").exists()
    ref = freeze(tmp_path)
    with pytest.raises(assets.DocumentAssetError, match="chart_source_mismatch"):
        assets.load_source(tmp_path, {**ref, "sceneDigest": "0" * 64})
    with pytest.raises(assets.DocumentAssetError, match="chart_source_mismatch"):
        assets.load_source(tmp_path, {**ref, "sizeBytes": len(SOURCE) - 1})


def test_self_contained_input_keeps_both_input_and_original_scene_exactly(tmp_path, monkeypatch):
    state = chart()
    before = copy.deepcopy(state)
    # 此处故意不声称可见图计算等价；附件层只验证包/场景，不取得重放资格。
    bundled = chartbundle.embed_chart(SOURCE, SOURCE, state)

    def no_replay(*_args, **_kwargs):
        pytest.fail("资源层不应声称自己执行了实际重放")

    monkeypatch.setattr(chartbundle, "verify_replay", no_replay)
    ref = freeze(tmp_path, bundled)
    assert ref["bytesSha256"] == hashlib.sha256(bundled).hexdigest()
    assert ref["bytesSha256"] != ref["sceneDigest"] == SCENE_DIGEST
    loaded = assets.load_source(tmp_path, ref)
    assert loaded.path.read_bytes() == loaded.input_svg == bundled
    assert loaded.source_svg == SOURCE
    assert chartbundle.extract_chart(loaded.input_svg).chart == before
    assert state == before


def test_bundle_input_hash_is_checked_even_if_inner_scene_is_unchanged(tmp_path):
    bundled = chartbundle.embed_chart(SOURCE, SOURCE, chart())
    ref = freeze(tmp_path, bundled)
    path = assets.source_path(tmp_path, ref)
    changed = bundled.replace(b"encoding='utf-8'", b'encoding="utf-8"', 1)
    assert changed != bundled and len(changed) == len(bundled)
    assert chartbundle.extract_chart(changed).source_svg == SOURCE
    path.write_bytes(changed)
    with pytest.raises(assets.DocumentAssetError, match="chart_source_mismatch"):
        assets.load_source(tmp_path, ref)
    assert path.read_bytes() == changed


def test_embedded_image_bytes_are_carried_with_the_original_scene(tmp_path):
    png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jkXkAAAAASUVORK5CYII="
    source = SOURCE.replace(
        b"</svg>",
        f'<image x="0" y="0" width="1" height="1" href="data:image/png;base64,{png}"/></svg>'.encode(),
    )
    ref = freeze(tmp_path, source, scene_digest=hashlib.sha256(source).hexdigest())
    assert assets.load_source(tmp_path, ref).source_svg == source
    assert list((tmp_path / "chart_sources").iterdir()) == [assets.source_path(tmp_path, ref)]


def test_unsafe_svg_is_rejected_before_any_storage_write(tmp_path):
    source = SOURCE.replace(b"</svg>", b'<image href="https://example.com/private.png"/></svg>')
    with pytest.raises(assets.DocumentAssetError):
        freeze(tmp_path, source)
    assert not (tmp_path / "chart_sources").exists()


def test_input_budget_is_checked_before_parsing_or_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "MAX_SOURCE_BYTES", len(SOURCE) - 1)
    monkeypatch.setattr(chartbundle, "extract_chart", lambda _: pytest.fail("越界字节进入解析器"))
    with pytest.raises(assets.DocumentAssetError, match="svg_resource_limit"):
        freeze(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_oversized_file_is_rejected_before_opening_or_allocating_it(tmp_path, monkeypatch):
    ref = reference()
    path = assets.source_path(tmp_path, ref)
    path.parent.mkdir()
    with path.open("wb") as stream:
        stream.truncate(assets.MAX_SOURCE_BYTES + 1)
    monkeypatch.setattr(assets.os, "open", lambda *_: pytest.fail("越界文件已打开"))
    with pytest.raises(assets.DocumentAssetError, match="svg_resource_limit"):
        assets.load_source(tmp_path, ref)


@pytest.mark.parametrize("location", ["root", "sources_inside", "sources_outside"])
def test_directory_redirects_are_refused_without_writing_the_target(tmp_path, location):
    root = tmp_path / "tavottofile"
    target = tmp_path / "target"
    target.mkdir()
    if location == "root":
        directory_link(root, target)
    else:
        root.mkdir()
        if location == "sources_inside":
            target = root / "target"
            target.mkdir()
        directory_link(root / "chart_sources", target)
    try:
        with pytest.raises(assets.DocumentAssetError, match="chart_source_mismatch"):
            freeze(root)
        with pytest.raises(assets.DocumentAssetError, match="chart_source_mismatch"):
            assets.load_source(root, reference())
        assert list(target.iterdir()) == []
    finally:
        link = root if location == "root" else root / "chart_sources"
        if link.is_symlink():
            link.unlink()
        else:
            link.rmdir()  # Windows junction 只删这一 namespace entry。


def test_lstat_symlink_is_refused_before_open(tmp_path, monkeypatch):
    ref = freeze(tmp_path)
    path = assets.source_path(tmp_path, ref)
    real_lstat = Path.lstat

    def lstat(target):
        if target == path:
            return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777)
        return real_lstat(target)

    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(assets.os, "open", lambda *_: pytest.fail("跟随了符号链接"))
    with pytest.raises(assets.DocumentAssetError, match="chart_source_mismatch"):
        assets.load_source(tmp_path, ref)


def test_parent_traversal_in_the_authority_root_is_refused(tmp_path):
    with pytest.raises(assets.DocumentAssetError, match="chart_source_mismatch"):
        freeze(tmp_path / "owned" / ".." / "escape")
    assert list(tmp_path.iterdir()) == []


def test_explicit_schema4_entry_preserves_chart_data_and_requires_a_source():
    raw = document(reference())
    before = atomicio.dumps_json(raw)
    assert documents.validate_chart_document(raw) is raw
    assert atomicio.dumps_json(raw) == before
    pending = document()
    assert documents.validate_chart_document(pending, require_sources=False) is pending
    with pytest.raises(documents.DocumentError, match="无效的计算图表来源引用"):
        documents.validate_chart_document(pending)


@pytest.mark.parametrize("bad", [2, 3, 4.0, True, 5, None])
def test_chart_entry_requires_its_own_exact_schema(bad):
    with pytest.raises(documents.DocumentError):
        documents.validate_chart_document({**document(reference()), "schema": bad})


@pytest.mark.parametrize("change", ["source_id", "scene", "data", "binding_rev", "non_panel"])
def test_schema4_rejects_a_reference_or_chart_that_does_not_describe_its_panel(change):
    raw = document(reference())
    panel = raw["canvases"][0]["objects"][0]
    if change == "source_id":
        panel["chartSource"]["sourceId"] = "other.svg"
    elif change == "scene":
        panel["chartSource"]["sceneDigest"] = "0" * 64
    elif change == "data":
        panel["chart"]["model"]["dataset"]["columns"]["y"][0] = 99
    elif change == "binding_rev":
        panel["chart"]["binding"]["model_rev"] = 2
    else:
        panel["type"] = "text"
    with pytest.raises(documents.DocumentError):
        documents.validate_chart_document(raw)


def test_legacy_entry_still_refuses4_and_never_normalizes_old_payloads():
    assert documents.SCHEMA_CURRENT == documents.SCHEMA_PROJECT == 3
    assert documents.SUPPORTED_SCHEMAS == (2, 3)
    with pytest.raises(documents.DocumentError) as raised:
        documents.validate_document(document(reference()))
    assert raised.value.code == "schema_too_new"
    for raw in ({"schema": 2, "objects": None}, {"schema": 3, "canvases": [None]}):
        # 旧2/3有意只查骨架；不能因本批给旧用户加逐字段门。
        before = json.dumps(raw)
        assert documents.validate_document(raw) is raw
        assert json.dumps(raw) == before
    old = {**document(), "schema": 3}
    before = copy.deepcopy(old)
    assert documents.validate_document(old) is old
    assert old == before
