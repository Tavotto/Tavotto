#!/usr/bin/env python3
"""beta 分支专用（不合进 main）：Tavotto Beta 的配置 / 数据目录与正式版分开。

壳（src-tauri/src/sidecar/beta_dirs.rs）在没设时给 sidecar 补上
TAVOTTO_CONFIG_DIR / TAVOTTO_DATA_DIR = ~/Library/Application Support/Tavotto Beta。
这里在一个临时 HOME 下照壳的补法起冻结 sidecar，验三件事：
  1. 后端自己报告的数据 / 配置目录（/api/diagnostics/summary 的 paths）落在 Tavotto Beta 下；
  2. 跑完后临时 HOME 里正式版的 Application Support/Tavotto 不存在（没有任何东西写过去）；
  3. 用户真实 config.json 的 mtime 由 package_beta.sh 在外面前后比对。
壳本身的补法（含「用户显式设置就不动」）由 beta_dirs.rs 的 cargo 单测看护；这里不起 GUI。

用法：python scripts/beta_isolation_check.py --exe <.app>/Contents/Resources/sidecar/Tavotto/Tavotto
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import smoke_app  # noqa: E402

BETA = "Tavotto Beta"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    args = ap.parse_args()

    with tempfile.TemporaryDirectory(prefix="beta-iso-") as tmp:
        home = Path(tmp) / "home"
        support = home / "Library" / "Application Support"
        beta_dir = support / BETA
        home.mkdir(parents=True)
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("TAVOTTO_CONFIG_DIR", "TAVOTTO_DATA_DIR", *smoke_app._HOSTILE_TO_BUNDLED)
        }
        env.update(
            HOME=str(home),
            TAVOTTO_CONFIG_DIR=str(beta_dir),
            TAVOTTO_DATA_DIR=str(beta_dir),
            TAVOTTO_NO_UPDATE_CHECK="1",
            TAVOTTO_NO_TELEMETRY="1",
        )
        port = smoke_app._free_port()
        log = (Path(tmp) / "server.log").open("w", encoding="utf-8")
        proc = subprocess.Popen(
            [args.exe, "--port", str(port), "--no-browser"], env=env, stdout=log, stderr=subprocess.STDOUT
        )
        try:
            base = f"http://127.0.0.1:{port}"
            smoke_app._wait_ready(base, proc, smoke_app.BOOT_TIMEOUT_S)
            if not smoke_app.adopt_session_credentials(beta_dir, port):
                raise SystemExit(f"✗ 会话凭据不在 {beta_dir}——数据目录没落到 beta 目录")
            paths = smoke_app._get(f"{base}/api/diagnostics/summary", timeout=60)["report"]["paths"]
            print(f"· 后端报告：data_dir={paths['data_dir']}  config_dir={paths['config_dir']}")
            for key in ("data_dir", "config_dir"):
                if not paths[key].rstrip("/\\").endswith(BETA):
                    raise SystemExit(f"✗ {key} 不在 {BETA} 下：{paths[key]}")
        finally:
            proc.terminate()
            try:
                proc.wait(30)
            except subprocess.TimeoutExpired:
                proc.kill()
            log.close()
            time.sleep(0.5)
        official = support / "Tavotto"
        if official.exists():
            raise SystemExit(f"✗ 正式版目录被写过：{official} → {sorted(p.name for p in official.iterdir())}")
        print(f"✓ 数据 / 配置目录都在 {BETA} 下；正式版的 Application Support/Tavotto 没被创建")
    return 0


if __name__ == "__main__":
    sys.exit(main())
