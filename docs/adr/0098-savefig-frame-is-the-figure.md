# ADR 0098：脚本 savefig 的裁切框就是这张图的图幅——tight 图不再被切边

日期：2026-09-26 · 状态：**Accepted**（方向 A、§三选 C、§四捷径改法均由用户 2026-09-26 决定；实现随本 PR）
相关：[0017 显示回退 ≠ 几何权威](0017-display-fallback-vs-geometry-authority.md)、
[0049 写回像素门](0049-write-back-pixel-verification.md)、
[0051 保留式规范化](0051-preserving-normalization.md) §3、
[0080 修复事务](0080-spec-fix-transaction.md)、
[0082 属性页按页面 pt](0082-inspector-shows-page-pt.md)、
[0083 override 目标身份](0083-override-target-identity.md)；
前置 PR #675（`savefig_calls` 记进捕获描述符）。

## 问题

脚本用 `savefig(bbox_inches="tight", pad_inches=0.02)` 存的图，磁盘原件是 matplotlib
按「所有画出来的东西的包围盒 + pad」裁出来的；Tavotto 截获 savefig 时丢了这些参数，live
图按 figsize 渲染。教程 Fig1_kinetics：figsize 80 × 57.6 mm，原件约 73.5 × 57.8 mm（随
字体环境差零点几毫米），X 轴标题的包围盒纵向 0.980–1.030，有一半在 figsize 外：

- 画布（排版与快速编辑）上它被 SVG 在图幅边界切掉半截，外面那半截点不中（#670 查实，
  ADR 0086「命中排查表」）；
- `do_export` 出的 PDF / PNG 同样被切，**写回原始文件会把原件从 tight 改成 figsize、
  切掉轴标题**；
- 预检报 `element-outside-figure`，而原件里什么都没切。

四份文档都登记过它、都写着「tight 当不当图幅是 ADR 级决定，没做」
（`figure-capture-and-execution.md`、ADR 0051 §3、0080、0086）。用户 2026-09-26 拍板：修，方向 A
——live 图的图幅 = 脚本 savefig 时的那个裁切框。

## 裁决摘要

| 问题 | 裁决 |
|---|---|
| 图幅是什么 | **frame（F）**：定义这张图的那次 savefig 调用，matplotlib 自己的 `print_figure` 会裁到的那个框（英寸，figure 左下为原点，可以伸到 figsize 外）。`bbox_inches` 为 None（绝大多数脚本）→ 没有 frame，一切与今天逐字节相同 |
| 怎么算 | 不复刻算法：用那次调用记下的参数（`savefig_calls` + 会话里留着的 `bbox_extra_artists` 对象）真跑一遍 matplotlib 的 savefig 到内存，读它交给 `_tight_bbox.adjust_bbox` 的那个框。显式 Bbox 直接用 |
| 哪次调用定义它 | 与写回目标同格式的第一次调用（原件是 `Fig1.pdf` 就取第一次存 pdf 的那次），没有原件取第一次调用；pyplot 捕获、调用没观察到 → 没有 frame |
| 什么时候算 | 每次 build：脚本跑完、instrument 之前（一切 override 之前）。热会话、写回的一次性重放、native 屏障、浏览器 playground 都走这一处 |
| 编辑会不会移动它 | **不会**。frame 只由脚本决定；拖动、改字号都不改它（拖出去的照常被裁、预检照常报，与非 tight 的图一致）。只有改图幅（`size_mm`）改它 |
| 对外的坐标系 | **frame 就是这张图**：manifest 的 `size_mm`、一切几何分数、预览 SVG / PNG、导出、描述符尺寸都以 F 为准；前端一行不用知道 figsize |
| 对内 | matplotlib 的 Figure 仍是 figsize（G）。frame 以 matplotlib 自己 savefig 时的同一个函数（`adjust_bbox`）落在三处：输出（`bbox_inches=F`）、manifest 测量、输入（`frac_to_display` 与 `axes.position` 的写入换算） |
| `size_mm` 改图幅 | 值 = F 的新尺寸；G 按同样的差值变（伸出 figsize 的那几圈毫米不变） |
| 老版面 | **C（用户 2026-09-26 决定）**：升级前靠引擎出图的面板补一条 `figure.frame = "figsize"`，与升级前逐字节相同；面板上提示，一键切换 = 内容在排版上不动、外框变，一次提交可撤销；新面板直接用 F（§三） |
| `paper_style.save` 捷径 | 不再整个替换：照常跑用户那份 `save`，里面的 savefig 由拦截记账、归到 `save(fig, stem)` 的 stem 名下（stem 规则不变）。同时满足 ADR 0094 §五.4 的前置修正（§四） |
| 其余参数 | `dpi` / `transparent` / `facecolor` 只记不用：导出的这几项仍由导出设置决定（非目标，见 §六） |

