# ADR 0093：方向键微调——图内元素与拖动同一套移动规则，一段连续按键一条撤销

日期：2026-09-26 · 状态：**Accepted**（实现随本 PR）
相关：[0017 显示回退 ≠ 几何权威](0017-display-fallback-vs-geometry-authority.md)（权威缺席时不许做几何写操作）、
[0082 属性页按页面上的实际大小显示](0082-inspector-shows-page-pt.md)（界面上的量按页面值说话）、
`docs/rules/frontend/hit-and-selection-geometry.md`「方向键微调」、`docs/rules/frontend/fake-realtime-preview.md`

## 问题

用户：「设计一下微调功能，按上下左右键可以微调位置」——方向键微调其实早就有（`nudgeSelected`），
但用户感觉不到它。2026-09-26 在改动前的 main 上用真浏览器实测（Fig1_kinetics，chromium）：

| 状态 | 选中 | 按方向键的结果 |
| --- | --- | --- |
| 画布排版 | 面板 | 面板挪 0.5 mm / ⇧ 5 mm；**每按一下一条撤销**（4 下要撤 4 次） |
| 画布排版，图内编辑态 | 图例 / 标题 / 子图 | **整张图在版上挪了，选中的图内元素纹丝不动**（图内编辑态下面板仍在画布选区里）；零渲染 |
| 快速编辑 | 图例 / 标题 / 子图 | 什么都不发生（键被吃掉，快速编辑里不推面板的 x/y） |
| 画布排版 | 面板，焦点在素材卡上 | 素材卡换了焦点，**画布上的面板也挪了**（控件 preventDefault 过的键照样冒到画布） |
| 画布排版 | 面板，焦点在属性页输入框 / 左栏按钮 | 输入框：归输入框；按钮：推面板 |

图内对象——用户真正想「挪一点点」的标题、图例、标注、文字、箭头——**从来不能用方向键挪**。

## 裁决

### 一、单一权威：图内平移只有一份 `InFigureMove`

拖动里「谁能动、带谁走、贴边怎么钳、拖回原处不写、一次提交写哪几条 override」原本散在四个
`start*Drag` 的闭包里（文字 / 图例 / 形状、独立箭头整体平移、子图平移、多选整组平移）。本 ADR
把它们抽成 `canvas/interactions.ts` 的 `InFigureMove`（`elementMove` / `arrowMove` / `axesMove` /
`groupMove`，单个元素按哪一种走只在 `inFigureMoveOf` 判）：起手时量好基准与随行集合，之后只接
「累计的内容分数位移」，`preview` 只动预览平面，`commit` 一次 setOverride(s)，`cancel` 还原。

两个输入端：

- 指针（`trackInFigureMove`）：屏幕位移 → 内容分数 → ⇧ 锁向 → 吸附 → move；PanelView 按下时的
  分派改成 `startInFigureDrag` 调同一个 `inFigureMoveOf`；
- 键盘（`canvas/nudge.ts`）：页面 mm → 内容分数 → move。

所以方向键与拖动在「可移动判据」「父子 / 随行元素整组平移」「形状带着装在里面的内容走」「子图贴边
钳位后随行元素按净位移走」「净位移为零不写 override（GEO-07）」上**逐条一致**——它们调的是同一段代码。
锁定（`lockedGids`）与隐藏的图内元素不动；命中层本来就点不中锁定元素，元素树里仍选得到，所以
键盘这边显式过滤。选中的元素都不能动时说一句「选中的图内元素不能移动」。

画布对象这边同理：可移动判据唯一出处是 `draggableSelection`（`movableTargets` + 隐藏的不动），
方向键与拖动共用。旧 `nudgeSelected` 不过滤隐藏对象，与拖动不一致，删掉。

### 二、谁归方向键推

- 图内编辑态（含快速编辑）：只推**图内选中的元素**；没选中图内元素时吃掉这个键、什么都不改
  ——面板在版上的 x/y 不归这里推（这正是上表第二行的缺陷）。退出图内编辑（Esc）后选中的是面板，
  方向键照常推面板。
- 画布排版：推画布选区（快速编辑里不推，理由见 `useKeyboard` 原注释：那一屏上没有版面）。
- 焦点：输入框 / 可编辑文本 / 对话框（`inEditableTarget`，原有）归它们；**控件已经处理过这一下**
  （`defaultPrevented`：素材卡、元素树、问题列表……）或焦点在 ARIA 复合控件里（listbox / tree /
  treegrid / grid / tablist / radiogroup / menu / menubar / slider / spinbutton / combobox）也归它们。
  `toolbar` 不在此列：本应用的工具条不做方向键漫游，点完对齐按钮接着按方向键微调是常见动作。
  ⌘ / Ctrl + 方向键不微调（原有的 `if (mod) return`；留给原生菜单与脚本切换）。

### 三、步长：页面 mm

