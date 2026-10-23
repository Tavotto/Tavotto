# RuntimeFigureAsset（无磁盘产物的捕获 Figure）

> 原文出自 `src/tavotto/AGENTS.md`「渲染引擎核心机制」（2026-09-17 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 看护用例（`src/tavotto/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`tests/test_runtime_asset.py`、`test_asset_library.py`

<!-- agents-route
sheet: src/tavotto/AGENTS.md
order: 140
改到: `engine/runtimeasset.py`、`runtime:` id、`/api/runtime/*`
主题: RuntimeFigureAsset → `runtime-figure-assets.md`
必守要点: id 不透明；端点只读绝不执行；写回硬拒绝；导出必须当次 live 渲染
-->

- **RuntimeFigureAsset（2026-08-26，Compatibility Bridge Session 4，ADR 0013
  定稿）**：没有磁盘产物的捕获 Figure 是正式素材类型，面板 fileId 是
  `runtime:<script>#<stem>`（**不透明标识**）。引擎侧唯一实现
  `engine/runtimeasset.py`（纯标准库，Flask import）：
  * **解析正向重算**：`resolve()` 拿注册表里每对 (script, stem) 重算
    `figcapture.runtime_asset_id` 与目标比对——任何消费方**不得反解 id**
    （脚本名里可以有 `#`）。app 层 runtime 判别只看前缀
    （`is_runtime_id`），`_engine_worker` / 导出 / 写回拒绝都从这里走。
  * **materialized cache 是派生物不是原件**：落
    `data_dir()/cache/runtime/<slug>/`（preview.svg + metadata.json，
    metadata 标 `generated_by: "Tavotto"`）；**metadata 永远最后写**（两个
    文件都 tmp + os.replace），预览写一半失败磁盘上就没有 metadata，整个
    cache 按不存在处理；坏/错版 metadata 一律当没有。cache 可删除可重建
    （probe 成功与 runtime 渲染成功时物化/刷新，描述符取
    `worker.last_build_descriptors`，**绝不为物化二次执行**），清理走
    `prune_cache`（app 启动线程，与引擎会话缓存同一治理）。
  * **lazy rehydrate（总纲原则 5）**：重开文档先显示 cache 预览/占位，
    `/api/runtime/status`、`/api/runtime/preview` **只读绝不执行**；进入
    对象级编辑 / 显式刷新 / 导出才 build 并重放 overrides。前端的门在
    `useEngineSync.renderTargets` 的 runtime 分支（editing 或本会话
    latest 才入队；tracked 不构成 runtime 自动重跑理由）。
  * **stale 只是提示**：`stale_status` 比脚本 sha256 + 注册表 entry（六档
    `fresh/possibly_stale/missing_source/missing_environment/needs_rerun/
    rerun_failed`，最后一档 producer 在前端）；数据依赖不追踪，文案说
    「可能已变化」。注册表条目丢了用文档描述块兜底，但 **fail closed**：
    重算 id 对不上就是未知，绝不套到猜出来的脚本上。
  * **写回硬拒绝**：runtime id 的 update_source / history/restore 一律
    400 `runtime_asset_has_no_original_artifact`（裁决唯一出处
    `writeback_rejection`；savefig 来源且磁盘有产物的走它的 FileAsset
    身份写回）；source writeback（改脚本）v1 整个不存在，码
    `runtime_source_writeback_unsupported` 先落表。**导出必须当次 live
    worker 渲染**（`_resolve_panel_source` runtime 分支），worker 起不来
    就报错，绝不拿 cache 旧文件冒充。项目包只带描述符 + 脚本。
  * **素材库清单（2026-08-26，Session 5）**：`runtimeasset.list_assets`
    是「图 → RuntimeFigureAsset」条目的唯一实现——注册表里**磁盘无原件**
    的 (script, stem) 各成一条（有原件的归 FileAsset/scan_panels，同一张
    图绝不双列）；`GET /api/runtime/assets` 只读绝不执行（零执行用例看护
    `tests/test_asset_library.py`）。**「有没有原件」按捕获来源判，不按
    文件名巧合**（Session 6 评审修复）：物化描述符说 pyplot 捕获的
    （结构上从没有过原件，figcapture 工厂钉死），同名磁盘文件是旧样本，
    不得把 runtime 素材顶掉——判据唯一出处
    `runtimeasset.is_pyplot_capture`，消费点 `list_assets` /
    `handoff._script_figures` / 前端 `applyOpenRequest` 三处同步。六档 stale 阶梯抽成
    `_status_ladder`（`stale_status` 与 `list_assets` 共用，列表场景只探
    一次解释器）。条目带物化 cache 里的描述符（前端「添加到画布」的数据
    源）；没跑过的条目没有尺寸没有描述符——不给假值。
  * 看护 `tests/test_runtime_asset.py`；`tavotto open` 的自动 probe 仍
    刻意未动（Session 6）。素材库普通入口已落地（Session 5，前端规则见
    `web/AGENTS.md`）。

## 运行配置维度（T03）

`runtime:<script>#<stem>` 是无参数运行的身份，**逐字节不变**。给了精确 argv 的运行是同一脚本的另一份配置：id 末尾多 `~rc_<12 位十六进制>`
（本机不透明引用，不是 argv 摘要）。`resolve()` 仍是正向重算——对注册表里每对 (script, stem) 算 base id 比对，再认 `base + "~rc_…"`
这一种后缀，别的形状一律不认；无配置时返回的字典键与此前相同。**歧义显式失败**：stem 可以合法地形如 `plot~rc_<12 hex>`，其无参数 id 与同脚本 stem `plot` 的带参数 id 逐字相同；同一 id 有不止一种读法时 `resolve()` 返回 `None`（调用方回 `runtime_asset_unknown`），不按注册表顺序猜。注册表装载不拒绝这种并存（否则整个图库打不开），只有撞上的那一个 id 不可解析；其余 id、单独一个形如后缀的 stem、不同脚本的同名 stem 都不受影响。
（409），渲染 / 导出 / 重开**不回落成空参数**。`list_assets` 把"真跑过、cache 里有"的配置变体各列一条（带 `run_config`，无参数值）。
无参数身份是否列出依赖 **runconfig 的持久捕获历史**，不能从 cache 缺失推断。`captures[script].runs` 按配置引用保存产出的 stem 并集，空引用记录无参数产出；这份记录位于 `runconfigs/`，不随 runtime cache 清理，也不随配置淘汰删除。
`probe.register_probed` 在合并注册表前记录成功捕获，`runtimeasset.materialize` 补记绕过 probe 的文档重放 / 渲染捕获。只有历史明确为「仅带参数产出」的新 stem 才隐藏无参数占位；真实无参数捕获在 cache 删除后仍列为待重跑。
来源未知的旧注册项缺少历史、或历史格式不可读时保留占位（fail-open）；后来带参数重跑也不能将其历史改判为「从未无参数运行」，由 `legacy_stems` 保留这份未知。明确由静态发现登记的候选不算捕获证据（落地入口见 [注册表规则](registry-discovery-and-probe.md)）；来源仅存在本机同一份历史的可选 `discovered` 集合中，不扩展用户项目的注册表格式。首次带参数捕获时将实际产出的 stem 从 discovered 移入配置历史，不混入 `legacy_stems`；尚未运行的候选仍保留待运行占位。若该候选已有真实无参数 cache metadata，则登记时先将这份正证据补记到持久历史，再处理本次产出，之后清理 cache 也不丢身份。判据只影响清单，不改资产 id、解析或已存文档。
看护 `tests/test_runtime_config_listing.py`：真实 `prune_cache` 只删除旧无参数目录后素材仍在、文档重放补记、配置专属 stem、旧项与坏历史保留占位、静态来源保存与旧无参数正证据补记。`tests/test_run_argv_e2e.py` 以真实 sidecar 验证字面量 savefig 先静态登记、再仅带参数执行后只列该配置素材。

## 速查表原要点（2026-09-25 迁入，#608）

`src/tavotto/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- id 不透明、解析正向重算不反解
- cache 是派生物、metadata 最后写
- status / preview / assets 端点只读绝不执行
- 写回硬拒绝 `runtime_asset_has_no_original_artifact`
- 导出必须当次 live 渲染
