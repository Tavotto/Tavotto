# 出版规范 profile 与预检

> 原文出自 `src/tavotto/AGENTS.md`「出版规范 profile 与预检」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 看护用例（`src/tavotto/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`tests/test_preflight.py`、`tests/test_preflight_log_axis.py`、`tests/test_preflight_bar_baseline.py`、`tests/test_preflight_interference.py`、`tests/golden/interference_vectors.json`、`test_manifest_clip_bbox.py`、`test_profile_store.py`、`tests/golden/preflight_vectors.json`；期刊库（ADR 0155）：`tests/test_journal_catalog.py`、`web/src/lib/journals.test.ts`、`tests/golden/journal_vectors.json`；旧期刊底标签的新输出替换：`tests/test_mcp_dpi_attribution.py`、`web/src/lib/profile.test.ts` 与 `tests/golden/profile_display_vectors.json`

<!-- agents-route
sheet: src/tavotto/AGENTS.md
order: 260
改到: `profiles/publication.json`、`profiles/journals.json`、`engine/profiles.py`、`engine/journals.py`、`engine/journalremote.py`、`preflight.py`、`profilestore.py`
主题: 出版规范 profile 与预检、内置期刊库 → `profiles-and-preflight.md`
必守要点: 规则唯一权威是那份 JSON；两侧求值器靠 golden vectors 对齐；期刊 = 对 journal-base-v1 的覆盖 + 逐条出处，未规定不补
-->

- **规则的唯一权威文件是 `src/tavotto/profiles/publication.json`**（随 wheel 分发）。
  Python 走 `engine/profiles.py`（`importlib.resources` 定位，装成 wheel 后源码树的
  相对路径不存在），TypeScript 经 **`@profiles` 路径别名**整份 import 进 bundle
  （`web/vite.config.ts` / `vitest.config.ts` / `vite.mcp.config.ts` **各配一次**）。
  **绝不在任一侧硬编码同一条规则**——旧代码里 `preflight.ts` 的 6pt/300dpi 与
  ExportDialog 的 85/150/180mm 就是各写一份，规范一改两处同时开始撒谎。
- **预检有两个求值器**（`engine/preflight.py` 给 MCP，`web/src/lib/preflight.ts` 给
  画布与导出对话框）——浏览器跑不了 Python，这是必需的第二份，不是重复。
  两份靠 `tests/golden/preflight_vectors.json` 对齐：**pytest 与 vitest 各跑一遍同一份
  向量**（与 patchspec ↔ Rust 同一套纪律），只比 `id/severity/object_ids/gids/detail`，
  不比中文措辞。改任一侧跑 `python scripts/gen_preflight_vectors.py --write` 并人工读 diff。
- **面板 `kind`（矢量 / 位图）的主语是「本次导出嵌入的产物」**，不是文件后缀（#477）：
  带 override 且有脚本的 PNG 会被脚本重画成矢量，按 pdf 档判，不报
  `raster-text-not-verifiable`。前端造输入时的唯一判据是 `web/src/lib/exportRoute.ts`
  的 `embedsDiskBitmap(panel, assets, scope, formats)`，它与后端真值（原图范围
  `app._resolve_panel_source`，**受所选格式影响**：要 EPS 且不要 SVG 时 `app._original_rerender`
  让有脚本的图哪怕未编辑也重画成矢量，所以格式必须一路传到预检——`OriginalTarget.formats`；、画布范围 `rendercore.sources.needs_execution`）是严格同源对，
  靠 `tests/golden/export_route_vectors.json` 对拍（`tests/test_export_route_vectors.py` 驱动
  后端真实代码求期望，`exportRoute.test.ts` 比前端）。Python 求值器与 `PreflightPanelSpec`
  不变；Python 产品代码里 `kind` 一直是 `pdf`（MCP 入口）。
- 输入是**规范化的 figure spec**（页面 + 面板 + 文字 + 对象几何），不是画布文档也不是
  manifest 本身：同一套规则同时服务「一张图」（MCP，scale=1）与「多面板拼版」
  （画布，每面板带自己的 scale）。
- **字号按最终物理尺寸判**：manifest 的 `fontsize` 是脚本坐标系里的 pt，面板缩到 60%
  时读者量到的是 `fontsize × scale`。只看原始值 = 「缩一缩就放行」。默认规范里字号下限
  **只有一个数 8.0pt**（绝对下限，**含等号**：正好 8.0 通过，ADR 0029 §6b；ADR 0029 删掉了比它更严的那条
  8.5pt）。两条检查仍然是两条——规范把两档设成不同值时（`free-form-v1` 6.0/5.0、
  期刊覆盖）各自出场。**阈值一个字都不许写进求值器**，缺键时的兜底只有
  `profiles.FALLBACK_MIN_FONT_SIZE_PT` 一处（TS 侧同名，严格同源对）。
- **`element-outside-figure`（审计 T14）**：manifest 里可见元素的 bbox **先折进
  `clip_bbox`**（见下条），再看任一边超出 [0, 1]、折成**图自身 mm** 后大于
  `FIGURE_CLIP_EPS_MM = 0.3`（两侧同名同值，理由写在常量旁：布局框不是墨迹，
  0.56 mm 的探出已经是肉眼可见的裁切）就报，阻断级、定位到那个元素、不给自动
  修复；`figure` 与 `ticks` 组不查（前者按定义满幅，后者由单条 `ticklabel`
  代言）。它说的是「导出会静默丢内容」，不是规范偏好。
- **`clip_bbox`（manifest 的第二个几何维度）**：matplotlib 会在这个框处把元素
  切掉，框外一笔都不画。**`bbox` 不含这一维**——曲线 / 散点 / 填充的包围盒是
  未裁剪的整个数据范围，`xlim=(0, 1)` 配一个 x=1e3 的离群点能把它撑到图幅的
  几百倍宽，而图幅边界处什么都没丢。产生者只有 `manifest._clip_bbox()`，消费者
  只有上面那条规则；**命中与选中高亮仍然用 `bbox`**（改它会连带改掉命中几何与
  写回自检比的那个框）。判据**两个维度缺一不可**：`get_clip_on()` 为 False 时
  `get_clip_box()` 照样是子图框（`Axes.text()` 默认就是这个组合），为 True 时
  框却可能整个是 None（标题 / 图例 / 刻度 / spine）——只看一个都会判错。
  裁剪框包住整幅图时不发（等于什么都没裁掉），说不出裁到哪也不发（当作不裁，
  宁可多报不漏报）。看护在 `tests/test_manifest_clip_bbox.py`。
- 四档：`error`（默认阻止导出，显式确认才放行且写进 proof）/ `warn`（放行必展示）/
  `not_verifiable`（**查不了**，如位图内部文字，需人工确认并写进 proof）/ `suggestion`
  （需要上下文才能判的数据语义——误差棒、拟合置信带、曲线可分辨——全在这档，**绝不替用户裁决**；
  2026-11-21 用户裁决修订：**客观可判定的数据 ↔ 编码不一致**可以进 warn，例如下条 V-001，仍不自动修）。
  **没登记的检查项兜底为 warn**，刻意不是 suggestion——忘了登记会让用户以为它通过了。
- **`log-axis-nonpositive`（V-001，对数轴上有 ≤ 0 的值画不出来；1.0 豁免见 `docs/1.0-release-readiness.md` §1.8）**：
  事实是 axes 元素上的只读 `scale_domain`，产生者只有 `engine/manifest.scale_domain`（不进 `editable`、不进文档），
  只在某一维尺度是 `log` 时出现，色条轴 / 3D 轴不发。只数三类可见元素——折线 `get_xydata()`、散点
  `PathCollection` 的 offsets（offset transform）、`ErrorbarContainer` 的误差线段（下端单独计 `errorbar_low_nonpositive`，
  只数沿这一维的段）——且只数**这一维真正经过该轴 `transData`** 的坐标（`contains_branch_seperately`），有限值才算。
  刻意排除：柱 / 直方图、`fill_between` 等填充、色条与 LogNorm、其它 `LineCollection`（vlines、stem 的茎）、stem 基线、
  误差棒的帽。配额与 `FINGERPRINT_BUDGET` 同一个上限、**另一份计数器**（共用会把原本量得了的指纹挤成 `over_budget`）；
  `status` 不是 `ok` 时没有计数、规则不判。三张 severity 表都是 `warn`，`fix: none`（改 symlog / 删点是科学决策），
  问题面板归 `lines`。求值器 `_check_log_axis_nonpositive` ↔ `preflight.ts` `checkLogAxisNonpositive`（计数口径
  `_domain_count` ↔ `domainCount`），golden `log-axis-*`；真 worker 看护 `tests/test_preflight_log_axis.py`，
  它重出的 `tests/golden/scale_domain_real.json` 由 `web/src/lib/preflight.logAxis.test.ts` 消费。
- **`bar-baseline-truncated`（V-002，线性轴上柱的共同基线被截掉；与 V-001 同一行 1.0 豁免）**：事实是 axes 元素上的只读
  `bar_baseline`，产生者只有 `engine/manifest.bar_baseline`（不进 `editable`、不进文档），只在**直角坐标轴**
  （`ax.name == "rectilinear"`，twin / inset 都是；极坐标玫瑰图、3D 的 `bar` 不发）上有**可见**的 `BarContainer`
  （`bar` / `barh` / `hist`）时出现；竖柱记 `y`、横柱（`orientation == "horizontal"`）记 `x`，说不出方向（手工构造的
  容器没有 `orientation`）两维都写 `unavailable`。`baselines` 只收**有共同基线**的容器：所有可见柱的底（竖柱
  `get_y()`、横柱 `get_x()`，即锚点——负值柱的底仍是 0）为有限值且精确相等；浮动柱 / 甘特图不收，堆叠柱的上层
  （每根柱都正好叠在另一容器同位置那根的顶上，`_stacked_on_others`；「正好」的容差是这一组柱自身量级的 1e-9 倍，**没有绝对下限**——否则柱高 3e-10 的整组柱处处「相等」，最底层也被当成上层，Codex #2124）不收；**柱长那一维不经过该轴 `transData`**（`transform=ax.transAxes`、`get_xaxis_transform()` 上的竖柱；判据同 V-001 的 `contains_branch_seperately`，`_length_in_data`）的容器与轴范围无关，也不收，混合变换只要长度维是数据坐标就照常判；底相同的容器合并、柱数相加，数值不舍入。
  **事实只有基线**：规则从同一元素 editable 的 `[xy]lim` / `[xy]scale` 读范围与尺度——检查器里改 `ylim` 不改事实、
  只改结论；尺度必须**正是** `linear`（log / symlog / logit / asinh / 缺席一律不判，log 轴上的柱是 G-003 的事），
  范围两个有限数且 `lo < hi`（按 min / max，反转的轴一样），基线落在闭区间 `[lo, hi]` 外（容差为跨度的 1e-9）才报；
  差值柱（0 在范围内）自然不报。配额与 `FINGERPRINT_BUDGET` 同一个上限、**第三份计数器**（按柱数扣，超了写 `over_budget`
  不扣）；`status` 不是 `ok` 一律不判。三张 severity 表都是 `warn`，`fix: none`（从 0 起还是改点图是作者的决定，MCP
  手修提示只列两种改法），问题面板归 `lines`，定位到那一维的 `[xy]lim`。detail `{axis, baseline, lim_low, lim_high, bars}`，
  `worse` 是截掉的距离占跨度的比例；message key 竖柱 `barBaselineTruncatedY`（纵轴）、横柱 `barBaselineTruncatedX`（横轴），
  数字经 `_axis_num` ↔ `axisNum` 写成字符串（分档定小数位、对精确二进制值四舍五入远离 0——Python 用 `Decimal`
  `ROUND_HALF_UP`，因为 `f"{v:.nf}"` 正好一半时取偶，会与 JS `toFixed` 分叉）。求值器 `_check_bar_baseline_truncated` ↔
  `preflight.ts` `checkBarBaselineTruncated`，golden `bar-baseline-*`；真 worker 看护 `tests/test_preflight_bar_baseline.py`
  （含仓库里其它含柱真脚本的防误报扫描），它重出的 `tests/golden/bar_baseline_real.json`（带那几个 editable 字段）由
  `web/src/lib/preflight.barBaseline.test.ts` 消费。已知会报、规格未裁决的写法（维护者待定，不加特例）：inset 放大图、
  断轴图的上半幅、逐柱循环画的瀑布 / 甘特图（每个容器只有一根柱，天然有「共同基线」）。
- **干涉检测接进常规预检（V-011，用户 2026-11-23 裁决；1.0 豁免并在 V-001 那一行）**：`text-overlap` / `text-over-axes` /
  `legend-over-data` 的判据只有 `engine/interference.detect` 一份（MCP、保留式规范化、改图验收逐对用的也是它）。`preflight.run`
  在矢量面板上经 `_check_panel_interference` 把逐对结果折进 `_Sink`：文字重叠两段文字都是主语（排名 = 面积）；压到别的子图的
  主语是那段文字、被压的子图进 `detail.against`；图例压数据的主语是图例，「确定」（真折线相交，`detail.certain`）与「风险」（只有
  包围盒）同一 id、同一档位、两种文案，排名 = 命中数 + `INTERFERENCE_CERTAIN_RANK`，确定恒压过风险。文案不带 gid（问题行的主语已经是
  那个元素），毫米数沿用 `detect` 格式化好的参数。档位沿用 `publication.json` 原有登记（warn），`fix: none`、问题面板归 `layout`。
  **前端是逐行移植**（`web/src/lib/interference.ts`，不是消费后端结果：画布的预检只在浏览器求值，事实——`bbox` / `clip_bbox` /
  `geometry` / `visible` / `position`——manifest 里都已经有，不加协议字段、老后端与 playground 照样成立），数值格式化与取整走
  `pyFixed` / `pyRound`（Python 的取偶规则；`toFixed` 在 1.25 这种恰好的半值上进位）。**已经逐对调 `detect` 的调用方不许再收到聚合项**
  （同一次碰撞两把键、会被当成新增的确定性干涉挡掉事务）：所以 Python 侧 `preflight.run(…, with_interference=False)` **默认关**，
  只有 golden 生成器与对拍用例显式开（TS 的 `runSpec` 恒开，两边对的是开着的那份输出）——已经发出去的插件的桥调的是不带参数的
  `run()` 再逐对加 `detect`，插件只设引擎下限，默认开着会让它们在新引擎上重复报并挡掉规范化事务。`specfix.profile_issues` /
  `figureverify._issue_keys` 显式写 `False`；MCP 桥另按 id 滤掉（`bridge._without_interference`，不传这个参数——插件要能配旧引擎）。整体隐藏的图例的条目不参与文字重叠
  （`interference.hidden_legends`，与 `_hidden_legends` 同口径）。**斜排文字的复核只能否决**：布局框判为重叠、且一方是斜排
  （`text_block.rotation` 不是 90° 的整数倍、无 `clip_bbox`）时，按 `text_block` 的有向矩形在四条边法线上再量一次，阈值仍是
  `MIN_OVERLAP_MM`（轴对齐时退化成 dx / dy）；三角函数只产出布尔，detail / 文案不含它算出的数（跨平台末位差不进 golden），
  向量离阈值远。golden：`tests/golden/interference_vectors.json`
  （`scripts/gen_interference_vectors.py --write`，逐对，pytest 与 `web/src/lib/interference.test.ts` 各跑一遍）+
  `preflight_vectors.json` 的 `interference-*`（聚合）；其余旧向量的占位几何改成零面积点（只量规范、不量版面），结论逐条不变。
  真 worker 看护 `tests/test_preflight_interference.py`（FigureBench 防误报、`multi_panel` 的真碰撞、长标签 / 竖排 / `loc="best"` /
  inset / 色条 / 隐藏图例）。已知误报来源：误差棒容器的包围盒盖住整个数据范围（`errorbar_fit` 报「可能」）；没有 `text_block`
  的斜排文字仍按轴对齐的布局框判；`text-over-axes` 不做斜排复核。
- 文档里存**绑定 + 绑定那一刻的规则全文快照**（`FigureDocument.profile`，可选，
  schema 仍是 2）：`{id, journal?, snapshot?, snapshotVersion?, follow?}`。ADR 0029
  之前这里只存 `{id, journal}`，于是改一次全局规范，上个月定稿的图会**悄悄换一套
  判据**。规则进文档**只有 `snapshot` 这一个位置**（写入口在 `web/src/lib/
  specBinding.ts`），「有没有新版可同步」的判据是**内容不等**而不是版本号——版本号
  是人写的，谁都可能忘了改；同步由用户明确确认，进文档历史。
  期刊自定义仍走覆盖（浅合并 + 几个子对象深合并），结果带 `derived_from`/`journal`
  并进 proof report。整套换掉用 `TAVOTTO_PROFILES_FILE`。
- 全局清单（Style / Spec 两类）落在用户数据目录，磁盘入口只有
  `engine/profilestore.py`（原子写、乐观并发、损坏回退内置、**坏文件不删**）。
  磁盘上那份是更高版本时**只读不写**：判据在 `_write_user()` 一处，因为
  `_read_user()` 对「读不懂」与「一条都没有」回的是同一个空清单。
- 导出目录规则收在 `engine/config.project_export_dir(project, fallback)` —— Flask 与
  MCP server 都调它（`fallback` 是参数不是常量：app 的 `EXPORT_DIR` 会被测试 monkeypatch）。

## 内置期刊标准库（ADR 0155）

- **期刊 = 对中性底 `journal-base-v1` 的一份覆盖 + 每条规则的原文出处**，数据唯一权威是
  `src/tavotto/profiles/journals.json`（与 `publication.json` 同目录，随 wheel / PyInstaller `profiles/` 分发）。
  Python `engine/journals.py`（`list_journals` / `journal_override` / `journal_profile` / `journal_sources`）
  与 TS `web/src/lib/journals.ts`（同名 camelCase，经 **`@journals` 别名**读同一个 JSON，
  `vite.config.ts` / `vitest.config.ts` / `vite.mcp.config.ts` / `vite.playground.config.ts` / `tsconfig.app.json` 各配一次）
  是**严格同源对**，看护 `tests/golden/journal_vectors.json`（`scripts/gen_journal_vectors.py --write`；pytest 与 vitest 各跑一遍）。
  失败码闭集 `ERROR_CODES` ↔ `JOURNAL_ERROR_CODES` 逐字相同，每个校验码至少一条失败向量。
- **期刊没写的项不补**：覆盖叠在 `journal-base-v1`（无宽高比限制、不限定拉丁字体、不要求 CJK、字号下限 0、线宽预设 / 坐标轴 / 图例策略全空，
  只留越界 error、缺字形 warn、DPI 300、字号上限 72 的安全兜底）上，**绝不叠在 `lab-publication-v1` 上**，也不拿任何别的规范去填空。
  `journal-base-v1` 带 `"listed": false`：`list_profiles()` / `listProfiles()` / `builtinCatalog()` 过滤它（不进规范下拉与 `profilestore` 内置记录），但**可解析**：`load()`、`profilestore.resolve_spec()`（判内置用 `profiles.all_profile_ids()`，**不许**用 `builtins()`/`list_profiles()`）照取，前端 `resolveDocumentSpec` 对它不报 `globalMissing`（`specBinding.isHiddenBuiltin`）。栏宽 `widths_mm.single/double` 为 null = 没规定（`_validate` 两侧放宽为「正数或 null」）。
- **加载即校验，没出处的数值进不了库**：每条 rule 要有 `value`、`src`（在可见 sources 里）、非空 `quote`（只存一两句短摘录，不存整页）；键路径必须是
  merge 能精确落到的 `journal-base-v1` 字段（深合并子对象写 `<子对象>.<键>`，其余顶层单段，身份 / 展示字段不可覆盖）；`value` 类型须与底一致；
  折叠后整份过 `validate_spec`（TS `profile.validateSpecShape` 是 `_validate` 的镜像，严格同源对，同一批向量看护）。出版社 rules 先、期刊 rules 后覆盖。
- **schema 扩展位**（全文 ADR 0155 §3b）：顶层 `catalog_rev`；`info.<闭集 id>` 展示项（同样强制 src/quote，不折进 override）；规则的 `qualifier` / `value_alt` / `stage` / `mark` / `derivation` / `unset`；
  来源 `coverage`（`single_journal_sample` 不得被出版社 rules 引用）；`journal_sources()` 返回 `rule_scope` + `source_scope` + `overrides` + `affects`。期刊填了值的检查按 `SEVERITY_BUMP` 从 suggestion 抬到 warn（只抬不降、显式优先）。
  闭集常量（`INFO_IDS` / `QUALIFIERS` / `STAGES` / `COVERAGES` / `EXACT_ONLY_PATHS` / `SEVERITY_BUMP` / `KIND_OVERRIDES` / 错误码）两侧逐项相同，随 golden 向量的 `constants` 一起比。
  链接安全：url/archive_url `https://`、anchor `#:~:text=`、retrieved ISO 日期；别名撞名 NFKC+小写。
