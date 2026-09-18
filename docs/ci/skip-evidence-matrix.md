# 快线 skip 的补验矩阵（2026-09-17，main @ 6fe6e38d，run 35176687604）

审计任务书第四节第 3 条：**55 个 skip 不等于 55 个缺失测试，但也不等于 55 个别处补验过
的测试**。这张表逐条回答四件事：快线为什么跳 → 哪个 job 提供它缺的前提 → **同一提交**上
有没有实际通过的证据 → 发布要不要求。回答不了的标 `unknown`，不把「预期有某个 job 会跑」
写成「通过」。

数字部分由 `scripts/dev/skip_matrix.py` 从 junit 报告算出（`gh run download <run-id>
-p "pytest-*"` 之后跑它）；「哪个 job 提供前提」「发布要不要求」是人工登记的，改了 CI
拓扑要回来改这张表。

五条 pytest lane 在这个 run 上的数字（4858 条 testcase，全部 0 失败）：

| lane | passed | skipped |
| --- | ---: | ---: |
| backend-fast ubuntu 3.10 | 4799 | 59 |
| backend-fast ubuntu 3.13 | 4803 | 55 |
| backend-fast ubuntu 3.14 | 4803 | 55 |
| backend-platforms macos 3.13 | 4813 | 45 |
| backend-platforms windows 3.13 | 4774 | 84 |

以 ubuntu 3.13 的 55 条为基准，**别的 pytest lane 上真的 pass 过的只有 11 条**；其余 44 条
要么在非 pytest 的 job 里按产物验（有证据但不是这条用例本身），要么谁都没跑过。

## 矩阵

判定四档：**covered**（同一提交上有 lane 让这条用例 pass）/ **product-path**（同一提交上有
job 验了同一件事的产品路径，但不是这条用例）/ **manual**（只有人手动开环境变量才跑，CI 里
没有通道）/ **unknown**（没有任何通道，或通道存在但没有证据）。

