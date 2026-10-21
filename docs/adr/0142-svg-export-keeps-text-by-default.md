# ADR 0142：SVG 导出默认保留可编辑文字（`text_policy=text`），字形轮廓降为保留退路

日期：2026-10-21 · 状态：**Accepted**（用户 2026-10-21 明确拍板「改成默认保留文字」；1.0 收敛纪律下的**用户授权**能力变更）
相关：[0031 统一导出管线](0031-unified-export-pipeline.md)、[0060 批准字体与 allowlist](0060-font-policy-and-allowlist.md)、
[0072 默认渲染后端切换](0072-default-render-backend-cutover-and-pymupdf-retirement.md)；X03（`svgwriter` 模块 docstring）；
规则全文 `docs/rules/backend/rendercore.md`「SVG 写入」、`export-pipeline.md`「SVG（X03）」。

## 动机

用户要把导出的 SVG 插进 Word / PowerPoint，用「转换为形状」后**直接改文字**。X03 起 SVG 一律把文字画成字形轮廓（`text_policy=outlines`），
转换后只剩路径，字改不了。

## 决策

1. 新增 `text_policy="text"` 并设为 `svgwriter.write_svg` 的**默认值**；`"outlines"` 保留为内部可选值（`write_svg(..., text_policy="outlines")`）。
   不加界面开关：导出请求结构（`exportreq`）与出版规范同源对都不需要变，开关要同时动请求闭集、前后端两侧与设置页，收益只是少数人要像素级稳定时的退路，先不付这个代价。
2. **写法**：每个 shaped run 一个 `<text>`——`transform="matrix(1 0 0 -1 x y)"`（基线原点 + 抬高，局部坐标 y 向下；页面外层的 y 翻转只做一次，组矩阵照旧）、
   `x` 列表逐字符锁字距（取自 IR 的 advance，不靠渲染器自己排）、`font-size` 取 pt（与页面单位一致）、`xml:space="preserve"`、`text-rendering="geometricPrecision"`、
   `font-weight` / `font-style` 取批准脸的粗斜，颜色与透明度仍在外层 `<g>`。
   `font-family` = 批准脸**真实族名**（Liberation Serif / Sans / Mono、Noto Sans SC）+ 度量兼容或平台原生的回退链（Times New Roman、Arial、Courier New、微软雅黑 / 苹方），最后是通用族。
   内容是 cluster 原文还原的真实 Unicode，不是 gid。一个 cluster 多个字符（`fi` 连字）按字符均分该 cluster 的 advance；多字形同一 cluster（连字尾、组合记号）并入前一个 cluster。
3. **逐 run 回退，不让整份导出失败**：首字形无 cluster 原文、`.notdef`、字体不覆盖该字符、私用区 / 控制符 / 非字符码位的 run 退回字形轮廓；
   facts 记 `text_runs` / `text_fallback_runs`，manifest plan 记 `text_policy` 与 `text_fallback_runs`。
4. **mathtext**（上下标、°、⁻¹）：引擎本来就拆成多个 run（`rise` 抬高），每个 run 一个 `<text>`，位置各自正确。合成上下标（画 `5`、原文 `⁵`）写的是画出来的字符，不写 `actual_text`。
5. **导入的 SVG 伴随件含 `<text>`**：text 策略下原样保留（不再报 `svg_source_text_not_outlined`）。ID / CSS 隔离、`svgcheck` 的 DTD / 脚本 / 外链 / 远程字体闸门没变；
   `<text>` 只引用字体**名字**，不带字体文件、不联网，所以没有新增安全面。风险是显示换字体（同下）。`outlines` 策略下仍保持旧的拒收。
6. **核验**：`artifactcheck.check_svg` 读 `<text>` 的 `font-family` 属性 / style / matplotlib 的 `font:` 简写，取列表**第一个**族核字体；有 `<text>` 才给 `font_ok`，
   纯轮廓文件仍如实回 `None`（查不了）并说明。返回里新增 `has_text`。

## 代价（如实告知用户）

* **字体替换**：对方没装同款（或度量兼容）字体时，Office / 浏览器会换字体，字距与换行宽度会变；`x` 列表锁字距，但换了字体后字形宽度不同，字与字之间会出现疏密不均。
* **像素不再与 PDF 逐一致**：`<text>` 由渲染器栅格化与 hint，不同引擎有亚像素差；与 PDF 的逐像素对照改在 `outlines` 策略上做，`text` 策略用「装了同款字体的 Chromium 里与轮廓版墨量 / 平均差」判。
* 仍不承诺 Office 兼容性已验证。
* **`scope=original` 的 matplotlib 图本轮不变**：它用的 SVG 伴随件由 worker 以 `svg.fonttype='path'` 写出，同一份字节还是 #1185「SVG 素材 ↔ 活 Figure 等价证人」的比对基准
  （脚本自己存的 SVG 默认也是 path）；改成 `none` 会让默认脚本的 SVG 素材配对失败。要让原样导出也保留文字，需另给伴随件加一路 text 渲染（worker 协议、池、桥四处同步），另立。
  `_original_svg_result` 的 `text_policy` 因此按文件里**实际有无 `<text>`** 如实记，不预设。画布预览的 `svg.fonttype='path'` 没动。

## 退路

`write_svg(..., text_policy="outlines")` 与旧行为逐字节等价（旧用例原样钉着它）。若 Office 兼容性或字体替换投诉集中，把默认值改回 `outlines` 是一行改动，不需要迁移。
