# Compatibility Guardian —— 兼容性证据 runner、Agent 接口与缺口报告

S0 的取样与观测见 [S0_BASELINE.md](../implementation/script-compat/S0_BASELINE.md)。
科研任务登记在独立的 `research-manifest.json`，普通 CompatBench 保持 149 项；只有显式选择科研 case
时才加载科研登记。上游源文件按完整提交 SHA 拉取到仓库外，并逐文件核对 `research-cohort.json` 的
SHA-256。Guardian 同时核验登记与 HEAD 一致、缓存字节一致，之后复用已有 worker 测量路径。

`compat_guardian_research.py verify` 只核验元数据；`fetch --case ... --cache ...` 显式下载，不执行脚本。
每个项目的依赖在独立 hosted job 中准备。原始 `research-baseline.json` 保留，完整证据由 Actions
artifact / 外部归档保存；`verify-snapshot --evidence-directory ...` 可从归档重算快照。
`runner_error` 保留为 unknown 观测及原始异常，不进入产品六分类；SVG 校验使用标准库 XML。

研究 case 的产品腿按登记的 `reference_invocation` 起脚本：argv 经 `runconfig.selection_for` 成为产品的运行配置，
cwd 经 `workdir.set_mode` 成为项目的工作目录模式（脚本目录或项目根），与原生腿同源。脚本、原生腿与 worker 跑在 `TAVOTTO_SCIENCE_PYTHON` 指定的科研 venv（未设则用当前解释器），Guardian 与 Tavotto 依赖留在自己的 venv，对应产品的进程边界；`verify-science-environment` 断言科研 venv 没有 RenderCore 包。登记的目录两者都不是时，
产品腿在 discover 阶段以 `launch_context_unsupported`（`cwd_or_data_path`）失败，报告带登记的 cwd 与可表达的模式，不在别处运行，也不是 `runner_error`。报告的 `raw.line_data` 只读比较原生曲线数组与产品 manifest 的
`data_fingerprint.parts.xy`，是诊断证据，不影响 Guardian 的成功判据，也不代替桌面/CLI/native 用户路径验收。

本文件是 Guardian 的**唯一**架构/接口文档：CG-00 审计汇总、CG-01 runner 的契约、给 Agent 的调用
接口都在这里，后续 PR（CG-02 起）引用本文，不再另写总架构文档。

Guardian 的目标一句话：让尽可能多的科研用户的 matplotlib 脚本**第一次**就跑通、并进入真正可用的
编辑流程；做法是 Agent 可调用的「发现 → 复现 → 诊断 → 最小化 → 聚类 → 修复 → 验证 → 沉淀」闭环。
**CG-01 只交付闭环的第一块：可信的、可机读的「原生 vs Tavotto」证据 runner。**

它不替代 CompatBench、`agent_verify`、worker、环境解析或现有 CI 门禁；它复用它们，并明确区分两个维度：

- **这次测试跑成了吗**（run outcome：`passed / failed / expected_limitation / not_reproduced /
  environment_unavailable / not_run / invalid_fixture / unknown`，外加本文定义的 `partial_coverage`）；
- **Tavotto 对这类脚本支持到哪一级**（CompatBench 的六类产品分类，原样保留在 `product_classification`）。

---

## 1. 它保证什么、不保证什么（措辞以此为准）

**代码能保证的**

- `run` 只收登记在册的 case id（`compat_engine` 的 manifest、`compat_v2` 的场景表），**没有「跑任意脚本」的入口**。
- 被执行的每个夹具文件，字节都**等于当前检出的 HEAD 里提交的字节**（`git show HEAD:<path>` 比对），
  执行元数据（manifest、场景表）同理；运行期间整棵源码树（含未跟踪文件）内容摘要不变，变了就不发布报告。
- 报告绑定 HEAD、树内容摘要、是否 dirty、环境指纹；证据文件按 sha256 引用；`validate` 把报告与**当前**
  检出重新对一遍，旧 SHA / 旧树 / 被改过的证据文件都过不了。

**代码不能保证的（不要在任何文档、PR 描述、日志里写成保证）**

- 「夹具被评审过」。上面的比对是**自洽检查**，不是评审证明：在 PR 分支上，作者可以同时改夹具和 manifest，
  HEAD 就是新内容，检查照样通过。真正的评审边界是**合入 main 的评审**，由 `.github/CODEOWNERS` 里
  `tests/compat/**`、`tests/fixtures/compat_v2/**`、`scripts/ci/compat_guardian*`、本文档与 Guardian
  workflow 的显式条目提供（见 §9）。因此：PR 上的 Guardian 运行是「这个分支自己的夹具」的观察，不是
  「已评审夹具」的观察。
- OS 级隔离。HOME / 配置 / 数据 / 缓存 / TMP 指向每例临时目录、环境变量白名单、进程树超时回收，**都不是
  沙盒**：同 UID 的绝对路径照读照写，网络不拦。所以 Guardian 只在 GitHub 托管的临时 runner 上跑，
  永不进 self-hosted / `tavotto-lab`（§7）。
- 用户成功率。语料通过率 ≠ 用户首次成功率，每份汇总都会这么写。

## 2. 命令（机器可调用，稳定接口）

```sh
python scripts/ci/compat_guardian.py run --suite {compat_engine|compat_v2} [--case id1,id2] \
    --out DIR [--expected-sha SHA] [--timeout SEC]
python scripts/ci/compat_guardian.py validate DIR/RUN_ID/reports/guardian.json
python scripts/ci/compat_guardian.py summarize REPORT [REPORT ...] [--json-out F] [--markdown-out F] [--allow-partial-shards]
python scripts/ci/compat_guardian.py promote REPORT --case ID --out FILE     # 只出提案
python scripts/ci/compat_guardian.py explore --seed N --out DIR [--limit N] [--parent a,b] [--static-only] [--shard K/N] [--max-seconds S]
python scripts/ci/compat_guardian.py triage REPORT [REPORT ...] --out FILE [--allow-partial-shards]
python scripts/ci/compat_guardian.py minimize --report EXPLORE_REPORT --candidate ID --out DIR [--max-runs N --max-seconds S]
python scripts/ci/compat_guardian.py minimize --replay DIR/repro.json --out DIR2
```

- `--out` 必须在源码工作树之外；每次 `run` 建一个 UUID 目录：`reports/guardian.json`（原子写）、
  `reports/summary.md`（每 case 一行的表）、`evidence/<case>/…`（原始证据，按 sha256 引用）。
  校验失败时保留 `reports/guardian.unvalidated.json` 供诊断，`validate` 永不接受这个名字。
- 省略 `--case` = 跑该 suite 的全部登记 case。
- `explore` 不带 `--static-only` 时，执行前要求整个 `scripts/ci/` 与 HEAD 一致（`git status --porcelain`，含未跟踪文件）：
  生成、执行、取证的代码（变换、driver、matrix、执行观测器……）都在这里，脏工作区里改过的不会被当作评审过的模板执行。
  `minimize` 与 `minimize --replay` 执行前做同一道核对。
- `explore --shard K/N`（CG-09，1 ≤ K ≤ N ≤ 16，写法严格：`0/4`、`5/4`、`1/17`、`01/4`、`a/b` 都是用法错误，退出码 2）只跑去重后候选池的第 K 片：
  候选按 `candidate_id` 排序后取 `下标 % N == K-1`，所以同 seed 下 N 片的并集 == 不分片的全集、两两不相交、各片大小至多差 1，与候选在池里的顺序无关。
  与其它参数的组合：`--parent` / `--families` **先**过滤、再切片（总数与过滤条件记进报告，N 片必须一致）；`--limit` 只作用于本片（家族均衡选取照旧）；
  `--static-only` 照常（切片后全部只做静态检查）；与 `--candidates` 互斥（退出码 2）；片内为空则不发布报告（退出码 2）。
  报告多一块 `shard: {k, n, candidates_in_shard, candidates_total, filters}`；不带 `--shard` 的报告与以前逐字节同形。
