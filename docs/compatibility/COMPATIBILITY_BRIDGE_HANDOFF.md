# Compatibility Bridge Session Handoff

> 每个 Session 结束前必须更新本文件。

## 当前状态

- 日期：2026-08-26
- 当前 branch：`compat/bridge-session03-script-probe`（worktree
  `.claude/worktrees/compat-bridge-session01`，stacked 在
  `compat/bridge-session02-execution-spec` → `compat/bridge-session01-audit`
  之上；三支均未推送——按审计 §七，Session 2–6 合成一个 PR 1 再走
  push → PR → merge）
- 基于 commit：Session 2 落库提交 `8d41f94`
- 本 Session Prompt：`04_SESSION_03_ARBITRARY_SCRIPT_PROBE.md`（外部实施包）
- 目标 PR：PR 1（本 Session 交付发现维 + 任意脚本 safe probe + 错误模型）
- 当前工作树状态：见 git log（本 Session 一个提交）

## 本轮唯一目标

让用户能把**任意项目根内合理的 `.py` 脚本**交给 safe probe，不要求静态扫描
先认出 savefig、entry 或 stem。只做后端、API 与类型接线；**不做 Runtime
Figure Asset，不做普通素材库 UI，不改 `tavotto open` 自动行为，不做
native run。**

## 已完成

- [x] **脚本清单**：`probe.script_inventory()`（唯一数据源）——项目内全部
  合理 .py，每条 `{script, registered, static_stems, entry_candidates,
  reason, can_probe}`；稳定 reason code 六种：`registered` /
  `static_candidate` / `dynamic_stems` / `no_static_output` /
  `infrastructure` / `unparseable`。walk 规则单一实现
  （`discover._iter_py` 两个视图：`iter_scripts` 起草口径原样 +
  `iter_all_scripts` 清单口径），被 prune 目录不列出。
- [x] `/api/registry` 响应新增 `all_scripts`（加字段；`candidates` 口径
  一字不变——负向反证确认起草范围没被顺手放宽）。
- [x] **任意项目内脚本 safe probe**：`/api/registry/probe` 路径校验重写，
  一律 realpath 之后判（`..` 回溯 / symlink 逃逸 / 项目外绝对路径 →
  `script_path_outside_project`；目录、非 .py → `unsupported_script_type`；
  不存在 → `script_not_found`）；项目内绝对路径规范化成相对键。解释器仍走
  pool runtime selection，请求体只收 script + cost。执行仍经
  `execspec.safe_spec()`（pool 是它的消费者，Session 2 不变式）。
- [x] **entry 候选静态化**：`discover.probe_entry_candidates()`（新增，
  绘图宽口径 `PLOT_FUNCS` 只喂 probe，不进起草）：main/render 零参可调
  才试；裸顶层绘图直接 `__main__`（show-only 脚本从「盲试 3 次」降到
  1 次执行）；自定义零参绘图函数按 `_ENTRY_RANK` 排序、上限 4；解析不了
  退回盲试 FALLBACK_ENTRIES。`probe.entry_candidates` 合并两级静态推断。
- [x] **结构化错误模型**：`probe.ERROR_*` 稳定码表 10 条（见下），错误对象
  `{code, message(中文回退), params, traceback(≤4KB 尾部)}`；worker 错误
  映射收敛（missing_dependency / worker_timeout→execution_timeout /
  session_dead→execution_cancelled / 其余→script_probe_failed）；失败不写
  注册表、不留半写文件；「第一个最有解释力的错误」语义保留。
- [x] **stem 冲突不静默覆盖**：产出 stem 已被另一份**仍在磁盘上的**脚本
  登记 → `multiple_stem_conflict` + `stem_conflicts` 映射，注册表零改动；
  归属脚本已不在磁盘的死条目照旧重登记（改名/删除后的重探测顺畅）。
- [x] probe 结果新增 `timings`（worker v1 build 计时透传）与
  `dropped_figures`（兜底超上限不再只进 worker.log）。
