# ADR 0185：画布矢量编辑——自由路径、路径查找器、渐变与剪切蒙版

日期：2026-11-14 · 状态：**Accepted**（2026-11-14 用户拍板：方案五项全按推荐；1.0 豁免已登记在 `docs/1.0-release-readiness.md` §1.8）
相关：[0059 RenderCore 路径编译](0059-render-ir-and-render-plan.md)、[0072 渲染闭包零 PyMuPDF](0072-default-render-backend-cutover-and-pymupdf-retirement.md)、
[0086 精确命中几何](0086-precise-hit-geometry-for-series-shapes-and-annotations.md)、[0163 画布文字自由旋转](0163-canvas-text-rotation-and-copy-figure-text.md)、
[0174 形状与箭头的描边属性](0174-shape-stroke-properties.md)。能力进度：`docs/ANNOTATION_CAPABILITY_MATRIX.md`「E. 矢量编辑」。

## 问题

画布标注只有文字、箭头和 7 种参数形状（矩形 / 椭圆 / 三角 / 菱形 / 多边形 / 大括号 / 直线）。用户原话：「目前我们的矢量编辑工具太弱了，
无法满足用户最基本的排版添加图形的需要」，要求至少具备 Illustrator 相关编辑功能的 80%，并且更好用。

缺的是一整层：自由路径（钢笔、锚点编辑）、路径查找器（合并 / 相交 / 减去 / 形状生成器）、偏移与轮廓化、渐变、剪切蒙版、嵌套组、
变换面板、SVG 互通。导出底座本来就够：`ir.Path` 支持 M/L/C/Z（三次贝塞尔），`ir.Group` 支持 clip / transform / opacity。

## 决定

### 一、「80%」的口径

不按 Illustrator 的全部功能算——3D、透视网格、网格渐变、画笔、图像描摹、封套扭曲、效果库、符号喷枪、分色与排版无关，不做。
按**科研排版与补示意图会用到的那部分**列一张清单（`ANNOTATION_CAPABILITY_MATRIX.md`「E」，71 项），每项按使用频率加权
（高 3 / 中 2 / 低 1，合计 162），已有算满分、部分算一半。**加权覆盖率 ≥ 80% 算达标。**

2026-11-14 的基线是 **46%**。按分期累计：M0 47% → M1 62% → M2 72% → **M3 80%** → M4 93% → M5 99%。
清单里的项与权重改动要在这份 ADR 的修订里写明理由，不许为了凑数改权重。

### 二、新对象类型 `path`

```ts
interface PathObject extends ObjectBase {
  type: 'path'
  subpaths: { closed: boolean; nodes: PathNode[] }[]
  fillRule?: 'evenodd'            // 缺省 nonzero，不写字段
  // 填充 / 描边字段与 ShapeObject 逐字相同（ADR 0174）：
  strokePt; color; fill; fillOpacity?; stroke?; strokeOpacity?; dash?; lineCap?; lineJoin?
  // 只对开放子路径生效（ArrowObject 同名同义）：
  headStart?; headEnd?; headSizeStart?; headSizeEnd?
}
interface PathNode {
  p: [number, number]              // 锚点，包围盒比例坐标（与 EndPoint 的 rx/ry 同一套）
  in?: [number, number]            // 入手柄（绝对，同一坐标系）；缺省 = 与锚点重合
  out?: [number, number]           // 出手柄
  smooth?: true                    // 平滑点（拖一侧手柄另一侧共线）；缺省尖角
}
```

- **坐标是包围盒比例**，`x/y/w/h` 仍是权威包围盒：缩放、对齐、吸附、多选联合框、`visualBounds`、`rotationDeg` 全部沿用，不开第二套。
  包围盒按曲线实际范围算（不按控制点），手柄可以落在 [0,1] 之外。编辑锚点改了曲线范围时，同一次 commit 里重算包围盒并把比例坐标换算回去。
- 结构与 vectorcraft 的 `PathData / SubPath / Anchor`（绝对 in/out 手柄 + Corner/Smooth）一一对应，进出几何内核只是坐标换算。
- 磁盘格式不升版。旧构建读到 `type: 'path'` 的行为由 §六 的兜底决定，**兜底比第一个能写 `path` 的版本早至少一个正式版发出**。
- 现有形状「转为路径」：一次 commit、**不换对象 id**、数组位置不动，几何取自 `lib/shapeGeometry.ts`（与 `rendercore/geometry.py` 同源）。
  这是跨族转换，不走 `lib/shapeSwitch.ts`（那里只管族内互换），是单向的（撤销负责回去）。

### 三、几何内核：vectorcraft `geom` + `pathops` 编成 WASM，只在前端跑

