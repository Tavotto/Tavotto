# 运行时 import 图与增量架构守卫

2026-09-17 可维护性审计任务书的 PR B。仓库里此前没有任何依赖图 / 循环检测工具；结构性
守卫只有两条 AST 用例（`test_axes_traversal_authority.py`、`test_desktop_close_guard.py`）。
这里补的是**两张图 + 两道门禁 + 一份旧债基线**，不改任何产品代码，也不改既有 CI gate 的
语义（两道门禁各自落在已有的 backend-fast / frontend 里）。

## 图怎么建

| | Python（`src/tavotto`） | 前端（`web/src`） |
| --- | --- | --- |
| 解析 | `ast`，不 import 产品模块 | TypeScript 编译器 API（`ts.createSourceFile`），不执行模块 |
| 建图 | `tests/support/importgraph.py` | `web/scripts/import-graph.mjs` |
| 报告 | `python tests/support/importgraph.py [--json]` | `node web/scripts/import-graph.mjs [--json]` |
| 门禁 | `tests/test_import_architecture.py` | `web/src/importArchitecture.test.ts` |
| 基线 | `tests/import_architecture_baseline.json` | `web/import-architecture-baseline.json` |

判据的主语是**生产模块之间的运行时边**。每条边带 `kind`：

* `static`：普通 import（TS 侧含 `export … from` 与副作用 import）。
* `flat`（Python）：平铺 import。`worker.py` / `browser.py` / `manifest.py` 这些先把
  `engine/` 塞进 `sys.path` 再裸 `import manifest`——静态解析按「同目录有这个文件」补上。
  不补的话 worker 侧九个模块在图上是孤岛（上一轮 boundlens 扫描正是这样漏掉的）。
* `dynamic`：`importlib.import_module(…)` / `import('…')`。字面量能解析；非字面量**必须
  登记**（基线的 `dynamic_calls` / `unknownDynamic`），给出目标或说明目标是用户代码 / CDN。
  `overrides._sibling("manifest")` 就是这样登记进图的——普通静态扫描据此宣称「没有环」
  是错的。
* `type`：`if TYPE_CHECKING:` / `import type`。编译后不存在，不是运行时边，不参与环。

Python 侧每条边还带 `scope`（`module` / `function`）：**延后到函数体里再 import 也是
逻辑依赖**，照样算边；只是报告里把「每一步都在模块层」的环标成「加载期」、其余标成
「逻辑」。测试文件（`tests/`、`*.test.ts(x)`、`src/test/`）不进图。

解析不了的东西不会被当成「没有」：相对路径解析不开进 `unresolved`、非字面量动态 import
进 `unknown_dynamic`，两者非空门禁就红。

## 门禁的判据

环 = 强连通分量（≥ 2 节点）。基线登记每个已知环的成员与内部边，并要求 `reason` /
`since` / `remove_when`。门禁：

1. 与任何基线环不相干的分量 = **新环** → 红。
2. 基线环**多出成员或内部边** = 旧环扩大 → 红（新的延后 import 也算）。
3. 基线里的环在图上**不存在了** → 红，提示删登记（过期的豁免会安静地变成盲区）。
4. 跨层反向边：`* → entry`（底层不许 import `app.py` / `cli_entry.py` / `__main__.py`；
   前端是 `main.tsx` / `App.tsx` / playground 与 mcp 的 `main.tsx`）；前端另有
   `lib → hooks`、`types → *`、`store → ui`——这些今天都是零违例，门禁只是钉住。
5. 动态 import 登记表双向核对：没登记的红，登记了却已不存在的也红。

前端另有一条只报告不门禁的观察：`lib → store` 60 条、`lib → ui` 2 条，来自 13 个 lib
模块（`issueFocus` / `openRequest` / `clipboard` / `onboarding/*` / `exportFigures` /
`exportRequest` / `closeGuard` / `validationText`）。它们按设计是**编排**模块，不是纯
函数层；把它们当违例去挡等于发明一条没人定过的规则。要不要分出编排层，归任务书的 PR E。

