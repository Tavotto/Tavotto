# ADR 0184：脚本即图片——素材区按「图」不按「文件」，从脚本进来只看脚本的图

日期：2026-11-14 · 状态：**Accepted**（用户 2026-11-14 三项拍板；1.0 登记例外，见 `docs/1.0-release-readiness.md` §1.8）
相关：[0092 主页拖放](0092-home-native-file-drop.md)（§六 拖入 .py 打开上级目录并运行它）、
[0139 来源驱动采用](0139-source-backed-figure-adoption.md)、[0098 savefig 图幅就是这张图](0098-savefig-frame-is-the-figure.md)、
[0113 逐次保存的来源事实](0113-save-occurrence-provenance.md)、#1251（`Registry.for_artifact` 是「磁盘文件 → 图」的唯一出处）。

## 问题

用户 2026-11-14：

1. **拖进一个脚本，素材区却是整个文件夹。** ADR 0092 §三 把 `.py` 的上级目录当项目根（脚本按相对路径读数据、cwd 都靠它，这一条不动）。
   但 `scan_panels` → `project_refresh.iter_assets` 随后把整棵目录树里的 PDF / PNG / SVG 全列进素材区，就绪度横幅也按它们计数：
   拖进一个脚本，看到的是「100 张待连接 / 仅版面」。用户的对象是**这个脚本画的图**，不是这个文件夹。
2. **同一张图的几种格式各占一张卡。** 现行唯一的合并是「同目录、同一张图的 PDF 盖住位图」（`asset_kind(pdf_twin=…)`）；
   SVG 不参与，`figures/pdf/fig1.pdf` 与 `figures/png/fig1.png` 这种按格式分目录的也不合并。用户要排版的是图本身，不想挑格式。
3. **同一张图，有的格式能改、有的不能。** 能否进图内编辑（`engine/readiness`）本来按图名算、各格式一致；分歧出在**打开时按文件 id**
   走不同的来源路径——位图走 ADR 0139 的「完整 PNG 采用」（逐像素零差，跨 matplotlib 小版本几乎必拒），PDF 直接按脚本重画，
   SVG 还要另核字节绑定（#1185）。于是「能不能改」取决于用户恰好拖的是哪个格式。

## 决定（用户 2026-11-14 三项拍板）

### 一、素材区的单位是「图」，一张图可以有几份文件

后端给每份素材挂一个 **图键 `figure_key`**（`engine/figuregroup.py`，唯一出处；`/api/panels` 与就绪度共用）：

- **有脚本的图**（`Registry.for_artifact(rel)` 认得）：`script:<登记的 stem>`。注册表里 stem → 脚本本来就是一对一，所以跨目录、跨格式的
  同一张图天然同键。
- **没脚本的图**：`dir:<归一目录>/<文件名 stem>`。归一目录 = 文件所在目录；若该目录名本身是格式名（`pdf` `png` `svg` `jpg` `jpeg`
  `tif` `tiff`，大小写不敏感）则取它的上一级。同目录同名合并，格式名子目录里的同名文件也合并，**不跨普通目录**
  （`a/fig1.png` 与 `b/fig1.png` 可能是两张不同的图，不合并）。

一张图的几份文件里选一份 **主文件**（`primary`）：PDF > SVG > 位图（像素面积大者优先，再按路径）。素材卡只显示主文件，详情里列出全部格式。
放到画布上的是主文件；导出格式照旧在导出时选，与主文件无关。

现行的「同目录 PDF 盖位图」（`iter_assets`）保留不动：被它盖住的位图本来就不在 `/api/panels` 里，也就不进 `formats`；
本规则只在它之上把**剩下的**同图文件（SVG、格式名子目录里的、脚本登记在别处的）归成一张卡。`/api/panels` 仍是一份文件一项
（老排版按 `fileId` 解析），归组只是多出来的字段。

### 二、能不能编辑是图的属性，不是文件的属性

- 一张图只有一个可编辑结论（就绪度本来就按 stem 算，这里把主语从「stem」正名为「图键」）。
- **有脚本的图，进图内编辑一律从主文件走**：主文件是 PDF / SVG 时按脚本重画，与今天打开 PDF 同一条路；位图兄弟不再各自触发
  ADR 0139 的位图采用。素材卡本来就放主文件；老排版里放着没动过的 `fig1.png`、而这张图有矢量主文件时，进图内编辑那一刻把这个面板
  换成主文件（一条可撤销的历史，外框左上角与宽度不变，高度按主文件宽高比、差 ≤1% 原样）。裁剪 / 旋转 / 翻转过的不换（换了会挪动画面），
  照旧走 0139。
- **只有一份位图、有脚本**的图仍走 ADR 0139（`exact` / `needs_confirmation` / `reconstructed` / `unavailable`），判据一字不改——
  那里没有「几种格式结论不同」的问题。
