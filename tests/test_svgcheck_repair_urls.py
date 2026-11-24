"""`svgcheck._repair_urls`：worker 输出改名时改写 `url(#…)`，结果与旧正则逐字一致、且是线性的（#2119）。"""

import random
import re

from tavotto.rendercore import svgcheck

#: #1893 起用的正则（平方级）：只当对照的标准答案，产品代码里已不再用
_OLD = re.compile(r"url\(\s*(['\"]?)#([^'\")]+?)\1\s*\)", re.I)


def _old(value: str, renamed: dict[str, str]) -> str:
    return _OLD.sub(
        lambda m: f"url({m.group(1)}#{renamed.get(m.group(2), m.group(2))}{m.group(1)})", value
    )


_PIECES = ["url(", "URL(", "uRl(", "rl(", "#", "'", '"', ")", "(", " ", "\t", "　", "\x1c"]
_PIECES += ["\xa0", "a", "b", "x", "面板 A"]
_NAMES = ["a", "b", "x", " ", "a ", "ab", "面板 A", "a　", "　"]


def test_repair_urls_matches_the_old_regex_on_random_inputs():
    rnd = random.Random(2119)
    for _ in range(40000):
        value = "".join(rnd.choice(_PIECES) for _ in range(rnd.randint(0, 14)))
        renamed = {n: f"tvid{i}" for i, n in enumerate(_NAMES) if rnd.random() < 0.5}
        assert svgcheck._repair_urls(value, renamed) == _old(value, renamed), (value, renamed)


def test_repair_urls_known_shapes():
    renamed = {"面板 A": "tvid1", " ": "tvid2"}
    cases = [
        "fill:url(#面板 A)",
        "fill:url( '#面板 A' )",
        'fill:url("#面板 A"  )',
        "fill:URL(#面板 A  )",
        "url(#   )",
        "url(#面板 A",
        "url('#面板 A\")",
        "url(#a(b))",
        "stroke:#000000",
        "url(#面板 Aurl(#面板 A)",
    ]
    for value in cases:
        assert svgcheck._repair_urls(value, renamed) == _old(value, renamed), value


class _CountingStop:
    """数 `_REPAIR_URL_STOP` 为找停止符一共往前扫了多少个字符。"""

    def __init__(self, pattern):
        self.pattern, self.scanned = pattern, 0

    def search(self, value, pos):
        found = self.pattern.search(value, pos)
        self.scanned += (found.start() if found else len(value)) - pos
        return found


def test_repair_urls_scans_each_char_for_a_stop_once(monkeypatch):
    # 平方级的根因：每个失败的起点都从头重扫到下一个引号 / `)`。线性实现里找停止符的扫描总长不超过
    # 输入长度——数扫过的字符（确定性），不计时（负载高的机器上计时会抖）。
    counter = _CountingStop(svgcheck._REPAIR_URL_STOP)
    monkeypatch.setattr(svgcheck, "_REPAIR_URL_STOP", counter)
    for unit in ("url(#a", "URL(  #a b ", "url('#a url(#a "):
        counter.scanned = 0
        value = unit * 20000  # 没有一个 `)`：每个起点都不成立，旧正则每个起点都扫到串尾
        assert svgcheck._repair_urls(value, {"a": "z"}) == value
        assert counter.scanned <= len(value), (unit, counter.scanned, len(value))
