#!/usr/bin/env python3
"""生成 Codex 插件的版本清单 `codex-plugin.json`（+ 可选的插件 zip）。

    python scripts/make_plugin_manifest.py --tag v0.7.1 --out out/codex-plugin.json
    python scripts/make_plugin_manifest.py --tag v0.7.1 --out out/codex-plugin.json \
        --zip out/codex-plugin-0.7.1.zip

发布时把这两份挂到 GitHub Release，插件下次被调用就会看到新版本
（`codex-plugin/skills/tavotto-figure/scripts/update_check.py` 拉的
`releases/latest/download/codex-plugin.json` 就是它）。

**为什么不放在 desktop-tauri.yml 的 updater-manifest 里**：那个 job 依赖桌面
构建产物和 minisign 私钥，没配私钥时整个跳过。插件清单跟这两样都没关系——
挂在那儿等于「哪天没配签名密钥，插件的更新通道就悄悄停了」，而且全绿。
所以它跟 wheel 一起走 release.yml，每个 tag 都发。

版本号**只从 `.codex-plugin/plugin.json` 读**，并与 tag 核对：对不上直接失败。
发一份说自己是 0.7.1、里面装着 0.7.0 的清单，用户会永远看到「有新版本」，
更新完还是看到——而且没有任何报错。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / "codex-plugin"
PLUGIN_JSON = PLUGIN_DIR / ".codex-plugin" / "plugin.json"

#: 清单 schema。改字段语义要 +1，读的一方（update_check.SCHEMA）同步。
SCHEMA = 1
#: 这个插件最低要求的 Tavotto 版本 = **第一个装得下 `mcp/server.py` 里
#: `_BRIDGE_IMPORT` 那整组引擎模块的版本**。判据的主语是「桥 import 得动吗」，
#: 不是「有没有 `tavotto open`」——后者是 v0.7.0 时的理由，在桥只 import
#: handoff 的年代成立，早已不是真正的下限。
#: 插件 0.13 的桥新增 import 了 previewbudget / project_refresh / profilestore /
#: exportjob / exportreq（ef9ac026 / aa61094c / b2a4b156 / 3ff49622），
#: **五个全部晚于 v0.12.0**，所以 0.7–0.12 的引擎一 import 就 ImportError。
#: 往上调的代价：本机 Tavotto 更老的用户会看到「去升级 Tavotto」
#: （`update_check.tavotto_hint`，经 `handoff.py` 打到 stderr）——他们**本来就该
#: 看到**：不提示的话，他们会照提示只升插件，然后撞上降级 server。
#: **改 bridge.py 的 import 集时必须回来重估这个值**——`_BRIDGE_IMPORT` 与桥之间
#: 有对拍（test_mcp_resolver），但它与本常量之间没有，只能靠这条约定。
MIN_TAVOTTO_VERSION = "0.13.0"
#: 上面那个版本号是**对着这一组桥 import** 算出来的。改了 `bridge.py` 的
#: `from tavotto.engine import ...`，`tests/test_codex_plugin.py` 会红，逼你回来
#: 重估 `MIN_TAVOTTO_VERSION` 再同步这里。散句约定靠人记得，这条靠退出码。
BRIDGE_IMPORTS_AT_MIN = frozenset(
    {
        "config",
        "exportjob",
        "exportreq",
        "handoff",
        "patchspec",
        "pool",
        "preflight",
        "previewbudget",
        "profiles",
        "profilestore",
        "project_refresh",
        "readiness",
        "registry",
        "telemetry",
    }
)
CHANNEL = "stable"
REPO = "Tavotto/Tavotto"

#: 打包时跳过的东西（缓存与本机产物，跟插件本身无关）
ZIP_SKIP = {"__pycache__", ".DS_Store", ".pytest_cache"}


def _stage_module():
    """`scripts/plugin_stage.py`：确定性 zip 与完整插件验证的唯一实现（同目录 import）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import plugin_stage
    finally:
        sys.path.pop(0)
    return plugin_stage


def plugin_version(path: Path = PLUGIN_JSON) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        raise SystemExit(f"{path} 里没有可用的 version")
    return version.strip()


def check_tag(tag: str, version: str) -> None:
    """tag 与插件版本必须一致（tag 允许带 v 前缀）。"""
    if tag.lstrip("vV") != version:
        raise SystemExit(
            f"tag {tag} 与插件版本 {version} 对不上。\n"
            f"  发版前先改 {PLUGIN_JSON.relative_to(ROOT)} 的 version——"
            "清单说自己是新版、里面却是旧版，用户会永远看到「有新版本」。"
        )


