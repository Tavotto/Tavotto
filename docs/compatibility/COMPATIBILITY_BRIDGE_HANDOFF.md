# Compatibility Bridge Session Handoff

> 每个 Session 结束前必须更新本文件。

## 当前状态

- 日期：2026-08-26
- 当前 branch：`compat/bridge-session05-asset-library`（worktree
  `.claude/worktrees/compat-bridge-session01`，stacked 在
  `compat/bridge-session04-runtime-asset` → `…session03-script-probe` →
  `…session02-execution-spec` → `…session01-audit` 之上）
- 基于 commit：Session 5 落库提交 `f3b09ab`
- 本 Session Prompt：`07_SESSION_06_TAVOTTO_OPEN_AND_PRODUCT_ROUTES.md`
- 目标 PR：**PR 1 收口**（本 Session 交付 `tavotto open script.py` 自动
  safe probe、单/多 Figure 产品交接、CompatBench 产品路由、完整
  保存/重开/重放/预检/导出 E2E）
- **PR 1（#127）已合入 main：2026-08-26 15:16Z，squash `6aeca9e`**
  （排在 v0.11.0 tag 与 #129 之后）。合并队列走了 4 轮（各轮定性见下方
  「合并队列轮 1–3」小节；轮 4 = 修复 nav toggle 盲点击后，先打
  `full-ci` 标签在 PR SHA 上取得 windows-exe-smoke / macos-app-smoke
  全绿证据再入队，一次通过）。16 条 CompatBench case 已升 full_support
  随 PR 落库。
- **合并后教训（进入约束节）**：merge_group-only 套件（windows-exe-smoke
  的 Playwright E2E、backend-platforms）在 PR CI 上不跑——大改动入队前
  用 `full-ci` 标签在 PR SHA 上先跑一轮，别拿整轮队列资格试错。

## 本轮唯一目标

`tavotto open script.py` 在静态发现失败时安全 probe；单/多 Figure 正确
交接到桌面或浏览器；CompatBench 真正测产品路由（不再直接调内部 probe）；
保存/重开/重放/预检/导出 E2E 完整；收口 PR 1。**不做** `tavotto run`、
native profile、generic Artist fallback、source hints、Copy as Python、
新 Artist family、无关 UI 重构。

## 已完成

- [x] **`tavotto open script.py` 自动 safe probe**
  （`handoff.resolve_script_route`）：显式给出 `.py` = 运行意图（总纲原则
  5）。顺序：静态发现/注册表的每张图都已有路由（磁盘原件或 runtime
  cache）→ 复用零执行；否则 probe——本机实例在 `--port` 上跑就**委托**
  （`POST /api/registry/probe`，同一个 `_PROBES` 并发闸，409 →
  `probe_in_progress`），否则本进程 `probe_and_register` + 物化 cache
  （只复制热 worker 的预览 SVG，绝不二次执行），返回前 `pool.invalidate`
  关净 worker（不留 orphan）。交接目标进程读注册表 + cache，零重跑。
- [x] **CLI 参数**：`--no-probe`（关掉探测）、`--stem <名字>`（多图显式
  选，只对 `.py` 有效）；`--json` 单行 UTF-8、成功失败都机器可读、
  stdout/stderr 纪律与既有 open/doctor 一致。稳定错误码（全表在
  `docs/handoff-protocol.md`）：`script_no_figure` / `script_probe_failed` /
  `multiple_figures_found`（`--no-launch` 的机器调用必须显式选；extra 带
  `figures`）/ `invalid_stem`（extra 带 `stems`）/ `runtime_asset_failed` /
  `native_run_required`（missing_dependency 的映射，extra 带 `module` +
  原始 `probe_code`）/ `probe_in_progress`（retryable）；其余 probe 码
  原样透传。成功 payload 带 `probe{performed,via,entry,dropped_figures}` /
  `figures[{stem,asset_id,artifact,cached}]` / `pick`。
