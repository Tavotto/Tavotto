# ADR 0122：选中的依赖声明是新托管环境的安装根；版本差异进准备状态；互斥 provider 先仲裁再装

日期：2026-10-12 · 状态：**Proposed**（兼容性改造 P02，随该 PR 评审）
相关：[0061 联合依赖准备](0061-joint-dependency-preparation.md)、[0114 环境建议 / 检查 / 采用](0114-environment-recommend-check-adopt.md)、
[0115 授权绑定实际影响](0115-dependency-authorization-binds-impact.md)、[0119 Import 来源解析](0119-import-origin-static-evidence.md)；
规则全文 `docs/rules/backend/dependency-repair-and-packages.md`「声明 roots（P02）」。

## 问题

联合计划（`depplan.plan`）只把「脚本里被 AST 认出的无条件第三方 import」当安装根；声明（requirements / PEP 621 / PEP 723）解析出来之后，
没被某条 import 点名的只做 **constraint**（约束版本，不触发安装）。审计（DEP-02 / 03 / 04 / 11）复现了三种后果：

1. 异名包（`import auditclient`，项目声明 `audit-driver`）、变量名 / 别名 / 函数体内的动态导入——声明明明点了名，计划 `nothing_needed`、`requirements=[]`，
   脚本在新一代里缺模块（DEP-10：把声明交给同一个 pip 路径，传递依赖一并装上、脚本输出 42）；
2. 已装 `auditroot 1.0`、项目声明 `>=2.0`：`nothing_needed`，`satisfied[].matches_declared=false` 只是一个字段，没人读；
3. 若把声明直接并进 roots，旧 curated 推断（`cv2` → `opencv-python`）会和声明的 `opencv-python-headless` 一起装，同一代里两个互斥 provider。

## 决策

### 一、声明是安装根，扫描只补充与解释

目标是**受管环境**时，选中的 runtime 声明（组与 marker 已按目标求值；纯 constraint、未选组、marker 为假不算）里，**与这个脚本相关**、且目标里没有（或装着的版本不满足声明）的，
进 `requirements`（带 extras / 版本 / hash 闭包），由既有的 generation 事务一次 pip 联合求解、`pip check`、关键 import、worker 自检、激活——没有第二套安装器、没有自己的传递依赖求解。
「相关」（`depplan._relevant_declarations`）= 脚本里有 import 能指到这个发行包（无条件 / 函数体内 / 别名动态导入；`try … except ImportError` 的可选 import、条件、`TYPE_CHECKING` 不算），
或脚本有归不到任何发行包的 import / 非字面量动态导入（逐条对应不了时，声明就是权威）。requirements.txt 里躺着、这个脚本从没碰过的包不因此被装、也不因此让计划 `ready`——
旧合同「不相干的声明不污染别的脚本」保留（`test_a_tool_script_s_missing_package_never_enters_an_independent_plot_s_plan`）。

用户自己的 venv 目标**永不**并入 roots：它是只进不退的原地安装，只装脚本静态缺的。

### 二、当前环境的差集与新一代的完整集合分开建模

`JointPlan.declared_gaps`（可选字段，只在有差集时出现，`plan_version` 不升）：选中的声明相对**此刻跑脚本的环境**缺什么 / 版本不满足什么，每条带 `relevant` 与 `action`。
`action=install` 只在计划 `ready` 且它在安装集合里；`recorded` = 只记录偏差。`ready` 的触发多了一条：相关差集 + 此刻跑脚本的环境是 Tavotto 自己的
（受管代 / 内置 runtime / worker-env / 私有 Python / 尚未落盘的新一代，`deprepair._run_env_ours` → `plan(run_env_replaceable=)`）。
用户交给我们的系统 / 本进程解释器能原样跑就不为声明偏差动手（不静默升降包）；缺了模块由运行后的有界修复接手（既有路径）。

### 三、roots 不与 curated 取并集：互斥 provider 的最小仲裁（AC-08 启用门）

同一个 import 名有多个互斥发行包只有一张表：`depresolve.ALTERNATIVE_DISTRIBUTIONS`（原本只用于观测）。仲裁只读**声明**：
点名了其中恰好一个 = 它胜出，替掉 curated 的名字（`missing` / `install_only` 的 `distribution` 也是它）；点名了两个及以上 = `dependency_conflict`（不是都装）；
一个都没点名 = 维持 curated，**观察到的 provider 不是授权**（`test_the_observed_provider_never_becomes_an_install_name`、
`test_the_installer_and_the_resolver_do_not_read_the_new_observation` 照旧成立）。账本合并同源：新一代的 delta 点名了某个 provider，账上互斥的那个不带回
（`deprepair.generation_requirements`），记账时划掉（`forget_install`），账上已经同时有两个的只留最后记的。

### 四、有意改动的旧合同

`test_possible_imports_are_listed_but_never_installed` 的一半：**函数体内**的 import 且项目声明了它，从「只列出不装」改为「声明 root，要装」（DEP-03）；
可选 / 条件 / TYPE_CHECKING 仍只列出。`test_install_facts_measure_the_set_against_the_target_not_the_current_interpreter`：脚本里有一个归不到任何发行包的 import 时，
marker 求值为真的声明进新一代而不只是约束。

## 不做

不新增包名表、不猜 import 同名 PyPI 包、不为声明去改用户环境、不碰 P03 的范围（观察到的来源进计划、editable / VCS / 本地 wheel 的来源保留、`.pth` 不完整）。
