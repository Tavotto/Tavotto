# ADR 0210：推荐配色——许可清楚的配色目录、画布级色键表、样式面板里的「配色」一节

日期：2026-11-24 · 状态：**Accepted（设计维护者 2026-11-24 定稿；第一期分三个 PR 实现）**
**1.0 收敛纪律：新增产品能力。维护者 2026-11-24 裁决「现在就做」（不等 1.1），登记在 `docs/1.0-release-readiness.md` §1.8；
入口默认关（`PALETTE_UI = false`），关着时界面、文档、导出与 1.0 逐字节相同。**
相关：[0081 画布跟随样式](0081-canvas-follows-style.md)（override 的所有权、一次 commit）、[0083 目标身份](0083-override-target-identity.md)
（`artist_identity` = 显式 label 的摘要）、[0034 图例条目](0034-legend-entry-binding.md)（`follow_source`）、[0155 期刊库](0155-journal-catalog.md)
（「界面入口一个常量、开发键只在测试里设」的先例）、[0198 闭源高级版扩展](0198-closed-source-pro-extension.md)（许可边界）；
跟踪 #2160（RFC，验收条件照用）、#1487（导入 / 自建色图，第三期）。

## 问题

今天的配色只有样式里的 `palette`：`stylePresets.planStyle` 按元素在**每张图里的出现顺序**循环取色、每张图从第 0 色重新数。
组图里同一个实验组（Control、Drug A）在不同子图里顺序不同时就是不同颜色；名字叫「色盲友好」的方案配上具体几组也可能撞色
（实测：Okabe–Ito 的绿配 50% 灰，绿弱下 CAM02-UCS 色差只有 6.8；tab10 前 4 色里橙和绿在红弱下只有 1.6）。

## 决定

### 一、目录：两份 JSON，色值只有一处

- `src/tavotto/profiles/palettes.json`：许可宽松的方案（Okabe–Ito、Paul Tol、ColorBrewer、Crameri S 系列）。
- `src/tavotto/profiles/palettes_copyleft.json`：GPL 的期刊风格色板（ggsci 的 NPG、AAAS、NEJM、Lancet、JAMA、JCO）。
- 每个方案：稳定 `id`（`crameri.batlowS`）、`name`、`source`（指向文件里的 `sources` 表：名字、许可、网址、引用）、`kind`、`colors`，
  可选 `neutral`（方案作者自带的中性灰）；文件顶层 `neutral` 是没有自带灰时的对照色（`#A6A6A6`）。
- 前端经 `@palettes` / `@palettesCopyleft` 别名整份 import；引擎经 `engine/palettes.py` 读（加载、校验、按许可分档）。
  `web/src/lib/swatches.ts` 的 Okabe–Ito 改成读目录（第二个 PR），不再是第二份色值。
  前端的纯逻辑在 `web/src/lib/palette/`：`catalog`（目录）、`cvd`（色差与色觉模拟）、`colorKeys`（色键表）、`rank`（排序）、`plan`（落成 override）。
- 生成：`scripts/dev/gen_palettes.py`（只在升级来源版本时手动跑；运行时不依赖 cmcrameri / tol-colors）。

### 二、来源与许可

| 来源 | 许可 | 收法 |
| --- | --- | --- |
| Okabe–Ito | 事实性色值（`LicenseRef-Factual`） | 8 色，黑色最后 |
| Paul Tol（tol-colors 2.2.0） | BSD-3-Clause | bright / vibrant / muted / high-contrast；bright、vibrant 自带的 #BBBBBB 作中性色 |
| ColorBrewer | Apache-2.0，带致谢句 | Set1 / Set2 / Dark2 / Paired |
| Crameri 科学色图 v8（cmcrameri 1.10） | MIT | 分类 S 系列，只收对白底 ≥ 3:1 的颜色（细线、小点要看得见），每套最多 12 个 |
| ggsci 期刊风格 | GPL-3.0-or-later | 只在 copyleft 那份；名字写「风格」，不写「官方」 |