- [x] **单/多 Figure 交接**：单图直达 stem（frontend `applyOpenRequest`
  找不到磁盘面板时按 stem 查 runtime 清单，有描述符 `addRuntimePanel`，
  没有描述符如实引导不造假面板）。多图**不静默选第一张**：桌面契约扩展
  `--open <目录> [--stem <s> | --pick-script <脚本>]`（`desktop_argv` ↔
  `parse_open_args` 双侧单测同步；macOS `open -na … --args` 复用
  desktop_argv 切片不再手拼），浏览器 `?pick=`、browser-new `--open-pick`，
  三条路汇进前端 `FigurePickerDialog`（每张可见、各自可加、磁盘图走
  addPanel、runtime 走描述符、没预览的不渲染假按钮）。
- [x] **CompatBench 产品路由**：manifest case 可声明 `product_routes`
  （闭集 desktop_project / cli_open / safe_probe / browser_playground /
  native_run；`native_run=true` 被 schema 拒绝——第一阶段只许
  not_implemented，不伪装 pass）。runner 走真实产品面：safe_probe =
  `POST /api/registry/probe`；desktop_project = `GET /api/registry` +
  `GET /api/runtime/assets`（条目带物化描述符，零执行）；cli_open = 真
  spawn `python -m tavotto open --json --no-launch --port 0`（多图脚本验
  完整契约：裸调必须 `multiple_figures_found` 显式拒绝 + `--stem` 选中；
  「多图」判据是 expected_figures，不是组内 case 数）。声明为 true 的路由
  失败 = product_bug（stage `route:<名>`，报告带 code/reason/follow_up）。
  guard `tests/test_compat_product_routes.py`：改回内部 probe 当场红
  （app 端点必物化 cache、CLI 输出必带 protocol 字段）。
- [x] **16 条「入口不可达」case 升级 full_support**（show-only 家族 12 条
  + 静态发现缺口家族 4 条；art_contour/contourf 的 partial 是 artist
  识别问题，不动）。基线在 **target bundled**（3.13 venv + mpl 3.11.1 全
  钉版）上重生成并逐条读过 diff：恰好这 16 条 partial→full，其余零变化，
  `generated_for` 指纹不变。全量：full 134 / partial 2 / by_design 7 /
  env 6 / **product_bug 0**；路由 16/16 全通。
  `test_compat_manifest.py` 的结构性守卫同步升级：no-savefig case 只有
  声明并验证三条产品路由才许 full_support（新 case 不许搭便车）。
- [x] **完整 E2E**（`web/e2e/asset-library.spec.ts`，共 4 条全绿）：
  * 黄金路径 + 窄视口（Session 5 原有 2 条）；
  * **完整链**：show-only 项目 → 素材库运行发现 → 加画布 → 编辑标题字号
    + 曲线线宽 → undo/redo → 磁盘自动保存（读 autosave JSON 验 override
    落盘）→ **关闭 App** → 重开（同数据目录；localStorage 恢复索引经
    addInitScript 带过去，等价真实浏览器重启）→ lazy rehydrate（cache
    占位零执行）→ 进入编辑重放（字号 12 恢复）→ 出版预检（导出对话框）
    → `/api/export` PDF+PNG（同一次合成；runtime 面板由当次 live worker
    按 override 渲染）；
  * **多 Figure**：真实 probe 两张 → `?pick=` 选择器两张全可见 → 选第二
    张 → autosave 里面板 fileId = 第二张的 asset id（stem/id 不串）。
  * **CLI E2E**（pytest `tests/test_open_script_route.py`，真 worker）：
    open_target 本地 probe 一次 → 项目目录零写入（safe 隔离）→ CLI 池
    清零（无 orphan）→ Flask app 打开项目列清单/取预览全程只读、cache
    mtime 不变（execution-count 纪律，反证 #6 的看护）。
- [x] **测试**：后端新增 `tests/test_open_script_route.py` 23 项（fake
  probe 单元 + 契约 + 真执行）、`tests/test_compat_product_routes.py` 2 项
  guard；`tests/test_handoff.py` / `test_desktop_launch.py` 原样全绿；
  src-tauri `cargo test` 14 项（含 `--pick-script` 契约 3 项新增）；前端
  vitest 88 文件 957 项（openRequest +5、FigurePickerDialog +3）；
  `pnpm build` / `pnpm i18n:check` 绿；全量 pytest 绿；smoke_app 绿。
