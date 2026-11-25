# ADR 0171：导出里可以同时要一份修改后的 .py，原脚本不动

日期：2026-11-06 · 状态：**Accepted**（用户 2026-11-06：「用户希望能够有导出修改后的 py 文件的功能，调研一下难度做一个吧，在导出界面增加一个导出修改后的 py 文件的修改框就可以」；同日看过评审页后：「plt show 和字体修改都做了吧」；1.0 豁免见 `docs/1.0-release-readiness.md` §1.8）
相关：[0094 写回脚本](0094-script-writeback.md)（形态与生成器的来源，仍是 Proposed，本 ADR 不实施它）、[0031 统一导出管线](0031-unified-export-pipeline.md)、[0049 写回像素门](0049-write-back-pixel-verification.md)、[0143 影子脚本](0143-convert-to-editable.md)、[0083 override 的目标身份](0083-override-target-identity.md)。

## 问题

用户在 Tavotto 里调好的样子只存在项目文档里。交给合作者、投稿附脚本、或者换台电脑跑 `python fig.py`，都拿不到这些修改。

ADR 0094 设计过「写回原脚本」，但它改的是用户文件，所以要两处备份、前滚日志、确认令牌、写回后清 override、恢复界面，五个 PR，一直停在 Proposed。

## 决定

**导出一份新文件，不写回。** 导出对话框的格式行多一项 `PY`，产物 `<文件名>_edited.py` 落在导出目录，**原脚本一个字节不动**（`never-modify-user-scripts` 的硬规则不放松）。

因此 0094 里只保留两样：钩子块形态（§二方案 A）和验证纪律（§五）。备份、日志、令牌、清 override、恢复都不需要：没有东西被覆盖。

### 生成

`engine/scriptadjust.py`（纯标准库，Flask 父进程）把 override 翻译成顶部 import 段之后的一段代码：脚本调用 `savefig` 的那一刻，把调整作用到对应的图上。只用 matplotlib 公开 API，不 import Tavotto。与 0094 的原型相比：

- 块的代码一律 ASCII，任何 PEP 263 编码都放得下，不需要第二套模板；用户的值按文件编码能写就原样写，写不下才转义。
- 只带这次用得到的辅助函数，一条改色不带 40 行图例重建。
- 块排在 shebang 与编码声明之后（它们只在头两行才算数）。
- 脚本里已经有块（用户换回导出版继续改）时新块排在其后，两段都生效。

- 字体：与引擎同一条族链（用户选的字体 + 本机回退尾巴，尾巴经 manifest 的 `font_fallback_tail` 拿到，含探测到的中日韩字体），公式字体同步切到 custom 字体集；「中英两格」的选择值也支持。
- `plt.show()`：没有 savefig 的图（`capture_source=pyplot`）块多挂一个 `plt.show` 钩子（`_watch_show`，只在需要时才带进块，普通脚本不多 import pyplot）。这种图没有文件名，按引擎给 stem 的同一顺序（第几张没存盘的图）认。推翻 0094 Q11 的「v1 不支持」。既不保存也不显示的图没有生效时机，`script_export_no_savefig`。
- `bbox_inches="tight"`：块在 savefig 那一刻改图，tight 若按改后的样子重裁，改字号就会改图幅，与界面不一致（Tavotto 的图幅在脚本跑完时定下）。这个框只有格式自己的 renderer 量得准，读它要私有 API（0094 Q10 不许），所以把界面上的框写进块（manifest `frame.savefig_in`，英寸），`savefig` 时只替换 `tight`。代价：导出之后再改脚本的数据，这个框不会跟着变；删掉 `_install` 最后一个参数就恢复自动裁切。同一个框也是挪位置的基准：引擎的 `pos_frac` / `loc_frac` 是**这一页**里的比例（ADR 0098），块的 `_disp` 在有框时按框换算（与 `pathgeom.frac_to_display` 同口径），不按整张 figsize。

`WRITES` 与 `NOT_WRITABLE` 合起来覆盖 `overrides.HANDLERS` 的每个键，新键忘了表态用例就红。初版写得进 105 个键，155 个明说不写（2026-11-16 起逐族补写，见文末「修订」）：`WRITES` 里的键都在 `tests/support/invariant_figures.py` 上逐键过了验证，普查没过的（单根柱、图例条目显隐、隐藏子图、刻度线宽）移进 `NOT_WRITABLE`，靠 `script_report.skipped` 如实告诉用户。

