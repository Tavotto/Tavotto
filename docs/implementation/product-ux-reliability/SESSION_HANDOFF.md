# SESSION_HANDOFF — 跨 Session 交接

> **整段重写，不加行。** 半新半旧的状态块会继承「刚被动过」的可信度，
> 而下一个 Session 没有办法分辨哪几行是这次更新的。

---

## 最近一次：Session 01 + 02（2026-08-29）

### 目标

01 = 全仓基线审计 + 产品合同 + 交接骨架；02 = 文档 schema / 稳定 ID /
迁移 / **原子写入基础设施**。

### 实际完成

**01（全部完成）**

- 建立 `docs/implementation/product-ux-reliability/`：`00_SHARED_RULES.md`
  （从 Prompt 套件复制）、`STATUS.md`、`ARCHITECTURE.md`、`DECISIONS.md`、
  `TEST_MATRIX.md`、`UX_CONTRACTS.md`、本文件。
- 跑通四条真实基线（结果见 `STATUS.md`），**零失败**。
- 18 条风险登记到 `STATUS.md` 并逐条分配到阶段，其中 5 条是**实测**出来的
  （标 ✔），不是读代码推断的。

**02（完成，落成 ADR 0023）**

- 新增 `src/tavotto/engine/atomicio.py` —— Flask 父进程一侧文档类写入的
  **唯一实现**：tmp → flush → fsync 文件 → `os.replace` → fsync 目录 →
  失败清 tmp + 结构化错误；`dumps_json` 用 `allow_nan=False` 在序列化那一步
  挡下 NaN/∞；`content_revision()` 给出内容修订号。
- 新增 `src/tavotto/engine/documents.py` —— 文档格式判据的唯一实现：
  schema 版本、骨架校验、收纳目录里谁不是用户文档（**枚举**，不是 `_` 前缀）。
- `app.py`：四处手写 tmp+replace 并入 `atomicio`；
  **`POST /api/layouts/<name>` 从非原子的 `write_text` 改成原子写**（R-01）；
  autosave / versions 的 schema 校验并入 `documents.validate_document`；
  `/api/layouts` 列表与 `layout_path` 挡住 Tavotto 自己的文件（R-04）；
  新增两个 errorhandler 把 `DocumentError` / `AtomicWriteError` 映射成
  带 `code` 的 400 / 409 / 500。
- 新增 `tests/test_document_persistence.py`（18 条），**十条变异逐一反证**
  （记录见 `TEST_MATRIX.md` 末尾）——其中一条第一版是空判据，已修。

### 关键 API / 类型 / 格式（Prompt 03 可以直接调用）

```python
# src/tavotto/engine/atomicio.py
write_json(path, obj, *, indent=None) -> None      # 原子写；失败抛 AtomicWriteError
write_bytes(path, data) -> None
dumps_json(obj, *, indent=None) -> bytes           # allow_nan=False
content_revision(path) -> str | None               # 内容 hash（不掺 mtime）
class AtomicWriteError(OSError):  code / message / path / as_payload()

# src/tavotto/engine/documents.py
SCHEMA_FIGURE = 2; SCHEMA_PROJECT = 3; SCHEMA_CURRENT = 3
SUPPORTED_SCHEMAS = (2, 3)
validate_document(raw) -> dict                     # 失败抛 DocumentError
is_user_document_stem(stem) -> bool
require_user_document_stem(stem) -> None           # 撞上保留名 → DocumentError(409)
STYLES_FILENAME / AUTOSAVE_DIRNAME / VERSIONS_DIRNAME
class DocumentError(ValueError): code / message / status / as_payload()
```

HTTP 层的新增契约：

| 端点 | 变化 |
| --- | --- |
| `PUT /api/autosave/<id>` | 响应多一个 `revision`（内容 hash）；`schema_too_new` 是新 code |
| `GET /api/autosave/<id>` | 响应头多一个 `X-Tavotto-Revision` |
| `POST /api/layouts/<name>` | 原子写；保留名回 409 `reserved_name`；非有限数回 400 `non_finite_number` |
| `GET /api/layouts` | 不再列出 `_styles` |

**错误 code**（一旦发布不能改名）：`invalid_document`、`schema_too_new`、
`reserved_name`、`non_finite_number`、`mkdir_failed`、`write_failed`、`replace_failed`。

### 迁移

**没有数据迁移。** 磁盘格式一个字节没动：schema 2/3 的语义、
`_autosave/` `_versions/` `_styles.json` 的位置、`tavottofile/` 的布局全部照旧。
改的只是**写入方式**与**校验时机**。旧文档、旧位置、旧扁平布局照常打开。

唯一的行为变化是三条：`_styles` 不再出现在画布列表里（它本来就不该在）、
非有限数的载荷现在会被拒绝（此前会写出一份没人能读的文件）、
更高 schema 的文档回 `schema_too_new` 而不是笼统的 `invalid_document`。

### 修改的文件

