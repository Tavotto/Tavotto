# ADR 0096：⌘S 保存到项目——排版绑定项目里的文件，自动保存仍只在本机

日期：2026-09-26 · 状态：**Accepted**（用户 2026-09-26 拍板「⌘S 保存到项目」，实现随本 PR）
相关：[0023 文档落盘的唯一权威](0023-document-persistence-authority.md)（原子写、内容修订号）、
[0024 保存生命周期与外部修改检测](0024-save-lifecycle-and-external-change.md)（§3 两个基线、§3d 另存为共用判据、§6 ⌘S 真的保存、§9 R-07）、
[0008 统一本机会话认证](0008-unified-local-session-auth.md)（本条不加端点，沿用已认证的 `/api/layouts/<名>`）、
[0001 项目 / 画布 / 标签 / 对象层级](0001-project-canvas-tab-object.md)。

## 问题

界面名词（PR #668）：**项目**（脚本文件夹）/ **排版**（schema 3 JSON，可含多张画布）/ **画布** / **项目包**。

改造前：

| 动作 | 写到哪 |
| --- | --- |
| ⌘S「保存排版」 | 本机数据目录 `layouts/_autosave/<documentId>.json`（与自动保存同一个槽位）|
| 自动保存 | 同上 |
| ⇧⌘S「另存为新排版」 | 项目 `tavottofile/<名>.json`（没开项目时数据目录 `layouts/`），**只是一份快照** |

于是从「项目里的排版」打开一份，改完按 ⌘S，项目里那份一个字节都不变；换电脑、把项目
文件夹发给合作者、`git commit`，改动都带不走。ADR 0024 §9 把「autosave 搬进项目」
（R-07）列为单独处置——本条是那次处置，但**没有**把自动保存搬家。

## 裁决

### 一、排版可以「绑定」项目里的一个文件

绑定 = `{projectId, name, file, revision, dirty}`，按 `documentId` 存本机
（`localStorage` 的 `tavotto.projectFile.<documentId>`，`web/src/lib/projectFile.ts`），
**不进文档**：文档跟着项目包 / git 到了别的电脑上，那边的同一个文件不该带着这台电脑的
绑定。两条路会建立绑定：

- 从「项目里的排版」打开（`GET /api/layouts/<名>`）——新会话绑定那个文件，基线是这次读到的那一份；
- 存进项目（第一次 ⌘S 的「存进项目」、以及开着项目时的「另存为」）——绑定刚写成的那个文件。

`projectId` 只认当前项目：一份在项目 A 里绑定的排版被「最近排版」带进项目 B 时，
在 B 里它是一份还没存进项目的排版（⌘S 走第二条），A 的绑定原样保留。

### 二、⌘S 的三条路

| 这份排版 | ⌘S |
| --- | --- |
| 绑定了当前项目里的文件 | 先存本机自动保存（与以前一样等到写完），再 `POST /api/layouts/<名>?target=project&base_revision=<绑定的修订号>` 原子写回 |
| 开着项目、没有绑定 | 先存本机；弹「存进项目」命名框（与另存为同一个表单，**预填排版名**，不是画布名 `Figure 1`），写成后绑定 |
| 没开项目 | 只存本机，提示「已保存在本机（没有打开项目）」 |

老数据（只在 autosave 里的排版）落在第二条：按一次 ⌘S 问一次名字，**不自动批量写进项目**。
本机那一份与别的窗口撞了（`saveState === 'conflict'`，docConflict 规则）时不写项目文件——
先裁决哪一版是对的。

### 三、自动保存仍只写本机

自动保存是崩溃恢复，不是「保存」。它若写项目文件，用户没按保存，项目里（可能在 git 里、
可能正被合作者读）的文件就变了。于是项目文件多了一根轴：绑定里的 `dirty` = 项目里那份
落后于工作副本。它**只跟用户编辑**（`startAutosave` 订阅里的「用户编辑」那一档，外加改
排版名——名字写在文件里）；外部派生同步（`applyDerivedUpdate`）不置位，理由同
`saveState` 不推 `dirty` 那条。它跟着绑定存本机，刷新之后圆点照样在。**不进关闭保护**：
工作副本已经在本机，关掉应用不丢东西；拦人会让关闭保护说一件不是数据丢失的事。

### 四、基线是绑定里的修订号，不是本窗口的观察

`lib/layoutRevision.ts` 的注释说「只活一个窗口的生命周期，不进 localStorage」——那个量
回答的是**本窗口读到过哪一份**，给另存为用。⌘S 要的是另一个量：**这份工作副本基于项目
文件的哪一版**（像 git 的 base commit）。它是这份工作副本的事实，刷新之后依然成立；而
持久化它不会放过外部修改——磁盘上那份被 git pull / 另一台电脑改过的话，内容 hash 对不上，
后端 `_revision_conflict` 照样 409。不知道（`null`）就发 `absent`：磁盘上真有一份，让用户
点头（ADR 0024 §3b）。