- **栏位与建议**（ADR 0155 §3b）：下游读栏位只读 `derive_page_columns`/`derivePageColumns`（`page_columns`，没写由 `widths_mm` 派生单栏 / 双栏）；`page-width` 命中任一 exact 栏位即过。
  `recommended`/`typical` 的字号与 `min_script_font_size_pt` 折成 `advisories`，只产生 suggestion 级 `font-size-recommended`（带期刊名与原话），不是判据；`affects` 为 `advice`。两侧同源，向量见 `journal_vectors.json` 与 `preflight_vectors.json`（`journal-columns-*` / `journal-advice-*`）。
- **选择器与出处卡（PR 2，前端）——入口已随 PR 3 的种子数据打开（`JOURNAL_CATALOG_UI = true`，ADR 0155 §11；快照资产根 `/assets/journal-catalog/`，文件在 `web/public/assets/journal-catalog/snapshots/`）；以下是 PR 2 当时「默认关闭」的描述，其中关着时的行为已不适用**：所有界面入口由 `web/src/lib/journalUi.ts` 的 `JOURNAL_CATALOG_UI`（默认 false）总控，测试用 localStorage 键 `tavotto.dev.journalCatalogUi='1'`；关着时界面与此 PR 之前相同（`journalUiOn.test.tsx` 看护），带期刊绑定的旧文档照旧按绑定预检。选期刊的唯一写入口 `store/actions.selectJournal`——**一次 commit** 写绑定（中性底 `journal-base-v1` + `journalOverride` + `journalRef {id, catalogRev, retrieved, label}` + 精简出处 `journalSources`）并把页宽跟到该期刊栏位里**离当前页宽最近**的一栏（`derivePageColumns`，只读这一种形状）；没规定栏宽 = 页宽不动，`info.*`（如 174 mm 只展示）不参与；撤销一步全回去，确认只用胶囊 toast。判据层 `lib/journalPick.ts`（搜索 / 分组 / `pickJournal` / 出处卡文字 / `highlightQuote` / `requirementRows`），组件在 `components/journal/`（选择器放在设置 › 期刊规范「正在使用」组；`SourceButton` 同时挂在要求清单与问题树的行尾，`sourcesForCheck` 按检查 id 反查规则，不另起第二套）。换成别的规范时 `specBinding.journalCarry` 整套放下期刊字段（期刊覆盖不得叠在课题组规范上）。目录读取只经 `store/journalCatalogStore`：本地缓存槽 `tavotto.journalCatalog`（整份过 `loadCatalog`，不过整份弃用退回种子），远程拉取 PR 把拉到的目录交给 `setCatalog`。`typical` / `recommended` / `range` 显示原话；期刊没写的要紧项说「该期刊未规定」。出处卡有快照图（`snapshot`，ADR 0155 §6：内容寻址 `snapshots/<sha256>.webp`、相对 `journalCatalogStore` 的资产根解析，缺失 / 加载失败退回摘录框）；列表最上面「不按期刊」经 `actions.clearJournal` 退回 `journalPrev`（§7）。看护：`lib/journalPick.test.ts`、`store/journalSelect.test.ts`、`components/journal/journal.test.tsx`、`e2e/journal-picker.spec.ts`（`@feature:settings.journal-picker`）。
- **Agent / MCP 入口与同一个开关（ADR 0155 §9）**：`journals.JOURNAL_CATALOG_AGENTS` ↔ `journalUi.ts` 的 `JOURNAL_CATALOG_UI` 字面值相同（同源对，`tests/test_journal_agents.py` 对拍），两边一起开。
  `select_for_agent` / `journal_ref` / `search_journals` 是 Agent 解析的唯一实现（插件只翻译）；`journal_catalog_disabled` / `journal_id_conflict` 不进库校验闭集 `ERROR_CODES`。`profiles.stamp` 带 `journal_ref`（有才带）。