- [x] 两个受管产物重建且 `--check` 一致：canvas.html 指纹
  `dad5cb6bc0df93a5`、playground 指纹 `95afb3e65de4ebe6`。
  PR 1 合并后 re-sync 网站仓库。
- [x] 文档：`docs/handoff-protocol.md`（safe probe 小节 + 新码表）、
  `docs/adr/0005` 增补（契约扩展）、`docs/ci/matplotlib-compatibility.md`
  §6b（产品路由）、`src/tavotto/AGENTS.md` / `web/AGENTS.md`、
  codex-plugin `references/desktop-handoff.md`（新码的分诊话术）。
  ADR 0013 仍 Accepted、0014 仍 Proposed（native 属 PR 2）。

## PR #127 评审轮 1（Codex，2026-08-26）

三条全部核实成立并已修复（各带看护测试 + 手工反证一次红）：

- **P1 归属按捕获来源判，不按文件名巧合**：pyplot 捕获从来没有原件
  （figcapture 工厂本来就钉死了这个语义——消费点漏了这一维）。新判据
  `runtimeasset.is_pyplot_capture`，三个消费点同步修：`list_assets`
  （旧同名文件不再把 runtime 素材顶掉）、`handoff._script_figures`
  （交接路由不再指向陈旧文件）、前端 `applyOpenRequest`（stem 碰撞时
  pyplot 捕获优先）。savefig 来源 + 磁盘原件照旧归 FileAsset 不双列。
- **P1 runtimeAssetStore 项目代际**：模块级 in-flight（清单 + 逐面板
  status）活得比 Zustand reset 长——`clear()` 现在换 epoch + 清 inflight，
  A 项目的响应绝不落进 B（与 scriptRunStore 同一条纪律）。
- **P2 scriptLibraryStore 同一条代际纪律**（顺手修掉，不转 issue）。

## PR #127 合并队列轮 1（CI #444，2026-08-26）

v0.11.0 tag 落地后（用户经 tavotto-0a 入队）queue run #444 的 Windows 腿
（backend-platforms windows-latest 3.13）红了两条——`backend-platforms`
在 PR 分支 CI 上是 SKIPPED（#120 分层，完整跨平台只在队列跑），所以
Session 2 的这两处 Windows 事实第一次被执行到：

- **`test_execspec` argv golden 把 POSIX 拼接写死**：`worker_argv` 在
  Windows 上出 `\proj\fig.py` 与重构前 pool.py 的
  `str(Path(figures_dir) / script_name)` 逐字节一致（查过
  session01-audit 的旧代码），**产品语义没漂移，是 golden 自己平台相关**。
  修测试：`--script` 期望改为 `str(Path("/proj") / "fig.py")`，注释钉明
  Windows 反斜杠是被冻结的旧语义。macOS 上无法反证（POSIX 拼接恒同），
  红证据即 CI #444 本身。
- **`source_fingerprint` 行尾分叉**：worker 从磁盘 `read_bytes`（Windows
  文本模式检出 = CRLF），browser 拿编辑器 `str`（LF）——同一份逻辑源码
  两个指纹，描述符对拍红。修在唯一出处：`figcapture.source_fingerprint`
  内 CRLF/CR→LF 归一后再哈希（LF 输入指纹不变，POSIX 零漂移；消费面全是
  不透明比对）。新增平台无关回归：`test_line_endings_do_not_split_the_
  fingerprint` + parity 的 `test_crlf_checkout_matches_the_editor_source`
  （显式 `write_bytes` CRLF，任何平台可红）；负向反证：抽掉归一化两条
  当场红，还原后绿。
- 同轮 merge origin/main（v0.11.0 bump + #125 旧名注册表看护，无冲突；
  合并态 test_handoff 56 项预演过）。playground 产物因 figcapture 变更
  重建（指纹 `97cfbe416d563733`）；canvas.html 不受影响
  （`2e3df71339c204d3`）。
- **真机取证口径（重要）**：v0.11.0 正式产物**不含** #127 代码，不能用它
  取桥接功能的真机证据；有效证据只能来自 PR 分支（或合并后 main）构建的
  候选产物。

## PR #127 合并队列轮 2（CI #447，2026-08-26）

