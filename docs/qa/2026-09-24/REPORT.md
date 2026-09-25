# Tavotto 可靠性 QA 执行报告（2026-09-24 规范）

- 规范：`Tavotto_QA_Test_Spec_2026-09-24.md`（实施规格与验收清单）
- 被测源码：`62af729c2f84652bf3979c25b0c3117fcfa756a5`（= 执行时的 `origin/main`）
- 执行日期：2026-09-25，macOS 27.0（Darwin 27.0.0，arm64）；后端 `.venv` Python 3.13.11；worker 科学栈 matplotlib 3.10.8（Homebrew Python 3.13）；Chromium / WebKit 由仓库锁定的 Playwright 提供
- 执行方式：每节一个执行 Agent，在隔离 worktree 里执行，产出台账、日志、复现脚本和回归测试；随后由一个独立复核 Agent 在另一个 worktree 里逐字重跑命令，尽量推翻结论，只把改判写进 `verification.json`。
- **最终裁定以复核结果（`audited_verdict`）为准。**

> 本报告不能表述为「Tavotto 已证明任意操作都不丢位置」。规范 §10 的两条第一批强验收里，ACCEPT-2 通过，ACCEPT-1 只做到 partial（导出段有缺陷），见下文。

## 1. 结论一览

| | 数量 |
| --- | --- |
| 用例总数（规范全部 ID + 旗舰两分支 + 两条强验收） | 59 |
| 复核后 **pass** | 19 |
| 复核后 **partial**（部分验收标准有证据，其余未覆盖或有缺口） | 24 |
| 复核后 **fail**（有复现的产品缺陷，或验收标准不成立） | 16 |
| not_run（无证据的「未执行」） | 0（ENV-08 原判 not_run，复核在本机造出 conda 布局实测后改为 partial） |
| 复核重跑且计数、退出码与原记录一致 | 59 / 59 |
| 复核改判 | 6 条：GEO-02、SCI-01、SCI-02、LONG-03、RAND-01 由 pass 降为 partial；ENV-08 由 not_run 升为 partial |

产品结果分布（§0.1 枚举）：自动成功 31、引导后成功 6、安全停止 6、功能失败 15、未执行 1。
**安全停止不计入自动兼容成功数。**

## 2. 逐条结果

「覆盖」一列指执行前仓库既有测试的覆盖状态（QA-1 缺口台账）。每条的命令、退出码、日志 sha256、夹具 hash、反证记录都在对应节的 `ledger.json`；复核的重跑记录在同目录的 `verification.json` 与 `logs/verify/`。

