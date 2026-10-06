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
    写 / 改 / 删 / 重命名一个字节都不经过它。沙盒**之外**的绝对路径回退一个都不碰
    （用户亲手指认的只读改指表是另一回事，见下一条「数据改指」）。
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
    **首开就把用户带到这个开关前（ADR 0084，2026-09-26 用户实报 `glob.glob('run-*…')`）**：
    `databinding.probe_literals` 把 `glob` / `iglob` / `Path(常量).glob|rglob|iterdir|exists…` /
    `listdir` / `scandir` / `walk` / `exists` / `isfile` / `isdir` / `stat` / ovito `import_file` 的
    常量相对目标认成**探路证据**；只在脚本目录找得到 → 结论 `script_parent` → 首开问
    （`script_dir_evidence`，推荐 `project`），沙盒那档的 `found` 不列探路目标。列 cwd 本身
    （`listdir()`）只记脚本目录那档。用户选「继续沙盒」后盲区如实保留，走上面的零张图路径。
    结论只看**找得到**（`found`）：glob 预算用完的 `unjudged` 只记在候选里、**不驱动结论、不会问**。
    别名按 import 认（`import glob as g` / `from glob import glob as gg` / `Path as P`）；名字在别处
    被重新绑定过（参数、赋值、`for` …）时不做作用域解析，按模块 glob 与 `Path.glob` 的并集匹配
    （`**` 递归、含隐藏名）——宁可多问一次。
    一个字都没打印是**另一个 code** `no_figures_captured_silent`（占位是界面
    文案，不塞进 traceback 区）。日志尾部按**这一代的偏移**读（`_log_offset`，
    两条控制面都在启动前记；目录跨代复用、append 模式，不记的话读到的是上一代
    的尾巴）、按字节读再 **UTF-8** 解码（cp936 的 Windows 上 `read_text()` 会
    把中文与 `µ` 读成乱码）。
  * **数据改指（ADR 0106，2026-09-28）**：脚本要读的数据不在它写的位置（脚本被单独复制出来、
    数据被挪走、换了电脑）时，界面请用户**亲手指认**那个文件或它所在的文件夹，按项目记一张只读
    改指表（本机项目设置 `input_remap`，唯一出处 `engine/inputremap.py` 的 `rules_for`）。三件事：
    ① **认出来**：worker 在四个打开入口外面再包一层（`figcapture.install_input_remap`，装在观察器外、
    只读回退内），只读打开落空时记下脚本写的那串（`InputMisses`）；build 失败且异常链里有「文件不存在」、
    又对得上一次落空的只读打开时报 **`missing_input`**（`figcapture.missing_input_of`，写模式的「目录
    不存在」与对不上的一律仍是 `script_error`）；pool 两条控制面与试运行给错误挂上弹窗载荷
    （`inputremap.payload_for`：缺的那串 + 脚本里其余此刻哪儿都找不到的路径，含常量绝对路径——只
    `exists`，不读不列；「其余」只收**真进了读取调用**的常量——`READ_FUNCS` / `read_*` 的路径实参整条是
    常量或只赋值一次的名字、拼路径打头的那一段、读模式的打开，外加探路调用问的；标签、写出目标、输出目录、`.py` 不算）；「没出图」（`no_figures_captured*` / 试运行的 `script_no_figure`）只挂静态那部分。
    ② **推规则**：`inputremap.derive` 按路径段求最长公共后缀，推出 `prefix`（相对 `""` = 所有相对路径）
    或 `file`（改了名只改这一个）；推完自检落到存在的文件上。**不搜同名、不预选**（FO08）。
    ③ **改道**：只在**原路径打开抛 `FileNotFoundError` 之后**查表（原路径存在永远读原件、成功的打开零
    开销），只读、目标是文件才改；判据 `figcapture.remap_target` 父进程与 worker 共用一份。改指表进
    `ExecutionSpec.input_remap`（本机路径，**不进** `stable_payload`），`worker_argv` 只在非空时多
    `--input-remap <json>`，三条 spawn 路径都从 `inputremap.rules_for` 取。**探路调用（exists / glob /
    listdir）与 C++ 读取器救不回**——载荷里 `via` 标 `probe` / `glob` / `native`，对话框不给选择器；
    绝对路径常量的 `via` 同样按它所在的调用判（被 `exists()` 问的给了选择器就是「指认 → 重跑 → 又弹」
    的死循环）。先判空再 `sys.exit` 的（`script_exited`）与「没出图」一样挂静态载荷。**C++ 读取器**
    （ADR 0110 §一）：`missing_input_of` 说不出时 worker 给 `script_error` 多带 `extra.enoent`
    （`figcapture.enoent_fact`：`filename`，或 h5py 那种只在消息里的 `named`，加 cwd），父进程
    `inputremap.native_miss` 拿静态证据对——整串相等或按路径段是前缀、只认对上的那条，对不上就不判、
    码一字不变；对上了码仍是 `script_error`，只挂 `via = native` 的载荷。这几档的出口是经确认改写脚本
    （`script-edits.md`）。native 不改指。④ **代次 + 按项目的一把互斥锁**（ADR 0106 §五，`inputremap.project_mutex`，可重入）：改指表按项目一个代次（跨重启单调）。
    同一把锁里：改表与换代、`state()` / `snapshot()` 的「表 + 代次」、注册表整段读改写（`discover.register`）与登记标记、
    所有落地提交（渲染回包的核对、runtime 物化、试运行登记与重新登记、写回的备份 + 整个 replace 循环、导出作业
    经 `exportjob.run(commit_guard=)` 的整个发布循环）。对不上报 `input_remap_changed`（409 可重试）。**锁序：池锁 →
    项目锁**，持项目锁时不取池锁、不起会话、不跑脚本。前端作废按代次幂等（`envStore.onInputRemapChanged(generation)`）：
    接口响应、事件流、重连 / 页面恢复补拉三条路都调，同一代只执行一次。试运行登记的 stems 在本机项目设置里记指纹
    （无标记 + 有规则 = 过期），下一次按新表 build 后按真实产出重新登记（一张没出也算）。落在 cwd 里的绝对路径
    证得出是相对路径规范化来的才按相对处理（`figcapture.path_literals`），否则保留绝对身份。新增依赖映射的工作点先接到这里，清单与各自的持锁区间在 ADR 0106 §五。
    看护 `tests/test_missing_input.py`（真 worker 相对 / 绝对 / 脚本目录模式 / 原件回来 /
    同名诱饵 / 试运行 / 三条 spawn 路径）、`web/src/components/MissingInputDialog.test.tsx`、
    `web/e2e/missing-input.spec.ts`。
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
    `PATCH /api/engine/workdir`，改了就 `shutdown_all(root)`。**没决定过时的默认档**是 `workdir.default_mode()`：
    项目用的是用户自己的 Python（`pool.user_interpreter_in_effect`：项目级决策指向项目外、不归 Tavotto 管的解释器，
    且无全局显式选择）→ `project`（脚本目录，ADR 0107 §二），其余 → `sandbox`。派生、不写设置；`grant_for` 此时回
    `granted=True, granted_at=None, implied_by=user_interpreter`；首开证据只指向脚本目录时不再问。
  * **首开的一次确认（U03，ADR 0057 §三）**：`workdir` 键不存在 = 没决定过。起第一个 worker
    之前 `pool._new_worker()` 调 `workdir.resolve_mode(root, script)`：决定过就用记住的；没决定过
    按 `engine/databinding.py` 的静态证据——脚本里的相对数据路径字面量只在项目根找得到
    （`project_root`）、脚本目录与项目根各有一份同名而内容不同（`ambiguous`）、或探路调用
    （glob / listdir / exists）只在脚本目录找得到（`script_parent`，ADR 0084）——才抛
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
- **手动色条的宿主记录（#792 / ADR 0102）**：safe worker、native bridge 与浏览器 playground
  都在脚本执行前调用 `figcapture.install_colorbar_capture`。它幂等地包装 `FigureBase.colorbar`，
  原样委托 matplotlib，只在手动 `cax=` 带显式 `ax` 时记录标准 Axes / list / tuple / ndarray，
  不消费被原调用忽略的迭代器，不写 `_colorbar_info`。结构、随行、宿主数统一读 `declared_parents`；
  颜色关系不能补布局声明。看护 `tests/test_colorbar_capture.py`、`tests/test_browser_session.py`、
  `tests/bridge/test_bridge_e2e.py`。
