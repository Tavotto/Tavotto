"""项目包成员身份与错误的纯标准库权威；不读取 ZIP 或项目配置。"""

from __future__ import annotations

import ntpath
import unicodedata
import zipfile
from pathlib import PureWindowsPath

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