| 用例 | 覆盖 | 产品结果 | 执行判定 | **复核判定** | 证据 |
| --- | --- | --- | --- | --- | --- |
| GEO-01 | 部分覆盖 | 功能失败 | partial | **partial** | [geo](./geo/ledger.json) |
| GEO-02 | 部分覆盖 | 自动成功 | pass | **partial** | [geo](./geo/ledger.json) |
| GEO-03 | 部分覆盖 | 功能失败 | fail | **fail** | [geo](./geo/ledger.json) |
| GEO-04 | 部分覆盖 | 自动成功 | partial | **partial** | [geo](./geo/ledger.json) |
| GEO-05 | 部分覆盖 | 自动成功 | partial | **partial** | [geo](./geo/ledger.json) |
| GEO-06 | 部分覆盖 | 自动成功 | pass | **pass** | [geo](./geo/ledger.json) |
| GEO-07 | 部分覆盖 | 功能失败 | fail | **fail** | [geo](./geo/ledger.json) |
| GEO-08 | 部分覆盖 | 功能失败 | fail | **fail** | [geo](./geo/ledger.json) |
| GEO-09 | 部分覆盖 | 自动成功 | pass | **pass** | [geo](./geo/ledger.json) |
| GEO-10 | 部分覆盖 | 功能失败 | fail | **fail** | [geo](./geo/ledger.json) |
| STATE-01 | 部分覆盖→本次补全 | 自动成功 | pass | **pass** | [state](./state/ledger.json) |
| STATE-02 | 部分覆盖 | 功能失败 | fail | **fail** | [state](./state/ledger.json) |
| STATE-03 | 已有完整断言 | 自动成功 | pass | **pass** | [state](./state/ledger.json) |
| STATE-04 | 部分覆盖 | 功能失败 | fail | **fail** | [state](./state/ledger.json) |
| STATE-05 | 已有完整断言 | 自动成功 | pass | **pass** | [state](./state/ledger.json) |
| STATE-06 | 部分覆盖 | 功能失败 | fail | **fail** | [state](./state/ledger.json) |
| STATE-07 | 待实现 | 功能失败 | fail | **fail** | [state](./state/ledger.json) |
| STATE-08 | 部分覆盖 | 自动成功 | partial | **partial** | [state](./state/ledger.json) |
| STATE-09 | 部分覆盖 | 功能失败 | fail | **fail** | [state](./state/ledger.json) |
| ENV-01 | 部分覆盖 | 自动成功 | pass | **pass** | [env](./env/ledger.json) |
| ENV-02 | 部分覆盖 | 自动成功 | pass | **pass** | [env](./env/ledger.json) |
| ENV-03 | 部分覆盖 | 安全停止 | fail | **fail** | [env](./env/ledger.json) |
| ENV-04 | 部分覆盖 | 功能失败 | fail | **fail** | [env](./env/ledger.json) |
| ENV-05 | 部分覆盖 | 安全停止 | fail | **fail** | [env](./env/ledger.json) |
| ENV-06 | 部分覆盖 | 自动成功 | partial | **partial** | [env](./env/ledger.json) |
| ENV-07 | 部分覆盖 | 自动成功 | partial | **partial** | [env](./env/ledger.json) |
| ENV-08 | 部分覆盖 | 未执行 | not_run | **partial** | [env](./env/ledger.json) |
| PATH-01 | 部分覆盖→本次补全 | 自动成功 | pass | **pass** | [path](./path/ledger.json) |
| PATH-02 | 已有完整断言 | 引导后成功 | pass | **pass** | [path](./path/ledger.json) |
| PATH-03 | 已有完整断言 | 自动成功 | pass | **pass** | [path](./path/ledger.json) |
| PATH-04 | 已有完整断言 | 引导后成功 | pass | **pass** | [path](./path/ledger.json) |
| PATH-05 | 部分覆盖 | 自动成功 | partial | **partial** | [path](./path/ledger.json) |
| PATH-06 | 部分覆盖 | 安全停止 | partial | **partial** | [path](./path/ledger.json) |
| PATH-07 | 部分覆盖 | 安全停止 | partial | **partial** | [path](./path/ledger.json) |
| PATH-08 | 部分覆盖 | 安全停止 | partial | **partial** | [path](./path/ledger.json) |
| PATH-09 | 已有完整断言 | 自动成功 | pass | **pass** | [path](./path/ledger.json) |
| SCI-01 | 部分覆盖 | 自动成功 | pass | **partial** | [sci](./sci/ledger.json) |
| SCI-02 | 部分覆盖 | 自动成功 | pass | **partial** | [sci](./sci/ledger.json) |
| SCI-03 | 待实现 | 功能失败 | fail | **fail** | [sci](./sci/ledger.json) |
| SCI-04 | 已有完整断言 | 自动成功 | partial | **partial** | [sci](./sci/ledger.json) |
| SCI-05 | 部分覆盖 | 功能失败 | fail | **fail** | [sci](./sci/ledger.json) |
| SCI-06 | 已有完整断言 | 自动成功 | pass | **pass** | [sci](./sci/ledger.json) |
| SCI-07 | 部分覆盖 | 自动成功 | pass | **pass** | [sci](./sci/ledger.json) |
| SCI-08 | 部分覆盖 | 自动成功 | pass | **pass** | [sci](./sci/ledger.json) |
| QA-FLAGSHIP-01 | 部分覆盖 | 引导后成功 | partial | **partial** | [flagship](./flagship/ledger.json) |
| QA-FLAGSHIP-01-snapshot | 待实现 | 自动成功 | pass | **pass** | [flagship](./flagship/ledger.json) |
| QA-FLAGSHIP-01-recompute | 部分覆盖 | 引导后成功 | pass | **pass** | [flagship](./flagship/ledger.json) |
| ACCEPT-1 | 部分覆盖 | 引导后成功 | partial | **partial** | [flagship](./flagship/ledger.json) |
| ACCEPT-2 | 部分覆盖 | 引导后成功 | pass | **pass** | [flagship](./flagship/ledger.json) |
| LONG-01 | 部分覆盖 | 自动成功 | partial | **partial** | [long](./long/ledger.json) |
| LONG-02 | 部分覆盖 | 自动成功 | pass | **pass** | [long](./long/ledger.json) |
| LONG-03 | 部分覆盖 | 安全停止 | pass | **partial** | [long](./long/ledger.json) |
| RAND-01 | 部分覆盖 | 自动成功 | pass | **partial** | [long](./long/ledger.json) |
| RAND-02 | 部分覆盖 | 自动成功 | partial | **partial** | [long](./long/ledger.json) |
| REL-01 | 部分覆盖 | 功能失败 | fail | **fail** | [rel](./rel/ledger.json) |
| REL-02 | 部分覆盖 | 自动成功 | partial | **partial** | [rel](./rel/ledger.json) |
| REL-03 | 部分覆盖 | 自动成功 | partial | **partial** | [rel](./rel/ledger.json) |
| REL-04 | 部分覆盖 | 自动成功 | partial | **partial** | [rel](./rel/ledger.json) |
| REL-05 | 部分覆盖 | 功能失败 | fail | **fail** | [rel](./rel/ledger.json) |

