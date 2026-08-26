# Compatibility Bridge Session Handoff

> 每个 Session 结束前必须更新本文件。

## 当前状态

- 日期：2026-08-26
- 当前 branch：`compat/bridge-session05-asset-library`（worktree
  `.claude/worktrees/compat-bridge-session01`，stacked 在
  `compat/bridge-session04-runtime-asset` → `…session03-script-probe` →
  `…session02-execution-spec` → `…session01-audit` 之上；五支均未推送——
  按审计 §七，Session 2–6 合成一个 PR 1 再走 push → PR → merge）
- 基于 commit：Session 4 落库提交 `191e6c1`
- 本 Session Prompt：`06_SESSION_05_ASSET_LIBRARY_UI.md`（外部实施包）
- 目标 PR：PR 1（本 Session 交付素材库普通入口：图/脚本两区、
  「运行并发现图」、取消真终止、runtime 卡片与写回区文案）
- 当前工作树状态：干净（本 Session 一个提交）

## 本轮唯一目标

把兼容能力从高级注册表工具带到普通用户素材库：用户打开旧项目后，能在
素材面板看到脚本并点击「运行并发现图」；Runtime Figure 出现在「图」区、
可加画布可编辑；状态机、取消、并发与项目切换正确；safe 说明与失败恢复
路径就位。**不做** native backend（只有文案与「复制诊断」，无假按钮）、
不改 RegistryDialog 的高级职能、不动 `tavotto open`（Session 6）。

## 已完成

- [x] **后端取消与并发闸**：`probe(should_cancel=...)` 协作取消（判取消
  即停，**不再试下一个 entry**；被 cancel 硬杀的 WorkerError 如实归类
  `execution_cancelled`，绝不报成「脚本坏了」；取消输给成功——跑完照常
  登记）；`pool.force_cancel`（当场 kill，绕开要抢 `w.lock` 的优雅关停）；
  app 层 `_PROBES` 按 (项目 id, script) 互斥（第二个请求 409
  `probe_in_progress`，新码 + 双语文案 + USER_VISIBLE_CODES）；
  `POST /api/registry/probe/cancel`（幂等；没有在跑回 `cancelling:false`）；
  SSE `probe.started`（starting_runtime → running 的边界）。
- [x] **runtime 素材清单**：`runtimeasset.list_assets` 唯一实现——注册表
  里**磁盘无原件**的 (script, stem) 各成一条（有原件的归
  FileAsset/scan_panels，绝不双列）；带物化 cache 的描述符与六档 status
  （阶梯抽成 `_status_ladder` 与 stale_status 共用，列表只探一次解释器）；
  `GET /api/runtime/assets` 只读零执行。
- [x] **前端状态机 `scriptRunStore`**（四条纪律，vitest 12 项看护）：
  同脚本防并发（busy no-op + 后端 409 兜底）；cancel 打后端端点、行内
  状态等**原请求**以 execution_cancelled 落地；每次 run 换代（gen），
  迟到响应丢弃；`clear()` 升 epoch，切项目在途响应作废。错误存原始
  code+params，显示时才翻。相位含
  idle/starting_runtime/running/captured_one/captured_many/no_figure/
  missing_dependency/timeout/cancelled/failed；`needsNative()` 是
  「可能需要原环境」的分组判据（missing_dependency/timeout/failed）；
  possibly_stale/missing_source/missing_environment 属素材侧
  （RuntimeStaleStatus），missing_environment ≈ prompt 的 needs_native。
- [x] **素材库两区**（AssetBrowser 重构 + 新 `ScriptLibrary`）：
  * 「图」= FileAsset 卡 + RuntimeFigureAsset 卡同一个 listbox（方向键/
    Enter/Space 全通）。runtime 卡：「运行时图」badge、cache 预览
    （`previewNonce` 重跑换 src）、stale 角标（复用 panelBadge.runtime*）
    + 行内重跑、尺寸只在跑过后显示；**没跑过的没有假尺寸假路径**，主
    动作是「运行并发现图」；大图弹层带「没有原始图文件」的如实说明；
    「加入画布」只走描述符 `addRuntimePanel`。类型筛选新增「运行时图」。
  * 「脚本」= `script_inventory` 全量，四组：已关联（含已关联张数）/
    尚未运行 / 静态解不出输出 / 可能需要原环境（+ 折叠的工具脚本组）。
    每行：路径、状态一行话（aria-live=polite，只随相位变化）、运行/取消
    **同一个按钮**（焦点天然不搬家）、错误按 code 翻译、高级详情折叠
    （entry 候选/静态 stems/reason 码——内部术语只住在这里）。
  * safe 首次说明（隔离写入 + 点击才运行；localStorage 关闭）；失败恢复
    块：解释可能依赖原环境 + 「选择渲染环境」（设置 about 段）+
    「复制诊断」+ traceback 折叠；**无任何可点的 native 假按钮**。
  * 多 Figure：结果 Dialog（focus trap）逐张列出 + 各自添加 +
    dropped_figures 如实显示。
