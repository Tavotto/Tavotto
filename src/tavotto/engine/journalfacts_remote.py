"""Signed, isolated read-only v2 journal facts client.

This module does not produce v1 publication overrides or change preflight.
The v2 cache is independent from v1; the device token remains shared.
"""

from __future__ import annotations

import binascii
import hashlib
import json
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import zlib
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from . import (
    atomicio,
    config,
    contactsend,
    journalfacts_contract as contract,
    journalremote,
    tlstrust,
)

_FACTS_URLS = (
    "https://tavotto.cn/v2/journal-catalog/",
    "https://www.tavotto.com/v2/journal-catalog/",
)
_FILE = re.compile(r"^snapshots/([0-9a-f]{64})\.webp$")
MAX_INDEX_BYTES = 16 << 20
MAX_SNAPSHOT_BYTES = 8 << 20
MAX_PAGE_BYTES = 300 << 10
#: 刚同步过的签名索引多久内直接用缓存、不发请求（秒）。强制刷新（`sync(force=True)`）不受限。
INDEX_FRESH_S = 300
_now = time.time  # 测试替换


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not forward a device token, or any other header, to a redirected origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _gunzip(data: bytes, cap: int) -> bytes:
    """gzip 响应体按**解压后**大小封顶（防压缩炸弹）：解压到 cap+1 字节就停，超了当作过大。"""
    try:
        out = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(data, cap + 1)
    except zlib.error as exc:
        raise journalremote.RemoteCatalogError("bundle_invalid", "bad gzip") from exc
    if len(out) > cap:
        raise journalremote.RemoteCatalogError("bundle_invalid", "response too large")
    return out


def _http(
    method: str, url: str, cap: int, headers: dict[str, str]
) -> tuple[int, bytes, dict[str, str]]:
    """HTTPS using the shared TLS trust, with redirects refused at every origin.

    只有调用方自己带了 `Accept-Encoding: gzip` 才会收到压缩体；`Content-Encoding: gzip` 的 200 响应
    在这里解压（封顶按解压后算）。其余编码一律拒绝，不猜。"""
    if not url.startswith("https://"):
        raise journalremote.RemoteCatalogError("network", "https required")
    req = urllib.request.Request(
        url,
        method=method,
        headers={"User-Agent": journalremote._user_agent(), "Accept": "*/*", **headers},
        data=b"" if method == "POST" else None,
    )
    opener = urllib.request.build_opener(
        tlstrust.https_handler(tlstrust.client_context()), _NoRedirect()
    )
    # Trust-store and proxy setup can take time; re-check immediately before I/O.
    if journalremote.disabled():
        raise journalremote.RemoteCatalogError("disabled")
    try:
        with opener.open(req, timeout=journalremote.NETWORK_TIMEOUT_S) as response:
            status, data = response.status, response.read(cap + 1)
            coding = (response.headers.get("Content-Encoding") or "").strip().lower()
            etag = response.headers.get("ETag") or ""
    except urllib.error.HTTPError as exc:
        status, data, coding, etag = exc.code, exc.read(65536), "", ""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise journalremote.RemoteCatalogError("network", type(exc).__name__) from exc
    if status == 200:
        if coding in ("gzip", "x-gzip"):
            if len(data) > cap:  # 压缩体本身也不该比解压后的上限还大
                raise journalremote.RemoteCatalogError("bundle_invalid", "response too large")
            data = _gunzip(data, cap)
        elif coding not in ("", "identity"):
            raise journalremote.RemoteCatalogError("bundle_invalid", "unsupported encoding")
    if status == 200 and len(data) > cap:
        raise journalremote.RemoteCatalogError("bundle_invalid", "response too large")
    return status, data, ({"etag": etag} if etag else {})


_request = _http


def _call_full(
    method: str, url: str, cap: int, headers: dict[str, str]
) -> tuple[int, bytes, dict[str, str]]:
    """同 `_call`，另带回响应头（目前只有小写键 `etag`）。`_request` 可以只回 (状态码, 体)。"""
    if journalremote.disabled():
        raise journalremote.RemoteCatalogError("disabled")
    result = _request(
        method, url, cap, {"X-Tavotto-Client": journalremote.client_type(), **headers}
    )
    return (result[0], result[1], result[2] if len(result) > 2 else {})


def _call(method: str, url: str, cap: int, headers: dict[str, str]) -> tuple[int, bytes]:
    status, data, _headers = _call_full(method, url, cap, headers)
    return status, data


