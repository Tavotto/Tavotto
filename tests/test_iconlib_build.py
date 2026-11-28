"""开源图标库离线构建脚本（scripts/iconlib，ADR 0222）：许可过滤、署名句、预检、元数据、索引、取得通道（全离线）。"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ICONLIB_DIR = Path(__file__).resolve().parents[1] / "scripts" / "iconlib"
sys.path.insert(0, str(ICONLIB_DIR))

import build_iconlib  # noqa: E402
import iconlib_attribution as attr  # noqa: E402
import iconlib_index as index  # noqa: E402
import iconlib_license as lic  # noqa: E402
import iconlib_normalize as norm  # noqa: E402
import iconlib_sources as src  # noqa: E402

GOOD_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20">'
    b'<path d="M0 0 L10 0 L10 10 L0 10 Z" fill="#336699"/>'
    b'<path d="M10 10 L20 10 L20 20 L10 20 Z" fill="#99ccff"/>'
    b'<path d="M2 12 L8 12 L8 18 L2 18 Z" fill="#336699"/></svg>'
)


# ---- 许可过滤 ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("cc-0", "CC0-1.0"),
        ("CC0", "CC0-1.0"),
        ("cc-by-3.0", "CC-BY-3.0"),
        ("cc-by-4.0", "CC-BY-4.0"),
        ("CC BY 4.0", "CC-BY-4.0"),
        ("Public Domain", "PD"),
        ("cc-by-sa-4.0", None),
        ("cc-by-sa-3.0", None),
        ("CC-BY-NC-4.0", None),
        ("cc-by-nd-4.0", None),
        ("mit", None),
        ("bsd", None),
        ("cc-by", None),  # 没写版本不认
        ("", None),
        (None, None),
        (42, None),
    ],
)
def test_normalize_license(raw, want):
    assert lic.normalize_license(raw) == want


def test_filter_drops_unknown_and_audits_reason():
    recs = [
        {"id": "a", "source": "S", "license_raw": "cc-0"},
        {"id": "b", "source": "S", "license_raw": "cc-by-sa-4.0"},
        {"id": "c", "source": "S", "license_raw": "mit"},
        {"id": "d", "source": "S", "license_raw": "weird"},
        {"id": "e", "source": "S"},
    ]
    kept, audit = lic.filter_records(recs)
    assert [r["id"] for r in kept] == ["a"]
    assert kept[0]["license_id"] == "CC0-1.0"
    assert "license_id" not in recs[0]  # 不改入参
    assert {a["id"]: a["reason"] for a in audit} == {
        "b": "license_restricted",
        "c": "license_software",
        "d": "license_unknown",
        "e": "license_missing",
    }


# ---- 署名句 ------------------------------------------------------------------


def _rec(**kw):
    base = {
        "source": "Servier Medical Art",
        "creator": "Servier",
        "title": "Sun",
        "license_id": "CC-BY-3.0",
    }
    return {**base, **kw}


def test_cc_by_3_has_titles_and_modified():
    s = attr.attribution_line([_rec(title="Sun"), _rec(title="Egg")])
    assert s == (
        "Icons: “Egg”, “Sun” by Servier, Servier Medical Art, CC BY 3.0 "
        "(https://creativecommons.org/licenses/by/3.0/), modified"
    )


def test_cc_by_4_marks_modified_only_when_modified():
    mod = attr.attribution_line([_rec(license_id="CC-BY-4.0", modified=True)])
    unmod = attr.attribution_line([_rec(license_id="CC-BY-4.0", modified=False)])
    assert mod.endswith(", modified")
    assert "modified" not in unmod
    assert "“" not in mod  # 4.0 不要求标题
    assert "CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)" in unmod


def test_same_source_license_creator_merge_into_one_entry():
    s = attr.attribution_line(
        [_rec(license_id="CC-BY-4.0"), _rec(license_id="CC-BY-4.0", title="Other")]
    )
    assert s.count("CC BY 4.0") == 1


def test_different_license_or_creator_stay_separate():
    s = attr.attribution_line(
        [
            _rec(license_id="CC-BY-4.0"),
            _rec(license_id="CC-BY-4.0", creator="Someone"),
            _rec(license_id="CC-BY-3.0"),
        ]
    )
    assert s.count("; ") == 2


def test_public_domain_produces_no_sentence_unless_asked():
    recs = [_rec(license_id="CC0-1.0"), _rec(license_id="PD")]
    assert attr.attribution_line(recs) == ""
    assert "CC0 1.0" in attr.attribution_line(recs, include_public_domain=True)


def test_mixed_cc0_and_ccby_only_credits_ccby():
    s = attr.attribution_line(
        [_rec(license_id="CC0-1.0", creator="Z"), _rec(license_id="CC-BY-4.0")]
    )
    assert "Z" not in s and "CC BY 4.0" in s


def test_unknown_license_is_skipped_not_invented():
    assert attr.attribution_line([_rec(license_id="CC-BY-SA-4.0")]) == ""


def test_zh_words_and_order_is_stable():
    a = attr.attribution_line(
        [_rec(license_id="CC-BY-4.0", creator="B"), _rec(license_id="CC-BY-4.0", creator="A")],
        lang="zh",
    )
    b = attr.attribution_line(
        [_rec(license_id="CC-BY-4.0", creator="A"), _rec(license_id="CC-BY-4.0", creator="B")],
        lang="zh",
    )
    assert a == b and a.startswith("图标：") and "已修改" in a


def test_creator_same_as_source_not_repeated():
    s = attr.attribution_line([_rec(creator="Reactome", source="Reactome", license_id="CC-BY-4.0")])
    assert s.count("Reactome") == 1


# ---- 预检 / 规范化 ------------------------------------------------------------


def test_precheck_accepts_plain_svg_with_plan():
    r = norm.precheck(GOOD_SVG)
    assert r["ok"] and r["paths"] == 3
    assert [s["role"] for s in r["slots"]][0] == "main"
    assert {s["color"] for s in r["slots"]} == {"#336699", "#99ccff"}
    assert r["groups"] and r["quality"]["verdict"] in {"clean", "acceptable", "fail"}
    assert set(r["quality"]) >= {"verdict", "paths", "by_verdict", "fail_fraction", "issue_codes"}


def test_precheck_rejects_bitmap_before_import():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/png;base64,AAAA" width="5" height="5"/></svg>'
    r = norm.precheck(svg)
    assert not r["ok"] and r["reasons"] == ["bitmap"]


def test_precheck_rejects_filter_with_svgimport_code():
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><defs><filter id="f">'
        b'<feGaussianBlur stdDeviation="1"/></filter></defs>'
        b'<path d="M0 0 L5 0 L5 5 Z" fill="#000" filter="url(#f)"/></svg>'
    )
    r = norm.precheck(svg)
    assert not r["ok"] and any(x.startswith("unsupported:") for x in r["reasons"])


def test_precheck_size_and_path_gates():
    assert norm.precheck(GOOD_SVG, max_bytes=10)["reasons"] == ["too_big"]
    assert "too_many_paths" in norm.precheck(GOOD_SVG, max_paths=2)["reasons"]


def test_precheck_garbage_does_not_raise():
    for junk in (b"", b"\xff\xfe\x00", b"<svg", b"<!DOCTYPE svg [<!ENTITY a 'b'>]><svg/>"):
        r = norm.precheck(junk)
        assert not r["ok"] and r["reasons"]


def test_group_plan_keeps_existing_groups():
    objs = [{"id": "s1", "fill": "#112233", "groupId": "sg1"}]
    assert norm.group_plan(objs, norm.color_slots(objs))[0]["kept"] is True


# ---- 取得通道（离线） ----------------------------------------------------------


def test_stride_pick_spreads_and_is_deterministic():
    items = list(range(100))
    got = src.stride_pick(items, 4)
    assert got == [0, 25, 50, 75] and src.stride_pick(items, 4) == got
    assert src.stride_pick(items, 500) == items and src.stride_pick([], 3) == []


def test_fetcher_rejects_hosts_outside_allowlist(tmp_path):
    f = src.Fetcher(tmp_path, delay=0)
    with pytest.raises(ValueError):
        f.get("https://example.com/x.svg")
    with pytest.raises(ValueError):
        f.get("http://reactome.org/icon/x.svg")


def test_fetcher_cache_hit_and_cached_404_make_no_network(tmp_path):
    f = src.Fetcher(tmp_path, delay=0)
    url = "https://reactome.org/icon/R-ICO-000001.svg"
    body, meta = f._paths(url)
    body.write_bytes(b"<svg/>")
    meta.write_text(json.dumps({"url": url, "status": 200, "sha256": "x", "retrieved_at": "t"}))
    assert f.get(url)[0] == b"<svg/>"
    url404 = "https://reactome.org/icon/R-ICO-000002.svg"
    _, m404 = f._paths(url404)
    m404.write_text(json.dumps({"url": url404, "status": 404}))
    assert f.get(url404) is None
    assert f.network_calls == 0


class FakeFetcher:
    def __init__(self, files: dict[str, bytes]):
        self.files, self.network_calls = files, 0

    def get(self, url):
        if url not in self.files:
            return None
        b = self.files[url]
        return b, {
            "url": url,
            "sha256": hashlib.sha256(b).hexdigest(),
            "retrieved_at": "2026-11-28T00:00:00Z",
        }


def _xml(name, cat, designer="D"):
    return (
        f"<metadata><categories><category>{cat}</category></categories>"
        f'<person role="designer" url="https://d.example">{designer}</person>'
        f"<name>{name}</name><description>desc</description>"
        "<synonyms><synonym>syn</synonym></synonyms></metadata>"
    ).encode()


def test_reactome_candidates_skip_protein_and_missing():
    base = src.REACTOME_ICON
    ff = FakeFetcher(
        {
            base + "R-ICO-000001.xml": _xml("Mito", "cell_element"),
            base + "R-ICO-000002.xml": _xml("P53", "protein"),
            base + "R-ICO-000004.xml": _xml("Rec", "receptor"),
        }
    )
    cands, stats = src.reactome_candidates(ff, 1, 10, want=5)
    assert [c["title"] for c in cands] == ["Mito", "Rec"]
    assert stats["protein_skipped"] == 1 and stats["missing"] == 6
    assert cands[0]["license_raw"] == "CC-BY-4.0" and cands[0]["creator"] == "D"
    assert cands[0]["synonyms"] == ["syn"]


def test_reactome_xml_with_doctype_is_rejected():
    assert src.parse_reactome_xml(b'<!DOCTYPE x [<!ENTITY a "b">]><metadata/>') is None
    assert src.parse_reactome_xml(b"not xml") is None


def test_bioicons_candidates_map_origin_and_license():
    idx = [
        {"name": "sun", "category": "General_items", "license": "cc-by-3.0", "author": "Servier"},
        {"name": "x_y", "category": "Animals", "license": "cc-by-sa-4.0", "author": "Foo"},
        {"name": "bad"},
    ]
    ff = FakeFetcher(
        {
            src.BIOICONS_RAW + "icons.json": json.dumps(idx).encode(),
            src.BIOICONS_RAW + "authors.json": json.dumps(
                {"Servier": "https://smart.servier.com/"}
            ).encode(),
        }
    )
    cands = src.bioicons_candidates(ff)
    assert len(cands) == 2
    assert cands[0]["source"] == "Servier Medical Art" and cands[0]["via"] == "Bioicons"
    assert cands[0]["creator_url"] == "https://smart.servier.com/"
    assert cands[0]["url"].endswith("/cc-by-3.0/General_items/Servier/sun.svg")
    kept, audit = lic.filter_records(cands)
    assert [k["title"] for k in kept] == ["sun"] and audit[0]["reason"] == "license_restricted"


# ---- 元数据与索引 --------------------------------------------------------------

MANIFEST_FIELDS = {
    "id", "title", "category", "source", "via", "source_url", "file_url", "creator", "creator_url",
    "license_id", "license_name", "license_url", "copyright_notice", "modified", "retrieved_at",
    "sha256", "paths", "bytes", "size_mm", "slots", "groups", "quality",
}  # fmt: skip


def test_manifest_entry_has_all_provenance_fields_and_feeds_attribution():
    rec = {
        "id": "x-1", "source": "Servier Medical Art", "via": "Bioicons", "title": "Sun",
        "category": "General_items", "creator": "Servier", "license_id": "CC-BY-3.0",
        "source_url": "https://bioicons.com/",
    }  # fmt: skip
    info = {
        "url": "https://raw.githubusercontent.com/x.svg",
        "sha256": "ab" * 32,
        "retrieved_at": "2026-11-28T00:00:00Z",
    }
    pre = norm.precheck(GOOD_SVG)
    m = build_iconlib.make_manifest_entry(rec, info, pre)
    assert MANIFEST_FIELDS <= set(m)
    assert m["modified"] is True and m["license_name"] == "CC BY 3.0"
    assert m["license_url"] == "https://creativecommons.org/licenses/by/3.0/"
    assert "“Sun”" in attr.attribution_line([m])


def test_process_records_audit_for_failed_fetch_and_precheck():
    audit: list = []
    rec = {"id": "r1", "source": "S", "url": "https://reactome.org/icon/a.svg"}
    assert (
        build_iconlib.process(rec, FakeFetcher({}), max_bytes=10_000, max_paths=10, audit=audit)
        is None
    )
    ff = FakeFetcher(
        {
            rec[
                "url"
            ]: b'<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/png;base64,AA"/></svg>'
        }
    )
    assert build_iconlib.process(rec, ff, max_bytes=10_000, max_paths=10, audit=audit) is None
    assert [(a["stage"], a["reason"]) for a in audit] == [
        ("fetch", "fetch_failed"),
        ("precheck", "bitmap"),
    ]
    ok = build_iconlib.process(
        rec, FakeFetcher({rec["url"]: GOOD_SVG}), max_bytes=10_000, max_paths=10, audit=audit
    )
    assert ok is not None and ok[2]["ok"]


def test_index_structure_and_keywords():
    m = {
        "id": "b-2", "title": "Mitochondrion outline", "category": "Intracellular_components",
        "source": "Bioicons", "license_id": "CC0-1.0", "description": "", "synonyms": ["powerhouse"],
        "paths": 3, "bytes": 100, "size_mm": [1, 2], "quality": {"verdict": "clean"},
    }  # fmt: skip
    e = index.build_entry(m, m)
    assert "mitochondrion" in e["keywords_en"] and "powerhouse" in e["keywords_en"]
    assert "线粒体" in e["keywords_zh"] and "胞内结构" in e["keywords_zh"]
    assert e["quality"] == "clean" and "sha256" not in e  # 索引不带出处细节
    idx = index.build_index([{**e, "id": "z"}, {**e, "id": "a"}], "2026-11-28T00:00:00Z")
    assert (
        idx["version"] == 1 and idx["count"] == 2 and [i["id"] for i in idx["icons"]] == ["a", "z"]
    )


def test_main_refuses_cache_or_out_inside_repo(tmp_path, capsys):
    repo = Path(__file__).resolve().parents[1]
    rc = build_iconlib.main(["--cache", str(repo / ".tmp-iconlib"), "--out", str(tmp_path)])
    assert rc == 2 and "仓库" in capsys.readouterr().err