- **远程目录（ADR 0155 §10，`engine/journalremote.py`；Ed25519 验签用 PyNaCl，只在验签函数里延后 import，用户 2026-11-05 拍板不手写加密）**：取用顺序 服务端（验签、校验通过）→ 本地缓存 → 内置种子。**索引**整份下载（签名信封，Ed25519 验签只认内置 `TRUSTED_KEYS`，`catalog_rev` 不得低于历史最高与种子）、**刊包**选中时按需下载（由签名索引里的 `sha256` + 字节数担保，不单独签），快照内容寻址。
  任何一步不过整次作废、缓存原样、界面不弹错；拼出的完整目录过 `journals.validate_catalog`，前端 `setCatalog` 再过 `loadCatalog`。设备令牌与缓存在 `data_dir()/journal-catalog/`，**app 与 Agent 宿主共用**（谁先到谁领，目录锁里先重读再签发）；请求只带刊 id、令牌、`rev`、`X-Tavotto-Client`，**绝无项目 / 路径 / 脚本**；只认 `TAVOTTO_NO_JOURNAL_SYNC=1`（`TAVOTTO_NO_TELEMETRY` 不代管）；两个入口 `tavotto.cn`（后端本体）与 `tavotto.com`（Vercel 薄反代，真实 IP 经共享密钥头才被后端采信；只缓存签名索引），先后用 `contactsend.region_is_cn` 同一判据。
  生产代码只信 `prod-1`，测试钥只在测试里注入（`test_production_code_does_not_trust_the_test_key`）。**Agent / MCP 读远程目录**：`bridge.list_journals`（种子 + 缓存索引，不计数、离线可用）与 `bridge.journal_selection`（先判冲突、再 `journalremote.agent_resolve`，本地没有才下载并计 1 次）；超限 / 离线 / 验签失败回 `AGENT_ERROR_CODES` 里的 `journal_quota_exceeded` / `journal_offline` / `journal_verification_failed`，不静默退回；测试进程硬关同步（`tests/conftest.py` 钉 `TAVOTTO_NO_JOURNAL_SYNC=1`）。看护：`tests/test_journal_agent_remote.py`、`tests/test_journal_remote.py`、`web/src/lib/journalCatalogSync.test.ts`；服务端 `services/journal_catalog/`（`tests/test_journal_catalog_service.py`）。
