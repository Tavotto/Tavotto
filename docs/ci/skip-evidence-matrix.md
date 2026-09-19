# 快线 skip 的补验矩阵（2026-09-19，main @ addec48d，run 35416413461）

审计任务书第四节第 3 条：**55 个 skip 不等于 55 个缺失测试，但也不等于 55 个别处补验过
的测试**。这张表逐条回答四件事：快线为什么跳 → 哪个 job 提供它缺的前提 → **同一提交**上
有没有实际通过的证据 → 发布要不要求。回答不了的标 `unknown`，不把「预期有某个 job 会跑」
写成「通过」。

数字部分由 `scripts/dev/skip_matrix.py` 从 junit 报告算出（`gh run download <run-id>
-p "pytest-*"` 之后跑它）；「哪个 job 提供前提」「发布要不要求」是人工登记的，改了 CI
拓扑要回来改这张表。**表头的 run 就是证据所在的那一个 run**：它是 `merge_group` 的完整
CI，head 等于表头的 main 提交；别的提交上「曾经绿过」不算这张表的证据。表里的 **row 编号
是稳定引用**（`ci.yml` 与 `docs/RELEASING.md` 按 #1 / #5 / #10 指过来），行只增不重排；
一行清零了就留着标 0。

五条 pytest lane 在这个 run 上的数字（4962 条 testcase，全部 0 失败）：

| lane | passed | skipped |
| --- | ---: | ---: |
| backend-fast ubuntu 3.10 | 4901 | 61 |
| backend-fast ubuntu 3.13 | 4905 | 57 |
| backend-fast ubuntu 3.14 | 4905 | 57 |
| backend-platforms macos 3.13 | 4915 | 47 |
| backend-platforms windows 3.13 | 4876 | 86 |

以 ubuntu 3.13 的 57 条为基准，**别的 pytest lane 上真的 pass 过的只有 11 条**；其余 46 条
要么在非 pytest 的 job 里按产物验（有证据但不是这条用例本身——其中 26 条现在有 job 级的
「真跑了」证据），要么谁都没跑过。

上一版（2026-09-17，main @ `6fe6e38d`，run 35176687604）是 55 条：这版少了 #17 那条死用例
（#409 修了），多了 #18 / #19 的 3 条结构性 skip。**中间的 v0.15.0（`eb140494`，
run 35379456735）是 67 条**——比这版多出的 10 条是 `tests/test_override_sequences.py` 当时
用 `xfail(strict)` 挂着的已知分岔，junit 把 xfail 记成 `<skipped>`；#420 把它们改成正向断言
之后不再是 skip。所以「v0.15.0 有 67 个 skip」这个数字里有 10 个不是 skip，别拿它当发行
版的测试统计。

## 矩阵

判定四档：**covered**（同一提交上有 lane 让这条用例 pass）/ **product-path**（同一提交上有
job 验了同一件事的产品路径，但不是这条用例）/ **manual**（只有人手动开环境变量才跑，CI 里
没有通道）/ **unknown**（没有任何通道，或通道存在但没有证据）。

