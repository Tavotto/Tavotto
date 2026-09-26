# Figure 捕获、执行描述与 live-figure 会话

> 原文出自 `src/tavotto/AGENTS.md`「渲染引擎核心机制」（2026-09-17 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **Figure 捕获策略是共享语义（`engine/figcapture.py`，2026-08-21）**：
  桌面 worker 与浏览器 playground **各调一次同一份实现**。三件事只有这一个
  出处：`savefig` 的 stem 怎么取、脚本跑完还活着的 pyplot Figure 怎么补进来
  （去重按 Figure 身份、上限 `MAX_PYPLOT_FALLBACK=8`）、相对路径只读回退。
  * **没有 savefig 的脚本也要捕获**（`plt.plot(...); plt.show()` 是 AI 最常见
    的输出形态）。以前只有 browser.py 有兜底，桌面一张都捕获不到——同一份
    脚本两个入口两个答案，是数据级的分叉。
  * **fallback stem 按「本次捕获里的第几张」编号**（`<脚本名>`、`-2`、`-3`），
    **不按 `plt.get_fignums()` 的 figure 号**：脚本中途 `plt.close()` 过一次
    号就跳，用户的 override 于是挂在一个不存在的 stem 上，表现是「打开是
    空白的，什么都没报错」。
  * build 响应按 stem 带 `source`（`savefig` / `pyplot`）。`pyplot` 的那些
    **没有原始产物**：渲染 / 编辑 / 导出都成立，「写回原始文件」无从谈起
    （面板列表扫的是磁盘产物，因此它们天然不成为可写回的面板——这条结构性
    保证由 `test_compat_capture_parity.py` 看护）。
  * **相对路径只读回退**：worker 的 cwd 在沙盒里（那是**写入**边界），而
    `pd.read_csv("data.csv")` 在 `python figure.py` 下天经地义。只有「只读
    模式 + 相对路径（或**指向沙盒内部的**绝对路径）+ 按真正的 open 会用的那条
    路径判确实不存在 + 换算后仍在图库内」四条同时成立才改指到脚本目录；
    写 / 改 / 删 / 重命名一个字节都不经过它。沙盒**之外**的绝对路径一个都不碰。
    **`builtins.open` 与 `io.open` 两个都要 patch**——它们指向同一个 C 函数
    却是两个独立绑定，`pathlib.Path.read_text` 走的是后者，只补前者会让
    `open("x")` 好使而 `Path("x").read_text()` 报 FileNotFoundError；
    **3.10 还要第三个 patch 打在 `pathlib.Path.open` 上**（那一版
    `_NormalAccessor.open` 在类定义时就绑好了，前两个都够不着它）。
    **只认裸相对路径是不够的**：不少库在 open 之前先 realpath 一下
    （Pillow 10.4.0 的 `Image.open` 就是，12.x 已改回 fspath），回退看到的是
    `<沙盒>/x.png`——CompatBench 的 minimum 档抓到的正是这个。存在性判据
    **必须按真正的 open 会用的那条路径走**，拿沙盒根去拼的话，脚本
    `os.chdir()` 进子目录后自己写出来的中间结果会被无声换成图库里的原件。
  * **第四个入口：numpy 的 `DataSource.open`（2026-09-24）**。`np.loadtxt` / `np.genfromtxt`
    经它读，它先 `os.path.exists` 再 open，三个 open 入口够不着——脚本旁边的 `data.txt`
    读不到，而 `databinding` 对这种脚本判 `default_ok`（不问），前提是假的。包它的判据与
    三个 open 入口逐条相同（同一个 `_fallback_path`），外加「DataSource 的 `destpath` 就是
    此刻的 cwd」；只包已载入的 numpy（worker 经 matplotlib 已载入），不替脚本 import。
    候选名按 numpy 自己的那串找（`_possible_names`：原名、`.gz` / `.bz2` / `.xz` …）；整串里有一个在
    cwd 下存在就不改道（numpy 自己会读到它）。
    这**不是**把回退扩到 `exists`：脚本自己问 `exists()` 仍然得到沙盒里的真话。
    **输入观察器（`InputObserver`）同样包它**：numpy 的打开器在载入时就绑了原来的
    `io.open`，三处 open 包装看不见它读了什么；按返回文件对象的 `.name` 记实际打开的
    那个文件（改指来的与绝对路径的都算），否则数据身份与绑定比对漏掉它们（#545 评审）。
  * **回退只覆盖上面四个打开入口**：`os.path.exists` / `os.stat` / `glob` /
    `os.listdir` 与任何 C++ 读取器（ovito、h5py 的原生打开）都在盲区。用
    `exists()` 先判再 `open()` 的脚本在沙盒里会把「数据不存在」当真、跳过
    全部分析、一张图都不画——那时 `known == []` 的 `unknown_stem` 由
    `pool._explain_empty_capture` 换成 **`no_figures_captured`**，消息说清
    「脚本跑完但没出图」，worker.log 的尾部进 `traceback_text`（前端错误块
    的折叠区直接显示）。**只认显式为空的 `known` 列表**：字段缺失分不清
    「没有」和「没说」。两条控制面各接一处（`_error_of` / `_to_worker_error`）。
    **根治是 ADR 0047 的项目级开关「在脚本目录里运行」**：cwd 换成脚本目录、
    不装回退，守卫与 savefig 捕获不动；错误块直接给这个入口。**不扩回退到
    `exists` / `glob`**——救不了 C++ 读取器，只会让脚本「以为」数据在。
    一个字都没打印是**另一个 code** `no_figures_captured_silent`（占位是界面
    文案，不塞进 traceback 区）。日志尾部按**这一代的偏移**读（`_log_offset`，
    两条控制面都在启动前记；目录跨代复用、append 模式，不记的话读到的是上一代
    的尾巴）、按字节读再 **UTF-8** 解码（cp936 的 Windows 上 `read_text()` 会
    把中文与 `µ` 读成乱码）。
  * 浏览器侧**刻意没有**这条回退：playground 是单文件的，相对读报
    `missing_file` 才是对的。桌面的 `entry` 机制同样是超集（浏览器按
    `python figure.py` 跑，只有 `def main():` 而没人调用的脚本在原生 Python
    下也不画图）——这两条差异是**记录在案的**，不是疏漏。
