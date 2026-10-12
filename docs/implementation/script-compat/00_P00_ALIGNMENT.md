# 脚本兼容性改造 — P00 对齐记录

> 范围：一次性短对齐，**不改任何产品代码、不新增 fixture/测试**。把首批阶段（P01a、P01b、P05a，
> 及 P02/P03/P04 的入口）对到当前 `main` 的事实上，之后各阶段 PR 直接引用本表。
>
> AC 编号的条文见 [ACCEPTANCE_CRITERIA.md](ACCEPTANCE_CRITERIA.md)（仓库内权威出处）。
>
> 证据分四级，文中用方括号标注：
>
> * `[运行]` 本次在当前 main 的代码上真跑过（复现脚本与输出在会话 scratch，不进仓库；命令见 §6）；
> * `[测试]` 有现成用例钉着；
> * `[代码]` 读代码得出，未运行（含"自审计基线起相关文件未变"这种由 `git diff` 得出的结论）；
> * `[审计]` 只有 2026-10-10 审计包里的观察，本次没有重跑。
>
> 审计包的探针断言的是**当时的观察（含缺陷）**，退出 0 不等于兼容通过。本文不把已登记的 149 个
> CompatBench case、baseline 里 134 条 `full_support`（`tests/compat/baseline.json`：full_support 134 /
> unsupported_by_design 7 / environment_dependency 6 / partial_support 2）当作新结果或用户成功率。

## 0. 基线

| 项 | 值 |
| --- | --- |
| 审计基线 | `b632483039e0230a03c34ed029fa855a0bb02c5c`（审计包 `SOURCE_MANIFEST.json`；其 `final_source_status_porcelain` 为空，审计未改产品源码） |
| 本次 HEAD = `origin/main` | `711b44cb831eff0f72afdd9dbd49149a5b61b261`（#937）；基线是它的祖先 |
| 分支 / 工作树 | `compat/p00-compat-alignment`，worktree `.claude/worktrees/p00-compat-alignment`，起点即 origin/main，开工时干净 |
| 发行 artifact SHA | **未核**：本阶段不涉及发行物（not_run） |

基线之后 main 前进了 9 个提交（`git log --oneline b632483..HEAD`）。相关的是依赖计划 / Import Origin 一线：

| 提交 | 内容 | 对审计缺口的影响 |
| --- | --- | --- |
| #920 `59a90e27e` | Import Origin PR4：计划消费来源与覆盖度；来源未定不进 missing；目标解释器事实 | DEP-05 **部分**（见 §3.3） |
| #930 `51d233cdd` | PR5a：层指纹绑定执行期过期检查、歧义先于映射、未建模钩子不判缺、保留 sys.path 顺序 | DEP-05 部分；与 DEP-07/08 的来源语义相邻 |
| #933 `e090923b4` | PR5b：预填 + 用户确认（`user_specified`）、装后发行包与版本核验 | DEP-05 部分（"用户明确选择"这一层有了入口） |
| #934 `3db9c9cc5` | PR6：前端五种展示态、确认包名 | 展示层，不改下列任何缺口 |
| #937 `711b44cb8` | PR7：真浏览器验收、ADR 0119、FINAL_ACCEPTANCE | 验收文档（`docs/implementation/import-origin-resolver/FINAL_ACCEPTANCE.md`） |
| #919 / #923 / #926 / #857 | 补审修复 / 反馈上传 / CI 超时 / 弹窗选文件提示 | 与本组缺口无关 |

`git diff --stat b632483..HEAD` 对下列执行/环境相关文件：**未变**的是 `worker.py`、`bridge_runner.py`、
`runcli.py`、`figsession.py`、`preparation.py`、`userenvs.py`、`projscan.py`、`projectenv.py`——
所以 P01a、P01b、P05a、P04 的审计行号与结论在源码层面仍是原样 `[代码]`；**变了**的是
`depplan.py`（+761）、`deprepair.py`、`distmeta.py`、`envadvice.py`、`importscan.py`、`depresolve.py`、
`managedenv.py`、`app.py`（P02/P03 入口行号已平移，下表用当前行号）。

## 1. 首批缺口总表

