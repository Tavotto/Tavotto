"""U02 runtime_spike 的快速看护（`scripts/dev/u02_spikes/runtime_spike.py` + `hashcheck.py`）。

真跑的 spike 要下载 uv / CPython / wheel（~80 MB，评审在 evidence 的 report 里）；这里只量三样
**不用联网**就能量的：

* `hashcheck`：坏 hash 拒绝（篡改的字节 / 登记错的期望值）、下载到 `.part` 校验过才改名、
  越出私有目录（含软链接逃逸）拒绝；
* 钉法的**单一出处**：macOS 目标的 CPython 来源逐字等于 `packaging/runtime-lock.json`，spike 自己
  补的表只有锁里没有的目标；wheel 版本与 `dependency_declarations` 夹具同一组；
* provisioning 的控制流：拿一个本地假归档（一个会打印 JSON 的 `python3` 脚本）走
  staging → 真起一次 → 原子改名 → active 指针；坏 hash 那条路上**没有解释器被执行**、最终目录不存在。

本机真跑出的 report 只核「存在、all_ok、来源与锁一致」——那份 report 是 evidence，不是这里重新
生产的结论。
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from dev.u02_spikes import hashcheck, runtime_spike  # noqa: E402

EVIDENCE = ROOT / "docs" / "implementation" / "tavotto-foundation" / "evidence" / "u02" / "runtime"
LOCK = json.loads((ROOT / "packaging" / "runtime-lock.json").read_text(encoding="utf-8"))
HEX64 = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# hashcheck
# ---------------------------------------------------------------------------
def test_verify_sha256_accepts_matching_bytes_and_returns_the_path(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    digest = hashcheck.sha256_file(f)
    assert hashcheck.verify_sha256(f, digest) == f
    assert hashcheck.verify_sha256(f, digest.upper()) == f  # 大小写不算差异


def test_verify_sha256_refuses_tampered_bytes_and_wrong_expectation(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    digest = hashcheck.sha256_file(f)
    f.write_bytes(b"hellO")
    with pytest.raises(hashcheck.HashMismatch) as ei:
        hashcheck.verify_sha256(f, digest)
    assert ei.value.expected == digest and ei.value.got == hashcheck.sha256_file(f)
    with pytest.raises(hashcheck.HashMismatch):
        hashcheck.verify_sha256(f, "0" * 64)
    with pytest.raises(ValueError):
        hashcheck.verify_sha256(f, "not-a-hash")


def test_download_verified_only_renames_after_the_hash_checks_out(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"payload" * 100)
    good = hashcheck.sha256_file(src)
    dest = tmp_path / "dl" / "got.bin"
    url = src.as_uri()
    assert hashcheck.download_verified(url, dest, good, attempts=1) == dest
    assert dest.read_bytes() == src.read_bytes()
    assert not dest.with_name("got.bin.part").exists()
    # 坏 hash：拿不到 dest，也不留 .part
    dest2 = tmp_path / "dl" / "bad.bin"
    with pytest.raises(hashcheck.HashMismatch):
        hashcheck.download_verified(url, dest2, "0" * 64, attempts=1)
    assert not dest2.exists() and not dest2.with_name("bad.bin.part").exists()
    # 已有但被篡改的 dest：不复用，重新下并校验
    dest.write_bytes(b"tampered")
    assert hashcheck.download_verified(url, dest, good, attempts=1) == dest
    assert dest.read_bytes() == src.read_bytes()


def test_download_verified_reports_transport_failure_without_leaving_a_part_file(tmp_path):
    dest = tmp_path / "dl" / "nope.bin"
    with pytest.raises(OSError):
        hashcheck.download_verified((tmp_path / "missing.bin").as_uri(), dest, "0" * 64, attempts=2)
    assert not dest.exists() and not dest.with_name("nope.bin.part").exists()


def test_sha256_text_lf_ignores_line_endings_but_not_content(tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_bytes(b'{"x": 1}\n{"y": 2}\n')
    b.write_bytes(b'{"x": 1}\r\n{"y": 2}\r\n')
    assert hashcheck.sha256_text_lf(a) == hashcheck.sha256_text_lf(b)
    assert hashcheck.sha256_file(a) != hashcheck.sha256_file(b)
    b.write_bytes(b'{"x": 1}\r\n{"y": 3}\r\n')
    assert hashcheck.sha256_text_lf(a) != hashcheck.sha256_text_lf(b)


def test_assert_under_uses_realpath_so_symlink_escapes_are_refused(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    inside = root / "x" / "y"
    inside.mkdir(parents=True)
    assert hashcheck.assert_under(inside, root) == Path(os.path.realpath(inside))
    with pytest.raises(hashcheck.OutsidePrivateDir):
        hashcheck.assert_under(tmp_path / "elsewhere", root)
    if os.name != "nt":
        link = root / "escape"
        link.symlink_to(tmp_path)
        with pytest.raises(hashcheck.OutsidePrivateDir):
            hashcheck.assert_under(link / "anything", root)


# ---------------------------------------------------------------------------
# 钉法的单一出处
# ---------------------------------------------------------------------------
def test_macos_python_source_is_read_from_the_runtime_lock_not_duplicated():
    for target in ("macos-arm64", "macos-x86_64"):
        src = runtime_spike.python_source(target, LOCK)
        t = LOCK["targets"][target]["python"]
        assert src["source"] == "packaging/runtime-lock.json"
        assert (src["url"], src["sha256"], src["triple"]) == (t["url"], t["sha256"], t["triple"])
    assert set(runtime_spike.PBS_EXTRA) & set(LOCK["targets"]) == set(), (
        "spike 自己的表不许覆盖锁文件已有的目标"
    )
    for key, e in runtime_spike.PBS_EXTRA.items():
        assert HEX64.match(e["sha256"]), key
    assert runtime_spike.PBS_RELEASE == LOCK["targets"]["macos-arm64"]["python"]["release"]


def test_provisioner_and_wheel_pins_are_complete_and_hex():
    for target, w in runtime_spike.UV_WHEELS.items():
        assert runtime_spike.UV_VERSION in w["file"], target
        assert HEX64.match(w["sha256"]), target
    assert {"macos-arm64", "linux-x86_64", "windows-amd64"} <= set(runtime_spike.UV_WHEELS)
    fixture = json.loads(
        (
            ROOT / "tests" / "fixtures" / "foundation" / "dependency_declarations" / "truth.json"
        ).read_text(encoding="utf-8")
    )
    want = {r["name"]: r["spec"][2:] for r in fixture["files"]["requirements.txt"]}
    assert {w["name"]: w["version"] for w in runtime_spike.WHEELS} == want
    for w in runtime_spike.WHEELS:
        assert HEX64.match(w["sha256"]) and w["file"].endswith(".whl")


def test_windows_target_uses_the_full_pbs_python_not_the_embeddable_for_provisioning():
    """embeddable 没有 venv / ensurepip（spike 静态检查的结论），所以 provisioning 的来源在 Windows 上
    是 pbs 的 install_only；embeddable 只在 `inspect_embeddable` 里被检查，不被当来源。"""
    src = runtime_spike.python_source("windows-amd64", LOCK)
    assert src["kind"] == "pbs-install_only" and src["triple"] == "x86_64-pc-windows-msvc"
    assert src["python_rel"] == "python.exe"
    assert LOCK["targets"]["windows-amd64"]["kind"] == "windows-embeddable"


# ---------------------------------------------------------------------------
# provisioning 控制流（本地假归档，不联网）
# ---------------------------------------------------------------------------
FAKE_PY = (
    '#!/bin/sh\nprintf \'%s\' \'{"version": "3.13.15 fake", "prefix": "x", "executable": "y"}\'\n'
)


def _fake_archive(path: Path) -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = FAKE_PY.encode()
        info = tarfile.TarInfo("python/bin/python3")
        info.size = len(data)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(data))
        info = tarfile.TarInfo("python/lib/marker.txt")
        info.size = 2
        tar.addfile(info, io.BytesIO(b"ok"))
    path.write_bytes(buf.getvalue())
    return hashcheck.sha256_file(path)


@pytest.mark.skipif(os.name == "nt", reason="假解释器是 sh 脚本；Windows 上没有 sh")
def test_provisioning_goes_through_staging_and_publishes_atomically(tmp_path):
    data_dir = tmp_path / "data"
    home = tmp_path / "home"
    data_dir.mkdir()
    home.mkdir()
    spike = runtime_spike.Spike(tmp_path / "out", data_dir, home, None, "linux-x86_64")
    archive = tmp_path / "fake.tar.gz"
    digest = _fake_archive(archive)
    src = {"url": "file:///fake", "sha256": digest, "python_rel": "bin/python3"}
    python = spike.provision_python(src, archive_override=archive, name="fake")
    final = data_dir / "runtimes" / f"fake-{digest[:12]}"
    assert python == final / "bin" / "python3"
    assert python.is_file() and spike.interpreter_executions == 1
    assert not any(p.name.startswith(".staging") for p in (data_dir / "runtimes").iterdir())
    assert not any(p.name.startswith(".active") for p in (data_dir / "runtimes").iterdir())
    active = json.loads((data_dir / "runtimes" / "active.json").read_text(encoding="utf-8"))
    assert active == {"active": final.name, "sha256": digest, "url": "file:///fake"}


@pytest.mark.skipif(os.name == "nt", reason="假解释器是 sh 脚本；Windows 上没有 sh")
def test_replacing_a_runtime_never_deletes_the_one_in_use_and_only_switches_the_pointer(tmp_path):
    """同名重跑：同一份字节 → 同一个目录，直接复用（不重解，目录里的标记文件还在）；不同字节 →
    **新目录**就位后才切指针，旧目录原样还在（正在用它的消费者看不到「解释器消失」）。
    第一版在 `os.replace` 之前 `rmtree(final)`——Codex 在 #455 指出那不是原子发布。"""
    data_dir = tmp_path / "data"
    home = tmp_path / "home"
    data_dir.mkdir()
    home.mkdir()
    spike = runtime_spike.Spike(tmp_path / "out", data_dir, home, None, "linux-x86_64")
    archive = tmp_path / "fake.tar.gz"
    digest = _fake_archive(archive)
    src = {"url": "file:///fake", "sha256": digest, "python_rel": "bin/python3"}
    first = spike.provision_python(src, archive_override=archive, name="fake")
    marker = first.parent.parent / "marker-from-consumer"
    marker.write_text("in use", encoding="utf-8")
    # 同一份字节再来一次：复用，不重解（标记还在），指针不变，但仍真起了一次
    again = spike.provision_python(src, archive_override=archive, name="fake")
    assert again == first and marker.read_text(encoding="utf-8") == "in use"
    assert spike.interpreter_executions == 2
    # 不同字节（第二份归档多一个文件）：新目录，旧目录原样，指针切到新的
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = FAKE_PY.encode()
        info = tarfile.TarInfo("python/bin/python3")
        info.size = len(data)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(data))
        info = tarfile.TarInfo("python/lib/v2.txt")
        info.size = 2
        tar.addfile(info, io.BytesIO(b"v2"))
    archive2 = tmp_path / "fake2.tar.gz"
    archive2.write_bytes(buf.getvalue())
    digest2 = hashcheck.sha256_file(archive2)
    assert digest2 != digest
    second = spike.provision_python(
        {"url": "file:///fake2", "sha256": digest2, "python_rel": "bin/python3"},
        archive_override=archive2,
        name="fake",
    )
    assert second != first and second.is_file()
    assert first.is_file() and marker.read_text(encoding="utf-8") == "in use", "旧 runtime 被动了"
    active = json.loads((data_dir / "runtimes" / "active.json").read_text(encoding="utf-8"))
    assert active["active"] == second.parent.parent.name and active["sha256"] == digest2