### 验证

`app._verify_edited_script`：一次性 worker 跑**新脚本**，再应用没写进去的 override，结果与热态比 `_compare_manifests`（几何）+ `_replay_pixel_diff`（像素），**与写回同一把尺**。证明的命题是「新脚本 + 没写进去的调整 == 用户看到的图」。

- 字节留在导出临时目录，经影子脚本运行（`__file__`、cwd 都是原脚本的），项目里不出现任何文件。
- 用隔离重放模式（`writetripwire.isolate_entries`）让重放碰不到原脚本，且不装授权根绊线；`pool` / `execspec` 不用改。
- 不过 → `script_export_diverged`，这份 .py 不交付，图片格式照常（`partial`）。**不做自动二分**：宁可不给。

### 接入导出

`ExportRequest.include_script`（线上键 `include_edited_script`）。产物是 `outputs[]` 里 `format: "script"` 的一项，不进 `FORMATS`：它不是图片格式，不走分辨率、产物检查与文件名同源对。覆盖策略、预留、原子发布、逐项失败全部沿用 `exportjob`。可以只要脚本（`formats` 为空）。

后缀固定 `_edited`，但这保证不了名字不同（脚本可能本来就叫这个）：`_produce_edited_script` 按「是不是同一个文件」判，是就 `script_export_same_file`，绝不覆盖。

只对单张图：`scope=canvas` 与多页 PDF 是版面，不是哪个脚本画的，`offers.script` 报不可用，选项禁用并说原因。「每张图单独导出」时每张各一份。

每次要多跑一遍脚本，所以不记进导出默认值。

### 界面

格式行的第六项 `PY`，不新开一行、不撑大卡片（用户 2026-11-06 驳回了单开一行 + 多句说明的第一版）。不可用时禁用，原因在 `title`。

`Output.script_report`：写进去几条、哪些没写进去（`skipped`）。全写进去了界面什么都不说。有没写进去的：桌面版胶囊说条数，不为它开结果块；浏览器版（本来就要结果块放下载链接）摘要行说条数，详情一行列名字。

## 不做 / 已知边界

- `from matplotlib.pyplot import show` 在块之前绑定了原函数，钩子挂不上：验证拦下，不交付。
- **依赖相对路径的脚本**：导出的文件要放回原脚本旁边运行，不改写脚本里的路径。
- **组合才出现的差异**：单键都等价，极端组合可能不等价（实测：翻转坐标轴 + 改图像 + 改色条标签 va 三者同时；3D 图所有字段一次全改；对数轴 + 改刻度字体）。验证拦下，不交付。
- **无 label 对象按位置定位**（与 0083 同一条边界）：用户之后改脚本结构可能落错。
- 导出按 600 dpi 重画、脚本 savefig 用默认 dpi：矢量里坐标框直线吸附有轻微差异（抗锯齿边缘约 20/255），不是块的问题。
- MCP / Codex 插件不暴露这个选项；要的话以后再加。

## 与 0094 的关系

0094 保持 Proposed。将来真要写回，生成器可以直接用这一份，缺的是事务那一半。

## 修订（2026-11-16）：补写跳过的参数，逐族一个 PR

来信 TVM-VP1Q（Windows 0.21.2）：「在 tavotto 中修改的参数，在导出的 .py 文件似乎没有被修改」。用户 2026-11-16 拍板：
按推荐把常用的六族补进 `WRITES`，1.0 豁免沿用本 ADR 那一条（「按推荐来，豁免给，①到⑥都做」）。顺序：
① 图例 → ② 网格与边框 → ③ 刻度定位与格式 → ④ 色条 → ⑤ 误差棒 / 单根柱 / 茎叶 / 散点 → ⑥ 文字描边与底框、箭头。
图幅 `size_mm` 另行评估；色条 `orientation` / `extend`、3D `axis_arrows` 一族只能靠私有 API，仍然永远不写。

**验收口径收紧（随 ① 落地，之后每族沿用）**：

- `test_every_writable_key_is_swept_or_named`：`WRITES` 的每个键要么在某张夹具图上**真被写进了脚本**并过了验证
  （被 `_Unsupported` 挡下留作重放的不算），要么在 `_DEDICATED` 里点名证明它的专用用例。补这条时当场查出
  `visible` 一族、`xlim` / `ylim`、`xscale` / `yscale` 从没被验证过——现在由 `test_held_out_fields_replay_identically`
  按组单独扫（对数轴用 `InvScale`），全部等价。
