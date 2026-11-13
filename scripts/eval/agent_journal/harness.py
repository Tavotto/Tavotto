"""评测：Agent 单独把图改到期刊合规 vs Agent + Tavotto（外部分析第 5 条，2026-11-12 用户拍板）。

被测 agent：`opencode run -m opencode-go/deepseek-v4-pro`（不用 Claude）。判分**只用 Tavotto 预检**：
无头起 `tavotto mcp`（stdio），`tavotto_open_figure(script_path)` → `tavotto_preflight(journal_id)`，
读 error / warn 计数与规则名。两组条件拿到的是同一份期刊规则原文（内置期刊库里的引文与数值）。

子命令：
  corpus   按 briefs.json 让 agent 各写一张图（= 「agent 生成的图」），存进 corpus/<id>/fig.py
  judge    给一个目录（含 fig.py）与 journal_id，打印判分
  run      跑条件 A（agent 单独）或 B（agent + Tavotto MCP），结果追加到 results/<cond>.jsonl
  report   汇总 results/*.jsonl

环境变量：
  TAVOTTO_MCP    判分 / B 组用的 MCP 启动命令（空格分隔），缺省 `uvx -q --from <wheel>[worker] tavotto mcp`
  TAVOTTO_WHEEL  上面缺省命令里的 wheel 路径
  AGENT_PYTHON   给 agent 用的 python（要有 numpy / matplotlib / scipy）
  EVAL_MODEL     缺省 opencode-go/deepseek-v4-pro
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
#: 语料：agent 写的脚本原样存成字符串（不落成 .py 进仓库——它们是数据，不该被 ruff 改写）
CORPUS_JSON = HERE / "corpus.json"
RESULTS = HERE / "results"
RUNS = Path(os.environ.get("EVAL_RUNS", "/tmp/tavotto-eval-runs"))
CORPUS = RUNS / "_corpus"  # 运行时从 corpus.json 写出来
MODEL = os.environ.get("EVAL_MODEL", "opencode-go/deepseek-v4-pro")
JOURNALS = ["nature", "journal-of-the-american-chemical-society", "jmst"]


# ---------------------------------------------------------------- MCP 判分


def mcp_cmd() -> list[str]:
    raw = os.environ.get("TAVOTTO_MCP")
    if raw:
        return raw.split()
    wheel = os.environ.get("TAVOTTO_WHEEL")
    if not wheel:
        sys.exit("设 TAVOTTO_WHEEL（main 上 `uv build --wheel` 出的 wheel）或 TAVOTTO_MCP")
    # scipy：语料里有的脚本用它（与 AGENT_PYTHON 同一套科学栈）
    return ["uvx", "-q", "--from", f"{wheel}[worker]", "--with", "scipy", "tavotto", "mcp"]


class Mcp:
    """stdio 上的最小 MCP 客户端。服务端 stdout 落临时文件、这边跟着读（仓库规则：scripts/ 的子进程不开输出管道）。"""

    def __init__(self, cwd: Path, env: dict | None = None):
        self._tmp = tempfile.TemporaryDirectory(prefix="tavotto-eval-mcp-")
        out = Path(self._tmp.name) / "stdout.jsonl"
        self._w = out.open("w", encoding="utf-8")
        self.p = subprocess.Popen(
            mcp_cmd(),
            cwd=cwd,
            env={**os.environ, **(env or {})},
            stdin=subprocess.PIPE,
            stdout=self._w,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        )
        self._r = out.open("r", encoding="utf-8")
        self._buf = ""
        self.i = 0
        self.call_raw(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "tavotto-eval", "version": "0"},
            },
        )
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, msg: dict) -> None:
        assert self.p.stdin
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()

    def call_raw(self, method: str, params: dict) -> dict:
        self.i += 1
        self._send({"jsonrpc": "2.0", "id": self.i, "method": method, "params": params})
        while True:
            line = self._readline()
            r = json.loads(line)
            if r.get("id") == self.i:
                return r

    def _readline(self) -> str:
        """读到一整行为止；文件里暂时没有新行就等一下，服务端退出了还没有就报错。"""
        exited = False
        while True:
            chunk = self._r.readline()
            self._buf += chunk
            if self._buf.endswith("\n"):
                line, self._buf = self._buf, ""
                return line
            if not chunk:
                if exited:
                    raise RuntimeError("MCP 服务端退出了")
                # 先记下退出，再读一轮：退出前写下的最后一行不会漏掉
                exited = self.p.poll() is not None
                if not exited:
                    time.sleep(0.02)

    def tool(self, name: str, **args) -> dict:
        r = self.call_raw("tools/call", {"name": name, "arguments": args})
        res = r.get("result") or {}
        sc = res.get("structuredContent") or {}
        if res.get("isError") or r.get("error"):
            sc = {"ok": False, **sc, "_error": r.get("error") or res.get("content")}
        return sc

    def close(self) -> None:
        if self.p.stdin:
            self.p.stdin.close()
        try:
            self.p.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait()
        self._r.close()
        self._w.close()
        self._tmp.cleanup()


def _roles(manifest: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in manifest.get("elements", []):
        out[e.get("role", "?")] = out.get(e.get("role", "?"), 0) + 1
    return out


def latest_session(data_dir: Path | None) -> str | None:
    """B 组：agent 那个 MCP 最后落盘的会话（Tavotto 的修改只在会话里，不写回 fig.py；ADR 0078 落盘记录）。"""
    if data_dir is None:
        return None
    best = None
    for f in (data_dir / "mcp-sessions").glob("s-*.json"):
        try:
            rec = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if best is None or rec.get("saved_at", 0) > best[0]:
            best = (rec.get("saved_at", 0), rec.get("id"))
    return best[1] if best else None


def judge(run_dir: Path, journal_id: str, data_dir: Path | None = None) -> dict:
    """对 run_dir/fig.py 现在画出来的图按 journal_id 判分。

    给了 `data_dir`（B 组）且 agent 在 Tavotto 里留下过会话：按落盘记录恢复**最后那个会话**判分——
    脚本现状 + 会话里的修改，就是用户在 Tavotto 里导出会得到的图；没有会话就照常只跑 fig.py。"""
    m = Mcp(run_dir, {"TAVOTTO_DATA_DIR": str(data_dir)} if data_dir else None)
    try:
        sid = latest_session(data_dir)
        via = "session"
        opened = m.tool("tavotto_session_state", session_id=sid) if sid else {"ok": False}
        if not opened.get("ok"):
            via = "script"
            opened = m.tool("tavotto_open_figure", script_path=str(run_dir / "fig.py"))
        if not opened.get("ok"):
            return {"opened": False, "error": str(opened)[:600]}
        sid = opened.get("session_id") or sid
        pf = m.tool("tavotto_preflight", session_id=sid, journal_id=journal_id)
        # 对照：同一张图在**中性底规范**（期刊未规定的项一律不判）下的问题。期刊带来的问题 =
        # 期刊下有、底规范下没有的那些。通用质量提示（文字重叠、柱子没误差棒……）期刊原文里没写，
        # A 组拿不到这份信息，不能算进「期刊合规」
        base = m.tool("tavotto_preflight", session_id=sid, profile_id=pf["profile"]["derived_from"])
        rules = lambda k: sorted({i.get("id") for i in pf.get(k, [])})  # noqa: E731
        key = lambda i: (i.get("id"), tuple(i.get("gids") or []))  # noqa: E731
        base_keys = {key(i) for k in ("errors", "warnings") for i in base.get(k, [])}
        journal_issues = sorted(
            {
                i.get("id")
                for k in ("errors", "warnings")
                for i in pf.get(k, [])
                if key(i) not in base_keys
            }
        )
        counts = pf.get("counts", {})
        return {
            "opened": True,
            "size_mm": pf.get("size_mm"),
            "counts": counts,
            "journal_issues": journal_issues,
            "pass": not journal_issues,
            "pass_all": counts.get("error", 0) == 0 and counts.get("warn", 0) == 0,
            "error_rules": rules("errors"),
            "warn_rules": rules("warnings"),
            "roles": _roles(opened.get("manifest") or {}),
            "via": via,
        }
    finally:
        m.close()


# ---------------------------------------------------------------- 期刊规则原文


def journal_text(journal_id: str) -> tuple[str, str]:
    """(期刊名, 给 agent 的规则原文)。出处：Tavotto 内置期刊库（与判分同一份）。"""
    sys.path.insert(0, str(HERE.parents[2] / "src"))
    from tavotto.engine import journalremote

    j = journalremote.seed_catalog()["journals"][journal_id]
    lines = []
    for key, rule in j.get("rules", {}).items():
        quote = rule.get("quote")
        val = rule.get("value")
        tag = (
            ""
            if rule.get("qualifier") in (None, "required", "exact")
            else f" ({rule['qualifier']})"
        )
        if isinstance(val, list) and val and isinstance(val[0], dict):  # page_columns
            val = ", ".join(
                f"{c.get('label', {}).get('en', c.get('id'))} {c.get('width_mm')} mm"
                + (f" ({c['qualifier']})" if c.get("qualifier") else "")
                for c in val
            )
        elif isinstance(val, list):  # 字体名单
            val = " or ".join(map(str, val))
        lines.append(f"- {key}{tag}: {val}" + (f'\n  Guideline text: "{quote}"' if quote else ""))
    return j["label"], "\n".join(lines)


# ---------------------------------------------------------------- opencode


#: 被测 agent 的权限：只许改本目录的文件、跑 `python fig.py`；别的 shell 命令、联网一律拒（不用 --auto）
PERMISSION = {
    "edit": "allow",
    # 这一版 opencode 的 shell 工具叫 `shell`；旧名 `bash` 也写上，哪个生效都拦得住
    "bash": {"*": "deny", "python fig.py": "allow", "python3 fig.py": "allow"},
    "shell": {"*": "deny", "python fig.py": "allow", "python3 fig.py": "allow"},
    # 代码模式工具能在 JS 里转手调 tools.shell，绕过上面的 shell 规则：整个拒掉
    "execute": "deny",
    "webfetch": "deny",
    "websearch": "deny",
    "external_directory": "deny",
}


def write_config(cwd: Path, mcp: list[str] | None = None, mcp_env: dict | None = None) -> None:
    cfg: dict = {"$schema": "https://opencode.ai/config.json", "permission": PERMISSION}
    if mcp:
        cfg["mcp"] = {"tavotto": {"type": "local", "command": mcp, "enabled": True}}
        if mcp_env:
            cfg["mcp"]["tavotto"]["environment"] = mcp_env
    (cwd / "opencode.json").write_text(json.dumps(cfg, indent=1))


def opencode(cwd: Path, prompt: str, timeout: int = 900) -> dict:
    if not (cwd / "opencode.json").exists():
        write_config(cwd)
    env = {
        **os.environ,
        "OPENCODE_DISABLE_CLAUDE_CODE": "1",
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        # opencode 按 PWD 认项目目录，subprocess 的 cwd 改不到它：不设的话 agent 会跑进调用方目录（2026-11-12 实测）
        "PWD": str(cwd),
    }
    py = os.environ.get("AGENT_PYTHON")
    if py:
        env["PATH"] = f"{Path(py).parent}:{env['PATH']}"
    t0 = time.time()
    try:
        out = subprocess.run(
            # --standalone：私有服务、以 cwd 为项目目录。不加时连的是后台常驻服务，
            # agent 会跑到调用方的目录里去干活，运行目录里的权限配置也读不到（2026-11-12 实测）
            ["opencode", "run", "--standalone", "-m", MODEL, "--format", "json", prompt],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        text, timed_out = out.stdout + out.stderr, False
    except subprocess.TimeoutExpired as e:
        text, timed_out = (
            (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""),
            True,
        )
    steps = tools = tin = tout = 0
    tool_names: list[str] = []
    errors: list[str] = []
    for line in text.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            if "Error:" in line:
                errors.append(line[:300])
            continue
        part = ev.get("part") or {}
        if ev.get("type") == "step_finish":
            steps += 1
            tk = part.get("tokens") or {}
            tin += tk.get("input", 0)
            tout += tk.get("output", 0) + tk.get("reasoning", 0)
        if part.get("type") == "tool":
            tools += 1
            tool_names.append(part.get("tool", "?"))
        if ev.get("type") == "error":
            errors.append(json.dumps(ev)[:300])
    (cwd / "agent.log").write_text(text)
    # agent 有没有写到运行目录以外：任何一次 write / edit 的路径不在 cwd 下，这一组作废
    strays = sorted(
        {
            p
            for p in _written_paths(text)
            if not Path(p if os.path.isabs(p) else cwd / p).resolve().is_relative_to(cwd.resolve())
        }
    )
    return {
        "wall_s": round(time.time() - t0, 1),
        "steps": steps,
        "tool_calls": tools,
        "tools": tool_names,
        "tokens_in": tin,
        "tokens_out": tout,
        "timed_out": timed_out,
        "agent_errors": errors[:5],
        "stray_writes": strays,
    }


def _written_paths(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        try:
            part = json.loads(line).get("part") or {}
        except ValueError:
            continue
        if part.get("type") == "tool" and part.get("tool") in ("write", "edit", "patch"):
            inp = (part.get("state") or {}).get("input") or {}
            for k in ("path", "filePath", "file_path"):
                if inp.get(k):
                    out.append(inp[k])
    return out


# ---------------------------------------------------------------- 子命令


def materialize() -> None:
    """corpus.json → RUNS/_corpus/<id>/fig.py。"""
    data = json.loads(CORPUS_JSON.read_text()) if CORPUS_JSON.exists() else {}
    for item, src in data.items():
        (CORPUS / item).mkdir(parents=True, exist_ok=True)
        (CORPUS / item / "fig.py").write_text(src)


def cmd_corpus(args) -> None:
    briefs = json.loads((HERE / "briefs.json").read_text())
    if args.only:
        briefs = [b for b in briefs if b["id"] in args.only]

    def one(b):
        if (
            b["id"] in json.loads(CORPUS_JSON.read_text() if CORPUS_JSON.exists() else "{}")
            and not args.force
        ):
            return b["id"], "exists"
        work = RUNS / "corpus" / b["id"]
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        prompt = (
            f"Write a Python script fig.py in the current directory that makes this figure with "
            f"matplotlib: {b['brief']} Use synthetic data generated in the script with a fixed "
            f"numpy random seed (no input files). The script must save the figure to fig.png. "
            f"Run `python fig.py` to make sure it works. Reply DONE when finished."
        )
        meta = opencode(work, prompt)
        if not (work / "fig.py").exists():
            return b["id"], f"no fig.py ({meta['agent_errors']})"
        return b["id"], (work / "fig.py").read_text()

    data = json.loads(CORPUS_JSON.read_text()) if CORPUS_JSON.exists() else {}
    with cf.ThreadPoolExecutor(args.jobs) as ex:
        for bid, st in ex.map(one, briefs):
            if st != "exists" and not st.startswith("no fig.py"):
                data[bid], st = st, "ok"
            print(bid, st, flush=True)
    CORPUS_JSON.write_text(
        json.dumps(dict(sorted(data.items())), ensure_ascii=False, indent=1) + "\n"
    )


def cmd_judge(args) -> None:
    print(json.dumps(judge(Path(args.dir).resolve(), args.journal), ensure_ascii=False, indent=1))


PROMPT_A = """fig.py in the current directory makes a figure that will be submitted to the journal {label}.
The journal's figure requirements (quoted from its author guidelines):
{rules}

