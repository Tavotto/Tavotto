# ADR 0174：形状与箭头的描边属性补齐（无边框、描边不透明度、线型、端型、线端 / 拐角）

- 状态：已接受（用户 2026-11-10 裁决：「画出来的方块无法设置取消边框」；对照 Illustrator 描边面板与 PowerPoint「设置形状格式」过一遍形状和箭头属性并补齐，给 1.0 豁免，见 `docs/1.0-release-readiness.md` §1.8）
- 相关：[0059 RenderCore 路径编译](0059-render-ir-and-render-plan.md)（形状 / 箭头 → `ir.Path`）、[0072 渲染闭包零 PyMuPDF](0072-default-render-backend-cutover-and-pymupdf-retirement.md)、[0163 画布文字自由旋转](0163-canvas-text-rotation-and-copy-figure-text.md)（同一类「可选字段缺省不写」的做法）、[0171 导出修改后的 .py](0171-export-edited-script.md)（见「不受影响的消费者」）

## 问题

画布形状只有填充、描边色、线宽、线型（实 / 虚 / 点）、圆角、边数；箭头只有线型、三种端型和反向。对照 Illustrator 与 PowerPoint 缺：

1. **形状没法去掉边框。**画布与导出都把线宽钳到 ≥ 0.05 pt（`ShapeView` / `geometry.shape_paths`），描边永远在。
2. 描边不透明度（形状、直线、箭头）。
3. 线型只有三种。
4. 箭头端型只有三角 / 开口 / 短线，且大小固定。
5. 线端（平头 / 圆头 / 方头）与拐角（尖角 / 圆角 / 斜角）写死成圆头 + 圆角。

## 决定

### 字段（全部可选；缺省 = 加这些属性之前的画法，缺省值**不写字段**）

| 对象 | 字段（文档 / 导出载荷） | 取值与缺省 |
| --- | --- | --- |
| 形状 | `stroke` / `stroke` | 只有 `false` 会落盘（= 无边框）；缺省 / `true` = 有描边。**只对封闭形状**（矩形 / 椭圆 / 三角形 / 菱形 / 多边形）；直线与大括号只有描边，字段被忽略 |
| 形状、箭头 | `strokeOpacity` / `stroke_opacity` | 0–1，缺省 1 不写；形状里只作用于描边（填充有自己的 `fillOpacity`），箭头里作用于杆与端型 |
| 形状、箭头 | `dash` / `dash` | 在 `dashed` / `dotted` 之外加 `longdash`（长虚线）、`dashdot`（点划线）、`longdashdot`（长点划线） |
| 箭头 | `headStart` / `headEnd`（`head_start` / `head_end`） | 在 `triangle` / `open` / `bar` 之外加 `dot`（圆点）、`diamond`（菱形）、`stealth`（燕尾） |
| 箭头 | `headSizeStart` / `headSizeEnd`（`head_size_start` / `head_size_end`） | `small` / `medium` / `large` = 0.7 / 1 / 1.5 倍；缺省 = `medium` = 现在的大小，`medium` 不落盘 |
| 形状、箭头 | `lineCap` / `line_cap` | `butt` / `round` / `square`；缺省 `round`（现在的画法） |
| 形状、箭头 | `lineJoin` / `line_join` | `miter` / `round` / `bevel`；缺省：封闭形状与箭头 `round`，直线与大括号 `miter`（见下） |

**为什么 `stroke: false` 而不是「color 置 null」：**`color` 是必填字符串，几乎所有消费者（预设、类型切换、写回、旧构建）直接读它；改成可空等于逼每个读取点加分支，旧构建读到 `null` 还会画成黑线。单独一个只在关掉时才出现的布尔，旧文档不带它、旧构建读到它只是忽略（描边仍在）——最保守。读取只有一个入口 `types/document.shapeStrokeOn()`，后端 `shape_paths` 同一判据。

磁盘格式不升版。旧文档（不带任何新字段）的画布显示与导出**逐字不变**：`tests/golden/shape_legacy_paths.json` 是加属性之前的 `geometry.py` 对 510 个旧写法对象出的全部路径，`tests/test_shape_stroke_props.py` 要求现在逐字相同。

