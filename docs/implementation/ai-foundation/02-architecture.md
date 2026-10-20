# 02 · 架构

决定见 ADR 0138；代码事实见 `01-existing-system-audit.md`。本文件回答：TAF 的五个子系统各自落在哪、
依赖方向是什么、两条首发流程怎么走、和现有 ADR 哪里有张力。

## 一、依赖图（箭头 = import / 调用方向）

```
                       ┌──────────────── Flask 父进程（纯标准库） ────────────────┐
  web AiPanel ──HTTP──▶│ app.py /api/ai/*  ──▶ ai_tasks（Task Core）               │
   (SSE ai.*)◀─────────│        │                │   ├─ registry: task_type → Adapter
                       │        │                │   ├─ orchestrator: 阶段推进 / 预算 / 取消
                       │        │                │   └─ verify: Figure Verifier（L1–L3 判据）
                       │        ▼                ▼                                   │
                       │   ai_bridge（会话编排：快照 / spawn / SSE / diff / revert）  │
                       │     │   ▲  ai_history（sessions + ai_requests + 新列）       │
                       │     │   └─ ai_agents（AGENT_REGISTRY）← ai_providers         │
                       │     ▼                                                        │
                       │   scriptlock / scriptbackup / atomicio（唯一写脚本入口）      │
                       │   preflight / interference / normalize（L3 判据，复用）       │
                       │   write-back verify：_compare_manifests / _replay_pixel_diff │
                       │   rendercore.inspector（四态 VERDICTS、导出检查，L4）         │
                       └───────────────┬──────────────────────────────────────────────┘
                                       │ worker 协议（safe，显式 execution=safe）
                       ┌───────────────▼──────────── worker 子进程 ─────────────────────┐
                       │ figsession / manifest（+ 只读 data_fingerprint，Phase 2 新增）   │
                       └─────────────────────────────────────────────────────────────────┘
  scripts/dev/taf_bench.py（Harness Lab，离线）──▶ ai_tasks + Fake Executor + FigureBench（tests/fixtures/figurebench）
```

单向约束：

- `ai_tasks` 可以调 `ai_bridge` / `ai_history` / 验证判据；**反过来不行**——`ai_bridge` 保持「会话编排」，不知道任务类型。
- 适配器只依赖 Task Core 的契约与 Tool 层；不 import `ai_agents` 拼命令行（执行者经 `RunContext` 选择）。
- 验证判据（L1–L3）在父进程纯标准库求值；需要 numpy 的事实（数据指纹）只在 worker 里算、以 JSON 回来。
- Harness Lab 只在 `scripts/dev/`，产品运行时不 import 它；它读 FigureBench 的 **dev** 拆分。

## 二、五个子系统落点

| 子系统 | 落点（Phase 1 起，以审计后实况为准） | 说明 |
| --- | --- | --- |
| Task Core | `engine/ai_tasks.py`（一个模块起步：契约 dataclass、适配器注册表、编排、预算）；超过约 600 行再拆包 | 不为命名拆文件；纯标准库，进 import 架构白名单 |
| Task Adapters | `ai_tasks` 内注册 `figure_edit`（Phase 3）与 `data_to_figure`（Phase 5）；首个 Fake Adapter 只在测试里 | 适配器声明：输入校验、意图契约、计划、所需工具 / 权限、候选产物类型、必做验证、结果解释 |
| Tool Layer | 薄适配，映射到已有函数：`inspect_project`→registry / readiness，`inspect_figure`→worker manifest，`render_candidate`→`pool.build`（safe），`run_preflight`→`preflight.run`，`compare_figures`→`_compare_manifests` + 数据指纹比对，`inspect_export`→`inspector.inspect`，`stage_script_edit` / `commit_candidate` / `revert_candidate`→`scriptbackup.replace` / `ai_bridge.revert` | 每个工具的读写性、副作用、超时、错误码见 `03-contracts.md`；Phase 1 只落契约与 Fake，真实绑定随用随加 |
| Verification | `engine/ai_tasks.py` 的 verify 段（L1–L3 判据）+ worker 侧 `manifest` 新增只读数据指纹 + 抽出的重放一致性检查（L4） | 四态沿用 `rendercore.inspector.VERDICTS`；详见 `05-verification-design.md` |
| Harness Lab | `scripts/dev/taf_bench.py`、`scripts/dev/taf_optimize.py`（Phase 6）；Prompt Pack 版本化文件随产品发布，默认版本与今天 `_build_prompt` 的文本逐字节相同 | 离线、开发用；不得改安全面（`03-contracts.md` §六） |