| 阶段 | 证据 | AC | 当前 main 状态 | 依据 |
| --- | --- | --- | --- | --- |
| P01a | EX03（replace 值 1/ok）、EX02（savefig 后读文件） | [AC-02](ACCEPTANCE_CRITERIA.md#ac-02)，后续 [AC-03](ACCEPTANCE_CRITERIA.md#ac-03) | **EX03 仍存在**；EX02 是"明确报错"而非假成功 | `[运行]` |
| P01b | EX10（script_end terminate 退出码）、EX12（半 SVG 计数） | [AC-04](ACCEPTANCE_CRITERIA.md#ac-04)、[AC-05](ACCEPTANCE_CRITERIA.md#ac-05) 前半 | **仍存在** | `[代码]`（文件自基线未变）；EX10 的实机退出码 `[审计]` |
| P05a | E08、E09 | [AC-26](ACCEPTANCE_CRITERIA.md#ac-26)、[AC-24](ACCEPTANCE_CRITERIA.md#ac-24) | **仍存在** | `[代码]`（`preparation.py` 自基线未变）；转换结果 `[审计]` |
| P02 | DEP-02、03（后三种）、04、10、11 | [AC-06](ACCEPTANCE_CRITERIA.md#ac-06)、[AC-07](ACCEPTANCE_CRITERIA.md#ac-07)、[AC-08](ACCEPTANCE_CRITERIA.md#ac-08)、[AC-22](ACCEPTANCE_CRITERIA.md#ac-22) | **仍存在** | `[运行]`（计划层，合成 TargetFacts）；DEP-10 的真实 pip 链 `[审计]` |
| P03 | DEP-05、07、08、14 | [AC-08](ACCEPTANCE_CRITERIA.md#ac-08)、[AC-09](ACCEPTANCE_CRITERIA.md#ac-09)、[AC-22](ACCEPTANCE_CRITERIA.md#ac-22) | **部分修复**：已有来源/歧义状态，安装计划仍选旧映射 | `[运行]` + `[测试]` |
| P04 | E01–E07 | [AC-10](ACCEPTANCE_CRITERIA.md#ac-10)、[AC-11](ACCEPTANCE_CRITERIA.md#ac-11) | **仍存在**（相关文件自基线未变） | `[代码]` |

## 2. 首批阶段逐条

### 2.1 P01a — safe worker 静默错算（EX02 / EX03）

* **入口**：`src/tavotto/engine/worker.py:838-844`，`_guard_delete` 把 `os.remove/unlink/rmdir/rename/replace`、
  `shutil.rmtree/move` 换成"真身落在真实图库内就打印 `[guard]` 并**返回 None**"（`_inside_real`，同文件 :829-836）。
  返回 None 即"调用成功"，没有任何事件进入 build 结果。build 成功响应由协议层生成，见 `worker.py` 的 build 响应路径。
* **现状 `[运行]`**（当前 main，主工作区 `.venv` Python 3.13 + matplotlib 3.11.2，worker 直接按协议 build）：

  | 用例 | 直接 `python figure.py` | worker build |
  | --- | --- | --- |
  | EX03：写 `cache.tmp`=2 → `os.replace` 到已有 `cache.csv`(=1) → 读值绘图 | 观察值 **2** | `ok:true`，观察值 **1**，stems 含 `computed` |
  | EX02：`savefig('stage.png')` → `Image.open('stage.png')` | `[640, 480]` | `ok:false`，`No such file or directory: 'stage.png'`，无 `observed.json` |

  结论：EX03 假成功**仍在**；EX02 是诚实失败，保留其错误分类即可，真实文件语义归 P06/P07（[AC-03](ACCEPTANCE_CRITERIA.md#ac-03)）。
* **P01a 的既有设施**：`tests/test_worker_roundtrip.py`（真实 worker 协议 round-trip，模块在找不到带 matplotlib 的
  解释器时整体 skip——跑前确认没被 skip）、`tests/test_worker_exit_report.py`（脚本自身 `sys.exit` 的分类）、
  `tests/test_savefig_capture_params.py`（savefig 捕获参数，**正对照**：普通 savefig 捕获不能被标失败）。
  现有测试里提到守卫文案的在 `tests/test_workdir_mode.py`、`tests/test_diagnostics_worker_evidence.py`
  `[代码]`（grep 命中，未逐条读断言）。
* **拟新增测试入口**：并入 `tests/test_worker_roundtrip.py`（协议层）+ `tests/test_worker_exit_report.py`
  风格的终局状态断言；fixture 脚本内联（EX03 的 replace 最小反例、`except Exception` 吞异常变体、
  普通 savefig 与合法回写正例）。**不**走 `tests/compat`（那是 CompatBench 量化口径，且 EX03 属于已知
  `unsupported_by_design` 一类，不应混进 149 条基线）。
* **不要做**：删守卫；把所有 monkeypatch 脚本拒绝；P01a 之外为 EX02/EX03 加 fixture 的活归 P01a 的 PR。

### 2.2 P01b — native 终局事实（EX10 / EX12，正对照 EX05 / EX11）

* **EX10 入口** `bridge_runner.py:947-952`：

  ```python
  if run.control is not None and _CAPTURE:
      try:
          run.barrier("script_end")
      except SystemExit:
          pass
  ```

  `terminate` 在 `barrier()` 内置 `run.terminated=True` 并 `raise SystemExit(TERMINATE_EXIT)`（:619-624），
  这里被 `pass` 吃掉；而 `if run.terminated: exit_code = TERMINATE_EXIT`（:932-937）在**屏障之前**就判完了，
  所以 script_end 屏障上的 terminate 不会回写 `exit_code`，进程以 0 退出。`[代码]`。
  `show` 屏障里的 terminate 在脚本帧内抛出，会被 :932 的判据覆盖，因此是 5（EX05/EX10-show 正对照）。
  `runcli.py:208-225` 把 `code` 原样透传并写 status-file。
* **EX12 入口** `figsession.py:829-831`（`render()` 直接 `open(svg_path, "w")` 写正式 `<stem>.svg`，绘制失败留下半文件）与
  `runcli.py:332-340` `_figures_written`（`os.listdir(out_dir)` 数 `.svg` 后缀）。两处自基线未变 `[代码]`。
  半 SVG 被计为完成、也会覆盖上一份有效预览。
* **既有设施**：`tests/native/test_run_codes.py`（含
  `test_the_runner_and_the_cli_agree_on_the_terminate_code`，钉的是两处常量一致，**不**覆盖 script_end 屏障）、
  `tests/native/test_run_cli_integration.py::test_terminate_gives_a_deterministic_exit_code`（:479，show 屏障路径）、
  `tests/native/test_native_barrier_semantics.py`、`tests/native/test_native_inconsistent.py`、
  `tests/bridge/test_bridge_capture.py`、`tests/bridge/test_bridge_parent_gone.py`（含 script_end）。
  grep 显示 `script_end` 只出现在 `tests/bridge/test_bridge_parent_gone.py` 与
  `tests/bridge/test_bridge_backend_and_show.py`，**native 的 CLI 集成测试里没有 script_end 屏障 terminate 用例** `[代码]`。
* **拟新增测试入口**：`tests/native/test_run_cli_integration.py`（真实 CLI+transport：show / script_end 两种 terminate，
  对拍 session / CLI 退出码 / status-file；正常 continue=0；脚本自身 `SystemExit`/异常透传）；半 SVG 与旧 SVG
  的计数/覆盖用例放 `tests/bridge/test_bridge_capture.py` 或 figsession 单测旁。正对照复用 EX05/EX11 的跑法。
* **边界**：EX12 的"健康图仍可编辑"归 P08a，P01b 只保证半图不计完成、不覆盖旧预览。

### 2.3 P05a — 准备路径丢诊断与恢复信息（E08 / E09）

* **入口**：`preparation.py:826`，`error = {"code": ..., "message": str(exc)}`；其后只补 `module`、`install_route`、
  `project_env`、`missing_input`、`input_facts`（:833-858）。对照旧 probe：`probe.py:115-120`（`_err`，保留 `traceback`）、
  :175-201（`script_needs_arguments` 的 `argv_count/parse_kind/usage`）。`preparation.py` 与 `probe.py` 自基线未变 `[代码]`。
  E08/E09 的实际序列化结果（准备错误 keys 只有 `code,message`）`[审计]`，本次未重跑。
* **前端**：`web/src/components/PreparationCard.tsx`、`web/src/lib/preparationText.ts`；
  既有测试 `web/src/components/PreparationCard.test.tsx`（"一句话 + 至多一个主按钮"状态矩阵）、
  `preparationEntries.test.tsx`。展示纪律见仓库"卡片一句话读懂"：默认只露一句话 + 一个主按钮，详情折叠。
* **既有设施**：`tests/test_preparation_api.py`、`tests/test_preparation_session*.py`、
  `tests/test_script_probe.py`、`tests/test_missing_dependency_route.py`；前端契约 fixture 必须取自**真实后端序列化**
  （[AC-26](ACCEPTANCE_CRITERIA.md#ac-26)）——可参考 `web/e2e/first-run-qualification.spec.ts` 是否已有后端 JSON 取样方式（未逐行核）。
* **拟新增测试入口**：后端并入 `tests/test_preparation_session.py`（普通异常、missing/invalid argv、无应答 input、
  `missing_input` 四类各自保留稳定恢复字段；敏感 argv/getpass 反例不泄漏；共享诊断仍脱敏——对拍
  `tests/test_diagnostics_worker_evidence.py` 一类现有脱敏用例）；前端并入 `PreparationCard.test.tsx`。

### 2.4 P02 / P03 入口（依赖计划，不要两个 agent 同时改 `depplan.py`）

* **入口**：`depplan.py:973` `plan(...)`；状态 `STATUS_NOTHING_NEEDED` 在 :67 / :1287；roots 与 constraints 的拆分在
  `plan()` 内（声明选中后只进 constraints 的路径，需 P02 作者在改动时定位）。`depresolve.py`（声明解析）、
  `deprepair.py`（pip argv、联合事务）、`managedenv.py`（generation）、`importscan.py` / `distmeta.py`（IOR 来源）。
* **现状 `[运行]`**（当前 main，`depplan.plan(..., target_kind=managed)`，`TargetFacts` 为合成，不代表装了真包）：

  | 场景 | status | requirements | constraints | 结论 |
  | --- | --- | --- | --- | --- |
  | DEP-02：`import auditclient`，声明 `audit-driver==1.0` | nothing_needed | `[]` | `audit-driver==1.0` | **仍存在**，`unknown=[auditclient]` |
  | DEP-03 别名调用 / 变量名 / 函数体内 import，声明 `auditlate==1.0` | nothing_needed ×3 | `[]` | `auditlate==1.0` | **仍存在** |
  | DEP-03 正例：`importlib.import_module('auditlate')` 字面量 | ready | `auditlate==1.0` | `[]` | 正例保留（现多了 `resolution_status: unverified`） |
  | DEP-11：已装 `auditroot 1.0`，声明 `>=2.0` | nothing_needed | `[]` | `auditroot>=2.0` | **仍存在** |
  | DEP-05：声明 `opencv-python-headless==4.10.0.84`（同理 contrib / contrib-headless），`import cv2` | ready | `["opencv-python"]`（`resolution_source=curated`） | 声明的 provider 仅作 constraint | **仍选旧映射**；但 `missing[].resolution_status="ambiguous"` 已出现 |
  | DEP-05 声明 `opencv-python==…` | ready | `opencv-python==4.10.0.84`（`project_declared`） | `[]` | 同名声明走得通；`ambiguous` 标注已有 |

  DEP-04（requirements / PEP621 / PEP723 三种声明格式均被读取但不成 roots）与 DEP-10（真实 venv + pip 联合安装 + 42）本次
  只重跑了 requirements 变体的计划层；PEP621/723 与 DEP-10 的真实 pip 链 `[审计]`，P02 开工时按其验收重做。
* **P03 已有部分**：#920/#930/#933 让计划消费"来源未定不进 missing"、"歧义先于映射"、`user_specified` 预填 `[测试]`。
  相关测试里有两类不同的东西，P03 不能混为一谈：
  * `tests/test_import_origin_metadata.py:245` `test_installed_evidence_wins_over_the_curated_table` 已用
    `selected_distribution == "opencv-python-headless"` 验证新的 IOR 结果；其中
    `got.distribution == "opencv-python"  # 旧字段不动` 是**有意保留**的旧 curated 字段，属兼容覆盖，P03 不删这条断言。
  * 须保持的不变量（"观测到的 provider 不得变成安装名"）在
    `tests/test_dependency_plan.py` `TestPlanOrigins.test_the_observed_provider_never_becomes_an_install_name`
    与 `tests/test_import_origin_metadata.py` `TestInstalledMetadataIsNotAnInstallAuthorization.test_the_installer_and_the_resolver_do_not_read_the_new_observation`。
  * P03 要新增的是"**声明的** provider（headless / contrib / contrib-headless）→ requirements 选对应发行包"的测试
    （即上表 DEP-05 的修复，[AC-08](ACCEPTANCE_CRITERIA.md#ac-08)）；同时须让上述两条不变量继续成立——声明是授权来源，观测不是。
* **既有设施**：`tests/test_dependency_plan.py`、`test_dependency_transaction.py`、`test_dependency_impact.py`、
  `test_dependency_scope.py`、`test_dependency_repair*.py`、`test_dependency_executor_entry.py`、
  `test_preparation_session_dependencies.py`、`test_project_scan_declarations.py`、
  `tests/test_import_origin_*.py`（含 `test_import_origin_metadata.py`、`..._namespace_and_links.py`）；
  fixture：`tests/fixtures/foundation/dependency_declarations/`（⑤，声明样例，含 `truth.json`）与
  `tests/fixtures/foundation/joint_dependencies/`（⑧，离线 wheelhouse 联合依赖，**P02 的真实安装事务首选复用**）。
* **拟新增测试入口**：计划层并入 `test_dependency_plan.py`；真实 generation / 联合 pip / pip check / 激活走
  `test_dependency_transaction.py` + `joint_dependencies`（`support.dependency_repair.build_wheel` 造 `auditroot`/`auditleaf`）；
  四 provider 仲裁（[AC-08](ACCEPTANCE_CRITERIA.md#ac-08)）为 P02/P03 共同启用门。

### 2.5 P04 入口（环境候选统一）

全部 `[代码]`（相关文件 `userenvs.py` / `projscan.py` / `projectenv.py` 自基线未变；`deprepair.py` / `envadvice.py` 有改动，
下表已按当前行号取）：

| 证据 | 当前入口 | 状态 |
| --- | --- | --- |
| E01 VSCode 指向 venv 文件夹得 0 候选 | `userenvs.py:144` `_is_python_file`、:188 `_vscode_pythons` | 仍存在（推断：该函数仍只认文件） |
| E02 system 候选只进执行不进建议 | `deprepair.py:5498-5525` `user_environment_candidates` 追加 `pool.system_python_candidates()`；`envadvice.py` `_rows` 只用 `projscan.environment_evidence`（:471） | 仍存在 |
| E03 预算先截断后优选 | `userenvs.py:571` `PROBE_LIMIT=12`、:752 `candidates[:PROBE_LIMIT]` | 仍存在 |
| E04 pyenv-win 路径组合 | `userenvs.py:207` `_pyenv_root` / :211 `_pyenv_version_dirs` | 仍存在；平台未验证 |
| E05 登录 shell 空结果缓存 | `userenvs.py:353` `login_shell_pythons` | 仍存在 |
| E06 两 venv 共享基础 Python 仍不同身份 | `userenvs.py` `_key` | **正对照，已正确** |
| E07 裸 import probe 假阴性 | `projectenv.py:351` `_PROBE_SRC` | 仍存在 |

既有设施：`tests/test_user_environments.py`、`test_user_environment_no_follow.py`、`test_user_environment_network_hints.py`、
`test_project_scan*.py`；fixture `tests/fixtures/foundation/project_venv/`（`make_venv.py` 现建）。P04 必须隔离其他枚举源，
否则 `.venv` 发现会掩盖 E01（[AC-10](ACCEPTANCE_CRITERIA.md#ac-10) 已写）。

## 3. 可复用的现有设施（不另起首跑框架）

| 设施 | 用途 | 说明 |
| --- | --- | --- |
| `tests/compat/manifest.json` + `baseline.json` | CompatBench 语料与分类基线 | 149 条已登记；是**回归口径**，不是新结果 |
| `tests/fixtures/foundation/`（十一组，含 `script_only_first_run/`、`joint_dependencies/`、`dependency_declarations/`、`project_venv/`） | 首跑 / 联合依赖 / 声明样例 | 自带 `truth.json`；`README.md` 写明纪律：原生参考在独立临时目录跑、harness 不替产品装包 |
| `tests/test_foundation_script_first_run.py` | T11 首跑资格（S1 脚本-only、S2 本地 wheel 缺包、热会话/双标签） | 首跑链路的唯一权威；后续阶段挂这里，不重复造 |
| `web/e2e/first-run-qualification.spec.ts` | T11 真浏览器首跑 | 同上 |
| `tests/native/`、`tests/bridge/` | native 终局 / 屏障 / 捕获 | P01b、P06 的落点 |

## 4. 未执行平台与项目（not_run）

* 全部真实 Windows / macOS 桌面（含冻结包、WebView、NSIS）：**未执行**。本次只在 macOS 开发机（Darwin）上跑了
  Linux 无关的 Python 级复现，不能当作任何桌面验收。
* 发行 artifact SHA 核对：未执行。
* 仓库 pytest 全量、vitest、Playwright、完整 CompatBench：**未执行**（只为 §6 的复现与文档门禁跑了所列命令）。
* 审计包 44 组探针：未重跑；EX05/EX10/EX11/EX12 的 native 实机（CLI + transport）未重跑，沿用 `[审计]`。
* DEP-10 的真实 venv + pip 联合安装、DEP-12/13/14 的 wheel 边界、E01–E07 的函数级重跑、E08/E09 的序列化重跑：未执行。
* 真实 PyPI 下载、Conda 激活、外部 SDK / 数据库：未执行。

## 5. 各阶段测试入口一览

| 阶段 | 复用 | 拟新增位置 |
| --- | --- | --- |
| P01a | `test_worker_roundtrip.py`、`test_worker_exit_report.py`、`test_savefig_capture_params.py` | 上述文件内；移除守卫的 None 返回应使 EX03 反例变红 |
| P01b | `tests/native/test_run_cli_integration.py`、`test_run_codes.py`、`tests/bridge/test_bridge_capture.py` | `test_run_cli_integration.py`（script_end terminate）；预览发布失败保留旧图 |
| P05a | `test_preparation_session*.py`、`PreparationCard.test.tsx`、`preparationEntries.test.tsx` | 同文件；契约 fixture 取自真实后端 JSON |
| P02 | `test_dependency_plan.py`、`test_dependency_transaction.py`、`joint_dependencies/` | 同文件；真实 generation + pip check 事务 |
| P03 | `test_import_origin_metadata.py` 等 IOR 系列、`test_dependency_scope.py` | 新增"声明 provider → 对应发行包"测试，保留旧字段兼容断言与"观测不成安装名"两条不变量；四 provider 仲裁与账本合并 |
| P04 | `test_user_environments.py`、`test_project_scan*.py`、`project_venv/` | 同文件；13 候选 / VSCode 文件夹 / system-only |

## 6. 本次实际执行（命令与退出码）

复现均用主工作区 `.venv` 的 Python（只读使用），`python -I`，输出落在会话 scratch，不进仓库：

* `python -I repro_ex.py <worktree> <py> <scratch>`（worker 协议 build，EX02/EX03 各 direct / worker 两种）→ 退出码 0，结果见 §2.1。
* `python -I dep_probe.py <worktree> <scratch>`（`depplan.plan` 合成 TargetFacts，DEP-02/03/05/11）→ 退出码 0，结果见 §2.4。
* 审计包自带 `audit_dependencies.py --mode plans --repo <worktree>`：**退出码 1**（脚本内置 `Repository baseline changed` 基线校验，
  非产品失败），因此改用上面自写的最小复现。
* `PYTHONPATH=$PWD/src python -m pytest tests/test_docs_references.py tests/test_agents_rules_index.py -q` → 23 passed，退出码 0（文档相对链接 / ADR 号 / 指导文档索引）。未跑 ruff（无 Python 改动）。
