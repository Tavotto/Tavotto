#!/usr/bin/env python3
"""CLA 资格判定：这个 PR 里出现的每一个人，是不是都已经签过、或被显式豁免。

    python3 scripts/ci/cla_gate.py \
        --event pull_request \
        --policy .github/cla-policy.json \
        --pr-author someone \
        --commits-json commits.json \
        [--provider-checks-json check-runs.json]

为什么判定要单独成脚本（而不是几行 Bash 或者一个第三方 action 说了算）：

* 它经 `CI fast gate` 参与合并资格，和 `aggregate_gate.py` 同一条纪律——
  判定逻辑散在 workflow 的 Bash 里没法单测，而「CLA 判错」的两头都不便宜：
  放过一个没签的外部贡献，将来那段代码就不能进任何非 AGPL 的发行版；
  错拦一个已签的人，PR 卡住而且没人看得懂为什么。
* **这个脚本不是签署机制，仓库也不保存签署事实。**
  签署的法律权威**只有一个：签名服务商**（provider）。仓库存的是协议正文、
  版本、哈希、政策与显式豁免——不存 signer 名单。早期版本曾在
  `docs/legal/cla-signatures.json` 里手工维护一份，那会和服务商的数据库
  变成两个权威，分叉之后没有任何机制说得清哪一份算数，因此已经删掉。
  「在 PR 里回一句 I agree 然后 grep 评论」那种东西不在这里，也不该有。
* **唯一的例外是「服务商还不存在」的那段时间。** provider 未配置时，签署事实的
  权威是著作权人私下保存的**双签纸面协议**（双方签字的执行副本）；
  policy 的 `offline_signatures` 只是指向它的索引（账号、不可变账号 id、
  协议与版本、两个签署日期），不含任何个人信息。**任何时刻只有一个权威**：
  provider 一旦标成已配置，`offline_signatures` 必须为空（validate_policy
  结构性地拒绝两者并存）——启用服务商时，线下签署人要先迁进服务商。

判定规则（详见 `decide()`）：

* `--event merge_group`：provider 未走 tavotto-cla-v1 时判 `not_applicable` 并
  **成功**（资格在 PR 阶段验过）。服务模式下要么候选的全部提交（`--commits-json`，
  compare base...head）里出现的每一个人都按不可变 id 命中豁免、本地直接成功，要么
  要求服务在候选提交上给出新鲜凭证。**这一格必须是成功而不是 skipped**——
  `aggregate_gate.py --mode fast` 把 skipped 一律当失败，这个 job 在
  merge_group 上被跳过会把整个 `CI fast gate` 卡死（tests/ 里有专门的用例钉它）。
* 服务模式下有外部贡献者时，判定来自服务的实时回答（ADR 0172，`--eligibility-json`）：
  问不到、超时、回答对不上这次判定，一律不算过。这个脚本自己不联网。
* 服务模式（provider.protocol = tavotto-cla-v1）下 login 命中豁免**不算**身份证明；
  只有可信来源给出的账号 id 全部等于豁免条目的 `github_id` 才本地放行
  （`_id_exempt`），否则一律要服务凭证。服务模式下每条豁免都必须写 `github_id`。
* `--event pull_request`：PR 里出现的每一个人类贡献者，要么在 policy 的豁免表
  里被**点名**，要么由 provider 判定为已签，要么在 `offline_signatures` 里有
  一条**版本对得上、账号 id 对得上**的双签记录。除此之外没有任何人能被判成
  已签——非豁免的人类一律阻断，并给出明确指引。绝不因为「服务还没接上」
  就把外部贡献者当成签过。
* 其他事件：配置错误 → 失败。没设计过的上下文不许静默变绿。

fail-closed 的几处（每一处都是「宁可红」）：

* 认不出 GitHub 账号的 co-author（邮箱不是 GitHub noreply 形态）→ 失败，
  让人来认领，而不是猜一个 login 或者当它不存在；
* 线下签署按 login 找到条目后，还要**按不可变的 GitHub 账号 id 核身份**：
  这个 PR 里该 login 名下**由可信来源**给出的每一个账号 id 都必须等于条目里的
  `github_id`，且至少有一个——login 可以改名、被别人注册，id 不会。可信来源
  只有两个：PR 事件里 PR 作者的 id（`CLA_PR_AUTHOR_ID` / `--pr-author-id`）与
  GitHub API 对提交作者解析出的 `author.id`。`Co-authored-by` 与 noreply 邮箱里
  的数字是贡献者自己写的字符串，**不算身份证明**（Codex 评审 #1630）；
  PR 作者是线下签署人却拿不到 PR 作者 id → 不算；
  签的版本 ≠ 当前协议版本 → 不算（新版本要新同意，见 CLA_VERSIONING.md）；
* 豁免只按 login 精确匹配显式名单，**没有「名字像机器人就放行」这种规则**——
  `dependabot[bot]` 能过是因为它在表里写着，不是因为它带个 `[bot]` 后缀；
* policy 里记的文档哈希与磁盘上的文档对不上 → 失败（改了 CLA 正文却没走
  版本流程，见 docs/legal/CLA_VERSIONING.md）；
* 版本带 `-draft` 后缀 → provider 不许被标成已配置（草案上不存在有效签署，
  见 docs/legal/CLA_VERSIONING.md）；
* provider 说了已配置，却拿不到它的 check 结论 → 失败，不猜。

退出码：0 = success 或 not_applicable；1 = 资格不足；2 = 配置 / 输入错误
（同样让 Gate 红——判定器自己坏了不能算通过）。

stdout 恒输出一行机器可读 JSON；`$GITHUB_STEP_SUMMARY` 存在时另写
人类可读的 Markdown。纯标准库。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

# Windows 上 stdout 一旦不是真控制台（被 CI 捕获 / 管道 / 重定向）就退回系统区域
# 编码（cp1252/cp936），第一句中文或 ✓ 的输出就 UnicodeEncodeError——脚本明明
# 做完了却以非零退出，而父进程只看得见「它挂了」。写法与 build_frontend.py 同源。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

#: 只有这两个事件有明确定义的判定。别的一律配置错误。
KNOWN_EVENTS = ("pull_request", "merge_group")

#: `Co-authored-by: Name <email>` trailer。大小写不敏感，行首匹配。
CO_AUTHOR_RE = re.compile(r"(?im)^\s*Co-authored-by:\s*(?P<name>.*?)\s*<(?P<email>[^>]+)>\s*$")

#: GitHub 的 noreply 邮箱形态：`login@users.noreply.github.com` 或
#: `12345+login@users.noreply.github.com`。**只有这两种能可靠反解出账号**，
#: 别的邮箱一律算认不出（见模块 docstring 的 fail-closed 一节）。
NOREPLY_RE = re.compile(
    r"(?i)^(?:(?P<id>\d+)\+)?(?P<login>[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)@users\.noreply\.github\.com$"
)

#: `offline_signatures` 每一条必须有、且只能有的字段。**故意不收地址、姓名、
#: 邮箱**：那些只在执行副本上，公开仓库里一个字都不放。
OFFLINE_SIGNATURE_KEYS = (
    "login",
    "github_id",
    "agreement",
    "version",
    "contributor_signed_on",
    "countersigned_on",
    "record",
)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: 服务的固定地址（ADR 0172）。只认 https 根地址：不带路径、端口、账号、查询串。
AUTHORITY_ORIGIN_RE = re.compile(r"^https://[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
#: 服务时钟与 runner 时钟允许的偏差（秒）。
ELIGIBILITY_SKEW_SECONDS = 5

#: 草案后缀。带它的版本不可签署，因此也不允许把 provider 标成已配置。
DRAFT_SUFFIX = "-draft"

#: GitHub Actions 自己的 App。**它绝不能被配成 provider**——每个 PR 都能驱动它
#: （PR 里加一个 job、或把已有 job 改名成配置里那个 check 名，就能自己给自己签）。
#: 认应用身份这件事的全部意义，就是排除「被判定的对象能提供的那个来源」。
GITHUB_ACTIONS_APP_SLUG = "github-actions"
GITHUB_ACTIONS_APP_ID = 15368


class ConfigError(Exception):
    """输入 / 配置错误。它也让 Gate 失败：判定器自己坏了不能算通过。"""


# ────────────────────────────────────────────────────────────── 读入与校验


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path, what: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"{what} 找不到：{path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{what} 不是合法 JSON（{path}）：{exc}") from exc


def load_commits(path: Path, expected: int | None) -> list:
    """把 PR 的提交列表读成一个扁平的提交列表。

    来源是 `compare/{base}...{head}` 逐页的 `.commits`（#318：`pulls/{n}/commits`
    的响应封顶 250 条，分页也突破不了，已不再使用）；每条仍是同一种 commit 对象。
    workflow 用 `--jq '.commits'` 取，每页吐一个数组——下面第二种形状。

    **三种形状都要接受，因为 `gh --paginate` 的输出形状随版本而变**：

    * 单个数组 —— gh 2.97 实测对 REST 数组端点会把各页合并（per_page=5、9 页
      仍然是一个合法的 41 条数组）；
    * 多个数组首尾相接（`[...][...]`）—— `gh api --help` 写的是「Each page is
      a separate JSON array or object」，旧版本确实这么输出；
    * 数组的数组 —— 加了 `--slurp` 的形状（**不要**给数组端点加 `--slurp`，
      它会把每页包成一层，正是这里要兼容掉的那种）。

    **但兼容形状远不是重点。** 真正的危险是「只解析到第一页就当成全部」——
    那会让判定器对一个 41 提交的 PR 只看前 30 个贡献者，**报 success 而实际
    漏判**。对一个法律门禁来说这是最坏的结局：它看起来在守，其实没守。

    所以这里**不靠信任分页，靠核对数量**：`expected` 来自 PR 自身的
    `commits` 字段，对不上就当场失败。无论 gh 哪个版本、分不分页、合不合并，
    少一条都红。
    """
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        raise ConfigError(f"commits JSON 是空的（{path}）——取数那步多半失败了")

    docs: list = []
    dec = json.JSONDecoder()
    idx = 0
    while idx < len(raw):
        try:
            obj, end = dec.raw_decode(raw, idx)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"commits JSON 解析失败（{path}，偏移 {idx}）：{exc}") from exc
        docs.append(obj)
        idx = end
        while idx < len(raw) and raw[idx] in " \t\r\n":
            idx += 1

    commits: list = []
    for doc in docs:
        if not isinstance(doc, list):
            raise ConfigError(f"commits JSON 里有非数组的顶层文档：{type(doc).__name__}")
        for item in doc:
            if isinstance(item, list):  # --slurp 的数组的数组
                commits.extend(item)
            else:
                commits.append(item)

    if expected is not None and len(commits) != expected:
        raise ConfigError(
            f"取到 {len(commits)} 个提交，但这个 PR 声明有 {expected} 个——"
            f"分页多半只拿到了第一页。**绝不按不完整的贡献者名单判定**："
            f"漏掉的那些人不会被检查，而门禁会报绿。"
            f"（`gh api --paginate` 的输出形状随版本而变，所以这里核数量而不是信分页）"
        )
    return commits


def validate_policy(policy: dict) -> None:
    """policy 的形状必须严格——安静地解析出空表的判定器会把「谁都没签」判成任何东西。"""
    if not isinstance(policy, dict):
        raise ConfigError("cla-policy 不是对象")
    for key in ("schema", "agreements", "exemptions", "provider"):
        if key not in policy:
            raise ConfigError(f"cla-policy 缺字段 `{key}`")
    if not isinstance(policy["agreements"], dict) or not policy["agreements"]:
        raise ConfigError("cla-policy.agreements 必须是非空对象")
    for name, ag in policy["agreements"].items():
        for key in ("path", "version", "sha256"):
            if key not in ag:
                raise ConfigError(f"cla-policy.agreements['{name}'] 缺 `{key}`")
    if not isinstance(policy["exemptions"], list):
        raise ConfigError("cla-policy.exemptions 必须是数组")
    seen: set[str] = set()
    seen_ids: set[int] = set()
    for i, ex in enumerate(policy["exemptions"]):
        if not isinstance(ex, dict):
            raise ConfigError(f"cla-policy.exemptions[{i}] 不是对象")
        for key in ("login", "kind", "reason"):
            if not ex.get(key):
                # 豁免必须写得出理由——「显式、可复核、有记录」是它存在的条件，
                # 一条没有 reason 的豁免下次没人敢删，也没人说得清为什么在这儿。
                raise ConfigError(
                    f"cla-policy.exemptions[{i}] 缺 `{key}`（豁免必须显式且写明理由）"
                )
        login = ex["login"].lower()
        if login in seen:
            raise ConfigError(f"cla-policy.exemptions 里 `{ex['login']}` 重复")
        seen.add(login)
        if ex["kind"] not in ("rights_holder", "bot"):
            raise ConfigError(
                f"cla-policy.exemptions[{i}].kind=`{ex['kind']}` 认不出"
                "（只允许 rights_holder / bot）"
            )
        if "github_id" in ex:
            gid = ex["github_id"]
            if type(gid) is not int or gid <= 0:
                raise ConfigError(f"cla-policy.exemptions[{i}].github_id 必须是正整数")
            if gid in seen_ids:
                raise ConfigError(f"cla-policy.exemptions 里账号 id {gid} 重复")
            seen_ids.add(gid)
    _validate_provider(policy)
    origin = policy.get("authority_origin")
    if origin is not None and not (isinstance(origin, str) and AUTHORITY_ORIGIN_RE.match(origin)):
        raise ConfigError("cla-policy.authority_origin 必须是 https 根地址（不带路径、端口、账号）")
    if _service_mode(policy):
        if origin is None:
            raise ConfigError(
                "provider 走 tavotto-cla-v1 时必须写 `authority_origin`（服务的固定地址）"
            )
        # 服务模式下本地放行只认不可变账号 id（见 _id_exempt），每条豁免都要写明。
        missing = [ex["login"] for ex in policy["exemptions"] if "github_id" not in ex]
        if missing:
            raise ConfigError(
                "provider 走 tavotto-cla-v1 时每条豁免都必须写 `github_id`（不可变账号 id）："
                + "、".join(missing)
            )
    _validate_offline_signatures(policy, seen)


def _validate_offline_signatures(policy: dict, exempt_logins: set[str]) -> None:
    """`offline_signatures`：服务商还不存在时，双签纸面协议的索引。

    缺省等于空表。形状严格：字段闭集（多一个也不行——防有人顺手把姓名 /
    地址补进公开仓库）、id 是正整数、日期是 ISO、协议键存在、联署不早于
    贡献者签署、login 不与豁免重复。**provider 已配置时它必须为空**：
    两份签署记录同时有效就是两个法律权威，这正是本仓库拒绝 signer 名单的理由。
    """
    entries = policy.get("offline_signatures", [])
    if not isinstance(entries, list):
        raise ConfigError("cla-policy.offline_signatures 必须是数组")
    if entries and policy["provider"]["configured"]:
        raise ConfigError(
            "provider 已配置时 offline_signatures 必须为空——任何时刻签署事实只能有"
            "一个权威。启用服务商前先把线下签署人迁进服务商，再清空这一节"
            "（见 docs/legal/CLA_AUTOMATION_SETUP.md）"
        )
    seen: set[str] = set()
    for i, sig in enumerate(entries):
        where = f"cla-policy.offline_signatures[{i}]"
        if not isinstance(sig, dict):
            raise ConfigError(f"{where} 不是对象")
        if set(sig) != set(OFFLINE_SIGNATURE_KEYS):
            raise ConfigError(
                f"{where} 的字段必须恰好是 {', '.join(OFFLINE_SIGNATURE_KEYS)}"
                f"（多了 {sorted(set(sig) - set(OFFLINE_SIGNATURE_KEYS))}，"
                f"缺了 {sorted(set(OFFLINE_SIGNATURE_KEYS) - set(sig))}）——"
                "姓名、地址、邮箱只在执行副本上，不进公开仓库"
            )
        for key in ("login", "agreement", "version", "record"):
            if not isinstance(sig[key], str) or not sig[key].strip():
                raise ConfigError(f"{where}.{key} 必须是非空字符串")
        if type(sig["github_id"]) is not int or sig["github_id"] <= 0:
            raise ConfigError(f"{where}.github_id 必须是正整数（GitHub 不可变账号 id）")
        if sig["agreement"] not in policy["agreements"]:
            raise ConfigError(f"{where}.agreement=`{sig['agreement']}` 不是 agreements 里的协议")
        for key in ("contributor_signed_on", "countersigned_on"):
            if not isinstance(sig[key], str) or not DATE_RE.match(sig[key]):
                raise ConfigError(f"{where}.{key} 必须是 YYYY-MM-DD")
        if sig["countersigned_on"] < sig["contributor_signed_on"]:
            raise ConfigError(f"{where}：联署日期早于贡献者签署日期")
        login = sig["login"].lower()
        if login in exempt_logins:
            raise ConfigError(f"{where}：`{sig['login']}` 已在豁免表里，不能同时是线下签署人")
        if login in seen:
            raise ConfigError(f"cla-policy.offline_signatures 里 `{sig['login']}` 重复")
        seen.add(login)


def _validate_provider(policy: dict) -> None:
    """provider 段的形状。

    **草案版本上不许把 provider 标成已配置**——那等于宣称一份还没定稿的
    文本已经在收签名了。这条是结构性的，不靠人记得。
    """
    prov = policy["provider"]
    if not isinstance(prov, dict) or "configured" not in prov:
        raise ConfigError("cla-policy.provider 必须是带 `configured` 的对象")
    if not isinstance(prov["configured"], bool):
        raise ConfigError("cla-policy.provider.configured 必须是布尔")
    if not prov["configured"]:
        return
    for key in ("name", "check_name", "app_slug", "app_id"):
        if not prov.get(key):
            raise ConfigError(
                f"provider.configured=true 时必须写明 `{key}`——判定要指向一个具体的、"
                "可核对的 check，**而且要认它来自哪个 App**：只按 check 名匹配的话，"
                "PR 自己加一个同名 job 就能给自己签"
            )
    if not isinstance(prov["app_id"], int):
        raise ConfigError("provider.app_id 必须是 GitHub App 的整数 id")
    if (
        str(prov["app_slug"]).lower() == GITHUB_ACTIONS_APP_SLUG
        or prov["app_id"] == GITHUB_ACTIONS_APP_ID
    ):
        raise ConfigError(
            "provider 不能是 GitHub Actions 本身——**每个 PR 都能驱动它**："
            "在 PR 里加一个 job、或把已有 job 改名成配置里那个 check 名，"
            "就能让判定器认自己签的 CLA。签名服务商必须是独立的 GitHub App。"
        )
    drafts = [
        f"{k} {ag['version']}"
        for k, ag in policy["agreements"].items()
        if str(ag["version"]).endswith(DRAFT_SUFFIX)
    ]
    if drafts:
        raise ConfigError(
            f"协议仍是草案（{'、'.join(drafts)}），不许把 provider 标成已配置——"
            "草案上不存在有效签署，见 docs/legal/CLA_VERSIONING.md"
        )


def verify_documents(policy: dict, root: Path) -> list[str]:
    """policy 记的哈希 vs 磁盘上的正文。对不上就是改了 CLA 没走版本流程。"""
    problems = []
    for name, ag in policy["agreements"].items():
        path = root / ag["path"]
        if not path.is_file():
            problems.append(f"agreement `{name}`：文件不存在 {ag['path']}")
            continue
        actual = sha256_file(path)
        if actual != ag["sha256"]:
            problems.append(
                f"agreement `{name}`：{ag['path']} 的 SHA-256 是 {actual}，"
                f"policy 里记的是 {ag['sha256']}——改了正文就必须走 "
                f"docs/legal/CLA_VERSIONING.md 的版本流程"
            )
    return problems


# ────────────────────────────────────────────────────────────── 贡献者收集


def collect_contributors(
    pr_author: str | None, commits: list, pr_author_id: int | None = None
) -> tuple[list[dict], list[dict]]:
    """把 PR 作者 + 每个 commit 的 author + Co-authored-by 收成一张去重的表。

    纯函数：`commits` 是 PR 的全部提交对象（`GET /repos/{o}/{r}/compare/{base}...{head}` 逐页的 `commits`）。
    只检查 PR 发起人是不够的——一个 PR 里可以有别人写的 commit，也可以有
    co-author，那些人同样在向仓库投稿。
    """
    found: dict[str, dict] = {}
    unresolved: list[dict] = []

    def add(login: str | None, source: str, detail: str = "", account_id=None) -> None:
        if not login:
            return
        key = login.lower()
        entry = found.setdefault(key, {"login": login, "sources": []})
        if source not in entry["sources"]:
            entry["sources"].append(source)
        if detail and detail not in entry.get("details", []):
            entry.setdefault("details", []).append(detail)
        # 账号 id 是这个 login 名下**实际出现过**的不可变身份。线下签署按它核人：
        # login 能改名、能被别人重新注册，id 不会。
        if account_id is not None:
            ids = entry.setdefault("ids", [])
            if account_id not in ids:
                ids.append(account_id)
        else:
            # 这一次出现没有可信 id（trailer、noreply 邮箱、或缺 PR 作者 id）。
            # 服务模式的本地豁免据此拒绝放行（_id_exempt）。
            entry["unidentified_sources"] = True

    add(pr_author, "pr_author", account_id=_account_id(pr_author_id))
    if pr_author and _account_id(pr_author_id) is None:
        # PR 作者的 id 没传进来：线下签署不能只凭 login 认 PR 作者。
        found[pr_author.lower()]["pr_author_id_missing"] = True

    if not isinstance(commits, list):
        raise ConfigError(f"commits JSON 不是数组：{type(commits).__name__}")

    for c in commits:
        if not isinstance(c, dict):
            raise ConfigError("commits JSON 里有非对象条目")
        sha = str(c.get("sha", ""))[:8]
        # GitHub 已经解析好的账号。null = 这个 commit 的邮箱没绑到任何账号。
        author = c.get("author")
        if isinstance(author, dict) and author.get("login"):
            # GitHub 按已验证邮箱解析出的账号——它的 id 是可信的身份证明。
            add(author["login"], "commit_author", sha, _account_id(author.get("id")))
        else:
            meta = (c.get("commit") or {}).get("author") or {}
            email = str(meta.get("email", ""))
            m = NOREPLY_RE.match(email)
            if m:
                # 邮箱是提交者自己写的字符串：认得出 login，但里面的数字不是身份证明。
                add(m.group("login"), "commit_author", sha)
            else:
                unresolved.append(
                    {
                        "kind": "commit_author",
                        "sha": sha,
                        "name": str(meta.get("name", "")),
                        "email": email,
                    }
                )

        message = str((c.get("commit") or {}).get("message", ""))
        for m in CO_AUTHOR_RE.finditer(message):
            email = m.group("email").strip()
            nm = NOREPLY_RE.match(email)
            if nm:
                # trailer 是 PR 作者自己写的：login 要检查，数字不能当身份证明。
                add(nm.group("login"), "co_author", sha)
            else:
                unresolved.append(
                    {"kind": "co_author", "sha": sha, "name": m.group("name"), "email": email}
                )

    out = sorted(found.values(), key=lambda e: e["login"].lower())
    for e in out:
        e.setdefault("details", [])
        e.setdefault("ids", [])
    return out, unresolved


def _group_members(raw) -> list[dict]:
    """队列前缀的有序成员，PR 号和该 PR 的原始 head 都必须精确绑定。"""
    if not isinstance(raw, list) or not 1 <= len(raw) <= 100:
        raise ConfigError("group members 缺失或不完整")
    seen = set()
    for member in raw:
        if (
            not isinstance(member, dict)
            or set(member) != {"pr", "headSha"}
            or type(member["pr"]) is not int
            or member["pr"] <= 0
            or member["pr"] in seen
            or not isinstance(member["headSha"], str)
            or not SHA_RE.fullmatch(member["headSha"])
        ):
            raise ConfigError("group members 的 PR / head 无效或重复")
        seen.add(member["pr"])
    return raw


def _queue_snapshot(pages, repository_id: int) -> list[dict]:
    """核对 gh graphql --paginate 的原始页，绝不把截断页当完整队列。"""
    if not isinstance(pages, list) or not 1 <= len(pages) <= 10:
        raise ConfigError("mergeQueue 页缺失")
    entries, cursors, total = [], set(), None
    for index, page in enumerate(pages):
        try:
            if page.get("errors") not in (None, []):
                raise ValueError("GraphQL errors")
            repo = page["data"]["repository"]
            if type(repo["databaseId"]) is not int or repo["databaseId"] != repository_id:
                raise ValueError("repository")
            connection = repo["mergeQueue"]["entries"]
            count, nodes, info = (
                connection["totalCount"],
                connection["nodes"],
                connection["pageInfo"],
            )
            if (
                type(count) is not int
                or not 1 <= count <= 1000
                or total is not None
                and count != total
                or not isinstance(nodes, list)
                or not 1 <= len(nodes) <= 100
                or type(info["hasNextPage"]) is not bool
                or info["hasNextPage"] != (index < len(pages) - 1)
            ):
                raise ValueError("pagination")
            total = count
            if info["hasNextPage"]:
                cursor = info["endCursor"]
                if not isinstance(cursor, str) or not 1 <= len(cursor) <= 1000 or cursor in cursors:
                    raise ValueError("cursor")
                cursors.add(cursor)
            entries.extend(nodes)
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise ConfigError("mergeQueue 分页、仓库或响应不完整") from exc
    if len(entries) != total:
        raise ConfigError("mergeQueue 枚举条数与 totalCount 不符")
    ids, prs = set(), set()
    try:
        entries.sort(key=lambda e: e["position"])
        for position, entry in enumerate(entries, 1):
            pr = entry["pullRequest"]
            if (
                not isinstance(entry["id"], str)
                or not entry["id"]
                or entry["id"] in ids
                or type(entry["position"]) is not int
                or entry["position"] != position
                or type(pr["number"]) is not int
                or pr["number"] <= 0
                or pr["number"] in prs
                or not isinstance(pr["headRefOid"], str)
                or not SHA_RE.fullmatch(pr["headRefOid"])
                or not isinstance(pr["baseRefName"], str)
                or not pr["baseRefName"]
            ):
                raise ValueError("entry")
            for key in ("baseCommit", "headCommit"):
                if entry[key] is not None and (
                    not isinstance(entry[key]["oid"], str)
                    or not SHA_RE.fullmatch(entry[key]["oid"])
                ):
                    raise ValueError("candidate")
            ids.add(entry["id"])
            prs.add(pr["number"])
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ConfigError("mergeQueue 位置、成员或候选不完整") from exc
    return entries


def _validate_group_commit_content(commits: list) -> None:
    """GitHub 原始提交内容必须齐全，不能把丢失的消息 / 身份字段当空字符串。"""
    if not isinstance(commits, list):
        raise ConfigError("group 提交列表不是数组")
    for item in commits:
        if not isinstance(item, dict):
            raise ConfigError("group 提交内容不是对象")
        meta = item.get("commit")
        if (
            not isinstance(meta, dict)
            or not isinstance(meta.get("message"), str)
            or not isinstance(meta.get("author"), dict)
            or not isinstance(meta["author"].get("email"), str)
            or "author" not in item
        ):
            raise ConfigError("group 提交缺少有效 message / author.email / author")
        author = item["author"]
        if author is not None and (
            not isinstance(author, dict)
            or not isinstance(author.get("login"), str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*(?:\[bot\])?", author["login"])
            or type(author.get("id")) is not int
            or not 0 < author["id"] <= 2**53 - 1
        ):
            raise ConfigError("group 提交的 GitHub author login / id 无效")


def collect_group_contributors(raw, context: dict, commits: list) -> tuple[list, list, list]:
    """独立 GitHub 快照：完整队列前后不变，逐 PR 核作者 id、head 和全部提交。"""
    try:
        repo_id, base, head = (context[k] for k in ("repository_id", "base_sha", "head_sha"))
        if (
            type(repo_id) is not int
            or repo_id <= 0
            or not isinstance(base, str)
            or not SHA_RE.fullmatch(base)
            or not isinstance(head, str)
            or not SHA_RE.fullmatch(head)
            or not isinstance(raw, dict)
            or set(raw)
            != {"repository_id", "base_sha", "head_sha", "queue_before", "queue_after", "prs"}
            or type(raw["repository_id"]) is not int
            or raw["repository_id"] != repo_id
            or raw["base_sha"] != base
            or raw["head_sha"] != head
        ):
            raise ConfigError("group 快照与本次仓库 / base / head 不符")
        queue = _queue_snapshot(raw["queue_before"], repo_id)
        if queue != _queue_snapshot(raw["queue_after"], repo_id):
            raise ConfigError("mergeQueue 在收集期间改变")
        matches = [
            e
            for e in queue
            if e["baseCommit"] == {"oid": base} and e["headCommit"] == {"oid": head}
        ]
        if len(matches) != 1:
            raise ConfigError("mergeQueue 无法唯一定位这个 base / head 候选")
        prefix = queue[: matches[0]["position"]]
        if len(prefix) > 100 or any(e["pullRequest"]["baseRefName"] != "main" for e in prefix):
            raise ConfigError("mergeQueue 候选前缀不完整")
        members = _group_members(
            [
                {"pr": e["pullRequest"]["number"], "headSha": e["pullRequest"]["headRefOid"]}
                for e in prefix
            ]
        )
        if not isinstance(raw["prs"], list) or len(raw["prs"]) != len(members):
            raise ConfigError("group PR 发起人枚举不完整")
        # 候选 compare 只核完整性与 head 绑定；GitHub 生成的提交可能属于合入者。
        # 贡献者与服务 binding 一致，只来自完整原始 PR（含发起人、作者、trailer）。
        _validate_group_commit_content(commits)
        candidate_shas = [item["sha"] for item in commits]
        if (
            not candidate_shas
            or head not in candidate_shas
            or any(not isinstance(s, str) or not SHA_RE.fullmatch(s) for s in candidate_shas)
            or len(set(candidate_shas)) != len(candidate_shas)
        ):
            raise ConfigError("group 候选提交 head 缺失或分页重复")
        all_commits, authors = [], []
        for member, snapshot in zip(members, raw["prs"], strict=True):
            before, after = snapshot["before"], snapshot["after"]

            def metadata(pr):
                return (
                    pr["number"],
                    pr["state"],
                    pr["head"]["sha"],
                    pr["base"]["sha"],
                    pr["base"]["ref"],
                    pr["base"]["repo"]["id"],
                    pr["commits"],
                    pr["user"]["login"],
                    pr["user"]["id"],
                )

            if metadata(before) != metadata(after) or any(
                type(a) is not type(b)
                for a, b in zip(metadata(before), metadata(after), strict=True)
            ):
                raise ConfigError("group PR 在收集期间改变")
            number, state, pr_head, pr_base, ref, pr_repo, count, login, uid = metadata(before)
            if (
                type(number) is not int
                or number != member["pr"]
                or state != "open"
                or pr_head != member["headSha"]
                or ref != "main"
                or type(pr_repo) is not int
                or pr_repo != repo_id
                or not isinstance(pr_base, str)
                or not SHA_RE.fullmatch(pr_base)
                or type(count) is not int
                or not 1 <= count <= 1000
                or not isinstance(login, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*(?:\[bot\])?", login)
                or type(uid) is not int
                or uid <= 0
            ):
                raise ConfigError("group PR 的作者 id / head / 状态无效")
            items = snapshot["commits"]
            if (
                not isinstance(items, list)
                or len(items) != count
                or type(snapshot["total_commits"]) is not int
                or snapshot["total_commits"] != count
                or snapshot["compare_base"] != pr_base
            ):
                raise ConfigError("group PR 的提交枚举不完整")
            _validate_group_commit_content(items)
            shas = [item["sha"] for item in items]
            if (
                any(not isinstance(s, str) or not SHA_RE.fullmatch(s) for s in shas)
                or len(set(shas)) != count
                or pr_head not in shas
            ):
                raise ConfigError("group PR 的提交 head 缺失或分页重复")
            all_commits.extend(items)
            authors.append((login, uid))
        # 复用同一个贡献者收集器，并把所有 PR 发起人作为可信 API 来源合并进去。
        contributors, unresolved = collect_contributors(None, all_commits)
        by_login = {c["login"].lower(): c for c in contributors}
        for login, uid in authors:
            rows, _ = collect_contributors(login, [], uid)
            row = rows[0]
            old = by_login.get(login.lower())
            if old is None:
                by_login[login.lower()] = row
            else:
                old["ids"] = sorted(set(old["ids"] + row["ids"]))
                if "pr_author" not in old["sources"]:
                    old["sources"].append("pr_author")
        return sorted(by_login.values(), key=lambda c: c["login"].lower()), unresolved, members
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ConfigError("group GitHub 快照缺失、错误或不完整") from exc


def _account_id(raw) -> int | None:
    """可信来源（PR 事件 / API）给的正整数 id。别的形状一律当没看到。"""
    if type(raw) is int and raw > 0:
        return raw
    if isinstance(raw, str) and raw.isdigit() and int(raw) > 0:
        return int(raw)
    return None


# ────────────────────────────────────────────────────────────── 判定


def _exemption_for(login: str, policy: dict) -> dict | None:
    for ex in policy["exemptions"]:
        if ex["login"].lower() == login.lower():
            return ex
    return None


def _service_mode(policy: dict) -> bool:
    prov = policy.get("provider") or {}
    return bool(prov.get("configured")) and prov.get("protocol") == "tavotto-cla-v1"


def _id_exempt(contributor: dict, policy: dict) -> bool:
    """按**不可变账号 id** 认豁免，供服务模式在本地直接放行。

    login 能改名、能被别人重新注册，所以服务模式下 login 命中豁免不算数
    （见 decide 里 service_mode 那段）。这里要求三样同时成立：login 命中一条写了
    `github_id` 的豁免；这个 login 名下由**可信来源**给出的 id 至少一个（PR 事件
    的 PR 作者 id、GitHub API 解析的提交作者 id）；且这些 id 全都等于那条豁免的
    `github_id`。`Co-authored-by` 与 noreply 邮箱只给 login 不给可信 id，因此
    只经它们出现的人**不会**被本地放行，交给服务按 id 判。
    """
    ex = _exemption_for(contributor["login"], policy)
    # 缺 PR 作者 id 的情形由 unidentified_sources 兜住（collect_contributors 里 add 未带 id）。
    if ex is None or "github_id" not in ex:
        return False
    ids = contributor.get("ids") or []
    if not ids or any(i != ex["github_id"] for i in ids):
        return False
    # 每一次出现都必须带可信 id：只经 trailer / 邮箱出现过一次也不放行。
    return not contributor.get("unidentified_sources")


def _offline_signature_for(login: str, policy: dict) -> dict | None:
    for sig in policy.get("offline_signatures", []):
        if sig["login"].lower() == login.lower():
            return sig
    return None


def _offline_verdict(sig: dict, contributor: dict, policy: dict) -> str | None:
    """线下双签能不能让这个人通过。返回 None = 通过，否则是不通过的原因。"""
    current = policy["agreements"][sig["agreement"]]["version"]
    if sig["version"] != current:
        return (
            f"`{sig['login']}` 线下签的是 {sig['agreement']} CLA {sig['version']}，"
            f"当前版本是 {current}——新版本要新同意（docs/legal/CLA_VERSIONING.md），"
            "请本人签当前版本后由维护者更新 offline_signatures"
        )
    if contributor.get("pr_author_id_missing"):
        return (
            f"`{sig['login']}` 是 PR 作者且有线下签署记录，但判定器没拿到 PR 作者的"
            "账号 id（CLA_PR_AUTHOR_ID），无法确认是同一个账号——不凭 login 放行"
        )
    ids = contributor.get("ids") or []
    if not ids:
        return (
            f"`{sig['login']}` 有线下签署记录，但这个 PR 里没有可信来源给出它的 GitHub 账号 id"
            "（Co-authored-by / noreply 邮箱里的数字不算），无法确认是同一个账号——"
            "需要它是 PR 作者，或有 GitHub 认得出的、由该账号署名的提交"
        )
    stray = [i for i in ids if i != sig["github_id"]]
    if stray:
        return (
            f"`{sig['login']}` 名下出现了账号 id {stray}，与线下签署记录的 "
            f"{sig['github_id']} 不符——同名不同人（改名或被重新注册），不算已签"
        )
    return None


def signed_logins(
    provider_checks: list | None, policy: dict, context: dict | None = None
) -> tuple[set[str], str | None]:
    """从 provider 的 check-run 结论里读出「谁算已签」。

    返回 (已签 login 的小写集合, 不可用原因)。provider 未配置时永远是
    (空集, 原因)——**未配置不等于放行**。

    判据认三样东西同时成立：check **名字**、它来自哪个 **App**（slug 与整数
    id 都要对）、以及它的 `conclusion == "success"`。

    **为什么必须认 App 而不只认名字**：check-run 的名字是任何集成都能取的。
    只匹配名字的话，一个 PR 在自己的 workflow 里加一个 job、或把已有 job
    改名成配置里那个 check 名，它跑绿之后就出现在同一个 head SHA 上，判定器
    会认——**PR 给自己签了 CLA**，哪怕真正的服务商红了或者压根没跑。这与
    「PR 自带 policy 把自己写进豁免表」是同一个洞的两个入口，堵法也一样：
    **判据的输入不能由被判定的对象提供**（Codex 评审 P1，#184）。

    签署事实的权威在服务商，仓库不复制、不缓存、不二次解释它的数据库。
    """
    prov = policy["provider"]
    if not prov["configured"]:
        return set(), "provider_not_configured"
    if context and context.get("eligibility") is not None and _service_mode(policy):
        return _eligibility_verdict(policy, context)
    if provider_checks is None:
        return set(), "provider_checks_unavailable"
    want, want_slug, want_id = prov["check_name"], prov["app_slug"], prov["app_id"]
    if prov.get("protocol") == "tavotto-cla-v1":
        return _service_verdict(provider_checks, policy, context)
    impostor = False
    for run in provider_checks:
        if not isinstance(run, dict):
            raise ConfigError("provider check-runs JSON 里有非对象条目")
        if run.get("name") != want:
            continue
        app = run.get("app") or {}
        if app.get("slug") != want_slug or app.get("id") != want_id:
            # 名字对、来源不对——这正是冒充的形状。**记下来继续找**：真正的
            # provider check 可能也在这批里，不该被一个冒名的挤掉。
            impostor = True
            continue
        if run.get("status") != "completed":
            return set(), f"provider_check_incomplete:{run.get('status')}"
        if run.get("conclusion") != "success":
            return set(), f"provider_check_not_success:{run.get('conclusion')}"
        return {"*"}, None
    if impostor:
        return set(), "provider_check_wrong_app"
    return set(), "provider_check_missing"


def _policy_digest(policy: dict) -> str:
    trusted = {k: policy[k] for k in ("agreements", "exemptions")}
    raw = json.dumps(trusted, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _eligibility_verdict(policy: dict, context: dict) -> tuple[set[str], str | None]:
    """校验服务的实时判定回答（ADR 0172）。

    回答来自网络、由 cla_eligibility_fetch.py 落盘，这里按不可信输入逐字段核对：
    字段集合恰好相符，仓库、PR 或合并组、head、策略摘要与本次判定一致，nonce 是这次
    校验生成的（存下来的旧回答不能重放），时间落在这次校验之内，贡献者覆盖这里看到的
    每一个人。任何一处不符都不算过；`eligible` 只有是 `True` 才算过。
    """
    answer, group = context["eligibility"], context.get("kind") == "merge_group"
    if isinstance(answer, dict) and set(answer) == {"error"}:
        code = answer["error"]
        code = code if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,64}", code) else "unknown"
        return set(), f"provider_eligibility_unavailable:{code}"
    binding = ("base_sha",) if group else ("pr",)
    if any(
        context.get(k) is None
        for k in binding + ("head_sha", "repository_id", "not_before", "now", "nonce")
    ):
        return set(), "provider_verification_context_missing"
    fields = {
        "protocol",
        "repository_id",
        "kind",
        "head_sha",
        "base_sha",
        "policy_sha256",
        "contributors",
        "revision",
        "observed_at",
        "nonce",
        "eligible",
        "members" if group else "pr",
    }
    try:
        if not isinstance(answer, dict) or set(answer) != fields:
            raise ValueError("fields")
        if answer["protocol"] != "cla-eligibility-v1":
            raise ValueError("protocol")
        if answer["kind"] != ("merge_group" if group else "pr"):
            raise ValueError("kind")
        for key in binding + ("head_sha", "repository_id", "nonce"):
            if answer[key] != context[key] or type(answer[key]) is not type(context[key]):
                raise ValueError("binding")
        if not isinstance(answer["base_sha"], str) or not SHA_RE.match(answer["base_sha"]):
            raise ValueError("base")
        if group:
            try:
                if _group_members(answer["members"]) != _group_members(
                    context.get("group_members")
                ):
                    raise ValueError("members")
            except ConfigError as exc:
                raise ValueError("members") from exc
        if answer["policy_sha256"] != _policy_digest(policy):
            raise ValueError("policy")
        if type(answer["eligible"]) is not bool:
            raise ValueError("eligible")
        if type(answer["revision"]) is not int or answer["revision"] < 0:
            raise ValueError("revision")
        observed = answer["observed_at"]
        if (
            type(observed) is not int
            or observed < context["not_before"] - ELIGIBILITY_SKEW_SECONDS
            or observed > context["now"] + ELIGIBILITY_SKEW_SECONDS
        ):
            raise ValueError("freshness")
        people = answer["contributors"]
        if not isinstance(people, list) or not people:
            raise ValueError("contributors")
        ids_by_login: dict[str, int] = {}
        for person in people:
            if not isinstance(person, dict) or set(person) != {"id", "login"}:
                raise ValueError("contributor")
            if type(person["id"]) is not int or person["id"] <= 0:
                raise ValueError("contributor")
            if not isinstance(person["login"], str) or not person["login"]:
                raise ValueError("contributor")
            login = person["login"].lower()
            if login in ids_by_login:
                raise ValueError("contributor")
            ids_by_login[login] = person["id"]
        # 逐人比对：只比两个集合的话，两个人的 id 互换也能过。
        for seen in context.get("contributors") or []:
            login = seen["login"].lower()
            if login not in ids_by_login or any(
                i != ids_by_login[login] for i in seen.get("ids") or []
            ):
                raise ValueError("author")
    except (KeyError, TypeError, ValueError, AttributeError):
        return set(), "provider_eligibility_invalid"
    if answer["eligible"] is not True:
        return set(), "provider_not_eligible"
    return {"*"}, None


def _service_verdict(checks: list, policy: dict, context: dict | None):
    """Short-lived service proof. A previous green never overrides a newer failure.

    This is a point-in-time check, NOT an expiring GitHub branch-protection grant.
    Activation must additionally arrange a fresh merge-candidate verification.
    """
    prov = policy["provider"]
    binding = ("base_sha",) if context and context.get("kind") == "merge_group" else ("pr",)
    if not context or any(
        context.get(k) is None for k in binding + ("head_sha", "repository_id", "not_before", "now")
    ):
        return set(), "provider_verification_context_missing"
    own = [
        r
        for r in checks
        if isinstance(r, dict)
        and r.get("name") == prov["check_name"]
        and (r.get("app") or {}).get("id") == prov["app_id"]
        and (r.get("app") or {}).get("slug") == prov["app_slug"]
    ]
    if not own or any(type(r.get("id")) is not int for r in own):
        return set(), "provider_check_missing_or_invalid"
    run = max(own, key=lambda r: r["id"])
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        return set(), "provider_latest_check_not_success"
    try:
        # Cloudflare 服务把凭证写在 `output.summary`（services/cla-cloudflare 的 github.ts，
        # src/ci/consumer.ts 读的也是它）；早期 Python 服务写在 `text`，它的 `summary` 是
        # 给人看的说明。所以按内容认：只取以协议行开头的那个字段，都没有就算无效。
        output = run["output"]
        text = next(
            (
                v
                for v in (output.get("summary"), output.get("text"))
                if isinstance(v, str) and v.startswith("tavotto-cla-v1\n")
            ),
            None,
        )
        if text is None:
            raise ValueError("proof")
        prefix, raw = text.split("\n", 1)
        proof = json.loads(raw)
        digest = _policy_digest(policy)
        if prefix != "tavotto-cla-v1" or proof["protocol"] != prefix:
            raise ValueError("protocol")
        if proof["policy_sha256"] != digest or run["head_sha"] != context["head_sha"]:
            raise ValueError("binding")
        if context.get("kind") == "merge_group":
            if proof.get("kind") != "merge_group" or not proof.get("members"):
                raise ValueError("candidate membership")
        elif proof.get("kind") == "merge_group":
            raise ValueError("wrong verification kind")
        for key in binding + ("head_sha", "repository_id"):
            if proof[key] != context[key]:
                raise ValueError("context")
        issued, expires = proof["issued_at"], proof["expires_at"]
        if type(issued) is not int or type(expires) is not int:
            raise ValueError("timestamp")
        if not context["not_before"] <= issued <= context["now"] < expires <= issued + 120:
            raise ValueError("freshness")
        if type(proof["revision"]) is not int or proof["revision"] < 0:
            raise ValueError("revision")
    except (KeyError, TypeError, ValueError, AttributeError):
        return set(), "provider_proof_invalid_or_stale"
    return {"*"}, None


def _unqualified_detail(reason: str | None, policy: dict) -> str:
    """把「为什么没通过」翻译成维护者和贡献者都能照着做的一句话。

    只写 `CLA check failed` 是最没用的一种红：看的人不知道是自己没签、
    还是服务还没接上、还是判定器配错了。
    """
    prov = policy["provider"]
    if reason == "provider_not_configured":
        return (
            f"CLA 签名服务尚未启用（{prov.get('note') or 'provider.configured=false'}）"
            "——目前只有 .github/cla-policy.json 里点名豁免的账号、以及"
            "已与著作权人双签纸面协议（offline_signatures）的账号能通过。"
            "想贡献请先开一个 issue 或写信 contact@tavotto.com，维护者会走人工流程；"
            "启用步骤见 docs/legal/CLA_AUTOMATION_SETUP.md"
        )
    if reason == "provider_check_missing":
        return (
            f"没找到 provider 的 check `{prov.get('check_name')}`——"
            "它可能还没跑完，或者 GitHub App 没装在这个仓库上"
        )
    if reason and reason.startswith("provider_check_incomplete:"):
        return f"provider 的 check 还没跑完（status={reason.split(':', 1)[1]}），等它出结论"
    if reason and reason.startswith("provider_check_not_success:"):
        return f"provider 的 check 结论是 {reason.split(':', 1)[1]}——按它给的链接签署 CLA 后重跑"
    if reason == "provider_check_wrong_app":
        return (
            f"有一个叫 `{prov.get('check_name')}` 的 check，但它**不是来自配置的"
            f"签名服务商**（期望 App `{prov.get('app_slug')}` id={prov.get('app_id')}）"
            "——判定只认那个 App 的结论，同名的别家 check 一律不算"
        )
    if reason == "provider_not_eligible":
        return (
            f"CLA 服务判定：这个 PR 里还有人没签。到 {policy.get('authority_origin')} 签署，"
            "著作权人联署后重跑这个检查，或推一个新提交"
        )
    if reason == "provider_eligibility_invalid":
        return "CLA 服务的回答对不上这次判定（PR、head、作者或时间不符）——判定器不猜，直接红"
    if reason and reason.startswith("provider_eligibility_unavailable:"):
        return (
            f"问不到 CLA 服务的判定（{reason.split(':', 1)[1]}）——服务恢复后重跑这个检查；"
            "问不到就不算过"
        )
    if reason == "provider_checks_unavailable":
        return "取不到 provider 的 check 列表（权限或 API 失败）——判定器不猜，直接红"
    return "没有签署记录"


def _exempt_by_id_verdict(
    event: str, policy: dict, contributors: list[dict], unresolved: list[dict]
) -> dict | None:
    """服务模式下的本地快速放行：出现的每一个人都按不可变 id 命中豁免。

    主仓库几乎所有 PR 与合并组都只有著作权人和机器人，它们不需要、也不该去
    排服务的队（服务端对豁免作者的 PR 不登记）。只要有一个人不是按 id 豁免的、
    或有任何一处认不出账号、或一个人都没收集到，就返回 None，回到原来要求服务
    凭证的判定——这里只会让本来就该过的放得更早，不会放过别的。
    """
    if unresolved or not contributors:
        return None
    if not all(_id_exempt(c, policy) for c in contributors):
        return None
    rows = []
    for c in contributors:
        ex = _exemption_for(c["login"], policy)
        rows.append(
            {
                "login": c["login"],
                "verdict": "exempt",
                "detail": f"{ex['kind']}: {ex['reason']}（账号 id {ex['github_id']}）",
                "sources": c["sources"],
            }
        )
    return {
        "event": event,
        "status": "success",
        "reason": "merge_candidate_exempt_by_id"
        if event == "merge_group"
        else "all_contributors_exempt_by_id",
        "contributors": rows,
        "problems": [],
    }


def decide(
    event: str,
    policy: dict,
    provider_checks: list | None,
    contributors: list[dict],
    unresolved: list[dict] | None = None,
    context: dict | None = None,
) -> dict:
    """核心判定。只做纯计算，不碰环境——单测才测得动每一格。"""
    unresolved = unresolved or []
    if context is not None:
        context = {**context, "contributors": contributors}

    if event == "merge_group" and _service_mode(policy):
        # 必须先独立枚举 PR 发起人，再判断全员豁免；缺快照不能靠提交作者全豁免绕过。
        try:
            _group_members((context or {}).get("group_members"))
        except ConfigError:
            return {
                "event": event,
                "status": "failure",
                "reason": "group_snapshot_required",
                "contributors": [],
                "problems": ["group PR 发起人与成员快照缺失或不完整"],
            }
        if unresolved:
            return {
                "event": event,
                "status": "failure",
                "reason": "unresolved_contributors",
                "contributors": [],
                "problems": [
                    f"unresolved: commit `{u['sha']}` 的 {u['kind']} "
                    f"`{u['name']} <{u['email']}>` 解析不出 GitHub 账号；请用绑定账号的邮箱重新提交"
                    for u in unresolved
                ],
            }

    if event in KNOWN_EVENTS and _service_mode(policy):
        exempt = _exempt_by_id_verdict(event, policy, contributors, unresolved)
        if exempt is not None:
            return exempt

    if event == "merge_group" and _service_mode(policy):
        signed, why = signed_logins(provider_checks, policy, context)
        valid = "*" in signed and context and context.get("kind") == "merge_group"
        return {
            "event": event,
            "status": "success" if valid else "failure",
            "reason": "fresh_merge_candidate_qualified"
            if valid
            else "fresh_merge_verification_required",
            "contributors": [],
            "problems": [] if valid else [why or "Candidate context missing"],
        }
    if event == "merge_group":
        # 队列候选上没有 PR 上下文；资格在 PR 阶段验过了。
        # **必须成功，不能 skipped**：fast gate 把 skipped 当失败。
        return {
            "event": event,
            "status": "not_applicable",
            "reason": "qualified_at_pull_request",
            "contributors": [],
            "problems": [],
        }

    if event not in KNOWN_EVENTS:
        raise ConfigError(f"事件 `{event}` 没有定义过的 CLA 判定——只支持 {'/'.join(KNOWN_EVENTS)}")

    signed, why = signed_logins(provider_checks, policy, context)
    detail_for_missing = _unqualified_detail(why, policy)

    problems: list[str] = []
    service_mode = _service_mode(policy)
    if service_mode and "*" not in signed:
        # A login exemption is not an identity proof. The independent service must
        # verify pinned immutable IDs even when every visible login is exempt.
        problems.append(why or "authoritative_service_verdict_required")
    for u in unresolved:
        # **认不出账号一律红，但必须红得可操作**：说清是哪个 commit、哪个身份、
        # 为什么解析不出、以及维护者可以怎么处置。只有一句 "CLA check failed"
        # 会让一个合法 PR 永久卡住而没人知道下一步该做什么。
        problems.append(
            f"unresolved: commit `{u['sha']}` 的 {u['kind']} "
            f"`{u['name']} <{u['email']}>` 解析不出 GitHub 账号"
            f"（只有 `<login>@users.noreply.github.com` 与 "
            f"`<id>+<login>@users.noreply.github.com` 两种形态能可靠反解）。"
            f"处置：让本人用绑定该账号的邮箱重新提交该 commit；"
            f"或由维护者确认其身份后，在 .github/cla-policy.json 里为其"
            f"补一条写明理由的显式豁免（仅限确实无需授权的情形）"
        )

    rows = []
    for c in contributors:
        login = c["login"]
        ex = _exemption_for(login, policy)
        if ex is not None:
            rows.append(
                {
                    "login": login,
                    "verdict": "exempt",
                    "detail": f"{ex['kind']}: {ex['reason']}",
                    "sources": c["sources"],
                }
            )
            continue
        sig = _offline_signature_for(login, policy)
        if sig is not None:
            offline_problem = _offline_verdict(sig, c, policy)
            if offline_problem is None:
                rows.append(
                    {
                        "login": login,
                        "verdict": "signed",
                        "detail": (
                            f"线下双签 {sig['agreement']} CLA {sig['version']}"
                            f"（贡献者 {sig['contributor_signed_on']}，"
                            f"联署 {sig['countersigned_on']}；账号 id {sig['github_id']}）"
                        ),
                        "sources": c["sources"],
                    }
                )
                continue
            rows.append(
                {
                    "login": login,
                    "verdict": "missing",
                    "detail": offline_problem,
                    "sources": c["sources"],
                }
            )
            problems.append(f"missing: `{login}` — {offline_problem}")
            continue
        if "*" in signed:
            rows.append(
                {
                    "login": login,
                    "verdict": "signed",
                    "detail": f"provider `{policy['provider']['name']}` 判定已签",
                    "sources": c["sources"],
                }
            )
            continue
        rows.append(
            {
                "login": login,
                "verdict": "missing",
                "detail": detail_for_missing,
                "sources": c["sources"],
            }
        )
        problems.append(f"missing: `{login}` — {detail_for_missing}")

    if not rows and not problems:
        # 一个人都没收集到 = 上游取数据出了问题，不是「所有人都签了」。
        problems.append(
            "empty: 这个 PR 里一个贡献者都没收集到——取 PR 数据那步多半失败了，不能当成资格通过"
        )

    status = "failure" if problems else "success"
    reason = "unqualified_contributors" if problems else "all_contributors_qualified"
    return {
        "event": event,
        "status": status,
        "reason": reason,
        "contributors": rows,
        "problems": problems,
    }


# ────────────────────────────────────────────────────────────── 输出


def render_summary(verdict: dict, policy: dict) -> str:
    icon = {"success": "✅", "not_applicable": "⏭️", "failure": "❌"}[verdict["status"]]
    lines = [f"## {icon} CLA check — {verdict['status']}", ""]
    if verdict["status"] == "not_applicable":
        lines += ["合并队列候选上不重新验 CLA——资格在 PR 阶段已经验过。", ""]
        return "\n".join(lines) + "\n"
    if verdict["contributors"]:
        lines += ["| contributor | verdict | detail |", "|---|---|---|"]
        for r in verdict["contributors"]:
            lines.append(f"| `{r['login']}` | {r['verdict']} | {r['detail']} |")
        lines.append("")
    if verdict.get("problems"):
        lines += ["问题："] + [f"- {p}" for p in verdict["problems"]] + [""]
        ind = (
            policy.get("agreements", {})
            .get("individual", {})
            .get("path", "docs/legal/CLA_INDIVIDUAL.md")
        )
        corp = (
            policy.get("agreements", {})
            .get("corporate", {})
            .get("path", "docs/legal/CLA_CORPORATE.md")
        )
        lines += [
            f"协议正文 [`{ind}`]({ind}) / [`{corp}`]({corp})；"
            "签署事实的权威是签名服务商（服务商启用前是著作权人保存的双签纸面协议），"
            "仓库不保存签署人的个人信息——"
            "见 [`docs/legal/CLA_AUTOMATION_SETUP.md`]"
            "(docs/legal/CLA_AUTOMATION_SETUP.md)。",
            "",
        ]
    return "\n".join(lines) + "\n"


def _emit(verdict: dict, policy: dict) -> None:
    print(json.dumps(verdict, ensure_ascii=False, sort_keys=True))
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(render_summary(verdict, policy))
        except OSError as exc:  # summary 写不进不改变结论
            print(f"::warning::写不进 GITHUB_STEP_SUMMARY：{exc}", file=sys.stderr)


def refresh_hashes(policy_path: Path, root: Path) -> int:
    """把 policy 里的哈希按磁盘上的正文重算一遍。**只改哈希，不动版本号**——
    版本是个决定，见 docs/legal/CLA_VERSIONING.md。"""
    policy = load_json(policy_path, "cla-policy")
    validate_policy(policy)
    changed = []
    for name, ag in policy["agreements"].items():
        path = root / ag["path"]
        if not path.is_file():
            print(f"跳过 `{name}`：{ag['path']} 不存在", file=sys.stderr)
            continue
        actual = sha256_file(path)
        if actual != ag["sha256"]:
            changed.append(f"{name}: {ag['sha256'][:12]}… → {actual[:12]}…")
            ag["sha256"] = actual
    policy_path.write_text(
        json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if changed:
        print("哈希已更新：")
        for c in changed:
            print(f"  {c}")
        print("\n版本号没有动。正文如果有实质改动，按 docs/legal/CLA_VERSIONING.md 决定是否 bump。")
    else:
        print("哈希本来就是对的，没有改动。")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--event", help="github.event_name")
    ap.add_argument("--policy", default=".github/cla-policy.json")
    ap.add_argument(
        "--provider-checks-json",
        default=None,
        help="`GET /repos/{o}/{r}/commits/{sha}/check-runs` 的响应文件"
        "（provider.configured=true 时才需要）",
    )
    ap.add_argument("--pr-author", default=None)
    ap.add_argument(
        "--pr-author-id",
        type=int,
        default=None,
        help="PR 作者的不可变账号 id（github.event.pull_request.user.id）。"
        "缺省读环境变量 CLA_PR_AUTHOR_ID——workflow 用环境变量传，"
        "免得默认分支上还不认识这个参数的旧判定器在自举期报错",
    )
    ap.add_argument(
        "--commits-json",
        default=None,
        help="PR 的提交列表：`compare/{base}...{head}` 逐页 `.commits` 首尾相接的文件",
    )
    ap.add_argument(
        "--expected-commits",
        type=int,
        default=None,
        help="这个 PR 声明的提交数（PR 对象的 `commits` 字段）。对不上就失败"
        "——防「分页只取到第一页、静默按不完整的贡献者名单判绿」",
    )
    ap.add_argument("--verification-pr", type=int)
    ap.add_argument("--verification-head")
    ap.add_argument("--verification-base")
    ap.add_argument("--verification-repository-id", type=int)
    ap.add_argument(
        "--verification-not-before",
        type=int,
        help="Trusted workflow/candidate start time; service proof must be issued after it",
    )
    ap.add_argument(
        "--eligibility-json",
        help="cla_eligibility_fetch.py 落盘的服务实时判定（ADR 0172）",
    )
    ap.add_argument("--eligibility-nonce", help="这次校验生成的 nonce，回答必须原样带回")
    ap.add_argument(
        "--group-snapshot-json", help="CI 独立从 GitHub 取到的完整队列前后页、逐 PR 作者与提交快照"
    )
    ap.add_argument("--repo-root", default=".", help="核对协议正文哈希时的仓库根")
    ap.add_argument(
        "--refresh-hashes",
        action="store_true",
        help="按磁盘正文重算 policy 里的哈希（只改哈希，不动版本）",
    )
    args = ap.parse_args(argv)

    root = Path(args.repo_root)
    policy_path = Path(args.policy)

    if args.refresh_hashes:
        try:
            return refresh_hashes(policy_path, root)
        except ConfigError as exc:
            print(f"配置错误：{exc}", file=sys.stderr)
            return 2

    if not args.event:
        print("配置错误：--event 是必须的", file=sys.stderr)
        return 2

    policy: dict = {}
    try:
        policy = load_json(policy_path, "cla-policy")
        validate_policy(policy)
        doc_problems = verify_documents(policy, root)
        if doc_problems:
            raise ConfigError("；".join(doc_problems))

        checks = None
        if args.provider_checks_json:
            raw = load_json(Path(args.provider_checks_json), "provider check-runs JSON")
            checks = raw.get("check_runs", []) if isinstance(raw, dict) else raw
        context = {
            "kind": args.event,
            "pr": args.verification_pr,
            "head_sha": args.verification_head,
            "base_sha": args.verification_base,
            "repository_id": args.verification_repository_id,
            "not_before": args.verification_not_before,
            "now": int(time.time()),
            "nonce": args.eligibility_nonce,
            "eligibility": load_json(Path(args.eligibility_json), "服务实时判定")
            if args.eligibility_json
            else None,
        }
        if args.event == "merge_group":
            contributors, unresolved = [], []
            if _service_mode(policy):
                if (
                    not args.group_snapshot_json
                    or not args.commits_json
                    or args.expected_commits is None
                ):
                    raise ConfigError("服务模式合并组必须独立枚举完整队列、PR 发起人与全部提交")
                commits = load_commits(Path(args.commits_json), args.expected_commits)
                contributors, unresolved, members = collect_group_contributors(
                    load_json(Path(args.group_snapshot_json), "group GitHub 快照"), context, commits
                )
                context["group_members"] = members
            verdict = decide(args.event, policy, checks, contributors, unresolved, context)
        else:
            commits = []
            if args.commits_json:
                commits = load_commits(Path(args.commits_json), args.expected_commits)
            pr_author_id = args.pr_author_id
            if pr_author_id is None and os.environ.get("CLA_PR_AUTHOR_ID", "").isdigit():
                pr_author_id = int(os.environ["CLA_PR_AUTHOR_ID"])
            contributors, unresolved = collect_contributors(args.pr_author, commits, pr_author_id)
            verdict = decide(args.event, policy, checks, contributors, unresolved, context)
    except ConfigError as exc:
        verdict = {
            "event": args.event,
            "status": "failure",
            "reason": "config_error",
            "problems": [str(exc)],
            "contributors": [],
        }
        _emit(verdict, policy)
        return 2

    _emit(verdict, policy)
    return 0 if verdict["status"] in ("success", "not_applicable") else 1


if __name__ == "__main__":
    raise SystemExit(main())