class FactsError(RuntimeError):
    def __init__(
        self,
        code: str,
        retry_after_s: int | None = None,
        *,
        sync_state: str | None = None,
        sync_code: str | None = None,
        expected_catalog_rev: int | None = None,
        current_catalog_rev: int | None = None,
    ):
        super().__init__(code)
        self.code = code
        self.retry_after_s = retry_after_s
        self.sync_state = sync_state
        self.sync_code = sync_code
        self.expected_catalog_rev = expected_catalog_rev
        self.current_catalog_rev = current_catalog_rev


def cache_dir() -> Path:
    return config.data_dir() / "journal-catalog-v2"


def _bases(ui_locale: str | None = None) -> tuple[str, ...]:
    locale = ui_locale or journalremote._read_json(journalremote._state_path()).get("locale")
    cn = contactsend.region_is_cn(locale if isinstance(locale, str) else None)
    return _FACTS_URLS if cn else tuple(reversed(_FACTS_URLS))


def _json(raw: bytes) -> object:
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise FactsError("invalid_json")
            out[key] = value
        return out

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _v: (_ for _ in ()).throw(FactsError("invalid_json")),
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise FactsError("invalid_json") from exc


def _open_envelope(raw: bytes, *, cap: int, bad: str) -> object:
    """验签信封（alg / key_id / payload / sig），回已解码的载荷 JSON。索引与署名文件共用这一条路，
    只用 v1 的受信公钥表；形状 / 大小不对抛 `bad`，签名与钥匙问题抛 signature_invalid / key_unknown。"""
    if not isinstance(raw, bytes) or len(raw) > cap:
        raise FactsError(bad)
    env = _json(raw)
    if not isinstance(env, dict) or set(env) != {"alg", "key_id", "payload", "sig"}:
        raise FactsError(bad)
    if env["alg"] != "ed25519" or not isinstance(env["key_id"], str):
        raise FactsError("signature_invalid")
    key64 = journalremote.TRUSTED_KEYS.get(env["key_id"])
    if not key64:
        raise FactsError("key_unknown")
    try:
        key = journalremote._b64decode(key64)
        payload = journalremote._b64decode(env["payload"])
        signature = journalremote._b64decode(env["sig"])
    except (TypeError, ValueError, binascii.Error) as exc:
        raise FactsError("signature_invalid") from exc
    if not journalremote._ed25519_verify(key, payload, signature):
        raise FactsError("signature_invalid")
    return _json(payload)


def verify_index(raw: bytes) -> dict:
    """Verify signature, schema and contents; use only the v1 trusted key authority."""
    payload = _open_envelope(raw, cap=MAX_INDEX_BYTES, bad="index_invalid")
    try:
        return contract.validate_index(payload)
    except contract.FactValidationError as exc:
        raise FactsError("index_invalid") from exc


def _connect() -> sqlite3.Connection:
    try:
        cache_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
        con = sqlite3.connect(cache_dir() / "cache.sqlite3", timeout=10, isolation_level=None)
        con.execute(
            "CREATE TABLE IF NOT EXISTS current_index (singleton INTEGER PRIMARY KEY CHECK(singleton=1), rev INTEGER NOT NULL, envelope BLOB NOT NULL)"
        )
        # 条件请求元数据：ETag 与「上次确认索引没变」的时刻，绑在某一份信封的 sha256 上（信封换了就作废）。
        con.execute(
            "CREATE TABLE IF NOT EXISTS index_meta (singleton INTEGER PRIMARY KEY CHECK(singleton=1), envelope_sha256 TEXT NOT NULL, etag TEXT, checked_at REAL NOT NULL)"
        )
        return con
    except (OSError, sqlite3.Error) as exc:
        raise FactsError("cache_error") from exc


def _row(con: sqlite3.Connection) -> tuple[bytes, dict] | None:
    try:
        row = con.execute("SELECT rev, envelope FROM current_index WHERE singleton=1").fetchone()
    except sqlite3.Error as exc:
        raise FactsError("cache_error") from exc
    if row is None:
        return None
    raw = bytes(row[1])
    index = verify_index(raw)
    if row[0] != index["catalog_rev"]:
        raise FactsError("cache_invalid")
    return raw, index


def _meta(con: sqlite3.Connection, raw: bytes) -> tuple[str | None, float] | None:
    """(etag, checked_at)；只在它记的是当前这份信封时才算数。"""
    try:
        row = con.execute(
            "SELECT envelope_sha256, etag, checked_at FROM index_meta WHERE singleton=1"
        ).fetchone()
    except sqlite3.Error as exc:
        raise FactsError("cache_error") from exc
    if row is None or row[0] != hashlib.sha256(raw).hexdigest():
        return None
    return (row[1] if isinstance(row[1], str) and row[1] else None), float(row[2])