声明全文在 `THIRD_PARTY_NOTICES.md`；Crameri 请求的引用（Zenodo doi:10.5281/zenodo.1243862；Crameri, Shephard & Heron 2020）同时放进关于页。

### 三、GPL 色板的边界

开源版是 AGPL-3.0-only，收 GPL-3.0-or-later 的色值兼容；闭源高级版组件（ADR 0198 的 sidecar）不许带。边界是**文件**，不是字段：

- 闭源组件只许读 `palettes.json`；引擎侧调用方显式 `catalog(include_copyleft=False)`。
- 构建 / 运行时开关 `TAVOTTO_NO_COPYLEFT_PALETTES=1`：引擎不收 copyleft 那份；前端构建时加载 `@palettesCopyleft` 那份文件的那一刻换成空表
  （`web/src/lib/palette/copyleftEmpty.json`；四份 vite / vitest 配置都挂 `web/scripts/palette-copyleft-path.mjs` 的
  `noCopyleftPalettes` 插件，别名目标本身保持字面量，静态 import 判据读得出），产物里没有那些色值。
- 看护：`tests/test_palettes.py`（宽松那份出现 GPL 来源即红；关掉开关后目录里没有任何 copyleft 条目）。

### 四、色键表（画布级，存在文档里）

`FigureDocument.colorKeys`（缺省 = 没用过推荐配色，旧文档逐字不变）：

```
colorKeys: {
  version: 1,
  scheme: 'crameri.batlowS',          // 当前方案 id
  keys: [{ key: 'k1', slot: 'neutral' | 0..n, identities: ['l1:…'] }],
  owned: [{ panel, gid, prop, value }] // 配色写下的 override（判「手改过」用）
}
```

- **键的证据**：高 = 显式 label 的身份相同（`artist_identity`，ADR 0083，同名跨图同摘要），自动成组；
  中 = 归一化后同名（全半角、大小写、空格）但身份不同，界面上「并入」一键确认，确认后把对方身份记进 `identities`；
  低 = 没有 label，只列在「未处理」，不参与统一（不按位置、不按空间相邻猜）。
- 文档里只存引擎给的身份摘要，不存 label 原文（与 ADR 0083 同一条隐私线）；组名每次从 manifest 现读。
- **槽一旦分配就不动**：新键取下一个空槽；删键、重排图例、换图顺序都不换色。**换方案只换「槽 → 颜色」**，不重算「键 → 槽」。
- 色数不够时明说（换方案或合并键），不静默循环复用颜色。
- **中性对照**：名字像对照的（Control、Vehicle、WT、Sham、对照…）**只建议**用中性灰，用户点确认才生效。
  这是默认可改的点（维护者 2026-11-24 未定，按设计建议先做），见 §八。
- 用户手改过的颜色（override 存在、且不是 `owned` 里配色写下的那个值）不覆盖，界面列出跳过了几处。
  系列那一格盖着的窄端也算（引擎 `alias_covers`：柱形系列的填充色盖着每一根柱）：写系列会在同一次 commit 里
  清掉各根柱上的 override，所以单独改过某根柱颜色的系列整条跳过，不让那根柱的颜色被悄悄删掉。

### 五、推荐排序

- 硬约束：色数 ≥ 分类键数；正常视觉与三种色觉模拟（Machado 2009，严重度 100：红弱 / 绿弱 / 蓝黄弱；模拟结果夹回 sRGB 色域，按屏幕上看到的算）下，
  任意两键在 CAM02-UCS 里的色差 ≥ 10（实现与 colorspacious 对拍，golden 向量 `tests/golden/palette_cvd_vectors.json`）。
- 排序：四种视觉下的最小色差为主；细线 / 小点颜色对白底 < 3:1 时提示。卡片上只写一句结论（「四种色觉下都能分开 · 最小色差 21」），
  不过关时说清是哪两组撞色。