## 一、frame 怎么定

**不自己写 tight 算法。** matplotlib 的 `print_figure` 在 3.8 与 3.11 之间改过：
`pad_inches="layout"`（3.8+）取布局引擎的 `w_pad / h_pad`、`bbox_extra_artists` 会**替换**
而不是追加图级的默认附加 artist、`adjust_bbox` 的签名多了 `renderer`、constrained /
tight 布局在算框之前先跑一遍。复刻一份就是第二份实现，版本一变就分叉。做法：

1. 拿到定义这张图的那次调用（`savefig_calls` 里记的实效值；`bbox_extra_artists` 的**对象**
   只活在这个进程里，会话按 stem 留一份引用）；
2. `bbox_inches` 是显式 Bbox → F 就是它（matplotlib 对显式框不加 pad，这里也不加）；
3. 是 `"tight"` → 以同样的参数（格式、dpi、pad、extra artists）调一次真的
   `Figure.savefig` 写进内存缓冲，期间给 `matplotlib._tight_bbox.adjust_bbox` 套一层只读的
   观察，拿它收到的 `bbox_inches`（已经加过 pad）。这就是原件被裁成的那个框——用的是那个
   格式自己的 renderer（PDF 的文字度量与 Agg 不同，差零点几毫米）；
4. 观察不到（这一版 matplotlib 换了内部函数名、脚本的 savefig 自己抛了）→ 没有 frame，
   响应里报一条 `frame_unavailable` 诊断，不猜。

原型实测（3.11.2 与 3.8.4，九种 tight 形状 + Fig1）：F 与脚本用同一版 matplotlib 存出的
磁盘原件尺寸逐位相同（0.01 mm）；跨版本（3.11 存的原件、3.8 算 F）差 ≤ 0.24 mm，那是
两版的文字度量不同，不是算法不同。

**时机是脚本跑完之后，不是 savefig 那一刻。** 那一刻算要在脚本中途多画一遍（慢，且
平白改变今天「build 期间不画图」的行为）；两者只在「存盘之后脚本又改了这张图」时不同，
那时 Tavotto 显示的本来就是跑完之后的图，框跟着它走才自洽。代价写在明处：这类脚本的
导出与磁盘原件可能不同，与今天一样。

## 二、坐标系：frame 就是这张图

### 对外（前端、manifest、导出、预检、Codex 插件）

一切以 F 为准：`size_mm` = F 的尺寸，元素 `bbox` / `anchor` / `geometry` / `position` 值 /
图例与文字的位置分数都是 F 里的分数（top-origin 照旧），预览 SVG 与 PNG 探针、导出的五种
格式都是 F。前端的命中几何、预览平面、吸附、对齐、方向键、页面 pt 换算、预检求值器
**一行都不用改**——它们本来就只认 `size_mm` 与分数，现在那个 `size_mm` 与原件一致了。

override 的分数（`pos_frac` / `loc_frac` / `endpoints_frac` / `axes.position`）因此是
**F 里的分数**：用户拖动时看到的就是这一页，写下的就是这一页上的位置。

### 对内（引擎）

matplotlib 的 Figure 不动，仍是 figsize（G）：布局引擎、`subplots_adjust`、脚本自己的
figure 分数都照旧解释。frame 以 matplotlib savefig 时**同一个函数**落地，只有三处：

1. **输出**：`render`（预览 SVG，hybrid 两遍）、`render_png`、`preview_png`、`export`、
   浏览器 playground 的 SVG 与缩略图，一律 `savefig(..., bbox_inches=F)`——与脚本自己的
   savefig 是同一条代码路径；按像素宽出 PNG 时 dpi 按 F 的宽算。
