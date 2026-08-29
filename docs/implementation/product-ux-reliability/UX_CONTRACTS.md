# UX_CONTRACTS — 可直接验收的产品合同

> 合同 = 「怎样算做到了」。每条都要能被一条测试或一次人工验收判定真假。
> 现状（"今天是什么样"）在 `ARCHITECTURE.md`，差距在 `STATUS.md` 的风险表。

---

## 0. 五条长期合同（冲突时按此排序）

1. 用户的修改绝不能静默丢失。
2. 单图快速编辑与多图画布排版共享同一文档模型，但不互相强迫。
3. 样式、出版规范、检查规则、导出设置分层，每层只有一个事实来源。
4. 任何问题提示都必须能回到真实对象和真实字段。
5. 预览、PDF、PNG 与项目恢复保持结果一致。

---

## 1. 文档状态合同

**已落地（Session 03，ADR 0024）。**

```text
clean ──编辑──▶ dirty ──flush──▶ saving ──成功且期间没再编辑──▶ saved ──▶ clean
                  ▲                 │                └──期间又编辑了──▶ dirty
                  │                 ├──写盘失败──▶ save_error ──重试──▶ saving
                  └────编辑─────────┴──409────────▶ conflict ──裁决──▶ dirty/clean
```

另有一根**正交**的轴 `docNotice`（同一时刻至多一件，与保存进度并存）：

| 值 | 含义 | 出口 |
| --- | --- | --- |
| `recovery` | 本机还留着一份没人裁决的副本 | 恢复 / 保留主版本 |
| `schema_too_new` | 那份文档来自更新的 Tavotto，**没有打开** | 知道了 |

为什么不合成一个枚举：见 `DECISIONS.md` 的 T-10。

**验收**（全部有用例，见 `TEST_MATRIX.md`）：

- 状态是 store 里可读的单一字段 `saveState`，不是散落的事件；
- `save_error` / `conflict` 不会被"下一次成功保存"以外的任何东西清掉
  （冲突期间继续编辑不顶掉冲突态）；
- 保存期间继续编辑，写成功后**仍是 `dirty`**，绝不显示成已保存；
- `⌘S` 真的保存并等到磁盘写完；`⇧⌘S` 才是另存为画布文件；
- `beforeunload` 只在 `dirty` / `saving` / `save_error` / `conflict` 时拦；
- 冲突时磁盘一个字节不被覆盖，三个出口都在（重新加载 / 明确覆盖 / 另存为），
  且"重新加载"之前当前内存版本已经变成可恢复副本；
- 恢复动作只进内存并置 `dirty`（`saveState` 与 store 的 `dirty` 两根轴都置），
  **用户确认保存后才覆盖主文档**。

**不变式**：`dirty` 必须覆盖**所有**用户修改，包括只改非激活画布的结构性操作
（重命名 / 删除 / 复制 / 重排画布）——这条现状已经守住了
（`startAutosave` 同时盯 `doc` 与 `canvases`），后续改动不得回退。

---

## 1b. 外部修改合同（Session 03）

**Tavotto 绝不静默覆盖磁盘上它没读过的内容。**

- 写入基线是**内容 hash**（`base_revision`），不是文档自报的 `updatedAt`：
  编辑器外的工具改完往往一个字节的 `updatedAt` 都不动。
- 「我以为磁盘上没有这份文件」也是一个基线（哨兵 `absent`），因此
  两个标签页同时新建同一份文档不会互相盖掉。
- 本会话没确认过磁盘状况时，**写之前先确认一次**，没有例外。
- 反方向不过严：hash 基线遇上文件被外部删掉照常重建——挡的是"覆盖别人的
  内容"，不是"重建一个被删掉的文件"。

**验收**：外部改动后的整份 PUT 回 409 `external_change`，磁盘内容逐字节不变，
错误体带结构化摘要（schema / 画布数 / 对象数 / `updatedAt` 与 `mtime` 两个
时间维度 / 当下的修订号）。

---

## 1c. 版本检查点合同（Session 03）

**检查点恢复到它自己那张画布，不是"恰好激活的那张"。**

| 落点 | 行为 |
| --- | --- |
| 同一张画布 | 直接恢复，不打扰 |
| 另一张仍存在的画布 | 说清会切过去写，当前画布不动 |
| 原画布已删除 | 说清会覆盖当前画布（danger 确认） |
| 旧检查点无画布身份 | 同上；**不补默认身份** |

**验收**：检查点带 `canvasId`/`canvasName`；恢复前的自动存档拍的是**即将被
覆盖的那张**；自动检查点去重按画布分（复制画布后两张内容相同，第二张仍能
留下检查点）。

---

## 2. 两种工作流合同

```text
Fast edit: 打开一张图 → 修改 → 按原图规格导出
Layout   : 加入多张图 → 排列   → 按画布规格导出
```

- **快速编辑不得要求用户先配置画布**。现状不满足：图内编辑必须先把面板放进画布
  （见 `ARCHITECTURE.md` §5.1）。归属 Prompt 09。
- 两条工作流共享：对象模型、属性编辑、撤销栈、样式、检查、渲染与导出底层。
  **不得出现第二套编辑器或第二份文档模型。**
- 原图导出**不得**套用画布缩放；画布导出必须忠实于画布。

---

## 3. 数据所有权合同

