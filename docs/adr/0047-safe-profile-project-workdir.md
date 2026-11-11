# ADR 0047：safe 档的工作目录可选脚本目录——项目级开关「在脚本目录里运行」

日期：2026-09-06 · 状态：**Accepted**

> 2026-09-28 修订（[ADR 0107](0107-silent-system-interpreter-adoption-and-native-workdir.md) §二，用户拍板）：项目用的是**用户自己的
> Python**（项目外、不归 Tavotto 管的解释器，`pool.user_interpreter_in_effect`）且没决定过工作目录时，默认档是脚本目录
> （`project`），不单独确认——与原生 `cd 脚本目录 && python fig.py` 一致。派生的默认、不写设置；用户选过任何一档按他选的；
> 内置 / 受管 / 项目 venv / 全局显式选择仍默认沙盒；守卫、savefig 捕获、写回一字不动。§三与「不做的事」第一条对其余情形原样成立。

> 2026-11-11 修订（用户拍板「所有用户当小白：低风险可撤销的事静默替用户决定」；Guardian cl-0fb5a738f0 / cl-0c29eca81d，
> #1253 / #1254）：「不做的事」第一条「不自动切换」改为**有运行时证据就自动切换**。项目**没决定过**工作目录、默认档是沙盒，
> 沙盒里跑完一次后运行时证据（ADR 0084 §运行时证据：`workdir.probe_hint`，`source = runtime`，至少一条相对目标——glob / 列目录 /
> 探路、或 C++ 读取器点名的路径——在脚本目录里找得到）说数据只在脚本目录里：失败 / 没出图带 `workdir_hint`，或 build 成功但
> glob 回空（画出来的是没数据的图）——`pool._build_with` 记下这条证据（项目设置 `workdir_runtime_evidence`，**不是**
> `workdir` 决定）、关掉这个项目的会话、在脚本目录里**静默重跑一次**，结局就是那一次的结局。默认档随之是脚本目录，
> `implied_by = runtime_evidence`，`decided` 仍为假、授权 `granted_at = None`——与 §二修订（ADR 0107，用户自己的 Python）同一种
> 派生默认，准备计划同样就地吸收（`preparation.implied_grant_absorbable`）。护栏：用户决定过（含选了沙盒）不动；默认已不是沙盒
> 不动；只换一次；没有证据不动。撤销：渲染环境里选沙盒（一次决定，从此不再自动；失败卡照旧给「在脚本目录里运行」入口）。
> 风险与点那个入口完全相同：守卫、savefig 捕获、写回一字不动，变的只有 cwd。代价：沙盒那一遍已跑过，脚本被执行两次
> （与 ADR 0133 safe→native 回退同一条已接受的代价）。看护 `tests/test_runtime_probe_hint.py`（没决定过就出图 / 无证据不换 /
> 选了沙盒不换 / parquet）、`tests/test_preparation_session_e2e.py::test_an_undecided_project_gets_its_figures_without_a_click`。

> 编号更正（2026-09-07）：本文最初以 0045 落地（PR #301），与先落地的
> `0045-cjk-font-fallback-chain.md`（PR #295）撞号。内容一字未改，只换号。

## 背景

worker 把 cwd 切到会话沙盒是 safe 档的写入边界：脚本用相对路径写出 / 删除的东西不碰
真实图库。为了让 `pd.read_csv("data.csv")` 这类相对读仍然成立，`figcapture` 给只读的
`open` 装了回退到脚本目录的逻辑（`builtins.open` / `io.open` / `Path.open` 三个入口）。（这是本 ADR 当时的状况；2026-09-24 起另有第四个入口 numpy 的 `DataSource.open`——`np.loadtxt` / `np.genfromtxt` 经它读——见 `figcapture` 模块头与 `docs/rules/backend/figure-capture-and-execution.md`）

真实用户数据（2026-09-06，`2d 处理/` 的九个 ovito 脚本）暴露了这条回退的盲区：脚本用
`os.path.exists("1/etch_5-5.lammpstrj")`、`glob("./**/*.lammpstrj")` 判数据在不在，再交给
ovito 的 C++ `import_file` 读——三样都不经 Python 的 `open`。沙盒 cwd 下它们全部
「未找到」，脚本一张图都不画，界面报「stem 不存在」（#298 之后报「脚本跑完没出图」）。
**脚本永远不改**——用户就是典型用户，脚本要改才能出图等于产品不兼容这类脚本。