def build_manifest(tag: str, version: str, *, published_at: str | None = None) -> dict:
    base = f"https://github.com/{REPO}/releases"
    out = {
        "schema": SCHEMA,
        "plugin": "tavotto",
        "channel": CHANNEL,
        "latest_version": version,
        "download_url": f"{base}/download/{tag}/codex-plugin-{version}.zip",
        "release_notes_url": f"{base}/tag/{tag}",
        "min_tavotto_version": MIN_TAVOTTO_VERSION,
    }
    if published_at:
        out["published_at"] = published_at
    return out


#: zip 里**必须**有的东西。
#:
#: `canvas.html` 是构建产物（`scripts/build_mcp_widget.py`）。它进版本库，所以
#: 正常情况下 checkout 就有——但它**能以任何理由缺席**：有人 clean 掉、有人在
#: 一次半途而废的重建里删了它、某个 CI 步骤把插件目录当临时目录用过。
#:
#: 缺了之后没有任何东西会喊：`build_zip` 照打不误，用户装到一个没有 UI 的插件，
#: 而 MCP server 会**如实降级成 widget_missing、零报错**。正因为它安静，这条
#: 路上必须有一道闸——发布是单向的，发出去才发现就晚了。
_REQUIRED_IN_ZIP = ("mcp/widget/canvas.html",)


def build_zip(
    target: Path, source: Path = PLUGIN_DIR, *, require_build_manifest: bool = False
) -> Path:
    """把插件目录打成 zip（顶层目录固定叫 codex-plugin）。

    zip 本身由 `plugin_stage.write_zip` 写：条目排序、时间戳钉死、模式取自构建清单
    ——同一份 staging 在任何机器上打出逐字节相同的 zip，发行分支与 ZIP 才比得出
    「是不是同一份内容」。`require_build_manifest=True`（发布链）时 `source` 必须是
    `plugin_stage.py stage` 组装并验证过的 staging，不接受直接从源码目录打包。
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in source.rglob("*") if p.is_file())
    kept = [p for p in files if not ZIP_SKIP & set(p.relative_to(source).parts)]
    if not kept:
        raise SystemExit(f"{source} 里没有文件可打包")
    have = {p.relative_to(source).as_posix() for p in kept}
    missing = [rel for rel in _REQUIRED_IN_ZIP if rel not in have]
    if missing:
        raise SystemExit(
            f"插件 zip 缺少构建产物 {missing}——先跑 python scripts/build_mcp_widget.py"
        )
    stage = _stage_module()
    if require_build_manifest:
        problems = stage.verify_dir(source)
        if problems:
            raise SystemExit(
                f"{source} 不是一份验证通过的完整插件 staging，不打包：\n  " + "\n  ".join(problems)
            )
    try:
        return stage.write_zip(source, target)
    except stage.StageError as exc:
        raise SystemExit(str(exc)) from exc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tag", required=True, help="发布的 tag（如 v0.7.1）")
    ap.add_argument("--out", required=True, help="清单写到哪儿")
    ap.add_argument("--zip", default=None, help="同时打一个插件 zip 到这儿")
    ap.add_argument("--published-at", default=None, help="ISO8601 发布时间（CI 传 date -u）")
    ap.add_argument(
        "--plugin-dir",
        type=Path,
        default=None,
        help="从这份 plugin_stage 组装并验证过的 staging 读版本、打 zip（发布链必须走这条）",
    )
    args = ap.parse_args(argv)

    plugin_dir = args.plugin_dir if args.plugin_dir is not None else PLUGIN_DIR
    version = plugin_version(plugin_dir / ".codex-plugin" / "plugin.json")
    check_tag(args.tag, version)
    manifest = build_manifest(args.tag, version, published_at=args.published_at)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"* 插件清单: {out}（{version}）")
    if args.zip:
        made = build_zip(
            Path(args.zip), plugin_dir, require_build_manifest=args.plugin_dir is not None
        )
        print(f"* 插件包: {made}（{made.stat().st_size // 1024} KiB）")
        if made.name != f"codex-plugin-{version}.zip":
            print(
                f"::warning::zip 名字与清单里的 download_url 对不上: {made.name}", file=sys.stderr
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
