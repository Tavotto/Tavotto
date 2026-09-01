# STATUS — 产品体验、可靠性与输出一致性改造

**唯一的进度事实来源。** 每个 Session 结束时更新，整段重写，不半新半旧。

---

## 基线

| 项 | 值 |
| --- | --- |
| 分支 | `feat/product-ux-reliability-v2` |
| 起始 commit | `ef9ac02`（`origin/main`，2026-08-29） |
| worktree | `.claude/worktrees/product-ux-v2` |
| Prompt 套件 | `Tavotto_Product_UX_Reliability_Phased_Prompts_v2`（共享规则已复制为 `00_SHARED_RULES.md`） |

### 起始基线测试结果（Session 01 实跑）

| 命令 | 结果 |
| --- | --- |
| `PYTHONPATH=<wt>/src <repo>/.venv/bin/python -m pytest -q` | ✅ exit 0 —— 3023 passed / 34 skipped / 0 failed |
| `cd web && pnpm test` | ✅ exit 0 —— 115 files / 1338 tests passed |
| `cd web && pnpm build` | ✅ exit 0 —— `tsc -b && vite build`，2773 modules |
| `cd web && pnpm i18n:check` | ✅ exit 0 —— zh-CN 2524 / en-US 2609 条，无问题 |

> worktree 里跑 pytest **必须**带 `PYTHONPATH=<worktree>/src`：`.venv` 是对
> 主工作区的 editable 安装，不带就会 import 到主工作区当前分支的代码
> （子进程尤其明显，同一次跑里会出现两个不同版本的 tavotto）。

---

## 23 阶段

| 阶段 | 内容 | 状态 |
| ---: | --- | --- |
| 01 | 全仓基线、产品合同、交接骨架 | ✅ 完成（本次） |
| 02 | 文档 schema、稳定 ID、迁移、原子写入 | ✅ 完成（本次，ADR 0023） |
| 03 | 保存状态机、autosave、恢复、历史 | ✅ 完成（本次，ADR 0024） |
| 04 | 后端统一 refresh | ✅ 完成（本次，ADR 0025） |
| 05 | 项目 watcher、批次合并、SSE | ✅ 完成（本次，ADR 0026） |
| 06 | 前端事件消费与派生元数据同步 | ✅ 完成（本次） |
| 07 | Readiness 后端事实模型 | ✅ 完成（本次） |
| 08 | Readiness 前端与常驻左栏 | ✅ 完成（本次） |
| 09 | 快速编辑 / 画布双工作流、原图输出合同 | ✅ 完成（本次，ADR 0028） |
| 10 | Style / Spec 分层 | ✅ 完成（本次，ADR 0029） |
| 11 | 统一检查引擎与问题面板 | ✅ 完成（本次，ADR 0030） |
| 12 | 导出管线与精简导出 UI | ✅ 完成（本次，ADR 0031） |
| 13 | 统一属性系统、文字控件、标注字体 | ✅ 完成（本次，ADR 0032） |
| 14 | 科学文本 / Unicode / 字体回退 | ✅ 完成（本次，ADR 0033） |
| 15 | 图例绑定与控件 | ⬜ |
| 16 | 刻度线直接操作 | ⬜ |
| 17 | 多选浮动栏 | ⬜ |
| 18 | QuickEdit 右键动作 | ⬜ |
| 19 | 设置 / Agent / 包管理 | ⬜ |
| 20 | 离线教程资源后端 | ⬜ |
| 21 | onboarding UI 与提示 | ⬜ |
| 22 | Codex/AI、i18n、遥测、文档整合 | ⬜ |
| 23 | 全量 QA 与发布门禁 | ⬜ |

## 六个 Gate

| Gate | 覆盖 | 状态 |
| --- | --- | --- |
| 1 数据安全 | 01–03 | ✅（三个阶段全部完成；遗留项见下方风险表） |
| 2 项目实时状态 | 04–08 | ✅ 04（后端刷新）+ 05（watcher）+ 06（前端消费闭环）+ 07（就绪度事实模型）+ 08（就绪度界面与常驻左栏）全部完成 |
| 3 核心工作流与输出 | 09–12 | ✅ 09（双工作流）+ 10（Style/Spec 分层）+ 11（统一检查与问题定位）+ 12（统一导出管线与精简导出面板）全部完成 |
| 4 编辑一致性 | 13–18 | ⬜ |
| 5 产品外壳 | 19–22 | ⬜ |
| 6 发布 | 23 | ⬜ |

---

## 风险登记（Session 01 审计，**已实测**的标 ✔）

严重度用本仓库 `docs/1.0-release-readiness.md` 的分级口径。

