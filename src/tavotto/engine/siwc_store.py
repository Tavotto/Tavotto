"""Sign in with ChatGPT（SIWC）的凭据存储与跨进程刷新锁（ADR 0209 §二）。**不联网**：令牌请求在 `siwc_login`。

布局（`config.data_dir()/siwc/`，目录 0700）：

* `host.json`：`ext_agent_host_id`（`urn:uuid:<v4>`）。首次登录前生成并持久，之后永不变（官方：同一主机发同一个 id；不透明，不含 email / 用户 id）。
* `state.json`：`{"active": <client_id>}`——当前用哪一个账号。
* `accounts/<client_id>.json`（0600，原子写）：一个**已签发** client_id（`oaiapp_…`）+ 一个验过的 `sub` 一份，字段见 `Account`。
  一份记录的 client_id 永不配另一份的令牌；退出登录只清令牌，保留 client_id / sub / email（下次登录同一账号复用 client_id）。
* `refresh.lock`：刷新的跨进程锁（官方：「serialize refreshes … so two processes do not race a rotating token」——`refresh_token_reused`
  是终态错误，两个 Tavotto 进程抢着刷新会把用户踢下线）。macOS / Linux `fcntl.flock`，Windows `msvcrt.locking`。

令牌只在这里与 `siwc_login`、`ai_bridge` 的 spawn 注入之间流动；对外只给 `public_status()`（无令牌）。
**不进 `ai.providers[]`**：「挑第一个能用的连接」的调用点（AI 修复、结构化调用……）结构上看不见它。
纯标准库，Flask 父进程可 import；不 import 网络模块、`ai_agents`、`ai_oauth`、`ai_providers`。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from . import atomicio, config, siwc_auth

ISSUER = "https://auth.openai.com"
#: 首次注册用的公共 client_id——**永不**存成已签发 id
DYNAMIC_CLIENT_ID = "dynamic_agent_client"
_CLIENT_ID_RE = re.compile(r"oaiapp_[A-Za-z0-9_-]{1,200}")
#: 跨进程锁最多等多久（另一个进程正在刷新：一次刷新 ≤ 30 s 超时）
LOCK_TIMEOUT_S = 90.0
_PROC_LOCK = threading.RLock()


def valid_client_id(value: object) -> bool:
    """已签发 client_id 的形状（也决定它能不能当文件名）。`dynamic_agent_client` 不算。"""
    return isinstance(value, str) and bool(_CLIENT_ID_RE.fullmatch(value))


@dataclass(frozen=True)
class Account:
    """一份账号记录。`repr` 不含令牌。`expires_at_ms` / `earliest_refresh_at_ms` 是绝对毫秒。"""

    client_id: str
    subject: str
    email: str = ""
    issuer: str = ISSUER
    ext_agent_host_id: str = ""
    id_token: str = field(default="", repr=False)
    access_token: str = field(default="", repr=False)
    refresh_token: str = field(default="", repr=False)
    token_type: str = "Bearer"
    expires_at_ms: int = 0
    earliest_refresh_at_ms: int | None = None
    scopes: tuple[str, ...] = ()
    saved_at: str = ""
    #: `GET /v1/models` 的缓存：`[{"id": slug, "display_name"?: str}]`（服务端顺序）
    models: tuple[dict, ...] = ()

    def tokens(self) -> siwc_auth.TokenSet | None:
        if not self.access_token:
            return None
        return siwc_auth.TokenSet(
            access=self.access_token,
            refresh=self.refresh_token,
            expires_at_ms=self.expires_at_ms,
            scopes=frozenset(self.scopes),
            earliest_refresh_at_ms=self.earliest_refresh_at_ms,
        )

    def with_tokens(self, ts: siwc_auth.TokenSet) -> Account:
        """刷新成功后四样（访问令牌、过期、授权范围、轮换后的刷新令牌）一起替换。"""
        return replace(
            self,
            access_token=ts.access,
            refresh_token=ts.refresh,
            expires_at_ms=ts.expires_at_ms,
            earliest_refresh_at_ms=ts.earliest_refresh_at_ms,
            scopes=tuple(sorted(ts.scopes)),
            saved_at=_utc_now(),
        )

    def signed_out(self) -> Account:
        """退出 / 终态失效：清令牌，保留 client_id、身份与型号缓存（下次登录同一账号复用 client_id）。"""
        return replace(
            self,
            id_token=self.id_token,  # 保留：下次登录发 id_token_hint（官方：可以是过期的）
            access_token="",
            refresh_token="",
            expires_at_ms=0,
            earliest_refresh_at_ms=None,
            saved_at=_utc_now(),
        )


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------- 路径与权限
def root() -> Path:
    return config.data_dir() / "siwc"


def _accounts_dir() -> Path:
    return root() / "accounts"


def _ensure_dirs() -> None:
    for d in (root(), _accounts_dir()):
        d.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(
            OSError
        ):  # Windows 上 chmod 基本无效：数据目录本就在用户自己的配置目录下
            os.chmod(d, 0o700)


def _write_private(path: Path, obj: dict) -> None:
    _ensure_dirs()
    atomicio.write_bytes(path, atomicio.dumps_json(obj, indent=2), mode=0o600)


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# --------------------------------------------------------------------------- host id
def host_id() -> str:
    """持久的 `ext_agent_host_id`；没有就生成并写下（首次登录之前）。"""
    with _PROC_LOCK:
        got = (_read_json(root() / "host.json") or {}).get("ext_agent_host_id")
        if isinstance(got, str) and got.startswith("urn:uuid:"):
            return got
        hid = f"urn:uuid:{uuid.uuid4()}"
        _write_private(root() / "host.json", {"ext_agent_host_id": hid})
        return hid


# --------------------------------------------------------------------------- 账号记录
def _account_path(client_id: str) -> Path:
    if not valid_client_id(client_id):
        raise ValueError("client_id")
    return _accounts_dir() / f"{client_id}.json"


def _from_dict(d: dict) -> Account | None:
    cid, sub = d.get("client_id"), d.get("subject")
    if not valid_client_id(cid) or not isinstance(sub, str) or not sub:
        return None

    def s(k: str) -> str:
        v = d.get(k)
        return v if isinstance(v, str) else ""

    def i(k: str) -> int | None:
        v = d.get(k)
        return v if isinstance(v, int) and not isinstance(v, bool) else None

    models = tuple(
        {
            "id": m["id"],
            **(
                {"display_name": m["display_name"]}
                if isinstance(m.get("display_name"), str)
                else {}
            ),
        }
        for m in (d.get("models") or [])
        if isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"]
    )
    return Account(
        client_id=cid,
        subject=sub,
        email=s("email"),
        issuer=s("issuer") or ISSUER,
        ext_agent_host_id=s("ext_agent_host_id"),
        id_token=s("id_token"),
        access_token=s("access_token"),
        refresh_token=s("refresh_token"),
        token_type=s("token_type") or "Bearer",
        expires_at_ms=i("expires_at_ms") or 0,
        earliest_refresh_at_ms=i("earliest_refresh_at_ms"),
        scopes=tuple(x for x in (d.get("scopes") or []) if isinstance(x, str)),
        saved_at=s("saved_at"),
        models=models,
    )


def load(client_id: str) -> Account | None:
    try:
        path = _account_path(client_id)
    except ValueError:
        return None
    d = _read_json(path)
    acc = _from_dict(d) if d else None
    return acc if acc is not None and acc.client_id == client_id else None


def save(acc: Account) -> None:
    """原子写一份记录（0600）。同一 client_id 已有记录、但 `sub` 不同 → 拒绝（永不混用两个账号的凭据）。"""
    with _PROC_LOCK:
        old = load(acc.client_id)
        if old is not None and old.subject != acc.subject:
            raise ValueError("subject_mismatch")
        d = asdict(acc)
        d["scopes"] = list(acc.scopes)
        d["models"] = [dict(m) for m in acc.models]
        _write_private(_account_path(acc.client_id), d)


def accounts() -> list[Account]:
    out = []
    try:
        names = sorted(p.stem for p in _accounts_dir().glob("oaiapp_*.json"))
    except OSError:
        return []
    for n in names:
        acc = load(n)
        if acc is not None:
            out.append(acc)
    return out


def find_subject(subject: str) -> Account | None:
    """同一个验过的 `sub` 已有的记录（再次登录同一账号复用它的 client_id）。"""
    return next((a for a in accounts() if a.subject == subject), None)


def active_client_id() -> str | None:
    cid = (_read_json(root() / "state.json") or {}).get("active")
    return cid if valid_client_id(cid) and load(cid) is not None else None


def set_active(client_id: str | None) -> None:
    with _PROC_LOCK:
        if client_id is not None and not valid_client_id(client_id):
            raise ValueError("client_id")
        _write_private(root() / "state.json", {"active": client_id})


def active() -> Account | None:
    cid = active_client_id()
    return load(cid) if cid else None


# --------------------------------------------------------------------------- 跨进程锁
_HELD = threading.local()


@contextlib.contextmanager
def refresh_lock(timeout_s: float = LOCK_TIMEOUT_S) -> Iterator[None]:
    """进程内 + 跨进程的刷新锁，两段共用一个截止时间。等不到 → `siwc_auth.TokenUnavailable("refresh_locked")`（凭据不动，稍后再试）。

    同一线程里重入直接放行：flock / msvcrt 对同一文件的两个打开描述互斥，重入时再开一个 fd 会自己等自己。"""
    depth = getattr(_HELD, "depth", 0)
    if depth:
        _HELD.depth = depth + 1
        try:
            yield
        finally:
            _HELD.depth -= 1
        return
    deadline = time.monotonic() + timeout_s
    if not _PROC_LOCK.acquire(timeout=max(0.0, timeout_s)):
        raise siwc_auth.TokenUnavailable("refresh_locked")
    try:
        _ensure_dirs()
        fd = os.open(root() / "refresh.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            while not _try_lock(fd):
                if time.monotonic() >= deadline:
                    raise siwc_auth.TokenUnavailable("refresh_locked")
                time.sleep(0.1)
            _HELD.depth = 1
            try:
                yield
            finally:
                _HELD.depth = 0
                _unlock(fd)
        finally:
            os.close(fd)
    finally:
        _PROC_LOCK.release()


if os.name == "nt":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        with contextlib.suppress(OSError):
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)


# --------------------------------------------------------------------------- 令牌提供方
class StoreProvider(siwc_auth.RefreshingProvider):
    """当前账号（`state.json` 的 active）的令牌；`refresh_fn(account, tokens) -> TokenSet` 由 `siwc_login` 注入（联网）。

    整段「读 → 判 → 刷新 → 写回」在跨进程锁里：另一个进程刚刷新过，这里读到的就是新令牌，不会拿旧刷新令牌再刷一次。"""

    def __init__(
        self,
        refresh_fn: Callable[[Account, siwc_auth.TokenSet], siwc_auth.TokenSet],
        *,
        clock: Callable[[], int] = siwc_auth._now_ms,
    ):
        super().__init__(clock=clock)
        self._refresh_fn = refresh_fn
        self._acc: Account | None = None

    def __repr__(self) -> str:
        return "StoreProvider(<redacted>)"

    def _critical(self):
        return refresh_lock()

    def _load(self) -> siwc_auth.TokenSet | None:
        self._acc = active()
        return self._acc.tokens() if self._acc else None

    def _save(self, tokens: siwc_auth.TokenSet) -> None:
        if self._acc is None:
            raise siwc_auth.TokenUnavailable("account_gone")
        self._acc = self._acc.with_tokens(tokens)
        save(self._acc)

    def _clear(self) -> None:
        if self._acc is not None:
            self._acc = self._acc.signed_out()
            save(self._acc)

    def _refresh(self, current: siwc_auth.TokenSet) -> siwc_auth.TokenSet:
        if self._acc is None:
            raise siwc_auth.RefreshError("network")
        return self._refresh_fn(self._acc, current)


def public_status() -> dict:
    """前端 / 连接视图 / 诊断包能看到的全部：没有令牌。"""
    acc = active()
    if acc is None:
        return {"signed_in": False, "has_account": False}
    return {
        "has_account": True,
        "signed_in": bool(acc.access_token and acc.refresh_token),
        "plan_usage_enabled": siwc_auth.REQUIRED_SCOPE in acc.scopes,
        "email": acc.email,
        "expires_at_ms": acc.expires_at_ms or None,
        "models": [dict(m) for m in acc.models],
    }


__all__ = [
    "DYNAMIC_CLIENT_ID",
    "ISSUER",
    "Account",
    "StoreProvider",
    "accounts",
    "active",
    "active_client_id",
    "find_subject",
    "host_id",
    "load",
    "public_status",
    "refresh_lock",
    "root",
    "save",
    "set_active",
    "valid_client_id",
]