- 来源 [storytold/vectorcraft](https://github.com/storytold/vectorcraft)，许可 **MIT OR Apache-2.0**，与本仓库 AGPL-3.0-only 兼容；
  vendor 时保留 LICENSE 与 NOTICE，登记进第三方许可清单。**钉死一个上游提交，只 vendor `geom` 与 `pathops` 两个 crate**（依赖只有
  `kurbo`、`linesweeper`、`thiserror`、`serde`），不跟着上游跑；升级是一次单独的 PR，带黄金用例对比。
- 调用面是平坦 f64 缓冲（不用 wasm-bindgen）：`[子路径数, (闭合, 锚点数, (px,py,ix,iy,ox,oy,smooth)*)*]`。
- **2026-11-14 实测**（上游 `47500c4`，`opt-level=z` + LTO，含布尔 / 偏移 / 轮廓化 / 简化四个入口）：wasm 420 KB，gzip 155 KB，
  brotli 121 KB；Node 里实例化 0.9 ms；两个圆的合并冷启动 3.3 ms、热态 0.15 ms，相交 / 减去 / 排除 0.2 ms，偏移 1.4 ms。
- 按需加载：第一次用到路径查找器时才拉，不进首屏包。
- **结果直接写成 `path` 对象**（破坏性，与 Illustrator 路径查找器的默认一致）：一次 commit、一条撤销；参与运算的原对象删除，
  结果占顶层那个对象的数组位置。后端永远不需要 pathops——几何只在一处算、只算一次，热态 == 文件 == 重放天然成立。
  实时复合形状（保留原件可再调）不做。
- 备选与否决：纯 TS（paper.js 的布尔在重合边上常出错；Clipper2 只算多边形，曲线要先打散再拟合，丢精度）。

### 四、导出侧只加「画路径」

- `rendercore/plan.py` 加 `_path_node` → `ir.Path`；`geometry.py` 加 `path_segments`，与前端 `lib/pathGeom.ts` 共读
  `tests/golden/path_vectors.json`（与 `shape_geometry_vectors.json` 同一做法，两侧各跑一遍）。开放路径的端型复用 `arrow_paths` 的端型几何。
- 要一起接上的消费者（漏一个就是静默少画）：`svgwriter` / `epswriter`、`CanvasThumb` / `ThumbShape`、`exportPayload.toExportObject`、
  `exportreq.ORIGINAL_OBJECT_TYPES`、`rendercore/facade.place`、`writeBackAnnotations.ANNOTATION_TYPES` → `annotate_asset`、
  `app.py` 写回标注的类型过滤、版本草图、命中几何（填充按 fillRule、描边距离换到 mm）、图层树图标、`objectLabel`。

### 五、后续各期的边界（各自修订本 ADR 或另开 ADR）

- **M1 钢笔与锚点**：工具写成纯状态机（指针事件进，预览 + 命令出），画的过程只走预览平面（`svgPreviewStore`），完成一条路径 =
  一次 commit；直接选择拖锚点走 `beginTxn/endTxn`；离散动作先 `finishActiveGesture()`。每个动作注册成命令，命令面板、右键菜单、
  快捷键与 MCP 用同一份——AI 也能画。快捷键按 Illustrator 习惯，开工前对一遍与现有快捷键的冲突。
- **M2 路径查找器**：§三。
- **M3 渐变**：见 [ADR 0186](0186-gradients.md)（扩 `ir.Paint` 闭集：PDF Shading Pattern、SVG `<linearGradient>`、EPS `shfill`）。
- **M4 剪切蒙版与嵌套组**：现在的 `groupId` 是平铺一层；文档级加 `groups: { id, parentId?, clipBy? }[]`，导出映射到 `ir.Group(clip=…)`。
  必须与 `LayoutGroup`（行 / 列 / 网格排版组）分开，不许变成第二套组模型。另开 ADR。
- **M5 SVG 互通**：粘贴 / 拖入 SVG 落成可编辑对象，复制为 SVG。

### 六、旧构建的兜底（M0 第一个 PR，先于 `path` 发出）

2026-11-14 核过当时的 main：读盘、保存、后端 `validate_document` 都不按对象类型过滤，不认识的对象**会被原样保留**；但图层树
`iconFor` 拿到 undefined 会让整个界面落进错误边界，导出时 `toExportObject` 回 undefined → 载荷里是 null → 后端静默丢掉。
兜底：判据只有 `types/document.isKnownObject`；画布只画虚线占位框、不接指针；全选 / 框选不收；图层树用通用图标、名字「新版对象」；
导出前报个数、不许开始。全文在 `docs/rules/frontend/canvas-objects-and-workspace.md`。

### 七、比 Illustrator 好用的地方（产品目标，逐期落实）

1. 锚点与箭头端点能吸到坐标轴、刻度、数据点、图例框（扩 `inFigureSnapCandidates`）。
2. 吸管能吸图内元素的颜色；色板默认是本图配色 + 所选期刊推荐色。
3. 期刊口径预检：线宽低于期刊下限、EPS 投稿里的渐变，走现有问题面板。
4. 科研预设：显著性括号 + 星号、比例尺、局部放大框连线、弯箭头，拖进来就是可编辑路径。
5. 单位全程 mm / pt，与导出尺寸、期刊要求同一套数。

## 后果

- 新增一个对象类型，§四 列出的消费者一个都不能漏；每个消费者要有用例。
- 前端多一个按需加载的 WASM（约 120–155 KB 压缩）。桌面壳 CSP 要允许 `'wasm-unsafe-eval'`；Codex 内嵌画布有载荷上限（ADR 0069），
  WASM 不进内嵌载荷：能外链加载就外链，不能就在内嵌画布里不提供路径查找器并说明原因。
- 每期的界面先给浅色 / 深色截图拍板，再开 PR；新控件塞进现有属性栏行与「更多」折叠行，不撑大卡片。
