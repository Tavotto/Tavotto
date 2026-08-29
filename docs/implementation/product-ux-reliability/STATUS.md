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
| 08 | Readiness 前端与常驻左栏 | ⬜ |
| 09 | 快速编辑 / 画布双工作流、原图输出合同 | ⬜ |
| 10 | Style / Spec 分层 | ⬜ |
| 11 | 统一检查引擎与问题面板 | ⬜ |
| 12 | 导出管线与精简导出 UI | ⬜ |
| 13 | 统一属性系统、文字控件、标注字体 | ⬜ |
| 14 | 科学文本 / Unicode / 字体回退 | ⬜ |
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
| 2 项目实时状态 | 04–08 | 🟡 04（后端刷新）+ 05（watcher）+ 06（前端消费闭环）+ 07（就绪度后端事实模型）完成，08 未开始 |
| 3 核心工作流与输出 | 09–12 | ⬜ |
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
| R-09 | **快速编辑不存在**：图内编辑必须先把面板放进画布，普通用户被迫理解画布 | 全仓无独立单图编辑入口 | P1（产品） | 09 |
| R-10 | **导出偏好只在 localStorage**：换机器 / 清缓存即丢，也不随项目走 | `lib/exportDefaults.ts` | P2 | 12 |
| R-11 | **最小字号有两个数**：`absolute_min_font_size_pt: 8.0` 与 `legend_policy.min_font_size_pt: 8.5` | `profiles/publication.json:43,65` | P2 | 10 |
| R-12 | **问题项没有画布维度**：`PreflightIssue` 有 `objectIds`/`gids`，无 `canvasId`，多画布项目里无法跨画布定位 | `lib/preflight.ts` | P2 | 11 |
| R-13 | ✅ **已修（05）** **没有 watcher 事件批次合并**：项目 watcher（`engine/project_watch.py`）把一批连续写入合并成**一次**刷新；`registry.changed`/`assets.changed` 仍只由统一刷新发，watcher 自己只发 `panel.file_changed` | 原证据 `pool.py:2003`（已删） | P2 | 05 |
| R-14 | **教程 / onboarding 完全不存在** | 全仓搜 `tutorial`/`onboarding` 零命中 | P2（产品） | 20/21 |
| R-15 | **a11y 门禁半盲**：axe 的 `incomplete` 不进 violations | 既有 issue #130 | P2 | 22 |
| R-16 | **E2E 只有 Windows 腿** | 既有 issue #30 | P2 | 23 |
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

---

## 遗留（Session 07 之后仍开着的）

| ID | 事项 | 归属 |
| --- | --- | --- |
| R-05 | `engine/` 里另外五处手写原子写未并入 `atomicio`（config / runspec / runtimeasset / locate / session_client / nativehandoff） | 择机 |
| R-07 | autosave 仍在数据目录（`LAYOUT_DIR/_autosave`）而非项目内 | 未定 |
| — | **`test_ctrl_c_reaches_the_script_and_leaves_no_orphan` 偶发红**（Session 06 的全量里红一次，Session 07 的两次全量都绿；属 `tavotto run` 线，与本轨道无代码路径相交） | 待查 |
| — | 项目打开仍走自己的静态草稿逻辑，没并进统一服务（为了不扫两遍） | 择机 |
| — | 「编辑历史」仍在文档菜单里，不是左上区域的独立入口（Prompt 03 §六） | 08（左栏改造） |
| — | `/api/layouts/<name>` 的载荷仍不做 schema 校验（ADR 0023 §5a） | 23 前 |
| — | 没有 index.json（`/api/layouts` 靠 glob 现算） | 未定 |

| — | **就绪度只覆盖磁盘素材**（`/api/panels` 的 id 空间）。runtime figure 素材（ADR 0013，`runtime:` 前缀）不在报告里——它们按定义就有脚本，且 id 空间不同，混进来会破坏「id 与 `PanelInfo.id` 逐字相同」 | 08 决定要不要在 UI 上补一句 |
| — | **`needs_probe` 的候选是项目级的**（`details.candidate_scope: "project"`）：静态解不出这些脚本的产物，所以说不出"这张图来自其中哪一个"。项目里有一个动态脚本，所有没有专属候选的图都会变成 `needs_probe` | 08 的措辞要照顾到这一点 |

---

## 下一阶段

**Prompt 08（Readiness 前端与常驻左栏）**，入口见 `SESSION_HANDOFF.md` 的
「下一阶段入口」。事实模型到 07 为止已经完整——**08 不得在前端重新猜状态**：
每张图的能力事实只有 `capability.status` / `reason_code` 一个出处（`/api/panels`
每项都带，或整份 `GET /api/project/readiness`），前端**不许**再按 `script`
有没有值自己判一遍。reason code 是闭集，08 负责给它们配中英文文案。
