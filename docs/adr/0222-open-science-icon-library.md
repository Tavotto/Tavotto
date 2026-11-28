# ADR 0222：开源科研图标库导入——只收公有领域 / CC0 / CC BY，每个图标带出处，导出自动拼署名；图库免费、走公开 CDN

日期：2026-11-28 · 状态：**Proposed**（维护者 2026-11-28 已拍板两条，见 §九；其余待定项见 §十）
相关：ADR 0220（机理图三层：部件语法 / 图库 / 组合——图库是第 ② 层，免费；0220 未并入 main 时以其分支为准）、
[0208 矢量元素](0208-agent-vector-elements.md)（§七之二 路径质量、第 ⑥ 期 6.A `engine/pathquality.py`）、
[0189 SVG 互通](0189-svg-interop.md)（`rendercore/svgimport.py` 与受限解析器）、[0191 嵌套组与剪切蒙版](0191-nested-groups-and-clipping-masks.md)。

## 问题

Agent 画机理图的图元分三层：① 部件语法（Tavotto 自己画的几何基元）、② **开源图库批量导入**（器官、细胞、动物、实验器材等长尾图标）、③ 组合。
② 不自己画，直接从成熟的开源科研图库取。风险全在许可与出处：许可混杂（CC0 / CC BY / CC BY-SA / NC / MIT 品牌标志混在一起），
改编后要署名，且许可方对「自动下载」「整体再分发」可能有附加条款。

## 调研结论（2026-11-28，逐库读条款原文；细节与未核实项见调研报告，不在本文复制）

- **没有任何一个候选图库的条款禁止整体重新分发成另一个图库。**
- 整体许可：Reactome Icon Library 全库 CC BY 4.0（`reactome.org/license` 1a）；Servier Medical Art 官方现行 CC BY 4.0。
  逐个许可：Bioicons（`icons.json` 2804 条：cc-by-3.0 1375、cc-by-4.0 883、cc-0 465、mit 39、cc-by-sa-4.0 35、cc-by-sa-3.0 4、bsd 3）、
  NIH BioArt（只有 CC-BY 与 Public Domain）、SciDraw（CC BY 4.0 / CC0）。
- Servier 条款的用户义务含「不得用自动方式（bots、scrapers）访问或下载 Site Content」；图片是否属于 Site Content 条款没写明。
- CC BY 4.0 的「不得施加额外限制 / 不得用技术措施限制他人」。

## 决定

### 一、范围：先接两个库

1. **Bioicons**（聚合源，GitHub 公开仓库）：只取 `cc-0` / `cc-by-3.0` / `cc-by-4.0`；`cc-by-sa-*`、`mit`、`bsd` 一律不收（MIT 项多是品牌标志与图表模板）。
2. **Reactome Icon Library**：去掉 **Protein 类**（1,431 个蛋白标签图，文字已转曲、改不了名）后约 1,271 个（图库各非蛋白分类页列出的 ID 数，13 + 41 + 242 + 116 + 457 + 69 + 203 + 56 + 74；少数图标也属蛋白类的在取元数据时再排除）。
3. NIH BioArt、SciDraw 第二期；Servier 官网（**一律不碰**：Servier 的图只走 Bioicons 里的 CC BY 3.0 副本）、DBCLS 整库、SwissBioPics、Openclipart、Health Icons 不接（理由见调研报告）。

### 二、许可白名单（唯一出处：`scripts/iconlib/iconlib_license.py`）

内部许可号只有四种：`CC0-1.0`、`PD`、`CC-BY-3.0`、`CC-BY-4.0`。其余（SA / NC / ND / MIT / BSD / 没写版本的 `cc-by` / 缺失 / 不认识）一律丢弃，
**未知 = 丢**，丢弃项写进审计文件（原因码 `license_restricted` / `license_software` / `license_unknown` / `license_missing`）。

### 三、取得通道

- Bioicons：只走 `raw.githubusercontent.com` 上的 `static/icons/icons.json`、`authors.json` 与逐个 SVG；**不碰 bioicons.com 与 Servier 官网**。
- Reactome：**不暴力扫编号**。编号来自官网自己的分类列表页：`https://reactome.org/icon-lib/<分类>?page=N`（服务端渲染，每页 28 个 `R-ICO-xxxxxx`，翻到某页没有新 ID 为止；
  2026-11-28 实测 `cell_element` 9 页：8×28 + 18 = 242，与图库页写的个数一致）。分类取 `arrow / background / cell_element / cell_type / compound / human_tissue / receptor / therapeutic / transporter`，
  **不含 `protein`**。再按 ID 逐个取同名 `https://reactome.org/icon/R-ICO-xxxxxx.xml`（元数据，含 `category`，仍含 protein 的排除）与 `.svg`。
  全部非蛋白图标约 1,271 个，加分类页约 40 次、元数据与 SVG 各约 1,271 次请求，按下面的限速要跑约 1 小时，可续跑。
  图库页上写的整包链接（`/icon/icon-lib-svg.tgz`、`/icon/icon-lib-png.tgz`）2026-11-28 实测 404，不能用。