- 结构化字段（pair / order）`_sample_value` 挑不出值，在 `_STRUCTURED` 里按 prop 给采样；使能项沿用
  `overridesample._ENABLERS`。
- 导出脚本自己要的夹具图只追加在 `tests/support/scriptadjust_figures.py`（`invariant_figures` 别的用例按编号写死）。
- 定位单键：`TAVOTTO_SWEEP_ONLY=legend.ncol,axes.grid_color pytest tests/test_script_adjust_sweep.py -k replays`。

**① 图例**：标题 / 标题字号（脚本原样有标题时；没有时图例里凭空多一个元素）、圆角、锚点、列数、四种间距、
示意线五种样式与「断开跟随」写进脚本。

- 构建期参数（字号、列数、间距）合成**一条** `_legend_rebuild`：从图例条目收集自的源对象重建一次
  `ax.legend(...)`，其余样子（边框、标题、文字的字体颜色）从脚本原样的图例搬过来；列数读不到公开 API，取 manifest。
  锚点表达不出来（manifest 不发 `loc_anchor`）、或脚本给的是坐标 loc 又没拖动时不写——重建会把它弄丢。
- 先后（`_rank`）：刻度类型 → 重建 → 锚点与「断开」→ 普通属性 → 示意线样式。锚点排在拖动之前（拖动清锚框，与位置
  模型的优先级一致）；断开排在源对象改样式之前，`_with_legend` 跳过断开的项（引擎里断开的项 = 脚本原样 + handle_*）。
- 误差棒 / 茎叶的条目一格画好几条，引擎把颜色落到整格每个 artist 上，公开 API 够不着那一格：这两种的 handle_* 留给重放。
- **条目顺序、条目显隐仍不写**：脚本重建出来的图例按显示顺序编号，引擎的条目模型按脚本原样序号认、隐藏项留在编号里，
  验证逐项比对必然对不上（这就是初版普查里「图例条目显隐」没过的原因）。
- 写进脚本的条数按 patch 计（`script_report.written`），一条重建语句合并了几条 patch 就算几条。


**② 网格与边框**：网格四个样式（颜色 / 线型 / 线宽 / 透明度）、刻度线四边开关、边框「全部」档的颜色与线宽、3D 轴的网格总开关
（`grid_visible`）写进脚本。十一个键都等价，没有留下的。

- 网格样式与引擎同一句公开 API：`ax.tick_params(axis='both', which='both', grid_*=...)`。网格没开时先存着，之后 `grid_x` /
  `grid_y` 打开就是这个样子，两类键写的是不同的 kw，互不覆盖、先后无关。
- 刻度线四边：`tick_params(axis=..., which='both', bottom/top/left/right=...)`（界面开关的口径，主次两档一起）。
- 边框「全部」是广播键，一次改 `ax.spines` 的每一条（含不在四边里的）；四边各自的键是窄条目，**窄的盖广播**（引擎
  `spinemodel`：自己的设定 > 「全部」 > 脚本原样）。`_rank` 新增一档：广播排在普通属性（含窄条目）之前，列表里谁先谁后结果都一样。
  广播写不进脚本留作重放时，同一 axes 上四边各自的颜色 / 线宽也一起留下（`_narrower`；`ALIAS_GROUPS` 不登记边框这一族，
  所以脚本侧自己记这张表）。
- 3D 轴的 role 是 `axes3d`，原先没映射进 `_ROLE_CLS`，3D 的任何键都写不进；现在映射到 `axes`，但 3D 的 `elev` / `azim` /
  `axis_arrows` / 面板等仍在 `NOT_WRITABLE`，实际多写进去的只有 `grid_visible`。
- 验收：新夹具图 `AdjGrid`（只开 x 网格，y 网格靠扫描翻开，样式才看得出）、`AdjGrid3D`。整图扫描里所有颜色都采成同一个
  `#123456`、四条边同时被关，网格与边框样式在那里是隐形的（变异实测：把网格线宽 / 透明度 / 线型改成常量扫描仍然绿），
  所以网格样式给了可见的专用采样值，边框颜色 / 线宽拆成「全部」「四边」两个 `_HELD_OUT` 组单独扫（合在一起四边各自的设定会把
  「全部」整个盖住）。