def _write_meta(con: sqlite3.Connection, raw: bytes, etag: str | None) -> None:
    con.execute(
        "INSERT OR REPLACE INTO index_meta VALUES (1, ?, ?, ?)",
        (hashlib.sha256(raw).hexdigest(), etag, _now()),
    )


def _clean_etag(value: str | None) -> str | None:
    """服务端给的 ETag 原样存、原样回（含 `W/`）；只拒绝过长 / 含控制字符的值。"""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > 200 or any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        return None
    return value


def cached_index() -> dict | None:
    try:
        con = _connect()
        try:
            current = _row(con)
            return current[1] if current else None
        finally:
            con.close()
    except (sqlite3.Error, OSError) as exc:
        raise FactsError("cache_error") from exc


def _save_index(raw: bytes, index: dict, etag: str | None = None) -> bool:
    """Serialize compare-and-write with SQLite so concurrent clients cannot lower rev."""
    con = _connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        current = _row(con)
        if current is not None:
            old = current[1]
            if index["catalog_rev"] < old["catalog_rev"]:
                raise FactsError("rev_rollback")
            if index["catalog_rev"] == old["catalog_rev"]:
                if index != old:
                    raise FactsError("index_conflict")
                _write_meta(con, current[0], etag)
                con.commit()
                return False
        con.execute(
            "INSERT OR REPLACE INTO current_index VALUES (1, ?, ?)", (index["catalog_rev"], raw)
        )
        _write_meta(con, raw, etag)
        con.commit()
        return True
    except (sqlite3.Error, OSError) as exc:
        con.rollback()
        raise FactsError("cache_error") from exc
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _remote_error(exc: journalremote.RemoteCatalogError) -> FactsError:
    return FactsError(exc.code, exc.retry_after_s)


def _cached_with_meta() -> tuple[dict | None, str | None, float | None]:
    """已验签的缓存索引，加上它对应的 (ETag, 上次确认时刻)；元数据与信封对不上就当没有。"""
    try:
        con = _connect()
        try:
            current = _row(con)
            if current is None:
                return None, None, None
            meta = _meta(con, current[0])
            return current[1], (meta[0] if meta else None), (meta[1] if meta else None)
        finally:
            con.close()
    except (sqlite3.Error, OSError) as exc:
        raise FactsError("cache_error") from exc


def _touch_meta(index: dict) -> bool:
    """304：索引没变，只把「上次确认」记到现在。信封在这期间被别的进程换了就不动它（返回 False）。"""
    con = _connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        current = _row(con)
        if current is None or current[1] != index:
            con.rollback()
            return False
        meta = _meta(con, current[0])
        _write_meta(con, current[0], meta[0] if meta else None)
        con.commit()
        return True
    except (sqlite3.Error, OSError) as exc:
        con.rollback()
        raise FactsError("cache_error") from exc
    finally:
        con.close()


def sync(ui_locale: str | None = None, *, force: bool = False) -> dict:
    """Refresh signed index; failure preserves and returns a verified v2 cache.

    No valid v2 cache means index=None, not a v1 or seed substitution.

    省流量（索引会涨到几百 KB）：缓存里记着服务端给的 ETag，请求带 `If-None-Match` 与 `Accept-Encoding: gzip`，
    304 沿用已验签的缓存（state=unchanged）；`INDEX_FRESH_S` 内刚确认过就不发请求。`force=True` 跳过
    新鲜期（版本竞态后要看服务端现在是什么）。验签、回滚保护、离线回退语义不变：200 才验签落盘，304 不改缓存内容。
    """
    old, etag, checked = _cached_with_meta()
    if journalremote.disabled():
        return {"state": "disabled", "code": "disabled", "index": old}
    if (
        not force
        and old is not None
        and checked is not None
        and 0 <= _now() - checked < INDEX_FRESH_S  # 时钟被往回拨时 now < checked：当作过期
    ):
        return {"state": "unchanged", "code": None, "index": old}
    conditional = old is not None and etag is not None
    request_headers = {"Accept-Encoding": "gzip"}
    if conditional:
        request_headers["If-None-Match"] = etag
    last = FactsError("network")
    for base in _bases(ui_locale):
        try:
            status, raw, got = _call_full(
                "GET", base + "index.signed.json", MAX_INDEX_BYTES, request_headers
            )
            if status == 304 and conditional:
                # 缓存在请求期间被别的进程换成了更新的已验签索引时 `_touch_meta` 回 False：以当前缓存为准
                current = old if _touch_meta(old) else cached_index()
                return {"state": "unchanged", "code": None, "index": current}
            if status != 200:
                raise _remote_error(journalremote._error_of(status, raw))
            index = verify_index(raw)
            changed = _save_index(raw, index, _clean_etag(got.get("etag")))
            return {"state": "updated" if changed else "unchanged", "code": None, "index": index}
        except journalremote.RemoteCatalogError as exc:
            last = _remote_error(exc)
        except FactsError as exc:
            last = exc
        # A bad signature/schema or rollback is a security failure, not a reason
        # to silently switch origins and consume a different answer.
        if last.code not in ("network", "server_error"):
            break
    old = cached_index()
    return {
        "state": "offline" if last.code in ("network", "server_error") else "rejected",
        "code": last.code,
        "index": old,
    }