| ID | 风险 | 证据 | 严重度 | 归属 |
| --- | --- | --- | --- | --- |
| R-01 ✔ | ✅ **已修（02）** **用户的「另存为」不是原子写**：`POST /api/layouts/<name>` 直接 `write_text` 覆盖既有文件，中途失败留下截断文件且旧内容已没了 | `app.py:4192`（实测源码） | P1 | 02 |
| R-02 ✔ | ✅ **已修（02）** **非有限数被原样写进磁盘**：`json.dumps` 默认允许 NaN/Infinity，写出的 `{"w": NaN}` 不是合法 JSON，浏览器 `JSON.parse` 解不动 → 该文档在前端表现为"读不出来"，静默退回本机副本 | 实测：PUT `/api/autosave/d1` 带 NaN → 200，磁盘上就是 `NaN`；`json.loads(..., parse_constant=raise)` 报错 | P1 | 02 |
| R-03 | ✅ **已修（03）** **版本检查点没有画布身份**：检查点存的是**激活画布**（`useVersionCheckpoints` 传 `state.doc`），却按 `documentId`（项目）归档；在画布 B 上产生的检查点，在画布 A 上恢复会把 B 的内容与名字盖到 A 上 | `hooks/useVersionCheckpoints.ts:29`、`VersionDialog.tsx:319` | P1 | 03 |
| R-04 ✔ | ✅ **已修（02）** **`_styles` 被列成一份用户文档**：`GET /api/layouts` 对数据目录 `glob("*.json")`，而样式预设就存在 `LAYOUT_DIR/_styles.json` | 实测：存一个样式后 `/api/layouts` 返回 `{"layouts": ["_styles"]}` | P2 | 02 |
| R-05 | 🟡 **部分（02 + 04）** `app.py` 四处（02）与 `discover.write_config`（04，注册表落盘）已并入 `engine/atomicio`；`engine/` 里另外五处（config / runspec / runtimeasset / locate / session_client / nativehandoff）未动——它们写的不是文档，各有各的生命周期，合并要逐个看过。**原子写实现散落 9 处以上**，无一做 fsync、无一在失败时清理 tmp、无一返回结构化错误 | `app.py:329/4269/4331/4463`、`engine/config.py:182`、`runspec.py:411`、`runtimeasset.py:132`、`locate.py:279`、`session_client.py:71`、`nativehandoff.py:108` | P2 | 02 |
| R-06 | ✅ **已修（03）** **没有显式的保存状态机**：`saving` / `save_error` / `conflict` / `recovery_available` 都不是文档状态（错误只是一个 `window` 事件，刷新即丢） | `documentStore.ts` 无对应字段 | P1 | 03 |
| R-07 | **autosave 存在数据目录而非项目内**：`AUTOSAVE_DIR = LAYOUT_DIR/_autosave`，项目整个拷到另一台电脑不会带上未落名的工作副本 | `app.py:4206` | P2 | 03 |
| R-08 | ✅ **已修（03）** **没有外部修改冲突检测**：只有跨标签页的 `updatedAt` 乐观并发；用户在编辑器外改了 `tavottofile/*.json`，Tavotto 会静默覆盖 | `app.py:4226` 只比 `updatedAt` | P1 | 03 |
| R-09 | ✅ **已修（09）** **快速编辑不存在**：图内编辑必须先把面板放进画布，普通用户被迫理解画布 | 全仓无独立单图编辑入口 | P1（产品） | 09 |
| R-10 | ~~**导出偏好只在 localStorage**~~ **已处置（Session 12，重新定性）**：Prompt 12 §六明写「最近导出目录属于 UI preference，不进入项目 undo」——格式 / PPI / 报告开关同属这一档，**它们本来就该是本机偏好**（换机器丢掉一个 600 ppi 的选择不构成数据损失）。真正属于项目的那一项（用哪套规范）在 Session 10 就已经写进文档（`doc.profile` 带快照）。**这一行的原始定性是错的**，不是没做 | `lib/exportDefaults.ts` + ADR 0029 的 `doc.profile` | P2 | ✅ 12（重新定性） |
| R-11 | ~~**最小字号有两个数**~~ **已处置（Session 10，T-48）**：三个数（8.5 严格 / 8.0 绝对 / 8.5 图例）收敛成一个 8 pt；8 pt 那条边的语义未动 | `profiles/publication.json` | P2 | ✅ 10 |
| R-12 | ✅ **已修（11）** **问题项没有画布维度**：`PreflightIssue` 有 `objectIds`/`gids`，无 `canvasId`，多画布项目里无法跨画布定位。`ValidationIssue.objectRef` 带 `documentId` / `canvasId`，定位会切画布 | `lib/preflight.ts` | P2 | ✅ 11 |
| R-13 | ✅ **已修（05）** **没有 watcher 事件批次合并**：项目 watcher（`engine/project_watch.py`）把一批连续写入合并成**一次**刷新；`registry.changed`/`assets.changed` 仍只由统一刷新发，watcher 自己只发 `panel.file_changed` | 原证据 `pool.py:2003`（已删） | P2 | 05 |
| R-14 | **教程 / onboarding 完全不存在** | 全仓搜 `tutorial`/`onboarding` 零命中 | P2（产品） | 20/21 |
| R-15 | **a11y 门禁半盲**：axe 的 `incomplete` 不进 violations | 既有 issue #130 | P2 | 22 |
| R-16 | **E2E 只有 Windows 腿** | 既有 issue #30 | P2 | 23 |
| R-19 ✔ | ✅ **已兑现（2026-08-30）** **e2e 与 axe 两层在 05–09 里从没真跑过**。真跑起来之后共 8 条红：#207 三条（两条**夹具跟不上契约**、一条**自算对比度尺子的假红**）、#208 五条（三条是「轨道按钮是开关不是打开」、一条跨标签页共享工作区模式、一条只有**合并态**才红）。全部已修，本机全量 111 passed。**本行原来的理由被证伪**：E2E 在本机跑得起来——`TAVOTTO_PYTHON` 指主仓库 venv + `PYTHONPATH=<worktree>/src` + 先跑一次 `scripts/build_frontend.py`，单条 2.6 秒、全量 ~10 分钟。剩余的「有模态/无模态下自算对比度结论不一致」记 issue #210 | 见下方「E2E 本机跑法」 | P2（门禁未执行） | ✅ 09/10 |
| R-17 | 前端主 chunk 1.57 MB（gzip 487 kB），构建有大小告警 | `pnpm build` 输出 | P3 | 23 |
| R-18 ✔ | **N-1 升级验收里两个检查是空的**：① 它 PUT 给 `/api/autosave/` 的是 `{"doc":…, "updatedAt":…}`，没有 `schema`，后端从**一开始**就 400，异常被 `except` 吞成 `autosave_saved=False`，于是"自动保存读得回来"这条检查**从来没跑过**；② `"老布局可列出"` 对 `layouts`（一个字符串列表）做 `x.get("name")`，必然 `AttributeError` 被同一个 `except` 接住记成 False | `scripts/ci/upgrade_acceptance.py:344,353,455` | P1（门禁空转） | 23 |

**本轨道之外**（记录但不处理，README「明确不包含」）：PyMuPDF 替换、
Tavotto run 兼容层、matplotlib 捕获范围、CLA/法务。

### Session 02 之后（改动后实跑）

| 命令 | 结果 |
| --- | --- |
| `PYTHONPATH=<wt>/src <repo>/.venv/bin/python -m pytest` | ✅ exit 0 —— **3041** passed / 34 skipped / 0 failed（比基线 +18 = 新增的 `tests/test_document_persistence.py`） |
| `ruff check .` | ✅ exit 0 |
| `ruff format --check .` | ✅ exit 0 |
| `git diff --check` | ✅ 无空白问题 |

前端未改动，沿用基线结果（`pnpm test` / `build` / `i18n:check` 三条 exit 0）。

### Session 03 之后（改动后实跑）

| 命令 | 结果 |
| --- | --- |
| `PYTHONPATH=<wt>/src <repo>/.venv/bin/python -m pytest` | ✅ exit 0 —— **3053** passed / 34 skipped / 2 deselected（比 02 +12） |
| `cd web && pnpm test` | ✅ exit 0 —— 118 files / **1370** tests passed（比基线 +32） |
| `cd web && pnpm build` | ✅ exit 0 |
| `cd web && pnpm i18n:check` | ✅ exit 0（zh-CN 2560 / en-US 2645） |
| `cd web && pnpm lint` | ✅ 无新增告警（只有既有的 fast-refresh 提示） |
| `ruff check . && ruff format --check .` | ✅ exit 0 |
| 变异反证 24 条 | ✅ 全部 KILLED（记录见 `TEST_MATRIX.md`） |

> 改了 `web/src` 就要重建 `codex-plugin/mcp/widget/canvas.html`
> （`python scripts/build_mcp_widget.py`），否则 `test_mcp_server.py` 与
> `test_windows_regressions.py` 两条会红，而红的原因与改动本身无关。

### Session 04 之后（改动后实跑）

| 命令 | 结果 |
| --- | --- |
| `PYTHONPATH=<wt>/src <repo>/.venv/bin/python -m pytest` | ✅ exit 0 —— **3093** passed / 34 skipped / 2 deselected（比 03 +40 = 新增的 `tests/test_project_refresh.py` 38 条 + `test_error_codes.py` 2 条），9 分 15 秒 |
| `cd web && pnpm test` | ✅ exit 0 —— 118 files / 1370 tests passed（前端只改了类型，用例数不变） |
| `cd web && pnpm build` | ✅ exit 0 |
| `cd web && pnpm i18n:check` | ✅ exit 0（新 code 的双语文案 + 重新生成 `resources.d.ts`） |
| `cd web && pnpm lint` | ✅ 无新增告警（只有既有的 fast-refresh 提示） |
| `ruff check . && ruff format --check .` | ✅ exit 0（273 files） |
| `git diff --check` | ✅ 无空白问题 |
| `python scripts/build_mcp_widget.py` | ✅ 已重建（改了 `web/src/lib/api.ts`） |
| 变异反证 29 条 | ✅ 全部 KILLED（记录见 `TEST_MATRIX.md`；**其中 3 条第一轮活了下来**，判据已加固） |

### 评审回合 1 之后（PR #201，改动后实跑）