- [x] 前端：`api.ts` 新类型（CapturedFigureDescriptor / ProbeError /
  ScriptInventoryEntry / RegistryView.all_scripts / ProbeResult 重构）、
  `backendCodeMsg()`（code→当前语言，`backendErrorMsg` 的内核提出来）；
  RegistryDialog 增「全部脚本」折叠段（开发/高级验证入口，视觉最小），
  错误主文案按 code 翻译、traceback 收进「诊断详情」折叠块。i18n 中英
  各 +9 条 backend 错误文案 +10 条 dialogs 键；`resources.d.ts` 重新生成；
  `pnpm i18n:check` 全过。
- [x] 两个受管产物重建且 `--check` 一致（web/src 变了）：playground 指纹
  `44d264fa956732f5`、canvas.html 指纹 `92f10dc334517910`（本轮 canvas.html
  逐字节**有**变化——widget 打包了 api.ts/RegistryDialog）。PR 1 合并后要
  re-sync 网站仓库（`pnpm sync-playground`）。
- [x] 测试：`tests/test_script_probe.py` 38 项新增（清单 / API 安全边界 /
  entry 选择 / 错误模型 / 捕获结果 / 注册表效果 / 一次执行）；
  `test_projects.py` 越权用例改为逐 code 断言；`test_error_codes.py` 表
  +3 code；probe 码表←→双语文案的对拍用例（test_error_codes 纪律的延伸）
  放在 test_script_probe 里。四条负向反证完成（见下）。

## 未完成

- [ ]（无——本轮范围内全部完成）

## 本轮关键决策

### 决策 1：probe 的 stem 冲突从「静默权威抢占」改为显式报错

- 旧行为：`probe_and_register` 无条件调 `discover.register`，把别的脚本
  名下的 stem 摘走（「探测结果是权威的」）。
- 新行为：冲突对象**仍在磁盘上**时报 `multiple_stem_conflict`（带
  `stem_conflicts` 映射），注册表零改动；对象已不存在（改名/删除）时照旧
  重登记。`discover.register` 本身语义未动——PUT /api/registry 的手工裁决
  仍然整条替换，那条路是用户显式指认的归属，覆盖才是语义。
- 理由：两个脚本 fallback stem 同名（`panels/figure.py` 与 `misc/figure.py`
  都产 `figure`）时静默抢占会让先登记的那个凭空丢失登记。

### 决策 2：entry 候选顺序偏离 Prompt 的字面清单（__main__ 的位置动态化）

- Prompt 顺序：静态 entry → 常见函数名 → `__main__` → 自定义函数 → 顶层。
- 落地顺序：`__main__` 只在「有 `if __name__` 守卫或顶层代码够得着绘图」时
  排在自定义函数前；纯 def 库模块把它压到最后兜底。main/render 不再盲试
  （零参可调才进候选）。
- 理由：每试一个 entry 都是一次 worker 冷启动，而且**盲试不存在的 entry 也
  会把顶层整个跑一遍**（import 后 getattr 才炸）——show-only 脚本以前要
  白跑 2 次。产品目标（任意脚本可探测）不变，只是省掉浪费。
- 必填参数的 entry 刻意不试（Prompt 禁区「不自动构造必填参数」）。

### 决策 3：错误码表放 probe.py、app.py 用字面量

- `tests/test_error_codes.py` 的结构扫描认的是 app.py 里的**字面量**
  `"code": "..."`（那套门禁保证「每个 code 双语有文案且占位符对得上」）。
  所以 app 层三个路径校验码写字面量并登记进 USER_VISIBLE_CODES；probe
  层七个结果码（200 响应里的 result.error，不经 jsonify error 块）由
  test_script_probe 里的对拍用例延伸同一纪律。
- `execution_cancelled` 的现网 producer 是 workerd 控制面的 `session_dead`
  映射（本机 Python 池不产生；同步 probe 没有用户取消入口——真正的取消要等
  Session 4+ 的异步/SSE 化，届时复用该码）。

### 决策 4：旧 code `script_not_in_project` 退役

