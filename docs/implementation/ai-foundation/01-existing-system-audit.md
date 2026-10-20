# 01 · 现有系统审计（代码事实表）

基线：origin/main `494d147db`（2026-10-19）。来源：五路只读审计（运行时 / 验证 / 产品接入 / 安全 / Harness²+基准），
行号取自审计当时。标 ✅ 的条目由主会话在本机亲自复核过；其余是审计代理读源码得出的结论，**没有运行**——
用到某条之前先核一次（`docs/rules/repo/predicate-subject.md`：引用的权威也要复核）。路径省略 `src/tavotto/` 前缀。

## 一、AI 任务运行时

| 位置 | 事实 |
| --- | --- |
| `app.py` `api_ai_run` → `_ai_run_impl` | 外壳用 `ai_bridge.reset_spawn_mark/spawn_started` 区分 spawn 前后：前失败删 pending 行；后失败一律 503 `ai_request_unresolved`、账本不删 |
| ✅ `engine/ai_bridge.py` `run()` | `require_usable` → `with script_guard, _REQUEST_LOCK`（锁序固定）→ 去重 / 忙碌判定 → 快照 `copy2` 到 `data_dir/cache/ai_snapshots/{sid}__{name}` + sidecar → `_build_prompt` → **先** `ai_history.record_start` **后** `Popen(cwd=figures_dir)` |
| ✅ 同上 | **Agent 直接改原脚本**；「只修改目标脚本；不要运行脚本；不要修改其他脚本或数据文件」只是提示词第 3 条（`_build_prompt`），没有任何强制；diff 只比目标脚本 |
| `ai_bridge._pump` | stdout 逐行 `classify_event` → SSE `ai.delta{kind∈delta/message/thinking/action}`；`TIMEOUT_S=900`；结束后 `_record_after`（`after_sha256`）、unified diff、`refresh_outcome`、`_finish_session`（终态字段唯一写入点）→ `ai.done` |
| 会话 status 全集 | running / done / failed / timeout / cancelled / interrupted / reverted；`FINAL_STATUSES={done,failed,timeout,interrupted}`；refresh.status ∈ skipped / not_wired / ok / failed |
| 取消 / 中断 | `cancel()` killpg / `taskkill /T /F` 后置 cancelled，`_pump` 仍补齐 diff 与刷新；`interrupt_all()` 关停时；启动时 `ai_history.mark_interrupted_running` |
| `engine/ai_agents.py` | `AGENT_REGISTRY=(CodexAgent(), ClaudeAgent())`；Codex：`exec -C cwd --json --sandbox workspace-write --skip-git-repo-check …`；Claude：`-p … --permission-mode acceptEdits --output-format stream-json …`；`spawn_env()` 全量继承 `os.environ` + 补 PATH |
| `engine/ai_providers.py` | 第三方接口按协议族在 spawn 时注入（环境变量 / `-c` 覆盖），绝不改 `~/.claude` / `~/.codex`；依赖方向 `ai_agents ← ai_providers ← ai_bridge` |
| `engine/ai_history.py` | `data_dir/cache/ai_history.sqlite3`；表 `sessions`（含 `client_request_id`、`owner_pid`、`refresh` 等迁移列）、`ai_requests`（幂等账本，主键 (project, rid)，墓碑 `~superseded`）、`ai_drafts`、`ai_pending` |
| 同上：迁移 | **没有 `user_version`**：每次 `_connect` 执行 `_SCHEMA` + `_migrate`（`PRAGMA table_info` 看缺哪列再 `ADD COLUMN`）。TAF 加列必须沿用 |
| 同上：跨进程 | `BEGIN IMMEDIATE` + 唯一约束；`record_start` 同事务预留 rid + 认领脚本（属主存活即 `ScriptBusy`）；属主判定 `owner_alive(pid, started_ms)`，24h 上限 |
| 被测试钉住的结构 | `SESSION_DTO_KEYS` / `SESSION_TERMINAL_FIELDS` / `SESSION_INTERNAL_KEYS`：新增会话字段必须在三者之一表态 |
| `engine/scriptlock.py` | 进程内 `RLock`（键 `normcase(realpath)`，**不跨进程**）；`write_script` 未持锁即抛；调用方清单在文件头并由 `tests/test_script_lock.py` 对账 |
| `engine/scriptbackup.py` | `replace`：校验 sha256 → 两处备份（项目内 `tavottofile/script-backups/` + 数据目录镜像）都 fsync → `atomicio` 原子替换；保留 20 条 |
| `ai_bridge.revert` / `_refuse_stale_revert` | `contained_path` 钉项目内；有 `after_sha256` 且磁盘内容 ∉ {快照, after} → 409 `ai_revert_conflict`；无记录放行（「不知道」一档） |
| `engine/execpolicy.py`（0127） | `AttemptLedger`（内存、有界 16、不持久化）+ `RetryGuard.admit`：native 后不回 safe、safe→native 至多一次、同 `SpecKey`+同根因拒（`no_new_root_cause`）。只挂在 `prepsession` 上，**没有可直接给任务预算用的通用计数器** |
| `engine/runroute.py`（0133） | 静态证据决定 safe / native，只在用户发起的 `preparation.plan_for` 里调用；CLI / MCP 不得 import（测试钉住） |
| 进程边界 | 上述父进程侧模块纯标准库（会 import config / projectenv / atomicio 等兄弟模块）；`execpolicy` import `pool` |

