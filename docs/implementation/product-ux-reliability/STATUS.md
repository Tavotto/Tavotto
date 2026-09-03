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
| 15 | 图例绑定与控件 | ✅ 完成（本次，ADR 0034） |
| 16 | 刻度线直接操作 | ✅ 完成（本次，ADR 0035） |
| 17 | 多选浮动栏 | ✅ 完成（本次，ADR 0036） |
| 18 | QuickEdit 右键动作 | ✅ 完成（本次，ADR 0037） |
| 19 | 设置 / Agent / 包管理 | ✅ 完成（本次，ADR 0038） |
| 20 | 离线教程资源后端 | ✅ 完成（本次，ADR 0039） |
| 21 | onboarding UI 与提示 | ✅ 完成（本次，ADR 0040） |
| 22 | Codex/AI、i18n、遥测、文档整合 | ✅ 完成（本次，ADR 0041） |
| 23 | 全量 QA 与发布门禁 | 🟡 **BLOCKED — 不建议发布**（本次；本分支 P0 = 0、P1 = 0，阻断在 main 上另外三条轨道的连红与桌面产物未验证，见「发布结论」） |

## 六个 Gate

| Gate | 覆盖 | 状态 |
| --- | --- | --- |
| 1 数据安全 | 01–03 | ✅（三个阶段全部完成；遗留项见下方风险表） |
| 2 项目实时状态 | 04–08 | ✅ 04（后端刷新）+ 05（watcher）+ 06（前端消费闭环）+ 07（就绪度事实模型）+ 08（就绪度界面与常驻左栏）全部完成 |
| 3 核心工作流与输出 | 09–12 | ✅ 09（双工作流）+ 10（Style/Spec 分层）+ 11（统一检查与问题定位）+ 12（统一导出管线与精简导出面板）全部完成 |
| 4 编辑一致性 | 13–18 | ✅ 13（属性能力层）+ 14（科学文本 / 字体回退）+ 15（图例绑定与控件）+ 16（刻度直接操作）+ 17（多选浮动栏）+ 18（右键菜单）全部完成 |
| 5 产品外壳 | 19–22 | ✅ 19（设置外壳 / Agent 精简 / 包管理 / 诊断拆页）+ 20（离线教程资源与 Tutorial API）+ 21（交互式 onboarding 与一次性提示）+ 22（Codex / AI 显式刷新、遥测整合、入口与文档）全部完成 |
| 6 发布 | 23 | 🟡 本树全量真跑过、打包 wheel 验过、场景 A–N 有映射；**桌面产物只有 CI 证据且本分支的桌面改动尚未在 CI 执行过**，main 上 Lab / Nightly / Metrics 连红 |

---

## 风险登记（Session 01 审计；**Session 23 之后整段重写，逐格复核到代码**）

严重度用本仓库 `docs/1.0-release-readiness.md` 的分级口径。

**状态是一个闭集**，五个值互不合并：`✅ 已修` / `✅ 已处置（重新定性）` /
`🟡 部分（剩余有 issue）` / `❌ 未修（有 issue）` / `❓ 未验证`。
**「不知道」是独立一档**——指不出兑现它的那行代码就写 `❓ 未验证`，不许滑进相邻的
`✅`。「证据」一栏里每一项都必须是能点开的东西：commit、`文件:行`、或 issue 号。