### 无边框的几何

封闭形状有描边时，描边居中、整个形状内缩半个线宽，让外沿正好贴合包围盒。**无边框时不内缩，填充铺满整个包围盒**（画布 `shapeInset()` 与后端 `shape_paths` 同一条；圆角半径也按铺满后的尺寸钳制）。这与 PowerPoint 一致——框就是形状；切换描边开关时形状会外扩 / 内缩半个线宽，是有意的。

没有边框又没有填充：什么都不画（IR 不收「既不填也不描」的路径，`plan._shape_node` 返回 `None`），仍可选中（命中层沿轮廓）。这是用户自己设出来的，不报错也不替用户补填充。

### 线型表（前后端同一张）

段长 = 线宽的倍数，「画 / 空 / 画 / 空…」：`dashed` 4, 2.5（不变）、`longdash` 8, 3、`dashdot` 4, 2.5, 1, 2.5、`longdashdot` 8, 3, 1, 3。`dotted` 仍是 0.01×线宽 的段 + 2×线宽 的空，靠圆线帽画点。表在 `shapeGeometry.DASH_RATIOS` ↔ `geometry.DASH_RATIOS`，共享向量接住。

### 线端与点线

`dotted` 的点只有圆线帽才有面积，所以**点线强制圆头**：选了平头 / 方头在点线下不生效，属性栏的线端行置灰、固定显示「圆头」，原因放 `title`；规则只有 `strokeCap()` ↔ `stroke_cap()` 一处。其余线型（含点划线，它的点有 1×线宽 的长度）尊重用户的选择。

### 拐角缺省与斜接上限

- 缺省拐角按形状族：封闭形状 / 箭头 `round`（现在的画法）；直线与大括号 `miter`——**后端本来就是 miter，画布以前写的是 round**。它们没有可见的拐角（一段直线；大括号的曲线与直线相切衔接），两种写法画出来一样，这次把画布对齐到后端，不改任何导出。
- **斜接上限写成 10：**SVG 缺省 4、PDF 缺省 10。选了尖角后，很尖的三角形在画布上会被削成斜角、导出却是尖的，所以画布显式写 `stroke-miterlimit: 10`（`STROKE_MITER_LIMIT`）。
- 箭头杆只有一段，拐角对它不可见；拐角作用于开口端型（V 形的尖）。短线端型沿用旧产物的 miter（一段线，无差别）。端型不画虚线、不受点线强制圆头影响。

### 箭头端型

几何（`shapeGeometry.arrowGeometry` ↔ `geometry.arrow_geometry` / `head_geometry`，共享向量逐点对拍）。`L = 4 × 线宽 × 大小档位`，`H = 1.7 × 线宽 × 大小档位`；下面的距离都是沿箭杆从尖向后量：

| 端型 | 形状 | 杆回缩 |
| --- | --- | --- |
| `triangle` | 尖、后 L 处 ±H（不变） | 0.75 L（不变） |
| `stealth` 燕尾 | 尖、后 L 处 +H、后 0.7 L 处的凹口、后 L 处 −H | 0.6 L |
| `diamond` 菱形 | 尖、后 L/2 处 ±H、后 L 处 | 0.5 L |
| `dot` 圆点 | 圆心在尖后 H 处、半径 H（直径 = 帽全宽，贴着端点） | H |
| `open` / `bar` | 不变 | 无 |

不认识的端型（更新版本写的）：不画端型、杆不回缩（线到端点）——与旧构建读到新端型时的行为一致，不报错。

**`legacyHead` 与新端型。**`ArrowObject.head`（none / end / both）是 `headStart` / `headEnd` 出现之前的旧字段，读取时**只有两个新字段都缺席才看它**。所以新端型不需要为旧字段挑一个「近似的旧值」：`legacyHead` 仍然只回答「这一端有没有端型」，`dot` / `diamond` / `stealth` 都算「有」（`head: 'end'`）。旧构建 / 旧后端读到带新端型的文档时优先认 `headStart` / `headEnd`，不认识的端型画不出端型（只剩一条线到端点）；它们也不会因此崩溃。这个降级是可接受的：新端型是新能力，降回旧版本本来就拿不到。

