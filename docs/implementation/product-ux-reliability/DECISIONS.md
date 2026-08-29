# DECISIONS — 本改造轨道的架构决策

> **单一权威原则**（根 `AGENTS.md`）：一条规则只有一个出处。
> 因此本文件里**大部分决策已经是仓库既有 ADR**，这里只写"本轨道怎么用它"
> 并指向权威；只有轨道内新产生的决策才在 `docs/adr/` 新开一份。
>
> 编号 `T-nn` 只是本文件内的索引，不是 ADR 编号。

---

## T-01 自动刷新只做静态扫描，绝不运行用户脚本

**权威**：`docs/adr/0014-safe-native-execution-profiles.md`、
`docs/adr/0020-native-matplotlib-bridge.md`、根 `AGENTS.md` 安全边界段。

**背景**：打开项目、autosave、watcher、readiness、问题检查、自动刷新都想
"知道图现在长什么样"，而最直接的做法是跑一遍用户的脚本。

**决定**：这些路径只允许静态扫描、读 AST、合并 registry、算 fingerprint、
校验文档。**执行**（probe / 渲染 / native 会话 / 依赖安装）一律由用户显式触发，
并显示目标环境与结果。

**现状已落实**：`POST /api/registry/probe` 是显式动作；watcher 只比 mtime；
`/api/native/*` 全部要用户批准（`engine/nativeperm.py`）。

**禁止的替代实现**：在 readiness / watcher / 打开项目里"顺手 probe 一下"；
为了拿一个尺寸就跑一次 `main()`。

---

## T-02 项目文档必须版本化、可迁移、原子写入

**权威**：本轨道新增 → `docs/adr/0023-document-persistence-authority.md`（Prompt 02 落地）。

**背景**：起始 commit 上有 6 处以上各自手写的 "tmp + replace"，其中
`POST /api/layouts/<name>`（用户的"另存为"，最显眼的一次保存）**根本不是原子写**；
没有一处做 fsync，没有一处在失败时清理临时文件，没有一处返回结构化错误。

**决定**：Python 侧只保留**一个**原子写实现，所有文档类写入经它；
文档格式有显式 schema version 与显式 migrator；写入前拒绝非有限数
（NaN / Infinity）。

**后果**：新增写入点必须复用它；`ruff`/测试会挡住第二份实现。

**禁止的替代实现**：在新端点里再抄一遍 tmp+replace；用 `write_text` 直接盖；
用 `json.dumps` 默认参数（它会写出非标准的 `NaN` 字面量，浏览器 `JSON.parse` 解不动）。

---

## T-03 用户文档与派生元数据严格分离

**权威**：本文件 + `UX_CONTRACTS.md` §3 的所有权表。

**决定**：派生数据（registry 映射、fingerprint、worker id、缓存路径、SSE 状态）
的刷新**不得**把文档标脏，也不得进普通撤销历史。出口只有
`documentStore.silent(recipe)` 一个。

**理由**：一旦派生刷新能置 dirty，autosave 就会在用户什么都没做的时候
写出新的 `updatedAt`，多标签页立刻互撞 `stale_write`（这条路径在
`documentStore.ts:892` 的注释里已经被踩过一次并写进了代码）。

---

## T-04 快速编辑与画布排版共享一个文档和编辑底层

**权威**：`docs/adr/0001-project-canvas-tab-object.md`。

**决定**：Prompt 09 引入的"快速编辑"是**同一个 `ProjectDocument` 上的一种模式**，
不是第二个文档模型。模式差异只体现在：默认页面尺寸取原图规格、
导出默认按原图规格、UI 隐藏画布相关控件。

**禁止的替代实现**：给快速编辑单开一套 store / 序列化 / 导出路径；
把 `FigureDocument` 复制成 `FastEditDocument`。

---

## T-05 Style / Spec / Validation / Export 分层

**权威**：`src/tavotto/profiles/publication.json`（Spec 规则的唯一出处）+
根 `AGENTS.md` 的严格同源对表。

**决定**：四层边界见 `UX_CONTRACTS.md` §4。Validation 只读 Spec 求值，
两个求值器靠 `tests/golden/preflight_vectors.json` 对齐。
文档里只存 `profile.id` 与期刊覆盖。

**禁止的替代实现**：在 `preflight.ts` 或 `preflight.py` 里硬编码任何阈值；
把规则快照冻进 `.tavotto` 文档。

---

## T-06 Validation 结果通过稳定对象引用定位

**权威**：`web/src/lib/preflight.ts` 的 `PreflightIssue`。

**决定**：问题项持有 `objectIds` + `gids`（+ 后续补 `canvasId`），
不用数组下标 / 文件名 / matplotlib 内部 id。`issue.id` 是稳定的判据身份，
措辞变化不影响 golden vectors。

**已有的等价类型就是 ObjectRef，不另造第二套。**

---

## T-07 Codex / AI / 包管理复用统一服务

**权威**：`docs/adr/0015`（Coding Agent 注册表）、`docs/adr/0018`（项目 Python 环境解析）、
`docs/adr/0019`（受控依赖修复）、`docs/adr/0005`/`0006`/`0012`（Codex 插件与 MCP）。

**决定**：Prompt 19/22 只做 UI 外壳与接线，不新建第二套 agent 发现、
环境解析或安装实现。包管理只操作 Tavotto 管理的环境，
package spec 结构化校验，禁止 shell 拼接。

---

## T-08 中英文、无障碍、数据恢复是完成条件

**权威**：`docs/i18n.md`、`pnpm i18n:check` 门禁、根 `AGENTS.md`。

**决定**：任何阶段的"完成"都包含：中英文文案齐全、键盘可达、
`prefers-reduced-motion` 生效、以及"这个阶段引入的数据在崩溃后可恢复"。
缺任何一条视为未完成，不得靠"后续阶段补"通过 Gate。

---

## T-09（本轨道自加）不为了本轨道推翻已通过测试的架构

**背景**：起始仓库已经有 1338 条前端用例与百余个 pytest 文件，
写回事务、几何权威、遥测白名单等都有专门的不变量看护。

**决定**：本轨道遇到"现状与 Prompt 描述不符"时，**以现状为准并记录**，
只补缺口。禁止把仓库恢复成 Prompt 编写时的旧版本，禁止趁机重写已稳定模块
（与根 `AGENTS.md` 的 1.0 收敛纪律一致）。