@pytest.mark.skipif(os.name == "nt", reason="假解释器是 sh 脚本；Windows 上没有 sh")
def test_active_pointer_is_replaced_atomically(tmp_path, monkeypatch):
    """指针文件只经 tmp + os.replace 落盘：让 os.replace 在切指针那一下失败，磁盘上必须还是上一个
    完整的 active.json，而不是半个 / 空的。"""
    data_dir = tmp_path / "data"
    home = tmp_path / "home"
    data_dir.mkdir()
    home.mkdir()
    spike = runtime_spike.Spike(tmp_path / "out", data_dir, home, None, "linux-x86_64")
    archive = tmp_path / "fake.tar.gz"
    digest = _fake_archive(archive)
    src = {"url": "file:///fake", "sha256": digest, "python_rel": "bin/python3"}
    spike.provision_python(src, archive_override=archive, name="fake")
    pointer = data_dir / "runtimes" / "active.json"
    before = pointer.read_bytes()
    real_replace = os.replace

    def failing_replace(a, b):
        if str(b).endswith("active.json"):
            raise OSError("模拟：切指针那一下磁盘满")
        return real_replace(a, b)

    monkeypatch.setattr(runtime_spike.os, "replace", failing_replace)
    with pytest.raises(OSError):
        spike._switch_active(data_dir / "runtimes", "whatever", "0" * 64, "file:///x")
    assert pointer.read_bytes() == before, "指针被半写了"
    assert json.loads(before)["active"].startswith("fake-")


