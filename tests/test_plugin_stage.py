"""完整插件的组装 / 验证 / 归档（scripts/plugin_stage.py）与画布构建器的写盘纪律。

这些判据的主语是**一份插件目录（或 zip）里的字节与模式**，不是源码树：

* 三种身份分开：改审计信息不改 content_digest；改一个字节 / 一个模式 / 多一个文件就改。
* 画布的六种坏法（缺失 / 空 / 无戳 / 过期 / 损坏 / 截断）各自点名，不合成「不可用」。
* 已装副本的合法本地修改（两份清单一起钉 command）不误报；其余任何改动都报。
* zip 是确定性的，解包后不依赖源码树；`..` 与顶层目录错的条目一律拒绝。
* 真起 server 读资源：交出去的画布必须与磁盘上那份逐字相同。

反证记录见 PR 正文（每条 assert 都拿掉过对应的实现看它红）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from tests.support import pluginkit as kit

ROOT = kit.ROOT
stage = kit.load_script("plugin_stage")
widget_builder = kit.load_script("build_mcp_widget")


@pytest.fixture()
def staging(tmp_path):
    d = tmp_path / "stage"
    manifest = kit.synthetic_staging(d)
    return d, manifest


# ================================================================ 身份


def test_a_synthetic_staging_verifies_clean(staging):
    d, m = staging
    assert stage.verify_dir(d, source_sha=kit.FAKE_SHA, version=m["plugin_version"]) == []
    assert m["content_digest"] == stage.read_manifest(d)["content_digest"]


def test_audit_information_does_not_enter_the_content_digest(tmp_path):
    a = kit.synthetic_staging(tmp_path / "a", audit={"run_id": "1", "built_at": "x"})
    b = kit.synthetic_staging(tmp_path / "b", audit={"run_id": "2", "built_at": "y"})
    assert a["content_digest"] == b["content_digest"]
    assert a["audit"] != b["audit"]


def test_one_changed_byte_changes_the_content_digest(tmp_path):
    a = kit.synthetic_staging(tmp_path / "a")
    b = kit.synthetic_staging(tmp_path / "b", widget_salt="different")
    assert a["content_digest"] != b["content_digest"]


def test_a_changed_git_mode_changes_the_content_digest():
    base = [("a", "100644", "x" * 64), ("b", "100644", "y" * 64)]
    assert stage.content_digest(base) != stage.content_digest(
        [("a", "100755", "x" * 64), ("b", "100644", "y" * 64)]
    )
    assert stage.content_digest(base) == stage.content_digest(list(reversed(base)))


def test_the_manifest_does_not_hash_itself(staging):
    d, m = staging
    assert all(e["path"] != stage.BUILD_MANIFEST for e in m["files"])


def test_the_three_identities_are_distinct_fields(staging):
    _d, m = staging
    assert m["source_sha"] == kit.FAKE_SHA
    assert m["build_inputs_fingerprint"] == "feedfacecafebeef"
    assert len(m["content_digest"]) == 64
    assert m["content_digest"] != m["build_inputs_fingerprint"]
    assert m["lockfile_sha256"] == "0" * 64
    assert m["toolchain"]["node"] == "22.0.0"
    assert m["min_tavotto_version"] == "0.13.0"


# ================================================================ 画布六种坏法


def test_widget_problems_name_each_failure_mode(tmp_path):
    p = tmp_path / "canvas.html"
    assert any("缺失" in s for s in stage.widget_problems(p, expect_fingerprint=None))
    p.write_bytes(b"")
    assert any("空文件" in s for s in stage.widget_problems(p, expect_fingerprint=None))
    p.write_bytes(b"<html>" + b"x" * 200_000)
    probs = stage.widget_problems(p, expect_fingerprint=None)
    assert any("指纹戳" in s for s in probs) and any("损坏" in s for s in probs)
    kit.write_fake_widget(p, fingerprint="aaaaaaaaaaaaaaaa")
    assert stage.widget_problems(p, expect_fingerprint="aaaaaaaaaaaaaaaa") == []
    assert any("过期" in s for s in stage.widget_problems(p, expect_fingerprint="bbbbbbbbbbbbbbbb"))
    p.write_bytes(kit.fake_widget_bytes()[:50_000])
    assert any("截断" in s for s in stage.widget_problems(p, expect_fingerprint=None))
    p.write_bytes(
        f'{widget_builder.STAMP}deadbeefdeadbeef -->\n<html><script src="/canvas.js"></script>'
        '<div id="root"></div>'.encode()
        + b"x" * 200_000
    )
    assert any("外链" in s for s in stage.widget_problems(p, expect_fingerprint=None))


# ================================================================ 验证


def test_verify_catches_every_kind_of_drift(staging):
    d, m = staging
    (d / "extra.txt").write_text("x", encoding="utf-8")
    assert any("多出" in s for s in stage.verify_dir(d))
    (d / "extra.txt").unlink()

    (d / "README.md").write_bytes(b"tampered\n")
    assert any("README.md" in s and "sha256" in s for s in stage.verify_dir(d))
    kit.synthetic_staging(d)  # 复位

    (d / "assets" / "tavotto.svg").unlink()
    assert any("清单里有、目录里没有" in s for s in stage.verify_dir(d))
    kit.synthetic_staging(d)

    assert any("source SHA" in s for s in stage.verify_dir(d, source_sha="f" * 40))
    assert any("版本对不上" in s for s in stage.verify_dir(d, version="9.9.9"))
    assert any(
        "content_digest 对不上" in s for s in stage.verify_dir(d, expect_content_digest="0" * 64)
    )


def test_a_tampered_manifest_is_caught_by_recomputation(staging):
    d, _m = staging
    mp = d / stage.BUILD_MANIFEST
    data = json.loads(mp.read_text(encoding="utf-8"))
    data["content_digest"] = "0" * 64
    mp.write_text(json.dumps(data), encoding="utf-8")
    assert any("清单被改过" in s for s in stage.verify_dir(d))


def test_a_missing_canvas_is_a_verification_failure_not_a_fallback(staging):
    d, _m = staging
    (d / "mcp" / "widget" / "canvas.html").unlink()
    probs = stage.verify_dir(d)
    assert any("画布缺失" in s for s in probs)


def test_a_modified_canvas_with_the_old_stamp_is_caught(staging):
    """改画布字节但保留旧戳：戳看不出来，sha256 看得出来。"""
    d, m = staging
    p = d / "mcp" / "widget" / "canvas.html"
    data = p.read_bytes().replace(b"<title>t ", b"<title>u ")
    assert data != p.read_bytes()
    p.write_bytes(data)
    assert stage.widget_problems(p, expect_fingerprint="feedfacecafebeef") == [], "戳本身还是对的"
    assert any("canvas.html" in s and "sha256" in s for s in stage.verify_dir(d))


def test_a_canvas_from_another_build_with_this_identity_is_caught(tmp_path):
    """拿 A 的画布配 B 的身份：清单是 B 的，画布是 A 的。"""
    a = tmp_path / "a"
    b = tmp_path / "b"
    kit.synthetic_staging(a, widget_salt="A")
    kit.synthetic_staging(b, widget_salt="B")
    shutil.copyfile(a / "mcp" / "widget" / "canvas.html", b / "mcp" / "widget" / "canvas.html")
    assert any("canvas.html" in s for s in stage.verify_dir(b))


def test_release_staging_refuses_an_absolute_interpreter_path(staging):
    d, _m = staging
    mcp = d / ".mcp.json"
    data = json.loads(mcp.read_text(encoding="utf-8"))
    for entry in data["mcpServers"].values():
        entry["command"] = sys.executable
    mcp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    probs = stage.verify_dir(d)
    assert any("只许裸名字" in s for s in probs)
    with pytest.raises(stage.StageError):
        stage.describe(d, {})


# ================================================================ 已装副本


def _pin_with_the_real_installer(plugin_dir: Path, command: str) -> None:
    from tavotto.engine import codexinstall

    codexinstall.pin_launcher_command(plugin_dir, command)


def test_installed_copy_pinned_by_the_real_installer_verifies(staging):
    """`tavotto codex install` 钉过的副本：两份 command 一起变，其余一字不动——合法。"""
    d, _m = staging
    _pin_with_the_real_installer(d, sys.executable)
    assert stage.verify_dir(d) != [], "发行件模式下绝对路径必须红"
    assert stage.verify_dir(d, installed=True) == []


def test_installed_copy_with_only_one_side_pinned_is_reported(staging):
    d, _m = staging
    mcp = d / ".mcp.json"
    data = json.loads(mcp.read_text(encoding="utf-8"))
    for entry in data["mcpServers"].values():
        entry["command"] = sys.executable
    mcp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    probs = stage.verify_dir(d, installed=True)
    assert any("只钉了一侧" in s for s in probs), probs


def test_installed_copy_pinned_to_a_missing_interpreter_is_reported(staging, tmp_path):
    d, _m = staging
    ghost = str(tmp_path / "no-such-python")
    _pin_with_the_real_installer(d, ghost)
    probs = stage.verify_dir(d, installed=True)
    assert any("不存在的解释器" in s for s in probs), probs


def test_installed_copy_with_a_changed_non_command_field_is_reported(staging):
    d, _m = staging
    mcp = d / ".mcp.json"
    data = json.loads(mcp.read_text(encoding="utf-8"))
    for entry in data["mcpServers"].values():
        entry["command"] = sys.executable
        entry["args"] = ["./mcp/evil.py"]
    mcp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    probs = stage.verify_dir(d, installed=True)
    assert any("超出了 command 字段" in s for s in probs), probs


def test_installed_copy_ignores_pycache_but_not_other_extra_files(staging):
    d, _m = staging
    (d / "mcp" / "__pycache__").mkdir()
    (d / "mcp" / "__pycache__" / "server.cpython-313.pyc").write_bytes(b"\0")
    assert stage.verify_dir(d, installed=True) == []
    (d / "mcp" / "hook.py").write_text("print(1)\n", encoding="utf-8")
    assert any("多出" in s for s in stage.verify_dir(d, installed=True))


def test_installed_copy_with_a_modified_release_file_is_reported(staging):
    d, _m = staging
    _pin_with_the_real_installer(d, sys.executable)
    p = d / "mcp" / "server.py"
    p.write_bytes(p.read_bytes() + b"\n# tampered\n")
    probs = stage.verify_dir(d, installed=True)
    assert any("server.py" in s and "发行文件被改过" in s for s in probs), probs


def test_yaml_canonical_form_matches_the_installers_scanner(staging):
    """两侧的「哪一行是 command」必须是同一条规则：钉完 canonical 不变。"""
    d, m = staging
    yaml_rel = "skills/tavotto-figure/agents/openai.yaml"
    before = m["pinnable"][yaml_rel]["canonical_sha256"]
    _pin_with_the_real_installer(d, "/opt/some where/python3")
    canon, commands = stage._canonical_yaml((d / yaml_rel).read_bytes())
    assert stage.sha256_bytes(canon) == before
    assert commands == ["/opt/some where/python3"]
    # CRLF 副本同样规范化到同一个值
    crlf = (d / yaml_rel).read_bytes().replace(b"\n", b"\r\n")
    canon2, _ = stage._canonical_yaml(crlf)
    assert stage.sha256_bytes(canon2) == before


# ================================================================ 归档


def test_zip_is_deterministic_and_round_trips(staging, tmp_path):
    d, m = staging
    z1 = stage.write_zip(d, tmp_path / "one.zip")
    z2 = stage.write_zip(d, tmp_path / "two.zip")
    assert stage.sha256_file(z1) == stage.sha256_file(z2)
    names = zipfile.ZipFile(z1).namelist()
    assert all(n.startswith("codex-plugin/") for n in names)
    assert "codex-plugin/.codex-plugin/plugin.json" in names, "dotfile 进了 zip"
    assert "codex-plugin/.mcp.json" in names
    assert "codex-plugin/plugin-build.json" in names
    out = stage.unpack_zip(z1, tmp_path / "unpacked")
    assert (
        stage.verify_dir(out, source_sha=kit.FAKE_SHA, expect_content_digest=m["content_digest"])
        == []
    )
    for p in stage._walk(out):
        rel = stage._rel(out, p)
        assert p.read_bytes() == (d / rel).read_bytes(), rel


def test_zip_keeps_executable_mode_from_the_manifest(tmp_path):
    d = tmp_path / "s"
    kit.synthetic_staging(d)
    mp = d / stage.BUILD_MANIFEST
    data = json.loads(mp.read_text(encoding="utf-8"))
    for e in data["files"]:
        if e["path"] == "skills/tavotto-figure/scripts/handoff.py":
            e["mode"] = "100755"
    data["content_digest"] = stage.content_digest(
        [(e["path"], e["mode"], e["sha256"]) for e in data["files"]]
    )
    mp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    z = stage.write_zip(d, tmp_path / "p.zip")
    info = zipfile.ZipFile(z).getinfo("codex-plugin/skills/tavotto-figure/scripts/handoff.py")
    assert (info.external_attr >> 16) & 0o111, "可执行位没进 zip"


def test_unpack_refuses_escaping_or_misrooted_entries(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("codex-plugin/../evil.txt", "x")
    with pytest.raises(stage.StageError):
        stage.unpack_zip(bad, tmp_path / "o1")
    other = tmp_path / "other.zip"
    with zipfile.ZipFile(other, "w") as zf:
        zf.writestr("elsewhere/plugin.json", "{}")
    with pytest.raises(stage.StageError):
        stage.unpack_zip(other, tmp_path / "o2")


def test_archive_refuses_an_unverified_staging(staging, tmp_path):
    d, _m = staging
    (d / "mcp" / "widget" / "canvas.html").unlink()
    rc = stage.main(["archive", "--stage", str(d), "--out", str(tmp_path / "x.zip")])
    assert rc == 2
    assert not (tmp_path / "x.zip").exists()


def test_make_plugin_manifest_zips_only_a_verified_staging(staging, tmp_path):
    mpm = kit.load_script("make_plugin_manifest")
    d, m = staging
    version = m["plugin_version"]
    out = tmp_path / "codex-plugin.json"
    z = tmp_path / f"codex-plugin-{version}.zip"
    assert (
        mpm.main(
            ["--tag", f"v{version}", "--out", str(out), "--zip", str(z), "--plugin-dir", str(d)]
        )
        == 0
    )
    assert z.is_file()
    (d / "README.md").write_bytes(b"tampered\n")
    with pytest.raises(SystemExit):
        mpm.main(
            [
                "--tag",
                f"v{version}",
                "--out",
                str(out),
                "--zip",
                str(tmp_path / "no.zip"),
                "--plugin-dir",
                str(d),
            ]
        )
    assert not (tmp_path / "no.zip").exists()


# ================================================================ 真起 server


def _engine_python() -> str:
    return sys.executable


def test_serve_check_reads_the_canvas_back_through_stdio(staging):
    d, _m = staging
    problems = stage.serve_check(d, _engine_python())
    assert problems == [], problems


def test_serve_check_notices_a_server_that_has_no_canvas(staging):
    d, _m = staging
    (d / "mcp" / "widget" / "canvas.html").unlink()
    problems = stage.serve_check(d, _engine_python())
    assert problems, "画布不在时 server 不声明资源，这里必须有话说"
    assert any("没挂画布" in s or "resources/list" in s for s in problems), problems


# ================================================================ 真实源码树上的 stage()


def _tree_status() -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def test_stage_on_the_real_tree_leaves_the_tree_untouched(tmp_path):
    head = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    before = _tree_status()
    widget = kit.write_fake_widget(tmp_path / "canvas.html")
    out = tmp_path / "stage"
    m = stage.stage(
        out,
        widget,
        source_sha=head,
        root=ROOT,
        allow_dirty=True,
        skip_fingerprint=True,
        audit={"run": "test"},
    )
    assert _tree_status() == before, "stage() 动了源码树"
    assert m["source_sha"] == head
    assert stage.verify_dir(out, source_sha=head) == []
    for rel in stage.REQUIRED:
        assert (out / rel).is_file(), rel
    assert not (ROOT / "web" / "dist-mcp").exists()


def test_stage_refuses_a_source_sha_that_is_not_head(tmp_path):
    widget = kit.write_fake_widget(tmp_path / "canvas.html")
    with pytest.raises(stage.StageError, match="HEAD"):
        stage.stage(
            tmp_path / "s",
            widget,
            source_sha="f" * 40,
            root=ROOT,
            allow_dirty=True,
            skip_fingerprint=True,
        )
    assert not (tmp_path / "s").exists(), "失败不许留下半个 staging"


def test_stage_refuses_a_missing_widget_and_leaves_nothing_behind(tmp_path):
    head = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    with pytest.raises(stage.StageError, match="画布"):
        stage.stage(
            tmp_path / "s",
            tmp_path / "nope.html",
            source_sha=head,
            root=ROOT,
            allow_dirty=True,
            skip_fingerprint=True,
        )
    assert not (tmp_path / "s").exists()
    assert not list(tmp_path.glob(".plugin-stage-*"))


def test_stage_refuses_a_non_empty_output_directory(tmp_path):
    head = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    widget = kit.write_fake_widget(tmp_path / "canvas.html")
    out = tmp_path / "s"
    out.mkdir()
    (out / "leftover").write_text("x", encoding="utf-8")
    with pytest.raises(stage.StageError, match="非空"):
        stage.stage(
            out, widget, source_sha=head, root=ROOT, allow_dirty=True, skip_fingerprint=True
        )


def test_stage_takes_sources_from_the_index_not_the_whole_directory(tmp_path):
    """工作区里多放的文件不进 staging；索引说了算。"""
    head = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    stray = ROOT / "codex-plugin" / "mcp" / "stray_untracked_file.txt"
    stray.write_text("do not ship\n", encoding="utf-8")
    try:
        widget = kit.write_fake_widget(tmp_path / "canvas.html")
        out = tmp_path / "s"
        stage.stage(
            out, widget, source_sha=head, root=ROOT, allow_dirty=True, skip_fingerprint=True
        )
        assert not (out / "mcp" / "stray_untracked_file.txt").exists()
    finally:
        stray.unlink()


# ================================================================ 画布构建器的输入与写盘


def test_widget_fingerprint_inputs_cover_more_than_ts_tsx_css():
    rels = {p.relative_to(ROOT).as_posix() for p in widget_builder.source_inputs()}
    assert "web/pnpm-lock.yaml" in rels
    assert "scripts/build_mcp_widget.py" in rels
    assert "web/src/i18n/locales/zh-CN/common.json" in rels
    assert "web/src/playground/generated/examples-manifest.json" in rels
    assert "web/tsconfig.app.json" in rels
    assert not any(".test." in r for r in rels)


def test_widget_fingerprint_changes_when_a_locale_json_changes(tmp_path, monkeypatch):
    base = widget_builder.source_fingerprint()
    target = ROOT / "web" / "src" / "i18n" / "locales" / "zh-CN" / "common.json"
    original = target.read_bytes()
    try:
        target.write_bytes(original + b"\n")
        assert widget_builder.source_fingerprint() != base
    finally:
        target.write_bytes(original)
    assert widget_builder.source_fingerprint() == base


def test_write_output_is_atomic_and_keeps_the_old_artifact_on_failure(tmp_path, monkeypatch):
    out = tmp_path / "canvas.html"
    out.write_text("old\n", encoding="utf-8")

    def boom(_src, _dst):
        raise OSError("disk full")

    monkeypatch.setattr(widget_builder.os, "replace", boom)
    with pytest.raises(OSError):
        widget_builder.write_output("new\n", out)
    assert out.read_text(encoding="utf-8") == "old\n"
    assert not list(tmp_path.glob(".canvas.html.*.tmp")), "临时文件没清掉"


def test_check_with_explicit_out_distinguishes_missing_stale_ok(tmp_path, capsys):
    out = tmp_path / "canvas.html"
    assert widget_builder.main(["--check", "--out", str(out)]) == 2
    out.write_text(f"{widget_builder.STAMP}deadbeefdeadbeef -->\n", encoding="utf-8")
    assert widget_builder.main(["--check", "--out", str(out)]) == 1
    out.write_text(
        f"{widget_builder.STAMP}{widget_builder.source_fingerprint()} -->\n", encoding="utf-8"
    )
    assert widget_builder.main(["--check", "--out", str(out)]) == 0
    capsys.readouterr()


@pytest.mark.skipif(shutil.which("pnpm") is None, reason="需要 pnpm 真构建一次画布")
@pytest.mark.skipif(
    not (ROOT / "web" / "node_modules").is_dir(),
    reason="web/node_modules 未安装（先 pnpm install）",
)
def test_a_real_build_writes_only_the_requested_output(tmp_path):
    """真跑一次 vite：产物落在 --out，源码树不变，web/ 下不留中间目录。"""
    before = _tree_status()
    out = tmp_path / "built" / "canvas.html"
    rc = widget_builder.main(["--out", str(out), "--json"])
    assert rc == 0
    assert _tree_status() == before, "构建改动了源码树"
    assert not (ROOT / "web" / "dist-mcp").exists()
    assert stage.widget_problems(out, expect_fingerprint=widget_builder.source_fingerprint()) == []
    assert os.path.getsize(out) > stage.WIDGET_MIN_BYTES


def test_the_widget_stamp_is_one_string_on_both_sides():
    """写戳的一侧（build_mcp_widget）与验戳的一侧（engine/pluginmanifest）必须逐字相同。"""
    from tavotto.engine import pluginmanifest

    assert widget_builder.STAMP == pluginmanifest.WIDGET_STAMP
    assert stage.pm.WIDGET_STAMP == pluginmanifest.WIDGET_STAMP


def test_stage_refuses_when_a_widget_input_is_dirty(tmp_path):
    """dirty 判据的主语是 staging 的**每一份输入**：改了 web/src 或 LICENSE 而没提交，
    source_sha 就是假的——不只看 codex-plugin/（Codex 在 #289 上指出）。"""
    head = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    target = ROOT / "web" / "src" / "i18n" / "locales" / "en-US" / "common.json"
    original = target.read_bytes()
    widget = kit.write_fake_widget(tmp_path / "canvas.html")
    try:
        target.write_bytes(original + b"\n")
        assert any("common.json" in d for d in stage.plugin_source_dirty(ROOT))
        with pytest.raises(stage.StageError, match="未提交"):
            stage.stage(tmp_path / "s", widget, source_sha=head, root=ROOT, skip_fingerprint=True)
    finally:
        target.write_bytes(original)
    lic = ROOT / "LICENSE"
    lic_bytes = lic.read_bytes()
    try:
        lic.write_bytes(lic_bytes + b"\n")
        assert any(d == "LICENSE" for d in stage.plugin_source_dirty(ROOT))
    finally:
        lic.write_bytes(lic_bytes)


def test_a_malformed_manifest_is_a_problem_not_a_crash(staging):
    """`files: null`、条目缺 path、pinnable 不是对象：验证要报出来，不许 TypeError。"""
    d, _m = staging
    mp = d / stage.BUILD_MANIFEST
    good = json.loads(mp.read_text(encoding="utf-8"))
    for mutate in (
        lambda m: m.__setitem__("files", None),
        lambda m: m["files"].__setitem__(0, {"sha256": "0" * 64, "mode": "100644"}),
        lambda m: m.__setitem__("pinnable", []),
        lambda m: m.__setitem__("content_digest", None),
    ):
        data = json.loads(json.dumps(good))
        mutate(data)
        mp.write_text(json.dumps(data), encoding="utf-8")
        problems = stage.verify_dir(d, installed=True)
        assert problems and all(isinstance(p, str) for p in problems), problems
    mp.write_text(json.dumps(good, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert stage.verify_dir(d) == []
