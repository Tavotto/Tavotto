"""格式是显式存储选择；旧目录不变，新目录逐层拒绝重定向。"""

from __future__ import annotations

import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from tavotto.engine import documentformat as formats, documents


def _path(kind, base, *, create=False, document_format=formats.CHART4):
    if kind == "named":
        return formats.named_dir(base, document_format, create=create)
    if kind == "autosave":
        return formats.autosave_dir(base, document_format, create=create)
    return formats.versions_dir(base, base / "_versions", document_format, create=create)


PARTS = {
    "named": ("documents", "v4"),
    "autosave": ("_autosave", "v4"),
    "versions": ("documents", "v4", "versions"),
}


@contextmanager
def _directory_redirect(redirect, target):
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(redirect), str(target)],
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
    else:
        redirect.symlink_to(target, target_is_directory=True)
    try:
        assert redirect.resolve() == target.resolve()
        yield
    finally:
        if os.name == "nt":
            redirect.rmdir()  # 只删除本测试创建的联接入口，不递归、不跟随目标。
        else:
            redirect.unlink()


def test_default_paths_preserve_legacy_values_and_do_not_create_directories(tmp_path):
    store, layouts = tmp_path / "project/tavottofile", tmp_path / "data/layouts"
    assert formats.named_dir(store) == store
    assert formats.autosave_dir(layouts) == layouts / documents.AUTOSAVE_DIRNAME
    assert formats.versions_dir(store, layouts / "_versions") == store / "versions"
    assert formats.versions_dir(None, layouts / "_versions") == layouts / "_versions"
    assert formats.named_dir(Path("relative")) == Path("relative")
    assert not list(tmp_path.iterdir())
    assert formats.session_namespace() is None
    assert formats.session_namespace(formats.CHART4) == "v4"


@pytest.mark.parametrize("kind", PARTS)
def test_v4_paths_are_disjoint_and_read_side_does_not_create_them(tmp_path, kind):
    base = tmp_path / "base"
    expected = base.joinpath(*PARTS[kind])
    assert _path(kind, base) == expected
    assert not base.exists()
    assert _path(kind, base, create=True) == expected
    assert expected.is_dir()
    assert base.is_dir()
    assert _path(kind, base) == expected


def test_v4_no_project_versions_use_layouts_namespace_not_legacy_versions(tmp_path):
    legacy = tmp_path / "layouts/_versions"
    expected = tmp_path / "layouts/documents/v4/versions"
    assert formats.versions_dir(None, legacy, formats.CHART4) == expected
    assert not expected.exists()


@pytest.mark.parametrize("bad", [None, "", "V4", "v5", "native-manifest/1", 4, True, {}, []])
def test_unknown_format_is_rejected_before_touching_any_path(tmp_path, monkeypatch, bad):
    def touched(*_args, **_kwargs):
        pytest.fail("format准入之前已访问文件系统")

    monkeypatch.setattr(Path, "lstat", touched)
    for operation in (
        lambda: formats.validate_format(bad),
        lambda: formats.session_namespace(bad),
        lambda: formats.validate_document({}, bad),
        lambda: formats.named_dir(tmp_path, bad),
        lambda: formats.autosave_dir(tmp_path, bad),
        lambda: formats.versions_dir(tmp_path, tmp_path, bad),
    ):
        with pytest.raises(documents.DocumentError) as caught:
            operation()
        assert caught.value.code == "invalid_document"


def test_validation_uses_documents_authority_and_keeps_legacy_objects(monkeypatch):
    legacy = {"schema": 2, "name": "old", "objects": []}
    assert formats.validate_document(legacy) is legacy
    project = {"schema": 3, "canvases": [{"objects": []}]}
    assert formats.validate_document(project) is project
    chart = {"schema": 4, "canvases": [{"objects": []}]}
    assert formats.validate_document(chart, formats.CHART4) is chart
    with pytest.raises(documents.DocumentError, match="schema 4"):
        formats.validate_document(legacy, formats.CHART4)
    with pytest.raises(documents.DocumentError) as caught:
        formats.validate_document(chart)
    assert caught.value.code == "schema_too_new"
    calls = []

    def authoritative(raw, *, require_sources=True):
        calls.append((raw, require_sources))
        return raw

    monkeypatch.setattr(documents, "validate_chart_document", authoritative)
    assert formats.validate_document(chart, formats.CHART4, require_sources=False) is chart
    assert calls == [(chart, False)]


@pytest.mark.parametrize(
    "kind,level",
    [(kind, level) for kind, parts in PARTS.items() for level in range(len(parts) + 1)],
)
@pytest.mark.parametrize("create", [False, True])
def test_every_owned_component_rejects_redirect_even_to_same_project(tmp_path, kind, level, create):
    project = tmp_path / "project"
    project.mkdir()
    base = project / "tavottofile"
    target = project / "old-layouts"
    target.mkdir()
    components = [base, *[base.joinpath(*PARTS[kind][:i]) for i in range(1, len(PARTS[kind]) + 1)]]
    redirect = components[level]
    redirect.parent.mkdir(parents=True, exist_ok=True)
    with _directory_redirect(redirect, target):
        before = list(target.iterdir())
        with pytest.raises(documents.DocumentError) as caught:
            _path(kind, base, create=create)
        assert caught.value.code == "layout_outside_project"
        assert list(target.iterdir()) == before
        assert redirect.resolve() == target.resolve()


def test_directory_redirect_cannot_hide_an_existing_v4_document(tmp_path):
    base = tmp_path / "tavottofile"
    base.mkdir()
    target = tmp_path / "old-layouts"
    target.mkdir()
    redirect = base / "documents"
    (target / "v4").mkdir()
    document = target / "v4/keep.json"
    document.write_bytes(b"existing chart project")
    with _directory_redirect(redirect, target):
        with pytest.raises(documents.DocumentError) as caught:
            formats.named_dir(base, formats.CHART4, create=True)
        assert caught.value.code == "layout_outside_project"
        assert document.read_bytes() == b"existing chart project"


def test_file_in_a_directory_slot_or_upward_path_is_not_used(tmp_path):
    base = tmp_path / "tavottofile"
    base.mkdir()
    (base / "documents").write_bytes(b"keep")
    with pytest.raises(documents.DocumentError):
        formats.named_dir(base, formats.CHART4)
    assert (base / "documents").read_bytes() == b"keep"
    with pytest.raises(documents.DocumentError):
        formats.named_dir(base / ".." / "elsewhere", formats.CHART4)


def test_format_adapter_import_has_no_scientific_dependencies(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = "import sys;sys.path.insert(0,sys.argv[1]);from tavotto.engine import documentformat;assert not any(x in sys.modules for x in ('matplotlib','numpy','PIL','pikepdf','pypdfium2'))"
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(root / "src")],
        cwd=tmp_path,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