## 二、验证可复用件

| 位置 | 事实 |
| --- | --- |
| ✅ `rendercore/inspector.py` | `VERDICTS=(verified, failed, unknown, not_applicable)`——与 TAF 四态一字不差；模块只 import 标准库；`public_projection()` 是唯一允许离开本机的投影 |
| `engine/manifest.py` 顶层 | `stem / size_mm / elements[] / font_families / font_family_names`，可选 `frame / groups / unsupported[]`；只在 worker 子进程生成 |
| 元素 | `gid`（位置式 `axes_i.lines_j`）、`role`、`editable[]`、`identity`（`overrides.artist_identity`，只认显式 label 的哈希；0083 刻意不含数据摘要）、`bbox`（figure 分数、y 向下）、`clip_bbox`、`geometry` |
| ✅ `manifest._line_fields` | 只有 label / color / linewidth / linestyle / alpha / marker / zorder 等样式；**没有 x/y 数据，也没有数据指纹** |
| `pathgeom.element_geometry` | figure 分数折线点，RDP 抽稀 + `TOTAL_BUDGET=8000` + `MAX_MARKERS=500`：有损显示形状，**不能证明数据未变** |
| axes 字段 | `xlim / ylim / xscale / yscale / invert_x`；轴标题、标题是独立 text 元素；单位无专门字段 |
| ✅ `engine/preflight.py` | `run(spec, profile)`、`spec_from_manifest`、`summarize()`（error / warn / not_verifiable / suggestion + blocking）；纯标准库；字号、出界（`element_overflow`，容差 0.3 mm）在这里 |
| ✅ `engine/interference.py` | `detect(manifest, profile)`：text-overlap / text-over-axes / legend-over-data；纯标准库 |
| `engine/normalize.py`（0051） | `build_contract` 记 B0、`compare(contract, manifest)` 逐项比对，`protected_snapshot` / `manifest_hash` 可复用 |
| `engine/specfix.py`（0080） | preflight gid → 计划 → 真实渲染 → `verdict`（done / nothing_to_do / not_resolved）；`/api/engine/specfix` 返回最终全量 patches 由前端一次 commit |
| ✅ `app.py` 写回 verify | `_compare_manifests(hot, fresh)`（`REPLAY_GEOM_TOL=0.005`、`REPLAY_SIZE_TOL=0.01`）+ `_replay_pixel_diff`（`REPLAY_PIXEL_WIDTH=1000`、`REPLAY_PIXEL_TOL`）；纯函数但住在 `app.py`，`scripts/ci/compat_matrix.py` 已 import 复用 |
| `scripts/ci/compat_matrix.py` `stage_replay` | 「热态 == 清空重放 == 全新 worker 重放」三方对照 + `_prop_diffs`，是最接近「可调用重放一致性检查」的现成件 |
| 缺口 | 无通用 `diff_manifest(a, b)`；无数据级 diff；无多子图边对齐检查；重放检查比的是「热态 vs 重放」，不是「改前 vs 改后」 |

## 三、产品接入面

| 位置 | 事实 |
| --- | --- |
| `web/src/components/ai/AiPanel.tsx` | `send` 先 `draftSnapshot` → `aiStore.start` 写后端提交账本 → 成功后才 `aiRun`；目标在第一个 await 前固定（`pinned`、`pinnedProject/pinnedGeneration`） |
| 请求体 `AiRunRequest` | `agent, id, prompt, gid, label, overrides[], model, effort, scope, target, canvas, client_request_id`；**不带 `identity`**（gid 会随脚本改动漂移） |
| SSE 消费 | `useServerEvents.ts` 的 `ai.delta` / `ai.done`；早到事件按 rid 暂存回放；结果只写状态栏（`status.ai*`），不加横条 |
| 前端状态 | `AiStatus = running/done/failed/timeout/cancelled/reverted`；提交相位 `sending/unconfirmed/not_received`；**没有**「理解需求 / 检查中 / 部分完成 / 无法验证」 |
| 结构化编辑 | override 归前端 `documentStore`，⌘Z 一步撤回要求一次 commit；后端 `/api/engine/render` 只算不写；**没有**「把字号改成 X」的服务端接口 |
| 新建图 | 无「新建图」UI；脚本落进项目目录 → `refresh_project`（AST 静态识别 stem）→ 识别不到走 `probe_and_register`（会执行用户代码，需确认） |
| MCP（`codex-plugin/mcp/tavotto_mcp`） | `open_figure / apply_overrides / normalize_figure / preflight / export / verify_replay / refresh_project / session_state / close_session / health`；会话在 MCP 进程里、不进用户 undo 历史、无 rid——**复用 `bridge.py` 的函数语义，不复用传输层** |
| 指导文档体积 | 根 `AGENTS.md` + `web/AGENTS.md` 拼接距 32 KiB 硬线不到 10 B；`src/tavotto/AGENTS.md` 也不足 100 B——TAF 的规则全文只能放 `docs/rules/` 细则，速查表加行前要先腾字 |

