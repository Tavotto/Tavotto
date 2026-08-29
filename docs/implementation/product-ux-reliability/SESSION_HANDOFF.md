# SESSION_HANDOFF — 跨 Session 交接

> **整段重写，不加行。** 半新半旧的状态块会继承「刚被动过」的可信度，
> 而下一个 Session 没有办法分辨哪几行是这次更新的。

---

## 最近一次：Session 06（2026-08-29）

### 目标

把 04/05 建起来的后端刷新与 watcher 接到**用户看得见的地方**：

```text
外部修改 → SSE → 前端素材元数据刷新 → 当前画布 PanelObject 原地更新
        → 受影响 Figure 局部重建
```

本阶段**只做前端事件消费与派生元数据同步**——不做 Readiness UI（07/08）、
不做多选栏、不做 onboarding、不增强解析器。后端一行没改。

### 实际完成

**1. 事件字段解码（`web/src/lib/api.ts`）。** `ServerEvent` 的四条形状
Session 04/05 已经补齐，本轮补的是三个纯函数：

```ts
affectedScriptsOf(event)   // registry.changed 的批量 scripts ∪ 单脚本兼容字段 script
affectedStemsOf(event)     // registry.changed / panel.file_changed 的 stems
affectedAssetIdsOf(event)  // assets.changed 的 ids ∪ added ∪ removed ∪ changed
```

理由是**可选字段与兼容字段**：不收口的话每个 handler 都要自己写一遍「先看
批量、没有再看单条」，写三遍就会有一遍漏掉 `script`——而那正是 probe 与手工
登记这两条最常见路径的形状。三个函数都容忍畸形载荷（payload 是 `JSON.parse`
出来的，类型声明不是运行时保证），非数组、混了非字符串一律当"这一维没有
信息"，不抛。**没有用 `any`。**

**2. `assetStore` 的并发治理。** `load()` 今天有七个触发点，而一次统一刷新
连发 `registry.changed` + `assets.changed` 两条事件——「同一批里被调好几次」
是常态。

```ts
load(opts?: { force?: boolean }): Promise<PanelsResponse | null>
refresh(): Promise<PanelsResponse | null>     // 事件驱动的入口，与 load() 同一条路
```

* **合并**：同项目的在途请求被后来者复用，一批事件只发一个 `/api/panels`；
* **旧响应不覆盖新响应**：判据是**请求序号**，不是"谁最后返回"（后者正是
  缺陷本身）。序号比 AbortController 稳——被 abort 的请求在 jsdom 与真浏览器
  里抛的东西不一样，而要挡的行为（旧值落地）两边一模一样；
* **项目隔离**：发请求那一刻这个标签页认领的 pj，与落地那一刻的比。
  `null`（跟随后端默认项目）与某个具体 id 是**两个取值，不合并**；
* **`force` 永远另起一次**：手动刷新被一次早就发出的在途请求吞掉的话，
  用户按了没反应，而"没反应"正是他按它的原因；
* **失败不清空**：`panels` / `byId` 一个都不动，只多一条非阻塞错误；
  首次加载失败时 `loaded` 仍是 false，界面照旧显示 EmptyState；
* **返回本次生效的响应**（被丢弃或失败时 `null`）——调用方据此决定要不要
  拿它去同步文档。

模块级账本有 `resetAssetLoadBookkeeping()` 供用例清零（它们活得比一次
`setState` 长）。

**3. PanelObject 派生元数据同步（新模块 `web/src/store/panelSourceSync.ts`）。**

```ts
syncPanelSourceMetadata(
  panelsById: Record<string, PanelInfo>,
  options?: { affectedIds?: readonly string[] },   // 素材 id；不给 = 全量比对
): PanelSyncResult
```

```ts
interface PanelSyncResult {
  upgraded: string[]        // 对象 id：不可编辑 → 可编辑
  downgraded: string[]      // 对象 id：可编辑 → 不可编辑
  changed: string[]         // 对象 id：任意派生字段变了（前两者是它的子集）
  missing: string[]         // 对象 id：文档引用了、本次清单里没有
  staleFileIds: string[]    // 素材 id：要按新脚本重建（渲染层按文件粒度工作）
  droppedFileIds: string[]  // 素材 id：刚失去脚本，失效的 manifest / 缓存按它清
}
```

