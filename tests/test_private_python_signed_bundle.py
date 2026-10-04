"""Signed transport acceptance: synthetic bundles exercise the real trust predicates.

System codesign is stubbed here; only the native final-app smoke proves Apple signing.
"""

from __future__ import annotations

import dataclasses
import io
import json
import os
import plistlib
import shutil
import struct
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from tavotto.engine import privatepython, privatepython_bundle, runtime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import codesign_macos as cs  # noqa: E402

MANIFEST = Path("tavotto/resources/private_python_bundle.json")
TEAM = "AB12345678"
HOST_PYTHON = sys.executable


def make_archive(path, *, extra=None):
    with tarfile.open(path, "w:gz") as tar:
        for name in ("python", "python/bin", "python/lib"):
            directory = tarfile.TarInfo(name)
            directory.type, directory.mode = tarfile.DIRTYPE, 0o750
            tar.addfile(directory)
        for name, kind in (("bin/python3.13", cs.MH_EXECUTE), ("lib/native.so", cs.MH_BUNDLE)):
            load = struct.pack("<IIIIII", cs.LC_BUILD_VERSION, 24, 1, 11 << 16, 0, 0)
            data = (
                b"\xcf\xfa\xed\xfe"
                + struct.pack("<IIIIIII", cs.CPU_TYPE_ARM64, 0, kind, 1, len(load), 0, 0)
                + load
            )
            item = tarfile.TarInfo("python/" + name)
            item.size, item.mode = len(data), 0o755
            tar.addfile(item, io.BytesIO(data))
        link = tarfile.TarInfo("python/bin/python3")
        link.type, link.linkname = tarfile.SYMTYPE, "python3.13"
        tar.addfile(link)
        if extra:
            tar.addfile(extra)


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    app = tmp_path / "Tavotto.app"
    internal = app / "Contents/Resources/sidecar/Tavotto/_internal"
    archive_dir = internal / "private-python"
    archive_dir.mkdir(parents=True)
    internal.joinpath(*MANIFEST.parts[:-1]).mkdir(parents=True)
    executable = internal.parent / "Tavotto"
    executable.write_bytes(b"sidecar")
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"LSMinimumSystemVersion": "14.0"}))
    real = privatepython.source_for("macos-arm64")
    archive = archive_dir / real.archive_name
    make_archive(archive)
    source = dataclasses.replace(
        real, sha256=privatepython._sha256_file(archive), size=archive.stat().st_size
    )
    monkeypatch.setattr(runtime, "private_python_bundle_dirs", lambda: [str(archive_dir)])
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(internal), raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.delenv(runtime.PRIVATE_PYTHON_BUNDLE_ENV, raising=False)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    return app, internal, archive, source


def receipt(internal, archive, source):
    path = internal / MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema": 1,
        "target": source.target,
        "version": source.version,
        "release": source.release,
        "archive_root": source.archive_root,
        "python_rel": source.python_rel,
        "source_id": source.id,
        "upstream_sha256": source.sha256,
        "upstream_size": source.size,
        "archive_name": source.archive_name,
        "sha256": privatepython._sha256_file(archive),
        "size": archive.stat().st_size,
    }
    path.write_text(json.dumps(data))
    return path, data


def fake_codesign(monkeypatch, *, deny=None, team=TEAM):
    calls = []
    original_run = subprocess.run

    def run(argv, **kw):
        if argv[0] != "/usr/bin/codesign":
            return original_run(argv, **kw)
        calls.append(argv)
        rc = 1 if deny and deny(argv) else 0
        if rc and kw.get("check"):
            raise subprocess.CalledProcessError(rc, argv)
        return subprocess.CompletedProcess(
            argv,
            rc,
            "",
            f"TeamIdentifier={team}\nTimestamp=Oct 4 2026\nCodeDirectory flags=0x10000(runtime)\n",
        )

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_signed_transport_is_accepted_without_changing_source_identity(bundle, monkeypatch):
    app, internal, archive, source = bundle
    archive.write_bytes(archive.read_bytes() + b"signed transport")
    receipt(internal, archive, source)
    calls = fake_codesign(monkeypatch)
    assert privatepython.bundled_archive(source) == archive
    assert source.id.endswith(source.sha256[:12])
    assert any("--verify" in call and str(app) in call for call in calls)
    assert all(call[0] == "/usr/bin/codesign" for call in calls)


