"""读包索引的 simple 页：某个包有哪些文件（版本 / wheel 文件名 / `requires-python` / 是否被撤回）（ADR 0221 §三）。

只服务 `check_package_availability`。问的是**安装器这次实际用的那个索引**（`packagecheck` 决定是哪一个），
PEP 691 JSON 优先，退回 PEP 503 HTML（镜像 / 私有仓库不一定支持 JSON）。

* **出站只发包名**：请求就是 `GET <索引>/<规范化包名>/`，没有别的内容。
* **HTTPS 只经 `tlstrust`**（`_opener`，不跟随重定向——索引地址带凭据时绝不替用户转交到另一个主机）。明文 http 不读
  （读不了 = 查不到，调用方如实说 `index_unreachable`；明文私网镜像的放行归 `netpolicy`，不在这一档）。
* **凭据**：索引 URL 里的 `user:token@` 只在这里拆成 Basic 头发给**这一个**主机；缓存键、日志、返回值里都没有它。
  异常只带稳定 code，不带 URL。
* **缓存**：（索引主机 + 路径〔不含凭据〕, 包名）→ 文件列表，`data_dir()/cache/package_availability/` 下一小时。
  只缓存**与环境无关**的索引事实；「这个环境会装哪一版」随环境变，不进缓存。失败不缓存。

纯标准库。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from . import tlstrust

LOG = logging.getLogger("tavotto.packageindex")

CACHE_TTL_S = 3600.0
TIMEOUT_S = 15.0
#: 页面体积上限（numpy 这类 simple JSON 约 1–2 MB；畸形 / 恶意响应不许撑爆内存）
MAX_BODY_BYTES = 8 * 1024 * 1024
#: 缓存的文件条数上限（同一道闸：写进磁盘的也有界）
MAX_FILES = 20000
ACCEPT = (
    "application/vnd.pypi.simple.v1+json, application/vnd.pypi.simple.v1+html;q=0.2, "
    "text/html;q=0.01"
)

#: `IndexFetchError.code` 的闭集
CODE_UNREACHABLE = "unreachable"
CODE_NOT_FOUND = "not_found"
CODE_BAD_PAGE = "bad_page"


class IndexFetchError(Exception):
    """索引读不到。只带稳定 code（`CODE_*`），**不带 URL、不带响应原文**。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class IndexFile:
    filename: str
    requires_python: str = ""
    yanked: bool = False


