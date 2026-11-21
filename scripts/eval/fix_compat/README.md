# 兼容性修复评测：内置 AI 修复 vs 编码 agent + Tavotto MCP

问题：同一份在 Tavotto 里跑不起来 / 不出图的脚本，Tavotto 内置的 AI 修复（A）和通用编码 agent 加 Tavotto MCP 插件（B）谁能修好、卡在哪。
结构、跑法、结果格式沿用 `scripts/eval/agent_journal/`。不进 CI，不在 PR 里跑；被测模型要额度。

- **题库**：`corpus.json`，11 题，每题是一个小项目（脚本和数据都是合成的，不含任何真实用户内容），形状来自 Tavotto 已修过 / 在修的兼容性家族。
  脚本存成 JSON 字符串而不是 .py，免得被 ruff 改写。每题有 `id`、`family`、`script`（目标脚本）、`files`；`solvable: false` 的是对照题。
  `outside_files`（可选）是写在项目文件夹之外的兄弟文件，不进哈希。
- **成功判据（两组一致，只有这一条）**：修复结束后，用真 sidecar 对**原始目标脚本**再建一次准备会话、点运行、等落定，
  `phase == completed` 且 `facts.figure_captured is True`；并且项目里原有的每个文件字节不变。新增文件允许，记在 `new_files`。
- **未修前先跑一遍**：每次运行先在独立目录里真跑一次（`before`）。本来就出图的题（已修家族，记 `already_ok`）不再叫 A / B。
- **A 内置 AI 修复**：起真 `tavotto --desktop-sidecar`（`tests/support/foundation_app.sidecar_app`），经 `POST /api/ai/connections`
  存一条 OpenCode Go 连接（模型 `deepseek-v4.1-flash`），对失败的准备会话 `POST /api/ai/fix`，自动回答确认卡（approve 一律 yes，`pick` 类答不了记为 `unanswerable_card`），
  轮询到 `finished`。轨迹取 sidecar 数据目录里的修复审计 `ai/fix_audit.jsonl`（每次工具调用一行：工具、档、参数摘要、批准人、结果码；内容不落盘）。
- **B 编码 agent + Tavotto MCP**：`opencode run --standalone -m opencode-go/deepseek-v4.1-flash`，Tavotto MCP 指向与判分 sidecar **同一份** Tavotto 状态
  （数据 / 配置 / HOME 同一个 work 目录），所以 agent 在 Tavotto 里记下的项目设置（脚本参数、数据重映射、改入口）判分时算数。
  任务说明只有一句：这个项目里的 X 脚本在 Tavotto 里打不开 / 不出图，让它在 Tavotto 里能出图，不许修改原有文件。
  权限不替它拦「改原文件」，由哈希比对判定。
- **C 组**（Codex CLI）：只预留接口（`harness.py run C` 直接退出），本次不跑。
- **轨迹**：每次运行一行 JSONL，字段有 `id family cond rep success stuck before final originals_changed new_files trajectory steps tokens_in/out wall_s_total timed_out agent_errors`。
  `trajectory` 是工具调用序列（A：审计行；B：opencode 事件里的工具名、参数摘要、结果码）。`stuck` 是首个卡点的粗分类，规则在 `classify_stuck`。

## 跑法

```sh
export PYTHON=/path/to/python                 # 装好 tavotto 依赖（matplotlib 等）的解释器；源码树经 PYTHONPATH 生效
export EVAL_A_API_KEY=...                     # 只有 A 组需要（缺省读 OPENCODE_GO_API_KEY）；只经环境变量，不进任何文件 / 日志 / 轨迹
export EVAL_MODEL=opencode-go/deepseek-v4.1-flash   # A 组用的「厂商 id/模型」（厂商 id 取 ai_catalog.json，如 openai-api/gpt-6-luna）
export EVAL_RESULTS=$PWD/scripts/eval/fix_compat/results/<日期-模型>   # 每轮结果单独一个目录，不和别的轮次混
python scripts/eval/fix_compat/harness.py list
python scripts/eval/fix_compat/harness.py probe                 # 未修前的真实结局（不叫模型）
python scripts/eval/fix_compat/harness.py run baseline          # 不修，只判
python scripts/eval/fix_compat/harness.py run A --reps 1
python scripts/eval/fix_compat/harness.py run B --reps 1 --timeout 900
python scripts/eval/fix_compat/harness.py report                # = summarize.py：题 × 组成功率表、卡点计数、工具统计
```