- **live-figure 会话**：worker 跑一次脚本（拦截 `Figure.savefig` + `paper_style.save`，
  不写真实文件），Figure 常驻内存；override 直接 mutate artist 再导出带 gid 的
  SVG（dpi≈120 预览）——冷启动秒到分钟级，热态 ~40ms。
  * **逐次保存的来源观察（ADR 0113）**：三条入口共用 `figcapture.SavefigObservations`，
    在旧 stem 去重之前记录每次路径保存的执行内序号、Figure 序号、相对目的地与调用时的
    参数 / 尺寸 / DPI。它不是 Figure 快照，不参与图幅选择；`savefig_calls` 与旧编辑语义不变。
    每次执行最多 128 条、每条 8192 字节；漏记即 `complete=false`，不许把最后留下的记录
    当成最后保存。沙盒相对路径不冒充项目路径，根外 / 自定义 PathLike 不猜、不发绝对路径。
    native 原调用结果分 saved / failed，safe 分 intercepted，stream 不算路径产物。
    看护 `tests/test_savefig_observations.py`、`tests/bridge/test_bridge_savefig_observations.py`。
  * **savefig 的参数记进捕获描述符（`savefig_calls`，PR #675）**：以前拦截只取
    stem，`bbox_inches` / `pad_inches` / `dpi` / `transparent` 一个都没记。后果（审计
    T14 / T33 实测，教程 Fig1_kinetics）：脚本 `savefig(bbox_inches="tight", pad_inches=0.02)`
    的磁盘原件是 tight 的，live 图的框却是 figsize，紧贴图幅的 x 轴标题在预览与导出里被切掉半截。
    三条入口（safe worker 的 `_patched_savefig`、native bridge 的透传钩子、浏览器 playground）
    都经 `figcapture.savefig_call()` 记下每次调用的**实效值**（显式参数 > 调用那一刻的
    `rcParams["savefig.*"]`），记账规则唯一出处 `figcapture.record_savefig_call()`：只记认领
    这个 stem 的那张图的调用、最多 `MAX_SAVEFIG_CALLS` 次（之后新出现的格式各记第一次，总数
    不超过 `MAX_SAVEFIG_CALLS_HARD`：图幅按「与原件同格式的第一次」挑）。字段三档，「不知道」独立一档：
    `None` = 没观察到、`[]` = pyplot 捕获从没存过盘、非空 = 调用列表。它不进 fingerprint。
    实效值跟着 matplotlib 自己的 `setdefault` 走：`transparent` 时没给的底色是 `"none"`。native
    里先在 `show()` 屏障按 pyplot 兜底捕获、之后又被按同一个 stem 存盘的图，来源升级为 savefig
    （钩子与会话同步各一处），调用照记，不再报「从没存过盘」。
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
    脚本这几处都回到原来那一行，逐字节不变。算不出来的原因挂在 Figure 上、manifest 报
    `frame_unavailable`（与「本来就不裁」分得开）。native 里屏障之后才第一次存盘的图，在下一个
    屏障 `rebase()` 重放之前补上图幅。看护 `tests/test_savefig_frame.py`。
  * **单次被拦截的持久 tight 布局**：`pathgeom.stabilize_captured_tight_layouts`
    在 safe worker / browser 的脚本结束后、图幅与缩略图的第一次绘制前，记下
    subplot 参数。标准 `TightLayoutEngine` 的每次执行都从这份种子开始，再委派给
    **原引擎实例**；不删除 manifest 准备绘制，也不关闭后续自动布局。第一次布局
    会改变自动刻度密度，拿它的结果再做一轮会缩短刻度文字、重算边距，因而「未编辑」
    也挪图。字号、文字、locator、图幅大小仍取当前值；外部明确改了 subplot 参数，
    以区别于上一次引擎结果的新参数更新种子；用户的 axes 位置 pin 仍最后落回。
    **范围窄且明示**：只处理同一 Figure 恰好一次已观察、被拦截的 savefig；多次保存、
    多 stem、文件对象透传、native、手动摆位或不能用 subplot 参数表达的轴、未知/自定义
    布局引擎都保持旧行为。排不下时还原临时种子，不覆盖原引擎本会保留的落位。它消除的是多余
    布局轮次，不承诺旧磁盘图与当前脚本、字体环境或 PDF/PNG/SVG 渲染器逐像素相同。
    看护 `tests/test_initial_tight_layout.py`。
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