| 命令 | 结果 |
| --- | --- |
| `PYTHONPATH=<wt>/src <repo>/.venv/bin/python -m pytest` | ✅ exit 0 —— **3102** passed / 34 skipped / 2 deselected（比 04 首轮 +9），9 分 18 秒 |
| `cd web && pnpm test` | ✅ exit 0 —— 118 files / **1371** tests passed（+1） |
| `cd web && pnpm build` / `i18n:check` / `lint` | ✅ exit 0（`resources.d.ts` 已重新生成） |
| `ruff check . && ruff format --check .` / `git diff --check` | ✅ exit 0 |
| `python scripts/build_mcp_widget.py` | ✅ 已重建（指纹 `0433b760e29720c5`） |
| 变异反证 6 条 | ✅ 全部 KILLED（**1 条第一轮活了下来**：修订号挪到锁外读） |

> **canvas.html 要在所有 `web/src` 改动之后再重建。** 本轮先重建、后跑
> `i18next-cli types`（它写 `web/src/i18n/resources.d.ts`），于是同步判据在
> 全量跑到 94% 时红了——产物是对的，只是比源码早了三分钟。

> 跑全量时**别再多加一个 `-q`**：`pytest.ini` 的 `addopts` 里已经有一个，
> 叠成 `-qq` 会把最后那行统计**整个吞掉**——于是"跑过了"只剩一个退出码，
> 数字无从核对。

---

### Session 05 之后（改动后实跑）

| 命令 | 结果 |
| --- | --- |
| `PYTHONPATH=<wt>/src <repo>/.venv/bin/python -m pytest` | ✅ exit 0 —— **3146** passed / 34 skipped / 2 deselected（比评审回合 1 的 3102 整好 +44 = 新增的 `tests/test_project_watch.py`），10 分 24 秒 |
| `cd web && pnpm test` | ✅ exit 0 —— 118 files / 1371 tests passed（本轮只动了类型，用例数不变） |
| `cd web && pnpm build` | ✅ exit 0 |
| `cd web && pnpm i18n:check` | ✅ exit 0（没有新增 key，`resources.d.ts` 未变） |
| `cd web && pnpm lint` | ✅ exit 0（18 条既有的 fast-refresh 提示，无新增） |
| `ruff check . && ruff format --check .` | ✅ exit 0（275 files） |
| `git diff --check` | ✅ 无空白问题 |
| `python scripts/build_mcp_widget.py` | ✅ 已重建（指纹 `cfba8a5c965ad282`，改了 `web/src/lib/api.ts`） |
| 变异反证 31 条 | ✅ 全部 KILLED（记录见 `TEST_MATRIX.md`；**其中 1 条第一轮"活了下来"，但那是变异自己写错了——语义 no-op**） |

> **中途红过两条**（`test_mcp_server.py` + `test_windows_regressions.py` 的画布
> 同步判据）：`web/src/lib/api.ts` 改了而 `canvas.html` 没重建。重建之后两条
> 都绿，上表是重建之后**重跑一遍完整套件**的结果，不是拼起来的。

### Session 06 之后（改动后实跑）

| 命令 | 结果 |
| --- | --- |
| `PYTHONPATH=<wt>/src <repo>/.venv/bin/python -m pytest` | ⚠️ exit 1 —— **3145 passed / 1 failed** / 34 skipped / 2 deselected，10 分 58 秒。唯一那条红的是 `tests/native/test_run_cli_integration.py::test_ctrl_c_reaches_the_script_and_leaves_no_orphan`，**与本阶段无关**（本轮 Python 一行没改）——详见下面的「全量套件里的那条红」 |
| `cd web && pnpm test` | ✅ exit 0 —— **124** files / **1456** tests passed（比 05 的 1371 +85 = 六个新用例文件） |
| `cd web && pnpm build` | ✅ exit 0（`tsc -b && vite build`，2777 modules） |
| `cd web && pnpm i18n:check` | ✅ exit 0（zh-CN 2570 / en-US 2657；`resources.d.ts` 已重新生成） |
| `cd web && pnpm lint` | ✅ exit 0（18 条既有 fast-refresh 提示，无新增） |
| `ruff check . && ruff format --check .` | ✅ exit 0（275 files；本轮没动 Python） |
| `git diff --check` | ✅ 无空白问题 |
| `python scripts/build_mcp_widget.py` | ✅ 已重建（指纹 `6c61fe2315e158ba`；排在所有前端改动**与** `i18next-cli types` **之后**） |
| 变异反证 55 条 | ✅ 全部被打红（清单见 `TEST_MATRIX.md`；**其中 5 条第一轮活了下来**：两条是变异自己没问对问题、两条是判据缺一维、一条查出来是**多余的守卫**（已删）——处置都记在 TEST_MATRIX） |

> `npx vitest` 直接跑会漏掉 `NODE_OPTIONS=--no-experimental-webstorage`（在
> `package.json` 的 `test` 脚本里）：没有它 Node 自带的 `localStorage` 全局会
> 盖住 jsdom 那份并且不可用，任何碰 localStorage 的用例都报
> `Cannot read properties of undefined`。单跑某个文件时要自己补上这个环境变量。

#### 全量套件里的那条红（`test_ctrl_c_reaches_the_script_and_leaves_no_orphan`）

**现象**：`os.killpg(..., SIGINT)` 发出去之后，`proc.communicate(timeout=90)` 超时
——`tavotto run` 的 CLI 在 90 秒内没有退出。stdout 里有 `READY`（用户脚本已经
跑到 `time.sleep(120)`，信号窗口是对的），stderr 停在「Waiting for Tavotto
desktop…」。

**范围（四组实测，本机）**：

| 跑法 | 结果 |
| --- | --- |
| 全量 `pytest` | ❌ 2 次 2 红（本阶段跑的两次干净全量） |
| `pytest tests/native/test_run_cli_integration.py` ×5 | ✅ 5 次全绿 |
| `pytest tests/golden tests/native`（全量里排在它前面的全部） | ✅ 264 passed |
| `pytest -k <这条>`（**收集整个套件**，只跑它一条） | ✅ 1 passed |

也就是说：不是收集期的副作用（第四组排除），也不是排在它前面那些用例留下的
状态（第三组排除），窄范围里一次都复现不出来。

**性质不定，范围定了**：这是 PR #189（`tavotto run` 控制面，ADR 0021）带进来
的用例，属于 `tavotto run` 那条线，与本阶段（纯前端）**没有任何代码路径相交**
——本轮 `src/tavotto/**` 一个字节没改，而这条用例走的是 Python CLI，不加载
`web/src/**` 的任何东西。Session 05 今天早些时候的全量是 3146 passed exit 0
（总数一致：3145 + 1 = 3146），所以它在同一台机器上**今天早些时候还是绿的**。

**没有处置成绿**：不 skip、不 xfail、不删。它需要 `tavotto run` 那条线的人
按「全量里红、窄范围绿」这个形状去查（多半在控制通道的关闭/唤醒那一族——
参见 compat-bridge 轨道踩过的「`close()` 不唤醒阻塞的 `recv`」）。

### Session 07 之后（改动后实跑）

| 命令 | 结果 |
| --- | --- |
| `PYTHONPATH=<wt>/src <repo>/.venv/bin/python -m pytest` | ✅ exit 0 —— **3199** passed / 34 skipped / 2 deselected，9 分 57 秒 |
| `cd web && pnpm test` | ✅ exit 0 —— 124 files / 1456 tests passed（本轮前端只加类型，用例数不变） |
| `cd web && pnpm build` | ✅ exit 0（`tsc -b && vite build`，2777 modules） |
| `cd web && pnpm i18n:check` | ✅ exit 0（没有新增 key——就绪度的文案归 08） |
| `cd web && pnpm lint` | ✅ exit 0（18 条既有的 fast-refresh 提示，无新增） |
| `ruff check . && ruff format --check .` | ✅ exit 0（277 files） |
| `git diff --check` | ✅ 无空白问题 |
| `python scripts/build_mcp_widget.py` | ✅ 已重建（指纹 `47aee0ca4eee6e47`，改了 `web/src/lib/api.ts`） |
| 变异反证 35 条 | ✅ 全部被打红（**第一轮有 7 条活了下来**，两种成因与处置见 `TEST_MATRIX.md`） |

