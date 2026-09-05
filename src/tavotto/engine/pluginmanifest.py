"""完整 Codex 插件的构建清单 `plugin-build.json`：形状、内容摘要与逐条验证（ADR 0043）。

这是**唯一**一份实现——`scripts/plugin_stage.py`（CI 组装 / 归档）、
`scripts/plugin_publish.py`（发行分支）与 `engine/codexinstall.py`（`tavotto codex
doctor` 体检已装副本）都消费它。前两者按路径 import（scripts/ 在 wheel 里不存在，
而安装器跑在 wheel 里，所以实现只能住在包内）。

三种身份分开回答三个问题：

* `source_sha` —— 哪个 commit 造的；
* `build_inputs_fingerprint` —— 参与编译的输入指纹（`build_mcp_widget.source_fingerprint`）；
* `content_digest` —— 成品内容摘要：sorted(相对路径, git 模式, sha256)。构建时间、run id
  这类可变审计信息放在 `audit` 里，**不参与**任何身份；清单不把自己的哈希写进自己
  ——`content_digest` 只覆盖清单之外的文件。

「原始发行件的完整性」与「已装副本的合法本地修改」是两件事：`tavotto codex install`
会把已装副本 `.mcp.json` 与 `openai.yaml` 的启动 `command` 一起钉成本机解释器的
绝对路径。`installed=True` 按**具体字段**验：两份 command 相等，且要么等于发行原值、
要么是本机一个真实存在的绝对路径；这两份文件其余内容（换行归一后）与发行时一致；
其它任何文件都不许改。发行件本身不许含任何路径形态的 command。

**纯标准库，不 import 本包的任何其它模块**——脚本按路径加载它时没有包上下文。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

SCHEMA = 1
#: 清单文件名（staging 与已装副本里都叫这个；zip 与发行分支里在 codex-plugin/ 之下）
BUILD_MANIFEST = "plugin-build.json"
#: zip 顶层目录 / 发行分支里插件所在目录——与旧 `codex-plugin-<版本>.zip` 的形状一致
PLUGIN_SUBDIR = "codex-plugin"
#: 插件里**由构建产生**的文件：不从 `git ls-files` 取，只认显式交进来的那份
GENERATED = ("mcp/widget/canvas.html",)
CANVAS = GENERATED[0]
#: 画布产物开头的指纹注释。与 `scripts/build_mcp_widget.STAMP` 逐字相同
#: （tests/test_plugin_stage.py 对拍）：那边是写的一侧，这边是验的一侧。
WIDGET_STAMP = "<!-- tavotto-mcp-widget "
#: 一份完整插件**必须**有的文件（缺一个都不是「完整插件」）
REQUIRED = (
    ".codex-plugin/plugin.json",
    ".mcp.json",
    "mcp/server.py",
    "mcp/tavotto_mcp/server.py",
    "mcp/tavotto_mcp/widget.py",
    "mcp/widget/canvas.html",
    "skills/tavotto-figure/SKILL.md",
    "skills/tavotto-figure/agents/openai.yaml",
    "skills/tavotto-figure/scripts/handoff.py",
    "assets/tavotto.svg",
    "LICENSE",
)
#: 已装副本里允许被 `tavotto codex install` 改动 command 的两份清单（严格同源对）
PINNABLE_MCP = ".mcp.json"
PINNABLE_YAML_GLOB = re.compile(r"^skills/[^/]+/agents/openai\.yaml$")
#: 画布产物的最小体量：它内联了大半个前端，真产物 1 MiB 上下；小于这个数就是截断
WIDGET_MIN_BYTES = 100_000
#: 已装副本里可以无视的本机杂物
IGNORED_LOCAL = {"__pycache__", ".pytest_cache", ".DS_Store"}
IGNORED_SUFFIXES = (".pyc", ".pyo")


class PluginManifestError(Exception):
    """清单 / 目录形状不成立。**一律抛**——猜不出来时给默认值等于把要防的问题再造一遍。"""


# ------------------------------------------------------------------ 小工具


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def walk(plugin_dir: Path) -> list[Path]:
    """插件目录里参与身份的文件：跳过本机杂物，符号链接一律拒绝。"""
    files: list[Path] = []
    for p in sorted(plugin_dir.rglob("*")):
        if p.is_symlink():
            raise PluginManifestError(f"{p} 是符号链接——插件目录里不许有链接")
        if not p.is_file():
            continue
        parts = p.relative_to(plugin_dir).parts
        if any(part in IGNORED_LOCAL for part in parts) or p.name.endswith(IGNORED_SUFFIXES):
            continue
        files.append(p)
    return files


def rel(plugin_dir: Path, p: Path) -> str:
    return p.relative_to(plugin_dir).as_posix()


def _is_path_like(command: str) -> bool:
    return os.path.isabs(command) or "\\" in command or "/" in command


# ------------------------------------------------------------------ 画布


def widget_problems(path: Path, *, expect_fingerprint: str | None) -> list[str]:
    """画布产物**分档**说明哪儿不对：缺失 / 空 / 无戳 / 过期 / 损坏 / 截断，
    绝不把它们合成一句「不可用」——处置各不相同。"""
    if not path.is_file():
        return [f"画布缺失：{path}（先跑 python scripts/build_mcp_widget.py --out {path}）"]
    size = path.stat().st_size
    if size == 0:
        return [f"画布是空文件：{path}"]
    data = path.read_bytes()
    head = data[:200].decode("utf-8", "replace")
    m = re.match(re.escape(WIDGET_STAMP) + r"([0-9a-f]+) -->", head)
    problems: list[str] = []
    if not m:
        problems.append(f"画布没有 tavotto-mcp-widget 指纹戳（截断或旧格式）：{path}")
    elif expect_fingerprint is not None and m.group(1) != expect_fingerprint:
        problems.append(
            f"画布过期：戳里是 {m.group(1)}，当前源码指纹 {expect_fingerprint}——重建一次"
        )
    if b'<div id="root">' not in data:
        problems.append('画布损坏：里面没有 <div id="root">（不是完整的单文件页面）')
    if b'canvas.js"' in data[:4096] or b'canvas.css"' in data[:4096]:
        problems.append("画布损坏：还留着外链引用（没内联完成）")
    if size < WIDGET_MIN_BYTES:
        problems.append(f"画布只有 {size} 字节（< {WIDGET_MIN_BYTES}）——像是被截断的")
    return problems


# ------------------------------------------------------------------ 可钉字段的规范形


def canonical_mcp(data: bytes) -> tuple[bytes, list[str]]:
    """`.mcp.json` 去掉 command 之后的规范形 + 各 server 的 command。"""
    obj = json.loads(data.decode("utf-8"))
    commands: list[str] = []
    servers = obj.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        raise PluginManifestError(".mcp.json 里没有 mcpServers")
    for entry in servers.values():
        if not isinstance(entry, dict) or not isinstance(entry.get("command"), str):
            raise PluginManifestError(".mcp.json 的 server 条目缺 command")
        commands.append(entry["command"])
        entry["command"] = "<command>"
    canon = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return canon.encode("utf-8"), commands


def canonical_yaml(data: bytes) -> tuple[bytes, list[str]]:
    """`openai.yaml` 把 `dependencies:` 块里的 `command:` 值换成占位后的规范形。

    与 `codexinstall._replace_dependency_command` 同一条扫描规则（逐行、只在
    dependencies 块里、行尾归一）；两侧的等价由 tests/test_plugin_stage.py 用
    真安装器钉过的副本对拍。
    """
    text = data.decode("utf-8").replace("\r\n", "\n")
    out: list[str] = []
    commands: list[str] = []
    in_deps = False
    for line in text.split("\n"):
        if re.match(r"\w", line):
            in_deps = line.startswith("dependencies:")
        elif in_deps:
            m = re.match(r"(\s*command:\s*)(.*)$", line)
            if m:
                commands.append(_yaml_unquote(m.group(2).strip()))
                line = m.group(1) + "<command>"
        out.append(line)
    return "\n".join(out).encode("utf-8"), commands


def _yaml_unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return json.loads(value)
    return value


def is_pinnable(path: str) -> bool:
    return path == PINNABLE_MCP or bool(PINNABLE_YAML_GLOB.match(path))


def canonical(path: str, data: bytes) -> tuple[bytes, list[str]]:
    return canonical_mcp(data) if path == PINNABLE_MCP else canonical_yaml(data)


# ------------------------------------------------------------------ 内容摘要与清单


def content_digest(entries: list[tuple[str, str, str]]) -> str:
    """成品内容摘要：sorted(相对路径, git 模式, sha256)。

    只看这三样：构建时间、run id、造它的机器都不在里面——同一份源码在任何机器上
    造出来的插件，摘要相同；改一个字节、改一个可执行位、多一个文件，摘要就变。
    """
    h = hashlib.sha256()
    for path, mode, digest in sorted(entries):
        h.update(f"{path}\0{mode}\0{digest}\n".encode())
    return h.hexdigest()


def describe(plugin_dir: Path, modes: dict[str, str]) -> tuple[list[dict], dict[str, dict]]:
    """逐文件 sha256 / 大小 / 模式 + 可钉文件的规范形。`modes` 缺的路径按 100644。"""
    entries: list[dict] = []
    pinnable: dict[str, dict] = {}
    for p in walk(plugin_dir):
        path = rel(plugin_dir, p)
        if path == BUILD_MANIFEST:
            continue
        data = p.read_bytes()
        mode = modes.get(path, "100644")
        entries.append(
            {"path": path, "sha256": sha256_bytes(data), "size": len(data), "mode": mode}
        )
        if is_pinnable(path):
            canon, commands = canonical(path, data)
            for cmd in commands:
                if _is_path_like(cmd):
                    raise PluginManifestError(
                        f"{path} 里的 command 是一条路径（{cmd}）——发行件里只许是裸名字，"
                        f"绝对路径只属于装它的那台机器"
                    )
            pinnable[path] = {"canonical_sha256": sha256_bytes(canon), "commands": commands}
    return sorted(entries, key=lambda e: e["path"]), pinnable


def write_build_manifest(
    plugin_dir: Path,
    *,
    modes: dict[str, str],
    source_sha: str,
    fingerprint: str,
    lockfile_sha256: str | None,
    toolchain: dict,
    min_tavotto_version: str,
    audit: dict | None = None,
) -> dict:
    """给一份已经摆好文件的插件目录写 `plugin-build.json`，返回清单。

    CI 的 `stage()` 与测试里的合成 staging 都走这一条：清单的形状只有一份实现。
    """
    plugin_json = json.loads(
        (plugin_dir / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    version = plugin_json.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise PluginManifestError(f"plugin.json 的 version 不合法：{version!r}")
    if plugin_json.get("name") != "tavotto":
        raise PluginManifestError(f"plugin.json 的 name 不是 tavotto：{plugin_json.get('name')!r}")
    entries, pinnable = describe(plugin_dir, modes)
    manifest = {
        "schema": SCHEMA,
        "plugin": "tavotto",
        "plugin_version": version,
        "min_tavotto_version": min_tavotto_version,
        "source_sha": source_sha,
        "build_inputs_fingerprint": fingerprint,
        "lockfile_sha256": lockfile_sha256,
        "toolchain": toolchain,
        "files": entries,
        "pinnable": pinnable,
        "content_digest": content_digest([(e["path"], e["mode"], e["sha256"]) for e in entries]),
        # 可变的审计信息单独一格：它**不参与**任何身份
        "audit": dict(audit or {}),
    }
    (plugin_dir / BUILD_MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def read_manifest(plugin_dir: Path) -> dict | None:
    """读 `plugin-build.json`；没有回 None，有但坏了抛（坏清单不是「没清单」）。"""
    p = plugin_dir / BUILD_MANIFEST
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PluginManifestError(f"{p} 读不出来：{exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise PluginManifestError(f"{p} 的 schema 不是 {SCHEMA}")
    return data


# ------------------------------------------------------------------ 验证


def verify_dir(
    plugin_dir: Path,
    *,
    source_sha: str | None = None,
    version: str | None = None,
    expect_content_digest: str | None = None,
    installed: bool = False,
    legacy: bool = False,
) -> list[str]:
    """逐条核对一份插件目录。回问题清单（空 = 通过）。

    * 默认（发行件）：清单必须在，每个文件的 sha256 与清单一致，不多不少，REQUIRED
      齐全，画布本身合格，command 是裸名字，content_digest 重算一致。
    * `installed=True`：允许 `.mcp.json` / `openai.yaml` 的 command 被一起钉成本机
      绝对路径（规范形与清单一致），其余文件仍然逐字节核对。
    * `legacy=True`：没有清单的旧发行件（bootstrap 用）——只验 REQUIRED（不含清单与
      LICENSE）、画布合格、command 裸名字。
    """
    problems: list[str] = []
    if not plugin_dir.is_dir():
        return [f"{plugin_dir} 不是目录"]
    try:
        files = walk(plugin_dir)
    except PluginManifestError as exc:
        return [str(exc)]
    have = {rel(plugin_dir, p): p for p in files}

    problems += widget_problems(plugin_dir / CANVAS, expect_fingerprint=None)

    try:
        manifest = None if legacy else read_manifest(plugin_dir)
    except PluginManifestError as exc:
        return [str(exc)]

    required = [r for r in REQUIRED if not (legacy and r == "LICENSE")]
    for path in required:
        if path not in have:
            problems.append(f"缺少必需文件 {path}")

    seen_commands: dict[str, list[str]] = {}
    for path, p in have.items():
        if is_pinnable(path):
            try:
                _canon, commands = canonical(path, p.read_bytes())
            except (PluginManifestError, ValueError, UnicodeDecodeError) as exc:
                problems.append(f"{path} 解析不了：{exc}")
                continue
            seen_commands[path] = commands
            if not installed:
                for cmd in commands:
                    if _is_path_like(cmd):
                        problems.append(
                            f"{path} 里的 command 是一条路径（{cmd}），发行件里只许裸名字"
                        )
            else:
                for cmd in commands:
                    if _is_path_like(cmd) and not Path(cmd).is_file():
                        problems.append(f"{path} 的 command 指向不存在的解释器 {cmd}")
    if installed:
        # 严格同源对：不管有没有清单，两份的 command 都必须一致
        mcp_now = set(seen_commands.get(PINNABLE_MCP, []))
        yaml_now = {c for k, cmds in seen_commands.items() if k != PINNABLE_MCP for c in cmds}
        if mcp_now and yaml_now and mcp_now != yaml_now:
            problems.append(
                f".mcp.json 的 command {sorted(mcp_now)} 与 openai.yaml 的 {sorted(yaml_now)} "
                f"不一致——严格同源对只钉了一侧"
            )

    if legacy:
        return problems
    if manifest is None:
        problems.append(f"缺少构建清单 {BUILD_MANIFEST}（不是由 plugin_stage 组装的插件）")
        return problems

    if version is not None and manifest.get("plugin_version") != version:
        problems.append(f"版本对不上：清单 {manifest.get('plugin_version')}，期望 {version}")
    if source_sha is not None and manifest.get("source_sha") != source_sha:
        problems.append(
            f"source SHA 对不上：清单 {manifest.get('source_sha')}，期望 {source_sha}——"
            f"这份插件不是那个 commit 造的"
        )
    try:
        pj = json.loads((plugin_dir / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        if pj.get("version") != manifest.get("plugin_version"):
            problems.append(
                f"plugin.json 的 version（{pj.get('version')}）与清单"
                f"（{manifest.get('plugin_version')}）不一致"
            )
    except (OSError, ValueError) as exc:
        problems.append(f"plugin.json 读不出来：{exc}")

    listed = {e["path"]: e for e in manifest.get("files", [])}
    extra = sorted(set(have) - set(listed) - {BUILD_MANIFEST})
    missing = sorted(set(listed) - set(have))
    for path in missing:
        problems.append(f"清单里有、目录里没有：{path}")
    for path in extra:
        problems.append(f"目录里多出清单没有的文件：{path}")

    pinned_commands: dict[str, list[str]] = {}
    for path, entry in listed.items():
        p = have.get(path)
        if p is None:
            continue
        data = p.read_bytes()
        got = sha256_bytes(data)
        if got == entry["sha256"]:
            continue
        if installed and is_pinnable(path):
            try:
                canon, commands = canonical(path, data)
            except (PluginManifestError, ValueError, UnicodeDecodeError) as exc:
                problems.append(f"{path} 解析不了：{exc}")
                continue
            spec = manifest.get("pinnable", {}).get(path)
            if not spec or sha256_bytes(canon) != spec["canonical_sha256"]:
                problems.append(f"{path} 改动超出了 command 字段（其它内容与发行时不同）")
                continue
            pinned_commands[path] = commands
            for cmd in commands:
                if cmd in spec["commands"]:
                    continue
                if not os.path.isabs(cmd):
                    problems.append(
                        f"{path} 的 command 被改成了 {cmd!r}：既不是发行原值也不是绝对路径"
                    )
                elif not Path(cmd).is_file():
                    problems.append(f"{path} 的 command 指向不存在的解释器 {cmd}")
            continue
        problems.append(
            f"{path} 的 sha256 对不上（清单 {entry['sha256'][:12]}…，磁盘 {got[:12]}…）"
            + ("——发行文件被改过" if installed else "")
        )
    # 两份启动清单的 command 是否一致在上面 seen_commands 那一段已经判过（有没有清单都判），
    # 这里不再判第二遍——同一条保证实现两遍，变异反证时会互相掩护。

    recomputed = content_digest(
        [(e["path"], e["mode"], e["sha256"]) for e in manifest.get("files", [])]
    )
    if recomputed != manifest.get("content_digest"):
        problems.append("清单的 content_digest 与它自己列的文件算不出同一个值（清单被改过）")
    if (
        expect_content_digest is not None
        and manifest.get("content_digest") != expect_content_digest
    ):
        problems.append(
            f"content_digest 对不上：清单 {manifest.get('content_digest')[:12]}…，"
            f"期望 {expect_content_digest[:12]}…"
        )
    return problems


def semver(value: str | None) -> tuple[int, int, int] | None:
    m = re.fullmatch(r"\s*v?(\d+)\.(\d+)\.(\d+)\s*", value or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))
