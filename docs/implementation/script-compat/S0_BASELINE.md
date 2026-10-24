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
与原生腿同源。首轮快照 `research-baseline.json` 保留原样，尚未用新测量重跑。

| 项目 | 已知证据 | 下一步 |
| --- | --- | --- |
| Celerite | 5 次执行的 26 条曲线数据、RNG、末次协方差矩阵一致；原生对照丢失 `bbox_inches` 已由 #1321 修正。同尺寸 PDF 仍有布局偏移。 | 用新对照重跑；定位零编辑布局重算。详见下节。 |
| Leaf | 登记入口 `plot.py hppc`；首轮产品腿没传 argv。真实入口复测通过。 | 用新测量重跑。 |
| Qudit | 登记 cwd 为项目根，相对 glob；首轮产品腿用了 sandbox。真实入口复测通过。 | 用新测量重跑。 |
| Cantera | 登记 cwd 为 `data`，既不是脚本目录也不是项目根。实测（2026-10-24，本机 cantera 3.2.0 pip wheel）：`ct.Interface("sofc.yaml", "tpb")` 会在 cantera 包自带数据目录（`site-packages/cantera/data/sofc.yaml`）里找到文件，从脚本目录运行 exit 0，受保护试跑捕获 1 张图。首轮的 `launch_context_unsupported` 是测量方式，不是产品能力缺口。 | 新测量规则：登记 cwd 不可表达时，产品腿不再提前拒绝，而按产品真实行为跑（脚本目录，用户 Python 的首开默认，与把 .py 拖进 Tavotto 同源），并在报告 `detail.launch_context` 记 `{registered_cwd, product_mode, substituted: true}`；只有这样跑失败时才归 `launch_context_unsupported`（`cwd_or_data_path`），失败 stage 与原始错误一并保留。原生腿不变。待用新测量重跑。 |
| Gala | PDF 报 Cython 共享类型尺寸错误：测量环境把 Tavotto（RenderCore 的 uharfbuzz，abi3 Cython）与科研依赖（astropy 的 abi3 Cython 扩展）装进同一 venv，matplotlib PDF 后端 → `fontTools.subset` → `import uharfbuzz` 与 astropy 的同名共享类型冲突；原生在同一 venv 里存 PDF 同样失败。最小复现 `python -c "import astropy.table, uharfbuzz"`。真实产品的 worker 经 bridgeboot 私有包加载引擎，不带 Tavotto 的 site-packages，不受影响。 | 非产品问题。workflow 已拆成两个 venv（Tavotto / 科研），并断言科研 venv 无 uharfbuzz、pikepdf、pypdfium2；用新测量重跑。 |
| PySB | 我们的配方固定 SymPy 1.11.1，上游（固定提交的 setup.py）要求 `sympy>=1.12`。 | 准备配方错误，产品未运行；修配方不能记成产品修复。配方已改为 `sympy==1.12.1`（满足上游下限的最低稳定补丁版），待重跑。 |
| SGWB | TeX 准备超过 35 分钟。 | 原生和产品都未运行；准备完成后另记一次观测。 |
| Vitay | 原始 NPZ 的 r 为 2×2，脚本 delays 有 10 个。 | 保留 invalid_fixture，不改数据凑通过。 |

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

旧报告的 fidelity failed 包含“对照保存参数未对齐”（已修）和“零编辑布局漂移”（仍待定位）两项。
不改随机种子、科学计算、样本数据或像素阈值。

完整证据（报告、数值日志、两份 PDF）留在
[Actions artifact 12427200839](https://github.com/Tavotto/Tavotto/actions/runs/39779799508/artifacts/12427200839)
及本地归档，不重新塞回 Git。

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