@pytest.mark.skipif(os.name == "nt", reason="假解释器是 sh 脚本；Windows 上没有 sh")
def test_bad_hash_refuses_before_extracting_or_executing_anything(tmp_path):
    data_dir = tmp_path / "data"
    home = tmp_path / "home"
    data_dir.mkdir()
    home.mkdir()
    spike = runtime_spike.Spike(tmp_path / "out", data_dir, home, None, "linux-x86_64")
    archive = tmp_path / "fake.tar.gz"
    digest = _fake_archive(archive)
    src = {"url": "file:///fake", "sha256": digest, "python_rel": "bin/python3"}
    tampered = tmp_path / "tampered.tar.gz"
    raw = bytearray(archive.read_bytes())
    raw[-10] ^= 0xFF
    tampered.write_bytes(bytes(raw))
    with pytest.raises(hashcheck.HashMismatch):
        spike.provision_python(src, archive_override=tampered, name="bad")
    with pytest.raises(hashcheck.HashMismatch):
        spike.provision_python(
            src, archive_override=archive, expected_override="0" * 64, name="bad2"
        )
    assert spike.interpreter_executions == 0
    assert not (data_dir / "runtimes").exists() or list((data_dir / "runtimes").iterdir()) == []
    assert not (data_dir / "runtimes" / "active.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="假解释器是 sh 脚本；Windows 上没有 sh")
def test_an_interpreter_that_does_not_start_leaves_no_published_runtime(tmp_path):
    data_dir = tmp_path / "data"
    home = tmp_path / "home"
    data_dir.mkdir()
    home.mkdir()
    spike = runtime_spike.Spike(tmp_path / "out", data_dir, home, None, "linux-x86_64")
    archive = tmp_path / "broken.tar.gz"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"#!/bin/sh\nexit 7\n"
        info = tarfile.TarInfo("python/bin/python3")
        info.size = len(data)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(data))
    archive.write_bytes(buf.getvalue())
    src = {
        "url": "file:///broken",
        "sha256": hashcheck.sha256_file(archive),
        "python_rel": "bin/python3",
    }
    with pytest.raises(runtime_spike.SpikeFailure):
        spike.provision_python(src, archive_override=archive, name="broken")
    assert not any(p.name.startswith("broken") for p in (data_dir / "runtimes").iterdir())
    assert not any(p.name.startswith(".staging") for p in (data_dir / "runtimes").iterdir())
    assert not (data_dir / "runtimes" / "active.json").exists()


