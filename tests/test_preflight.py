"""出版规范 profile 与预检的看护。

盯的是三件「坏了也不报错，只是悄悄放行」的事：

1. **规范文件是唯一权威**——Python 与 TypeScript 读的必须是同一个文件；
2. **等级不许静默降级**——新加的检查项忘了在 severity 表里登记，用户会以为它过了；
3. **两个求值器不许分叉**——golden 向量在 pytest 与 vitest 各跑一遍
   （`web/src/lib/preflight.golden.test.ts`），任一侧改了都得让两边同时绿。
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import preflight, profiles

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "golden" / "preflight_vectors.json"


# --------------------------- profile 的加载与校验 ----------------------------
def test_canonical_file_lives_in_the_package():
    """规范文件必须在包里——装成 wheel 之后源码树的相对路径不存在。"""
    path = profiles.profiles_path()
    assert path.is_file()
    assert path.name == profiles.PROFILE_FILE
    assert path.parent.name == "profiles"
    assert path.parent.parent.name == "tavotto"


def test_typescript_reads_the_same_file():
    """TS 侧的 `@profiles` 别名必须指向同一份 JSON，不能各存一份。"""
    for cfg in ("vite.config.ts", "vitest.config.ts"):
        text = (ROOT / "web" / cfg).read_text(encoding="utf-8")
        assert "'@profiles'" in text, f"{cfg} 没配 @profiles 别名"
        assert "../src/tavotto/profiles/publication.json" in text, (
            f"{cfg} 的 @profiles 指到了别的文件——两侧规则一分叉，"
            "同一张图会得到两个互相矛盾的体检结论"
        )


def test_default_profile_exists_and_validates():
    pid = profiles.default_profile_id()
    profile = profiles.load(pid)
    assert profile["profile_id"] == pid
    for key in profiles._REQUIRED:
        assert key in profile


def test_lab_profile_matches_the_agreed_numbers():
    """课题组规范的硬数字：改这些等于改验收口径，必须是有意识的。"""
    p = profiles.load("lab-publication-v1")
    assert p["widths_mm"]["single"] == 80.0
    assert p["widths_mm"]["double"] == 150.0
    assert p["default_font_size_pt"] == 9.0
    # 最小字号**只有一个数**（ADR 0029）。曾经是 8.5（严格）+ 8.0（绝对），
    # 而规范原文里本来就有 8 pt 的图例/刻度——比被守护的规范更严的那条删了。
    assert p["min_effective_font_size_pt"] == 8.0
    assert p["absolute_min_font_size_pt"] == 8.0
    assert p["legend_policy"]["min_font_size_pt"] == 8.0
    assert p["min_raster_dpi"] == 300
    assert p["line_widths_pt"] == [0.5, 0.75, 1.0, 1.5]
    assert p["axis_policy"]["tick_direction"] == "in"
    assert p["axis_policy"]["enclosed_spines"] is True
    assert p["legend_policy"]["frame"] is False
    assert p["preferred_formats"]["vector"] == ["pdf", "svg"]
    assert {r["id"] for r in p["allowed_aspect_ratios"]} == {"16:9", "4:3", "1:1"}
    # PDF 里把 Times New Roman 拼成了 "Times New Roma"；规范里只许有正确拼写
    assert p["font_family"]["latin"] == "Times New Roman"
    assert "Times New Roma" not in json.dumps(p, ensure_ascii=False).replace("Times New Roman", "")
    assert p["cjk_fallback"]["required"] is True and p["cjk_fallback"]["accepted"]
    assert p["palette_policy"]["auto_recolor"] is False, "绝不能默认替用户的图重新配色"


def test_unknown_profile_is_an_error_not_a_silent_default():
    with pytest.raises(profiles.ProfileError):
        profiles.load("no-such-profile")


def test_journal_override_is_a_shallow_merge_that_keeps_identity():
    p = profiles.load("lab-publication-v1", {"widths_mm": {"double": 178.0}})
    assert p["widths_mm"]["double"] == 178.0
    assert p["widths_mm"]["single"] == 80.0  # 没点名的键继承
    assert p["profile_id"] == "lab-publication-v1"  # 覆盖不换身份
    assert p["derived_from"] == "lab-publication-v1"
    assert p["journal"] == {"widths_mm": {"double": 178.0}}
    assert profiles.stamp(p)["journal"] == {"widths_mm": {"double": 178.0}}


def test_bad_journal_override_is_rejected():
    with pytest.raises(profiles.ProfileError):
        profiles.load("lab-publication-v1", {"widths_mm": {"single": -1}})
    with pytest.raises(profiles.ProfileError):
        profiles.load("lab-publication-v1", {"severity": {"page-width": "fatal"}})


def test_env_override_points_at_another_file(tmp_path, monkeypatch):
    """企业/期刊自带一套规范时的扩展点。"""
    custom = tmp_path / "custom.json"
    doc = json.loads(profiles.profiles_path().read_text(encoding="utf-8"))
    doc["profiles"]["lab-publication-v1"]["widths_mm"]["double"] = 190.0
    custom.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv(profiles.PROFILE_ENV, str(custom))
    assert profiles.load("lab-publication-v1")["widths_mm"]["double"] == 190.0


def test_every_check_id_has_a_registered_severity():
    """检查项忘了登记等级 = 用户以为它通过了。兜底是 warn，但不许靠兜底过日子。"""
    ids = _all_check_ids()
    p = profiles.load("lab-publication-v1")
    missing = sorted(i for i in ids if i not in p["severity"])
    assert not missing, f"这些检查项没在 lab-publication-v1 的 severity 表里登记: {missing}"


def _all_check_ids() -> set[str]:
    """从 golden 向量 + 源码里收集全部检查 id。"""
    ids = set()
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for case in data["cases"]:
        ids |= {i["id"] for i in case["expected"]}
    src = (ROOT / "src" / "tavotto" / "engine" / "preflight.py").read_text(encoding="utf-8")
    import re

    ids |= set(re.findall(r'sink\.add\(\s*"([a-z0-9-]+)"', src))
    return ids


def test_default_severity_is_not_silently_permissive():
    p = profiles.load("lab-publication-v1")
    assert profiles.severity_of(p, "brand-new-check-nobody-registered") == "warn"
    assert profiles.DEFAULT_SEVERITY != "suggestion"


# ------------------------------- 预检本体 -----------------------------------
def _manifest(**fields) -> dict:
    """一张最小的合规图（80×60、9pt、Times、封闭轴、刻度朝内）。"""

    def el(gid, role, **props):
        return {
            "gid": gid,
            "role": role,
            "label": gid,
            "draggable": False,
            "bbox": [0.1, 0.1, 0.2, 0.2],
            "editable": [{"prop": k, "value": v} for k, v in props.items()],
        }

    elements = [
        el(
            "axes_0",
            "axes",
            spine_top=True,
            spine_right=True,
            spine_bottom=True,
            spine_left=True,
            spine_linewidth=0.75,
        ),
        el("axes_0.xticks", "ticks", direction="in", fontsize=fields.get("tick_pt", 9.0)),
        el(
            "axes_0.xlabel",
            "axis_label",
            text="Temperature (K)",
            fontsize=9.0,
            fontfamily=fields.get("family", "Times New Roman"),
        ),
    ]
    return {"stem": "Fig1", "size_mm": [80.0, 60.0], "elements": elements}


def _ids(issues) -> list[str]:
    return [i["id"] for i in issues]


def test_clean_figure_reports_nothing():
    p = profiles.load("lab-publication-v1")
    spec = preflight.spec_from_manifest(_manifest())
    assert preflight.run(spec, p) == []


def test_width_check_uses_the_profile_not_a_hardcoded_number():
    p = profiles.load("lab-publication-v1")
    spec = preflight.spec_from_manifest(_manifest())
    spec["page"]["w_mm"] = 85.0  # 老代码里写死的那个数
    assert "page-width" in _ids(preflight.run(spec, p))
    # 期刊覆盖之后同一张图必须放行
    p85 = profiles.load("lab-publication-v1", {"widths_mm": {"single": 85.0}})
    assert "page-width" not in _ids(preflight.run(spec, p85))


def test_aspect_ratio_check():
    p = profiles.load("lab-publication-v1")
    spec = preflight.spec_from_manifest(_manifest())
    spec["page"] = {"w_mm": 150.0, "h_mm": 40.0, "margin_mm": 0.0}
    assert "page-aspect" in _ids(preflight.run(spec, p))
    spec["page"] = {"w_mm": 150.0, "h_mm": 84.375, "margin_mm": 0.0}  # 16:9
    assert "page-aspect" not in _ids(preflight.run(spec, p))


def test_effective_font_size_follows_the_final_physical_size():
    """**缩放后的字号才是判据**：原始 9pt 的图缩到 80% 就只剩 7.2pt。"""
    p = profiles.load("lab-publication-v1")
    spec = preflight.spec_from_manifest(_manifest())
    assert "font-too-small" not in _ids(preflight.run(spec, p))
    shrunk = preflight.spec_from_manifest(_manifest(), scale=0.8)
    issues = {i["id"]: i for i in preflight.run(shrunk, p)}
    assert "font-below-absolute-floor" in issues
    assert issues["font-below-absolute-floor"]["detail"]["effective_pt"] == 7.2


def test_the_default_spec_has_exactly_one_minimum_font_size():
    """统一为 8 pt（ADR 0029）。

    **8 pt 那条边一个字都没动**：正好 8.0 仍然不算过（`eff <= floor`）。变的是
    「比 8 pt 更严的那条规则」——8.2 pt 从阻断项变成了通过。
    """
    p = profiles.load("lab-publication-v1")
    at_8_2 = _ids(preflight.run(preflight.spec_from_manifest(_manifest(tick_pt=8.2)), p))
    assert "font-too-small" not in at_8_2
    assert "font-below-absolute-floor" not in at_8_2
    at_8_0 = _ids(preflight.run(preflight.spec_from_manifest(_manifest(tick_pt=8.0)), p))
    assert "font-below-absolute-floor" in at_8_0
    # 两档同值时只报下面那条，不报两条：同一件事说两遍等于让用户找两次
    assert "font-too-small" not in at_8_0


def test_the_strict_threshold_and_the_absolute_floor_are_still_two_checks():
    """统一成一个数是**默认规范的取值**，不是把其中一条检查删掉了。

    规范只要把两档设成不同的值（`free-form-v1` 是 6.0 / 5.0），两条判据就
    各自出场——期刊覆盖也走同一条路。
    """
    p = profiles.load("free-form-v1")
    assert p["min_effective_font_size_pt"] != p["absolute_min_font_size_pt"]
    at_5_5 = _ids(preflight.run(preflight.spec_from_manifest(_manifest(tick_pt=5.5)), p))
    assert "font-too-small" in at_5_5
    assert "font-below-absolute-floor" not in at_5_5
    at_5_0 = _ids(preflight.run(preflight.spec_from_manifest(_manifest(tick_pt=5.0)), p))
    assert "font-below-absolute-floor" in at_5_0


def test_font_substitution_is_reported():
    p = profiles.load("lab-publication-v1")
    spec = preflight.spec_from_manifest(_manifest(family="DejaVu Serif"))
    issues = {i["id"]: i for i in preflight.run(spec, p)}
    assert "font-family-substituted" in issues
    assert issues["font-family-substituted"]["detail"]["family"] == "DejaVu Serif"


def test_cjk_without_fallback_is_reported():
    p = profiles.load("lab-publication-v1")
    man = _manifest(family="DejaVu Sans")
    man["elements"][2]["editable"][0]["value"] = "温度 (K)"
    spec = preflight.spec_from_manifest(man)
    assert "cjk-fallback-missing" in _ids(preflight.run(spec, p))


def test_raster_inner_text_is_not_verifiable_never_silently_passing():
    """位图里的文字字号查不了。如实报 not_verifiable，绝不假装通过。"""
    p = profiles.load("lab-publication-v1")
    spec = preflight.spec_from_manifest(_manifest())
    spec["panels"][0].update(kind="raster", manifest=None, px_w=600)
    issues = {i["id"]: i for i in preflight.run(spec, p)}
    assert issues["raster-text-not-verifiable"]["severity"] == "not_verifiable"
    assert issues["raster-dpi"]["severity"] == "error"


def test_vector_panel_without_manifest_is_not_verifiable():
    p = profiles.load("lab-publication-v1")
    spec = preflight.spec_from_manifest(_manifest())
    spec["panels"][0]["manifest"] = None
    issues = {i["id"]: i for i in preflight.run(spec, p)}
    assert issues["panel-text-not-verifiable"]["severity"] == "not_verifiable"


def test_data_semantics_are_only_suggestions():
    """柱状图误差棒、拟合置信带这类判断**绝不替用户裁决**。"""
    p = profiles.load("lab-publication-v1")
    man = _manifest()
    man["elements"].append(
        {
            "gid": "axes_0.barseries_0",
            "role": "bar_series",
            "label": "",
            "draggable": False,
            "bbox": [0, 0, 1, 1],
            "editable": [{"prop": "linewidth", "value": 0.75}],
        }
    )
    spec = preflight.spec_from_manifest(man)
    issues = {i["id"]: i for i in preflight.run(spec, p)}
    assert issues["bar-without-errorbar"]["severity"] == "suggestion"


def test_summarize_blocks_only_on_errors():
    p = profiles.load("lab-publication-v1")
    # 8.0 而不是 8.2：统一为 8 pt 之后 8.2 是合规的，用它当"有阻断项"的样例
    # 会让这条用例在实现坏掉时照样绿（判据挑了个不会触发的输入）
    spec = preflight.spec_from_manifest(_manifest(tick_pt=8.0))
    summary = preflight.summarize(preflight.run(spec, p))
    assert summary["blocking"] is True
    assert summary["counts"]["error"] >= 1
    clean = preflight.summarize(preflight.run(preflight.spec_from_manifest(_manifest()), p))
    assert clean["blocking"] is False
    assert clean["counts"] == {"error": 0, "warn": 0, "not_verifiable": 0, "suggestion": 0}


# ----------------------------- golden 向量 ----------------------------------
def test_golden_vectors_match_this_implementation():
    """向量文件与本实现一致（vitest 断言 TS 侧也一致，两边同一份输入）。"""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_preflight_vectors.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_golden_vectors_are_asserted_on_the_typescript_side_too():
    """光有 Python 一侧等于没看护——分叉正是从「只改了一边」开始的。"""
    ts = ROOT / "web" / "src" / "lib" / "preflight.golden.test.ts"
    assert ts.is_file()
    text = ts.read_text(encoding="utf-8")
    assert "tests/golden/preflight_vectors.json" in text


def test_golden_vector_file_covers_the_severity_ladder():
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    seen = {i["severity"] for c in data["cases"] for i in c["expected"]}
    assert seen == {"error", "warn", "not_verifiable", "suggestion"}


# --------------------- 可翻译描述符（issue #30） -----------------------------
def test_every_issue_carries_a_translatable_message():
    """每条 issue 必须带 message = {key, params}——MCP 画布按 locale 渲染靠它。

    `_Sink.add` 的 message 是必填参数，这里再从数据层守一遍：向量覆盖的每条
    issue 都真的带着形状合法的描述符（漏写的调用点在 collection 阶段就会
    TypeError，这条防的是将来有人把参数改回可选）。
    """
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    seen = 0
    for case in data["cases"]:
        for issue in case["expected"]:
            msg = issue.get("message")
            assert (
                isinstance(msg, dict)
                and isinstance(msg.get("key"), str)
                and msg["key"]
                and isinstance(msg.get("params"), dict)
            ), issue["id"]
            seen += 1
    assert seen > 20


def test_every_message_key_is_registered_in_both_locales():
    """Python 发出的每个 message key 都必须在前端双语文案表里登记。

    渲染发生在 widget（errors:preflight.<key>）：Python 发明了新 key 而前端
    没登记时，widget 会静默回退 Python 的中文成文——英文界面重新漏出中文，
    正是 issue #30 要堵的洞。所以在这儿跨边界对拍，缺一边就红。
    """
    keys = set()
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for case in data["cases"]:
        for issue in case["expected"]:
            keys.add(issue["message"]["key"])
    # bridge 的导出 dpi 检查不经引擎求值器，key 单独登记
    keys.add("exportRasterDpi")
    for locale in ("zh-CN", "en-US"):
        table = json.loads(
            (ROOT / "web" / "src" / "i18n" / "locales" / locale / "errors.json").read_text(
                encoding="utf-8"
            )
        )["preflight"]
        missing = keys - set(table)
        assert not missing, f"{locale} 缺 preflight 文案 key：{sorted(missing)}"


# ----------------------- 导出上下文那条规则的跨语言同源 -----------------------
def test_the_export_context_rule_is_one_rule_on_both_sides():
    """「这次导出的 PPI 够不够」在两条入口上必须是**同一条规则**。

    MCP 那条在 `codex-plugin/mcp/tavotto_mcp/bridge.py::export_raster_issues()`，
    画布这条在 `web/src/lib/validation.ts::exportContextRaw()`（ADR 0030）。
    两侧必须共用同一个 **rule code**（`raster-dpi`）与同一个 **message key**
    （`exportRasterDpi`）：另起一个 code 的话，期刊覆盖里把 `raster-dpi` 调成
    warn 对其中一条路就不生效，同一份规范在两条入口上说不同的话。

    判据落在源码文本上（两侧语言不同，没有共用的运行时），所以**改名会当场红**
    ——那正是这条看护要拦的事。
    """
    py = (ROOT / "codex-plugin" / "mcp" / "tavotto_mcp" / "bridge.py").read_text(encoding="utf-8")
    ts = (ROOT / "web" / "src" / "lib" / "validation.ts").read_text(encoding="utf-8")
    assert "def export_raster_issues(" in py
    assert "export function exportContextRaw(" in ts
    for token, where in (
        ('"raster-dpi"', "Python 侧的 rule code"),
        ('"exportRasterDpi"', "Python 侧的 message key"),
        ('"min_raster_dpi"', "Python 侧读的规范键"),
    ):
        assert token in py, f"{where}变了"
    for token, where in (
        ("'raster-dpi'", "TS 侧的 rule code"),
        ("'preflight.exportRasterDpi'", "TS 侧的 message key"),
        ("min_raster_dpi", "TS 侧读的规范键"),
        ("preferred_formats.raster", "TS 侧读的格式清单"),
    ):
        assert token in ts, f"{where}变了"