- [x] **数据层**：`scriptLibraryStore`（/api/registry 视图缓存）、
  `runtimeAssetStore.assets/loadAssets/previewNonce/bumpPreview`；
  `useServerEvents`：probe.started → markRunning、registry.changed →
  重取**已经取过的**两份清单；projectStore 切项目清 scriptRunStore
  （epoch）+ scriptLibraryStore。
- [x] **runtime 面板写回区**（PanelSection.RuntimeSourceArea）：不是藏
  按钮——显示原因（「这张图来自脚本运行，没有对应的原始图文件。你仍然
  可以编辑、组图和导出；导出会创建新文件。」）+ 来源脚本 + 重新运行；
  文件面板的写回按钮与历史照旧。「写回成功」在 runtime 路径上结构性
  不可能出现（后端 400 硬拒绝未动）。
- [x] **i18n**：全部新文案 zh-CN/en-US（workspace 的 assets.*/scripts.*、
  errors 的 probe_in_progress）；复数按语言分形态；`pnpm i18n:check` 绿；
  英文主路径无中文（vitest 看护）。
- [x] **测试**：后端 `tests/test_asset_library.py` 7 项（cancel
  sentinel、取消先于执行零 spawn、被杀错误不误报、并发 409、清单零执行、
  不双列、probe→清单带描述符）；前端 vitest +27（scriptRunStore 12、
  ScriptLibrary 8、AssetBrowser.runtime 3、runtimeSourceSection 2 +
  既有全绿）；e2e `asset-library.spec.ts` 2 条（show-only 真实后端
  黄金路径：脚本区→运行→runtime 卡→画布→图内编辑→改字号/线宽→
  undo/redo；窄视口按钮可见）。六条负向反证完成（见下）。
- [x] 两个受管产物重建且 `--check` 一致：canvas.html 指纹
  `9ad3e162ab2476e9`、playground 指纹 `5a5adb8f6abf942f`。
  PR 1 合并后 re-sync 网站仓库。

## 未完成

- [ ]（无——本轮范围内全部完成）

## 本轮关键决策

### 决策 1：取消 = 后端硬杀，UI 只等原请求落地

- 同步阻塞的 probe 里「取消」必须真正终止（反证 #3）：cancel 端点置
  Event + `pool.force_cancel`（直接 `proc.kill()`——invalidate 的优雅
  关停要抢被 build 占着的锁，等到超时的取消不叫取消）。阻塞中的请求
  拿到 EOF → probe 判取消 → `execution_cancelled`。前端绝不先行改状态
  ——「界面装停了、脚本还在跑」是撒谎；cancelRequested 只是按钮态。
- 取消输给成功：脚本在取消前跑完就照常登记（已发生的执行不装作没发生）。
  SSE 化（进度流）留给 Session 6+，本轮码表已经就绪可复用。

### 决策 2：starting_runtime → running 由 SSE probe.started 驱动

- 同步请求本身分不出「在启动」与「在执行」；后端在真正开始执行前发
  `probe.started`（带 pj + script）。SSE 丢了也无害（停在 starting 文案，
  结果照常落地）。这是加事件不是加协议：worker/browser 协议零改动。

### 决策 3：runtime 素材清单 = 注册表减磁盘原件，注册表仍是唯一权威

- 「图」区的 RuntimeFigureAsset 条目来自 `list_assets`：注册表每对
  (script, stem) 中 `find_original_artifact` 找不到原件的那些。cache 里
  有、注册表里没有的**不列**（解析不到就渲染不了，列出来是幽灵——与
  Session 4 决策 1 同一条权威链）。描述符只从物化 cache 取；没跑过的
  条目没有尺寸没有描述符，「添加到画布」不开放——不给假值。
- 已登记但从未产出原件的静态条目也会作为 runtime 素材出现
  （needs_rerun）：这是语义正确的——它们的图只存在于运行时。