# ---------------------------------------------------------------------------
# 本机真跑出的 evidence
# ---------------------------------------------------------------------------
def test_macos_arm64_report_exists_all_ok_and_matches_the_lock():
    report = json.loads((EVIDENCE / "report-macos-arm64.json").read_text(encoding="utf-8"))
    assert report["all_ok"] is True and report["target"] == "macos-arm64"
    assert report["python_source"]["sha256"] == LOCK["targets"]["macos-arm64"]["python"]["sha256"]
    # 内容 hash（LF 归一化）：锁文件没钉 eol=lf，Windows 检出是 CRLF，字节 hash 会假红（windows-latest 腿实测）
    assert report["lock_file"]["sha256_lf"] == hashcheck.sha256_text_lf(
        ROOT / "packaging" / "runtime-lock.json"
    ), "锁文件变了，report 是按旧锁跑的：重跑 runtime_spike"
    names = [s["step"] for s in report["steps"]]
    for must in (
        "install.offline_from_wheelhouse_with_dead_proxy",
        "install.negative_empty_wheelhouse_fails",
        "install.network_blocked_control",
        "negative.tampered_archive_refused_before_any_execution",
        "negative.wrong_expected_hash_refused_before_any_execution",
        "embeddable.static_inspection",
        "isolation.home_untouched_and_path_unchanged",
    ):
        assert must in names, must
    emb = next(s for s in report["steps"] if s["step"] == "embeddable.static_inspection")
    assert (
        emb["has_pth"] is True and emb["has_venv_module"] is False and emb["has_ensurepip"] is False
    )
    assert report["provisioner"] == {
        "name": "uv",
        "version": runtime_spike.UV_VERSION,
        "license": runtime_spike.UV_LICENSE,
        "wheel": runtime_spike.UV_WHEELS["macos-arm64"],
    }
