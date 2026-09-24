#!/usr/bin/env python3
"""REL-05 隐私探针：真后端 + 真 worker 渲染 + 本地遥测接收器 + 诊断包全文搜索。

两组（同一份已保存的「同意 enabled」配置、同一个本地接收器）：
  control: 不设 TAVOTTO_NO_TELEMETRY → 接收器应收到 ≥1 条（证明「观测是活的」，
           否则 B 组的 0 只是「没看见」）
  nohard : TAVOTTO_NO_TELEMETRY=1 → 渲染 / 导出 / 前端事件 / 报错之后接收器 0 条；
           设置接口报 hard_disabled；诊断包（GET + POST）与复制诊断全文不含金丝雀：
           脚本源码片段、CSV 独有数值、图内文字、合成密钥、项目原始路径（原样与 realpath）、
           假用户名 / 主目录。另测越界：未认证 401、`../` 面板 id 被拒、项目外 PDF 不被当面板。

夹具全部在 --scratch 里现造；随机量固定。端口默认 5308。
用法（worktree 根目录）：
    PYTHONPATH=<worktree>/src /Volumes/Projects/Tavotto/.venv/bin/python \
      docs/qa/2026-09-24/rel/repro/rel05_privacy_probe.py --scratch <dir> \
      --worker-python /opt/homebrew/opt/python@3.13/libexec/bin/python3 \
      --fonts-dir /Volumes/Projects/Tavotto/src/tavotto/resources/fonts
退出码：0 = 全部判据成立；1 = 有判据不成立（逐条打印）；2 = 夹具/环境错误。
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

SCRIPT_CANARY = "SCRIPTCANARY_b41c9e"
DATA_CANARY = "7318.0931"  # CSV 里独有的数值串
FIG_CANARY = "FIGTEXTCANARY_Q7Z"
ERR_CANARY = "ERRMSGCANARY_patient_0931"
# 合成密钥：运行时拼出来，源码里不出现完整的「像密钥」的串（免得被秘密扫描器当真）
KEY_CANARY = "-".join(
    ["sk", "ant", "api03", "QACANARYxK2vT9mB7nL4pR8sW1yD6fH3jZ0cE5gA", "QAQAQAAA"]
)
USER_CANARY = "qa_private_user_zz"
PROJ_SEG = "QAPRIV_Alice 论文 项目"

FIG_PY = f'''"""QA 私有脚本 {SCRIPT_CANARY}"""
import csv
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

API_KEY = "{KEY_CANARY}"  # 合成密钥：不许出现在任何外发物里


def main():
    here = Path(__file__).resolve().parent
    rows = list(csv.reader(open(here / "data_priv.csv", encoding="utf-8")))[1:]
    xs = [float(r[0]) for r in rows]
    ys = [float(r[1]) for r in rows]
    fig, ax = plt.subplots(figsize=(3, 2))
    ax.plot(xs, ys, label="series")
    ax.set_title("{FIG_CANARY}")
    ax.text(0.5, 0.5, "{FIG_CANARY}-inner", transform=ax.transAxes)
    fig.savefig(here / "fig_priv.pdf")
    plt.close(fig)


if __name__ == "__main__":
    main()
'''

BOOM_PY = f'''import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    print("stdout {SCRIPT_CANARY}-print")
    raise ValueError("{ERR_CANARY}")


if __name__ == "__main__":
    fig, ax = plt.subplots()
    fig.savefig("boom.pdf")
'''


class Sink(http.server.BaseHTTPRequestHandler):
    hits: list = []

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        Sink.hits.append({"path": self.path, "len": n, "t": time.time()})
        self.rfile.read(n)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    do_GET = do_POST  # noqa: N815

    def log_message(self, *a):
        pass


def req(method, url, body=None, headers=None, timeout=180):
    data = None if body is None else json.dumps(body).encode()
    h = {"Content-Type": "application/json", **(headers or {})}
    r = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def build_fixture(scratch: Path, worker_python: str) -> Path:
    home = scratch / "home" / USER_CANARY
    proj = home / "Documents" / PROJ_SEG
    if scratch.exists():
        shutil.rmtree(scratch)
    proj.mkdir(parents=True)
    (proj / "fig_priv.py").write_text(FIG_PY, encoding="utf-8")
    (proj / "boom.py").write_text(BOOM_PY, encoding="utf-8")
    rows = ["x,y", "0,1.5", f"1,{DATA_CANARY}", "2,3.25", "3,2.0"]
    (proj / "data_priv.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (proj / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "fig_priv.py": {"entry": "main", "cost": "light", "stems": ["fig_priv"]},
                    "boom.py": {"entry": "main", "cost": "light", "stems": ["boom"]},
                }
            }
        ),
        encoding="utf-8",
    )
    # 夹具的 PDF 由原生 matplotlib 先画出来（测试造夹具，不替产品做事）
    subprocess.run([worker_python, "fig_priv.py"], cwd=proj, check=True)
    subprocess.run([worker_python, "boom.py"], cwd=proj, check=True)
    # 项目外的一份 PDF：越界访问的目标
    outside = scratch / "outside_secret"
    outside.mkdir()
    shutil.copy(proj / "fig_priv.pdf", outside / "outside.pdf")
    return proj


def seed_consent(env: dict) -> None:
    """用产品自己的 set_consent 存下「enabled」——在硬开关下存，存的动作本身不外发。"""
    code = (
        "from tavotto.engine import telemetry as t;"
        "t.set_consent(t.CONSENT_ENABLED);"
        "import json;s=t.settings();print(json.dumps({'consent':s['consent'],'has_id':bool(s['install_id'])}))"
    )
    e = dict(env)
    e["TAVOTTO_NO_TELEMETRY"] = "1"
    r = subprocess.run(
        [sys.executable, "-c", code], env=e, capture_output=True, text=True, check=True
    )
    print("seed_consent:", r.stdout.strip())


def start_app(env, proj, port, log_path):
    fh = open(log_path, "wb")
    p = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tavotto",
            "--port",
            str(port),
            "--no-browser",
            "--figures",
            str(proj),
        ],
        env=env,
        stdout=fh,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 90
    while time.time() < deadline:
        if p.poll() is not None:
            raise SystemExit(f"app exited early rc={p.returncode}")
        try:
            st, _ = req("GET", f"{base}/api/version", timeout=3)
            if st == 200:
                return p, base
        except Exception:
            pass
        time.sleep(0.3)
    p.kill()
    raise SystemExit("app not ready")


def stop_app(p):
    p.terminate()
    try:
        p.wait(15)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait(5)


def auth_headers(data_dir: Path, port: int) -> dict:
    sec = json.loads((data_dir / "session" / f"port-{port}.json").read_text())["secret"]
    return {"X-Tavotto-Auth": sec}


def exercise(base, H, results):
    st, body = req("GET", f"{base}/api/telemetry/settings", headers=H)
    results["telemetry_settings"] = json.loads(body) if st == 200 else {"status": st}
    st, body = req("POST", f"{base}/api/engine/render", {"id": "fig_priv.pdf", "patches": []}, H)
    ok = st == 200 and bool(json.loads(body).get("manifest"))
    results["render_ok"] = ok
    st, body = req("POST", f"{base}/api/engine/render", {"id": "boom.pdf", "patches": []}, H)
    results["boom_status"] = st
    spec = {
        "page_w_mm": 80,
        "page_h_mm": 40,
        "formats": ["pdf"],
        "stem": "qa_rel05",
        "objects": [
            {"type": "panel", "id": "fig_priv.pdf", "x_mm": 5, "y_mm": 5, "w_mm": 60, "h_mm": 30}
        ],
    }
    st, body = req("POST", f"{base}/api/export", spec, H)
    results["export_status"] = st
    try:
        out = json.loads(body)
        results["export_files"] = [f["name"] for f in out.get("files", [])]
        results["export_dir"] = out.get("export_dir")
    except Exception:
        results["export_files"] = None
    # 前端才发的那类事件：走同一校验。硬开关下必须 0 外发
    st, body = req(
        "POST",
        f"{base}/api/telemetry/event",
        {"event": "canvas_created", "properties": {"creation_kind": "blank"}},
        H,
    )
    results["frontend_event_status"] = st


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--worker-python", required=True)
    ap.add_argument("--fonts-dir", required=True)
    ap.add_argument("--port", type=int, default=5308)
    a = ap.parse_args()
    scratch = Path(a.scratch).resolve()
    proj = build_fixture(scratch, a.worker_python)
    home = scratch / "home" / USER_CANARY
    data_dir = scratch / "data"
    cfg_dir = scratch / "config"

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Sink)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    sink_url = f"http://127.0.0.1:{srv.server_address[1]}/v1/events"

    base_env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("TAVOTTO_NO_TELEMETRY", "TAVOTTO_TELEMETRY_ENDPOINT")
    }
    base_env.update(
        HOME=str(home),
        TAVOTTO_DATA_DIR=str(data_dir),
        TAVOTTO_CONFIG_DIR=str(cfg_dir),
        TAVOTTO_TELEMETRY_ENDPOINT=sink_url,
        TAVOTTO_NO_UPDATE_CHECK="1",
        TAVOTTO_WORKER_PYTHON=a.worker_python,
        TAVOTTO_FONTS_DIR=a.fonts_dir,
        ANTHROPIC_API_KEY=KEY_CANARY,
        OPENAI_API_KEY=KEY_CANARY,
    )
    seed_consent(base_env)
    failures: list[str] = []
    report: dict = {"sink": "127.0.0.1:<ephemeral>/v1/events"}

    # ---- control arm
    Sink.hits.clear()
    p, base = start_app(base_env, proj, a.port, scratch / "app-control.log")
    H = auth_headers(data_dir, a.port)
    ctrl: dict = {}
    exercise(base, H, ctrl)
    time.sleep(6)
    stop_app(p)
    ctrl["sink_hits"] = len(Sink.hits)
    report["control"] = ctrl
    if ctrl["sink_hits"] < 1:
        failures.append("control: 接收器 0 条——观测无效，B 组的 0 不能当证据")

    # ---- hard-disabled arm
    Sink.hits.clear()
    env_b = dict(base_env, TAVOTTO_NO_TELEMETRY="1")
    p, base = start_app(env_b, proj, a.port, scratch / "app-nohard.log")
    try:
        H = auth_headers(data_dir, a.port)
        b: dict = {}
        # 越界 1：未认证
        st, _ = req("GET", f"{base}/api/project")
        b["unauth_status"] = st
        if st != 401:
            failures.append(f"未认证请求应 401，实际 {st}")
        exercise(base, H, b)
        ts = b["telemetry_settings"]
        if not (ts.get("hard_disabled") is True and ts.get("enabled") is False):
            failures.append(f"设置接口没报硬关: {ts}")
        if ts.get("consent") != "enabled":
            failures.append(f"反证失败：已保存同意应仍是 enabled（证明硬开关压过它）: {ts}")
        if not b["render_ok"]:
            failures.append("反证失败：B 组渲染没成功（没有内容流经日志，搜索无意义）")
        if b["boom_status"] == 200:
            failures.append("boom.py 渲染应失败")
        if b["export_status"] != 200 or not b.get("export_files"):
            failures.append(f"导出失败: {b['export_status']}")
        # 越界 2：`../` 面板 id；越界 3：项目外 PDF
        st, body = req(
            "POST",
            f"{base}/api/engine/render",
            {"id": "../outside_secret/outside.pdf", "patches": []},
            H,
        )
        b["traversal_engine_render_status"] = st
        st2, body2 = req(
            "GET",
            f"{base}/api/render?id={urllib.request.quote('../../../outside_secret/outside.pdf')}&w=200",
            headers=H,
        )
        b["traversal_preview_status"] = st2
        b["traversal_preview_is_png"] = body2[:8] == b"\x89PNG\r\n\x1a\n"
        if st == 200 or st2 == 200 or b["traversal_preview_is_png"]:
            failures.append(f"越界 id 被接受: engine={st} preview={st2}")
        # 诊断包
        st, zbytes_get = req("GET", f"{base}/api/diagnostics/bundle", headers=H)
        st2, zbytes_post = req(
            "POST",
            f"{base}/api/diagnostics/bundle",
            {"frontend_state": {"app": {"version": "x"}}, "interaction_trace": []},
            H,
        )
        st3, summary = req("GET", f"{base}/api/diagnostics/summary", headers=H)
        b["bundle_status"] = [st, st2, st3]
        time.sleep(6)  # 让任何可能的异步投递有机会发生
        b["sink_hits"] = len(Sink.hits)
        if b["sink_hits"] != 0:
            failures.append(f"硬开关下接收器收到 {b['sink_hits']} 条")
    finally:
        stop_app(p)

    canaries = {
        "script_source": SCRIPT_CANARY,
        "data_value": DATA_CANARY,
        "figure_text": FIG_CANARY,
        "error_message": ERR_CANARY,
        "synthetic_key": KEY_CANARY,
        "key_prefix": "sk-ant-api03-QACANARY",
        "username": USER_CANARY,
        "project_segment": "QAPRIV_Alice",
        "project_path_raw": str(proj),
        "project_path_real": os.path.realpath(proj),
        "home_path": str(home),
        "outside_dir": "outside_secret",
    }
    texts: dict[str, str] = {}
    for label, blob in (("GET", zbytes_get), ("POST", zbytes_post)):
        z = zipfile.ZipFile(io.BytesIO(blob))
        for n in z.namelist():
            texts[f"{label}:{n}"] = z.read(n).decode("utf-8", "replace")
    texts["summary"] = summary.decode("utf-8", "replace")
    hits = []
    for name, body in texts.items():
        for cname, c in canaries.items():
            if c in body:
                hits.append(f"{cname} in {name}")
    b["bundle_files"] = sorted(texts)
    b["canary_hits"] = hits
    if hits:
        failures.append(f"诊断包泄漏: {hits}")
    # 反证：包里确实有东西（项目记号 + worker 证据段 + app.log 非空）
    rep = json.loads(texts["GET:report.json"])
    b["report_project_token"] = rep.get("project", {}).get("figures_dir")
    b["app_log_bytes"] = len(texts.get("GET:app.log", ""))
    b["worker_logs_present"] = bool(rep.get("render", {}).get("worker_logs"))
    if not str(b["report_project_token"]).startswith("<project:"):
        failures.append("反证失败：report 没写出项目记号")
    if b["app_log_bytes"] < 200:
        failures.append("反证失败：app.log 几乎是空的")
    # 原始日志里金丝雀确实存在（证明「包里没有」是脱敏的结果，不是因为没写）
    raw_worker = "".join(
        p.read_text("utf-8", "replace") for p in data_dir.rglob("worker.log") if p.is_file()
    )
    raw_app = (scratch / "app-nohard.log").read_text("utf-8", "replace")
    # 报错文字走的是 worker 的结构化回包（→ Flask 的 ERROR 日志），不一定进 worker.log；两处都看
    b["raw_worker_log_has_err_canary"] = ERR_CANARY in raw_worker
    b["raw_app_log_has_err_canary"] = ERR_CANARY in raw_app
    b["raw_app_log_has_project_path"] = str(proj) in raw_app or os.path.realpath(proj) in raw_app
    if not (b["raw_worker_log_has_err_canary"] or b["raw_app_log_has_err_canary"]):
        failures.append("反证失败：原始日志里没有报错金丝雀——包里没有它证明不了脱敏")
    if not b["raw_app_log_has_project_path"]:
        failures.append("反证失败：原始 app.log 没有项目路径——包里没有它证明不了脱敏")
    report["nohard"] = b
    report["bundle_sha256_get"] = hashlib.sha256(zbytes_get).hexdigest()
    report["failures"] = failures
    out = scratch / "rel05_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (scratch / "bundle_get.zip").write_bytes(zbytes_get)
    (scratch / "bundle_post.zip").write_bytes(zbytes_post)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    srv.shutdown()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