**数字对得上**：Session 06 那一次全量是 3145 passed + 1 failed；本轮
3145 + 53（新增的 `tests/test_project_readiness.py`）+ 1 = **3199**。

**Session 06 那条红本轮两次全量都绿**
（`tests/native/test_run_cli_integration.py::test_ctrl_c_reaches_the_script_and_leaves_no_orphan`）。
**这不构成"它被修好了"**：本轮 `tavotto run` 那条线一个字节没改，两次绿只
说明它是偶发的——而偶发红先当缺陷查，不当背景噪音。它仍留在遗留表里。

### Session 08 之后（改动后实跑）

| 命令 | 结果 |
| --- | --- |
| `PYTHONPATH=<wt>/src <repo>/.venv/bin/python -m pytest` | ✅ exit 0 —— **3200** passed / 34 skipped / 2 deselected，10 分 27 秒（Session 07 的 3199 + 本轮新增的 1 条后端用例 = 3200，数字对得上） |
| `cd web && pnpm test` | ✅ exit 0 —— **131** files / **1557** tests passed（比 07 的 124/1456 +7 文件 / +101 条） |
| `cd web && pnpm build` | ✅ exit 0（`tsc -b && vite build`） |
| `cd web && pnpm i18n:check` | ✅ exit 0（zh-CN 2612 / en-US 2697；新增 `readiness.*`，删掉 33 个死掉的 `registry.*`） |
| `cd web && pnpm lint` | ✅ exit 0（18 条既有 fast-refresh 提示，无新增） |
| `ruff check . && ruff format --check .` | ✅ exit 0（277 files） |
| `git diff --check` | ✅ 无空白问题 |
| `python scripts/build_mcp_widget.py` | ✅ 已重建（指纹 `ebea0b57749239f2`）+ `--check` 通过 |
| `python scripts/build_browser_playground.py` | ✅ 已重建（指纹 `4dd2877615f06445`）+ `--check` 通过；不进 git，网站仓库另行 sync |
| 变异反证 33 条 | ✅ 全部被打红（**第一轮有 5 条活了下来**，四种成因与处置见 `TEST_MATRIX.md`；其中一条查出来是**杀不死的冗余**，已删） |

> **又踩了一次「产物比源码早」**：第一遍全量里
> `test_widget_artifact_is_in_sync_with_the_frontend` 与
> `test_maintenance_scripts_report_under_cp1252_stdout` 两条红——`canvas.html`
> 重建之后我又改了 `web/src`。**重建必须排在所有 `web/src` 改动之后**，
> `i18next-cli types` 写的 `resources.d.ts` 也算一次改动。
> 上表是**把所有前端改动做完、两个产物都重建并 `--check` 通过之后**重跑一遍
> 完整套件的结果，不是拼起来的。

### Session 09 之后（改动后实跑，**冻结前端之后**跑的一遍）

| 命令 | 结果 |
| --- | --- |
| `PYTHONPATH=<wt>/src <repo>/.venv/bin/python -m pytest` | ✅ exit 0 —— **3217** passed / 34 skipped / 0 failed（Session 08 的 3200 + 本轮新增的 17 条 = 3217，数字对得上） |
| `cd web && pnpm test` | ✅ exit 0 —— **134** files / **1618** tests passed（比 08 的 131/1557 +3 文件 / +61 条：workspace 19 + originalSpec 25 + fastEditStage 8 + overflow 预算 9） |
| `cd web && pnpm build` | ✅ exit 0（`tsc -b && vite build`） |
| `cd web && pnpm i18n:check` | ✅ exit 0（zh-CN 2632 / en-US 2717；新增 `fastEdit.*` 与 `assets.open*`，删掉死掉的 `assets.addAria`） |
| `cd web && pnpm lint` | ✅ exit 0（只有既有的 fast-refresh 提示，无新增） |
| `ruff check . && ruff format --check .` | ✅ exit 0（279 files） |
| `git diff --check` | ✅ 无空白问题 |
| `python scripts/build_mcp_widget.py` | ✅ 已重建（指纹 `dc25a773a91b099b`）+ `--check` 通过 |
| `python scripts/build_browser_playground.py` | ✅ 已重建（指纹 `2541bd56c77053d9`）+ 不进 git，网站仓库另行 sync |
| 变异反证 26 条 | ✅ 全部被打红（**第一轮有 2 条活了下来**，两条都是「判据没被执行到它该看的那个点上」，成因与处置见 `TEST_MATRIX.md`） |
| `cd web && pnpm e2e` | ⚠️ **本轮（Session 09 当时）没跑**，只确认 `playwright test --list` 收得到全部 110 条 —— **收得到 ≠ 跑得过**。**2026-08-30 补跑并修完**：5 条红，见 R-19 与下方「E2E 本机跑法」 |

> **「产物比源码早」这一轮踩了三次。** 每次都是同一个形状：重建 `canvas.html`
> 之后又改了 `web/src`（哪怕只是一行注释——指纹算的是内容，不是语义），
> 于是 `test_widget_artifact_is_in_sync_with_the_frontend` 与
> `test_maintenance_scripts_report_under_cp1252_stdout` 两条一起红，而红的原因
> 与它们看护的事毫无关系。
> **上表是把前端整个冻结、两个产物重建并 `--check` 通过之后重跑的一遍完整套件**，
> 不是拼起来的。下一轮的纪律：**改完所有 `web/src` 再重建，重建之后一个字都不动**
> ——`i18next-cli types` 写的 `resources.d.ts` 也算一次改动。

### Session 10 之后（改动后实跑，**冻结前端 + 重建两个产物之后**跑的一遍）

| 命令 | 结果 |
| --- | --- |
| `PYTHONPATH=<wt>/src <repo>/.venv/bin/python -m pytest` | ✅ exit 0 —— **3271** passed / 34 skipped / 0 failed（收集 3305 条 = 09 的 3251 + 54，逐项对得上：`test_profile_store.py` 37 + `test_preflight.py` 新增 1 + `test_error_codes.py` 那条按 code 参数化的用例随 16 个新 code 一起 +16） |
| `cd web && pnpm test` | ✅ exit 0 —— **138** files / **1659** tests passed（比 09 的 134/1618 +4 文件 / +41 条：specBinding 18 + profileStore 6 + styleAndSpec 6 + profilesSettings 11 = 41；ExportDialog / profile 里那几条只改判据，不增条数） |
| `cd web && pnpm build` | ✅ exit 0（`tsc -b && vite build`） |
| `cd web && pnpm i18n:check` | ✅ exit 0（zh-CN 2713 / en-US 2798；新增 `profiles.*` / `export.profile*` / 16 条 backend 错误码，删掉死掉的 `export.profileStamp`） |
| `cd web && pnpm lint` | ✅ exit 0（只有既有的 fast-refresh 提示，无新增） |
| `ruff check . && ruff format --check .` | ✅ exit 0（281 files） |
| `python scripts/build_mcp_widget.py` | ✅ 已重建（指纹 `2e72e0094357a576`） |
| `python scripts/build_browser_playground.py` | ✅ 已重建（指纹 `22b775a453e77970`）+ 不进 git，网站仓库另行 sync |
| 变异反证 36 条 | ✅ 全部被打红，**第一轮 0 条存活**——但有**两条判据在反证之前就先改掉了**，因为它们恒等成立（内置样式派生、错误文案按语言渲染），成因与处置见 `TEST_MATRIX.md` |
| `cd web && pnpm e2e` | ⛔ **没跑**（同 08/09 的限制：Playwright 要真实后端与浏览器）。**本轮没有改任何 e2e spec** |

