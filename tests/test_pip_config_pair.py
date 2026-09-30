"""严格同源对：`pip config list` 的读法（#737）。

引擎 `engine/deprepair.pip_config_keys`（问「用户配没配包源」）与插件 `codex-plugin/mcp/server.py`
的 `pip_config_items` / `pip_config_index_url`（问「pip 从哪个索引装」）都**直接问 pip**，只把它打印的
`<节>.<键>=<值>` 按节筛（`global` / `install` / `:env:`）、按 pip 的规则规范化键名。插件 import 不到引擎，
所以是两份实现：两侧各读 `tests/golden/pip_config_list_vectors.json`，不读对方源码。判据的主语：同一段
`pip config list` 输出，两侧认出的「作用于 pip install 的键」必须是同一个集合，`index-url` 设没设必须
同一个结论（`download.index-url` 两侧都不认）。
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

from tavotto.engine import deprepair

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))
launcher = importlib.import_module("server")

VECTORS = json.loads(
    (ROOT / "tests" / "golden" / "pip_config_list_vectors.json").read_text(encoding="utf-8")
)["vectors"]
IDS = [v["name"] for v in VECTORS]


def test_the_vectors_cover_the_cases_that_matter():
    """前提：向量里真有「只在 download 节」「三节都有」「键名要规范化」「空输出」这几档。"""
    texts = [v["text"] for v in VECTORS]
    assert any(
        t.startswith("download.index-url") and v["index_section"] is None
        for t, v in zip(texts, VECTORS)
    )
    assert any(v["index_section"] == ":env:" for v in VECTORS)
    assert any(v["index_section"] == "install" for v in VECTORS)
    assert any("--index-url" in t for t in texts)
    assert any(t == "" for t in texts)


@pytest.mark.parametrize("vec", VECTORS, ids=IDS)
def test_engine_side_reads_the_golden(vec):
    assert sorted(deprepair.pip_config_keys(vec["text"])) == vec["keys"]


@pytest.mark.parametrize("vec", VECTORS, ids=IDS)
def test_plugin_side_reads_the_golden(vec):
    keys = {key for _section, key, _value in launcher.pip_config_items(vec["text"])}
    assert sorted(keys) == vec["keys"]
    assert launcher.pip_config_index_url(vec["text"]) == (vec["index_url"], vec["index_section"])


@pytest.mark.parametrize("vec", VECTORS, ids=IDS)
def test_both_sides_agree_whether_an_index_url_is_set(vec):
    """同一段输出，引擎说「配了 index-url」⇔ 插件找得到生效的那一节。"""
    engine_says = "index-url" in deprepair.pip_config_keys(vec["text"])
    plugin_says = launcher.pip_config_index_url(vec["text"])[1] is not None
    assert engine_says == plugin_says


def test_the_install_sections_are_one_closed_set_on_both_sides():
    """节的闭集与顺序（插件按这个顺序定覆盖：后者生效）两侧相同。"""
    assert launcher._PIP_INSTALL_SECTIONS == deprepair._PIP_INSTALL_SECTIONS