| ID | 风险 | 状态 | 兑现证据（commit / `文件:行` / issue） | 严重度 |
| --- | --- | --- | --- | --- |
| R-01 | **「另存为」不是原子写**：`POST /api/layouts/<name>` 直接 `write_text` 覆盖既有文件，中途失败留下截断文件且旧内容已没了 | ✅ 已修（02） | `app.py:5154` 走 `engine_atomicio.write_json`；六步写入序列见 `engine/atomicio.py:1-30`；失败经 `app.py:682` 的 `AtomicWriteError` errorhandler 出结构化错误 | P1 |
| R-02 | **非有限数被原样写进磁盘**：`json.dumps` 默认允许 NaN/Infinity，写出的 `{"w": NaN}` 不是合法 JSON，前端表现为「读不出来」并静默退回本机副本 | ✅ 已修（02） | 判据放在序列化边界：`engine/atomicio.py:118` `dumps_json`（`allow_nan=False` → `AtomicWriteError`）；这条例外记在 `engine/documents.py:83` | P1 |
| R-03 | **版本检查点没有画布身份**：检查点存的是**激活画布**，却按 `documentId`（项目）归档；在画布 B 上产生的检查点，在画布 A 上恢复会把 B 的内容与名字盖到 A 上 | ✅ 已修（03） | `web/src/hooks/useVersionCheckpoints.ts:25,32-35` 现在把 `canvasId: activeCanvasId` 一并存进版本条目 | P1 |
| R-04 | **`_styles` 被列成一份用户文档**：`GET /api/layouts` 对数据目录 `glob("*.json")`，而样式预设就存在 `LAYOUT_DIR/_styles.json` | ✅ 已修（02） | `app.py:5114-5121` 显式剔掉 Tavotto 自己的文件；旧位置的 `_styles.json` 另有一次性迁移进 store（`app.py:5547`、`engine_profilestore.migrate_legacy_styles`） | P2 |
| R-05 | **原子写实现散落**：`app.py` 四处（02）与 `discover.write_config`（04）已并入 `engine/atomicio`；`engine/` 里另外**六处**仍是各写各的手写 `tmp + os.replace`——无 fsync、失败不清 tmp、无结构化错误 | 🟡 部分（02 + 04），剩余有 issue | 已并：`app.py:344/5154/5338/5412`、`discover.py:961`。未并六处：`engine/config.py:181`、`runspec.py:421`、`runtimeasset.py:135`、`locate.py:295`、`session_client.py:61`、`nativehandoff.py:103` → **#241**。（原文「散落 9 处以上」把已并的 `app.py` 那批也算在内） | P2 |
| R-06 | **没有显式的保存状态机**：`saving` / `save_error` / `conflict` / `recovery_available` 都不是文档状态（错误只是一个 `window` 事件，刷新即丢） | ✅ 已修（03） | `web/src/store/documentStore.ts:838` `SaveState` 六态闭集 + `:840` 卡住原因；恢复副本是**单独一根轴**（`:856`），没有塞进同一个枚举；状态图在 `:826`；用例 `web/src/store/saveStateMachine.test.ts` | P1 |
| R-07 | **autosave 存在数据目录而非项目内**：`AUTOSAVE_DIR = LAYOUT_DIR/_autosave`，项目整个拷到另一台电脑不会带上未落名的工作副本 | ❌ 未修（有 issue） | `app.py:124` `LAYOUT_DIR = DATA_ROOT/"layouts"` → `app.py:5161` `AUTOSAVE_DIR` → `app.py:5168`；`engine/documents.py:38`。同一结构后果见下方场景 D 的备注 → **#243**。（原记的 `app.py:4206` 行号已过时） | P2 |
| R-08 | **没有外部修改冲突检测**：只有跨标签页的 `updatedAt` 乐观并发；用户在编辑器外改了 `tavottofile/*.json`，Tavotto 会静默覆盖 | ✅ 已修（03） | `app.py:5318-5327` 用 `engine_atomicio.content_revision` 比基线，不一致回 409 `external_change`；判据与写入在**同一把锁**里（`app.py:5316`）；修订号经 `X-Tavotto-Revision` 出网（`app.py:5214`） | P1 |
| R-09 | **快速编辑不存在**：图内编辑必须先把面板放进画布，普通用户被迫理解画布 | ✅ 已修（09，ADR 0028） | `web/src/canvas/QuickEdit.tsx`；原图输出合同 `engine/exportreq.py:37,242`（`scope=original` 没有 x/y/w/h，图幅由图自己定） | P1（产品） |
| R-10 | ~~**导出偏好只在 localStorage**~~ **这一行的原始定性是错的**：格式 / PPI / 报告开关本来就该是本机偏好，换机器丢掉一个 600 ppi 的选择不构成数据损失 | ✅ 已处置（12，重新定性） | 本机偏好：`web/src/lib/exportDefaults.ts`；真正属于项目的那一项（用哪套规范）Session 10 起写进文档 `doc.profile` 带快照（ADR 0029） | P2 |
| R-11 | ~~**最小字号有两个数**~~：三个数（8.5 严格 / 8.0 绝对 / 8.5 图例）收敛成一个 8 pt | ✅ 已处置（10，T-48） | `src/tavotto/profiles/publication.json:42-43`（`min_effective` = `absolute_min` = 8.0）；删掉 8.5 pt 那条的理由写在同文件 `:10`。**README 的两张预检截图还停在旧的两条下限** → #247 | P2 |
| R-12 | **问题项没有画布维度**：`PreflightIssue` 有 `objectIds`/`gids`，无 `canvasId`，多画布项目里无法跨画布定位 | ✅ 已修（11，ADR 0030） | `ValidationIssue.objectRef` 带 `canvasId`，定位会先切画布再落到对象：`web/src/lib/issueFocus.ts:93-100` | P2 |
| R-13 | **没有 watcher 事件批次合并**：一批连续写入会发出一串刷新 | ✅ 已修（05，ADR 0026） | `engine/project_watch.py:207` `diff_snapshots` + `Delta.absorb`（`:186`）+ `max_batch`（`:255`）把一批连续写入合成**一次**刷新；原证据里的 `pool.py:2003` 那份实现已删 | P2 |
| R-14 | **教程 / onboarding 完全不存在**（登记时全仓搜 `tutorial`/`onboarding` 零命中） | ✅ 已修（20/21，ADR 0039/0040） | 后端 `src/tavotto/engine/tutorial.py`（离线教程资源 + Tutorial API）；前端 `web/src/lib/onboarding/{flow,hints,position}.ts`；e2e `web/e2e/tutorial.spec.ts` 四条。下方场景 M 的覆盖就是这一条 | P2（产品） |
| R-15 | **a11y 门禁半盲**：axe 的 `incomplete` 不进 violations，遮挡层下的对比度从没被测过 | ✅ 已修（22） | issue **#130 已 CLOSED**；`web/e2e/a11y.spec.ts` 每条用例都把 `incomplete` 交代清楚、**不按规则 id 放行**（`:84`、`:125`、`:132`），对比度与标题层级各自补了一份自算判据。剩余「有模态 / 无模态下自算对比度结论不一致」是 **#210** | P2 |
| R-16 | **E2E 只有 Windows 腿** | ❌ 未修（原引用记错对象） | 事实属实：Playwright 黄金路径只挂在 windows 腿上（`.github/workflows/ci.yml:833`，失败产物名 `windows-smoke-logs`）。**原文引的 #30 是 i18n 议题，不是这件事**；桌面真机黄金路径那条是 **#31**，腿拓扑本身另有轨道在处理 | P2 |
| R-19 | **e2e 与 axe 两层在 05–09 里从没真跑过**（本行原来的理由「本机沙箱起不来真实后端 + 浏览器」已被实测证伪） | ✅ 已兑现（09/10，2026-08-30） | 真跑起来共 8 条红：#207 三条、#208 五条，全部已修，本机全量 111 passed；跑法见下方「E2E 本机跑法」。剩余一条记 **#210** | P2（门禁未执行） |
| R-17 | 前端主 chunk 从登记时的 1.57 MB（gzip 487 kB）涨到 **1.85 MB**（gzip 574 kB），`pnpm build` 长期带大小告警 | ❌ 未修（有 issue） | 数字见下方 Session 23 终审结果表与 `TEST_MATRIX.md` 的 Session 23 `pnpm build` 行；`web/vite.config.ts` 里没有 `manualChunks`、也没调 `chunkSizeWarningLimit` → **#246** | P3 |
| R-18 | **N-1 升级验收里两个检查是空的**：① PUT 给 `/api/autosave/` 的载荷没有顶层 `schema`，后端从一开始就 400，异常被 `except` 吞成 `autosave_saved=False`；② `"老布局可列出"` 对一个字符串列表做 `x.get("name")`，必然 `AttributeError` 被同一个 `except` 接住记成 False | ✅ 已修（23） | 载荷改成产品自己会写的那份文档（顶层带 `schema`）：`scripts/ci/upgrade_acceptance.py:387-394`；文档形状断言 `:329-341`；**写不成不再静静消失**，改判成失败的检查：`:345-356` `missing_state_checks` | P1（门禁空转） |

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
| `PYTHONPATH=<wt>/src <repo>/.venv/bin/python -m pytest` | ✅ exit 0 —— **3612** passed / 34 skipped / 0 failed（比 13 的 3498 +114：`test_glyph_plan.py` 103 条 + `test_glyph_coverage_figure.py` 6 条 + `test_font_provenance.py` 7 条 + 预检向量 4 条，减去参数化口径的变化） |
| `cd web && pnpm test` | ✅ exit 0 —— **157** files / **2094** tests passed（比 13 的 155/1986 +2 文件 / +108 条） |
| `cd web && pnpm build` | ✅ exit 0（`tsc -b && vite build`） |
| `cd web && pnpm i18n:check` | ✅ exit 0（zh-CN 2876 / en-US 2966；`errors:preflight.{glyphMissing,glyphSubstituted,textGlyphMissing,textGlyphSubstituted}` +4、`inspector:text.{interpretation*,glyphMissing,glyphFallback}` +7、`inspector:history.setInterpretation` +1） |
| `cd web && pnpm lint` | ✅ **19 条既有 fast-refresh 提示，无新增**（与 13 逐条相同） |
| `ruff check . && ruff format --check .` | ✅ exit 0（300 files） |
| `python scripts/gen_canvas_coverage.py` | ✅ 覆盖表与当前后端一致（pymupdf 1.28.2，1114 个区间） |
| `python scripts/gen_glyph_plan_vectors.py` | ✅ 69 条与 Python 侧一致（含 Prompt 清单里的 plain x10 / 已有 mathtext / 中英科学混排） |
| `python scripts/gen_preflight_vectors.py` | ✅ 23 → 27 条；**既有 23 条一条没变** |
| `python scripts/build_mcp_widget.py --check` | ✅ 已重建 + 一致（指纹 `1d0ca399a046dc8c`） |
| `python scripts/build_browser_playground.py --check` | ✅ 已重建 + 一致（指纹 `9a31ab339b26ef91`） |
| 变异反证 22 条 | ✅ 21 条被打红 + 1 条**故意的无害变异存活**（缓存上限改成 1：仍然正确，只是慢——它是这套反证的正向对照）。第一轮 14/15，存活的那条与处置见 `TEST_MATRIX.md` |
| Playwright e2e | ⚠️ **没跑**。改动没碰黄金路径的键位与文案，但这是「没跑」，不是「跑过没问题」；13 记的那六条红仍然开着 |