backend-platforms 全绿（轮 1 两条修复生效）。windows-exe-smoke 红 3 条，
全部是**测试缺陷**，产品行为均正确；这套 Playwright E2E 只在 merge_group
跑（issue #30 的覆盖缺口），PR 分支 CI 从未执行过它们：

- **asset-library 两条（merge 交互缺陷，平台无关）**：#109（8-25 进 main）
  给 NumberField 加了同值提交 no-op 拦截（Tab 路过不产生假历史，产品行为
  正确）；而 e2e 把标题字号「编辑」成 12——恰好等于初值（axes.titlesize=
  large = 12.0pt，本机 manifest 实测），fill+Enter 从 `d78e5ea` merge 起
  变成静默 no-op，「1 项已修改」永不出现。**macOS 本机红证复现**（同一行
  110 同形状失败），修为 fill('13')（重放断言联动 12→13）后 chromium
  全绿。Session 6 跑绿是在 merge 之前——**merge main 之后必须重跑 e2e**，
  这条教训记进下面的约束。
- **golden-paths 一条（选择器歧义）**：素材库脚本区（Session 5）让
  `render_map.py` 全页三处合法出现；断言收窄到注册表对话框 +
  `.first()`（对话框内候选区/列表区两处也都合法）。webkit + chromium
  本机验证过。

本机验证：`asset-library + golden-paths` chromium 12 passed；golden-paths
webkit 1 passed（worktree 首跑需 `pnpm exec playwright install`）。

## PR #127 合并队列轮 3（CI #453，2026-08-26）

轮 2 的三条修成两条；剩「完整链」换了形状：重开后 `nav 图内元素` 点击处
900s 挂死（树项 resolve 后 141ms detach、之后 860s 再未出现；终态截图
左栏收起、右栏「2 项已修改」——重放本身是好的）。根因（本机反证复现）：

- rail 按钮是 **toggle**（`uiStore.setLeftTab`：对已激活 tab 再点一次 =
  收起）；`enterElementEdit` 在 wide 布局会自动切左栏到元素树；e2e 随后的
  盲点击在「已自动切过去」的时序下把面板关掉，`usePresence` 的 ~150ms
  收起动画让树在窗口里仍可 resolve——click 一开始就 detach，面板永久
  关闭。CI/本机差异只是「点击时 leftTab 是否已是 elements」的时序差。
- 产品 toggle 行为正常，测试盲点击是缺陷。修法：`fixtures.openElementsTab`
  幂等 helper（按 nav 按钮 `aria-expanded` 判态，不在才点，点完等坐实），
  替换全部 4 处盲点击（asset-library ×3 + inspector-redesign ×1）。
- 反证：临时 spec 证明「已激活时盲点击 → treeitem count 0」成立、helper
  能救回（跑过即删）。本机三 spec chromium 15 passed。
- 下轮入队前先打 **full-ci 标签**在 PR SHA 上跑 windows-exe-smoke 取证，
  不再拿整轮队列资格试错。

CodeQL 报 9 条新告警（1 critical + 8 high）：逐条核实全部有结构性防线，
必需的「CodeQL gate」本来就是绿的（红的是非必需的原生 annotation
check）。#95（pool.py 命令行，critical）已带理由 dismiss（won't fix：
执行用户脚本是产品语义，argv 列表无 shell + safe 沙盒）；**#96–103 待
用户在 Security 页 dismiss**（会话权限拦截了批量操作），理由如下：

- #96–98（app.py probe 端点，py/path-injection）→ false positive：
  判据在 realpath 之后（resolve + is_relative_to(项目根)），回溯/
  symlink/项目外/非 .py 各有稳定 code + 用例（test_script_probe 路径
  边界组）；CodeQL 不识别 is_relative_to 这个 sanitizer。
- #99（discover.py probe_entry_candidates 读脚本）→ false positive：
  path 来自项目内 walk 或已过 probe 端点校验。
- #100–103（runtimeasset.py cache 读写）→ false positive：cache 目录名
  是 sha256(项目|asset_id) 的 hex slug（结构上无路径分隔符）；
  script_sha256 只读取比对、script 来自注册表且 stale_status 兜底路径
  有 fail-closed 的 id 重算校验。

## 未完成 / 待用户拍板

