# S0 探索基线与复核

状态：已取得首轮 engine 探索结果，尚未完成用户路径验收。原 PR #1305 保持草稿；SVG / runner_error
测量修复拆到 [#1307](https://github.com/Tavotto/Tavotto/pull/1307)。

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

| 项目 | 已知证据 | 下一步 |
| --- | --- | --- |
| Celerite | 零编辑 PNG 为 640×213 与 640×203；比较器在尺寸不同时提前返回。此前“先修边距/布局”的分诊撤回。 | 比较完整 x/y 数组与 RNG 状态，并对照原脚本 PDF 的尺寸及保存参数。数据差异、RNG/BLAS 根因目前均未确认。 |
| Leaf | 原生带 `hppc` 出 5 图，engine 探针没有传参；错误提示已有 CLI 参数入口。 | 经真实 CLI/桌面入口验证 argv，不据此宣称全产品不支持参数。 |
| Qudit | 原生完成；engine 的相对 glob 为空。 | 记录实际 cwd 与命中文件，验证已有项目目录运行路径。 |
| Gala | PNG/SVG 正常；PDF 报 Cython 共享类型尺寸错误。原生任务没有输出 PDF。 | 补同环境原生 PDF 和完整异常栈，再判断具体扩展。 |
| PySB | 我们的配方固定 SymPy 1.11.1，上游要求 >=1.12。 | 准备配方错误，产品未运行；修配方不能记成产品修复。 |
| SGWB | TeX 准备超过 35 分钟。 | 原生和产品都未运行；准备完成后另记一次观测。 |
| Vitay | 原始 NPZ 的 r 为 2×2，脚本 delays 有 10 个。 | 保留 invalid_fixture，不改数据凑通过。 |

## 获取与复现

上游源码、数据和完整日志已移出 PR 的当前文件树。仓库保留提交、文件哈希、许可证记录、任务、配方与结果摘要。
下载器从固定提交获取 365 个登记文件，逐个校验 SHA-256；已实测 16 项全部一致。
下载与验证不执行脚本，缓存须在仓库外。普通 CompatBench 仍是 149 项，不会隐式下载科研语料。

```sh
export TAVOTTO_RESEARCH_CACHE=/absolute/path/outside-checkout/research-sources
python scripts/ci/compat_guardian_research.py verify
python scripts/ci/compat_guardian_research.py fetch --case research_astro_celerite_sho
```

科学依赖、TeX 和批准字体在 `compat-research-s0.yml` 的 hosted runner 中准备。显式选择 `case_id`；
`all` 才运行全部开发项目。Celerite 的 `numeric_diagnostics` 只读取数组和 RNG 状态，不改变种子或计算。
输入提交与 SHA-256 不符、缓存被改动或登记元数据未提交时，Guardian 拒绝执行。

完整旧证据已保存为本地归档，并能复算原快照。历史 GitHub artifact 按 Actions 的保留期限提供；
长期复核可使用保留同一目录结构的归档：

```sh
python scripts/ci/compat_guardian_research.py verify-snapshot --evidence-directory /absolute/archive/root
```

后续验收以真实入口、数组一致性、编辑效果、保存重开和目标平台为准；不扩充领域特例来掩盖启动上下文问题。