## 运行参数（T03，ADR 0014 §2 修订）

用户手动给的**精确 argv** 贯通 探测 → 热编辑 → 冷重放 → 导出 → 重开；自动 argparse 表单是 T07，不在这里。

- **载荷**：`safe_spec(argv=, run_config=)` 是唯一构造入口（缺省空 = 旧行为逐字节不变）；调用方一律 `**execspec.run_kwargs(run)`，`RunSelection` 只由 `runconfig.selection*` 产生（T12，`test_execspec.py::TestSingleAssembly` AST 看护）。`worker_argv` 只在 argv 非空时
  多两个 flag：`--script-argv-json <JSON 字符串数组，ASCII 转义>` 与 `--run-config <rc_…>`；token 不直接摊在命令行上
  （空串 / `-` 开头 / `--` / 中文会被 worker 自己的 argparse 或 Windows 命令行重组弄坏）。worker 在 `sys.argv` 处设
  `[script, *argv]`——仍在 `paper_style` / `runpy` / 导入期 `parse_args()` 之前；载荷坏了 worker **拒绝启动**（不回落空 argv）。
  `MAX_ARGV_TOKENS` / `MAX_ARGV_CHARS` 超限和含 NUL 在边界上拒绝，不截断。
- **身份**：池键 = `(项目, 脚本[, selected-artifact][, run:<RunSelection.key>])`，key 是带**进程随机密钥**的 HMAC（不是裸 hash，
  不出进程）；`invalidate(script)` 按 `k[:2]` 前缀作废全部变体，`invalidate(script, run=…)` / `only_run=True` 只动那一份。
  资产 id 末尾拼 `~rc_…`（`figcapture.runtime_asset_id(script, stem, run_config)`，空串时与旧 id 相同），描述符多一个可选
  `run_config`（空时 payload 里**没有**这个键）。同脚本同 stem 不同 argv = 两张素材、两条热会话、两份 cache。