def test_signer_reaches_every_archived_macho_before_outer_seal(bundle, monkeypatch):
    app, internal, archive, source = bundle
    monkeypatch.setattr(privatepython, "source_for", lambda target=None: source)
    calls = []

    def sign(args):
        calls.append(args)
        if "--sign" in args and args[-1] != str(app):
            path = Path(args[-1])
            if cs.inspect(path):
                path.write_bytes(path.read_bytes() + b"signature")
        return (
            0,
            f"TeamIdentifier={TEAM}\nTimestamp=Oct 4 2026\nCodeDirectory flags=0x10000(runtime)\n",
        )

    monkeypatch.setattr(cs, "_codesign", sign)
    cs.sign(app, "Developer ID Application: Test", ROOT / "packaging/entitlements.plist")
    signed = [Path(c[-1]).name for c in calls if "--sign" in c]
    assert "python3.13" in signed and "native.so" in signed
    assert signed[-1] == app.name
    assert archive.stat().st_size != source.size
    assert (internal / MANIFEST).is_file()


@pytest.fixture
def transformed(bundle, monkeypatch):
    app, internal, archive, source = bundle
    archive.write_bytes(archive.read_bytes() + b"signed transport")
    path, data = receipt(internal, archive, source)
    fake_codesign(monkeypatch)
    return app, internal, archive, source, path, data


@pytest.mark.parametrize(
    "key,value",
    [
        ("target", "macos-x86_64"),
        ("version", "3.0.0"),
        ("release", "wrong"),
        ("archive_root", "elsewhere"),
        ("python_rel", "evil"),
        ("source_id", "forged"),
        ("upstream_sha256", "0" * 64),
        ("upstream_size", 1),
        ("archive_name", "elsewhere.tar.gz"),
        ("sha256", "0" * 64),
        ("size", 1),
        ("schema", 2),
    ],
)
def test_manifest_must_bind_full_source_and_transport(transformed, key, value):
    _, _, archive, source, path, data = transformed
    data[key] = value
    path.write_text(json.dumps(data))
    assert privatepython.bundled_archive(source) is None


@pytest.mark.parametrize("damage", ["missing", "invalid-json", "payload"])
def test_missing_metadata_and_payload_mutation_fail(transformed, damage):
    _, _, archive, source, path, _ = transformed
    if damage == "missing":
        path.unlink()
    elif damage == "invalid-json":
        path.write_text("[")
    else:
        archive.write_bytes(archive.read_bytes() + b"mutation")
    assert privatepython.bundled_archive(source) is None


@pytest.mark.parametrize("denied", ["sidecar", "outer", "same-team", "developer-id"])
def test_system_verification_is_required(transformed, monkeypatch, denied):
    app, _, archive, source, _, _ = transformed

    def deny(argv):
        if "--verify" not in argv:
            return False
        if denied == "sidecar":
            return argv[-1] == sys.executable
        if denied == "outer":
            return argv[-1] == str(app)
        if denied == "same-team":
            return "-R" in argv and f'subject.OU] = "{TEAM}"' in argv[argv.index("-R") + 1]
        return (
            "-R" in argv
            and privatepython_bundle.DEVELOPER_ID_REQUIREMENT in argv[argv.index("-R") + 1]
        )

    calls = fake_codesign(monkeypatch, deny=deny)
    assert privatepython.bundled_archive(source) is None
    assert any(deny(call) for call in calls)


@pytest.mark.parametrize("team", ["", "not set", "forged\nTeamIdentifier=also-forged"])
def test_absent_or_invalid_verified_team_fails(transformed, monkeypatch, team):
    _, _, _, source, _, _ = transformed
    fake_codesign(monkeypatch, team=team)
    assert privatepython.bundled_archive(source) is None


def test_payload_and_metadata_rewrite_cannot_replace_the_seal(transformed, monkeypatch):
    app, internal, archive, source, _, _ = transformed
    archive.write_bytes(archive.read_bytes() + b"attacker")
    receipt(internal, archive, source)
    fake_codesign(monkeypatch, deny=lambda args: args[-1] == str(app) and "--verify" in args)
    assert privatepython.bundled_archive(source) is None