## 四、安全面（详见 `04-security-model.md`）

| 位置 | 事实 |
| --- | --- |
| `security.py` | 全局 `before_request` 守卫；新端点挂在同一 app 上即受保护，不得加 `_PUBLIC_PATHS` |
| `app.safe_resolve` | 只放行 PDF / 图片后缀——**不能**拿来校验脚本或数据路径；脚本 / 数据用 `projectenv.contained_path`（先 realpath 再前缀判定） |
| safe 档（`engine/worker.py`） | 进程内猴子补丁守卫（unlink / remove / rename / rmtree / write_text …），cwd 切临时目录；**不限网络、不限读、不拦子进程、不拦 `open(abs, "w")`**；注释自称「不是沙盒」 |
| `_desktop_initiated()` | 桌面模式 + 无 api_secret + 有效 cookie 三者齐备；Agent 驱动的流程不属于桌面用户发起 |
| `engine/telemetry.py` | `EVENTS` 闭表（bool / 有界 int / 枚举 / 版本串）；AI 相关只有 `ai_assistant_invoked{agent}` 与 refresh 的 source 枚举；`CONSENT_VERSION=3` |
| `ai_history.sqlite3` | 明文存 prompt；未发现进诊断包的路径 |

## 五、Harness²（google-research/harness2，HEAD `1cfe254e8`，只读源码、未执行）

| 位置 | 事实 |
| --- | --- |
| 许可 | Apache-2.0，无 NOTICE；源文件头 "Copyright 2026 Google LLC" |
| `core/recursion.py` | 任务内 Propose–Probe–Compose：`run_parallel` 并行 2k 提议（通用 + 领域）→ `_roll_many` 逐个重跑 → k 个聚合会话合成 → 再各跑一次；`_accept_tree` 校验含 `assert_tree_has_no_grader_verdicts`；失败带原因重试一次后回退并记 `fell_back` |
| `core/selection.py` | 候选匿名化盲选；选择器失败回退到 base 不抛异常；`reflect_adoption` / `_distil` 提炼经验 → `core/library.py` |
| `core/library.py` | 纯文件系统经验库 `<tag>/<mode>/<domain>/harnesses/<task>/{harness/, EXPERIENCE.md}` + `INDEX.md`；条目缺 cue / lesson / evidence 任一即丢弃 |
| `core/ledger.py` | rollout / session 两类条目（usd、cached、attempts）；`assert_identity` 校验预算恒等式与标签不重复 |
| `core/context.py` | `assert_no_grader_verdicts` / `assert_no_leak_paths`：评分结果不进选择、反思、入库 |
| 不适合的部分 | 在线自修改 harness 需无限制联网（README 明说关掉 Codex 沙箱）；OpenCode / Codex 执行器；Vertex / gcloud；仅 Linux |

## 六、基准资产

| 位置 | 事实 |
| --- | --- |
| `tests/compat/manifest.json` | CompatBench：149 case / 79 脚本，每条带 `semantic_expectations{roles_present, editable}` 与 `mutations`；数据写死 |
| `tests/acceptance/` | c01–c03 + 像素容差三指标基线 |
| `tests/test_ai_agents.py` | `_FakeAgent(AgentDefinition)` 经 monkeypatch 注入注册表——Fake Executor 的现成桩，没有独立的假 CLI 可执行文件 |
| `scripts/ci/agent_verify.py`（0131） | 场景白名单 + 结果闭集 `passed/failed/error/environment_unavailable/not_run/skipped/not_reproduced` + `report.json`（commit、环境、证据 sha256、未验证项、隐私声明） |
| `tests/fixtures/foundation/` + `tests/test_foundation_fixtures.py` | 「真值手写、标准库核对、临时副本原生执行、夹具快照不变」的夹具纪律——FigureBench 照搬 |

## 七、Phase 0 本机实测（✅）

| 命令 | 结果 |
| --- | --- |
| `.venv/bin/python -m pytest tests/test_figurebench_fixtures.py -q`（`PYTHONPATH=$PWD/src`，matplotlib 3.11.2，Python 3.13） | 32 passed |
| 六个手工变异（反例 `must_fail` 改指一条成立的断言 / 参考解改错 / CSV 改值 / 编辑落点计数改错 / 脚本数据与真值不符 / 目标在基线上已满足） | 六个全部在预期那条断言上红（退出码 1），还原后 `git status` 干净 |
