# ADR 0043：源码与 Codex 插件发行解耦——机器维护的 `plugin-stable` 发行分支

日期：2026-09-05 · 状态：**Accepted**（分两个 PR 落地：A = 构建 / 验证 / 发布基础设施；
B = 切换安装来源、移除源码分支上的画布产物）

## 背景

Codex 内嵌画布 `codex-plugin/mcp/widget/canvas.html` 是 `web/src` 打出来的近 1 MiB 单文件。
ADR 0006 决定它**进版本库**：marketplace 清单（`.agents/plugins/marketplace.json`）用
`local ./codex-plugin` 把仓库本体当插件装，产物不在 checkout 里用户就装不到画布。

这条决定与 Merge Queue 的交互很坏：任何碰 `web/src` 的 PR 都带着一份重建过的它，两个互不
相关的前端 PR 前后脚排队就整组必撞、双双被踢（2026-08-29 一晚四次）。#211 试过把产物移出
版本库，作废理由正是上面那条：安装入口还指着源码 checkout。于是把 `max_entries_to_build`
从 2 调回 1，用吞吐换正确。

**要消除的是「互不相关的源码 PR 因同一编译产物而冲突」**，不是承诺真实源码冲突也能自动
解决，也不是删测试换速度。

## 决定

1. **源码分支只放源码。** `main` 与功能分支不再跟踪画布产物（PR B 落地时移出索引并
   忽略）。本地开发照旧 `python scripts/build_mcp_widget.py`，产物落在原位置，server 照原路
   加载；`--check` 仍分「一致 / 过期 / 还没构建」三档，给本地用。
2. **CI 从本次实际 checkout 构建并验证完整插件，不向源码分支回写。** `frontend` job
   真跑 vite → `scripts/plugin_stage.py stage`（`git ls-files` 的源码清单 + 显式构建物 +
   LICENSE，其余一律拒绝）→ 验证 → 确定性 zip → 作为 artifact 传给 `plugin-candidate` job，
   那里脱离源码树解包、逐条核对、真起 MCP server 走 stdio 读画布，并执行 `tests/
   test_plugin_candidate.py`（有产物时不许 skip）。两者都在 `CI fast gate` 的闭集里；
   PR 验的是合并提交，merge_group 验的是队列组合提交。候选只作验证，不发布。
3. **同仓库一条机器维护的发行分支 `plugin-stable`**：完整成品的投影——`codex-plugin/`
   （含画布与随包清单）、收据 `plugin-release.json`、`.gitattributes`（`* -text`，Windows
   检出字节与 zip 相同）、一份指向 `./codex-plugin` 的 marketplace 清单（旧客户端用
   `--ref plugin-stable` 的后路）、README、LICENSE。没有源码历史，不人工维护，不合回 main。
4. **marketplace 入口消费发行分支**（PR B）：

   ```json
   { "source": "git-subdir", "url": "https://github.com/Tavotto/Tavotto.git",
     "path": "./codex-plugin", "ref": "plugin-stable" }
   ```

   Codex 0.151.0 实测：`codex plugin add` 对它做 `git clone --filter=blob:none --sparse
   --no-checkout` + `sparse-checkout set --no-cone -- codex-plugin` + `checkout plugin-stable`，
   再拷进 `plugins/cache/tavotto/tavotto/<版本>/`；`codex plugin marketplace upgrade tavotto`
   刷新市场快照并按版本刷新插件缓存；旧的 `local` 来源用户升级后自动换到发行通道。
5. **ZIP 与发行分支来自同一份已验证 staging。** release.yml 的 `build` job（有 Node、固定
   发行 SHA、刚装了 wheel 的干净环境）造一次：构建画布 → 组装 → 验证（`--serve` 用发出去
   的 wheel 真起 server）→ `codex-plugin-<版本>.zip` + `codex-plugin.json` +
   `codex-plugin-build.json` 进 `dist/` 与产物清单；`validate_artifacts` 成对验证后挂 Release；
   `plugin_stable` job 在 Release 建好、PyPI 发完之后把**同一份** zip 投影到发行分支。
   `publish=false` 对临时 bare 仓库真实跑完 bootstrap / 幂等 / 拒绝 / 回退，并对真实远端
   只读 `plan`。
