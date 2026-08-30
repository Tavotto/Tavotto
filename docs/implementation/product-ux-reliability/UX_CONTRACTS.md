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

### 1a. 派生元数据同步（Session 06）

`doc` / `canvases` 的变化有**三种性质**，自动保存的订阅按两个代次
（`loadSeq` / `derivedSeq`）区分：

| 性质 | `dirty` | `saveState` | 撤销历史 | 落盘 |
| --- | --- | --- | --- | --- |
| 载入 | 由载入方声明 | 由载入方声明 | 清空 | 不排队 |
| 用户编辑 | 置位 | 推成 `dirty` | 进 | 排队 |
| **派生同步** | 置位 | **不动** | **不进** | **排队** |

唯一入口 `documentStore.applyDerivedUpdate()`，唯一调用方
`store/panelSourceSync.ts`。理由见 `DECISIONS.md` 的 T-26。

**可同步的字段**（磁盘 / registry 说了算）：`script`、`cost`、`fileKind`、`pxW`。

**绝不修改**：`x` / `y` / `w` / `h`、`nativeW` / `nativeH`、`crop`、`rotation`、
`overrides`、`annotations`、`groupId`、布局组、`locked`、`hidden`、`name`、
`opacity`、`flipH` / `flipV`、`lockedGids`、画布选择、当前文档名。
图幅为什么也在这一列：T-27。

**验收**：

- 外部编辑器给一张已在画布上的图补上脚本 → 面板**原地**可编辑，几何、裁剪、
  override、成组、选择、撤销栈一个字节不动，不需要删了重加；
- 脚本失效 → 面板原地降级、退出图内编辑并**保留画布选择**、清掉失效的
  manifest / 渲染缓存、`overrides` 一条不删，提示说清"图片和排版没有被删除"；
- 升级**不**自动把用户拽进图内编辑（下次双击才进）；
- 素材暂时不在清单里 ≠ 脚本关系失效：对象原样保留，走既有的缺失素材语义
  （T-28）；
- 无差异 = 零改动（不置 dirty、不排落盘、不弹提示）。

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

- **快速编辑不得要求用户先配置画布**。✅ **已落地（Session 09）**：素材卡双击 /
  Enter、`tavotto open <stem>` 的交接、运行时图卡片，落点都是快速编辑工作区
  （`store/workspace.openFastEdit()`）。用户不设毫米尺寸、不拖对象到版面、
  不选栏宽、不理解多面板布局。**不新增强制模式选择启动页。**
- 两条工作流共享：对象模型、属性编辑、撤销栈、样式、检查、渲染与导出底层。
  **不得出现第二套编辑器或第二份文档模型。** 落地形态（T-43）：一张图在文档里
  只有**一个**面板对象，快速编辑是它的另一种看法，画布排版是它的落位。
  因此「添加到画布」没有复制这一步，`overrides` 全程在同一个对象 id 上。
- **快速编辑一个字都不写 x/y/w/h。**「从画布进图内编辑再返回，布局不变」靠的是
  没动过，不是"回来时恢复一下"。
- **模式是工作区状态**：`workspaceMode` / `activePanelId` 不进文档、不进撤销、
  不置 dirty（数据所有权见 §3）；按 documentId 存本机一档，恢复前先验那个对象
  还在不在。source / Readiness 变化**不强制切换模式**。
- **layout-only 素材可以排版，但不伪装完整图内编辑**：进得去工作区，不进图内
  编辑态，说明条给出原因（取自 `readinessText`）与「连接源脚本」。
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

## 3b. 项目刷新合同（Session 04，ADR 0025）

**「项目里的东西变了」在后端只有一条链路。**

```text
app.refresh_project(ctx, reason=…) → engine/project_refresh.refresh_project_index()
```

**验收**（全部有用例，见 `TEST_MATRIX.md`）：

- **绝不执行用户脚本**：刷新只读 AST 与 `stat()`。判据是双份的——桩住 probe /
  worker 池的入口，**外加**脚本真跑起来会在图库里留下的那个文件不存在；
- **无差异 = 零事件**：一次什么都没发现的刷新不发事件、不重写注册表、
  不作废任何 worker；
- **批量是一条事件不是十几条**：一次发现四个新脚本 → 一条 `registry.changed`；
  恰好一个脚本变时照旧带 `{script, stems}`（老客户端）；
- **项目隔离**：事件带 `pj`；作废 worker 限本项目（两个项目里同名的
  `fig1.py`，刷新 A 不动 B）；
- **并发**：同一项目的刷新串行，不同项目可并行（锁是每项目一把）；
- **失败不伤现状**：注册表读不回来时内存里那份原封不动，项目照常能用，
  事件一条不发，错误带稳定 `code`；