素材粒度的处置有**三档**：有脚本 → 重建；刚失去 → 清缓存；**从来没有脚本
→ 两样都不做**。第三档单列的理由是一张普通位图的 `pxW` 也会变，把它标成
`tracked` 等于告诉显示层"这张图要走引擎产物"，而它根本没有脚本可跑。

```ts
```

**同步**：`script`、`cost`、`fileKind`、`pxW`。
**绝不碰**：`x/y/w/h`、`nativeW/nativeH`、`crop`、`rotation`、`overrides`、
`groupId`、布局组、`locked`、`hidden`、`name`、`opacity`、`flipH/flipV`、
`lockedGids`、选择、文档名。图幅为什么也在这一列见 DECISIONS 的 T-27
（它是几何，且权威在变体自己的 manifest 而不是磁盘文件）。
**runtime 面板整个跳过**（`runtime:` 前缀的 id 永远不在 `/api/panels` 里，
不跳过的话每轮都会被判成"素材不见了"）。

非激活画布同样同步；**激活画布只算一遍**（`canvases[active]` 只是快照，
权威在 `doc`）。

**4. 保存链路的第三档（`documentStore`）。** 新增状态字段 `derivedSeq` 与
导出入口 `applyDerivedUpdate({ doc?, canvases? })`。自动保存的订阅现在按
两个代次认三种性质：

| 性质 | `dirty` | `saveState` | 撤销历史 | 落盘 |
| --- | --- | --- | --- | --- |
| 载入（`loadSeq` +1） | 由载入方声明 | 由载入方声明 | 清空 | 不排队 |
| 用户编辑 | 置位 | 推成 `dirty` | 进 | 排队 |
| 派生同步（`derivedSeq` +1） | 置位 | **不动** | **不进** | **排队** |

「不标脏」与「必须可靠落盘」不矛盾，理由全文在 DECISIONS 的 T-26：`script`
是**存进文档的字段**（不落盘的话下次打开又回到不可编辑），但一次外部文件
改动不是用户的编辑（推 `saveState` 会让关闭保护弹一句用户没做过的事）。
写盘本身照常走完整状态机——`saving` / `save_error` / `conflict` 一个不吞。

**5. 编排（新模块 `web/src/store/liveSync.ts`）。** SSE 事件、手动刷新按钮、
SSE 重连恢复**三个入口共用一条路径**：

```ts
refreshAssetsAndSync(opts?: { force?: boolean; affectedIds?: readonly string[] })
refreshProjectNow()         // 手动刷新：POST /api/project/refresh，再走上面那条
syncLoadedDocument()        // 项目打开：拿手里的清单对一次账，**不发请求**
recoverAfterReconnect()     // 3 秒节流，**不调**后端静态刷新
```

**项目打开也要对账**（`App.tsx`：素材清单与 `restoreSession()` 都到齐之后调
`syncLoadedDocument()`）。理由是它是七个触发点里唯一「文档比清单晚到」的那个
——Tavotto 关着的时候用户完全可能在外面改脚本，而项目打开那一轮 watcher
**只建基线、一条事件都不发**。不对这一次账的话那些改动要等到下一次外部修改
或手动刷新才生效，而用户看到的现象是"我明明加了脚本，它就是不认"。

降级的处置（§七）：退出该面板的图内编辑 → 清 `selectedGids` → **画布选择
保持不变** → `overrides` 一条不删 → `renderStore.reset(fileId)` 清掉失效的
manifest 与渲染缓存 → 提示「这张图的源脚本关系已失效，已返回画布。图片和
排版没有被删除。」升级则**不**自动进编辑态，只给一条轻提示。

