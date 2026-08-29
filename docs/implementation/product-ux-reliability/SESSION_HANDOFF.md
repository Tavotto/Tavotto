# SESSION_HANDOFF — 跨 Session 交接

> **整段重写，不加行。** 半新半旧的状态块会继承「刚被动过」的可信度，
> 而下一个 Session 没有办法分辨哪几行是这次更新的。

---

## 最近一次：Session 03（2026-08-29）

### 目标

保存状态机 / autosave / 崩溃恢复 / 编辑历史。优先啃三条 P1：
**R-06**（没有显式保存状态机）、**R-08**（没有外部修改冲突检测）、
**R-03**（版本检查点没有画布身份）。

### 实际完成

裁决全文在 **`docs/adr/0024-save-lifecycle-and-external-change.md`**。

**R-06 保存状态机。** `documentStore` 新增两个字段：

- `saveState`：`clean | dirty | saving | saved | save_error | conflict`；
- `saveIssue`：保存卡住的原因（`io` / `stale` / `external`）+ 磁盘那份的摘要。

外加**一根正交的轴** `docNotice`（`recovery` / `schema_too_new`）。
Prompt 03 §二 把 `recovery_available` / `read_only` 与那六个并列，我们**没有**
照抄——它们与保存进度是两根互不相干的轴，塞进同一个枚举会互相顶掉（实现过程中
真的撞见：一次成功的自动保存把刚设上的 `recovery_available` 覆盖掉，而副本还在
本机躺着）。理由写在 `DECISIONS.md` T-10。

状态的唯一写入口是 `setSaveState()` / `setDocNotice()`，别在别处 `setState`。

**R-08 外部修改检测。** `PUT /api/autosave/<id>` 新增 `?base_revision=`
（内容 hash，02 备好的 `content_revision`），强于既有的 `?base=`（updatedAt）。
判据**两条边都钉住**：哨兵 `base_revision=absent` 表示「我读过，磁盘上没有」。
基线缺席时一律先 GET 确认一次，**没有例外**。冲突未决时只写本机副本，一次都
不再撞磁盘；三个出口 = 重新加载 / 明确覆盖 / 另存为。

**崩溃恢复。** 改造前 `readAutosaveDoc` 按 `updatedAt` 挑赢家并立刻推回磁盘
（= Prompt 明令禁止的「启动时自动覆盖主文档」）。现在主文档照常打开，本机副本
**挪**进 `tavotto.recovery.<id>` 等裁决；恢复只进内存并置 `dirty`，用户确认保存
后才覆盖主文档。

**R-03 检查点的画布身份。** 检查点记 `canvasId` + `canvasName`，落点判据在
`web/src/lib/versionTarget.ts` 一处（`same` / `other` / `missing` / `unknown`
四条分支，后三条都要用户点头）。自动检查点的去重判据也加上画布身份。

**顺带修掉三处**（都是这次改动直接照出来的）：

1. `⌘S` 原本打开「保存为画布文件」对话框。现在 `⌘S` = 真的保存并**等到磁盘
   写完**，`⇧⌘S` = 另存为；`⌘S` 在输入框/对话框里也拦（否则浏览器弹「保存网页」）。
2. `beforeunload` 只在有未落盘工作时拦，且**先读状态再冲刷**——反过来
   `flushAutosave()` 会把状态推成 `saving`，于是干净文档每次刷新都拦一次。
3. 排队中的写入带走**排队那一刻**的 `pj`：`dropProject()` 先冲刷再忘掉 pj，
   而 PUT 要过几个 await 才发出，读全局的话这份自动保存落进后端的默认项目。

### 关键 API（Prompt 04 及之后可以直接用）

```ts
// web/src/store/documentStore.ts
type SaveState = 'clean'|'dirty'|'saving'|'saved'|'save_error'|'conflict'
type DocNotice = {kind:'recovery', docId, summary} | {kind:'schema_too_new', docId, schema}
useDocumentStore: { saveState, saveIssue, docNotice, ... }

saveNow(): Promise<SaveState>          // 真实保存，等到磁盘写完
hasUnsavedWork(state): boolean         // 关闭保护、切文档提示都问它
reloadFromDisk(): Promise<boolean>     // 加载前把内存版本挪进恢复槽位
overwriteDisk(): Promise<SaveState>    // 拿 409 回的 hash 当基线，不是清空基线
recoverLocalCopy() / discardLocalCopy() / dismissDocNotice()
readAutosaveDoc(id): Promise<{ doc, notice }>   // ← 返回结构变了

// web/src/lib/versionTarget.ts
resolveRestoreTarget(meta, {activeCanvasId, canvases}): RestoreTarget

// web/src/lib/session.ts
apiUrlFor(path, pj) / withProjectFor(init, pj)   // 排队稍后才发出的写入用这两个
```

后端：

```python
# src/tavotto/app.py
REVISION_ABSENT = "absent"
_revision_conflict(base_revision, current) -> bool   # 两侧故意不对称
document_summary(path) -> dict | None                # 读不出来回 None，不回空壳
GET /api/autosave/<id>/summary                       # 冲突面板用
```

新增错误 code：**`external_change`**（已登记进 `USER_VISIBLE_CODES` + 双语文案）。

### 迁移

**没有数据迁移，磁盘格式一个字节没动。** 新增的只有 localStorage 里的
`tavotto.recovery.<id>` 键（旧版本没有它，缺席 = 没有待恢复副本）。
旧前端不发 `base_revision`，后端照常走 `base` 那条判据。

### 修改的文件