> **图内文字那条路的判据与渲染器对拍过 9/9。** 「这套字体画不画得出这些字」
> 用的是字体文件的 cmap，而 matplotlib 在渲染时会自己 warn 缺哪个码位——
> 两把尺子互相独立（一把读文件，一把看渲染器实际画的时候说了什么）。九组
> （默认族 / Times New Roman / 回退链 / 中文 / mathtext / 纯 ASCII…）逐组一致。

---

### Session 15 之后（改动后实跑，**冻结前端 + 重建两个产物之后**跑的一遍）

| 命令 | 结果 |
| --- | --- |
| `PYTHONPATH=<wt>/src <repo>/.venv/bin/python -m pytest` | ⚠️ **3642** passed / 34 skipped / **1 failed**（比 14 的 3612 +30：`test_legend_binding.py` 28 条 + `test_legend_model_pairs.py` 2 条 + 向量 1 条 − invariants 里删掉的那条豁免用例 + 改名 1 条）。红的那一条是 `test_ctrl_c_reaches_the_script_and_leaves_no_orphan`——遗留表里记着的「对机器负载敏感」那条：这一遍我把 vitest + `pnpm build` 与它**同时**开着（又一条证据），单跑 1.89 秒绿 |
| 第一遍全量（作废） | 17 条红全部是 `test_project_env.py` / `test_dependency_repair_e2e.py`：我把 `TAVOTTO_WORKER_PYTHON` 设在了整个 shell 里，它们测的正是「没有环境变量时该选哪个解释器」。**worker 解释器本机能自动发现，这个变量不需要设**；去掉后 40/40 绿。另两条真缺陷（`_all_legends` 走了 `fig.axes`、roundtrip 用例还按显示序断言）已修 |
| `cd web && pnpm test` | ✅ exit 0 —— **158** files / **2114** tests passed（比 14 的 157/2094 +1 文件 / +20 条） |
| `cd web && pnpm build` | ✅ exit 0（`tsc -b && vite build`） |
| `cd web && pnpm i18n:check` | ✅ exit 0（zh-CN 2913 / en-US 3004；`inspector:legend.*` +23 组、`inspector:prop.{handle_*,binding,handletextpad,columnspacing,frame_linewidth,frame_rounded}` +10、`inspector:enum.binding.*` +2、`inspector:group.legendEntry`、`workspace:history.{hideLegendEntry,legendFollowSource}`） |
| `cd web && pnpm lint` | ✅ **20 条既有 fast-refresh 提示，无新增** |
| `ruff check . && ruff format --check .` | ✅ exit 0 |
| `python scripts/gen_preflight_vectors.py` | ✅ 27 → 28 条；**既有 27 条一条没变** |
| `python scripts/build_mcp_widget.py --check` | ✅ 已重建 + 一致（指纹 `13af9ce29dc7172a`） |
| `python scripts/build_browser_playground.py --check` | ✅ 已重建 + 一致（指纹 `162ab50a1c10af91`） |
| 变异反证 17 条 | ✅ 16 红 + 1 结构性存活（M4：重建喂快照被同一轮 `sync_legends` 治回来，双保险不是判据缺口）。第一轮 14/17，两条用例形状的盲区已补（热会话两步的重放、折叠区里的重复要先展开），见 `TEST_MATRIX.md` |
| 真应用（worktree 起在 5099） | ✅ 图例卡 / 图例项页 / 恢复跟随 / 改曲线颜色图例跟着变，四步各截了图（scratchpad，不进仓库） |
| Playwright e2e | ⚠️ **没跑**。改动没碰黄金路径的键位；图例位置「自动」的文案 e2e 里没有引用（grep 过）。这是「没跑」，不是「跑过没问题」；13 记的那六条红仍然开着 |

---

### Session 16 之后（改动后实跑，**冻结前端 + 重建两个产物之后**跑的一遍）

| 命令 | 结果 |
| --- | --- |
| `PYTHONPATH=<wt>/src <repo>/.venv/bin/python -m pytest -x --deselect tests/test_codex_e2e.py` | ✅ exit 0 —— **3655** passed / 34 skipped / 0 failed（比 15 的 3642 +13：`test_tick_sides_geometry.py` 12 条 + 15 那条负载敏感的 `test_ctrl_c_…` 这一遍绿）。这一遍与 vitest 全量**串行**跑，没有同时开别的重活 |
| `cd web && pnpm test` | ✅ exit 0 —— **160** files / **2185** tests passed（比 15 的 158/2114 +2 文件 / +71 条：`tickSides.test.ts` 40 + `spineZones.test.tsx` 22 + 刻度卡 +9） |
| `cd web && pnpm build` | ✅ exit 0（`tsc -b && vite build`；第一版 `spineZones.test.tsx` 有两条 TS 报错，被 `build_frontend.py` 抓到——`pnpm build` 的 tsc 覆盖测试文件，vitest 不覆盖类型） |
| `cd web && pnpm i18n:check` | ✅ exit 0（zh-CN 2936 / en-US 3027；`inspector:tick.{side.*,sides,sidesAria,sideAria,minorLength,minorWidth,dir.hidden}` `inspector:control.zoneAria` `inspector:prop.{minor_length,minor_width}` `workspace:history.tickSide{On,Off,Hide}` `workspace:spineZone.*` 7 条） |
| `cd web && pnpm lint` | ✅ 20 条既有 fast-refresh 提示 + `TickAndSpineDiagram.tsx` 新增 1 条同类（导出 `TICK_SPINE_PROPS` 常量，与 LegendCard 同形），无 error |
| `ruff check . && ruff format --check .` | ✅ exit 0 |
| `python scripts/build_mcp_widget.py --check` | ✅ 已重建 + 一致（指纹 `98f076bdcc65eb78`） |
| `python scripts/build_browser_playground.py --check` | ✅ 已重建 + 一致（指纹 `ca979f12d73899d2`） |
| 变异反证 10 条 | ✅ 10/10 全红（见 `TEST_MATRIX.md`） |
| 真浏览器（Playwright chromium，临时 spec 不进仓库） | ✅ 打开 `Fig1_kinetics`（脚本朝内刻度）：从面板底沿往上扫，先出现「下边 · 朝外刻度 关着 · 点击显示」（外侧带），中间一段无带，再出现「下边 · 朝内刻度 开着 · 点击隐藏这一边的刻度线」（内侧带）；点内侧带 → 下边刻度线消失、刻度数字仍在、属性页方向档「隐藏」高亮、「显示边」两开关关、恢复芯片「下边刻度线 ×」出现。三张截图在 scratchpad。**顺带抓到一条**：状态文字往框外推会出面板的裁剪框（overflow hidden）整个被裁掉——改成往框里推 |
| Playwright e2e 全量 | ⚠️ **没跑**（只跑了上面那条临时 spec）。13 记的六条红仍开着 |