- `explore --max-seconds S`（有限正数，非法值退出码 2）是整次 explore 的时间预算：预算花完后**还没开始**的可执行候选记 `outcome: not_run`
  （note 写 `time budget exhausted`，阶段全 `not_run`，执行次数 0），已执行的照常出报告；预算花完后不再开复跑确认（未确认的差异不会触发提醒）。
  报告多一块 `time_budget: {max_seconds, exhausted, not_run}`，没有未执行候选时退出码走原来的 0 / 1 / 3，**有 `not_run` 且无差异时退出码 4**（部分覆盖：不是通过，也不是失败）。
  `not_run` 不算「已执行」（样本、家族覆盖、轴表都不计），也不是 triage 的发现。不带该参数时报告与以前逐字节同形。
  预算对**每一次**子进程调用生效：父本快照、候选 adapter、复跑确认的超时都取 `min(--timeout, 剩余预算)`；剩余不足 30 s（`MIN_ATTEMPT_SECONDS`，
  最快的真实候选约 15–18 s，再小只会被截断白费）就不再开始，该候选记 `not_run`。因预算截断而超时的候选记 `not_run`（不是 `unknown`、不是差异）；
  候选自己的超时（没被预算限制）仍按原规则。预算截断的父本快照不进缓存，已完成的照旧缓存。
  预算到期后到报告写完最多再耗 `OVERRUN_BOUND_SECONDS` = 清理 16 s（SIGTERM 等 10 s + 两次 3 s 的进程组确认）+ 固定开销 60 s = 76 s（预算时钟从 `explore()` 入口起算）。
  不带该参数时行为与报告都不变。
- `summarize` / `triage` 吃同一次周扫的 N 份分片报告时，先过合并校验：同 seed、同 HEAD、同生成器摘要与覆盖、同过滤条件与候选总数、
  片号恰好 1..N 各一份、没有候选出现在两片里、混入不分片的报告也不行——任何一条不满足退出码 2，**不静默合并**。
  缺片只有显式 `--allow-partial-shards` 才放行，放行后汇总 / 周报 / triage 到处标「N 片中 M 片完成」并写明「不是整个候选池」；
  报告里的 `time_budget` 必须与候选行一致（有 `not_run` 行却无预算记录、计数对不上、声明耗尽却没有 `not_run` 行都失败）；合并信息给出 `not_run` 总数、预算耗尽的片号、`fully_executed`（片齐且无 `not_run`）；周报写「time budget exhausted in shard(s) […]: N candidate(s) NOT RUN」。
  重复片、混 seed、混 HEAD、混生成器永远失败，`--allow-partial-shards` 也不放行。合并后的覆盖与指标按全集算（家族覆盖、候选数、结局计数取各片之和）。
- `promote` 只写一个候选记录（`proposal_only: true, applied: false`），**拒绝**写入
  `tests/compat/`、`tests/compat_guardian/`（含期望声明）、`tests/fixtures/`、`tests/support/`，也拒绝写任何名为 `manifest.json` /
  `baseline.json` 的仓库内文件；基线与 manifest 的改动只能是人写的、过 CODEOWNERS 的普通 PR。

**退出码（`run`）**

| 码 | 含义 |
| --- | --- |
| 0 | 干净通过：每个 case 都是 `passed`，且有完整证明 |
| 1 | 至少一个有独立证据支撑的失败（`failed`） |
| 2 | 调用错误 / 证据无效 / 报告未能发布（含源码树在运行中变化） |
| 3 | 已完成但无结论：`unknown / environment_unavailable / invalid_fixture / not_run` |
| 4 | **已判定、无失败、但不是干净通过**：有层「未覆盖」（`partial_coverage`），或是已声明的限制（`expected_limitation`） |

4 **永远不会被映射成 0**。CI 里只有 4 被明确放行为「带警告的绿」，其余非零都让 job 变红（§8）。

## 3. 报告契约（`tests/compat/guardian-report.schema.json`，版本 1.0.0）

顶层字段：`schema_version, run_id, source{commit,tree,dirty,content_sha256}, expected_sha, suite,
expected_cases, corpus_sha256, cases[], evidence[], verdict, coverage, unverified[], duration_s`。
缺字段、未知字段、未知枚举一律是显式失败（内置校验器拒绝它不认识的 schema 关键字，所以 schema 升级
不会悄悄放松校验；独立的 `jsonschema` 读者在可用时另有测试）。

每个 case 行（`cases[]`）：

| 字段 | 内容 |
| --- | --- |
| `id, layer, profile, desktop_shell_verified` | case id；`engine` / `authenticated_server_safe`；固定 `safe`；固定 `false`（真桌面壳不在范围） |
| `status` | 见 §3.1 |
| `stages` | CompatBench 九级漏斗各一格 `{status, criterion}`；`criterion` 写明判据 |
| `native` | `{status, result}`：原生参照的判定与事实（§4） |
| `executions` | `{native, tavotto, basis, attempt_history, attempt_history_agrees}`（§5） |
| `figures` | `{native, tavotto_captured, tavotto_valid}` |
| `timings_s` | `{total, native, tavotto, stages{九级}}`；**未测 = null，绝不写 0** |
| `needs_authorization` | `{required: bool\|null, basis}`：真实用户流程是否需要授权决定（装包 / Native）；Guardian 自己从不装、从不升 Native |
| `failure_category` | `{category, source_code, basis}`（§6）；绿色行为 `null`，其余必有（`unknown` 合法） |
| `comparable, environment_before/after` | 环境一致性；环境指纹 = 事实摘要的 sha256 |
| `product_classification` | CompatBench 六类原值，仅作旁证，**单独不构成失败证据** |
| `fixture` | 夹具文件 sha256、`source_sha256`、`data_sha256` |
| `raw, unverified` | 原始结构化证据；本行**没有**验证的东西 |

报告里的环境只有版本摘要（Python、平台、关键科学包版本）+ 已装包全表的 sha256 与数量；**完整包清单不进
报告、不进日志、不进 artifact**。

### 3.1 状态词表（含「该层未覆盖」的精确定义）

行状态 = 简报的八态 + `partial_coverage` + `control_met`；格状态 = 八态 + `not_covered`。

- `not_run`：这一层**在范围内但没有执行**（前序失败、被跳过）。
- `not_covered`：这个 adapter **根本不驱动**这一层。V2 会话 adapter 只走「扫描 → 检查 → 运行 → 捕获 →
  渲染 → 标题语义」，所以它的 `edit / replay / export / fidelity` 恒为 `not_covered`，且判据写
  `layer not exercised by the V2 session adapter`。
- `partial_coverage`（行/报告）：**同时满足**——原生参照 `passed`；两侧执行次数均为独立计数且 > 0；环境可比；
  所有被驱动的层都 `passed`；其余层全是 `not_covered`，且至少有一个。它**不是通过**：不进「通过」分子，
  汇总里单列，退出码 4。要升级成 `passed`，必须有 adapter 真正驱动那些层——不能靠改词或调阈值。
- `passed` 的必要条件：原生参照通过；两侧计数 > 0；环境可比；九格全部 `passed`。
- `expected_limitation`：来源——CompatBench 清单里**显式声明**期望为假的阶段；以及 V2「设计停止」场景
  （`ideal_outcome == stop_no_fallback` 且 V2 停止判据逐项满足：一次执行、明确原因码、无图、不回退）。
  以及 V2「设计内 partial」场景（`ideal_outcome == partial_figure` 且 V2 判据逐项满足：phase=partial、
  失败的图在 `failed_figures` 里指认）：执行层 `passed`（`partial`+`succeeded` 就是跑完了），捕获层
  `expected_limitation`。其余情况下捕获张数少于原生张数，捕获层一律 `failed`（`partial_capture`），
  不会因为「至少捕获了一张」判过。
- `control_met`：见 §3.2，只由已提交的声明文件产生。
- `failed` 必须有独立证据（阶段失败 / 路由失败 / 浏览器失败）；遗留的 `product_bug` 标签单独只给 `unknown`。
- 聚合 `verdict`：有 `failed` → `failed`；全 `passed` → `passed`；其余全在 {passed, partial_coverage,
  expected_limitation} → `partial_coverage`（只要有一个 partial）否则 `expected_limitation`；全是同一种
  环境/夹具问题 → 该状态；其余 `unknown`。空集合 → `unknown`。

### 3.2 声明式预期（`tests/compat_guardian/expectations.json`）

