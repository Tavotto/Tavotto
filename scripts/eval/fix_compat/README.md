# 兼容性修复评测：内置 AI 修复 vs 编码 agent + Tavotto MCP

问题：同一份在 Tavotto 里跑不起来 / 不出图的脚本，Tavotto 内置的 AI 修复（A）和通用编码 agent 加 Tavotto MCP 插件（B）谁能修好、卡在哪。
结构、跑法、结果格式沿用 `scripts/eval/agent_journal/`。不进 CI，不在 PR 里跑；被测模型要额度。

- **题库**：`corpus.json`，11 题脚本兼容 + 4 题环境故障（见下「环境故障家族」），每题是一个小项目（脚本和数据都是合成的，不含任何真实用户内容），形状来自 Tavotto 已修过 / 在修的兼容性家族。
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

## 提示看护与「提示跟进」表（ADR 0197 第 3 层·离线）

- 未修前还失败的题带 `hint: {code, trace?, tool}`：实测的结局码（traceback 才认得出的形状带一段 `trace`）与修好它要用的工具；
  已经不用 AI 就出图的题不带。`tests/test_fix_compat_corpus_hints.py` 在普通 CI 里按 `results/` 最近一轮 `baseline.jsonl`
  核对这两条，再核对每道题都拿得到点名那个工具的提示，不叫模型。每轮新 baseline 进仓库后若对不上，按实测更新题库；
  `harness.py probe` 对不上时也会打印 `!!`。
- A 组轨迹里应用试运行那一行带 `attempt`（`n / before / after / hint / changes`，只有码）。`report` 最后一张表按每次试运行列出
  给的提示、模型接着动的第一个非观察工具、下一次的失败形状——给了提示没照做、照做了形状没动的行，就是该改写或新增提示的地方。

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

## 环境故障家族 `environment`（ADR 0221 §六）

脚本兼容家族之外，量「装包 / 受管环境」这条线上的修复：4 题，脚本与数据合成、不含真实用户内容，包名一律 `tavfx-*`（PyPI 上没有，只会从本地 find-links 目录找到）。

| 题 | 模拟的故障 | 怎么造 | 未修前（一键安装）的稳定错误码 |
|---|---|---|---|
| env-selftest-blocked | 装上了，受管环境自检被写入守卫拦了自己的输出（TVD-FTH9 形） | **产品里唯一的注入点**：`TAVOTTO_DEV=1` + `TAVOTTO_DEV_FAULT_SELFTEST_BLOCKED=1` 时 `worker_self_test` 回 `error_reply` + `safe_file_operation_blocked` | `dependency_worker_selftest_failed` |
| env-wheel-python-mismatch | 包只有别的 Python 小版本的 wheel（phreeqpython 形） | 假 wheel，标签是与本解释器不同的 cp 小版本；不改产品 | `dependency_not_found`（`--only-binary` 下「没有适合的 wheel」与「没这个包」同码，这正是 ADR 0221 §三 要补的信息） |
| env-import-dll-fail | 包装上了，import 时 `ImportError("DLL load failed")` | 假 wheel：2.0 import 即抛、1.9 是好的；不改产品 | `dependency_import_still_failed` |
| env-permission-denied | 包 import 时目录 `PermissionError`（HZ0A 形） | 假 wheel：没设 `TAVFX_CACHE` 就抛 `PermissionError`（**模拟**：不在本机真改目录权限，root / Windows 上 chmod 不可靠）；设了即好 | `dependency_import_still_failed` |