### Session 17 之后（改动后实跑，**冻结前端 + 重建两个产物之后**跑的一遍）

| 命令 | 结果 |
| --- | --- |
| `cd web && pnpm test` | ✅ exit 0 —— **165** files / **2265** tests passed（比 16 的 160/2185 +5 文件 / +80 条：`context-bar/position.test.ts` 13 + `multiSelectionBar.test.tsx` 42 + `primarySelection.test.tsx` 5 + `alignSelectedTo.test.ts` 17 + `arrangeStore.test.ts` 2；既有 `contextBar.test.tsx` 的「多选不出现」改成「换成多选栏」） |
| `cd web && pnpm build` | ✅ exit 0（`tsc -b && vite build`；第一版 `alignSelectedTo.test.ts` 有两条 TS 报错——同 16：tsc 覆盖测试文件、vitest 不查类型） |
| `cd web && pnpm i18n:check` | ✅ exit 0（新增 `workspace:contextBar.{multiAria,selectedCount,alignMenu,distributeMenu,sizeMenu,groupMenu,moreArrange,moreArrangeTip,primaryHint}`、`workspace:status.{alignLockedSkipped,alignAllLocked}`、`inspector:arrange.refLabel`；`resources.d.ts` 重新生成） |
| `cd web && pnpm lint` | ✅ 无 error；新文件 0 条提示（按钮表 / 文案助手 / 打开属性页各自单独成文件，避开 fast-refresh 提示） |
| `PYTHONPATH=<wt>/src <repo>/.venv/bin/python -m pytest -q tests/test_mcp_server.py tests/test_codex_plugin.py tests/test_i18n_dead_keys.py tests/test_pr_conflict_domains.py` | ✅ exit 0（177 条，含 12 skipped）。**Python 侧没有改动，全量 pytest 这一轮没跑**（16 那遍 3655/34/0 仍是最近一次全量） |
| `python scripts/build_mcp_widget.py --check` | ✅ 已重建 + 一致（指纹 `97162c7183a44a0f`） |
| `python scripts/build_browser_playground.py --check` | ✅ 已重建 + 一致（指纹 `ac90363fcc807f16`；`web/dist-playground/` 不进 git） |
| `git diff --check` | ✅ |
| 变异反证 14 条 | ✅ 14/14 全红（见 `TEST_MATRIX.md`） |
| 真浏览器（Playwright chromium，临时 spec 不进仓库） | ✅ 1400×900：放一张图 + 两段文字，⌘A → 浮动栏贴在联合框上方、右沿在右栏左沿之内（bar 415–1032 / aside 1040）；主选轮廓更粗、联合框在；点「左对齐」三个对象贴齐、栏重贴；参照切「画布」属性页当场同步、tooltip「水平居中（画布）」；拖动期间栏消失、松手回来。900×700：右侧覆盖式抽屉开着 → 让位，关掉 → 回来（完整档）。600×700：静态阈值放行（600 ≥ 600）但量出 617 > 584 → **压缩档**、右沿 592 ≤ 600；点「对齐 ▾」弹层里切「画布」+「右对齐」生效；拉回 1000 宽回到完整档。**抓到两条**：① `fixed` 盒子的 `width:auto` 被可用宽度压扁，量到的不是自然宽度（改 `w-max`）；② 弹层自动聚焦第一个分段项，它的 tooltip 盖住下一排按钮，点击落在气泡上什么都不发生（tooltip 连 Radix 外壳一起 `pointer-events:none`）。八张截图在 scratchpad |
| Playwright e2e 全量 | ⚠️ **没跑**（只跑了上面那条临时 spec）。13 记的六条红仍开着 |

---

### Session 18 之后（改动后实跑，**冻结前端 + 重建两个产物之后**跑的一遍）

| 命令 | 结果 |
| --- | --- |
| `cd web && pnpm test` | ✅ exit 0 —— **167** files / **2344** tests passed（比 17 的 165/2265 +2 文件 / +79 条：`canvas/objectContextMenu.test.tsx` 62 + `store/quickEditActions.test.ts` 18；既有用例一条没改） |
| `cd web && pnpm build` | ✅ exit 0（`tsc -b && vite build`） |
| `cd web && pnpm i18n:check` | ✅ exit 0（新增 `workspace:quickEdit.*` 19 组、`workspace:history.{lock,unlock,hide,show}Objects`、`workspace:status.{panelRebuilt,panelRerenderedNoRerun,rebuildFailed}`、`workspace:confirm.resetOverrides*`；`quickEdit.openInspector` 文案改为「打开全部属性」；`resources.d.ts` 重新生成。第一版把中文的 `_other` 写成了基键，门禁当场红） |
| `cd web && pnpm lint` | ✅ 无 error；新文件 0 条提示 |
| `ruff check . && ruff format --check .` | ✅ |
| `PYTHONPATH=<wt>/src <repo>/.venv/bin/python -m pytest -q tests/test_engine_invalidate.py tests/test_i18n_dead_keys.py tests/test_engine_variants.py tests/test_error_codes.py` | ✅ exit 0（145 条）。**后端只加了一个端点，全量 pytest 这一轮没跑**（16 那遍 3655/34/0 仍是最近一次全量） |
| `python scripts/build_mcp_widget.py --check` | ✅ 已重建 + 一致（指纹 `f0875a2608115edd`） |
| `python scripts/build_browser_playground.py --check` | ✅ 已重建 + 一致（指纹 `36826a10beef7d5a`；`web/dist-playground/` 不进 git） |
| `git diff --check` | ✅ |
| 变异反证 22 条 | ✅ 19 红、3 存活且成因说得清（M8 / M9 结构性——jsdom 没有监听器之间的微任务检查点；M12 语义 no-op），见 `TEST_MATRIX.md` |
| 真浏览器 `e2e/quick-menu.spec.ts`（Playwright chromium，**进仓库**） | ✅ 1400×900：面板右键菜单十项、hover 排列层级子菜单、**子菜单上按 Esc → 菜单关、选区不动、单选浮动栏回来**（第一遍红：选区被全局 Esc 清空，见 T-98）；「重新构建」真的冷构建一遍脚本 → toast「已按源脚本重新构建」；↓ ↓ 聚焦到「重新构建」、按 r 不切矩形工具；面板拖到画布右下角右键 → 菜单翻到光标上方（y 551–876 ≤ 885）、子菜单翻到左边（x 852–1026 ≤ 1030）；⌘A 三对象右键 → 多选菜单「参照：选区」→ 左对齐 x 全等（664）；文字右键「编辑文字」进编辑态。截图七张在 scratchpad |
| Playwright e2e 全量 | ⚠️ **没跑**（只跑了上面那条）。13 记的六条红仍开着 |