不再有写死的 id 清单。一个 case 的**观测结果本来就不是干净通过**时（别的规则的对照、故意写错的夹具、
设计停止、前提设计上不成立），必须在声明文件里登记：`suite, case, outcome, reason`，以及**至少一条**
`references`（`issue`：`#123`；`test`：`path::name`，文件和名字都要真实存在）。schema 在
`tests/compat_guardian/expectations.schema.json`；可声明的结果只有
`invalid_fixture / expected_limitation / environment_unavailable / partial_coverage / not_reproduced`
（`passed` 不需要声明，`failed` / `control_met` 是结果不是声明）。

判定是机械的：**观测结果 == 声明** → 行状态 `control_met`（不是通过、不是失败，不进通过分子，摘要单列）；
**不等** → `failed`，行上带 `expectation.diff`（「declared X but observed Y」），类别 `unknown /
expectation_mismatch`。比声明更好的结果同样是不等：它可能是静默的行为变化（例如受限入口悄悄升了 Native），
必须有人改声明。观测结果里「失败但形状与记录的 main 基线逐项相等」按 `expected_limitation` 计，这只在存在声明
时才起作用；没有声明的 case 保持原样（如 `s07 / s17 / s25` 仍是 `failed`）。声明文件与它的 schema 同夹具一样
按 `git show HEAD:` 比对，报告里带 `expectations_sha256`，`validate` 会按声明重算每一行的状态。

## 4. 原生参照（`compat_driver.py --mode native_ref`，M1）

普通 `python script.py` 语义（缺省 cwd = 脚本目录），只观测不干预；**独立实现，不 import 产品的
`figcapture`**，图的身份只用 Figure 对象本身、文件按原样记路径。事实：

`exit_status`（自然结束 0；`SystemExit(code)` 记 code；异常 1）、`error`（类型 + 截断 traceback）、
`figure_count`（被 `savefig` 的不同 Figure ∪ 退出时 pyplot 仍开着的）、`figures_saved / figures_open_at_exit /
savefig_calls`、`same_path_distinct_figures`（不同 Figure 写同一路径——静默覆盖的信号）、
`artifacts[]`（新增/改动文件的字节数、sha256、**能否读回**：图像用 PIL 完整解码，PDF 验头，SVG 验 XML）、
`side_effects{created,modified,deleted}`（项目目录整体快照的前后差）、`semantics`（各图标题）、
`script_entries`（驱动自己数到的入口次数，仅作交叉核对）。

S0 的已登记真实项目可声明 `reference_invocation: {cwd, argv}`。`cwd` 是物化项目内已存在的
相对目录，`argv` 只含脚本名之后的字符串参数；绝对/越界目录、shell 启动器和任意入口路径均不接受。
它仅转交原生参照及诊断腿，不替产品传启动参数或修补用户脚本。原生通过而产品不能沿用这份启动
语义，仍是可观测差异。没有该字段时，既有夹具行为保持原样。

判定（`_native_status`）：`exit_status != 0` → `invalid_fixture`（缺模块 → `environment_unavailable`）；
图类产物读不回来 → `invalid_fixture`；`figure_count == 0` 是合法的原生结果（脚本不画图），仍为 `passed`，由对侧判：Tavotto 捕获了原生没有产生的图 →
`failed`。否则 `passed`。**原生失败永远先于 Tavotto 归因，且不会进入产品缺陷人群**；`validate` 会按原生事实重算
这个状态，伪造「原生失败但 Tavotto failed」的报告会被拒。

## 5. 执行次数

权威计数 = **项目目录之外**的追加式计数器：`sitecustomize` 注册的 CPython 审计钩子，对
「源码 sha256 命中夹具」的模块执行逐次 `O_APPEND` 写一行到每个 leg 各自的日志（native / tavotto /
诊断类 native、census 各自独立）；V2 另有夹具自写的项目外 `exec_log.jsonl`，并与钩子记录互核。
会话 `attempt_history`（V2 的 `provider.attempts`）**只做交叉核对**，写入 `executions.attempt_history` 与
`attempt_history_agrees`，永不作为计数来源（它不等于执行次数：缺包换环境重跑、probe、写回前 one_shot 都不同）。
计数器没打开 → `null`（未知），不是 0；任一侧 0 或 null → 该行最多 `unknown`。

## 6. 失败类别（`failure_category`，`scripts/ci/compat_guardian_taxonomy.py`）

闭集（简报 §7.1）：`environment_resolution, dependency_provider, dependency_version, argument_or_input,
cwd_or_data_path, safe_execution_divergence, figure_capture, partial_capture, product_entry_reachability,
manifest_semantics, editing_capability, replay_inconsistency, export_or_fidelity, timeout_or_process_crash,
invalid_fixture, unknown`。映射表只把**已存在的码**指向类别，表外一律 `unknown` 并保留原始 `source_code`：

| 来源 | 类别 |
| --- | --- |
| 阶段失败（原生通过的前提下）discover / execute / capture / open·semantic / edit / replay / export·fidelity | product_entry_reachability / safe_execution_divergence / figure_capture（捕获数 0 < n < 原生数则 partial_capture）/ manifest_semantics / editing_capability / replay_inconsistency / export_or_fidelity |
| `native_script_error`、`native_render_failed`、原生退出码非 0 | invalid_fixture |
| `driver_timeout`、`driver_crashed`、adapter 超时 | timeout_or_process_crash |
| `driver_no_output`、`driver_bad_output`、**runner 崩溃** | unknown（runner 崩溃永不记到产品头上） |
| 前置缺失（模块/字体/前提不成立） | environment_resolution |
| `ModuleNotFoundError` | dependency_provider |
| `safe_file_operation_blocked` / `no_figures_captured` / `multi_figure_contract` | safe_execution_divergence / figure_capture / partial_capture |

CompatBench 的 `run_group` 之外 runner 崩溃目前会被 `compat_matrix.py:main` 记成 `product_bug`
（M4，未改，不在 CG-01 的文件归属内）；Guardian 层不使用该路径：adapter 崩溃/无终态 → `unknown`。

## 7. 安全护栏（所有阶段）

- 候选/探索脚本永不进 self-hosted 或 `tavotto-lab` job；lab 只跑已合入 main 的夹具。
  `tests/test_compat_guardian_workflow.py` 的判据主语是「所有引用 Guardian 脚本的 workflow」，
  不是一张手写名单。注意这类静态测试量的是 main 上的文件；PR 自带的 workflow 先在 PR 事件上跑一次，
  这一半由 runner group 配置与私有 `ci-infra` 兜底（见 `docs/rules/ci/runner-trust-zones.md`）。
- Python 层 Safe 不是沙盒。可执行范围：登记夹具（托管 runner / 本机隔离目录）；由已评审模板对已评审父本做
  确定性变换得到的派生候选（CG-02）视同父本信任级；任何非确定性/外部来源候选只做静态检查，直到有 OS 级
  隔离证明。
- 探索器对依赖授权端点零调用；Guardian 自己从不装包、从不改基线。
- 报告证据只在同一次 run 内成立；artifact 只传白名单；不把已装包全表与原始 stderr 打进公开日志。
- 语料三级标签（curated / generated / user_derived）；`user_derived` 需 `share_permission`，仓库只存哈希。

## 8. CI 集成（`.github/workflows/compat-guardian.yml`）

独立的、**仅托管 runner** 的 workflow；不在 `ci.yml` 里（`compat-smoke` 保持 main 的内容）。

- 触发：`workflow_dispatch`、每日 `schedule`、以及 `pull_request`（`branches: [main]`，`paths` 只列
  Guardian 自己的文件）。不监听 `merge_group` / `push` / `pull_request_target`。
- `permissions: contents: read`；checkout `persist-credentials: false`；无 secrets、无 token。
- **fork PR**：job 级 `if` 只在「非 PR 事件，或 PR 的 head 仓库 == 本仓库」时运行，所以 fork 的 PR 不会执行
  Guardian（`test_fork_pull_requests_do_not_run_guardian` 钉着）。依据与纵深：① `pull_request`（不是
  `pull_request_target`）下 fork 的 token 本来就只读、拿不到 secrets，且本 workflow 不引用任何 secret；
  ② 这条 `if` 让 fork 代码根本不被执行；③ 同仓库分支的 PR 会执行该分支提交的夹具与脚本——这正是 §1
  说的「自洽检查不是评审」，所以只在托管临时 runner 上跑、无凭据、无 secrets。注意这类静态保证量的是 main 上的
  yml，PR 自带的 yml 能改掉它，兜底在仓库的 fork-PR 审批策略。
