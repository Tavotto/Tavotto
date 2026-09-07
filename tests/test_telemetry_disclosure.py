"""界面上那份「会发送哪些数据」必须与 `EVENTS` 表逐条对得上（UI/UX 审计 T49）。

在此之前，关于页里的数据说明是**一整句散文**。它写下的那天是准确的，之后
`CONSENT_VERSION` 升到 2、`EVENTS` 长了九条，而那句话一个字没动——真机上已经
在发「用了哪个改图助手」「更新装到哪个版本号」，说明里找不到它们。

**漏一条和多写一条同样坏**：多写的那条让人以为我们采得更多；漏掉的那条，是
用户从来没有同意过的采集。而这种漂移不会有任何信号——加事件的人改的是
`telemetry.py`，说明在另一个仓库目录的 JSON 里。

所以把它做成结构：界面按 `web/src/lib/telemetryDisclosure.ts` 的闭集逐条渲染，
这条用例把那个闭集、`EVENTS` 与两份文案钉成一份。加一条事件而不写它的说明，
这里当场红。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tavotto.engine import telemetry
from tests.support.tsconst import exported_string_array

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web" / "src"
DISCLOSURE_TS = WEB / "lib" / "telemetryDisclosure.ts"
LOCALES = WEB / "i18n" / "locales"

pytestmark = pytest.mark.skipif(
    not DISCLOSURE_TS.is_file(),
    reason="没有 web/（wheel/sdist 里不含前端源码）",
)


def _frontend_events() -> list[str]:
    r"""从 TS 现取那个闭集。**不在这里抄第二份**——抄一份就又多了一个会漂的出处。

    读法是结构性的（`tests/support/tsconst.py`）：先把注释与字符串字面量抹掉，
    再在剩下的代码上找**导出的**那一处声明、配对方括号、取里面的字符串。
    从前这里是 `re.search(r"TELEMETRY_DISCLOSED_EVENTS = \[(.*?)\]")`——正则看不见
    语法结构，注释满足它，而 `re.search` 取的是**第一处**匹配：在活声明前面留
    一份注释掉的旧数组，门禁读的就是那段注释，界面已经与 `EVENTS` 漂开了它却
    照样绿（评审 #300-5）。
    """
    return exported_string_array(
        DISCLOSURE_TS.read_text(encoding="utf-8"), "TELEMETRY_DISCLOSED_EVENTS"
    )


def _sends(locale: str) -> dict[str, str]:
    table = json.loads((LOCALES / locale / "dialogs.json").read_text(encoding="utf-8"))
    return table["settings"]["about"]["telemetry"]["sends"]


def test_frontend_closed_set_is_exactly_the_events_table():
    """前端闭集 == `EVENTS` 的键，**连顺序一起比**。

    顺序也是内容的一部分：那张表按「同意 → 会话 → 编辑 → 产出 → 集成 → 维护」
    排，界面照抄，读起来才是用户在产品里走过的路。
    """
    assert _frontend_events() == list(telemetry.EVENTS)


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_every_event_has_a_line_and_no_line_describes_a_phantom(locale: str):
    """两份文案的子键与 `EVENTS` **精确相等**：多一个是在描述不存在的采集。"""
    assert set(_sends(locale)) == set(telemetry.EVENTS)


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_no_line_is_empty(locale: str):
    """空翻译会让那一条在界面上变成一个空 `<li>`——看起来像「这条不发」。"""
    for event, text in _sends(locale).items():
        assert text.strip(), f"{locale} 的 {event} 是空的"


def test_the_lines_mention_the_properties_that_carry_a_closed_enum():
    """抽查几条**最容易漏**的：属性是后加的、而说明是先写的那几条。

    不逐字比对每个属性名——中英文说明是给人读的散文，逐字比会把它锁死成
    机器格式。挑的是三条有明确产品含义、漏掉就会误导的：改图助手用的是哪个
    agent、更新装到了哪个版本、包操作的结局（且**不含包名**）。
    """
    zh = _sends("zh-CN")
    assert "Codex" in zh["ai_assistant_invoked"] and "Claude" in zh["ai_assistant_invoked"]
    assert "pipx" in zh["update_completed"]
    assert "包名" in zh["package_action"]
