# ADR 0106：缺包时无提示改用这台机器上装着它的 Python；用户自己的 Python 默认在脚本目录里跑

日期：2026-09-28 · 状态：**Accepted**（两条都由用户 2026-09-28 拍板）
修订：[0044 系统解释器作为修复候选](0044-system-interpreter-as-repair-candidate.md) §二（「不无感切换：采用要用户点」）与
「不做的事」第二条（自动采用系统解释器）；[0047 在脚本目录里运行](0047-safe-profile-project-workdir.md) §三（首次开启确认一次）与
「不做的事」第一条（不自动切换）；[0079 缺包时先找用户自己的 Python](0079-user-environment-discovery-and-auto-adoption.md)
「不做的事」第三条（运行后那条修复卡片不自动改用）
相关：[0018 项目 Python 环境解析](0018-project-python-environment-resolution.md)、[0057 首开的环境与工作目录](0057-first-open-environment-and-workdir.md)、
[0084 探路调用是首开证据](0084-probe-calls-are-first-open-evidence.md)、[0053 准备接口](0053-foundation-contracts-and-preparation.md)（grant 字段）

## 用户的决定（2026-09-28，原话）

> py 里引用其他的包，Tavotto 内置环境不存在，但是用户环境支持 Python 版本时，能够更加无感的切换，并能够实现与原生环境一致的功能。

在两个选项上明确拍板：

1. **全自动、无提示切换**：内置环境缺包，而这台机器上有受支持且健康、import 得到缺的那个包的解释器时，直接采用它并重新渲染，
   不需要用户点，不弹提示。
2. **采用用户自己的 Python 时，脚本默认运行目录用「脚本目录」**，与原生 `cd 脚本目录 && python fig.py` 一致；内置 / 受管环境仍默认沙盒。

## 实测现状（Windows Server 2025，冻结安装包，PR #682 head f765cd90）

机器上有 Python 3.7.6（装了 adjustText，不受支持）与 Python 3.12.10（matplotlib 3.11.2 + adjustText 1.4.0），脚本
`from adjustText import adjust_text`。渲染返回 `project_env.system = [{python: …Python312\python.EXE, ok: true, support: "verified",
requested_module_ok: true}]`，修复卡片第一项「改用这台机器上已有的环境 … Python 3.12.10，不安装任何东西」，**要用户点**；点后约 10 s
出图，重启后项目仍记住。采用后运行目录仍是沙盒：相对读 `data.csv`、import 同目录 `helper` 都成立（只读回退 + `sys.path`），但
`os.getcwd()` 是沙盒，相对路径写出的文件不进项目目录——原生会进。

跑前的门（ADR 0079 §四）本来就会在这种形状上自动改用，这次没有生效的具体原因在那台机器上没有取到证据（门在「用户选过直接跑 / 轮次用完 /
工作目录还要先问」时不开，开关 `TAVOTTO_USER_ENV_DISCOVERY=0` 时整个关掉）；无论原因是哪一条，**运行后**缺包这一层（ADR 0044）都是
兜底，而它恰恰是唯一还要用户点的那一层。

## 决策

### 一、运行后缺包：项目 venv 那一层没接手成，就无提示采用第一个合格的系统解释器

`pool.try_project_env`（运行后缺包的唯一接手点）原样先走项目 venv 那一层；没成时 ADR 0044 的第二层照旧把
`pool.system_python_candidates()` 逐个体检（第一个健康的就停、上限 `SYSTEM_PROBE_LIMIT`、报缺包的那个不再探）。新增的只是紧接着的
一步（`pool._adopt_system_interpreter`）——**五道判据全过才采用**：

1. 开关没关：`projectenv.auto_adoption_off()`（`TAVOTTO_USER_ENV_DISCOVERY=0`），与跑前的门**同一个开关、同一个判据**；关着时照旧
   只列候选。测试进程默认关（`tests/conftest.py`）。
2. 项目里**没有** venv（venv 那一层的结论是 `project_env_not_found`）。有 venv 而它缺包 / 不合格时，那是用户为这个项目备的环境，
   缺包该装进它（ADR 0079 §四同一条），不绕过它换一个项目外的解释器。
