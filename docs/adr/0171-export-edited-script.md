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

`WRITES` 与 `NOT_WRITABLE` 合起来覆盖 `overrides.HANDLERS` 的每个键，新键忘了表态用例就红。这一版写得进 105 个键，155 个明说不写：`WRITES` 里的键都在 `tests/support/invariant_figures.py` 上逐键过了验证，普查没过的（单根柱、图例条目显隐、隐藏子图、刻度线宽）移进 `NOT_WRITABLE`，靠 `script_report.skipped` 如实告诉用户。

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