- **v2 来源事实（ADR 0155 §12）**：`engine/journalfacts_contract.py` 是服务、客户端共用的唯一事实与索引校验；`services/journal_catalog/facts.py` 只转发。`engine/journalfacts_remote.py` 只读取验签索引、按需刊包与内容寻址快照，事务化独立缓存与回滚水位，共用 v1 设备令牌，禁止重定向；`tavotto_get_journal_facts` 分页只读、不发遥测。条件、阶段、禁止项与示例只作来源事实。**例外（ADR 0155 §14 修订，2026-11-17）：无条件的数值事实由 `engine/journalfacts_fold.py` 折成 v1 同形规则，前端 `lib/journalV2.ts` 按项叠到期刊绑定上（v2 优先），带条件的只进「需要你确认」；求值器与阈值不变（唯一新增判据：数据线线宽下限 `min_line_width_pt` / `line-width-below-min`，可选键、`journal-base-v1` 默认 0 = 不判，只判 `preflight.DATA_LINE_ROLES`——折线 / 线集 / 有可见描边的柱，按 线宽 × 面板缩放 比，等于不报；散点与填充的描边 manifest 分不出有无，不判；与 `preflight.ts` 的 `DATA_LINE_ROLES` / `isDataLine` 同源，golden `journal-min-line-width-*`，真渲染看护 `tests/test_preflight_min_line_width_real.py`），看护 `tests/test_journalfacts_fold.py` + `tests/golden/journal_v2_fold_vectors.json` + `web/src/lib/journalV2.test.ts`。** 无 v2 数据不能用 v1 / 种子冒充；旧版、既有项目快照照旧。详见 `services/journal_catalog/FACTS-V2.md`。
- **真实期刊数据严禁凭记忆写**：只收人工核对过、带原文摘录的条目；单元测试 / golden 向量只许用夹具期刊（`fx-*`，`example.invalid`）。
  项目快照（`FigureDocument.profile.snapshot`）机制原样适用，期刊库更新不会改已定稿项目的结论。

## 速查表原要点（2026-09-25 迁入，#608）

`src/tavotto/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 规则唯一权威是那份 JSON，两侧求值器靠 golden vectors 对齐（只比判据）
- 字号按最终物理尺寸判、阈值不进求值器
- `clip_bbox` 只给 `element-outside-figure` 用
- 没登记的检查项兜底 warn
- 磁盘更高版本只读不写