- **没脚本的图**照旧仅版面。

### 三、从脚本进来，素材区只显示脚本的图

项目的**视图范围**（`view_scope`）记在最近项目条目里（`engine/config`，与 `last_opened` 同一条），后端解释，前端只消费：

| 进入方式 | 范围 |
| --- | --- |
| 拖入 / 选中 `.py`（ADR 0092 §六 的所有入口） | `scripts`，把这个脚本加进范围的脚本表 |
| 拖入文件夹、目录浏览器、新建项目、教程 | `folder`（今天的行为） |
| 从最近 / 收藏重开 | 沿用上次记的范围 |

`scripts` 范围里素材区显示的图 = 范围脚本表里的脚本产出的图 ∪ 当前排版里已经用到的图（任一份文件；**用着的绝不藏**）。
任何「打开并运行」入口（脚本库 ▶、主页 / 画布拖入）运行一个脚本，都把它加进脚本表（`POST /api/project/view_scope`）。同文件夹里**没有脚本的图完全不显示**（用户拍板：不折叠、不计数），
也不进横幅「已找到 N 张图」的计数、不出「待连接」。要用它们就从文件夹打开这个项目（范围变成 `folder`），或导入素材（ADR 0176）。

范围只影响**显示与计数**：盘点、watcher、写回、导出、诊断包看到的素材清单不变；没有端点因范围而拒绝访问项目内的文件。

### 四、R 和 MATLAB 与 Python 一样是图的来源（用户 2026-11-14 补：「不可以只认 Python 的」）

Python 的「这个文件是谁画的」有注册表；R / MATLAB Tavotto 不运行，所以**只读源码**（`engine/foreignoutputs.py`）：

- R：`ggsave` / `pdf` / `png` / `svg` / `jpeg` / `tiff` / `bmp` / `cairo_pdf` / `svglite` / `save_plot` 的文件参数，
  `dev.copy` / `dev.print`；路径是字面量，或全字面量参数的 `file.path(...)` / `here(...)`；
- MATLAB：`saveas`（含第三个参数给格式）、`exportgraphics`、`print`（函数式与命令式，`-dpng` 补后缀）；路径是字面量或全字面量的 `fullfile(...)`；
- 拼出来的（`paste0`、变量、`num2str`）一律不认，注释与字符串里的不算；相对路径按脚本所在目录解析，出了项目的丢掉；
  同一输出被两个脚本声称 → 不归任何一个（不猜）；
- 另认**同目录同名**的 R / MATLAB 脚本（`foreignsource.sibling_of`，已有判据）。

认出来之后与 Python 同一套：图键 `foreign:<脚本>|<文件名 stem>`（同一个脚本存的同名文件跨目录、跨格式是一张图）、
`/api/panels` 多 `source_script`（画它的脚本，任何语言），范围按它判。`script` 字段的含义不变——仍只表示「Python 注册表、可进图内编辑」；
R / MATLAB 的图照旧仅版面，可编辑走 ADR 0143 的「生成可编辑版本」。

## 不做

- 不改项目根（仍是脚本所在文件夹）；不在用户文件夹里写任何标记文件——范围在 `data_dir()` 的配置里。
- 不迁移旧排版里的 `fileId`；不合并跨普通目录的同名无脚本图。
- 不给素材卡加格式切换（用户拍板：自动挑矢量）。

## 后果

- 拖脚本进来，素材区只有这个脚本的图；同一张图的 PDF / PNG / SVG 合成一张卡，能不能改与拖的是哪个格式无关。
- 加字段协议：`/api/panels` 每项多 `figure_key`、`primary`、`formats`，响应多 `view_scope`；老前端忽略，行为同今天。
- 加字段协议：`/api/projects/open` 收可选 `entry`（`script` + `script` / `folder`），缺省沿用；响应的 `settings.view_scope` 同步。
- 看护（R / MATLAB）：`tests/test_foreignoutputs.py`（各保存写法的正反例、相对脚本目录、出项目 / 两个脚本声称都不认、与 Python 同样归组）；
  `web/e2e/script-is-figure.spec.ts` 两条（拖入 .py / 拖入 .R：一张卡、散图不出现，登记 `assets.script-is-figure`）。
- 看护：`tests/test_figuregroup.py`（图键与主文件选择、格式名子目录、跨普通目录不合并）、
  `tests/test_view_scope.py`（三种进入方式、重开沿用、坏名字不进范围、清单不按范围裁）、
  `web/src/lib/figureGroup.test.ts`（一张卡、范围过滤、用着的不藏、胶囊计数）、
  `web/src/store/vectorPrimaryEntry.test.ts`（PNG 换矢量主文件进编辑：外框不动、可撤销；裁剪 / 旋转 / 翻转 / 无脚本不换）。
