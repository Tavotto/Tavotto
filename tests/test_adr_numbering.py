"""ADR 编号不许重复——一条枚举判据，替掉「取号时记得看一眼」。

**为什么值得有**：2026-09-06/07 真的撞了两次（`0045` 与 `0046` 各有两份），而且
重号**穿过了完整的 PR 门禁合进 main**，19 项检查一项都没响，是评审时人眼看出来的。

**成因是结构性的，不是谁不小心**：取号方式是「看目录里最大号 + 1」——共享序列上的
读改写。#295 与 #301 同期在飞，各自看到的最大号都是 0044，于是必然都取 0045。靠
「大家小心」维持不住：并发下每个人看到的都是过期的最大值。真正能防住的是派工时
预分配号段，加上这里这条落地前的枚举判据。

判据只有一条，也只该有一条：**文件名前四位数字在 `docs/adr/` 内唯一**。不检查内容、
不检查连续、不检查有没有跳号——那些都不是缺陷（一个被否掉的 ADR 留个空号完全正常）。
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