3. 候选合格：`projectenv.auto_adoptable_system_candidate(system)`——就是 `healthy_system_candidate()` 那一条（按候选优先级的第一个
   健康者），再明写两道：
   * **支持档在 `projectenv.AUTO_ADOPT_SUPPORT = (verified, unverified_but_compatible)` 里**。`verified` = Python 在
     `PYTHON_TESTED`、matplotlib 在钉版区间；`unverified_but_compatible` = Python 在支持区间内、matplotlib 在钉版区间外但 import 得到——
     与跑前的门同一口径（ADR 0079 §三：同档 verified 优先，未验证的也可以被自动挑中），`support_status` 的文档本来就说它「照用，但如实
     标注」（标注进项目记录与诊断包）。`unsupported`（Python 在 `[3.10, 3.15)` 之外，如实测里的 3.7.6）**永不**自动采用，照旧进
     `system_rejected` 说明原因。判据按档位明写，不靠「`ok` 恰好蕴含」。
   * **缺的那个包确实 import 得到**：`requested_module_ok is True`（None = 没量到，不算）。
   第一个健康者不合格时**不往后找**：后面的根本没体检过，拿没体检过的去换环境是替用户做没根据的决定。
4. 此刻的解释器是**机器替用户挑的**：`pool.machine_chosen_interpreter()`——环境变量 / 设置里的全局显式选择、用户为本项目挑过的
   （`automatic=False`）、明确选回默认链条的（`mode=default`）一个都不碰。这份判据以前只住在 `deprepair._auto_adopt_allowed` 里，
   现在搬进 `pool`（解释器决策的权威），跑前的门与运行后这一层共用；项目记录那一半（`projectenv.record_allows_auto_adopt`）在
   `remember(only_if=…)` 的写入锁里再判一次，判完到写之间落地的用户决定不会被盖掉。
5. 那个环境此刻没有正在被改动（`pool.is_mutating`，`envlease` 那一张表）。

采用的机制与项目 venv 那一层**完全相同**，不另起一套：`projectenv.remember(automatic=True, trigger="missing_dependency", module=…,
health=体检结论)` → `note_project_python_ok` → `invalidate(script)`；`pool._build_with` 拿到 `ok=True` 重取一次会话，新会话按
`resolve_worker_python` 第 3 条解析到它，用户看到的就是图出来了。防循环沿用 `projectenv.mark_attempted`：同一 (项目, 脚本) 一次 build
最多自动切一次，用户点「重试」（`reset_cache`）才重新来一轮。trigger 与项目 venv 那一层同一个 `missing_dependency`（触发事件是同一件：
此刻的解释器跑脚本时报了缺包）；接手的是哪一种由 `pool.remembered_source()` 分——项目外、不归 Tavotto 管的是 `system`。

多个健康候选：体检在第一个健康者处就停，所以「采用哪一个」= `system_python_candidates()` 的优先级顺序里第一个健康者，与
`healthy_system_candidate()` 与修复卡片列出的那一个一致。

### 二、用户自己的 Python：没决定过时默认在脚本目录里跑

`workdir.mode_for()` 在项目**没决定过**工作目录（`workdir` 键不存在或值不认识）时回 `workdir.default_mode()`：

* 项目此刻用的是**用户自己的 Python** → `project`（脚本目录）；
* 其余（内置 runtime、Tavotto 受管环境、项目自带的 venv、全局显式选择）→ `sandbox`，与 ADR 0047 相同。

「用户自己的 Python」的判据唯一出处 `pool.user_interpreter_in_effect()`：项目级环境决策指向一个**项目之外、不归 Tavotto 管**的解释器
（`remembered_source() == system`：本 ADR 自动采用的、用户在渲染环境 / 修复卡片里为项目挑的、跑前的门改用的 Conda / pyenv 环境），
文件还在，且没有全局显式选择压过它。只读设置与 `stat`，不起子进程。

这是**派生的默认，不写设置**：解释器被改回内置（界面上的「改回」= `remember_default`）或换成受管环境，默认随之回到沙盒——不把环境的
临时状况变成用户的长期设置。用户在渲染环境里选过任何一档（含沙盒、项目根），按他选的，派生默认不再起作用。

**修订 ADR 0047 §三（首次开启确认一次）与「不自动切换」**：对「系统解释器 + 脚本目录」这一组合不再单独确认。理由：用户选择（或接受
Tavotto 替他选）自己平时跑脚本的那个 Python，本身就是「像我在终端里那样跑」的意思表示；在他的终端里，相对路径写出的文件本来就落进脚本
目录——这正是用户拍板要的「与原生环境一致」。ADR 0047 当时确认的理由（「那是他项目里的写入边界，不替他决定」）在内置环境下仍然成立，
所以只对用户自己的 Python 放开，边界如下：

