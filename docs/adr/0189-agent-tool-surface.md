# ADR 0189：Agent 工具面收敛——一步交付、改动不丢、报错给下一步、开场零开销

- 状态：**已接受**（2026-11-17，产品负责人裁决：「1，2，3，4都开始做」；1.0 豁免「给，1 和 2 都豁免」；改动不丢选「草稿自动带回」）
- 相关：[0180 Agent 一步改到期刊合规](0180-fix-to-journal.md)（第 1 条直接复用它）、[0078 会话跨进程恢复](0078-mcp-session-survives-server-process.md)（第 2 条扩展它的落盘记录）、
  [0006 Codex MCP](0006-codex-mcp-app-and-publication-profile.md)、[0155 内置期刊库](0155-journal-catalog.md)、[0171 导出修改后的 .py](0171-export-edited-script.md)、
  [0127 执行策略](0127-execution-policy-and-unified-retry-guard.md)、`docs/rules/plugin/` 各细则

## 背景：评测说明了 agent 在哪里卡住

`scripts/eval/agent_journal`（#1902 / #1957）让同一个 agent 把 20 张图 × 3 本刊改到合规，A = 只改脚本，B = 再给 Tavotto MCP。
被测模型 `opencode-go/deepseek-v4.1-flash` 的 B 组 60 次运行里：

| 现象 | 数 | 说明 |
| --- | --- | --- |
| 平均工具调用 | 31.1（A 组 22.2） | 用 Tavotto 反而多走 40% 的路，用时 329 s vs 178 s |
| `tavotto_open_figure` | 247 次（≈4 次 / 运行） | agent 反复开关会话；`tavotto_close_session` 119 次 |
| `tavotto_health` / `tavotto_guide` / `tavotto_list_journals` | 61 / 56 / 51 次 | 几乎每次运行都先绕这三圈才开始干活 |

v4-pro 首轮（#1957 README）的 B 组失败几乎全部是同一类：**agent 改完关会话或回脚本改完重开，会话里没导出的改动就没了**（#1903
加了提醒，但提醒要靠 agent 读到并照做）；另有一组 `fix_to_journal` 只回 `constraint_conflict` / `unsupported`，没说哪条约束冲突，
agent 只好去猜 override 的属性名。

共同的原因：工具面是给「会读说明、会记状态、会照规矩收尾」的调用方设计的；agent 不是。

## 裁决

### 1. 一步交付：`tavotto_make_journal_ready`

新增一个高层工具，一次调用走完 **打开 → 改到合规 → 预检 → 导出**，返回「改了什么、还剩什么、文件在哪」。

- **只编排，不新写逻辑**：打开 = `open_figure` 同一条路（含第 2 条的草稿带回）；改 = `fix_to_journal` 同一个事务（ADR 0180，
  整体提交或回 B0、栏位自动选的规则不变）；导出 = `export` 同一条路（导出前预检、产物核验、确认门槛一个不少）。
- 入参：`project`、`stem`（或 `script`）、`journal`（第 4 条的宽松解析）、可选 `column`、`formats`（缺省与 `tavotto_export` 的缺省一致）、
  `out_dir`（缺省与 `tavotto_export` 一致）、`dry_run`。
- 任一步没过就停在那一步，**不导出**，回包说明停在哪、剩哪些问题、下一步调什么（第 3 条的 `next`）；会话**留着不关**，agent 可以
  接着用细粒度工具。成功时同样不关会话。
- 跟期刊库总开关走（与 `tavotto_fix_to_journal` 同一处置）：开关关着时不进 `tools/list`；工具名不进 `usage.TOOLS` 遥测闭集，走 `other` 桶。
- 细粒度工具全部保留，行为不变。

### 2. 改动不丢：没导出的改动存成草稿，重开同一张图时自动带回

- 草稿 = ADR 0078 落盘记录的一种状态：会话**关闭 / 被淘汰**时，如果有**没导出的改动**（`unexported_changes` 为真），记录不删，
  改标为草稿，按（项目、stem、运行配置）可找到；没改动或已导出的照旧删除。