**6. 手动刷新入口（`AssetBrowser`）。** 按钮从 `assetStore.load()` 改成
`POST /api/project/refresh` + 合并刷新；忙碌态覆盖**整条**（后端那一步才是
最慢的，只看 `assetStore.loading` 的话按钮在那一步是不转的）；失败弹常驻
错误。文案从「重新扫描 / 重新扫描素材目录」改成「刷新项目 / 检查项目里的
新文件与脚本改动 / 正在检查新文件…」——普通用户看不到 registry、stem 这类词。
与 RegistryDialog 的手工扫描是两件事（那条是冲突裁决用的高级入口）。

### 关键 API（Prompt 07 及之后直接用）

```ts
// web/src/store/assetStore.ts
load(opts?: { force?: boolean }): Promise<PanelsResponse | null>
refresh(): Promise<PanelsResponse | null>
resetAssetLoadBookkeeping()            // 只给用例：清模块级并发账本

// web/src/store/panelSourceSync.ts
syncPanelSourceMetadata(panelsById, options?): PanelSyncResult

// web/src/store/documentStore.ts
applyDerivedUpdate({ doc?, canvases? })   // 外部派生数据的唯一写入口
derivedSeq                                 // 状态字段：派生同步的代次

// web/src/store/liveSync.ts
refreshAssetsAndSync(opts?)                // 事件 / 手动刷新 / 重连共用
refreshProjectNow() / syncLoadedDocument() / recoverAfterReconnect()
resetReconnectThrottle()                   // 只给用例：节流是模块级状态

// web/src/lib/api.ts
affectedScriptsOf / affectedStemsOf / affectedAssetIdsOf

// web/src/hooks/useServerEvents.ts
handleServerEvent(ev)                      // 导出给用例驱动（同 syncEngine 的先例）
```

### 迁移

**没有数据迁移，磁盘格式一个字节没动。** `derivedSeq` 是运行时状态，不进
文档。唯一的接口变化是 `assetStore.load()` 从 `Promise<void>` 变成
`Promise<PanelsResponse | null>`——既有调用方全部忽略返回值，不受影响。

### 修改的文件

```text
新增  web/src/store/panelSourceSync.ts         派生元数据同步（唯一写入口）
新增  web/src/store/liveSync.ts                事件 / 手动刷新 / 重连的共同路径
新增  web/src/lib/serverEventFields.test.ts    13 条
新增  web/src/store/assetStore.test.ts         14 条
新增  web/src/store/panelSourceSync.test.ts    19 条
新增  web/src/store/derivedAutosave.test.ts     8 条
新增  web/src/hooks/useServerEvents.test.ts    26 条
新增  web/src/components/left/AssetBrowser.refresh.test.tsx   5 条
改动  web/src/lib/api.ts                       +三个事件解码纯函数
改动  web/src/store/assetStore.ts              并发治理；load 返回权威数据
改动  web/src/store/documentStore.ts           +derivedSeq、+applyDerivedUpdate、订阅认第三档
改动  web/src/hooks/useServerEvents.ts         +assets.changed/project.error handler；重连恢复
改动  web/src/components/left/AssetBrowser.tsx 刷新按钮改走统一刷新 + 忙碌态 + 错误可见
改动  web/src/App.tsx                       启动时清单与文档都到齐之后对一次账
改动  web/src/i18n/locales/*/workspace.json    assets.rescan* → assets.refresh*；status 新增四条
改动  web/src/i18n/resources.d.ts              i18next-cli types 重新生成
重建  codex-plugin/mcp/widget/canvas.html      改了 web/src 就要重建
```

### 测试命令与真实结果

```sh
# 前端（先 cd web）
pnpm test && pnpm build && pnpm i18n:check && pnpm lint
# 单跑某个文件时**必须自己补上环境变量**（它在 package.json 的 test 脚本里）
NODE_OPTIONS=--no-experimental-webstorage npx vitest run src/store/assetStore.test.ts
# 后端全量（worktree 里必须带 PYTHONPATH，否则 import 到主工作区）
PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest
# 格式（与 CI 那一格逐字相同）
ruff check . && ruff format --check .
# 改了 web/src 之后（排在所有前端改动**与** i18next-cli types **之后**）
python scripts/build_mcp_widget.py
```

