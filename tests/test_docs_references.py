"""`docs/` 的交叉引用：ADR 号不许重复，文档间的相对链接必须解析得开。

两条判据同一个成因——**改名与取号都是在共享命名空间上的读改写**，靠「记得回来改」
维持不住。它们各自都在落地前抓到过真东西（见下）。

## 一、ADR 编号不许重复

**为什么值得有**：2026-09-06/07 真的撞了两次（`0045` 与 `0046` 各有两份），而且
重号**穿过了完整的 PR 门禁合进 main**，19 项检查一项都没响，是评审时人眼看出来的。

**成因是结构性的，不是谁不小心**：取号方式是「看目录里最大号 + 1」——共享序列上的
读改写。#295 与 #301 同期在飞，各自看到的最大号都是 0044，于是必然都取 0045。靠
「大家小心」维持不住：并发下每个人看到的都是过期的最大值。真正能防住的是派工时
预分配号段，加上这里这条落地前的枚举判据。

判据只有一条，也只该有一条：**文件名前四位数字在 `docs/adr/` 内唯一**。不检查内容、
不检查连续、不检查有没有跳号——那些都不是缺陷（一个被否掉的 ADR 留个空号完全正常）。

## 二、文档之间的相对链接必须解析得开

文件名唯一 ≠ 链接指得到。改名漏改引用在这仓库**已经发生过**：
`0035-axis-tick-direct-manipulation.md` 指着 `0017-exact-manifest-authority.md`，而实际
文件叫 `0017-display-fallback-vs-geometry-authority.md`——本轮之前就断着，没人发现。

这条判据与上一条是搭档：改号必然要动引用，而「引用还指得到吗」只有它答得出。范围是
`docs/` 下所有 Markdown 里的相对 `.md` 链接（外链、锚点不管——那是另一件事，需要联网
或解析标题，不该混进一条判据里）。
"""

import re
from collections import defaultdict
from pathlib import Path

ADR_DIR = Path(__file__).resolve().parent.parent / "docs" / "adr"
#: 文件名形状：四位号 + `-` + slug + `.md`。README / 模板这类非 ADR 不参与。
ADR_NAME = re.compile(r"^(\d{4})-[a-z0-9-]+\.md$")


def _adrs() -> list[Path]:
    return sorted(p for p in ADR_DIR.glob("*.md") if ADR_NAME.match(p.name))


def test_the_corpus_is_actually_there():
    """判据的前提：真的扫到了一批 ADR。

    没有这一条，`docs/adr/` 挪了地方或命名整体变了时，下面那条会在**空集合**上
    恒真地绿——空门禁比没有门禁更坏。
    """
    found = _adrs()
    assert len(found) >= 40, f"只扫到 {len(found)} 份 ADR，判据多半已经量在空集合上"


def test_adr_numbers_are_unique():
    """同一个号只能有一份 ADR。

    重号的代价不是不好看：仓库里到处是「见 ADR 00xx」的引用，撞号之后那些引用
    指向两份互不相干的决策，读的人无从判断该看哪一份。
    """
    by_number: dict[str, list[str]] = defaultdict(list)
    for path in _adrs():
        by_number[ADR_NAME.match(path.name).group(1)].append(path.name)
    dupes = {n: sorted(names) for n, names in by_number.items() if len(names) > 1}
    assert not dupes, (
        f"ADR 号重复: {dupes}。取号请用「目录里最大号 + 1」之外的方式确认——"
        f"并发的两个 PR 看到的最大号是同一个，必然撞。"
    )


def test_every_markdown_file_here_is_either_an_adr_or_a_known_exception():
    """新文件要么合形状，要么显式登记。

    不登记的话，`0045-cjk.md` 与 `0045_cjk.md` 这类**不合形状**的重号会从判据的
    正则底下溜过去——它扫不到的东西，它当然也说不出重复。
    """
    allowed = {"README.md", "TEMPLATE.md"}
    stray = sorted(
        p.name for p in ADR_DIR.glob("*.md") if not ADR_NAME.match(p.name) and p.name not in allowed
    )
    assert not stray, f"docs/adr/ 里这些文件不合 `NNNN-slug.md` 形状，也没登记为例外: {stray}"


# ---------------------------------------------------------------- 二、链接
DOCS = ADR_DIR.parent
#: `[文字](相对路径.md)` 与 `[文字](相对路径.md#锚点)`。绝对 URL 不匹配（`://`），
#: 锚点部分丢掉——判的是「文件在不在」，不是「标题在不在」。
MD_LINK = re.compile(r"\]\(((?!\w+://)[^)#\s]+\.md)(?:#[^)]*)?\)")


def _links() -> list[tuple[Path, str, Path]]:
    out = []
    for doc in sorted(DOCS.rglob("*.md")):
        for m in MD_LINK.finditer(doc.read_text(encoding="utf-8")):
            out.append((doc, m.group(1), (doc.parent / m.group(1)).resolve()))
    return out


def test_there_are_links_to_check():
    """判据的前提：真的扫到了链接。正则写错时这条先红，而不是让下面那条空转。"""
    found = _links()
    assert len(found) >= 100, f"只扫到 {len(found)} 条相对链接，判据多半已经量在空集合上"


def test_every_relative_doc_link_resolves():
    """`docs/` 里指向别的 Markdown 的相对链接，目标文件必须真的在。

    改名漏改引用的表现是**读的人点开 404**，而写的人永远看不到——他改完就走了。
    """
    broken = [
        f"{doc.relative_to(DOCS.parent)} → {target}"
        for doc, target, resolved in _links()
        if not resolved.is_file()
    ]
    assert not broken, "这些相对链接指向不存在的文件（多半是改名漏改引用）:\n  " + "\n  ".join(
        broken
    )