def search(
    query: str = "",
    *,
    offset: int = 0,
    limit: int = 20,
    ui_locale: str | None = None,
    expected_catalog_rev: int | None = None,
    response_fits: Callable[[dict], bool] | None = None,
) -> dict:
    """Search signed v2 index only. If unavailable, say so without substituting v1."""
    if not isinstance(query, str) or len(query) > 512:
        raise FactsError("bad_request")
    _validate_page(offset, limit)
    _validate_expected_rev(expected_catalog_rev)
    result = sync(ui_locale)
    index = result["index"]
    if index is None:
        raise FactsError(result["code"] or "offline")
    _check_expected_rev(expected_catalog_rev, index["catalog_rev"])
    import unicodedata

    needle = unicodedata.normalize("NFKC", query).casefold().strip()
    entries = []
    for jid, item in sorted(index["journals"].items()):
        names = [
            jid,
            item["label"],
            index["publishers"][item["publisher"]]["label"],
            item.get("issn", ""),
            item.get("eissn", ""),
            *item.get("aliases", []),
        ]
        if needle and not any(
            needle in unicodedata.normalize("NFKC", name).casefold() for name in names
        ):
            continue
        entries.append({"id": jid, **{k: v for k, v in item.items() if k != "bundle"}})
    total = len(entries)
    out = {
        "catalog_rev": index["catalog_rev"],
        "journals": [],
        "total": total,
        "next_offset": None,
        "source": "cache" if result["state"] in ("offline", "rejected", "disabled") else "network",
        "sync_state": result["state"],
        "sync_code": result["code"],
    }
    for entry in entries[offset : offset + limit]:
        out["journals"].append(entry)
        count = len(out["journals"])
        out["next_offset"] = offset + count if offset + count < total else None
        if not _page_fits(out) or (response_fits is not None and not response_fits(out)):
            out["journals"].pop()
            if not out["journals"]:
                raise FactsError("result_too_large")
            out["next_offset"] = offset + len(out["journals"])
            break
    return out


def listing(*, ui_locale: str | None = None) -> dict:
    """Every journal of the signed v2 index, for the journal picker.

    Reads the public signed index only (no device token, no bundle, no per-journal quota); a verified
    cache answers when the network is down. No valid index at all is an error, never a v1 substitute.
    """
    result = sync(ui_locale)
    index = result["index"]
    if index is None:
        raise FactsError(result["code"] or "offline")
    journals = []
    for jid, item in sorted(index["journals"].items()):
        journals.append(
            {
                "id": jid,
                "label": item["label"],
                "publisher": item["publisher"],
                "publisher_label": index["publishers"][item["publisher"]]["label"],
                "issn": item.get("issn", ""),
                "eissn": item.get("eissn", ""),
                "aliases": list(item.get("aliases", [])),
            }
        )
    return {
        "catalog_rev": index["catalog_rev"],
        "journals": journals,
        "sync_state": result["state"],
        "sync_code": result["code"],
    }


def _page_fits(value: dict) -> bool:
    return (
        len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        <= MAX_PAGE_BYTES
    )


def _validate_page(offset: int, limit: int) -> None:
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 20:
        raise FactsError("bad_request")


def _validate_expected_rev(expected: int | None) -> None:
    if expected is not None and (type(expected) is not int or not 1 <= expected <= 9_999_999_999):
        raise FactsError("bad_request")


def _check_expected_rev(expected: int | None, current: int) -> None:
    if expected is not None and expected != current:
        raise FactsError(
            "catalog_changed", expected_catalog_rev=expected, current_catalog_rev=current
        )


