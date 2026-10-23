# ADR 0148：智能修复路由——脚本跑不了时，后端按失败类型选唯一一种修法

日期：2026-10-23 · 状态：**提议**（维护者 2026-10-23 已批准方案页；本 PR 只落路由、报告字段、前端按钮与反馈卡，AI 环境修复任务在后续 PR）
相关：[0127 执行策略与重试闸门](0127-execution-policy-and-unified-retry-guard.md)（safe→native 静默回退，**不改**）、
[0143 生成可编辑版本](0143-convert-to-editable.md)（转写）、[0118 发送问题反馈](0118-diagnostics-send-report.md)（逐次确认、源码默认不勾）、
[0147 内置执行器](0147-builtin-agent-runner.md)（AI 修复将来的执行面）。

## 问题

用户的脚本跑不了时，今天的卡片按失败码各画各的：缺包给「安装」、位图脚本给「生成可编辑版本」、其余只剩「再试一次」或
「发送问题反馈」。同一类失败在不同入口出路不同；而最常见的「脚本自己的错误 / 环境对不上」没有一个能真正修的动作。
前端又在好几处各自判「这个失败该怎么修」，迟早分叉。

## 决定

### 一、后端一个路由，报告一个字段

`engine/fixrouter.py` 的 `choose(failure, ctx) -> Remedy` 是**纯函数、闭集**，结果写进准备会话报告的新字段 `remedy`
（失败 / 没出图 / 走不通的结局上才有，其余为 `null`；只加字段，旧前端读不到也不坏）。前端 `prepView` 只按它画主按钮，不再自己判。

```ts
type Remedy =
  | { kind: 'switch_native'; silent: true }
  | { kind: 'install_packages'; packages: string[]; ask: boolean }
  | { kind: 'change_python'; from: string; to: string | null }
  | { kind: 'convert_to_matplotlib'; source: string }
  | { kind: 'ai_fix' }
  | { kind: 'connect_ai' }
  | { kind: 'feedback'; tried: string[] }
```

前后端是严格同源对：golden `tests/golden/fix_remedy_vectors.json` + `tests/test_fixrouter_pair.py`（读 `web/src/lib/fixRemedy.ts` 的字段表）+
`web/src/lib/fixRemedy.test.ts`。

### 二、顺序即优先级，取第一条成立的

1. `safe_file_operation_blocked` 且允许 native（没被「只用安全运行」/ 运维开关挡、账本没记「本可回退却没回退」）→ `switch_native`（silent）。
   **只是标注**：真正的回退仍由 `execpolicy.decide_fallback` 做（0127 不改）。
2. `missing_dependency` 且能在会话里装、认得出包名 → `install_packages`。`ask=false` 当且仅当**全部包在 `autoinstall_dists.txt` 名单内**
   且环境允许直接装（自动安装开关开着，环境是 Tavotto 自管的项目 venv / 受管环境，或用户已按「每环境问一次」同意过）——与 `convertdeps` 同一口径。
3. Python 版本类失败（`PYTHON_VERSION_CODES`：`project_env_unsupported_python` / `root_python_mismatch` / `python_out_of_range` /
   `unsupported_python_version`）→ `change_python`。本 PR 只产出 remedy，执行在 PR B；前端此时走现有「选择运行环境」入口。
4. 位图脚本（`no_figure_hint.kind == 'raster_script'`）或 R / MATLAB 来源 → `convert_to_matplotlib`（复用现有「生成可编辑版本」）。
5. 其余：AI 修复轮数用完 → `feedback`；已连 AI（`ai_bridge.capabilities()` 里有 `usable` 的 agent，与设置页同一判据）→ `ai_fix`；否则 `connect_ai`。

**一次「运行」最多走 3 种修法**：读 `attempt_history` 里本次 run 的 `changed`（换了执行语义 = `switch_native`，换了解释器 =
`change_python`）加本次 run 里装过包（`install_packages`），已满 3 种再失败就是 `feedback`；同一种修法一次运行只走一回。

### 三、按钮规则（维护者 2026-10-23 裁决）

* 卡片仍是**一句话 + 至多一个主按钮**，其余折叠进「详情」；不加挤页面的横条；同一时刻只有一张卡。
* `ai_fix` / `connect_ai` 时主按钮是「✦ 使用你的 AI 来修复」；装包 / 转写 / 选环境三种确定修法主按钮照旧。
* **每张失败卡**（`switch_native` 静默、运行中、教程层激活除外）都有一颗**显眼的次按钮「✦ 使用你的 AI 来修复」**
  （蓝色描边 + 图标集的 Sparkles 图标——iconography 门禁不许拿 ✦ 字符当图标，所以文案本身是「使用你的 AI 来修复」；颜色走语义 token；`connect_again=always`：每次失败都出，不管 remedy 是什么，也不因 `ai_fix_available === false` 而隐藏）。
* 点它：没连 AI → 打开设置的 AI 页，连好回来（`aiStore.caps` 出现可用的 AI）自动接着进入修复，设置关了还没连上就放弃等待；
  已连 AI → `POST /api/ai/fix`（body：准备会话 id / 脚本）。
* AI 环境修复任务本 PR 不实现：端点一律 `200 {ok:false, reason:"ai_fix_unavailable"}`，`/api/ai/capabilities` 带 `ai_fix_available: false`；
  前端两种情况都把卡片落成「这个问题 AI 暂时还不会修」+ 主按钮「发送」。
* `feedback`：一句话「这个脚本暂时用不了，发给我们看看？」+ 主按钮「发送」（复用 `SendReportButton`，0118 的规矩不变：**每次确认、不记住**、
  源码复选框默认不勾）。
* 界面只说「AI」「连接 AI」，不出现「执行器」「CLI」「agent」。

### 四、诊断包「修复经过」

`detail-runs.json` 多一段 `fix_journey`（`prepsession.SESSIONS.fix_journeys`）：每个会话走过的修法（`tried` / 最后的 `remedy`）、账本里每次尝试的
结局码、最后一次的报错原文。与该文件其余字段同一遍 `Scrubber`——路径与密钥形状照常被抹。

## 本阶段不做

AI 环境修复任务本身（PR D）；`change_python` 的执行（PR B）；AI 修复轮数（`ai_rounds_exhausted` 目前恒 false）；R / MATLAB 入口卡（`ForeignScriptCard`）接 remedy。

## 后果

* 失败的出路只有一个判据；新增一种修法要同时改 `REMEDY_KINDS`、golden、`fixRemedy.ts` 的字段表——任何一侧漏改，同源对用例当场红。
* 每张失败卡多一颗按钮，换来「失败 → AI」这条路永远看得见；代价是在任务落地前点它会落到反馈卡（已在文案里如实说）。
* 规则全文：`docs/rules/backend/preparation-and-receipts.md`、`docs/rules/frontend/readiness-and-left-shell.md` 各有一句指向本 ADR；看护
  `tests/test_fixrouter.py` / `tests/test_fixrouter_pair.py` / `tests/test_fixrouter_report.py`、`web/src/lib/preparationText.test.ts`、
  `web/src/components/PreparationCard.test.tsx`（「智能修复」一节）。
