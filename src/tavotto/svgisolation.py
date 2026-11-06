"""已通过 svgcheck 的 SVG id / 局部引用 / 受限 CSS 隔离（纯标准库）。"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

SVG = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"
_URL_REF = re.compile(r"url\(\s*(['\"]?)#([^\s)'\"]+)\1\s*\)", re.I)
_CSS_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_CSS_ID = re.compile(r"#([A-Za-z_][A-Za-z0-9_-]*)")


def isolate_svg(
    root: ET.Element,
    *,
    prefix: str,
    scope: str | None = None,
    owned_class: str | None = None,
) -> dict[str, str]:
    """原地隔离派生树；返回原 id→新 id。原输入字节不参与修改。

    scope 保留 SVG writer 的既有实例祖先作用域。owned_class 则标记每个
    原节点并限定选择器的末级目标，防止新插入的其他来源后代被全局 * / g
    规则污染；所有规则增加相同 specificity，原有规则优先级不变。
    """
    ids = {node.get("id"): prefix + node.get("id") for node in root.iter() if node.get("id")}

    def urls(value: str) -> str:
        return _URL_REF.sub(lambda m: f"url(#{ids[m.group(2)]})", value)

    for node in root.iter():
        for key, value in list(node.attrib.items()):
            if key == "id":
                node.set(key, ids[value])
            elif key in ("href", f"{{{XLINK}}}href") and value.startswith("#"):
                node.set(key, "#" + ids[value[1:]])
            else:
                node.set(key, urls(value))
        if node.tag == f"{{{SVG}}}style" and node.text:
            rules = []
            for match in _CSS_RULE.finditer(node.text):
                selectors = _CSS_ID.sub(
                    lambda m: "#" + ids.get(m.group(1), prefix + m.group(1)), match.group(1)
                )
                scoped = []
                for selector in selectors.split(","):
                    selector = selector.strip()
                    if owned_class:
                        selector += "." + owned_class
                    if scope:
                        selector = f"#{scope} {selector}"
                    scoped.append(selector)
                rules.append(",".join(scoped) + "{" + urls(match.group(2)) + "}")
            node.text = "\n".join(rules)
        if owned_class:
            node.set("class", " ".join(filter(None, (node.get("class", ""), owned_class))))
    return ids