> **「跑全量的时候别动树」这一轮踩了两次。** 变异反证会写文件再改回来，而
> 全量套件正在读同一批文件——两次的表现都是"跑到一半开始红，红的原因与被测
> 的事毫无关系"。纪律：**反证与全量串行，全量开始之后一个字都不改**
> （文档除外，套件不读 `docs/`）。

### Session 11 之后（改动后实跑，**冻结前端 + 重建两个产物之后**跑的一遍）

| 命令 | 结果 |
| --- | --- |
| `PYTHONPATH=<wt>/src <repo>/.venv/bin/python -m pytest` | ✅ exit 0 —— **3370** passed / 34 skipped / 2 deselected / 0 failed，9 分 49 秒 |
| `cd web && pnpm test` | ✅ exit 0 —— **147** files / **1805** tests passed（比 10 的 138/1659 +9 文件 / +146 条） |
| `cd web && pnpm build` | ✅ exit 0（`tsc -b && vite build`） |
| `cd web && pnpm i18n:check` | ✅ exit 0（zh-CN 2808 / en-US 2898；新增 `errors:problems.*`（含 32 条规则短标题）、`workspace:rail.problems` / `history.fixIssue*`、`dialogs:export.openProblems` / `preflightFailed*`） |
| `cd web && pnpm lint` | ✅ 只有既有的 fast-refresh 提示，**无新增** |
| `ruff check . && ruff format --check .` | ✅ exit 0（288 files） |
| `git diff --check` | ✅ 无空白问题 |
| `python scripts/build_mcp_widget.py` | ✅ 已重建（指纹 `2b28899feb865bf1`）+ `--check` 通过 |
| `python scripts/build_browser_playground.py` | ✅ 已重建（指纹 `71b114bf1a448afc`）+ 不进 git，网站仓库另行 sync |
| 变异反证 44 条 | ✅ 全部被打红（第一轮 38/44，六条存活的成因与处置见 `TEST_MATRIX.md`） |
| `npx playwright test e2e/a11y.spec.ts --project=chromium` | ✅ **8 passed**（新增「问题面板」一条，见下） |
| `npx playwright test e2e/asset-library e2e/keyboard-golden-path --project=chromium` | ✅ **7 passed**（这三条 spec 断言导出对话框里的预检块，本轮重写过它） |

> **后端 3370 与 Session 10 的 3271 之间的差额不是本轮的。** 本轮只加了 1 条
> 后端用例（导出上下文跨语言同源）；其余来自 Session 10 之后合进 `main` 的
> PR（基线是 `main@dd7c5b5`，不是 Session 10 收工那一刻）。

> **a11y 那条真跑起来当场红了一次，而且红得对**：问题面板里「技术详情」的
> `<summary>` 用了 `text-ink-faint`（2.54:1，axe serious）。`ink-faint` 按 UI
> 纪律只给装饰与禁用态，而 summary 是个真控件、上面是要读的字。改成 `ink-3`
> 之后 8/8 绿。**这正是 08/09 两轮只做到 `--list` 时漏掉的那一类**。

---

---

> 单跑某个前端用例文件时**必须自己带上**
> `NODE_OPTIONS=--no-experimental-webstorage`（它在 `package.json` 的 `test`
> 脚本里）：没有它 Node 自带的 `localStorage` 会盖住 jsdom 那份且不可用，
> 报错看起来像被测代码坏了。本轮又踩了一次。

### Session 12 之后（改动后实跑，**冻结前端 + 重建两个产物之后**跑的一遍）

| 命令 | 结果 |
| --- | --- |
| `PYTHONPATH=<wt>/src <repo>/.venv/bin/python -m pytest` | ✅ exit 0 —— **3467** passed / 34 skipped / 2 deselected / 0 failed，约 11 分（比 11 的 3370 +97：70 条导出用例 + 27 条参数化的错误码） |
| `cd web && pnpm test` | ✅ exit 0 —— **151** files / **1919** tests passed（比 11 的 147/1805 +4 文件 / +114 条） |
| `cd web && pnpm build` | ✅ exit 0（`tsc -b && vite build`） |
| `cd web && pnpm i18n:check` | ✅ exit 0（zh-CN 2841 / en-US 2931；`dialogs:export.*` 删 21 组 / 加 27 组，`errors:backend.*` +27 条，`workspace:topbar.exportPackage` + `status.packaged*`） |
| `cd web && pnpm lint` | ✅ 只有既有的 fast-refresh 提示，**无新增** |
| `ruff check . && ruff format --check .` | ✅ exit 0（293 files） |
| `git diff --check` | ✅ 无空白问题 |
| `python scripts/gen_filename_vectors.py` | ✅ 向量与实现一致（无参 = 校对模式） |
| `python scripts/build_mcp_widget.py` | ✅ 已重建（指纹 `f22a72331cc5617d`，第六轮评审后）+ `--check` 通过 |
| `python scripts/build_browser_playground.py` | ✅ 已重建（指纹 `e539f57cec7a516e`，第四轮评审后）+ 不进 git，网站仓库另行 sync |
| 变异反证 23 条 | ✅ 全部被打红（第一轮 20/23，三条存活的成因与处置见 `TEST_MATRIX.md`） |
| **评审回合 3（PR #214，`0031649`）** | ✅ 3 P1 + 3 P2 **全部成立、全部改**。处置见 `TEST_MATRIX.md`「评审回合 3」 |
| **复审回合（PR #214，`0c92c5a`）** | ✅ 1 P1 + 5 P2 **全部成立、全部改**。处置见 `TEST_MATRIX.md`「复审回合」 |
| **第三轮评审（PR #214，`8c1f7d4`）** | ✅ 1 P1 + 3 P2 **全部成立、全部改**，并修掉变异脚本自己把「锚点找不到」与「存活」混报的空转。处置见 `TEST_MATRIX.md`「第三轮评审」 |
| **第四轮评审（PR #214，`07fc7c2`）** | ✅ 2 P1 + 2 P2 **全部成立、全部改**（其中一条 P1 是第三轮的修复制造出来的）。处置见 `TEST_MATRIX.md`「第四轮评审」 |
| **第五轮评审（PR #214，`c8479335`）** | ✅ 1 P1 + 2 P2 **全部成立、全部改**（两条 P2 都在十行以内，比开 Issue 便宜）。处置见 `TEST_MATRIX.md`「第五轮评审」 |
| **第六轮评审（PR #214，`13ec3b69`）** | ✅ **0 P1 + 4 P2**，全部成立、全部改（都在 20 行以内、都在本 PR 已动过的文件里，其中一条是本 PR 自己声明的不变式被违反）；变异反证扩到**后端 28 / 前端 27，全红**。处置见 `TEST_MATRIX.md`「第六轮评审」 |
| **合并 main（`#215`）** | ✅ 唯一冲突是 `codex-plugin/mcp/widget/canvas.html`——**生成物，在合并态重建**（挑一边留下都会得到一份与源码不对应的产物）。#215 同时动了 `app.py` / `pymupdf_backend.py` / `overrides.py`，与本 PR 同模块不同区域，**文本干净不等于语义干净**，所以全量套件在合并态重跑了一遍 |
| **合并队列的 Windows 腿（PR #214）** | ✅ 被踢出来两次，两次都是**真缺陷**：① `atomicio.publish_file()` 对只读 fd 调 `os.fsync()`，Windows 的 `_commit()` 只接受可写句柄 → EBADF → **每一次导出都失败**（70 条用例连带红，打包版 `POST /api/export` 直接 500）；② `ElementInspector` 的 pair/rect 控件不给数字框任何可访问名（axe `label`，**critical**，main 上原有）。都已修 + 回归用例 + 变异反证。处置见 `TEST_MATRIX.md`「合并队列的 Windows 腿」 |
| **`full-ci` 标签** | ✅ `backend-platforms` / `windows-exe-smoke` / `package` 在 PR 上默认 skipping，只在 `merge_group` 跑。给 PR 打 `full-ci` 让它们**直接在 PR 上跑**，别拿合并队列当探测器。注意：打标签会当场触发新 run 并取消旧的，被取消的 run 留下的是**假红**（先看 `conclusion` 是不是 `cancelled`） |
| **第七轮评审（PR #214，`4f5f1855`）** | ✅ **0 P1 + 3 P2**，全部成立、全部改。第一条虽挂 P2，后果是「新导出永远停在进行中」——**按后果处置不按标签处置**。变异反证 5 条：第一轮 4 红 1 存活，存活的那条是「判据只钉了一条边」（补了 `.then` 没补 `.catch`），补齐后 5/5 全红。处置见 `TEST_MATRIX.md`「第七轮评审」 |
| `npx playwright test e2e/a11y e2e/asset-library e2e/keyboard-golden-path e2e/i18n --project=chromium` | ✅ **27 passed**（2.4 分钟）——含「导出对话框：axe 干净 + 焦点 trap」、中英文各一遍导出对话框、纯键盘走完导出闭环 |