---

### Session 19 之后（改动后实跑，**冻结前端 + 重建两个产物之后**跑的一遍）

| 命令 | 结果 |
| --- | --- |
| `PYTHONPATH=<wt>/src <repo>/.venv/bin/python -m pytest -x --deselect tests/test_codex_e2e.py` | ✅ exit 0 —— **3705** passed / 34 skipped / 0 failed（比 16 的 3655 +50：`test_package_management.py` 45 + 其余 5）。**跑了三遍才绿，前两遍都是我的错**：第一遍带了 `TAVOTTO_WORKER_PYTHON`，它压过项目记住的 venv，`test_dependency_repair_e2e::test_golden_path_install_into_the_project_venv` 在第二次 probe 时拿到 `project_env_already_attempted`（Session 15 记过同形状——全量别带那个变量，只有单跑 worker 用例文件才要）；第二遍 84% 处被 `test_source_hygiene::test_windows_bound_subprocesses_pin_their_decoding` 拦住——新测试里一个 `subprocess.run` 没给 `encoding="utf-8"`。三遍都与 vitest 全量**串行** |
| `cd web && pnpm test` | ✅ exit 0 —— **171** files / **2387** tests passed（比 18 的 167/2344 +4 文件 / +43 条：`SettingsDialog.test.tsx` 12 + `PackagesSettings.test.tsx` 19 + `DiagnosticsSettings.test.tsx` 7 + `agentState.test.ts` 2 + Agent 页 +3；`settingsDisclosure.test` 的 About 四条改写成诊断页四条，`profilesSettings.test` 的「切到规范」改成按 kind 重渲染） |
| `cd web && pnpm build` | ✅ exit 0（`tsc -b && vite build`；第一版 `e2e/settings-shell.spec.ts` 把 `app` 夹具当对象用，被 tsc 抓到——e2e 文件也在 tsc 范围里） |
| `cd web && pnpm i18n:check` | ✅ exit 0（zh-CN 3069 / en-US 3165；新增 `dialogs:settings.section.{interface,style,spec,packages,diagnostics}`、`settings.packages.*` 60 组、`settings.diagnostics.*` 9 条、`settings.agents.{versionAria,detail.copy*}`、`settings.{copy,copied}`、`profiles.{binding.*,details,detail.*}`；`errors:engine.repairError.package_*` 7 条；删掉 `section.{profiles,shortcuts}` / `agents.{intro,codexIntegrationDesc}` / `about.{environmentTitle,engineOk,engineStatusHint,diagnosticsTitle}` / `profiles.{kind.style,kind.spec,kindAria}` / `engine.bundledPackages`。第一版 `PackagesSettings.tsx` 里写死了一个 "Python"，`i18next-cli lint` 当场红） |
| `cd web && pnpm lint` | ✅ 无 error；新文件 0 条提示 |
| `ruff check . && ruff format --check .` | ✅ exit 0 |
| `PYTHONPATH=<wt>/src TAVOTTO_WORKER_PYTHON=… pytest tests/test_package_management.py tests/test_dependency_repair.py tests/test_error_codes.py tests/test_i18n_dead_keys.py tests/test_diagnostics_bundle.py tests/test_ai_bridge.py` | ✅ exit 0（`test_package_management.py` 45 条，含三条离线真安装：建受管环境 → 装本地 wheel → 升级 → 卸载 → 账 / import / 快照 / 宿主解释器全部核过；`test_dependency_repair.py` 的清单断言多了 `reason` 枚举） |
| `python scripts/build_mcp_widget.py --check` | ✅ 已重建 + 一致（指纹 `4f10cda116943005`） |
| `python scripts/build_browser_playground.py --check` | ✅ 已重建 + 一致（指纹 `de4a1f68a2a0afc7`；`web/dist-playground/` 不进 git） |
| `git diff --check` | ✅ |
| 变异反证 23 条（Python 12 + 前端 11） | ✅ **23/23 全红**，基线绿（见 `TEST_MATRIX.md`） |
| 真浏览器 `e2e/settings-shell.spec.ts` + `e2e/coding-agents.spec.ts`（Playwright chromium，**进仓库**） | ✅ 11/11。**第一遍 5 红**：① Agent 一级列表的状态徽章定宽 `w-24`，英文「Sign-in required」把行撑破（4 条测溢出的一起红）；② 本机 claude 的 shim `--version` 第一行是 bash 报错（带 `/Users/…` 完整路径），`agentVersionLabel` 抽不出数字就回原文 → 一级页面出现了完整路径；③ 进场动画没跑完就量 `boundingBox`（747×590 vs 760×600）；④ <1024 的抽屉遮罩淡入动画让 Playwright 恒判「不稳定」，点不到设置按钮。前两条是产品缺陷，后两条是量法。截图八张在 scratchpad（`s19-*.png`） |
| Playwright e2e 全量 | ⚠️ **没跑**（只跑了上面两条 spec）。13 记的六条红仍开着 |

---

### Session 20 之后（改动后实跑，**冻结前端 + 重建两个产物之后**跑的一遍）

| 命令 | 结果 |
| --- | --- |
| `PYTHONPATH=<wt>/src <repo>/.venv/bin/python -m pytest -x --deselect tests/test_codex_e2e.py`（**不带** `TAVOTTO_WORKER_PYTHON`，与 vitest 串行，树干净） | ✅ exit 0 —— **3756** passed / 34 skipped / 0 failed，11 分 09 秒（比 19 的 3705 +51：`test_tutorial.py` 47 + `test_error_codes.py` 四个 `tutorial_*` code 各一条参数化） |
| `PYTHONPATH=<wt>/src TAVOTTO_WORKER_PYTHON=… pytest tests/test_tutorial.py` | ✅ **47** passed（含 worker 真跑两张教程图；读 dist 的三条在 `python -m build` 之后跑，也绿） |
| `pytest tests/test_error_codes.py tests/test_projects.py tests/test_project_env.py tests/test_diagnostics_bundle.py tests/test_runtime_build.py tests/test_source_hygiene.py tests/test_ci_tooling.py tests/test_release_workflow_contract.py tests/test_merge_queue_workflows.py tests/test_update_chain_gates.py tests/test_autosave.py tests/test_package.py tests/test_i18n_dead_keys.py` | ✅ 520 passed / 6 skipped（第一遍 `test_source_hygiene` 抓到新测试里一个没钉 `encoding` 的 `subprocess.run`——与 19 同形状） |
| `python -m build` | ✅ `tavotto-0.12.0-py3-none-any.whl`（1.47 MB）+ `tavotto-0.12.0.tar.gz`；wheel 里 `tavotto/resources/tutorial_project/` 9 个成员 37 524 字节，sdist 同 9 个；`tavotto/web/index.html` 在 |
| `cd web && pnpm test` | ✅ exit 0 —— **171** files / **2387** tests passed（与 19 相同：本轮没有前端用例） |
| `cd web && pnpm build` | ✅ exit 0 |
| `cd web && pnpm i18n:check` | ✅ exit 0（`errors:backend.tutorial_*` 四条双语；`resources.d.ts` 经 `pnpm i18n:types` 重生成） |
| `ruff check . && ruff format --check .` | ✅ exit 0 |
| `python scripts/build_mcp_widget.py` / `build_browser_playground.py` | ✅ 已重建（指纹 `2745c510f75b89fc` / `455ea989fd650a30`） |
| `git diff --check` | ✅ |
| 变异反证 22 条（Python 22） | ✅ 20 红 + 2 存活各自处置（M9 补用例后复跑红；M22 语义 no-op 删掉），见 `TEST_MATRIX.md` |
| `python scripts/smoke_app.py --python <repo>/.venv/bin/python --tutorial`（真进程，`TAVOTTO_WORKER_PYTHON` 指向 homebrew 3.13 + matplotlib 3.10.8） | ✅ 冒烟通过：`GET /api/tutorial` → open（2 个脚本）→ `Fig1_kinetics` 300 ms 冷启动 → `Fig2_correlation` 310 ms → reset → 副本完整 → 干净退出无残留 worker |
| 桌面 PyInstaller 产物 | ⚠️ **本机跑不了**（`.venv` 没装 PyInstaller，也没有 Rust supervisor 二进制）。spec 的 datas 改动只有静态断言（`test_desktop_spec_ships_the_tutorial_resources_as_datas`）+ CI 桌面腿的 `--tutorial` 冒烟能证明 |
| Playwright e2e 全量 | ⚠️ **没跑**（本轮没有 UI 改动；13 记的六条红仍开着） |