def _read_bundle(index: dict, jid: str) -> dict | None:
    entry = index["journals"].get(jid)
    if entry is None:
        raise FactsError("unknown_journal")
    path = cache_dir() / "bundles" / (entry["bundle"]["sha256"] + ".json")
    try:
        with path.open("rb") as stream:
            raw = stream.read(contract.MAX_BUNDLE_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise FactsError("cache_error") from exc
    try:
        return contract.validate_indexed_bundle(index, jid, raw)
    except contract.FactValidationError as exc:
        raise FactsError("cache_invalid") from exc


def _cached_journal(jid: str) -> tuple[dict | None, dict | None]:
    """Read index and its cached bundle under a SQLite snapshot transaction."""
    con = _connect()
    try:
        con.execute("BEGIN")
        row = _row(con)
        if row is None:
            con.commit()
            return None, None
        index = row[1]
        cached = _read_bundle(index, jid)
        con.commit()
        return index, cached
    except (sqlite3.Error, OSError) as exc:
        con.rollback()
        raise FactsError("cache_error") from exc
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def cached_bundle(jid: str) -> tuple[int, dict] | None:
    """本机已缓存且验过的 v2 刊包 `(catalog_rev, bundle)`；没有 / 缓存读不了 = None。**只读本机，不联网**
    （Agent 提交期刊线索要取「目录现值」，ADR 0213 §三）。"""
    try:
        index, bundle = _cached_journal(jid)
    except FactsError:
        return None
    if index is None or bundle is None:
        return None
    return int(index["catalog_rev"]), bundle


# --------------------------------------------------------------------------- 署名（ADR 0213 三期）
#: 署名文件（`thanks.signed.json`）：同一把钥匙签名、信封与索引相同，载荷
#: `{"schema":1,"kind":"journal_facts_thanks","catalog_rev":N,"thanks":{<journal_id>:{<fact id>:<称呼>}}}`。
#: 它**只是装饰**：取不到（老服务端没有这个文件是常态）、验签失败、`catalog_rev` 与本机已验签索引对不上、
#: 形状不对，一律当没有——不显示、不报错、不影响事实读取。称呼是用户自填、会给别的用户看的文字，这里在渲染前
#: 再校验一遍（`contactsend.clean_credit`，与服务端、入库工具同一条规则），单条不合规只丢那一条。
THANKS_FILE = "thanks.signed.json"
MAX_THANKS_BYTES = 2 << 20
MAX_THANKS_ENTRIES = 50_000
#: 没有这个文件（404 / 网络失败 / 被拒）时，同一个 catalog_rev 多久内不再问（秒）；进程内记忆，不落盘
THANKS_RETRY_S = 600
_thanks_miss: dict[int, float] = {}


def _clean_thanks(payload: object, catalog_rev: int) -> dict[str, dict[str, str]]:
    """严格校验署名载荷的形状并取出 `{journal_id: {fact_id: 称呼}}`；整体不对抛 FactsError，单条称呼不合规丢单条。"""
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema", "kind", "catalog_rev", "thanks"}
        or type(payload["schema"]) is not int
        or payload["schema"] != 1
        or payload["kind"] != "journal_facts_thanks"
        or type(payload["catalog_rev"]) is not int
        or payload["catalog_rev"] != catalog_rev
        or not isinstance(payload["thanks"], dict)
    ):
        raise FactsError("thanks_invalid")
    out: dict[str, dict[str, str]] = {}
    total = 0
    for jid, facts in payload["thanks"].items():
        if (
            not isinstance(jid, str)
            or contract._ID.fullmatch(jid) is None
            or not isinstance(facts, dict)
        ):
            raise FactsError("thanks_invalid")
        for fid, name in facts.items():
            total += 1
            if (
                total > MAX_THANKS_ENTRIES
                or not isinstance(fid, str)
                or contract._ID.fullmatch(fid) is None
            ):
                raise FactsError("thanks_invalid")
            clean = contactsend.clean_credit(name)
            if clean is not None:
                out.setdefault(jid, {})[fid] = clean
    return out


def verify_thanks(raw: bytes, catalog_rev: int) -> dict[str, dict[str, str]]:
    """验签 + 严格校验署名文件，`catalog_rev` 必须等于本机已验签索引的版本。"""
    return _clean_thanks(
        _open_envelope(raw, cap=MAX_THANKS_BYTES, bad="thanks_invalid"), catalog_rev
    )


def _cached_thanks(rev: int) -> dict[str, dict[str, str]] | None:
    """本机缓存里这一版的署名；不在 / 验不过 = None。只读盘，不联网。"""
    try:
        return verify_thanks((cache_dir() / THANKS_FILE).read_bytes(), rev)
    except (OSError, FactsError):
        return None


