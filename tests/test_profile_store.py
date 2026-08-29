"""全局 Style / Spec 清单（`engine/profilestore.py`，ADR 0029）。

这份用例盯着四件事，每一件都对应一个"错了用户不会知道"的失败形态：

1. **落盘在用户数据目录**，不在包目录——装成 wheel 之后 site-packages 不可写，
   写在那里的东西升级即失，而失的时候没有任何报错。
2. **乐观并发**：两个窗口同时改一条，后写的必须先看到对方写了什么。
   不挡的表现是"我改的东西不见了"，同样没有报错。
3. **损坏回退内置，且坏文件不删**——回退是为了应用起得来，不删是因为那是
   用户的东西，只是我们读不懂它。
4. **最小字号只有一个数（8 pt）**，而且那个数不在任何求值器/界面里硬编码。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import preflight, profiles, profilestore as store

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(data_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(m, "LAYOUT_DIR", tmp_path / "layouts")
    m.app.config["TESTING"] = True
    return m.app.test_client()


# --------------------------- 内置与用户自定义 --------------------------------
def test_builtin_specs_come_from_the_canonical_json(data_dir):
    """内置规范**不复制数字**：它就是 publication.json 里那几条。"""
    records = store.list_profiles(store.KIND_SPEC)
    assert [r["id"] for r in records] == [p["profile_id"] for p in profiles.list_profiles()]
    assert all(r["built_in"] and r["read_only"] for r in records)
    lab = next(r for r in records if r["id"] == "lab-publication-v1")
    assert lab["data"] == profiles.load("lab-publication-v1")


def test_builtin_style_is_derived_from_the_default_spec(data_dir, tmp_path, monkeypatch):
    """内置样式是**从规范派生的**，不是第二份数字。

    判据刻意**换一份规范来量**：两侧都取自同一份文件时，把派生换成写死的
    9.0 / "Times New Roman" 也照样绿——那种用例什么都没量到
    （同一个值填了两个出处 = 恒等成立）。这里给一份改过数字的规范，
    样式必须跟着变。
    """
    doc = json.loads(profiles.profiles_path().read_text(encoding="utf-8"))
    lab = doc["profiles"]["lab-publication-v1"]
    lab["default_font_size_pt"] = 11.5
    lab["font_family"]["latin"] = "Nimbus Roman"
    lab["line_widths_pt"] = [0.25, 2.0]
    lab["axis_policy"]["tick_direction"] = "out"
    other = tmp_path / "other-profiles.json"
    other.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv(profiles.PROFILE_ENV, str(other))

    spec = profiles.load()
    style = store.list_profiles(store.KIND_STYLE)[0]
    el = style["data"]["element"]
    assert el["text"]["fontsize"] == spec["default_font_size_pt"] == 11.5
    assert el["text"]["fontfamily"] == spec["font_family"]["latin"] == "Nimbus Roman"
    assert el["line"]["linewidth"] == spec["line_widths_pt"][0] == 0.25
    assert el["ticks"]["direction"] == spec["axis_policy"]["tick_direction"] == "out"
    assert style["read_only"] is True


def test_the_style_profile_has_no_ppi_of_its_own(data_dir):
    """PPI 归 Export 层；"规范推荐多少"已经在 Spec 里。样式里再放一份就是
    同一个数的第三个出处——正是本阶段要消掉的东西。"""
    style = store.list_profiles(store.KIND_STYLE)[0]
    assert "output_ppi" not in style["data"]
    assert "output_ppi" not in store._STYLE_KEYS


# ------------------------------ 增删改复制 -----------------------------------
def test_create_update_delete_and_duplicate(data_dir):
    rec = store.create_profile(store.KIND_STYLE, {"element": {"title": {"fontsize": 11}}}, "投稿用")
    assert rec["display_name"] == "投稿用" and rec["revision"] == 1 and not rec["built_in"]

    changed = store.update_profile(
        store.KIND_STYLE, rec["id"], {"data": {"element": {"title": {"fontsize": 12}}}}, 1
    )
    assert changed["revision"] == 2
    assert changed["data"]["element"]["title"]["fontsize"] == 12

    copy = store.duplicate_profile(store.KIND_STYLE, rec["id"])
    assert copy["id"] != rec["id"] and copy["derived_from"] == rec["id"]
    assert copy["display_name"] != changed["display_name"]  # 重名要能分辨
    assert copy["data"] == changed["data"]

    store.delete_profile(store.KIND_STYLE, rec["id"])
    assert store.get_profile(store.KIND_STYLE, rec["id"]) is None
    assert store.get_profile(store.KIND_STYLE, copy["id"]) is not None


def test_duplicate_names_get_a_suffix_instead_of_merging(data_dir):
    a = store.create_profile(store.KIND_STYLE, {"element": {}}, "投稿用")
    b = store.create_profile(store.KIND_STYLE, {"element": {}}, "投稿用")
    assert a["display_name"] == "投稿用"
    assert b["display_name"] == "投稿用 (2)"


def test_builtins_cannot_be_changed_or_deleted(data_dir):
    with pytest.raises(store.ProfileStoreError) as exc:
        store.update_profile(store.KIND_SPEC, "lab-publication-v1", {"data": {}}, 1)
    assert exc.value.code == "profile_read_only" and exc.value.status == 409
    with pytest.raises(store.ProfileStoreError) as exc:
        store.delete_profile(store.KIND_STYLE, store.BUILTIN_STYLE_ID)
    assert exc.value.code == "profile_read_only"
    # 但复制得出来——那正是"改内置"的正确出口
    assert store.duplicate_profile(store.KIND_SPEC, "lab-publication-v1")["built_in"] is False


def test_revision_conflict_does_not_silently_overwrite(data_dir):
    rec = store.create_profile(store.KIND_STYLE, {"element": {}}, "A")
    store.update_profile(store.KIND_STYLE, rec["id"], {"display_name": "B"}, rec["revision"])
    with pytest.raises(store.RevisionConflict) as exc:
        # 第二个窗口手里还是第 1 版
        store.update_profile(store.KIND_STYLE, rec["id"], {"display_name": "C"}, rec["revision"])
    assert exc.value.current["display_name"] == "B"
    assert store.get_profile(store.KIND_STYLE, rec["id"])["display_name"] == "B"


def test_reset_restores_the_builtin_it_was_copied_from(data_dir):
    copy = store.duplicate_profile(store.KIND_SPEC, "lab-publication-v1")
    store.update_profile(
        store.KIND_SPEC,
        copy["id"],
        {"data": {**copy["data"], "min_effective_font_size_pt": 20.0}},
        copy["revision"],
    )
    back = store.reset_profile(store.KIND_SPEC, copy["id"])
    # 内容回到内置那一份，**但身份仍然是自己的**——恢复默认值不该把这条
    # 配置变成第二个 `lab-publication-v1`（proof 里就分不清用的是哪一份了）
    assert back["data"] == {**profiles.load("lab-publication-v1"), "profile_id": copy["id"]}
    assert back["revision"] > copy["revision"]  # 恢复也是一次修改，不是回到过去


def test_user_specs_go_through_the_same_validation_as_builtins(data_dir):
    with pytest.raises(store.ProfileStoreError) as exc:
        store.create_profile(store.KIND_SPEC, {"profile_id": "x"}, "缺一堆字段")
    assert exc.value.code == "profile_bad_spec"


# ------------------------------ 落盘位置与原子写 -----------------------------
def test_the_store_lives_in_the_user_data_dir(data_dir):
    store.create_profile(store.KIND_STYLE, {"element": {}}, "A")
    path = store.store_path(store.KIND_STYLE)
    assert path == data_dir / "profiles" / "styles.json"
    assert path.is_file()
    # 包目录里一个字节都不该多出来
    pkg = Path(store.__file__).resolve().parent.parent
    assert not (pkg / "profiles" / "styles.json").exists()


def test_write_leaves_no_temp_file_behind(data_dir):
    store.create_profile(store.KIND_STYLE, {"element": {}}, "A")
    store.create_profile(store.KIND_STYLE, {"element": {}}, "B")
    assert not list((data_dir / "profiles").glob("*.tmp*"))
    assert json.loads(store.store_path(store.KIND_STYLE).read_text(encoding="utf-8"))["schema"] == 1


def test_damaged_store_falls_back_to_builtins_and_keeps_the_bad_file(data_dir):
    path = store.store_path(store.KIND_STYLE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json", encoding="utf-8")
    records = store.list_profiles(store.KIND_STYLE)
    assert [r["id"] for r in records] == [store.BUILTIN_STYLE_ID]
    assert not path.exists()  # 挪走了
    kept = list((data_dir / "profiles" / "backup").glob("styles-unparsable-*.json"))
    assert len(kept) == 1 and "not json" in kept[0].read_text(encoding="utf-8")


def test_a_newer_store_schema_is_left_completely_alone(data_dir):
    """比本构建新的清单：当作"没有用户 profile"，但**原样留着别动**。
    挪走它等于把用户在新版里建的东西藏起来。"""
    path = store.store_path(store.KIND_SPEC)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"schema": store.STORE_SCHEMA + 1, "profiles": [{"id": "x"}]})
    path.write_text(payload, encoding="utf-8")
    assert all(r["built_in"] for r in store.list_profiles(store.KIND_SPEC))
    assert path.read_text(encoding="utf-8") == payload


def test_one_broken_entry_does_not_take_the_whole_list_down(data_dir):
    good = store.create_profile(store.KIND_STYLE, {"element": {}}, "好的")
    path = store.store_path(store.KIND_STYLE)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["profiles"].insert(0, {"id": "坏的 id 有空格", "data": {}})
    doc["profiles"].append({"id": "no-data"})
    path.write_text(json.dumps(doc), encoding="utf-8")
    ids = [r["id"] for r in store.list_profiles(store.KIND_STYLE) if not r["built_in"]]
    assert ids == [good["id"]]


# ------------------------------ 导入 / 导出 ----------------------------------
def test_export_import_roundtrip_creates_a_new_profile(data_dir):
    rec = store.create_profile(store.KIND_STYLE, {"element": {"title": {"fontsize": 11}}}, "投稿用")
    payload = store.export_profile(store.KIND_STYLE, rec["id"])
    got = store.import_profile(json.dumps(payload))
    assert got["id"] != rec["id"], "导入绝不覆盖既有配置"
    assert got["data"] == rec["data"]
    assert got["display_name"] == "投稿用 (2)"


@pytest.mark.parametrize(
    "payload,code",
    [
        ("{ nope", "profile_bad_json"),
        (json.dumps({"format": "other", "schema": 1, "kind": "style"}), "profile_bad_format"),
        (
            json.dumps({"format": store.EXPORT_FORMAT, "schema": 99, "kind": "style"}),
            "profile_bad_schema",
        ),
        (
            json.dumps({"format": store.EXPORT_FORMAT, "schema": 1, "kind": "nope"}),
            "profile_bad_kind",
        ),
        (
            json.dumps({"format": store.EXPORT_FORMAT, "schema": 1, "kind": "style", "data": {}}),
            "name_missing",
        ),
    ],
)
def test_invalid_import_is_rejected_with_a_specific_code(data_dir, payload, code):
    with pytest.raises(store.ProfileStoreError) as exc:
        store.import_profile(payload)
    assert exc.value.code == code
    assert not [r for r in store.list_profiles(store.KIND_STYLE) if not r["built_in"]]


def test_oversized_import_is_rejected_before_parsing(data_dir):
    with pytest.raises(store.ProfileStoreError) as exc:
        store.import_profile("x" * (store.MAX_IMPORT_BYTES + 1))
    assert exc.value.code == "profile_too_large"


def test_unmapped_fields_survive_the_import_and_are_reported(data_dir):
    payload = {
        "format": store.EXPORT_FORMAT,
        "schema": 1,
        "kind": "style",
        "display_name": "未来版本存的",
        "data": {"element": {}, "somethingNew": {"a": 1}},
    }
    rec = store.import_profile(json.dumps(payload))
    assert rec["data"]["extra"] == {"somethingNew": {"a": 1}}
    assert rec["warnings"] == ["unmapped_field:somethingNew"]


# ------------------------------ 旧位置迁移 -----------------------------------
def _write_legacy(data_dir: Path, styles: list[dict]) -> Path:
    path = data_dir / "layouts" / "_styles.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"styles": styles}), encoding="utf-8")
    return path


def test_legacy_styles_move_into_the_store_and_the_old_slot_is_emptied(data_dir):
    legacy = _write_legacy(
        data_dir,
        [
            {
                "id": "s1",
                "name": "老样式",
                "element": {"title": {"fontsize": 10}},
                "palette": ["#111"],
            }
        ],
    )
    report = store.migrate_legacy_styles()
    assert report["migrated"] == 1
    rec = next(r for r in store.list_profiles(store.KIND_STYLE) if not r["built_in"])
    assert rec["id"] == "s1" and rec["display_name"] == "老样式"
    assert rec["data"]["element"]["title"]["fontsize"] == 10
    assert rec["data"]["palette"] == ["#111"]
    # 两份权威不许并存；但内容有备份
    assert not legacy.exists()
    # 备份是**逐字节的原件副本**（所以这里比解析后的内容，不比字面文本）
    backup = Path(report["backup"])
    assert backup.is_file()
    assert json.loads(backup.read_text(encoding="utf-8"))["styles"][0]["name"] == "老样式"


def test_migration_is_idempotent(data_dir):
    _write_legacy(data_dir, [{"id": "s1", "name": "老样式", "element": {}}])
    store.migrate_legacy_styles()
    again = store.migrate_legacy_styles()
    assert again == {"migrated": 0, "skipped": 0, "warnings": [], "backup": ""}
    assert len([r for r in store.list_profiles(store.KIND_STYLE) if not r["built_in"]]) == 1


def test_migration_keeps_unmappable_fields_and_records_a_warning(data_dir):
    _write_legacy(data_dir, [{"id": "s1", "name": "老样式", "element": {}, "从未见过": 1}])
    report = store.migrate_legacy_styles()
    assert report["warnings"] == ["unmapped_field:从未见过"]
    rec = next(r for r in store.list_profiles(store.KIND_STYLE) if not r["built_in"])
    assert rec["data"]["extra"] == {"从未见过": 1}


def test_migration_without_a_legacy_file_does_nothing(data_dir):
    assert store.migrate_legacy_styles()["migrated"] == 0
    assert not store.store_path(store.KIND_STYLE).exists()


# --------------------------------- 8 pt --------------------------------------
def test_the_default_spec_carries_exactly_one_minimum_font_size():
    """统一为 8 pt（ADR 0029）：三个数（8.5 严格 / 8.0 绝对 / 8.5 图例）收敛成一个。"""
    p = profiles.load("lab-publication-v1")
    assert p["min_effective_font_size_pt"] == 8.0
    assert p["absolute_min_font_size_pt"] == 8.0
    assert p["legend_policy"]["min_font_size_pt"] == 8.0


def test_no_evaluator_or_ui_hardcodes_a_minimum_font_size():
    """**代码搜索式的回归看护**（Prompt 10 的退出条件之一）。

    求值器与界面都不许自己写下限：唯一允许出现那个数的地方是规范文件本身，
    以及 `profiles.FALLBACK_MIN_FONT_SIZE_PT`（缺键时的兜底，两侧同源）。
    """
    watched = [
        ROOT / "src" / "tavotto" / "engine" / "preflight.py",
        ROOT / "web" / "src" / "lib" / "preflight.ts",
        ROOT / "web" / "src" / "components" / "ExportDialog.tsx",
        ROOT / "web" / "src" / "components" / "settings" / "ProfilesSettings.tsx",
    ]
    for path in watched:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        # 8.5 / 8.0 / "8 pt" 这类字面量：注释里可以谈它，代码里不许出现
        code = "\n".join(
            line for line in text.splitlines() if not re.match(r"\s*(#|//|\*|/\*)", line)
        )
        assert "8.5" not in code, f"{path.name} 里还留着 8.5 的硬编码"
        assert not re.search(r"(?<![\w.])8\.0(?![\w])", code), f"{path.name} 里还留着 8.0 的硬编码"


def test_font_floor_fallback_is_one_number_on_both_sides():
    """严格同源对：`engine/profiles.FALLBACK_MIN_FONT_SIZE_PT` ↔
    `web/src/lib/profile.ts` 的同名常量。两侧分叉的后果是同一份 spec 在
    MCP 与画布上给出相反的合规结论。"""
    ts = (ROOT / "web" / "src" / "lib" / "profile.ts").read_text(encoding="utf-8")
    match = re.search(r"export const FALLBACK_MIN_FONT_SIZE_PT = ([\d.]+)", ts)
    assert match, "TS 侧找不到 FALLBACK_MIN_FONT_SIZE_PT"
    assert float(match.group(1)) == profiles.FALLBACK_MIN_FONT_SIZE_PT


def test_an_explicit_historical_八点五_is_preserved(data_dir):
    """旧项目**显式**存了 8.5 的（期刊覆盖）保留其历史值，不擅自改结果；
    没显式存过的按新默认走 8 pt。"""
    kept = profiles.load("lab-publication-v1", {"min_effective_font_size_pt": 8.5})
    assert kept["min_effective_font_size_pt"] == 8.5
    spec = {
        "page": {"w_mm": 80.0, "h_mm": 60.0},
        "panels": [
            {
                "id": "p1",
                "kind": "pdf",
                "rect_mm": [0, 0, 80, 60],
                "scale": 1.0,
                "manifest": {
                    "elements": [
                        {
                            "gid": "g",
                            "role": "ticks",
                            "editable": [{"prop": "fontsize", "value": 8.2}],
                        }
                    ]
                },
            }
        ],
        "texts": [],
        "objects": [{"id": "p1", "type": "panel", "rect_mm": [0, 0, 80, 60]}],
    }
    ids = {i["id"] for i in preflight.run(spec, kept)}
    assert "font-too-small" in ids, "显式存下的 8.5 仍然生效"
    assert "font-too-small" not in {i["id"] for i in preflight.run(spec, profiles.load())}


# --------------------------------- HTTP --------------------------------------
def test_http_crud_and_conflict(client):
    listed = client.get("/api/profiles/style").get_json()["profiles"]
    assert [r["id"] for r in listed] == [store.BUILTIN_STYLE_ID]

    made = client.post(
        "/api/profiles/style", json={"display_name": "投稿用", "data": {"element": {}}}
    )
    assert made.status_code == 200
    rec = made.get_json()["profile"]

    stale = client.patch(
        f"/api/profiles/style/{rec['id']}",
        json={"display_name": "改名", "expected_revision": rec["revision"] + 5},
    )
    assert stale.status_code == 409
    body = stale.get_json()
    assert body["code"] == "profile_revision_conflict" and body["current"]["id"] == rec["id"]

    ok = client.patch(
        f"/api/profiles/style/{rec['id']}",
        json={"display_name": "改名", "expected_revision": rec["revision"]},
    )
    assert ok.status_code == 200 and ok.get_json()["profile"]["display_name"] == "改名"

    assert client.delete(f"/api/profiles/style/{rec['id']}").status_code == 200
    assert client.delete(f"/api/profiles/style/{rec['id']}").status_code == 404


def test_http_refuses_a_patch_without_a_revision(client):
    rec = client.post(
        "/api/profiles/style", json={"display_name": "A", "data": {"element": {}}}
    ).get_json()["profile"]
    resp = client.patch(f"/api/profiles/style/{rec['id']}", json={"display_name": "B"})
    assert resp.status_code == 400 and resp.get_json()["code"] == "profile_revision_missing"


def test_http_migrates_the_legacy_file_on_first_read(client, data_dir):
    _write_legacy(data_dir, [{"id": "s1", "name": "老样式", "element": {}}])
    names = [r["display_name"] for r in client.get("/api/profiles/style").get_json()["profiles"]]
    assert "老样式" in names
    assert not (data_dir / "layouts" / "_styles.json").exists()


def test_http_bad_kind_is_a_400_not_a_500(client):
    resp = client.get("/api/profiles/nope")
    assert resp.status_code == 400 and resp.get_json()["code"] == "profile_bad_kind"


# ------------------------- 任意 id → 规范（唯一入口） -------------------------
def test_resolve_spec_finds_both_builtin_and_user_specs(data_dir):
    assert store.resolve_spec()["profile_id"] == profiles.default_profile_id()
    mine = store.duplicate_profile(store.KIND_SPEC, "lab-publication-v1", "我的规范")
    got = store.resolve_spec(mine["id"])
    assert got["profile_id"] == mine["id"], "复制出来的规范有自己的身份"
    assert got["min_effective_font_size_pt"] == 8.0
    merged = store.resolve_spec(mine["id"], {"widths_mm": {"double": 178.0}})
    assert merged["widths_mm"]["double"] == 178.0
    assert merged["widths_mm"]["single"] == 80.0, "journal 只覆盖点名的键"


def test_resolve_spec_refuses_an_unknown_id(data_dir):
    with pytest.raises(store.ProfileStoreError) as exc:
        store.resolve_spec("没有这个")
    assert exc.value.code == "profile_not_found"


def test_a_bad_journal_says_so_instead_of_blaming_the_profile_id(data_dir):
    """两个成因不许压成同一句话：id 不认识 vs 覆盖本身不合法。

    靠捕获 `ProfileError` 来分流的写法会把「你改的这个字段不合法」说成
    「没有这个出版规范」——用户拿着一句指错方向的话去找一个存在的东西。
    """
    with pytest.raises(store.ProfileStoreError) as exc:
        store.resolve_spec("lab-publication-v1", {"widths_mm": {"single": -1}})
    assert exc.value.code == "profile_bad_journal"
    assert "widths_mm" in str(exc.value)
