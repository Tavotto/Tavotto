# ADR 0186：渐变填充与描边——扩 `ir.Paint`

日期：2026-11-15 · 状态：**Accepted**（2026-11-15 用户拍板：待定三项全按推荐；1.0 豁免沿用 ADR 0185 在 `docs/1.0-release-readiness.md` §1.8 的那一行，M3 属其范围）
相关：[0059 RenderCore 路径编译](0059-render-ir-and-render-plan.md)、[0072 渲染闭包零 PyMuPDF](0072-default-render-backend-cutover-and-pymupdf-retirement.md)、
[0174 形状与箭头的描边属性](0174-shape-stroke-properties.md)、[0185 画布矢量编辑](0185-vector-editing.md)

## 问题

ADR 0185 能力矩阵 E 节「线性 / 径向渐变 + 画布渐变柄」（●●●）是 M3 的主项。今天的 `ir.Paint` 只有「一个 RGB + 常量 alpha」，
填充与描边都只能是纯色。科研图里渐变的常见用法：示意图的光照 / 浓度梯度、色标旁的说明块、强调区域的淡出。

## 决定

### 一、IR：`Paint` 扩成和类型（闭集）

```python
@dataclass(frozen=True)
class Paint:                      # 保留现名 = 纯色，旧代码与旧 IR 一字不动
    rgb: RGB
    alpha: float = 1.0

@dataclass(frozen=True)
class GradientStop:
    offset: float                 # 0–1，单调不减
    rgb: RGB
    alpha: float = 1.0

@dataclass(frozen=True)
class LinearGradient:
    stops: tuple[GradientStop, ...]   # ≥ 2
    p0: tuple[float, float]           # 起点、终点：**对象框空间**（与路径段同一坐标，随 Group.transform 走）
    p1: tuple[float, float]
    spread: str = "pad"               # 只做 pad（PDF Extend true true）；reflect / repeat 不做

@dataclass(frozen=True)
class RadialGradient:
    stops: tuple[GradientStop, ...]
    center: tuple[float, float]
    radius: float                     # 只做同心圆（焦点 = 圆心）；椭圆径向用 Group.transform 的缩放表达
    spread: str = "pad"

AnyPaint = Paint | LinearGradient | RadialGradient
```

- `Path.fill` / `Stroke.paint` 的类型换成 `AnyPaint`；`validate()` 加：stops ≥ 2、offset 单调、颜色在 0–1、半径 > 0。
- `operations_of()` 加两个操作 `gradient_fill` / `gradient_stroke`；任一 stop 的 alpha < 1 再记 `object_alpha`（与今天同一条）。
- 渐变的几何（p0 / p1 / center / radius）在**对象框空间**：路径段已经在框空间、`Group.transform` 负责落到页面，渐变跟着一起走，
  旋转对象的渐变天然跟着转——写入器不另算坐标。

### 二、三种写入器怎么画

| 格式 | 线性 | 径向 | alpha | 能力表 |
| --- | --- | --- | --- | --- |
| PDF | Shading Pattern：`/ShadingType 2`（axial），`/Coords [x0 y0 x1 y1]`，`/Extend [true true]`；颜色用 Type 2（两个 stop）/ Type 3（缝合）函数；`/Pattern cs /Pn scn` 再 `f` / `S` | `/ShadingType 3`（radial，两个圆：r0 = 0 同心） | stop 带 alpha：另建一个 luminosity 软蒙版（`/SMask` 的 Shading 灰度版）；全不透明时不建 | full |
| SVG | `<linearGradient gradientUnits="userSpaceOnUse" x1 y1 x2 y2>` + `<stop offset stop-color stop-opacity>`，放进 `<defs>`，id 走写入器现有的 `_name()` 防撞；`fill="url(#…)"` | `<radialGradient … cx cy r>` | `stop-opacity` 原生 | full |
| EPS | Level 3 `shfill`：`<< /ShadingType 2 /ColorSpace /DeviceRGB /Coords […] /Function … /Extend [true true] >>`，先 `clip` 到路径再 `shfill`；描边渐变 = `strokepath clip shfill` | `/ShadingType 3` | **没有**：stop alpha < 1 照今天 EPS 的透明一样抛 `UnsupportedCapability`，走「导出前提醒」（ADR 0174 后续那条：改 PDF / 按不透明导出，`flatten_alpha` 把 stop alpha 压成 1） | full（无 alpha）|

