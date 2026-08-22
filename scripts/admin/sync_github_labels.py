#!/usr/bin/env python3
"""把 `.github/labels.yml` 同步到 GitHub 仓库标签。

默认 **dry-run**：只打印远程现状与将要发生的变更，一个字节都不改。
真要改加 `--apply`。

三条设计约束（每一条都花过一次真实代价）：

* **不删不认识的标签。** 只增改清单里的这些。别人手建的、GitHub 默认的、
  dependabot 建的一律原样留着——一个同步脚本不该替人决定「你那个没用」。
  想删自己去 UI 删，那是一次有意识的动作。
* **幂等。** 名称一致而颜色/描述不同 → PATCH；完全一致 → 什么都不做并如实
  报告 "unchanged"。跑两遍第二遍必须是全 unchanged，这条有用例看护。
* **没权限不是错误，是另一种输出。** 403 时把等价的 `gh api` 命令原样打出来，
  交给有权限的人执行。CI 上跑不动的脚本会被绕过，绕过之后就没人跑了。

纯标准库：调 `gh` 子进程，不引入 requests / PyGithub。
**也不用 PyYAML**——它不在 `.venv` 里（Flask 那侧刻意只有 flask+pymupdf），
`importorskip` 会让看护用例在本地静默跳过，那正是空门禁。清单的形状是固定
的三键列表，自己解析并对任何解析不出的行**报错**（不是跳过）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / ".github" / "labels.yml"


class ManifestError(Exception):
    """清单解析失败。**一律抛，绝不静默跳过一行。**

    静默跳过的后果是「同步跑成功了，但那个标签根本没建」——而人会以为它建了。
    """


def parse_manifest(text: str) -> list[dict]:
    """解析 `.github/labels.yml`。

    只认这一种形状（清单本身就长这样，不追求通用 YAML）::

        labels:
          - name: "severity:P0"
            color: "b60205"
            description: "……"

    任何在 `labels:` 之下、既不是注释也不是空行、又不匹配上面两种模式的行，
    **抛 ManifestError**。
    """
    labels: list[dict] = []
    in_labels = False
    cur: dict | None = None
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(r"^labels:\s*$", line):
            in_labels = True
            continue
        if not in_labels:
            # 清单顶层目前只有 `labels:`。将来若加别的顶层键，这里要显式支持。
            if re.match(r"^\S", line):
                raise ManifestError(
                    f"第 {lineno} 行：`labels:` 之外的顶层键 {line!r} 还没有支持")
            continue

        m = re.match(r'^  - name:\s*(.+)$', line)
        if m:
            if cur is not None:
                labels.append(_finish(cur, lineno))
            cur = {"name": _scalar(m.group(1), lineno)}
            continue
        m = re.match(r'^    (color|description):\s*(.+)$', line)
        if m:
            if cur is None:
                raise ManifestError(f"第 {lineno} 行：{m.group(1)} 出现在任何 - name 之前")
            key = m.group(1)
            if key in cur:
                raise ManifestError(f"第 {lineno} 行：{cur['name']} 的 {key} 出现了两次")
            cur[key] = _scalar(m.group(2), lineno)
            continue
        raise ManifestError(f"第 {lineno} 行解析不了：{line!r}")

    if cur is not None:
        labels.append(_finish(cur, lineno))
    if not labels:
        raise ManifestError("清单里一个标签都没有")
    names = [x["name"] for x in labels]
    dupe = {n for n in names if names.count(n) > 1}
    if dupe:
        raise ManifestError(f"重复的标签名：{sorted(dupe)}")
    return labels


def _scalar(raw: str, lineno: int) -> str:
    v = raw.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    raise ManifestError(f"第 {lineno} 行：值必须加引号（拿到 {raw!r}）")


def _finish(cur: dict, lineno: int) -> dict:
    for key in ("color", "description"):
        if key not in cur:
            raise ManifestError(f"标签 {cur['name']} 少了 {key}（在第 {lineno} 行之前）")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", cur["color"]):
        raise ManifestError(
            f"标签 {cur['name']} 的颜色 {cur['color']!r} 不是 6 位十六进制（不带 #）")
    return cur


# ── GitHub 侧 ──────────────────────────────────────────────────────────────

def _gh(args: list[str], repo: str) -> tuple[int, str, str]:
    exe = shutil.which("gh")
    if exe is None:
        return 127, "", "找不到 gh（https://cli.github.com/）"
    proc = subprocess.run(
        [exe, *args],
        capture_output=True,
        # Windows 上默认按 ANSI 代码页解码，标签描述里的中文会当场炸掉，
        # 而报错信息恰恰是这时最需要的（与 #57 同一个形状）。
        encoding="utf-8", errors="replace",
        env={**os.environ, "GH_REPO": repo},
    )
    return proc.returncode, proc.stdout, proc.stderr


def fetch_remote(repo: str) -> list[dict] | None:
    rc, out, err = _gh(
        ["api", "--paginate", f"repos/{repo}/labels",
         "--jq", ".[] | {name, color, description}"], repo)
    if rc != 0:
        print(f"读取远程标签失败：{err.strip()}", file=sys.stderr)
        return None
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def plan(desired: list[dict], remote: list[dict]) -> dict:
    by_name = {r["name"]: r for r in remote}
    create, update, unchanged = [], [], []
    for d in desired:
        r = by_name.get(d["name"])
        if r is None:
            create.append(d)
        elif (r.get("color") or "").lower() != d["color"].lower() \
                or (r.get("description") or "") != d["description"]:
            update.append((d, r))
        else:
            unchanged.append(d)
    # **只报告，绝不删。** 见模块 docstring 第一条。
    foreign = sorted(set(by_name) - {d["name"] for d in desired})
    return {"create": create, "update": update,
            "unchanged": unchanged, "foreign": foreign}


def manual_commands(repo: str, p: dict) -> list[str]:
    """没权限时给出可以原样粘贴执行的命令。"""
    cmds = []
    for d in p["create"]:
        cmds.append(
            f"gh api -X POST repos/{repo}/labels "
            f"-f name={_shq(d['name'])} -f color={_shq(d['color'])} "
            f"-f description={_shq(d['description'])}")
    for d, _ in p["update"]:
        cmds.append(
            f"gh api -X PATCH repos/{repo}/labels/{_urlq(d['name'])} "
            f"-f new_name={_shq(d['name'])} -f color={_shq(d['color'])} "
            f"-f description={_shq(d['description'])}")
    return cmds


def _shq(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _urlq(s: str) -> str:
    from urllib.parse import quote
    return quote(s, safe="")


def apply(repo: str, p: dict) -> int:
    failed = 0
    for d in p["create"]:
        rc, _, err = _gh(["api", "-X", "POST", f"repos/{repo}/labels",
                          "-f", f"name={d['name']}", "-f", f"color={d['color']}",
                          "-f", f"description={d['description']}"], repo)
        print(("  建 " if rc == 0 else "  建失败 ") + d["name"]
              + ("" if rc == 0 else f"：{err.strip()}"))
        failed += rc != 0
    for d, _ in p["update"]:
        rc, _, err = _gh(["api", "-X", "PATCH", f"repos/{repo}/labels/{_urlq(d['name'])}",
                          "-f", f"new_name={d['name']}", "-f", f"color={d['color']}",
                          "-f", f"description={d['description']}"], repo)
        print(("  改 " if rc == 0 else "  改失败 ") + d["name"]
              + ("" if rc == 0 else f"：{err.strip()}"))
        failed += rc != 0
    return failed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default="Tavotto/Tavotto")
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--apply", action="store_true",
                    help="真的改。不给这个就是 dry-run。")
    a = ap.parse_args(argv)

    try:
        desired = parse_manifest(a.manifest.read_text(encoding="utf-8"))
    except ManifestError as e:
        print(f"清单有问题：{e}", file=sys.stderr)
        return 2

    print(f"清单：{a.manifest}（{len(desired)} 个标签）")
    remote = fetch_remote(a.repo)
    if remote is None:
        print("\n拿不到远程现状——下面是**全量**建标签命令，交给有权限的人执行：")
        for c in manual_commands(a.repo, {"create": desired, "update": []}):
            print("  " + c)
        return 1

    # **先打印远程现状**，再打印变更：读的人要能自己核对。
    print(f"\n远程现状（{len(remote)} 个）：")
    for r in sorted(remote, key=lambda x: x["name"]):
        print(f"  {r['name']:<32} #{r.get('color','')} {(r.get('description') or '')[:60]}")

    p = plan(desired, remote)
    print(f"\n变更计划：新建 {len(p['create'])}，修改 {len(p['update'])}，"
          f"已一致 {len(p['unchanged'])}，不认识（**保留不动**）{len(p['foreign'])}")
    for d in p["create"]:
        print(f"  + {d['name']}  #{d['color']}")
    for d, r in p["update"]:
        if (r.get("color") or "").lower() != d["color"].lower():
            print(f"  ~ {d['name']}  颜色 #{r.get('color')} → #{d['color']}")
        if (r.get("description") or "") != d["description"]:
            print(f"  ~ {d['name']}  描述 {(r.get('description') or '')[:40]!r} → {d['description'][:40]!r}")
    for n in p["foreign"]:
        print(f"  · {n}（不在清单里，保留）")

    if not a.apply:
        if p["create"] or p["update"]:
            print("\n这是 dry-run。要真改：加 --apply；没权限就执行下面这些：")
            for c in manual_commands(a.repo, p):
                print("  " + c)
        else:
            print("\n远程已经与清单一致，无事可做。")
        return 0

    print("\n开始应用：")
    failed = apply(a.repo, p)
    if failed:
        print(f"\n{failed} 条失败（多半是没有 issues:write 权限）。"
              f"下面这些交给有权限的人：")
        for c in manual_commands(a.repo, p):
            print("  " + c)
        return 1
    print("完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