- **不是必需检查**：job 名不在 `GATE_CONTEXTS`，`ci-fast-gate` / `ci-integration-gate` 不 `needs` 它，
  因此 required contexts 不变（`merge_queue_ruleset.py verify-live` 与此无关，未运行）。
- 失败可见：不用 `continue-on-error`；`set +e` 的步骤必须按退出码重新抛出，只有 4 放行为 `::warning::`；
  `validate` 与摘要步骤在失败后也运行（`!cancelled()`），摘要写 `GITHUB_STEP_SUMMARY`；没有任何报告 = 失败。
- 公开日志只含每个 case 一行状态，失败时只补最后 30 行 Guardian 自己的输出；原始 stdout/stderr 在证据目录里，
  不上传。
- artifact 成功/失败都上传，但只有白名单：`guardian.json`、`summary.md`、`guardian.unvalidated.json`、
  汇总 JSON/MD、证据里的 `*.png`。注意：下载到的 artifact 不含全部证据文件，**完整 `validate` 只在
  产生它的 job 内成立**。
- 观察的 case 集：engine = `shape_oo_savefig, shape_pyplot_show_only, shape_relative_csv,
  shape_local_helper_import, shape_multi_figure_a, sci_pillow`；V2 = 全部场景。
  没放进去的 V2 场景及原因：全部 15 个场景（不挑子集）。`s16a/b`、`s04b/s05/s06`、`s14`、`s18`、`s21`、`s26` 由声明文件产出 `control_met`；
  `s01/s04/s27` 为 `partial_coverage`；`s07/s17` 与 `s04b/s05/s06` 同属案例 13（声明为 `expected_limitation`）；`s25` 是真实产品缺口
  （#1134，G3），不声明、保持 `failed`——所以只要它未修，这个观察 workflow 每次都会红，这是有意的，不是被吞掉。

- **周扫（CG-09）**：同一个 workflow 里另有三个 job，只由 `schedule`（`17 2 * * 0`，周日 02:17 UTC，与 nightly 的 03:41 错开）或
  `workflow_dispatch` 的 `weekly: true` 触发，详见 §13。两条 cron 都会启动整个 workflow，所以每个 job 的 `if` 用
  `github.event.schedule == '17 2 * * 0' || inputs.weekly` 分流：`guardian` / `explore` / `trend` 在周扫触发下跳过，`explore-weekly*` 在其它触发下跳过。

## 9. 评审边界（CODEOWNERS）

`.github/CODEOWNERS` 为下列路径列出显式条目：`tests/compat/**`（manifest、基线、schema）、
`tests/fixtures/compat_v2/**`、`scripts/ci/compat_guardian*`、`scripts/ci/guardian_exec_observer.py`、
`scripts/ci/compat_driver.py`、`tests/test_compat_guardian*`、Guardian workflow 与本文档。
`tests/compat_guardian/**`（声明文件与 schema）同样在列。目前只有一位维护者，条目等于默认值；它们的作用是：等有第二位维护者时，这些路径不会跟着默认规则漂走，
且「改夹具 = 改权利边界」在文件里写明了。

## 10. 15 个对抗验收案例的覆盖

| # | 案例 | CG-01 状态 |
| --- | --- | --- |
| 1 | 普通脚本一次运行出图 | 登记夹具 `shape_oo_savefig` / `s01_plain` 的真实运行 |
| 2 | show-only | `shape_pyplot_show_only` 真实运行；G1（show 后 close 无 savefig 零图）已由 #1138 修复，现为回归用例 |
| 3 | savefig 后立即读回 | `s06`（受限入口对照 → `expected_limitation`）；Native 回退由 `tests/test_execution_fallback_e2e.py` 拥有 |
| 4 | os.replace 后读回 | `s04b`（同上）；`s04_replace_readback` 为 `partial_coverage` |
| 5 | 多图不能静默只留一张 | `tests/test_compat_guardian_adversarial.py::Case05MultiFigure`（原生计图数、捕获少于原生 → `partial_capture`）；`shape_multi_figure_a` 的 `expected_figures` 由 CompatBench capture 阶段判 |
| 6 | 本地 helper + 相对 CSV | `shape_local_helper_import`、`shape_relative_csv` 真实运行 |
| 7 | 缺第三方依赖：provider + 授权 | 现有 V2 `s14/s26`（本环境前提不成立 → `environment_unavailable`）；`needs_authorization` 字段已就位；CG-02 |
| 8 | OpenCV/Pillow 位图输出 | `sci_pillow` 真实运行；位图产物可读性在 `native_ref.artifacts`；能力分级留 CG-02 |
| 9 | 原生自己失败 → 不是 Tavotto bug | `Case09NativeItselfFails`（真实驱动 + 伪造报告被拒） |
| 10 | 缩小丢了原始错误 → 最小化器拒绝 | CG-03（`minimize` 是桩） |
| 11 | 什么都没跑 → 报告不能成功 | `Case11NothingRan` |
| 12 | 旧 SHA 的证据证明不了新提交 | `Case12StaleEvidence` |
| 13 | 受限入口不能自动升 Native | 行为由 `tests/test_execution_fallback_e2e.py::test_a_browser_mode_service_never_falls_back_to_native` 拥有；Guardian 加一层：`s04b / s05 / s06` 在声明文件里声明为 `expected_limitation`（理由「案例 13」+ 上述 owner 测试）；观测偏离声明（包括「变好」，可能是静默升 Native）即 `failed` |
| 14 | 两边都出图但语义不同 | `v2_stages` 的标题语义对比（`test_v2_both_graphs_wrong_science_fails`）；更一般的数据级语义留 CG-02 |
| 15 | 两边都对 → 不能编造缺陷 | `Case15BothCorrect` |

---

## 11. Explore（CG-02）

`explore` 从**已评审父本**出发，用**已评审的确定性 AST 变换**生成派生候选，真实运行 native 与 Tavotto 引擎路径，
把差异记成「候选失败记录」。实现：`scripts/ci/compat_explore_transforms.py`（变换/去重/覆盖）、
`scripts/ci/compat_explore.py`（执行/比较/报告）。

**候选 = 父本 + 变换链 + seed，仅此而已。** 执行器只接受这三个输入，自己重新推导文件并核对 `candidate_id`；
不存在「运行某个路径/源码字符串」的入口，候选清单里的文件或字段被改了也拿不到执行权（`derive` 不通过）。

| 信任级 | 条件 | 做什么 |
| --- | --- | --- |
| 父本级 | 父本是已提交语料（`tests/compat` 引擎父本）、变换是固定确定性模板、静态判定无随机/时间/网络/子进程/`eval` | 在 CG-01 同一套隔离里真实执行（托管临时 runner 或本机隔离目录；仍不是 OS 隔离） |
| 仅静态 | V2 父本（会话 adapter 不由 explore 驱动）；任何来源不确定或非确定性（`random/secrets/uuid/time/datetime/socket/urllib/subprocess/ctypes`、`numpy.random`、`eval/exec/input`） | 只 `compile`，永不执行；报告里写原因 |

依赖授权端点零调用、不装任何东西、不用外部 LLM/API；缺包只会让 native 失败 → `environment_unavailable`。

**变换（一次只动一个轴；二因子组合按两两覆盖确定性选取，见下）**