- `open_figure`（及第 1 条）打开同一（项目、stem、运行配置）且没有活会话时，按草稿的 patches 渲染；**先渲染成功再登记**（与 open 同一条
  纪律）。回包带 `restored_draft: {"patches": N, "from_session": "<旧 id>"}` 和一句如实的说明。脚本改过导致部分 patch 不再适用时，
  能用的带回、用不了的逐条列在 `restored_draft.dropped`，不整体失败。
- 新参数 `discard_draft: true`：不带回、并删掉草稿，从脚本原样打开。
- 导出成功即清草稿（这一版已经落到文件）。`close_session` 的 `UNEXPORTED_NOTE` 改为「修改已存为草稿，重新打开这张图会自动带回」。
- 草稿与记录同一套校验：记录不能自证权限，越界一律拒在渲染之前（ADR 0078 不变）；草稿保留期与记录过期同一个口径。
- **用户的原脚本一字节不动**（`never-modify-user-scripts`）；草稿只在 Tavotto 的数据目录里。桌面端不读 MCP 草稿（不在本 ADR 范围）。

### 3. 报错给下一步：`next`

- 每个 `BridgeError` 的回包可带 `next`：一个可以照抄的下一步，形如
  `{"tool": "tavotto_open_figure", "args": {...}, "why": "一句话"}`，或在必须回代码改时 `{"action": "edit_script", "why": "..."}`，
  或要用户决定时 `{"action": "ask_user", "question": "...", "options": [...]}`。参数能从当前上下文补全的一律补全。
- 码 → `next` 的映射集中在 MCP 层一个模块（`tavotto_mcp/nextstep.py`），不散在各个 raise 点；守卫用例按 AST 收集桥里出现的全部错误码，
  每个码要么有映射、要么列在显式的「没有可行下一步」清单里。
- `fix_to_journal` 的 `constraint_conflict` / `unsupported` / `not_resolved` 回包必须点名是哪条规则、哪个元素（gid）、哪两个约束冲突，
  能给出 override 的属性名就给。
- 不改 `code` 的取值与含义（老调用方照旧按 `code` 分诊）；`next` 是增量字段。

### 4. 开场零开销

- **健康状态并进回包**：`open_figure` / `make_journal_ready` 每个 MCP 进程的第一次成功回包带 `engine` 摘要（版本、`update` 提醒）；
  技能与 server `instructions` 改为「直接开始；工具缺失或报引擎错误才调 `tavotto_health`」。`tavotto_health` 保留。
- **期刊参数宽松解析**：接受期刊 id、全名、常见缩写（大小写、空格、标点不敏感）；唯一命中就用并在回包里写出解析到的 id；多个命中
  回 `journal_ambiguous` + 候选（第 3 条的 `next` 带上候选）；没命中回 `journal_unknown` + 最接近的几个。`tavotto_list_journals` 保留。
- **规则写进工具描述**：宿主不一定加载技能（opencode 这类），关键规则（patches 发全量、改完别关会话就先导出 / 现已自动存草稿、
  什么必须回代码改）写进对应工具的 description 与 server `instructions`；`tavotto_guide` 保留，但不再要求「开工前先调」。
- **技能瘦身**：`SKILL.md` 正文只留会话入口与规则，细节进 `references/` 按需读。

## 1.0 豁免

第 1、2 条扩大产品能力，按 §1.8 登记（用户 2026-11-17「给，1 和 2 都豁免」）；第 3、4 条是错误信息与调用开销的改进，不扩大能力。

## 验收

- 每条一个 PR，各带用例；第 1 条依赖第 2、3 条合入后再定稿回包。
- 全部合入后用同一套评测（Flash，B 组 60）重跑：期望工具调用数、用时下降，合规率不降；结果补进 `scripts/eval/agent_journal/README.md`。

## 不做

- 不把改动写回用户的原脚本（导出修改后的 `.py` 仍只写导出目录，ADR 0171）。
- 不去掉任何现有工具、不改现有错误码的含义。
- 不在 MCP 进程里自动安装 / 升级任何东西（首次使用契约不变）。