6. **三种身份分开**：`source_sha`（哪个 commit 造的，组装时与 HEAD 对拍，下游与 trust 解析
   出的 SHA 对拍）、`build_inputs_fingerprint`（参与编译的输入指纹：`web/src/**` 全部文件
   + 锁文件 + tsconfig + 构建脚本 + 规范 JSON + 字形覆盖表）、`content_digest`（成品内容摘要：
   相对路径 + git 模式 + 字节；构建时间 / run id 在 `audit` 里，不参与身份；清单不含自身哈希）。
7. **发布器 `scripts/plugin_publish.py`**：默认演练；目标分支固定匹配 `plugin-*`（`main`
   结构上写不到）；只接受通过验证、`source_sha` 可达远端 `main` 的 staging；一次提交整套
   内容；同版本同内容 no-op、同版本不同内容拒绝、旧版本晚到拒绝；`promote` / `rollback`
   必须带 `--expected-remote-sha`，推送用 `--force-with-lease=<ref>:<读现状那一刻的值>`；
   不重写历史（新提交以当前发行提交为父）；推送响应丢失时读回远端判 landed / not_landed /
   moved；推送后 fetch 回来重算树摘要与收据；回退是显式操作（授权人 + 理由 + 新提交）；
   发布器不执行插件包里的任何代码；引擎可获得性（GitHub Release / PyPI）只读查询。
8. **安装器 / 诊断（`tavotto codex install / doctor`）**回答四个问题：装的是哪份插件
   （Codex 报的版本 → `cache/<marketplace>/<plugin>/<版本>`；git 来源时 `plugin list` 的
   PATH 列是来源描述不是路径，实测）、来自哪里（marketplace 来源 + 快照里的条目 →
   `stable` / `legacy-local` / `custom` / `unknown`）、画布完整吗（按随包清单核对，允许两份
   启动清单一起钉 command；旧发行件只验画布本身）、引擎版本满足吗
   （`engine_too_old`）。「不知道」是独立一档（`marketplace_state_unknown` /
   `plugin_state_unknown`），来源客户端不认识另有一档（`plugin_source_unsupported`），定位
   歧义不按版本号猜（`plugin_install_ambiguous`）。doctor 只读；install 不升级健康插件。
9. **队列并发**：以上落地并验证后，把线上 ruleset 的 `max_entries_to_build` 从 1 调到 2
   （`scripts/ci/merge_queue_ruleset.py` 新增 `set-build-concurrency` 阶段：只改这一个字段，
   前置条件 = 源码分支已不跟踪画布、marketplace 已切到发行分支、`plugin-stable` 存在）。
   `max_entries_to_merge` / `min_entries_to_merge` / ALLGREEN / required contexts 不动。

## 明确不做

- 不加 `plugin-dev` 通道、新仓库、制品平台、运行时下载器、远程缓存；不做「每会话自动
  marketplace add」；不用 `pull_request_target` / 带写 token 的 `workflow_run`。
- 不在 main 保留任何形式的生成物副本（长期兼容目录也不行）。
- 不动 `resources.d.ts`、golden 向量等其它生成文件；官网 `/try` 契约不变。

## 代价与边界

- 发行分支的推进要求匹配版本的引擎已经经用户正常渠道拿得到：Release 上有、PyPI 上有。
  GitHub Release、PyPI、Git 分支不是一个原子事务——Release 已公开而分支推进失败时，如实记
  「Release 已发布、marketplace 通道尚未推进」，旧稳定插件原样保留，用同一份产物幂等重试；
  绝不删公开版本伪装回滚。
- `git-subdir` 来源需要认识它的 Codex 客户端（0.151.0 实测支持）。更老的客户端有
  `codex plugin marketplace add Tavotto/Tavotto --ref plugin-stable --sparse .agents/plugins --sparse codex-plugin`
  这条后路（发行分支根带一份 `local ./codex-plugin` 的市场清单）。
- 本地开发者要看到自己的改动：`python scripts/build_mcp_widget.py` 一次，产物落在原位；
  或把指向工作副本的本地 marketplace 装进 Codex。不必发 stable。

## 修订 ADR 0006

ADR 0006「产物进 git、`--check` 在 CI 看着」那条**由本 ADR 取代**：当时的判断没错，错的是
前提——那时安装入口只能指向源码 checkout。现在安装入口指向发行分支，产物由 CI 从固定源码
状态构建、验证、发布。
