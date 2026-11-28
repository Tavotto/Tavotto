# ADR 0221：内置修复 Agent 修环境——看得见自检、认得已知问题、查得到包事实、以真实运行为准

日期：2026-11-28 · 状态：**Proposed（定契约与分期；实现见 §八）**
**1.0 收敛纪律：§一 §二 §五 §六 属 correctness（让已有修复会话不再在 Tavotto 自身缺陷上空转）；§三 §四 扩大修复能力，维护者 2026-11-28 立项（「这些都开始做」），豁免待登记 `docs/1.0-release-readiness.md` §1.8。**
相关：[0149 AI 修复工具箱](0149-fix-toolbox.md)（注册表、档位、读取范围、审计）、[0147 内置执行器](0147-builtin-agent-runner.md)、
[0019 受控依赖修复](0019-controlled-dependency-repair.md)（「pip exit 0 ≠ 修好」、自检不过不切 active）、[0141 写入绊线](0141-mcp-write-root-tripwire.md)；
`docs/rules/backend/dependency-repair-and-packages.md`、`docs/rules/backend/ai-agent-bridge.md`、`docs/rules/backend/diagnostics.md`；PR #2370（自检布局 + 被拦操作进诊断包）。

## 问题

2026-11-28 两封 Windows 0.21.3 来信（TVD-FTH9、TVM-C7RT）：装包 pip 成功，受管环境自检报 `dependency_worker_selftest_failed`。
FTH9 用户开了 AI 修复：约 4 分钟里建了两代受管环境、换 Python、回滚，最后的结论是「可能是环境检查问题，现有日志未说明具体原因」——**判断是对的，但修不了**。
原因不在模型，在工具面：

1. **看不见 Tavotto 自己的检查**：自检没过时修复会话只拿到错误码 + 最多 800 字输出（FTH9 那次为空）；`selftest.stage` / `worker_code` / 被拦操作只进诊断包，「看」组没有一个工具读得到。
2. **代理检查是死路**：自检是一张极简 matplotlib 图，不是用户的脚本；它自己有缺陷时（#2370 的根因），用户脚本能不能跑无从验证，环境整代丢弃。
3. **不知道包事实**：`lookup_package` 只查离线映射表。phreeqpython 1.6.2 只有 cp312 wheel，Windows 受管环境是 3.13，`--only-binary` 下只能装到 1.5.7——模型无从得知。
4. **不认得已知问题**：同一个产品缺陷，每个用户的修复会话都从头查一遍、再报一遍。
5. **复现不了现场**：Lab 上复现不出（#2370 自述），用户机器上的临时目录、短文件名、杀软只在那台机器上看得见；`run_snippet` 禁写，模拟不了「守卫下跑一次自检」。
6. **没有环境故障的评测**：`scripts/eval/fix_compat` 11 题全是脚本兼容家族，环境故障一题没有，补了 1–5 也量不出变好没有。

## 决定

### 一、看得见自检：`read_environment_checks`（observe 档）

新「看」工具，回答「Tavotto 对这个环境做过哪些检查、哪一道没过、卡在哪一步、拦了什么」：
- 数据源是项目状态里已有的 `managed_environment_jobs`（与诊断包同一份，**不另建记录**）：最近 N（≤5）代的 `requested`、`outcome`、`error_code`、`selftest{stage, exit_code, worker_code, structure, engine_frame}`，以及 #2370 起的 `operations` / `fidelity`。
- 另加只给本机模型的 `detail_tail`：自检原文尾部（≤4 KiB），**经 ADR 0149 §三 同一套脱敏**——诊断包里没有原文，这里有，因为修复会话本来就能读本机日志（`read_logs` 同口径）。
- `install_packages` / `change_python` / `select_environment` 在自检没过时，结果里直接带同一份 `selftest` 形状 + 一句「用 read_environment_checks 看全」，不再只回错误码。
- 「看」组的一员：只读，不改档位表。

### 二、认得已知问题：随版本内置的已知问题表（observe 档，离线）

- `src/tavotto/resources/known_issues.json`：闭集条目 `{id, match{error_code, selftest_stage?, worker_code?, os?, versions?}, summary_zh, summary_en, fixed_in?, workaround{tools[], user_steps_zh, user_steps_en}}`。**只随版本发**，不联网拉取（不新增出站面；要远程更新另开 ADR）。
- 匹配只用结构化字段（码、阶段、平台、版本区间），**不按自由文本**；匹配器纯标准库，命中结果挂进 `read_run_report` / `read_environment_checks` / §一 的工具结果（`known_issue: {id, summary, fixed_in, workaround}`）。
- 模型拿到命中后的行为写进系统提示：先告诉用户「这是已知问题（X 版本修复）」，按 `workaround` 做；`prepare_diagnostics` 自动带 `known_issue_id`，不再当新缺陷描述一遍。
- 首条：`selftest-guard-blocks-own-output`（`dependency_worker_selftest_failed` + `error_reply` + `safe_file_operation_blocked`，windows，≤ 0.21.3，`fixed_in` 为 #2370 所在版本），workaround = 换用用户自己的 Python（#1934 的第三目标）或 §四。
- 看护：表的 schema 测试；每条 `match` 至少有 `error_code`；`fixed_in` 不得早于 `versions` 上界。

### 三、查得到包事实：`check_package_availability`（outward 档，每会话首次问用户）