数字见 `STATUS.md` 的表：前端 **124 files / 1456 passed**（比 05 的 1371
+85），`build` / `i18n:check` / `lint` 三条 exit 0。**变异反证 55 条全部被
打红**（清单与五条第一轮活下来的分析见 `TEST_MATRIX.md`——其中一条查出来是个
多余的守卫，处置是删掉它）。

**后端全量 exit 1**，唯一那条红是
`tests/native/test_run_cli_integration.py::test_ctrl_c_reaches_the_script_and_leaves_no_orphan`
——本轮 `src/tavotto/**` 一个字节没改，那条用例走的是 Python CLI，不加载
`web/src/**` 的任何东西。四组范围实测与「不处置成绿」的理由写在 `STATUS.md`
的「全量套件里的那条红」。**不要因为它在你这一轮也红就当成背景噪音**：它今天
早些时候（Session 05）还是绿的。

### 这一轮踩到的两个环境坑

1. **`npx vitest` 会漏掉 `NODE_OPTIONS=--no-experimental-webstorage`。**
   没有它，Node 自带的 `localStorage` 全局会盖住 jsdom 那份**并且不可用**，
   任何碰 localStorage 的用例报的是 `Cannot read properties of undefined`
   ——看起来像被测代码坏了，其实是跑法不对。
2. **`npx` 会在仓库根建一个空的 `node_modules/.vite`**，而 `.gitignore` 只
   忽略 `web/node_modules/`。跑完记得删，否则它会出现在 `git status` 里。

### 尚存限制

1. **`affectedScriptsOf` 还没有生产调用方**（另外两个有）。它是 §三 明确要求
   的解码收口，有单测；自然的第一个消费者是 07 的就绪度（脚本 → 就绪状态）。
   这件事写在这里，不假装它已经在用。
2. **`assets.changed` 里 `removed` 的素材不清渲染缓存**：面板继续显示最后
   一次成功的那张图，由 `preflight` 的 `missing-asset` 报出来。清掉的话画布会
   当场变成占位框，而文件很可能只是暂时不见（T-28 的同一条取舍）。
3. **升级会触发一次引擎重建**（`markStale` 置 `tracked`）。这与
   `panel.file_changed` 的既有行为同源（那条路径 Session 05 之前就这么做），
   代价是一张一直静静躺着的 PDF 在获得脚本的那一刻会跑一次 worker。要改成
   "等用户双击才跑"的话，需要给 `markStale` 分出"只作废、不跟踪"的第二档
   ——本阶段没做，因为 Prompt 06 §十明确要求"registry 升级后新 `script`
   能触发引擎构建"。
4. **没有 SSE 事件名的同源门禁**（Session 05 遗留第 10 条，仍然开着）：后端
   `sse_publish` 的事件名与前端 `EVENT_KINDS` 写错一个字，后端全绿而前端
   永远收不到。本轮四条事件都有端到端用例钉住，但门禁本身还没做。
5. 04/05 的其余遗留（R-05 五处手写原子写、R-07 autosave 位置、
   `/api/layouts/<name>` 无 schema 校验、「编辑历史」入口位置）原样开着。

### 工作树状态

- worktree：`/Volumes/Projects/Tavotto/.claude/worktrees/product-ux-v2`
- 分支：`feat/product-ux-reliability-v2`，从 `origin/main` 的 `ef9ac02` 开出
- **PR #201** 已开，带的是 Session 01–04。**05 与 06 的提交还没有推**
  ——用户定的节奏是「每个 Session 一个独立提交，攒够几个再一次推」，
  推上去会立刻触发一轮 Codex 评审，所以由用户决定什么时候推
- author 用 `88193520+erwanjun@users.noreply.github.com`（与 `main` 上每一个
  提交一致）。本机 `~/.gitconfig` 是 `1259959884@qq.com`，两者不一致会让
  cla-check 在同一个仓库里数出两个贡献者；提交时用
  `git -c user.email=… commit`，**别改共享的 `.git/config`**
  （linked worktree 默认共享它，一条命令污染所有会话）