机器可读汇总：[`summary.json`](./summary.json)，含每条的一句话结论、复核说明，以及执行与复核两次提交的 SHA。

## 3. 发现的产品缺陷

每条都有复现脚本，在对应节的 `repro/` 下。「退出码 1 = 缺陷仍在」这类约定写在各节台账里。**本 PR 不修任何产品代码。**
严重度是执行者的初判，建议分诊时再定。

### P1 / 高

| ID | 摘要 | 位置（初判） | 复现 |
| --- | --- | --- | --- |
| REL-01-B1 | **非内置解释器起 worker 时不带 `-B`，会把 `__pycache__` 写进安装目录，破坏 .app 代码签名。** 本机实测：`/Applications/Tavotto.app` 0.16.0 比发布 tarball 多出 17 个 `engine/*.pyc`，`codesign --verify --deep --strict` 和 `spctl` 均失败；原样解包的那份通过。复核指出复现脚本只写出其中 16 个，`worker.cpython-313.pyc` 还有一个写入方没有定位到。 | `src/tavotto/engine/pool.py:1310`、`:1867` | `rel/repro/repro_nonbundled_worker_writes_pyc_into_install_dir.py` |
| STATE-04-B1 | 权威渲染在途时撤销：文档回到了拖动前，画布上的预览位移却一直留着，拖动那一版的回包到达后也不撤。画面、选择框、文档三方不一致，点击会命中别的对象。jsdom 和真浏览器都复现了。 | 预览会话在等拖动那一版的键；PanelView 的 reattach effect 不跑；拖动手势没在 gestureCoordinator 登记 | `state/repro/state04_undo_while_authority_pending.test.tsx`、`state_bugs_repro.spec.ts --grep STATE-04` |
| GEO-B1 | 用 `annotate(..., textcoords≠'data')` 画的注释，manifest 宣称可拖，拖动后落点错 0.09–0.33 figure 分数，而且没有 warning；热态与重放一致，所以写回校验拦不住。 | 引擎的 `pos_frac` 落点 | `geo/repro/repro_title_and_annotation_drag.py` |
| GEO-B2 | `layout='constrained'` 或 `'tight'` 下拖标题或轴标签，y 分量不生效。 | 同上 | 同上（TConstrained / TTight 行） |
| SCI-03-B1 | override 按位置式 gid（`axes_i.lines_j`）匹配。脚本重排、插入、删除曲线或子图后，旧 override 静默落到另一条曲线上，4/4 个变体都复现，warnings 为空。 | `engine/manifest.py:685` | `sci/repro/sci03_structure_change.py` |
| SCI-05-B1 | 写回提交循环里，第二个目标备份时磁盘满：PDF 已替换、PNG 还是旧的，残留 `.updating` 文件，返回 HTTP 500，没有回滚。**违反写回事务不变式。** | `app.py::_write_source_files`（`shutil.copy2` 在 try 之外） | `sci/repro/test_sci05_writeback_faults.py -k backup` |