| 家族 | 变换 | 轴 | 类别 |
| --- | --- | --- | --- |
| show_close | `show_close`（去 savefig，末尾 show()+close('all')，**G1**）、`show_only` | output_call | 改轴 |
| same_path_multi_figure | `same_path_loop`（循环里 N 张图写同一路径，**G2**） | output_name | 改轴 |
| import_alias | `import_alias`（pyplot/numpy 别名） | import_alias | 保型 |
| entry_form | `wrap_main`（顶层改 `main()` + `__main__` 守卫） | entry | 保型 |
| argparse_defaults | `argparse_out`（保存路径来自 argparse，默认值=原路径） | args | 保型 |
| data_source | `inline_to_file`（第一个字面量数据列表改读相对 JSON）；CG-07 读文件写法：`read_csv_pandas`、`read_csv_numpy`（`loadtxt`）、`genfromtxt_names`、`read_tsv_stdlib`、`read_npy`、`read_pickle`、`read_excel`（openpyxl）、`read_parquet`（pyarrow）、`glob_dir`（拆成 `data/*.csv` 再 `sorted(glob)`） | data_source | 保型 |
| entry_form | `class_wrapper`（绘图代码移进 `class Plotter: def run(self)`）、`return_fig`（`_cg_draw()` 返回 fig，调用方 savefig）、`sys_argv_out`（保存路径来自 `sys.argv[1]`，缺省为原路径）、`cell_markers`（语句之间插 `# %%` / `# %% [markdown]` 注释，模拟 notebook 导出的 .py）——CG-08 | entry（`class_wrapper`、`return_fig`，与 `wrap_main` 互斥）、args（`sys_argv_out`，与 `argparse_out` 互斥）、cells（`cell_markers`） | 保型 |
| output_call | `multi_format`（同一 stem 存 png+pdf+svg）、`pdfpages`（`PdfPages` 多页）、`bytesio_close`（savefig 到 `io.BytesIO` 后 close，**#749**）、`same_stem_two_figs`（两张不同的图写同一 stem 不同格式、后写的被 close；#1250 后第二张另存 `<stem>-2` 不再丢，只剩同路径内容差异 `same_path_content_mismatch`）、`bbox_tight_dpi`（`bbox_inches="tight", dpi=600`）——CG-08 | output_call（与 `show_only` / `show_close` 互斥） | 改轴 |
| style_rc | `rcparams`、`style_use` | style | 改轴；现在可执行（CG-06 起 `compare` 之外还有像素判定，样式差异观察得到） |

**data_source 家族（CG-07，`scripts/ci/compat_explore_data.py`）**：同轴互斥，一条链至多一个数据来源；全部以第一个字面量数值列表为数据源，
确定性写出数据文件并把该实参改成读文件的表达式。读回的值与字面量逐值相等、元素类型一致（整数列表按整数、含浮点按浮点，
`dtype=` / `int()` / `float()` 显式写出，pandas 用 `float_precision="round_trip"`），否则刻度会变、保型检查会把模板的锅记成假差异。
由 `seed + 父本结构签名（规范化 AST）+ 变换名` 派生两个参数：**路径写法**（`os.path.join(os.path.dirname(__file__), 名)` / cwd 相对 / `Path(__file__).resolve().parent / "data" / 名`）和
**编码**（仅文本格式：utf-8、utf-8-sig、gbk，或中文文件名 `实验数据`；读端显式写 encoding，表头第一列是中文所以 gbk / BOM 真的改变字节）。
二进制逐字节确定：npy 头固定；pickle protocol 4；xlsx 重新打包（固定时间戳、不压缩、抹掉 docProps 的 created/modified）；parquet 不压缩、不写统计、不带 pandas 元数据
（footer 的 `created_by` 带 pyarrow 版本，所以版本钉在 workflow 里）。去重口径：仅编码 / 文件名不同的候选 AST 签名相同被归一；路径写法不同是结构不同，各留一份。
`openpyxl`、`pyarrow` **只在 Guardian workflow 的 job 里装**（版本钉在 `compat-guardian.yml`，产品依赖与 pyproject 不变）；缺失时 `read_excel` / `read_parquet`
`applies` 为 False，原因进报告 `coverage.unavailable_transforms`。新增 import（pandas/numpy/csv/glob/os/pathlib/openpyxl/pyarrow）不在 `_UNCERTAIN_IMPORTS` 里，也没有放宽那张表。

**脚本写法与输出写法（CG-08，`scripts/ci/compat_explore_shapes.py`）**：同样是 AST 编辑、必须能 `compile`、参数由 `seed + 父本结构签名 + 变换名` 派生、
新 import（`io` / `sys` / `matplotlib.backends.backend_pdf`）不在 `_UNCERTAIN_IMPORTS` 里，那张表没有放宽。入口形态里 `class_wrapper` / `return_fig` 排在输出变换**之后**
（顺序号 80/81，要把前面各步改出来的区域整体搬进函数），搬不动的（区域里有 `return` / `global` / 通配 import，或搬进函数后外面的代码还要读区域里赋值的名字，
或 savefig 之前的变量被之后的语句用到）一律「不适用」，不硬搬。`cell_markers` 的注释不在 AST 里：变换先放独占一行的哨兵语句，`ast.unparse` 之后把**整行等于哨兵**的行
换成注释行（序列化的一部分，不是对已有源码的字符串替换）；注释不进规范化 AST 签名，所以带标记的候选与父本同签名、靠轴向量区分。

保型：native 输出（图数、标题）必须等于父本——执行器同时跑父本 native 核对，不等就是 `invalid_candidate`（模板有 bug，
不是 Tavotto 缺陷）。改轴：只能与这个变体自己的 native 比。所有改动都是 AST 编辑，结果必须能 `compile`。
同 seed 逐字节相同（参数由 `seed + 父本结构签名 + 变换名` 的哈希派生，不依赖其它候选）。

**比较（`compare`，纯函数）**：`zero_figures`（原生 N 图 / Tavotto 0 图）、`fewer_figures`（partial_capture）、
`same_path_content_mismatch`（原生最后写入该路径的图 vs Tavotto 保留的图）、`title_missing`。差异带 Guardian 失败类别。
多输出变换另比输出本身（`output_call_differences`，Codex #1368 P1）：`multi_format` 比同一 stem 原生写出的格式集合与产品记录的 savefig 格式
（`format_set_mismatch`）；`pdfpages` 比原生 PDF 的页数（driver 数 `/Type /Page`）与产品记录的 pdf 保存次数（`page_count_mismatch`）——
`PdfPages.savefig` 不是 `Figure.savefig`，不进调用表、产品没有页的概念，所以 `page_count_mismatch` 目前恒为已知差异。
#1370 PR1 起写进 `PdfPages` 的图**被认领**（不再 `script_no_figure` / `zero_figures`；stem 取 PDF 文件名、来源 pyplot，
模板里同一张图写 N 页仍是 1 张图），只剩页数差异；把编辑导回多页 PDF（A）尚未做，页数差异要等它才会消失。产品没观察到调用表的 stem 不在此比。
去重键是「轴向量 + AST 签名 + 判定身份」：判定身份取父本用例的 `stem / entry / expected / expected_figures / semantic_expectations / mutations / export_formats`，
同一脚本服务的多个 must 用例（如 `ca_basic_series.py`）各自保留候选，判定相同的孪生父本仍去重。
Tavotto 侧走产品自己的发现链（静态注册表，不足时试运行探测）再起一次性 worker 取 manifest。
`show()` 之后 `close()` 的图原生算「出现过」（`native_ref` 记录 show 时的在开图）。

**补判定（CG-06，`scripts/ci/compat_explore_oracles.py`）**：图数与标题之外再问四件事，判据全部借用 CompatBench 现成的，
不另设一套——这样 explore 与 CompatBench 对同一张图不会给出相反结论。

| 层 | 适用 | 判据 | 差异 kind（类别） |
| --- | --- | --- | --- |
| fidelity | 全部可执行候选 | 原生 PNG 与 Tavotto 零 patch PNG（同一 `FIDELITY_WIDTH`），`pixelcompare.verdict` 用 `FIDELITY_TOLERANCE`——与 CompatBench 同一档，不另设 | `pixel_mismatch`、`render_failed`（export_or_fidelity）；证据含三项指标、control/tavotto/diff PNG 的相对路径与 sha256 |
| structure | 仅保型 | 父本在 Tavotto 里的 `{gid.prop: value}` 快照（每次 explore 运行每个父本只算一次）的每个键，候选里必须存在且 `_same_value`；候选多出的键不算；**数据指纹**（manifest 每个数据类元素的只读 `data_fingerprint`，快照键 `gid.__data__.part` → sha256 + shape + finite）逐部件精确比较，不等记 `structure_mismatch`（evidence 带 `aspect: "data"` 与两侧 shape/finite），任一侧 `over_budget` / `unavailable` 则结构层 `not_run`、候选落 unknown；阶段记录里 `data_compared` 是真比过的部件数（0 = 没比数据） | `structure_mismatch`（manifest_semantics） |
| edit / replay / export | 仅保型 | 继承父本用例已评审的 `mutations` 与 `export_formats`（缺省 pdf、png），在同一 worker 上调 `compat_matrix.stage_edit/stage_replay/stage_export` | `edit_failed`（editing_capability）、`replay_mismatch`（replay_inconsistency）、`export_failed`（export_or_fidelity） |