### 决策 4：needs_native 是判据不是新相位

- prompt 状态机里的 needs_native 落地为两半：脚本侧
  `scriptRunStore.needsNative()`（missing_dependency/timeout/failed →
  「可能需要原环境」组 + 恢复文案）；素材侧 RuntimeStaleStatus 的
  `missing_environment`。不新造第二套枚举。PR 2 落地后这两处升级为
  实际 native 入口，本轮只有文案 + 「选择渲染环境」+「复制诊断」
  （不渲染可点但无功能的按钮，vitest 断言看护）。

### 决策 5：RegistryDialog 原样保留，素材库不复用它的组件

- RegistryDialog 继续做冲突裁决 / 手工 stem / 高级诊断 / 批量重扫；
  素材库脚本区是独立组件但**同一数据源与同一 probe 端点**（并发闸对
  两个入口同时生效——同一脚本在对话框与素材库各点一次，后端 409）。
  没有第二套 probe 语义。

## 架构与数据契约

### 新增/修改接口

```text
engine/probe.py
  probe(figures_dir, script, entries=None, should_cancel=None)
  probe_and_register(..., should_cancel=None)
engine/pool.py
  force_cancel(script_name, figures_dir) -> bool   # 当场 kill
engine/runtimeasset.py
  _status_ladder(...)（stale_status / list_assets 共用）
  list_assets(project_root, registry, worker_python=None) -> list[dict]

web/src/store/scriptRunStore.ts（新）  run/cancel/markRunning/reset/clear
web/src/store/scriptLibraryStore.ts（新）  /api/registry 视图缓存
web/src/store/runtimeAssetStore.ts  assets/loadAssets/previewNonce/bumpPreview
web/src/components/left/ScriptLibrary.tsx（新）
web/src/components/left/AssetBrowser.tsx  图/脚本两区 + RuntimeAssetCard
web/src/components/inspector/PanelSection.tsx  RuntimeSourceArea
web/src/lib/api.ts  cancelProbe / fetchRuntimeAssets / RuntimeAssetInfo
                    / runtimePreviewUrl(id, nonce) / SSE probe.started
```

### API/协议形状

```jsonc
// POST /api/registry/probe —— 形状不变；新增并发闸：
//   409 {code: "probe_in_progress", params: {script}}
// POST /api/registry/probe/cancel  {script} → {cancelling: bool}（幂等）
// GET  /api/runtime/assets → {assets: [{id, script, stem, entry, status,
//   cached, size_mm|null, capture_source|null, descriptor|null}]}
// SSE probe.started {pj, script}
```

worker/browser 协议**零改动**；文档 schema 零改动；注册表格式零改动。

### 稳定错误码（本轮新增）

```text
probe_in_progress   app（409；同一脚本已有 probe 在跑）
```

## 修改文件

