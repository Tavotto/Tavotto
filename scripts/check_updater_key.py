#!/usr/bin/env python3
"""核对「tauri.conf.json 里的更新器公钥」与「手上的私钥」是不是同一对。

**发版前跑一次**。配错了的表现极其安静：CI 全绿、Release 资产齐全、
`latest.json` 也在，用户的壳下载完更新包、校验签名失败——界面上只是
「更新失败」，而问题其实早在几周前换密钥那一刻就埋下了。

判据是 minisign 的 key id。公钥文件第 2 行与签名文件第 2 行，base64 解出来
的第 2–10 字节都是那 8 字节 key id，对得上就是同一对。**不比公钥本身**：
私钥文件里存的是加密后的密钥体，直接比字节没有意义；让私钥真的签一次、
再看签名认不认这个公钥，才是端到端的判据。

用法：

    # 1) 用私钥签一个探针文件
    printf probe > /tmp/probe.bin
    pnpm dlx @tauri-apps/cli@2.11.4 signer sign -f ~/tavotto-updater.key -p "" /tmp/probe.bin
    # 2) 核对
    python scripts/check_updater_key.py --sig /tmp/probe.bin.sig
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONF = ROOT / "src-tauri" / "tauri.conf.json"


def key_id(minisign_blob: str) -> bytes:
    """minisign 公钥 / 签名文本 → 8 字节 key id。

    两种文件都是「注释行 + base64 行」，base64 解出来是
    `算法(2) || key_id(8) || 载荷`。
    """
    lines = [ln for ln in minisign_blob.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        raise SystemExit(f"不像 minisign 文本（只有 {len(lines)} 行）")
    raw = base64.b64decode(lines[1], validate=True)
    if len(raw) < 10:
        raise SystemExit("base64 解出来太短，不是 minisign 载荷")
    return raw[2:10]


def _decode_maybe_b64(text: str) -> str:
    """剥掉外面那层 base64。

    tauri.conf.json 的 pubkey 与打包器写出的 .sig 都是「整份 minisign 文本
    再 base64 一次」；但手工放进去的也可能已经是明文。判据取解出来的东西
    像不像 minisign 文本（带 untrusted comment 且至少两行），而不是找某个
    具体字样——公钥写「minisign public key」，签名写的是别的。
    """
    stripped = text.strip()
    try:
        decoded = base64.b64decode(stripped, validate=True).decode("utf-8")
    except Exception:  # noqa: BLE001 — 不是外层 base64 就当它已经是明文
        return stripped
    looks_minisign = "untrusted comment" in decoded and len(decoded.strip().splitlines()) >= 2
    return decoded if looks_minisign else stripped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--conf", type=Path, default=DEFAULT_CONF)
    ap.add_argument("--sig", type=Path, required=True,
                    help="用私钥签出来的 .sig 文件")
    args = ap.parse_args(argv)

    conf = json.loads(args.conf.read_text(encoding="utf-8"))
    try:
        pubkey_field = conf["plugins"]["updater"]["pubkey"]
    except KeyError:
        raise SystemExit(f"{args.conf} 里没有 plugins.updater.pubkey——更新器没配")

    want = key_id(_decode_maybe_b64(pubkey_field))
    # .sig 的内容同样是整份签名文本再 base64 一层
    got = key_id(_decode_maybe_b64(args.sig.read_text(encoding="utf-8")))

    print(f"tauri.conf.json 公钥 key id : {want.hex().upper()}")
    print(f"私钥签出来的      key id : {got.hex().upper()}")
    if want != got:
        print("✗ 不是一对——按这份配置发出去的更新，用户装不上")
        return 1
    print("✓ 同一对")
    return 0


if __name__ == "__main__":
    sys.exit(main())
