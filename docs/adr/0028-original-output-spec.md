# ADR 0028：原图输出规格 —— 「按原图导出」有一份说得出口的定义

状态：**Accepted**
日期：2026-08-29
相关：[0016 诊断 V2 / 几何权威](0016-diagnostics-v2-frontend-state-tracing.md)（`exactPanelRender` 是画布几何的唯一权威，本 ADR 说的是**图自己的规格**，两者不重叠）、
[0013 Runtime Figure 素材](0013-runtime-figure-assets.md)（那条 id 空间没有磁盘原件，图幅在描述符里）、
[0017 显示回退 ≠ 几何权威](0017-display-fallback-vs-geometry-authority.md)（同一条纪律的另一面：能看见的那一份不一定是权威的那一份）、
本轨道文档 [`docs/implementation/product-ux-reliability/`](../implementation/product-ux-reliability/STATUS.md)。

## 裁决摘要

| 问题 | 裁决 |
|---|---|
| 「按原图导出」是什么 | 按**这张图自己的规格**出一份：忽略它在画布上的落位、缩放、页面尺寸/背景/边距与页面裁切；保留图内 edits |
| 谁说了算 | 前端一份服务 `web/src/lib/originalSpec.ts`（决策），后端一个模块 `engine/originalspec.py`（事实）。**决策只有一处** |
| 来源优先级 | ① 这一变体渲染回来的 manifest `size_mm` → ② 文档里那个面板的 `nativeW/nativeH` → ③ `/api/panels` 的 `original_spec` → ④ 明确 fallback |
| 位图密度 | 先按文件格式**解析**（PNG `pHYs` / JPEG JFIF / Exif）；读不到才落到 `ASSUMED_DPI`，并报 `dpi_source: "assumed"` |
| 「不知道」 | 独立一档。`dpi: null` 与 `dpi: 96` 是两个不同的答案；`dpi_source` 有四个取值（`metadata` / `assumed` / `derived` / `unknown`） |
| layout 变换 | 缩放 / 裁剪 / 旋转 / 翻转 / 透明度**不套用**，逐项列进 `ignored`，界面据此说明 |
| 源不可用 | 保留上次已知的规格并标 `stale`，**不报"不知道"**——文档里那份就是上一次同步到的事实 |
| 找不到这张图 | `getOriginalOutputSpec()` 回 `null`。**不发明一张不存在的图** |
| `native_*_mm` | 是 spec 的**投影**，不是第二次计算（后端同一次算出来的） |

---

## 1. 背景：尺寸是在导出那一刻猜出来的

改造前，「这张图有多大」没有出处。`/api/panels` 里是这么算的：

```python
ppi = 600 if ext == ".png" else 300          # ← 唯一的依据是旁边那句注释
native_w_mm = round(probe["px_w"] / ppi * 25.4, 3)
```

注释写着「matplotlib 输出 PNG 为 600ppi；照片等按 300ppi 给个初始物理尺寸」。
这句话对 matplotlib 产的图基本成立，对用户从相机、显微镜、别的软件拿来的位图
则是**一个没有根据的数**。猜错的后果不是"差一点"，是物理尺寸差一倍——而界面上
一个字都不说，用户看不出错在哪一步。

矢量那一档没有猜，但也没有出处：pt 尺寸在这里换算一次，导出时再各自换算。
两处一旦漂移，用户看到的「原图尺寸」和真导出的不是一回事，且没有任何判据
会红。

更根本的是**「按原图导出」这句话本身没有定义**。它要不要套用用户在画布上做的
缩放？裁剪算不算？旋转呢？没有定义就只能在写导出面板那一刻现定，而那时候
定出来的规则不会有第二个人知道。

## 2. 裁决：事实与决策分开，决策只有一处

### 2.1 事实层（后端 `engine/originalspec.py`）

只回答「这个文件自己说了什么」，一个字都不掺画布信息：

```python
{"source_kind": "vector" | "raster",
 "logical_w_mm": …, "logical_h_mm": …,
 "px_w": … | None, "px_h": … | None,
 "dpi": … | None, "dpi_source": "metadata" | "assumed" | "unknown",
 "viewport_pt": [w, h] | None,
 "transparent": True | False | None}
```

密度按格式自己解析（纯标准库）：PNG 的 `pHYs`、JPEG 的 JFIF 密度、以及
JFIF 只给长宽比时 Exif 的 `XResolution` / `YResolution`。

**为什么不用 MuPDF 的 `Pixmap.xres`**：实测（PyMuPDF 1.28.2），没有 `pHYs` 的
PNG 与真的写着 96 dpi 的 PNG，它一律回 `96`。两个不同的答案被压成同一个值，
而「不知道」正是这里最需要区分的那一档——把它合并进 96，界面就再也说不出
「这个数是我们假定的」。

