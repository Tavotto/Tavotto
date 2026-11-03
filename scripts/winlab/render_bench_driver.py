"""render-bench 的测量驱动（Windows Lab 上由 agent.py 起子进程运行；随 deploy-agent 一同部署；Mac 上也能直接跑）。

只用标准库，**只测量、不改任何东西**：起 Tavotto 后端（127.0.0.1 随机端口、一次性数据目录），打
`/api/engine/render`，把计时写成一个 JSON。不起模型、不联网、不装包。

三块测量（`--mode` 选 rotation / cold / all；探针总是跑）：

* `rotation`：池上限悬崖。同一个后端里依次对 2 / 3 / 4 / 6 张图「轮流渲染」（A→B→C→A→…），每组先走一遍预热
  （不计入统计），再正式 `--rounds` 轮；每次记 server_ms / wall_ms / 是否冷启动（响应 timings 里有
  `script_build_ms` 才算真跑了脚本）；每组前后各数一次 app.log 里「LRU 淘汰」的行数。
* `cold`：冷启动分解。同一张图，每次**重起后端 + 全新数据目录**再渲染第一次，`--cold-runs` 次，记 timings
  里所有数字段（worker_get_ms / script_build_ms / script_exec_ms …）与后端就绪耗时。
* `probe`（每个模式都跑）：解释器入口 vs 导入。对 `--probe-python` 指定的每个解释器，计时 `-c "import sys"`、
  `-c "import numpy, matplotlib"`、`-c "import matplotlib.pyplot"` 各 `--probe-runs` 次。

解释器参数化：`--python` 是跑后端的解释器；`--worker-python` 经 `TAVOTTO_WORKER_PYTHON` 指定渲染解释器
（不给就由产品自己选）。项目目录（`--figures`）由调用方准备好，驱动不创建也不删除它。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

BOOT_TIMEOUT_S = 180
RENDER_TIMEOUT_S = 900
ROTATION_SETS = (2, 3, 4, 6)
PLANES = ("python", "workerd")
#: 结果里只留这些形状的计时键：数字、有限、非负（用户字符串一律不进结果）
_MAX_MS = 3_600_000


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def pct(values: list[float], q: float) -> float | None:
    """最近邻分位（不插值）：样本少时 P90 就是真实出现过的一次耗时。"""
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, max(0, math.ceil(q * len(s)) - 1))], 1)


def median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def summarize(values: list[float]) -> dict:
    return {
        "n": len(values),
        "median": median(values),
        "p90": pct(values, 0.9),
        "max": pct(values, 1.0),
    }


def numeric_timings(t: dict | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for k, v in (t or {}).items():
        if (
            isinstance(v, (int, float))
            and not isinstance(v, bool)
            and math.isfinite(v)
            and 0 <= v < _MAX_MS
        ):
            out[str(k)] = float(v)
    return out


# ------------------------------- HTTP ---------------------------------------
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def http(
    url: str, payload: dict | None = None, method: str | None = None, timeout: float = 60
) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method or ("GET" if payload is None else "POST"),
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


class Backend:
    """一个后端进程 + 它的一次性数据目录。`with` 退出时干净关掉。"""

    def __init__(self, args, plane: str, base_dir: Path, tag: str):
        self.args = args
        self.plane = plane
        self.dir = base_dir / tag
        self.data = self.dir / "data"
        self.proc: subprocess.Popen | None = None
        self.base = ""
        self.boot_ms: float | None = None

    @property
    def app_logs(self) -> list[Path]:
        cache = self.data / "cache"
        return [cache / "app.log", *(cache / f"app.log.{i}" for i in (1, 2, 3))]

    def count_log(self, needle: str) -> int:
        n = 0
        for p in self.app_logs:
            try:
                n += p.read_text(encoding="utf-8", errors="replace").count(needle)
            except OSError:
                pass
        return n

    def __enter__(self) -> "Backend":
        a = self.args
        for sub in ("data", "config", "exports", "mpl"):
            (self.dir / sub).mkdir(parents=True, exist_ok=True)
        port = free_port()
        self.base = f"http://127.0.0.1:{port}"
        env = {
            **os.environ,
            "TAVOTTO_DATA_DIR": str(self.data),
            "TAVOTTO_CONFIG_DIR": str(self.dir / "config"),
            "TAVOTTO_NO_UPDATE_CHECK": "1",
            "TAVOTTO_NO_TELEMETRY": "1",
            "TAVOTTO_ALLOW_SHUTDOWN": "1",
            # 与 scripts/bench_render.py 同口径：只量渲染链路，不办会话 cookie 手续
            "TAVOTTO_INSECURE_NO_AUTH": "1",
            "TAVOTTO_WORKERD": "0" if self.plane == "python" else str(a.workerd),
            "PYTHONIOENCODING": "utf-8",
        }
        if a.mplconfigdir:
            env["MPLCONFIGDIR"] = a.mplconfigdir
        if a.worker_python:
            env["TAVOTTO_WORKER_PYTHON"] = a.worker_python
        cmd = [
            a.python,
            "-m",
            "tavotto",
            "--port",
            str(port),
            "--no-browser",
            "--figures",
            str(a.figures),
        ]
        out = (self.dir / "server-stdout.log").open("w", encoding="utf-8")
        t0 = time.perf_counter()
        # 不用 PIPE：没人读的管道写满会让应用在日志锁上永久阻塞（bench_render.py 同一条教训）
        self.proc = subprocess.Popen(cmd, env=env, stdout=out, stderr=subprocess.STDOUT)
        deadline = time.time() + BOOT_TIMEOUT_S
        while True:
            if self.proc.poll() is not None:
                raise RuntimeError(f"backend exited before ready (rc={self.proc.returncode})")
            try:
                http(f"{self.base}/api/version", timeout=5)
                break
            except (urllib.error.URLError, OSError, TimeoutError):
                if time.time() > deadline:
                    raise RuntimeError(f"backend not ready in {BOOT_TIMEOUT_S}s") from None
                time.sleep(0.25)
        self.boot_ms = round((time.perf_counter() - t0) * 1000, 1)
        http(
            f"{self.base}/api/project/settings", {"export_dir": str(self.dir / "exports")}, "PATCH"
        )
        return self

    def __exit__(self, *exc) -> None:
        p = self.proc
        if p is None:
            return
        if p.poll() is None:
            try:
                http(f"{self.base}/api/shutdown", {}, timeout=10)
                p.wait(timeout=30)
            except (urllib.error.URLError, OSError, TimeoutError, subprocess.TimeoutExpired):
                pass
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                p.kill()

    def panels(self) -> list[str]:
        return sorted(p["id"] for p in http(f"{self.base}/api/panels")["panels"] if p.get("script"))

    def render(self, pid: str, patches: list) -> dict:
        t0 = time.perf_counter()
        res = http(
            f"{self.base}/api/engine/render",
            {"id": pid, "patches": patches},
            timeout=RENDER_TIMEOUT_S,
        )
        wall = round((time.perf_counter() - t0) * 1000, 1)
        t = numeric_timings(res.get("timings"))
        return {
            "id": pid,
            "wall_ms": wall,
            "server_ms": t.get("server_ms"),
            "cold": "script_build_ms" in t,
            "timings": t,
            "_manifest": res.get("manifest"),
        }


def pick_patch(manifest: dict | None) -> dict | None:
    """挑一个改了必重画的数字属性（与 bench_render.py 同）；值每次不同，免得 worker 走「没变」的捷径。"""
    for prop in ("fontsize", "linewidth", "lw"):
        for el in (manifest or {}).get("elements", []):
            for field in el.get("editable", []):
                if field.get("prop") == prop and field.get("type") == "number":
                    try:
                        return {"gid": el["gid"], "prop": prop, "value": float(field.get("value"))}
                    except (TypeError, ValueError):
                        continue
    return None


def variant(patch: dict | None, i: int) -> list:
    if patch is None:
        return []
    return [{**patch, "value": round(patch["value"] + (0.5 if i % 2 else -0.5), 2)}]


def rotation_plan(subset: list, rounds: int) -> list[tuple[str, int]]:
    """轮转的请求序列：(面板, 这张面板自己第几次被访问)。

    补丁值按**每张面板自己的访问次数**交替，而不是按全局请求序号：全局序号下 2/4/6 张时每张面板每轮
    拿到同一个值，`overrides.apply()` 对没变的值跳过 setter，热基线量的是「没改」而 3 张时量的是
    真修改，各组不可比（Codex #1641 P2）。"""
    visits: dict = {}
    plan = []
    for _ in range(rounds):
        for pid in subset:
            visits[pid] = visits.get(pid, 0) + 1
            plan.append((pid, visits[pid]))
    return plan


