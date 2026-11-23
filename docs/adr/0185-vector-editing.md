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

**修订（2026-11-15，清单加 2 项）**：对照 Illustrator 的描边 / 颜色面板补齐填充与描边时，有两项是科研排版常用、原清单却没列的——
「斜接上限」（●，尖角的折线 / 三角形在期刊图里被削成斜角是常见投诉）与「默认填色描边 D / 互换填色和描边 ⇧X / 无 /」（●●，
Illustrator 用户最常按的三个颜色键）。两项都按其余项的频率口径定权重，没有动任何已有项的权重；清单变成 73 项、合计 165。
加了它们之后覆盖率是 64.5%，不加是 63.9%（+0.6 个百分点），在矩阵里可以逐项核对。

**修订（2026-11-15，M3 第一期：渐变落地，细节归 ADR 0186）**：线性 / 径向渐变的填充与描边在属性栏里调（同日用户返工成 PPT 式：「填充」「描边」那一行渐变时是开关 + 图标下拉 + 角度 + 反向，
下面常显一条占满控件列的横向色带直接编辑节点，再下面是选中节点一行；径向的中心与半径在「更多」里），PDF / SVG / EPS / PNG 都接上；画布渐变柄按 0186 的拍板留到第二期，所以矩阵里「线性 / 径向渐变 + 画布渐变柄」
只算一半。覆盖率 71.5% → **72.4%**（计划 M3 80% 的差额：渐变柄 1.5、形状生成器 2、实时圆角 2、色板 1.5 等 M3 其余项）。
落地时定下、0186 正文没写到的几条（0186 在 main 上，本栈落到 main 时补进它的修订）：
- 径向半径按**框的长边**的比例（同心圆，不随宽高比变成椭圆）；
- 色标的实际 alpha = 色标不透明度 × 对象的填充 / 描边不透明度（「不透明度」那一行照常管整体）；
- 渐变只在这一侧生效时才画、才进载荷（关掉填充 / 描边就不画，字段留着，再打开还在）；读不成的字段退回纯色；
- 带端型的路径：描边渐变只上杆，端型照用纯色 `color`；
- 降级纯色不在界面上出现：每次写渐变都把位置最靠前的色标颜色同步进 `fill` / `color`（旧版本与切回纯色都用它）；
- 转为路径、编辑锚点（收框）时比例坐标换到新框上、画面不动；路径查找器 / 偏移的结果按原比例铺满新框；轮廓化描边把描边渐变变成轮廓的填充渐变；
  D 清掉渐变、/ 清掉填充渐变、⇧X 跟着颜色互换、吸管吸到纯色时渐变让位、格式刷带上渐变、形状互换在形状之间带着（换成箭头不带）；
- PDF 的 Pattern 矩阵相对**所在内容流**的缺省空间（透明组的 Form 里是 Do 那一刻的 CTM），色标带 alpha 时另建 luminosity 软蒙版；
  EPS 用 Level 3 `shfill`，外层 EPS 只有叠加层里真有 `shfill` 才声明 `%%LanguageLevel: 3`（没有渐变时逐字节不变）。

**修订（2026-11-15，M3 第二批：画布渐变柄 + 实时圆角；0186 的部分在本栈落到 main 时补进 0186）**：覆盖率 72.4% → **73.9%**。
- **画布渐变柄**（`canvas/GradientHandles`，几何 `lib/gradientHandles`）：单选一个带渐变的形状 / 路径、用选择工具时自动出现，不加工具、
  不占键位（G 仍空着）；线性 = 起点 / 终点、径向 = 中心 / 半径柄（在对象自己的水平方向，旋转跟着转），中间色标是线上的点，位置 0 / 1
  的色标由端点代表（端点填它的颜色）。端点吸框的四角 / 边中点 / 中心，⇧ 以另一端为原点锁 45°；色标沿线拖、不越过邻居。一次拖动一条
  历史。编辑哪一侧、选中哪个色标与属性栏的色带共用 `store/gradientEditStore`。编辑锚点、钢笔、多选时不出现。
- **实时圆角**（`canvas/LiveCorners`，几何 `lib/liveCorners`）：矩形每个角里面一个圆点（圆角的圆心），拖它改形状的 `cornerRadius`
  （四角一起，夹到短边一半，可反复改）；路径的尖角（两侧都是直线段、5°–175°）同样有圆点，拖它把角**烤成圆弧**——一个锚点换成
  两个切点 + 圆弧（转过超过 90° 的切成几段，误差 < 0.03%），结果仍是普通 path，导出不认识新东西。半径夹到相邻边长一半 × tan(θ/2)。
  没在编辑锚点时拖一个圆全部能圆的角，编辑锚点时只给、只圆选中的。对象在屏幕上短边 < 32 px 时不出圆点。参考实现
  `scripts/dev/gen_live_corner_vectors.py` ↔ 前端 `roundCorners` 对拍 `tests/golden/live_corner_vectors.json`，后端 `tests/test_live_corners.py`
  把同一份结果经 `geometry.path_segments` 导出、核弧在圆上且与边相切。