### P2 / 中

| ID | 摘要 | 复现 |
| --- | --- | --- |
| STATE-06-B1 | 切项目后，A 项目在途渲染的成功回包没有代际检查，会写进 B 的 `byKey`/`latest`（`renderStore.render()`）。 | `state/repro/state06_cross_project_render.test.ts` |
| STATE-09-B1 | `ai_bridge.revert()` 不核对版本，会静默覆盖 AI 之后的人工改动。 | `state/repro/test_state09_ai_revert_stale.py` |
| STATE-02-B1 | 拖动中连按两次 Esc 退出图内编辑态后，手势没有被取消，松手仍然写 override 并渲染。 | `state_bugs_repro.spec.ts --grep STATE-02` |
| GEO-B3 | 整组平移同时选中 Axes 和它自己的孩子时，孩子在预览里走 2Δ，权威图到达后跳回。 | `geo/repro/geoParentChild.repro.test.ts` |
| GEO-B4 | 图例首次写 `loc_frac` 后，SVG 内容相对 manifest bbox 横移，112% 下约 1.39–1.49 CSS px。与 PR #575 记录的遗留问题同族。 | `geo/repro/probe_legend_svg_vs_manifest.py`、`legend-jump.repro.spec.ts` |
| GEO-B5 | 拖出去再拖回原点：仍写一条等于原值的 override，另加 1 条历史、1 次渲染，把对象钉出了自动布局。 | `geo/repro/geoNoopDragBack.repro.test.ts`（A） |
| ENV-03-B1 | 设置里的全局解释器在运行中被删除后，返回 `internal_error(FileNotFoundError)`，而不是 `explicit_python_unusable`。 | `env/repro/env03_configured_python_vanishes.py` |
| ENV-04-B1 | 项目 `.venv` 在同一路径被重建成坏环境后，进程内沿用旧的健康体检，反复 `session_dead`，只有重启后端才会重新体检。 | `env/repro/env04_replaced_venv_stale_probe.py` |
| ENV-08-B1（复核新发现，待按合同确认） | 缺失的 import 映射为 unknown 时联合计划不是 ready，已经装了该包的命名环境永远不会被发现。 | `env/logs/verify/env08_conda_layout_feasibility.py` |
| PATH-B1 | 准备接口对没有捕获到任何图的面板返回 `ready`。 | `path/repro/repro_path06_prepare_ready_without_figure.py` |
| PATH-B2 | 项目根里的 `.venv` 库文件被 InputObserver 记成回执输入，挤满 256 条预算，导致 `truncated=true`，真正的数据文件可能丢失。 | `path/repro/repro_path06_observer_venv_pollution.py` |
| FLAG-B1 | 「原图尺寸」导出被画布页越界（out-of-page）判为阻断，违反规范 SCI-06 的「原图范围不混入画布 x/y/w/h」。 | `flagship/repro/bug_original_scope_out_of_page.py`（需先按台账从夹具复制样式检查报告，见复核说明） |
| REL-05-B1 | 用户脚本异常的 message 原样进入诊断包（`report.json`、`app.log`、诊断摘要）。 | `rel/repro/repro_diag_error_message_leak.py` |