- **公开投影不带参数值**：`ExecutionSpec.stable_payload()` / `launch_context()` / `PreparationPlan.to_payload()` / 会话
  `public_target()` 与 `impact` 只有 `argv_count` 与 `run_config`；`script_needs_arguments` 的 params 只有 `argv_count` 与
  （有 argparse 实际证据时）`parse_kind`（`missing_required` / `invalid_value` / `unknown`），普通 `sys.exit(2)` 不猜。
- **登记**（`engine/runconfig.py`）：本机 Tavotto 数据目录 `runconfigs/<项目摘要>.json`，不写用户项目（只读项目照样能用）。
  同 (脚本, argv, 敏感标记) 复用引用，改任一 token = 新引用（"编辑 = 新修订"，在途 spec 与旧产物不变）。`sensitive=True` 只活在进程内存，
  文件里只有占位；格式版本高于本读者 → `run_config_unsupported`。错误码：`invalid_argv`(400) / `run_config_missing` /
  `run_config_secret_missing` / `run_config_unsupported`(409)。
- **谁读哪份配置**：`runtime:` 素材读资产 id 里冻结的引用（`runtimeasset.run_selection`）；热会话 / 写回重放读 `worker.run`
  （`pool.one_shot(run=)`）；磁盘面板（用户脚本自己写出的 `fig.pdf`）没有 Tavotto 的执行产物可绑，读脚本最近一次**明确运行**的配置
  （`runconfig.set_default`，无参数运行会清掉它）。**不存在**"项目最新配置"这个读取点。