- 三种拒绝合并在一个 code 里说不清是哪种错；拆成 outside_project /
  not_found / unsupported_type 三个码后旧码无 producer，i18n 两侧文案与
  USER_VISIBLE_CODES 登记一并删除。HTTP 状态：outside/unsupported 400、
  not_found 404（旧实现三种全 404，`test_projects.py` 用例已按新契约改写）。

## 架构与数据契约

### 新增/修改接口

```text
engine/discover.py
  PLOT_FUNCS（绘图宽口径，只喂 probe 候选）/ MAX_PROBE_CUSTOM_ENTRIES = 4
  _iter_py(include_infrastructure=…)   # walk 唯一实现
  iter_scripts() = 起草视图（口径不变）；iter_all_scripts() = 清单视图
  probe_entry_candidates(path) -> list[str] | None（None = 解析不了）
  is_infrastructure_name(name)          # SKIP_* 两张表的唯一消费口
  rel_key(path, root)                   # _rel_key 公开化（写法唯一出处）
  _reaches_calls(fn, funcs, names)      # _reaches_save 的一般化

engine/probe.py
  ERROR_*（10 个稳定码）/ REASON_*（6 个清单码）
  probe() -> {script, entry, stems, descriptors, tried,
              error: None | {code, message, params, traceback},
              timings, dropped_figures}
  probe_and_register() -> 上述 + {registered, stem_conflicts?}
  script_inventory(figures_dir, registered=None) -> list[条目]
  entry_candidates()  # analyze_script 静态 entry + probe_entry_candidates 合并
```

### API/协议形状

```jsonc
// GET /api/registry：新增键（其余原样）
{ "all_scripts": [{
    "script": "panels/figure.py", "registered": false,
    "static_stems": [], "entry_candidates": ["__main__"],
    "reason": "no_static_output", "can_probe": true }] }
// POST /api/registry/probe 的拒绝（HTTP 400/404 + app 错误约定）：
//   script_path_outside_project(400) / unsupported_script_type(400) /
//   script_not_found(404)，params 均含 script
// POST /api/registry/probe 成功（HTTP 200）：probe_and_register 结果原样，
//   error 为 null 或 {code, message, params, traceback}
```

worker/browser 协议**零改动**（descriptors/dropped_figures/timings 都是
Session 2 与更早就有的响应字段，本轮只是透传出 probe）。

### 稳定错误码（本轮新增）

```text
script_path_outside_project   app 层（400）
script_not_found              app 层（404）+ probe 引擎层
unsupported_script_type       app 层（400）
script_probe_failed           probe（params: entry, reason）
script_no_figure              probe（params: entry）
missing_dependency            probe（params: module；worker 同名码的透传）
execution_timeout             probe（worker_timeout 的映射）
execution_cancelled           probe（workerd session_dead 的映射；见决策 3）
invalid_entry                 probe（显式 entries 非法；app 层 PUT 早已有）
multiple_stem_conflict        probe_and_register（params: detail；
                              结果另带 stem_conflicts 映射）
```

### Schema/version 变化

- 用户文档 schema：零改动。注册表文件格式：零改动。
- 协议版本不升（响应加字段，ADR 0003 §1）。

## 修改文件