- **统一执行描述与捕获描述符（2026-08-25，ADR 0013/0014）**：
  * 「跑一个脚本」的语义收在 `engine/execspec.py`：safe 档默认值唯一出处
    `safe_spec()`，worker 子进程 argv 唯一出处 `worker_argv()`——
    `EngineWorker.__init__` 与 `_spawn_spec()` 都是它的消费者
    （`test_workerd_pool.py` 对拍 + `test_execspec.py` golden 看护）。
    新入口不得再手拼 entry/cwd/argv。`spec.env` 只存**注入增量**，
    序列化绝不携带整份父进程环境。**safe 档的 `cwd_mode`（ADR 0047 / 0057）**：
    `sandbox`（默认）/ `project`（脚本所在目录）/ `project_root`（项目根），唯一出处
    `engine/workdir.py`（项目设置 `workdir.mode`，不写全局），三条 spawn 路径（Python 池 /
    `_spawn_spec` / `one_shot`）都从它取——写回的重放必须和热态用同一个 cwd。
    默认模式 argv 逐字节不变，两个真实 cwd 模式只多 `--cwd`。切换走
    `PATCH /api/engine/workdir`，改了就 `shutdown_all(root)`。
  * **首开的一次确认（U03，ADR 0057 §三）**：`workdir` 键不存在 = 没决定过。起第一个 worker
    之前 `pool._new_worker()` 调 `workdir.resolve_mode(root, script)`：决定过就用记住的；没决定过
    按 `engine/databinding.py` 的静态证据——脚本里的相对数据路径字面量只在项目根找得到
    （`project_root`）、或脚本目录与项目根各有一份同名而内容不同（`ambiguous`）——才抛
    `workdir_confirmation_required`（结构化选项 / 证据 / 怎么回答，四类入口同一个 code）；
    证据说不出话（`none` / `unknown`）或默认够用（`default_ok`）不问。**不猜、不就近替换、
    不搜同名、不自动切到真实 cwd**；决定项目级、问一次记一次，切回沙盒撤销授权但决定留着。
  * 每张捕获 Figure 的结构化描述（`CapturedFigureDescriptor`）唯一实现在
    `figcapture`：asset id `runtime:<script>#<stem>`（不透明标识，entry
    刻意不进 id）、`source_fingerprint`（只是 stale hint，别声称覆盖数据
    依赖）、writeback 能力**只能派生不能指定**（pyplot 捕获结构上拿不到
    原件）。worker v1 build 响应与 browser load 响应各带一份 `descriptors`
    （加字段不升版；legacy 信封零改动），probe 原样透传——worker/browser
    的逐字段对拍在 `test_compat_capture_parity.py`。
  * 「什么算一份图产物」唯一出处 `figcapture.ARTIFACT_EXTS`
    （`discover.OUT_EXTS` / `handoff.OUT_EXTS` 是镜像别名）；「stem 的原始
    产物在哪」唯一判据 `figcapture.find_original_artifact`。
