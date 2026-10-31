# ADR 0144：SVG 导出默认保留可编辑文字（`text_policy=text`），字形轮廓降为保留退路

日期：2026-10-21 · 状态：**Accepted**（用户 2026-10-21 明确拍板「改成默认保留文字」；1.0 收敛纪律下的**用户授权**能力变更）
相关：[0031 统一导出管线](0031-unified-export-pipeline.md)、[0060 批准字体与 allowlist](0060-font-policy-and-allowlist.md)、
[0072 默认渲染后端切换](0072-default-render-backend-cutover-and-pymupdf-retirement.md)；X03（`svgwriter` 模块 docstring）；
规则全文 `docs/rules/backend/rendercore.md`「SVG 写入」、`export-pipeline.md`「SVG（X03）」。

> 2026-10-23 补注（[ADR 0150](0150-pdf-svg-pair-from-one-draw.md)）：Figure 面板的 PDF / SVG（含本 ADR 的 text 变体）现在出自**同一次 draw**，不再是「PDF 之后再画两遍 SVG」；text 变体与 path 基准的语义不变。

## 动机

用户要把导出的 SVG 插进 Word / PowerPoint，用「转换为形状」后**直接改文字**。X03 起 SVG 一律把文字画成字形轮廓（`text_policy=outlines`），
转换后只剩路径，字改不了。

## 决策

1. 新增 `text_policy="text"` 并设为 `svgwriter.write_svg` 的**默认值**；`"outlines"` 保留为内部可选值（`write_svg(..., text_policy="outlines")`）。
   不加界面开关：导出请求结构（`exportreq`）与出版规范同源对都不需要变，开关要同时动请求闭集、前后端两侧与设置页，收益只是少数人要像素级稳定时的退路，先不付这个代价。
2. **写法**：每个 shaped run 一个 `<text>`——`transform="matrix(1 0 0 -1 x y)"`（基线原点 + 抬高，局部坐标 y 向下；页面外层的 y 翻转只做一次，组矩阵照旧）、
   每个 `<text>` 只写一个起点 `x`（不写逐字形 x 列表，见「取舍」）、`font-size` 取 pt（与页面单位一致）、`xml:space="preserve"`、`text-rendering="geometricPrecision"`、
   `font-weight` / `font-style` 取批准脸的粗斜，颜色与透明度仍在外层 `<g>`。
   `font-family` = 批准脸**真实族名**（Liberation Serif / Sans / Mono、Noto Sans SC）+ 度量兼容或平台原生的回退链（Times New Roman、Arial、Courier New、微软雅黑 / 苹方），最后是通用族。
   内容是 cluster 原文还原的真实 Unicode，不是 gid。**一个 run 只写一个起点 `x`**（`y` 只在某个字形真有竖直偏移时才逐字符写）。
   取舍（用户在真实 Word 里测了首版）：首版写过逐字形 `x` 列表锁字距，Word「转换为形状」按每个显式 x 切成独立文本段，字母间出现大间距、每个字后带段落标记、中文与上下标被逐字打散，编辑体验反而更差。
   所以字距交给渲染器：装了同一张批准脸（或度量兼容的 Times New Roman / Arial）时就是脸自己的 advance。`textLength` + `lengthAdjust="spacing"` 没有加：Word 对它的处理没在真机验证过，拿不准是否会再次拆字，不加。
3. **逐 run 回退，不让整份导出失败**：首字形无 cluster 原文、`.notdef`、字体不覆盖该字符、私用区 / 控制符 / 非字符码位的 run 退回字形轮廓；
   facts 记 `text_runs` / `text_fallback_runs`，manifest plan 记 `text_policy` 与 `text_fallback_runs`。
4. **mathtext**（上下标、°、⁻¹）：引擎本来就拆成多个 run（`rise` 抬高），每个 run 一个 `<text>`，位置各自正确。合成上下标（画 `5`、原文 `⁵`）写的是画出来的字符，不写 `actual_text`。
5. **导入的 SVG 伴随件含 `<text>`**：text 策略下原样保留（不再报 `svg_source_text_not_outlined`）。ID / CSS 隔离、`svgcheck` 的 DTD / 脚本 / 外链 / 远程字体闸门没变；
   `<text>` 只引用字体**名字**，不带字体文件、不联网，所以没有新增安全面。风险是显示换字体（同下）。`outlines` 策略下仍保持旧的拒收。
