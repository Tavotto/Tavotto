"""单一存储的目录派生：命名文档 / 自动保存 / 版本是同一套路径，每级都过目录守卫。

schema 3 与 schema 4 没有第二套目录、没有格式参数、没有会话命名空间 —— 这里钉住的
是「只有一个答案」这件事本身：派生函数不带格式轴，且每个自有目录组件都拒链接重定向。
"""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from tavotto.engine import documentformat as formats, documents

#: 每种目录的组成部分（相对权威根）。三种目录同根：项目 `tavottofile/` 或数据目录 `layouts/`。
PARTS = {
    "named": (),
    "autosave": (documents.AUTOSAVE_DIRNAME,),
    "versions": ("versions",),
}


def _path(kind, base, *, create=False):
    if kind == "named":
        return formats.named_dir(base, create=create)
    if kind == "autosave":
        return formats.autosave_dir(base, create=create)
    return formats.versions_dir(base, Path(base).parent / documents.VERSIONS_DIRNAME, create=create)


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


def test_paths_are_the_single_storage_and_read_side_creates_nothing(tmp_path):
    store, layouts = tmp_path / "project/tavottofile", tmp_path / "data/layouts"
    assert formats.named_dir(store) == store
    assert formats.autosave_dir(layouts) == layouts / documents.AUTOSAVE_DIRNAME
    assert formats.versions_dir(store, layouts / "_versions") == store / "versions"
    assert formats.versions_dir(None, layouts / "_versions") == layouts / "_versions"
    assert not store.exists()
    assert not layouts.exists()


def test_there_is_no_format_axis_left():
    """同一套目录 = 派生函数不带格式参数；两套目录的入口已经删掉。"""
    for name in ("CHART4", "FORMATS", "session_namespace", "validate_format", "validate_document"):
        assert not hasattr(formats, name), name
    for function in (formats.named_dir, formats.autosave_dir, formats.versions_dir):
        parameters = inspect.signature(function).parameters
        assert "document_format" not in parameters and "format" not in parameters


def test_relative_base_is_resolved_before_the_guard(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "store").mkdir()
    assert formats.named_dir(Path("store")) == (tmp_path / "store").resolve()


@pytest.mark.parametrize("kind", PARTS)
def test_each_level_can_be_created_on_demand(tmp_path, kind):
    base = tmp_path / "tavottofile"
    expected = base.joinpath(*PARTS[kind])
    assert _path(kind, base) == expected
    assert not base.exists()
    assert _path(kind, base, create=True) == expected
    assert expected.is_dir()
    assert base.is_dir()


@pytest.mark.parametrize(
    "kind,level",
    [
        (kind, level)
        for kind, parts in PARTS.items()
        for level in range(len(parts) + 1)
        if kind != "versions"
    ],
)
@pytest.mark.parametrize("create", [False, True])
def test_every_owned_component_rejects_redirect_even_to_same_project(tmp_path, kind, level, create):
    project = tmp_path / "project"
    project.mkdir()
    base = project / "tavottofile"
    target = project / "old-layouts"
    target.mkdir()
    components = [base] + [base.joinpath(*PARTS[kind][:i]) for i in range(1, len(PARTS[kind]) + 1)]
    redirect = components[level]
    redirect.parent.mkdir(parents=True, exist_ok=True)
    with _directory_redirect(redirect, target):
        before = list(target.iterdir())
        with pytest.raises(documents.DocumentError) as caught:
            _path(kind, base, create=create)
        assert caught.value.code == "layout_outside_project"
        assert list(target.iterdir()) == before
        assert redirect.resolve() == target.resolve()


@pytest.mark.parametrize("level", [0, 1])
@pytest.mark.parametrize("create", [False, True])
def test_project_versions_directory_rejects_redirect(tmp_path, level, create):
    project = tmp_path / "project"
    project.mkdir()
    base = project / "tavottofile"
    target = project / "old-layouts"
    target.mkdir()
    redirect = [base, base / "versions"][level]
    redirect.parent.mkdir(parents=True, exist_ok=True)
    with _directory_redirect(redirect, target):
        before = list(target.iterdir())
        with pytest.raises(documents.DocumentError) as caught:
            formats.versions_dir(base, tmp_path / "_versions", create=create)
        assert caught.value.code == "layout_outside_project"
        assert list(target.iterdir()) == before


@pytest.mark.parametrize("level", [0, 1])
@pytest.mark.parametrize("create", [False, True])
def test_no_project_versions_directory_rejects_redirect(tmp_path, level, create):
    """未打开项目时的 `layouts/_versions` 也过同一道守卫（旧派生不经守卫的时代结束）。"""
    layouts = tmp_path / "layouts"
    target = tmp_path / "target"
    target.mkdir()
    redirect = [layouts, layouts / documents.VERSIONS_DIRNAME][level]
    redirect.parent.mkdir(parents=True, exist_ok=True)
    with _directory_redirect(redirect, target):
        before = list(target.iterdir())
        with pytest.raises(documents.DocumentError) as caught:
            formats.versions_dir(None, layouts / documents.VERSIONS_DIRNAME, create=create)
        assert caught.value.code == "layout_outside_project"
        assert list(target.iterdir()) == before


def test_autosave_redirect_cannot_hide_an_existing_slot(tmp_path):
    base = tmp_path / "layouts"
    base.mkdir()
    target = tmp_path / "old-slots"
    target.mkdir()
    slot = target / "keep.json"
    slot.write_bytes(b"existing autosave")
    with _directory_redirect(base / documents.AUTOSAVE_DIRNAME, target):
        with pytest.raises(documents.DocumentError) as caught:
            formats.autosave_dir(base, create=True)
        assert caught.value.code == "layout_outside_project"
        assert slot.read_bytes() == b"existing autosave"


def test_directory_redirect_cannot_hide_an_existing_document(tmp_path):
    base = tmp_path / "tavottofile"
    target = tmp_path / "old-layouts"
    target.mkdir()
    document = target / "keep.json"
    document.write_bytes(b"existing chart project")
    with _directory_redirect(base, target):
        with pytest.raises(documents.DocumentError) as caught:
            formats.named_dir(base, create=True)
        assert caught.value.code == "layout_outside_project"
        assert document.read_bytes() == b"existing chart project"


def test_file_in_a_directory_slot_or_upward_path_is_not_used(tmp_path):
    base = tmp_path / "tavottofile"
    base.mkdir()
    (base / "documents").write_bytes(b"keep")
    with pytest.raises(documents.DocumentError):
        formats.named_dir(base / "documents")
    assert (base / "documents").read_bytes() == b"keep"
    with pytest.raises(documents.DocumentError):
        formats.named_dir(base / ".." / "elsewhere")


def test_directory_adapter_import_has_no_scientific_dependencies(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = "import sys;sys.path.insert(0,sys.argv[1]);from tavotto.engine import documentformat;assert not any(x in sys.modules for x in ('matplotlib','numpy','PIL','pikepdf','pypdfium2'))"
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(root / "src")],
        cwd=tmp_path,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