### P3 / 低

- GEO-B6：单条 `setOverride` 在同值写入时也会把 override 挪到数组末尾，导致变体键变化、多一次白渲染。
- STATE-07-B1：拖动中改视图倍率，下一次 move 跳约 Δ×(k−1)。
- STATE-08-B1：`port_is_free()` 不带 `SO_REUSEADDR`，约 31 秒 TIME_WAIT 内同端口重启会顺延端口，localStorage 里记的「上次文档」因此丢失。
- ENV-05-B1：包内部缺子模块被误报为 `missing_dependency`（同形例子：numpy ABI 损坏）。
- PATH-B3：build 失败留下的会话，在静态证据变化后仍被渲染入口复用。
- SCI-04-B1：写回 verify 阶段 worker 崩溃返回 500 而不是 409（字节不变）。既有用例断言的正是 500，与根 AGENTS.md「一律 409」矛盾，需要先定合同。
- SCI-05-B2：自动保存在 `os.replace` 之前被杀，留下的 `.tmp` 永不回收。
- LONG-03-B1：worker 在两次请求之间被杀，同一故障会落进三种错误分类，其中 `BrokenPipeError` 未翻译成 WorkerError。
- REL-05-B2：诊断包的 `app.log` 保留脚本文件名和 4xx 请求的查询串。

### 待调查（未定性为缺陷）

- LONG-01：35 分钟缩短档里，Flask 父进程 RSS 中位数 52.7 → 78.3 MiB（约 +48.6 MiB/h）。按规范 §7 需要基线校准后再判。
- QA-FLAGSHIP-01 P15：9 条导出文字里有 2 条位移 0.144 mm，超过预设的 0.05 mm；根因未确诊。
- SCI-01：`art_colorbar` 重序列化腿有 0.39% 像素差（阈值 0.4%），原因未查明。
- REL-02：updater-consumer-fidelity 在 nightly 里红，因为 `latest.json` 缺 darwin-x86_64。复核确认这不是 v0.16.0 的缺陷（Intel 条目是发布后才进支持矩阵的），应记为下一次发版的前置条件。
- REL-03：`release-publish::github_release` 两次 403，Release 是在自动发布链之外建的；lab 不验收 dmg/NSIS。

## 4. 本 PR 新增的回归测试

全部为绿，每条都做过一次定点变异反证（变异下以预期不变量变红，还原后变绿，记录在各节 `ledger.json` 的 `mutation_check`）。会红的复现只放在 `repro/`，不进测试集。

| 文件 | 规范 ID | 说明 |
| --- | --- | --- |
| `web/src/canvas/geometryReference.test.ts` + `__fixtures__/geoReference.json` | GEO-01/02/04/05/06/07/09 | 32 条。独立 T/T⁻¹ 坐标尺，不调用生产换算；夹具来自真实 worker 输出，由 `geo/repro/dump_geo_fixtures.py` 确定性生成 |
| `tests/test_geometry_reference.py` | GEO-01/02/03/08/10 | 15 条，引擎侧，走真 worker；没有 worker Python 时整体 skip |
| `web/e2e/geometry-reference.spec.ts` | GEO-01/02 | 5 条，chromium，覆盖 94/118/72% 与 DPR2 |
| `web/src/canvas/fakeRealtimeDrag.test.tsx`、`dragReleaseSnapback.test.tsx`（增补） | STATE-02/03/04 | pointercancel / lostpointercapture、旧 manifest 不作几何写入、拖动中 SVG 真替换 |
| `web/e2e/fake-realtime.spec.ts`（增补） | STATE-01/02/05/08 | 逐帧预览、取消入口、乱序回包、保存后重开 |
| `tests/test_first_open_same_name_package.py` | ENV-02/03 | 真实 venv 的同名包竞争，以及显式环境失效 |
| `tests/test_first_open_paths_d1.py` | PATH-01…09 | D1 夹具，逐点比较全序列和输入 hash |
| `tests/test_write_back_real_409.py` | SCI-04 | 真实 worker 上的五条 409 路径，断言原字节不变 |
| `tests/test_export_anchor_readback.py` | SCI-06 | 用独立读取器复核锚点、页面盒、透明度 |
| `tests/test_export_version_snapshot.py` | SCI-07/08 | 慢导出不混版本；快照与重算分离 |
| `tests/test_appearance_edits_keep_science.py` | SCI-02 | 只改外观时数据不变 |
| `tests/test_qa_first_open_env_and_data.py` | ACCEPT-2 | E1×D1 同一次真实 HTTP 首开 |
| `web/e2e/multi-select-geometry.spec.ts` | ACCEPT-1 | 图内混合多选整组拖动 + 撤销 / 重做 |
| `tests/test_override_history_sequences.py` | RAND-01 | 引擎层的拖动、挪子图、改图幅 + undo/redo 随机序列 |
| `web/src/hooks/useKeyboardTextFocus.test.tsx` | REL-04 | 焦点在输入框里时，快捷键不改文档、选区和工具 |

