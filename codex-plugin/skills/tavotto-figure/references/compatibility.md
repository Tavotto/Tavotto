# Tavotto 能改什么、不能改什么

判断标准很实在：Tavotto 把 figure 常驻在内存里，直接 mutate matplotlib 的 artist
再重画。**能被 artist 属性表达的改动就能鼠标改；需要重建图形结构的就得回代码。**

这条界线对三条入口是**同一条**：Codex 里的 MCP 工具（`tavotto_apply_overrides`）、
Codex 内嵌画布、Tavotto 桌面窗口——背后是同一个引擎、同一套 override 语义。

> 可改的属性不用背：`tavotto_open_figure` 回来的 manifest 里，每个元素的
> `editable` 就是它的完整属性表（`prop` / `type` / 当前 `value` / 枚举 `options` /
> 数值 `min`/`max`/`step`）。**照着它发 patch，别猜 prop 名。**

## 鼠标能改（别写进代码里反复调）

| 类别 | 能做的事 |
| --- | --- |
| 文字 | 标题、轴标签、刻度标签、图例条目、图内 text：内容、字号、字体、颜色、粗细斜体、对齐、**拖动位置** |
| 线 | 颜色、线宽、线型、透明度、marker 形状/大小/填充；散点 marker 整体替换 |
| 图例 | 位置（拖）、列数、字号、边框、条目顺序 |
| 刻度 | 朝内/朝外、长度、宽度、标签字号与颜色（x/y/z 三轴） |
| 子图 | 位置与大小（拖、缩放） |
| 箭头 | 脚本里 `add_patch` 的独立箭头：整体拖、拖单个端点、换 arrowstyle/线型 |
| 3D | 视角（elev/azim/roll）、投影方式、轴线/背景面板/网格、轴箭头开关与样式 |
| 画布层 | 多图拼版、加文字/箭头/形状标注、(a)(b) 编号、对齐分布、导出 PDF/PNG |

改完可以「写回原始文件」——Tavotto 会把这些 override 烙进磁盘上的 PDF/PNG，
**你的脚本一个字都不会被动**（写回只在桌面窗口里有，MCP 那边不提供）。

## override 的两条语义（发 patch 前必读）

* **全量列表**：`patches` 是这张图当前的完整修改清单，不是增量。列表里没有的
  `(gid, prop)` 会被自动恢复成脚本原始值——这正是「撤销」的实现方式。
  发增量的后果是：你以为只改了一项，实际上把别的修改全撤销了。
* **形状不合法的条目不会静默丢**：`tavotto_apply_overrides` 的响应里有 `rejected`
  （带 index 与原因，如 `bad_gid` / `non_finite_float`）。元素不存在则出现在
  `warnings` 里（多半是脚本改过了，会话该重开）。两者都要看。

## 出版规范预检查什么

`tavotto_preflight` 按 profile（默认 `lab-publication-v1`）体检，四档：

| 等级 | 含义 | 举例 |
| --- | --- | --- |
| `errors` | **默认阻止导出** | 最终有效字号 ≤ 8pt、低于 8.5pt、位图 <300dpi、越界、缺素材、渲染失败、override 没应用上 |
| `warnings` | 放行但要展示 | 页面比例不符、图例带框、刻度朝外、坐标轴不封闭、线宽不在档位、字体被替代、缺中文 fallback |
| `not_verifiable` | **查不了，需人工确认** | 外部位图内部的文字字号；没有 manifest 的矢量面板 |
| `suggestions` | 建议 | 柱状图没误差棒、拟合没置信带、色系不在推荐表里、多条曲线都没 marker、刻度标签超过 10 个、轴标题不是 `Title (unit)` |

**字号按最终物理尺寸判**：面板缩到 60% 摆上版面时，判据是 `fontsize × 0.6`。
只把脚本里的 `fontsize` 调大而把图缩小，预检照样拦。

## 必须回代码改

* **数据本身**：值、单位换算、拟合、筛选。
* **坐标范围与刻度尺度**：`set_xlim/ylim`、对数坐标、刻度定位器（盒内数据属性刻意不开放，
  改它会让「图与数据不符」变得无法追溯）。
* **图形结构**：加/删一条曲线、加/删子图、`sharex` 关系、colorbar 的方向
  （翻转要销毁重建色条轴，会打乱内部编号）。
* **`annotate()` 的箭头端点**：注释机制每帧重定位，拖完下一帧就弹回——所以它不给端点手柄。
  要挪就在代码里改 `xy`/`xytext`。

遇到这些时**如实告诉用户「这条得回代码改」**，不要造一个看起来能点、实际不持久的
控件或 patch。发一个 manifest 里不存在的 `prop`，worker 会回一条 warning 然后什么都
不发生——那不是「改好了」。

## 写脚本时的注意点（会影响可编辑性）

* **`imshow` 的位图** 只能整体改透明度/位置，像素内容不可编辑；矢量元素照常。
* **`tight_layout()` / `constrained_layout`** 可以用；用户在 Tavotto 里拖动子图后，
  布局以拖动结果为准。
* **中文**：脚本里要显式设中文字体（`font.family` 里加 `Noto Sans CJK SC` /
  `Source Han Sans` / `Microsoft YaHei`），否则导出 PDF 里是方框。
* **`paper_style.py` 是可选的图库方言**，不是 Tavotto 的依赖。用户图库里有就沿用它的
  `save()`；没有就直接 `fig.savefig()`，两条路 Tavotto 都认。
* **一个脚本出多张图**是常态：每张一个独立 stem，注册表会把每个 stem 映射回本脚本。

## 交接失败时的自查顺序

1. 脚本和产物在同一个目录吗？（最常见）
2. 产物名是脚本里的字面量吗？`sys.argv`、时间戳、遍历结果都不行。
3. 入口函数无参数、且模块 import 期没有副作用吗？
4. 同一个 stem 被两个脚本认领了吗？（输出里的 `conflicts`——Tavotto 只报告不裁决，
   要在图库的 `tavotto_registry.json` 里手工指定归属）

## MCP 工具报错时的对照表

| code | 意思 | 怎么办 |
| --- | --- | --- |
| `path_out_of_scope` | 路径不在允许的项目根内 | 用工作目录内的路径，或让用户设 `TAVOTTO_MCP_ROOTS` |
| `no_registry` | 这个目录不是 Tavotto 图库 | 指向含 `tavotto_registry.json` 的那一层；或先交接一次让它生成 |
| `stem_required` | 项目里有多张图 | 带 `stem` 点名（响应里的 `stems` 是候选） |
| `stem_not_parameterizable` | 这张图没有对应脚本 | 把 `.py` 放到产物同目录，产物名写成字面量 |
| `preflight_blocked` | 预检有阻断项 | **先修**；用户明确要求才带 `explicit_confirm: true` |
| `missing_dependency` | 渲染解释器缺包 | 告诉用户装哪个包，或换一个带科学栈的解释器 |
| `tavotto_missing` | 机器上没装 Tavotto | `pipx install tavotto` 或装桌面版，然后重开会话 |