#: 注入点签名：`(url, headers) -> (content_type, body)`，读不到抛 `IndexFetchError`。测试在这里换假索引。
Fetcher = Callable[[str, dict], tuple[str, bytes]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不跟随重定向：Basic 头只发给用户配置里的那个主机。"""

    handler_order = 400

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def _opener() -> urllib.request.OpenerDirector:
    opener = urllib.request.build_opener(tlstrust.https_handler(tlstrust.client_context()))
    opener.add_handler(_NoRedirect())
    return opener


def _fetch(url: str, headers: dict) -> tuple[str, bytes]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with _opener().open(req, timeout=TIMEOUT_S) as resp:
            body = resp.read(MAX_BODY_BYTES + 1)
            ctype = resp.headers.get("Content-Type", "") or ""
    except urllib.error.HTTPError as exc:
        raise IndexFetchError(CODE_NOT_FOUND if exc.code == 404 else CODE_UNREACHABLE) from None
    except Exception:  # noqa: BLE001 - 网络 / TLS / 超时：一律只报「读不到」，不带原因原文（可能含主机或凭据）
        raise IndexFetchError(CODE_UNREACHABLE) from None
    if len(body) > MAX_BODY_BYTES:
        raise IndexFetchError(CODE_BAD_PAGE)
    return ctype, body


def project_url(index_url: str, canonical: str) -> tuple[str, dict, str] | None:
    """索引地址 + 规范化包名 → `(请求地址〔不含凭据〕, 请求头, 缓存身份串〔不含凭据〕)`。只认 https；不合格回 None。"""
    try:
        parts = urllib.parse.urlsplit(str(index_url or "").strip())
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname:
        return None
    host = parts.hostname.lower()
    try:
        port = parts.port
        user, password = parts.username, parts.password
    except ValueError:
        return None
    netloc = host if ":" not in host else f"[{host}]"
    if port:
        netloc += f":{port}"
    path = parts.path.rstrip("/") + "/" + canonical + "/"
    url = urllib.parse.urlunsplit(("https", netloc, path, "", ""))
    headers = {"Accept": ACCEPT, "User-Agent": "Tavotto"}
    if user is not None:
        raw = f"{urllib.parse.unquote(user)}:{urllib.parse.unquote(password or '')}"
        headers["Authorization"] = "Basic " + base64.b64encode(raw.encode("utf-8")).decode("ascii")
    return url, headers, f"{netloc}{parts.path.rstrip('/')}\n{canonical}"


class _AnchorParser(HTMLParser):
    """PEP 503：`<a href=… data-requires-python=… data-yanked>文件名</a>`。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.files: list[IndexFile] = []
        self._attrs: dict | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._attrs = {k: (v or "") for k, v in attrs}
            self._text = []

    def handle_data(self, data):
        if self._attrs is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag != "a" or self._attrs is None:
            return
        name = "".join(self._text).strip()
        a, self._attrs = self._attrs, None
        if name:
            self.files.append(
                IndexFile(name, a.get("data-requires-python", "").strip(), "data-yanked" in a)
            )


def parse_page(body: bytes, content_type: str = "") -> list[IndexFile]:
    """simple 页 → 文件列表。认不出形状抛 `IndexFetchError(bad_page)`；空列表是合法回答（项目存在但没有文件）。"""
    text = body.decode("utf-8", errors="replace")
    stripped = text.lstrip()
    if "json" in content_type.lower() or stripped.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            raise IndexFetchError(CODE_BAD_PAGE) from None
        items = data.get("files") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise IndexFetchError(CODE_BAD_PAGE)
        out: list[IndexFile] = []
        for it in items:
            if not isinstance(it, dict) or not isinstance(it.get("filename"), str):
                continue
            rp = it.get("requires-python")
            out.append(
                IndexFile(
                    it["filename"],
                    rp.strip() if isinstance(rp, str) else "",
                    bool(it.get("yanked")),
                )
            )
        return out[:MAX_FILES]
    parser = _AnchorParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception:  # noqa: BLE001 - 畸形 HTML
        raise IndexFetchError(CODE_BAD_PAGE) from None
    if "<html" not in stripped[:2000].lower() and "<a" not in stripped.lower():
        raise IndexFetchError(CODE_BAD_PAGE)
    return parser.files[:MAX_FILES]


# --------------------------------------------------------------------------- 缓存
def cache_dir() -> Path:
    from . import config

    return config.data_dir() / "cache" / "package_availability"


def _cache_path(identity: str) -> Path:
    return cache_dir() / (hashlib.sha256(identity.encode("utf-8")).hexdigest()[:40] + ".json")


def _cache_read(identity: str, now: float) -> list[IndexFile] | None:
    try:
        data = json.loads(_cache_path(identity).read_text(encoding="utf-8"))
        if now - float(data["t"]) > CACHE_TTL_S or now < float(data["t"]) - 60:
            return None
        return [IndexFile(str(f[0]), str(f[1]), bool(f[2])) for f in data["files"]]
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None


def _cache_write(identity: str, files: list[IndexFile], now: float) -> None:
    try:
        d = cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        payload = {"t": now, "files": [[f.filename, f.requires_python, f.yanked] for f in files]}
        path = _cache_path(identity)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        LOG.debug("包索引缓存写不了", exc_info=True)


def project_files(
    index_url: str,
    canonical: str,
    *,
    fetcher: Fetcher | None = None,
    now: Callable[[], float] = time.time,
) -> tuple[list[IndexFile], bool]:
    """这个包在那个索引上有哪些文件 → `(文件, 是不是缓存命中)`。读不到抛 `IndexFetchError`。"""
    target = project_url(index_url, canonical)
    if target is None:
        raise IndexFetchError(CODE_UNREACHABLE)
    url, headers, identity = target
    t = now()
    cached = _cache_read(identity, t)
    if cached is not None:
        return cached, True
    ctype, body = (fetcher or _fetch)(url, headers)
    files = parse_page(body, ctype)
    _cache_write(identity, files, t)
    return files, False
