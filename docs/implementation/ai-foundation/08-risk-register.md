# 08 · 风险登记

| ID | 风险 | 触发场景 | 影响 | 缓解 / 状态 |
| --- | --- | --- | --- | --- |
| R-01 | 与 1.0 收敛纪律冲突 | TAF 是能力扩展 | 违反仓库纪律 | Phase 0–2 无用户可见变化；Phase 3 前维护者决定登记例外或推迟。**待决定** |
| R-02 | 两套状态机 / 两份历史 | Task Core 自建表或自建状态枚举 | 前端与历史互不知情 | 任务 = session；只加列；状态映射见 `03-contracts.md` §八；P1 结构测试钉住「没有第二张会话表」 |
| R-03 | CLI 的写入隔离与预期不同 | Codex workspace-write / Claude acceptEdits 在 cwd 外的实际行为随版本、用户全局配置变化 | 候选模式下仍可能改到原项目 | 不把它当保证：原项目有界指纹兜底（发现即 failed 并列文件）；P3 入口实测两 CLI 并记录版本。**2026-10-20 实测（macOS，产品参数）**：codex-cli 0.163.0 `exec --sandbox workspace-write` 与 Claude Code 2.1.303 `-p --permission-mode acceptEdits` 都没能写到 cwd 外（codex：OS 沙盒 `operation not permitted`；claude：`permission_denials` 列出 Write 与 Bash），cwd 内文件工具与 shell 都能写。注意 codex 的 workspace-write 默认放行 /tmp。单次、单平台、本机用户配置生效下的结论，Windows / Linux / 其他版本**未验证** |
| R-04 | 候选环境与原项目语义不同 | 相对数据路径、`__file__`、本地模块 import | 候选上验证通过、真实位置上失败 | L2–L4 放在提交后、真实位置做；候选只承担 L1；不能建立候选时降级并标注 |
| R-05 | 数据指纹成本 / 漂移 | 大数组；脚本用随机数 | 时延上升；同一脚本两次运行指纹不同 → 误报 | 流式哈希 + 上限后记 unknown；运行间指纹不稳定时（同一脚本连跑两次不同）该项记 unknown 而不是 failed；P2 实测 |
| R-06 | 验证渲染引入额外执行 | 每次改图多跑一次用户脚本 | 副作用脚本被多跑 | 不新增执行：用刷新本来就会触发的那次渲染；native 图不自动渲染（unknown）；预算计数 |
| R-07 | Agent 环境全继承 | `spawn_env()` 把所有环境变量交给 CLI | 密钥对 Agent 可见 | 现状如此、不在 TAF 范围内改；单独议题。**已登记** |
| R-08 | 提示注入经数据进入 | CSV 单元格 / 列名含指令 | 模型被诱导写文件或执行 | data_to_figure 只接受结构化 JSON 输出、脚本由模板生成；FB-D08 回归 |
| R-09 | 假成功 | outcome 与 verdict 混用；unknown 当通过 | 用户被误导 | 汇总规则写死在契约；`completed` 的准入条件；FigureBench 反例；UI 文案只从 verdict 派生 |
| R-10 | 基准过拟合 | 在同一批场景上反复调 Prompt | 产品上不成立的改善 | dev / holdout 固定拆分 + 泄漏守卫；场景只增不改 |
| R-11 | 指导文档预算耗尽 | 速查表距 32 KiB 硬线 < 100 B | 加不了规则行 | TAF 规则全文放 `docs/rules/backend/` 新细则；速查表加一行前先腾字（P1 处理） |
| R-12 | 多文件提交不是事务 | 某任务需要同时改脚本 + 样式文件 | 部分写入 | v1 不支持多文件提交，结局 partial 并说明 |
| R-13 | Harness² 许可 | 逐字抽取代码 | 许可证义务遗漏 | 目前只借思想；抽取时保留 Apache-2.0 头 + 修改声明 + 第三方声明登记 |
| R-14 | 夹具自证依赖本机解释器 | CI 环境没有带 matplotlib 的 worker Python | 用例 skip（not_run）被误读为绿 | skip 文案写明 not_run；P2 起由验证器在 safe worker 上跑同一批场景（不再依赖探针） |