```text
新增  src/tavotto/engine/atomicio.py
新增  src/tavotto/engine/documents.py
新增  tests/test_document_persistence.py
新增  docs/adr/0023-document-persistence-authority.md
新增  docs/implementation/product-ux-reliability/{00_SHARED_RULES,STATUS,ARCHITECTURE,
      DECISIONS,TEST_MATRIX,UX_CONTRACTS,SESSION_HANDOFF}.md
改动  src/tavotto/app.py（导入、两个 errorhandler、五处写入、两处校验、两处列表/路径守卫）
```

**前端一行没改。** Prompt 02 §九 的「前端模型接线」在起始仓库已经具备
（`ProjectDocument` 类型 + `documentStore` 的持久化边界 + `migrateToProject`）；
`revision` 的前端消费属于 Prompt 03 的冲突检测，不在这里提前接。

### 测试命令与真实结果

```sh
# 后端全量（worktree 里必须带 PYTHONPATH，否则 import 到主工作区）
PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest
# 前端
cd web && pnpm test && pnpm build && pnpm i18n:check
# 格式（与 CI 那一格逐字相同）
ruff check . && ruff format --check .
```

结果见 `STATUS.md` 的基线表与本次结论段。

### 尚存限制

1. **`/api/layouts/<name>` 的载荷仍不做 schema 校验**（ADR 0023 §5a）：
   已有调用方发的是 `{"doc": …}` 包一层的形状。收紧的前置是修 R-18。
2. **`engine/` 里另外五处手写原子写没并**（config / runspec / runtimeasset /
   locate / session_client / nativehandoff）：它们写的不是文档，生命周期各不相同，
   合并要逐个看过，不该在这个改动里顺手做。
3. **没有 index.json**：Prompt 02 §八 设想的项目文档 index 现状不存在，
   `/api/layouts` 靠三个目录的 `glob` 现算。要不要引入 index 是 Prompt 03 的
   决定（引入就多一份可能与磁盘不一致的真相，得先说清它坏了怎么重建）。
4. **autosave 仍在数据目录**（`LAYOUT_DIR/_autosave`）而不是项目内（R-07）：
   搬家会改变"项目拷到另一台电脑带不带工作副本"的语义，属于 Prompt 03。

### 工作树状态

- worktree：`/Volumes/Projects/Tavotto/.claude/worktrees/product-ux-v2`
- 分支：`feat/product-ux-reliability-v2`，从 `origin/main` 的 `ef9ac02` 开出
- 两个独立 commit（01 = 交接文档，02 = 落盘权威 + ADR 0023 + 用例），
  **未 push、未开 PR** —— 攒够几个 Session 再一次发出去
- author 用 `88193520+erwanjun@users.noreply.github.com`（与 `main` 上每一个
  提交一致）。本机 `~/.gitconfig` 现在是 `1259959884@qq.com`，两者不一致会让
  cla-check 在同一个仓库里数出两个贡献者；提交时用
  `git -c user.email=… commit`，**别改共享的 `.git/config`**（不带
  `--worktree` 的 `git config` 会影响所有 worktree 和所有会话）
- `web/node_modules` 已在 worktree 内真装（软链过不了 pnpm 的依赖检查）

---

## 下一阶段入口（Prompt 03：保存状态机、autosave、恢复、历史）

**从这里开始读**：`web/src/store/documentStore.ts` 的
`flushAutosave` / `scheduleDiskWrite` / `readAutosaveDoc` / `restoreSession`
（第 700–960 行），以及 `app.py` 的 `api_autosave_*` / `api_versions_*`。

**必须保留的不变式**（改动前先确认还成立）：

1. `loadSeq` 把「载入」与「编辑」分开——载入不得触发回写
   （否则刚打开一份文档就会带着新的 `updatedAt` 去和别的标签页抢）。
2. `dirty` 同时盯 `doc` 与 `canvases`——只改非激活画布的结构性操作也算编辑。
3. 收到 409 `stale_write` 后**基线故意不推进**，本机兜底副本**不清**。
4. `HistoryEntry.label` 存 `UiMessage` 描述符，绝不存翻译后的字符串。
5. 派生数据的写入只走 `documentStore.silent()`，不进历史、不置 dirty。
6. 落盘一律走 `engine/atomicio`（ADR 0023），不再手写 tmp+replace。

**Prompt 03 该优先处理的三条风险**（都在 `STATUS.md`）：

- **R-03（P1）版本检查点没有画布身份**——检查点存的是激活画布，却按项目归档；
  在画布 B 上产生的检查点，在画布 A 上恢复会把 B 的内容和名字盖到 A 上。
- **R-06（P1）没有显式保存状态机**——`saving`/`save_error`/`conflict` 都不是
  文档状态，错误只是一个 `window` 事件，刷新即丢。
- **R-08（P1）没有外部修改冲突检测**——`revision` 已经在 02 备好了，
  前端拿它当基线即可。

**别做的事**：不要为了状态机重写 `documentStore` 的事务/撤销；
不要引入第二套持久化路径；不要把 autosave 变成"最后一个可靠检查点"的替代。
