"""候选环境「落选原因」的文案对：后端 `userenvs.REJECT_CODES` == 前端 `envRejectionText.ts` 的 `SENTENCE` 键（ADR 0079 修订）。

判据的主语：**两侧的稳定码闭集**（含顺序），以及中英两份 `prep.envNotUsed.*` 文案是否给每个码都配了一句。
后端新增一个落选码而前端没配句子，用户就只会看到「起不来」的兜底；前端多出来的码后端不会发。
同时钉住报告里的 `environment.not_used`：只带稳定码 / 标签 / 缺的包名 / ROOT 版本，不带路径与不透明 id（ADR 0053 §二）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from tavotto.engine import envadvice, userenvs

ROOT = Path(__file__).resolve().parent.parent
TS = ROOT / "web" / "src" / "lib" / "envRejectionText.ts"
LOCALES = ROOT / "web" / "src" / "i18n" / "locales"


def _ts_codes() -> list[str]:
    text = TS.read_text(encoding="utf-8")
    body = text.split("const SENTENCE", 1)[1].split("}", 1)[0]
    return re.findall(r"^\s*([a-z_]+):\s*'prep\.envNotUsed\.", body, re.M)


def test_frontend_sentence_table_mirrors_reject_codes():
    codes = _ts_codes()
    assert codes, "没解析出任何码：判据空转"
    assert codes == list(userenvs.REJECT_CODES)


def test_every_code_has_a_sentence_in_every_language():
    for lang in ("zh-CN", "en-US"):
        copy = json.loads((LOCALES / lang / "workspace.json").read_text(encoding="utf-8"))["prep"][
            "envNotUsed"
        ]
        for code in userenvs.REJECT_CODES:
            assert copy.get(code), (lang, code)
        # 两个带包名的码在没有名字时各有一句不凑名字的变体
        assert copy["missing_imports_unnamed"] and copy["import_failed_unnamed"]


def test_report_not_used_is_public_shape_without_paths_or_ids():
    rej = userenvs.rejection_reason(
        {
            "python": "C:/Users/me/AppData/Local/Programs/Python/Python314/python.exe",
            "source": "system",
            "label": "Python 3.14 + ROOT 6.38",
            "overlay": {"kind": "root", "root": "C:/root_v6.38", "version": "6.38.02"},
            "overlay_reject": userenvs.REJECT_ROOT_NEEDS_MSVC,
        }
    )
    fact = envadvice.adoption_fact("/p", "", adopted=None, invalidated=None, rejected=[rej])
    assert fact["not_used"] == [
        {
            "source": "system",
            "label": "Python 3.14 + ROOT 6.38",
            "reason": "root_needs_msvc",
            "missing": [],
            "import_failed": [],
            "root_version": "6.38.02",
        }
    ]
    blob = json.dumps(fact)
    assert "C:/" not in blob and "id" not in fact["not_used"][0]


def test_no_rejections_keeps_the_old_shape_and_the_list_is_bounded():
    assert "not_used" not in envadvice.adoption_fact("/p", "", adopted=None, invalidated=None)
    many = [{"source": "s", "label": f"L{i}", "reason": "unusable"} for i in range(40)]
    fact = envadvice.adoption_fact("/p", "", adopted=None, invalidated=None, rejected=many)
    assert len(fact["not_used"]) == envadvice.NOT_USED_LIMIT


def test_report_overlay_snapshot_has_no_path():
    spec = {"kind": "root", "root": "C:/root_v6.38", "version": "6.38.02"}
    fact = envadvice.adoption_fact("/p", "", adopted=None, invalidated=None, overlay=spec)
    assert fact["overlay"] == {"kind": "root", "version": "6.38.02", "label": "ROOT 6.38"}
    assert "C:/" not in json.dumps(fact)
    assert "overlay" not in envadvice.adoption_fact("/p", "", adopted=None, invalidated=None)