- **注册表**：带配置的执行并进脚本的 stems（`discover.register(append=True)`），无参数执行仍是整条替换（权威）。
- **未覆盖**（显式）：MCP / CLI 没有 argv 入口（T10），仍总是无参数；workerd 的 Rust 控制面对 argv 不透明且 spec 哈希含 argv，
  本机无产物，行为未执行；运行时才产生的答案（getpass / 动态 input）的冻结转录是 T08。
- 看护：`tests/test_run_argv.py`（模型 + 真 worker 对拍）、`tests/test_run_argv_e2e.py`（HTTP 全链路）。

## 参数表单的静态 schema（T07）

`engine/scriptargs.py` 只读源码（`ast.parse`，不 import / eval / `literal_eval`、不跑 parser、不调 `--help`、不打开 `FileType` 的文件），
回答「这个脚本的 argparse 字面量声明了哪些参数」。权威仍是 token 列表：schema 只是表单建议与校验提示。只认可证明的字面量；
循环 / `**kwargs` / 自定义 Action / 子命令 / `parents` / `parse_args([...])` / `parse_known_args` / 多 parser 等一律 `partial` + 闭集理由
（`REASONS`；`FORM_BLOCKING` 里的让表单整体只读）。`required` 只来自显式 `required=True` 或位置参数规则，`default` 只展示、不注入；
角色只来自 `FileType` 的模式（名字 `x` / `time` / `range` 不定角色与单位）。表单 ↔ token 的转换在前端 `lib/scriptArgsForm.ts`，
与这里共用 `tests/golden/script_args_form_vectors.json`（后端跑真 argparse 对拍 Namespace，前端对拍编辑结果；重生成
`scripts/dev/gen_script_args_vectors.py`）。端点 `GET /api/engine/script-arguments?script=`（与试运行同一道路径检查，只读）。
看护：`tests/test_script_args.py`。

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

保存观察的 size/DPI 只经脚本运行前捕获的标准类型/描述符和原始数字存储；不得额外调用
用户 Figure getter/数值转换。自定义或无法确定的元数据为 null、账本 incomplete，仍保留
occurrence/figure ordinal/result。看护：`tests/bridge/test_bridge_savefig_observations.py` 的
真实 subclass/实例方法/类属性 getter 次数、PNG 字节与异常行为对拍。

## 选择具体产物的受限源图上下文

`source_policy=selected-artifact-v1` 是显式选择，不改变省略它的旧请求，也不迁移存量布局。
控制面从安全解析后的 PDF/PNG 文件生成 `SourceArtifact` 字节身份；有 edits 的请求必须带
`expected_source`（原始 hash/字节数），变了回 409 `artifact_source_unavailable`，不能把旧坐标
静默绑到新文件。首次无编辑采用会返回实际身份。对象身份仍遵守 ADR 0083 的既有边界。

- 只支持 safe 档、固定/已完成一次性布局的标准 rectilinear line/bar/text 图。
  活跃布局、脚本留下的 Python 线程、额外 bbox artists、自定义渲染钩子、locale/TeX/
  外部字体、其它 artist 家族拒绝；不序列化 Figure，不重放保存历史。