> **导出的 golden 基线在动手之前就取了**（`/api/export` 三个用例的 PDF 页面
> 尺寸 / 可提取文字 / 图片数 / 绘制数 / xobject 数 / 渲染像素 sha1 + PNG 尺寸
> 与 sha1）。整条管线重写之后**逐字节相同**——旧契约那条路一个像素没变。

---

### Session 13 之后（改动后实跑，**冻结前端 + 重建两个产物之后**跑的一遍）

| 命令 | 结果 |
| --- | --- |
| `PYTHONPATH=<wt>/src <repo>/.venv/bin/python -m pytest` | ✅ exit 0 —— **3498** passed / 34 skipped / 0 failed（比 12 的 3467 +31：新增 `test_typography_families.py` 15 条 + 预检向量 2 条 + 参数化） |
| `cd web && pnpm test` | ✅ exit 0 —— **155** files / **1986** tests passed（比 12 的 151/1919 +4 文件 / +67 条） |
| `cd web && pnpm build` | ✅ exit 0（`tsc -b && vite build`） |
| `cd web && pnpm i18n:check` | ✅ exit 0（zh-CN 2865 / en-US 2955；`errors:preflight.textFontFamilySubstituted*` +2、`inspector:history.{setFontFamily,resetTextProp}` +2、`inspector:textControls.font{MissingTag,MissingHint}` +2） |
| `cd web && pnpm lint` | ✅ 19 条既有 fast-refresh 提示，**无新增**（三态开关的两个 helper 搬进 `lib/typography.ts` 之后控件文件只导出组件） |
| `ruff check . && ruff format --check .` | ✅ exit 0（294 files） |
| `git diff --check` | ✅ 无空白问题 |
| `python scripts/gen_preflight_vectors.py --write` | ✅ 21 → 23 条；**既有 21 条一条没变**（新增的两条是画布文字的字体族） |
| `python scripts/build_mcp_widget.py` | ✅ 已重建（指纹 `42c3a0cc7b8e1c26`）+ `--check` 通过 |
| `python scripts/build_browser_playground.py` | ✅ 已重建（指纹 `e0a4ff5da0ef92df`）+ 不进 git，网站仓库另行 sync |
| 变异反证 17 条 | ✅ 全部被打红（第一轮 15/17，两条存活的成因与处置见 `TEST_MATRIX.md`） |
| `npx playwright test e2e/a11y e2e/i18n e2e/keyboard-golden-path e2e/asset-library --project=chromium` | ⚠️ **21 passed / 6 failed**——**六条在 `origin/main`（`c12c229c`）上一模一样地红**，见下 |

> **e2e 的六条红做过 A/B，不是「猜它不是我的」。** 同一台机器、同一份 fixture、
> 同一条命令，把 `web/src` 与 `src/tavotto` 整个换成 `c12c229c`（当前
> `origin/main`）、重跑 `scripts/build_frontend.py` 之后，`a11y.spec.ts:291`
> 以**逐字相同**的方式失败（等 `getByRole('navigation').getByRole('button',
> { name: /项目接入状态/ })` 超时 180 s）。两侧的 `error-context.md` 里那段
> 无障碍快照也逐字相同：左侧轨道上只有 `画布 / 素材 / 结构 / 图内元素 / 设置`
> 五个按钮，**「问题」与「项目接入状态」两个按钮不在 DOM 里**。六条红全是
> 这两个入口的下游（另外三条等的是 `[data-element-svg] svg`）。
>
> **这与 Session 12 的记录冲突**：那一轮同样四个 spec 是 27 passed。所以在
> `#214 → #219` 之间的 main 上、或者本机环境里，有一件事变了而没人发现——
> `LeftRail` 里那两个按钮都是**无条件渲染**的（`ITEMS` 五项 + 轨道底部的
> 接入状态入口），源码上找不到能让它们消失的判据。**没有查到根因就不许写成
> 「已知问题」**，所以它以一条开着的遗留留给下一个 Session，附本轮的复现命令。

---

### Session 14 之后（改动后实跑，**冻结前端 + 重建两个产物之后**跑的一遍）

| 命令 | 结果 |
| --- | --- |
| `PYTHONPATH=<wt>/src <repo>/.venv/bin/python -m pytest` | ⟪PYTEST⟫ |
| `cd web && pnpm test` | ✅ exit 0 —— **157** files / **2082** tests passed（比 13 的 155/1986 +2 文件 / +96 条） |
| `cd web && pnpm build` | ✅ exit 0（`tsc -b && vite build`） |
| `cd web && pnpm i18n:check` | ✅ exit 0（zh-CN 2876 / en-US 2966；`errors:preflight.{glyphMissing,glyphSubstituted,textGlyphMissing,textGlyphSubstituted}` +4、`inspector:text.{interpretation*,glyphMissing,glyphFallback}` +7、`inspector:history.setInterpretation` +1） |
| `cd web && pnpm lint` | ✅ **19 条既有 fast-refresh 提示，无新增**（与 13 逐条相同） |
| `ruff check . && ruff format --check .` | ✅ exit 0（300 files） |
| `python scripts/gen_canvas_coverage.py` | ✅ 覆盖表与当前后端一致（pymupdf 1.28.2，1114 个区间） |
| `python scripts/gen_glyph_plan_vectors.py` | ✅ 60 条与 Python 侧一致 |
| `python scripts/gen_preflight_vectors.py` | ✅ 23 → 27 条；**既有 23 条一条没变** |
| `python scripts/build_mcp_widget.py --check` | ✅ 已重建 + 一致（指纹 `90c7441a4f95b406`） |
| `python scripts/build_browser_playground.py --check` | ✅ 已重建 + 一致（指纹 `256bd5821164afb3`） |
| 变异反证 15 条 | ✅ 全部被打红（第一轮 14/15，存活的那条与处置见 `TEST_MATRIX.md`） |
| Playwright e2e | ⚠️ **没跑**。改动没碰黄金路径的键位与文案，但这是「没跑」，不是「跑过没问题」；13 记的那六条红仍然开着 |