改轴候选的 structure / edit / replay / export 记 `not_covered` 并写明判据：已评审的编辑目标按父本 gid 记，改轴之后没有已评审的目标。
候选行的 `stages` 逐层给 `{status, criterion}`（`passed / failed / not_run / not_covered`），报告 `stage_counts` 汇总各层状态，
没跑的层可见。缺 numpy / Pillow 时 fidelity 记 `not_run`，**不是差异**，也不是通过。

**能保证**：同一候选上，原生与 Tavotto 零 patch 渲染在 CompatBench 容差内一致；保型候选的父本编辑目标仍能应用、撤销、重放、导出，
且可编辑字段值不变。**不能保证**：容差内的一致不是逐像素相同；改轴候选的编辑 / 导出没有被验证；字体 hinting 之类的系统性微差
被容差吃掉；只覆盖父本里已评审的那几个编辑目标，别的属性没有被改过。

**父本按规则选取（CG-08）**：不再写死名单。`compat_engine` 里 `tier == "must"` 且 `expected` 中没有任何值为 `False`（语料自己声明某阶段不判）的 case 都是父本
（纯函数 `select_engine_parents`，随语料变）；V2 静态父本不变。语料里 `metamorphic` 没有任何 `must` 用例，所以它按规则没有父本——覆盖报告的
`parents.categories_without_parent` 明说这一点。父本自带不确定 import（如 `datetime`）的，静态判定仍是 static_only。

**两两覆盖（CG-08）**：对任意一对**不同轴**的变换 `(a, b)`（`a` 的顺序号小于 `b`），只要某个父本上两者能依次适用，就是一个「可行组合」。确定性贪心覆盖：每一轮每个父本
（顺序由 seed 洗牌）至多取一个**尚未被覆盖**且自己适用的组合，优先取适用父本最少的，平局由 seed 派生的哈希打破；一个组合只落在一个父本上，所以每次都新增覆盖，总数 =
可行组合数而不是父本数 × 抽样数。报告 `coverage.pairwise` 给 `feasible / covered / uncovered`。同 seed 同字节；`--limit` 的选取按**家族**轮转
（家族内再按变换轮转，变换内按 `seed|candidate_id` 的哈希排序），不会因为变换变多就把字典序靠后的家族（含 G1 / G2）挤出去。

**结构去重与覆盖**：按「轴向量 + 规范化 AST 签名」去重（数字/字符串常量与 `cg_plt3` 之类别名数字归一，所以仅参数不同不算新结构）。
报告 `coverage` 给出：每个家族的轴、适用父本数、候选数；每个轴的候选数；保型/改轴数；去重前后；以及父本按语料类别的分布
对比整个语料。父本现在是 `script_shapes / core_artists / axes_layout / scientific_stack` 里已经通过的 `must` case，`metamorphic` 为零（语料里它没有 `must` 用例），
#1250 手工加进名单的六个 `expected` 档父本（`art_linecoll_alpha`、`res_radar`、`lay_twin_manual`、`art_3d_surface_scatter`、`shape_helper_shadows_pypi`、`shape_same_stem_formats`）按规则**不再是父本**（档位是 `expected` 不是 `must`；同 stem 不同格式的边界改由 `same_stem_two_figs` 变换在 `must` 父本上直接造出）；usetex 需要系统 TeX，托管 runner 上跑不了，不作父本。
这个偏向在报告里是明文，候选与自己的 native 一致不代表别的形状没问题。

**输出**（`--out/RUN_ID/`）：`candidates.json`（确定性）、`candidates/<id>/files/`、`evidence/<id>/…`、
`reports/explore-report.json`、`reports/explore-summary.md`。报告含每候选的 seed、父本、变换链、AST 签名、信任级、
执行次数（项目外审计计数器）、差异，以及 `known_gaps`：G1/G2 是否**由探索自己**发现（AC-05）。
退出码：0 无差异；1 发现差异（候选失败记录）；3 有候选无结论；2 错误。**差异只是记录**：不改 baseline/manifest、
不开 issue、不 promote。

## 12. Triage 与 Minimize（CG-03）

### Triage

`triage` 读 `run` / `explore` 的报告，对每条发现给出归因，再聚类；它不运行任何东西。归因表是数据：
`tests/compat_guardian/triage_rules.json`（schema 同目录，加载时校验：未知事实、未知家族、不存在的模块路径、
重复 id、操作符与取值不匹配都是加载错误，不是静默不匹配）。规则按顺序、首个命中者生效；没有规则能证明的
一律 `unknown`（置信度 low，仍带下一步动作）。缺失的事实永远不满足正向条件。

每条归因：家族（简报 §7.1 的 16 类，含 `unknown`）、阶段、稳定错误码、最相关的原始证据、原生与 Tavotto 的差异、
置信度、可能涉及的代码模块、下一步验证动作、命中的规则 id 与理由。**「Safe 拦了 savefig 之后再读回」不会被判成缺数据**：
`safe_file_operation_blocked`，或 `FileNotFoundError` 且原生做过 `savefig`，先于任何 `cwd_or_data_path` 规则命中
（`safe_execution_divergence`）；`FileNotFoundError` 而没有 savefig 证据 → `unknown`。

**聚类**的键：阶段、家族、稳定码、执行策略、依赖来源、可复现的行为差异签名（差异种类 + 原生 show/save 关系）、
规范化调用位置（候选 AST 里 `figure / show / close / savefig` 的程序顺序，带循环标记，与别名/父本无关）。
**不看 traceback 文字**。已知家族记录（`tests/compat_guardian/families/*.json`）按行为种类 + 调用位置 + Tavotto 错误码关联。
CG-02 的 27 条真实差异聚成恰好两簇：G1（show 后 close，11 条）与 G2（同路径多图，16 条），不合并。

### Minimize

`minimize` 对**一条**explore 发现做有界 delta debugging。输入只是 `(报告, 候选 id)`：候选被**重新推导**并核对身份，
报告无法提供代码。缩减 = 对推导出的候选做语句/块级**删除或展开**（地址如 `/body2/body0/body3`）与删除无人引用的数据文件，
**只删不增**，所以缩减出来的中间脚本是父本的确定性子集，**信任级等同父本**（见 §11）；执行只走 CG-02 的受信派生路径，
静态判定不再通过的缩减不执行。

每个缩减步骤都重新验证（`accept`），必须同时满足：原生仍正常退出且预期不变（show/save/多图覆盖关系）；Tavotto 仍是**同一个**错误码
与同一组差异种类；归因的阶段与家族不变；因此语法错误、缺包、NameError 之类「换一个错误冒充原缺陷」的缩减会被拒绝，
原错误消失的缩减（案例 10）也会被拒绝。每次拒绝及理由都记入 `trials`。

预算：执行次数与总时间，均有限且为正；超出即 `minimization_failed`，**保留原始夹具**（不输出半成品当最小复现）。
基线（未缩减）不复现 → `not_reproduced`，什么也不编造。最终结果再独立确认运行若干次，必须次次复现，否则也是
`minimization_failed`。

输出 `DIR/repro.json`（最小脚本全文、必要数据哈希、运行命令、环境身份、原生行为、Tavotto 行为、可复现性、缩减前后、归因证据、
预算与全部试验）、`DIR/minimal/`、`DIR/work/evidence/`。`minimize --replay` 用记录的缩减重跑并核对签名。

首批真实结果与家族记录：`families/G1-show-close-zero-figures.json`（11 → 5 条语句）、`families/G2-same-path-multi-figure.json`
（13 → 7 条）；记录里写明根因、关键回归测试、已验证/未验证环境。两族都已修复：G1 由 #1138、G2 由 #1141，记录里 `fix_pr` 分别是
1138 / 1141，`in_corpus` / `promoted` 仍是 `false`（进语料要维护者决定）。`tests/test_compat_guardian_families.py::RealRepro` 与
`tests/test_compat_explore.py::RealDiscovery` 的 G1 / G2 用例已转为「修复后原生 / Tavotto 无差异」的回归；尚未修复的族仍按
「最小复现目前仍然失败」钉住——缺口修好时那条会红，那是更新记录（`fix_pr`）、把复现转成正式回归用例的信号。