**③ 刻度**：标签显隐（引擎只动标签，不动刻度线）、主刻度线宽、次刻度长度 / 线宽，以及定位与格式（`major_mode` / `major_step` /
`major_values` / `minor_visible` / `minor_mode` / `minor_step` / `format` / `minor_format`）、单条刻度文字，13 个键全部写进脚本。

- 引擎的刻度是模型（`tickmodel`：写进 cfg 再整体重建，没表态 = 脚本原样），所以同一条轴上这八条合成**一次**
  `_tick_model(ax, 'x', {只带改过的几条})`：用公开的 `MultipleLocator` / `FixedLocator` / `AutoMinorLocator`（对数类
  刻度各用自己那款）/ `NullLocator` 与 `FormatStrFormatter` / `NullFormatter` / `ScalarFormatter`，没表态的键不碰脚本原有的
  locator / formatter。「固定刻度没给值」「步长没给」取调用那一刻脚本原样的刻度（与引擎锚到 `orig_major_locator` 同义）。
- 单条刻度文字 = `_tick_text`：把那一刻的刻度位置冻成 `FixedLocator`，文字取当前 formatter 的输出再换掉这一条
  （与 `set_xticks(locs, texts)` 同结果，但不动视区，用不着事后还原）。同一条轴上几条文字依次落，结果与引擎一次盖全相同。
- 先后（`_rank`，与 `overrides._apply_rank` 在 worker 解释器里对账，`test_script_order_matches_the_engines_apply_order`）：
  刻度类型 → 边框 / 色条广播 → 坐标范围等普通属性 → 刻度定位与格式 → 单条刻度文字。刻度定位和冻结都按落下之后的视区现算，所以必须排在坐标范围之后。
- **整页 / 子图大小留给重放时，靠落笔那一刻刻度算的几条也留给重放**（单条文字、固定刻度没给值、固定间隔没给步长；
  同一条轴上的其余定位 / 格式跟着留下）：图幅、子图位置、色条方向与延伸（`NOT_WRITABLE`）在引擎里排在刻度之前，
  自动刻度的个数又跟轴长有关，两边在不同的轴长上各算各的，实测 `script_export_diverged`、整份不交付。
- 刻度组的标签全关掉之后量不出几何、manifest 把它丢了，翻译时按 gid 形状认它是刻度组（否则「标签显隐」永远写不进去）。
- 3D 轴不写两条：刻度线宽（每次 draw 被私有的 `_axinfo` 盖回，公开 API 留不住）、单条刻度文字（曲面图 Inv3DS 的 y 轴
  热态 5 条标签、脚本重放 9 条，位置错开；Inv3D / Inv3DP 上是等价的）。定位与格式在 3D 的 x / y 轴上逐键验过等价，z 轴本来就写不进。

**④ 色条**：标签、刻度字号 / 颜色、轮廓显隐 / 线宽、色条显隐、cmap / vmin / vmax 写进脚本；`orientation` / `extend` 只能靠私有 API，
永远不写（留在 `NOT_WRITABLE`，由 Tavotto 在重放时作用，重放时标签搬去新的长轴）。

- 色条轴就是 `axes_i.colorbar` 的 `axes_i`（`_ax(fig, i)`），刻度 / 轮廓 / 显隐直接落在它上面（`cb.outline` 即 `ax.spines['outline']`），
  不需要 `Colorbar` 对象，所以同一个 mappable 的两条色条（`InvCbar2`）、独立 `ScalarMappable` 的色条（`Inv3D`）都照样写。
  标签落在脚本原样方向的长轴上（manifest 的 `orientation.value_original`），多宿主色条不发这个字段，方向认不准就不写。
- cmap / vmin / vmax 与 mappable 是同一份状态，语句写在 mappable 上（manifest 的 `mappable_gid`）。没有 `mappable_gid`（独立
  `ScalarMappable`，公开 API 够不着它）、`mappable_gid` 指的是反解位图（该图元自己不发 cmap / vmin / vmax）、色条带 `scale_gids`
  （色阶兄弟的 cmap 引擎逐个换，写不出「谁是兄弟」；vmin / vmax 走共用 norm，照写）的，那一项留作重放。
