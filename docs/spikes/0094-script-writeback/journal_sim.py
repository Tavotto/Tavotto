"""ADR 0094 §五.7：脚本 + 文档两份文件的可恢复写回事务——协议模型与崩溃注入。

写回脚本要同时改两份文件：脚本（插入调整块）和画布文档（去掉已写进脚本的 override）。两者各自
原子，但**跨文件**不原子：只换了脚本、文档没写成时，重开会在「已含调整的脚本」上再应用一遍同样的
override；先写文档则是反过来的不一致（Codex #667 P1）。

本文件是这套协议的**可执行模型**（纯标准库，真文件、真 fsync、真 os.replace，在临时目录里跑），
不是产品代码。它做三件事：

1. 在提交的每一个步骤之间注入崩溃（进程在那一刻消失，内存全丢），再在恢复的每一步之间再注入一次
   崩溃，最后跑一次完整恢复；
2. 每一种组合都检查不变式：
     * 恢复完成后 (脚本, 文档) 要么都是写回前、要么都是写回后，或者脚本被外部改过、状态是
       `needs_user`（绝不自动选一边）；
     * 恢复完成之前，这张图的渲染被拒（`render_gate`）——不许在不一致的中间态上画图；
     * 自动保存槽里的旧文档（还带着已写进脚本的 override）不能被崩溃恢复拿回来；
     * 恢复是幂等的：恢复途中再崩、再恢复，结论不变；
3. `--mutate <名字>` 打掉协议里的一环，同一组检查必须变红（反证，结论看退出码）。

用法：python journal_sim.py [--mutate NAME] [--json 输出路径]；全绿退出码 0，否则 1。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

MUTATIONS = {
    "no_roll_forward": "恢复时脚本已换、文档没写：直接删日志（不补写文档）",
    "doc_first": "先写文档、后换脚本（顺序反过来）",
    "no_render_gate": "日志没收尾时照常渲染",
    "keep_autosave_slot": "不作废自动保存槽里的旧文档",
    "no_journal_fsync": "日志写了但不落盘（崩溃后日志丢失）",
}


class Crash(Exception):
    """模拟进程在这一刻消失。"""


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def write_atomic(path: Path, data: bytes, *, fsync: bool = True) -> None:
    tmp = path.with_name(f".{path.name}.tavotto-writing")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        if fsync:
            os.fsync(fh.fileno())
    os.replace(tmp, path)
    if fsync:
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class World:
    """一次写回涉及的全部持久状态：项目目录（脚本、文档、自动保存槽、项目内备份）+ 数据目录（日志、镜像备份）。"""

    def __init__(self, root: Path):
        self.root = root
        self.proj = root / "proj"
        self.data = root / "data"
        (self.proj / "tavottofile").mkdir(parents=True)
        (self.data / "script_writeback" / "journal").mkdir(parents=True)
        self.script = self.proj / "fig.py"
        self.doc = self.proj / "tavottofile" / "canvas.json"
        self.slot = self.data / "autosave" / "canvas.json"
        self.slot.parent.mkdir(parents=True)
        self.journal_dir = self.data / "script_writeback" / "journal"

    def seed(self):
        self.script_before = (
            b"import matplotlib.pyplot as plt\n\nfig, ax = plt.subplots()\nfig.savefig('f.pdf')\n"
        )
        self.script_after = self.script_before.replace(
            b"\n\nfig,",
            b"\n\n# >>> Tavotto \xe8\xb0\x83\xe6\x95\xb4 >>>\n...\n# <<< Tavotto \xe8\xb0\x83\xe6\x95\xb4 <<<\n\n\nfig,",
        )
        written = [{"gid": "axes_0.lines_0", "prop": "color", "value": "#d6278f"}]
        residual = [{"gid": "axes_0.title", "prop": "pos_frac", "value": [0.5, 0.1]}]
        self.doc_before = json.dumps(
            {"panels": {"f": {"overrides": written + residual}}}, sort_keys=True
        ).encode()
        self.doc_after = json.dumps(
            {"panels": {"f": {"overrides": residual}}}, sort_keys=True
        ).encode()
        self.written = written
        write_atomic(self.script, self.script_before)
        write_atomic(self.doc, self.doc_before)
        write_atomic(self.slot, self.doc_before)  # 前端早先的自动保存：带着要去掉的 override

    def state(self) -> tuple[str, str]:
        s = self.script.read_bytes()
        d = self.doc.read_bytes()
        ss = (
            "before"
            if s == self.script_before
            else ("after" if s == self.script_after else "other")
        )
        ds = "before" if d == self.doc_before else ("after" if d == self.doc_after else "other")
        return ss, ds


def journal_write(w: World, rec: dict, mutate: str | None) -> None:
    path = w.journal_dir / f"{rec['txn']}.json"
    write_atomic(path, json.dumps(rec, sort_keys=True).encode(), fsync=mutate != "no_journal_fsync")
    if mutate == "no_journal_fsync":
        # 没落盘 = 崩溃后可能不存在；模型取最坏情况：记一个「崩了就丢」的标记
        w.unsynced_journal = path


def commit(w: World, crash_at: int | None, mutate: str | None) -> None:
    """写回的 commit 段（verify 已过）。每个 step() 之前可能崩溃。"""
    n = 0

    def step():
        nonlocal n
        if crash_at is not None and n == crash_at:
            raise Crash(n)
        n += 1

    txn = "t1"
    rec = {
        "txn": txn,
        "state": "prepared",
        "script": str(w.script),
        "script_sha_before": sha(w.script_before),
        "script_sha_after": sha(w.script_after),
        "doc": str(w.doc),
        "doc_rev_before": sha(w.doc_before),
        "doc_rev_after": sha(w.doc_after),
        "removed": w.written,
    }
    step()  # 0
    journal_write(w, rec, mutate)
    step()  # 1
    bdir = w.proj / "tavottofile" / "script-backups" / txn
    mdir = w.data / "script_backups" / txn
    for d in (bdir, mdir):
        d.mkdir(parents=True, exist_ok=True)
        write_atomic(d / "original.py", w.script_before)
    step()  # 2
    rec["state"] = "backed_up"
    journal_write(w, rec, mutate)
    step()  # 3
    first, second = (w.doc, w.script) if mutate == "doc_first" else (w.script, w.doc)
    data = {w.script: w.script_after, w.doc: w.doc_after}
    write_atomic(first, data[first])
    step()  # 4
    rec["state"] = "script_done"
    journal_write(w, rec, mutate)
    step()  # 5
    write_atomic(second, data[second])
    step()  # 6
    if mutate != "keep_autosave_slot":
        rec["state"] = "doc_done"
        journal_write(w, rec, mutate)
        step()  # 7
        # 自动保存槽里是写回前的文档：作废（产品里是给槽打上「被 txn 取代」标记，恢复界面不再提供）
        if w.slot.exists():
            w.slot.unlink()
    step()  # 8
    (w.journal_dir / f"{txn}.json").unlink()
    step()  # 9


COMMIT_STEPS = 10


def recover(w: World, crash_at: int | None, mutate: str | None) -> list[str]:
    """项目打开 / 渲染这张图之前跑。返回需要用户处理的 txn。每个 step() 之前可能崩溃。"""
    n = 0

    def step():
        nonlocal n
        if crash_at is not None and n == crash_at:
            raise Crash(n)
        n += 1

    needs_user = []
    for jp in sorted(w.journal_dir.glob("*.json")):
        rec = json.loads(jp.read_bytes())
        if rec.get("state") == "needs_user":
            needs_user.append(rec["txn"])
            continue
        s = sha(Path(rec["script"]).read_bytes())
        d = sha(Path(rec["doc"]).read_bytes())
        step()
        if s == rec["script_sha_before"]:
            # 脚本还没换：整次写回等于没发生。文档也必须还是写回前（它排在脚本之后写）。
            if d != rec["doc_rev_before"]:
                rec["state"] = "needs_user"
                write_atomic(jp, json.dumps(rec, sort_keys=True).encode())
                needs_user.append(rec["txn"])
                continue
            for d_ in (
                w.proj / "tavottofile" / "script-backups" / rec["txn"],
                w.data / "script_backups" / rec["txn"],
            ):
                shutil.rmtree(d_, ignore_errors=True)
            step()
            jp.unlink()
            step()
        elif s == rec["script_sha_after"]:
            if mutate == "no_roll_forward":
                jp.unlink()
                continue
            # 脚本已换：补完文档（只去掉日志里点名的那几条、按 (gid, prop, value) 认），作废自动保存槽
            if d != rec["doc_rev_after"]:
                doc = json.loads(Path(rec["doc"]).read_bytes())
                gone = {(r["gid"], r["prop"], json.dumps(r["value"])) for r in rec["removed"]}
                for panel in doc["panels"].values():
                    panel["overrides"] = [
                        o
                        for o in panel["overrides"]
                        if (o["gid"], o["prop"], json.dumps(o["value"])) not in gone
                    ]
                write_atomic(Path(rec["doc"]), json.dumps(doc, sort_keys=True).encode())
            step()
            if mutate != "keep_autosave_slot" and w.slot.exists():
                w.slot.unlink()
            step()
            jp.unlink()
            step()
        else:
            # 脚本在崩溃之后被外部改过：绝不自动选一边
            rec["state"] = "needs_user"
            write_atomic(jp, json.dumps(rec, sort_keys=True).encode())
            needs_user.append(rec["txn"])
    return needs_user


RECOVER_STEPS = 4


def render_allowed(w: World, mutate: str | None) -> bool:
    """渲染闸：这个项目有没收尾的写回日志时，这张图不渲染（先恢复）。"""
    if mutate == "no_render_gate":
        return True
    return not any(w.journal_dir.glob("*.json"))


def simulate_crash(w: World):
    """进程消失：没 fsync 的日志在最坏情况下不存在。"""
    p = getattr(w, "unsynced_journal", None)
    if p is not None and p.exists():
        p.unlink()


def check_case(
    tmp: Path, crash_commit: int, crash_recover: int | None, mutate: str | None
) -> list[str]:
    w = World(tmp)
    w.seed()
    errs: list[str] = []
    try:
        commit(w, crash_commit, mutate)
    except Crash:
        simulate_crash(w)
    # ① 崩溃之后、恢复之前：渲染闸
    ss, ds = w.state()
    if (ss, ds) not in (("before", "before"), ("after", "after")) and render_allowed(w, mutate):
        errs.append(f"恢复前可渲染的不一致态 script={ss} doc={ds}")
    # ② 恢复途中再崩一次，再恢复
    if crash_recover is not None:
        try:
            recover(w, crash_recover, mutate)
        except Crash:
            pass
    needs = recover(w, None, mutate)
    ss, ds = w.state()
    if needs:
        errs.append("没有外部改动，恢复却停下来要用户处理（协议自己制造了分叉）")
    if not needs and (ss, ds) not in (("before", "before"), ("after", "after")):
        errs.append(f"恢复后不一致 script={ss} doc={ds}")
    # ③ 自动保存槽：脚本已含调整时，槽里带着已写入 override 的旧文档不许留着给崩溃恢复拿回
    if ss == "after" and w.slot.exists() and w.slot.read_bytes() == w.doc_before:
        errs.append("自动保存槽里仍是写回前的文档（崩溃恢复会把已写进脚本的 override 拿回来）")
    # ④ 幂等：再恢复一次，状态不变
    again = recover(w, None, mutate)
    if w.state() != (ss, ds) or again != needs:
        errs.append("恢复不幂等")
    return errs


def run(mutate: str | None) -> dict:
    cases = []
    for c in list(range(COMMIT_STEPS)) + [None]:
        for r in [None] + list(range(RECOVER_STEPS)):
            with tempfile.TemporaryDirectory() as t:
                errs = check_case(Path(t), c, r, mutate)
            cases.append({"crash_commit_at": c, "crash_recover_at": r, "errors": errs})
    # 外部改动：写回在「脚本已换、文档未写」处崩溃后，用户在编辑器里又改了脚本
    with tempfile.TemporaryDirectory() as t:
        w = World(Path(t))
        w.seed()
        try:
            commit(w, 5, mutate)
        except Crash:
            simulate_crash(w)
        w.script.write_bytes(w.script_after + b"# edited\n")
        needs = recover(w, None, mutate)
        errs = (
            []
            if needs == ["t1"] and not render_allowed(w, mutate)
            else ["外部改过脚本时没有停下来交给用户"]
        )
        cases.append(
            {"crash_commit_at": 5, "crash_recover_at": None, "external_edit": True, "errors": errs}
        )
    failed = [c for c in cases if c["errors"]]
    return {"mutation": mutate, "cases": len(cases), "failed": len(failed), "failures": failed[:5]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mutate", choices=sorted(MUTATIONS))
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    res = run(a.mutate)
    print(f"mutation={res['mutation']} cases={res['cases']} failed={res['failed']}")
    for f in res["failures"][:3]:
        print("  ", f)
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