6. **核验**：`artifactcheck.check_svg` 读 `<text>` 的 `font-family` 属性 / style / matplotlib 的 `font:` 简写，取列表**第一个**族核字体；有 `<text>` 才给 `font_ok`，
   纯轮廓文件仍如实回 `None`（查不了）并说明。返回里新增 `has_text`。

## 代价（如实告知用户）

* **字体替换**：对方没装同款（或度量兼容）字体时，Office / 浏览器会换字体，字距与换行宽度会变；不再用 `x` 列表锁字距（见上），所以整行宽度跟着换后的字体走，与 PDF 的行宽不再逐一致。
* **像素不再与 PDF 逐一致**：`<text>` 由渲染器栅格化与 hint，不同引擎有亚像素差；与 PDF 的逐像素对照改在 `outlines` 策略上做，`text` 策略用「装了同款字体的 Chromium 里与轮廓版墨量 / 平均差」判。
* 仍不承诺 Office 兼容性已验证。
* **`scope=original` 的 matplotlib 图也默认保留文字（用户 2026-10-21 追加裁决）**：
  worker 协议加两个可选字段，走**独立的一路** `svg.fonttype='none'`，只写成品，**不碰**画布预览的 `path` 与 #1185「SVG 素材 ↔ 活 Figure 等价证人」用的 path 基准字节（两份字节各管各的，text 版从不当比对基准）：
  * `svg_fonttype: "none" | "path"`（`export`，仅 `format=svg`）：单格式原样 SVG（活 Figure 直出）用它。
  * `svg_text_path: str`（`export`，要带 `svg_path`、不与 `snapshot_only` 同用）：PDF + SVG 配对导出时，同一次 owner-thread 命令、同一份布局（引擎钉成 none）里，
    在 path 伴随件之后再写一份文字版，应答多一个 `svg_text: {path, sha256, size_bytes}`（失败只记 `svg_text_error`，不牵连 PDF 与伴随件）。
  * 加字段协议：未知键被旧 worker 静默忽略，应答里就没有 `svg_text`；父进程只对 `export` 签名里**有**这两个参数的 worker 才带上（`_supported_export_kwargs`），否则整体回退为 path 版，
    `_original_svg_result` 按文件里实际有无 `<text>` 记 `text_policy`，不预设。
  * `FrozenSource.svg_text`（同回执 / generation / patch_hash 才认）只在**当次执行**的导出里有；已存快照（svgstore）不带它，导出时回退为 path 版——存储格式没动。
    画布里的 live Figure 面板同走这一份：`rendercore/job.py` 读到 `svg_text` 就以 `imported_text_svgs` 交给 `svgwriter`（身份仍由 path 基准的 hash 钉住，文字版解析失败 / 超读取预算 / 旧 worker / 已存快照一律回退 path 版）。
  * `svgfonts.extend_matplotlib_families` 只改标签里的 `style` / `font-family` 属性值，不碰文本节点。
  * 字体族：批准族名（Liberation Serif / Sans / Mono、Noto Sans SC）补上与 `svgwriter` 同一条回退链（共用 `rendercore/svgfonts.py`），用户脚本自己指定的字体按 matplotlib 输出原样保留。
  * matplotlib 的 `svg.fonttype=none` 本来就是每段文字一个 x，不触发 Word 的逐字拆分问题。

## Word 实测（2026-10-21，用户在 macOS Word 上「转换为形状」）

* 普通文字（标题、轴标签、`25 °C`）转换后是整段文本框，可直接改字。逐字形 `x` 列表那一版会被 Word 逐字拆开（字间大空隙、每字一个段落标记），因此改为每个 run 一个起点 x。
* **已知限制，用户裁决保持现状**：带公式的文字（mathtext 上下标、`$k=0.125\ \mathrm{min^{-1}}$`）转换后会拆成多个碎片——画布导出按 run 拆，
  matplotlib 原样导出的 mathtext 是逐字形 `<text>`，Word 摆放会挤成一团。不转换、直接插入时显示正确。用户选择公式也保留文字（可改），不按公式转轮廓。
* Word 深色模式下文字颜色会被 Word 自动调浅 / 调白，是 Word 的显示行为；浅色模式与打印为原色。

## 退路

`write_svg(..., text_policy="outlines")` 与旧行为逐字节等价（旧用例原样钉着它）。若 Office 兼容性或字体替换投诉集中，把默认值改回 `outlines` 是一行改动，不需要迁移。