@pytest.mark.parametrize(
    "boundary", ["linux", "win32", "not-frozen", "override", "whitespace-override"]
)
def test_derived_trust_never_crosses_platform_or_override_boundary(
    transformed, monkeypatch, boundary
):
    _, _, archive, source, _, _ = transformed
    if boundary in ("linux", "win32"):
        monkeypatch.setattr(sys, "platform", boundary)
    elif boundary == "not-frozen":
        monkeypatch.setattr(sys, "frozen", False)
    else:
        monkeypatch.setenv(
            runtime.PRIVATE_PYTHON_BUNDLE_ENV,
            " " if boundary == "whitespace-override" else str(archive.parent),
        )
    assert privatepython.bundled_archive(source) is None


@pytest.mark.parametrize("boundary", ["darwin", "linux", "win32", "override"])
def test_original_hash_stays_usable_without_any_signature(bundle, monkeypatch, boundary):
    _, _, archive, source = bundle
    if boundary == "override":
        monkeypatch.setenv(runtime.PRIVATE_PYTHON_BUNDLE_ENV, str(archive.parent))
    else:
        monkeypatch.setattr(sys, "platform", boundary)
    calls = fake_codesign(monkeypatch, deny=lambda args: True)
    assert privatepython.bundled_archive(source) == archive
    assert calls == []


@pytest.mark.parametrize("escape", ["archive", "manifest", "base", "sidecar"])
def test_resources_must_be_contained_in_executing_real_app(
    transformed, tmp_path, monkeypatch, escape
):
    _, internal, archive, source, path, _ = transformed
    external = tmp_path / "outside"
    external.mkdir()
    if escape in ("archive", "manifest"):
        old = archive if escape == "archive" else path
        moved = external / old.name
        old.rename(moved)
        old.symlink_to(moved)
    elif escape == "base":
        shutil.copytree(internal, external / "_internal")
        monkeypatch.setattr(sys, "_MEIPASS", str(external / "_internal"))
    else:
        executable = external / "other.app/Contents/MacOS/Other"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"other signed app")
        monkeypatch.setattr(sys, "executable", str(executable))
    assert privatepython.bundled_archive(source) is None


def test_receipt_next_to_arbitrary_archive_is_never_authority(transformed, tmp_path, monkeypatch):
    _, _, archive, source, path, _ = transformed
    other = tmp_path / "arbitrary"
    other.mkdir()
    shutil.copy2(archive, other / archive.name)
    shutil.copy2(path, other / path.name)
    monkeypatch.setattr(runtime, "private_python_bundle_dirs", lambda: [str(other)])
    assert privatepython.bundled_archive(source) is None


def test_no_positive_trust_cache(transformed, monkeypatch):
    app, _, archive, source, _, _ = transformed
    assert privatepython.bundled_archive(source) == archive
    fake_codesign(monkeypatch, deny=lambda args: args[-1] == str(app))
    assert privatepython.bundled_archive(source) is None


def test_planned_bundle_corruption_does_not_download(transformed, monkeypatch):
    _, _, archive, source, _, _ = transformed
    archive.write_bytes(b"damaged")
    monkeypatch.setattr(
        privatepython, "_fetch", lambda *a, **kw: pytest.fail("network fallthrough")
    )
    with pytest.raises(privatepython.ProvisionError) as error:
        privatepython.provision(source, required_origin=privatepython.ORIGIN_BUNDLED)
    assert error.value.code == privatepython.ERROR_SOURCE_CHANGED


def test_snapshot_is_the_checked_input_even_if_bundle_path_is_replaced(
    bundle, tmp_path, monkeypatch
):
    _, _, archive, source = bundle
    staging = privatepython.runtimes_dir() / ".staging-test"
    staging.mkdir(parents=True)
    original_extract = privatepython._extract

    def replace_before_extract(path, target, src, **kw):
        path.write_bytes(b"path replaced after snapshot")
        return original_extract(path, target, src, **kw)

    monkeypatch.setattr(privatepython, "_extract", replace_before_extract)
    result = privatepython._extract_bundle(archive, staging, source)
    assert (result / "bin/python3.13").read_bytes().startswith(b"\xcf\xfa\xed\xfe")
    assert not list(staging.glob("*.tar.gz"))


def test_snapshot_rejects_changed_bytes_before_extract(bundle, monkeypatch):
    _, _, archive, source = bundle
    archive.write_bytes(b"changed before snapshot")
    staging = privatepython.runtimes_dir() / ".staging-test"
    staging.mkdir(parents=True)
    monkeypatch.setattr(
        privatepython, "_extract", lambda *a, **kw: pytest.fail("unverified extraction")
    )
    with pytest.raises(privatepython.ProvisionError) as error:
        privatepython._extract_bundle(archive, staging, source)
    assert error.value.code == privatepython.ERROR_SOURCE_CHANGED