## 2026-09-17 的旧债基线

**Python（89 个模块，274 条运行时边，0 条 type-only）**——三个环，全是逻辑环、没有一个
在加载期成环：

| 环 | 反向那步 | 删除条件 |
| --- | --- | --- |
| `manifest ↔ overrides` | `overrides.FigState` 里 `_sibling("manifest")._ordered_axes`（动态、函数内） | PR D：axes 遍历权威提到底层模块 |
| `pool ↔ bootstrap ↔ managedenv` | 四条边全在函数体里 | 「解释器候选 / 环境指纹」抽成底层事实模块 |
| `diagnostics ↔ telemetry ↔ updater` | `telemetry` 在函数里问 `diagnostics.install_kind()` | 「安装方式」判据抽成底层模块 |

跨层反向边 0；动态 import 3 处全部登记（`_sibling`、bridgeboot 的装载器——目标归属到
`bridge_runner` 的 `_PHASE1` / `_PHASE2`、worker 装用户脚本）。入度最高：`config.py` 23、
`runtime.py` 14、`pool.py` 12；`app.py` 出度 45、入度 1（只有 `cli_entry`）。

**前端（368 个模块，2198 条运行时边，228 条 type-only）**——两个环：

| 环 | 性质 | 删除条件 |
| --- | --- | --- |
| `diagnostics/{index,snapshot,authority} ↔ store/{document,render,svgPreview,env}Store`（7 文件） | ADR 0016 有意的双向依赖；安全条件 = 环上每个模块只在函数体内用对端导出（issue #397） | 诊断改成不 import store 时 |
| `hooks/useEngineSync ↔ store/actions` | `requestRender` / `isJustBakedBaseline` 互相引用，都是运行时边 | PR C |

第一个环另有一道**求值顺序**门禁 `web/src/importArchitecture.evaluationOrder.test.ts`：
把环上每个成员各当一次「第一个被求值的」动态 import 一遍。反证：在 `renderStore.ts` 顶层
加 `const _probe = variantHash({} as never)`，七个入口里三个（index / documentStore /
svgPreviewStore 先求值）当场 `TypeError: variantHash is not a function`，去掉后绿。

跨层反向边（门禁规则）0；动态 import 1 处（Pyodide 从 CDN 装）已登记。入度最高：
`i18n/index.ts` 151、`lib/utils.ts` 103、`store/uiStore.ts` 85、`store/documentStore.ts` 75；
`actions.ts` 入 51 / 出 27。

与同日 boundlens 扫描（跨语言 10 个环、判无害）的口径差：boundlens 看不见 Python 的平铺
import 与 `_sibling` 动态边，所以它没有 `manifest ↔ overrides`；它按文件级计的
diagnostics ↔ store 环是 5 文件，这里算上 `authority.ts` 与 `envStore.ts` 的 `import()`
边是 7 文件。两边对「加载期无害」的结论一致。

## 怎么用

* 改了 import 结构先看报告：`python tests/support/importgraph.py`、`node web/scripts/import-graph.mjs`
  （`--json` 给机器）。
* 拆掉一个环（PR C / PR D）：把基线里那条删掉——不删门禁会红着提醒你。
* 新写了一处非字面量的动态 import：登记到基线，写清目标；不登记门禁红。
* 门禁的反证（每条变异都红、恢复后绿）记在开出这两道门禁的 PR 正文里：新增延后 import 成环 /
  `TYPE_CHECKING` 里的不算 / 平铺 import 让旧环扩大 / 未登记的动态 import / 底层反向
  import 入口 / 基线删环 / 基线登记不存在的调用 / 拆掉 `_sibling` 后基线过期；前端同款
  外加 `import type` 不算、相对路径与 `@/` 别名同样认、`import()` 动态边算、
  `store → components` 红。