### Session 21 之后（改动后实跑，**冻结前端 + 重建两个产物之后**跑的一遍）

| 命令 | 结果 |
| --- | --- |
| `cd web && pnpm test` | ✅ exit 0 —— **179** files / **2452** tests passed（比 20 的 171 / 2387 多 8 个文件 78 条：`onboardingStore` 12 / `activity` 5 / `selectionStore` 3 / `position` 8 / `flow` 13 / `tutorial` 10 / `hints` 8 / `onboardingLayer` 6，另 `alignSelectedTo` 两条改成只看排列三种 kind） |
| `cd web && pnpm build` | ✅ exit 0（`tsc -b` 含 e2e 工程） |
| `cd web && pnpm i18n:check` | ✅ exit 0（zh-CN 3170 / en-US 3266 条；`resources.d.ts` 经 `pnpm i18n:types` 重生成） |
| `cd web && pnpm lint` | ✅ 无 error（只有既有的 fast-refresh 提示；第一遍抓到 `ObjectContextMenu` 里我把 `useEffect` 放在早退之后——Hook 顺序随 `obj` 变，已改） |
| `PYTHONPATH=<wt>/src <repo>/.venv/bin/python -m pytest tests/test_i18n_dead_keys.py tests/test_error_codes.py tests/test_source_hygiene.py tests/test_ci_tooling.py tests/test_mcp_resolver.py tests/test_tutorial.py -k "not worker"` | ✅ **247** passed / 1 skipped / 5 deselected。**本轮 Python 源码零改动**，全量 pytest 没跑（20 的 3756 / 34 / 0 仍是最近一次全量） |
| `python scripts/build_mcp_widget.py` / `build_browser_playground.py`（各 `--check`） | ✅ 已重建并一致（指纹 `e24359828915068d` / `532128103da274fa`） |
| `python scripts/build_frontend.py` + `npx playwright test e2e/tutorial.spec.ts --project=chromium`（真后端 + 真 matplotlib） | ✅ **4 passed**（第一遍 4 红：treeitem 折叠 / Tab 顺序 / 像素判据 / 切回项目是空白文档；第二遍 3 红：dialog 定位器把 coachmark 算进去 / `data-*` 布尔值是 `"true"` / 拖动落在抽屉把手上；第三遍 1 红：坐标对象被平移出工作区——**产品缺口**，加 `hiddenInStage` 后绿） |
| `npx playwright test --project=chromium`（全量，冻结前端 + `build_frontend.py` 之后，14 分钟） | ⚠️ **89 passed / 3 failed**：`cross-tab-paste`（**本轮引入**：`HintToast` 第一版占了 `role=status`，与状态区撞名——已改成只留 `aria-live`，重建后单跑复绿）；`ux-consistency` 流程 B / D（**既有**：16 把刻度卡拆成 X/Y 页签、19 把「项目与路径」改名「项目」，用例没跟上；画面里没有教程元素）。13 记的那六条红这次**没有再出现**（a11y / i18n / keyboard-golden-path / asset-library 全绿） |
| 变异反证 24 条（前端） | ✅ 22 红 + 2 存活各补用例后红（M8 教程外的信号、M24 选区没变也发），见 `TEST_MATRIX.md` |
| `git diff --check` | ✅ |

### Session 22 之后（改动后实跑，**冻结前端 + 重建两个产物之后**跑的一遍）

| 命令 | 结果 |
| --- | --- |
| `PYTHONPATH=<wt>/src <repo>/.venv/bin/python -m pytest -q tests`（全量，含 `TAVOTTO_NO_TELEMETRY=1`） | ✅ **exit 0**，0 failed / 34 skipped；总数按进度点估约 3.8k（此配置下摘要行不打印，20 的最近一次是 3756，本轮净增 47 条） |
| `pytest tests/test_ai_refresh.py tests/test_telemetry_integrations.py tests/test_mcp_server.py`（新增 47 + MCP 全部） | ✅ exit 0（`test_widget_artifact_is_in_sync` 在重建产物前红一次，重建后绿） |
| `cd web && pnpm test` | ✅ exit 0 —— **182** files / **2496** tests passed（比 21 的 179 / 2452 多 3 个文件 44 条）；第一遍 exit 1 是新用例留下一条 unhandled rejection（`fetchPanels` 没给值），补 mock 后绿 |
| `cd web && pnpm build` | ✅ exit 0（`tsc -b` 含 e2e 工程） |
| `cd web && pnpm i18n:types && pnpm i18n:check` | ✅ exit 0（zh-CN 3178 / en-US 3276 条）；`pnpm i18n:extract` 跑过一次：它往 inspector / project / shortcuts / common 塞空键与拆复数基键，`i18n:check` 当场红，产物 `git checkout` 掉（T-121） |
| `cd web && pnpm lint` | ✅ 无 error |
| `ruff check . && ruff format --check .` | ✅ |
| `python scripts/build_mcp_widget.py` / `build_browser_playground.py`（各 `--check`） | ✅ 已重建并一致（`317e8e756cd08a1a` / `ce546102484da66b`） |
| `python scripts/build_frontend.py` + `TAVOTTO_PYTHON=<repo>/.venv/bin/python PYTHONPATH=<wt>/src npx playwright test e2e/{tutorial,quick-menu,asset-library}.spec.ts --project=chromium` | ✅ **9 passed**（教程 4 / 右键菜单含多选对齐 1 / 素材库含 readiness 入口 4，1.7 分钟）。没有为命令面板 / 接入中心遥测新写 e2e（遗留表） |
| 变异反证 17（后端）+ 19（前端） | ✅ 后端 14 红 + 3 存活各处置后红（M3 补用例、M6 删冗余、M13 补断言）；前端 18 红 + 1 存活补断言后红（F4），见 `TEST_MATRIX.md` |
| `git diff --check` | ✅ |