周扫（run 39758109474，seed 202643）新发现的两族已登记，**行为按 ADR 0047 / 0084 有意保留、修的是诊断**，所以 explore 里仍是差异：
`families/G4-cwd-relative-reader-zero-figures.json`（沙盒 cwd 下经 C++ 读取器读 cwd 相对数据 → 零图，issue #1253，诊断修复 #1297）与
`families/G5-sandbox-empty-glob-empty-figure.json`（沙盒里 cwd 相对 `glob` 扫空 → 空图 / 下游报错，issue #1254，诊断修复 #1299）。
记录里 `fix_pr` 分别是 1297 / 1299，`RealRepro` 钉的是「差异仍在」；根治（项目级「在脚本目录里运行」默认）落地时那两条会红，届时改成回归用例。

最小化过程中暴露并修掉了 explore 比较里的一个误报：无标题的图（原生 `[""]` vs Tavotto `[]`）曾被当作同路径内容差异；
最小化签名因此新增「原生确有多图写同一路径」这一条，缩减不能漂移到别的原因上。

## 13. 自动化（CG-05）

同一个 workflow（`.github/workflows/compat-guardian.yml`），三档，**观察优先**：只出报告与注解，不改任何东西。

| 档 | 触发 | 跑什么 | 时长 |
| --- | --- | --- | --- |
| PR smoke | `pull_request`（base 是 main，非 fork，且只在 Guardian 自己的文件变化时） | 合同测试 + 引擎 6 个登记 case + V2 全部场景 | 约 10–20 分钟，job 上限 35 |
| Nightly | `schedule`（每日 03:41 UTC） | 上面一档 + `explore`（固定 seed 42、`--limit 24`、复现确认 3 个）→ `triage` → `trend` 汇总 | explore job 上限 60 分钟 |
| Manual | `workflow_dispatch` | 同 Nightly，可传 `seed`、`limit`、`families`；本机另可用 `minimize` / `promote` 做深入调查 | 同上 |
| Weekly | `schedule`（周日 02:17 UTC）或 `workflow_dispatch` 的 `weekly: true`（可传 `weekly_seed`） | 全量候选池切 4 片并行 explore → 合并校验 → triage → 周报；不跑上面三档的 job | 4 片 × 60 分钟 + 汇总 15 分钟 |

**CI 上能做的**：在 GitHub 托管的临时 runner 上执行已提交夹具，和由已评审父本经确定性变换得到的派生候选（执行器自己重新推导，
非确定性/来源不确定的只做静态检查）；上传白名单 artifact；把 triage/趋势写进 `GITHUB_STEP_SUMMARY`；对「新的、可复现、未登记家族」
发 `::warning` 注解；读上一次成功的定时运行留下的趋势 artifact（唯一用到 token 的步骤，`actions: read`，只读）。

**CI 上不做的**：不跑用户或外部来源脚本，不进 self-hosted / `tavotto-lab`；不开 issue、不开 PR、不评论、不写任何东西
（没有 `issues:` / `pull-requests:` / `contents: write`）；不改 baseline / manifest / 声明文件；不装依赖；不触发 merge_group，不是必需检查；
不证明桌面（Native）路径、Windows、其它 Python / matplotlib 版本，也不产出真实用户成功率。`minimize` 不在 CI 里跑（耗时且需要人判断）。

**周扫（CG-09）**：全量候选池每周扫一遍，不设 `--limit`；nightly 不变（seed 42、`--limit 24`、单例 `--timeout 240`）。

| job | 做什么 | 预算 / 权限 |
| --- | --- | --- |
| `explore-weekly-plan` | 算一次 seed：`date -u +%G%V`（ISO 年 + 周号，如 `202643`），dispatch 可用 `weekly_seed` 覆盖；校验为纯数字后经 job output 交给各片 | 5 分钟；`contents: read` |
| `explore-weekly`（matrix `shard: [1, 2, 3, 4]`，`fail-fast: false`） | 每片 `explore --seed <周 seed> --shard K/4 --max-seconds 2700 --confirm-differences 3 --timeout 240`；上传白名单 artifact（报告、摘要、`candidates.json`、候选入口脚本 `*.py`——triage 用它算调用位置；不含数据文件 / 证据目录 / stdout） | 每片 job 60 分钟（步骤 50）= 4 片 × 60 分钟；`contents: read` |
| `explore-weekly-summary`（`always()`，plan 成功即跑） | 按名字取本 run 的各片 artifact（缺片不让这一步失败）→ `triage` + `summarize --previous <上一周周报>` → step summary 与 artifact（保留 30 天） | 15 分钟；`contents: read` + `actions: read`（读本 run 的分片 artifact 与上一周周报，与 `trend` 同理由） |

- **每片时间预算**：步骤上限 50 分钟，`--max-seconds 2700`（45 分钟 = 步骤上限减 5 分钟）。池约 156 个候选（可执行约 137），每片约 39 个（约 33–36 个可执行），
  本机实测约 15–18 s/候选（见 PR 回报），一片约 10–12 分钟；预算存在的理由是候选池会随新变换增长，而被步骤超时杀掉的片**没有报告**、每周重复。
  预算花完 → 该片 `not_run` 若干、退出码 4（workflow 里 `::warning::`）、汇总标「partial coverage」，不算通过。
- **seed 只算一次**：4 片共用 plan job 的输出，不会因为跨 ISO 周边界各算各的；就算被人为改乱，合并校验也会因混 seed 失败。
- **缺片与失败**：某片崩了，其余片照常跑完（`fail-fast: false`），汇总照样产出，但标「N 片中 M 片完成」并发 `::warning::`，该片自己的 job 是红的；
  一份报告都没有则汇总 job 红。重复片 / 混 seed / 混 HEAD 直接让汇总红（退出码 2）。
- **提醒**：新报警簇（无家族记录 + 被复跑复现 + 不是 fixture / 环境问题）只发 `::warning::` 注解并写进周报（triage 小节），**不自动开 issue**，没有任何写权限。
  周报里簇与轴的列出顺序按 `compat_guardian_summary.AXIS_WEIGHTS`（data_source 3、output_call 3、entry 2，其余 1；来源是遥测 `export_result` / 诊断包错误码的月度汇总，人工更新）：
  **只影响列出顺序**，不改任何判定、退出码、计数或指标（`tests/test_compat_explore_shards.py::AxisWeightsOnlyReorder` 钉着：改权重后 JSON 逐字节不变）。
  周报照旧写明「语料通过率 ≠ 用户首次成功率」。
- **上一周周报**：最近已完成的定时运行里能下载到 `compat-guardian-weekly-summary-*` 的那次（往前最多 14 次）；手动 dispatch 的周扫不当基线。找不到则趋势 `not_computed`。
- **不做**：不跑 `tavotto-lab` / self-hosted；不加 `issues:` / `pull-requests:` / 任何 `write`；不开 issue、不开 PR；不改 baseline / manifest / 家族记录；
  不把周扫接进合并门禁；候选池仍是「已通过父本的派生」，全量扫完也不等于别的输入形状没问题。
- **没在 GitHub 上真跑过**：分片、合并校验、周报排序有本机测试；矩阵并发、`gh run download` 取本 run 的 artifact、`always()` 汇总在部分分片失败时的表现，要等第一次真实周扫验证。

**维护者提醒**：只有同时满足「没有家族记录（`tests/compat_guardian/families/`）」「被复跑复现」「不是 invalid_fixture / 环境问题」的 triage 簇
才发注解（并出现在 step summary 的 “New reproducible families” 里）。已知家族（G1、G2）的差异在 summary 里列出，不再提醒。
注解的下一步是人工：开 issue、写家族记录、派 `minimize`。

**趋势与汇总**（`summarize` 同时吃 run 报告与 explore 报告，`--previous` 指向上一份 summary）。每份汇总带：语料版本（各 suite 的
`corpus_sha256` 与声明文件摘要）、样本量与分母（失败从不出分母）、平台与依赖环境、不可比项、主要未覆盖场景，以及第 12 节指标：