- **live-figure 会话**：worker 跑一次脚本（拦截 `Figure.savefig` + `paper_style.save`，
  不写真实文件），Figure 常驻内存；override 直接 mutate artist 再导出带 gid 的
  SVG（dpi≈120 预览）——冷启动秒到分钟级，热态 ~40ms。
  * **savefig 的参数记进捕获描述符（`savefig_calls`，PR #675）**：以前拦截只取
    stem，`bbox_inches` / `pad_inches` / `dpi` / `transparent` 一个都没记。后果（审计
    T14 / T33 实测，教程 Fig1_kinetics）：脚本 `savefig(bbox_inches="tight", pad_inches=0.02)`
    的磁盘原件是 tight 的，live 图的框却是 figsize，紧贴图幅的 x 轴标题在预览与导出里被切掉半截。
    三条入口（safe worker 的 `_patched_savefig`、native bridge 的透传钩子、浏览器 playground）
    都经 `figcapture.savefig_call()` 记下每次调用的**实效值**（显式参数 > 调用那一刻的
    `rcParams["savefig.*"]`），记账规则唯一出处 `figcapture.record_savefig_call()`：只记认领
    这个 stem 的那张图的调用、最多 `MAX_SAVEFIG_CALLS` 次。字段三档，「不知道」独立一档：
    `None` = 没观察到、`[]` = pyplot 捕获从没存过盘、非空 = 调用列表。它不进 fingerprint。
    看护 `tests/test_savefig_capture_params.py`、`tests/bridge/test_bridge_savefig_params.py`。
  * **图幅（frame，ADR 0098）**：定义这张图的那次 savefig（`figcapture.frame_call`：与原件
    同格式的第一次，否则第一次）会裁到的框就是这张图。**不复刻 tight 算法**：
    `pathgeom.savefig_frame` 用记下的参数真跑一遍 savefig 到内存、读它交给
    `_tight_bbox.adjust_bbox` 的框；三条入口都经 `pathgeom.establish_frame`，在脚本跑完、
    instrument 之前（一切 override 之前）挂上，编辑不移动它。对外一切以 F 为准（`size_mm`、
    分数、预览、导出、描述符）；对内 Figure 仍是 figsize，F 只落在三处：输出
    `bbox_inches=F`（`pathgeom.output_kwargs`）、manifest 在 `pathgeom.in_frame`
    （同一个 `adjust_bbox`）里量、输入 `frac_to_display` 与 `axes.position` 的 setter 换算。
    `figure.frame = "figsize"` 关掉它（升级前的排版，ADR 0098 §三）。没有 `bbox_inches` 的
    脚本这几处都回到原来那一行，逐字节不变。看护 `tests/test_savefig_frame.py`。
  * **`paper_style.save` 捷径执行用户那份 `save`（ADR 0098 §四；也是 #667「写回原脚本」设计里点名的前置修正）**：
    以前整个换成只登记 stem 的 lambda，里面的 savefig 从没执行。现在先按 `stem` 认领，再调用
    原来的 `save`，其间每一次 savefig 被拦截、不落盘、记到这个 `stem` 名下（worker 的
    `_SAVE_AS`）；`save` 不可调用时照旧只登记。