def test_repackage_requires_pristine_upstream_bytes(bundle, tmp_path, monkeypatch):
    app, _, archive, source = bundle
    monkeypatch.setattr(privatepython, "source_for", lambda target=None: source)
    monkeypatch.setattr(cs, "ROOT", tmp_path / "no-build-input")
    archive.write_bytes(archive.read_bytes() + b"not the locked original")
    monkeypatch.setattr(
        cs,
        "_codesign",
        lambda args: (
            0,
            f"TeamIdentifier={TEAM}\nTimestamp=now\nCodeDirectory flags=0x10000(runtime)\n",
        ),
    )
    with pytest.raises(cs.SignError, match="upstream lock"):
        cs.sign_private_python(app, "identity", None)


@pytest.mark.parametrize("bad", ["duplicate", "type-change", "escape", "no-data-filter"])
def test_signing_rejects_unsafe_archive_before_codesign(bundle, tmp_path, monkeypatch, bad):
    _, _, archive, source = bundle
    if bad == "no-data-filter":
        monkeypatch.delattr(tarfile, "data_filter")
    else:
        info = tarfile.TarInfo(
            "python/bin/python3.13" if bad in ("duplicate", "type-change") else "python/link"
        )
        if bad != "duplicate":
            info.type, info.linkname = (
                tarfile.SYMTYPE,
                "../../outside" if bad == "escape" else "bin",
            )
        make_archive(archive, extra=info)
    with pytest.raises((cs.SignError, privatepython.ProvisionError, tarfile.TarError, ValueError)):
        cs._unpack_private(archive, tmp_path / "unpack", source)


@pytest.mark.parametrize("reverse", [False, True], ids=["backward-links", "forward-links"])
@pytest.mark.parametrize(
    "links,reason",
    [
        pytest.param(
            [("python/link", "."), ("python/out", "link/../outside")],
            "escapes root",
            id="chain-escape",
        ),
        pytest.param(
            [
                ("python/link", "."),
                ("python/alias", "link"),
                ("python/out", "alias/../outside"),
            ],
            "escapes root",
            id="nested-chain-escape",
        ),
        pytest.param([("python/link", "link")], "cyclic", id="self-cycle"),
        pytest.param(
            [("python/link", "other"), ("python/other", "link")],
            "cyclic",
            id="indirect-cycle",
        ),
        pytest.param(
            [("python/link", "bin"), ("python/link/child", ".")],
            "symlink ancestor",
            id="alias-installed-member",
        ),
    ],
)
def test_signing_checks_original_posix_link_graph_before_extraction(
    bundle, tmp_path, monkeypatch, reverse, links, reason
):
    _, _, archive, source = bundle
    with tarfile.open(archive, "w:gz") as tar:
        for name, target in reversed(links) if reverse else links:
            info = tarfile.TarInfo(name)
            info.type, info.linkname = tarfile.SYMTYPE, target
            tar.addfile(info)
    # The subject is the original graph that repacking preserves, before the
    # host can create links or data_filter can normalize their meaning away.
    monkeypatch.setattr(
        tarfile.TarFile, "extractall", lambda *a, **kw: pytest.fail("unsafe extraction started")
    )
    with pytest.raises(cs.SignError, match=reason):
        cs._unpack_private(archive, tmp_path / "unpack", source)