- **没做的**：Conda 激活失败、镜像不通回退。前者要伪造一个带 `conda-meta/` 的环境加一个会失败的 conda，且项目要先指到它（`launcher` 只对「可信 conda 安装根」下的环境激活）；后者要拦网络。两个都不改产品也许做得出来，但这一期没验证过，不放没验证的题。
- **故障注入只在开发里**：`src/tavotto/engine/devfault.py`，`TAVOTTO_DEV=1` **且**点名的 `TAVOTTO_DEV_FAULT_<名>=1` 才生效，名字是闭集；其它任何组合都走产品原路径。这些变量只由 harness 写进被测 sidecar 的环境，不要在日常 shell 里 export。注入点数量与「不设就不生效」由 `tests/test_dev_fault_injection.py` 看护。
- **夹具构建期生成**：`envfaults.py` 在每次运行的隔离目录（`$EVAL_RUNS/<组>/<题>__<rep>/wheelhouse`）里现造 wheel，不提交二进制；sidecar 的 `PIP_FIND_LINKS` 指过去，**不关 PyPI**（受管环境还要装 matplotlib，所以跑它要联网）。用户自配了包源，Tavotto 不走镜像回退，这与线上行为一致。
- **未修前**：`harness.py probe --only env-…` 起真 sidecar，开准备会话（`missing_dependency`），再对缺的包走 `POST /api/engine/dependency/plan` → `install`（装进 Tavotto 受管环境，与界面「装上并继续」同一条后端路由），记 `before.install.code`；与题库 `fault.expect.code` 对不上会打印 `!!`。每题约 20–40 秒，不叫模型。
- **跑法**：`harness.py run baseline` / `run A`；B、M 组不支持环境题（MCP 与 warmup 接不上假 wheel 夹具），`run B` 会自动跳过。A 组的修复会话从「一键安装没装好」之后开始（`install_attempt` 记在结果里）；**这条接线没有用真模型验证过**。
- **修复会话看不到安装失败（产品如此，评测不造状态）**：题库的包名不在内置映射里，准备会话对它只给「指定包名 / 换环境」、没有「安装依赖」动作，用户点的是独立依赖路由，它的作业不记进准备会话。评测照这条路走，`install_attempt` 的失败码只记在结果里；修复助手从「缺包」出发，得自己走到故障。
- **成功判据不变**：原脚本再建一次准备会话、点运行，`completed` 且出图，原文件字节不变。对 `env-wheel-python-mismatch`，本机没有对应的 Python 小版本时，没有出路，题库里标了 `solvable: false`（对照题，不进合计、单列；不按运行机有没有对应解释器动态判定，那会让同一题在不同机器上进出分母、跨机器不可比）。
- **两列**（`report` 最后一张表，每题每组）：
  - `cited_known_issue`：轨迹里有没有任何一行带 `known_issue`（harness 从审计结果的 `known_issue` 键搬进轨迹）。现在的产品审计里还没有这个键（ADR 0221 第 2 期才加），所以眼下全是 0，不会报错。
  - `misjudged_user_fault`：**近似规则**，审计只留码和长度、不留模型的结论原文：① `prepare_diagnostics` 带 `issue` 却被退回（`bad_arguments`，`product_reason` 为空是其中一种；或 `not_a_product_defect`）；② 题库标了 `fault.product_defect`（目前只有 `env-selftest-blocked`）、这次没修好、整条轨迹里又没有一次成功的 `prepare_diagnostics`——当成用户环境问题收尾了。只对 A / M 组有意义。
- 不进 CI、不在 PR 里跑；每期 ADR 0221 的 PR 合入后跑一轮 baseline + A，记到 `results/<日期-模型>/`。

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

## 11-21 第三轮：失败提示、试运行记录、小实验都在（#2064 / #2079 / #2093 之后）

`results/2026-11-21-deepseek-flash/`，Tavotto 源码 8ddcb9432（main + #2093）。A 组走 Tavotto 内置的 DeepSeek 连接（`EVAL_MODEL=deepseek/deepseek-flash`），**每题 3 次**；先跑一轮 baseline。

| | baseline | A |
|---|---|---|
| 要 AI 动手的 5 题（不含已经出图的题和对照题） | 0/5 | **15/15** |
| 对照题 `lib-needs-data-arg`（solvable=false） | 0/1 | 0/3 |

