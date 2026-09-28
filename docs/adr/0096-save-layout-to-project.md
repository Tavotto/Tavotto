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

### 四之二、写回串行，落账记在发请求那一刻的项目名下（评审第 1 轮）

- **串行**：⌘S 连按时两次都读到同一个旧绑定并发发出，后到的那次带着旧基线撞上前一次
  刚写成的修订号，回一个「被别处改过」——而别处就是自己。`writeBoundProjectFile()` 同一时刻
  至多一次在路上、后面至多排一次；排着的那次轮到时**现读**绑定（拿到推进后的修订号与最新
  内容），前一次没写成就不再排，换了排版就不写。另存为对话框同理挡回车连按（同步 ref，不等渲染）。
- **pj 在发请求那一刻取**：修订号缓存（`lib/layoutRevision.ts`）按「项目 + 名字」记，await 之后
  落账时读此刻的 `currentProjectId()` 会把 A 的修订号记到 B 的同名排版头上。现在三个落账点
  （⌘S 写回、另存为、打开）都显式传发请求时的 pj；冲突岔口只在同一个项目（⌘S 还要求同一份排版）
  仍开着时打开——切走之后再弹，「仍然覆盖」会把 A 的 hash 发到 B 的同名文件上；打开途中切了项目
  就不把 A 的排版当成 B 的文件打开。

### 四之三、按排版合并、显式保存不许静默落空、修订号按规范名记（评审第 2 轮）

- **合并只在同一份排版之内**：第 1 轮是全局「一次在路上 + 一次排着」，A 有一次排着时切到 B
  按 ⌘S，B 被并进 A 的排队，轮到时 documentId 已不是 A 被丢掉——B 的保存静默没发生。现在全局
  仍串行，但每份排版各记最近一项：同一份排版还没开始的那次才并进去，别的排版自成一项排在队尾。
  队列空闲时当场开始（同步读绑定与内容）。
- **没写成必须说出来**：轮到时已换了排版 / 项目、不写的那一次，状态条报 `save.projectSkipped`
  （error 色调），与冲突、写失败一样不静默。
- **修订号按后端规范名记**：后端净化文件名（`Untitled layout` → `Untitled_layout`），另存为却按输入名
  查修订号，第二次另存为带 `absent` 撞出假冲突。前端不复刻净化规则（Python `\w` 与 JS 正则认的字符集
  不同源），只记后端回过的「输入名 → 规范名」（`rememberLayoutName`），查修订号与「已有同名」提示
  都先经它换成规范名。净化是确定性的，这张表不会过期。
- **旧回执不覆盖新绑定**：⌘S 在路上时另存为把这份排版改绑到了别的文件，旧请求落账时只在本机记录
  仍指着请求时的那个文件（同项目、同 `file`）时才更新绑定——否则新绑定是用户更晚的决定，写回去
  会让之后的 ⌘S 静默写回旧文件。
- **读不出修订号就拒写**（后端，P1）：目标文件在却读不出（mode 000 / 被独占），旧实现把它当成
  「被删了」放行覆盖；现在 409 `revision_unreadable`，详见 `docs/rules/backend/layout-versions-and-documents.md`。

### 四之四、保存上下文：入口取一次，每个 await 点之后用同一个判据（评审第 3 轮）

同一家族第三次（请求途中切项目 → 排队途中切排版 → `saveNow()` 途中切排版）都是「await 之后
现读此刻的 documentId / 项目 / 绑定」。逐个 await 补守卫补不完，换成结构（`store/saveContext.ts`）：

- ⌘S（`runManualSave`）、另存为、打开三个入口各调一次 `captureSaveContext()`，整条链只用这份捕获值；
  写回队列按上下文分项（排版 + 载入代次 + 项目 + 绑定文件）。
- 每个 await 之后，要作用于**此刻界面**的事（写项目文件、弹「存进项目」、开冲突岔口、打开排版、
  往对话框放名单）先过 `stillCurrent(ctx)`；不是了就报「没写成 / 没打开」（`save.projectSkipped` /
  `save.skippedSwitched` / `layout.openSkipped`），不静默。已经发生的事实（写成的修订号、那份排版的
  绑定）照样记在 `ctx` 名下。
- 判据三维：载入代次（全局单调，换排版必换，重新载入也算）、项目（没绑定的排版切项目只有它看得见）、
  入口那一刻绑定的文件（另存为改绑）。await 点清单写在 `saveContext.ts` 头上，`projectSave.test.ts`
  按「await 点 × 切走方式」参数化逐个覆盖。
- **「写的途中改没改过」比的是被提交那份排版自己的编辑代次**（评审第 4 轮）：`projectFileSnapshot()`
  原先比整个 store 的 `persistedIdentity`（含 `documentId` / `loadSeq`），写的途中一切走就必判「变过」，
  给原排版记上 `dirty`。现在 documentStore 按 `documentId` 记本会话的**用户**编辑代次，快照记那一份的代次，
  写成后只比它。代次与绑定的 `dirty` 同源：只在 `markProjectFileDirty()` 里前进（`saveState` 推 `dirty`
  那一处 + 改排版名），派生同步（`applyDerivedUpdate`）、载入、换文档都不算——第 4 轮一度按「要落盘的
  内容变没变」算，写的途中来一次元数据同步就亮起圆点（评审第 6 轮）。
- **对话框里 await 之后的每一项界面变更都走 `ifStillCurrent(ctx, …)`**（评审第 5 轮）：开关、错误、冲突岔口、
  打开；切走了不碰此刻的对话框，结果改在状态条上说。例外只有状态条（全局、点名文件）与对话框自己的
  `busy`（请求结束就复位）。另存为成功后那次刷新名单的 `fetchLayoutNames()`（对话框随即关闭，没人看得见）
  连同它的 await 点删掉。
- **绑定以本会话内存为准，localStorage 尽力而为**（评审第 7 轮）：`lib/projectFile.ts` 读先看会话层、写先写
  会话层再尽力持久化（解绑记成 `null`，`removeItem` 也失败时不读回旧副本）。`setItem` 被禁用 / 配额满时，
  ⌘S 写成之后照样推进修订号、清圆点，不会带着旧修订号撞假冲突；只影响刷新之后还认不认得这个文件。
- **「仍然覆盖」写冲突那份文件、不改文档标题**：覆盖目标（可能是规范名 `Untitled_layout`）与名字字段
  分开传；裁决冲突不是改名。

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