Edit fig.py so that the figure it produces complies with these requirements. Keep the data and the
scientific content of the figure unchanged. You can run `python fig.py` to check your work.
Reply DONE when finished."""

PROMPT_B = """fig.py in the current directory makes a figure that will be submitted to the journal {label}
(Tavotto journal id: `{jid}`). The journal's figure requirements (quoted from its author guidelines):
{rules}

Make the figure comply with these requirements. Keep the data and the scientific content of the figure
unchanged. The Tavotto MCP tools are available for opening, checking, fixing and exporting matplotlib
figures. Leave the Tavotto session open when you finish. Reply DONE when finished."""
#: B 组提示词的版本：2 = 加了「做完别关 Tavotto 会话」（首批 32 组是 1，关会话会丢掉没导出的修改）
PROMPT_B_REV = 2


def cmd_run(args) -> None:
    materialize()
    items = sorted(p.name for p in CORPUS.iterdir() if (p / "fig.py").exists())
    if args.only:
        items = [i for i in items if i in args.only]
    journals = args.journals or JOURNALS
    RESULTS.mkdir(exist_ok=True)
    out_path = RESULTS / f"{args.cond}.jsonl"
    done = set()
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            r = json.loads(line)
            done.add((r["item"], r["journal"]))
    todo = [(i, j) for i in items for j in journals if (i, j) not in done]
    print(f"{len(todo)} 组待跑（已有 {len(done)}）", flush=True)

    def one(pair):
        item, jid = pair
        work = RUNS / args.cond / f"{item}__{jid}"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        shutil.copy(CORPUS / item / "fig.py", work / "fig.py")
        before = judge(work, jid)
        label, rules = journal_text(jid)
        rec = {"cond": args.cond, "item": item, "journal": jid, "model": MODEL, "before": before}
        if args.cond == "baseline":
            rec["after"] = before
            return rec
        if args.cond == "A":
            prompt = PROMPT_A.format(label=label, rules=rules)
            data_dir = None
        else:
            # 每次运行一份独立的 Tavotto 数据目录（放在运行目录外，agent 的文件工具够不着）
            data_dir = RUNS / args.cond / "_data" / f"{item}__{jid}"
            shutil.rmtree(data_dir, ignore_errors=True)
            data_dir.mkdir(parents=True)
            write_config(work, mcp_cmd(), {"TAVOTTO_DATA_DIR": str(data_dir)})
            prompt = PROMPT_B.format(label=label, jid=jid, rules=rules)
            rec["prompt_rev"] = PROMPT_B_REV
        rec["agent"] = opencode(work, prompt)
        rec["after"] = judge(work, jid, data_dir)
        return rec

    with cf.ThreadPoolExecutor(args.jobs) as ex, out_path.open("a") as fh:
        for rec in ex.map(one, todo):
            errs = (rec.get("agent") or {}).get("agent_errors") or []
            if any("go-limit" in e or "usage limit" in e for e in errs):
                # 模型额度用完：这一组 agent 一步没走，不记结果（下次 `run` 会补跑）
                print(rec["item"], rec["journal"], "SKIP 模型额度用完", flush=True)
                continue
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            a = rec["after"]
            print(
                rec["item"],
                rec["journal"],
                "PASS" if a.get("pass") else "fail",
                a.get("counts"),
                (rec.get("agent") or {}).get("tool_calls"),
                flush=True,
            )


def cmd_rejudge(args) -> None:
    """判分条件变了（不重跑 agent）：按运行目录里现有的 fig.py 重判 results/<cond>.jsonl 的 after。"""
    materialize()
    path = RESULTS / f"{args.cond}.jsonl"
    recs = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]

    def one(r):
        work = RUNS / args.cond / f"{r['item']}__{r['journal']}"
        data_dir = RUNS / args.cond / "_data" / f"{r['item']}__{r['journal']}"
        r["after"] = judge(work, r["journal"], data_dir if args.cond == "B" else None)
        if args.cond == "baseline":
            r["before"] = r["after"]
        else:
            orig = RUNS / "_orig" / f"{r['item']}__{r['journal']}"
            orig.mkdir(parents=True, exist_ok=True)
            shutil.copy(CORPUS / r["item"] / "fig.py", orig / "fig.py")
            r["before"] = judge(orig, r["journal"])
        return r

    with cf.ThreadPoolExecutor(args.jobs) as ex:
        recs = list(ex.map(one, recs))
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs))


def cmd_report(args) -> None:
    for f in sorted(RESULTS.glob("*.jsonl")):
        recs = [json.loads(x) for x in f.read_text().splitlines() if x.strip()]
        n = len(recs)
        if not n:
            continue
        passed = sum(1 for r in recs if r["after"].get("pass"))
        passed_all = sum(1 for r in recs if r["after"].get("pass_all"))
        noopen = sum(1 for r in recs if r["after"].get("opened") is False)
        # 结构变化：刻度标签数随刻度密度变，不算；其余角色（线、柱、图例、子图……）个数变了才算
        strip = lambda roles: {k: v for k, v in (roles or {}).items() if k != "ticklabel"}  # noqa: E731
        broke = sum(
            1
            for r in recs
            if r["after"].get("opened")
            and strip(r["after"].get("roles")) != strip(r["before"].get("roles"))
        )
        scored = n - noopen
        ag = [r["agent"] for r in recs if r.get("agent")]
        avg = lambda k: round(sum(a[k] for a in ag) / len(ag), 1) if ag else None  # noqa: E731
        print(
            f"{f.stem}: n={n} Tavotto 打不开={noopen} 可判={scored} 期刊合规={passed} "
            f"({passed / max(scored, 1):.0%} of 可判) 连通用提示也全过={passed_all} 结构变了={broke} "
            f"tool_calls={avg('tool_calls')} steps={avg('steps')} wall_s={avg('wall_s')} "
            f"tokens_in={avg('tokens_in')}"
        )
        by_j: dict[str, list] = {}
        for r in recs:
            by_j.setdefault(r["journal"], []).append(r["after"].get("pass"))
        print("   按期刊:", {j: f"{sum(map(bool, v))}/{len(v)}" for j, v in by_j.items()})
        left: dict[str, int] = {}
        for r in recs:
            for rule in r["after"].get("journal_issues", []):
                left[rule] = left.get(rule, 0) + 1
        print("   剩下的期刊问题:", dict(sorted(left.items(), key=lambda kv: -kv[1])))
        via = [r["after"].get("via") for r in recs if r["after"].get("via")]
        if any(v == "session" for v in via):
            print("   判分来源:", {v: via.count(v) for v in sorted(set(via))})
        tools: dict[str, int] = {}
        for a in ag:
            for t in a.get("tools") or []:
                tools[t] = tools.get(t, 0) + 1
        if tools:
            print("   工具调用:", dict(sorted(tools.items(), key=lambda kv: -kv[1])))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("corpus")
    c.add_argument("--only", nargs="*")
    c.add_argument("--force", action="store_true")
    c.add_argument("--jobs", type=int, default=3)
    j = sub.add_parser("judge")
    j.add_argument("dir")
    j.add_argument("journal")
    r = sub.add_parser("run")
    r.add_argument("cond", choices=["baseline", "A", "B"])
    r.add_argument("--only", nargs="*")
    r.add_argument("--journals", nargs="*")
    r.add_argument("--jobs", type=int, default=3)
    rj = sub.add_parser("rejudge")
    rj.add_argument("cond", choices=["baseline", "A", "B"])
    rj.add_argument("--jobs", type=int, default=4)
    sub.add_parser("report")
    a = ap.parse_args()
    {
        "corpus": cmd_corpus,
        "judge": cmd_judge,
        "run": cmd_run,
        "rejudge": cmd_rejudge,
        "report": cmd_report,
    }[a.cmd](a)


if __name__ == "__main__":
    main()