- **派生刷新不碰文档**：不设 dirty、不进撤销历史、不读写 autosave / 版本目录
  （这条在 04 是**结构性**的：服务模块不 import `engine/documents`）。

**不变式 C**：`reason` 是闭集（`manual` / `watcher` / `registry` / `probe` /
`codex` / `ai` / `open` / `external`），表外的值一律归成 `manual`。它进日志、
进事件、以后还会进遥测维度——透传客户端字符串等于让外面往指标里写自由文本。

**不变式 D**：「哪些文件算素材」只有 `project_refresh.iter_assets()` 一处判据，
`/api/panels` 与刷新共用。两份判据分叉的表现是"刷新说有一张新图、素材库里
找不到"。

---

## 3c. 自动发现合同（Session 05，ADR 0026）

**用户不需要点刷新。** 在编辑器里做的事，Tavotto 自己会看见：

| 用户做的事 | 会发生什么 |
| --- | --- |
| 改一个已登记脚本 | 它的渲染会话作废 + `panel.file_changed`（这张图会重画） |
| 新建一个 `.py` | 一次静态刷新；能静态认出来的自动进注册表，认不出来的等用户显式试运行 |
| 删掉一个脚本 | 渲染会话作废；**图片不删、文档不删**；面板由前端后续降级为"源文件不见了"（Prompt 07/08） |
| 重命名 / 原子替换（编辑器的标准保存法） | 与上面两条同等对待，且合成**一批** |
| 改 `paper_style*` | 本项目**全部**渲染会话作废（别的项目一个不动） |
| 在编辑器里改 / 删 `tavotto_registry.json`（或旧名） | 重新装载并校验；非法 JSON 时**保留上一次有效的那份**，发一条可操作的错误，修好后自动恢复 |
| 往图库里放 / 改 / 删 / 改名一张 PDF·PNG·JPG | 一次刷新 + `assets.changed`；**不作废任何不相干的渲染会话** |

**验收**（全部有用例）：

- **一次保存最多一次刷新**：连续写入按 300–800 ms 防抖合并；目录永远不安静时
  有批次年龄上限，刷新不会被无限期推迟；
- **刷新执行期间到达的写入不丢**，进下一批（快照在结算之前就换掉了）；
- **不循环**：刷新自己写的注册表认得出来（内容修订号，不是时间窗口），
  而外部**紧接着**再改仍然触发；摘掉的只是注册表那几个路径，不是整批；
- **绝不执行用户脚本、绝不自动 probe**（同 3b 的双份判据）；
- **项目隔离**：每项目一个 watcher；同一路径重复挂会停掉旧的（不留第二个
  线程）；关项目停它自己那一个，且不再发事件；
- **目录暂时读不到时什么都不做**——「看不见」不是「不存在」，把一次网盘抖动
  当成"用户删光了"会打掉整个项目的渲染会话；
- **空闲不烧 CPU**：轮询之间有 `wait(interval)` 挡着，且不读任何文件内容。

**不变式 E**：`registry.changed` / `assets.changed` **只由统一刷新发**。
watcher 自己发的只有 `panel.file_changed`（"这张图的源码变了"）与
`project.error`（后台刷新失败，可恢复）。两处各发一份的话，前端会收到两条
互相矛盾的 diff。

**不变式 F**：watcher 的遍历规则不新写第三份——脚本用
`discover.iter_all_scripts()`，素材用 `project_refresh.iter_assets()`。
盯得比 discover 宽 = 为一个永远进不了注册表的文件反复刷新；窄 = 用户新建的
脚本发现不了。

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
| 原图导出 | 不套用任何画布缩放；尺寸来自 **`OriginalOutputSpec`**（ADR 0028，唯一服务 `web/src/lib/originalSpec.ts`） |
| 画布导出 | 忠实于画布（mm、栏宽、页边距） |
| 预览 / PDF / PNG | 尽量同一语义渲染源；不允许"预览正常但导出缺字/方框/错位" |
| 失败 | 不留半文件；覆盖已有文件必须明确 |
| 降级 | 格式不支持某能力时清楚降级，**不得伪称矢量**（现状已有先例：`opacity<1`、翻转面板按 DPI 位图嵌入，见 `types/document.ts` 注释） |
| 写回 | 热态所见 == 写进文件的 == 重开后重放出来的（根 `AGENTS.md` 的写回事务不变式） |

---

## 6b. 原图规格合同（Session 09，ADR 0028）

**「按原图导出」是一句有定义的话，定义在一处。**

来源优先级（顺序是判据的一部分，不许导出时临时挑）：