- [ ] **真机最终产物证据（§六，顺序已调整）**：合并前未能产出（本机产不了
  签名候选产物）；经用户拍板改为**合并后**用新 main（≥`6aeca9e`）构建
  签名候选产物在真机/lab runner 补齐。注意：v0.11.0 正式产物**不含**
  #127 代码，不能用于桥接功能取证。CI 侧的部分替代证据：queue 轮 4 +
  full-ci 的 windows-exe-smoke（Playwright × 打包 EXE 服务面）与
  macos-app-smoke 全绿——但 WebView2/WKWebView 壳内交互仍需真机。
- [ ] **真实用户复测清单（Session 7 前置）**：PR 1 合并后请真实用户
  （issue #83 的外部科研用户等）重测此前失败的项目。仍失败且归因于
  原 Python/Conda 环境、cwd、argv、env、本地模块、matplotlibrc/style/
  font、safe 写入隔离的，每个做脱敏最小 fixture——这是 Session 7
  （native execution 设计）的入场券；没有这些证据不开工。
- [x] PR #127 merge（2026-08-26 squash `6aeca9e`，队列轮 4）。
- [ ] 网站 playground re-sync（在合并后 main 上
  `python scripts/build_browser_playground.py` → 网站仓库
  `pnpm sync-playground`；指纹 `97cfbe416d563733`，含 figcapture
  行尾归一）。

## 本轮关键决策

### 决策 1：probe 委托优先，本地兜底

- 实例在跑（浏览器模式端口可达）→ 试运行**委托给它**：同一个 `_PROBES`
  并发闸真实生效（素材库与 CLI 并发同脚本 = 409）、热会话与 cache 留在
  实例手里、交接零重跑。桌面 sidecar 绑动态端口够不着——那时本地 probe，
  注册表 + materialized cache 落盘即共享状态（「复用同一登记表」）。
- 老版本实例的响应形状不归我们管：`_probe_error` 对非 dict error 防御性
  包装（实测本机 5089 上挂着一个 magplot 时代实例，错误是一句字符串）。

### 决策 2：多 Figure 的选择信息走契约扩展（`--pick-script` / `?pick=`）

- stem 与 pick 在 Target 上互斥；壳只透传不选择，Figure 选择器唯一实现
  在前端（`FigurePickerDialog`，条目从 assetStore + runtimeAssetStore
  现算——快照存 store 会陈旧）。`--no-launch` 没有界面接选择器，机器
  调用必须 `--stem`，否则 `multiple_figures_found` + figures 列表。
- macOS `open -na … --args` 之后的 argv 改为**复用 `desktop_argv()[1:]`**
  ——同一契约两处手拼迟早漂移。

### 决策 3：产品路由失败与引擎阶段失败同罪

- `product_routes` 声明 true 的路由失败直接 product_bug（stage
  `route:<名>`）。「引擎全绿、用户够不着」正是 show-only 被记两个月
  partial 的原因；路由失败不红，这套声明就只是装饰。
- runner 绝不直接调 `engine_probe` 代表产品成功；guard 钉**只有产品面才
  有的副作用**（cache 物化 / protocol 字段），改回内部调用当场红。
- 升级 full_support 的门票就是路由声明 + 验证（`test_compat_manifest` 的
  结构性守卫从「一律不许」升级成「验过才许」——守卫的精神保留，判据换
  成新的现实）。

### 决策 4：CompatBench 运行时数据隔离

- bench 进程 `TAVOTTO_DATA_DIR`/`CONFIG_DIR` setdefault 进本轮 scratch：
  产品路由会经真实端点物化 cache、写事件，benchmark 的临时项目不该在
  用户数据目录留派生物。cli_open 一律 `--port 0`：机器上碰巧开着的
  Tavotto 实例绝不能被 benchmark 委托执行。

## 架构与数据契约

### 新增/修改接口