- 先后与引擎同序：色条的刻度字号 / 颜色是广播，色条轴刻度组自己的 `fontsize` / `color`（窄）盖它；色条的 vmin / vmax 是广播，
  图像自己的（窄）盖它——`_rank` 的广播档，原先的 `_NARROWER` 表换成函数 `_narrower()`（按 patch 算被盖住的 `(gid, prop)`，边框、色条刻度、
  色条 vmin / vmax 三种）。cmap 是色条与图像互相广播（ADR 0135，文档序靠后的赢），脚本里按列表序落；一条留作重放时，同一个
  mappable 的另一条 cmap 也一起留下（重放永远排在脚本之后）。
- 色条与图像的 cmap 写出来是同一句话：`_translate` 以前按语句去重，A、B、A 会丢掉第二个 A，现在只合并图例重建 / 脱开。
- **带标签的色条藏起来不写**（实测）：热态里标签在藏起来之前排过位置，重放里轴一开始就不可见、轴不画标签就没排过位置，几何
  （`ylabel` 的 bbox / anchor）对不上，画面是一样的。验证不放松，这一种留作重放；没有标签的照写。
- 验收：整图扫描加了 `InvCbar2` / `InvCbarLC` / `InvShared`；色条的标签、刻度、vmin / vmax、cmap、轮廓显隐各拆成 `_HELD_OUT`
  一组（整图扫描里它们被同一元素上的窄条目 / 同值采样盖住，隐形），逐键反证：把翻译写成常量，对应组变红。`_rank` 的广播档、
  色条 vmin / vmax 的广播档各做了一次变异，组合用例（两种列表顺序）变红。

**⑤ 系列**：误差棒（颜色 / 线宽 / 横杠长度与粗细 / 透明度 / 显隐）、茎叶（颜色 / 线宽 / 线型 / 标记与大小 / 透明度 / 显隐 / 层级 / 名字）、
单根柱（面色 / 边色 / 线宽 / 透明度 / 显隐 / 花纹三项）、柱系列（花纹线宽与颜色 / 柱宽 / 名字）、散点与集合（名字 / 线型 / 花纹 /
大小 / 标记 / 色图与范围）、形状（花纹线宽与颜色 / 填充开关）、曲线名字写进脚本。只有渐变色带的两端色（`collection.gradient`）
留着：要认出一摞逐条一色的长条再带着图例色块重新配色，没有一句公开 API；单根柱的 `linestyle` / `fill` / `zorder` 界面从不发，
没有可采样的字段，不写。

- **容器**：误差棒与茎叶是 `ax.containers[j]`（gid 的序号与柱系列同一个口径），成员取 `container.lines` / `markerline` / `stemlines`
  （公开属性），一条 override 落到成员上的写法与引擎 `SeriesGroup.members()` 同一批：误差棒改色连横杠的 marker 边色一起改，
  线宽只写数据线与竖线，横杠长度 / 粗细是 `markersize` / `markeredgewidth`；茎叶的线宽 / 线型只写茎、标记与大小只写标记，零线不动。
- **图例里跟着源走的示意线**：引擎对 `follow_source` 的项是「从源重新派生」。柱系列与单根柱走 `_with_legend`；第一根柱是柱系列图例色块的
  来源（`HandlerPatch` 取首个子 artist），所以单根柱的 0 号包一层 `_FirstBar`、把系列的 label 借给它，改它图例也跟着变。
  误差棒 / 茎叶一格画好几条、散点示意线的大小按 `markerscale` 与 `scatterpoints` 现算，公开 API 就地改不了：这几种改动追加一条
  `_legend_rebuild`（同一份整份重排，`_refresh`），这条源对象修改排在重排之前（0.5 档），重排从已改好的源重新派生。
  示意线已脱开跟随（custom）的源对象修改不进这一档、照普通属性排在重排之后：重排从脚本原样的源收集，脱开的那一格才停在原样
  （实测：新夹具 `AdjSeries` 整图扫描的第一版把茎的修改一律排在重排之前，脱开的示意线被换成新颜色，不等价）。
  同一个图例里还有别的项已脱开、或锚点读不到时不写，这条修改留给重放。
- **广播与窄条目**：柱系列的八个样式键是广播，单根柱同名键是窄条目，窄的盖广播（`ALIAS_GROUPS`）。`_rank` 的广播档在原有的边框总颜色 /
  总线宽之外把这八个键也收进来；广播留作重放时单根柱同名键本来就一起留下（`_translate` 里「gid 是它子孙」那条）。