```text
新增  web/src/components/DocumentBanner.tsx     冲突/恢复/只读的常驻提示条
新增  web/src/lib/versionTarget.ts              恢复落点判据（+ 用例）
新增  web/src/store/saveStateMachine.test.ts    22 条
新增  web/src/hooks/useVersionCheckpoints.test.ts
新增  docs/adr/0024-save-lifecycle-and-external-change.md
改动  web/src/store/documentStore.ts            状态机 + 恢复 + 冲突（大头）
改动  web/src/lib/api.ts / session.ts           base_revision、显式 pj
改动  web/src/components/{TopBar,VersionDialog,CommandPalette,ShortcutHelp,App}.tsx
改动  web/src/hooks/{useKeyboard,useVersionCheckpoints}.ts
改动  web/src/store/actions.ts                  runManualSave + openRecentDocument
改动  web/src/types/document.ts                 SCHEMA_CURRENT（严格同源对）
改动  src/tavotto/app.py                        外部修改检测、摘要端点、画布身份
改动  tests/test_document_persistence.py        +11
改动  AGENTS.md                                 登记 schema 同源对
重建  codex-plugin/mcp/widget/canvas.html       改了 web/src 就要重建
```

### 测试命令与真实结果

```sh
# 后端全量（worktree 里必须带 PYTHONPATH，否则 import 到主工作区）
PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest
# 前端
cd web && pnpm test && pnpm build && pnpm i18n:check && pnpm lint
# 格式（与 CI 那一格逐字相同）
ruff check . && ruff format --check .
# 改了 web/src 之后
python scripts/build_mcp_widget.py
```

结果见 `STATUS.md` 的「Session 03 之后」表：后端 3053 passed、前端 1370 passed，
六条命令全部 exit 0。**24 条变异逐一反证，全部被打红**（记录见 `TEST_MATRIX.md`）。

### 这一轮变异反证抓到的两件事（值得下一个 Session 记住）

1. **变异跑之前先提交。** 脚本用 `git checkout -- <file>` 还原，会吃掉中途按
   变异结论改好的修复。本轮 `rememberRevision` 的修复就这么被还原掉一次，
   靠「锚点找不到」才发现。
2. **变异证伪过一次实现，不只是判据。** 「读到了但拿不到修订号」既不能当成
   「磁盘上没有」（后端 409），也不能当成「没确认过」（每次都探、每次判冲突），
   两条捷径都把文档锁成永远存不上。`diskRevision` 因此有第三档 `null`。

### 尚存限制

1. **「编辑历史」入口还在文档菜单里**，不是 Prompt 03 §六 要的左上区域独立入口。
   左栏形态由 Prompt 08 定，同一块区域不该两个 Session 各摆一次。
   抽屉本身已经区分 undo 栈 / 检查点 / 恢复，内容层面达标。
2. **autosave 仍在数据目录**（R-07）：搬进项目会改变「项目拷到另一台电脑带不带
   工作副本」的语义，单独处置。
3. **`/api/layouts/<name>` 仍不做 schema 校验**（ADR 0023 §5a），前置是修 R-18。
4. **没有 index.json**：`/api/layouts` 仍靠 glob 现算。
5. `engine/` 里另外五处手写原子写仍未并入 `atomicio`（R-05）。

### 工作树状态

- worktree：`/Volumes/Projects/Tavotto/.claude/worktrees/product-ux-v2`
- 分支：`feat/product-ux-reliability-v2`，从 `origin/main` 的 `ef9ac02` 开出
- 四个独立 commit（01 交接文档 / 02 落盘权威 / 03 保存状态机 / 03 的修订号第三档），
  **未 push、未开 PR** —— 攒够几个 Session 再一次发出去
- author 用 `88193520+erwanjun@users.noreply.github.com`（与 `main` 上每一个提交
  一致）。本机 `~/.gitconfig` 是 `1259959884@qq.com`，两者不一致会让 cla-check
  在同一个仓库里数出两个贡献者；提交时用 `git -c user.email=… commit`，
  **别改共享的 `.git/config`**
- `web/node_modules` 已在 worktree 内真装

---

## 下一阶段入口（Prompt 04：后端统一 refresh）

**从这里开始读**：`src/tavotto/app.py` 的 `/api/panels` 与 `sse_publish`
（`ARCHITECTURE.md` §3 记了现状链路），以及 `web/src/store/assetStore.ts`。

**Session 03 留给它的接口**：派生元数据的同步**绝不能污染 dirty / 历史**。
文档里已经有现成的两个出口，别造第三个：

- `documentStore.silent(recipe)` —— 不进历史、不置 dirty 的写入（渲染反推的
  派生值走这条）；
- `applyProject(pd, id, {dirty})` —— 「内容换了但会话没换」的装载，会 bump
  `loadSeq`，因此 `startAutosave` 的订阅**不会**把它当成一次编辑。

**必须保留的不变式**（改动前先确认还成立）：

1. `loadSeq` 把「载入」与「编辑」分开——载入不得触发回写。
2. `dirty` 同时盯 `doc` 与 `canvases`。
3. 收到 409 后**基线故意不推进**，本机兜底副本**不清**。
4. `HistoryEntry.label` 存 `UiMessage` 描述符，绝不存翻译后的字符串。
5. 落盘一律走 `engine/atomicio`（ADR 0023），不再手写 tmp+replace。
6. 保存状态只经 `setSaveState()` / `setDocNotice()` 改（ADR 0024）。
7. 排队稍后才发出的写入必须带走**排队那一刻**的 `pj`。

**别做的事**：不要为了 refresh 重写 `documentStore` 的事务/撤销；
不要引入第二套持久化路径；watcher 不得把 Tavotto 自己的 autosave 写入当成
素材变化（那会让每一次自动保存都触发一轮刷新）。
