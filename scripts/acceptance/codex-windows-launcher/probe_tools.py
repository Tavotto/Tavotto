"""#266 acceptance probe (POSIX twin of probe-tools.ps1).

Runs one `codex exec` turn under an isolated CODEX_HOME against an in-process fake OpenAI
Responses endpoint, records every request body, and reports which Tavotto MCP tools Codex handed
to the model. No login, nothing leaves the machine. Same verdicts as the PowerShell probe:
TOOLS_PRESENT / HEALTH_ONLY / NO_TAVOTTO_TOOLS / NO_REQUEST.

    python3 probe_tools.py --codex-home DIR --out DIR [--codex codex] [--env K=V ...]
"""

from __future__ import annotations

import argparse
import http.server
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SSE = (
    "event: response.created\n"
    'data: {"type":"response.created","response":{"id":"resp_w266"}}\n\n'
    "event: response.output_item.done\n"
    'data: {"type":"response.output_item.done","output_index":0,"item":{"type":"message",'
    '"role":"assistant","id":"msg_w266","content":[{"type":"output_text","text":"w266-ok"}]}}\n\n'
    "event: response.completed\n"
    'data: {"type":"response.completed","response":{"id":"resp_w266","usage":{"input_tokens":1,'
    '"input_tokens_details":null,"output_tokens":1,"output_tokens_details":null,"total_tokens":2}}}\n\n'
).encode()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codex-home", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--codex", default="codex")
    ap.add_argument("--env", action="append", default=[], help="extra K=V for codex (PATH=...)")
    a = ap.parse_args()
    out = Path(a.out)
    req = out / "requests"
    req.mkdir(parents=True, exist_ok=True)
    for f in req.glob("*.json"):
        f.unlink()
    work = out / "workspace"
    work.mkdir(exist_ok=True)
    count = [0]

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            count[0] += 1
            name = re.sub(r"[^A-Za-z0-9]", "_", self.path)
            (req / f"{count[0]:03d}-{name}.json").write_bytes(body)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(SSE)))
            self.end_headers()
            self.wfile.write(SSE)

        def log_message(self, *args):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    provider = (
        f"model_providers.w266={{name='w266',base_url='http://127.0.0.1:{port}/v1',"
        "wire_api='responses',env_key='W266_FAKE_KEY',request_max_retries=0,stream_max_retries=0}"
    )
    env = dict(os.environ)
    env.update(
        CODEX_HOME=a.codex_home,
        W266_FAKE_KEY="not-a-real-key",
        RUST_LOG="info,rmcp=debug,codex_rmcp_client=debug,codex_core::mcp_connection_manager=debug",
    )
    for kv in a.env:
        k, _, v = kv.partition("=")
        env[k] = v
    with open(out / "codex.stderr", "wb") as err, open(out / "codex.stdout", "wb") as so:
        rc = subprocess.run(
            [
                a.codex,
                "exec",
                "--skip-git-repo-check",
                "-C",
                str(work),
                "-c",
                "model_provider='w266'",
                "-c",
                "model='gpt-5'",
                "-c",
                provider,
                "reply with ok",
            ],
            stdout=so,
            stderr=err,
            env=env,
            timeout=300,
            check=False,
        ).returncode
    srv.shutdown()
    bodies = sorted(req.glob("*.json"))
    names: set[str] = set()
    for b in bodies:
        names |= set(
            re.findall(r'"name"\s*:\s*"([^"]*tavotto[^"]*)"', b.read_text("utf-8", "replace"))
        )
    if not bodies:
        verdict = "NO_REQUEST"
    elif any("open_figure" in n for n in names):
        verdict = "TOOLS_PRESENT"
    elif any("health" in n for n in names):
        verdict = "HEALTH_ONLY"
    else:
        verdict = "NO_TAVOTTO_TOOLS"
    lines = [
        f"codex exit: {rc}",
        f"requests: {len(bodies)}",
        f"tavotto tools: {', '.join(sorted(names))}",
        f"VERDICT: {verdict}",
    ]
    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