| 指标 | 怎么算（对象永远是语料夹具） | 不能算时 |
| --- | --- | --- |
| First Figure Success | 原生有图的 case 中，capture 与 open 都通过的比例（分母含已声明限制与失败） | `not_computed` + 原因 |
| Execution Attempts | 项目外计数器的真实执行次数：总数、均值、分布；未测量的 case 单独计数 | 没有计数器则 `not_computed` |
| Semantic Fidelity | semantic 阶段 passed / (passed + failed)；口径是标题/角色/可编辑字段，不是科学数值 | 没有评估过的 semantic 阶段则 `not_computed` |
| Editable & Export Success | edit 与 export 都评估过的 case 中两者都通过的比例 | adapter 不驱动这些层（V2）则 `not_computed` |
| Family Coverage | 有执行过的候选的变换家族 / 已定义家族；另列失败家族记录数、已入语料数、已修复数 | 没有 explore 报告则 `not_computed` |
| 真实用户成功率 | 永远 `not_computed`：CI 没有真实用户数据，语料成功率不得当作它 | — |

**固定集合上的回归与范围扩张分开报**：只在「上次和这次都有、夹具字节完全相同」的 case 上给回归/改善结论；新增、删除、夹具变化的 case、
语料版本变化统一记为「范围变化」，**不同 case 集合的汇总比例不互相比较**，删掉难例只会被列为范围变化（并点名被删的非通过用例），
不会变成改善。环境（平台、Python、关键依赖版本）不同则**不给回归结论**（`incomparable`），状态变化只作为观察列出。固定集合里有 case 的任一端没有结论（`unknown` / `not_run` / `environment_unavailable` / `invalid_fixture`）时，没有回归的情况下结论是 `inconclusive`，不是 `no_regression`。没有上一份汇总时，
趋势与增删一律 `not_computed`。

---

# 附：CG-00 架构与缺口报告（2026-10-17，基线 main fb556e129，主控汇总）

五份只读审计（PR 栈、架构、执行策略、渲染、安全）的结论汇总如下；后续 PR 引用本节。

## A. 已有能力（复用，不重建）

- CompatBench 九级漏斗 + 六类产品分类 + baseline + 四档门禁：`compat_matrix.py`（`run_group`、`classify`、`GATES`）。
- V2 首图会话层：`compatbench_v2` / `compat_v2_scenarios` / fixtures；Foundation（预期集合先于执行、空集不通过、实例 id 绑 SHA）。
- #1002 `agent_verify.py`：SEVERITY 闭集、白名单、前置探针、进程树超时、环境清洗、原子写；`perf_agent.py` 的 incomparable / no_baseline。
- 执行策略：桌面准备会话内自动选 safe/native；运行后只有 `safe_file_operation_blocked` 可单向回退一次；受限来源在认领时降回 safe。
- 依赖：三通道安装名，表外 import 永不装；cv2 四选一仲裁（已装多个且无声明 → ambiguous 不装）。
- 下游编辑/撤销/重放/导出：5 条不变式 + 等价矩阵约 28 场景。

## B. 真实缺口（按影响排序）

产品（实测）
- **G1** show 后 close、无 savefig：safe 返回 `ok:true` 但 0 张图；`no_figures_captured` 不允许回退 → 用户「运行成功却没有图」。根因 `worker.py` 只 patch savefig。
- **G2** 同一路径被不同 Figure 反复 savefig：Tavotto 留第 1 张，native 磁盘是最后 1 张（静默错误结果）。根因 `figsession.py` first-wins。
- 两者所在文件被在飞 #1055 改动；修复 PR 排在 #1055 之后或与其作者协调。

安全（实测，假 runner）
- **S1** 显式 `execution:"native"` 无来源闸门：浏览器会话 / API 客户端可直接起 native，不经 nativeperm。**待维护者裁决**（建议单独安全修复 PR）。

测量层
- **M1** 原生对照只比像素和 stem，且复用产品 figcapture 命名；无退出码、文件副作用、执行次数、数据读写。→ 本 PR 的 `native_ref`（§4）。
- **M2** 真实执行次数不可见：`attempt_history` ≠ 执行次数；「缺包重跑 + blocked 回退」最多 3 次无测试。→ §5 的项目外计数器。
- **M3** V2 只走浏览器模式 → 桌面来源首图没有会话层指标；s04b/s05/s06 是案例 13 的反例对照，不是待修缺陷。
- **M4** runner 崩溃记成 `product_bug`（`compat_matrix.py` 主入口）；多个报告非原子写；成功时 CI 不上传报告；PR 档跳过阶段不算失败。Guardian 层已规避前三项，`compat_matrix.py` 本身未改。

覆盖
- **C1** CompatBench 零覆盖：style/rcParams/rc_context、animation、SubFigure、位图输出进九级漏斗、类方法 / `-m` 入口、import 别名、argparse、`sys.exit`、show+close、同路径多图。baseline 90% full_support 本身是偏向警报。
- **C2** manifest 无 provenance / license 字段。

## C. 前序 Codex 草稿栈结论

- #1118 CG-01：保留并完成（本 PR）。质量好、复用对；CI 红是两处真缺陷（subprocess 缺 encoding；`ci.yml` 新步骤未在 `ci_baseline` 登记）——Guardian 移出 `ci.yml` 后后者随之消失。
- #1119 CG-02：重做（语义回退「调用过即执行成功」；候选从不执行；变体仅参数级）。保留确定性 AST 模板与「不可自我授权」设计。
- #1120 CG-03：重做（diagnose 恒 unclassified、cluster 恒空、最小化只删顶层语句）。证据校验脚手架可留。
- #1121：保留，独立先行（注释锚点撤销修复，产品代码，不属于 Guardian 基础设施）。

## D. PR 计划与文件归属（全部不自动合并）

| PR | 范围 | 唯一 owner 文件 | 依赖 |
| --- | --- | --- | --- |
| #1121 | 注释锚点撤销修复（产品） | `engine/overrides.py` | 无，先行 |
| #1118 CG-01 | runner + schema + 独立 workflow + 本文 | `scripts/ci/compat_guardian*.py`、`compat_driver.py`（native_ref）、`tests/test_compat_guardian*.py`、`compat-guardian.yml`、本文 | 无 |
| CG-02（重做 #1119） | explore：保型/改轴两类 AST 变换、派生候选经 `run` 真执行、seed、结构去重；含「加 close/show」「同路径循环 savefig」以主动发现 G1/G2 | `scripts/ci/compat_explore*.py`、`tests/compat_guardian/templates` | CG-01 |
| CG-03（重做 #1120） | triage 根因映射 + 聚类 + 有界最小化（含拒绝错误缩减） | `compat_triage*.py` / `compat_minimize*.py` | CG-02 |
| CG-04 | G1 / G2 产品修复（各自独立 PR）+ Guardian 闭环证据 | `worker.py` / `figsession.py` | #1055 合入或协调 |
| CG-05 | nightly/dispatch 调度、趋势报告、只观察 | `compat-guardian.yml`（扩展） | CG-03 |
| 待裁决 | S1 显式 native 来源闸门 | `prepsession.py` / `app.py` | 维护者 |

Guardian 系列不改任何 `engine/` 文件。

## E. 已知限制（CG-01 如实留下的）

- **缺桌面来源变体**：`s04b/s05/s06/s07/s17` 声明的是「浏览器模式按设计不升 Native」；桌面路径由 #1028 的回退覆盖，
  但 V2 adapter 只走浏览器模式，所以**现在没有任何 Guardian 证据证明桌面路径真的能出图**。需要桌面来源变体才能补上。
- **s25 / G3**：partial 被投影成 completed、失败的图不可见，见 #1134；保持 `failed`，修复在 CG-04。

- V2 adapter 只覆盖扫描→标题语义；edit/replay/export/fidelity 是 `not_covered`，因此 V2 case 的最好结果是
  `partial_coverage`，不是 `passed`。引擎 adapter 覆盖九级，但**不**覆盖产品路由（专用路由测试仍是权威）。
- 语义对比目前只到图标题；科学数组、保存次数快照、RGBA 保真度、真 WebView2/WKWebView 都需要各自的证据。
- 语料是受控夹具，不是真实用户样本。
- 无签名：本机任何人可重算全部哈希伪造一份自洽报告；报告只作同一 job 内的证据，不据历史报告放行。