409 `external_change` 不静默覆盖：弹「存进项目」命名框，预填绑定的文件名并带上冲突岔口，
出口与另存为那一屏同一个（「仍然覆盖」拿 409 里回的 hash 当基线，§3c；也可以改个名字存成
另一份，之后绑定新文件）。多个窗口同时开着同一份排版（同一个 documentId）沿用现有的
docConflict；同一个项目文件被两份不同的会话打开（打开两次 = 两个 documentId）时，后存的
那一份带着旧修订号，同样 409。

### 五、后端：同一个端点，多一档 `target=project`

不加新端点（ADR 0008 的认证面不变）。`POST /api/layouts/<名>` 加查询参数 `target=project`：

- **必须开着项目**（`current_ctx()`，否则 409 `no_project`）——没开项目时 `project_layout_dir()`
  会静默退回数据目录，用户以为存进了项目；
- 冲突判据仍是 `_revision_conflict` / `REVISION_ABSENT` / `_external_change`，锁仍是
  `_document_lock(path)`，写入仍是 `atomicio.write_json`——一份都不新增；
- **落点只能在当前项目的 `tavottofile/` 下**（开着项目时另存为同样适用）：名字这一维由
  `layout_path` 的净化管住（`/`、`\`、`..` 都变 `_`）；符号链接逃逸由
  `_project_layout_target` 拒——`tavottofile/` 解析后不在项目根之内，或目标文件本身是链接，
  400 `layout_outside_project`（后者不拒的话 `os.replace` 会把用户摆好的链接换成普通文件）；
- **只读卷 / 无写权限**（`EROFS` / `EACCES` / `EPERM`）：403 `layout_read_only`，不是一句可重试的
  500 `write_failed`；磁盘满之类照旧走 atomicio 的通用映射；
- 响应多两个字段 `name`（净化后的文件名）与 `file`（相对项目根的 `tavottofile/<名>.json`）。
  `GET /api/layouts/<名>` 开着项目时多一个响应头 `X-Tavotto-Layout-File`（百分号编码的同一个
  相对路径——⌘S 会写到哪；从旧位置读出来的也指向 `tavottofile/`）。界面原样说出这个路径，
  **不自己拼**（收纳规则只有 `project_layout_dir()` 一份）。

### 六、界面说清去向

- 顶栏保存状态：落定之后说「已保存到项目」/「未存进项目」/「已存在本机」；tooltip 写明
  「已保存到项目：tavottofile/<名>.json」或「已保存在本机（没有打开项目）」；
- 绑定的项目文件落后时，排版名旁亮一颗圆点（画布页签「未保存」的同一颗：`h-1.5 w-1.5 rounded-full bg-ink-3`）；
- ⌘S 之后的状态条：「已保存到项目：tavottofile/<名>.json」/「已保存在本机（没有打开项目）」。

## 存储位置一览

| 东西 | 位置 | 谁写 | 何时 |
| --- | --- | --- | --- |
| 工作副本（崩溃恢复） | 数据目录 `layouts/_autosave/<documentId>.json` + localStorage 兜底 | 自动保存、⌘S | 防抖 1 s / ⌘S |
| 项目里的排版文件 | `<项目>/tavottofile/<名>.json` | ⌘S（已绑定）、「存进项目」、另存为（开着项目） | 只在用户按保存时 |
| 没开项目时的另存为 | 数据目录 `layouts/<名>.json` | 另存为 | 用户按另存为 |
| 绑定 | localStorage `tavotto.projectFile.<documentId>` | 打开 / 存进项目 / ⌘S 成功 / 编辑置 dirty | — |
| 最近排版索引 | localStorage `tavotto.docIndex` | 自动保存 | 不变 |

## 没做的

- 不把自动保存搬进项目（见第三条）。
- 不在排版文件里记「我是哪个项目的文件」：绑定是这台电脑上工作副本的事实，不是文档内容。
- 不给关闭保护加「项目文件未保存」一档（见第三条）；若用户反馈需要，另开一条。

## 看护

- 后端 `tests/test_project_layout_save.py`：写回项目并报出相对路径、`target=project` 不退回
  数据目录、外部修改 409 且磁盘零改动、名字里的路径分量留在 `tavottofile/`、符号链接两种逃逸、
  只读（EROFS 注入 + 真 chmod 0555）、磁盘满仍走通用映射、只走一份 atomicio 与一份冲突判据、
  GET 的 `X-Tavotto-Layout-File`。
- 前端 `web/src/store/projectSave.test.ts`（三条路、冲突、写途中又改、圆点只跟用户编辑）、
  `web/src/components/LayoutDialog.test.tsx`「存进项目与绑定」、
  `web/src/components/topBarSaveDestination.test.tsx`（tooltip 去向、圆点）。
- e2e `web/e2e/save-to-project.spec.ts`：项目里新建排版 → ⌘S 命名 → 改一处 → ⌘S →
  读磁盘上的 `tavottofile/<名>.json` → 刷新后从「项目里的排版」打开，内容一致。