2. **manifest 测量**：先照常 draw 一遍（布局在 G 上跑，与 `print_figure` 的顺序相同），再在
   `adjust_bbox(F)` 里量——显示坐标的原点与 `fig.bbox` 此时就是 F，`W, H`、每个
   `get_window_extent`、`_to_frac` 自然都是 F 里的数，与同一次 savefig 画出来的 SVG 是同一组
   变换。量完还原。`axes.position` 的值（`ax.get_position()` 是 G 的分数）在这里换算成 F 的分数。
3. **输入**：`pathgeom.frac_to_display`（`pos_frac` / `loc_frac` / `endpoints_frac` / 形状
   `pos_frac` 的唯一入口）按 F 换算到 G 的显示坐标；`axes.position` 的 setter 把 F 分数换成
   G 分数再 `set_position`。还原走 G 的原值（getter / restore 不变），与 F 无关。

没有 frame 的图这三处都是原来的那一行，逐字节不变。

### frame 不跟着编辑动（回答「拖到图幅外扩大 tight 盒」的反馈环）

F 在 override 之前算、之后不再算。考虑过「每次渲染按当前状态重算 tight」（与脚本里写死
这些编辑再 `python fig.py` 的结果一致），否决：

- **反馈环**：用户把文字拖出去 → 框变大 → 一切分数的基准变了 → 别的拖过的元素在页面上
  跟着挪；拖动过程中框每帧在变，手底下的坐标系在动；
- **热态 ≠ 重放**：分数是 F 里的数，F 又取决于先应用了哪些 override——同一组 patch 换个
  顺序、增量应用 vs 全量重放，会落到不同的地方，写回事务的不变式直接破；
- **版面跳**：排版里面板外框随每次编辑变，别的面板被推开或压住。

冻结的代价：编辑让内容伸出 F 时会被裁，预检 `element-outside-figure` 照常报——与非 tight
图今天的行为完全一致；要更大的页就改图幅。

### 改图幅（`size_mm`）

`("figure", "size_mm")` 的值是 **F 的尺寸**（用户看到、导出得到的那个）。frame 按
「相对 figsize 的四边外伸（英寸）」存：左、下外伸是 F 的原点，右、上外伸是 F 的右上角减去
G。setter：`G = 值 − (右外伸 − 左外伸, 上外伸 − 下外伸)`，四边外伸不变——伸出 figsize 的
轴标题、图例、色条标签的尺寸本来就不随图幅缩放（pt 是死的）。getter 回 F 的尺寸，还原
走同一对换算（可逆）。没有 frame 时外伸为零，与今天逐位相同。ADR 0051 的规范化「改成
8 cm」于是真的得到 8 cm 宽的页（以前得到的是 8 cm 的 figsize、再被 tight 裁成别的宽度）。

### 写回事务不变式

「热态所见 == 写进文件的 == 重开后重放出来的」照旧成立，而且现在第二项与原件同尺寸：

- F 在每次 build 里由同一段代码、从同一份脚本算出（热会话与一次性重放各算一次，同一台
  机器同一版 matplotlib 逐位相同），在任何 override 之前；
- 几何门比的是两份 manifest（都在各自的 F 里）；像素门的两张探针都 `bbox_inches=F`；
- commit 之后的 `probe_asset` 尺寸核对拿的是 manifest 的 `size_mm`（= F），与写进去的页面
  同尺寸，也与原件同尺寸——以前 tight 原件写回后会被静默改成 figsize。
- prepare → verify → commit 一步不省。

## 三、老版面怎么迁移（用户 2026-09-26 决定：C）

老项目里这张图在排版上的面板是按旧图幅（G）存的：`nativeW/H = 80 × 57.6`，页面 `w/h`
= 原生 × 缩放比，裁剪是原生图幅的比例，`x/y` 是页面上的左上角。升级后 manifest 回来
`size_mm = F`，现有的同步器（`useEngineSync` → `syncPanelNativeSize`）会**静默**把原生图幅换成
F、缩放比不变、左上角不动——结果是：内容大小不变，但整张图在页面上平移了 F 原点那么多
（Fig1 横向 0.4 mm、纵向 2.1 mm；图例放在图外的那种，外框从 76 mm 宽变成 110 mm），裁剪框也换了基准。
用户已经导出过的排版，再导出一次就不一样了。考虑过三个选项：