B 组用 opencode 自己已登录的 OpenCode Go 凭据（`opencode auth`），harness 不读它。结果追加到 `results/<cond>.jsonl`，同一 (题, rep) 已有就跳过，可断点续跑。
基础设施故障（sidecar 起不来等）打印 `INFRA_ERROR` 不记结果；模型额度用完（输出里有 `usage limit` / `go-limit`）停下，不记那一组。
opencode 报错时退出码也可能是 0，`agent_errors` 里查 `Error:`。

## 费用口径

单次 A 运行：内置循环有步数 / 时间预算（`FIX_LIMITS`），一次几万 token 量级；单次 B 运行：opencode 一般 20–40 次工具调用、几十万 input token（含缓存）。
记录里有 `tokens_in / tokens_out`（B 来自 opencode 的 step_finish；A 的 token 内置循环不暴露，只有步数）。全部是 `deepseek-v4.1-flash`，按 OpenCode Go 套餐的额度计，不按美元。

## 怎么读结果

- 看 `report` 的第一张表：每题每组 `成功数/运行数`。`already_ok` 的题两组都算成功（没叫模型），合计里单独标；`solvable=false` 的对照题不进合计。
- 看 `stuck` 计数找共性；再翻对应 JSONL 行的 `trajectory` 看是哪一个工具、哪一步。
- 失败但 `final.phase` 是 `completed` 而 `originals_changed` 非空 = 图出来了但改了原文件（违规，不算成功）。


## 题库清单与未修前的真实结局

用真 sidecar（桌面来源）对每题跑一次准备会话得到（`harness.py probe`，Tavotto 0.21.2 源码树 0a676aabd）：

| 题 | 家族 | 未修前的真实结局 |
|---|---|---|
| savefig-stat-readback | 读回（ADR 0127） | `completed` 出图（safe 被拦 `safe_file_operation_blocked` → 静默回退 native），已修家族，不叫模型 |
| savefig-pil-readback | 读回 | 同上 |
| lib-zero-arg-no-main | 库式模块 | `action_required` / `no_figure_captured`（safe 先被 `safe_file_operation_blocked` 拦，native 跑完仍没图） |
| lib-needs-data-arg | 库式模块（ADR 0188） | `partial` / `no_figures_captured`；**对照题**：目标脚本没有数据来源，不改它就出不了图 |
| data-moved-away | 数据被挪走（ADR 0106） | `action_required` / `missing_input` |
| argv-unpack | 脚本参数 | `action_required` / `script_needs_arguments` |
| argparse-required | 脚本参数 | `action_required` / `script_needs_arguments` |
| backend-tkagg | 后端写死 | `action_required` / `missing_dependency`（无 tkinter） |
| stdin-prompt | 交互输入 | `action_required` / `script_needs_input` |
| env-var-required | 环境变量 | `action_required` / `script_error`（KeyError） |
| relative-import | 包结构 | `action_required` / `script_error`（相对导入） |

**造过但删掉的题**（探测结局如实记在这里，免得以为没覆盖是漏了）：
`tmpfile-replace-readback`（os.replace 读回，本来就出图）、`abs-out-dir`（savefig 到不存在的绝对目录，safe 下本来就出图）、
`private-deps-syspath`（`../shared_deps` 插进 sys.path，本来就出图；ADR 0153 的「读不了」形状要 chmod 000，JSON 题库造不出）、
`missing-pkg-yaml`（缺包，结局是 `awaiting_confirmation` / `dependency_preparation_required`——走依赖准备流程，不是修复入口）、
`subdir-cwd-data`（结局 `awaiting_configuration` / `workdir_confirmation_required`，要先确认工作目录）。
所以任务点名的「缺 import 名≠包名的包」这一类**没有覆盖**（要联网装包，且结局不走 `run`）；数据相对路径只剩 `data-moved-away` 一题。

## 基线结论