## 三、流程 A：自然语言改图（figure_edit）

```
AiPanel.send（不改：草稿快照 → 提交账本 → aiRun，rid 原样）
 → /api/ai/run（不改外壳：spawn 前后 503 语义原样）
 → ai_tasks.submit(envelope)            # 实验开关关闭时这一步不存在，直接走今天的 ai_bridge.run
    固定目标：project_key、脚本相对路径、base_sha256（脚本字节）、gid + identity（0083）、manifest 指纹
    Figure Intent Contract：目标断言 + 保持断言（默认：数据 / 误差 / 坐标含义 / 单位不变）
    路由：路径 A（结构化 patch 可表达）| 路径 B（需要改脚本）
 → 路径 B：ai_bridge.run(candidate=…)   # 候选工作区，见下
 → 验证：L1（候选树差异、语法、figure contract）→ 提交 → 刷新 → L2/L3（新旧 manifest + 数据指纹）→ L4（按需）
 → 结局：completed / partial / needs_input / failed；验证摘要进 sessions 新列与 ai.done
```

### 路径 A（结构化编辑）

前提：意图能落成 `patchspec` 的 `{gid, prop, identity?, value}` 且 prop 在该元素 manifest 的 `editable[]` 里。
后端只**提议**（`propose_figure_patch`：校验 → `/api/engine/render` 预渲染 → L2/L3 验证），**提交仍由前端
`documentStore` 一次 commit**，这样 ⌘Z 一步撤回、排版保存语义都不变。不改脚本、不重跑用户代码以外的东西。
意图到 patch 的翻译：Phase 3 先不做（路径 B 打底）；Phase 3b 用 Agent 的「只输出 JSON、不写文件」模式提议、
后端用 `patchspec.canonicalize_with_diagnostics` 校验，拒绝一切不在 `editable[]` 的 prop。

### 路径 B 的候选工作区（Phase 3，开关后面）

| 步骤 | 做法 | 理由 / 坑 |
| --- | --- | --- |
| 建候选 | `data_dir()/cache/ai_candidates/<sid>/`：**只复制**目标脚本（及同目录的 `paper_style.py` 等提示词会提到的文件）；不复制数据 | 放项目目录会触发 watcher / registry 扫描；符号 / 硬链接会让改副本变成改原件（禁止） |
| Agent 的 cwd | 候选目录；提示词给出原项目的**只读**绝对路径供参考 | Codex `workspace-write` 把写入限制在 cwd（+ 临时目录）；Claude `-p` + `acceptEdits` 对 cwd 外的编辑没有交互批准——**两者都是 CLI 的行为，不是 Tavotto 的保证**，Phase 3 入口条件是在两个 CLI 上各实测一次（`08-risk-register.md` R-03） |
| 结束后 L1 | 候选目录文件树前后比对：只允许目标脚本变化；`ast.parse` 通过；静态 stem 集合与原脚本一致（新建图任务例外） | 今天没有「其他文件未被改动」的校验，这是第一次有 |
| 原项目防护 | spawn 前后对项目目录做一次有界的文件指纹（大小 + mtime + 小文件 sha256），发现非目标文件变化 → L1 failed、结局 failed、明确列出被动过的文件 | 防的是 CLI 沙盒失效或 Agent 绕路；有界（文件数 / 字节上限），超限记 unknown 而不是 verified |
| 提交 | 持 `script_guard`，比对原脚本当前 sha256 == `base_sha256`（否则 409 冲突、候选作废）→ `scriptbackup.replace`（两处备份后原子替换）→ 记 `after_sha256` → 统一刷新 | 写脚本字节只经 `write_script`；过期候选不覆盖新内容；回滚沿用 `ai_bridge.revert` |
| 提交后验证 | 刷新后的 manifest vs 改前 manifest：目标断言、保持断言（数据指纹、xlim / ylim / scale、文字）；失败 → 结局 failed / partial，并按任务策略**提示**一键回滚（默认不自动回滚：回滚本身也是一次写） | 候选在原位置之外运行会改变 `__file__`、相对数据路径、本地模块 import 的语义，所以 L2–L4 放在提交之后、在真实位置上做——这是诚实的「提交 + 可回滚」，不是「提交前已验证」，UI 文案必须一致 |
| Phase 3a 实现差异 | 提交用 `scriptlock.write_script`（持锁核基线后原子替换），备份沿用 AI 桥起跑时拍的改前快照（回滚走 `ai_bridge.revert`），没有再走 `scriptbackup.replace` 的两处备份；候选建不起来时整条拒绝，下面的「降级」未实现 | 与今天的 AI 路径同一个备份权威，不出现两套回滚入口 |
| 降级 | 候选无法建立（脚本 import 了同目录多个本地模块、目标不是单文件……）→ 退回今天的原地修改 + 快照回滚，并把「提交前隔离」记为 not_applicable、原项目指纹照做 | 不为形式上的 Stage–Verify–Commit 牺牲兼容性 |