普通 **0.5 mm**、⇧ **5 mm**、⌥ **0.1 mm**（⇧ 优先）。单位与画布对象的坐标、读数盒同一个：页面 mm。
图内元素按**面板在页面上的实际大小**（`panelFullRect`，含裁剪、旋转换回内容坐标系）折成分数位移，
所以缩放过的图上「按一下挪 0.5 mm」照样在页面上量得到——与 ADR 0082「按页面上的实际大小说话」
同一取舍。属性页不显示图内元素的位置（`BATCH_SKIP`），没有第二个单位要对齐。
快捷键帮助写明三档与「连按算一步撤销」。

### 四、一段连续按键 = 一次移动

按下第一下开一段，点按与按住连发都并进这一段；方向键**全部松开**且停顿 `NUDGE_QUIET_MS`（400 ms）
之后收尾。按住时系统连发的首延迟可能长于 400 ms，所以「停了」按「没有方向键按着」判，不按计时器判。
收尾的其它出口：离散动作（`registerGesture` → `finishActiveGesture`，撤销重做按钮 / 菜单也走它）、
按了别的键（`useKeyboard` 顶部 `finishNudge`，免得删除 / 复制并进移动那条撤销）、按下指针、选区变了、
窗口失焦。

- 画布对象：一段 = 一个 documentStore 事务（`beginTxn` / `txnUpdate` / `endTxn`），与鼠标拖动同一种
  历史；净位移为零时丢弃事务。
- 图内元素：这一段里**只动预览平面**（`svgPreviewStore.previewTransform`，rAF 合并，不 commit、不进历史、
  不发后端），收尾时 `InFigureMove.commit` 写一次 override、发一次权威渲染。写回事务
  （prepare → verify → commit）只在用户显式写回时发生，与微调无关；微调改变的只是「写回之前要重放
  多少条 override」，不跳过任何一环。
- 微调**不占** `interactionStore.kind`：占了的话 `undoRedoBlocked` 会把这一段里的 ⌘Z 挡掉；现在 ⌘Z
  先收掉这一段、再撤销它。读数盒（`CanvasHud`）在这一段里显示累计位移「位移 +1.5, 0.0 mm」，
  选中框跟着预览走（`gidDrag` / `elementPreview`，收尾时清掉）。

### 五、几何权威缺席

上一段刚提交、它的权威渲染还没回来时，文档里的 overrides 与画面上的 manifest 对不上
（`displayedExactManifest` 为 null）。这时**不拿旧 manifest 算要写进文档的值**（ADR 0017）：
这一段的按键先记着位移（读数盒照常跟），权威那一版挂上画面后再建 `InFigureMove` 并预览；
这一段若已经停下来了，权威一到直接提交。等 `NUDGE_AUTHORITY_WAIT_MS`（10 s）还没来就放弃；
等待期间被要求「现在收」（离散动作、按别的键、换选区）也放弃——文档不变。

### 六、不吸附

拖动吸附（#575）不作用于方向键。步长固定的移动一吸就被拽回参考线上：离不开，也走不到想要的那一格。
Figma / Illustrator 的方向键同样不吸。这是与拖动**唯一刻意不同**的一条，写在 `canvas/nudge.ts` 顶部。

## 不做

- 方向键不改大小（⌥⇧ 组合不另起语义）；
- 不给独立箭头的单个端点做键盘微调（选中的是整根箭头，微调整体平移）；
- 不做「微调时显示对齐参考线」：参考线在不吸附时出现会误导成「已经对齐」。

## 看护

- `web/src/canvas/arrowNudge.test.tsx`：步长三档、点按 + 按住（首延迟长于停顿阈值）一条撤销、停顿后是
  新一段、⌘Z / 撤销按钮先收再撤、别的键先落定、净位移为零不留历史、锁定 / 隐藏、快速编辑、焦点分派
  （复合控件 / defaultPrevented / 输入框 / 工具条对照）、图内这一段零渲染且收尾一条 override 一次渲染、
  ↓ 方向与子图 bottom-origin、多选整组、锁定与不可移动元素、权威缺席时不写与放弃。每条都跑过变异。
- `web/e2e/arrow-nudge.spec.ts`（真浏览器 + 真 matplotlib）：画布里进图内编辑、选中图例按 → 6 次，
  图例位移 = 6 × 画布对象一步的像素数（误差 0.01 px，预算 0.5 px）、面板不动、按键期间 0 次渲染、
  收尾 1 次；撤销一次回原位；重做、保存、重开后位置与热态逐像素一致；快速编辑里图例能动。
  另一条现造一张带标注的图，**图内每一类鼠标能拖的对象**（子图、图例、自由文字、带文字的标注、
  纯箭头、标题、y 轴标题）各按 → 6 次：位移都等于 6 × 页面步长（误差 ≤ 0.02 px）、挪的不是整个子图；
  然后经顶栏「写回」走写回事务，必须报「已通过干净重放校验」且原件被改写；重开后七个对象的位置
  与热态逐像素一致。前后截图、写回后的原件都挂在 test-results 里。
- `web/src/canvas/groupLockDrag.test.ts`：组内锁定语义，方向键这一侧改为走键盘路径。
- 拖动这一侧行为不变：既有的 `inFigureDrag` / `patchCarryDrag` / `axesCompanionDrag` /
  `fakeRealtimeDrag` / `dragGestureLifecycle` 与 e2e 全部原样通过。