**复核指出的测试强度缺口**（测试本身是绿的，但判据偏弱，建议后续补强）：

- `test_appearance_edits_keep_science.py` 只看相对位置。复核用「外观补丁同时把数据乘 1.1 + 自动缩放」做变异，用例仍然是绿的。
- `test_first_open_paths_d1.py` 在 PATH-01/04/05 里导出时没带编辑，按 FO-065 交出的是磁盘原件，读错数据文件也照样绿。应改成带编辑导出。
- `test_override_history_sequences.py`：测试图库里没有可拖动的文字（文字拖动变异存活）；撤销走的是测试侧模型。
- `web/e2e/fake-realtime.spec.ts` 的 STATE-05 守的是画面，不守 `latest`；`latest` 由 jsdom 用例守。

## 5. 未执行与缩短档（诚实记账）

- 长时：LONG-01 只跑了 16 分钟和 35 分钟两档，LONG-02、LONG-03 各 10 分钟，都不等于规范的 2–4 小时、24 小时或 72 小时档。
- 故障注入：磁盘写满（安装中途）、睡眠唤醒、环境准备取消、数据目录断开（LONG-03）没有执行。复核认为这几项在本机其实可以做。
- 平台：没有 Windows（REL-01/02、PATH-07 跨盘、网盘）；没有真实跨显示器或系统缩放（STATE-07、REL-04）；桌面壳交互流程没有自动化，旗舰流程走的是 **HTTP + 浏览器入口，不是桌面入口**。
- 已安装的 `/Applications/Tavotto.app` 只做了只读检查（签名、hash），没有用它打开用户数据。
- STATE-08 的导出腿没有执行。复核认为可以先跑 `scripts/fetch_fonts.py` 补齐字体再做。
- 真实 AI CLI 没有调用（STATE-09 走的是 `ai_bridge` 接口层）。

## 6. 复现方式

1. `git checkout` 本分支，按各节 `ledger.json` 里 `commands` 字段逐字执行。命令从仓库根目录起跑；Python 统一为 `PYTHONPATH=$PWD/src <主仓库>/.venv/bin/python`。需要真渲染时设 `TAVOTTO_WORKER_PYTHON=<装有 matplotlib 的解释器>`。E2E 先跑 `scripts/build_frontend.py`。
2. 各节 `repro/` 下是夹具生成脚本和缺陷复现脚本，都是确定性的，随机种子固定。
3. 复核的重跑日志在各节 `logs/verify/`。复核记录的台账笔误（例如 GEO-03/07/10 的一条命令转录、STATE-07 的数值、flagship 复现脚本依赖手工改名的文件）以 `verification.json` 为准，本报告已经采纳。

## 7. 合并态门禁（本分支）