def _download_thanks(rev: int, ui_locale: str | None) -> None:
    """联网取这一版的署名并写进缓存；任何失败只记一次「这一版暂时没有」。在后台线程里跑。"""
    try:
        for base in _bases(ui_locale):
            try:
                status, raw, _h = _call_full(
                    "GET", base + THANKS_FILE, MAX_THANKS_BYTES, {"Accept-Encoding": "gzip"}
                )
            except (journalremote.RemoteCatalogError, FactsError):
                continue  # 这一条线路不通：试另一条
            if status != 200:
                break  # 404 = 老服务端没有这个文件；别的状态码也不换线重试
            try:
                verify_thanks(raw, rev)
            except FactsError:
                break  # 验签 / 形状 / 版本不对：不显示，也不换线路去拿另一份答案
            path = cache_dir() / THANKS_FILE
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            atomicio.write_bytes(path, raw, mode=0o600)
            return
        _thanks_miss[rev] = _now()
    except Exception:  # noqa: BLE001 - 后台装饰任务，任何意外都只当「没有」
        _thanks_miss[rev] = _now()
    finally:
        with _thanks_lock:
            _thanks_inflight.discard(rev)


def _spawn(fn) -> None:
    threading.Thread(target=fn, name="journal-thanks", daemon=True).start()


_thanks_lock = threading.Lock()
_thanks_inflight: set[int] = set()


def _refresh_thanks(rev: int, ui_locale: str | None) -> None:
    """缓存没有这一版时在后台取一次（同一版本同时只取一个；没取到的 10 分钟内不再问）。
    预检那条路只读缓存、从不等网络：署名是装饰，不许拖慢本来全在本机的事实读取。"""
    if journalremote.disabled():
        return
    missed = _thanks_miss.get(rev)
    if missed is not None and 0 <= _now() - missed < THANKS_RETRY_S:
        return
    with _thanks_lock:
        if rev in _thanks_inflight:
            return
        _thanks_inflight.add(rev)
    _spawn(lambda: _download_thanks(rev, ui_locale))


def thanks_for(jid: str, *, ui_locale: str | None = None) -> dict[str, str]:
    """某一刊 v2 事实的署名 `{fact id: 称呼}`；任何问题都回 `{}`，**永不抛、从不等网络**。
    只读本机缓存；缓存没有这一版就在后台取，下一次读取再显示。只认本机已验签索引的版本。"""
    try:
        index = cached_index()
        if index is None:
            return {}
        rev = int(index["catalog_rev"])
        names = _cached_thanks(rev)
        if names is None:
            _refresh_thanks(rev, ui_locale)
            names = _cached_thanks(rev)  # 测试里后台任务同步跑完；线上这里通常还是 None
        return dict(names.get(jid, {})) if names else {}
    except Exception:  # noqa: BLE001 - 署名只是装饰，任何意外都不许碰事实读取
        return {}


def _token_call(base: str, route: str, cap: int) -> bytes:
    if journalremote.disabled():
        raise FactsError("offline")
    tok = journalremote._token(base)
    status, raw = _call("GET", base + route, cap, {"X-Tavotto-Device": tok})
    if status == 401:
        err = journalremote._error_of(status, raw)
        if err.code == "device_unknown":
            tok = journalremote._token(base, renew=True, stale=tok)
            status, raw = _call("GET", base + route, cap, {"X-Tavotto-Device": tok})
    if status != 200:
        raise journalremote._error_of(status, raw)
    return raw


def get_journal(jid: str, *, ui_locale: str | None = None, _allow_refresh: bool = True) -> dict:
    """Return source facts and provenance, never a preflight profile or v1 fallback."""
    if not isinstance(jid, str) or re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,95}", jid) is None:
        raise FactsError("bad_request")
    index, cached = _cached_journal(jid)
    if index is None:
        state = sync(ui_locale)
        if state["index"] is None:
            raise FactsError(state["code"] or "offline")
        index, cached = _cached_journal(jid)
        if index is None:
            raise FactsError("cache_invalid")
    if cached is not None:
        return {"catalog_rev": index["catalog_rev"], "source": "cache", "bundle": cached}
    if journalremote.disabled():
        raise FactsError("offline")
    last = FactsError("network")
    for base in _bases(ui_locale):
        try:
            rev = index["catalog_rev"]
            raw = _token_call(
                base, f"journals/{quote(jid, safe='')}?rev={rev}", contract.MAX_BUNDLE_BYTES
            )
            try:
                contract.validate_indexed_bundle(index, jid, raw)
            except contract.FactValidationError as exc:
                raise FactsError("bundle_invalid") from exc
            # Re-read under the same write transaction used by sync: a new index
            # cannot race an older bundle into the current cache.
            con = _connect()
            try:
                con.execute("BEGIN IMMEDIATE")
                row = _row(con)
                if row is None:
                    raise FactsError("cache_invalid")
                latest = row[1]
                try:
                    checked = contract.validate_indexed_bundle(latest, jid, raw)
                except contract.FactValidationError as exc:
                    raise FactsError("stale_rev") from exc
                digest = latest["journals"][jid]["bundle"]["sha256"]
                path = cache_dir() / "bundles" / (digest + ".json")
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                atomicio.write_bytes(path, raw, mode=0o600)
                con.commit()
                return {
                    "catalog_rev": latest["catalog_rev"],
                    "source": "network",
                    "bundle": checked,
                }
            finally:
                con.close()
        except journalremote.RemoteCatalogError as exc:
            last = _remote_error(exc)
        except (OSError, sqlite3.Error) as exc:
            raise FactsError("cache_error") from exc
        except FactsError as exc:
            last = exc
        if last.code not in ("network", "server_error"):
            break
    if last.code == "stale_rev" and _allow_refresh:
        state = sync(ui_locale, force=True)  # 版本竞态：不能被新鲜期挡回缓存
        fresh = state["index"]
        if fresh is not None and fresh["catalog_rev"] > index["catalog_rev"]:
            return get_journal(jid, ui_locale=ui_locale, _allow_refresh=False)
    raise last


