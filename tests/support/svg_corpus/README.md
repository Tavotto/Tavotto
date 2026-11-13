# SVG 语料（SVG 计算图表编辑测试夹具）

全部文件由 `make_corpus.py` 在本目录生成或手工编写，随仓库提交（自建内容，无第三方版权样本）。
重新生成：`.venv/Scripts/python.exe tests/support/svg_corpus/make_corpus.py`
（matplotlib 输出的 `<dc:date>` 时间戳在生成时剥除，其余字节确定）。

| 文件 | 来源 | 独立原始数据 | 图表含义 |
| --- | --- | --- | --- |
| `line_two_series.svg` | matplotlib（svg.fonttype=none，文字保留） | `line_two_series.data.csv`：x=0..10 整数；growth=x²；decline=100−5x | 线性轴双系列折线图 + 图例；SVG pt 坐标 = 数据经 axes 变换的结果，可按 CSV 独立重算 |
| `line_two_series.data.csv` | 手写常量 | 同上 | 数据真值（不是从图反推） |
| `bar_error.svg` | matplotlib | `bar_error.data.csv`：A–D 的 value 与对称 error | 柱 + 误差棒 + 图例；误差是模型事实（给定 sd），不是从隐藏样本统计 |
| `bar_error.data.csv` | 手写常量 | 同上 | 数据真值 |
| `heatmap_colorbar.svg` | matplotlib（rng seed=42） | `heatmap_colorbar.data.csv`：5×7 矩阵，vmin=0 vmax=1 viridis | 标量场 + 色条；颜色可由 norm+cmap 独立核对 |
| `heatmap_colorbar.data.csv` | 固定种子生成 | 同上 | 数据真值 |
| `text_outlined.svg` | matplotlib（svg.fonttype=path） | 同 growth=x² | 文字转曲样本：断言转曲文字按路径处理、不冒充可编辑字符 |
| `plain_shapes.svg` | 手写 | 注释里写明已知绑定：绘图区 (60,40)–(360,260)，x∈[0,10]→60+30x px，y∈[0,100]→260−2.2y px | 纯图元"图表"（rect 轴框 + 三个数据点 + 轴标签），B 路径绑定流程主夹具 |
| `use_and_groups.svg` | 手写 | — | `<defs>` + 两个 `<use>` 实例 + 嵌套 `<g>` 带变换：结构保留/实例隔离测试 |
| `clip_opacity.svg` | 手写 | — | clipPath 裁剪 + fill-opacity 与组 opacity 两种透明：合成与区域替换反例（SG01） |
| `gradient_image.svg` | 手写 | 内嵌 2×2 PNG data-URI | 渐变 + 嵌入位图：导入边界 |
| `external_danger.svg` | 手写（恶意样本） | — | DTD+ENTITY、`<script>`、外链 `<a>`/`<image>`：只用于断言拒绝，绝不进入白名单 |

许可：全部为本仓库测试资产（AGPL-3.0-only 与仓库一致）。matplotlib 生成部分仅使用默认字体渲染、
不含任何用户数据。