```text
engine/handoff.py
  Target(project, stem, pick=None)            # pick 与 stem 互斥
  resolve_script_route(project, script, *, stem_arg, no_probe, port, …)
  _remote_probe / _local_probe / _probe_error / _script_figures
  desktop_argv(): … [--stem s | --pick-script script]
  browser_url(): ?open= | ?pick=
  open_target(raw, *, stem=None, no_probe=False, …)
  cli(): + --no-probe / --stem
src/tavotto/app.py
  main(): + --open-pick（browser-new 的 pick 通道，landing 复用 browser_url）
src-tauri/src/main.rs
  OpenRequest{project, stem, pick} + parse_open_args(--pick-script)
  landing URL: ?pick=；tavotto:open 事件带 pick

web/src/lib/openRequest.ts   OpenRequest.pick；?pick= 读取；runtime 素材
                             按 stem 兜底定位（loadAssets 只读）
web/src/store/figurePickerStore.ts（新）
web/src/components/FigurePickerDialog.tsx（新）
web/src/lib/desktop.ts       DesktopOpenPayload.pick

scripts/ci/compat_corpus.py  PRODUCT_ROUTES / ROUTE_EXPECTATIONS + 校验
scripts/ci/compat_matrix.py  route_probe_via_app / route_desktop_project /
                             route_cli_open / stage_product_routes；
                             classify 吃路由失败；报告 product_routes 节
```

### 稳定错误码（本轮新增，全表在 docs/handoff-protocol.md）

```text
script_no_figure / script_probe_failed / multiple_figures_found /
invalid_stem / runtime_asset_failed / native_run_required /
probe_in_progress（CLI 面；probe 其余码原样透传）
```

worker/browser 协议**零改动**；文档 schema 零改动；注册表格式零改动；
`/api/*` 只加了 `--open-pick` 启动参数，端点零新增。

## 修改文件