def get_facts(
    jid: str,
    *,
    offset: int = 0,
    limit: int = 20,
    ui_locale: str | None = None,
    expected_catalog_rev: int | None = None,
    response_fits: Callable[[dict], bool] | None = None,
) -> dict:
    """Page facts and scoped coverage together, with all referenced sources."""
    _validate_page(offset, limit)
    _validate_expected_rev(expected_catalog_rev)
    if not isinstance(jid, str) or re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,95}", jid) is None:
        raise FactsError("bad_request")
    # Refresh the public signed index even when the journal is already cached.
    # No journal ID or device token is sent with this request. On failure, sync
    # returns a verified cache and exposes its status to the caller.
    state = sync(ui_locale)
    if state["index"] is not None:
        _check_expected_rev(expected_catalog_rev, state["index"]["catalog_rev"])
    if state["index"] is None or (
        jid not in state["index"]["journals"] and state["state"] not in ("updated", "unchanged")
    ):
        raise FactsError(
            state["code"] or "offline", sync_state=state["state"], sync_code=state["code"]
        )
    try:
        result = get_journal(jid, ui_locale=ui_locale)
    except FactsError as exc:
        # An index refresh can remove the journal after sync but before its
        # bundle is read. Do not misreport a version race as a missing journal.
        # Integrity/cache errors remain errors and are never hidden by a race.
        if expected_catalog_rev is not None and exc.code in ("unknown_journal", "stale_rev"):
            current = cached_index()
            if current is not None:
                _check_expected_rev(expected_catalog_rev, current["catalog_rev"])
        raise
    _check_expected_rev(expected_catalog_rev, result["catalog_rev"])
    bundle = result["bundle"]
    facts = bundle["facts"]
    coverage = bundle.get("coverage", [])
    records = [("facts", fact) for fact in facts] + [("coverage", item) for item in coverage]
    total = len(records)
    out = {
        "catalog_rev": result["catalog_rev"],
        "source": result["source"],
        "journal_id": bundle["journal_id"],
        "journal": bundle["journal"],
        "catalog_completeness": "unknown",
        "sync_state": state["state"],
        "sync_code": state["code"],
        "facts": [],
        "coverage": [],
        "sources": {},
        "facts_total": len(facts),
        "coverage_total": len(coverage),
        "total": total,
        "next_offset": None,
    }
    count = 0
    for kind, record in records[offset : offset + limit]:
        out[kind].append(record)
        refs = (
            [record["source"], *(item["source"] for item in record.get("context", []))]
            if kind == "facts"
            else record["sources"]
        )
        for sid in refs:
            out["sources"][sid] = bundle["sources"][sid]
        out["next_offset"] = offset + count + 1 if offset + count + 1 < total else None
        if not _page_fits(out) or (response_fits is not None and not response_fits(out)):
            out[kind].pop()
            source_ids = {f["source"] for f in out["facts"]}
            source_ids.update(item["source"] for f in out["facts"] for item in f.get("context", []))
            source_ids.update(sid for item in out["coverage"] for sid in item["sources"])
            out["sources"] = {sid: bundle["sources"][sid] for sid in sorted(source_ids)}
            if not count:
                raise FactsError("result_too_large")
            out["next_offset"] = offset + count
            break
        count += 1
    return out