- Servier：**只用 Bioicons 里的副本**（CC BY 3.0），不访问 smart.servier.com，不发信。
- **不发信要整包**（维护者 2026-11-28 定）。
- 通用：主机白名单（只有 `raw.githubusercontent.com` 与 `reactome.org`）、`https` only、体积上限 8 MB、
  **每个联网请求之间至少间隔 1 秒（≤ 1 请求/秒）**、遇 429 / 5xx / 网络错误**指数退避重试**（2、4、8、16 秒，最多 4 次；服务端给了 `Retry-After` 取较大者，上限 120 秒），其余 4xx 不重试；
  磁盘缓存（命中不联网，404 也缓存，失败不缓存，可续跑）；记 `sha256` / `source_url` / `retrieved_at`。
  下载物是不可信数据：脚本只存字节，只交给 `svgimport` 的受限解析器，缓存目录不得在仓库内（脚本检查并拒绝）。

### 四、出处元数据（每个入库图标一条，`manifest.json`）

`id`、`title`、`category`、`source`（原库名：Servier Medical Art / TogoTV (DBCLS) / Reactome Icon Library / Bioicons…）、`via`（取自哪里）、`source_url`、`file_url`、
`creator`、`creator_url`、`license_id`、`license_name`、`license_url`、`copyright_notice`、`modified`（入库即 `true`：分组与色槽算改编）、`retrieved_at`、`sha256`，
外加规范化产物：`paths`、`bytes`、`size_mm`、`slots`、`groups`、`quality`，以及（有则带）`description`、`synonyms`、`references`。
**出处与索引分文件**：索引只服务搜索（§六），不带出处细节。

### 五、规范化流水线（离线，不改 `svgimport`，不改 SVG 字节）

抓取 → 许可过滤 → 预检 → 出处元数据 → 索引 → 审计。预检（`iconlib_normalize.precheck`）：

1. **体积闸门**：默认 100 KB / 500 路径（**起步值**，样本分布估的，待校准）。超过的连导入都不试。
2. **去位图**：含 `<image>` / `data:image` 的直接不收。
3. **拒滤镜 / 蒙版 / 图案 / 外链**：用画布导入同一个 `svgimport.import_svg`，凡 `unsupported` 非空的不收——这类图用户插入时会落到「整张当图片」兜底，不能入库。原因码写进审计。
4. **色槽**：按面积加权取最多 5 个主要填充色，角色 `main` / `highlight` / `shade`。渐变取 `fill`（端点色）。**只出计划，不改对象。**
5. **自动分组**：最小版——无分组的对象按所属色槽成组，组名中英；已有分组的保留。**更好的分组（包围盒包含、同 clipPath、部件语义）与 `groupNames` 的真实字段留给后续**；
   本期没有接 `groupNames`（svgimport 输出里还没有这个字段，另一会话在做）。
6. **质量核对**：逐路径过 `engine/pathquality.check_path`（6.A 已合入 main），汇成 `quality`（最差档、各档计数、`fail_fraction`、问题码计数）。
   **本期只记录、不据此丢图**：实测它对真实图库的描摹路径过严（见 §十一），先看分布再定闸门（§十）。

### 六、索引（`index.json`）

每图一条：`id`、`title`、`category`、`source`、`license_id`、`keywords_en`、`keywords_zh`、`paths`、`bytes`、`size_mm`、`quality`。
关键词 = 标题 + 类别 + 描述 + 同义词（Reactome XML 自带）；中文来自**人工写的最小词表**，没命中的不翻译、不编。扩词表必须人工抽检（生物学术语机翻易错）。
先做关键词，不做向量检索。

### 七、署名（界面待定，先只做元数据与拼句函数）

- 导出元数据里写全每个用到的图标的出处（§四字段）。
- 拼句函数 `iconlib_attribution.attribution_line(records, lang=…, include_public_domain=…)`：
  CC BY 3.0 带标题（该版本要求）、4.0 标注「是否修改」（`modified`）、同 来源 + 许可 + 创作者 + 是否修改 的合并成一条、CC0 / PD 默认不出句、不在白名单的许可不替它编一句、排序稳定。
- **界面（维护者 2026-11-28 拍板，下一期做，做之前截图先给维护者看）**：
  - 导出时出**一枚胶囊**「用了 N 个开源图标，图注里需要署名」+「复制署名」按钮；N 只数需要署名的（CC BY），**CC0 / 公有领域不计、不提示**，全是 CC0 / PD 时不出胶囊。
  - 署名句按用到的图库自动拼好，同库同许可合并；点「复制署名」复制的就是这句。
  - 完整出处（§四全部字段）写进导出文件的元数据。
  - **不往图上自动加字。**
  - 胶囊文案、位置与既有导出提示的关系，由那一期的 UI 稿定，不在本 ADR 定死。
