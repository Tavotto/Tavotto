"""素材库「从素材中移除」：脚本已被用户删掉的 runtime 素材，摘掉登记与物化 cache。

看护 `POST /api/runtime/remove`（`app.api_runtime_remove`）：
* missing_source 时移除成功：注册表少这一项、cache 目录没了（含运行配置变体）、
  用户目录下其余文件逐字节不变；
* 脚本还在 → 409 `runtime_asset_source_present`；
* 画布在用（含运行配置变体）→ 409 `runtime_asset_in_use`，注册表与 cache 零改动；
* 同脚本多个 stem 只摘这一个，脚本条目与别的 stem 保留；最后一个 stem 摘掉时整条脚本条目消失。
"""

import hashlib
import json
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import figcapture, project_watch as engine_watch, runconfig, runtimeasset


@pytest.fixture
def client():
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()


def _registry(figs: Path, scripts: dict) -> None:
    (figs / "tavotto_registry.json").write_text(
        json.dumps({"scripts": scripts}, ensure_ascii=False), encoding="utf-8"
    )


def _registry_scripts(figs: Path) -> dict:
    return json.loads((figs / "tavotto_registry.json").read_text(encoding="utf-8"))["scripts"]


def _materialize(figs: Path, tmp_path: Path, script: str, stem: str, run_config: str = ""):
    desc = figcapture.build_descriptor(
        script=script,
        entry="__main__",
        stem=stem,
        capture_source=figcapture.SOURCE_PYPLOT,
        execution_profile=figcapture.PROFILE_SAFE,
        size_mm=(120.0, 90.0),
        source_fingerprint="sha256:deadbeef",
        **({"run_config": run_config} if run_config else {}),
    ).to_payload()
    svg = tmp_path / f"{stem}-{run_config or 'base'}.svg"
    svg.write_text("<svg>preview</svg>", encoding="utf-8")
    assert runtimeasset.materialize(figs, desc, svg) is not None


def _tree_digest(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name != "tavotto_registry.json" and ".tavotto" not in p.parts
    }


def _open(client, tmp_path, scripts, *, existing=()):
    figs = tmp_path / "figs"
    figs.mkdir()
    for name in existing:
        (figs / name).write_text("import matplotlib\n", encoding="utf-8")
    (figs / "notes.txt").write_text("用户的文件\n", encoding="utf-8")
    _registry(figs, scripts)
    assert client.post("/api/projects/open", json={"path": str(figs)}).status_code == 200
    return figs


def _id(script, stem, run_config=""):
    return figcapture.runtime_asset_id(script, stem, run_config)


def _cache_exists(figs, asset_id) -> bool:
    return runtimeasset.cache_dir(figs, asset_id).exists()