> **图内文字那条路的判据与渲染器对拍过 9/9。** 「这套字体画不画得出这些字」
> 用的是字体文件的 cmap，而 matplotlib 在渲染时会自己 warn 缺哪个码位——
> 两把尺子互相独立（一把读文件，一把看渲染器实际画的时候说了什么）。九组
> （默认族 / Times New Roman / 回退链 / 中文 / mathtext / 纯 ASCII…）逐组一致。

---

## 遗留（Session 14 之后仍开着的）

| ID | 事项 | 归属 |
| --- | --- | --- |
| — | **不渲染的面板会成批报「无法核验」**：渲染只对激活画布上「编辑中 / 有 override / 脚本领先磁盘」的面板发起（`renderTargets`），所以多画布项目里 `panel-text-not-verifiable` 数量可观。它是 `not_verifiable` 这一档、有自己的分组，**而且是真话**——但数量上是噪音。改法要么按需渲染、要么把这一档折叠成每画布一条 | 未定 |
| — | **批量修复不跨画布**（撤销栈按画布换入换出，跨画布的"一个批事务"在这套模型里不存在）。界面只在当前画布上给「全部修复」，别的画布上的一条一条修 | 已处置（说明写在按钮文案里） |
| — | **问题面板没有虚拟滚动**：有多少条渲染多少行。本轮用例里最多几十条，真实上限没量过（与接入中心同一条遗留） | 待量 |
| — | **`user_choice` 目前只有页宽一条规则**。这一档不是为它而设——它是 `applyIssueFix(id, choice?)` 这个签名成立的前提 | 已处置 |
| — | **MCP 内嵌画布保留自己的等级图标表**：它消费的是 MCP 聚合载荷、且是另一个尺寸敏感的 bundle。图标一致的看护覆盖应用内两处 | 已处置 |
| R-05 | `engine/` 里另外五处手写原子写未并入 `atomicio`（config / runspec / runtimeasset / locate / session_client / nativehandoff） | 择机 |
| R-07 | autosave 仍在数据目录（`LAYOUT_DIR/_autosave`）而非项目内 | 未定 |
| — | **`test_ctrl_c_reaches_the_script_and_leaves_no_orphan` 偶发红**（Session 06 的全量里红一次；07/08 三次全量都绿。属 `tavotto run` 线，与本轨道无代码路径相交）。**Session 12 新证据：它对机器负载敏感。** 本轨道第 12 次全量里红了一次——而那一遍我把 pytest 全量与 Playwright e2e **同时**开着；单跑 0.7–2.9 秒，同一棵树串行全量 8 次全绿。它等的是「SIGINT 之后 `tavotto run` 在 90 秒内退出」，重载下这个预算不够。**但这解释不了 Session 06 那次**（那一遍是串行的），所以问题没关：判据把"进程反应有多快"当成了产品性质，而它其实是机器性质 | 待查（多一条证据） |
| — | 项目打开仍走自己的静态草稿逻辑，没并进统一服务（为了不扫两遍） | 择机 |
| — | 「编辑历史」仍在文档菜单里，不是左上区域的独立入口（Prompt 03 §六）。**08 没做**：本阶段的左栏改造只到「常驻外壳 + 项目状态入口」，历史入口的位置牵涉顶栏与文档菜单的分工 | 未定 |
| — | `/api/layouts/<name>` 的载荷仍不做 schema 校验（ADR 0023 §5a） | 23 前 |
| — | 没有 index.json（`/api/layouts` 靠 glob 现算） | 未定 |
| — | **接入中心没有虚拟滚动**：报告里有多少张图就渲染多少行。**本轮没有实测过大项目**（用例里最多 6 行），真实上限不知道 | 待量 |
| — | **「重新扫描」只有项目级一个入口**（对话框顶部）。Prompt 08 的原文也把它列进 `editable` 行内动作；18 行里每行挂一个项目级动作是噪音，故未做 | 已决定不做 |
| — | ~~就绪度前端的 axe 覆盖靠 e2e，本轮没跑过 Playwright~~ **已跑**：第七轮合并 main 之后在本机跑了**完整** 115 条（114 passed / 1 skipped）。「本机沙箱里起不来」是错的判断（见 R-19）；worktree 里要带 `TAVOTTO_PYTHON=<主工作区>/.venv/bin/python`，且**必须先 `python scripts/build_frontend.py`**——包内 `src/tavotto/web/` 优先于 `web/dist`，不重建的话测的是上一次的界面，而且一路绿 | ✅ 已闭合 |
| — | **就绪度只覆盖磁盘素材**（`/api/panels` 的 id 空间）。runtime figure 素材（ADR 0013，`runtime:` 前缀）不在报告里。**08 的处置：界面对它们一个字不说**——runtime 卡片有自己那套角标（`panelBadge.runtime*`），接入状态的四个出口都只在拿得到 `capability` 时才出现 | 已处置 |
| — | **`codex-plugin` 那条导出入口没并进统一管线**（`bridge.py` 自己的 `_write_proof` 仍写 `_proof.json`）。另一个进程、另一份载荷、另一条分发路径，并进来要连 widget 一起改 | 未定（12 刻意没动） |
| — | **「按另一个像素网格导出位图」这个能力不存在**：评审回合 3 把那条没有调用点的 `native_grid=False` 分支删了（它用的密度常量还是错的）。要加这个能力时，密度得从 `engine/originalspec` 来，像素网格由调用方算好传进来 | 未定 |
| — | **源文件不在素材清单里时不能按原图导出**，哪怕它有脚本能重新画：`_resolve_panel_source()` 的 `safe_resolve()` 排在查注册表之前。界面已经如实说出来（不给必然失败的按钮），但**能力本身是缺的**。改它要动画布导出共用的那条路 | 未定（评审回合 3 记录） |
| — | **`/api/package` 仍是同步的**，没有进作业模型（它不出图，没有部分失败） | 择机 |
| — | **导出进度只有阶段与步数，没有百分比**：合成那一步占大头而它不可分 | 已处置（界面说的是阶段，不假装有百分比） |
| — | **透明背景对 PDF 是「不画白底」**，不是 PDF 的透明组；位图源装进 PDF 时 `vector: false`，界面没有单独说这一句 | 未定 |
| — | **README 里两张预检截图是旧规范拍的**（alt 文本如实描述图里的「低于 8.5 pt」）。改 alt 会让它不再描述那张图；重拍要跑真实应用 | 23 前 |
| — | **左侧轨道上「问题」与「项目接入状态」两个按钮不在 DOM 里**（本机 chromium e2e，六条红的共同上游）。**在 `origin/main`（`c12c229c`）上一模一样地红**，两侧的无障碍快照逐字相同——不是本轮引入的。但 Session 12 的记录是同样四个 spec **27 passed**，而 `LeftRail` 里这两个入口都是无条件渲染的，源码上找不到能让它们消失的判据。复现：`python scripts/build_frontend.py` 之后 `cd web && TAVOTTO_PYTHON=<repo>/.venv/bin/python npx playwright test e2e/a11y.spec.ts --project=chromium -g "项目接入状态"` | **未查明，优先** |
| — | **「新建标注时套用当前 Style」没有做**：本仓库里 Style 是一次性应用、不是文档上的绑定（ADR 0029 绑的是 Spec），「当前 Style」这个概念不存在。做成本机 UI 偏好会让同一个动作在两台机器上建出不同的对象，比现状更坏。13 只把新建默认值收敛成 `canvasTextDefaults()` 一处 | 待用户拍板 |
| — | **画布文字的字体族只有三个通用族**：具体字体名要么内嵌用户磁盘上的字体（另一件事、另一份许可证讨论），要么就是静默替换。图内文字那侧才有「装不上的具体字体」这一档（`options_unavailable`） | 已处置（闭集是能力承诺） |
| — | **中日韩字形不跟着族走**：实测 PyMuPDF 1.28.2 的四个 `china-*` 别名回同一张 `Droid Sans Fallback Regular`。**14 的处置：不改这条能力，改成说得出**——画布文字里的中日韩字符现在会在 `glyph-substituted` 里被报成「不是用所选字体画的」 | 已处置 |
| — | **`text_weight_policy` 里的 `annotation` 一档仍然没有执行者**：规范声明「标注一律常规字重」，而 `addSubLabels()` 造出来的 (a)(b)(c) 按惯例加粗。现在执行会让每一份既有文档立刻多出一批警告——**这是规范范围的问题**，不是属性层的问题 | 待用户拍板 |
| — | **`valign` / `lineHeight` / `rotationDeg` 在能力表里，但控件只出前六条**：行距与旋转仍在各自的「更多」里用原来的控件（数据已经经过能力层，控件还没并进 `TypographyControls`） | 择机 |
| — | **Session 13 没跑 e2e**：改动没碰黄金路径的键位与文案，但这是**没跑**，不是「跑过没问题」 | 23 前 |
| — | **图内中日韩没有自动回退**：回退尾巴只有 matplotlib 自带的 DejaVu Sans（在每个平台上都在，回退结果确定）。往里放一个平台相关的中文字体会让同一份文档在两台机器上画出不同的字。用户在字体下拉里选（候选取自出版规范的 `cjk_fallback.accepted`，按运行时探测过滤） | 已处置（明示取舍） |
| — | **`scientific` 档的代价只写在 tooltip 里**：合成之后 PDF 文本层里 `×10⁵` 抽回来是 `×105`。没有做「导出前再确认一次」——那会给一个每次导出都要点掉的对话框 | 未定 |
| — | **PDF 字体子集嵌入没有判据**：由 PyMuPDF 自己管，本轮没碰也没量过子集完整性。`preferred_formats` 那条规范没有新增看护 | 未定 |
| — | **覆盖表是在 macOS + pymupdf 1.28.2 上生成的**。它随 PyMuPDF 的 wheel 走，理论上跨平台一致，但**没有在 Linux / Windows 上实测过**；`gen_canvas_coverage.py --check` 会在 CI 上第一次回答这个问题 | 待 CI 回答 |
| — | **`interpretation` 只有画布文字有**：图内文字的上下标是 matplotlib 的 `$…$`（另一条管线）。能力表里它是 `figureText` 不支持的一条 | 已处置 |
| — | **`needs_probe` 的候选是项目级的**（`details.candidate_scope: "project"`）。**08 的处置：措辞如实**——「项目里有会画图的脚本，但要运行一次才知道它生成的是哪个文件」，动作叫「试运行并连接」而不是「连接到某某」；`candidate_scope` 进技术详情 | 已处置 |