- 本期代码只做元数据与拼句函数，**不做界面**。运行时消费这些函数时再把脚本里的实现搬进 `src/tavotto/`（现在在 `scripts/` 是因为只服务离线构建）。

### 八、分发

- **公开 CDN、按需下载**：安装包只带索引（估计 1–2 MB，未实测），图标本体在首次预览 / 插入时下载并缓存；Agent 搜图只读索引。
- **不放进 `/pro`、不加密、不做授权校验**（CC BY 的「不得用技术措施限制他人」）。图库对用户免费。
- 体积是估算（Servier 1373 个 × 约 25 KB；Reactome 约 1271 个 × 约 8 KB；规范化 + gzip 后合计约 10–20 MB），要等规范化稳定后实测。
- 许可方要求撤图时可以按 `id` 下架（CDN 删文件 + 索引去条目），不需要发版。

### 九、维护者 2026-11-28 拍板

1. **先接 Bioicons（只取 CC0 / CC BY 3.0 / CC BY 4.0）+ Reactome（去掉 Protein 类 1,431 个蛋白标签图）；BioArt / SciDraw 第二期。**
2. **图库免费、走公开 CDN、不进 `/pro`**（与 CC BY 不得技术限制一致）。
3. **不写信要整包**：Reactome 按编号限速逐个取，Servier 只走 Bioicons 副本、不碰官网。
4. **署名界面**：导出时一枚胶囊 + 「复制署名」，不往图上加字，CC0 / PD 不提示（§七；界面下一期做，截图先给维护者看）。

### 十、待定项（未拍板，实现里不替维护者决定）

- Reactome 许可页第 4 条赔偿条款、BioArt（第二期）的「integrity」倡议语气，要不要法务看。
- 署名胶囊的具体文案与位置（下一期 UI 稿）。
- 体积闸门阈值；`pathquality` 判据对图库图标怎么用（见 §十一）；是否加一步「合并重合锚点 / 去零长段」的修复。
- 一张机理图是否限制取自同一个库（画风一致）。
- 渐变在期刊色盲 / CMYK 检查里怎么处理。
- 版本漂移：定期重抓对账的节奏与许可变化时的下架流程。

## 第一步实现与实证（2026-11-28，两库各取 20 个候选，真下载）

代码：`scripts/iconlib/`（`iconlib_license` / `iconlib_sources` / `iconlib_normalize` / `iconlib_index` / `iconlib_attribution` / `build_iconlib`），测试 `tests/test_iconlib_build.py`（全离线）。
运行：`python -I scripts/iconlib/build_iconlib.py --cache <仓库外> --out <仓库外> --limit 20`。

- **Bioicons**：按等距抽样 20 个（索引 2804 条，白名单后 2723）→ 入库 13、预检拒收 7（`css_selector` 2、`unsupported_element` 1、`too_big` 4）。
  拒收的 4 个 `too_big` 是 DBCLS / Servier / Plasmodium 的照片级大图（175–556 KB）。
- **Reactome**：先按分类列表页枚举出 1,271 个非蛋白 ID，再等距抽 30 个、取元数据到 20 个候选 → 入库 10、拒收 10（`css_selector` 9、`too_big` 1）。
  9 个 `css_selector` 同一原因：Figma 导出的 `<mask style="mask-type:luminance">`，`svgcheck` 把这条内联样式判为不安全并整张拒收（且这些图本来就用了 `mask`，`svgimport` 也不支持）。
  （第一次用连续 ID 段探测的结果是 11/20，同一个原因占 8 个。）**Reactome 约一半的图会因蒙版被预检挡掉**，是否值得为它们在规范化阶段做「去蒙版」留给后续。
- **质量核对**（第一次运行入库 24 个）：clean 7、fail 17；问题码计数 `extremum_off_anchor` 1028、`zero_length_segment` 243、`not_really_closed` 136、`too_many_anchors` 94、`coincident_anchors` 3。
  例：Servier `sun` 86 路径里 65 条 fail（103 处零长段）。**结论：描摹出来的图库图标普遍有零长段 / 锚点不落极值，若按「有任何 fail 路径就拒」几乎全拒；
  本期只记录。**
- 样本量小（每库 20），通过率只能说明形状，不能外推到整库。

## 后果

- 新增离线脚本与测试，不进运行时与安装包；`pyproject.toml` 的 ruff `src` 增加 `scripts/iconlib`。
- 运行时（插入图标、署名导出、CDN、Agent 搜图工具）全部是后续各期，本 ADR 不含。
- 若维护者否决 §一 的范围或 §八 的分发方式，只需改 `scripts/iconlib` 的白名单与来源表，不影响现有功能。