- 没做：路径圆角烤进去之后不能再当圆角改（Illustrator 是一直可调的；要做得给锚点加一个圆角半径字段、前后端几何都改），矩阵算一半。

**修订（2026-11-15，M3 第三批：浮动栏让柄、形状生成器、推荐色板）**：覆盖率 73.9% → **76.1%**。
- 拖渐变柄、拖实时圆角的圆点算一次画布交互（`interaction.kind` = `gradient` / `corner`），单选浮动栏按「交互中就让开」的老规矩藏起来、
  松手再现（用户拍板第 8 处）。
- **形状生成器 ⇧M**（`store/shapeBuilder` + `canvas/ShapeBuilderOverlay`，几何 `lib/pathfinder.builderFaces / mergeFaces / builderPlan`）：
  选中两个以上能参与的对象按 ⇧M，按「分割」切成区域画在画布上（悬停高亮）；拖过几块并成一块（沿用第一块的样式来源）、⌥ 拖 / 点删掉。
  会话不碰文档；Esc / Enter / 换选区或工具时收尾，有改动才一次 commit（id / 样式 / 位置规则与路径查找器相同，未触碰的区域也按分割后的
  样子落地——与 Illustrator 一致）；原对象在别处被改了（撤销等）就不落地退出。会话里其它键不响应。内核加载不了时与路径查找器一样灰掉。
- **色板**：取色面板多「图内配色」（当前画布各张图的 manifest 里引擎报的颜色与渐变两端，按次数、去灰阶、前 12 个）与「推荐配色」。
  **期刊目录里没有任何期刊的配色规定**（56 刊的规则里只有 `info.color_mode` 这类色彩模式；私有目录仓库里同样没有），所以推荐配色一律是
  Okabe-Ito 色盲友好八色，说明里写明所选期刊没有规定配色，不编期刊配色；将来目录里有了配色字段在 `lib/swatches.recommendedSwatches` 接上。

**修订（2026-11-15，M4 第一批：嵌套组、组内编辑、剪切蒙版）**：覆盖率 76.1% → **80.3%**。设计按 §五 另开
[ADR 0191](0191-nested-groups-and-clipping-masks.md)；与本 ADR 原写法不同的一处：组结构放在对象上（`groupPath` / `clipMask`），不加文档级 `groups` 表。

**修订（2026-11-15，M4 第二批：变换面板）**：覆盖率 80.3% → **87.0%**。纯函数在 `lib/transform`，写入在 `store/transformActions`，
偏好与「上一次变换」在 `store/transformPrefs`。
- **九宫格参考点**坐在 X / Y 两格之间那个 28 px 的空位（`inspector/RefPointGrid`），**等比锁**坐在 W / H 之间（与面板那一节同一副），
  不加行；镜像两颗与「缩放描边和效果」开关放在节头。参考点缺省左上——X / Y 的含义与以前一样；参考点、等比锁、缩放描边是个人偏好
  （localStorage `tavotto.transform`），不进文档。
- **单个 / 多个独立对象**：X / Y 报各自框上的参考点；改 W / H、旋转都以它为不动点（旋转 = 框中心绕参考点转，参考点在中心时就是原来的
  「绕中心转」）。**选区恰好是一整个组**（含剪切组）：X / Y / W / H 是整组**看得见的范围**（`transform.visibleBounds`：每个对象的视觉框
  与裁它的蒙版外接框求交——剪切组 = 蒙版范围），改它 = 整组变换：位移、按参考点缩放（文字字号随 √(kx·ky)、面板保持宽高比）、绕参考点转。
  画布上剪切组的选框也按这个范围画、描蒙版的轮廓（ADR 0191 审阅第 5 处）。
- **镜像**：页面上的翻转 = 自身框里同一根轴的翻转 + 角度取反（F·R(θ) = R(−θ)·F）：框关于参考点翻过去、角度取反；路径翻比例坐标的
  锚点与渐变，箭头 / 直线翻端点，面板切自己的 flipH / flipV（面板落位是「先翻再转」），文字只翻位置（字不倒过来），自身对称的形状不动几何，
  不对称的（三角 / 多边形 / 星形上下翻、弧线、大括号左右翻、带渐变的形状）先换成等价路径再翻。
- **倾斜**：水平倾斜，以过参考点的水平线为不动线，页面上 x' = x + tan θ·(ref.y − y)；形状与箭头先换成路径，锚点（含旋转）换到页面、
  斜切、重新收框——结果是不带旋转角的路径（烤进几何）；渐变的比例坐标原样带着（不跟着斜切）。文字与图不能倾斜（跳过并说一句）。
- **⌘D 重复复制和位移**：选区正好是上一次 ⌘D 的副本、且副本之后被挪 / 转过，再按 ⌘D 就按「副本 − 原件」的位移与角度差再复制一份
  （Figma / PPT 的做法）；否则照旧错开 4 mm。**⌥⌘D 再次变换**：把上一次在变换面板 / 镜像里做的变换（移动、缩放、旋转、倾斜、镜像）
  按同样的参考点作用到现在的选区（`transformAgain`）。一次变换一次 commit、同 id、同数组位置。