补回退覆盖面（`exists` / `stat` / `glob` / `listdir`）救不了 C++ 读取器；把 native 档
做进池被 ADR 0021 §1（桌面 sidecar 不得 spawn 用户 Python）与 §9.4（native 恒禁写回）
两条硬约束否掉。剩下唯一根治：**让脚本在自己的目录里跑**。

## 决策

### 一、safe 档多一个维度 `cwd_mode`，其余一字不动

`execspec.ExecutionSpec` 新增 `cwd_mode ∈ {sandbox, project}`（进 `STABLE_FIELDS`：它
改变脚本看到的世界，是语义不是路径）与 `sandbox`（机器相关，写入边界目录）。
`safe_spec(cwd_mode=project)` 把 `cwd` 换成脚本所在目录，`worker_argv` 只在这个模式下
多两个 token `--cwd <脚本目录>`——**默认模式的 argv 逐字节不变**（golden 用例钉着）。
worker 收到 `--cwd` 就 `chdir` 到那里，并且**不装**相对路径只读回退（相对路径本来就指向
项目）。

不变的清单，每一条都有用例：解释器链（ADR 0018 / 0044）、savefig 捕获不落盘、
`Path.unlink` / `Path.write_text` 守卫、写回（one_shot 重放取同一个模式）、沙盒目录仍存在
但脚本不往里写。

### 二、变的只有一条，而且要如实说

**脚本用相对路径写的中间文件会像终端里一样落进项目目录**（`open("cache/x.txt","w")`、
`np.save`、`to_csv`、C++ 写入器）。这是这个模式的定义，不是漏洞：用户在终端里跑同一个
脚本得到的就是这些文件。文案与机制逐条一致（ADR 0014 的纪律）：确认框写明「读得到 /
写出的落进项目 / Tavotto 仍不替它保存图片、不删不改已有文件 / 只对这个项目生效」。

### 三、项目级开关，首次开启确认一次

`engine/workdir.py` 是模式的唯一出处：存项目设置 `workdir.mode`，不写全局（A 项目的
脚本形状不该决定 B 项目的写入边界）；不认识的值当默认（设置文件被手改坏了，写入边界
不许悄悄消失）。`PATCH /api/engine/workdir {mode}` 改了就 `pool.shutdown_all(root)`——
cwd 是 spawn 时定下的，活着的会话还端着旧目录。三条 spawn 路径（Python 池、workerd
spawn 规格、one_shot）从同一个出处取模式。

界面：设置 → 渲染环境多一行开关；「脚本跑完没出图」（`no_figures_captured*`）的错误块
直接给「改为在脚本目录里运行」的入口——从失败到修好一步。切换成功后把这些面板重新排上。

### 四、这不是 native

进程仍是 Tavotto 自己起的 safe worker：ADR 0021 §1 的所有权约束一个字没动，ADR 0014
「不把 safe 的守卫悄悄搬进 native」也没动——这里是 safe 档自己多了一个开关。native 的
`cwd` 是用户的，没有 `cwd_mode` 这个维度（构造时拒绝）。

## 不做的事

* ~~不自动切换：零捕获时只给入口，不替用户决定（那是他项目里的写入边界）。~~ 2026-11-11 修订：没决定过的项目有运行时证据就自动切换并重跑一次（见文首）；决定过的照旧只给入口。
* 不扩回退覆盖面到 `exists` / `glob`：救不了 C++ 读取器，只会让脚本「以为」数据在。
* 不改根 AGENTS 的「守卫不放松」：守卫原样；变的是 cwd，且是用户按项目显式开的。

## 看护

`tests/test_workdir_mode.py`：argv 只多 `--cwd`、`cwd_mode` 进 stable payload、老 payload
读回默认、native 拒绝；开关项目级 / 不写全局 / 坏值当默认；真 worker 沙盒模式零张图 vs
项目模式 exists / glob / listdir / 相对 open 全成立且图被捕获、相对写落进项目、守卫拦住
删除、savefig 不落盘、沙盒空；三条 spawn 路径同源；端点 400 码 + 切换重建会话。
`web/src/components/WorkdirRow.test.tsx`：开启先确认、取消不改、成功重排「没出图」面板、
关闭不确认、建议只在沙盒模式出现、英文无中文泄漏。
