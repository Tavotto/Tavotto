#!/usr/bin/env python3
"""线上更新链的消费者保真检查（nightly）。

回答的问题：**已经发出去的那些字节**，更新器插件真的消费得了吗？

发布链上每一条既有绿灯量的都是生产者侧的替身指标——zip 存在、.sig 存在、
文件名匹配、清单两平台齐全。没有任何一步以真实消费者（tauri-plugin-updater）
的身份去消费产物，于是「签名后重打 zip 用了 deflate、插件只解得开 STORED」
这个 bug 让 Windows 应用内更新从 v0.7.0 起坏了四个版本，而整条链全绿。

这里做四件事，全部对着**线上已发布产物**：

1. 从应用真正烤死的 endpoint（`src-tauri/tauri.conf.json` 的
   `plugins.updater.endpoints[0]`）拉 `latest.json`；
2. 下载它指向的**全部**平台更新包，用应用内置的公钥（同一份
   tauri.conf.json 的 `plugins.updater.pubkey`）做 minisign 验签；
3. Windows 包交给 `tools/updater-extract-probe`（zip crate
   `default-features = false`，与插件逐字同形态）执行
   `ZipArchive::extract`，并断言解出的顶层有且仅有一个 `.exe`；
4. macOS 包做 tar.gz 解包冒烟（须含 `Tavotto.app/`）。

为什么查线上而不是构建产物：这个 bug 住在**发出去的字节**里（签名后重打
zip 那步），构建机上的中间产物检查（`tests/test_updater_zip.py`）抓不住
「发布编排在签名后又动了产物」这类回归。两层都要有。

跑正事之前先做**反证自检**：现造一个 STORED 包与一个 deflate 包各喂一次
探针，STORED 必须过、deflate 必须红。自检失败说明门禁本身坏了（比如有人
给探针的 zip 依赖加了 feature），比线上红灯更优先。

退出码（网络波动与产物坏掉必须分开——前者可重试/可 warning，后者是 P0）：

    0  全部通过
    1  断言失败：产物解不开 / 验签不过 / 清单缺平台 —— P0 级红灯
    2  网络失败：GitHub 抓不下来（DNS / 超时 / 5xx）—— infra 波动
    3  自检失败：门禁自身坏了
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TAURI_CONF = REPO_ROOT / "src-tauri" / "tauri.conf.json"

# 与 make_updater_manifest 的 --require 同一份硬要求：少一个平台 = 那个平台的
# 用户永远收不到更新，而且全绿。
REQUIRED_PLATFORMS = ("darwin-aarch64", "windows-x86_64")

EXIT_OK = 0
EXIT_ASSERTION = 1
EXIT_NETWORK = 2
EXIT_SELFTEST = 3


class NetworkFailure(Exception):
    """GitHub 抓不下来——infra 波动，不是产物问题。"""


class AssertionFailure(Exception):
    """抓下来了但消费不了——P0 级红灯。"""


def load_updater_config(conf_path: Path = TAURI_CONF) -> tuple[str, bytes]:
    """从应用自己的配置读 endpoint 与公钥——不另抄一份常量。

    公钥字段是 minisign 公钥文件内容的 base64（第一行 untrusted comment，
    第二行 base64 的 key 本体）——解出来就能直接喂给 `minisign -p`。
    """
    conf = json.loads(conf_path.read_text(encoding="utf-8"))
    updater = conf["plugins"]["updater"]
    endpoint = updater["endpoints"][0]
    pubkey = base64.b64decode(updater["pubkey"])
    if b"minisign public key" not in pubkey.splitlines()[0]:
        raise AssertionFailure(
            f"tauri.conf.json 的 pubkey 解出来不是 minisign 公钥文件：{pubkey[:60]!r}"
        )
    return endpoint, pubkey


def fetch(url: str, *, retries: int = 3, timeout: int = 120) -> bytes:
    """下载一个 URL；网络类失败重试后归为 NetworkFailure，4xx 归为断言失败。

    404 不是网络波动：endpoint 是烤死在已发布应用里的，latest.json 拉不到
    等于**所有桌面用户**都查不到更新——那是产物/发布问题，必须红。
    """
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "tavotto-ci-updater-check"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code >= 500:
                last = e
            else:
                raise AssertionFailure(f"HTTP {e.code} for {url}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
        time.sleep(2**attempt * 5)
    raise NetworkFailure(f"抓不下来 {url}: {last}")


def run_probe(probe: Path, zip_path: Path, out_dir: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [str(probe), str(zip_path), str(out_dir)], capture_output=True, text=True, timeout=600
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def selftest(probe: Path, workdir: Path) -> None:
    """反证自检：STORED 必须过、deflate 必须红。

    deflate 那半就是让 v0.7.0–v0.10.0 逃逸的坏产物形态（Compress-Archive
    的默认压缩）。探针连它都放行的话，后面的线上检查全是假绿——比如有人
    给探针的 zip 依赖加了 deflate feature「修好」了一条红灯。
    """
    st = workdir / "selftest"
    st.mkdir(parents=True, exist_ok=True)
    exe = st / "fake.exe"
    exe.write_bytes(b"MZ" + b"\0" * 128)
    stored = st / "stored.zip"
    with zipfile.ZipFile(stored, "w", compression=zipfile.ZIP_STORED) as z:
        z.write(exe, "fake.exe")
    deflated = st / "deflate.zip"
    with zipfile.ZipFile(deflated, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.write(exe, "fake.exe")

    rc, out = run_probe(probe, stored, st / "out-stored")
    if rc != 0:
        raise SelftestFailure(f"探针连 STORED 包都解不开（rc={rc}）：{out}")
    rc, out = run_probe(probe, deflated, st / "out-deflate")
    if rc == 0:
        raise SelftestFailure(
            "探针把 deflate 包也放行了——它的 zip 依赖形态已经不再等于插件的"
            "（检查 tools/updater-extract-probe/Cargo.toml 是不是被加了 feature）"
        )
    print("✓ 自检：STORED 过、deflate 红（探针与插件能力面同形）")


class SelftestFailure(Exception):
    pass


def verify_minisign(
    minisign: str, pubkey: bytes, payload: Path, sig_b64: str, workdir: Path
) -> None:
    """latest.json 的 signature 字段是 minisig 文件内容的 base64。"""
    try:
        sig = base64.b64decode(sig_b64)
    except Exception as e:  # noqa: BLE001 - 坏 base64 就是坏产物
        raise AssertionFailure(f"{payload.name} 的 signature 不是合法 base64: {e}") from e
    pub_file = workdir / "updater.pub"
    pub_file.write_bytes(pubkey)
    sig_file = workdir / (payload.name + ".minisig")
    sig_file.write_bytes(sig)
    proc = subprocess.run(
        [minisign, "-Vm", str(payload), "-x", str(sig_file), "-p", str(pub_file)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        raise AssertionFailure(
            f"{payload.name} minisign 验签失败：{(proc.stdout + proc.stderr).strip()}"
        )
    print(f"✓ 验签：{payload.name}")


def check_windows_package(probe: Path, pkg: Path, workdir: Path) -> None:
    rc, out = run_probe(probe, pkg, workdir / "extract-windows")
    if rc != 0:
        raise AssertionFailure(
            f"更新器插件消费不了 {pkg.name}（探针 rc={rc}）：{out}\n"
            "这正是 v0.7.0–v0.10.0 的形态：下载完成、验签通过、解包失败，"
            "用户看到「无法安装更新」。"
        )
    print(f"✓ Windows 包：插件同形态解包成功，顶层恰好一个 exe（{out}）")


def check_macos_package(pkg: Path, workdir: Path) -> None:
    out_dir = workdir / "extract-macos"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(pkg, "r:gz") as tar:
            names = tar.getnames()
    except (tarfile.TarError, OSError, EOFError) as e:
        raise AssertionFailure(f"macOS 更新包解不开（tar.gz）：{pkg.name}: {e}") from e
    if not any(n == "Tavotto.app" or n.startswith("Tavotto.app/") for n in names):
        raise AssertionFailure(f"macOS 更新包里没有 Tavotto.app/（前几个条目：{names[:5]}）")
    print(f"✓ macOS 包：tar.gz 可解，含 Tavotto.app/（{len(names)} 个条目）")


def check_live(endpoint: str, pubkey: bytes, probe: Path, minisign: str, workdir: Path) -> str:
    raw = fetch(endpoint)
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as e:
        raise AssertionFailure(f"latest.json 不是合法 JSON: {e}") from e
    version = manifest.get("version")
    platforms = manifest.get("platforms") or {}
    missing = [p for p in REQUIRED_PLATFORMS if p not in platforms]
    if missing:
        raise AssertionFailure(
            f"latest.json（version={version}）缺平台 {missing}——"
            f"那个平台的用户永远收不到更新（只有 {sorted(platforms)}）"
        )
    print(f"· 线上清单 version={version}，平台 {sorted(platforms)}")

    for key in REQUIRED_PLATFORMS:
        entry = platforms[key]
        url, sig = entry.get("url"), entry.get("signature")
        if not url or not sig:
            raise AssertionFailure(f"{key} 缺 url 或 signature")
        pkg = workdir / Path(url).name
        print(f"· 下载 {key}: {url}")
        pkg.write_bytes(fetch(url))
        verify_minisign(minisign, pubkey, pkg, sig, workdir)
        if key == "windows-x86_64":
            check_windows_package(probe, pkg, workdir)
        else:
            check_macos_package(pkg, workdir)
    return str(version)


def _summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--probe", required=True, type=Path, help="tools/updater-extract-probe 的 release 二进制"
    )
    ap.add_argument("--minisign", default="minisign", help="minisign 可执行文件")
    ap.add_argument("--workdir", type=Path, default=Path("build/updater-consumer-check"))
    ap.add_argument(
        "--selftest-only", action="store_true", help="只跑反证自检，不打线上（pytest / 本地调试用）"
    )
    args = ap.parse_args(argv)

    if not args.probe.is_file():
        print(f"::error::探针不存在：{args.probe}（先 cargo build --release）")
        return EXIT_SELFTEST
    args.workdir.mkdir(parents=True, exist_ok=True)

    try:
        selftest(args.probe, args.workdir)
    except SelftestFailure as e:
        print(f"::error::消费者保真检查自检失败：{e}")
        return EXIT_SELFTEST
    if args.selftest_only:
        return EXIT_OK

    endpoint, pubkey = load_updater_config()
    try:
        version = check_live(endpoint, pubkey, args.probe, args.minisign, args.workdir)
    except NetworkFailure as e:
        print(f"::warning::线上产物抓取失败（infra 波动，重试后仍不行）：{e}")
        return EXIT_NETWORK
    except AssertionFailure as e:
        print(f"::error::线上更新链消费者保真检查失败：{e}")
        _summary(f"### ❌ 线上更新链消费不了\n\n```\n{e}\n```\n")
        return EXIT_ASSERTION
    _summary(f"### ✅ 线上更新链消费者保真检查通过（version {version}）\n")
    print(f"✓ 线上 version {version}：两平台验签 + 消费者解包全部通过")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
