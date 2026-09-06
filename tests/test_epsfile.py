"""`engine/epsfile.py`：EPS 头的只读解析（ADR 0044）。"""

from __future__ import annotations

from tavotto.engine import epsfile

HEAD = b"%!PS-Adobe-3.0 EPSF-3.0\n%%Creator: test\n"


def test_hires_bounding_box_wins_over_the_integer_one(tmp_path):
    p = tmp_path / "a.eps"
    p.write_bytes(
        HEAD
        + b"%%BoundingBox: 0 0 216 144\n%%HiResBoundingBox: 0.000000 0.000000 215.433 143.622\n"
        + b"%%EndComments\n%%EOF\n"
    )
    assert epsfile.is_eps(p)
    w, h = epsfile.bounding_box_pt(p)
    assert (w, h) == (215.433, 143.622)


def test_integer_bounding_box_alone_is_enough(tmp_path):
    p = tmp_path / "a.eps"
    p.write_bytes(HEAD + b"%%BoundingBox: 10 20 210 170\n%%EOF\n")
    assert epsfile.bounding_box_pt(p) == (200.0, 150.0)


def test_atend_bounding_box_is_read_from_the_trailer(tmp_path):
    """`%%BoundingBox: (atend)` 是合法 DSC：真值在文件尾部。"""
    p = tmp_path / "a.eps"
    body = b"%% filler\n" * 2000  # 把 trailer 推出前 8 KiB 之外
    p.write_bytes(
        HEAD
        + b"%%BoundingBox: (atend)\n"
        + body
        + b"%%Trailer\n%%BoundingBox: 0 0 300 200\n%%EOF\n"
    )
    assert epsfile.bounding_box_pt(p) == (300.0, 200.0)


def test_not_an_eps_gives_none_not_a_guess(tmp_path):
    p = tmp_path / "a.eps"
    p.write_bytes(b"%PDF-1.7\n%%BoundingBox: 0 0 100 100\n")
    assert not epsfile.is_eps(p)
    assert epsfile.bounding_box_pt(p) is None
    assert epsfile.bounding_box_pt(tmp_path / "missing.eps") is None


def test_degenerate_box_is_none(tmp_path):
    p = tmp_path / "a.eps"
    p.write_bytes(HEAD + b"%%BoundingBox: 0 0 0 144\n%%EOF\n")
    assert epsfile.bounding_box_pt(p) is None
