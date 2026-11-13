"""包成员权威移层：固定 d0befe02 原实现 oracle，不依赖测试时 git/HEAD。"""

from __future__ import annotations

import ast
import inspect
import ntpath
import sys
import unicodedata
import zipfile
from pathlib import PureWindowsPath

import pytest

from tavotto.engine import documentpackage, packageexecution, packagemembers, runconfig

# 原实现从 git show d0befe02:src/tavotto/engine/documentpackage.py 的 AST 节点原样定格。
# 仅旧版本对照，不提供任何生产规则入口；测试无需 git 历史存在。
_LEGACY_SOURCE = r'''
LAYOUT_MEMBER = "layout.json"

MANIFEST_MEMBER = "package_manifest.json"

_METADATA = frozenset({LAYOUT_MEMBER, MANIFEST_MEMBER})

_is_reserved = getattr(ntpath, "isreserved", lambda name: PureWindowsPath(name).is_reserved())

class PackageError(ValueError):
    """包结构/预算不合法；既有 package_invalid 错误码，reason 是诊断而非权限。"""

    code = "package_invalid"

    def __init__(self, reason: str, detail: str = "Invalid project package") -> None:
        self.reason, self.detail = reason, detail
        self.params = {"reason": reason}
        super().__init__(detail)

def _member_key(name: object, *, directory: bool = False, legacy_names: bool = False) -> str:
    if (
        not isinstance(name, str)
        or not name
        or "\x00" in name
        or (not legacy_names and "\\" in name)
    ):
        raise PackageError("unsafe_member", "Invalid ZIP member path")
    if legacy_names:
        # Same actual member spelling as the stdlib writer; never normalize IDs
        # or grant an extraction path. The caller supplies its approved plan.
        name = zipfile.ZipInfo(name).filename
    path = name[:-1] if directory and name.endswith("/") else name
    parts = path.split("/")
    if any(
        not part
        or part in {".", ".."}
        or (
            not legacy_names
            and (
                part.endswith((".", " "))
                or any(ord(c) < 32 or ord(c) == 127 or c in '<>:"|?*' for c in part)
                or _is_reserved(part)
            )
        )
        for part in parts
    ):
        raise PackageError("unsafe_member", "Unsafe or nonportable ZIP member path")
    if (directory and parts[0] not in {"assets", "scripts"}) or (
        not directory
        and name not in _METADATA
        and (parts[0] not in {"assets", "scripts"} or len(parts) < 2)
    ):
        raise PackageError("unknown_member", "Unknown package member namespace")
    return path if legacy_names else unicodedata.normalize("NFC", path).casefold()

def project_member_key(name: str) -> str:
    """完整 ZIP 资源名 → 项目相对比较键；不是项目目标路径的校验入口。

    输入必须带 assets/ 或 scripts/。仅复用严格成员路径规则，再剥去包内
    namespace；保留科学 ID 原拼写由调用者负责，本函数只返回别名比较键。
    """
    key = _member_key(name)
    _, separator, relative = key.partition("/")
    if not separator:
        raise PackageError("unknown_member", "Only resource members have project targets")
    return relative

'''


@pytest.fixture(scope="module")
def legacy():
    namespace = {
        "__name__": "legacy_package_members_d0befe02",
        "ntpath": ntpath,
        "unicodedata": unicodedata,
        "zipfile": zipfile,
        "PureWindowsPath": PureWindowsPath,
    }
    exec(compile(_LEGACY_SOURCE, "documentpackage.py@d0befe02", "exec"), namespace)
    return namespace


def _outcome(function, *args, **kwargs):
    try:
        return {"value": function(*args, **kwargs)}
    except ValueError as exc:
        return {
            "type": type(exc).__name__,
            "value_error": isinstance(exc, ValueError),
            "reason": exc.reason,
            "code": exc.code,
            "detail": exc.detail,
            "params": exc.params,
            "args": exc.args,
            "text": str(exc),
        }


