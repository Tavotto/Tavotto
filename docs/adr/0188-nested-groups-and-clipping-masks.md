# ADR 0188：嵌套组与剪切蒙版

日期：2026-11-15 · 状态：**Accepted**（2026-11-15 用户拍板：M4 两批审阅页共 14 处全按推荐。1.0 豁免沿用 ADR 0185 在
`docs/1.0-release-readiness.md` §1.8 的那一行，M4 属其范围）
相关：[0185 画布矢量编辑](0185-vector-editing.md)（§五 M4 指定另开本 ADR）、[0059 RenderCore 路径编译](0059-render-ir-and-render-plan.md)、
[0161 单图画布留白](0161-drag-corner-scale-edge-canvas.md)

## 问题

ADR 0185 能力矩阵 E 节里「嵌套组 + 双击进隔离模式」「剪切蒙版 ⌘7」都是 ●●●。现状：`groupId` 只有平铺一层，没有剪切。科研排版的常见
用法：把一张图裁成圆形 / 局部放大框、几组标注各自成组再整体成组、裁掉图边多余的留白而不改原图。

导出底座本来就够：`ir.Group` 有 `clip`，PDF / SVG / EPS 写入器都认，PNG 由 Canonical PDF 栅格化。缺的是文档模型、交互与载荷。

约束：

1. 旧构建打开新文档，对象一个都不许丢，也不许多出一个看得见的东西；
2. 与布局组（`layoutGroups`，行 / 列 / 网格排版约束）分开，不许变成第二套组模型；
3. 导出必须真裁剪，不许把矢量栅格化，不许静默少画。

## 决定

### 一、数据模型：放在对象上，不加文档级组表

ADR 0185 §五原写「文档级加 `groups: { id, parentId?, clipBy? }[]`」。改成对象上的三个可选字段：

| 字段 | 含义 |
| --- | --- |
| `groupId` | 对象所在的**最内层**组（原字段，语义不变：旧构建只认它） |
| `groupPath?: string[]` | 外 → 内整条组链，末项必须等于 `groupId`；两层及以上才写 |
| `clipMask?: true` | 这个对象是它最内层组的剪切路径 |
| `clipOff?: true` | 剪切路径本身被隐藏 = 暂时不裁（见 §三） |

理由：旧构建的 `canvasToDoc / docToCanvas` 只搬它认识的画布字段，文档级新表在旧构建里**存一次就没了**；对象上的未知字段旧构建原样保留
（结构克隆、Immer 补丁都不碰）。组存在 = 有对象挂在它下面，所以删对象、换画布、复制都不会留下悬空的组。

读法唯一出处 `lib/groupTree.groupPathOf`：链合法（末项 = `groupId`、无重复、无空串）就用链，否则只认 `[groupId]`。旧构建只认
`groupId`：嵌套组摊成最内层一层组；旧构建改过 `groupId`（成组 / 取消成组 / 复制）时链对不上，新构建按一层读——对象都在。

布局组只按 `groupId` 认成员，本 ADR 不碰它。

### 二、选择按「单元」，组内编辑是作用域

点中一个对象，选的是它在**当前这一层**里的单元：直接成员就是它自己，否则是紧挨着这一层的那个子组的全员。移动、锁定（组里任一成员锁定
整组不动）、成组、取消成组、建蒙版都按单元算（`actions.groupMates / expandGroups / selectedUnits / movableTargets`）。

当前这一层由组内编辑（隔离模式）决定：`store/isolationStore` 存外 → 内的组链，不进文档、不进历史；链上的组没了自动退回还在的那一截，
换画布清空。双击组里的对象先进组（`groupActions.enterGroupAt`），进到它是直接成员那一层后双击才交给对象自己（改字、编辑锚点、
进图内编辑）——与 Illustrator 一致。Esc 退一层并选中刚退出的组；双击空白处全退。组外对象变淡、不接指针。「我在改哪一层」与出口在
`WorkspaceContextBar`（只说一遍）。

