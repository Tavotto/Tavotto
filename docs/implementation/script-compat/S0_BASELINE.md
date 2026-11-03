# S0 探索基线与复核

状态：已取得首轮 engine 探索结果，尚未完成用户路径验收。测量修复已拆出并合入 main：
[#1307](https://github.com/Tavotto/Tavotto/pull/1307)（SVG 测量、runner_error）、
[#1321](https://github.com/Tavotto/Tavotto/pull/1321)（原生对照沿用 savefig 实际裁切框）、
[#1322](https://github.com/Tavotto/Tavotto/pull/1322)（素材清单不列虚构的无参数占位）。
本 PR 只保留研究 Guardian 与登记语料。

## 原始观测

- 产品基线：`6dcf344cef79be2c948953449b6b055be133e314`。
- 测量提交：`dc5922e642bd64a4452af8c33414e30aacaaebec`；[hosted run 39761204696](https://github.com/Tavotto/Tavotto/actions/runs/39761204696)。
- Ubuntu 24.04 / Python 3.13.16；核心依赖按 runtime-lock.json，每个项目使用独立冻结配方。
- 16 个独立上游、6 个领域；9 passed、4 failed、1 invalid_fixture、2 not_run。原生合格分母为 13。
  这是便利取样的 engine 任务结果，不是用户总体覆盖率。
- 8 个未运行样本的冻结承诺保留；不再要求代理保管或到 S5 才揭封。

| 项目 | 原始状态 | 原始失败码 |
| --- | --- | --- |
| [sgwbprobe](https://github.com/pcampeti/SGWBProbe/tree/eaa33cefba9bdcba88ab4032543f5370b7f4925b) | not_run | setup_receipt_missing |
| [qudit_tomography](https://github.com/perlinm/spin_qudit_tomography/tree/1939d4958446945f8235c72867ea65f158a9a019) | failed | stage:execute |
| [steel_dilatometry](https://github.com/Nassehk/Dilatometry-analysis/tree/53d2f4e6afbc24a08a9a43d32d021518a6a0af42) | passed | — |
| [monogenic_signal](https://github.com/pinga-lab/paper-monogenic-signal/tree/06367d48861086f7feb6a4d17721d3a43eedc86d) | passed | — |
| [infiltration_theory](https://github.com/mashadab/hyperbolic-infiltration-theory/tree/2079ad1eac07f3099379f312d0c3b28af0eb44dd) | passed | — |
| [burnman_seismic](https://github.com/geodynamics/burnman/tree/e2cfac31052273b3fa9d5ce905978e97723005e9) | passed | — |
| [aec_calibration](https://github.com/Lingkangjin/AEC-Modelling/tree/f16945d00b1647ced6b56fed693453b26a3ad738) | passed | — |
| [cantera_sofc](https://github.com/cantera/cantera/tree/dbdb9ed525fa2c0066210fda0c285c78cdd3564a) | passed | — |
| [astro_celerite_sho](https://github.com/dfm/celerite/tree/11d42b67ad97e175c98b31e2d66eee1ff49c10ac) | failed | stage:fidelity |
| [astro_ztf_m31](https://github.com/kunalb541/ZTF/tree/e4c8cf06a72bda43e7fca4eae1038c6be56cbedb) | passed | — |
| [astro_gala_draco](https://github.com/adrn/gala/tree/d66ab8b46829399b9a90f73e4a0bb4c5cd6140d4) | failed | stage:export |
| [bio_brian_hindmarsh](https://github.com/brian-team/brian2/tree/79ccfe2c0a2e5ddb2d084ab408fea2ec34dc7192) | passed | — |
| [bio_vitay_timing](https://github.com/ReScience-Archives/Vitay-2016/tree/ca09339a81528437d17a6182fe0ac199850a85e6) | invalid_fixture | native_failed |
| [bio_pysb_tyson](https://github.com/pysb/pysb/tree/9fc6bcb118683ad851e0a4d5604abe646f77cae3) | not_run | environment_setup_failed |
| [engineering_leaf_hppc](https://github.com/batterysim/nissan-leaf-data/tree/b2222ed14a678424c1ba4cd07b4adea38b9d08b0) | failed | stage:discover |
| [engineering_cruise_control](https://github.com/python-control/python-control/tree/b4cf7360138985f84afe299337ef9ee6c25e9c38) | passed | — |

固定 13 个原生合格项目的阶段通过数：discover 12；execute/capture/open/semantic/edit/replay 各 11；
export/fidelity 各 10。历史机器快照 `tests/compat/research-baseline.json` 保留原始结果，不用后来的解释覆盖旧观测。

## 重新分诊

首轮的研究产品腿没有传登记的 `launch`（argv 为空、cwd 为 sandbox），Leaf 与 Qudit 的失败来自测量，
不是产品缺陷（真实入口复测见 [2026-10-22-research-entry-retest.md](../../qa/2026-10-22-research-entry-retest.md)）。
现在 Guardian 的产品腿把登记的 argv 交给产品的运行配置、把 cwd 交给项目的工作目录模式（脚本目录或项目根），
与原生腿同源。首轮快照 `research-baseline.json` 保留原样；用新测量的重跑已在 2026-10-24 完成，见「第二轮托管观测」一节与 `research-baseline-2026-10-24.json`。

| 项目 | 已知证据 | 下一步 |
| --- | --- | --- |
| Celerite | 5 次执行的 26 条曲线数据、RNG、末次协方差矩阵一致；原生对照丢失 `bbox_inches` 已由 #1321 修正；零编辑布局偏移已由 #1359 修掉（2026-10-24 本机用真脚本复核：逐词坐标差 0，回归用例 `tests/test_zero_edit_usetex_pdf.py`）。 | 已在托管机上用新对照重跑（[run 40111589396](https://github.com/Tavotto/Tavotto/actions/runs/40111589396)）：零编辑布局偏移的 PDF 层面由本机复核；托管 fidelity 仍 failed（PNG 像素占比 0.0304），见「第二轮托管观测」的 Celerite 小节。 |
| Leaf | 登记入口 `plot.py hppc`；首轮产品腿没传 argv。真实入口复测通过。 | 已重跑通过（[run 40111589396](https://github.com/Tavotto/Tavotto/actions/runs/40111589396)）：discover → fidelity 九阶段全 passed。 |
| Qudit | 登记 cwd 为项目根，相对 glob；首轮产品腿用了 sandbox。真实入口复测通过。 | 已重跑通过（[run 40111589396](https://github.com/Tavotto/Tavotto/actions/runs/40111589396)）：1 张图捕获并通过，九阶段全 passed。 |
| Cantera | 登记 cwd 为 `data`，既不是脚本目录也不是项目根。实测（2026-10-24，本机 cantera 3.2.0 pip wheel）：`ct.Interface("sofc.yaml", "tpb")` 会在 cantera 包自带数据目录（`site-packages/cantera/data/sofc.yaml`）里找到文件，从脚本目录运行 exit 0，受保护试跑捕获 1 张图。首轮的 `launch_context_unsupported` 是测量方式，不是产品能力缺口。 | 新测量规则：登记 cwd 不可表达时，产品腿不再提前拒绝，而按产品真实行为跑（脚本目录，用户 Python 的首开默认，与把 .py 拖进 Tavotto 同源），并在报告 `detail.launch_context` 记 `{registered_cwd, product_mode, substituted: true}`；只有这样跑失败时才归 `launch_context_unsupported`（`cwd_or_data_path`），失败 stage 与原始错误一并保留。原生腿不变。已重跑通过（[run 40111589396](https://github.com/Tavotto/Tavotto/actions/runs/40111589396)）：报告 `detail.launch_context` 为 `{registered_cwd: data, product_mode: project, substituted: true}`，九阶段全 passed。 |
| Gala | PDF 报 Cython 共享类型尺寸错误：测量环境把 Tavotto（RenderCore 的 uharfbuzz，abi3 Cython）与科研依赖（astropy 的 abi3 Cython 扩展）装进同一 venv，matplotlib PDF 后端 → `fontTools.subset` → `import uharfbuzz` 与 astropy 的同名共享类型冲突；原生在同一 venv 里存 PDF 同样失败。最小复现 `python -c "import astropy.table, uharfbuzz"`。真实产品的 worker 经 bridgeboot 私有包加载引擎，不带 Tavotto 的 site-packages，不受影响。 | 非产品问题。workflow 已拆成两个 venv（Tavotto / 科研），并断言科研 venv 无 uharfbuzz、pikepdf、pypdfium2；已重跑通过（[run 40111589396](https://github.com/Tavotto/Tavotto/actions/runs/40111589396)）：2 张图全部通过，含 PDF 导出。 |
| PySB | 我们的配方固定 SymPy 1.11.1，上游（固定提交的 setup.py）要求 `sympy>=1.12`。 | 准备配方错误，产品未运行；修配方不能记成产品修复。配方已改为 `sympy==1.12.1`（满足上游下限的最低稳定补丁版），已重跑通过（[run 40111589396](https://github.com/Tavotto/Tavotto/actions/runs/40111589396)）：九阶段全 passed。 |
| SGWB | TeX 准备超过 35 分钟。 | 已另记一次观测（[run 40111589396](https://github.com/Tavotto/Tavotto/actions/runs/40111589396)）：TeX 准备在预算内完成（receipt `tex: success`、`tex_setup: null`），原生与产品均运行，九阶段全 passed。 |
| Vitay | 原始 NPZ 的 r 为 2×2，脚本 delays 有 10 个。 | 保留 invalid_fixture，不改数据凑通过。第二轮（[run 40111589396](https://github.com/Tavotto/Tavotto/actions/runs/40111589396)）依旧 invalid_fixture / `native_failed`，原生脚本自己抛 `ValueError: 'x' and 'y' must have the same size`（matplotlib errorbar）。 |

## Celerite 复核结论

[复核运行 39779799508](https://github.com/Tavotto/Tavotto/actions/runs/39779799508) 使用
`cc038006df3a5446dbeb5bb42629b5319957e34b`，其产品源码仍等于原基线 `6dcf344c`。
该历史观测不冒充新基线的产品验收。当时用 Celerite 专用的 RNG/数组观察器取证；该观察器和复核脚本已移出仓库，
结论与 artifact 链接保留在此，通用的曲线数据比对见本文末。

- **数据没有变。** 原生参照、PNG 对照、hot/fresh worker 和 census 共 5 次执行，覆盖 69 次 Figure 观察。
  3 个 axes 的 5/5/16 条曲线（其中 12 条随机样本）的完整 x/y 数组逐项同哈希；每次 seed 后状态相同，
  每次脚本结束状态相同，末次 1000×1000 协方差矩阵相同。历史 control / Tavotto PNG 均逐字节重现。
  这支持撤回本例“第三栏是两组数据”的判断，不能泛化为所有科学脚本都数值等价。
- **线宽和颜色没有被改。** 脚本结束时逐条 Line2D 的 color/linewidth 一致；两份 PDF 的
  `w/RG/rg/G/g/Tf` 指令序列相同，文字内容相同。
- **原 PNG 判据有协议问题（已由 #1321 修正）。** `compat_driver.run_native` 再存 PNG 时只传 format/dpi，
  漏掉原脚本 `bbox_inches="tight"`。它拿完整画布 640×213 对比产品裁切后的 640×203；旧报告中的 1.0 / 255
  是尺寸短路的占位值，不能当作已量出的像素差，更不能据此判断数组改变。
- **产品仍有零编辑布局差异。** 原生 PDF 与任何编辑前的产品 PDF 都是 843.84×267.84 pt；用同一
  PDFium、同一 640 px 宽度栅格化后均为 640×204。3 个 axes 的底边上移约 0.442767 pt，高度相应缩短；
  文字坐标最大差约 1.051598 pt。沿用现有像素判据：changed ratio 0.084766、mean 2.6069、max 255，
  三项都超过原阈值。重放后回零的 PDF 像素与编辑前一致，差异已在编辑前出现。

旧报告的 fidelity failed 包含“对照保存参数未对齐”（已修）和“零编辑布局漂移”两项。

**2026-10-24 定位结论：零编辑布局漂移已由 #1359 修掉。** 根因：产品给 rcParams 的 `font.family` 补 CJK 回退尾巴后变成多项，usetex 的 TexManager 只认单项 family、多项时退回 serif，刻度文字度量变了，`figure.autolayout` 的 tight 布局把 axes 底边抬高约 0.44 pt。在当前 main 上用真 `paper/figures/sho.py` 比对原生 `savefig` 与 worker 零编辑导出：页面同为 843.84×267.84 pt，45 个词最大坐标差 0，13 个 axes 外框逐项相同（matplotlib 3.11.1 / 3.11.2 相同）；把 #1359 的 usetex 判据短路回去，即重现底边上移 0.443 pt、词坐标最大差 1.051605 pt，与上面的历史观测吻合。PDF 级回归用例 `tests/test_zero_edit_usetex_pdf.py`（同形状合成图，无 latex / dvipng / cmbright 时跳过）。托管机（Linux）上的确认随下一次 S0 重跑。
不改随机种子、科学计算、样本数据或像素阈值。

完整证据（报告、数值日志、两份 PDF）留在
[Actions artifact 12427200839](https://github.com/Tavotto/Tavotto/actions/runs/39779799508/artifacts/12427200839)
及本地归档，不重新塞回 Git。

## 第二轮托管观测（2026-10-24，run 40111589396 @ fb36427d9）

- [run 40111589396](https://github.com/Tavotto/Tavotto/actions/runs/40111589396)，产品与测量均为 `fb36427d99b437be0da00cff7480dcbb43cbaf3d`（工作树干净）；Ubuntu 24.04 / Python 3.13.16，
  双 venv，16 个 case 各一个 job，每个 job 的 setup receipt：core / fonts / scientific 均 success，tex 对需要 TeX 的项目（含 celerite、sgwbprobe）为 success、其余为 skipped，
  报告与 receipt 按 #1401 绑定同一 run / attempt / job / case。
- 快照 `tests/compat/research-baseline-2026-10-24.json`（schema 与首轮相同，`cohort_id` / `product_baseline_sha`
  沿用 cohort 登记值 `6dcf344c…`，`enrollment_sha` 为本轮 `fb36427d9…`）。由 `compat_guardian_research.py summarize`
  对 16 份报告与 16 份 receipt 生成，再补 `evidence`（16 份报告、16 份 receipt、各 receipt 的两份包清单及 SHA-256）；
  `verify-snapshot --evidence-directory <归档根>` 可原样复算。证据文件不进 Git，保存在 Actions artifact 与本地归档，
  布局与首轮相同（`docs/implementation/script-compat/s0-evidence/baseline/<case>/…`）。
  首轮 `research-baseline.json` 一字未动。
- 结果：14 passed、1 failed、1 invalid_fixture；原生合格分母 15（首轮 13）。这仍是便利取样的 engine 任务结果，
  不是用户总体覆盖率。

### 两轮并排

| 项目 | 首轮（run 39761204696） | 第二轮 | 归因（只列能用提交记录对上的） |
| --- | --- | --- | --- |
| sgwbprobe | not_run / setup_receipt_missing | passed | TeX 准备在 workflow 预算内完成；预算（TeX 步 40 分钟 timeout、job 90 分钟）与 receipt 机制在 #1305 引入。首轮 35 分钟 job 超时，本轮没有复现，无法证明是预算还是 apt 速度差异 |
| qudit_tomography | failed / stage:execute | passed | #1305 产品腿传登记的 argv / cwd（项目根）；真实入口复测见 2026-10-22-research-entry-retest.md |
| steel_dilatometry | passed | passed | — |
| monogenic_signal | passed | passed | — |
| infiltration_theory | passed | passed | — |
| burnman_seismic | passed | passed | — |
| aec_calibration | passed | passed | — |
| cantera_sofc | passed | passed | 首轮已 passed；本轮报告记 `launch_context.substituted: true`（#1399 新规则） |
| astro_celerite_sho | failed / stage:fidelity | failed / stage:fidelity | 未变；#1359 修的是 PDF 零编辑布局，本轮 PNG 像素判据仍失败，见下 |
| astro_ztf_m31 | passed | passed | — |
| astro_gala_draco | failed / stage:export | passed | #1305 双 venv（Tavotto 与科研依赖分开，规避 uharfbuzz 与 astropy 的 abi3 共享类型冲突）；#1360 只是 README 提示，不是修复 |
| bio_brian_hindmarsh | passed | passed | — |
| bio_vitay_timing | invalid_fixture / native_failed | invalid_fixture / native_failed | 未变，预期保留 |
| bio_pysb_tyson | not_run / environment_setup_failed | passed | #1399 配方 `sympy==1.12.1`（满足上游 `>=1.12`） |
| engineering_leaf_hppc | failed / stage:discover | passed | #1305 产品腿传登记的 `launch` argv（`plot.py hppc`） |
| engineering_cruise_control | passed | passed | — |

固定分母阶段通过数（首轮 / 第二轮）：discover 12/13 → 15/15；execute 11/13 → 15/15；capture 11 → 15；open 11 → 15；
semantic 11 → 15；edit 11 → 15；replay 11 → 15；export 10/13 → 15/15；fidelity 10/13 → 14/15。分母由 13 变 15，
是因为 sgwbprobe 与 pysb_tyson 首轮没能运行、这次有了可信的原生对照。分母不同，阶段通过数不能直接相减当“进步幅度”。

#1401 不改变任何结果，只让本轮的 receipt 与报告可以按同一次运行配对验证。

### Celerite：fidelity 仍失败（只调查，未改产品）

**已核实**（来自本轮产物与仓库代码）：

- 比的是哪两张图：`compat_matrix.stage_fidelity` 取 **原生**参照（`compat_driver.run_native` 用真实 `savefig` 重新存出的 640 px 宽 PNG，
  沿用原脚本 `bbox_inches="tight"` 解出的裁切框，#1321）对 **产品**热 worker 的 `render_png(stem, 640)`
  （`figsession.do_render_png`：`state.fig.savefig(png, dpi=max(50, 640/w_in), **pathgeom.output_kwargs(fig))`，按产品记录的 frame 裁切）。
  两边都是同一个 matplotlib（3.11.1）的 Agg 光栅，**不经 PDFium、不是导出的 PDF/PNG**。两张图都是 640×203（旧版“尺寸短路”不再触发）。
- 时点：`run_group` 里 fidelity 在 `stage_edit` 之前，源码注释明写“保真度必须在任何编辑之前量”。所以**不是编辑后的图**，
  报告里 edit（把 `axes_0.lines_0` 的颜色改为 `#7b2cbf`）、replay、export 的字段与该比对无关。
- 灰度、先扣底噪：`pixelcompare.load_pixels` 转 `L` 灰度；`NOISE_FLOOR = 3` 先扣；三指标 changed_pixel_ratio / mean_abs_diff / max_abs_diff。
  DPI 由宽度反推，不是固定值。
- TeX：本轮 receipt `steps.tex = success`、`tex_setup = null`；两张图的文字都是 TeX 排版样式，墨量一致，说明两侧都真实使用了 usetex。
  同一托管机、同一 TeX、同一 matplotlib 比较的是两边，所以“托管机与本机 TeX/字体不同”**不能**解释这一项（它只能解释托管与本机数值的不同，解释不了同一次运行里两路的差别）。
- 阈值来历：`compat_matrix.FIDELITY_TOLERANCE = {changed_pixel_ratio: 0.004, mean_abs_diff: 1.2, max_abs_diff: 140}`，
  随 CompatBench 初版（9084e62ab）引入；代码注释的口径是“比 golden 视觉回归松一档，底噪约 1e-4，真问题约 1e-2”。该数值是经验值，没有按多图 / 多曲线 / usetex 图单独标定。
- 本轮数值：changed_pixels 3950 / 129920 = 0.030403，mean_abs_diff 0.2274（阈值 1.2，过），max_abs_diff 24（阈值 140，过）。
  **只有占比一项超阈值**，且差异幅度很小：3950 个变化像素里 2380 个差 4–7 级灰度，仅约 100 个超过 16 级。
- 本轮像素比对（用产物里的 `sho.control.png` 与 `sho.tavotto.png` 直接复算）：全图整体平移搜索最优在 (0, 0)（亚像素 ±0.5 px、步长 0.05 也是 0）；
  总墨量比 0.99998；三个子图的墨心偏移分别是 -0.028 / +0.022 / +0.005 px（x），y 方向 ≤0.013 px。差异位于曲线抗锯齿边缘与少量刻度文字边缘，分布在三个子图。
  因此**不是整图平移、缩放或元素错位**，与首轮观测的“底边上移 0.44 pt、词坐标差约 1 pt”不同类。

**推测**（没有复现，需要后续用同脚本做 PNG 级对拍确认）：

- 最可能是亚像素级的几何差：原生裁切框来自 `_tight_bbox.adjust_bbox` 的实际观测，产品裁切框是 `frame_bbox`（记录的 frame 外扩量重建）；
  两者浮点上差 1e-3 英寸量级就足以让 `dpi = 640/w_in` 与裁切原点出现 0.01–0.03 px 的偏差，Agg 对细线（3 个子图共 26 条曲线）
  的抗锯齿边缘会整体抖动，而粗体块和文字位置不变。这与“墨量守恒、无整体位移、max 只有 24”吻合，但也与 usetex 文字位图取整等其它来源吻合。
- 这类差异在其它 14 个有 fidelity 数值的 case 里没有出现（changed_pixel_ratio 全为 0，最大单像素差最多 1），说明不是通用噪声，是 Celerite 这张图独有的组合（usetex + 多条细曲线 + `bbox_inches="tight"` + 手工多子图）触发的。
- 若推测成立，这更接近“判据对细线密集图过于严苛 / 裁切框浮点口径不一致”，而不是产品改了图；但这一结论在未对拍前不应写进产品验收。

**没有做**：未改产品、阈值、判据；本机没有 celerite 依赖，没法在这次调查里重放该脚本的 PNG 对拍。
建议的下一步（需要另行批准）：用本机 TeX 跑同脚本，打印原生 `crop` 与 `frame_bbox` 的数值差、两侧 `dpi`，再决定是统一裁切口径还是给判据加“占比 + 幅度”联合条件（例如占比超限但 max_abs_diff < 32 时只记 warning）。

## 获取与复现

上游源码、数据和完整日志已移出 PR 的文件树及待合入提交历史。仓库保留提交、文件哈希、许可证记录、任务、配方与结果摘要。
下载器从固定提交获取 365 个登记文件，逐个校验 SHA-256；已实测 16 项全部一致。
下载与验证不执行脚本，缓存须在仓库外。普通 CompatBench 仍是 149 项，不会隐式下载科研语料。

```sh
export TAVOTTO_RESEARCH_CACHE=/absolute/path/outside-checkout/research-sources
python scripts/ci/compat_guardian_research.py verify
python scripts/ci/compat_guardian_research.py fetch --case research_astro_celerite_sho
```

科学依赖、TeX 和批准字体在 `compat-research-s0.yml` 的 hosted runner 中准备。显式选择 `case_id`；
`all` 才运行全部开发项目。输入提交与 SHA-256 不符、缓存被改动或登记元数据未提交时，Guardian 拒绝执行。

每个 case 的报告在 `raw.line_data` 里给出只读的曲线数据比对：原生每条 `Line2D.get_xydata()` 与产品打开的
manifest `data_fingerprint.parts.xy` 按同一摘要规则（形状 + 小端 float64）逐条比较，保留重复曲线，
报告相等 / 不同及两侧独有条数。它只覆盖两侧都是普通 Line2D 的曲线；误差棒、stem、3D 线、指纹未取得或原生取不到的曲线一律给 `unknown`（带不可比 / 未观测计数），不给 different。它只是诊断，不改变任何阶段判据。

完整旧证据已保存为本地归档，并能复算原快照。历史 GitHub artifact 按 Actions 的保留期限提供；
长期复核可使用保留同一目录结构的归档：

```sh
python scripts/ci/compat_guardian_research.py verify-snapshot --evidence-directory /absolute/archive/root
```

后续验收以真实入口、数组一致性、编辑效果、保存重开和目标平台为准；不扩充领域特例来掩盖启动上下文问题。

## 工作流出处绑定（#1363）

setup receipt 与 Guardian 报告都写 `workflow`（`run_id`、`run_attempt`、`job`、`case_id`），两处都由
`scripts/ci/compat_provenance.py` 从 `GITHUB_RUN_ID` / `GITHUB_RUN_ATTEMPT` / `GITHUB_JOB` 取值。
`compat_guardian.py validate --setup-receipt` 与 `compat_guardian_research.py verify-snapshot` / `summarize`
要求配对一致：缺任一环境变量（本地 / 非 Actions）时字段写 `null`，两边都为 `null` 才按旧规则放行；
一边有一边没有、或 run id / attempt / job / case 任一不同都失败。字段缺席的旧快照（含首轮
`research-baseline.json`）两边都缺席，按旧规则验证，原样保留。