- **前后端**：结果只写旧字段（框、比例坐标锚点、旋转角、flip、线宽），导出端没有新东西。参考实现 `scripts/dev/gen_transform_vectors.py`
  ↔ 前端 `lib/transform.golden.test.ts` 对拍 `tests/golden/transform_vectors.json`；后端 `tests/test_transform_vectors.py` 把同一份前后两个
  路径经 `plan.clip_outline`（`geometry.path_segments` + 对象变换）换到页面，逐点核「倾斜 = 页面斜切、镜像 = 页面翻转」。
- 没做：竖直倾斜（Illustrator 可选轴）；旋转过的对象非等比缩放时按它自己的框缩（Illustrator 会斜切出来）；对齐到关键对象、按指定间距分布、
  阵列（矩阵里仍是 ⬜，留到后面）。

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

**修订（2026-11-15，M2 落地）**：

- **构建方式**：源码（`vecops/`，上游两个 crate 原样放进 `vecops/vendor/`）与编好的 `web/src/lib/vecops/vecops.wasm` 一起进仓库；
  前端构建与桌面打包都不需要 Rust 工具链。可信度靠**逐字节重建**：`vecops/rust-toolchain.toml` 钉 1.95.0、`--locked`、
  `--remap-path-prefix` 抹掉本机路径、`vecops/rustc-wrap.sh` 把 Cargo 混进了宿主平台的 `-C metadata` 换成平台无关的值
  （不换的话 macOS 与 Linux 编出来大小相同、字节不同）；macOS arm64 与 Linux x86_64 实测同一份字节。CI 的快线 job `vecops`（进 fast gate）跑
  `vecops/build.sh --check`，改了 Rust 没重建 .wasm 就红。否决的两种：CI 里现编（每个前端构建、每台打包机都要装 wasm32 目标，
  出错面大）；只提交 .wasm 不提交源码（不可审计，违反「vendor 保留来源」）。
- **实测**（入口：路径查找器 / 偏移 / 轮廓化 / 简化）：wasm 659 KB（比 11-14 的探针多了分割 / 修边要用的平面剖分），gzip 210 KB、
  brotli 164 KB，比上面的估计大三成；Node 里编译 1.2 ms、两圆合并冷启动 4 ms、分割热态 0.24 ms。只在选区适用（右键菜单打开、
  属性栏出现「路径查找器」那一行）时预取，不进首屏。
- **桌面壳不用改 CSP**：`tauri.conf.json` 的 `security.csp` 是 null，后端也不给页面发 CSP，WASM 照常编译；「后果」里原来要求加
  `'wasm-unsafe-eval'` 的那一句已改掉。
- **Codex 内嵌画布不提供要内核的命令**：内嵌画布是单文件构建，`vite.mcp.config.ts` 把 `VITE_VECOPS` 定成 off，内核连同
  `?url` 导入一起被摇掉（单文件里没有这 660 KB）；那几个按钮灰掉，停上去说「请在桌面版或浏览器里打开」。复合路径、反转方向
  不要内核，照常可用。
- **结果的 id**：每个原对象的样式第一次被结果沿用时，那块结果继承它的 id 与名字（版式里指着它的东西不断）；其余结果新 id。
  建立复合路径沿用最后面那个对象（与 Illustrator 一致），释放后第一条子路径留原 id。偏移路径是在原对象上面**多一个**新对象；
  轮廓化描边在原对象没有填充时同 id 换掉它，有填充时原对象关掉描边、轮廓作为新对象放在它上面（Illustrator 是编组，这里不编组）。
- **路径的描边对齐**：路径缺省居中（与 Illustrator 的路径相同；形状缺省仍是内侧），只对全部子路径闭合的路径生效；内侧 / 外侧是
  两倍线宽的描边裁到路径里面 / 外面（外侧的裁剪区是大矩形 + 路径按奇偶），前后端同一画法（`pathGeom.pathStrokeAlign` ↔
  `geometry.path_stroke_align`）。
- 没做：形状生成器 ⇧M（交互式，留到后面一期）；轮廓化描边暂不支持虚线、端型和设了内外对齐的路径（内核不画这些，命令不出现）；
  简化是固定容差 0.5 pt，没有滑块。

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
- **M4 剪切蒙版与嵌套组**：现在的 `groupId` 是平铺一层；~~文档级加 `groups: { id, parentId?, clipBy? }[]`~~（2026-11-15 修订：改成对象上的 `groupPath` / `clipMask`，见 [ADR 0191](0191-nested-groups-and-clipping-masks.md)），导出映射到 `ir.Group(clip=…)`。
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
- 前端多一个按需加载的 WASM（实测 gzip 210 KB / brotli 164 KB，见 §三 修订）。桌面壳不用改 CSP；Codex 内嵌画布有载荷上限（ADR 0069），
  WASM 不进内嵌载荷，内嵌画布里要内核的命令灰掉并说明原因。
- 每期的界面先给浅色 / 深色截图拍板，再开 PR；新控件塞进现有属性栏行与「更多」折叠行，不撑大卡片。
