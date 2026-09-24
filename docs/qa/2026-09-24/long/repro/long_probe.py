#!/usr/bin/env python3
"""QA §7 LONG-01 / LONG-02 / LONG-03 的真后端探针（HTTP 入口，按比例缩短档）。

**不是产品代码，不进测试集。**它起一个真的 `python -m tavotto` 后端（端口默认 5307、
数据 / 配置 / HOME / TMPDIR 全部指向本次 run 目录），经真实 HTTP 链路循环做：

    LONG-01  同一项目同一面板：逐条加编辑（样式 / 拖文字 / 拖图例 / 改图幅 / 挪子图）
             → 随机 undo / redo 游走 → 导出 PDF+PNG → 全部撤销回基线，一轮接一轮。
    LONG-02  A → 编辑 → 导出 → 关闭 → B → 编辑 → 导出 → 关闭，循环；两项目同名脚本、
             同名面板 `Fig1.pdf`、点序列不同（夹具见 make_fixtures.py）。
    LONG-03  在 LONG-01 的循环里注入故障：请求间 SIGKILL worker、请求进行中 SIGKILL
             worker、导出目录临时只读（数据目录暂不可用的代理）。

每一次渲染都对照**测试侧独立的参考**（不调用产品的坐标换算）：
  * 身份：元素 gid 集合 == 基线；标题文字 == 本项目的真值（"Panel A" / "Panel B"）。
  * 数据：从 SVG 里 `axes_0.lines_0` 的 path 取 4 个点，按 (y_i - y_0) / (y_3 - y_0) 算比值，
    与 CSV 真值的同一比值比较（仿射不变，与图幅 / 子图位置无关）；A=[0,.9,.2,1]，B=[0,.2,.9,1]。
  * 几何：生效中的 `pos_frac` / `loc_frac` 拖动，manifest 里该元素的 `anchor` 必须等于声明值
    （容差 ATOL，FigS3 那类「后续几何变动带走已拖文字」会在这里红）。
  * 历史：同一份全量 override 列表（同一个历史状态）第二次出现时，manifest 必须与第一次
    **逐字相等**、SVG 在抹掉白名单非语义字段（`svg_normal`）后逐字节相等
    （undo / redo 回到的必须是同一个状态）。
资源采样（macOS 无 /proc，用 ps / lsof）：进程树 RSS、线程、FD、子进程数、TMPDIR 文件、
数据目录缓存字节、导出目录字节。结束后 shutdown，再查本次数据目录名下的残留 worker。

输出：<out>/samples.jsonl（资源样本）、<out>/events.jsonl（每个动作一行）、<out>/summary.json。
退出码：0 = 全部不变量成立；1 = 有不变量失败（summary.json 里 first_failure）；2 = 探针自身出错。

用法（worktree 根目录，科学栈解释器经 TAVOTTO_WORKER_PYTHON 给后端）：
    PYTHONPATH=$PWD/src TAVOTTO_WORKER_PYTHON=/opt/homebrew/opt/python@3.13/libexec/bin/python3 \\
      /Volumes/Projects/Tavotto/.venv/bin/python docs/qa/2026-09-24/long/repro/long_probe.py \\
      --mode long01 --minutes 15 --fixtures <make_fixtures 输出目录> --out <run 目录>
随机数：`random.Random(f"{mode}:{seed}")`，默认 seed=0；同一 seed 同一动作序列（时长决定走多远）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "scripts"))
import smoke_app as SA  # noqa: E402  —— 复用启动等待与本机凭据交接（唯一实现）

ATOL = 0.005  # figure 分数；80 mm 图幅上约 0.4 mm
DATA_RTOL = 1e-3
TRUTH = {
    "projA": {"title": "Panel A", "ratios": [0.0, 0.9, 0.2, 1.0]},
    "projB": {"title": "Panel B", "ratios": [0.0, 0.2, 0.9, 1.0]},
}
PANEL = "Fig1.pdf"


class InvariantFailure(AssertionError):
    pass


# ----------------------------------------------------------------- HTTP
class Client:
    def __init__(self, base: str):
        self.base = base

    def call(self, method: str, path: str, payload=None, project: str | None = None, timeout=600):
        headers = dict(SA._AUTH)
        if project:
            headers["X-Tavotto-Project"] = project
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                return e.code, json.loads(body)
            except ValueError:
                return e.code, {"raw": body[:500]}


# ----------------------------------------------------------------- 资源采样（macOS / POSIX）
def _ps_table() -> list[tuple[int, int, int, str]]:
    out = subprocess.run(
        ["ps", "-axww", "-o", "pid=,ppid=,rss=,args="], capture_output=True, text=True
    ).stdout
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) >= 3:
            try:
                rows.append(
                    (
                        int(parts[0]),
                        int(parts[1]),
                        int(parts[2]),
                        parts[3] if len(parts) > 3 else "",
                    )
                )
            except ValueError:
                continue
    return rows


def process_tree(root: int) -> list[tuple[int, int, str]]:
    rows = _ps_table()
    kids: dict[int, list] = {}
    for pid, ppid, rss, args in rows:
        kids.setdefault(ppid, []).append((pid, rss, args))
    out, frontier = [], [root]
    me = next(((p, r, a) for p, pp, r, a in rows if p == root), None)
    if me:
        out.append(me)
    while frontier:
        cur = frontier.pop()
        for pid, rss, args in kids.get(cur, []):
            out.append((pid, rss, args))
            frontier.append(pid)
    return out


def _threads(pid: int) -> int:
    r = subprocess.run(["ps", "-M", "-p", str(pid)], capture_output=True, text=True)
    return max(0, len(r.stdout.splitlines()) - 1)


def _fds(pids: list[int]) -> dict[int, int]:
    if not pids:
        return {}
    r = subprocess.run(
        ["lsof", "-nP", "-p", ",".join(map(str, pids))], capture_output=True, text=True
    )
    counts: dict[int, int] = {}
    for line in r.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) > 1 and parts[1].isdigit():
            counts[int(parts[1])] = counts.get(int(parts[1]), 0) + 1
    return counts


def _dir_stats(p: Path) -> tuple[int, int]:
    n = b = 0
    if p.exists():
        for dp, _dn, fn in os.walk(p):
            for f in fn:
                try:
                    b += (Path(dp) / f).stat().st_size
                    n += 1
                except OSError:
                    pass
    return n, b


def sample(server_pid: int, run: Path, export_dirs: list[Path]) -> dict:
    tree = process_tree(server_pid)
    pids = [p for p, _r, _a in tree]
    fds = _fds(pids)
    tmp_n, tmp_b = _dir_stats(run / "tmp")
    cache_n, cache_b = _dir_stats(run / "data" / "cache")
    exp = [_dir_stats(d) for d in export_dirs]
    return {
        "t": round(time.time(), 1),
        "processes": len(tree),
        "workers": sum(1 for _p, _r, a in tree if "worker.py" in a),
        "rss_kib": sum(r for _p, r, _a in tree),
        "rss_server_kib": next((r for p, r, _a in tree if p == server_pid), 0),
        "threads": sum(_threads(p) for p in pids),
        "threads_server": _threads(server_pid),
        "fds": sum(fds.values()),
        "fds_server": fds.get(server_pid, 0),
        "tmp_files": tmp_n,
        "tmp_bytes": tmp_b,
        "cache_files": cache_n,
        "cache_bytes": cache_b,
        "export_files": sum(n for n, _b in exp),
        "export_bytes": sum(b for _n, b in exp),
        "tree": [[p, r, a.split()[-1][-60:] if a else ""] for p, r, a in tree],
    }


def leftover(run: Path) -> list[str]:
    marker = str(run)
    return [
        a
        for _p, _pp, _r, a in _ps_table()
        if marker in a and ("worker.py" in a or "workerd" in a or "renderchild" in a)
    ]


# ----------------------------------------------------------------- 独立参考
_PATH_RE = re.compile(r'<g id="axes_0\.lines_0">\s*<path d="([^"]+)"', re.S)


def svg_ratios(svg: str) -> list[float]:
    m = _PATH_RE.search(svg)
    if not m:
        raise InvariantFailure("SVG 里找不到 axes_0.lines_0 的 path（数据曲线丢失或身份变了）")
    pts = re.findall(r"[ML]\s*([-\d.]+)\s+([-\d.]+)", m.group(1))
    ys = [float(y) for _x, y in pts]
    if len(ys) != 4:
        raise InvariantFailure(f"数据曲线应有 4 个点，SVG 里是 {len(ys)} 个")
    span = ys[3] - ys[0]
    return [round((y - ys[0]) / span, 6) for y in ys]


_DATE_RE = re.compile(r"<dc:date>[^<]*</dc:date>")
_HASHID_RE = re.compile(r"\b([mp][0-9a-f]{10})\b")


def svg_normal(svg: str) -> str:
    """SVG 的**非语义字段白名单**（规范 §0.2 要求列出）：
    * `<dc:date>`：matplotlib 每次 savefig 写入的时间戳；
    * marker / clipPath 的 id（`m` / `p` + 10 位十六进制）：matplotlib `_make_id` 在
      `svg.hashsalt` 未设时每次加随机 uuid 盐，同一图两次序列化的 id 不同。
    id 按首次出现顺序换成 `#0 #1 …`——引用关系保留，只抹掉随机名字。其余字节原样比较。"""
    svg = _DATE_RE.sub("<dc:date/>", svg)
    names: dict[str, str] = {}
    return _HASHID_RE.sub(lambda m: names.setdefault(m.group(1), f"#{len(names)}"), svg)


def canon(man: dict) -> str:
    return json.dumps(man, sort_keys=True, ensure_ascii=False)


def el(man: dict, gid: str) -> dict | None:
    return next((e for e in man["elements"] if e["gid"] == gid), None)


# ----------------------------------------------------------------- 编辑集合
def edit_catalog(base: dict, rng) -> list[dict]:
    """7 条编辑，按顺序叠加。拖动的目标值 = 基线锚点 + 固定种子的偏移（测试侧算，不经产品）。"""
    t = el(base, "fig.texts_0")
    lg = el(base, "axes_0.legend")
    if not (t and t.get("anchor") and lg and lg.get("anchor")):
        raise RuntimeError("夹具的 fig.texts_0 / axes_0.legend 没有 anchor——夹具与产品不符")

    def jitter(a):
        return [
            round(a[0] + rng.uniform(-0.08, 0.08), 4),
            round(a[1] + rng.uniform(-0.08, 0.08), 4),
        ]

    return [
        {"gid": "axes_0.title", "prop": "fontsize", "value": 14.0},
        {"gid": "axes_0.lines_0", "prop": "color", "value": "#d62728"},
        {"gid": "fig.texts_0", "prop": "pos_frac", "value": jitter(t["anchor"])},
        {"gid": "axes_0.legend", "prop": "loc_frac", "value": jitter(lg["anchor"])},
        {"gid": "figure", "prop": "size_mm", "value": [90.0, 66.0]},
        {"gid": "axes_0", "prop": "position", "value": [0.16, 0.16, 0.7, 0.68]},
        {"gid": "axes_0.legend", "prop": "fontsize", "value": 7.0},
    ]


# ----------------------------------------------------------------- 会话
class Session:
    def __init__(self, args, run: Path):
        self.args = args
        self.run = run
        self.proc: subprocess.Popen | None = None
        self.events = (run / "events.jsonl").open("a", encoding="utf-8")
        self.samples = (run / "samples.jsonl").open("a", encoding="utf-8")
        self.refs: dict[
            tuple, tuple[str, str]
        ] = {}  # (project, state) -> (manifest canon, svg sha)
        self.counts = {
            "render_ok": 0,
            "render_checked": 0,
            "revisits_equal": 0,
            "exports_ok": 0,
            "undo": 0,
            "redo": 0,
            "kills": 0,
            "faults": 0,
            "recovered": 0,
            "visible_errors": 0,
            "project_cycles": 0,
        }
        self.first_failure: dict | None = None
        self.render_errors: list[str] = []
        self.export_dirs: list[Path] = []

    def log(self, **kw):
        kw["t"] = round(time.time(), 2)
        self.events.write(json.dumps(kw, ensure_ascii=False) + "\n")
        self.events.flush()

    def start(self, figures: Path):
        for d in ("data", "config", "home", "tmp"):
            (self.run / d).mkdir(parents=True, exist_ok=True)
        env = {
            **os.environ,
            "TAVOTTO_DATA_DIR": str(self.run / "data"),
            "TAVOTTO_CONFIG_DIR": str(self.run / "config"),
            "HOME": str(self.run / "home"),
            "TMPDIR": str(self.run / "tmp"),
            "TAVOTTO_NO_UPDATE_CHECK": "1",
            "TAVOTTO_ALLOW_SHUTDOWN": "1",
            "TAVOTTO_NO_TELEMETRY": "1",
        }
        port = self.args.port
        self.base = f"http://127.0.0.1:{port}"
        out = (self.run / "server-stdout.log").open("w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tavotto",
                "--port",
                str(port),
                "--no-browser",
                "--figures",
                str(figures),
            ],
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
        )
        SA._wait_ready(self.base, self.proc, 120)
        if not SA.adopt_session_credentials(self.run / "data", port):
            raise RuntimeError("会话凭据文件缺失")
        self.c = Client(self.base)

    def stop(self) -> dict:
        res = {"clean_exit": None, "leftover": []}
        try:
            self.c.call("POST", "/api/shutdown", {}, timeout=60)
        except Exception as exc:  # noqa: BLE001
            self.log(kind="shutdown_error", error=str(exc)[:200])
        try:
            self.proc.wait(timeout=120)
            res["clean_exit"] = self.proc.returncode == 0
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(30)
            res["clean_exit"] = False
        time.sleep(2)
        res["leftover"] = leftover(self.run)
        return res

    def take_sample(self, it: int, phase: str):
        s = sample(self.proc.pid, self.run, self.export_dirs)
        s.update({"iteration": it, "phase": phase})
        self.samples.write(json.dumps(s, ensure_ascii=False) + "\n")
        self.samples.flush()
        return s

    def fail(self, where: str, msg: str, **ctx):
        if self.first_failure is None:
            self.first_failure = {"where": where, "invariant": msg, **ctx}
        self.log(kind="INVARIANT_FAIL", where=where, invariant=msg, **ctx)

    # ---- 核心：渲染一次并对照参考
    def render_check(
        self,
        proj: str,
        pj: str | None,
        state: tuple,
        patches: list,
        where: str,
        base_gids: set | None,
    ) -> dict | None:
        st, resp = self.c.call(
            "POST",
            "/api/engine/render",
            {"id": PANEL, "patches": patches, "inline_svg": True},
            project=pj,
        )
        if st != 200:
            self.render_errors.append(repr(resp.get("code")))
            self.log(
                kind="render_error",
                where=where,
                status=st,
                code=resp.get("code"),
                error=str(resp.get("error"))[:200],
            )
            return None
        self.counts["render_ok"] += 1
        man, svg = resp["manifest"], resp.get("svg") or ""
        truth = TRUTH[proj]
        try:
            if resp.get("warnings"):
                raise InvariantFailure(f"渲染带 warnings: {resp['warnings'][:3]}")
            gids = {e["gid"] for e in man["elements"]}
            if base_gids is not None and gids != base_gids:
                raise InvariantFailure(
                    f"元素身份集合变了：多 {sorted(gids - base_gids)[:5]} 少 {sorted(base_gids - gids)[:5]}"
                )
            title = el(man, "axes_0.title")
            tv = next((f["value"] for f in title["editable"] if f["prop"] == "text"), None)
            if tv != truth["title"]:
                raise InvariantFailure(f"标题 {tv!r} ≠ 本项目真值 {truth['title']!r}（串项目？）")
            r = svg_ratios(svg)
            if any(abs(a - b) > DATA_RTOL for a, b in zip(r, truth["ratios"])):
                raise InvariantFailure(f"数据点序列比值 {r} ≠ 真值 {truth['ratios']}")
            for p in patches:
                if p["prop"] in ("pos_frac", "loc_frac"):
                    e = el(man, p["gid"])
                    a = e.get("anchor") if e else None
                    if (
                        not a
                        or abs(a[0] - p["value"][0]) > ATOL
                        or abs(a[1] - p["value"][1]) > ATOL
                    ):
                        raise InvariantFailure(
                            f"{p['gid']} 锚点 {a} ≠ 声明的 {p['prop']} {p['value']}（容差 {ATOL}）"
                        )
            key = (proj, state)
            sig = (canon(man), hashlib.sha256(svg_normal(svg).encode("utf-8")).hexdigest())
            if key in self.refs:
                if self.refs[key][0] != sig[0]:
                    raise InvariantFailure(f"同一历史状态 {state} 再次出现时 manifest 不同")
                if self.refs[key][1] != sig[1]:
                    raise InvariantFailure(
                        f"同一历史状态 {state} 再次出现时 SVG（抹掉白名单非语义字段后）不同"
                    )
                self.counts["revisits_equal"] += 1
            else:
                self.refs[key] = sig
            self.counts["render_checked"] += 1
        except InvariantFailure as exc:
            self.fail(
                where,
                str(exc),
                state=list(state),
                project=proj,
                server_ms=resp.get("timings", {}).get("server_ms"),
            )
            return None
        self.log(
            kind="render",
            where=where,
            project=proj,
            state=list(state),
            server_ms=resp.get("timings", {}).get("server_ms"),
        )
        return man

    def export(self, proj: str, pj: str | None, patches: list, stem: str, where: str) -> dict:
        spec = {
            "page_w_mm": 100,
            "page_h_mm": 80,
            "formats": ["pdf", "png"],
            "dpi": 150,
            "stem": stem,
            "objects": [
                {
                    "type": "panel",
                    "id": PANEL,
                    "x_mm": 5,
                    "y_mm": 5,
                    "w_mm": 90,
                    "h_mm": 66,
                    "overrides": patches,
                }
            ],
        }
        st, resp = self.c.call("POST", "/api/export", spec, project=pj)
        rec = {"status": st, "code": resp.get("code"), "files": []}
        if st == 200:
            for f in resp.get("files", []):
                path = Path(resp.get("export_dir") or "") / f["name"]
                if not path.exists():
                    for d in self.export_dirs:
                        if (d / f["name"]).exists():
                            path = d / f["name"]
                data = path.read_bytes() if path.exists() else b""
                ok = (
                    (data[:5] == b"%PDF-")
                    if f["name"].endswith(".pdf")
                    else data[:8] == b"\x89PNG\r\n\x1a\n"
                )
                rec["files"].append(
                    {
                        "name": f["name"],
                        "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "magic_ok": ok,
                    }
                )
                if not ok:
                    self.fail(where, f"导出文件 {f['name']} 头部不是预期格式或文件不存在")
            if len(rec["files"]) == 2:
                self.counts["exports_ok"] += 1
            else:
                self.fail(where, f"导出成功但文件数 {len(rec['files'])} ≠ 2")
        self.log(kind="export", where=where, project=proj, **rec)
        return rec

    # ---- worker 故障注入
    def worker_pids(self) -> list[int]:
        return [p for p, _r, a in process_tree(self.proc.pid) if "worker.py" in a]

    def kill_workers(self, where: str) -> list[int]:
        pids = self.worker_pids()
        for p in pids:
            try:
                os.kill(p, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.counts["kills"] += len(pids)
        self.log(kind="kill_worker", where=where, pids=pids)
        return pids


# ----------------------------------------------------------------- 各模式
def run_long01(s: Session, rng, deadline: float, faults: bool):
    proj = "projA"
    figs = Path(s.args.fixtures) / proj
    s.start(figs)
    st, pinfo = s.c.call("GET", "/api/project")
    s.export_dirs = [Path(pinfo["export_dir"])]
    st, resp = s.c.call(
        "POST", "/api/engine/render", {"id": PANEL, "patches": [], "inline_svg": True}
    )
    if st != 200:
        raise RuntimeError(f"基线渲染失败 {st} {resp}")
    base = resp["manifest"]
    base_gids = {e["gid"] for e in base["elements"]}
    edits = edit_catalog(base, rng)
    s.log(kind="catalog", edits=edits)
    s.take_sample(0, "warm")
    it = 0
    while time.time() < deadline:
        it += 1
        stack: list[int] = []
        redo: list[int] = []
        # 1) 逐条加编辑
        for i in range(len(edits)):
            stack.append(i)
            redo.clear()
            s.render_check(
                proj, None, tuple(stack), [edits[k] for k in stack], f"it{it}-add{i}", base_gids
            )
            if faults and rng.random() < 0.08:
                inject(s, rng, proj, tuple(stack), [edits[k] for k in stack], it, base_gids)
        # 2) 随机 undo / redo 游走
        for j in range(8):
            if stack and (not redo or rng.random() < 0.6):
                redo.append(stack.pop())
                s.counts["undo"] += 1
            elif redo:
                stack.append(redo.pop())
                s.counts["redo"] += 1
            s.render_check(
                proj, None, tuple(stack), [edits[k] for k in stack], f"it{it}-walk{j}", base_gids
            )
            if faults and rng.random() < 0.08:
                inject(s, rng, proj, tuple(stack), [edits[k] for k in stack], it, base_gids)
        # 3) 导出当前状态（每 2 轮一次）
        if it % 2 == 1:
            s.export(proj, None, [edits[k] for k in stack], f"long-{it}", f"it{it}-export")
        # 4) 全部撤销回基线
        s.render_check(proj, None, (), [], f"it{it}-reset", base_gids)
        if it % 3 == 0 or it == 1:
            s.take_sample(it, "loop")
        if s.first_failure and not s.args.keep_going:
            break
    s.take_sample(it, "end")
    return it


def inject(s: Session, rng, proj, state, patches, it, base_gids):
    """三种故障轮流：请求间杀 worker / 请求中杀 worker / 导出目录暂时只读。"""
    kind = ["kill_between", "kill_during", "export_dir_readonly"][s.counts["faults"] % 3]
    s.counts["faults"] += 1
    where = f"it{it}-fault-{kind}"
    if kind == "kill_between":
        s.kill_workers(where)
        n_err = len(s.render_errors)
        man = s.render_check(proj, None, state, patches, where + "-after", base_gids)
        if man is None:
            s.counts["visible_errors"] += 1
            code = s.render_errors[-1] if len(s.render_errors) > n_err else "invariant"
            s.counts.setdefault("between_codes", {})
            s.counts["between_codes"][code] = s.counts["between_codes"].get(code, 0) + 1
        else:
            s.counts.setdefault("between_codes", {})
            s.counts["between_codes"]["200"] = s.counts["between_codes"].get("200", 0) + 1
        if man is None:
            man = s.render_check(proj, None, state, patches, where + "-retry", base_gids)
        if man is not None:
            s.counts["recovered"] += 1
        else:
            s.fail(where, "杀 worker 之后重试一次仍未恢复到同一状态")
    elif kind == "kill_during":
        # 热态渲染只要 ~30 ms，睡一下再杀多半落在两次请求之间。要真的「请求进行中」：
        # 先杀掉现有 worker，下一次渲染必然冷启动 build（~1 s），等新 worker 出现后再杀它。
        s.kill_workers(where + "-pre")
        time.sleep(0.3)
        box = {}

        def go():
            box["r"] = s.c.call(
                "POST", "/api/engine/render", {"id": PANEL, "patches": patches, "inline_svg": True}
            )

        th = threading.Thread(target=go)
        th.start()
        new = []
        t_end = time.time() + 5
        while time.time() < t_end and not new and th.is_alive():
            new = s.worker_pids()
            time.sleep(0.01)
        delay = rng.uniform(0.05, 0.4)
        time.sleep(delay)
        killed = s.kill_workers(where) if th.is_alive() else []
        th.join(600)
        st, resp = box.get("r", (None, {}))
        code = resp.get("code") if st != 200 else None
        s.log(
            kind="during_result",
            where=where,
            status=st,
            code=code,
            killed_mid_request=bool(killed),
            delay_s=round(delay, 3),
            error=str(resp.get("error"))[:200] if st != 200 else None,
        )
        s.counts.setdefault("during_codes", {})
        s.counts["during_codes"][str(code) if killed else "not_mid_request"] = (
            s.counts["during_codes"].get(str(code) if killed else "not_mid_request", 0) + 1
        )
        if st != 200:
            s.counts["visible_errors"] += 1
            # 规范的不变量是「错误可见」：失败响应必须带非空的错误文字。错误码是否稳定另行
            # 统计（during_codes / between_codes），不在这里判红——那是分类问题，不是看不见。
            if not str(resp.get("error") or "").strip():
                s.fail(where, f"请求中杀 worker：失败响应 {st} 没有任何错误文字")
        man = s.render_check(proj, None, state, patches, where + "-after", base_gids)
        if man is None:
            man = s.render_check(proj, None, state, patches, where + "-retry", base_gids)
        if man is not None:
            s.counts["recovered"] += 1
        else:
            s.fail(where, "请求中杀 worker 之后未恢复到同一状态")
    else:
        d = s.export_dirs[0]
        d.mkdir(parents=True, exist_ok=True)
        mode = d.stat().st_mode
        os.chmod(d, stat.S_IRUSR | stat.S_IXUSR)
        try:
            rec = s.export(proj, None, patches, f"ro-{it}", where + "-readonly")
        finally:
            os.chmod(d, mode)
        if rec["status"] == 200:
            s.fail(where, "导出目录只读时导出仍报成功")
        elif not rec.get("code"):
            s.fail(where, f"导出目录只读：失败响应 {rec['status']} 没有稳定错误码")
        else:
            s.counts["visible_errors"] += 1
        rec2 = s.export(proj, None, patches, f"rw-{it}", where + "-restored")
        if rec2["status"] == 200:
            s.counts["recovered"] += 1
        else:
            s.fail(where, f"导出目录恢复可写后导出仍失败 {rec2['status']} {rec2.get('code')}")
        s.render_check(proj, None, state, patches, where + "-render", base_gids)


def run_long02(s: Session, rng, deadline: float):
    fx = Path(s.args.fixtures)
    s.start(fx / "projA")
    st, info = s.c.call("GET", "/api/project")
    first_id = info["id"]
    s.c.call("POST", "/api/projects/close", {"id": first_id})
    s.take_sample(0, "empty")
    it = 0
    base_by_proj: dict[str, set] = {}
    while time.time() < deadline:
        it += 1
        for proj in ("projA", "projB"):
            st, info = s.c.call(
                "POST", "/api/projects/open", {"path": str(fx / proj), "default": False}
            )
            if st != 200:
                s.fail(f"it{it}-{proj}-open", f"打开项目失败 {st} {info.get('code')}")
                continue
            pj = info["id"]
            ed = Path(info["export_dir"])
            if ed not in s.export_dirs:
                s.export_dirs.append(ed)
            patches0: list = []
            man = s.render_check(
                proj, pj, (), patches0, f"it{it}-{proj}-base", base_by_proj.get(proj)
            )
            if man is None:
                continue
            base_by_proj.setdefault(proj, {e["gid"] for e in man["elements"]})
            edits = edit_catalog(man, rng) if (proj, "cat") not in s.refs else s.refs[(proj, "cat")]
            s.refs[(proj, "cat")] = edits
            stack = []
            for i in (0, 2, 4, 1):
                stack.append(i)
                s.render_check(
                    proj,
                    pj,
                    tuple(stack),
                    [edits[k] for k in stack],
                    f"it{it}-{proj}-e{i}",
                    base_by_proj[proj],
                )
            stack.pop()
            s.counts["undo"] += 1
            s.render_check(
                proj,
                pj,
                tuple(stack),
                [edits[k] for k in stack],
                f"it{it}-{proj}-undo",
                base_by_proj[proj],
            )
            s.export(
                proj, pj, [edits[k] for k in stack], f"sw-{proj}-{it}", f"it{it}-{proj}-export"
            )
            st, r = s.c.call("POST", "/api/projects/close", {"id": pj})
            if not (st == 200 and r.get("ok")):
                s.fail(f"it{it}-{proj}-close", f"关闭项目失败 {st} {r}")
            time.sleep(0.5)
            w = s.worker_pids()
            if w:
                time.sleep(3)
                w = s.worker_pids()
            if w:
                s.fail(f"it{it}-{proj}-close", f"关闭项目 3.5 s 后仍有 worker 进程 {w}")
            s.log(kind="closed", project=proj, workers_after=w)
        s.counts["project_cycles"] += 1
        if it % 2 == 1:
            s.take_sample(it, "after_close")
        if s.first_failure and not s.args.keep_going:
            break
    s.take_sample(it, "end")
    return it


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("long01", "long02", "long03"), required=True)
    ap.add_argument("--minutes", type=float, default=15)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--port", type=int, default=5307)
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--keep-going", action="store_true")
    args = ap.parse_args(argv)
    import random

    run = Path(args.out)
    if run.exists():
        shutil.rmtree(run)
    run.mkdir(parents=True)
    # 夹具的 tavottofile/ 是产品在项目里建的文档目录——每次从干净夹具开始
    for proj in ("projA", "projB"):
        shutil.rmtree(Path(args.fixtures) / proj / "tavottofile", ignore_errors=True)
    before = {
        str(p.relative_to(args.fixtures)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in Path(args.fixtures).rglob("*")
        if p.is_file() and p.suffix in (".py", ".csv")
    }
    rng = random.Random(f"{args.mode}:{args.seed}")
    s = Session(args, run)
    started = time.time()
    deadline = started + args.minutes * 60
    stop_res = {}
    err = None
    iters = 0
    try:
        if args.mode == "long01":
            iters = run_long01(s, rng, deadline, faults=False)
        elif args.mode == "long03":
            iters = run_long01(s, rng, deadline, faults=True)
        else:
            iters = run_long02(s, rng, deadline)
    except Exception:  # noqa: BLE001
        import traceback

        err = traceback.format_exc()
        s.log(kind="probe_error", error=err[-2000:])
    finally:
        if s.proc is not None:
            stop_res = s.stop()
    after = {
        str(p.relative_to(args.fixtures)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in Path(args.fixtures).rglob("*")
        if p.is_file() and p.suffix in (".py", ".csv")
    }
    samples = [json.loads(line) for line in (run / "samples.jsonl").read_text().splitlines()]
    trend = {}
    if len(samples) >= 3:
        warm = samples[1:] if len(samples) > 3 else samples
        for k in (
            "rss_kib",
            "rss_server_kib",
            "threads",
            "fds",
            "processes",
            "tmp_files",
            "cache_bytes",
            "export_bytes",
        ):
            trend[k] = {
                "first_after_warm": warm[0][k],
                "last": warm[-1][k],
                "min": min(x[k] for x in warm),
                "max": max(x[k] for x in warm),
            }
    summary = {
        "mode": args.mode,
        "seed": args.seed,
        "minutes_budget": args.minutes,
        "wall_s": round(time.time() - started, 1),
        "iterations": iters,
        "counts": s.counts,
        "first_failure": s.first_failure,
        "probe_error": err,
        "stop": stop_res,
        "user_files_unchanged": before == after,
        "user_files": after,
        "samples": len(samples),
        "trend": trend,
        "states_seen": len([k for k in s.refs if k[1] != "cat"]),
    }
    (run / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k != "user_files"}, ensure_ascii=False, indent=1
        )
    )
    if err:
        return 2
    ok = (
        s.first_failure is None
        and before == after
        and stop_res.get("clean_exit")
        and not stop_res.get("leftover")
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