### Session 23 之后（终审：改动后在**最终树**上再跑一遍全量）

| 命令 | 结果 |
| --- | --- |
| `ruff check . && ruff format --check .` | ✅（第一遍红：矩阵用例改判据后漏跑 formatter，`64adbebe` 修） |
| `build_mcp_widget.py --check` / `build_browser_playground.py --check` | ✅ 一致（`e97fd2530046d37a` / `09a8abe9eab7a60b`） |
| `PYTHONPATH=<wt>/src .venv/bin/python -m pytest -q tests`（`TAVOTTO_NO_TELEMETRY=1`，junit 计数） | **3861 条：1 failed / 34 skipped**（753 s）——唯一红仍是 `tests/native/test_run_cli_integration.py::test_ctrl_c_reaches_the_script_and_leaves_no_orphan`（两次全量都红、单跑 2/2 绿 0.8 s；`tavotto run` 线，本分支未动 `runspec` / `cli` / `tests/native`，Session 06 起就有） |
| `cd web && pnpm test` | ✅ 184 文件 / 2503 条（17 s；比基线多 2 个文件 7 条） |
| `pnpm build` / `pnpm i18n:check` / `pnpm lint` | ✅ / ✅ / ✅ 无 error（主 chunk 1.85 MB，R-17） |
| `build_frontend.py` + Playwright 三个 project 全量 | ✅ **126 条：125 passed / 1 skipped / 0 failed**（707 s；改动前 2 红的流程 B / D 已绿） |
| `python -m build` + `scripts/ci/lab_acceptance.py --dist dist` | ✅ 结构断言 9 项 + 端到端冒烟全过（wheel 1.50 MB） |
| `scripts/bench_render.py` 交错 A/B/C ×3 / `scripts/bench_document.py` / watcher 空闲采样 | ✅ 数据在 `docs/perf-baseline.md`「发布终审」 |

改动前的基线（本树 `5608008f`）：pytest 3840 条 1 红（`test_ctrl_c…` 负载敏感、单跑绿）、vitest 2496 绿、
e2e 126 条 2 红（流程 B / D）——全部记在 `TEST_MATRIX.md` Session 23。

## 发布结论（Session 23）

```text
BLOCKED — 不建议发布
```

本分支：**P0 = 0；P1 = 1**（本轮修掉的 P1：原图 PDF 文本层缺科学字符 T-122、升级验收两条空检查 R-18、
另存为不校验 T-123）。剩下的那 1 条是 **#240**——它不是本分支引入的（`tavotto run` 线，本分支未动
`runspec` / `cli` / `tests/native`），但**它红着，「全量自动化真实通过」这条门禁就没通过**，
所以不再记成 0。阻断项与最短修复路径：

1. **main 上 Lab Qualification 连红**（#225，`test_project_env.py` 14 条）——release 档的 lab 资格拿不到。
   **已定性（PR #239）：根因是用例的前提错了，不是机器残缺——机器侧无待办。**
   `tests/support/venvfixture.py:57` 的 `make_project_venv()` 用 `python -m venv --system-site-packages`，
   而 `--system-site-packages` 继承的是 **`sys.base_prefix` 的 site-packages，不是交给夹具的那个解释器的**；
   两者只在「那个解释器就是基础解释器」时才重合。GitHub 腿一直绿是因为**重合**
   （`.github/workflows/ci.yml:395-397` 把 `-e ".[dev]"` 与 matplotlib 装进 setup-python 的解释器，
   pytest 用的也是它）；Lab 红是因为**不重合**（`_lab-qualification.yml:114-116` 是
   `python3 -m venv "$VENV"` 之后往 `$VENV` 里装，base 是 `/usr/bin/python3`）。这条规则夹具自己
   早就写在 `venvfixture.py:32-38` 的 docstring 里，当时只推到了「遮掉 `tavotto`」，没有推到 matplotlib 上。
   ~~修法：给实验室 runner 的 base 解释器装 matplotlib~~ —— **这条解药是错的，别照做。**
   科学栈版本由 `packaging/runtime-lock.json` 钉住、经 `scripts/ci/runtime_pins.py` 装进那个一次性 venv
   （理由写在 `runtime_pins.py:4-5`：matplotlib 换一个小版本就会改掉抗锯齿、字体度量或默认样式）；
   装到系统解释器上，等于让一个**没锁版本**的 matplotlib 经 `--system-site-packages` 漏回夹具 venv，
   像素基线随之失去意义。**修法只有一条**：让 `make_project_venv` 不假设 base 带它 —— 见 **PR #239**。
   状态：**待 CI 验证**（PR #239 未合；#225 真正关闭仍需 Lab Qualification 实跑一次绿）。
2. **main 上 Nightly CompatBench 连红**（#226，四个多图用例 `native_run` 退成 product_bug）——nightly 门禁。
   修法：compat-bridge 轨道修 native run 的多图路由或按证据改基线声明。
3. **桌面产物本分支未在 CI 执行过**：Session 19–23 改了 `tavotto.spec` 的 datas 与两条 workflow 的
   `--tutorial`，它们只在合并队列 / `full-ci` 标签 / tag 上第一次执行。修法：先给本分支的 PR 打 `full-ci`
   标签，看 `windows-exe-smoke` / `macos-app-smoke` 两腿真过一次。
4. Distribution metrics 连红（#227）不阻断产品，但发行量指标断了六天，发版前该修。
5. **`test_ctrl_c_reaches_the_script…` 在全量里长期红**（#240，Session 06 / 15 / 23×2；07 / 16 绿）
   ——门禁「全量自动化真实通过」指的就是这张表。修法：先**定性**（`tavotto run` 控制通道的真缺陷，
   还是用例的时序前提），再按定性处置；不允许 skip / xfail / 删。

以上五条都不在本分支的改动范围内；1–3、5 任一不清，结论不变。清完之后本分支满足门禁清单其余各项
（见下方逐条）。

### 门禁清单逐条