## 四、流程 B：从数据出图（data_to_figure，Phase 5）

```
用户选 CSV（项目内，contained_path）→ inspect_data_source：标准库 csv 读受控摘要（列名、类型、缺失、行数、sha256）
 → 列映射：确定性启发式；不明确时 Agent 只输出结构化 JSON 建议（不写文件、不执行），后端校验列名真实存在
 → 决定科学含义的项（x/y 选择、单位）推不出 → needs_input，带中性预览
 → 脚本由 Tavotto 固定模板生成（不是模型写的代码）：无参 main()、静态 stem、相对数据路径、来源注释（文件名 + sha256 + 映射）
 → 写入项目（新文件、不覆盖已有文件）→ refresh_project 静态识别 stem → safe 渲染
 → 验证：数据指纹 == 由 CSV 按映射计算的期望指纹（父进程标准库算）、点数、缺失处断开、轴标题单位只来自列名
 → 之后就是普通图：图内编辑、改图助手（figure_edit）照常可用
```

「不执行模型写的代码」是 v1 的硬边界：safe 档不是沙盒，CSV 里的文字可以影响模型；模板生成让被执行的代码
完全由 Tavotto 决定。需要模型写自由代码的复杂绘图，留给后续适配器，并且先解决隔离问题（`04-security-model.md` §五）。

## 五、流程 C：开发阶段自优化（Harness Lab，Phase 6）

Baseline Prompt Pack（v1 = 今天的提示词逐字节）→ 候选 Pack（人写或模型提议，说明改了什么）→ 在 FigureBench **dev**
上用 Fake Executor（CI）或真实 CLI（单独命令、显式预算）执行 → 由 Figure Verifier 判 → 比较首发通过率、科学保持率、
非预期修改率、时延、成本 → 在 **holdout** 上复核 → 生成可审查的差异报告 → 人工审核后另开 PR 改默认 Pack。
运行时不读经验库原文；进入产品的只有审核过、带版本与来源的条目。

## 六、与既有 ADR 的张力（不悄悄绕开）

| 既有规则 | 张力 | 处理 |
| --- | --- | --- |
| 根 `AGENTS.md` 1.0 收敛纪律 | TAF 是能力扩展 | Phase 0–2 无用户可见变化；Phase 3 起要维护者登记例外或推迟（README「待决定」第 1 条） |
| ai-agent-bridge 规则：AI 结束后「不 probe、不跑脚本」 | 提交后验证需要渲染 | 不在 AI 桥里跑；验证渲染是刷新之后、前端本来就会触发的那次渲染 + 一次 worker manifest 读取，显式 safe；native 图不自动渲染，记 unknown |
| 0094 / 0110：只有用户看过 diff、确认后才写脚本 | 今天的 AI 路径本来就不经确认直接改（有快照回滚）；候选提交延续「AI 会话 = 用户发起的改写」 | 不扩大：候选提交的语义与今天相同（同一次用户请求、同一份快照回滚），只是多了备份与过期检查；多文件提交不支持 |
| 0127：只有桌面用户发起的运行才可回退 native | 有界修正可能想重跑 | 修正循环只重跑 **Agent**，不重跑用户脚本的 native；验证渲染显式 safe，不调用 `decide_fallback` |
| 0132：spawn 之后状态不确定 → 不自动重试 | 有界修正是「再来一次」 | 修正只在上一轮**确定结束**（`final=True`、有 after_sha256）且验证给出具体失败条件时发生；同一失败条件 + 同一配置不重复（`no_new_root_cause` 同一原则） |
| 0083：identity 不含数据摘要 | 需要数据指纹 | 指纹是独立的只读字段，不参与 gid / identity 匹配、不进 `editable`、不进文档 |