def snapshot(jid: str, file: str, *, ui_locale: str | None = None) -> Path:
    """Fetch only snapshots referenced by this validated journal; verify every read."""
    match = _FILE.fullmatch(file) if isinstance(file, str) else None
    if match is None:
        raise FactsError("bad_request")
    result = get_journal(jid, ui_locale=ui_locale)
    referenced = any(f.get("snapshot", {}).get("file") == file for f in result["bundle"]["facts"])
    if not referenced:
        raise FactsError("unknown_snapshot")
    path = cache_dir() / file
    if path.is_file():
        try:
            with path.open("rb") as stream:
                data = stream.read(MAX_SNAPSHOT_BYTES + 1)
        except OSError as exc:
            raise FactsError("cache_error") from exc
        if len(data) <= MAX_SNAPSHOT_BYTES and hashlib.sha256(data).hexdigest() == match.group(1):
            return path
        raise FactsError("cache_invalid")
    if journalremote.disabled():
        raise FactsError("offline")
    last = FactsError("network")
    for base in _bases(ui_locale):
        try:
            raw = _token_call(base, file, MAX_SNAPSHOT_BYTES)
            if hashlib.sha256(raw).hexdigest() != match.group(1):
                raise FactsError("snapshot_invalid")
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            atomicio.write_bytes(path, raw, mode=0o600)
            return path
        except journalremote.RemoteCatalogError as exc:
            last = _remote_error(exc)
        except OSError as exc:
            raise FactsError("cache_error") from exc
        except FactsError as exc:
            last = exc
        if last.code not in ("network", "server_error"):
            break
    raise last


_INSPECTION_KINDS = frozenset({"line_art", "halftone", "combination"})
_INSPECTION_STAGES = frozenset({"submission", "revision", "accepted", "production"})


def _structured_conditions(fact: dict, kind: str, stage: str) -> dict:
    """Compare a tiny explicit vocabulary; never infer the source's meaning."""
    reasons = []
    parts = []
    source_stage = fact.get("stage")
    if stage not in _INSPECTION_STAGES or source_stage not in _INSPECTION_STAGES | {"any"}:
        parts.append({"field": "stage", "status": "unknown"})
    else:
        parts.append(
            {
                "field": "stage",
                "status": "match" if source_stage in ("any", stage) else "mismatch",
            }
        )
    conditions = fact.get("conditions", {})
    if not isinstance(conditions, dict):
        reasons.append("invalid_conditions")
        conditions = {}
    for key, value in conditions.items():
        if key != "figure_kind" or kind not in _INSPECTION_KINDS:
            parts.append({"field": key, "status": "unknown"})
            continue
        if isinstance(value, str) and value in _INSPECTION_KINDS:
            candidates = [value]
        elif (
            isinstance(value, dict)
            and set(value) == {"relation", "value"}
            and value["relation"] == "in"
            and isinstance(value["value"], list)
            and value["value"]
            and all(isinstance(v, str) and v in _INSPECTION_KINDS for v in value["value"])
        ):
            candidates = value["value"]
        else:
            parts.append({"field": key, "status": "unknown"})
            continue
        parts.append({"field": key, "status": "match" if kind in candidates else "mismatch"})
    if kind not in _INSPECTION_KINDS:
        reasons.append("figure_kind_unknown")
    if fact.get("condition_text"):
        reasons.append("free_text_condition")
    if fact.get("context"):
        reasons.append("source_context_uninterpreted")
    statuses = {part["status"] for part in parts}
    status = (
        "unknown"
        if reasons or "unknown" in statuses
        else "mismatch"
        if "mismatch" in statuses
        else "match"
    )
    return {"status": status, "components": parts, "unknown_reasons": reasons}


def inspect_facts_page(page: dict, selection: dict) -> dict:
    """Compare structured fields only; signed bytes do not prove source semantics."""
    if not isinstance(selection, dict) or set(selection) - {"object_label", "figure_kind", "stage"}:
        raise FactsError("bad_request")
    label = selection.get("object_label")
    kind = selection.get("figure_kind", "unknown")
    stage = selection.get("stage", "unknown")
    if (
        not isinstance(label, str)
        or not label.strip()
        or len(label) > 128
        or not isinstance(kind, str)
        or kind not in _INSPECTION_KINDS | {"unknown"}
        or not isinstance(stage, str)
        or stage not in _INSPECTION_STAGES | {"unknown"}
    ):
        raise FactsError("bad_request")
    if not isinstance(page, dict) or not isinstance(page.get("facts"), list):
        raise FactsError("bad_request")
    result = dict(page)
    result["inspection"] = {
        "object_label": label,
        "figure_kind": kind,
        "stage": stage,
        "semantic_review": "not_authenticated",
        "status": "not_verifiable",
        "facts": [
            {
                "id": fact["id"],
                "status": "not_verifiable",
                "reason": "semantic_review_not_authenticated",
                "structured_conditions": _structured_conditions(fact, kind, stage),
            }
            for fact in page["facts"]
        ],
    }
    return result