| 文件 | 修改原因 | 是否有测试 |
|---|---|---|
| src/tavotto/engine/handoff.py | 脚本路由 + pick 契约 + probe 委托 | test_open_script_route 23 项 + test_handoff 原样绿 |
| src/tavotto/app.py | --open-pick | smoke_app + landing 复用 browser_url |
| src-tauri/src/main.rs | --pick-script 契约 + ?pick= | cargo test 14（+3） |
| scripts/ci/compat_corpus.py | product_routes schema | load_manifest 全量校验 + test_compat_manifest |
| scripts/ci/compat_matrix.py | 产品路由 runner + 分类 + 报告 + 数据隔离 | test_compat_product_routes + --all 全绿 |
| tests/compat/manifest.json | 16 条声明路由并升级 full_support | schema 校验 + 守卫 |
| tests/compat/baseline.json | bundled target 重生成（逐条读过） | 门禁 diff |
| tests/test_compat_manifest.py | 守卫判据升级（验过路由才许 full） | 自身 |
| tests/test_open_script_route.py（新） | Session 6 CLI 面 | 23 项 |
| tests/test_compat_product_routes.py（新） | 反证 #2 的 guard | 2 项 |
| web/src/lib/openRequest.ts(+test) | pick + runtime 兜底 | vitest +5 |
| web/src/store/figurePickerStore.ts（新） | 选择器状态 | 经 Dialog 测试 |
| web/src/components/FigurePickerDialog.tsx(+test)（新） | Figure 选择器 | vitest 3 |
| web/src/App.tsx / lib/desktop.ts | pick 贯通 | 间接 |
| web/src/i18n/locales/*/project.json | figurePicker/runtimeNeedsRun 双语 | i18n:check |
| web/e2e/asset-library.spec.ts | 完整链 + 多 Figure e2e | e2e 4 条 |
| web/e2e/fixtures.ts | freePort 导出 | — |
| docs/handoff-protocol.md 等五份文档 | 契约与路由记录 | 文档 |
| codex-plugin/…/desktop-handoff.md | 新码分诊话术 | 文档 |

## 实际运行的测试

```bash
# worktree 内、PYTHONPATH=src、主仓 .venv 解释器
PYTHONPATH=src …/python -m pytest tests/test_open_script_route.py \
    tests/test_handoff.py tests/test_desktop_launch.py     # 91 passed
PYTHONPATH=src …/python -m pytest tests/test_compat_product_routes.py  # 2 passed
PYTHONPATH=src …/python -m pytest -q                       # 全量绿
cd src-tauri && cargo test                                 # 14 passed（需 dist/Tavotto 占位目录）
cd web && pnpm test                                        # 88 文件 957 项
cd web && pnpm build && pnpm i18n:check
python scripts/build_mcp_widget.py && … --check            # dad5cb6bc0df93a5
python scripts/build_browser_playground.py && … --check    # 95afb3e65de4ebe6
python scripts/smoke_app.py --python .venv/bin/python      # 通过
# CompatBench：bundled target 基线重生成（3.13 venv 按 runtime-lock 钉版）
python scripts/ci/compat_matrix.py --all --update-baseline \
    --target bundled --python <钉版 venv>/bin/python       # 134 full / 0 bug / 路由 16/16
python scripts/ci/compat_matrix.py --smoke --gate pr       # 通过
# e2e（先 python scripts/build_frontend.py；worktree 跑法同 Session 5）
cd web && TAVOTTO_PYTHON=… PYTHONPATH=<worktree>/src \
    pnpm playwright test e2e/asset-library.spec.ts         # 4 passed
    pnpm playwright test e2e/playground.spec.ts e2e/mcp-canvas.spec.ts
```

（坑复述：① 本机 5089 上可能挂着旧实例——涉及 `_remote_probe` 的测试
必须 monkeypatch 掉委托，CompatBench 的 cli_open 一律 `--port 0`；
② e2e 里「关闭再重开」不要钉同一端口（TIME_WAIT 会让 resolve_port 换
端口），恢复索引用 addInitScript 跨 origin 带过去；③ `#` 在 URL 查询里
是 fragment，runtime asset id 进 URL 必须编码；④ 基线必须在 target
bundled 的钉版环境上重生成——在 current 上重生成会把 seaborn 六条写成
execute:false，属于环境倒退不是产品事实。）

## 负向反证（本轮六条，全部先红后还原）

| # | 变异 | 判据测试 | 结果 |
|---|---|---|---|
| 1 | resolve_script_route 不再自动 probe（探测分支钉 False） | `test_open_script_route`（14 条连锁红，含 CLI show-only E2E） | **红**（还原后绿） |
| 2 | CompatBench safe_probe 路由改回内部 `probe_and_register` | `test_compat_product_routes::test_safe_probe_route…`（cache 未物化） | **红**（还原后绿） |
| 3 | 多 Figure 静默选第一张（pick 分支改 stems[0]） | `test_open_script_route` 的 multi/picker 三条 | **红**（还原后绿） |
| 4 | useEngineSync runtime 分支恒 false（重开只显示旧 cache 不重放） | vitest `useEngineSync.test.ts` runtime 门 2 条 | **红**（还原后绿） |
| 5 | `_resolve_panel_source` runtime 分支改拿 cache 文件交差 | `test_runtime_asset::test_export_uses_the_live_worker_not_the_cache` | **红**（还原后绿） |
| 6 | `GET /api/runtime/assets` 触发 build（交接重新执行脚本） | `test_open_script_route::…executes_once…` | 首轮**没红**——「池清零」被 close_project 洗掉；哨兵改成项目外执行计数文件后**红**（还原后绿）。检测洞已修进测试 |

## 真机/产品证据

- macOS（arm64，开发机）：e2e 真实链路 4 条（真 Flask + 真 matplotlib
  worker + 真浏览器），含关闭重开与导出双格式。CLI 面真执行（pytest）。
- **Windows WebView2 / macOS WKWebView 最终候选产物证据未产出**（本机
  产不了签名候选产物；lab runner 待用户启动）。原计划合并前补齐（§六）；
  经用户拍板改为**合并后跟进**（见「未完成」首条）：用 ≥`6aeca9e` 的
  main 构建候选产物取证。CI 侧已有部分替代证据（queue 轮 4 + full-ci 的
  windows-exe-smoke / macos-app-smoke 全绿），壳内交互仍需真机。

## 已知失败与限制

| 问题 | Stage/Route | 严重度 | 是否本轮 | 后续 |
|---|---|---|---|---|
| 真机最终产物证据缺失 | 全路由 × desktop | 高（**合并后跟进**，经用户拍板由合并阻断改期；发版声明前补齐） | 是 | lab runner / 用户真机（main ≥`6aeca9e` 构建候选产物） |
| 桌面 sidecar 动态端口 → CLI 探测委托够不着在跑的桌面实例（本地 probe 兜底，registry+cache 仍共享） | cli_open × desktop | 低（记录在案） | 是 | 不修（单实例转发语义不变） |
| probe 仍同步阻塞（CLI 无进度输出，只等结果） | cli_open | 低 | 是 | SSE 进度流条目沿用 |
| `browser_playground` 路由平时 not_run（只有 --browser 腿跑） | compatbench | 低（如实记账） | 是 | nightly browser 腿覆盖 |
| runtime 卡片在「来源」筛选生效时整体隐藏 | asset_model | 低 | 否（S5 记录） | 不修 |

## 不得被下一 Session 破坏的约束

- Session 2–5 的全部约束仍然有效（runtime id 不透明、打开绝不执行、
  cache 是派生物、writeback 拒绝在后端、lazy 门、取消端到端、`_PROBES`
  并发闸、`GET /api/runtime/assets` 零执行、scriptRunStore 代际纪律、
  不渲染假 native 入口、素材库两区是普通路径唯一入口）。
- **`tavotto open script.py` 的执行次数纪律**：probe 一次；交接目标进程
  零重跑；CLI 退出前池清零（execution-count 用例看护）。
- **多 Figure 绝不静默选第一张**：CLI（multiple_figures_found /
  --stem）、pick 契约、FigurePickerDialog 三层都有用例看护。
- **契约同源对新增一对**：`desktop_argv()` ↔ `parse_open_args()` 现在含
  `--pick-script`；macOS open -na 复用 desktop_argv 切片——别再手拼。
- **CompatBench 产品路由不得旁路**：safe_probe/desktop_project 走真实
  端点、cli_open 真 spawn + `--port 0`；no-savefig case 升 full_support
  的门票是三条路由声明 + 验证（结构性守卫看护）。
- **基线只在 target bundled 的钉版环境上重生成**，并逐条读 diff。
- **merge main 之后必须重跑 e2e**（asset-library / golden-paths 至少各一
  遍）：windows-exe-smoke 的 Playwright 套件只在 merge_group 跑，PR CI 绿
  不代表它绿；队列轮 2 的三条红全是 merge 交互/选择器歧义这类「两侧各自
  正确、合起来才红」的形状。

## 下一 Session 唯一目标

> 只设计 native execution 契约与安全边界，写 ADR（0014 定稿）和
> contract tests，**不直接实现全部 `tavotto run`**。

**入场券（先于一切）**：真实用户复测清单（见「未完成」第二条）已产出
脱敏最小 fixture——Session 7 prompt 的前置条件原文是「PR 1 已合并 +
真实用户重测后仍有项目因环境/cwd/argv/env/本地模块/matplotlibrc/safe
隔离失败」。**没有这些证据不开工**（2026-08-26 已因此叫停过一次，
用户拍板顺序：先合并链，后复测，再 Session 7）。

## 下一 Session 首先阅读

```text
AGENTS.md / CLAUDE.md（src/tavotto/AGENTS.md 的「tavotto open 自动 safe
  probe」一节 + 外部交接一节）
docs/compatibility/COMPATIBILITY_BRIDGE_MASTER_PLAN.md
docs/compatibility/COMPATIBILITY_BRIDGE_HANDOFF.md（本文件）
docs/adr/0014-safe-native-execution-profiles.md（Proposed 草案，本轮定稿）
docs/handoff-protocol.md（native_run_required 的对外承诺口径）
src/tavotto/engine/execspec.py（profile 字段已预留 safe|native）
scripts/ci/compat_matrix.py（native_run 路由从 not_implemented 升级的位置）
```

注意：merge SHA 与复测清单已登记（本 PR）；开工判据只剩**真实用户复测
证据**（上节入场券）。`native_run_required` 已是对外错误码，ADR 0014
的契约必须与它的文案承诺一致（「按项目原方式运行」）。

## 建议启动命令

```bash
git status --short && git log -8 --oneline
PYTHONPATH=src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest -q \
    tests/test_open_script_route.py tests/test_compat_product_routes.py
/Volumes/Projects/Tavotto/.venv/bin/python scripts/ci/compat_matrix.py --smoke
```
