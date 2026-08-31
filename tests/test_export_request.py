"""统一 ExportRequest（ADR 0031）：规范化、文件名规则、覆盖策略。

文件名规则是**严格同源对**（`web/src/lib/exportName.ts`）。这里跑 Python 侧，
`web/src/lib/exportName.golden.test.ts` 跑 TS 侧，两边**同一份向量**。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import exportreq

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "golden" / "filename_vectors.json"


# ----------------------------- golden 向量 ----------------------------------
def test_golden_vectors_match_this_implementation():
    """向量文件与本实现一致（vitest 断言 TS 侧也一致，两边同一份输入）。"""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_filename_vectors.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_golden_vectors_are_asserted_on_the_typescript_side_too():
    """光有 Python 一侧等于没看护——分叉正是从「只改了一边」开始的。"""
    ts = ROOT / "web" / "src" / "lib" / "exportName.golden.test.ts"
    assert ts.is_file()
    assert "tests/golden/filename_vectors.json" in ts.read_text(encoding="utf-8")


def test_golden_vectors_cover_every_rule():
    """每一条判据都得被向量碰过。碰不到的规则可以两侧分叉而没人发现。"""
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    seen = {c["reason"] for c in data["check"] if c["reason"]}
    assert seen == {
        "empty",
        "whitespace_edge",
        "too_long",
        "control_char",
        "illegal_char",
        "trailing_dot",
        "dot_only",
        "reserved_name",
    }


def test_edge_whitespace_is_not_the_language_builtin():
    """判据不许退回 `str.strip()`。

    `str.strip()` 与 JS 的 `String.trim()` 认的字符集**不一样**：U+FEFF 只有
    JS 认，`\\x1c`–`\\x1f` 只有 Python 认。谁用了自己语言的内建函数，
    两侧就会对同一个名字给出不同答案——而那种分叉在单侧用例里永远看不见。
    """
    assert exportreq.check_filename("﻿Fig") == "whitespace_edge"
    assert exportreq.check_filename("Fig﻿") == "whitespace_edge"
    # `\x1c` 是 Python 的 strip() 认、JS 的 trim() 不认的那一类：两侧都必须
    # 把它判成控制字符（而不是一边"首尾空白"、一边"控制字符"）
    assert exportreq.check_filename("Fig\x1c") == "control_char"


# ------------------------------ 规范化 --------------------------------------
def _canvas_spec(**over):
    base = {
        "scope": "canvas",
        "filename": "Fig 1",
        "formats": ["pdf", "png"],
        "ppi": 600,
        "canvas": {"page_w_mm": 80, "page_h_mm": 60, "objects": []},
    }
    base.update(over)
    return base


def test_ppi_is_none_when_no_raster_format():
    """**「这次 PPI 没有意义」与「PPI 是 600」是两个不同的答案。**

    压成一个默认数字的话，界面就会去显示一个不影响任何东西的设置，
    而用户会以为改它有用（T-49 同一个形状）。
    """
    req = exportreq.normalize(_canvas_spec(formats=["pdf"]))
    assert req.ppi is None
    assert req.has_raster is False
    assert exportreq.normalize(_canvas_spec(formats=["png"])).ppi == 600


def test_format_order_is_stable_not_click_order():
    """结果里的 outputs[] 要与界面上的清单逐项对上，而点勾选框的先后不是身份。"""
    assert exportreq.normalize(_canvas_spec(formats=["png", "pdf"])).formats == ("pdf", "png")
    assert exportreq.normalize(_canvas_spec(formats=["pdf", "pdf"])).formats == ("pdf",)


@pytest.mark.parametrize(
    "spec, code",
    [
        ({"formats": []}, "no_format"),
        ({"formats": ["tiff"]}, "unsupported_format"),
        ({"ppi": "x"}, "bad_ppi"),
        ({"ppi": 5}, "ppi_out_of_range"),
        ({"ppi": 99999}, "ppi_out_of_range"),
        ({"filename": "Fig?1"}, "bad_filename"),
        ({"filename": ""}, "bad_filename"),
        ({"scope": "half"}, "bad_scope"),
        ({"overwrite": "maybe"}, "bad_overwrite"),
        ({"background": "plaid"}, "bad_background"),
    ],
)
def test_bad_requests_fail_before_touching_the_disk(spec, code):
    with pytest.raises(exportreq.ExportRequestError) as exc:
        exportreq.normalize(_canvas_spec(**spec))
    assert exc.value.code == code


def test_extension_typed_by_the_user_is_stripped_once():
    assert exportreq.normalize(_canvas_spec(filename="Fig 1.pdf")).filename == "Fig 1"
    # 一次只剥一层：剥到底的话 `data.tar.gz.png` 会变成 `data.tar`
    assert exportreq.normalize(_canvas_spec(filename="v1.2")).filename == "v1.2"


def test_original_scope_has_no_layout_fields_at_all():
    """`scope=original` 里**根本没有** x/y/w/h 与页面尺寸。

    不是"记得别用"——那几个键不在 `OriginalSource` 上。想让画布缩放漏进
    原图导出，得先改这个结构，而改结构会当场撞上这条用例。
    """
    req = exportreq.normalize(
        {
            "scope": "original",
            "filename": "Fig 1",
            "formats": ["pdf"],
            "original": {
                "figure_id": "Fig1.pdf",
                "w_mm": 80,
                "h_mm": 60,
                "ignored": ["scale", "crop"],
                # 混进来的布局字段被规范化层原样丢掉
                "x_mm": 10,
                "w": 40,
                "page_w_mm": 180,
            },
        }
    )
    assert req.canvas is None
    assert req.original is not None
    assert not hasattr(req.original, "x_mm")
    assert not hasattr(req.original, "page_w_mm")
    assert req.original.ignored == ("scale", "crop")


def test_legacy_payload_still_normalizes():
    """老标签页（`stem` + `items[]` + `texts[]`）抬成同一个作业。"""
    req = exportreq.normalize(
        {
            "page_w_mm": 100,
            "page_h_mm": 50,
            "dpi": 300,
            "formats": ["pdf"],
            "stem": "老 布局/名",
            "items": [{"id": "a.pdf", "x_mm": 0, "y_mm": 0, "w_mm": 10, "h_mm": 10}],
            "texts": [{"text": "hi", "x_mm": 0, "y_mm": 0, "w_mm": 10, "h_mm": 5, "size_pt": 9}],
        }
    )
    assert req.legacy_naming is True
    assert req.filename == "老_布局_名"  # 旧路径清洗，不当场报错
    assert req.scope == "canvas"
    assert [o["type"] for o in req.canvas.objects] == ["panel", "text"]
    # 旧路径带时间戳，天生撞不了车，所以默认是 replace 而不是 ask
    assert req.overwrite == "replace"


def test_new_payload_defaults_to_asking_before_overwriting():
    """**静默覆盖用户上一次的成果是不可逆的**，所以默认先问一句。"""
    assert exportreq.normalize(_canvas_spec()).overwrite == "ask"


def test_legacy_overwrite_boolean_is_still_understood():
    """`scripts/smoke_app.py` 至今发的是 `overwrite: true`。"""
    assert exportreq.normalize(_canvas_spec(overwrite=True)).overwrite == "replace"


def test_dedupe_numbering():
    taken = {"Fig 1.pdf", "Fig 1 (2).pdf"}
    assert exportreq.dedupe_name("Fig 1", "pdf", lambda n: n in taken) == "Fig 1 (3).pdf"
    assert exportreq.dedupe_name("Fig 1", "png", lambda n: n in taken) == "Fig 1.png"