@pytest.mark.parametrize("reverse", [False, True], ids=["backward-links", "forward-links"])
def test_signing_accepts_contained_pbs_style_link_graph(bundle, tmp_path, monkeypatch, reverse):
    _, _, archive, source = bundle
    links = [
        ("python/bin/python3", "python3.13"),
        ("python/bin/python", "python3"),
        ("python/lib/libpython3.dylib", "libpython3.13.dylib"),
        ("python/lib/pkgconfig/python3.pc", "python-3.13.pc"),
        ("python/config", "lib/pkgconfig"),
        ("python/library", "config/../libpython3.dylib"),
        ("python/bin/python-config", "../lib/python3.13/config/python-config.py"),
        ("python/current", "."),
        ("python/repeated", "current/current/bin/python3"),
    ]
    with tarfile.open(archive, "w:gz") as tar:
        for name, target in reversed(links) if reverse else links:
            info = tarfile.TarInfo(name)
            info.type, info.linkname = tarfile.SYMTYPE, target
            tar.addfile(info)
    extracted = []
    monkeypatch.setattr(
        tarfile.TarFile, "extractall", lambda *a, **kw: extracted.extend(kw["members"])
    )
    members = cs._unpack_private(archive, tmp_path / "unpack", source)
    assert extracted == members
    assert {m.name: m.linkname for m in members} == dict(links)


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX fixture; native macOS acceptance runs the real signed interpreter",
)
def test_transformed_bundle_provisions_offline_with_zero_cache_and_requests(
    bundle, tmp_path, monkeypatch
):
    from support.private_python import fake_archive, host_python_version

    _, internal, archive, source = bundle
    real, sha, rel = fake_archive(
        tmp_path / "fixture", host_python=HOST_PYTHON, launches_log=tmp_path / "launches"
    )
    source = dataclasses.replace(
        source,
        sha256=sha,
        size=real.stat().st_size,
        python_rel=rel,
        version=host_python_version(HOST_PYTHON),
    )
    archive.write_bytes(real.read_bytes() + b"signed transport")
    receipt(internal, archive, source)
    fake_codesign(monkeypatch)
    monkeypatch.setattr(privatepython, "_fetch", lambda *a, **kw: pytest.fail("network requested"))
    result = privatepython.provision(source, required_origin=privatepython.ORIGIN_BUNDLED)
    assert Path(result).is_file()
    assert privatepython.read_ledger()["runtimes"][source.id]["origin"] == "bundled"
    assert list(privatepython.downloads_dir().iterdir()) == []
    assert (tmp_path / "launches").read_text(encoding="utf-8").splitlines() == ["x"]


def test_repacking_preserves_modes_directories_links_and_verifies_final_archive(
    bundle, monkeypatch
):
    app, internal, archive, source = bundle
    monkeypatch.setattr(privatepython, "source_for", lambda target=None: source)
    original = {}
    with tarfile.open(archive) as tar:
        original = {m.name: (m.type, m.mode, m.linkname) for m in tar.getmembers()}
    calls = []

    def sign(args):
        calls.append(args)
        return (
            0,
            f"TeamIdentifier={TEAM}\nTimestamp=Oct 4 2026\nCodeDirectory flags=0x10000(runtime)\n",
        )

    monkeypatch.setattr(cs, "_codesign", sign)
    assert cs.sign_private_python(app, "Developer ID", ROOT / "packaging/entitlements.plist") == 2
    with tarfile.open(archive) as tar:
        assert {m.name: (m.type, m.mode, m.linkname) for m in tar.getmembers()} == original
    signatures = [c for c in calls if "--sign" in c]
    assert len(signatures) == 2
    for command in signatures:
        assert "--timestamp" in command and command[command.index("--options") + 1] == "runtime"
        assert ("--entitlements" in command) == (Path(command[-1]).name == "python3.13")
    verified = [c for c in calls if "--verify" in c and "checked" in c[-1]]
    assert len(verified) == 2
    assert all(f'subject.OU] = "{TEAM}"' in c[c.index("-R") + 1] for c in verified)
    cs.verify_private_python(app)
    assert json.loads((internal / MANIFEST).read_text(encoding="utf-8"))["source_id"] == source.id


@pytest.mark.parametrize(
    "failure", ["unsigned", "wrong-team", "timestamp", "runtime", "arch", "min-os"]
)
def test_final_repacked_member_checks_cannot_be_skipped(bundle, tmp_path, monkeypatch, failure):
    app, _, archive, source = bundle
    tree = tmp_path / "verified"
    cs._unpack_private(archive, tree, source)
    requirement = (
        privatepython_bundle.DEVELOPER_ID_REQUIREMENT
        + f' and certificate leaf[subject.OU] = "{TEAM}"'
    )

    def check(args):
        if "--verify" in args:
            if failure == "unsigned":
                return 1, "not signed"
            if (
                failure == "wrong-team"
                and "-R" in args
                and "subject.OU" in args[args.index("-R") + 1]
            ):
                return 1, "other Developer ID team"
        return 0, ("" if failure == "timestamp" else "Timestamp=now\n") + (
            "" if failure == "runtime" else "CodeDirectory flags=0x10000(runtime)\n"
        )

    monkeypatch.setattr(cs, "_codesign", check)
    if failure == "arch":
        source = dataclasses.replace(source, target="macos-x86_64")
    elif failure == "min-os":
        monkeypatch.setattr(cs, "minos", lambda *a: (99, 0, 0))
    with pytest.raises(cs.SignError):
        cs._verify_private(app, tree, source, requirement)