- **改名（`label`）最后落**：曲线按原 label 认（`_line`）、图例色块按 label 对（`_with_legend`），改名放在所有语句之后。引擎里
  改名不动图例文字（图例项的文字是另一个元素），脚本这边同样只改对象的 label。
  同一张图的所有改名合成一条语句（`_rename`）：先把每个对象按原 label / 位置认全、再统一改，否则 A→B、B→C 的第二条会撞上刚改好的那条。
  对象上（或子孙 gid 上）还有留作重放的修改时，改名也留作重放：拖动带的 `identity` 按改名前的 label 算，改名写进脚本后重放认不出它，
  引擎拒绝这条修改、整份不交付。
  语句里有用户写的文字（改名、标题、图例 / 刻度文字），所以**不从语句文本里切东西、也不按语句文本判档**：合并改名从 patch 与 `_selector` 现拼，
  档位由 `_kind(cls, prop)` 在生成语句时定下（`_rank` 吃标记，不吃文本）。
- **散点色图**：`cmap` 写 `set_cmap`，`vmin` / `vmax` 是 `set_clim`。有色阶兄弟（`scale_gids`，共用同一份 norm 的别的图元）的 `cmap` 与 ④
  色条一侧同一条规矩：公开 API 写不出「谁是兄弟」，留作重放。图元自己的色阶（cmap / vmin / vmax）留作重放时，挂在它上面的色条轴上写的修改（轴本身、刻度、色条自己的字段）一起留下
  （重放里色条重画会重置刻度定位与轴线样式；`InvShared`、`InvCbarLC` 整图扫描红过）。`collections_N`（网格 / 等值线）上这一族自己的字段仍按 ④ 的口径不写。
- **`_rank` 最终档位**：xscale / yscale 0 → 图例重建 1 → 锚点 / 脱开 2 → 广播 3（边框总色 / 总线宽、色条刻度、色条 vmin / vmax、柱系列八个键）
  → 普通属性 4 → 刻度模型 5 → 单条刻度文字 6 → 示意线样式 7 → 改名（`set_label`）8；有跟随项的误差棒 / 茎叶 / 散点大小与形状由 `_translate`
  直接排 0.5（在重建之前）。色条的标签是 `set_ylabel`，不算改名。
- **验收**：柱系列与单根柱在整图扫描里同扫会让窄的盖掉广播，拆成 `bar_series` / `bar_each` 两个 `_HELD_OUT` 组（`cls.prop` 只认那一族），
  显隐各自再拆一组（隐藏时两边一样什么都画不出来）。每个新键都做过变异（翻译写成常量），整图扫描或所属的 `_HELD_OUT` 组变红才算数。
  两处夹具补洞：茎的 `zorder` 在 InvCont 里离图例很远、写成常量仍然绿，新夹具图 `AdjSeries` 让茎压在图例框上才红；
  `label` 四个键不动像素也不动几何（脚本的图例在 savefig 之前就建好了），整图扫描对它隐形，改由 `test_renames_reach_the_objects`
  直接跑导出的脚本、读各对象最后的 label。组合用例走真 `/api/export`：柱系列 / 单根柱两种列表顺序、第一根柱喂图例、误差棒单独改色
  （不靠图例自己的改动带出重排）、茎叶与散点的示意线、改名排最后。

**⑥ 文字描边与底框、箭头**：文字的行距、描边、底框（含图例项的文字）、3D 轴标签的 `labelpad`、图内箭头的样式 / 端点、
`artist` 的显隐与层次写进脚本。

- 底框：`Text.set_bbox` / `get_bbox_patch()`，与引擎同一套语义（`_bbox`）：改样式时框没有就现建一个**不可见**的，显隐只归
  `bbox_visible` 管，没有框时「关」是空操作。所以「先改底色再打开」「先打开再改底色」「只改底色从不打开」三种都与热态一致。
- 描边三条（开关 / 颜色 / 线宽）合成一条 `_stroke(...)`：`withStroke` 的参数读不到公开 API，三个值都在生成时定死（改过的取改的、
  没改的取 manifest 里的脚本原样），重排路径效果的方式与引擎同（描边放最前，其余效果不动）。
- 箭头：样式类的键是引擎同一句公开 API。注释箭头 `update_positions` 每次 draw 都按注释的锚点重放，所以端点（`endpoints_frac`）落在
  **注释本身的 `xy` / `xyann`**（`_ann_to`，坐标系换算只用公开属性，覆盖引擎开放的那一批可逆坐标系）；独立箭头 `set_positions`。
  `mutation_scale` / 没有 `arrowstyle` 的注释箭头每帧会被注释自己重设，脚本照搬引擎的写法，结果一致。
