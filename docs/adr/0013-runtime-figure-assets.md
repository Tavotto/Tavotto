# ADR 0013：Runtime Figure Assets（运行时 Figure 成为正式素材类型）

状态：**Accepted**（Session 1 草案 2026-08-25；Session 4 实施并定稿
2026-08-26——三个待定稿事项的裁决见文末「定稿裁决」）
相关：[Compatibility Bridge 总纲](../compatibility/COMPATIBILITY_BRIDGE_MASTER_PLAN.md)、
[事实审计](../compatibility/compatibility-bridge-audit.md)、
[0014 Safe/Native Execution Profiles](0014-safe-native-execution-profiles.md)、
[0003 worker 协议 v1](0003-worker-protocol-v1.md)（零协议改动——`stems.<s>.source`
已随 build 响应带出）。

## 背景

Tavotto 的素材模型是"磁盘文件"：`scan_panels()` 只列真实存在的 PDF/图片，
文档面板的身份是 `PanelObject.fileId`（图库相对路径）+
`fileKind: 'pdf' | 'raster'`。引擎侧早已能捕获从未存过盘的 Figure
（show-only 脚本的 pyplot 兜底，`figcapture.SOURCE_PYPLOT`），CompatBench
九级漏斗对它全绿——但这些 Figure 在产品里**无处存在**：没有素材条目、
没有文档表达、没有可保存重开的身份。审计 §五列了四个具体不可达 case。

## 决策（草案）

### 1. 素材双形态

```text
AssetSource
├── FileAsset          现状：磁盘文件（PDF / 位图），一个字节不改
└── RuntimeFigureAsset 新增：一次受控执行捕获的 Figure，没有（或不依赖）磁盘原件
```

RuntimeFigureAsset 与 FileAsset 在能力面上等价：编辑、撤销、重放、组图、
预检、导出全部走**现有的同一套语义**（FigState → manifest → apply →
render → replay → export）。差别只在"原件"与"写回"。

### 2. 稳定 asset id

```text
runtime:<script 相对路径（POSIX 分隔）>#<stem>
例：runtime:panels/myplot.py#myplot
```

- 只由 `(项目, 脚本相对路径, stem)` 决定。stem 的确定性由现有捕获策略保证
  （savefig 字面量，或 figcapture 的"本次捕获里第几张"编号——后者的稳定性
  论证见 `figcapture.py` 模块头）。
- **禁止**混入 PID、临时目录、绝对路径、会话 id、时间戳——保存重开后
  override 必须还挂在同一个身份上（FigS3 与 fallback-stem 两次事故的教训）。
- 前缀 `runtime:` 与现有 fileId（图库相对路径）在字面上不可能冲突
  （相对路径不含 `:`，Windows 盘符不会出现在**相对**路径里；`safe_resolve`
  对它 404 也是这个前缀挡住的）。
- **落地记录（Session 2）**：id 生成实现为 `figcapture.runtime_asset_id()`
  （worker/browser/probe 共用），并已作为 `CapturedFigureDescriptor.asset_id`
  随 build/probe 响应带出。id 是**不透明标识**——脚本名里可以有 `#`，
  消费方不得从 id 反解 script/stem，要用就取描述符的独立字段。entry 刻意
  不进 id（本决策的理由照旧；entry 的差异由描述符字段与 fingerprint 承担）。

### 3. 文档 schema 表达

`PanelObject` 增加 `fileKind: 'runtime'` 取值，`fileId` 即上述 asset id；
另加可选 `source` 描述块（CapturedFigureDescriptor 的持久化子集）：

```jsonc
{ "type": "panel", "fileId": "runtime:myplot.py#myplot",
  "fileKind": "runtime",
  "source": { "script": "myplot.py", "entry": "__main__", "stem": "myplot",
              "captureSource": "pyplot", "fingerprint": "sha1:…",
              "sizeMm": [120.0, 90.0] } }
```

- **schema 版本不升**（仍是 project=3 / canvas=2）：新增的是可选字段与
  枚举值。旧版本前端打开含 runtime 面板的文档会把它当"缺失素材"显示
  （现有缺失处理路径），不会崩、不会丢数据——降级行为要有用例钉住。
- `migrateToProject` 零改动。

### 4. cache 与 materialization

- 每个 RuntimeFigureAsset 的预览（SVG/PNG）与可选的 materialized PDF 落
  `engine/config.data_dir()` 下的缓存（沿用 `cache/engine/` 的会话目录 +
  `prune_engine_cache` 治理；**绝不写进用户图库**）。
- cache 是**显示与占位用的派生物，不是用户原件**：UI 一律以"由脚本生成"
  标注，绝不把缓存 PDF 冒充成图库里的图；打包（project package）与导出
  合成引用 materialized 产物时要标明来源。
- **export 必须以当次权威 worker 渲染为准**：导出走现有 export 命令
  （状态中立、全质量），不许直接拷缓存文件交差。

### 5. stale 判定

- `fingerprint` 的实现（Session 2 落地，`figcapture.source_fingerprint`）比
  本节草案更宽一档：脚本内容 sha256 + ExecutionSpec 稳定字段（script/entry/
  profile/target_kind/argv/passthrough_savefig）+ matplotlib 版本 + 描述符
  schema 版本。**它只是 stale hint**：脚本读的 CSV、本地 import 的模块、
  环境变量、数据库都不在指纹里，文档与 UI 文案不得声称它覆盖一切。
  Session 4 定稿本节时以已落地的口径为准（比对逻辑仍可另用
  `pool.script_sha1` 做写回防线，两者目的不同：一个是提示，一个是阻断）。
