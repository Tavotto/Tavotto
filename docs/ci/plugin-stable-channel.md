# `plugin-stable` 发行通道：上线 / 回退手册（ADR 0043）

三个路径分清楚：

| 路径 | 是什么 | 谁写 |
|---|---|---|
| 源码 `codex-plugin/` | 插件源码（PR B 之后**不含**画布） | 开发者 |
| staging（CI 临时目录 / 本地 `build/plugin-stage`） | 一次构建组装出的完整插件 | `scripts/plugin_stage.py` |
| 发行分支 `plugin-stable` | 已验证完整插件的投影（`codex-plugin/` + 收据） | `scripts/plugin_publish.py`（CI） |
| 已装副本 `~/.codex/plugins/cache/tavotto/tavotto/<版本>/` | 用户机器上的那份 | Codex 客户端 |

## 0. 本地开发

```sh
python scripts/build_mcp_widget.py            # 产物落在 codex-plugin/mcp/widget/canvas.html（不入库）
python scripts/build_mcp_widget.py --check    # 0 一致 / 1 过期 / 2 还没构建
python codex-plugin/mcp/server.py --health    # widget.available 应为 true
```

想像用户一样装本地版本：`codex plugin marketplace add /path/to/tavotto/codex-plugin`（插件目录
里的 dev 市场 `tavotto-dev`，仓库根那份指向发行分支）+ `codex plugin add tavotto@tavotto-dev`。
改了 `web/src` 重跑第一条命令即可，不必发 stable。

组装一份完整插件看看：

```sh
python scripts/build_mcp_widget.py --out build/canvas.html
python scripts/plugin_stage.py stage --widget build/canvas.html --out build/plugin-stage \
    --source-sha "$(git rev-parse HEAD)" --allow-dirty
python scripts/plugin_stage.py verify build/plugin-stage --serve .venv/bin/python
```

## 1. 前置条件（PR A 落地之后、bootstrap 之前）

1. **分支保护**（已建：ruleset **22330299**「plugin-stable: machine maintained」，2026-09-05）。
   仓库级 ruleset **不能把 GitHub Actions 设为 bypass actor**（API 422：Integration 必须属于
   组织），所以 `update`（只允许 Actions 更新）那条做不到；现在只有 `deletion` +
   `non_fast_forward`：分支删不掉、历史改不掉，但有 push 权限的人仍能快进推送。
   发布器自己的判据（收据、树摘要重算、版本顺序）是第二道；仓库迁到组织之后再补
   `update` + Actions bypass。当时的请求体：

   ```sh
   cat > /tmp/plugin-stable-ruleset.json <<'JSON'
   {
     "name": "plugin-stable: machine maintained",
     "target": "branch",
     "enforcement": "active",
     "conditions": {"ref_name": {"include": ["refs/heads/plugin-stable"], "exclude": []}},
     "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
     "bypass_actors": []
   }
   JSON
   gh api repos/Tavotto/Tavotto/rulesets --jq '.[] | {id,name,target}'          # 先看现状
   gh api -X POST repos/Tavotto/Tavotto/rulesets --input /tmp/plugin-stable-ruleset.json
   ```
2. **环境**（可选但推荐）：Settings → Environments 建 `plugin-stable`，加 required reviewers；
   然后在 `release.yml` 的 `plugin_stable` job 与 `plugin-stable.yml` 加 `environment: plugin-stable`。
   没配也能跑（凭据仍是 `GITHUB_TOKEN`，只在那两个 job 上有 `contents: write`）。
   发布器在自己的临时仓库里 push，`actions/checkout` 留在 checkout 本地 config 的凭据对它不可见——
   两个 workflow 都在真推步骤前把 `GITHUB_TOKEN` 配成全局 `http.https://github.com/.extraheader`
   （与 `actions/checkout` 同一形态；演练不配）。首次真跑就是这里死在 `could not read Username`，
   读回报 not_landed（退出码 4），分支没建出来（run 33979476158）。