回答「在**这个环境**里装 X 会得到哪个版本、为什么不是最新版、换哪个 Python 能拿到最新版」：
- **以真实安装器为准**：在目标环境里跑 Tavotto 安装器同一套参数的 `pip install --dry-run --only-binary=:all: --report -`（同一镜像 / 索引选择与回退，**不另写一套 wheel 标签判断**），取它会装的版本。
- 再读索引的 simple 页（PEP 503/691，镜像也支持；与安装器同一索引）列出版本与各版本 wheel 的 Python / 平台标签，算出「最新版要求的 Python」。
- 输出闭集：`{would_install, latest, latest_requires{python_tags[], platforms[]}, reason: up_to_date|no_wheel_for_python|no_wheel_for_platform|not_found|index_unreachable, suggest: change_python(<版本>)?}`。
- 出站只发包名（pip 装包本来就发）；按 ADR 0149 归 `outward` 档：会话里第一次用时弹卡片问用户，之后同会话放行；结果按（索引、包名）缓存 1 小时于 `data_dir()/cache/`。
- 离线映射 `lookup_package` 保持不变。

### 四、以真实运行为准：自检没过时「用你的脚本验证后采用」（tavotto 档，问用户）

第三路径审查（`docs/rules/repo/third-path-review.md`）：用户要保持的不变量是「**被采用的环境真能把用户的图跑出来**」，不是「极简图能跑」。自检是这条不变量的代理；代理本身出错时，用**更强**的判据替换它，而不是放松判据：
- 自检没过的那一代**不再立即丢弃**，标 `candidate_after_selftest`（不切 active、不进池、不被别的项目选到），保留到本次修复会话结束（会话结束 / 取消 / 超 2 小时即清）。
- 新工具 `verify_candidate_environment(env)`：在候选代里用 `try_run` 同一条受守卫路径跑**用户的目标脚本**；判据 = 真实运行 `phase == completed` 且捕获到 ≥1 张图，且没有被守卫判 `safe_file_operation_blocked`。过了才弹卡片问用户「用这个环境」，用户点头再切 active。
- 采用记 `adopted_over_selftest: true`（项目状态 + 诊断包 `managed_environment_jobs[]` 新字段，升 bundle schema）+ 已知问题 id（若命中），便于统计这条路被走了多少次。
- 不适用：自检 `spawn_failed` / `launcher_unavailable`（解释器根本起不来）、`timeout`；内置 runtime 与用户显式选过的环境（照 0019 不碰）。
- ADR 0019「pip exit 0 ≠ 修好」「自检不过不切 active」不变：切 active 仍以一次真实运行为前提，只是换成了用户的脚本。

### 五、现场复现：`run_environment_selftest(env)`（run 档）

- 在用户机器上按产品同一条 `worker_self_test` 跑一次自检，**观察模式**：守卫照常拦，但把账本（被拦操作的闭集类别、相对 out / sandbox / 项目 / 临时目录的位置类别、次数）和 worker stderr 尾部（脱敏）全部带回。
- 同时报本机路径事实闭集：临时目录是否 8.3 短名、是否含非 ASCII、是否在同步盘 / 重定向目录下、路径长度是否逼近 260；这些只回布尔 / 枚举，不回路径原文。
- 只读：不改环境、不切代；子进程照 ADR 0149 §三 同一条受守卫执行路径。

### 六、环境故障评测：`scripts/eval/fix_compat` 加 `environment` 家族

- 新题用**仅开发可用的故障注入**（环境变量 `TAVOTTO_DEV_FAULT_*`，只在 `TAVOTTO_DEV=1` 时读，产品构建里没有入口）：自检误报（守卫拦自己的输出）、只有别的 Python 版本的 wheel、包装上但 import 时动态库失败、Conda 激活失败、目录权限被拒（HZ0A 形）、镜像不通回退。
- 判据沿用该评测的唯一成功判据（原脚本真跑出图、原有文件不变）；另记「是否引用已知问题」「是否误判为用户问题」两列。
- 不进 CI（要模型额度）；每期 PR 合入后跑一轮记基线，结果放 `scripts/eval/fix_compat/results/`。

### 七、不变的东西

- 内置 runtime 永不是安装目标；用户显式选过的环境不碰；写入绊线与 `Path.unlink` 守卫不放松（§五 只是观察）。
- 诊断包不进路径原文；新字段全是闭集 / 计数 / 布尔；report.json 换形必升 bundle schema。
- 工具只登记进 `fixtools.REGISTRY`，内置执行器与 MCP 接法共用；Codex 插件侧要不要暴露另议。

## 八、分期

| 期 | 内容 | 依赖 | 风险档 |
|---|---|---|---|
| 1 | §一 `read_environment_checks` + 改组工具结果带自检形状 | #2370 合入 | high（工具面 + 环境） |
| 2 | §二 已知问题表 + 匹配器 + 挂进三处结果 + 首条 | 1 | medium |
| 3 | §六 评测家族 + 故障注入（先于 3–5 期落地，给它们量基线） | 无 | low（开发侧） |
| 4 | §三 `check_package_availability` | 无 | high（出站） |
| 5 | §五 `run_environment_selftest` | 1 | high |
| 6 | §四 候选代 + `verify_candidate_environment` + bundle schema | 1、5 | high |

## 后果

- FTH9 / C7RT 这类会话：第一步就能看到「自检被守卫拦、命中已知问题」，直接给出绕过办法，或用用户脚本验证后采用，不再白跑四分钟。
- 代价：多一个出站工具（§三，只发包名、首次问）；候选代占磁盘到会话结束；已知问题表要随版本维护（发版清单加一项：核对 `fixed_in`）。
