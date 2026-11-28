# ADR 0189：SVG 互通——导入成可编辑对象、文字转轮廓、复制为 SVG

日期：2026-11-15 · 状态：**Accepted**（2026-11-15 用户拍板：M5 两批审阅页共 11 处全按推荐，含第 6 处「解析和安全边界合一，换算各管各的」。1.0 豁免沿用 ADR 0185 在
`docs/1.0-release-readiness.md` §1.8 的那一行，M5 属其范围；PR 按 risk:high 走）
相关：[0185 画布矢量编辑](0185-vector-editing.md)（§五 M5）、[0191 嵌套组与剪切蒙版](0191-nested-groups-and-clipping-masks.md)、
[0182 SVG 计算图表编辑（外部 RFC #1877，Proposed）](https://github.com/Tavotto/Tavotto/issues/1877)、
[0059 RenderCore 路径编译](0059-render-ir-and-render-plan.md)、[0176 粘贴 / 拖入图片](0176-canvas-file-intake.md)

## 问题

ADR 0185 能力矩阵 E 节「粘贴 / 拖入 SVG 成可编辑对象」（●●●）、「复制为 SVG 到剪贴板」（●●）、「文字转轮廓」（●●）。科研作图的
常见来回：示意图在 Illustrator / Inkscape / BioRender 里画，要和 Tavotto 的图拼在一起改；投稿前要把标注文字转曲；Tavotto 里画的箭头、
框要贴回别的软件。

外部来的 SVG 是**不受信任的输入**（可能是网上下载的、别人发来的）：XXE、十亿笑声、外链、脚本、事件属性、引用环、超大尺寸都要挡住。

## 决定

### 一、只有一个受限解析器：`svgcheck`

导入**只经** `rendercore.svgcheck.parse_svg(…, tolerant=True)`——与静态 SVG 素材（ADR 0176 的粘贴 / 拖入）、写入器内嵌 SVG、产物检视、
以及 ADR 0182 的场景解析同一个受限解析器（标准库 expat + 有界 TreeBuilder）。不另起 XML 解析器。

`tolerant` 是给编辑器导出的 SVG 用的，只多做三件事，然后走与严格模式**完全相同**的验证：

- 删掉**没有内部子集**的公共 SVG DTD 声明（Illustrator 老版本的「另存为」）。带 `[` 的照旧拒绝；
- 删掉编辑器私有命名空间的元素与属性（`inkscape:` / `sodipodi:` / Adobe 的 `i:` `x:`……）以及整段 `metadata`；
- 删掉对画面没有作用的 CSS 声明（`enable-background`、`overflow`、`-inkscape-*`、`line-height`……，表在 `svgcheck._INERT_CSS`）。

安全规则一条不放：DTD / 实体（XXE、十亿笑声）、`<script>` 与一切非静态元素（`a`、`foreignObject`、`set`、`filter`……）、`on*` 事件属性、
`data:` 图片以外的 href、CSS 里的 `url(外部)` / `expression(` / `javascript:`、`data:` 图片超限（字节 / 像素）、引用环与指数展开、深度 /
元素数 / 属性 / 文字预算。导入用比素材更紧的预算 `svgimport.IMPORT_BUDGET`（8 MB、深度 64、5 万元素、`data:` 图片 4 MB / 4000 万像素、
展开 20 万）。

之后的换算再加三道：视口上限（宽高 10 m、viewBox 1e7、非有限数）、墙钟上限（5 秒，逐元素查）、换算时再数一遍展开元素（解析器那一道
漏了也停得下）。接口只回原因码，**不回显**任何输入内容；导入的文字与名字只以 React 文本节点显示，不当 HTML。

### 二、落成什么

解析在后端（`rendercore/svgimport.py`，`POST /api/svg/import`）；前端只做「认出剪贴板里是 SVG 标记」与「落位」（`lib/svgImport`：只收认识的
对象、id 与组 id 整批换新、平移到落点）。不在前端解析，因为量样式（`getComputedStyle`）要把不受信任的 SVG 挂进页面。

- path / rect / circle / ellipse / line / polyline / polygon → 自由路径：变换矩阵烤进锚点，弧换三次贝塞尔（每段 ≤ 90°），二次曲线精确升三次；
  线宽按变换的 √|det| 换成 pt；
- 样式：`<style>` 的类 / id / 标签选择器按优先级、style 属性最高、继承、currentColor；线性 / 径向渐变换成对象框的比例坐标（含 `userSpaceOnUse`、
  `gradientTransform`、href 继承）；
- 结构：两个以上子对象的 `<g>` 成组（ADR 0191 的 `groupPath`），包住全部对象的那一层（写入器翻 y 的根组、Inkscape 图层）不留；`use` / `defs` /
  `symbol` 展开；用户坐标的 `clipPath` 成剪切组；
- `<text>` → 文字对象（字号、颜色、粗斜——含 Illustrator 写在 PostScript 名里的、对齐、旋转），位置按基线估算。

**兜底不出半张图**：受限解析器拒绝的，以及换算时做不准的（蒙版、图案、内嵌图片、textPath、复杂的线端标记（简单三角 / 开口箭头 / 圆点已换成路径端型，ADR 0208 修订）、复杂选择器、比例坐标的剪切、逐字定位 /
斜切的文字）→ `unsupported`（原因码 + 次数），**整个不拆**。粘贴时先当 SVG 素材放（ADR 0176 同一条路）；素材检查也不收、而且不是资源滥用类
（超大 / 超时 / 引用环 / 坏 XML / 脚本）的，按浏览器 `<img>` 模式画出来的样子栅格化成 600 dpi PNG 放（`<img>` 模式不执行脚本、不取外链）；
每种结局说一句原因，素材导入那条路自己的失败原因不被盖掉。

**入口**：粘贴 SVG 标记直接变对象（一次 commit、全选、放页面中间）；**拖进来的 SVG 文件照旧是「图」**（matplotlib 导出的 SVG 多半要当图排版，
「脚本即图片」也靠它）；静态 SVG 图（没有脚本、没裁剪 / 旋转 / 翻转、没锁定）右键或命令面板「拆成可编辑对象」：按图在画布上的大小缩放、
原位替换、留在图原来的组里；做不准就图不动。

### 三、文字转轮廓 ⇧⌘O：字形在后端生成

`rendercore/textoutline.py`（`POST /api/text/outline`）：文字对象按导出的同一条路编成 IR（同一张字体登记表、同一次 HarfBuzz 排字、同一份公式
排版），每个字形的轮廓（fontTools，与 `svgwriter` 画轮廓字同一份 glyph set；二次样条精确升三次）按 IR 的落笔位置、字号、模拟斜体错切、对象旋转
换到页面。前端拿不到导出用的字体文件、浏览器画字用的是系统字体，所以不在前端描字形。同色的字并成一条复合路径；模拟粗体 = 同色描边；底框 / 边框 /
下划线 / 公式原样成路径；不止一条时成一个新组、留在原文字的组里，原位替换、一次 commit。

### 四、复制为 SVG

`POST /api/svg/copy` 用**导出 SVG 的同一个写入器**（`svgwriter`）写选中的标注 / 形状 / 路径 / 文字，页面 = 选区看得见的范围（剪切组按蒙版）。
剪贴板用 `ClipboardItem` 收 Promise（WKWebView 要求在手势里同步调 `write`），认 `image/svg+xml` 就两种都放，否则只放文本；不让写就退到
`writeText`。图（面板）不在这里——它的 SVG 走导出。写出的 SVG 再粘贴回来能完整导入。

接口的输入是导出载荷（JSON），同样当不受信任处理：先按请求字节数（8 MB）在解析 JSON 之前就拒，再数对象（2000）、每个对象的裁剪层数（8，
IR 的嵌套上限是 16 层）、全部路径锚点（20 万）、每段文字的字数（2 万；文字转轮廓同一个字数上限）；页面尺寸 0 < w / h ≤ 5000 mm。载荷里的
字符串只经写入器的 XML 转义落进输出，**输出一定能过严格模式的受限解析器**（没有 DTD / 实体 / 脚本 / 事件 / 外链 / 引用），文字粘回来原样
是文字。上限之内最重的一类输入在有限时间里写完——超时靠上限兜住，不靠杀线程。

### 五、与 ADR 0182（外部 RFC #1877 的 SVG 计算图表）的边界

两边都读 SVG，但要的东西不同，**解析与安全边界合一，换算各管各的**：

| | ADR 0182（#1879 起） | 本 ADR |
| --- | --- | --- |
| 输入 | 自己项目里的图（Figure 配对 / 计算 SVG） | 任意外部 SVG（剪贴板、拆图） |
| 解析与安全 | `svgcheck.parse_svg`（严格） | `svgcheck.parse_svg(tolerant=True)`，同一套规则 + 更紧的预算 |
| 产出 | 只读的场景清单 + RenderCore IR（`svgir`），忠实重画、绑定数据重算 | 画布上可编辑的对象（路径 / 文字 / 组 / 剪切组），做不准就整个不拆 |
| 原件 | 冻结，不改字节 | 用完即弃，结果是文档对象 |

不合成一个换算器的理由：0182 的 `svgir` 要**忠实重画**（遇到渐变 / 蒙版 / 滤镜按格式能力披露或失败，对象不拆开），场景节点 id 要稳定可绑定；
本 ADR 要**拆成能编辑的对象**（变换烤进锚点、样式落到对象字段、组按两个以上子对象才成），两边的输出类型与失败语义都不同，硬合成会让一边的规则
绑住另一边。能共用的已经共用：受限解析器、预算类型、拒绝码。#1879 合入后，路径 `d` / 变换 / 颜色 / CSS 级联这几个纯函数抽成共用模块，两边都调它
（届时修订本 ADR 与 0182）；#1879 在严格模式里把 `overflow` 加进认识的属性，与本 ADR 在 `tolerant` 里把它当无作用声明删掉不冲突。

## 后果

- 安全看护 `tests/test_svgimport_security.py`：XXE（文件 / 参数实体）、十亿笑声、外链（http / file / 相对 / CSS url / data:svg / data:html）、
  use 自引用与互相引用、渐变 href 环、指数展开（含解析器那一道失效时的二道）、超大 width / height / viewBox / 非有限数、超大字节、深度、
  `data:` 图片像素 / 字节超限、脚本与事件属性（script / onload / onclick / javascript: 链接 / CSS expression / foreignObject 里的 HTML / 动画 set）、
  超时；每条都核「不联网」（socket 被替换成当场失败）与「不回显」。复制为 SVG 接口的输入：文字里写 XXE / 实体 / 脚本 / 外链图片 /
  CDATA + onload / use 环，输出照样过严格解析、粘回来是原样的字；颜色 / 填充里写外链 / javascript: / 属性注入不进输出；超大或非有限的页面、
  超大对象、裁剪层数、对象 / 锚点 / 字数、请求字节超限都在写之前拒；顶格输入有限时间写完。前端看护导入的文字与名字只当文本显示（`canvas/TextView.test.tsx`、
  `components/left/layerTree.test.tsx`），资源滥用类不栅格化（`store/svgImportActions.test.tsx`）。
- 同源：后端快照 `tests/golden/svgimport/*.json` 前后端各测一侧；文字转轮廓按像素与导出对拍（`tests/test_text_outline.py`），快照
  `tests/golden/text_outline.json` 共用；复制出的 SVG 往返能完整导入。
- 没做：`mask`、`pattern`、滤镜（当图片 / 位图）；文字的精确基线与 dx / dy；`symbol` 自带 viewBox；组的 `opacity` 乘进每个子对象（重叠处与
  浏览器的透明组不同）；PowerPoint 不认剪贴板里的 SVG 文本。
- 超时的进程边界（2026-11-24 复议，暂维持原决定）：解析与换算仍跑在后端请求线程里，墙钟靠循环里的 `_check_deadline` 主动查，不改成
  「超时就强行结束的子进程」。如实说清它保证什么：**最坏情况被字节 / 元素 / 展开 / 视口上限压成线性**，5 秒墙钟只是第二道、不是硬上限——
  一步之内（一条正则、一次 expat 解析）查不到墙钟，CPython 的 `re` / expat 期间也不放 GIL，那几秒里整个后端都等着。复议时在 8 MB 上限附近
  试了 13 类刁钻输入（样式表 `@` / 注释 / 无括号长串、属性里成串 `url(#` / `rgb(`、长路径数字、长字体名、长空白等），全部在上限内停下，
  机器负载约 100 时最多比 5 秒晚约 3 秒。#2034 评审里同类超线性写法已修过十余处，各有具名用例（`tests/test_svgimport_security.py`
  里 `*_is_linear*` / `*_deadline*` 一组）。**重议条件**：导入或复制为 SVG 的路径上再报一处超线性写法，就不再补检查，改走现有 worker
  （先例：ADR 0182 的 `chartprocess`）用进程超时兜底，并修订本节。