---

## 下一阶段

**Prompt 15（图例文本与线条测量）**，入口见
`SESSION_HANDOFF.md` 的「下一阶段入口」。

14 留给 15 的可复用入口：

* `glyphplan.py` ↔ `glyphPlan.ts` —— **「这个字由哪张脸画出来」的唯一判据**
  （四层，顺序不可交换）。图例文本的测量要用**最终 render plan**，别再按
  `ord` 切一遍；
* `pdfbackend.text_width(s, size_pt, bold, italic, family)` —— 与落笔读同一份
  计划。**族必须传对**：等宽族比衬线族宽得多；
* `pdfbackend.missing_glyphs()`（问真字体，导出侧）与
  `glyphplan.text_diagnostics()`（读表，预检两侧）；
* manifest 的 `glyphs_missing` / `glyphs_fallback` —— 图内文字缺什么字，
  产生者只有 `manifest._glyph_scan()` 一处；
* `overrides.FONT_FALLBACK_TAIL` / `_family_chain()` —— 加一条尾巴前先回答
  「它在每个平台上都在吗」。

13 留给 14 的六个入口（14 已消费，对 15 原样有效）：`lib/typography.ts`
（现在有十条属性）、`typographyAdapter.TypographyAdapter`、`mathTextModeOf`、
`pdfbackend.CANVAS_TEXT_FAMILIES`、manifest 的 `options_unavailable`、
`propertyPathOf(kind, prop)`。

12 留给 13 的四个入口（原样有效）：`lib/exportRequest.buildExportRequest()`、
`lib/exportPayload.toExportObjects()`（**新属性加在这一处**）、
`lib/exportName.checkFilename()`、`store/exportStore.ts`。

以及 09–11 留下的四个（原样有效）：`lib/originalSpec.getOriginalOutputSpec()`、
`lib/specBinding.resolveDocumentSpec()`、
`store/validationStore.getValidationSummary()`、`lib/issueFocus.focusIssue()`。

---

## E2E 本机跑法（2026-08-30 实测，推翻 R-19 原来的理由）

R-19 原文写着「本机沙箱起不来真实后端 + 浏览器」，所以 Session 08 / 09 两轮都
只做到 `--list`。**实测不成立**：

```bash
cd <worktree>
PYTHONPATH=$PWD/src <主仓库>/.venv/bin/python scripts/build_frontend.py   # 包内前端就位
cd web
TAVOTTO_PYTHON=<主仓库>/.venv/bin/python PYTHONPATH=<worktree>/src \
  npx playwright test e2e/xxx.spec.ts --project=chromium -g "用例名"
```

* 单条约 **2.6 秒**，全量（三个浏览器 project、112 条）约 **10 分钟**；
* **`PYTHONPATH` 那一项别漏**：`.venv` 是对**主工作区**的 editable 安装，不带
  的话跑的是另一棵树上的代码（实测撞过：菜单项还显示着上个版本的文案，白查
  十分钟才发现测错了树）；
* `build_frontend.py` 的产物 `src/tavotto/web/` **本来就不进版本库**，所以
  「先构建再跑」在 E2E 这条路上早就是既定事实。

**代价是真金白银的**：#207 那三条红本来 3 分钟就能在本机看见，实际是等了一轮
45 分钟的合并组才亮出来，而且当时 main 上还排着别的 PR。#208 改成先在本机跑，
5 条红全部在推之前查清。

### 顺带确立的两条纪律

1. **验合并态的树，不只是分支产物。** #208 有一条红只在 `main + #208` 上出现
   （#205 刚落地的 `large-figure.spec.ts` 无条件点「编辑图内元素」，而 Prompt 09
   之后那颗按钮不存在）。两边**各自单跑都是绿的**。推之前跑一遍合并态全量。
2. **先跑「什么都不做」的对照组。** 查 #208 那三条时我先看到「点一下树就没了」，
   就去翻 `ElementTree` 找破坏性写法——是对照组把我拉回来的：完全不点，树同样
   500ms 后消失（关掉的是抽屉不是树）。差一点去修一个不存在的缺陷。