3. `release.yml` 的 `plugin_stable` job 已在 `publish=false` 的演练里对临时 bare 仓库跑过
   bootstrap / no-op / 拒绝 / rollback，并对真实远端只读 `plan`。看一次演练的 run 再进入下一步。

## 2. bootstrap（首次创建发行分支）

只用**已通过发行资格的正式版本**，不从开发 checkout 取文件。两种来源：

* **新版 Release**（0.14.0 起，`dist/` 里有 `codex-plugin-build.json`）：
  Actions → plugin-stable → `mode=bootstrap`, `release_tag=vX.Y.Z`，先不勾 `confirm` 看计划，
  再勾上执行。
* **旧版 Release**（v0.12.0 / v0.13.0 的 zip 没有随包清单）：同上，workflow 会自动走 legacy：
  包内字节原样保留，外部校验和取 GitHub 的 asset digest（`gh api releases/tags/v0.12.0`
  的 `assets[].digest`），收据 `plugin-release.json` 标 `legacy_bootstrap`。

本地等价命令（需要能推 `plugin-stable` 的凭据）：

```sh
gh release download v0.12.0 --pattern codex-plugin-0.12.0.zip --dir /tmp/rel
DIGEST=$(gh api repos/Tavotto/Tavotto/releases/tags/v0.12.0 \
  --jq '.assets[] | select(.name=="codex-plugin-0.12.0.zip") | .digest' | sed 's/^sha256://')
python scripts/plugin_publish.py bootstrap --legacy-zip /tmp/rel/codex-plugin-0.12.0.zip \
  --legacy-sha256 "$DIGEST" \
  --legacy-asset-url https://github.com/Tavotto/Tavotto/releases/download/v0.12.0/codex-plugin-0.12.0.zip \
  --release-tag v0.12.0                      # 不带 --yes = 演练；确认无误后加 --yes
python scripts/plugin_publish.py inspect     # 读回远端：tip、收据、树摘要
```

发布器会核对 PyPI 与 GitHub Release 上都有这个版本的引擎（`--engine-check none --reason …`
才能跳过，理由进收据）。

## 3. 隔离安装验收（切换入口之前）

在一台机器上，**不碰真实 `~/.codex`**：

```sh
export CODEX_HOME=/tmp/codex-home-test HOME=/tmp/home-test
mkdir -p $CODEX_HOME $HOME
codex plugin marketplace add Tavotto/Tavotto --ref plugin-stable --sparse .agents/plugins --sparse codex-plugin
codex plugin add tavotto@tavotto --json          # installedPath → cache/tavotto/tavotto/<版本>
python scripts/plugin_stage.py verify "$CODEX_HOME/plugins/cache/tavotto/tavotto/<版本>" --installed --serve python3
tavotto codex doctor --json                      # summary.canvas.complete == true
```

（这条用的是发行分支根那份 `local ./codex-plugin` 清单，验的是分支内容本身。）
真正的 `git-subdir` 入口在 PR B 落地之后用同样的步骤、去掉 `--ref` 再验一次。

2026-09-05 已在本机用 codex-cli 0.151.0 对**本地 bare 仓库**跑过：全新安装、旧 `local`
来源经 `marketplace upgrade` 换到发行分支（缓存里旧版本目录被客户端删除、启用新版本）、
再次 `plugin add` 显式升级、doctor 四问全部正确（tests/test_codex_real_client.py 在有
codex 的机器上重跑这一套）。

## 4. 切换安装来源（PR B）

PR B 落地 = `.agents/plugins/marketplace.json` 改为 `git-subdir → plugin-stable`、画布移出索引、
`.gitignore` 加 `codex-plugin/mcp/widget/canvas.html`、`brand.CODEX_SPARSE_PATHS` 只剩
`.agents/plugins`、conflict-domains 的 `mcp-widget` 去掉 `generated`、main 落地审计改查
「不许重新跟踪生成物」。**合并前置条件**：`plugin-stable` 存在且 `inspect` 读得到收据；
第 3 节的隔离安装验收过；`docs/ci/plugin-stable-channel.md` 的线上状态更新。