def test_adhoc_build_keeps_raw_archive_usable(bundle, monkeypatch):
    app, internal, archive, source = bundle
    monkeypatch.setattr(cs, "_codesign", lambda args: (0, ""))
    cs.sign(app, "-", None)
    assert privatepython._sha256_file(archive) == source.sha256
    assert not (internal / MANIFEST).exists()
    assert privatepython.bundled_archive(source) == archive


def test_missing_snapshot_source_has_structured_error(bundle):
    _, _, archive, source = bundle
    staging = privatepython.runtimes_dir() / ".staging-test"
    staging.mkdir(parents=True)
    archive.unlink()
    with pytest.raises(privatepython.ProvisionError) as error:
        privatepython._extract_bundle(archive, staging, source)
    assert error.value.code == privatepython.ERROR_SOURCE_CHANGED


def test_snapshot_disk_error_is_structured(bundle, monkeypatch):
    _, _, archive, source = bundle

    def full(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(privatepython.tempfile, "TemporaryFile", full)
    with pytest.raises(privatepython.ProvisionError) as error:
        privatepython._extract_bundle(archive, Path("unused"), source)
    assert error.value.code == privatepython.ERROR_WRITE_FAILED


def test_smoke_is_frozen_entry_and_added_to_final_app_gate(monkeypatch):
    import ast

    from tavotto.engine import privatepython_smoke
    from test_release_workflow_contract import _Workflow

    # Emulate the Windows locale default on every host; source is UTF-8.
    original_read_text = Path.read_text

    def locale_read_text(path, encoding=None, **kwargs):
        return original_read_text(path, encoding=encoding or "cp1252", **kwargs)

    monkeypatch.setattr(Path, "read_text", locale_read_text)
    entry = ast.parse((ROOT / "packaging/entry.py").read_text(encoding="utf-8"))
    branches = [
        n
        for n in ast.walk(entry)
        if isinstance(n, ast.If)
        and any(
            isinstance(c, ast.Constant) and c.value == "--private-python-smoke"
            for c in ast.walk(n.test)
        )
    ]
    assert len(branches) == 1
    assert any(
        isinstance(n, ast.ImportFrom) and any(a.name == "privatepython_smoke" for a in n.names)
        for n in ast.walk(branches[0])
    )
    tree = ast.parse(Path(privatepython_smoke.__file__).read_text(encoding="utf-8"))
    calls = [
        n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    ]
    assert any(
        c.func.attr == "provision"
        and any(
            k.arg == "required_origin"
            and isinstance(k.value, ast.Attribute)
            and k.value.attr == "ORIGIN_BUNDLED"
            for k in c.keywords
        )
        for c in calls
    )
    assert any(c.func.attr == "create_generation_venv" for c in calls)
    workflow = _Workflow(ROOT / ".github/workflows/desktop-tauri.yml")
    gate = next(step for _, step in workflow.all_steps() if "最终 .app 冒烟" in step)
    assert '"$SIDECAR" --private-python-smoke' in gate
    assert gate.index("ditto") < gate.index("--private-python-smoke") < gate.index('rm -rf "$DEST"')


def test_source_python_cannot_claim_final_app_acceptance(monkeypatch):
    from tavotto.engine import privatepython_smoke

    monkeypatch.setattr(sys, "frozen", False, raising=False)
    with pytest.raises(RuntimeError, match="frozen macOS"):
        privatepython_smoke.main()


def test_codesign_requirements_use_literal_source_and_apple_developer_id_oids(
    transformed, monkeypatch
):
    _, _, archive, source, _, _ = transformed
    calls = fake_codesign(monkeypatch)
    assert privatepython.bundled_archive(source) == archive
    requirements = [c[c.index("-R") + 1] for c in calls if "-R" in c]
    assert len(requirements) == 3
    for value in requirements:
        # codesign otherwise reads this argument as a filename (Apple TN3127).
        assert value.startswith("=anchor apple generic and ")
        assert "certificate 1[field.1.2.840.113635.100.6.2.6] exists" in value
        assert "certificate leaf[field.1.2.840.113635.100.6.1.13] exists" in value
    assert sum(f'subject.OU] = "{TEAM}"' in value for value in requirements) == 2