class TestRemoveOrphanRuntimeAsset:
    def test_missing_source_removes_registry_entry_and_caches_and_touches_no_user_file(
        self, client, tmp_path
    ):
        figs = _open(
            client, tmp_path, {"gone.py": {"entry": "__main__", "stems": ["gone"]}}
        )  # gone.py 不在磁盘上
        _materialize(figs, tmp_path, "gone.py", "gone")
        cfg = runconfig.put(figs, "gone.py", ["--k", "1"])
        _materialize(figs, tmp_path, "gone.py", "gone", cfg.id)
        base, variant = _id("gone.py", "gone"), _id("gone.py", "gone", cfg.id)
        assert _cache_exists(figs, base) and _cache_exists(figs, variant)
        before = _tree_digest(figs)

        resp = client.post("/api/runtime/remove", json={"id": base, "used_ids": []})
        assert resp.status_code == 200, resp.get_json()

        assert "gone.py" not in _registry_scripts(figs)
        assert not _cache_exists(figs, base)
        assert not _cache_exists(figs, variant)
        assert _tree_digest(figs) == before
        assert client.get("/api/runtime/assets").get_json()["assets"] == []

    def test_only_the_named_stem_is_removed_when_the_script_has_others(self, client, tmp_path):
        figs = _open(
            client,
            tmp_path,
            {"gone.py": {"entry": "__main__", "cost": "heavy", "notes": "n", "stems": ["a", "b"]}},
        )
        _materialize(figs, tmp_path, "gone.py", "a")
        _materialize(figs, tmp_path, "gone.py", "b")
        resp = client.post("/api/runtime/remove", json={"id": _id("gone.py", "a"), "used_ids": []})
        assert resp.status_code == 200, resp.get_json()
        entry = _registry_scripts(figs)["gone.py"]
        assert entry["stems"] == ["b"]
        assert entry["entry"] == "__main__" and entry["cost"] == "heavy" and entry["notes"] == "n"
        assert not _cache_exists(figs, _id("gone.py", "a"))
        assert _cache_exists(figs, _id("gone.py", "b"))
        ids = [a["id"] for a in client.get("/api/runtime/assets").get_json()["assets"]]
        assert ids == [_id("gone.py", "b")]

    def test_refused_while_the_script_is_still_on_disk(self, client, tmp_path):
        figs = _open(
            client,
            tmp_path,
            {"here.py": {"entry": "__main__", "stems": ["here"]}},
            existing=["here.py"],
        )
        _materialize(figs, tmp_path, "here.py", "here")
        before = (figs / "tavotto_registry.json").read_bytes()
        resp = client.post(
            "/api/runtime/remove", json={"id": _id("here.py", "here"), "used_ids": []}
        )
        assert resp.status_code == 409
        assert resp.get_json()["code"] == "runtime_asset_source_present"
        assert (figs / "tavotto_registry.json").read_bytes() == before
        assert _cache_exists(figs, _id("here.py", "here"))

    @pytest.mark.parametrize("variant", [False, True])
    def test_refused_while_the_canvas_uses_it(self, client, tmp_path, variant):
        figs = _open(client, tmp_path, {"gone.py": {"entry": "__main__", "stems": ["gone"]}})
        _materialize(figs, tmp_path, "gone.py", "gone")
        cfg = runconfig.put(figs, "gone.py", ["--k", "1"])
        _materialize(figs, tmp_path, "gone.py", "gone", cfg.id)
        base = _id("gone.py", "gone")
        used = [_id("gone.py", "gone", cfg.id)] if variant else [base, "plain.pdf"]
        before = (figs / "tavotto_registry.json").read_bytes()
        resp = client.post("/api/runtime/remove", json={"id": base, "used_ids": used})
        assert resp.status_code == 409
        assert resp.get_json()["code"] == "runtime_asset_in_use"
        assert (figs / "tavotto_registry.json").read_bytes() == before
        assert _cache_exists(figs, base)

    def test_other_assets_in_use_do_not_block(self, client, tmp_path):
        figs = _open(
            client,
            tmp_path,
            {"gone.py": {"entry": "__main__", "stems": ["a", "b"]}},
        )
        resp = client.post(
            "/api/runtime/remove",
            json={"id": _id("gone.py", "a"), "used_ids": [_id("gone.py", "b")]},
        )
        assert resp.status_code == 200, resp.get_json()
        assert _registry_scripts(figs)["gone.py"]["stems"] == ["b"]

    def test_unknown_id_and_non_runtime_id(self, client, tmp_path):
        _open(client, tmp_path, {"gone.py": {"entry": "__main__", "stems": ["gone"]}})
        r1 = client.post("/api/runtime/remove", json={"id": _id("nope.py", "x")})
        assert r1.status_code == 404 and r1.get_json()["code"] == "runtime_asset_unknown"
        r2 = client.post("/api/runtime/remove", json={"id": "plain.pdf"})
        assert r2.status_code == 400 and r2.get_json()["code"] == "runtime_asset_unknown"


class TestSavedLayoutsCountAsInUse:
    def _setup(self, client, tmp_path):
        figs = _open(client, tmp_path, {"gone.py": {"entry": "__main__", "stems": ["gone"]}})
        _materialize(figs, tmp_path, "gone.py", "gone")
        store = figs / "tavottofile"
        (store / "_autosave").mkdir(parents=True)
        return figs, store

    @pytest.mark.parametrize("variant", [False, True])
    def test_saved_layout_reference_refuses_and_changes_nothing(self, client, tmp_path, variant):
        figs, store = self._setup(client, tmp_path)
        cfg = runconfig.put(figs, "gone.py", ["--k", "1"])
        _materialize(figs, tmp_path, "gone.py", "gone", cfg.id)
        ref = _id("gone.py", "gone", cfg.id if variant else "")
        doc = {"schema": 3, "canvases": [{"objects": [{"type": "panel", "fileId": ref}]}]}
        (store / "Figure 1.json").write_text(json.dumps(doc), encoding="utf-8")
        before = (figs / "tavotto_registry.json").read_bytes()
        resp = client.post(
            "/api/runtime/remove", json={"id": _id("gone.py", "gone"), "used_ids": []}
        )
        assert resp.status_code == 409
        assert resp.get_json()["code"] == "runtime_asset_in_use"
        assert (figs / "tavotto_registry.json").read_bytes() == before
        assert _cache_exists(figs, _id("gone.py", "gone"))

    def test_autosave_broken_and_reserved_documents_do_not_block(self, client, tmp_path):
        figs, store = self._setup(client, tmp_path)
        ref = json.dumps({"objects": [{"fileId": _id("gone.py", "gone")}]})
        (store / "_autosave" / "x.json").write_text(ref, encoding="utf-8")
        (store / "broken.json").write_text("{not json", encoding="utf-8")
        (store / "nan.json").write_text('{"a": NaN}', encoding="utf-8")
        resp = client.post(
            "/api/runtime/remove", json={"id": _id("gone.py", "gone"), "used_ids": []}
        )
        assert resp.status_code == 200, resp.get_json()
        assert "gone.py" not in _registry_scripts(figs)