| | 做法 | 排版变不变 | 代价 |
|---|---|---|---|
| A 静默换基 | 升级即按 F 显示；原生图幅换成 F，缩放比不变，`x/y` 按 F 原点的偏移反推（内容在页面上**不动**），裁剪按内容换基；弹一次通知 | 内容不动，外框变 | 用户没同意排版就变了；伸得多的会压到相邻面板 |
| B 外框不变 | 页面 `w/h/x/y` 不变，内容按新原生图幅缩放 | 外框不变，内容缩放 | 缩放比变了 = 页面上的字号 / 线宽全变（ADR 0082 的页面 pt 全部失真） |
| **C 老项目不动 + 提示（采纳）** | 升级前的面板保持旧图幅；面板上提示，一键切换按 A 的换算；新面板直接用 F | 用户点之前一个字节都不变 | 老项目里 tight 图的切边要用户点一下才修好 |

原则是「不许悄悄改变已经导出过的排版」，只有 C 在用户点之前完全不变；切换用 A 的换算，
因为它保住的是排版里最重要的东西——内容的位置与页面 pt（B 牺牲的正是这个）。

### 落地

- **图级 override `figure.frame`**：`"figsize"` = 图幅按 figsize（引擎仍算出并在 manifest 的
  `frame` 字段里报告脚本的图幅，只是不生效）；缺席或 `"savefig"` = 按脚本存盘的图幅。它是
  一条普通的 override，所以变体键、全量重放、写回的一次性重放、导出、撤销全部现成可用。
  应用顺序排在一切之前（`_RANK_FRAME = -1`）；它这一轮换了，值以图幅为基准的 override
  （`size_mm` / `position` / 三个 `*_frac`，`overrides._FRAME_RELATIVE`）值没变也重放——否则
  热态与全量重放分岔（`test_switching_the_frame_mid_session_equals_a_fresh_replay`）。
- **迁移标记是面板上的**（`PanelObject.figureFrame = 1`），不是项目上的：面板会经检查点恢复、
  跨标签页粘贴、项目包在项目之间流动，项目级的位保不住它们。读档唯一入口
  `migrateToProject` 与布局版本恢复 `restoreLayoutVersion` 都调同一个
  `lib/figureFrameMigration.migrateFigureFrames`；新建面板的三个入口（`addPanel` /
  `addRuntimePanel` / 内嵌画布）生来带记号。磁盘格式不升版（加字段）。
- **只给「此刻样子来自引擎」的老面板补 override**：runtime 面板，与带着图内修改的 PDF 面板。
  没有图内修改的 PDF 面板在画布与导出里用的是**磁盘原件本身**——脚本自己存的那份，本来
  就是 tight 的——给它补 figsize 反而会把它换成引擎按 figsize 画的那张（逐字节不变的反面）。
  它们只打记号；日后第一次编辑时引擎按 F 出图，与它一直显示的原件同一个框。
- **提示与切换**：manifest 的 `frame.active == false` 且面板带着那条 override 时，画布角标
  「按 figsize 显示」（信息级、带说明）、属性页一条说明 + 「改用原图的图幅」。切换
  （`adoptScriptFrame`）一次提交：去掉 override；面板落位按「内容在页面上不动」换算（旋转、
  翻转、非等比缩放都按内容空间算；裁过的面板保留原来的可见范围与新图幅的交集）；图内
  `pos_frac` / `loc_frac` / `endpoints_frac` / `axes.position` 换到新图幅的分数、`size_mm` 换成
  同一个 figsize 对应的图幅尺寸——图内每个元素都留在原处。依据是这张面板**精确**的 manifest
  （ADR 0017）。
- 已知的一处：「恢复整张图」清掉全部 override 时也清掉这一条，图回到脚本的图幅——那本来就是
  「脚本原样」，但走的是图幅同步器（左上角不动），不是上面那条「内容不动」的换算。

## 四、`paper_style.save` 捷径（用户 2026-09-26 决定：做）

worker 以前把 `paper_style.save` 整个换成一个只登记 stem 的 lambda——它里面那句
`savefig(bbox_inches="tight", ...)` 从来没被执行过，参数看不见（PR #675 如实记成
「没观察到」），教程 Fig1 就走这条路。改成：照常调用用户那份 `save`，期间它里面的每一次
`savefig` 由拦截记账并**归到 `save(fig, stem)` 的 stem 名下**（stem 规则不变——有的图库
`save` 里存成 `f"{stem}_final.pdf"`，改成按文件名取 stem 会让已有的 override 全部挂空）。
行为变化与 `python fig.py` 一致的方向：`save` 里的 `plt.close(fig)`、建输出目录（沙盒里）
照常发生；它还给图改样式的话，Tavotto 现在显示的是改过的样子（与原件一致）。写盘仍被
拦截、`Path.unlink` 与写入守卫不变。native bridge 本来就调用真的 `save`，不变。`save` 不是
可调用对象的图库照旧只登记（脚本在终端里调用它本来就会报错）。

