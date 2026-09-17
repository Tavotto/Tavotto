# RuntimeFigureAsset（无磁盘产物的捕获 Figure）

> 原文出自 `src/tavotto/AGENTS.md`「渲染引擎核心机制」（2026-09-17 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

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