- `web/node_modules` 已在 worktree 内真装

---

## 下一阶段入口（Prompt 07：Readiness 后端事实模型）

**从这里开始读**：`docs/adr/0025-…`（统一刷新的编排与 `ProjectRefreshResult`
的形状）、`src/tavotto/engine/project_refresh.py` 的 `_events()`、
`web/src/store/panelSourceSync.ts`（前端手里现在有哪些派生事实）。

**Session 06 留给它的**：一份**稳定**的前端数据面。

| 东西 | 位置 | 07 可以直接依赖的性质 |
| --- | --- | --- |
| 素材清单 | `assetStore.byId` | 不会被旧响应覆盖、不会串项目、后台失败时是"上一次成功的那份"而不是空 |
| 画布上每个面板的派生字段 | `PanelObject.script/cost/fileKind/pxW` | 与 `/api/panels` 一致（事件、手动刷新、SSE 重连三条路都会把它拉平） |
| 一次同步的差异 | `PanelSyncResult` | upgraded / downgraded / changed / missing 四类 + 两组素材 id |
| 显式刷新 | `POST /api/project/refresh` → `refreshProjectNow()` | 界面上已经有入口（素材面板的刷新按钮） |

**绝不要做的事**：

1. **不得重新实现刷新。** 就绪度是在这份数据之上**再算一层事实**，不是第二个
   刷新器。素材清单的唯一取法是 `assetStore.load()`，文档里派生字段的唯一写
   入口是 `syncPanelSourceMetadata()`，事件与手动刷新的唯一路径是
   `store/liveSync.ts`。
2. **不要为就绪度再开一条事件通道**（SSE 已经是唯一通道）。
3. **不要在组件里直接改 `documentStore` 的文档字段**（走 actions / 派生入口）。
4. **`conflicts` 缺席 = 这一轮没跑静态扫描，不是"没有冲突"**——就绪度最容易
   在这里把"没测量"当成"测量结果是零"。

**必须保留的不变式**（改动前先确认还成立）：

1. `loadSeq` 把「载入」与「编辑」分开；`derivedSeq` 把「派生同步」从这两者里
   再分出来（三档表见上）。
2. `dirty` 同时盯 `doc` 与 `canvases`。
3. 收到 409 后**基线故意不推进**，本机兜底副本**不清**。
4. `HistoryEntry.label` 存 `UiMessage` 描述符，绝不存翻译后的字符串。
5. 落盘一律走 `engine/atomicio`（ADR 0023）。
6. 保存状态只经 `setSaveState()` / `setDocNotice()` 改（ADR 0024）。
7. 排队稍后才发出的写入必须带走**排队那一刻**的 `pj`；前端的每一次
   `/api/panels` 同样带走发请求那一刻的 `pj`。
8. **刷新的编排只有 `refresh_project_index()` 一份**（ADR 0025）；**发现只有
   `project_watch` 一份**（ADR 0026）；**前端的消费只有 `liveSync` 一份**。
9. **无差异 = 零事件、零写盘、零 worker 失效**（后端）；
   **无差异 = 零 `set()`、零 dirty、零提示**（前端）。
10. 「哪些文件算素材」只有 `iter_assets()` 一处判据；脚本遍历只有
    `discover.iter_all_scripts()` 一处。
11. `reason` 是闭集，表外的值归成 `manual`；客户端字符串不透传。
12. 刷新失败时内存里的注册表**原封不动**，事件一条不发；前端同理——
    刷新失败就**不拿旧清单去同步文档**，也不弹提示。
13. **派生数据刷新不得把文档标脏（对用户而言），也不得进普通撤销历史**
    （UX_CONTRACTS 不变式 A / §1a）。
14. **素材不在清单里 ≠ 脚本关系失效**：前者保留对象与 `script`，走既有缺失
    素材语义；只有后端明确说"这个素材没有脚本"才算降级（T-28）。