`pHYs` 存的是每米整数像素，300 dpi 落盘后读回来是 299.9994。量化误差的上界是
`0.0254/2 = 0.0127` dpi，所以「离最近的整数不到 0.02」这件事只可能由一个整数
dpi 产生——还原它不是四舍五入的方便，是把编码损失去掉。

`ASSUMED_DPI` 的取值与改造前**逐位相同**（PNG 600 / 其余 300）：老项目里已经
摆好的面板尺寸一个都不变，区别只是现在它说得出自己是假定的。

### 2.2 决策层（前端 `web/src/lib/originalSpec.ts`）

来源优先级是判据的一部分：

| # | 来源 | 什么时候有 | `origin` |
| - | --- | --- | --- |
| 1 | 这一变体渲染回来的 manifest `size_mm` | 可编辑 Figure 已经画过一次 | `render_metadata` |
| 2 | 文档里那个面板的 `nativeW/nativeH` | 面板在文档里 | `document` |
| 3 | `/api/panels` 的 `original_spec` | 素材还在清单里 | `asset` |
| 4 | `FALLBACK_MM`（单栏 80 × 60 mm 占位） | 以上都没有 | `fallback` |

第 1 档在第 2 档之前，是因为**图幅不是派生字段**（`web/AGENTS.md`）：`size_mm`
本身可以被 override 改，权威在这个变体自己渲染回来的 manifest 上，不在磁盘
文件上。第 2 档在第 3 档之前，是因为它就是第 1 档同步下来的那份，而且**它在源
文件消失之后还在**——`source_missing` 时界面要说的是"上次已知的规格"，不是
"不知道"。

第 4 档必然带 `fallback: true`：静默用一个编出来的尺寸导出，比报错更糟。

### 2.3 「按原图导出」的定义

**忽略**：面板在画布上的 x/y（落位）、w/h（缩放）、画布页面尺寸 / 背景 /
边距、页面裁切；以及面板上设的 crop / rotation / flip / opacity——那四样改变的
是"这张图在这版上怎么呈现"，不是这张图本身。

**保留**：图内 edits（overrides / 标注 / 文字 / 样式修改）。

**不做**：无意的上采样或下采样；位图源默认保持源像素网格；矢量源保持矢量
语义（除非用户选的格式就是 PNG）。

**尤其不做**：不因为用户曾把面板缩小，就把字号一起缩小。这是共享规则 §8
点名的那一条，也是本 ADR 存在的主要理由。

被忽略的那些**逐项列进 `spec.ignored`**（固定顺序 `scale` / `crop` /
`rotation` / `flip` / `opacity`），导出面板照此说明。忽略而不说等于骗人；
说了而不忽略等于套用画布缩放。两件事都要做到。

用户显式改了原 Figure 的尺寸（改 `size_mm`）是**图内文档修改**，不是 layout
变换——它经渲染 manifest 回到第 1 档，spec 跟着变。

## 3. 为什么决策层在前端

三条理由：

1. **输入在前端手里**。第 1 档（渲染 manifest）活在 `renderStore`，第 2 档活在
   文档，第 3 档是 `/api/panels` 的一个字段。后端看不到前两档——`size_mm` 的
   权威是"这个面板此刻的那份变体"，而变体是文档状态。
2. **消费方在前端**。快速编辑工作区要即时显示规格，Prompt 12 的导出面板要按它
   构造载荷（导出载荷本来就在 `lib/exportPayload.ts` 里拼）。
3. **后端不需要第二份判断**。它拿到的是显式数字，不是"再算一次"。

代价是它与 `preflight` 一样有两侧代码，但**两侧不是同一件事的两个实现**：
后端报事实、前端做决策。真正的对齐风险只在 `dpi_source` 的取值集合上，两边
各有一份闭集注释指着对方。

## 4. 后果

* `/api/panels` 每项多一个 `original_spec` 块；`native_w_mm` / `native_h_mm`
  改成它的投影（**语义没变，值只在"文件写了密度而它不等于我们的假定"时变**，
  那种情况下改造前的值本来就是错的）。
* `PanelObject` 多一个可选字段 `pxH`（与 `pxW` 成对）。源文件消失之后，原图
  规格还要报得出像素网格。schema 不升版，老文档没有它 = 那一维未知。
* `pdfbackend.probe_asset()` 的 raster 结果多一个 `alpha`。
* 判定「这张图有多大」的地方从三处（`/api/panels` 的两个分支 + 导出）收成一处。

## 5. 明确不在本 ADR 范围内

* **导出管线本身**（Prompt 12）：本 ADR 只给规格，不给写文件的那一步。
* **画布导出**：那一条忠实于画布，与原图规格无关。
* **规范 / Spec 层**（Prompt 10）：最小字号、栏宽这些是"这张图该满足什么要求"，
  与"这张图有多大"是两件事。
* **runtime 素材的密度**：它没有磁盘原件，`size_mm` 就是全部；像素网格由导出
  DPI 决定。