⌘G：把选区的单元收进一个新组，挂在当前这一层下面，子组整体成为成员；成员挪成连续一段、落在最上面那个的位置。⇧⌘G：只拆一层，成员升到
它原来所在的那一层。图层树按链逐层缩进（`groupTree.layerTree`，节点按整条前缀认，改乱的链各成一棵、不丢对象）。

### 三、剪切蒙版

- **建立 ⌘7**：选区在当前这一层至少两个单元，最上面那个是这一层的直接成员且能当蒙版（自由路径、封闭形状；直线 / 弧线 / 大括号 /
  文字 / 图不行）。一次 commit：按 ⌘G 建组，给蒙版写 `clipMask: true` + `hidden: true`。`hidden` 是给旧构建的：旧构建不画蒙版、内容不裁、
  对象都在；新构建只按 `clipMask` 判生效（同组多个标了 `clipMask` 时最上面的生效），不看 `hidden`。
- **释放 ⌥⌘7**：去掉 `clipMask / clipOff / hidden`，蒙版恢复原样式，组还在。⇧⌘G 拆剪切组 = 同时释放。被裁的对象删光了，蒙版自动还原成
  普通可见对象（`actions.releaseOrphanMasks`），不留看不见的东西。
- **隐藏剪切路径**：图层树上对蒙版点「隐藏」= 暂时不裁（`clipOff`，Illustrator 同一做法），内容全露出来、蒙版仍不画；再点「显示」恢复裁剪。
  `hidden` 永远留给旧构建，不跟着变；批量隐藏 / 显示（`setObjectsHidden`）跳过蒙版。
- **几何**：形状蒙版在画布与导出时都先换成等价路径（`convertToPath.toPathObject`，画面逐点不动的那一份），所以前后端只有「路径 → 轮廓」
  一条规则（`lib/clipMask.maskOutline` ↔ `plan.clip_outline`：`path_segments` 的填充几何 + 对象变换）。蒙版只借几何，填充、描边、透明度不参与。

### 四、画布与导出

- 画布（`CanvasStage.LayerObjects`）：生效的蒙版不画；被裁的对象外面一层层包贴在世界原点的零尺寸 div，`clip-path: path()`（外层蒙版在外），
  同时裁掉命中。
- 载荷：`toExportObjects(objects, context)` 不发蒙版；被裁的对象带 `clip: ExportClipPath[]`（外 → 内，只有几何）。没有剪切组时载荷与以前
  逐字节相同。**按对象逐个包裁剪**，不要求组成员在 z 序里连续（逐个裁与整组裁结果相同）。
- 后端 `plan._clipped`：每个蒙版包一层 `Group(clip=…)`（外层在外）；蒙版画不出轮廓 = 这个对象整个不画（与画布同一条）。
  PDF `W n`、SVG `<clipPath>`、EPS `clip`、PNG 由 PDF 栅格化。
- 单图编辑（`scope=original`）裁这张图：`original.clip`（`singleExportDoc.figureClipOf`），没有标注也走带标注的合成路径；EPS 由
  `epsoverlay.compose(figure_clip=…)` 在嵌入段外面 `clip`（仍是矢量）。写回原图时轮廓与标注同一套换算（`writeBackAnnotations.toFigureMm`）。
- 同源对拍 `tests/golden/clip_mask_vectors.json`（`scripts/dev/gen_clip_mask_vectors.py`）。

## 后果

- 新构建写的嵌套组 / 蒙版在旧构建里退化为一层组、不裁剪，对象都在、蒙版看不见；旧构建保存后再用新构建打开，结构按 §一的读法恢复或退化。
- 双击组里的对象要先进组：以前成组的文字双击一次就能改，现在要两次（与 Illustrator 一致）。
- 选中剪切组时选框按蒙版范围、描蒙版轮廓，属性栏的 X / Y / W / H 也是蒙版范围（ADR 0185 M4 第二批「变换面板」）。
- 没做（留到后面）：剪切路径在画布上只能从图层树选中；文字当蒙版。
- 看护：`lib/groupTree.test.ts`、`store/groupActions.test.tsx`、`lib/clipMask.golden.test.ts`、`canvas/workspaceContextBar.test.tsx`、
  `tests/test_clip_mask.py`、`tests/test_export_original_clip.py`。