- 首批默认推荐 batlowS：一张画布第一次建色键表时，它过得了硬约束就用它，过不了就用排第一的。
  实测（目录里收的颜色，即对白底 ≥ 3:1 之后）：batlowS 前 3 色加灰最小色差 17.1、前 4 色加灰 10.2，5 组起不过 10，
  这时会换成排第一的方案。设计稿里 21.4 那组含 #C09036，它对白底只有 2.9:1，没收进目录。

### 六、界面：方向 A

样式面板里的「配色」一节：方案下拉 + 组列表；方案下拉打开弹层（搜索、「适合这张画布」排前、期刊风格放「更多」）。
悬停或 ↑↓ 改弹层底部的预览（这几组在这套方案下的颜色 + 一句结论），同时**整张画布上的所有面板**换成那套颜色（只改显示，不 commit），
Enter / 点选写入文档（一次 commit、一步撤销），Esc 关上画布还原、什么都不变。
画布预览不走 `svgPreviewStore`：那个平面按「一次手势、一张面板」设计（会话、账本、等图都是一张），多张图同时预览另起一个平面
`store/palettePreviewStore`，DOM 写法与还原账本共用 `lib/svgStyle`。平时挂位图的面板，预览期间临时改挂**这一版**确切的矢量 SVG
（`displayView.kind === 'exact'`）好有地方画；这一版没有矢量 SVG 的（raster 档、被内存预算清掉、还没画过）不拿别的图冒充，
弹层说「有几条选用后才看得到」。图例里的色块由 matplotlib 按系列重画，预览期间不跟着变，选用后新图一到就对上。
选用之后预览挂着，等各面板新一版 SVG 换上来再退场（不先闪回旧色）；这期间撤销了，就地还原。
第一次在一张画布上建色键表、有中等证据的组待确认时，出一次确认对话框（方向 C 的精简版）；B（画布底部胶片条）放二期。
色觉预览只改显示，不写文档。

### 七、写入路径与 1.0

- 配色只写 override（`line.color`、单色 `scatter.facecolor`、`bar_series.facecolor`），与手动改色同一条链路：
  plan → overrides → `documentStore.commit`，一次撤销；回到脚本仍走 prepare → verify → commit。
- 入口常量 `RECOMMENDED_PALETTE_UI`（`web/src/lib/paletteUi.ts`）默认 `false`；单测 / e2e 用开发键 `tavotto.dev.paletteUi = '1'`。
  关着时不渲染「配色」一节，界面上写不出 `colorKeys`；没有 `colorKeys` 的文档 `planStyle` 照旧按样式的 `palette` 循环上色（有了才让位）。打开它是单独一个 PR（需要维护者点头）。

### 八、默认可改的点（实现照设计建议，维护者可以改）

1. 中性对照只建议、不自动生效（§四）。
2. 首批默认推荐 batlowS，过不了硬约束时换成排第一的方案（§五）。
3. Crameri S 只收 ≥ 3:1 的颜色（§二）——浅色留给柱、面积是第二期的事。

## 不做（留给后续期）

- 数值色图（热图、等高线、`c=数组`）的目录色图注册：worker、冷重放、导出 .py、写回四处要注册同一份定义，第二期。
- 「按原色替换」（seaborn hue、逐柱着色、箱线），伴随元素（误差带、误差棒）挂到主元素；有序组走色阶、成对组走深浅，第二期。
- 自定义 / 导入（粘贴 hex、图片取色、.json，#1487）、从原图修复、预检里的一致性体检、导出成代码，第三期。
- 跨画布导入色键表、Agent / MCP 工具，第四期。

## 后果

- 新增两份数据文件、一个纯标准库模块、文档里一个可选字段；不改协议、不改引擎的 override 语义。
- 旧文档没有 `colorKeys`：打开、编辑、保存逐字不变。