- PNG / TIFF：照今天，从 Canonical PDF 栅格化，不另写。
- 画布：SVG 同一套（`ShapeView` / `PathBody` 的 `fill="url(#…)"`，id 用对象 id 派生，导出预览 `ThumbShape` 同一组）。
- `epswriter.flatten_alpha` 扩到 stop；`lib/epsOpacity` 的判据跟着认渐变 stop 的 alpha（与后端 `operations_of` 对齐）。

### 三、文档模型（前端）

- `fill` / `color` 仍是**字符串**（纯色），不改类型——几乎所有消费者直接读它（ADR 0174「为什么 `stroke: false` 而不是 color 置 null」同一条理由）。
  渐变是**另外两个可选字段**：`fillGradient?: Gradient` / `strokeGradient?: Gradient`，有它时覆盖纯色；纯色仍保留，做旧构建的降级与渐变色标的缺省首色。
- `Gradient = { kind: 'linear' | 'radial'; stops: { offset; color; opacity? }[]; from: [rx, ry]; to: [rx, ry] }`（线性）/ `{ center; radius }`（径向），
  坐标与路径锚点同一套**包围盒比例**，缩放 / 转为路径 / 包围盒重算一起换算。
- 读取只走一个函数（仿 `readPath` / `shapeStrokeOn`）；导出载荷 snake_case `fill_gradient` / `stroke_gradient`；缺省不写不发。
- 旧构建读到带渐变的文档：只认 `fill` / `color`，画成首色纯色——降级可接受，不崩（与 ADR 0174 新端型同一种降级）。

### 四、界面（先截图拍板，再开 PR）

- 「填充」那一行的 `EffectToggle` 后面的色块加一个类型切换（纯色 / 线性 / 径向），不新增行；色标条与角度 / 中心收进「更多」。
- 画布渐变柄**放第二期**（第一期只在属性栏调）：选中有渐变的对象时，覆盖层画起点 → 终点一条线 + 两端圆点、中间色标小方块，拖动走 `beginTxn / endTxn`（与锚点编辑同一套坐标换算）。
- 快捷键：Illustrator 的 G（渐变工具）——**与现有键位冲突要先对一遍**（现在 G 未被占用）。

### 五、看护

- 共享向量 `tests/golden/gradient_vectors.json`：前端 `gradientOf()` 与后端 `geometry.gradient_paint()` 对同一份对象出同一份 IR 渐变（stops / 坐标）。
- pytest 读回：PDF 的 `/ShadingType 2|3`、`/Coords`、`/Extend`；SVG 的 `<linearGradient>` / `<radialGradient>` 与 `fill="url(#…)"`；EPS 的 `shfill`。
- PNG 逐像素：渐变两端取样等于首尾 stop 颜色（容差 2/255）、中点等于插值。
- 旧文档逐字不变（`shape_legacy_paths.json` 那一套）。

## 不做

网格渐变、自由渐变、沿描边的渐变（Illustrator「沿描边应用渐变」）、reflect / repeat、焦点偏移的径向。

## 拍板记录（2026-11-15）

1. EPS 遇到带透明度的 stop：与今天的透明一样**导出前提醒**（改 PDF / 按不透明导出），不静默压成不透明。
2. 文档字段：**两个可选字段** `fillGradient` / `strokeGradient`，旧构建降级成首色纯色、不读坏对象；不把 `fill` 改成联合类型。
3. 画布渐变柄**第二期**做；第一期只有属性栏（类型切换 + 「更多」里的色标、角度 / 中心）。