老用户什么都不用做也不会坏：他们的快照还是旧的 `local` 来源、画布还在快照里。想拿新版：
`codex plugin marketplace upgrade tavotto`（实测：刷新快照并按版本刷新插件缓存），Windows
再跑一次 `tavotto codex install`。`tavotto codex doctor` 的 `summary.channel` 会说出
`legacy-local` 并给这条命令。

## 5. 旧 PR 一次性处理

PR B 之后仍带着 `codex-plugin/mcp/widget/canvas.html` 改动的分支：

```sh
git fetch origin
git rebase origin/main          # 冲突只会在 canvas.html 上：保留「删除」那一侧
git rm --cached codex-plugin/mcp/widget/canvas.html 2>/dev/null || true
git status --short codex-plugin # 不该再列出 canvas.html
```

不替别人 force-push；把这三行发给分支的作者。此后前端 PR 不再需要重建并提交那份 HTML。

## 6. 队列并发调整（最后一步）

```sh
python scripts/ci/merge_queue_ruleset.py inspect
python scripts/ci/merge_queue_ruleset.py plan  --phase set-build-concurrency --max-entries-to-build 2
python scripts/ci/merge_queue_ruleset.py apply --phase set-build-concurrency --max-entries-to-build 2 --yes
```

前置条件由脚本自己核：默认分支上 `codex-plugin/mcp/widget/canvas.html` 不存在、marketplace
清单是 `git-subdir → plugin-stable`、`plugin-stable` 分支存在。只改 `max_entries_to_build`
这一个字段；`max_entries_to_merge=1`、`min_entries_to_merge=1`、ALLGREEN、required contexts、
无 bypass 原样保留；plan 记哈希，apply 前重读线上比对，并发漂移即拒绝。先开 2，不开 10。

真实队列的双候选演练：两个只改源码、各自碰 `web/src` 不同文件的小 PR 同时「Merge when
ready」，看队列把它们组成一组、`CI fast gate` 在组合提交上绿、两者依次合入。没有授权时不
造垃圾 PR；用两条真实的待合 PR 即可。

## 7. 失败恢复

| 情形 | 现象 | 做法 |
|---|---|---|
| Release 已公开、`plugin_stable` job 红 | 收据 artifact 里有 `reason` | 旧稳定插件原样在；修好原因后 Actions → plugin-stable → `promote`（同一 Release tag，`expected_remote_sha` = `inspect` 读到的 tip）。幂等：内容一致就 no-op |
| 推送退出码非零、退出 4 | 远端仍是旧 tip | 原样重跑同一条命令 |
| 退出 5 | 远端既不是旧 tip 也不是新提交 | 有人动过分支：`inspect` 看收据，人工判断后再 promote / rollback |
| 新版本坏了 | 用户反馈 | `rollback --to <上一个收据的 commit> --expected-remote-sha <现 tip> --authorized-by <你> --reason <为什么> --yes`：新提交、旧内容、历史保留 |
| 引擎没发出去就想推插件 | 发布器退 3「引擎还拿不到」 | 先发引擎；确有理由时 `--engine-check none --reason …` |

## 线上状态（改动时整段重写，不加行）

- 2026-09-05：**发行分支未创建**（PR A #289 已入合并队列，bootstrap 等它落地后经
  plugin-stable.yml 用 v0.12.0 执行）；marketplace 入口仍是 `local ./codex-plugin`（PR B #290 未合）；
  `plugin-stable` ruleset 已建（22330299：deletion + non_fast_forward，无 Actions bypass）；
  线上合并队列 `max_entries_to_build = 1`（读取值）。CodeQL alert #132 已按同族理由标为误报。