> **本轮只有 B 组跑了；A 组（内置 AI 修复）没跑成**：环境里没有 `OPENCODE_GO_API_KEY`，按规矩不去翻别的凭据。A 组的卡点不能靠推测写，下面不写。
> 补跑：`export OPENCODE_GO_API_KEY=...; python scripts/eval/fix_compat/harness.py run A`。A 组的接线（连接、确认卡、审计轨迹、判分）已写好，但**没有用真模型验证过**。

每题每组 1 次（`deepseek-v4.1-flash`，Tavotto 0.21.2 源码树）。分母只算有区分度的 8 题（不含 2 道本来就出图的、1 道对照题）。

| 组 | 成功 | 说明 |
|---|---|---|
| baseline（不修） | 0/8 | 另有 2/2 本来就出图，对照题 0/1 |
| B：opencode + Tavotto MCP | **2/8** | 对照题 0/1；平均 39 次工具调用、281 s、输入 token 4.4 万 |

B 的明细（行号 = `results/B.jsonl` 的行，从 1 数）：

- 成功 2 题，**都不是「原脚本被修好」**：`lib-zero-arg-no-main`（第 3 行）agent 手写了 `tavotto_registry.json`，给 `figlib.py` 登记 `entry: make_figure`，Tavotto 才认得这个零参函数；`data-moved-away`（第 5 行）agent 把 `archive/2024/results.csv` 复制成 `data/results.csv`（新增文件、原文件没动）。
- 失败 6 题（另加对照 1 题），首个卡点分类全是 `worked_around_with_new_script`：agent 新写了 `fig_tavotto.py` / `fig_fixed.py` 之类的替身脚本，用它在自己的 Tavotto 会话里把图打开、规范化、导出，**图在 Tavotto 里出来了，但原目标脚本照旧跑不起来**（`final.outcome_code` 仍是 `script_needs_arguments` / `script_needs_input` / `script_error` / `missing_dependency`）。这正是判据（原脚本字节不变、再跑一遍要出图）要抓的：用户打开的还是原来那个脚本。
- 参数类两题（第 6、7 行）：agent 给 `tavotto_open_figure` 传了 `argv`（MCP 有这个参数），会话里图开了，但 `argv` 只活在那次 MCP 会话里，不进 Tavotto 的项目设置，判分用的新准备会话看不到。**判据对「参数」家族偏严**：真正的修法是把参数记进项目设置，B 组拿不到这个动作。读结果时请把这两题单独看。
- `backend-tkagg` / `stdin-prompt` / `env-var-required` / `relative-import`（第 8–11 行）：脚本必须被改，或运行环境得有人给（后端、标准输入、环境变量、包上下文）才能跑；B 组的 Tavotto 工具里没有「给脚本设环境变量 / 换后端 / 按模块运行」的动作，agent 只能写替身脚本。这四题产品侧有没有出路，要等 A 组结果对照才知道。
- 行为统计：`shell` 154 次、`tavotto_open_figure` 57 次（第一次几乎都报「没有任何已登记且产物在磁盘上的图。先把脚本跑一遍」）、`tavotto_close_session` 29 次、`tavotto_refresh_project` 21 次——与 `agent_journal` 的结论一致，agent 要靠多次 open / close / refresh 才摸到登记表这条路。
- 产品侧发现（只记不修）：没有登记表时，零参函数 `make_figure()` 的库式模块不会被自动当成入口（真 sidecar 里 `no_figure_captured`），手写 `tavotto_registry.json` 登记 `entry` 后就出图了。这条与 ADR 0127 / 0188 相关；本树里没有找到任务说明提到的 `test_native_function_entry_e2e.py`。
- 对照题 `lib-needs-data-arg`（第 4 行）：B 组没有编数据糊弄，而是新写了 `figlib_figures.py` 去调用函数（数据来自同目录的 `data.csv`，其实是对的出路），原目标脚本照旧不出图，判失败符合预期。

内置 AI 修复（A）的结论：**未测**。

## 已知局限（如实）

