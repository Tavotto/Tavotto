"""用户可见的后端错误：稳定 code + 参数，文案归前端。

约定写在 `app.py` 的 API 段首。这批用例守两件事：

1. 这些端点**真的**给了 code（漏一个的表现不是报错，而是英文界面里冷不丁
   蹦出一句中文——只有装了英文界面的用户会遇到，本地永远复现不了）；
2. 每个 code 在**两种语言**里都有文案，且占位符与后端给的 params 对得上
   （`{{path}}` 写成 `{{dir}}` 的话，界面上就是一个原样的 `{{path}}`）。

新增用户可见的失败时：app.py 给 code + params → 两份 errors.json 加文案 →
这张表里加一行。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "src" / "tavotto" / "app.py"
LOCALES = ROOT / "web" / "src" / "i18n" / "locales"

# code → 后端会塞进 params 的键
USER_VISIBLE_CODES = {
    "no_project": set(),
    "missing_path": set(),
    "mkdir_failed": {"reason"},
    "open_project_failed": {"reason"},
    "invalid_path": set(),
    "dir_missing": {"path"},
    "permission_denied": {"path"},
    "read_failed": {"reason"},
}

pytestmark = pytest.mark.skipif(
    not (LOCALES / "zh-CN" / "errors.json").is_file(),
    reason="没有 web/（wheel/sdist 里不含前端源码）",
)


def _errors(locale: str) -> dict:
    return json.loads((LOCALES / locale / "errors.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("code", sorted(USER_VISIBLE_CODES))
def test_backend_emits_the_code(code: str):
    src = APP.read_text(encoding="utf-8")
    assert f'"code": "{code}"' in src, f"app.py 里没有发出 {code}"


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_every_code_has_text_in_both_languages(locale: str):
    table = _errors(locale).get("backend", {})
    missing = sorted(set(USER_VISIBLE_CODES) - set(table))
    assert not missing, f"{locale} 的 errors.json 缺这些 code 的文案：{missing}"
    empty = sorted(k for k, v in table.items() if not str(v).strip())
    assert not empty, f"{locale} 里这些 code 的文案是空的：{empty}"


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_placeholders_match_the_params_the_backend_sends(locale: str):
    table = _errors(locale)["backend"]
    for code, params in USER_VISIBLE_CODES.items():
        used = set(re.findall(r"\{\{\s*(\w+)\s*\}\}", table[code]))
        assert used == params, f"{locale} {code}：文案用了 {used}，后端给的是 {params}"


def test_english_texts_have_no_chinese():
    bad = {k: v for k, v in _errors("en-US")["backend"].items() if re.search(r"[一-鿿]", v)}
    assert not bad, f"英文文案里还留着中文：{bad}"


def test_error_field_is_still_there_as_the_fallback():
    """
    `error` 里的中文原文是回退，不能因为有了 code 就删掉：装着旧前端的用户、
    以及 curl 调试的人只看得到它。
    """
    src = APP.read_text(encoding="utf-8")
    for code in USER_VISIBLE_CODES:
        idx = src.index(f'"code": "{code}"')
        # code 与 error 在同一个 jsonify 里：往前找最近的 jsonify( 起点
        start = src.rindex("jsonify({", 0, idx)
        assert '"error"' in src[start:idx], f"{code} 所在的响应里没有 error 原文"