**这同时是 ADR 0094（#667，写回原脚本）§五.4 的前置修正**：那边发现同一个捷径不经过
`Figure.savefig`、钩子块在 Tavotto 里永远不触发，实施计划第一个 PR 是把捷径改成调用
`fig.savefig(f"{stem}.pdf")`。两边用同一个改法——本 PR 让捷径里的每一次 savefig 都经过
`Figure.savefig`（被拦截、不落盘、来源仍是 savefig），比那份补丁多做的一步是执行用户自己的
`save`，因而拿到的是原件真正的参数（`bbox_inches` / `pad_inches` 等），而不是一句固定的
`savefig(f"{stem}.pdf")`。#667 的那个 PR 不必再改这一行。

## 五、非 tight 的脚本逐字节不变

没有 frame 的图（`bbox_inches` 为 None、pyplot 捕获、调用没观察到）：输出不带
`bbox_inches`、manifest 不进 `adjust_bbox`、`frac_to_display` 与 `axes.position` 走原来那一行、
`size_mm` 外伸为零。验收用 PR #675 的对拍装置：examples、playground 示例在 origin/main 与本分支
上逐字节比 build 响应、manifest、预览 SVG、五种导出。

## 六、非目标与承认的边界

- `dpi` / `transparent` / `facecolor` / `edgecolor`：只记不用。导出的这几项由导出对话框与
  图级 `transparent` / `facecolor` override 决定；脚本存 `transparent=True` 的 PNG，Tavotto
  导出默认仍是不透明底（与今天一致）。要不要跟随脚本是另一个决定。
- 同一个 stem 的多次调用裁法不同（pdf tight、png 不 tight）：按「与原件同格式的那次」取，
  另一格式的导出与那份磁盘文件不同——响应里的 `frame.call` 说清按的是哪一次。
- 存盘之后脚本又改了图：frame 按跑完之后的图算（§一）。
- 跨机器 / 跨 matplotlib 版本：F 用本机本版的文字度量，与别处存的原件差零点几毫米
  （原型：3.8 vs 3.11 ≤ 0.24 mm），写回后的尺寸核对容差 0.5 mm 盖得住。
- 布局引擎：F 在布局跑过之后算、`adjust_bbox` 期间布局冻结——与 `print_figure` 一致。
  constrained 图改图幅后布局照常重排，F 的外伸按英寸保持。

## 看护

- `tests/test_savefig_frame.py`（真 worker / 真写回）：八种 tight 形状导出的 PDF 页面与脚本
  自己存的原件同尺寸、manifest 与描述符同尺寸、没有元素伸出图幅；PNG 原件按 PNG 那次的框；
  没有 `bbox_inches` 的脚本仍是 figsize；编辑不移动图幅；`pos_frac` 落在写下的位置、原样写回
  `axes.position` 一个像素都不动、`size_mm` 是图幅尺寸；热态 == 全量重放（含中途切换
  `figure.frame`）；`figure.frame = "figsize"` 与升级前同尺寸、manifest 报出脚本的图幅；
  `paper_style.save` 执行用户的 `save`、stem 归属不变；tight 原件写回后页面尺寸不变、
  轴标题整行在页面里；`FRAME_ATTR` 两侧字面量相等。
- `tests/test_savefig_capture_params.py`：记下的参数（PR #675）；捷径现在看得见。
- 前端：`lib/figureFrame.test.ts`（迁移三类面板、读档入口幂等、切换时内容在页面上不动——
  旋转 / 翻转 / 非等比 / 裁剪、分数类 override 的换算）、`store/figureFrameSwitch.test.ts`
  （打开老排版 → 切换 → 渲染回来同步器不再挪它 → ⌘Z）、`e2e/savefig-frame-migration.spec.ts`
  （真浏览器 + 真 matplotlib：老排版原样打开 → 切换后标题在屏幕上不动、外框变 → 撤销还原）。