# ------------------------------- rotation -----------------------------------
def run_rotation(args, plane: str, base_dir: Path) -> dict:
    out: dict = {"plane": plane, "sets": []}
    with Backend(args, plane, base_dir, f"rot-{plane}") as be:
        out["boot_ms"] = be.boot_ms
        ids = be.panels()
        if len(ids) < max(args.sets):
            raise RuntimeError(f"only {len(ids)} panels, need {max(args.sets)}")
        patches: dict[str, dict | None] = {}
        for n in args.sets:
            subset = ids[:n]
            ev0 = be.count_log("LRU")
            warm_cold = 0
            for pid in subset:  # 预热一遍：让这组的稳态从「每张都曾加载过」开始，不计入统计
                r = be.render(pid, [])
                patches.setdefault(pid, pick_patch(r.pop("_manifest")))
                warm_cold += r["cold"]
            ev_warm = be.count_log("LRU")
            samples = []
            for k, (pid, visit) in enumerate(rotation_plan(subset, args.rounds)):
                r = be.render(pid, variant(patches.get(pid), visit))
                r.pop("_manifest", None)
                r["round"] = k // n
                samples.append(r)
            ev1 = be.count_log("LRU")
            srv = [s["server_ms"] for s in samples if s["server_ms"] is not None]
            cold = [s for s in samples if s["cold"]]
            warm = [s for s in samples if not s["cold"]]
            row = {
                "scripts": n,
                "rounds": args.rounds,
                "renders": len(samples),
                "cold_count": len(cold),
                "warmup_cold_count": warm_cold,
                "lru_evictions_warmup": ev_warm - ev0,
                "lru_evictions": ev1 - ev_warm,
                "server_ms": summarize(srv),
                "wall_ms": summarize([s["wall_ms"] for s in samples]),
                "cold_server_ms": summarize(
                    [s["server_ms"] for s in cold if s["server_ms"] is not None]
                ),
                "warm_server_ms": summarize(
                    [s["server_ms"] for s in warm if s["server_ms"] is not None]
                ),
                "samples": [
                    {k: s[k] for k in ("id", "round", "wall_ms", "server_ms", "cold")}
                    for s in samples
                ],
            }
            out["sets"].append(row)
            log(
                f"  [{plane}] {n} scripts: cold {row['cold_count']}/{row['renders']}, "
                f"LRU {row['lru_evictions']}, server_ms median {row['server_ms']['median']} p90 {row['server_ms']['p90']}"
            )
    return out


