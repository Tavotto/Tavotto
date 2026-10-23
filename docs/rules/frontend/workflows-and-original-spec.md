# 两条工作流与原图规格（2026-08-29，Prompt 09；ADR 0028）

> 原文出自 `web/AGENTS.md`「两条工作流与原图规格（2026-08-29，Prompt 09；ADR 0028）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 看护用例（`web/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`store/workspace.test.ts`、`lib/originalSpec.test.ts`、`canvas/fastEditStage.test.tsx`、`e2e/nav-audit.spec.ts`、`tests/test_original_spec.py`

<!-- agents-route
sheet: web/AGENTS.md
order: 260
改到: `store/workspace.ts`、`openFastEdit` / `addFigureToLayout` / `focusLayoutPanel`、`lib/originalSpec.ts`
主题: 两条工作流与原图规格 → `workflows-and-original-spec.md`
必守要点: 模式由画布图数推出（1 张=单图编辑），`settleMode` 唯一写入点；三个稳定动作是唯一出口
-->

```text
单图编辑（代码里叫 fast_edit）：画布上恰好 1 张图 → 修改 → 按原图规格导出
多图排版（layout）          ：画布上 ≥2 张图 → 排列 → 按画布规格导出
```

**模式由激活画布上的图数推出，不是「这次怎么进来的」（2026-10-21，用户拍板）。** 1 张（面板，runtime
图与磁盘图同等计，标注 / 文字 / 形状不计，**隐藏的图也计**——隐藏 / 取消隐藏不改模式、不发通知）= 单图编辑；0 张 = 空画布（排版）；≥2 张 = 多图排版。
从素材库打开、拖入、新建项目、会话恢复、撤销 / 重做、删除 / 隐藏第二张，结果都一样；多画布各自判（切标签即改判）。
**写 `mode` / `activePanelId` 的只有 `store/workspaceStore.settleMode`**（幂等；订阅 `workspace.startWorkspaceModeSync`
在每次文档变化后调它，`openFastEdit` / `addFigureToLayout` 在改完文档后再调一遍是为了订阅没挂的单测一致）；入口只改文档，
不许各自 set 模式。第二张图进来（单图 → 多图）订阅取景「页面 ∪ 所有图」（`fitRectAnimated`，留在适应模式）、发一次
`fastEdit.enteredLayout` 浮动通知（`setStatus`，不是 BannerStack 横条）和一枪 `workspace.entered_layout`；删到只剩一张
回单图编辑并取景那张图本身；删光回按页面取景。切画布 / 载入 / 换项目由各自的适配点取景，订阅不插手。页面尺寸 / 规范 / 序号
留在文档里，删到一张只是不画页面，再加第二张原样回来。排版里双击图进图内编辑仍是排版的子状态（`uiStore.elementPanelId`）。

**单图编辑的舞台画这张画布上的全部对象（2026-10-21，用户拍板，取代 Codex #833 的「快编只画那张图」）**：图 + 标注 / 文字 /
形状都画、可选、可编辑，工具条与绘制快捷键在；页面纸 / 网格 / 标尺 / 页外遮罩仍让开（`FastEditPaper` 给图垫纸）。
于是「只认正在编辑的那张图」那组不变量整体取消：全选 = 这张画布上看得见且没锁的全部对象，删除删选区，方向键 / 对齐推选区，
创建副本与粘贴照常——**粘出的是图 = 第二张 → `settleMode` 切多图排版，粘标注留在单图编辑**。同一张图的图幅被**静默校正**（渲染回来按真实图幅改 w / h，不进历史）而视口还停在「适应」上（没平移 / 缩放、没有手势进行中）时，订阅按新图幅重取景一次——否则放大取景按占位图幅算、校正后图伸出舞台；用户的缩放 / 采用外框是历史，画面保持原位。单图编辑的世界层不占页面大小的盒（`width/height: 0`）：取景放大到图上时，页面大小的盒会伸到侧栏底下，axe 把侧栏文字判成「背景被盖住」。取景框（`stageFitFrame`）=
没隐藏对象 `visualBounds` 的并集（图 ∪ 标注），缩放到选中 / 标尺选区带 / 读数用同一个 `renderedSelection`（只滤隐藏）。

**「打开」= 换看那一张，不是加第二张（2026-10-21，用户拍板）**：`openFastEdit`（素材库双击 / 「打开」/ `tavotto open` 交接 /
接入状态「打开」/ 准备卡进入编辑）——图已单独在某画布上 → 切到那张画布；在多图画布上 → 切过去并对它进图内编辑（排版子状态）；
不在任何画布上 → `createCanvasAndActivate(图名)` 新建画布放它（单图编辑），原画布一个字节不动。新建画布本身全仓库都不进撤销栈
（`addCanvas`），放图这一步是一条历史：撤销把图撤掉，画布留着、空（排版）。拖入 / 「加到画布」（`addPanelToCanvas` /
`addFigureToLayout`）才是往当前画布加第二张。

**一个文档，不是两套应用。** 没有第二个 documentStore、第二个 override
writer、第二份对象模型：一张图在文档里只有**一个**面板对象，两种模式看的是
同一个对象。快速编辑 = 把它单独摆出来（页面纸 / 网格 / 参考线 / 别的对象
全部让开），画布排版 = 它在页面上的落位。

- **模式是工作区状态，也是文档的纯函数**：`store/workspace.ts` 的 `mode` / `activePanelId`（状态本体在叶子模块 `store/workspaceStore.ts`，`workspace.ts` 再导出；拆开是为了 `actions` 读它不成环），
  不进文档、不进撤销、不置 dirty，**不落本机**（此前按 documentId 存的 `tavotto.workspace.<id>` 与恢复逻辑已删：存的与推的对不上只会多出一个出口）。
  不变式：`mode === 'fast_edit'` ⟺ `activePanelId !== null` ⟺ 激活画布恰好一张看得见的图。
  **`activePanelId` 是对象 id 不是素材 id**——同一张素材可以有两个实例，
  用素材 id 的话工作区会在两个实例之间随机跳。
- **快速编辑一个字都不写 x/y/w/h**。「从画布进图内编辑再返回，布局不变」
  不是靠"回来时恢复一下"，而是靠**根本没动过**——恢复式的实现总有一条路径
  会漏掉（旋转、成组、布局组重排），而漏掉的表现是用户的版被悄悄改了。
- **视口是另一回事，切换时按目标取景**（2026-10-21 取代 2026-09-06 的「停放 / 还原」）。单图编辑把那张图本身
  的矩形取景（`fitRectAnimated`，与 ⌘1 / `stageFitFrame` 的快编分支同一个框）；第二张进来取景「页面 ∪ 所有图」并留在适应模式；
  不再有「进快编记下排版视口、回排版还原」（`parkLayoutView` / `layoutViewOnReturn` 已删——单图编辑没有「回排版」这一步，
  用户说取景「页面 ∪ 图」）。同一调用栈里加图入口的 `frameAddedPanel` 认 `framedBySwitch` 不再取第二遍。看护：
  `store/workspace.test.ts` 的「切模式的取景」、`store/addPanelFraming.test.ts`、`store/pageFit.test.ts`。
- **四个稳定动作是唯一出口**（11 定位 / 12 导出 / 18 QuickEdit / 21 onboarding
  复用它们，别在界面里重新拼一遍"找对象 / 没有就添加 / 切画布 / 选中"）：
  `openFastEdit(figureId)` / `addFigureToLayout(figureId)` / `focusLayoutPanel(panelId)`
  （`returnToLayout` 随「单图编辑没有排版可回」已删）。「文档里有没有这张图」的判据也只有一处：
  `documentStore.findFigurePanel()`（工作区重导出）；指定对象 id 时不退到同素材的另一个实例。
- **「添加到画布」不复制对象**：已经在文档里就只是聚焦它（`focused`），
  重复点不会叠出第二个面板，overrides 一直在同一个对象 id 上。
- **能不能进图内编辑用既有判据**（`panel.script`，与 `ObjectView` 双击、
  `enterElementEdit` 同一个）；**说什么话**归 `lib/readinessText.ts`。
  两者不是一件事，别在 workspace 里另起一份状态判断。
- **对象消失就改判模式**：图数变了 `settleMode` 自会改判（删 / 隐藏到剩一张 → 对着剩下那张，否则回排版），
  `usePruneSelection` 只管选区与图内编辑态，不再碰工作区模式。
- **原图规格只有 `lib/originalSpec.ts` 一份服务**（ADR 0028）。优先级
  ① 渲染回来的 manifest `size_mm` → ② 文档里的 `nativeW/nativeH` →
  ③ `/api/panels` 的 `original_spec` → ④ 明确 fallback（必带 `fallback: true`）。
  ① 在 ② 之前正是因为**图幅不是派生字段**（见 `docs/rules/frontend/project-document-and-autosave.md` 的派生字段表）。
  画布上的缩放 / 裁剪 / 旋转 / 翻转 / 透明度只进 `spec.ignored`，**绝不套进
  原图导出**——跟着缩的话字号会一起缩。`getOriginalOutputSpec()` 对不认识的
  id 回 `null`，不发明一张不存在的图。
- 看护：`store/workspace.test.ts`、`lib/originalSpec.test.ts`、
  `canvas/fastEditStage.test.tsx`；后端事实层 `tests/test_original_spec.py`。

## 速查表原要点（2026-09-25 迁入，#608）

`web/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 模式是工作区状态不进文档
- 快速编辑一个字不写 x/y/w/h，但视口必须还原（带画布 id）
- 四个稳定动作是唯一出口、`findFigurePanel()` 唯一
- 原图规格优先级 ① manifest `size_mm` → ② `nativeW/H` → ③ `original_spec` → ④ fallback，画布变换只进 `ignored`