* **守卫一字不动**：`Path.unlink` / `os.remove` / `shutil.rmtree` / `os.rename` / `write_text` 等守卫、savefig 捕获不落盘、写回事务
  （one_shot 取同一个模式）全部照旧——变的只是相对路径写到哪（根 AGENTS「安全边界」那一条的原意）。
* **授权记录如实**：`workdir.grant_for()` 在派生默认下回 `cwd_write = {granted: True, granted_at: None, mode: "project"}`、
  `decided: False`，并多一个 `implied_by: "user_interpreter"`（非派生时为 None）——写入许可成立，但没有「那一次点头」的时刻，从哪来写在
  `implied_by` 里；`workdir.state()` 同带 `implied_by`，渲染环境里「脚本的运行目录」那一行据此说「正在用你自己的 Python，所以和在终端里
  一样……想隔离就改回沙盒」。
* **首开的证据门（ADR 0057 / 0084）只少问一种**：证据只指向脚本目录（`script_parent`）时不问——推荐答案就是已经生效的默认；数据只在
  项目根 / 两处同名不同值时照样先问（脚本目录一样不够用 / 机器不替用户裁决）。
* 不是 native：进程仍是 Tavotto 自己起的 safe worker（ADR 0021 §1、ADR 0014 原样）。

### 三、可追溯：无提示不等于无痕

* 项目记录带 `automatic=True`、`trigger=missing_dependency`、`module`、体检当时的 `python_version` / `matplotlib_version` / `support`；
  诊断包的 `environment_resolution`（`source=system` + 上面这些）与环境状态 API（`/api/engine/environment` 的 `project`）原样读它，
  不重新体检。
* 渲染环境对话框里「项目已改用：<路径>」照旧显示，缘由一句按 `automatic` 分：自动采用的说「内置环境没有 X，已自动改用这台机器上装着它的
  Python」，用户挑的仍说「已改用你为项目选择的现有环境」；旁边的「改用内置环境」一键改回（`remember_default`：之后不再自动挑）。
* 不发 SSE、不弹通知：这是用户明确要的「无提示」。跑前的门那条（ADR 0079 §四）的通知轨行为不变。

### 四、修复卡片上的「改用这台机器上已有的环境」保留为兜底

判据任何一条不满足（开关关着、项目有 venv、用户决定过、环境正被改动……）时，体检结论照旧挂在失败结构上，`deprepair.offer()` 照旧列成
`system_interpreter` 目标，用户点一次采用（`automatic=False`）。前端不改这张卡片。

## 不做的事

* 往系统解释器里装包（ADR 0044 / 0019 原样：它不是安装目标）。
* 扩大候选发现：运行后这一层的候选仍是 `system_python_candidates()`（ADR 0044 §一），不换成 ADR 0079 的 `userenvs.discover()`——
  那一份在跑前的门里已经用上了。
* 把全局显式解释器（环境变量 / 设置里指定的）也当「用户自己的 Python」改默认运行目录：它压过所有项目，A 项目的脚本形状不该决定 B 项目
  的写入边界（ADR 0047 §三的原意）；项目自带的 venv 同样保持沙盒默认（用户这次只对「系统解释器」拍板）。
* 自动采用 `unsupported` 档（Python 版本在支持区间外）的解释器。

## 看护

`tests/test_project_env.py` 文末「无提示自动采用」一组：真 worker 端到端（项目外的 venv 扮演系统解释器，`build()` 直接出图、
记 automatic / trigger / module、运行目录是脚本目录、相对写落进项目）；诊断读到的记录；只有不受支持候选（3.7.6）不采用且留在
`system_rejected`；健康但缺包不采用；判据按档位明写（七档参数化）；多候选取优先级第一且之后不再体检；七种情形不采用（开关关 / 全局显式 /
设置指定 / 用户挑过 / 明确默认链条 / 项目有 venv / 环境正被改动）且候选照样留给卡片；一次 build 最多自动切一次。
`tests/test_workdir_mode.py` 文末「用户自己的 Python」一组：九种项目环境形状下的默认档 / `implied_by` / grant（派生、不写设置）；用户选
沙盒 / 项目根压过派生默认、「改回」解释器后默认回沙盒；真 worker：用户自己的 Python 下没决定过也在脚本目录里跑且不问、守卫与捕获原样，
改回沙盒后盲区回来、相对写不进项目。
前端：`EngineEnvironmentCard` 的缘由按 `automatic` 分，`WorkdirRow` 的现状句按 `implied_by` 分（`*.test.tsx` 各一条）。
