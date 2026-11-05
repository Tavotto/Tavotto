"""画布文字「用原图字体」的纯模型部分（ADR 0164）：文档里那条 `source_font` 的形状与解析。

纯标准库、纯模型层（`plan` 只认这里，不认 `userfonts` 的定位 / 读文件）。本机定位与许可检查在
`userfonts.py`（native 适配层）。

`TextObject.sourceFont`（前端驼峰）→ 导出载荷 `source_font`（`web/src/lib/exportPayload.ts`）→ 这里的
`parse_source_font`。三处字段名 / 闭集 / 取值范围严格同源（`docs/rules/repo/same-origin-pairs.md`；
看护 `tests/test_source_font_wire.py` + `web/src/lib/sourceFont.test.ts`）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 为什么一张用户脸此刻不能用。**闭集**，也是 `source_font_unavailable` 问题的 `reason`；
#: 前端 `web/src/lib/sourceFont.ts` 的 `SOURCE_FONT_REASONS` 与它严格同源。
REASONS = (
    "not_found",  # 本机没有这个字节的字体文件（换了电脑 / 卸载了）
    "embedding_restricted",  # OS/2 fsType 不允许嵌入（或不允许子集）
    "too_large",  # 超过读取上限
    "unsupported_format",  # 不是我们写得出的字体程序（变量 / 彩色 / 位图 / 非 CID 的 CFF / cmap 读不出）
    "unreadable",  # 读不出来 / 结构损坏
    "invalid",  # 文档里这条 source_font 本身不合形状
)


class UserFontUnavailable(Exception):
    """一张用户脸此刻不能用。`reason` 是 `REASONS` 里的一个，`detail` 只给日志（可含路径，**不出机器**）。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        assert reason in REASONS, reason
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


HEX64 = re.compile(r"^[0-9a-f]{64}$")


#: 一张脸最多带几张同族兄弟（RIBBI 一族四张，自己占一张）
MAX_SIBLINGS = 3


@dataclass(frozen=True)
class SiblingFace:
    """同族里另一张脸（ADR 0169 修订）：身份 + 它自己的粗斜。归属来自字体清单，不是按文件名猜的。"""

    sha256: str
    face: int = 0
    bold: bool = False
    italic: bool = False


@dataclass(frozen=True)
class FaceRef:
    """一张脸的身份。`family` 只用于显示 / 提示，**不参与认脸**。`siblings` 是同族的其它粗斜，
    缺席 = 这张脸是单个文件（复制自图内文字的老身份）。"""

    family: str
    sha256: str
    face: int = 0
    siblings: tuple[SiblingFace, ...] = ()

    @property
    def identity(self) -> tuple[str, int]:
        return (self.sha256, self.face)

    def sibling(self, bold: bool, italic: bool) -> FaceRef | None:
        """同族里正好是这个粗斜的那张脸；没有就 None。"""
        for s in self.siblings:
            if (s.bold, s.italic) == (bold, italic):
                return FaceRef(self.family, s.sha256, s.face)
        return None


@dataclass(frozen=True)
class SourceFont:
    """`TextObject.sourceFont`（导出载荷里是 `source_font`）。`bold` / `italic` 是这张脸**自己的**
    粗斜（来自原图那张脸），不是对象的粗斜开关。`cjk` 是原图里中日韩字符实际由哪张脸画的（可缺席：
    缺席就是批准的 CJK 脸（随族：衬线 Noto Serif SC、其余 Noto Sans SC，粗体随对象）画汉字）。"""

    primary: FaceRef
    bold: bool = False
    italic: bool = False
    cjk: FaceRef | None = None

    @property
    def family(self) -> str:
        return self.primary.family


def _identity_ok(sha: object, face: object) -> bool:
    if not isinstance(sha, str) or not HEX64.match(sha):
        return False
    return not isinstance(face, bool) and isinstance(face, int) and 0 <= face < 1024


def _siblings(raw: object) -> tuple[SiblingFace, ...] | None:
    """`siblings` → 元组；形状不对 → None。"""
    if raw is None:
        return ()
    if not isinstance(raw, list) or len(raw) > MAX_SIBLINGS:
        return None
    out: list[SiblingFace] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        sha, face = item.get("sha256"), item.get("face", 0)
        bold, italic = item.get("bold", False), item.get("italic", False)
        if (
            not _identity_ok(sha, face)
            or not isinstance(bold, bool)
            or not isinstance(italic, bool)
        ):
            return None
        out.append(SiblingFace(sha, face, bold is True, italic is True))
    return tuple(out)


def _face_ref(raw: object) -> FaceRef | None:
    if not isinstance(raw, dict):
        return None
    sha = raw.get("sha256")
    face = raw.get("face", 0)
    family = raw.get("family")
    if not _identity_ok(sha, face):
        return None
    if not isinstance(family, str) or not family.strip() or len(family) > 200:
        return None
    siblings = _siblings(raw.get("siblings"))
    if siblings is None:
        return None
    return FaceRef(family=family.strip(), sha256=sha, face=face, siblings=siblings)


def parse_source_font(raw: object) -> tuple[SourceFont | None, str | None]:
    """对象里的 `source_font` → (值, 原因)。缺席 → (None, None)——现状逐字不变；形状不对 →
    (None, "invalid")：手改坏的文档不炸导出，但留下问题码，不装作没这回事。"""
    if raw is None:
        return None, None
    primary = _face_ref(raw)
    if primary is None:
        return None, "invalid"
    assert isinstance(raw, dict)
    cjk = None
    if raw.get("cjk") is not None:
        cjk = _face_ref(raw["cjk"])
        if cjk is None:
            return None, "invalid"
    return (
        SourceFont(
            primary=primary,
            bold=raw.get("bold") is True,
            italic=raw.get("italic") is True,
            cjk=cjk,
        ),
        None,
    )