- 虚线只虚杆、头保持实线：用实例级 draw 钩子 + 包一层 renderer（`_arrow_dashes` / `_SolidHead`）复现，只用公开 API，
  间隙色那一遍按前景色认（引擎认的是私有的 `_dash_pattern`）。
- `artist`（`add_artist` 放进来的 AnchoredText / 比例尺与表格）：`visible` / `zorder` 写；`pos_frac` 要改各类锚框的 loc /
  bbox_to_anchor / xybox，不写。图例项文字的 `labelpad`（永远不发）也留在 `NOT_WRITABLE`。
- 端点把 figure 分数换成数据坐标，用的是**落那一刻**的坐标范围（轴的视图范围按需更新，脚本里先 `get_xlim()` 触一下）。引擎也是这样：
  端点排在 `ylim` 之前，箭头钉在数据位置、范围一改跟着数据走；排在之后才钉在屏幕上。所以端点与范围同档、按列表顺序，
  两种顺序都有用例（先前想当然排到最后，用例当场红）。`endpoints_frac` 不在可编辑字段里（端点另发 `arrow_endpoints`，
  此刻不是纯箭头的注释上它失效、留给引擎裁决），整图扫描采不到，
  由 `test_arrow_endpoints_replay_identically`（`_DEDICATED`）逐根箭头挪一遍。
- 验收里的采样：整图扫描默认的采样值对这一族是隐形的——箭头样式采第一个不同的选项 `-`（无头，虚线改了头也没得看）、层次采「原值 +5.5」
  （全图同步上移，相对次序不变）。给 `(cls, prop)` 定向采样 `_SAMPLE_BY_KEY`：箭头样式 `<|-|>`、帽子大小 36（默认只大两成，在像素阈值边上）、箭头层次抬到最上、锚定框沉到最下，
  夹具 `AdjText` 里二者叠在左下角，各自单独扫（`_HELD_OUT`：同扫时一个的变化把另一个遮住）；右下角那根独立箭头什么也不压，留给帽子大小、线型。`_bbox` 的现建默认值与引擎 `_BBOX_CREATE` 逐 prop 对拍（`test_block_bbox_matches_the_engine`）。

## 修订（2026-11-24）：图内副本的创建型语句（ADR 0207 PR 2）

图内副本（`copy_of`，ADR 0207）是 override 里唯一「新增 artist」的一类，写得进脚本，且是本 ADR 里第一种**创建型**语句：

- 块里多一个运行期函数 `_copy_of(fig, gid, 宿主, 源, [x, y], 文字)`：`Text(x, y, 文字)` + `update_from(源)`，再补 `update_from`
  不管的 `rotation_mode` / `zorder` / `wrap` / `parse_math` / label / 描边特效 / 底框，换成宿主的变换（子图 `transAxes` /
  整图 `transFigure`），`host.add_artist(t)`，`set_clip_on(False)`、`set_in_layout(False)`。全是公开 API，与引擎 `_ensure_copy`
  的静态部分逐项对拍（`tests/test_script_adjust_copy.py`）。块里每条语句是独立函数，没有跨语句的变量，所以副本存在
  `fig` 的一张私有表里，副本 gid 上的后续修改写成 `_copy_ref(fig, gid).set_fontsize(...)`。
- **排在全部属性语句之前**（`_rank` 的 `"copy"` 档 -1）：副本拿到的是脚本原样的源（与引擎 `_RANK_COPY` 先于其余档同一语义），
  源上的样式修改写在创建语句之后。
- 整条副本要么**整件事写进脚本**，要么**整件事留作重放**：源的选择器写不出、宿主是 3D / 色条轴、或副本 gid 上有一条写不进去，
  则 `copy_of` 与它 gid 上的全部修改进 `skipped`（由重放建出）；被留下的副本的源上的修改也一起留下。不生成半截代码。
- 验证（`_verify_edited_script`）不放松：新脚本里的副本只是普通 `ax.text`，gid 是序号名（`axes_N.texts_K` / `fig.artists_J`），
  `app._alias_baked_copies` 按宿主与创建次序把热态里副本的 gid 对到这些序号名，再照旧比 bbox / anchor 与像素；对不上就是分歧、不交付。