| # | 条数 | 快线跳过原因 | 用例文件 | 前提由谁提供 | addec48d 上的证据 | 发布要求 | 判定 |
| --- | ---: | --- | --- | --- | --- | --- | --- |
| 1 | 12 | 没有 `tavotto-workerd` 产物 | `test_equivalence_matrix`（workerd 三路等价 ×N、inline svg 逐字节、preview_png 状态中立）、`test_worker_roundtrip`（workerd 路径） | `workerd` job 在 `cargo test` 之后装科学栈、对着刚建的 `target/debug/tavotto-workerd` 跑 `-k workerd`（#409）；步骤末尾按 junit 核「真跑了几条」，全 skip 也算失败 | `workerd` job 步骤「校验 workerd 三路等价（对着刚建的二进制跑 12 条）」success，日志 `真跑了 12 条 workerd 用例`（junit 留在 runner.temp，不上传，所以 `skip_matrix.py` 看不见它） | 是（workerd 是发行的 supervisor） | **covered**（job 级） |
| 2 | 6 | 没有真实插件构建物 | `test_plugin_candidate` | `plugin-candidate` job 设 `TAVOTTO_PLUGIN_CANDIDATE` 后跑这一文件（PR 上就跑） | `plugin-candidate` success | 是（插件发行） | **covered**（job 级，不进 junit） |
| 3 | 7 | 中日韩字体 / 字体集 / 中文字体缺失（`test_cjk_figure_text` ×6、`test_equivalence_matrix::test_cjk_scenario_three_ways_agree`） | 同左 | `backend-platforms`（macOS / Windows 有系统 CJK 字体） | macOS + Windows lane 全部 pass | 是（图内中文回退链，ADR 0045） | **covered** |
| 4 | 4 | 本机没构建过 runtime | `test_runtime_build`（真渲染 / 不往自己里写 / manifest 自验门 / 许可证与声明进包） | `windows-exe-smoke` / `macos-app-smoke` / `package` 都 `build_worker_runtime.py --clean`，但**没有一个跑 `test_runtime_build`** | 冒烟 job 全绿（`smoke_desktop` 用产物真渲染） | 是（内置渲染 runtime） | **product-path**；许可证 / 声明进包那条 unknown |
| 5 | 3 | 先 `python -m build`（或 `TAVOTTO_DIST_DIR`） | `test_tutorial`（wheel / sdist 含全部教程资源、装好后能 import 到） | `package` job 冒烟之后用冒烟 venv 的解释器跑 `TAVOTTO_DIST_DIR=dist pytest tests/test_tutorial.py -k "wheel or sdist or installed_wheel"`（#409） | `package` ×4 步骤「校验产物里的教程资源（test_tutorial 的 wheel / sdist / 装开的 wheel 三条）」success；`-qq` 下只见四个点、没有 `s` | 是（教程随 wheel 分发，ADR 0039） | **covered**（job 级） |
| 6 | 2 | 桌面发行形态（Linux 没有） | `test_codex_plugin`（只装桌面版也能被发现 / 装了桌面没 CLI 的错误） | `backend-platforms` macOS | macOS lane pass | 是 | **covered** |
| 7 | 2 | Times New Roman 没装 | `test_glyph_coverage_figure` | `backend-platforms` macOS | macOS lane pass（Windows 也没有 TNR？——Windows lane 上同样 skip，见 junit） | 否（回退链有别的看护） | **covered**（单 lane） |
| 8 | 4 | 画布产物未构建 | `test_mcp_server`（3）、`test_mcp_stdio`（1） | `plugin-candidate` job 设 `TAVOTTO_MCP_WIDGET` 后跑 `-k "widget or canvas"` | `plugin-candidate` success | 是（内嵌画布） | **covered**（job 级） |
| 9 | 2 | 没有 tauri 渲染出来的 NSIS 中间脚本 | `test_nsis_template` | `desktop-tauri.yml`（tag / 手动）与 `nightly.yml` 设 `TAVOTTO_NSIS_GENERATED` 后跑 | **不在本 run**（不同 workflow、不同提交） | 是（Windows 安装包） | **有通道，非同提交** |
| 10 | 6 | 真 codex CLI / 网络 marketplace（`TAVOTTO_SKIP_REAL_CODEX` / `TAVOTTO_CODEX_REAL_SMOKE` / `TAVOTTO_CODEX_NET_SMOKE` / `TAVOTTO_REAL_CLI_SMOKE` / PATH 里的 codex） | `test_codex_real_client`（2）、`test_codex_install_cli`、`test_codex_plugin`（2）、`test_ai_agents` | **没有任何 workflow 设这些变量**；ADR 0012 明说默认 skip、手动 opt-in | 无（CI）；已写进 `docs/RELEASING.md`「只有人手动开环境变量才跑的六条」（#409）——**v0.15.0 发版有没有跑过没有记录** | 插件发行前该有人跑一次 | **manual**——发版记录里要留一行「跑过 / 没跑」 |
| 11 | 1 | `pnpm` + `node_modules` 真构建一次画布 | `test_independent_frontend_prs` | `plugin-candidate` job 单独跑它 | `plugin-candidate` success | 否（并行 PR 治理） | **covered**（job 级） |
| 12 | 1 | 浅克隆 | `test_blame_ignore_revs` | 需要 `fetch-depth: 0`：`main-landing-audit` 是全克隆但只跑 5 个文件、不含它；lab 全克隆 + 全量 pytest，无 `-rs` / junit | 无 | 否 | **unknown**（通道可能存在于 lab，没有证据） |
| 13 | 1 | 探针二进制未构建 | `test_update_chain_gates::test_selftest_passes_against_the_real_probe` | `nightly.yml` 建 `updater-extract-probe` 后跑更新链脚本（`--probe`），**不跑这条 pytest** | nightly 的脚本自验（非同提交） | 是（更新链） | **product-path**（nightly）；用例本身 unknown |
| 14 | 1 | `node_modules` 未安装 | `test_plugin_stage::test_a_real_build_writes_only_the_requested_output` | `plugin-candidate` 用同一个脚本真建插件（产品路径），不跑这条 | `plugin-candidate` success | 否 | **product-path** |
| 15 | 1 | 唯一装了 matplotlib 的解释器同时装了 Tavotto | `test_bridge_e2e::test_the_runner_never_imports_tavotto` | 所有 lane 都 `pip install -e .` 到同一个解释器；需要一个「有 matplotlib、没 tavotto」的解释器 | 无（行为性判据由同文件别的用例盖住，见其 skip 文案） | 是（native bridge 边界） | **unknown**——补法：任一 lane 另建一个只装 matplotlib 的 venv 当 `user_python` |
| 16 | 1 | 这台机器的 Python 没有自带 sitecustomize | `test_bridge_injection_models::test_naive_sitecustomize_breaks_homebrew_python` | 只在 Homebrew Python 那类「有人占了 sitecustomize 坑」的解释器上有意义；CI 的 setup-python 没有 | 无 | 否（它证明的是「别用 sitecustomize」这个决定的理由） | **unknown**（环境条件，不是缺口） |
| 17 | 0 | ~~「协议已脱离草案」~~ | `test_legal_contribution_policy::TestAgreementVersionBinding::test_draft_agreements_forbid_a_configured_provider` | #409 让用例自己造一份 `-draft` 策略来验，前提不再依赖仓库里的策略是不是草案 | ubuntu 3.13 lane pass | 否 | **已修**（保留行号；上一版判 dead） |
| 18 | 2 | `axestraversal` / `pathgeom` 没有 RESTORE 表 | `test_engine_family_modules::test_family_restore_table_is_merged_into_overrides[axestraversal\|pathgeom]` | 用例按 `FAMILIES` 全表参数化，族模块没导出 `RESTORE` 就 skip（基座与遍历族本来就没有还原表） | — | 否 | **不适用**（结构性：参数集比守的对象宽；收窄参数集就能归零，不是缺口） |
| 19 | 1 | `FIXED` 参数集为空 | `test_override_sequences::test_fixed_regressions[NOTSET]` | 固定回归表今天是空的（#412–#414 一条都还没修好），pytest 对空参数集记一条 skip | — | 否 | **不适用**（结构性：第一条分岔修好挪进 `FIXED` 那天它自然消失） |

