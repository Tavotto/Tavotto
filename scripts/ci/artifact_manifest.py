#!/usr/bin/env python3
"""发行产物清单：下游步骤**唯一**的文件名出处。

为什么要有它（2026-08-22 v0.9.1 发版实测的 #63）：SBOM 那一步把
`dist/*.whl` 喂给了只认**单个路径**的 syft，syft 把那串字符原样当文件名，
报 `no source providers were able to resolve the input`。而 `github_release`
这个 job 在 #45 之后**从来没成功跑到过**，所以整整没人发现。

根因不是那一处写错了，是**每个下游步骤都在自己猜产物叫什么**：
SBOM、SHA256SUMS、provenance、updater 清单、Release 附件、PyPI 校验、
exact artifact 冒烟——七处各猜一次，猜错的表现还各不相同。

清单把「有哪些产物、各自是什么角色、在哪、哈希是多少」**算一次、写下来**，
之后所有人读它。单值 action 输入只能收清单解出来的**具体路径**，
永远收不到一个 glob。

    # 造清单（构建完，在产物目录旁边跑）
    python3 scripts/ci/artifact_manifest.py build \
        --version 0.9.1 --source-sha <sha> \
        --add wheel:dist/tavotto-0.9.1-py3-none-any.whl:python \
        --add sdist:dist/tavotto-0.9.1.tar.gz:python \
        --out artifact-manifest.json

    # 校验（每个消费者用之前都该跑一次）
    python3 scripts/ci/artifact_manifest.py verify artifact-manifest.json \
        --require wheel,sdist --version 0.9.1 --source-sha <sha>

    # 取一个角色的具体路径（喂给单值输入）
    python3 scripts/ci/artifact_manifest.py path artifact-manifest.json --role wheel

    # 合并两条构建腿各自的清单（Python / Windows / macOS）
    python3 scripts/ci/artifact_manifest.py merge a.json b.json --out all.json

纯标准库。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

SCHEMA = 1

# **每个必须角色恰好一个。** 「恰好」不是洁癖：Release 上挂两个 wheel、
# updater 清单指向其中一个、PyPI 收到另一个，这种状态没有任何一步会报错，
# 而用户装到的和我们验过的不是同一个东西。
ROLES = {
    "wheel":              {"unique": True},
    "sdist":              {"unique": True},
    "windows-installer":  {"unique": True},
    "macos-installer":    {"unique": True},
    "windows-updater":    {"unique": True},
    "macos-updater":      {"unique": True},
    "updater-manifest":   {"unique": True},
    "sbom":               {"unique": False},
    "checksums":          {"unique": True},
    "codex-plugin":       {"unique": False},
}

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ManifestError(Exception):
    """清单不成立。**一律抛。**

    这个模块存在的全部理由就是「不要猜」——猜不出来时静默给个默认值，
    等于把它要解决的问题又造了一遍。
    """


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(version: str, source_sha: str, entries: list[tuple[str, str, str]],
          base: Path | None = None) -> dict:
    """按 `(role, path, platform)` 造清单。文件不存在**立刻抛**。"""
    if not _SHA_RE.match(source_sha or ""):
        raise ManifestError(f"source_sha 必须是 40 位十六进制，拿到 {source_sha!r}")
    base = base or Path.cwd()
    arts = []
    for role, rel, platform in entries:
        if role not in ROLES:
            raise ManifestError(
                f"不认识的 role {role!r}；认识的：{', '.join(sorted(ROLES))}")
        # **通配符先判。** 放在「文件不存在」之后的话，喂进来一个
        # `dist/*.whl` 会报「文件不存在 dist/*.whl」——挡是挡住了，
        # 但它把人指向「产物没造出来」，而真实原因是「这里不该写 glob」。
        # #63 的教训正是这一条：诊断指错方向比不诊断更坏。
        if any(ch in rel for ch in "*?["):
            raise ManifestError(
                f"{role}: 清单里不许出现通配符：{rel}。"
                f"清单存的必须是**一个具体文件**——glob 进了清单，"
                f"就等于把 #63 那个 bug 搬到了七个消费者面前")
        p = (base / rel) if not Path(rel).is_absolute() else Path(rel)
        if not p.is_file():
            raise ManifestError(f"{role}: 文件不存在 {rel}")
        arts.append({
            "role": role,
            "path": str(rel).replace(os.sep, "/"),
            "sha256": sha256_of(p),
            "size": p.stat().st_size,
            "platform": platform,
        })
    m = {"schema": SCHEMA, "version": version, "source_sha": source_sha,
         "artifacts": sorted(arts, key=lambda a: (a["role"], a["path"]))}
    check_shape(m)
    return m


def check_shape(m: dict) -> None:
    if m.get("schema") != SCHEMA:
        raise ManifestError(f"schema 不是 {SCHEMA}：{m.get('schema')!r}")
    if not m.get("version"):
        raise ManifestError("缺 version")
    if not _SHA_RE.match(m.get("source_sha") or ""):
        raise ManifestError(f"source_sha 形状不对：{m.get('source_sha')!r}")
    seen: dict[str, int] = {}
    for a in m.get("artifacts", []):
        for key in ("role", "path", "sha256", "platform"):
            if not a.get(key):
                raise ManifestError(f"产物条目缺 {key}：{a}")
        if a["role"] not in ROLES:
            raise ManifestError(f"不认识的 role：{a['role']}")
        if not re.fullmatch(r"[0-9a-f]{64}", a["sha256"]):
            raise ManifestError(f"{a['role']}: sha256 形状不对")
        seen[a["role"]] = seen.get(a["role"], 0) + 1
    for role, n in seen.items():
        if ROLES[role]["unique"] and n > 1:
            raise ManifestError(
                f"role {role} 出现了 {n} 次，而它必须**恰好一个**。"
                f"两个 wheel 谁都不会报错，但用户装到的和我们验过的不是同一个")


def verify(m: dict, require: list[str], base: Path,
           version: str | None = None, source_sha: str | None = None) -> list[str]:
    """把清单与磁盘、与期望的版本/SHA 逐条核对。回问题清单（空 = 通过）。"""
    problems: list[str] = []
    try:
        check_shape(m)
    except ManifestError as e:
        return [str(e)]

    if version is not None and m["version"] != version:
        problems.append(f"版本对不上：清单 {m['version']}，期望 {version}")
    if source_sha is not None and m["source_sha"] != source_sha:
        problems.append(
            f"source SHA 对不上：清单 {m['source_sha']}，期望 {source_sha}。"
            f"**这意味着产物不是同一个 commit 造的**——"
            f"「同一个 tag」证明不了这件事，两个 workflow 各自 checkout 就会分叉")

    have = {a["role"] for a in m["artifacts"]}
    for role in require:
        if role not in have:
            problems.append(f"缺少必须的 role：{role}")

    for a in m["artifacts"]:
        p = base / a["path"]
        if not p.is_file():
            problems.append(f"{a['role']}: 文件不在 {a['path']}")
            continue
        got = sha256_of(p)
        if got != a["sha256"]:
            problems.append(
                f"{a['role']}: sha256 对不上（清单 {a['sha256'][:12]}…，"
                f"磁盘 {got[:12]}…）——产物在造好之后被换过")
        if p.stat().st_size != a.get("size", p.stat().st_size):
            problems.append(f"{a['role']}: 大小对不上")
    return problems


def merge(manifests: list[dict]) -> dict:
    """合并多条构建腿的清单。version / source_sha **必须全都一样**。"""
    if not manifests:
        raise ManifestError("没有可合并的清单")
    for m in manifests:
        check_shape(m)
    versions = {m["version"] for m in manifests}
    shas = {m["source_sha"] for m in manifests}
    if len(versions) != 1:
        raise ManifestError(f"版本不一致：{sorted(versions)}")
    if len(shas) != 1:
        raise ManifestError(
            f"source SHA 不一致：{sorted(shas)}。"
            f"**wheel 与桌面产物必须来自同一个 commit**——"
            f"这正是合并这一步要挡住的东西")
    arts: list[dict] = []
    for m in manifests:
        arts.extend(m["artifacts"])
    out = {"schema": SCHEMA, "version": versions.pop(), "source_sha": shas.pop(),
           "artifacts": sorted(arts, key=lambda a: (a["role"], a["path"]))}
    check_shape(out)          # 合并之后 unique 约束才真正生效
    return out


def path_of(m: dict, role: str) -> str:
    hits = [a["path"] for a in m["artifacts"] if a["role"] == role]
    if not hits:
        raise ManifestError(f"清单里没有 role={role}")
    if len(hits) > 1:
        raise ManifestError(f"role={role} 有 {len(hits)} 个，取不出「那一个」：{hits}")
    return hits[0]


def render_summary(m: dict) -> str:
    L = [f"### 发行产物清单 · {m['version']} · `{m['source_sha'][:12]}`", "",
         "| role | 文件 | 平台 | 大小 | sha256 |", "|---|---|---|---:|---|"]
    for a in m["artifacts"]:
        L.append(f"| `{a['role']}` | `{a['path']}` | {a['platform']} | "
                 f"{a.get('size', 0):,} | `{a['sha256'][:16]}…` |")
    return "\n".join(L) + "\n"


def _load(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ManifestError(f"读不了 {p}：{e}") from e


def _utf8_stdout() -> None:
    """把 stdout / stderr 钉成 UTF-8。

    **这是 Windows 上的硬需求，不是洁癖。** GitHub 的 windows runner 上
    Python 的 stdout 默认编码是 cp1252（中文 Windows 上是 cp936），而本脚本
    的摘要是中文的：`print(render_summary(m))` 直接抛
    `UnicodeEncodeError: 'charmap' codec can't encode characters`，整条
    桌面构建腿当场失败——**产物已经造好了，倒在打印摘要这一步上**。
    2026-08-23 v0.9.2 的 publish=false 演练实测（run 32617869026）。

    `errors="replace"` 是兜底：真遇到编不出的字符，宁可打出问号，
    也不要让一条已经成功的构建因为一句日志而失败。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            # 被重定向成非 TextIOWrapper 时没有 reconfigure；那种情况下
            # 调用方自己决定编码，不该由这里改写。
            pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build")
    b.add_argument("--version", required=True)
    b.add_argument("--source-sha", required=True)
    b.add_argument("--add", action="append", default=[],
                   metavar="ROLE:PATH:PLATFORM",
                   help="可重复。PATH 必须是**一个具体文件**，不许是 glob")
    b.add_argument("--base", type=Path, default=Path.cwd())
    b.add_argument("--out", type=Path, required=True)

    v = sub.add_parser("verify")
    v.add_argument("manifest", type=Path)
    v.add_argument("--require", default="", help="逗号分隔的必须 role")
    v.add_argument("--base", type=Path, default=Path.cwd())
    v.add_argument("--version")
    v.add_argument("--source-sha")

    g = sub.add_parser("path")
    g.add_argument("manifest", type=Path)
    g.add_argument("--role", required=True)

    m_ = sub.add_parser("merge")
    m_.add_argument("manifests", type=Path, nargs="+")
    m_.add_argument("--out", type=Path, required=True)

    a = ap.parse_args(argv)
    _utf8_stdout()
    try:
        if a.cmd == "build":
            entries = []
            for spec in a.add:
                parts = spec.split(":")
                if len(parts) != 3:
                    raise ManifestError(f"--add 要 ROLE:PATH:PLATFORM，拿到 {spec!r}")
                entries.append((parts[0], parts[1], parts[2]))
            m = build(a.version, a.source_sha, entries, a.base)
            a.out.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
            print(render_summary(m))
            _step_summary(render_summary(m))
            return 0

        if a.cmd == "verify":
            m = _load(a.manifest)
            req = [r for r in a.require.split(",") if r]
            problems = verify(m, req, a.base, a.version, a.source_sha)
            if problems:
                for p in problems:
                    print(f"::error::{p}")
                print("\n产物清单校验未通过：\n  " + "\n  ".join(problems),
                      file=sys.stderr)
                return 1
            print(f"产物清单校验通过：{len(m['artifacts'])} 个产物，"
                  f"版本 {m['version']}，SHA {m['source_sha'][:12]}")
            _step_summary(render_summary(m))
            return 0

        if a.cmd == "path":
            print(path_of(_load(a.manifest), a.role))
            return 0

        if a.cmd == "merge":
            m = merge([_load(p) for p in a.manifests])
            a.out.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
            print(render_summary(m))
            _step_summary(render_summary(m))
            return 0
    except ManifestError as e:
        print(f"::error::{e}")
        print(f"产物清单：{e}", file=sys.stderr)
        return 1
    return 2


def _step_summary(text: str) -> None:
    dest = os.environ.get("GITHUB_STEP_SUMMARY")
    if dest:
        with open(dest, "a", encoding="utf-8") as fh:
            fh.write(text)


if __name__ == "__main__":
    raise SystemExit(main())