| 项 | 状态 |
| --- | --- |
| Gate 1–5 全部通过 | ✅（01–22 各自的结果表 + 本轮审计未发现空门禁） |
| P0 为 0 | ✅ |
| P1 为 0 或有批准的例外 | ✅ 本分支；❌ main 上 #225 / #226 |
| 全量自动化真实通过 | ⚠️ **不是绿的**：Session 23 终审两次全量都是 `1 failed`（`test_ctrl_c_reaches_the_script_and_leaves_no_orphan`，见上方 Session 23 结果表）。这一格在 #240 定性并处置之前**不算通过** |
| 关键 E2E 通过 | ✅ 见上方 Session 23 结果表：Playwright 三个 project 全量 125 passed / 1 skipped / 0 failed，改动前 2 红的流程 B / D 已绿 |
| 文档 migration / 保存 / recovery | ✅（Gate 1 用例 + 本轮 round-trip / 未来 schema 拒绝） |
| original / canvas export fidelity | ✅（既有像素 / 尺寸用例 + 本轮 PDF 文本层矩阵） |
| 特殊字符矩阵 | ✅ `test_scientific_text_matrix.py` 7/7 |
| package manager 隔离与安全 | ✅（审计 §3：结构性，34 条用例含 3 条真装） |
| wheel / sdist 与目标 desktop | ✅ wheel / sdist 本机验；❌ desktop 本机未验、CI 未执行本分支 |
| i18n / a11y | ✅ `i18n:check`、a11y / contrast / i18n e2e 三个 project 绿；对比度缺口已修 |
| telemetry / privacy 文档一致 | ✅（三方逐位一致；`target_version` 措辞收窄） |
| TEST_MATRIX / STATUS / DECISIONS / UX_CONTRACTS 更新 | ✅ |
| 工作树无临时文件和无关改动 | ✅（`git status` 干净；`dist/`、`src/tavotto/web/` 为 gitignore 产物） |

### 真实用户流程 A–N（自动化覆盖；映射逐条在会话目录 `scenario-coverage.md`）

| 场景 | 覆盖 | 无自动化 / 备注 |
| --- | --- | --- |
| A 单图快速编辑 | e2e `asset-library` 完整链（保存 → 关闭 → 重开 → 字号仍 13）+ store 用例 + 本轮 ⌘S 键位 | — |
| B dirty / 关闭 / 恢复 | `saveStateMachine` 全套（clean 不拦、保存中编辑、恢复副本裁决、主文档不动）+ atomicio 五条 | 三选一对话框不存在（#223）；真实进程 kill |
| C 迁移 / 项目移动 | schema 2 → 3 前端迁移 + 未来 schema 两侧拒；项目移动 `test_project_env` / registry 相对路径 | 迁移前逐文档备份（原文件在显式覆盖前不动） |
| D 外部修改 | `test_project_watch` 批次 / 自写不回环 / 外部紧接触发；不跑脚本 | 自动保存不进 watcher（结构上在数据目录，未断言） |
| E 就绪度六档 | `test_readiness*` + 接入中心 e2e | — |
| F 原图导出 | ADR 0028 用例（scope=original 无 x/y/w/h、DPI 来源、透明）+ 本轮文本层 | — |
| G 画布导出 | 页面 mm 尺寸 / 翻转 / 旋转 / hidden / 部分失败 / 覆盖 | 多面板与裁剪面板的像素断言 |
| H Style / Spec / 问题 | ADR 0029/0030 用例（快照、7.5 pt → 8 pt、focusIssue、safe fix undo、导出摘要同源） | — |
| I 科学文本 | **本轮矩阵** 六位置 × 四产物 | — |
| J 图例 / 刻度 | ADR 0034/0035 用例 + e2e 流程 B（本轮修） | — |
| K 多选 / QuickEdit | ADR 0036/0037 用例 + `quick-menu` e2e | — |
| L 设置 / 包管理 | `settings-shell` / `coding-agents` e2e + 包管理 34 条 | 真 150% scale（600 px 视口等价） |
| M 教程 | `tutorial` e2e 四条 + 后端 47 | — |
| N Codex / AI 刷新 | `test_ai_refresh` 18 + MCP 16 + 前端 `useServerEvents` | — |

## 遗留（Session 23 之后仍开着的）

| 事项 | 级别 | 复现 / 影响 | 归属 |
| --- | --- | --- | --- |
| main：Lab Qualification `test_project_env` 14 红 | **P1（发布）** | #225；已定性 = 夹具前提错（`--system-site-packages` 继承 base，不是交给它的解释器），**不是机器残缺**；修复在 **PR #239（未合）**，状态**待 CI 验证** | project-env（**lab runner 侧无待办**） |
| main：Nightly CompatBench `native_run` 4 用例退成 product_bug | **P1（发布）** | #226 | compat-bridge |
| 桌面产物：本分支的 datas / `--tutorial` 改动未在 CI 执行过；`delivered: local` 桌面限制 | P1（未验证，不是缺陷） | 合并队列 / `full-ci` 第一次执行 | 23 → PR |
| main：Distribution metrics HTTP 400 | P2 | #227 | ops |
| 热渲染比 main 慢 15%（manifest +27%） | P2 | #220，阈值 1.3× / 2× | engine |
| 版本时间线整份文档、autosave 无上限 | P2 | #221（5 000 对象 547 ms） | documents |
| 另存为无冲突检测；读侧 NaN 无对称闸 | P2 / P3 | #222 | documents |
| 桌面壳无 CloseRequested 处理 | P2（待真机） | #223 | desktop |
| MCP 插件第二份导出实现 | P2 | #224 | codex-plugin |
| 原图写回**已经是原子的**（`os.replace` + 备份 + 回滚），只缺 fsync | P3 | #252，直接换 `publish_file` 会引入半应用路径 | 择机 |
| `test_ctrl_c_reaches_the_script…` 在全量里红、窄范围绿（06 / 15 / 23×2 红，07 / 16 绿） | **P1（门禁）** | **#240**；性质未定：`tavotto run` 真缺陷 vs 判据的时序前提，两者都没被排除。**上方门禁清单「全量自动化真实通过」那一格指的就是这张红表** | tavotto run 线 |
| 前端主 chunk 1.85 MB / gzip 574 kB（R-17） | P3 | **#246**；`pnpm build` 告警，`vite.config.ts` 无 `manualChunks` | 择机 |
| README 两张预检截图是旧规范拍的 | P3 | **#247**；alt 如实，图过期——alt 里的「8.5 pt 与 8 pt 两条下限」在 ADR 0029 之后只剩一条 8 pt | 择机 |
| `problem_focused` / `export_completed.scope` 两条事件未加 | P3 | **#245**；两侧同源对（`engine/telemetry.py` ↔ `services/telemetry_proxy/.../contract.py`）必须一起改 | 下次遥测扩容 |

Session 19 之后那张长表里其余「已处置 / 已决定不做 / 择机」各项原样有效，不再复制。

## 下一阶段

没有下一阶段。本轨道 23 个 Session 全部完成；接下来是**发布路径**：

1. 推分支、开 PR（13–23 十一轮攒在 `feat/product-ux-13-properties`，按用户节奏拆或不拆），打 `full-ci`
   标签让两条桌面腿第一次执行本分支的 spec / workflow 改动；
2. 处理 #225 / #226（不在本分支），Lab release 档跑绿；
3. ~~`pnpm sync-playground` 同步网站 /try~~ —— **已完成**：网站仓 `014a997`「同步 /try playground 到 98a866c」，`public/try/playground-manifest.json` 与 `engine.zip` 已随之更新；
4. 之后按 `docs/1.0-release-readiness.md` 走 tag。

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

