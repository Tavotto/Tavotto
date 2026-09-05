#!/usr/bin/env python3
"""发行分支 `plugin-stable` 的发布器：把一份**已验证的完整插件**投影成分支上的一个提交。

    python scripts/plugin_publish.py inspect                      # 远端现状（只读）
    python scripts/plugin_publish.py plan      --staging DIR --source-sha SHA          # 演练（默认）
    python scripts/plugin_publish.py bootstrap --staging DIR --source-sha SHA --yes    # 分支不存在时首次创建
    python scripts/plugin_publish.py promote   --staging DIR --source-sha SHA \
        --expected-remote-sha SHA --yes                                             # 正常推进
    python scripts/plugin_publish.py rollback  --to SHA --expected-remote-sha SHA \
        --authorized-by NAME --reason TEXT --yes                                    # 显式回退
    # 旧发行件（没有构建清单）的 bootstrap：包内字节原样保留，收据放在分支根
    python scripts/plugin_publish.py bootstrap --legacy-zip codex-plugin-0.12.0.zip \
        --legacy-sha256 <hex> --legacy-asset-url <url> --release-tag v0.12.0 --yes

`plugin-stable` 是完整成品的投影，不是第二个开发分支：只放安装所需文件、收据、
许可与一份指向 `./codex-plugin` 的 marketplace 清单；没有源码历史、不人工维护、
不合回 main。marketplace 的 `git-subdir` 来源指到它，用户装到的就是这里的内容。

行为契约（每条都有 tests/test_plugin_publish.py 钉着，反证过）：

* **默认演练。** 没有 `--yes` 的任何命令一个写请求都不发；`plan` 永远只读。
* **目标固定。** 远端与分支名在可信配置里：分支必须匹配 `plugin-*`，`main` 结构上
  就写不到；`--remote` 只为测试时指向临时 bare 仓库而存在。
* **发布资格。** staging 必须通过 `plugin_stage.verify_dir`，且清单里的
  `source_sha` == `--source-sha`（由可信发布流程解析出的 SHA）；`promote` 还要求
  该 SHA 可达远端的 `main`，PR / fork / 普通分支的构建提升不了。
* **一次提交整套内容**；推送前任何一步失败远端分支不动。
* **幂等。** 同版本同 content_digest → no-op（退出 0，说明「已发布」）；同版本不同内容
  → 拒绝（退出 3，要求新版本）；新版本号小于远端 → 拒绝（退出 3）。
* **防旧覆盖新。** `promote` / `rollback` 必须带 `--expected-remote-sha`，与 ls-remote
  读到的现状不符即拒绝；推送用 `--force-with-lease=<ref>:<期望旧值>` 再钉一次
  （lease 钉在**读现状那一刻**的 SHA，不是 fetch 之后的）。
* **不重写历史。** 新提交以当前发行提交为父、快进推送；`bootstrap` 只在分支不存在
  时创建（lease 期望值为空 = 远端必须还没有这个 ref）。
* **故障可恢复。** 推送退出码非零时读回远端：等于新提交 = 其实成功了；等于旧值 =
  没落地，可原样重试（退出 4）；别的值 = 有人动过（退出 5，交给人判断）。
  推送成功后再 fetch 一次，核对远端树的 `codex-plugin/` 内容摘要与收据。
* **回退是显式操作。** `rollback --to <分支上一个已验证的旧提交>`：新提交的树 =
  那个提交的树 + 一份新收据（kind=rollback，记下谁授权、为什么），历史保留。
* **发布器不执行插件里的任何代码**；引擎可获得性（GitHub Release / PyPI 上有没有
  同版本）只经 HTTP 只读查询，`--engine-check none --reason …` 才能跳过，理由进收据。

纯标准库；GitHub / PyPI 只读查询走 urllib。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plugin_stage  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RECEIPT = "plugin-release.json"
RECEIPT_SCHEMA = 1
CHANNEL = "stable"
BRANCH = "plugin-stable"
BRANCH_PATTERN = re.compile(r"^plugin-[a-z0-9][a-z0-9-]*$")
MARKETPLACE_TEMPLATE = ROOT / ".agents" / "plugins" / "marketplace.json"
#: 分支上提交的作者身份（机器维护，不冒充任何人）
BOT_NAME = "tavotto-plugin-release"
BOT_EMAIL = "plugin-release@tavotto.invalid"
GIT_TIMEOUT = 300

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 3
EXIT_NOT_LANDED = 4
EXIT_REMOTE_MOVED = 5


def _brand():
    """`engine/brand.py` 是仓库地址的唯一出处；按路径 import，不依赖 tavotto 包装没装。"""
    spec = importlib.util.spec_from_file_location(
        "_tavotto_brand", ROOT / "src" / "tavotto" / "engine" / "brand.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def default_remote() -> str:
    return _brand().REPO_URL + ".git"


class PublishError(Exception):
    def __init__(self, message: str, code: int = EXIT_ERROR):
        super().__init__(message)
        self.code = code


# ------------------------------------------------------------------ git


def _git_env() -> dict[str, str]:
    env = {**os.environ}
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": BOT_NAME,
            "GIT_AUTHOR_EMAIL": BOT_EMAIL,
            "GIT_COMMITTER_NAME": BOT_NAME,
            "GIT_COMMITTER_EMAIL": BOT_EMAIL,
            # 分支内容不许被本机 autocrlf 改写：树里的字节就是发行字节
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "core.autocrlf",
            "GIT_CONFIG_VALUE_0": "false",
            "GIT_CONFIG_KEY_1": "core.safecrlf",
            "GIT_CONFIG_VALUE_1": "false",
        }
    )
    return env


def git(
    repo: Path | None, *args: str, check: bool = True, timeout: int = GIT_TIMEOUT
) -> subprocess.CompletedProcess:
    cmd = ["git", *(["-C", str(repo)] if repo else []), *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_git_env(),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise PublishError(
            f"git {' '.join(args)} 超过 {timeout}s 没有返回", EXIT_NOT_LANDED
        ) from exc
    if check and proc.returncode != 0:
        raise PublishError(f"git {' '.join(args)} 失败：{proc.stderr.strip()[:600]}")
    return proc


def remote_tip(remote: str, branch: str) -> str | None:
    """远端分支现在指向哪儿；不存在回 None。**网络失败抛，不当成「不存在」**。"""
    proc = git(
        None, "ls-remote", "--refs", remote, f"refs/heads/{branch}", check=False, timeout=120
    )
    if proc.returncode != 0:
        raise PublishError(f"ls-remote {remote} 失败：{proc.stderr.strip()[:400]}", EXIT_NOT_LANDED)
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == f"refs/heads/{branch}":
            return parts[0]
    return None


def fetch_branch(repo: Path, remote: str, branch: str, *, depth: int | None = 1) -> str:
    args = ["fetch", "--quiet"]
    if depth:
        args.append(f"--depth={depth}")
    args += [remote, f"refs/heads/{branch}"]
    git(repo, *args)
    return git(repo, "rev-parse", "FETCH_HEAD").stdout.strip()


def read_receipt(repo: Path, commit: str) -> dict | None:
    proc = git(repo, "show", f"{commit}:{RECEIPT}", check=False)
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        raise PublishError(f"{commit[:12]} 上的 {RECEIPT} 不是合法 JSON：{exc}")
    if not isinstance(data, dict) or data.get("schema") != RECEIPT_SCHEMA:
        raise PublishError(f"{commit[:12]} 上的 {RECEIPT} schema 不是 {RECEIPT_SCHEMA}")
    return data


def tree_digest(repo: Path, commit: str) -> str:
    """远端提交里 `codex-plugin/` 的 content_digest——**从树重算**，不信收据自报。"""
    out = git(repo, "ls-tree", "-r", "-z", commit, "--", plugin_stage.PLUGIN_SUBDIR).stdout
    entries: list[tuple[str, str, str]] = []
    for rec in out.split("\0"):
        if not rec:
            continue
        meta, _tab, path = rec.partition("\t")
        mode, kind, blob = meta.split()
        if kind != "blob":
            raise PublishError(f"{commit[:12]} 的树里 {path} 不是普通文件（{kind}）")
        rel = path[len(plugin_stage.PLUGIN_SUBDIR) + 1 :]
        if rel == plugin_stage.BUILD_MANIFEST:
            continue
        data = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "blob", blob],
            capture_output=True,
            check=True,
            env=_git_env(),
        ).stdout
        entries.append((rel, mode, plugin_stage.sha256_bytes(data)))
    if not entries:
        raise PublishError(f"{commit[:12]} 的树里没有 {plugin_stage.PLUGIN_SUBDIR}/")
    return plugin_stage.content_digest(entries)


# ------------------------------------------------------------------ 输入


def semver(v: str) -> tuple[int, int, int]:
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", v or "")
    if not m:
        raise PublishError(f"版本号不合法：{v!r}")
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def staging_identity(staging: Path, *, source_sha: str | None, legacy: bool) -> dict:
    """验 staging，回它的三种身份。legacy（没有清单的旧发行件）另算 digest。"""
    problems = plugin_stage.verify_dir(staging, source_sha=source_sha, legacy=legacy)
    if problems:
        raise PublishError("staging 没通过验证，拒绝发布：\n  " + "\n  ".join(problems))
    if legacy:
        entries = []
        for p in plugin_stage._walk(staging):
            rel = plugin_stage._rel(staging, p)
            mode = "100755" if os.name != "nt" and os.access(p, os.X_OK) else "100644"
            entries.append((rel, mode, plugin_stage.sha256_file(p)))
        pj = json.loads((staging / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        return {
            "version": pj["version"],
            "content_digest": plugin_stage.content_digest(entries),
            "source_sha": source_sha,
            "plugin_build": None,
        }
    manifest = plugin_stage.read_manifest(staging)
    assert manifest is not None
    return {
        "version": manifest["plugin_version"],
        "content_digest": manifest["content_digest"],
        "source_sha": manifest["source_sha"],
        "plugin_build": {
            k: manifest.get(k)
            for k in (
                "build_inputs_fingerprint",
                "lockfile_sha256",
                "toolchain",
                "min_tavotto_version",
            )
        },
    }


def _http_ok(url: str, fetch=None) -> tuple[bool, str]:
    if fetch is not None:
        return fetch(url)
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": BOT_NAME}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status == 200, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


def engine_check(version: str, checks: list[str], *, fetch=None) -> dict:
    """匹配版本的引擎能不能经用户正常渠道拿到：GitHub Release（桌面版）与 PyPI（pip/pipx）。

    `plugin-stable` 推进之后用户装到的插件要求 `min_tavotto_version`；若那个版本的引擎
    还没发出去，用户会撞上降级 server。这里问的是**渠道上有没有**，不是「造出来没有」。
    """
    brand = _brand()
    urls = {
        "github-release": f"https://api.github.com/repos/{brand.REPO_OWNER}/{brand.REPO_NAME}/releases/tags/v{version}",
        "pypi": f"https://pypi.org/pypi/{brand.DIST_NAME}/{version}/json",
    }
    results: dict[str, dict] = {}
    problems: list[str] = []
    for name in checks:
        if name not in urls:
            raise PublishError(f"不认识的 engine check：{name}（认识的：{', '.join(urls)}）")
        ok, detail = _http_ok(urls[name], fetch)
        results[name] = {"url": urls[name], "ok": ok, "detail": detail}
        if not ok:
            problems.append(f"{name}: {urls[name]} → {detail}")
    if problems:
        raise PublishError(
            "匹配版本的引擎还拿不到，stable 不能先于引擎推进：\n  " + "\n  ".join(problems),
            EXIT_REFUSED,
        )
    return results


# ------------------------------------------------------------------ 组树


def _marketplace_for_branch() -> str:
    """分支根的 marketplace 清单：保留 main 那份的名字 / policy / category，来源改成
    `./codex-plugin`（本地）。给不支持 git-subdir 的客户端一条
    `codex plugin marketplace add Tavotto/Tavotto --ref plugin-stable` 的后路。"""
    data = json.loads(MARKETPLACE_TEMPLATE.read_text(encoding="utf-8"))
    plugins = data.get("plugins") or []
    if len(plugins) != 1 or plugins[0].get("name") != "tavotto":
        raise PublishError(f"{MARKETPLACE_TEMPLATE} 的形状不是预期的单插件清单")
    plugins[0]["source"] = {"source": "local", "path": f"./{plugin_stage.PLUGIN_SUBDIR}"}
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _readme(receipt: dict) -> str:
    brand = _brand()
    return (
        f"# {brand.PRODUCT_NAME} Codex 插件 · 发行分支 `{receipt['branch']}`\n\n"
        "**机器维护，不要手工提交，不合回 main。** 这条分支上的每个提交是一份经过\n"
        "发布链验证的完整插件（含内嵌画布），由 `scripts/plugin_publish.py` 从固定的\n"
        "源码 commit 构建并投影到这里。源码在 `main`。\n\n"
        f"当前：插件 {receipt['version']}，源码 `{(receipt.get('source_sha') or '')[:12]}`，"
        f"内容摘要 `{receipt['content_digest'][:12]}…`（详见 `{RECEIPT}`）。\n\n"
        "安装：\n\n"
        "```sh\n"
        f"codex plugin marketplace add {brand.CODEX_MARKETPLACE} --sparse .agents/plugins\n"
        f"codex plugin add {brand.CODEX_PLUGIN_REF}\n"
        "```\n\n"
        "旧客户端（不支持 `git-subdir` 来源）的后路：\n\n"
        "```sh\n"
        f"codex plugin marketplace add {brand.CODEX_MARKETPLACE} --ref {receipt['branch']} --sparse .agents/plugins --sparse codex-plugin\n"
        "```\n"
    )


def build_commit(
    repo: Path,
    *,
    plugin_dir: Path,
    receipt: dict,
    parent: str | None,
    message: str,
) -> str:
    """把 `plugin_dir` + 收据 + 分支根文件组成一棵树，造一个以 `parent` 为父的提交。
    **只写本地对象**，不碰任何 ref、不推送。"""
    wt = repo / "wt"
    if wt.exists():
        shutil.rmtree(wt)
    dest = wt / plugin_stage.PLUGIN_SUBDIR
    dest.mkdir(parents=True)
    for p in plugin_stage._walk(plugin_dir):
        rel = plugin_stage._rel(plugin_dir, p)
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, out)
        if os.name != "nt" and os.access(p, os.X_OK):
            out.chmod(0o755)
    (wt / RECEIPT).write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (wt / "README.md").write_text(_readme(receipt), encoding="utf-8")
    # `* -text`：这条分支上没有任何文本会被 checkout 时换行转换——Windows 上
    # `core.autocrlf=true` 的克隆拿到的字节与 zip 里的逐字节相同
    (wt / ".gitattributes").write_text("* -text\n", encoding="utf-8")
    (wt / ".agents" / "plugins").mkdir(parents=True)
    (wt / ".agents" / "plugins" / "marketplace.json").write_text(
        _marketplace_for_branch(), encoding="utf-8"
    )
    lic = plugin_dir / "LICENSE"
    if lic.is_file():
        shutil.copyfile(lic, wt / "LICENSE")

    index = repo / "publish.index"
    if index.exists():
        index.unlink()
    env_index = {"GIT_INDEX_FILE": str(index)}
    base = ["git", "--git-dir", str(repo / ".git"), "--work-tree", str(wt)]

    def run(*args: str) -> str:
        proc = subprocess.run(
            [*base, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**_git_env(), **env_index},
            cwd=str(wt),
            timeout=GIT_TIMEOUT,
        )
        if proc.returncode != 0:
            raise PublishError(f"git {' '.join(args)} 失败：{proc.stderr.strip()[:400]}")
        return proc.stdout.strip()

    run("add", "-A", "--", ".")
    tree = run("write-tree")
    args = ["commit-tree", tree, "-m", message]
    if parent:
        args += ["-p", parent]
    return run(*args)


# ------------------------------------------------------------------ 推送与读回


def push(
    remote: str, branch: str, commit: str, *, expected_old: str | None, repo: Path
) -> tuple[str, str]:
    """推送并回 (outcome, detail)。outcome ∈ pushed / rejected / unknown。

    lease 的期望值是**读现状那一刻**的 SHA（bootstrap 时为空串 = 远端必须还没有这个 ref），
    不是 fetch 之后再取的——先 fetch 再 lease 会让 lease 恒真（memory：lease-neutralized-by-fetch）。
    """
    ref = f"refs/heads/{branch}"
    lease = f"--force-with-lease={ref}:{expected_old or ''}"
    proc = git(
        repo, "push", "--quiet", lease, remote, f"{commit}:{ref}", check=False, timeout=GIT_TIMEOUT
    )
    if proc.returncode == 0:
        return "pushed", ""
    err = proc.stderr.strip()
    if "stale info" in err or "[rejected]" in err or "remote ref" in err and "changed" in err:
        return "rejected", err[-600:]
    return "unknown", err[-600:]


def readback(remote: str, branch: str, commit: str, expected_old: str | None) -> str:
    """推送响应丢失时的判决：landed / not_landed / moved。"""
    tip = remote_tip(remote, branch)
    if tip == commit:
        return "landed"
    if tip == expected_old:
        return "not_landed"
    return "moved"


def verify_remote(repo: Path, remote: str, branch: str, commit: str, expect_digest: str) -> None:
    got = fetch_branch(repo, remote, branch)
    if got != commit:
        raise PublishError(
            f"推送后 fetch 到的 tip 是 {got[:12]}，不是刚发的 {commit[:12]}", EXIT_REMOTE_MOVED
        )
    receipt = read_receipt(repo, commit)
    if receipt is None or receipt.get("content_digest") != expect_digest:
        raise PublishError("推送后的收据与 staging 的内容摘要不一致", EXIT_REMOTE_MOVED)
    digest = tree_digest(repo, commit)
    if digest != expect_digest:
        raise PublishError(
            f"推送后远端树的 content_digest {digest[:12]} ≠ staging {expect_digest[:12]}",
            EXIT_REMOTE_MOVED,
        )


# ------------------------------------------------------------------ 决策


def decide(
    *,
    mode: str,
    remote_sha: str | None,
    remote_receipt: dict | None,
    remote_digest: str | None,
    new_version: str,
    new_digest: str,
) -> dict:
    """纯判定：这次该做什么。不碰网络、不碰磁盘。"""
    if mode == "bootstrap":
        if remote_sha is not None:
            return {
                "action": "refuse",
                "code": EXIT_REFUSED,
                "reason": f"分支已存在（{remote_sha[:12]}），bootstrap 只允许创建不存在的分支——用 promote",
            }
        return {"action": "bootstrap", "code": EXIT_OK, "reason": "分支不存在，首次创建"}
    if mode == "promote":
        if remote_sha is None:
            return {
                "action": "refuse",
                "code": EXIT_REFUSED,
                "reason": "分支还不存在——先 bootstrap",
            }
        if remote_receipt is None:
            return {
                "action": "refuse",
                "code": EXIT_REFUSED,
                "reason": f"远端 {remote_sha[:12]} 上没有收据 {RECEIPT}——不是这个发布器维护的分支",
            }
        if remote_digest != remote_receipt.get("content_digest"):
            return {
                "action": "refuse",
                "code": EXIT_REFUSED,
                "reason": "远端收据自报的 content_digest 与树重算的不符——分支被手工改过，先人工核查",
            }
        rv = remote_receipt.get("version")
        if semver(new_version) < semver(rv):
            return {
                "action": "refuse",
                "code": EXIT_REFUSED,
                "reason": f"新版本 {new_version} 低于远端 {rv}——旧任务晚到；要退回旧版请走 rollback",
            }
        if semver(new_version) == semver(rv):
            if new_digest == remote_digest:
                return {
                    "action": "noop",
                    "code": EXIT_OK,
                    "reason": f"{new_version} 已发布，内容一致",
                }
            return {
                "action": "refuse",
                "code": EXIT_REFUSED,
                "reason": f"远端已有 {rv} 但内容不同（{remote_digest[:12]} ≠ {new_digest[:12]}）——同版本不许静默覆盖，请发新版本",
            }
        return {"action": "promote", "code": EXIT_OK, "reason": f"{rv} → {new_version}"}
    raise PublishError(f"不认识的模式 {mode}")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_receipt(
    *,
    kind: str,
    branch: str,
    identity: dict,
    previous: str | None,
    release_tag: str | None,
    engine: dict,
    legacy: dict | None,
    audit: dict,
    restores: str | None = None,
    authorization: dict | None = None,
) -> dict:
    return {
        "schema": RECEIPT_SCHEMA,
        "channel": CHANNEL,
        "branch": branch,
        "plugin": "tavotto",
        "kind": kind,
        "version": identity["version"],
        "content_digest": identity["content_digest"],
        "source_sha": identity.get("source_sha"),
        "release_tag": release_tag,
        "plugin_build": identity.get("plugin_build"),
        "legacy_bootstrap": legacy,
        "engine_check": engine,
        "previous": previous,
        "restores": restores,
        "authorization": authorization,
        "published_at": _now(),
        "audit": audit,
    }


# ------------------------------------------------------------------ 命令


def _check_branch(branch: str) -> None:
    if not BRANCH_PATTERN.match(branch):
        raise PublishError(
            f"分支名 {branch!r} 不合法：发行分支必须匹配 plugin-*（main 结构上就写不到）"
        )


def _ancestor_of_main(repo: Path, remote: str, sha: str) -> bool:
    """`sha` 可达远端 main？可信发布流程的 SHA 必然满足；fork / 普通分支的不满足。"""
    git(repo, "fetch", "--quiet", remote, "refs/heads/main")
    main_tip = git(repo, "rev-parse", "FETCH_HEAD").stdout.strip()
    proc = git(repo, "fetch", "--quiet", remote, sha, check=False)
    if proc.returncode != 0:
        return False
    return git(repo, "merge-base", "--is-ancestor", sha, main_tip, check=False).returncode == 0


def run_publish(args, *, fetch=None) -> int:
    branch = args.branch
    _check_branch(branch)
    remote = args.remote or default_remote()
    mode = args.command
    legacy = None
    audit = dict(kv.split("=", 1) for kv in (args.audit or []))
    with tempfile.TemporaryDirectory(prefix="plugin-publish-") as tmp_s:
        tmp = Path(tmp_s)
        repo = tmp / "repo"
        repo.mkdir()
        git(repo, "init", "--quiet", "-b", "publish")
        staging = args.staging

        if mode in ("bootstrap", "plan") and args.legacy_zip:
            zip_path = Path(args.legacy_zip)
            if not zip_path.is_file():
                raise PublishError(f"没有这个文件：{zip_path}")
            got = plugin_stage.sha256_file(zip_path)
            if not args.legacy_sha256 or got != args.legacy_sha256.lower():
                raise PublishError(
                    f"{zip_path.name} 的 sha256 {got[:12]}… 与 --legacy-sha256 不符——旧发行件必须先有外部校验和",
                    EXIT_REFUSED,
                )
            staging = plugin_stage.unpack_zip(zip_path, tmp / "legacy")
            legacy = {
                "asset_url": args.legacy_asset_url,
                "sha256": got,
                "release_tag": args.release_tag,
                "zip_name": zip_path.name,
                "note": "legacy bootstrap: 包内字节原样保留，没有 plugin-build.json",
            }
            if not args.release_tag:
                raise PublishError(
                    "legacy bootstrap 必须给 --release-tag（旧发行件来自哪个 Release）"
                )
        if staging is None and mode in ("bootstrap", "promote", "plan"):
            raise PublishError(f"{mode} 需要 --staging（或 bootstrap 的 --legacy-zip）")

        # ---- 远端现状 ----
        tip = remote_tip(remote, branch)
        remote_receipt = None
        remote_digest = None
        if tip is not None:
            fetched = fetch_branch(repo, remote, branch, depth=None if mode == "rollback" else 1)
            if fetched != tip:
                raise PublishError(
                    f"ls-remote 与 fetch 之间远端从 {tip[:12]} 变成了 {fetched[:12]}——重新判断",
                    EXIT_REFUSED,
                )
            remote_receipt = read_receipt(repo, tip)
            if remote_receipt is not None:
                remote_digest = tree_digest(repo, tip)

        if mode == "inspect":
            print(
                json.dumps(
                    {
                        "remote": remote,
                        "branch": branch,
                        "tip": tip,
                        "receipt": remote_receipt,
                        "tree_digest": remote_digest,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return EXIT_OK

        if mode in ("promote", "rollback"):
            if not args.expected_remote_sha:
                raise PublishError(
                    f"{mode} 必须带 --expected-remote-sha（读到的现状：{tip}）", EXIT_REFUSED
                )
            if args.expected_remote_sha != tip:
                raise PublishError(
                    f"远端 {branch} 现在是 {tip}，与 --expected-remote-sha {args.expected_remote_sha} 不符——"
                    f"有别的发布在你之后落地了，重新判断再来",
                    EXIT_REFUSED,
                )

        # ---- rollback ----
        if mode == "rollback":
            if not (args.to and args.authorized_by and args.reason):
                raise PublishError(
                    "rollback 需要 --to / --authorized-by / --reason 三者齐全", EXIT_REFUSED
                )
            if tip is None or remote_receipt is None:
                raise PublishError("分支不存在或没有收据，无从回退", EXIT_REFUSED)
            if git(repo, "merge-base", "--is-ancestor", args.to, tip, check=False).returncode != 0:
                raise PublishError(
                    f"{args.to[:12]} 不在 {branch} 的历史上——只能回退到这条分支上已发布过的提交",
                    EXIT_REFUSED,
                )
            old_receipt = read_receipt(repo, args.to)
            if old_receipt is None:
                raise PublishError(f"{args.to[:12]} 上没有收据，不是一次已验证的发布", EXIT_REFUSED)
            old_digest = tree_digest(repo, args.to)
            if old_digest != old_receipt.get("content_digest"):
                raise PublishError(
                    f"{args.to[:12]} 的树与它的收据不符，拒绝回退到一个自相矛盾的快照", EXIT_REFUSED
                )
            # 把旧提交的插件目录检出到临时目录当作 staging
            restore_dir = tmp / "restore"
            restore_dir.mkdir()
            listing = git(
                repo,
                "ls-tree",
                "-r",
                "-z",
                "--name-only",
                args.to,
                "--",
                plugin_stage.PLUGIN_SUBDIR,
            ).stdout
            for path in listing.split("\0"):
                if not path:
                    continue
                rel = path[len(plugin_stage.PLUGIN_SUBDIR) + 1 :]
                out = restore_dir / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(
                    subprocess.run(
                        ["git", "-C", str(repo), "show", f"{args.to}:{path}"],
                        capture_output=True,
                        check=True,
                        env=_git_env(),
                    ).stdout
                )
            identity = {
                "version": old_receipt["version"],
                "content_digest": old_digest,
                "source_sha": old_receipt.get("source_sha"),
                "plugin_build": old_receipt.get("plugin_build"),
            }
            receipt = make_receipt(
                kind="rollback",
                branch=branch,
                identity=identity,
                previous=tip,
                release_tag=old_receipt.get("release_tag"),
                engine={
                    "checks": {},
                    "skipped_reason": "rollback restores a previously qualified snapshot",
                },
                legacy=old_receipt.get("legacy_bootstrap"),
                audit=audit,
                restores=args.to,
                authorization={"authorized_by": args.authorized_by, "reason": args.reason},
            )
            plan = {
                "action": "rollback",
                "code": EXIT_OK,
                "reason": f"{remote_receipt.get('version')}@{tip[:12]} → {identity['version']}@{args.to[:12]}",
            }
            plugin_dir = restore_dir
            expected_old = tip
        else:
            # ---- bootstrap / promote / plan ----
            identity = staging_identity(
                staging, source_sha=args.source_sha, legacy=legacy is not None
            )
            if legacy is None and args.source_sha and identity["source_sha"] != args.source_sha:
                raise PublishError("staging 清单的 source_sha 与 --source-sha 不一致", EXIT_REFUSED)
            effective_mode = (
                "bootstrap"
                if (mode == "plan" and tip is None)
                else ("promote" if mode == "plan" else mode)
            )
            plan = decide(
                mode=effective_mode,
                remote_sha=tip,
                remote_receipt=remote_receipt,
                remote_digest=remote_digest,
                new_version=identity["version"],
                new_digest=identity["content_digest"],
            )
            engine = {"checks": {}, "skipped_reason": None}
            if plan["action"] in ("bootstrap", "promote"):
                if (
                    legacy is None
                    and args.source_sha
                    and mode != "plan"
                    and not _ancestor_of_main(repo, remote, args.source_sha)
                ):
                    raise PublishError(
                        f"{args.source_sha[:12]} 不可达远端 main——只有已合入 main 的提交能提升为 stable",
                        EXIT_REFUSED,
                    )
                checks = [
                    c
                    for c in (args.engine_check or "github-release,pypi").split(",")
                    if c and c != "none"
                ]
                if checks:
                    engine["checks"] = engine_check(identity["version"], checks, fetch=fetch)
                else:
                    if not args.reason:
                        raise PublishError("--engine-check none 必须给 --reason", EXIT_REFUSED)
                    engine["skipped_reason"] = args.reason
            receipt = make_receipt(
                kind=effective_mode,
                branch=branch,
                identity=identity,
                previous=tip,
                release_tag=args.release_tag or (f"v{identity['version']}"),
                engine=engine,
                legacy=legacy,
                audit=audit,
            )
            plugin_dir = staging
            expected_old = tip

        summary = {
            "remote": remote,
            "branch": branch,
            "remote_tip": tip,
            "remote_version": (remote_receipt or {}).get("version"),
            "new_version": receipt["version"],
            "new_content_digest": receipt["content_digest"],
            "source_sha": receipt.get("source_sha"),
            "kind": receipt["kind"],
            **plan,
            "will_push": bool(args.yes)
            and plan["action"] not in ("noop", "refuse")
            and mode != "plan",
        }
        if plan["action"] == "refuse":
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            print(f"拒绝：{plan['reason']}", file=sys.stderr)
            return plan["code"]
        if plan["action"] == "noop":
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            print(f"无事可做：{plan['reason']}")
            return EXIT_OK

        message = f"{branch}: {receipt['kind']} {receipt['version']} from {(receipt.get('source_sha') or 'legacy')[:12]}"
        commit = build_commit(
            repo, plugin_dir=plugin_dir, receipt=receipt, parent=tip, message=message
        )
        summary["commit"] = commit
        summary["receipt"] = receipt
        if args.receipt_out:
            Path(args.receipt_out).write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        if mode == "plan" or not args.yes:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            print(
                f"\n演练：将以 {(tip or '（无）')[:12]} 为父提交 {commit[:12]}（{message}）——没有 --yes，一个写请求都没发。"
            )
            return EXIT_OK

        outcome, detail = push(remote, branch, commit, expected_old=expected_old, repo=repo)
        if outcome == "rejected":
            raise PublishError(
                f"远端拒绝（lease 不成立，分支在此期间被改过）：{detail}", EXIT_REFUSED
            )
        if outcome == "unknown":
            verdict = readback(remote, branch, commit, expected_old)
            if verdict == "not_landed":
                raise PublishError(
                    f"推送没有落地（远端仍是 {(expected_old or '无')[:12]}），可原样重试：{detail}",
                    EXIT_NOT_LANDED,
                )
            if verdict == "moved":
                raise PublishError(
                    f"推送结果未知且远端已不是期望值（{remote_tip(remote, branch)}），交给人判断：{detail}",
                    EXIT_REMOTE_MOVED,
                )
            summary["push"] = "landed-after-unknown"
        else:
            summary["push"] = "pushed"
        verify_remote(repo, remote, branch, commit, receipt["content_digest"])
        summary["verified_remote"] = True
        if args.receipt_out:
            Path(args.receipt_out).write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(
            f"\n已发布：{branch} → {commit[:12]}（{receipt['kind']} {receipt['version']}），远端树与收据已核对。"
        )
        return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    plugin_stage._force_utf8()
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("command", choices=("inspect", "plan", "bootstrap", "promote", "rollback"))
    ap.add_argument("--staging", type=Path, help="plugin_stage 组装并验证过的目录")
    ap.add_argument("--source-sha", help="可信流程解析出的源码 SHA（必须与清单一致）")
    ap.add_argument(
        "--remote", default=None, help="（测试用）临时 bare 仓库；默认取 brand.REPO_URL"
    )
    ap.add_argument(
        "--branch", default=BRANCH, help=f"发行分支（必须匹配 plugin-*，默认 {BRANCH}）"
    )
    ap.add_argument("--expected-remote-sha", help="promote / rollback 必填：你看到的远端现状")
    ap.add_argument("--release-tag", help="对应的 Release tag（默认 v<版本>）")
    ap.add_argument(
        "--engine-check", default=None, help="逗号分隔：github-release,pypi；none 需配 --reason"
    )
    ap.add_argument("--reason", help="跳过引擎检查 / 回退的理由（进收据）")
    ap.add_argument("--legacy-zip", help="bootstrap：旧 Release 上的 codex-plugin-<版本>.zip")
    ap.add_argument("--legacy-sha256", help="bootstrap：旧 zip 的外部校验和（GitHub asset digest）")
    ap.add_argument("--legacy-asset-url", help="bootstrap：旧 zip 的下载地址（进收据）")
    ap.add_argument("--to", help="rollback：回退到分支上的哪个提交")
    ap.add_argument("--authorized-by", help="rollback：谁授权")
    ap.add_argument(
        "--audit",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="进收据的审计信息（run id 等）",
    )
    ap.add_argument("--receipt-out", help="把计划 / 结果写到这个文件")
    ap.add_argument("--yes", action="store_true", help="真的推送；不带 = 演练")
    args = ap.parse_args(argv)
    try:
        return run_publish(args)
    except PublishError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
