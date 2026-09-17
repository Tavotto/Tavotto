# 注册表、静态扫描与试运行探测

> 原文出自 `src/tavotto/AGENTS.md`「渲染引擎核心机制」（2026-09-17 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- 素材扫描用 `os.walk` 当场剪枝隐藏目录（.venv/.git/.rendered/.qa_*）与隐藏文件，
  不是 rglob 后过滤——既是噪音也是性能（图库旁边常年躺着工具产物）。
- 新脚本 / stem 变化：改**图库目录下的 `tavotto_registry.json`**（注册表随图库走；
  `engine/registry.py` 只负责加载校验，重复 stem 仍直接报错；
  一脚本多产物 / 归属有歧义的 stem，裁决结果记在各图库自己的注册表文件里，勿改）。
  `entry` **不限于 main/render/__main__**——worker 就是 `getattr(module, entry)()`，
  任何合法标识符都行；`script` 键是图库**相对路径**（POSIX 分隔符），
  子目录里的脚本照样登记（worker 会把脚本自己所在目录也加进 sys.path）。
- **静态扫描（`engine/discover.py`）不再只认字符串字面量**：抽象求值覆盖
  模块级常量、f-string、`Path(...) / "x"`、`.with_suffix()/.with_name()/.joinpath()`、
  `os.path.join()`、`.format()`、`%`、`+` 拼接、`Path(__file__)` 自命名，
  以及**跨函数传播**（`save_panel(fig, "Fig1")` → 包装函数里的 `OUT / f"{stem}.pdf"`）
  与常量 for 循环展开。递归扫子目录（剪掉 .venv/__pycache__/node_modules…）。
  手动生成/合并：`python -m tavotto.engine.discover <figures_dir> --write`
  （现有条目永远优先，冲突 stem 只报告不裁决）。
- **试运行探测（`engine/probe.py`）**：stem 真的只有运行期才知道时（遍历数据
  目录、读配置、命令行参数），把脚本**跑一遍**按真实产出登记——worker 本来
  就在 build 阶段拦 savefig 并按真实文件名捕获，跑得起来 = 能参数化。
  静态仍解不出的报 `dynamic_names`，交给这条路；**绝不猜，也绝不静默跳过**
  （静默跳过 = 用户拿到空注册表却不知道为什么）。
  界面入口：顶栏项目菜单 / 设置 →「脚本注册表」（扫描 / 试运行 / 手工裁决）。
  * **任意项目内 `.py` 都可主动 probe（2026-08-26，Compatibility Bridge
    Session 3）**：`probe.script_inventory()` 是「项目里有哪些 .py、各自
    什么状态」的唯一清单（稳定 reason code：registered / static_candidate /
    dynamic_stems / no_static_output / infrastructure / unparseable），
    walk 规则复用 `discover._iter_py`（同一个实现的两个视图）——**发现维
    放宽只影响「列给用户挑」，自动静态起草的候选口径（`iter_scripts` +
    SAVE_FUNCS）一字不变**。`/api/registry/probe` 的路径校验一律在 realpath
    之后（`..` 回溯 / symlink 逃逸 / 项目外绝对路径 / 目录 / 非 .py 各有
    稳定 code），解释器仍走 pool 的 runtime selection，前端指定不了。
  * **probe 错误是结构化的**（`probe.ERROR_*` 稳定码表：script_not_found /
    script_path_outside_project / unsupported_script_type /
    script_probe_failed / script_no_figure / missing_dependency /
    execution_timeout / execution_cancelled / invalid_entry /
    multiple_stem_conflict）：主文案按 code 由前端换语言，traceback 只进
    诊断详情。**失败不写注册表**；产出 stem 已被另一份**仍在磁盘上的**脚本
    登记时报 `multiple_stem_conflict` 而不是静默抢走（裁决走 PUT
    /api/registry 的手工路——那才是用户显式指认；归属脚本已不存在的死条目
    照旧顺畅重登记）。
  * **probe 可取消、同脚本互斥（2026-08-26，Session 5）**：app 层
    `_PROBES` 按 (项目 id, script) 登记在跑的试运行（第二个请求 409
    `probe_in_progress`）；`POST /api/registry/probe/cancel` 置取消
    Event 并 `pool.force_cancel`（**当场 kill**，不走优雅关停——shutdown
    要抢被 build 占着的 `w.lock`，等到超时的取消不叫取消）。
    `probe(should_cancel=...)` 一旦判取消**不再尝试下一个 entry**，被杀
    worker 的失败如实归类 `execution_cancelled`（不报「脚本坏了」）；
    取消输给成功——脚本在取消前跑完就照常登记。SSE `probe.started` 在
    执行开始前发出（前端状态机 starting_runtime → running 的边界）。
    看护 `tests/test_asset_library.py`（cancel sentinel：30s 内返回 +
    会话从池里消失 + 注册表零改动）。
  * **entry 候选静态化**（`discover.probe_entry_candidates`，绘图宽口径
    `PLOT_FUNCS` 只喂它，不进起草）：main/render 零参可调才试、裸顶层绘图
    直接 `__main__`、自定义零参绘图函数上限 4 个——盲试不存在的 entry 也要
    把顶层跑一遍，纯属浪费冷启动。**成功路径只执行一次**：热会话留池复用，
    失败 entry 各自新建 worker（看护 `tests/test_script_probe.py` 的
    execution-count 用例）。
