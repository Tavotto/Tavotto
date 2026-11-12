#!/usr/bin/env python3
"""向 CLA 服务要一次实时判定，把回答原样落盘（ADR 0172）。

判定不在这里做：`cla_gate.py --eligibility-json` 逐字段核对这份文件。这个脚本只负责
把问题问对、把网络失败说清楚。**它从不让 cla-check 变绿**：问不到时写下错误码，
判定器照样判红（fail closed）。

* 地址只取默认分支 policy 的 `authority_origin`，不接受任何来自事件或 PR 的地址；
* 不带任何凭据：只发 PR 号、sha 和这次校验的 nonce；不跟随跳转；
* 服务忙、连不上、读 GitHub 暂时失败时在 `--budget-seconds` 内重试，其余拒绝立刻结束。

纯标准库。退出码：0 = 文件已写（内容是回答或错误码），2 = 参数或 policy 错误。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ORIGIN_RE = re.compile(r"^https://[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")
CODE_RE = re.compile(r"^[a-z_]{1,64}$")
MAX_BYTES = 64 * 1024
REQUEST_TIMEOUT = 20
RETRY_DELAY = 15
#: 服务端的这些拒绝过一会儿可能就好了；别的拒绝（格式错、PR 已关）重试也没用。
RETRYABLE_CODES = frozenset(
    {
        "github_unavailable",
        "state_changed",
        "merge_prefix_changed",
        "merge_prefix_candidate_absent",
        "merge_prefix_unavailable",
        "budget_deferred",
    }
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class Retry(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def build_url(origin: str, event: str, head: str, nonce: str, pr=None, base=None) -> str:
    if not ORIGIN_RE.match(origin) or not SHA_RE.match(head) or not NONCE_RE.match(nonce):
        raise ValueError("invalid locator")
    if event == "merge_group":
        if not isinstance(base, str) or not SHA_RE.match(base):
            raise ValueError("invalid base")
        path, query = "/eligibility/group", {"base": base, "head": head, "nonce": nonce}
    elif event == "pull_request":
        if type(pr) is not int or pr <= 0:
            raise ValueError("invalid pr")
        path, query = "/eligibility/pr", {"pr": str(pr), "head": head, "nonce": nonce}
    else:
        raise ValueError("invalid event")
    return origin + path + "?" + urllib.parse.urlencode(query)


def _error_code(raw: bytes) -> str:
    try:
        code = json.loads(raw)["error"]
    except (ValueError, KeyError, TypeError):
        return "unknown"
    return code if isinstance(code, str) and CODE_RE.match(code) else "unknown"


def read_once(url: str, opener) -> dict:
    """一次请求。返回 JSON 对象（回答或 `{"error": code}`），暂时失败抛 Retry。"""
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "Tavotto-CLA-gate"},
    )
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT) as response:
            raw = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read(MAX_BYTES + 1)
        if 300 <= status < 400:
            return {"error": "redirect_refused"}
        code = _error_code(raw)
        if status == 429 or status >= 500 or code in RETRYABLE_CODES:
            raise Retry(code if code != "unknown" else f"http_{status}".lower()) from None
        return {"error": code}
    except (urllib.error.URLError, TimeoutError, OSError):
        raise Retry("authority_unreachable") from None
    if len(raw) > MAX_BYTES:
        return {"error": "response_too_large"}
    try:
        answer = json.loads(raw)
    except ValueError:
        raise Retry("response_not_json") from None
    return answer if isinstance(answer, dict) else {"error": "response_not_object"}


def fetch(url: str, opener, *, budget_seconds: int, sleep=time.sleep, clock=time.monotonic) -> dict:
    deadline = clock() + budget_seconds
    while True:
        try:
            return read_once(url, opener)
        except Retry as retry:
            if clock() + RETRY_DELAY >= deadline:
                return {"error": retry.code}
            print(f"CLA 服务暂时不可用（{retry.code}），{RETRY_DELAY} 秒后重试。")
            sleep(RETRY_DELAY)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--policy", required=True, type=Path)
    ap.add_argument("--event", required=True, choices=("pull_request", "merge_group"))
    ap.add_argument("--head", required=True)
    ap.add_argument("--nonce", required=True)
    ap.add_argument("--pr", type=int)
    ap.add_argument("--base")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--budget-seconds", type=int, default=120)
    args = ap.parse_args(argv)
    if not 0 <= args.budget_seconds <= 300:
        print("配置错误：--budget-seconds 必须在 0..300", file=sys.stderr)
        return 2
    try:
        origin = json.loads(args.policy.read_text(encoding="utf-8"))["authority_origin"]
        url = build_url(origin, args.event, args.head, args.nonce, args.pr, args.base)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    answer = fetch(
        url, urllib.request.build_opener(_NoRedirect()), budget_seconds=args.budget_seconds
    )
    args.out.write_text(json.dumps(answer), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
