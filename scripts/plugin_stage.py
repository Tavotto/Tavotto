#!/usr/bin/env python3
"""完整 Codex 插件的组装、验证与归档（ADR 0043：源码与插件发行解耦）。

    # 从**已跟踪的源码清单** + **显式指定的画布构建物**组装一份干净 staging
    python scripts/plugin_stage.py stage --widget build/canvas.html --out build/plugin-stage \
        --source-sha "$(git rev-parse HEAD)"

    # 验证一份 staging / 一个 zip / 一份已装副本
    python scripts/plugin_stage.py verify build/plugin-stage --source-sha <sha>
    python scripts/plugin_stage.py verify out/codex-plugin-0.13.0.zip --version 0.13.0
    python scripts/plugin_stage.py verify ~/.codex/plugins/cache/tavotto/tavotto/0.13.0 --installed
    python scripts/plugin_stage.py verify build/plugin-stage --serve /path/to/python   # 真起 server 读资源

    # 确定性 zip（顶层目录固定叫 codex-plugin）与安全解包
    python scripts/plugin_stage.py archive --stage build/plugin-stage --out out/codex-plugin-0.13.0.zip
    python scripts/plugin_stage.py unpack out/codex-plugin-0.13.0.zip --out build/unpacked

为什么要有它（而不是 `git archive` 或「把目录递归打成 zip」）：

* 画布 `mcp/widget/canvas.html` 不再进版本库。`git archive` 只认索引，会**静默漏掉**
  它；递归打包又会把 `__pycache__`、本机缓存、任何顺手放进目录的东西一起带走。
  这里的规则是**已知源码清单（`git ls-files`）+ 显式构建输出**，其余一律拒绝。
* 清单形状、内容摘要与逐条验证只有一份实现：`src/tavotto/engine/pluginmanifest.py`
  （安装器的 `tavotto codex doctor` 也用它体检已装副本）。本脚本按路径加载它——
  scripts/ 不依赖 tavotto 包装没装，而那份实现不依赖包内任何别的模块。

纯标准库；`digest` 复用 `build_mcp_widget` 的路径 / 换行归一化。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_mcp_widget  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _load_manifest_module():
    spec = importlib.util.spec_from_file_location(
        "_tavotto_pluginmanifest", ROOT / "src" / "tavotto" / "engine" / "pluginmanifest.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


pm = _load_manifest_module()

# 供同目录脚本与测试直接取用的名字（实现只在 pluginmanifest 里）
BUILD_MANIFEST = pm.BUILD_MANIFEST
PLUGIN_SUBDIR = pm.PLUGIN_SUBDIR
GENERATED = pm.GENERATED
REQUIRED = pm.REQUIRED
WIDGET_MIN_BYTES = pm.WIDGET_MIN_BYTES
StageError = pm.PluginManifestError
sha256_bytes = pm.sha256_bytes
sha256_file = pm.sha256_file
widget_problems = pm.widget_problems
content_digest = pm.content_digest
describe = pm.describe
read_manifest = pm.read_manifest
verify_dir = pm.verify_dir
_walk = pm.walk
_rel = pm.rel
_canonical_yaml = pm.canonical_yaml
_canonical_mcp = pm.canonical_mcp

PLUGIN_SRC = ROOT / PLUGIN_SUBDIR
#: 确定性 zip 的时间戳（zip 最小合法值）；内容身份由 content_digest 说了算，不靠时间
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
#: `--serve` 时给 server 的协议版本（与 tests/test_mcp_stdio.py 一致）
PROTOCOL = "2025-11-25"
WIDGET_URI = "ui://tavotto/canvas/v1.html"
WIDGET_MIME = "text/html;profile=mcp-app"


# ------------------------------------------------------------------ 小工具


def _force_utf8() -> None:
    build_mcp_widget._force_utf8()


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise StageError(f"git {' '.join(args)} 失败：{proc.stderr.strip()[:400]}")
    return proc.stdout


def _is_generated(rel: str) -> bool:
    return rel in GENERATED


def tracked_plugin_files(root: Path = ROOT) -> list[tuple[str, str]]:
    """`git ls-files -s` 里的插件源码：[(插件内相对路径, git 模式)]，**不含生成物**。

    模式从索引读而不是从文件系统读：Windows 上 `st_mode` 恒 0o100666，而 zip 与
    发行分支要复现的正是索引里那个 100644 / 100755。符号链接（120000）一律拒绝——
    脱离源码树之后它指向的东西不在包里。
    """
    out = _git(root, "ls-files", "-s", "-z", "--full-name", "--", PLUGIN_SUBDIR)
    files: list[tuple[str, str]] = []
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, _tab, path = entry.partition("\t")
        mode = meta.split()[0]
        rel = PurePosixPath(path).relative_to(PLUGIN_SUBDIR).as_posix()
        if mode == "120000":
            raise StageError(f"插件源码里有符号链接 {path}——脱离源码树后它指不到任何东西")
        if not mode.startswith("100"):
            raise StageError(f"{path} 的 git 模式 {mode} 不是普通文件")
        if _is_generated(rel):
            continue  # 生成物不从索引取（PR B 之后它根本不在索引里）
        files.append((rel, mode))
    if not files:
        raise StageError(f"{root} 里 `git ls-files {PLUGIN_SUBDIR}` 一个文件都没有")
    return files


def plugin_source_dirty(root: Path = ROOT) -> list[str]:
    """插件源码目录里**未提交的改动**（生成物除外）。staging 声称的 source_sha
    是「造它的 commit」，工作区与 commit 不一致时这个声明就是假的。"""
    out = _git(root, "status", "--porcelain", "-z", "--untracked-files=all", "--", PLUGIN_SUBDIR)
    dirty: list[str] = []
    for entry in out.split("\0"):
        if not entry or len(entry) < 4:
            continue
        path = entry[3:]
        rel = PurePosixPath(path).relative_to(PLUGIN_SUBDIR).as_posix()
        if _is_generated(rel):
            continue
        if any(part in pm.IGNORED_LOCAL for part in PurePosixPath(rel).parts):
            continue
        dirty.append(path)
    return dirty


def _license_entry(root: Path) -> tuple[Path, str]:
    out = _git(root, "ls-files", "-s", "--", "LICENSE")
    if not out.strip():
        raise StageError("仓库里没有跟踪的 LICENSE——插件必须自带许可证")
    return root / "LICENSE", out.split()[0]


def _min_tavotto_version() -> str:
    """插件要求的最低引擎版本：唯一出处是 make_plugin_manifest.MIN_TAVOTTO_VERSION。"""
    import make_plugin_manifest  # 同目录

    return make_plugin_manifest.MIN_TAVOTTO_VERSION


def write_build_manifest(plugin_dir: Path, *, min_tavotto_version: str | None = None, **kw) -> dict:
    """`pluginmanifest.write_build_manifest` 的同目录门面：默认最低引擎版本取自
    make_plugin_manifest（那是它的唯一出处）。"""
    return pm.write_build_manifest(
        plugin_dir, min_tavotto_version=min_tavotto_version or _min_tavotto_version(), **kw
    )


# ------------------------------------------------------------------ 组装


def _toolchain(overrides: dict[str, str]) -> dict[str, str | None]:
    tc: dict[str, str | None] = {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "node": None,
        "pnpm": None,
    }
    for name in ("node", "pnpm"):
        exe = shutil.which(name)
        if exe:
            try:
                proc = subprocess.run(
                    [exe, "--version"], capture_output=True, text=True, timeout=30
                )
                if proc.returncode == 0:
                    tc[name] = proc.stdout.strip().lstrip("v")
            except (OSError, subprocess.TimeoutExpired):
                pass
    tc.update(overrides)
    return tc


def stage(
    out: Path,
    widget: Path,
    *,
    source_sha: str,
    root: Path = ROOT,
    allow_dirty: bool = False,
    skip_fingerprint: bool = False,
    toolchain: dict[str, str] | None = None,
    audit: dict | None = None,
) -> dict:
    """组装一份干净 staging，返回写进去的 `plugin-build.json`。

    失败一律抛 `StageError`，且 `out` 不会留下半成品：先在临时目录里组好，最后
    整体 rename 到位。
    """
    import re

    if not re.fullmatch(r"[0-9a-f]{40}", source_sha or ""):
        raise StageError(f"source_sha 必须是 40 位十六进制，拿到 {source_sha!r}")
    head = _git(root, "rev-parse", "HEAD").strip()
    if head != source_sha:
        raise StageError(
            f"--source-sha {source_sha[:12]} 与 checkout 的 HEAD {head[:12]} 不一致——"
            f"清单里的 source_sha 必须是**真造它的那个 commit**，不是随便传进来的一个"
        )
    dirty = plugin_source_dirty(root)
    if dirty and not allow_dirty:
        raise StageError(
            "插件源码目录有未提交改动，staging 声称的 source_sha 会是假的："
            + ", ".join(dirty[:8])
            + ("…" if len(dirty) > 8 else "")
            + "（本地试验用 --allow-dirty）"
        )
    expect_fp = None if skip_fingerprint else build_mcp_widget.source_fingerprint()
    problems = widget_problems(widget, expect_fingerprint=expect_fp)
    if problems:
        raise StageError("画布产物不合格：\n  " + "\n  ".join(problems))
    if out.exists() and any(out.iterdir()):
        raise StageError(f"{out} 已存在且非空——staging 必须从空目录开始，不叠在旧产物上")

    sources = tracked_plugin_files(root)
    license_path, license_mode = _license_entry(root)
    fingerprint = expect_fp or build_mcp_widget.source_fingerprint()

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".plugin-stage-", dir=out.parent))
    try:
        modes: dict[str, str] = {}

        def put(rel: str, data: bytes, mode: str) -> None:
            dest = tmp / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            if os.name != "nt" and mode == "100755":
                dest.chmod(0o755)
            modes[rel] = mode

        for rel, mode in sources:
            src = root / PLUGIN_SUBDIR / rel
            if not src.is_file():
                raise StageError(f"索引里有 {rel} 但工作区里没有这个文件")
            put(rel, src.read_bytes(), mode)
        put(GENERATED[0], widget.read_bytes(), "100644")
        put("LICENSE", license_path.read_bytes(), license_mode)
        for rel in REQUIRED:
            if not (tmp / rel).is_file():
                raise StageError(f"组装结果缺 {rel}")

        lock = root / "web" / "pnpm-lock.yaml"
        manifest = write_build_manifest(
            tmp,
            modes=modes,
            source_sha=source_sha,
            fingerprint=fingerprint,
            lockfile_sha256=sha256_file(lock) if lock.is_file() else None,
            toolchain=_toolchain(toolchain or {}),
            audit=audit,
        )
        version = manifest["plugin_version"]
        problems = verify_dir(tmp, source_sha=source_sha, version=version)
        if problems:
            raise StageError("刚组装的 staging 自检没过：\n  " + "\n  ".join(problems))
        if out.exists():
            out.rmdir()
        os.replace(tmp, out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return manifest


# ------------------------------------------------------------------ 验证入口


def verify_any(target: Path, **kw) -> tuple[list[str], dict | None]:
    """目录直接验；zip 先安全解包到临时目录再验。"""
    if target.is_dir():
        return verify_dir(target, **kw), read_manifest(target) if not kw.get("legacy") else None
    if target.is_file() and zipfile.is_zipfile(target):
        with tempfile.TemporaryDirectory(prefix="plugin-verify-") as tmp:
            plugin_dir = unpack_zip(target, Path(tmp))
            return verify_dir(plugin_dir, **kw), (
                read_manifest(plugin_dir) if not kw.get("legacy") else None
            )
    return [f"{target} 既不是目录也不是 zip"], None


# ------------------------------------------------------------------ 归档


def write_zip(plugin_dir: Path, target: Path, *, prefix: str = PLUGIN_SUBDIR) -> Path:
    """确定性 zip：条目按名排序、时间戳钉死、模式从清单（没有清单时按文件系统）取。

    先写到同目录临时文件，成功后 `os.replace` 换上去——失败不留下半个 zip。
    """
    manifest = read_manifest(plugin_dir)
    modes = {e["path"]: e["mode"] for e in (manifest or {}).get("files", [])}
    files = _walk(plugin_dir)
    if not files:
        raise StageError(f"{plugin_dir} 里没有文件可打包")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in files:
                rel = _rel(plugin_dir, p)
                mode = modes.get(rel) or (
                    "100755" if os.access(p, os.X_OK) and os.name != "nt" else "100644"
                )
                info = zipfile.ZipInfo(f"{prefix}/{rel}", date_time=ZIP_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (int(mode, 8) & 0o777 | 0o100000) << 16
                zf.writestr(info, p.read_bytes())
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target


def unpack_zip(zip_path: Path, dest: Path, *, prefix: str = PLUGIN_SUBDIR) -> Path:
    """安全解包：拒绝绝对路径、`..`、顶层目录不叫 `codex-plugin` 的条目。返回插件目录。"""
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename
            parts = PurePosixPath(name).parts
            if not parts or parts[0] != prefix:
                raise StageError(f"zip 条目 {name!r} 不在顶层目录 {prefix}/ 之下")
            if name.startswith("/") or ".." in parts or "\\" in name:
                raise StageError(f"zip 条目 {name!r} 试图逃出解包目录")
            if info.is_dir():
                continue
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise StageError(f"zip 条目 {name!r} 是符号链接")
            out = dest.joinpath(*parts)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(zf.read(info))
            if os.name != "nt" and ((info.external_attr >> 16) & 0o111):
                out.chmod(0o755)
    plugin_dir = dest / prefix
    if not plugin_dir.is_dir():
        raise StageError(f"zip 里没有 {prefix}/")
    return plugin_dir


# ------------------------------------------------------------------ 真起 server


def serve_check(plugin_dir: Path, python: str, *, timeout: float = 90.0) -> list[str]:
    """从**这份**插件目录起 MCP server，走 stdio 读画布资源，与磁盘上那份逐字比。

    量的是「安装出来的东西真的能把画布交给 host」——不是 `available()` 那一格布尔，
    也不是文件非空。需要 `python` 能 import tavotto.engine（否则 server 降级、
    不声明资源，这里如实报出来）。
    """
    import threading

    server = plugin_dir / "mcp" / "server.py"
    if not server.is_file():
        return [f"{server} 不存在"]
    env = {**os.environ}
    for name in ("TAVOTTO_MCP_WIDGET", "TAVOTTO_MCP_ROOTS", "TAVOTTO_MCP_WORKSPACE"):
        env.pop(name, None)
    with tempfile.TemporaryDirectory(prefix="plugin-serve-") as tmp:
        env["TAVOTTO_MCP_ROOTS"] = tmp
        env["TAVOTTO_DATA_DIR"] = os.path.join(tmp, "data")
        env["TAVOTTO_CONFIG_DIR"] = os.path.join(tmp, "config")
        env["TAVOTTO_NO_TELEMETRY"] = "1"
        proc = subprocess.Popen(
            [python, str(server)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(plugin_dir),
        )
        problems: list[str] = []
        n = 0

        def call(method: str, params: dict | None = None) -> dict:
            nonlocal n
            n += 1
            msg: dict = {"jsonrpc": "2.0", "id": n, "method": method}
            if params is not None:
                msg["params"] = params
            assert proc.stdin and proc.stdout
            proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
            proc.stdin.flush()
            line = proc.stdout.readline()
            if not line:
                err = proc.stderr.read().decode("utf-8", "replace")[-2000:] if proc.stderr else ""
                raise StageError(f"server 在 {method} 时挂了：{err}")
            return json.loads(line.decode("utf-8"))

        try:
            timer = threading.Timer(timeout, proc.kill)
            timer.start()
            try:
                init = call(
                    "initialize",
                    {
                        "protocolVersion": PROTOCOL,
                        "capabilities": {},
                        "clientInfo": {"name": "plugin_stage", "version": "1"},
                    },
                )
                info = init.get("result", {}).get("serverInfo", {})
                if info.get("version") in (None, "0"):
                    problems.append(
                        f"server 起来了但是降级模式（serverInfo.version={info.get('version')!r}）"
                        f"：{python} import 不到 tavotto.engine，验不了资源"
                    )
                    return problems
                tools = call("tools/list").get("result", {}).get("tools", [])
                by_name = {t.get("name"): t for t in tools}
                for name in ("tavotto_open_figure", "tavotto_apply_overrides"):
                    meta = (by_name.get(name) or {}).get("_meta") or {}
                    if meta.get("ui", {}).get("resourceUri") != WIDGET_URI:
                        problems.append(f"{name} 没挂画布 _meta（server 认为画布不可用）")
                listed = call("resources/list").get("result", {}).get("resources", [])
                if [r.get("uri") for r in listed] != [WIDGET_URI]:
                    problems.append(
                        f"resources/list 不是恰好一块画布：{[r.get('uri') for r in listed]}"
                    )
                read = call("resources/read", {"uri": WIDGET_URI})
                if "error" in read:
                    problems.append(f"resources/read 报错：{read['error']}")
                    return problems
                contents = read.get("result", {}).get("contents", [])
                if len(contents) != 1 or contents[0].get("mimeType") != WIDGET_MIME:
                    problems.append(f"resources/read 形状不对：{str(contents)[:200]}")
                    return problems
                served = contents[0].get("text", "")
                on_disk = (plugin_dir / GENERATED[0]).read_text(encoding="utf-8")
                if served != on_disk:
                    problems.append(
                        f"server 交出去的画布与 {GENERATED[0]} 不是同一份"
                        f"（{len(served)} vs {len(on_disk)} 字符）"
                    )
            finally:
                timer.cancel()
        except StageError as exc:
            problems.append(str(exc))
        finally:
            try:
                if proc.stdin:
                    proc.stdin.close()
                proc.wait(timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()
        return problems


# ------------------------------------------------------------------ CLI


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("stage", help="从 git 清单 + 显式画布组装 staging")
    s.add_argument("--widget", type=Path, required=True, help="build_mcp_widget.py --out 的产物")
    s.add_argument("--out", type=Path, required=True, help="staging 目录（必须不存在或为空）")
    s.add_argument("--source-sha", required=True, help="造它的 commit（必须等于 HEAD）")
    s.add_argument("--allow-dirty", action="store_true", help="本地试验：容忍插件源码未提交")
    s.add_argument("--skip-fingerprint", action="store_true", help="本地试验：不比画布戳与源码指纹")
    s.add_argument("--toolchain", action="append", default=[], metavar="NAME=VER")
    s.add_argument(
        "--audit",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="可变审计信息（不参与身份）",
    )
    s.add_argument("--json", action="store_true")

    v = sub.add_parser("verify", help="验证 staging / zip / 已装副本")
    v.add_argument("target", type=Path)
    v.add_argument("--source-sha")
    v.add_argument("--version")
    v.add_argument("--content-digest")
    v.add_argument("--installed", action="store_true", help="已装副本：允许两份清单一起钉 command")
    v.add_argument("--legacy", action="store_true", help="没有构建清单的旧发行件")
    v.add_argument("--serve", metavar="PYTHON", help="真起 server 走 stdio 读画布资源")
    v.add_argument("--json", action="store_true")

    a = sub.add_parser("archive", help="确定性 zip")
    a.add_argument("--stage", type=Path, required=True)
    a.add_argument("--out", type=Path, required=True)

    u = sub.add_parser("unpack", help="安全解包")
    u.add_argument("zip", type=Path)
    u.add_argument("--out", type=Path, required=True)

    d = sub.add_parser("digest", help="打印一份目录的 content_digest（按清单）")
    d.add_argument("target", type=Path)

    args = ap.parse_args(argv)
    try:
        if args.cmd == "stage":
            tc = dict(kv.split("=", 1) for kv in args.toolchain)
            audit = dict(kv.split("=", 1) for kv in args.audit)
            m = stage(
                args.out,
                args.widget,
                source_sha=args.source_sha,
                allow_dirty=args.allow_dirty,
                skip_fingerprint=args.skip_fingerprint,
                toolchain=tc,
                audit=audit,
            )
            summary = {
                "ok": True,
                "out": str(args.out),
                "plugin_version": m["plugin_version"],
                "source_sha": m["source_sha"],
                "content_digest": m["content_digest"],
                "files": len(m["files"]),
            }
            print(
                json.dumps(summary, ensure_ascii=False)
                if args.json
                else f"已组装 {args.out}：{m['plugin_version']} · {len(m['files'])} 个文件 · "
                f"content {m['content_digest'][:12]} · source {m['source_sha'][:12]}"
            )
            return 0
        if args.cmd == "verify":
            problems, manifest = verify_any(
                args.target,
                source_sha=args.source_sha,
                version=args.version,
                expect_content_digest=args.content_digest,
                installed=args.installed,
                legacy=args.legacy,
            )
            if args.serve and not problems:
                if args.target.is_dir():
                    problems += serve_check(args.target, args.serve)
                else:
                    with tempfile.TemporaryDirectory(prefix="plugin-serve-") as tmp:
                        problems += serve_check(unpack_zip(args.target, Path(tmp)), args.serve)
            report = {
                "ok": not problems,
                "target": str(args.target),
                "problems": problems,
                "plugin_version": (manifest or {}).get("plugin_version"),
                "content_digest": (manifest or {}).get("content_digest"),
                "source_sha": (manifest or {}).get("source_sha"),
            }
            if args.json:
                print(json.dumps(report, ensure_ascii=False))
            elif problems:
                print(f"插件验证未通过（{args.target}）：", file=sys.stderr)
                for p in problems:
                    print(f"  - {p}", file=sys.stderr)
            else:
                print(
                    f"插件验证通过：{args.target}"
                    + (
                        f"（{manifest['plugin_version']} · content {manifest['content_digest'][:12]}）"
                        if manifest
                        else "（legacy）"
                    )
                )
            return 0 if not problems else 1
        if args.cmd == "archive":
            problems = verify_dir(args.stage)
            if problems:
                raise StageError("staging 没通过验证，不打包：\n  " + "\n  ".join(problems))
            target = write_zip(args.stage, args.out)
            print(
                f"已写入 {target}（{target.stat().st_size // 1024} KiB，"
                f"sha256 {sha256_file(target)[:12]}…）"
            )
            return 0
        if args.cmd == "unpack":
            plugin_dir = unpack_zip(args.zip, args.out)
            print(str(plugin_dir))
            return 0
        if args.cmd == "digest":
            m = read_manifest(args.target)
            if m is None:
                raise StageError(f"{args.target} 里没有 {BUILD_MANIFEST}")
            print(m["content_digest"])
            return 0
    except StageError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