_NAMES = [
    "layout.json",
    "package_manifest.json",
    "assets/data.csv",
    "scripts/plot.py",
    "assets/中文/图.svg",
    "assets/é.txt",
    "assets/e\u0301.txt",
    "assets/A.txt",
    "assets/a.txt",
    "assets/CON.txt",
    "assets/lpt1",
    "assets/trailing.",
    "assets/trailing ",
    "assets/a:b",
    "assets/a?b",
    "assets/a|b",
    "assets/a*b",
    "assets/control\x01.txt",
    "assets/delete\x7f.txt",
    "assets/dir\\file.txt",
    "assets",
    "assets/",
    "scripts/",
    "assets/../plot.py",
    "assets/./plot.py",
    "assets//plot.py",
    "assets/zero\0suffix",
    "",
    "/assets/plot.py",
    "C:/assets/plot.py",
    "//server/assets/file",
    "other/plot.py",
    None,
    42,
    True,
]


@pytest.mark.parametrize("name", _NAMES)
@pytest.mark.parametrize("legacy_names", [False, True])
@pytest.mark.parametrize("directory", [False, True])
def test_strict_and_explicit_legacy_names_match_fixed_old_impl(
    legacy, name, legacy_names, directory
):
    expected = _outcome(legacy["_member_key"], name, directory=directory, legacy_names=legacy_names)
    assert (
        _outcome(packagemembers._member_key, name, directory=directory, legacy_names=legacy_names)
        == expected
    )
    assert (
        _outcome(documentpackage._member_key, name, directory=directory, legacy_names=legacy_names)
        == expected
    )


@pytest.mark.parametrize("name", _NAMES)
def test_project_projection_uses_identical_strict_name_authority(legacy, name):
    expected = _outcome(legacy["project_member_key"], name)
    assert _outcome(packagemembers.project_member_key, name) == expected
    assert _outcome(documentpackage.project_member_key, name) == expected


def test_existing_container_names_are_same_objects_not_parallel_implementations():
    for name in ("PackageError", "_member_key", "project_member_key", "_METADATA"):
        assert getattr(documentpackage, name) is getattr(packagemembers, name)
    assert documentpackage.LAYOUT_MEMBER == packagemembers.LAYOUT_MEMBER == "layout.json"
    assert (
        documentpackage.MANIFEST_MEMBER == packagemembers.MANIFEST_MEMBER == "package_manifest.json"
    )


def test_execution_and_runconfig_errors_share_container_exception_type(tmp_path):
    with pytest.raises(documentpackage.PackageError) as invalid:
        packageexecution.validate_execution({})
    assert type(invalid.value) is packagemembers.PackageError
    assert invalid.value.reason == "invalid_execution"
    assert invalid.value.params == {"reason": "invalid_execution"}
    with pytest.raises(runconfig.RunConfigUnsupported) as unsafe:
        runconfig.import_portable(
            tmp_path, [{"id": "rc_0123456789ab", "script": "../plot.py", "argv": ["--x"]}]
        )
    assert unsafe.value.reason == "portable_script_invalid"
    assert type(unsafe.value.__cause__) is packagemembers.PackageError


def test_shared_name_module_is_stdlib_leaf_and_consumers_do_not_import_container():
    tree = ast.parse(inspect.getsource(packagemembers))
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert imports
    for node in imports:
        if isinstance(node, ast.Import):
            names = [alias.name.split(".")[0] for alias in node.names]
        else:
            assert node.level == 0
            names = [(node.module or "").split(".")[0]]
        assert all(name in sys.stdlib_module_names for name in names)
    for module in (packageexecution, runconfig):
        tree = ast.parse(inspect.getsource(module))
        imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        local = {
            alias.name
            for node in imports
            if node.level == 1 and node.module is None
            for alias in node.names
        }
        assert "packagemembers" in local
        assert "documentpackage" not in local
