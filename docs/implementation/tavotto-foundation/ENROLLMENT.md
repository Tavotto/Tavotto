# case enrollment 台账（派生视图）

真值是 [`enrollment.json`](enrollment.json)，本文件由 `tools/generate_enrollment.py` 生成，不手改。
状态含义见 [`03_CI_POLICY.md`](03_CI_POLICY.md) §3 与 ADR 0053 §五：**只有 enforced 且结果目录里有有效通过记录的实例才算通过**；
planned / observing / later 是登记，不是成绩。

能力版本：`u04` · 计数：enforced 12 · later 1 · observing 6 · planned 14

| case | 标题 | enrollment | lane | 阶段 | 用例 | fixture | 场景 |
|---|---|---|---|---|---|---|---|
| FO01 | 脚本同目录 CSV | enforced | pr | U03 | `tests/test_foundation_first_open.py::test_fo01_same_directory_csv_opens_automatically` | `tests/fixtures/foundation/single_file_csv` | FO01 |
| FO02 | 脚本目录与运行根目录不同 | enforced | pr | U03 | `tests/test_foundation_first_open.py::test_fo02_scripts_and_data_split_asks_once_then_runs_at_the_project_root` | `tests/fixtures/foundation/split_scripts_data` | FO02 |
| FO03 | __file__ 与模块相对导入 | enforced | pr | U03 | `tests/test_foundation_first_open.py::test_fo03_file_relative_data_and_local_package_open_automatically` | `tests/fixtures/foundation/split_scripts_data` | FO03 |
| FO04 | 项目外有效绝对路径 | planned | integration | U03 | — | — | FO04 |
| FO05 | h5py 原生读取 | observing | integration | U03 | — | — | FO05 |
| FO06 | exists/glob/listdir/read 一致 | planned | integration | U03 | — | — | FO06 |
| FO07 | 同名干扰数据 | enforced | pr | U03 | `tests/test_foundation_first_open.py::test_fo07_same_name_data_in_two_places_asks_and_honours_the_choice` | `tests/fixtures/foundation/same_name_data` | FO07 |
| FO08 | 项目移动与外部数据失联 | planned | integration | U03 | — | — | FO08 |
| FO09 | 中文空格、大小写、跨盘符 | planned | integration | U03 | — | — | FO09 |
| FO10 | 读写权限与受保护原件 | planned | integration | U03 | — | — | FO10 |
| FO11 | 真实不同 Python minor | observing | integration | U03 | — | — | FO11 |
| FO12 | 宿主 AST 不认识目标合法语法 | observing | integration | U03 | — | — | FO12 |
| FO13 | 真实二进制依赖 ABI 隔离 | planned | integration | U04 | — | — | FO13 |
| FO14 | 项目外命名 Conda 环境 | later | nightly | X01 | — | — | FO14 |
| FO15 | 显式环境与项目约束冲突 | enforced | pr | U03 | `tests/test_foundation_first_open.py::test_fo15_explicit_interpreter_without_matplotlib_stops_with_a_reason` | `tests/fixtures/foundation/single_file_csv` | FO15 |
| FO16 | 不支持的 Python/能力 | observing | integration | U03 | — | — | FO16 |
| FO17 | 同一基础 Python 的两个 venv | observing | integration | U03 | — | — | FO17 |
| FO18 | 三个以上额外依赖联合准备 | observing | integration | U04 | — | `tests/fixtures/foundation/joint_dependencies` | FO18 |
| FO19 | 本地实验室模块和重名引擎模块 | enforced | pr | U03 | `tests/test_foundation_first_open.py::test_fo19_user_modules_shadow_engine_names_and_still_win` | `tests/fixtures/foundation/shadowed_engine_modules` | FO19 |
| FO20 | markers/extras/所选依赖组 | enforced | pr | U04 | `tests/test_foundation_dependencies.py::test_fo20_markers_extras_and_selected_groups_prepare_only_what_applies` | `tests/fixtures/foundation/joint_dependencies` | FO20 |
| FO21 | 依赖约束不可同时满足 | enforced | pr | U04 | `tests/test_foundation_dependencies.py::test_fo21_conflicting_declarations_stop_and_keep_the_environment` | `tests/fixtures/foundation/joint_dependencies` | FO21 |
| FO22 | 已安装但原生库无法 import | enforced | pr | U04 | `tests/test_foundation_dependencies.py::test_fo22_installed_but_unimportable_is_caught_by_verification` | `tests/fixtures/foundation/joint_dependencies` | FO22 |
| FO23 | 无系统 Python/uv/pip 冷启动 | planned | release | U05 | — | — | FO23 |
| FO24 | 离线且受管 runtime/wheels 缓存齐备 | planned | integration | U05 | — | — | FO24 |
| FO25 | 离线且无可用缓存 | planned | pr | U05 | — | — | FO25 |
| FO26 | 下载损坏、截断和错误哈希 | planned | integration | U05 | — | — | FO26 |
| FO27 | 准备/运行阶段取消 | enforced | pr | U04 | `tests/test_foundation_dependencies.py::test_fo27_cancel_during_install_is_a_clean_terminal_state` | `tests/fixtures/foundation/joint_dependencies` | FO27 |
| FO28 | 磁盘不足和只读目录 | planned | integration | U04 | — | — | FO28 |
| FO29 | 并发项目与活跃 native 会话 | planned | integration | U04 | — | — | FO29 |
| FO30 | 预检后输入或环境改变 | planned | integration | U09 | — | — | FO30 |
| FO31 | 首开/二开/会话重启不重复准备 | enforced | pr | U04 | `tests/test_foundation_dependencies.py::test_fo31_first_second_open_and_restart_do_not_prepare_again` | `tests/fixtures/foundation/joint_dependencies` | FO31 |
| FO32 | 真实打开—编辑—重放—导出 | planned | release | U09 | — | — | FO32 |
| U01-S1 | single_file_csv 经真实 HTTP 服务（会话认证）首开 → 准备 → 渲染 → 旧后端导出 PDF/PNG → 独立读回 | enforced | pr | U01 | `tests/test_foundation_harness.py::test_u01_s1_first_open_and_export_through_the_public_entry` | `tests/fixtures/foundation/single_file_csv` | FO01, FO32 |