- 安全：worker `cwd=沙盒`（挡相对路径写出/删除）+ `Path.unlink` 守卫
  （挡 fig6 的绝对路径删除）；脚本 stdout 重定向到 stderr 保护 JSON 协议。
- **paper_style 是图库方言，不是引擎依赖**：worker 的 `import paper_style` 必须留在
  try/except 里，捕获靠通用的 `_patched_savefig` 兜底。曾经这行是硬 import，
  任何不带 paper_style.py 的图库（论文的 supporting_information、外部用户的图库）
  都以 ModuleNotFoundError 开局，一张图都渲染不了（test_build_without_paper_style 看护）。
  它也是**用户代码**：import 排在 `sys.argv` 换好之后、且在与脚本同一道 `SystemExit`
  保护里（评审 #443 第七轮）——paper_style 里 `argparse` 缺参数的 `sys.exit(2)` 与脚本
  自己要参数同一个答案 `script_needs_arguments`，会话还活着；以前它在保护之外、argv 之前，
  worker 随之退出、上层报 session_dead（`test_paper_style_sees_the_scripts_own_argv` /
  `test_an_exit_raised_while_importing_paper_style_is_the_scripts_own` 看护）。
- **未使用的缺失 import 给占位（ADR 0061 §二 2026-09-24 修订）**：worker 在 `sys.argv` 换好之后、脚本开跑之前按
  `figcapture.unused_imports`（与联合计划同一份判据）装 `install_unused_import_placeholders`：只有脚本自己的
  `import X as Y`（X 在无副作用名单里）、真缺的正是 X 本身、且 `importlib.util.find_spec(X) is None`（找得到的同名模块自己抛同名的错不算缺，评审 #555 P2）时才回占位；占位不进 `sys.modules`、读属性抛逐字相同的 `No module named 'X'`；
  装了就不卸（脚本的函数在渲染期仍可能 import）。native 会话不装——那是用户自己的 `python script.py`
  （`tests/test_unused_missing_import.py` 看护）。
- worker 里 **`sys.argv` 必须换成脚本自己的**。不换的话按参数命名输出的脚本
  会拿到 worker 的 `--script/--out-dir/--entry`，存出一堆叫 `--entry` 的图
  （试运行探测时当场撞见过，`test_script_sees_its_own_argv_not_the_workers` 看护）。

## 速查表原要点（2026-09-25 迁入，#608）

`src/tavotto/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 捕获策略两条入口同一份实现
- fallback stem 按本次捕获序号
- 相对路径只读回退四条同时成立才改指（`builtins.open` / `io.open` / 3.10 的 `Path.open` / numpy `DataSource.open` 四处）、不扩到 `exists` / `glob`
- `safe_spec()` / `worker_argv()` 唯一出处
- `cwd_mode` 三档（`project` 仍是脚本目录，`project_root` 是项目根）
- 首开由 `workdir.resolve_mode` 按 `databinding` 静态证据决定问不问，问就 `workdir_confirmation_required`，不猜不就近不自动切
- `import paper_style` 留在 try 里、排在 argv 换好之后且在 SystemExit 保护内
- `sys.argv` 换成脚本自己的
