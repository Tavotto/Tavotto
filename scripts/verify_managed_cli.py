#!/usr/bin/env python3
"""真机验收应用内托管安装（`engine/managedcli.py`）：按锁从**指定的一个来源**真下载、校验、解包、`--version`。

    python scripts/verify_managed_cli.py --source npmjs    # 只走 registry.npmjs.org
    python scripts/verify_managed_cli.py --source mirror   # 只走 registry.npmmirror.com
    python scripts/verify_managed_cli.py --source all      # 依次各走一遍

每个来源用一个**新建的一次性数据目录**（`TAVOTTO_DATA_DIR`），走产品自己的 `managedcli.provision(cli=…)`——同一套
归档 sha256、解包成员校验、整棵树摘要与 `--version` 探测；之后本脚本再独立复算一遍装好的整棵树摘要，与锁里的
`tree_sha256` 比，并跑一次 `--version` 核版本号。只下载安装，不登录、不调模型。数据目录验完即删（每份几百 MB）。

stdout 最后一行是 JSON：`{"cli", "version", "results": [{source, ok, …}], "ok"}`；全部通过退出码 0。
Windows Lab 的 `managed-cli` 任务调它（#1739）。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def verify_one(name: str, key: str, base: Path) -> dict:
    """在 `base` 下的新数据目录里只用 `key` 这个来源装一次。不抛：失败写进结果。"""
    from tavotto.engine import managedcli as mc

    cli = mc.cli_for(name)
    if cli is None:
        return {"source": key, "ok": False, "error": "unsupported_platform"}
    keys = [d["key"] for d in mc.load_lock()["clis"][name]["sources"]]
    if key not in keys:
        return {"source": key, "ok": False, "error": f"no source {key!r} in lock ({keys})"}
    one = dataclasses.replace(cli, sources=(cli.sources[keys.index(key)],))
    data = base / f"data-{key}"
    os.environ["TAVOTTO_DATA_DIR"] = str(data)
    out: dict = {"source": key, "host": one.sources[0].host, "expected_id": cli.id}
    # root_dir 在 `cli/<名>/` 不存在时会去 MSIX 宿主私有区搬已有安装（Windows），那样 provision 不下载就回「已装」，
    # 不通的来源也会被报成通过。先建好这个目录（路径与 root_dir 同源：data_dir()/DIRNAME/名）挡住搬迁。
    (mc.config.data_dir() / mc.DIRNAME / name).mkdir(parents=True, exist_ok=True)
    downloads = 0
    real_download = mc._download

    def counting_download(*a, **kw):
        nonlocal downloads
        downloads += 1
        return real_download(*a, **kw)

    t0 = time.monotonic()
    try:
        mc._download = counting_download
        try:
            entry = Path(mc.provision(name, cli=one))
        finally:
            mc._download = real_download
        out["downloads"] = downloads
        if downloads < 1:
            out.update(ok=False, error="no_download", seconds=round(time.monotonic() - t0, 1))
            return out
        out["seconds"] = round(time.monotonic() - t0, 1)
        installed = entry.parent
        while installed.parent.name != name:  # entry = <root>/<name>/<id>/<entrypoint 的相对路径>
            installed = installed.parent
        out["installed_id"] = installed.name
        tree, files, size = mc.tree_digest(installed)
        out.update(tree_ok=tree == cli.tree_sha256, files=files, unpacked_size=size)
        ver = subprocess.run(
            [str(entry), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            stdin=subprocess.DEVNULL,
        )
        out["version_output"] = (ver.stdout or ver.stderr).strip()[:80]
        out["version_ok"] = ver.returncode == 0 and cli.version in out["version_output"]
        out["ok"] = bool(out["tree_ok"] and out["version_ok"] and out["installed_id"] == cli.id)
    except mc.ManagedCliError as exc:
        out.update(ok=False, error=exc.code, seconds=round(time.monotonic() - t0, 1))
    except (OSError, subprocess.SubprocessError) as exc:
        out.update(ok=False, error=type(exc).__name__, seconds=round(time.monotonic() - t0, 1))
    finally:
        shutil.rmtree(data, ignore_errors=True)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--cli", default="codex", choices=("codex", "claude"))
    ap.add_argument("--source", default="all", help="锁里的来源键（如 npmjs / mirror），或 all")
    args = ap.parse_args(argv)
    os.environ.setdefault("TAVOTTO_NO_TELEMETRY", "1")
    from tavotto.engine import managedcli as mc

    lock = mc.load_lock()["clis"][args.cli]
    keys = [d["key"] for d in lock["sources"]] if args.source == "all" else [args.source]
    with tempfile.TemporaryDirectory(prefix="tavotto-managed-cli-") as td:
        results = [verify_one(args.cli, k, Path(td)) for k in keys]
    report = {
        "cli": args.cli,
        "version": lock["version"],
        "results": results,
        "ok": all(r.get("ok") for r in results),
    }
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