# ------------------------------- cold ---------------------------------------
def run_cold(args, plane: str, base_dir: Path) -> dict:
    runs = []
    for i in range(args.cold_runs):
        with Backend(args, plane, base_dir, f"cold-{plane}-{i}") as be:
            pid = be.panels()[0]
            r = be.render(pid, [])
            r.pop("_manifest", None)
            r["run"] = i
            r["boot_ms"] = be.boot_ms
            runs.append(r)
            log(f"  [{plane}] cold #{i}: wall {r['wall_ms']} ms, cold={r['cold']}")
    keys = sorted({k for r in runs for k in r["timings"]})
    return {
        "plane": plane,
        "runs": runs,
        "wall_ms": summarize([r["wall_ms"] for r in runs]),
        "timings_median": {
            k: median([r["timings"][k] for r in runs if k in r["timings"]]) for k in keys
        },
        "timings_max": {
            k: pct([r["timings"][k] for r in runs if k in r["timings"]], 1.0) for k in keys
        },
    }


# ------------------------------- probe --------------------------------------
PROBES = {
    "import_sys": "import sys",
    "import_numpy_matplotlib": "import numpy, matplotlib",
    "import_pyplot": "import matplotlib.pyplot",
}


def run_probe(py: str, runs: int) -> dict:
    res: dict = {"python": py, "probes": {}}
    try:
        cp = subprocess.run(
            [py, "-c", "import sys;print(sys.version.split()[0], sys.executable)"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        res["version"] = (cp.stdout or "").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        res["error"] = type(exc).__name__
        return res
    for name, code in PROBES.items():
        times: list[float] = []
        rcs: list[int] = []
        tail = ""
        for _ in range(runs):
            t0 = time.perf_counter()
            try:
                cp = subprocess.run([py, "-c", code], capture_output=True, text=True, timeout=300)
            except (OSError, subprocess.SubprocessError) as exc:
                rcs.append(-1)
                tail = type(exc).__name__
                continue
            times.append(round((time.perf_counter() - t0) * 1000, 1))
            rcs.append(cp.returncode)
            if cp.returncode:
                tail = (cp.stderr or "").strip().splitlines()[-1:] and (
                    cp.stderr or ""
                ).strip().splitlines()[-1][:200]
        res["probes"][name] = {
            "ms": times,
            "first_ms": times[0] if times else None,
            **{f"later_{k}": v for k, v in summarize(times[1:]).items()},
            "exit_codes": rcs,
            "stderr_tail": tail or None,
        }
        log(
            f"  probe {name}: first {res['probes'][name]['first_ms']} ms, later median {res['probes'][name]['later_median']}"
        )
    return res


# ------------------------------- main ---------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mode", choices=["rotation", "cold", "all"], default="all")
    ap.add_argument("--python", default=sys.executable, help="跑后端的解释器")
    ap.add_argument(
        "--worker-python", default=None, help="经 TAVOTTO_WORKER_PYTHON 指定的渲染解释器"
    )
    ap.add_argument(
        "--figures", required=True, help="图库目录（已含 tavotto_registry.json 与脚本）"
    )
    ap.add_argument("--work", default=None, help="一次性数据目录的父目录（默认系统临时目录）")
    ap.add_argument("--mplconfigdir", default=None)
    ap.add_argument("--sets", default=",".join(map(str, ROTATION_SETS)))
    ap.add_argument("--rounds", type=int, default=20)
    ap.add_argument("--cold-runs", type=int, default=5)
    ap.add_argument("--probe-runs", type=int, default=5)
    ap.add_argument(
        "--probe-python", action="append", default=[], help="可重复；默认 = 后端解释器与渲染解释器"
    )
    ap.add_argument("--planes", default="python,workerd")
    ap.add_argument("--workerd", default=None, help="tavotto-workerd 二进制；不给就跳过该控制面")
    ap.add_argument("--result", required=True)
    args = ap.parse_args(argv)
    args.figures = Path(args.figures).resolve()
    args.sets = [int(x) for x in args.sets.split(",") if x]
    if not args.figures.is_dir():
        print(f"figures dir missing: {args.figures}", file=sys.stderr)
        return 2

    result: dict = {
        "meta": {
            "platform": platform.platform(),
            "python": args.python,
            "worker_python": args.worker_python,
            "figures_path_len": len(str(args.figures)),
            "rounds": args.rounds,
            "sets": args.sets,
            "cold_runs": args.cold_runs,
        },
        "rotation": [],
        "cold": [],
        "probe": [],
        "skipped": [],
        "errors": [],
    }
    planes = [p for p in args.planes.split(",") if p in PLANES]
    active = []
    for p in planes:
        if p == "workerd" and not (args.workerd and Path(args.workerd).is_file()):
            result["skipped"].append({"plane": p, "reason": "tavotto-workerd binary not available"})
        else:
            active.append(p)

    base = Path(tempfile.mkdtemp(prefix="tavotto-rbench-", dir=args.work))
    try:
        # 面板由「脚本 + 磁盘上已有的产物」登记出来：把夹具脚本各跑一遍，在图库目录里生成它们的 PDF（一次性的，不计时）
        pdf_py = args.worker_python or args.python
        for script in sorted(args.figures.glob("*.py")):
            if not (args.figures / (script.stem + ".pdf")).exists():
                cp = subprocess.run(
                    [pdf_py, script.name],
                    cwd=args.figures,
                    env={
                        **os.environ,
                        "MPLBACKEND": "Agg",
                        **({"MPLCONFIGDIR": args.mplconfigdir} if args.mplconfigdir else {}),
                    },
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
                if cp.returncode:
                    result["errors"].append({"part": "prepare", "error": (cp.stderr or "")[-200:]})
        # 探针每个模式都跑：几秒钟，且是区分「解释器入口慢」与「导入 / 磁盘慢」的依据
        pys = args.probe_python or list(
            dict.fromkeys(p for p in (args.python, args.worker_python) if p)
        )
        for py in pys:
            result["probe"].append(run_probe(py, args.probe_runs))
        for plane in active:
            for mode, fn, key in (
                ("rotation", run_rotation, "rotation"),
                ("cold", run_cold, "cold"),
            ):
                if args.mode not in (mode, "all"):
                    continue
                try:
                    result[key].append(fn(args, plane, base))
                except Exception as exc:  # 一块失败不拖垮另一块；原因进结果
                    result["errors"].append(
                        {
                            "plane": plane,
                            "part": mode,
                            "error": f"{type(exc).__name__}: {exc}"[:300],
                        }
                    )
                    log(f"  ! {plane}/{mode}: {type(exc).__name__}: {exc}")
    finally:
        # 一次性数据目录：Windows 上进程刚退时句柄可能还在，退让几次，删不掉就留给 Lab 的 run 清理
        import shutil

        for _ in range(5):
            shutil.rmtree(base, ignore_errors=True)
            if not base.exists():
                break
            time.sleep(1)
    Path(args.result).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