QA 用例都在 `62af729c` 上执行。集成分支合并时，`origin/main` 已前进到 `d5252e06`（#543，执行期间 fetch 到的）。所以本分支 = `main@d5252e06` + 8 节 QA 提交，下列门禁跑的都是这棵**合并态**的树。日志在 [`integration/logs/`](./integration/logs/)。

| 门禁 | 结果 |
| --- | --- |
| `ruff check .` / `ruff format --check .` | 全绿。8 个复核脚本做过格式化和安全修复，语义不变，单独一个提交 |
| 全量 pytest（已 `fetch_fonts`，不设 `TAVOTTO_WORKER_PYTHON`） | 共 2 条失败，与基线 `62af729c` 在同机同条件下的失败清单**逐条相同**，没有只在 QA 分支上红的用例。这 2 条都是 `tests/native/test_run_cli_integration.py`。其中 `test_run_messages_only_stderr` 的成因是仓库路径 `/Volumes/Projects/Tavotto` 本身含 "Tavotto"，断言把它误判为产品文字混入了 stdout（判据主语问题，另开 issue 为宜）；`test_ctrl_c_reaches_the_script_and_leaves_no_orphan` 未看成因，只确认基线上同样红。见 `pytest-diff.log` |
| 新增的 9 个 pytest 文件（设 worker Python） | 50 passed、0 skip，默认档和带 slow 两档都一样。见 `new-pytest-files.log` |
| `pnpm test`（vitest） | 295 个文件、4397 条全部通过 |
| `pnpm build`（含 `tsc -b`） | 通过 |
| 新增 / 改动的 E2E（chromium）：`geometry-reference`、`fake-realtime`、`multi-select-geometry` | 首跑时 10 条通过、1 条失败，失败的是 `multi-select-geometry`（见下一段）；修正后该条连续 3 次通过 |

**合并态暴露的测试缺陷（已修）**：`multi-select-geometry.spec.ts` 在旗舰分支（基线 62af729c）上是绿的。合入 #543 后，面板保持原生缩放比，第二个子图伸进了右侧属性栏底下，点它的标题会点到属性栏上，导致连续 3 次红。修法是点选前先适应页面（⌘1）再缩小一档（⌘-），并在每次点选前断言落点在 `[data-canvas-stage]` 上。反证见 `e2e-multiselect-mutation.log`：去掉 ⌘1/⌘- 后守卫在 `axes_1.title` 处红；「多选漏一人」变异下 `axes_0.title` 的锚点误差为 −30 px 并变红；还原后回绿。

**与在途 PR 的关系**：GEO-B4（图例首拖横跳）与 open 的 #579（#576）同族；#579 合入后应当重跑 `geo/repro/probe_legend_svg_vs_manifest.py` 与 `legend-jump.repro.spec.ts` 复核。

### 7.1 PR #583 首轮 CI 暴露的问题（2026-09-25，全部是新增用例的环境假设，已修）

首轮 CI 跑在 `73dd4b53` 的合并 ref 上。本机（macOS、开发档 matplotlib 3.10.8）全绿的新增用例，在 Linux / Windows 腿上红了 5 处，另有 CodeQL 4 条告警。修复后在两棵树上各跑了一遍受影响的用例：本分支，以及 `origin/main@f8928bca` + 本分支的本地合并树（重建了前端）。