| # | 来源 | `origin` |
| - | --- | --- |
| 1 | 这一变体渲染回来的 manifest `size_mm`（可编辑 Figure 的真实图幅） | `render_metadata` |
| 2 | 文档里那个面板的 `nativeW/nativeH`（上一次同步到的图幅，**源文件没了它还在**） | `document` |
| 3 | `/api/panels` 的 `original_spec`（矢量 page/viewBox；位图像素 + 可信 DPI） | `asset` |
| 4 | 明确 fallback（`FALLBACK_MM`），**必带 `fallback: true`，界面必须提示** | `fallback` |

**忽略**：面板在 layout 里的 x/y、w/h（画布缩放）、画布页面尺寸/背景/边距/
页面裁切，以及面板上的 crop / rotation / flip / opacity。被忽略的逐项列进
`spec.ignored`（固定顺序 `scale` / `crop` / `rotation` / `flip` / `opacity`）
——**忽略而不说等于骗人，说了而不忽略等于套用画布缩放**。

**保留**：图内 edits、文字、图例与样式修改。

**不做**：无意的上采样或下采样；位图源默认保持源像素网格；矢量源保持矢量
语义（除非用户选的格式就是 PNG）。**尤其不做**：不因为用户曾把面板缩小，
就把字号一起缩小。

**用户显式改了原 Figure 的尺寸**属于图内文档修改，经渲染 manifest 回到第 1 档，
spec 跟着变。

**「不知道」是独立一档**：`dpi_source` 四个取值 `metadata` / `assumed` /
`derived` / `unknown`；`dpi: null` 与 `dpi: 96` 是两个不同的答案。
`source_missing` 时保留上次已知的规格并标 `stale`，**不报"不知道"**。
文档与素材清单都不认识的 id 回 `null`——**不发明一张不存在的图**。

---

## 5b. 接入状态合同（Session 08）

**一张图能不能编辑，全产品只有一句话，主语固定为那张图。**

- 事实的唯一出处是后端 `engine/readiness.py`：六个 `status` + 十个
  `reason_code` 的闭集。界面**只翻译不判断**——不许按 `script` 有没有值再判
  一遍，也不许另起同义状态（要分得更细就回后端加 reason code）。
- 说的话按 **`reason_code`** 查，不按 `status`（`web/src/lib/readinessText.ts`
  一处实现）：同一个状态下不同 code 的下一步完全不同。
- **「没测量」不是「测量结果是零」**，三档一个都不许压扁：`conflicts` 的
  `null`、`project.registry_valid` 的 `null`、`PanelInfo.capability` 的
  `undefined`。第三档的界面表现是**什么都不说**。
- **`layout_only` 不是错误**：没有源脚本的图照旧能缩放、裁剪、对齐、标注和
  导出。界面必须说清它还能干什么，不许只说它不能干什么，不许用警告色。
- **绝不替用户决定**：冲突不自动挑一个（文件名更像"新版本"的那一个也不许
  赢）；试运行只有用户点了才跑，且点之前先说清"Tavotto 将运行这个脚本"。
- 摘要横幅**不阻塞画布、不自动弹对话框**；关闭按「项目 id + 报告 fingerprint」
  记，事实一变就再说一次（不是"永久别再提"）。
- 用户可见的部分不出现 registry / stem / entry / manifest / AST / probe；
  精确名词只出现在每一行的「技术详情」里（默认收起，给排障用）。

## 5c. 左侧工作区外壳合同（Session 08）

- 图标轨道**常驻**，抽屉默认展开；用户可折叠，偏好跨会话保存，
  **不进文档 undo**（它是 UI 会话状态，不是用户文档数据）。
- **响应式让位不是偏好**：互斥断点上的自动收起、窄屏开机的裁剪都只改
  「此刻开着没开」，绝不写回本机存的常驻偏好（T-40）。窗口拉回来、重启之后
  用户设的那一份必须原样生效。
- 展开 / 折叠之后画布视口必须重算，且 `zoom` / `panX` / `panY` **一位都不
  变**——对象在文档坐标里不许跳动。
- 轨道上有稳定的「素材」与「项目接入状态」入口；Prompt 11 的「问题」入口
  在 `LeftRail.ITEMS` 旁留了位置，**但在它真的有内容之前不渲染占位按钮**。

---

## 7. i18n / 无障碍 / 隐私（完成条件，不是加分项）

- 所有用户可见文案同时有自然中文与自然英文；门禁 `pnpm i18n:check`。
- 新增 UI：可读 `aria-label`、键盘可操作、保持 focus-visible、
  不新增 nested interactive、支持 `prefers-reduced-motion`、
  中英文 + 125%/150% 缩放不溢出、颜色不是唯一状态表达。
- 遥测与核心功能完全解耦；文件名 / 路径 / 脚本名 / stem / 图内文字
  **在结构上就发不出去**（白名单 + `tests/test_telemetry_invariants.py`）。
