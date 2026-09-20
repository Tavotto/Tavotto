"""一张**合成的**脸，给 RenderCore 纯模型的用例用（不需要 uharfbuzz / fontTools / 字体文件）。

它实现 `tavotto.rendercore.typography.Face` 协议：固定 cmap、固定 advance、逐字符 shaping。
几处刻意造出的排版形状，让写入器 / 排版层的边界有东西可量：

* `fi` 连字：`f` 后紧跟 `i` 合成**一个**字形，cluster 原文是两个码位（ToUnicode 多码位）；
* `e` + U+0301：合成一个 `eacute` 字形（分解序列 → 一个字形）；
* `a` + U+0303：**一个 cluster 出两个字形**（基字 + 组合标记未合成），第二个字形原文为空；
* `y_offset`：U+0303 那个标记字形带 y_offset（写入器要用 Ts 抬它）。

advance 是整数字体单位（upem 1000）：拉丁 600、空格 300、CJK 1000、.notdef 500。
"""

from __future__ import annotations

from dataclasses import dataclass

from tavotto.rendercore.ir import FontResource, Glyph

UPEM = 1000
LATIN_ADV = 600
SPACE_ADV = 300
CJK_ADV = 1000
NOTDEF_ADV = 500

#: 合成脸的 cmap：ASCII 可见字符 + 若干希腊 / 符号；CJK 脸只有汉字与 ℃。
_LATIN = {cp: 10 + i for i, cp in enumerate(range(0x20, 0x7F))}
_LATIN.update({0x03B1: 200, 0x03B2: 201, 0x03B3: 202, 0x0394: 203, 0x00B5: 204, 0x03BC: 205})
_LATIN.update({0x00E9: 210, 0x2075: 211, 0x2082: 212, 0x00B0: 213, 0x00C5: 214, 0x00D7: 215})
_LATIN.update({0x00E3: 216, 0xFB01: 220})  # ã（预组合）、ﬁ 连字字形
_CJK = {cp: 300 + i for i, cp in enumerate(range(0x4E00, 0x4E00 + 400))}
_CJK.update({0x2103: 800, 0x56FE: 801, 0x2077: 802})  # ℃ / 图 / ⁷（只在 CJK 脸里）

LIGATURE_GID = 220
EACUTE_GID = 210
TILDE_MARK_GID = 230  # 只在 shaping 里出现（没有 cmap 条目，像真实字体的 GPOS 标记字形）


def _resource(
    face_id: str, family: str, bold: bool, italic: bool, kind: str = "truetype"
) -> FontResource:
    digest = (face_id * 8).encode("utf-8").hex()[:64].ljust(64, "0")
    return FontResource(
        face_id=face_id,
        sha256=digest,
        family=family,
        bold=bold,
        italic=italic,
        kind=kind,
        postscript_name=f"Fake{face_id}",
        units_per_em=UPEM,
    )


@dataclass
class FakeFace:
    resource: FontResource
    cmap: dict[int, int]
    ascender: float = 0.891
    descender: float = -0.216
    default_adv: int = LATIN_ADV

    def covers(self, cp: int) -> bool:
        return cp in self.cmap

    def advance(self, gid: int) -> int:
        if gid == 0:
            return NOTDEF_ADV
        if gid == self.cmap.get(0x20):
            return SPACE_ADV
        return self.default_adv

    def shape(self, text: str) -> tuple[Glyph, ...]:
        out: list[Glyph] = []
        i = 0
        while i < len(text):
            ch = text[i]
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if ch == "f" and nxt == "i" and 0xFB01 in self.cmap:
                out.append(Glyph(LIGATURE_GID, "fi", self.advance(LIGATURE_GID)))
                i += 2
                continue
            if ch == "e" and nxt == "́" and 0x00E9 in self.cmap:
                out.append(Glyph(EACUTE_GID, "é", self.advance(EACUTE_GID)))
                i += 2
                continue
            if ch == "a" and nxt == "̃" and 0x00E3 in self.cmap:
                base = self.cmap[ord("a")]
                out.append(Glyph(base, "ã", self.advance(base)))
                out.append(
                    Glyph(TILDE_MARK_GID, "", 0, x_offset=-self.advance(base) // 2, y_offset=120)
                )
                i += 2
                continue
            gid = self.cmap.get(ord(ch), 0)
            out.append(Glyph(gid, ch, self.advance(gid)))
            i += 1
        return tuple(out)


class FakeProvider:
    """三个族 × 四态的合成脸 + 一张 CJK 脸。`cjk=False` 造「没有 CJK 脸」的世界。"""

    def __init__(self, cjk: bool = True) -> None:
        self._cjk = cjk
        self._cache: dict[tuple[str, bool, bool], FakeFace] = {}

    def face_for(self, family: str, bold: bool, italic: bool) -> FakeFace:
        key = (family, bool(bold), bool(italic))
        if key not in self._cache:
            style = ("Bold" if bold else "") + ("Italic" if italic else "") or "Regular"
            fid = f"{family}-{style}"
            self._cache[key] = FakeFace(
                _resource(fid, family, bool(bold), bool(italic)), dict(_LATIN)
            )
        return self._cache[key]

    def cjk_face(self) -> FakeFace | None:
        if not self._cjk:
            return None
        key = ("cjk", False, False)
        if key not in self._cache:
            self._cache[key] = FakeFace(
                _resource("cjk-Regular", "cjk", False, False, kind="cff-cid"),
                dict(_CJK),
                ascender=0.88,
                descender=-0.12,
                default_adv=CJK_ADV,
            )
        return self._cache[key]
