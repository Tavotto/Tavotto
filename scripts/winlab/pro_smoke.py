"""高级版组件 Windows 真机冒烟（ADR 0198 §六；规则 docs/rules/ci/windows-lab.md「高级版组件冒烟」）。

    python scripts/winlab/pro_smoke.py --package <cache\\packages\\tavotto-pro-…-windows-x64.zip> --work <目录> --out <结果.json>

由 `agent.run_pro_smoke_task` 在 Lab 的 venv 里、以仓库检出为 cwd 拉起。**走产品自己的代码**（`engine.proinstall` /
`engine.prohost` / `engine.license`），不另写一套安装逻辑：

1. 回环开发端点（`TAVOTTO_LICENSE_ENDPOINT=http://127.0.0.1:<端口>` + `TAVOTTO_DIAG_DEV_LOOPBACK=1`）上起一个本进程内的
   假授权服务：`POST /v1/pro/manifest` 回 Mac 端送来的**签好的发布清单**（`<zip>.release.json`）与回环下载地址，
   `GET /pro/…` 回这个 zip。不联网。
2. 数据目录（带中文与空格）里放好 Mac 端送来的测试授权（`pro-test-license.json`：Mac 上一次真激活得到的 `token.json` 原样 + 设备种子；测试服务端签的，
   只在开发端点下认）。
3. `proinstall.check_for_update()` → 必须 `installed`（验发布签名 → 下载 → 核摘要 → 解压 → 原子改名 → 写 current）；
   再查一次必须 `current`。
4. `prohost.call("hello")`：版本 / 通道 / 功能与清单一致；再发一条**要授权**的命令（空负载）：组件自己读令牌放行后
   应回 `bad_request`（负载不对），回 `license` 就是组件在 Windows 上读不到授权。
5. 安装目录里的 `tavotto-pro.exe`：Authenticode 必须 Valid；Defender（`MpCmdRun -Scan -ScanType 3`）扫安装目录无检出。

令牌与设备种子只写进本次的数据目录（run 目录里，任务结束随 run 一起清），**不进结果文件与日志**。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

#: 测试服务端（license-api-test）的令牌公钥，kid test-2026-11（公钥，不是秘密；客户端只在开发端点下认）
TEST_LICENSE_PUBKEY = "C9tw+S8674pcdwtmTOicytMtXce0vwW6/v9NnG306iw="
#: 测试发布公钥，kid pro-test（同上，只在开发端点下认；正式 RELEASE_KEYS 里没有它）
TEST_RELEASE_PUBKEY_FILE = "pro-test-release.pub"
LICENSE_FILE = "pro-test-license.json"
#: Defender 命令行扫描器的候选位置（Server 2025 上平台更新后在 ProgramData 下）
MPCMDRUN = (
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Windows Defender" / "MpCmdRun.exe",
)
MP_PLATFORM = (
    Path(os.environ.get("ProgramData", r"C:\ProgramData"))
    / "Microsoft"
    / "Windows Defender"
    / "Platform"
)


def _mpcmdrun() -> Path | None:
    cands = []
    if MP_PLATFORM.is_dir():
        cands += sorted((p / "MpCmdRun.exe" for p in MP_PLATFORM.iterdir()), reverse=True)
    cands += list(MPCMDRUN)
    return next((p for p in cands if p.is_file()), None)


class _Fake(http.server.BaseHTTPRequestHandler):
    envelope: dict = {}
    zip_bytes: bytes = b""
    zip_name: str = ""
    seen: list = []

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        self.rfile.read(int(self.headers.get("content-length") or 0))
        self.seen.append(f"POST {self.path}")
        if self.path != "/v1/pro/manifest":
            self._send(
                404,
                b'{"error":{"code":"client_error","message":"not in smoke"}}',
                "application/json",
            )
            return
        port = self.server.server_address[1]
        body = {
            "release": self.envelope,
            "url": f"http://127.0.0.1:{port}/pro/test/{self.zip_name}",
            "app_update_needed": False,
        }
        self._send(200, json.dumps(body).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        self.seen.append(f"GET {self.path.split('?')[0]}")
        if self.path.split("?")[0] == f"/pro/test/{self.zip_name}":
            self._send(200, self.zip_bytes, "application/zip")
        else:
            self._send(404, b"", "text/plain")

    def log_message(self, *a) -> None:
        pass


def _ps(command: str, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _check(checks: list, name: str, ok: bool | None, **facts) -> bool:
    """ok: True passed / False failed / None environment_unavailable。"""
    result = "passed" if ok else ("environment_unavailable" if ok is None else "failed")
    checks.append({"check": name, "result": result, **facts})
    return bool(ok)


def run(package: Path, work: Path) -> dict:
    checks: list = []
    staged = package.parent
    envelope = json.loads((staged / f"{package.name}.release.json").read_text(encoding="utf-8"))
    lic = json.loads((staged / LICENSE_FILE).read_text(encoding="utf-8"))
    release_pub = (staged / TEST_RELEASE_PUBKEY_FILE).read_text(encoding="ascii").strip()

    data = work / "数据 目录" / "Tavotto"
    (data / "license").mkdir(parents=True, exist_ok=True)
    (data / "license" / "token.json").write_text(json.dumps(lic["token_file"]), encoding="utf-8")
    (data / "license" / "device.key").write_bytes(base64.b64decode(lic["device_seed"]))

    _Fake.envelope, _Fake.zip_bytes, _Fake.zip_name = envelope, package.read_bytes(), package.name
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    os.environ.update(
        {
            "TAVOTTO_DATA_DIR": str(data),
            "TAVOTTO_CONFIG_DIR": str(work / "config"),
            "TAVOTTO_LICENSE_ENDPOINT": f"http://127.0.0.1:{port}",
            "TAVOTTO_DIAG_DEV_LOOPBACK": "1",
            "TAVOTTO_LICENSE_TEST_PUBKEY": TEST_LICENSE_PUBKEY,
            "TAVOTTO_PRO_TEST_RELEASE_PUBKEY": release_pub,
        }
    )
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from tavotto.engine import license as L, prohost as H, proinstall as P  # noqa: E402

    payload = json.loads(base64.b64decode(envelope["payload"]))
    _check(
        checks,
        "license_has",
        L.has("quick-layout"),
        features=sorted(k for k in L.FEATURES if L.has(k)),
    )

    t0 = time.monotonic()
    first = P.check_for_update()
    _check(
        checks,
        "install",
        first.get("status") == "installed",
        status=first,
        seconds=round(time.monotonic() - t0, 2),
    )
    again = P.check_for_update()
    _check(checks, "install_idempotent", again.get("status") == "current", status=again)
    root = H.locate()
    _check(
        checks,
        "locate",
        root is not None and root.is_dir(),
        under_data_dir=bool(root and data in root.parents),
    )

    try:
        t0 = time.monotonic()
        hello = H.call("hello", {}, feature="quick-layout")
        _check(
            checks,
            "hello",
            hello.get("pro_version") == payload.get("pro_version")
            and hello.get("channel") == "test"
            and "quick-layout" in (hello.get("features") or []),
            hello=hello,
            seconds=round(time.monotonic() - t0, 2),
        )
        try:
            H.call("quick_layout.plan", {}, feature="quick-layout")
            _check(checks, "gated_entitlement", False, got="ok on empty payload (unexpected)")
        except H.ProUnavailable as exc:
            # bad_request = 组件读到令牌、放行到负载校验；license = 组件在这台机器上读不到 / 不认授权
            _check(checks, "gated_entitlement", exc.code == "bad_request", code=exc.code)
    except H.ProUnavailable as exc:
        _check(checks, "hello", False, code=exc.code)
    finally:
        H.shutdown()

    exe = (root / "tavotto-pro.exe") if root else None
    if exe and exe.is_file():
        sig = _ps(
            f"$s = Get-AuthenticodeSignature -LiteralPath '{exe}'; "
            "[pscustomobject]@{status=[string]$s.Status; subject=$s.SignerCertificate.Subject} | ConvertTo-Json -Compress"
        )
        try:
            info = json.loads(sig.stdout.strip() or "{}")
        except json.JSONDecodeError:
            info = {"raw": sig.stdout[-300:], "stderr": sig.stderr[-300:]}
        _check(checks, "authenticode", info.get("status") == "Valid", **info)
        mp = _mpcmdrun()
        rtp = _ps("(Get-MpComputerStatus).RealTimeProtectionEnabled")
        realtime = rtp.stdout.strip().lower() == "true" if rtp.returncode == 0 else None
        if mp is None:
            _check(
                checks, "defender_scan", None, reason="MpCmdRun.exe not found", realtime=realtime
            )
        else:
            scan = subprocess.run(
                [str(mp), "-Scan", "-ScanType", "3", "-File", str(root), "-DisableRemediation"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=900,
            )
            # 0 = 没有威胁；2 = 发现威胁；其它（权限 / 引擎不可用）= 环境问题，不算通过
            ok = True if scan.returncode == 0 else (False if scan.returncode == 2 else None)
            _check(
                checks,
                "defender_scan",
                ok,
                exit_code=scan.returncode,
                realtime=realtime,
                tail=scan.stdout.strip().splitlines()[-3:],
            )
        # 实时防护开着时解压与拉起都过了一遍；扫完 exe 仍在、摘要未变 = 没有被静默隔离
        _check(
            checks,
            "exe_intact_after_scan",
            exe.is_file(),
            sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),
        )
    else:
        _check(checks, "authenticode", False, reason="tavotto-pro.exe missing")

    srv.shutdown()
    results = [c["result"] for c in checks]
    overall = (
        "failed"
        if "failed" in results
        else ("environment_unavailable" if "environment_unavailable" in results else "passed")
    )
    return {
        "result": overall,
        "pro_version": payload.get("pro_version"),
        "platform": payload.get("platform"),
        "zip_sha256": hashlib.sha256(_Fake.zip_bytes).hexdigest(),
        "fake_server": _Fake.seen,
        "checks": checks,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", required=True, type=Path)
    ap.add_argument("--work", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()
    try:
        out = run(a.package, a.work)
    except Exception as exc:  # noqa: BLE001 — 结论要落盘，异常折成 error
        out = {"result": "error", "summary": f"{type(exc).__name__}: {exc}"[:500]}
    a.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"result": out["result"]}, ensure_ascii=False))
    return 0 if out["result"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