| # | 条数 | 快线跳过原因 | 用例文件 | 前提由谁提供 | 6fe6e38d 上的证据 | 发布要求 | 判定 |
| --- | ---: | --- | --- | --- | --- | --- | --- |
| 1 | 12 | 没有 `tavotto-workerd` 产物 | `test_equivalence_matrix`（workerd 三路等价 ×N、inline svg 逐字节、preview_png 状态中立）、`test_worker_roundtrip`（workerd 路径） | 没有 lane 在 `cargo build` 之后跑 pytest：`workerd` job 只跑 cargo test；`macos-app-smoke` / `windows-exe-smoke` 建了 release 二进制但只跑冒烟脚本；lab 的 pytest 排在 `cargo test` **之前**（持久 runner 上可能撞见上一次的 `target/debug`，但 pytest 不带 `-rs`、无 junit，看不出跑没跑） | 冒烟 job 全绿（产品路径：产物自带的 workerd 被自动找到并渲染） | 是（workerd 是发行的 supervisor） | **product-path**；用例本身 unknown |
| 2 | 6 | 没有真实插件构建物 | `test_plugin_candidate` | `plugin-candidate` job 设 `TAVOTTO_PLUGIN_CANDIDATE` 后跑这一文件（PR 上就跑） | `plugin-candidate` success | 是（插件发行） | **covered**（job 级，不进 junit） |
| 3 | 7 | 中日韩字体 / 字体集 / 中文字体缺失（`test_cjk_figure_text` ×6、`test_equivalence_matrix::test_cjk_scenario_three_ways_agree`） | 同左 | `backend-platforms`（macOS / Windows 有系统 CJK 字体） | macOS + Windows lane 全部 pass | 是（图内中文回退链，ADR 0045） | **covered** |
| 4 | 4 | 本机没构建过 runtime | `test_runtime_build`（真渲染 / 不往自己里写 / manifest 自验门 / 许可证与声明进包） | `windows-exe-smoke` / `macos-app-smoke` / `package` 都 `build_worker_runtime.py --clean`，但**没有一个跑 `test_runtime_build`** | 冒烟 job 全绿（`smoke_desktop` 用产物真渲染） | 是（内置渲染 runtime） | **product-path**；许可证 / 声明进包那条 unknown |
| 5 | 3 | 先 `python -m build`（或 `TAVOTTO_DIST_DIR`） | `test_tutorial`（wheel / sdist 含全部教程资源、装好后能 import 到） | `package` job 跑了 `python -m build`，但没设 `TAVOTTO_DIST_DIR`、没跑这三条 | `package` ×4 success（`package_smoke.py` 装 wheel 起服务，**不数教程资源**） | 是（教程随 wheel 分发，ADR 0039） | **unknown**——最便宜的补法：`package` job 里加 `TAVOTTO_DIST_DIR=dist pytest tests/test_tutorial.py -k "wheel or sdist"` |
| 6 | 2 | 桌面发行形态（Linux 没有） | `test_codex_plugin`（只装桌面版也能被发现 / 装了桌面没 CLI 的错误） | `backend-platforms` macOS | macOS lane pass | 是 | **covered** |
| 7 | 2 | Times New Roman 没装 | `test_glyph_coverage_figure` | `backend-platforms` macOS | macOS lane pass（Windows 也没有 TNR？——Windows lane 上同样 skip，见 junit） | 否（回退链有别的看护） | **covered**（单 lane） |
| 8 | 4 | 画布产物未构建 | `test_mcp_server`（3）、`test_mcp_stdio`（1） | `plugin-candidate` job 设 `TAVOTTO_MCP_WIDGET` 后跑 `-k "widget or canvas"` | `plugin-candidate` success | 是（内嵌画布） | **covered**（job 级） |
| 9 | 2 | 没有 tauri 渲染出来的 NSIS 中间脚本 | `test_nsis_template` | `desktop-tauri.yml`（tag / 手动）与 `nightly.yml` 设 `TAVOTTO_NSIS_GENERATED` 后跑 | **不在本 run**（不同 workflow、不同提交） | 是（Windows 安装包） | **有通道，非同提交** |
| 10 | 6 | 真 codex CLI / 网络 marketplace（`TAVOTTO_SKIP_REAL_CODEX` / `TAVOTTO_CODEX_REAL_SMOKE` / `TAVOTTO_CODEX_NET_SMOKE` / `TAVOTTO_REAL_CLI_SMOKE` / PATH 里的 codex） | `test_codex_real_client`（2）、`test_codex_install_cli`、`test_codex_plugin`（2）、`test_ai_agents` | **没有任何 workflow 设这些变量**；ADR 0012 明说默认 skip、手动 opt-in | 无 | 插件发行前该有人跑一次（`docs/RELEASING.md` 未列） | **manual**——建议写进发版清单 |
| 11 | 1 | `pnpm` + `node_modules` 真构建一次画布 | `test_independent_frontend_prs` | `plugin-candidate` job 单独跑它 | `plugin-candidate` success | 否（并行 PR 治理） | **covered**（job 级） |
| 12 | 1 | 浅克隆 | `test_blame_ignore_revs` | 需要 `fetch-depth: 0`：`main-landing-audit` 是全克隆但只跑 5 个文件、不含它；lab 全克隆 + 全量 pytest，无 `-rs` / junit | 无 | 否 | **unknown**（通道可能存在于 lab，没有证据） |
| 13 | 1 | 探针二进制未构建 | `test_update_chain_gates::test_selftest_passes_against_the_real_probe` | `nightly.yml` 建 `updater-extract-probe` 后跑更新链脚本（`--probe`），**不跑这条 pytest** | nightly 的脚本自验（非同提交） | 是（更新链） | **product-path**（nightly）；用例本身 unknown |
| 14 | 1 | `node_modules` 未安装 | `test_plugin_stage::test_a_real_build_writes_only_the_requested_output` | `plugin-candidate` 用同一个脚本真建插件（产品路径），不跑这条 | `plugin-candidate` success | 否 | **product-path** |
| 15 | 1 | 唯一装了 matplotlib 的解释器同时装了 Tavotto | `test_bridge_e2e::test_the_runner_never_imports_tavotto` | 所有 lane 都 `pip install -e .` 到同一个解释器；需要一个「有 matplotlib、没 tavotto」的解释器 | 无（行为性判据由同文件别的用例盖住，见其 skip 文案） | 是（native bridge 边界） | **unknown**——补法：任一 lane 另建一个只装 matplotlib 的 venv 当 `user_python` |
| 16 | 1 | 这台机器的 Python 没有自带 sitecustomize | `test_bridge_injection_models::test_naive_sitecustomize_breaks_homebrew_python` | 只在 Homebrew Python 那类「有人占了 sitecustomize 坑」的解释器上有意义；CI 的 setup-python 没有 | 无 | 否（它证明的是「别用 sitecustomize」这个决定的理由） | **unknown**（环境条件，不是缺口） |
| 17 | 1 | 「协议已脱离草案」 | `test_legal_contribution_policy::TestAgreementVersionBinding::test_draft_agreements_forbid_a_configured_provider` | 永远不会再成立的前提（用例只在**仓库里的**策略还是草案时才验；协议已定版 1.0） | — | 否 | **dead**——但守的规则（草案上不许配置 provider）本身还在 gate 里：改成自己造一份草案策略来验，而不是删 |

合计 55。分布：covered 22（11 条进了别的 lane 的 junit、11 条在 job 级不进 junit）、
product-path 18、有通道但非同提交 2、manual 6、unknown 6、dead 1。

## 三件该做的（按便宜程度）

1. **#17 那条死用例**：它的前提（仓库里的策略仍是草案）永远不会再成立，留着只会让「55 个 skip」
   这个数字比真相大 1；但它守的规则（草案上不许配置 provider，`gate.validate_policy` 里的一条）
   还在——正确做法是让用例**自己把一份协议改成 `-draft`** 再验，而不是删掉规则的唯一看护。
2. **`package` job 加一步**（#5）：`TAVOTTO_DIST_DIR=dist python -m pytest tests/test_tutorial.py
   -k "wheel or sdist or installed_wheel"`——产物已经在那儿，只是没人对着它跑这三条。
3. **workerd 那 12 条要有一个真跑的地方**（#1）：`workerd` job 已经 `cargo test`（有
   `target/debug`），再加一步 `python -m pytest tests/test_equivalence_matrix.py
   tests/test_worker_roundtrip.py -k workerd`（要 matplotlib）；或者让 `backend-platforms`
   在 `cargo build` 之后跑。冒烟 job 验的是「产物自带的 workerd 能渲染」，验不了「三路
   逐字节等价」。

`manual` 那 6 条（真 codex / 网络）写进 `docs/RELEASING.md` 的发版清单；`unknown` 里
#15 值得补一个「只装 matplotlib 的解释器」，其余是环境条件。这些都不在本 PR 里做——
改 CI 拓扑归 `ci-control-plane` 域，得单独开。
