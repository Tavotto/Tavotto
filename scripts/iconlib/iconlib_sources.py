"""两库的取得通道（ADR 0222 §三）：只走许可方允许的公开地址，限速、可续、记出处。

**下载的东西一律是不可信数据**：这里只存字节，不解释、不执行；缓存目录由调用方指定（放仓库外）。
- Bioicons：GitHub raw（`icons.json` / `authors.json` 与逐个 SVG）。不碰官网。
- Reactome：`https://reactome.org/icon/R-ICO-xxxxxx.{xml,svg}`，无整包 / 列表接口，按 ID 顺序探测，间隔 `delay` 秒。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ALLOWED_HOSTS = frozenset({"raw.githubusercontent.com", "reactome.org"})
USER_AGENT = "tavotto-iconlib-build/1 (offline catalog build)"
MAX_FETCH_BYTES = 8 * 1024 * 1024
BIOICONS_RAW = "https://raw.githubusercontent.com/duerrsimon/bioicons/main/static/icons/"
REACTOME_ICON = "https://reactome.org/icon/"

#: Bioicons 里按作者名认出的原始出处（署名要写原库，不是 Bioicons）。其余算 Bioicons 自己。
BIOICONS_ORIGINS = {
    "Servier": "Servier Medical Art",
    "DBCLS": "TogoTV (DBCLS)",
    "SwissBioPics": "SwissBioPics",
    "OpenClipart": "Openclipart",
}


class Fetcher:
    """带磁盘缓存、限速、主机白名单、体积上限的 GET。命中缓存不联网、不等待。"""

    def __init__(self, cache_dir: Path, delay: float = 1.0, sleep=time.sleep):
        self.dir = Path(cache_dir) / "http"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        self._sleep = sleep
        self._last = 0.0
        self.network_calls = 0

    def _paths(self, url: str) -> tuple[Path, Path]:
        h = hashlib.sha1(url.encode()).hexdigest()
        return self.dir / f"{h}.bin", self.dir / f"{h}.json"

    def get(self, url: str) -> tuple[bytes, dict] | None:
        """返回 (字节, {url, sha256, retrieved_at})；404 / 失败返回 None（404 也缓存，续跑不再问）。"""
        host = urllib.parse.urlsplit(url).hostname or ""
        if not url.startswith("https://") or host not in ALLOWED_HOSTS:
            raise ValueError(f"host not allowed: {host}")
        body, meta = self._paths(url)
        if meta.exists():
            m = json.loads(meta.read_text("utf8"))
            if m.get("status") != 200:
                return None
            return body.read_bytes(), m
        wait = self._last + self.delay - time.monotonic()
        if wait > 0:
            self._sleep(wait)
        self._last = time.monotonic()
        self.network_calls += 1
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 — 主机白名单已核
                data = r.read(MAX_FETCH_BYTES + 1)
                status = r.status
        except urllib.error.HTTPError as e:
            if e.code == 404:
                meta.write_text(json.dumps({"url": url, "status": 404}), "utf8")
            return None
        except (urllib.error.URLError, TimeoutError, OSError):
            return None  # 网络问题不缓存，续跑时再试
        if len(data) > MAX_FETCH_BYTES:
            meta.write_text(json.dumps({"url": url, "status": 413}), "utf8")
            return None
        info = {
            "url": url,
            "status": status,
            "sha256": hashlib.sha256(data).hexdigest(),
            "retrieved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        body.write_bytes(data)
        meta.write_text(json.dumps(info), "utf8")
        return data, info


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower() or "x"


# ---- Bioicons -------------------------------------------------------------


def bioicons_candidates(fetcher: Fetcher) -> list[dict] | None:
    """`icons.json` → 候选记录（还没过许可白名单）。取不到索引返回 None。"""
    got = fetcher.get(BIOICONS_RAW + "icons.json")
    if got is None:
        return None
    try:
        items = json.loads(got[0].decode("utf8"))
    except (UnicodeDecodeError, ValueError):
        return None
    authors: dict = {}
    a = fetcher.get(BIOICONS_RAW + "authors.json")
    if a is not None:
        try:
            authors = json.loads(a[0].decode("utf8"))
        except (UnicodeDecodeError, ValueError):
            authors = {}
    out: list[dict] = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        name, cat, lic, author = (it.get(k) for k in ("name", "category", "license", "author"))
        if not all(isinstance(x, str) and x for x in (name, cat, lic, author)):
            continue
        path = f"{lic}/{cat}/{author}/{name}.svg"
        out.append(
            {
                "id": f"bioicons-{slug(lic)}-{slug(author)}-{slug(name)}",
                "source": BIOICONS_ORIGINS.get(author, "Bioicons"),
                "via": "Bioicons",
                "title": name.replace("_", " ").replace("-", " ").strip(),
                "category": cat,
                "creator": author,
                "creator_url": authors.get(author)
                if isinstance(authors.get(author), str)
                else None,
                "license_raw": lic,
                "url": BIOICONS_RAW + urllib.parse.quote(path),
                "source_url": "https://bioicons.com/",
            }
        )
    return out


def stride_pick(items: list, n: int) -> list:
    """等距抽 n 个（确定性，覆盖整个列表而不是只取开头）。"""
    if n <= 0 or not items:
        return []
    if n >= len(items):
        return list(items)
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


# ---- Reactome -------------------------------------------------------------


def parse_reactome_xml(data: bytes) -> dict | None:
    """图标 XML 元数据 → 字段。拒绝 DOCTYPE / ENTITY（不可信输入）；坏的返回 None。"""
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        return None
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None

    def text(path: str) -> str:
        e = root.find(path)
        return (e.text or "").strip() if e is not None else ""

    designer = root.find("person[@role='designer']")
    return {
        "title": text("name"),
        "description": text("description"),
        "categories": [(c.text or "").strip() for c in root.findall("categories/category")],
        "designer": (designer.text or "").strip() if designer is not None else "",
        "designer_url": (designer.get("url") or "") if designer is not None else "",
        "synonyms": [(s.text or "").strip() for s in root.findall("synonyms/synonym")],
        "references": [
            f"{(r.findtext('db') or '').strip()}:{(r.findtext('id') or '').strip()}"
            for r in root.findall("references/reference")
        ],
    }


def reactome_candidates(
    fetcher: Fetcher, start: int, end: int, want: int, max_probes: int = 400
) -> tuple[list[dict], dict]:
    """按 ID 顺序探测，收到 `want` 个非蛋白图标为止。返回 (候选, 探测统计)。

    蛋白类（Protein）整类不收（维护者 2026-11-28）；一个图标可属多个类，只要含 protein 就不收。"""
    out: list[dict] = []
    stats = {"probed": 0, "missing": 0, "bad_xml": 0, "protein_skipped": 0}
    for n in range(start, end):
        if len(out) >= want or stats["probed"] >= max_probes:
            break
        stats["probed"] += 1
        rid = f"R-ICO-{n:06d}"
        got = fetcher.get(f"{REACTOME_ICON}{rid}.xml")
        if got is None:
            stats["missing"] += 1
            continue
        meta = parse_reactome_xml(got[0])
        if meta is None or not meta["title"]:
            stats["bad_xml"] += 1
            continue
        if "protein" in meta["categories"]:
            stats["protein_skipped"] += 1
            continue
        out.append(
            {
                "id": f"reactome-{rid.lower()}",
                "source": "Reactome Icon Library",
                "via": "Reactome",
                "title": meta["title"],
                "category": (meta["categories"] or [""])[0],
                "creator": meta["designer"] or "Reactome",
                "creator_url": meta["designer_url"] or None,
                "license_raw": "CC-BY-4.0",  # 整库许可：https://reactome.org/license 1a
                "url": f"{REACTOME_ICON}{rid}.svg",
                "source_url": "https://reactome.org/icon-lib",
                "copyright_notice": "Reactome (CSHL, OICR, EBI)",
                "description": meta["description"],
                "synonyms": meta["synonyms"],
                "references": meta["references"],
            }
        )
    return out, stats
