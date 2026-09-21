# U04 PR C · 变异反证（2026-09-21，macOS arm64，应用 `.venv` 3.13.11；worker 解释器 3.11.14 / mpl 3.11.1）

脚本：session scratchpad `u04_mutate_c.py`（每条：改一处判据 → 针对性用例 → 退出码 → 还原 + 清 `__pycache__`）。

| # | 变异 | 文件 | 用例 | rc |
|---|---|---|---|---|
| C1 | `gate` 无视 skip | deprepair | test_dependency_transaction::test_skip_is_an_answer… | 1 |
| C2 | blocked 也问 | deprepair | …::test_gate_asks_only_when_the_plan_is_ready | 1 |
| C3 | 门不登记到 `pool.SPAWN_GATES` | deprepair | …::test_spawn_gate_is_registered… | 1 |
| C4 | `pool._new_worker` 不跑门 | pool | test_dependency_repair_e2e::test_golden_path…（真 HTTP 试运行：门先问） | 1 |
| C5 | 准备接口把门当 error | preparation | …::test_probe_and_preparation_project_the_door_as_needs_input | 1 |
| C6 | `plan_for` 不问 | preparation | test_foundation_dependencies::test_fo20_…（真 HTTP） | 1 |
| C7 | 试运行把门压成 script_probe_failed | probe | …::test_probe_and_preparation_project_the_door_as_needs_input | 1 |
| C8 | MCP 不投影 recovery | bridge | test_mcp_server::test_open_projects_the_dependency_preparation… | 1 |
| C9 | MCP `skip` 也去装 | bridge | test_mcp_server::test_prepare_dependencies_target_is_a_closed_set… | 1（脚本第一版目标串不在——skip 那段当时没写进 bridge，补上后红） |

前端（vitest，`DependencyPrepareDialog.test.tsx` 9 条）：`prepare` 不发 `/prepare`、`done` 不关框、skip 不 POST 各由用例钉着（改 store 那三处各红，手工各跑一次）。

9/9 红；还原后 TestGate 4 passed。

## 经真实公共入口的场景（`tests/test_foundation_dependencies.py`）

| case | 结果 | 关键数字 |
|---|---|---|
| FO20 | pass（guided） | 门以整份计划回来：requirements = alpha[wide]==1.0 / beta<2 / gamma；一次授权装 4 个（含 extra 拉进的 wide），never / train 不装；ylim == 真值 ± 5%；ylabel 一次编辑生效 |
| FO31 | pass（automatic） | installs = 1；二开 existing_runtime 同 generation；重启后 nothing_needed、freeze 逐字不变、generation 1 |
| FO21 | pass（safe_stop） | blocked = dependency_conflict（beta<2 vs beta>=2）；绑定 409；freeze / 声明文件不变；三个包一个没装 |
| FO22 | pass（safe_stop） | pip 退出 0 → 关键 import 失败 → dependency_import_still_failed（错误含真实 ImportError）；门一直问；skip 后跑一次以 missing_dependency 收场；轮次不减 |
| FO27 | pass（safe_stop） | 慢 find-links 服务（.whl 先睡 6 s）下 installing 阶段取消 → cancelled / dependency_install_cancelled；freeze 不变；计划一次性；门仍问 |
| FO18（observing，本机联网跑过一次） | pass（guided） | 项目没 venv → 目标受管 → 一次授权建新一代（真装 matplotlib + numpy + 三个包，wheelhouse 由 `pip download` 取）→ activated → ready → ylim == 真值；`/api/engine/environment` 的 `project.managed.active_generation` == 那一代 |
| FO05（observing，本机联网跑过一次） | pass（automatic） | 项目声明 h5py（真实二进制 wheel）→ 一次授权装进受管新一代 → 脚本用真 h5py 写 / 读 HDF5 → ylim == [3.5, 7.0, 10.5] ± 5% |

## Harness 三步（lane `pr`，本机）

`expected --lane pr` → 12 个实例 / 21 条 planned；六个用例文件按 enforced 集合跑（`EXIT=0`）→ `validate`：
**预期 12 · 提交 12 · 有效 12**；verdict 全 pass；按产品结果 automatic 5 / guided 3 / safe_stop 4
（automatic + guided = 8；safe_stop 的通过不计入自动成功）。U04 的五条：FO20 guided、FO31 automatic、
FO21 / FO22 / FO27 safe_stop。FO18 / FO05 observing 不在 `pr` lane 的预期里，联网各跑一次的证据见上表。