- 文档打开 / mtime watcher 触发时与当前脚本比：不一致 → 面板标
  stale（"脚本已变，图可能过时"），**overrides 原样保留**，用户显式
  "重新运行"后按新捕获结果对齐（stem 消失时走现有"元素不存在 → warning"
  路径，绝不静默丢 override）。
- stale 不阻断显示：缓存的旧预览继续显示（诚实标注），也不阻断导出——
  但导出走的是重新 build，结果如实反映新脚本（与写回的 `script_changed`
  409 防线是同一件事的两端，写回那侧一个字不动）。

### 6. 执行时机（与总纲原则 5 对齐）

- RuntimeFigureAsset 的**创建**只来自显式用户动作（素材库"运行并发现图"、
  `tavotto open script.py` 的 safe probe、RegistryDialog 试运行）。
- **重开文档不自动执行脚本**：先显示缓存预览；首次编辑/渲染/导出该面板时
  才 lazy build（与现有磁盘面板首次渲染同语义——那本来就是"用户对这个
  面板动手了"）。若认为 lazy build 仍属"打开即执行"的争议面，实施时可
  收紧为"stale 时必须点一次重新运行"；草案先记两案，Session 4 定稿。

### 7. 写回语义

- `captureSource == "pyplot"`（无原始产物）：**artifact writeback 整个
  不出现**（不是禁用，是不渲染入口）；`can_writeback_artifact: false`。
- `captureSource == "savefig"` 且磁盘上确有同 stem 产物：写回入口照旧
  （现有事务，包括 expected_mtime / script_sha1 / 全量重放 / 像素门，
  一条防线不减）。
- **source writeback（改写用户脚本）v1 一律 false**——见 Pylustrator 研究
  "保存即改源码：不吸收"。

### 8. 多 Figure

- 一次执行捕获的每个 stem 各成一个 RuntimeFigureAsset（savefig 认领的与
  pyplot 兜底的并存）；`dropped_figures`（超过 `MAX_PYPLOT_FALLBACK=8`
  被丢弃的张数）必须到达 UI（当前只写 worker.log，审计 §五-3），文案
  如实："还有 N 张未捕获（显式 savefig 不受限）"。

## 不做的事

- 不新增第二份 manifest / override / undo / replay / export 实现；
- 不用 pickle / RPC 传 Figure（ADR 0014 §为什么不 pickle）；
- 不把 runtime cache 写进用户图库或冒充原件；
- 不在项目打开时静默执行任何脚本。

## 定稿裁决（Session 4，2026-08-26）

1. **§6 取 lazy build 案**：重开文档先显示 materialized cache 预览（没有
   就显示占位），进入对象级编辑 / 显式刷新 / 导出那一刻才 build 并重放
   overrides——与磁盘面板首次渲染同语义。**本会话已经跑过的** runtime
   面板（`renderStore.latest` 里有它）之后与文件面板同一待遇（撤销/重做、
   AI 改脚本后的热重建照常）；"打开文档绝不自动执行"只约束重开那一刻
   （总纲原则 5），门在 `useEngineSync.renderTargets` 的 runtime 分支，
   前端用例 + 后端 status/preview 零执行用例双面看护。stale 时**不**强制
   先点重跑：角标如实提示"可能已变化"，重跑发生在下一次编辑/刷新。
2. **renderStore 键形态沿用 `fileId + overrides`**，fileId 即
   `runtime:` asset id。后端解析**正向重算**（`runtimeasset.resolve` 拿
   注册表里每对 (script, stem) 重算 id 比对，绝不反解）；"素材=磁盘文件"
   消费点的 sweep 结果：`_engine_worker`（runtime 分支）、
   `_resolve_panel_source`（导出走 live worker）、`update_source` 与
   `history/restore`（硬拒绝）、`/api/package`（描述符 + 脚本）、
   `scan_panels` 与素材库（**刻意不动**——普通素材库入口是 Session 5）、
   前端 `panelSrc` / `PanelView` / `useWriteBackTargets` / `preflight` /
   `useServerEvents`（描述块认领 stale，不解析 id）。
3. **项目包只带描述符 + 源脚本，不带 materialized 副本**：cache 是本机
   派生物不是原件，接收方跑一次脚本即可重建；`package_manifest.json` 新增
   `runtime_assets` 键（旧读取端原样忽略），`package/open` 不把 runtime
   id 记为缺失。

## 落地记录（Session 4）

- 引擎侧唯一实现 `engine/runtimeasset.py`：`resolve` / `materialize` /
  `load_metadata` / `preview_path` / `stale_status` / `prune_cache` /
  `writeback_rejection`。cache 落
  `data_dir()/cache/runtime/<slug>/`（preview.svg + metadata.json，
  **metadata 永远最后写**，坏 metadata 当没有；`generated_by: "Tavotto"`）。
- stale 枚举：`fresh / possibly_stale / missing_source /
  missing_environment / needs_rerun / rerun_failed`（最后一个的 producer
  在前端 runtimeAssetStore）。判据 = 脚本 sha256 + 注册表 entry，只是
  提示——UI 文案说"脚本或执行环境可能已变化"，不说"数据未变化"。
- 稳定错误码：`runtime_asset_unknown` / `runtime_asset_has_no_original_artifact`
  / `runtime_cache_missing`（app 层字面量，进 USER_VISIBLE_CODES）；
  `runtime_source_writeback_unsupported`（v1 无 producer 端点，码表 +
  双语文案先行，对拍在 `tests/test_runtime_asset.py`）。
- 看护：`tests/test_runtime_asset.py`（30 项）+ 前端
  `useEngineSync.test.ts` lazy 门 / `runtimeAssetStore.test.ts` /
  `document.test.ts` AssetSource 段。
