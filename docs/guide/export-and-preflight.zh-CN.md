# 导出格式、预检等级与出版规范

> 属于 [Tavotto 自述](../../README.zh-CN.md)，这里放 README 指过来的细节。

## 导出与投稿前检查

导出 PDF 时，每张原始矢量图按原样整块嵌入，**文字仍然是可选中、可搜索的真文字**。
另提供 SVG（默认文字保持可编辑）。PNG 与 TIFF（无损，Deflate 压缩，DPI 写进文件）由同一份 PDF 栅格化，位图里的就是
PDF 里的内容。两处明示的例外：图设了小于 1 的透明度、或做了翻转时，该图按导出 DPI
转成位图嵌入——PDF 的矢量内容不支持这两种效果。EPS 由 matplotlib 自己写出，所以只在
按原图尺寸导出一张有脚本的图时可用；画布版面与没有脚本的图给不出 EPS，Tavotto 会
直接说明，而不是把位图裹进 PostScript 冒充矢量。

在写出任何文件之前，Tavotto 会按出版规范 profile 检查一遍，把审稿人三周后才会告诉你的
事情先说出来：

<p align="center">
  <img src="https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/preflight.zh.png" width="82%"
       alt="导出弹窗：整页或逐图导出，PDF 与 PNG 600 ppi，按默认规范的导出前检查显示 1 项留意、3 项无法核验。">
</p>

字号一律按**最终物理尺寸**判——图摆成 60% 时比的是 `fontsize × 0.6`，
不是脚本里写的那个数。结论分四档，在导出弹窗里以标签显示：**要改**（阻断）、**留意**（警告）、**无法核验**和**建议**。没有哪一档会锁住导出按钮；
**无法核验**是真的查不了的那些（比如外部位图内部的文字），需要人来看；
**建议**绝不替你做决定。导出时仍未处理的项（阻断项、无法核验项，或这次没能跑完的检查）会自动记进随成图写出的样式检查报告；检查全部通过时也可以手动打开这份报告。

## 出版规范与预检

规则收在一个带版本的 JSON 文件里
（[`src/tavotto/profiles/publication.json`](../../src/tavotto/profiles/publication.json)），
Python 引擎与 TypeScript 前端读的是同一份，不存在第二份会漂移的副本。

默认的 `lab-publication-v1` 规定：单栏 80 mm / 双栏 150 mm；16:9、4:3、1:1 三种比例；
正文 9 pt，最终有效字号的下限只有一个数——不低于 8 pt（正好 8 pt 通过）；位图 ≥ 300 dpi；
Times New Roman 加一份显式的中日韩字体回退；线宽 0.5 / 0.75 / 1.0 / 1.5 pt；
刻度朝内、四边封闭、图例无边框；坐标轴标签写成 `Title (unit)`；
以及按语义类型选用的 Scientific colour maps。

期刊有自己的栏宽时用覆盖而不是分叉：`{"widths_mm": {"double": 178}}`
——其余全部继承，而且这次覆盖会记进 proof report。