| 文件 | 修改原因 | 是否有测试 |
|---|---|---|
| src/tavotto/engine/probe.py | should_cancel 协作取消 | test_asset_library |
| src/tavotto/engine/pool.py | force_cancel | test_asset_library（sentinel） |
| src/tavotto/engine/runtimeasset.py | list_assets + 阶梯共用 | test_asset_library + test_runtime_asset 原样绿 |
| src/tavotto/app.py | _PROBES/取消端点/清单端点/probe.started | test_asset_library |
| tests/test_error_codes.py | +probe_in_progress | — |
| tests/test_asset_library.py（新） | Session 5 后端面 | 7 项 |
| web/src/store/scriptRunStore.ts(+test)（新） | 运行状态机 | vitest 12 |
| web/src/store/scriptLibraryStore.ts（新） | 脚本区数据源 | vitest（间接） |
| web/src/store/runtimeAssetStore.ts | assets 清单 + previewNonce | 既有 5 项绿 + 间接 |
| web/src/components/left/ScriptLibrary.tsx(+test)（新） | 脚本区 | vitest 8 |
| web/src/components/left/AssetBrowser.tsx(+AssetBrowser.runtime.test) | 图/脚本两区 + runtime 卡 | vitest 3 + e2e |
| web/src/components/inspector/PanelSection.tsx(+runtimeSourceSection.test) | runtime 写回区 | vitest 2 |
| web/src/hooks/useServerEvents.ts | probe.started / registry.changed | 间接 |
| web/src/store/projectStore.ts | 切项目清两个新 store | scriptRunStore epoch 测试 |
| web/src/lib/api.ts | 新端点/类型/预览 nonce | 各消费测试 |
| web/src/i18n/locales/*/{workspace,errors}.json + resources.d.ts | 双语文案 | i18n:check |
| web/e2e/asset-library.spec.ts（新） | 普通入口黄金路径 | e2e 2 条 |
| codex-plugin/mcp/widget/canvas.html | 受管产物 | --check |
| src/tavotto/AGENTS.md / web/AGENTS.md | 规则记录 | 文档 |
| docs/compatibility/COMPATIBILITY_BRIDGE_HANDOFF.md | 本文件 | — |

## 实际运行的测试

```bash
# worktree 内、PYTHONPATH=src、主仓 .venv 解释器（worktree 无 .venv）
PYTHONPATH=src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest \
    tests/test_asset_library.py                       # 7 passed
PYTHONPATH=src …/python -m pytest tests/test_runtime_asset.py \
    tests/test_script_probe.py tests/test_error_codes.py   # 128 passed
PYTHONPATH=src …/python -m pytest -q                  # 全量 exit 0
    #（首轮红两条：canvas.html 指纹过期连锁 test_mcp_server 与
    #  test_windows_regressions 的 --check——重建受管产物后全绿）
PYTHONPATH=src /Volumes/Projects/Tavotto/.venv/bin/python \
    scripts/ci/compat_matrix.py --smoke               # 通过（门禁 nightly）
cd web && pnpm test        # 87 文件 950 项
cd web && pnpm build && pnpm i18n:check
python scripts/build_mcp_widget.py && … --check       # 9ad3e162ab2476e9
python scripts/build_browser_playground.py && … --check  # 5a5adb8f6abf942f
# e2e（先 python scripts/build_frontend.py；worktree 跑法：）
cd web && TAVOTTO_PYTHON=/Volumes/Projects/Tavotto/.venv/bin/python \
    PYTHONPATH=<worktree>/src pnpm playwright test e2e/asset-library.spec.ts
    # 2 passed（show-only 黄金路径 + 窄视口）
```

（坑复述：① 直接 `pnpm vitest run` 没有 package.json 里 test 脚本的
`NODE_OPTIONS=--no-experimental-webstorage`，jsdom 的 localStorage 被
node 内建遮蔽成 undefined——单跑文件要手动带上；② e2e 在 worktree 里
必须 `PYTHONPATH=<worktree>/src`，否则 `python -m tavotto` 跑的是主仓
editable install 的代码；③ 属性栏的 NumberField 可见标签是同级文本、
不入 accessible name，e2e 取「线宽」输入框要用快速编辑工具条里那个。）

## 负向反证（本轮六条，全部先红后还原）

| # | 变异 | 判据测试 | 结果 |
|---|---|---|---|
| 1 | ScriptLibrary 只列 registered/static_candidate | `ScriptLibrary.test::所有合理脚本可见`（连锁多条红） | **红**（还原后绿） |
| 2 | RuntimeAssetCard 加画布前要求 `descriptor.original_artifact` | `AssetBrowser.runtime.test::加入画布用描述符` | **红**（还原后绿） |
| 3 | cancel 端点只回 200、不置标志不杀 worker | `test_asset_library::test_cancel_kills_the_running_probe`（30s sentinel） | **红**（还原后绿） |
| 4 | scriptRunStore 只保留 descriptors[0] | `scriptRunStore.test::captured_many` + `ScriptLibrary.test::结果弹层` | **红**（还原后绿） |
| 5 | SourceSection 当普通面板 + UpdateSourceButton 去掉 runtime guard | `runtimeSourceSection.test::写回入口不出现` | **红**（还原后绿） |
| 6 | `stale()` 恒 false（作废检查拆除） | `scriptRunStore.test::切项目作废` + `::迟到响应` | **红**（还原后绿） |

## 真机/产品证据

- OS：macOS（arm64，本机开发环境）。e2e 真实链路（真 Flask + 真
  matplotlib worker + 真浏览器）：show-only 项目从素材库脚本区一路走到
  图内编辑与 undo/redo，全程无 RegistryDialog。窄视口（960px）按钮在
  可视区内。workerd 腿本机 skip（未 cargo build）：force_cancel 的
  workerd 侧 `force_kill` 为同一调用面（代码级同构），未真机验证。

## 已知失败与限制

| 问题 | Stage/Route | 严重度 | 是否本轮 | 后续 |
|---|---|---|---|---|
| `tavotto open script.py` 仍不自动 probe | product_entry × cli | 高 | 否 | Session 6 |
| probe 仍同步阻塞：无进度百分比/分钟级脚本只有「正在运行」一句话 | product_entry × desktop | 中 | 部分缓解（可取消、双相位文案） | SSE 进度流另立条目（码表可复用） |
| starting→running 依赖 SSE：事件丢失时停留在「正在启动」文案（结果不受影响） | product_entry × desktop | 低 | 是（记录在案） | 不修 |
| MCP 内嵌画布无素材库/脚本区（widget 只打包画布） | product_entry × mcp | 低 | 是（记录在案） | Session 5+ 视需要 |
| 「可能需要原环境」只有文案与复制诊断，无 native 入口 | product_entry | 按设计 | 是 | PR 2 升级为实际入口 |
| runtime 卡片在「来源」筛选生效时整体隐藏（它们没有 folder） | asset_model | 低 | 是（记录在案） | 不修（清筛选即回） |

## 不得被下一 Session 破坏的约束

- Session 2/3/4 的全部约束仍然有效（尤其：runtime id 不透明不反解、
  打开项目/文档绝不执行脚本、cache 是派生物、writeback 拒绝在后端、
  lazy 门、probe 物化不二次执行、受管产物重建）。
- **取消语义是端到端的**：`/api/registry/probe/cancel` 必须继续
  置标志 + `pool.force_cancel` 硬杀（sentinel 用例看护）；probe 判取消
  后**不再试下一个 entry**；前端绝不先行把状态改成 cancelled。
- **并发闸在后端**（`_PROBES` + 409 probe_in_progress）：前端的 busy
  no-op 只是礼貌，任何新入口（Session 6 的 `tavotto open` 自动 probe）
  同一脚本并发时都要吃这个 409 或复用同一登记表。
- **`GET /api/runtime/assets` 只读零执行**（用例看护）；清单唯一实现
  `runtimeasset.list_assets`，新消费点不得自己拼「注册表减原件」。
- **`scriptRunStore` 的代际纪律**：run 换代 + clear 升 epoch 是
  「迟到响应不落进新项目」的唯一闸门，改状态机必须保住那 12 条 vitest。
- **不渲染可点但无功能的 native 入口**：PR 2 合并前，「按项目原方式
  运行」只允许出现在文案里（vitest 断言看护）。
- **素材库两区是普通路径的唯一入口**：RegistryDialog 保留但不得成为
  show-only 用户的必经之路（e2e 黄金路径看护）。

## 下一 Session 唯一目标

> 让 `tavotto open script.py` 在静态发现失败时自动 safe probe，完成
> 单/多 Figure 打开；扩展 CompatBench 产品路由与完整保存/重开/export
> E2E，并收口 PR 1。

## 下一 Session 首先阅读

```text
AGENTS.md / CLAUDE.md（src/tavotto/AGENTS.md 的 probe 取消一节 +
  web/AGENTS.md 的素材库普通入口一节）
docs/compatibility/COMPATIBILITY_BRIDGE_MASTER_PLAN.md
docs/compatibility/COMPATIBILITY_BRIDGE_HANDOFF.md（本文件）
src/tavotto/engine/handoff.py + engine/cli.py（tavotto open 的现路径）
src/tavotto/engine/probe.py（probe_and_register + should_cancel）
scripts/ci/compat_matrix.py（产品路由的登记方式；smoke 当前
  24 cases：21 full / 3 partial）
web/e2e/asset-library.spec.ts（保存/重开/export E2E 的扩展基座）
```

注意：`tavotto open` 的自动 probe 是「用户执行了命令」——它就是显式
动作（总纲原则 5 允许），但要与素材库同用 `_PROBES` 并发闸；CompatBench
的 product route 至少要把 `safe_probe` 与 `desktop_project` 走真实端点
（不许绕过 UI 层已有语义再造第二套）。收口 PR 1 时记得：五支 stacked
分支合并、`docs/adr/0014` 仍是 Proposed（native 属 PR 2）、网站仓库
re-sync playground。

## 建议启动命令

```bash
git status --short && git log -8 --oneline
PYTHONPATH=src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest -q \
    tests/test_asset_library.py tests/test_script_probe.py tests/test_runtime_asset.py
/Volumes/Projects/Tavotto/.venv/bin/python scripts/ci/compat_matrix.py --smoke
```