| 文件 | 修改原因 | 是否有测试 |
|---|---|---|
| src/tavotto/engine/discover.py | walk 单实现两视图 + PLOT_FUNCS + probe_entry_candidates + rel_key 公开 | test_script_probe + test_discover（原样绿） |
| src/tavotto/engine/probe.py | 错误码表 + 结构化 probe + 冲突守卫 + script_inventory | test_script_probe + parity（原样绿） |
| src/tavotto/app.py | /api/registry 加 all_scripts；probe 端点路径校验重写 | test_script_probe::TestRegistryApi/TestProductApiProbe + test_projects |
| src/tavotto/AGENTS.md | 记录 Session 3 语义 | 文档 |
| tests/test_script_probe.py | 新增 38 项 | — |
| tests/test_projects.py | 越权用例逐 code 断言 | — |
| tests/test_error_codes.py | +3 app 层 code、退役 script_not_in_project | — |
| web/src/lib/api.ts | 新类型 + backendCodeMsg | pnpm test（913 项） |
| web/src/components/RegistryDialog.tsx | 全部脚本折叠段 + 结构化错误展示 | pnpm test |
| web/src/i18n/locales/*/{errors,dialogs}.json + resources.d.ts | 双语文案 | i18n:check + test_error_codes + probe 对拍用例 |
| web/dist-playground（gitignore）/ codex-plugin/mcp/widget/canvas.html | 受管产物（web/src 变了） | 各自 --check |
| docs/compatibility/COMPATIBILITY_BRIDGE_HANDOFF.md | 本文件 | — |

## 实际运行的测试

```bash
# worktree 内、PYTHONPATH=src、主仓 .venv 解释器（worktree 无 .venv）
PYTHONPATH=src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest \
    tests/test_script_probe.py                        # 38 passed
PYTHONPATH=src …/python -m pytest tests/test_discover.py \
    tests/test_error_codes.py tests/test_projects.py \
    tests/test_compat_capture_parity.py               # 全过
PYTHONPATH=src …/python -m pytest tests/test_execspec.py tests/test_handoff.py \
    tests/test_worker_protocol.py tests/test_workerd_pool.py   # 129 passed
PYTHONPATH=src …/python -m pytest -q                  # 全量（见下）
python scripts/build_browser_playground.py && … --check   # 44d264fa956732f5
python scripts/build_mcp_widget.py && … --check           # 92f10dc334517910
.venv/bin/python scripts/ci/compat_matrix.py --smoke      # 见下
cd web && pnpm test（82 文件 913 项）&& pnpm build && pnpm i18n:check
```

（坑复述：compat_matrix 必须用带 flask 的解释器；worktree 用主仓
`.venv/bin/python` 绝对路径，别 cd 出 worktree。）

## 负向反证（本轮四条，全部先红后还原）

| # | 变异 | 判据测试 | 结果 |
|---|---|---|---|
| 1 | probe 端点重新要求「必须是静态 candidate」（analyze_script 为 None 就 404） | `TestProductApiProbe::test_show_only_script_probes_via_the_product_api` | **红**（还原后绿） |
| 2 | 路径校验去掉 realpath（`resolve()` 换 normpath） | `TestRegistryApi::test_symlink_escape_is_rejected` | **红**（还原后绿） |
| 3 | probe 成功路径改成 invalidate（成功后重新 build） | `test_a_successful_probe_executes_exactly_once` | **红**（计数 2 ≠ 1；还原后绿） |
| 4 | probe 只返回第一张 descriptor/stem | `test_multi_figure_returns_every_descriptor_in_order` | **红**（还原后绿） |

## 真机/产品证据

- OS：macOS（arm64，本机开发环境）。产品 UI 变化仅 RegistryDialog 的折叠
  「全部脚本」段（开发/高级验证入口）；素材库、`tavotto open`、MCP 均未触碰。
- workerd 腿本机 skip（未 cargo build）：probe 走 Python 池实测；
  `session_dead → execution_cancelled` 的映射在 workerd 腿是代码级确认。

## 已知失败与限制

| 问题 | Stage/Route | 严重度 | 是否本轮 | 后续 |
|---|---|---|---|---|
| probe 登记但无磁盘产物 → 素材库不可见（show-only 登记成功后素材面板仍没有它） | asset_model × desktop | 高 | 否（既有；本轮把「登记」打通了，「可见」是 Session 4 的 RuntimeFigureAsset） | Session 4（ADR 0013） |
| `tavotto open script.py` 仍不自动 probe | product_entry × cli | 高 | 否 | Session 6 |
| probe 是同步阻塞请求：分钟级脚本没有进度/取消（`execution_cancelled` 码已备好，producer 只有 workerd session_dead） | product_entry × desktop | 中 | 否（审计风险 #4） | Session 4/5（SSE 化时复用码表） |
| 清单每次 /api/registry 现算（每脚本 2 次 AST parse）；数百脚本项目约几百 ms | 性能 | 低 | 是（记录在案，未做缓存） | 需要时再谈 |
| `script_inventory` 的 reason 只有六档，show-only 与「纯计算脚本」同为 no_static_output（运行后者得 script_no_figure，成本一次冷启动） | 发现维 | 低 | 是（诚实边界：静态分不出「会不会出图」） | 不修 |
| CompatBench 产品路由维仍未建（本轮 API 用例是 pytest 侧的产品路由覆盖） | ci | 中 | 否 | Session 6 |

## 不得被下一 Session 破坏的约束

- Session 2 的全部约束仍然有效（ExecutionSpec/描述符唯一语义、writeback
  只派生、legacy 信封零改动、记录在案的浏览器/桌面差异）。
- **发现维两个视图共用一个 walk**：`iter_scripts`（起草）与
  `iter_all_scripts`（清单）只差 infrastructure 过滤——新增 prune/深度规则
  改 `_iter_py` 一处；**起草口径（SAVE_FUNCS + SKIP_*）不得因清单需求
  放宽**（负向反证 #1 与 `test_inventory_does_not_widen_the_static_draft`
  看护）。
- **probe 错误码是契约**：`probe.ERROR_*` 与双语文案的对拍用例
  （`test_probe_error_codes_have_text_in_both_languages`）必须随码表同步；
  新码先加表、加文案、加对拍，再有 producer。
- **成功 probe 一次执行**：Session 4 的 RuntimeFigureAsset 取预览/登记/
  materialize 必须继续复用热会话（execution-count 用例看护）；失败路径
  不写注册表、不留半写 cache。
- **路径校验一律 realpath 后判**（symlink 用例看护）；probe 请求体不接受
  interpreter 之类的执行参数。
- **stem 冲突显式化**：任何新登记入口（Session 5 素材库、Session 6 CLI）
  复用 `probe_and_register` 的冲突守卫，不得绕过去直接调
  `discover.register`（那是手工裁决专用）。
- 清单/注册表键的路径写法只有 `discover.rel_key` 一个出处。
- web/src 或引擎四模块动了就重建 playground + canvas.html 两个受管产物。

## 下一 Session 唯一目标

> 把 probe 返回的 live Figure 建模为正式 RuntimeFigureAsset：稳定 ID
> （`runtime:<script>#<stem>`，descriptors 已带）、materialized cache、
> 保存/重开、重新运行、导出与正确 writeback capability；暂不做完整素材库
> UI。（ADR 0013 定稿并落地。）

## 下一 Session 首先阅读

```text
AGENTS.md / CLAUDE.md（含 src/tavotto/AGENTS.md 渲染引擎核心机制一节）
docs/compatibility/COMPATIBILITY_BRIDGE_MASTER_PLAN.md
docs/compatibility/COMPATIBILITY_BRIDGE_HANDOFF.md（本文件）
docs/adr/0013-runtime-figure-assets.md（Proposed → 本轮要定稿）
docs/compatibility/compatibility-bridge-audit.md §八 风险 1/2（asset id 与
  「素材=磁盘文件」假设的消费点清单——修判据必须 sweep 全部消费点）
src/tavotto/engine/figcapture.py（descriptor / runtime_asset_id）
src/tavotto/engine/probe.py（本轮产出：结果形状与错误码）
src/tavotto/app.py（scan_panels / safe_resolve / /api/engine/render 一族）
web/src/types/document.ts（PanelObject.fileId/fileKind——schema 表达缺口）
web/src/store/assetStore.ts / renderStore.ts（「新 fileId 形态」接入点）
```

注意：审计风险 #2——「素材 = 磁盘文件」假设散布在 `scan_panels`、
`safe_resolve`、`assetStore`、`renderStore` 键、写回对话框、导出合成、
包检视；RuntimeFigureAsset 接入时漏改一个消费点就是「面板显示了另一个
面板的图」级别的错位。决策 5（Session 2）的 `original_artifact` 只查项目
根一层与 `scan_panels` 递归口径的差异，也要在这轮一起裁决。

## 建议启动命令

```bash
git status --short && git log -8 --oneline
PYTHONPATH=src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest -q \
    tests/test_script_probe.py tests/test_compat_capture_parity.py
/Volumes/Projects/Tavotto/.venv/bin/python scripts/ci/compat_matrix.py --smoke
```