| 类别 | 归属 | 进 undo？ | 进 dirty？ | 进 autosave？ | 现状位置 |
| --- | --- | --- | --- | --- | --- |
| 对象位置/尺寸/裁剪/旋转/层级/锁定/隐藏 | 用户文档 | ✅ | ✅ | ✅ | `CanvasObject`（`types/document.ts`） |
| 图内 override / 标注 / 文字 | 用户文档 | ✅ | ✅ | ✅ | `PanelObject.overrides`、`TextObject` |
| 画布尺寸 / 背景 / 参考线 / 布局组 | 用户文档 | ✅ | ✅ | ✅ | `PageSetup`、`Guide`、`LayoutGroup` |
| 规范绑定（profile id + 期刊覆盖） | 用户文档 | ✅ | ✅ | ✅ | `CanvasData.profile` |
| 画布列表 / 顺序 / 激活画布 | 用户文档 | 部分（激活画布走 commit） | ✅ | ✅ | `ProjectDocument.canvases` |
| 打开的标签页 | UI 会话状态 | ❌ | ❌ | ❌（按机器存 localStorage） | `openTabs` + `tavotto.tabs.<id>` |
| 面板 fingerprint / registry 映射 / stem | 项目派生 | ❌ | ❌ | ❌ | `engine/registry.py` |
| worker id / 临时 URL / blob / 缓存路径 | 渲染缓存 | ❌ | ❌ | ❌ | `store/renderStore.ts` |
| SSE 连接状态 / toast / hover / 临时选择 | UI 会话状态 | ❌ | ❌ | ❌ | `uiStore` / `selectionStore` / `interactionStore` |
| 翻译后的文案 | **绝不持久化** | ❌ | ❌ | ❌ | 存 `UiMessage` 描述符 |
| autosave / 版本检查点 | 恢复数据 | ❌ | ❌ | — | `_autosave/`、`tavottofile/versions/` |

**不变式 A**：派生数据刷新**不得**把文档标脏，也不得进普通撤销历史。
现状的出口是 `documentStore.silent(recipe)`；新增派生写入一律走它。

**不变式 B**：翻译后的字符串不进任何长期存储（文档、历史、版本）。
存 message key + 结构化参数（`UiMessage`）。

---

## 4. Style / Spec / Validation / Export 分层合同

```text
Style      —— 图长什么样        （字体/字号/线宽/刻度/图例/页面预设）
Spec       —— 图要满足什么要求  （出版规范：栏宽、比例、字号下限、字族）
Validation —— 只读 Spec 求值    （不在任何页面硬编码阈值）
Export     —— 文件怎么生成      （格式、DPI、透明、目标路径）
```

- Spec 的唯一权威是 `src/tavotto/profiles/publication.json`；
  两个求值器（`engine/preflight.py` / `web/src/lib/preflight.ts`）
  **共读它**，靠 `tests/golden/preflight_vectors.json` 对齐。
  改判据先改 profile，再让两侧同时绿。
- 文档里只存 `profile.id` 与期刊覆盖，**规则本身一条都不冻进文档**——
  规范升级后旧文档自动跟新规则走。
- **当前混乱点**（归属 Prompt 10）：
  1. Style 预设存在数据目录 `_styles.json`，与项目无关；项目级快照/绑定字段
     在文档 schema 里**尚未预留**。
  2. 导出偏好存 localStorage（`lib/exportDefaults.ts`），换机器即丢，
     也不进文档。
  3. 最小字号有两个数：`absolute_min_font_size_pt: 8.0` 与
     `legend_policy.min_font_size_pt: 8.5`。合同要求默认只保留 8 pt。

---

## 5. 问题定位合同

```text
issue → document/mode → object → viewport → selection → inspector field
```

- 每个问题项必须持有**稳定对象引用**，不是数组下标、不是文件名、
  不是 matplotlib 临时内部 id。
- 现状：`PreflightIssue { id, severity, message: UiMessage, objectIds: string[],
  gids: string[], detail }`（`web/src/lib/preflight.ts`）——
  已经是「对象 id + 图内元素 gid」的稳定引用，`id` 是机器可读的稳定判据身份，
  措辞变化不影响 golden vectors。**这就是 ObjectRef 的现有等价物**，
  后续不另造第二套类型。
- 缺口（归属 Prompt 11）：问题项没有 `documentId` / `canvasId` 维度，
  多画布项目里无法跳到"另一张画布上的那个对象"。

---

## 6. 输出一致性合同

| 判据 | 要求 |
| --- | --- |
| 原图导出 | 不套用任何画布缩放；尺寸来自面板原生尺寸（`nativeW/nativeH`，mm 口径） |
| 画布导出 | 忠实于画布（mm、栏宽、页边距） |
| 预览 / PDF / PNG | 尽量同一语义渲染源；不允许"预览正常但导出缺字/方框/错位" |
| 失败 | 不留半文件；覆盖已有文件必须明确 |
| 降级 | 格式不支持某能力时清楚降级，**不得伪称矢量**（现状已有先例：`opacity<1`、翻转面板按 DPI 位图嵌入，见 `types/document.ts` 注释） |
| 写回 | 热态所见 == 写进文件的 == 重开后重放出来的（根 `AGENTS.md` 的写回事务不变式） |

---

## 7. i18n / 无障碍 / 隐私（完成条件，不是加分项）

- 所有用户可见文案同时有自然中文与自然英文；门禁 `pnpm i18n:check`。
- 新增 UI：可读 `aria-label`、键盘可操作、保持 focus-visible、
  不新增 nested interactive、支持 `prefers-reduced-motion`、
  中英文 + 125%/150% 缩放不溢出、颜色不是唯一状态表达。
- 遥测与核心功能完全解耦；文件名 / 路径 / 脚本名 / stem / 图内文字
  **在结构上就发不出去**（白名单 + `tests/test_telemetry_invariants.py`）。