合计 57。分布：covered 37（11 条进了别的 lane 的 junit、26 条在 job 级不进 junit）、
product-path 6、有通道但非同提交 2、manual 6、unknown 3、不适用 3。
对比上一版：covered 22 → 37、product-path 18 → 6、unknown 6 → 3——三件便宜补法（#409）
把 #1 的 12 条与 #5 的 3 条从「有产品路径 / 没人跑」变成了「同一提交上真跑过」。

## 三件便宜补法：已做（#409）

1. **#17 死用例**：用例自己把一份协议改成 `-draft` 再验，规则的看护回来了，行归零。
2. **#5 `package` job 加一步**：对着刚打好的 wheel / sdist 跑 `test_tutorial` 那三条。
3. **#1 workerd 12 条**：`workerd` job 在 `cargo test` 之后真跑，步骤末尾按 junit 数「真跑了几条」。

## 还剩的

- **manual 那 6 条（#10）**：通道在 `docs/RELEASING.md`，但「跑没跑」没有记录点——发版事实里
  加一行，否则下次审计仍只能写 unknown。
- **unknown 3 条**：#15 值得补一个「只装 matplotlib、不装 tavotto」的解释器当 `user_python`；
  #12 要 `fetch-depth: 0` 的 lane 跑它；#16 是环境条件。
- **product-path 6 条**：#4 的许可证 / 声明进包那条与 #13 的探针自验仍只有产品路径证据。
- 改 CI 拓扑归 `ci-control-plane` 域，得单独开 PR。