- 平均 9.3 步、44 s 一题；修好用的工具：`set_run_arguments` ×6、`write_launcher` ×6、`link_data_file` ×3，`run_snippet` 用了 5 次。
- 「提示跟进」表里每次应用试运行都是第一次就 `ok`：没有出现给了提示却重复同一失败的情况，本轮没有可提炼的新提示。
- 对照题 3 次都按 ADR 0188 的裁决收尾：说明 `figlib.py` 只是画图工具、请用户打开同文件夹的 `run_report.py`，不代跑。判分算不成功是对照题的设定。
  第 1 次被归成 `tool_denied:run_snippet`，其实是模型先猜错了环境 id（`env_unknown`），查过 `list_environments` 后就照常跑了，不是被拒。
- **不能直接和 11-19 比**：11-19 的 A 组是 OpenCode Go 的 `deepseek-v4.1-flash`、每题 1 次，代码里还没有失败提示、试运行记录与小实验；本轮换了模型和连接方式。
  要分清提示本身的作用，需要同一模型在 #2079 前后各跑一轮（未做）。

## 11-23 第四轮：本机记住修好的做法有没有用（M 组，ADR 0197 第 3 层·在线）

`results/2026-11-23-deepseek-flash-memory/`，模型 `deepseek/deepseek-flash`（与 11-21 那轮相同），每组每题跑 3 次。

- 新增 3 道「乙」题：`env-var-required-2`、`argparse-required-2`、`stdin-prompt-2`。每道题与一道已有的「甲」题失败类型相同，但脚本、参数名、数据文件都换了。
- 新增 **M 组**（`harness.py run M`）：在同一个数据目录里先修「甲」（`warmup`），本机记下的做法留在 `work/data/ai/fix_memory.json`，再修「乙」。
  - 记录里有 `warmup`（甲修没修好）和 `memory_before`（修乙之前记住了哪些失败类型、哪类做法，只有类型，不带值）。
  - 轨迹只取修「乙」那一段。
- **A 组**：「乙」在全新的数据目录里修，用作对照。
- `report` 最后一张表对比两组修「乙」的成功数、平均步数和修复用时。修复用时取 `wall_s`，只算修这一题；M 组的 `wall_s_total` 含修「甲」的时间，所以不用它。

| 乙题 | A 成功 | A 平均步数 | A 修复用时 s | M 成功 | M 平均步数 | M 修复用时 s | M 里甲修好 / 记下做法 |
|---|---|---|---|---|---|---|---|
| argparse-required-2 | 3/3 | 7.7 | 17.2 | 3/3 | 8.7 | 12.2 | 3/3 · 3/3 |
| env-var-required-2 | 3/3 | 13.0 | 17.7 | 3/3 | 11.7 | 17.3 | 3/3 · 3/3 |
| stdin-prompt-2 | 3/3 | 12.7 | 138.2 | 3/3 | 7.3 | 11.0 | 3/3 · 3/3 |

- **结论**：两组 9/9 全修好。在这批一次就能修好的题上，看不出「记住的做法」让修复明显变快。
  - `stdin-prompt-2` 两组的差别，主要来自 A 组一次失常：24 步、4 次 `run_snippet`、381 秒。去掉这一次，两组都是 7～8 步。M 组 3 次都稳定在 7～8 步。
  - 要量出作用，得用模型第一次修不好、或要绕好几步的题，目前的题库里还没有。
- **评测抓到的 bug（#2135）**：第一次跑 M 组时，`argparse-required-2` 3 次的「甲」都修好了，本机却一条做法都没记下（`memory_before` 为空）。
  - 原因：AI 先用 `set_run_arguments` 设参数，准备报告随即被重置，试运行前读不出失败形状。于是试运行记录的 `before` 为空，`_remember_fix` 拿不到失败类型，`fixmemory.remember` 整条跳过。
  - 缺环境变量、键盘输入两对不受影响，因为写启动脚本不会重置报告。
  - 那 3 次作废：成功 3/3，步数 8 / 9 / 8，没有记下做法。之后在带着 #2135 修复的代码上重跑了这一对的 M 组 3 次，表里是重跑的数。