| CI 腿 | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| backend-fast（3.10 / 3.13 / 3.14） | PATH-05 `assert 223 > 260` | 长路径靠写死 6 段目录凑。macOS 的 `tmp_path`（`/private/var/folders/…`）比 Linux 的 `/tmp/pytest-of-runner/…` 长六十来个字符，「> 260」只在本机成立 | `_pad_dirs` 按本机真实前缀现算要补几段目录。反证：去掉补段后在本机报 `168 > 260` |
| backend-platforms（windows，1/2） | PATH-05 `WinError 123` | 目录名里有 `"`，Windows 文件名不允许这个字符，用户在 Windows 上根本建不出这种目录 | 按平台造路径：POSIX 保留单、双引号；Windows 只放单引号。「> 260」这一维在 Windows 上只对开了 `LongPathsEnabled` 的机器成立（读注册表判断），且当前目录受 MAX_PATH−12 限制，所以目录停在 230 字符以内，由数据文件名把全路径推过 260；没开长路径的机器上这一维不适用，其余维度照测 |
| backend-platforms（windows，2/2） | ENV-02/03 前提断言：`python -c "import qa_probe_pkg"` 退出码 1 | 裸名 `python` 交给了 CreateProcess。它先搜父进程 exe 所在目录和系统目录，最后才搜 PATH，所以拿到的是跑 pytest 的那个解释器，而不是 PATH 最前面的 B | 改用 `shutil.which("python", path=PATH)` 按 PATH 查，并断言查到的就在 B 的目录里。反证：去掉给 PATH 加前缀的那一步，前提断言立刻红 |
| windows-exe-smoke（1） | STATE-08 `EBUSY … data\cache\app.log` | 用例体末尾就 `rmSync` 了，而第二个实例要到 fixture 收尾时才停，那时它还握着 app.log。POSIX 允许 unlink 打开着的文件，所以本机看不出来 | 删目录前先 `a2.stop()`，等进程确实退出，`rmSync` 再带有界重试。端口探针同一处的 `python3` 改成按平台取解释器名（同 `large-figure.spec.ts`），起不来就直接抛，不再当成「端口还忙」一直等到超时（Codex P1） |
| posix-e2e | GEO-02 `ylabel` 误差 1.016 px（预算 0.5） | 不是多选的缺陷，是 #576 的首拖跳。y 轴标签自动定位时，x 坐标由刻度标签宽度现算：manifest 在 100 dpi Agg 上量，画布用的是矢量 SVG，两边差一截与字形度量有关的量。第一次拖动把它换成显式位置时，就跳这么一截。从 CI 的 trace 里核对：两个元素写进 `pos_frac` 的分数位移完全相同（−0.10231），manifest 里 ylabel 的包围盒也恰好平移了这么多，但 SVG 里 ylabel 的平移比标题少 0.72 pt，也就是 1.02 CSS px。各环境实测首拖误差：Linux +1.02、Windows −0.28、本机 −0.17，标题恒为 0 左右 | 第一拖只用来把两者换成显式位置，误差只打进日志，标注 #576、不计入门禁；门禁量第二拖，预算仍是 0.5 px，没有放宽。第二拖实测误差：本机 0.002 px，合并树 0.001 px。反证：把整组平移改成漏写最后一名成员后，ylabel 误差为 (−29, 17) px，用例变红。#579 合入后，首拖这一截应当归零 |
| CodeQL | `flagship_driver.cjs:299` 正则只转义了点；`repro_diag_error_message_leak.py:36/37` 打印了金丝雀；`diagnostics.py:691` 的 SHA1 告警是同一个金丝雀的数据流带过去的（产品代码本 PR 没动） | repro 脚本自身的问题 | 正则按全部元字符转义；金丝雀改名为 `CANARY`，输出时替换成 `<CANARY>`，复现结论（布尔值）不变 |

一并处理了评审意见：素材卡改用 `data-card` 锚点定位；图内编辑宿主先断言恰好一个再使用，不再取第一个匹配（Codex P1）。SCI-02 增加了第二把尺子「绝对数据坐标」（Codex P2）：用产物里的刻度线位置和刻度数值，把曲线顶点换算回数据值，逐点和 `points.csv` 比较。反证：让改线宽的 override 顺带把 y 数据 ×1000 并自动缩放，原来的归一化尺子仍然是绿的（这正是评审指出的盲区），新尺子报 `(1.0, 9.0) → (1.0, 8999.99…)`，用例变红。

Windows 两腿的改动在本机跑不了，是按 Windows 语义推断出来的，推断依据写在 PR 评论里，要等下一轮 CI 确认。