- 完整保存账本里必须有唯一的完整路径匹配，且属于已捕获 Figure。执行目录相对路径
  只是候选，不能按 stem/basename 猜。拼写不同只在解析后仍位于项目内、且文件系统确认
  是同一个文件时视为别名；不按平台或统一小写猜测卷的大小写语义。重复覆盖、未知目的地/格式/backend 拒绝。
- 先按该次保存的 DPI/crop 定图幅，再由**初始化后、任何 patch 之前**的源格式候选验证
  实际文件的像素/图幅；不另画 pre-instrument 探针。PDF 经父进程 pdfbackend，科学 worker
  不新增 PDF 库。32 MiB、1600 万像素、单页 PDF 与既有 worker/renderer 超时是硬界。
  PNG pHYs 只容许一个整数像素/米量化步，色彩管理块拒绝，不拿宽松阈值掩盖差异。
- 所选保存的背景在采集 FigState originals 前落到该 worker 私有 Figure/axes 的既有属性：
  默认透明背景用 Figure patch 可见性与 axes 的 none 填充/边线，保留 Figure 潜在颜色供切回不透明。
  显式 facecolor/edgecolor 按保存记录应用，auto 保留当前属性；随后仅此 selected worker 把
  savefig 的透明、底色、边色、bbox 默认值归一为 False/auto/auto/None，避免后续 rc 覆盖编辑或重裁图幅。
  witness、manifest、所有预览/导出共用这个可编辑基线；native 与无 source context 的旧路径不变。
  若这一步把脚本里可见的 Figure 背景隐藏，而保存的 edits 只有 facecolor、没有明确布尔 transparent，
  不能猜旧版本的可见性意图：render/preview/export 在应用前以 background_visibility_required 拒绝。
  保留所有 edits，由既有控件让用户明确选择透明或显示底色；导出错误携带请求源身份，只撤销对应变体。
- 复用现有 FigState 与全量 overrides 语义；worker/输出目录按源上下文隔离，仍受池的
  3 个热 worker / 1 GiB 缓存治理。不是每个面板永久保存一个图；同一源的变体独立传完整 edits。
  新上下文会额外执行脚本一次。首次准入按 worker 锁串行；workerd 重启不能绕过重新准入。
  失败退役只针对那一条 worker 实例；选择子集不能重写整份脚本登记。
- 当前 API 切片只开放 render、带 manifest 的 paired preview、导出新文件；selected GET
  SVG/PNG、binary preview、specfix/sync、写回及历史恢复明确拒绝，不能退回旧坐标。
  空 overrides 原图导出仍保留磁盘像素；正常导出的字体/PPI政策独立于源图上下文。
- 这不是任意用户脚本的无写入沙盒：现有通用 open/原生扩展边界不扩张；新验证/导出路径
  不写原图，并在发布前复核字节。活跃布局/历史场景、产物级 baked/writeback 与非完整 PNG 的采用仍是后续门。

看护：`tests/test_selected_artifact_worker.py`、`tests/test_selected_artifact_api.py`、
`tests/test_artifact_context.py`。

`selected-figsize-v1` 是上述机制的窄子集：只接 PNG、已解析的 bbox_inches=None，非空编辑
必须保留有效 `figure.frame="figsize"`。后端直接检查当前 baked 基线，非空即拒绝；不能依赖
首选 PDF 可能隐藏 PNG 的素材清单，也不扫描更早的历史版本。它复用同一准入、预算与上下文键，供 ADR 0098 的
旧坐标兼容采用。`/api/export/validate` 在已验证的选中源请求上附 `artifact_sources`；
它只确认策略和磁盘字节、不启动 worker，也不宣称场景已经准入。看护：
`tests/test_selected_figsize_policy.py`、`tests/test_selected_artifact_api.py`。