### 描边不透明度与 EPS

进 `ir.Paint.alpha`（描边的 `Paint` 与箭头端型的填充 `Paint`），PDF / SVG / PNG 同一份 IR。**EPS 没有透明**：`epswriter` 对 alpha < 1 的路径抛 `UnsupportedCapability`，与今天的填充不透明度、面板不透明度同一条路径（EPS 这一项逐项失败、其余格式照常）。不为描边另开特例。

### 不受影响的消费者（逐个查过）

- **导出修改后的 .py（ADR 0171）：**`scriptadjust` 只把图内 override 翻译成 matplotlib 代码，画布标注不进脚本，不受影响。
- **写回原图携带标注（`writeBackAnnotations`）：**只缩放 `stroke_pt` / `corner_radius_mm`；新字段要么是比例（线宽倍数的线型、档位）要么无量纲，不需要缩放。`ANNOTATION_SCALED_FIELDS` 同理不变。
- **`originalspec` / `exportreq`：**标注对象原样透传给 `rendercore`，不按字段校验。
- **后端 schema 校验：**无（对象是 dict，`plan` 按 `type` 分发）。
- **Python 侧类型镜像：**`ExportObject`（`web/src/lib/api.ts`）一份，已同步。
- **缩略图：**导出预览 `ThumbShape` 与画布同一组函数；版本列表的 `timelineThumb` 是画布 2D 速写，无边框与描边不透明度照守，线型 / 端型不画（它从来不画）；`CanvasThumb` 的草图兜底（后端给的 `ThumbObject` 没有这些字段）不变。
- **导出裁切 `exportCrop.strokeOutset`：**按端型大小档位、线端（方头最远 √2/2 线宽）外扩，缺省与旧口径逐字相同。
- **类型切换（`shapeSwitch`）：**`stroke`、`headSizeStart/End` 随类型走（`KIND_FIELDS`，编译期完整性断言），`strokeOpacity` / `lineCap` / `lineJoin` 是共有外观（`SHARED_STYLE_FIELDS`）。无边框跟着封闭形状走，切成直线 / 大括号时丢掉。

### 界面（不撑大原有卡片）

- 无边框开关放进**现有「描边」那一行**，与填充同一个 `EffectToggle`（关着是「＋添加描边」，开着是真开关 + 颜色）；直线 / 大括号没有开关。关掉后线宽 / 线型 / 「更多」收起。不新增行。
- 线型下拉加三项（同一个 `GlyphSelect`）；端型下拉加三项。
- 新的低频项（描边不透明度、线端、拐角；箭头再加起点 / 终点大小）收进一行折叠的**「更多」**（`ui/SummaryRow`，右值写改过几项，默认折起）。线端 / 拐角 / 大小用 `Segmented`。点线时线端置灰并固定圆头。
- 文案一句话、走 i18n（中英）；解释放 `title`。

## 不做（后续）

渐变填充、阴影 / 发光、形状内文字、肘形 / 曲线连接线、四角独立圆角、描边内外对齐（居中 / 内侧 / 外侧）。描边不透明度放进「更多」而不是单独一行，是为了不撑大卡片；如果用户觉得它够常用，可以提到「描边」那一行右侧。

## 看护

- `tests/test_shape_stroke_props.py`：旧文档逐字不变（510 例）、共享向量、每个属性的合同；`tests/test_compose_shape_stroke.py`：经契约层合成 PDF / SVG 后读回内容流（无边框 = 只有填充指令、`/CA`、`J` / `j` / `d` 指令）。
- `web/src/lib/shapeGeometry.golden.test.ts`（共享向量的前端一侧）、`web/src/canvas/shapeStrokeProps.test.tsx`（画布、导出预览、导出载荷、属性栏）、`shapeSwitch.test.ts`、`exportCrop.test.ts`。
- 共享向量：`tests/golden/shape_geometry_vectors.json`（加 `cap` / `join_default` / `head_size_factor` / `arrow`，`dash` 加三种）与 `tests/golden/shape_legacy_paths.json`。
