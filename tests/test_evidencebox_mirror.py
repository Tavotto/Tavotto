"""证据机镜像补齐：只换来源，钉住的 sha256 不变（ADR 0166）。"""

from __future__ import annotations

import hashlib
import io
import sys
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "evidencebox"))

import mirror_fill as M  # noqa: E402

COMMIT = "a" * 40
RAW = f"https://raw.githubusercontent.com/o/r/{COMMIT}/dir/f.otf"
CDN = f"https://cdn.jsdelivr.net/gh/o/r@{COMMIT}/dir/f.otf"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _serve(monkeypatch, body: bytes | Exception, seen: list[str] | None = None):
    def fake(url, timeout=None):
        if seen is not None:
            seen.append(url)
        if isinstance(body, Exception):
            raise body
        return io.BytesIO(body)

    monkeypatch.setattr(M.fetch_fonts.urllib.request, "urlopen", fake)
    monkeypatch.setattr(M.fetch_fonts.time, "sleep", lambda _s: None)


def test_mirror_url_needs_a_raw_address_pinned_to_a_full_commit():
    assert M.mirror_url(RAW) == CDN
    assert M.mirror_url("https://raw.githubusercontent.com/o/r/main/dir/f.otf") is None
    assert M.mirror_url("https://github.com/o/r/files/1/f.tar.gz") is None
    assert M.mirror_url("") is None


def test_font_jobs_cover_only_missing_raw_files(tmp_path):
    (tmp_path / "noto").mkdir()
    (tmp_path / "noto" / "have.otf").write_bytes(b"x")
    allowlist = {
        "faces": {
            "have": {"file": "noto/have.otf", "sha256": "1" * 64, "source": {"url": RAW}},
            "miss": {"file": "noto/miss.otf", "sha256": "2" * 64, "source": {"url": RAW}},
            "tar": {
                "file": "lib/a.ttf",
                "sha256": "3" * 64,
                "source": {"url": "https://github.com/o/r/files/1/a.tar.gz"},
            },
        },
        "license_files": {
            "noto/LICENSE": {"url": RAW, "sha256": "4" * 64, "file_sha256": "5" * 64},
        },
    }
    assert M.font_jobs(tmp_path, allowlist) == [
        (CDN, tmp_path / "noto" / "miss.otf", "2" * 64),
        (CDN, tmp_path / "noto" / "LICENSE", "5" * 64),
    ]


def test_research_jobs_quote_paths_and_skip_cached_files(tmp_path, monkeypatch):
    directory = tmp_path / "o" / "r" / COMMIT
    (directory / "a").mkdir(parents=True)
    (directory / "a" / "cached.py").write_text("x")
    monkeypatch.setattr(M.sources, "project_directory", lambda _p, _c: directory)
    project = {
        "upstream": {"repository": "o/r", "commit": COMMIT},
        "upstream_path_map": {"a/cached.py": "one", "a/b c.py": "two"},
        "files": {"one": "1" * 64, "two": "2" * 64},
    }
    assert M.research_jobs(project, tmp_path) == [
        (
            f"https://cdn.jsdelivr.net/gh/o/r@{COMMIT}/a/b%20c.py",
            directory / "a" / "b c.py",
            "2" * 64,
        )
    ]


def test_verified_bytes_are_published(tmp_path, monkeypatch):
    _serve(monkeypatch, b"font")
    target = tmp_path / "noto" / "f.otf"
    assert M._run([(CDN, target, _sha(b"font"))]) == 0
    assert target.read_bytes() == b"font"


def test_tampered_mirror_bytes_are_never_published_and_never_retried(tmp_path, monkeypatch):
    seen: list[str] = []
    _serve(monkeypatch, b"tampered", seen)
    target = tmp_path / "f.otf"
    assert M._run([(CDN, target, _sha(b"font"))]) == 1
    assert list(tmp_path.iterdir()) == []
    assert seen == [CDN]


def test_transport_failure_is_counted_not_raised(tmp_path, monkeypatch):
    _serve(monkeypatch, urllib.error.URLError("reset"))
    assert M._run([(CDN, tmp_path / "f.otf", _sha(b"font"))]) == 1
    assert list(tmp_path.iterdir()) == []


def test_every_pinned_raw_font_in_the_allowlist_has_a_mirror():
    allowlist = M.fetch_fonts.load_allowlist()
    urls = [f["source"].get("url", "") for f in allowlist["faces"].values()]
    raw = [u for u in urls if u.startswith("https://raw.githubusercontent.com/")]
    assert raw, "allowlist 里没有 raw 来源了，这条用例要跟着改"
    assert all(M.mirror_url(u) for u in raw)