- B 组的隔离不严：agent 的 shell 没有沙盒，部分运行读过项目目录以外的路径（`lib-zero-arg-no-main` 4 次、`argparse-required` 7 次、`backend-tkagg` 2 次、`stdin-prompt` 1 次涉及评测目录或 Tavotto 源码）。已把项目放在与日志分开的目录、opencode 配置走环境变量而不是项目里的文件，但没做到真隔离。
- B 组的 MCP 与判分 sidecar 共用同一份 Tavotto 状态，所以 Tavotto 里记下的设置判分算数。agent 的 `tavotto_health` 报「画布资源缺失」是因为源码树没有构建画布产物，与本评测无关。
- 每题每组只跑 1 次，单次结果有随机性；不要用 1 次的差异下结论。
- 成功判据对「会话里改好、没有持久化到项目 / 项目设置」的修法一律判失败（见上面参数类说明）。
- 跑 A 组前后请对共享解释器做一次 `pip freeze` 对比（确认卡一律同意，内置修复可能在 Tavotto 管理的环境里装包），审计里出现 `user_env` 档调用要核对；跑完 `grep` 一遍 `results/` 与运行目录，确认没有密钥（harness 已把 sidecar 环境里的密钥变量置空，并在 A 跑完后删除连接）。

## A 组基线（2026-11-19，内置 AI 修复，deepseek-v4.1-flash，每题 1 次）

| 题 | A 内置 AI | B opencode+MCP | A 的结局 / 卡点 | 对应的补能力 |
|---|---|---|---|---|
| lib-zero-arg-no-main | ✗ | ✓（手写登记表绕过） | 用完预算（27 步） | #2044（native 调函数入口）、#2048（启动脚本） |
| lib-needs-data-arg（对照） | ✗（预期） | ✗（预期） | 正确指出真正画图的是另一个脚本 | —— |
| data-moved-away | ✓ 12 步 | ✓ | `link_data_file` 接上 | —— |
| argv-unpack | ✗ | ✗ | 推断出参数，却只能让用户自己填 | #2050（`set_run_arguments`） |
| argparse-required | ✗ | ✗ | 劝用户改脚本加默认值 | #2050 |
| backend-tkagg | ✗ | ✗ | 用完预算（31 步） | 待做：启动脚本可先设后端；或产品侧中和写死的交互后端 |
| stdin-prompt | ✗ | ✗ | 「窗口外没人能回答」 | 待做：启动脚本可喂 stdin；提示词要点名 |
| env-var-required | ✗ | ✗ | 知道缺 `LAB_DATA_DIR`，却「这里改不了」 | #2048（启动脚本设环境变量） |
| relative-import | ✗ | ✗ | 判成产品缺陷 | 待做：启动脚本按包导入；提示词要点名 |

A 组 1/8，B 组 2/8（B 的两题都是绕开、没有修原脚本）。**A 的失败几乎全是「知道原因、没有动作可做」**，不是推理不到：
argv 两题、环境变量、stdin 都在结束语里说对了原因。合入 #2044 / #2048 / #2050 后重跑这一套量改进。


## 11-19 第二轮：合入 #2044 / #2048 / #2050 / #2053 / #2054 / #2055 之后

结果目录：`results/2026-11-19-integration/`（不修）与 `results/2026-11-19-gpt-6-luna/`（A 组，`EVAL_MODEL=openai-api/gpt-6-luna`）。每题 1 次，分母同上 8 题。

| 组 | 成功 | 说明 |
|---|---|---|
| baseline（不修） | **3/8** | 只定义画图函数（#2044）、写死 GUI 后端（#2054）、相对导入（#2055）不用 AI 就出图 |
| A：内置 AI 修复（gpt-6-luna） | **8/8** | 原文件全部零改动；对照题也「修好」，见下 |

读这组数字要注意：

- **换了模型，不是同口径**：基线 A 组没跑成，B 组用的是 `deepseek-v4.1-flash`，这一轮 A 组是更强的 `gpt-6-luna`。与模型无关、能确定的只有两条：不修就出图从 0/8 到 3/8；参数类两题有了 `set_run_arguments` 这个动作。同口径的弱模型 A 组还没跑。
- **对照题 `lib-needs-data-arg` 也「修好」了**：AI 没编数据，而是写了一行启动脚本去运行项目里真正的入口 `run_report.py`，于是那张图挂在了 `figlib.py` 名下。维护者 11-19 裁决这种情况应引导用户去打开真入口，不用启动脚本代跑（ADR 0188），提示词随 ADR 0197 第 1 层一起改。
- 8/8 已经到顶，这套题量不出后续改动的提升，还要更难的题、弱模型、每题 3 次，并把步数和耗时也算进指标。
