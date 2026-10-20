# Compatibility Guardian —— 兼容性证据 runner、Agent 接口与缺口报告

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
python scripts/ci/compat_guardian.py summarize REPORT [REPORT ...] [--json-out F] [--markdown-out F]
python scripts/ci/compat_guardian.py promote REPORT --case ID --out FILE     # 只出提案
python scripts/ci/compat_guardian.py explore --seed N --out DIR [--limit N] [--parent a,b] [--static-only]
python scripts/ci/compat_guardian.py triage REPORT [REPORT ...] --out FILE
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

普通 `python script.py` 语义（cwd = 脚本目录），只观测不干预；**独立实现，不 import 产品的
`figcapture`**，图的身份只用 Figure 对象本身、文件按原样记路径。事实：

`exit_status`（自然结束 0；`SystemExit(code)` 记 code；异常 1）、`error`（类型 + 截断 traceback）、
`figure_count`（被 `savefig` 的不同 Figure ∪ 退出时 pyplot 仍开着的）、`figures_saved / figures_open_at_exit /
savefig_calls`、`same_path_distinct_figures`（不同 Figure 写同一路径——静默覆盖的信号）、
`artifacts[]`（新增/改动文件的字节数、sha256、**能否读回**：图像用 PIL 完整解码，PDF 验头，SVG 验 XML）、
`side_effects{created,modified,deleted}`（项目目录整体快照的前后差）、`semantics`（各图标题）、
`script_entries`（驱动自己数到的入口次数，仅作交叉核对）。

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

**变换（一次只动一个轴；少量二因子组合按 seed 抽样）**

| 家族 | 变换 | 轴 | 类别 |
| --- | --- | --- | --- |
| show_close | `show_close`（去 savefig，末尾 show()+close('all')，**G1**）、`show_only` | output_call | 改轴 |
| same_path_multi_figure | `same_path_loop`（循环里 N 张图写同一路径，**G2**） | output_name | 改轴 |
| import_alias | `import_alias`（pyplot/numpy 别名） | import_alias | 保型 |
| entry_form | `wrap_main`（顶层改 `main()` + `__main__` 守卫） | entry | 保型 |
| argparse_defaults | `argparse_out`（保存路径来自 argparse，默认值=原路径） | args | 保型 |
| data_source | `inline_to_file`（第一个字面量数据列表改读相对 JSON） | data_source | 保型 |
| style_rc | `rcparams`、`style_use` | style | 改轴；**仅静态**：compare 只看图数/标题/路径，观察不到样式，执行只会得到假的 `no_difference`，所以含这两步的候选恒为 static_only 并在报告里点名 |

保型：native 输出（图数、标题）必须等于父本——执行器同时跑父本 native 核对，不等就是 `invalid_candidate`（模板有 bug，
不是 Tavotto 缺陷）。改轴：只能与这个变体自己的 native 比。所有改动都是 AST 编辑，结果必须能 `compile`。
同 seed 逐字节相同（参数由 `seed + 父本 + 变换名` 的哈希派生，不依赖其它候选）。

**比较（`compare`，纯函数）**：`zero_figures`（原生 N 图 / Tavotto 0 图）、`fewer_figures`（partial_capture）、
`same_path_content_mismatch`（原生最后写入该路径的图 vs Tavotto 保留的图）、`title_missing`。差异带 Guardian 失败类别。
Tavotto 侧走产品自己的发现链（静态注册表，不足时试运行探测）再起一次性 worker 取 manifest。
`show()` 之后 `close()` 的图原生算「出现过」（`native_ref` 记录 show 时的在开图）。

**结构去重与覆盖**：按「轴向量 + 规范化 AST 签名」去重（数字/字符串常量与 `cg_plt3` 之类别名数字归一，所以仅参数不同不算新结构）。
报告 `coverage` 给出：每个家族的轴、适用父本数、候选数；每个轴的候选数；保型/改轴数；去重前后；以及父本按语料类别的分布
对比整个语料——**父本全是已经通过的 `script_shapes`，`core_artists / axes_layout / metamorphic / scientific_stack` 为零**，
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

最小化过程中暴露并修掉了 explore 比较里的一个误报：无标题的图（原生 `[""]` vs Tavotto `[]`）曾被当作同路径内容差异；
最小化签名因此新增「原生确有多图写同一路径」这一条，缩减不能漂移到别的原因上。

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
