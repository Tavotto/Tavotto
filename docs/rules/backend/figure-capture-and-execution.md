# Figure 捕获、执行描述与 live-figure 会话

> 原文出自 `src/tavotto/AGENTS.md`「渲染引擎核心机制」（2026-09-17 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 看护用例（`src/tavotto/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`tests/test_compat_capture_parity.py`、`test_missing_input.py`、`test_run_argv.py`、`test_run_argv_e2e.py`、`test_script_args.py`、`test_execspec.py`、`test_workdir_mode.py`、`test_databinding.py`；其余看护用例见细则
> `src/tavotto/AGENTS.md`「必守要点」压缩前原文（2026-10-23，与正文同等有效）：捕获与显式 cax 宿主三条入口同源；selected 队列替换不退役共享 worker；`safe_spec()` / `worker_argv()` 唯一出处；argv 非空只经 `safe_spec(argv,run_config)`、缺引用显式拒绝（T03）；argparse 表单 schema 只读源码、是建议不是门槛（T07）；首开问不问由 `workdir.resolve_mode` 定；数据改指见 ADR 0106
> `src/tavotto/AGENTS.md`「必守要点」压缩前原文（2026-10-24，与正文同等有效）：捕获三条入口同源；`safe_spec()` / `worker_argv()` 唯一出处、argv 只经 `safe_spec`；表单 schema 是建议不是门槛；数据改指见 ADR 0106

<!-- agents-route
sheet: src/tavotto/AGENTS.md
order: 50
改到: `engine/figcapture.py`、`artifactcontext.py`、`execspec.py`、`runconfig.py`、`scriptargs.py`、`workdir.py`、`databinding.py`、`inputremap.py`、`worker.py` 的 savefig 拦截与 sys.argv
主题: Figure 捕获、执行描述与 live-figure 会话 → `figure-capture-and-execution.md`
必守要点: 捕获三条入口同源；`safe_spec()` / `worker_argv()` 唯一出处、argv 只经 `safe_spec`；表单 schema 是建议不是门槛
-->

- **Figure 捕获策略是共享语义（`engine/figcapture.py`，2026-08-21）**：
  桌面 worker 与浏览器 playground **各调一次同一份实现**。三件事只有这一个
  出处：`savefig` 的 stem 怎么取、脚本跑完还活着的 pyplot Figure 怎么补进来
  （去重按 Figure 身份、上限 `MAX_PYPLOT_FALLBACK=8`）、相对路径只读回退。
  * **没有 savefig 的脚本也要捕获**（`plt.plot(...); plt.show()` 是 AI 最常见
    的输出形态）。以前只有 browser.py 有兜底，桌面一张都捕获不到——同一份
    脚本两个入口两个答案，是数据级的分叉。
  * **`plt.show()` 时开着的图，之后被 `plt.close()` 也要捕获（G1，safe worker）**：
    兜底只看跑完仍活着的图，`plt.show(); plt.close('all')` / 循环里逐张 show+close 的脚本以前
    ok 却 0 张。worker 在脚本 import pyplot 的那一刻包 `plt.show`（先记账、再原样调用，不改行为、
    不碰活动图），账是 `figcapture.ShownFigures`（有界：强引用 ≤ 上限、其余只计数；弱引用判重）；
    `collect_pyplot_figures(..., shown=)` 把 show 过的排在跑完仍活着的之前，**同一套** stem 命名 /
    来源 `pyplot` / 8 张上限与提示，savefig 认领或仍开着的图不重复。没 show 就 close 的图仍不捕获
    （交互用户也没见过）。**账每次 build 开始时重建**（`_install_show_capture`；`build_failed` 的会话会被复用、重跑脚本，
    上一次留下的已关闭图不许带进这一次；show 钩子每次读模块全局 `_SHOWN`，不抓闭包里的旧账）。**8 张上限只限 pyplot
    兜底**：show 时已被 savefig 认领的图（`figcapture.claimed_ids`，与 `collect_pyplot_figures` 去重同一判据）不占名额，
    之后才认领的在下一次 show 腾出名额；溢出且已关闭的图一旦丢了强引用就补不回（只计数），故「show 之后才 savefig 一张
    已溢出的已关闭图」不在保证内。native bridge 在 show 屏障本来就收，浏览器 playground 不传 `shown`。
  * **写进 `PdfPages` 的图也要捕获（#1370 PR1，三条入口同源）**：`pdf.savefig(fig)` 最终是
    `fig.savefig(<PdfPages>, format="pdf")`，第一个参数不是路径，stem 取不出来；逐页 `plt.close(fig)` /
    结尾 `plt.close("all")` 的脚本跑完一张不剩（以前 `script_no_figure`）。保存**成功之后**记进有界账本
    `figcapture.PdfPagesLedger`（safe worker 的 `_PDF_PAGES`、浏览器 `BrowserSession.pdf_pages`、native 的
    `bridge_runner._PDF_PAGES`，每次 build 重建），脚本结束时 `figcapture.adopt_pdf_pages` **先于** pyplot 兜底认领
    （native 在 show 屏障与脚本结束各调一次，且在「pyplot 已载入」判断之外——纯 OO 脚本也能用 PdfPages）。
    stem 取 PDF 文件名（`all_samples.pdf` → `all_samples`、`-2`、`-3` 按页序；同名第二份 PDF 接着编号；缓冲区 / 取不到文件名
    退回脚本名）；文件名读 PdfPages 的私有 `_filename`（matplotlib 3.8–3.11 都在，不包 `__init__`，不主动 import；
    判型用 `sys.modules.get("matplotlib.backends.backend_pdf")` 取类 isinstance）。同一张 Figure 只认领一次（写多页也一个 stem，
    取脚本结束时的状态）；强引用上限 `MAX_PDF_PAGES_LEDGER`（64，与 pyplot 兜底的 8 分开），超出只计数并在
    `pdf_pages[].omitted` 里说明。**来源恒为 `pyplot`——多页 PDF 不是任何一张图的原件**：`find_original_artifact` / 写回不得指向它；
    「把编辑导回多页 PDF」不做（#1370 PR2：写回一律拒绝，见下条）。build 响应 / 探测结果加字段
    `pdf_pages: [{name, pages, captured, stems, path, scope, page_stems, complete, omitted?}]`（没写过 PdfPages 时不出现；
    每份写过页的 PDF 一项，包括它的图都已被 savefig 认领的），前端 `lib/pdfPagesNotice.ts` 在通知轨出一条无按钮胶囊
    （只提 `captured > 0` 或有省略的）。stem 取法变了，`DESCRIPTOR_VERSION` 升到 2
    （旧指纹按设计失配一次）。浏览器同样传账（总数仍受 `MAX_FIGURES` 截断、如实进 `truncated_figures`），不像 `shown` 那样不传。
    看护 `tests/test_capture_pdfpages.py`、`tests/bridge/test_bridge_pdfpages.py`。
  * **PdfPages 的每一页记成那份 PDF 路径的一次 occurrence（#1370 PR2，ADR 0113 补充条款）**：`SavefigObservations.record`
    认得 PdfPages（`pdf_page` 页号、`stem` null、目的地规则与 savefig 同一份 `_destination`、以第一页为准；缓冲区不记；
    页的预算 = `MAX_PDF_PAGES_LEDGER`，与 savefig 的 128 分开，不让 `report()["complete"]` 变假）。三条入口各在原有的记录位置
    接入（worker `_patched_savefig` 的 record 本来就在写之前；浏览器在透传分支里补；native 的钩子本来就记），safe / 浏览器
    补 `intercepted`，native 补 `saved` / `failed`。摘要的 `path` / `scope` 由观察器解出（`unresolved` 时 `path` 为 null，
    根外路径不外发），`page_stems` 按页序，`complete` = 每页都有 stem 且都记上。`saved_originals` / `note_artifact` 不认页记录。
    pool 把 build 响应的 `pdf_pages` 存成 `last_build_pdf_pages`（`EngineWorker` / `WorkerdWorker`），写回守卫读它
    （`writeback-transaction.md`「不写多页 PDF」）。不改描述符、stem 取法与指纹，`DESCRIPTOR_VERSION` 不升。
    看护 `tests/test_pdfpages_record_pages.py`、`tests/bridge/test_bridge_pdfpages_pages.py`。
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
    **探路目标会折常量（2026-10-25，qudit_tomography）**：实参是 `_fold_str` 折得出的字符串表达式也算——字符串常量、
    模块顶层**整棵树里只绑定过一次**的常量名、`a + b`、`os.path.join(…)`、f-string；f-string 的动态片段只在 glob 且只落在
    **文件名那一段**时换成 `*`（`data_dir + f"times_{tag}_d*.txt"` → `./data/times_*_d*.txt`），目录部分动态 / 名字重绑定 /
    `{x!r}` / 列目录与问路径里的动态片段一律不说话。折出来的模式照样只在脚本目录 / 项目根下**找得到**才说话。
    **运行时证据（#1254，ADR 0084 §运行时证据）**：模式是参数、不是写在调用里的常量时（`def read_parts(pattern):
    glob.glob(pattern)`），静态探测认不出。沙盒会话里 worker 多装一层**只记不改**的观察
    （`figcapture.install_probe_observer`，装在改指之后、账本之前，cwd 已是脚本目录 / 项目根时不装）：
    `glob.glob|iglob` / `os.listdir|scandir` / `os.path.exists|isfile|isdir` / `Path.glob|rglob|iterdir|exists|is_file|is_dir`
    在沙盒 cwd 下回空 / 目录不存在 / 为假、目标是相对路径时记 `{kind, target, recursive, hidden}`
    （去重、`MAX_PROBE_MISSES=16`；stdlib 内部的逐段探路不记；`scandir` 的迭代器不包，只记目录不存在；列 cwd 本身不记）。
    **返回值逐项不变**（迭代器换成逐项转发的生成器，元素与异常原样），这一条是硬约束。
    **证据只来自脚本自己的探路**：相对路径只读回退（观察器先装、回退后装）自己问「沙盒里有没有这份」走 `os.stat`（`_present`），
    不经被观察的 `os.path.exists`——否则每个被回退救回来的读都会记成落空，读到了数据、内容不对才失败的脚本也会被问「在哪个目录运行」。build 失败（`script_error`）、
    没出图（`no_figures_captured*`）、先判空再 exit（`script_exited`）时，记录随错误 `extra.probe_misses` 到父进程
    （build 响应在有记录时也带 `probe_misses`），`workdir.probe_hint` 拿**首开同一把尺子**（`databinding._probe_hit`，项目外不看、
    glob 有预算）按脚本目录再判，至少一条找得到才在错误上挂 **`workdir_hint`**——载荷与首开确认框同形
    （`reason = script_dir_evidence`、推荐 `project`，多 `source = "runtime"`）。**它不是 `confirmation`**：后者是起会话前要先答的门，
    所有入口见到都会停下；`workdir_hint` 只诊断，码与消息一字不变，不自动切换。HTTP 错误体与试运行错误（`_attach_workdir_hint`）
    各带一份，不新造错误码。**C++ 读取器也算运行时证据**：错误的 `extra.enoent`（`filename`，或 pyarrow 那种整条消息就是路径的 `named`）
若是相对路径（cwd 之内的绝对路径换回相对），`pool.workdir_hint_for` 把它当一条 `path` 证据并进同一次判定——脚本目录里找得到就出引导；
**不放宽 ADR 0110 的 `native_miss` 候选**，码与 `missing_input` 不动。前端（`ApiError.workdirHint` / `PanelRender.workdirHint` /
素材库 `run.error.workdir_hint`）不论错误码都把它交给既有的 `WorkdirChooseButton`（同一个确认框，不新造组件和文案）。
**build 成功但图是空的**（`ax.plot(glob(...))` 回空，一条空线、不报错——静默的错误结果）：build 响应的 `probe_misses` 存在
`worker.last_build_probe_misses`，渲染成功响应（`/api/engine/render`）与试运行成功结果各挂同形 `workdir_hint`（`pool.workdir_hint_of_build`）；
**成功路径只认 `glob`**（`exists("out")` / `isdir("cache")` 这类先判再建的常规写法在沙盒里为假、脚本目录里却有同名目录，成功的图不该被提示），
出图结果、状态码不变。前端走通知轨（`lib/workdirHintNotice`：胶囊、同面板同证据只提醒一次、`decided` 后不再提醒），不是横条。
看护 `tests/test_runtime_probe_hint.py`、`web/src/components/workdirHint.test.tsx`、`web/src/store/workdirHintSuccess.test.tsx`（观察层返回值逐项不变 / 只记沙盒里的相对落空 / 判据 /
    真 worker 沙盒带引导、脚本目录模式出图、两种试运行形状）。
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
    （`figcapture.enoent_fact`：`filename`，或 h5py 那种只在消息里的 `named`——pyarrow 的
    `FileNotFoundError("cg_data.parquet")` 整条消息就是路径，同样取——加 cwd），父进程
    `inputremap.native_miss` 拿静态证据对——整串相等或按路径段是前缀、只认对上的那条，对不上就不判、
    码一字不变；对上了码仍是 `script_error`，只挂 `via = native` 的载荷。这几档的出口是经确认改写脚本
    （`script-edits.md`）。native 不改指。
    **对账只认和这次异常对得上的落空（#1253）**：异常没有 `filename`、消息里也没有引号括着的路径时，只认
    消息里按路径段点了名的那次落空（`figcapture._named_miss`），**不退到「最近一次落空的」**——被 `try` 吞掉的可选读、
    `importlib.metadata` 对不存在的 `sys.path` 条目（Homebrew 的 `python313.zip`）试 zipfile 打开，都会排在后面，
    以前被指认成「缺的输入」。`sys.path` 条目本身不记账（`_is_sys_path_entry`，也不占 `MAX_INPUT_MISSES` 名额）。
    数据在脚本目录里找得到、只是沙盒 cwd 下 C++ 读取器看不见时，静态证据里它不是「哪儿都找不到」的候选：
    不挂 native 载荷，错误仍是 `script_error` + `extra.enoent`（诊断，行为不变）。
    ④ **代次 + 按项目的一把互斥锁**（ADR 0106 §五，`inputremap.project_mutex`，可重入）：改指表按项目一个代次（跨重启单调）。
    同一把锁里：改表与换代、`state()` / `snapshot()` 的「表 + 代次」、注册表整段读改写（`discover.register`）与登记标记、
    所有落地提交（渲染回包的核对、runtime 物化、试运行登记与重新登记、写回的备份 + 整个 replace 循环、导出作业
    经 `exportjob.run(commit_guard=)` 的整个发布循环）。对不上报 `input_remap_changed`（409 可重试）。**锁序：池锁 →
    项目锁**，持项目锁时不取池锁、不起会话、不跑脚本。前端作废按代次幂等（`envStore.onInputRemapChanged(generation)`）：
    接口响应、事件流、重连 / 页面恢复补拉三条路都调，同一代只执行一次。试运行登记的 stems 在本机项目设置里记指纹
    （无标记 + 有规则 = 过期），下一次按新表 build 后按真实产出重新登记（一张没出也算）。落在 cwd 里的绝对路径
    证得出是相对路径规范化来的才按相对处理（`figcapture.path_literals`），否则保留绝对身份。新增依赖映射的工作点先接到这里，清单与各自的持锁区间在 ADR 0106 §五。
    看护 `tests/test_missing_input.py`（真 worker 相对 / 绝对 / 脚本目录模式 / 原件回来 /
    同名诱饵 / 试运行 / 三条 spawn 路径 / #1253 的 C++ 读取器与 sys.path 探测）、`web/src/components/MissingInputDialog.test.tsx`、
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
  （挡 fig6 的绝对路径删除）；脚本 stdout 重定向到 stderr 保护 JSON 协议。守卫拦下的操作之后，
  脚本若又读了受影响的路径（`os.replace` 缓存再读、`savefig('x.png')` 再 `Image.open('x.png')`），
  build 以 `safe_file_operation_blocked` 结束而不是 `ok`（P01a，见 `worker-protocol-and-lifecycle.md`）；
  这不拦 savefig 捕获本身，也不拦没有回读的拦截。
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

## 同一路径多个写入者（Compatibility Guardian G2）

脚本在循环里让不同的 Figure 反复写同一个路径（`fig.savefig("a.pdf"); plt.close(fig)`），原生 Python 下磁盘上留下的是
**最后一次成功写入**的那张；捕获表按 stem 记账，必须与之一致。**唯一实现 `figcapture.note_savefig_writer`**，三条入口同源
（desktop worker 经 `LiveFigureSession.note_savefig`、浏览器钩子、native 的 `_patched_savefig`），也是 `savefig_calls[stem]` /
`savefig_extras[stem]` 的**唯一写入点**（钩子里不再另调 `record_savefig_call`）。`add_figure` / `setdefault` 的先到先得认领不动。

* **成败怎么知道**：不预判（参数组合穷举不完）。native 在 `_REAL_SAVEFIG` **成功返回之后**才调用，失败的保存不进任何表。
  safe worker / 浏览器不真写盘，钩子传 `verify` 闭包（`figcapture.savefig_verify_render`：用未打补丁的 `Figure.savefig` 把同一组
  参数写进 `io.BytesIO()`，格式取显式 `format=` 或后缀）；共享函数：
  * **非持有者**写入（`fig` 不是 `capture[stem]`，`target` / `call` 非 None——顶不顶替都会更新 `files`）先 `verify`，失败整次忽略
    （不顶替、不更新 `files`、不记该写入者的调用）；
  * **持有者自己的写入**照常记账、不立刻验证，但它在 `files` 里的登记带着 `verify` 当惰性验证（`_FileEntry`，弱引用 Figure + args /
    kwargs 浅拷贝；Figure 已死 = 失败）。只有当它要**挡住一次顶替**（「持有者在别的目标上仍是最近写入者」）时才对挡路的登记验证：
    成功 = 确认落盘，标已验证、以后不再验；失败 = 那次保存从未落盘，删掉登记并重算。native 的登记都是已成功保存，不带 thunk。
  常规路径零渲染，只有同 stem 多图才有额外渲染。matplotlib 的
  savefig 自己会还原 dpi / 尺寸 / 布局（`TestVerifyRender::test_figure_state_is_unchanged` 钉着）。
* **每个 stem 一份状态** `writers[stem] = {"files": {目标文件键 -> 最近写它的 Figure 弱引用}, "by": {id(fig) -> [弱引用,
  该写入者自己的调用表, extras]}}`。**文件身份** `figcapture.savefig_target_key`：文件名经 `savefig_effective_names`（显式
  `format=` 不改文件名）；三条入口同一个**路径**键：`realpath`（符号链接解到目标）拆成目录 + 文件名，文件名折叠大小写与否按**该目录所在文件系统**判
  （`_dir_folds_case`：目录存在时翻转路径里最后一个有大小写的那一级名字——父目录 `listdir` 里字面存在该变体 = 独立条目（区分），否则翻转路径 `samefile` = 不区分；探测不了按平台默认 darwin / win32 不区分、其余区分，
  按目录缓存；Pyodide 虚拟 FS 区分）。缓冲区 / 认不出文件名 = `None`。
* **调用按写入者分表**，规则就是 `record_savefig_call` 的（上限、新格式第一次照记、`call is None` 整份「不知道」），与别的写入者
  交替多少次互不影响（交替 100 次，胜者的首次 `bbox_inches="tight"` 仍在）。持有者（`capture[stem]`）的表同步成
  `calls[stem]` / `extras[stem]` 的副本。
* **顶替**：`fig` 不是持有者，且**持有者不是本 stem 任何目标文件的最近成功写入者**（含持有者从未成功写过——先认领的图保存失败、
  第一个成功写入者取代它）→ `capture[stem] = fig`，`calls` / `extras` 换成 `fig` 自己的表（跨格式交替后顶回来的图带着它的全部
  调用，如 `["pdf","png","pdf"]`）。护栏：`target` 或 `call` 为 None 不参与顶替（`paper_style.save` 捷径不知道路径）；
  `locked`（stem 已进入编辑 `states` 或已记失败 `failures`；native 的同义是第一个屏障处被会话接走的 `_CAPTURE_LOCKED`）不换图；
  **pyplot 认领永不被顶**；持有者在别的目标文件上仍是最近写入者时不顶（甲写 `a.pdf` + `a.png`，乙再写 `a.pdf` → 仍是甲，
  磁盘上 `a.png` 还是甲）；不同目标文件互不顶替（`a.pdf` 甲 / `a.svg` 乙 → 甲，TIGHT 用例钉着）；不同 stem 互不影响。
* **有界**：每个 stem 最多 `MAX_SAVEFIG_WRITERS`（16）个写入者表；先淘汰已死的与「不是持有者也不是任何目标最近写入者」的，
  还超就按先来后到淘汰非持有者（连同它名下的目标记录）。持有者永不淘汰；被淘汰者日后重新成为写入者就从新调用开始——这是有界的
  代价。每个写入者的调用表本身由 `record_savefig_call` 的 HARD 上限约束。
* safe worker 的捕获发生在 instrument 之前，规则只在脚本执行期生效；写回重放起新 worker 重跑脚本，同一规则再走一遍。
* **文件身份与成败的真值（#1150）**：native 保存成功才登记（失败的原调用抛出，不进任何表；缺目录即如此），登记前对已登记目标做 `samefile` 对账（`_canonical_target`），硬链接别名并入已登记的键——**不替代路径键**，删掉重建同一路径仍是同一目标，且只在两个键都对应存在的文件时合并。键里只在**能证明**目录把 NFC / NFD 当同一个名字时才归一成 NFC（`_dir_folds_unicode`：拿真实存在的名字——这次要写的、或目录里的非 ASCII 条目——试另一种写法，`samefile` 为真才算；APFS 是，NTFS / ext4 / exFAT / 多数网络卷不是）；证明不了不归一，宁可漏并——误并会编辑到另一张图。safe / 浏览器在没有任何已存在名字可当证据的新目录里因此不归一；native 另有 samefile 对账兜底。持有者的**惰性验证**三值（`savefig_verifier(...).deferred`）：Figure 此刻渲染不出、但同一组参数在空白 Figure 上能渲染 = 未知（保存后可能被改坏），按已保存算，不推翻选择；参数本身造成的失败仍判失败。代价：持有者当时真因内容失败（如 tight 量不出包围盒）又恰好要挡住顶替时，会按未知挡住（回到 G2 之前的先到先得）。外来写入仍当场验证（Figure 此刻就是保存时的内容）。
* 已知未覆盖：`paper_style.save` 捷径不经 savefig 钩子，不参与；**safe / 浏览器的缺目录**——`savefig("missing/x.pdf")` native 抛 FileNotFoundError，safe 的代理渲染进 BytesIO 判成功（脚本的 makedirs 在沙盒里可能被拦或落在别处，磁盘上没有目录不代表原生会失败；d7f5b24b4 的裁决，safe 没有磁盘真值，只能近似）；**文件系统自己的文件名比较规则**（不区分大小写时只做 `lower()` 折叠；NTFS 大写表不模拟）；Windows 上的大小写探测只在 CI 跑，没有真机验过；`MAX_SAVEFIG_WRITERS` 淘汰行为不变（被淘汰者的文件之后算「没人写过」）。
* **被拒顶替、却仍是某个文件最近写入者的图也要捕获**（2026-10-21）：写入者表只握弱引用，乙写 `panel.pdf`
  （持有者甲写 `panel.png`）后被 close 就会被回收、整张丢失。`note_savefig_writer` 末尾 `_keep_displaced` 对这类图
  握强引用（有界，随 `_trim_writers`）；三条入口在 pyplot 兜底**之前**调 `figcapture.adopt_displaced_writers`，乙以
  `fallback_stems` 的规则成为 `panel-2`（来源 savefig，带它自己的调用表），native 在 show 屏障与脚本结束各调一次。
  原件按这张图写文件时的 stem（`LiveFigureSession.savefig_file_stem`）、只在它存过的格式里找
  （`find_original_artifact(formats=…)`），甲 → `panel.png`、乙 → `panel.pdf`；写回见 `writeback-transaction.md`。
  已知未覆盖：项目素材按**文件名 stem** 解析到 stem 同名的图（`registry.for_stem`），打开 `panel.pdf` 仍显示甲——
  注册表没有「哪个文件是哪张图写的」，乙可经运行期素材 `runtime:<脚本>#panel-2` 编辑；写回守卫保证不会因此覆盖乙的文件。
  看护 `tests/test_same_stem_other_figure.py`、`tests/bridge/test_bridge_same_path_last_writer.py`。

看护 `tests/test_capture_saved_facts_identity.py`（硬链接 / NFC-NFD / 保存后改坏 / 缺目录边界）、`tests/test_capture_same_path_last_writer.py`（共享函数 + 会话规则 + 目标键 + 真 worker / 浏览器端到端）、
`tests/bridge/test_bridge_same_path_last_writer.py`（native）。

* **存盘后又改过 → 保存瞬间快照（ADR 0139 §九，safe worker 专属）**：`LiveFigureSession.note_savefig` 在每次保存记完账后给该图拍
  `pickle.dumps(fig)`（`SaveMomentLedger`，只握在本进程内存、每图只留最近一次、单张 32 MiB / 累计 128 MiB，**只按字节预算不按墙钟**）；
  脚本跑完、`note_modified_after_save` 判真、pyplot 兜底之后，`adopt_save_moments` 用受限 Unpickler（只认 matplotlib / mpl_toolkits /
  numpy 本体）还原复制品顶替捕获表里的最终态 Figure。失败一律退回 #1250 的「只告知」，原因进闭集 `SAVE_MOMENT_REASONS`，
  manifest 的 `save_moment` / `save_moment_unavailable` 与 `modified_after_save` 一同出现。**不落盘、不读项目里的 pickle**；
  精确策略 `selected-figsize-v1`、native、浏览器不用快照。环境变量 `TAVOTTO_SAVE_MOMENT_SNAPSHOT=0` 整体关闭。
  看护 `tests/test_save_moment.py`（含反证用例）、`tests/test_modified_after_save.py`、`tests/test_script_backed_policy.py`。

## 运行参数（T03，ADR 0014 §2 修订）

用户手动给的**精确 argv** 贯通 探测 → 热编辑 → 冷重放 → 导出 → 重开；自动 argparse 表单是 T07，不在这里。

- **载荷**：`safe_spec(argv=, run_config=)` 是唯一构造入口（缺省空 = 旧行为逐字节不变）；调用方一律 `**execspec.run_kwargs(run)`，`RunSelection` 只由
  `runconfig.selection*` 产生（T12，`test_execspec.py::TestSingleAssembly` AST 看护）。`worker_argv` 只在 argv 非空时
  多 `--run-config <rc_…>`；**参数值不进 OS 命令行、环境变量或临时文件**，由 `pool._run_payload` 经已有私有 JSON 请求管道
  传入（两条控制面同源）。Windows 的命令行引号膨胀因此不受参数值影响。每条可执行请求带冻结配置，workerd 透明重开后仍能
  用原配置冷重建；worker 校验引用、类型与不可变性，缺失 / 错配就拒绝执行，绝不回落空 argv。
  worker 在 `sys.argv` 处设 `[script, *argv]`，仍在 `paper_style` / `runpy` / 导入期 `parse_args()` 之前。
  `MAX_ARGV_TOKENS` / `MAX_ARGV_CHARS` / `MAX_ARGV_WIRE_CHARS` 仍是 **safe 档**（私有请求管道）的输入预算，超限在边界上拒绝，不截断；native 的 argv 是用户自己的命令行（走 bridge_argv，不进私有管道），不受这三个预算约束，只拒绝含 NUL（操作系统装不下）。
- **敏感配置的诊断**：`RunSelection.sensitive` 随冻结配置传入。该 worker 整个生命周期的 fd 1/2（含 native 与继承标准句柄的
  子进程）进独立解释器的有界丢弃管道（不抢 worker 的 GIL），只在有新输出时最多每秒四次写固定进度标记，保留两条控制面的静默看门狗；
  协议用独立不可继承 fd，辅助进程随 worker 正常关停被回收、异常死亡后自行退出。
  错误的自由文字 / traceback、成功响应里的 warning 文字、输入问答的 prompt / stdout tail 不转录，保留 warning 个数与 code / 参数个数 / 有证据的 parse_kind 等结构性事实。
  敏感会话不自动填入或持久化交互答案；冻结重放以进程密钥派生的 HMAC prompt 身份核对原问题，不能拿同一占位文案当成相同问题。
  敏感运行的缺依赖：worker 只带分类标志 `missing_dependency`；模块名（`missing_module`）仅当 `ModuleNotFoundError.name` 的顶层包出现在入口脚本的静态 `import` / `from … import`（ast 解析，只读、有大小上限、解析失败即不可归因）里、且与任一敏感 token 无 casefold 子串关系时才带——动态 import 可由敏感 argv 变形（`pkg.sub`、`--plugin=pkg`），整串比较认不出。无名字时父进程（Python 池与 workerd 两条路径）仍走 `missing_dependency`（`module` 为空，通用缺依赖提示，不出依赖修复 offer、不记诊断现场）。
  非敏感运行的诊断原样。这里保护的是 Tavotto 自动记录的诊断，**不是**限制用户脚本主动写文件或将参数画进图的安全沙盒。
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
  `run_config_secret_missing` / `run_config_unsupported` / `run_config_unreadable`(409)。登记文件**不存在**才算空；存在却读不出 / 格式坏（含语法合法但内容不符 `put` 写出的结构：version、configs 每条的字段类型、defaults 的键值，整份校验，`runconfig._schema_ok`）= `run_config_unreadable`（不当空、不回落空 argv）；读写都失败关闭：写路径（`put` / `set_default`）遇坏文件同样抛 `run_config_unreadable`，文件字节不动（不隔离、不备份、不从空重建——重建会让别的脚本的默认配置凭空消失），由用户显式修复或删除登记文件（界面入口由 issue #847 补）。登记 id 形状严格：configs 键与 defaults 值须整串匹配 `runconfig.ID_RE`（`rc_` + 12 位小写十六进制，与 `runtimeasset._RUN_CONFIG_TAIL` 同源），`rc_bad` 这类只有前缀的算 unreadable。
- **谁读哪份配置**：`runtime:` 素材读资产 id 里冻结的引用（`runtimeasset.run_selection`）；热会话 / 写回重放读 `worker.run`
  （`pool.one_shot(run=)`）；磁盘面板（用户脚本自己写出的 `fig.pdf`）没有 Tavotto 的执行产物可绑，读脚本最近一次**明确运行**的配置
  （`runconfig.set_default`，无参数运行会清掉它）。**不存在**"项目最新配置"这个读取点。
- **注册表**：带配置的执行并进脚本的 stems（`discover.register(append=True)`），无参数执行仍是整条替换（权威）。
- **未覆盖**（显式）：MCP / CLI 没有 argv 入口（T10），仍总是无参数；workerd 的 Rust 控制面透传私有载荷，spawn 哈希只含配置引用，
  本机无产物，行为未执行；运行时才产生的答案（getpass / 动态 input）的冻结转录是 T08。
- 看护：`tests/test_run_argv.py`（模型 + 真 worker 对拍）、`tests/test_run_argv_e2e.py`（HTTP 全链路）、`tests/test_run_argv_privacy.py`（真实进程 argv / fd 输出 / 错误 / 静默看门狗）。

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
  失败退役只针对那一条 worker 实例；唯一的调度例外 `WorkerError(queue_superseded)`
  只表示排队项被更新请求替换，原错误继续返回，但不能关闭同源变体共享的 worker、连带中断
  其它在途请求。其余 worker 错误与源验证失败仍定向退役，不能推广成所有 nonfatal 错误保活。
  看护 `tests/test_selected_artifact_api.py` 的调用方/错误边界与
  `tests/test_selected_artifact_worker.py` 的真实 Python worker 在途及后续复用；后者注入队列错误，
  不替代真实 workerd / Windows 调度验证。选择子集不能重写整份脚本登记。
- 省略 `source_policy` 的 `/api/engine/render` 落在 PNG 源图上仍走 legacy（不拒绝，旧布局语义不变），
  但响应带 `source_unverified: {reason: "source_policy_missing", kind: "png"}`：这张图重跑了脚本、
  未与已存字节核对，不得当作已验证产物（#1145）。前端对未采用的 PNG 面板：`renderTargets` 跳过
  跨图邻居与 tracked 的补渲染（采用只走 `admitPngEntry` 带策略的请求）；**style-bound 例外，照发**——
  绑定画布上 `admitPngEntry` 本就拒绝，这次渲染是它唯一的 manifest 来源，`alignNewFigures` 靠它结清
  样式债，跳过会让面板整个会话上不了样式、进不了编辑（#1179 Codex P1）。
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

`script-backed-figure-v1`（来源驱动采用，ADR 0139 / #1200）是第三条策略，**应急开关
`config.experiments.script_backed_figure`，10-21 起默认开**（`artifactcontext.script_backed_enabled`；只有布尔 `false` 关）：
关着时请求带它一律 409 `policy_disabled`，其余行为逐字节不变。要点：① 安全条件分两档——
`figsession._require_static_artifact_figure`（像素档，精确策略，白名单原样；白名单是**唯一**拒绝原因时报
`artist_profile`，否则仍是 `unsupported_render_state`）与 `_require_editable_artifact_figure`（编辑档：类型须来自
matplotlib 本体、实例 / 类 / 被 monkeypatch 的方法都不能是用户代码，另查集合偏移变换、标注坐标回调、norm / cmap；其余
回调 / filter / path_effects / 非标准 transform·ticks·scale·canvas / usetex / 字体文件 / 子图·寄生轴 / 布局引擎 / draw_event /
存活线程 / `unregistered` 闸全部保留）；② 保存唯一性、`capture.get(stem) is fig`、PNG + `bbox_inches is None`、图幅（尺寸与
pHYs）、native 拒绝与精确策略相同；只收 PNG（PDF 的重画摘要含时间戳，没法当确认绑定）；③ 准入走
`artifactcontext.admit_reconstruction`：像素**逐字节**相等 `exact`、否则 `needs_confirmation` 并带差异度量
（`pdfbackend.compare_png_extent`，无底噪无阈值），**没有任何自动接受的容差**；响应带 `source_backed:{status,
reconstruction_sha256, metrics}`；④ 真实编辑（结构性 `figure.frame` 之外的 patch，render / preview_png / 导出同口径）必须带
`confirmed_reconstruction_sha256`，缺 → `confirmation_required`，与本次重画不等 → `reconstruction_changed`（均不可重试，不杀 worker）；
⑤ 采用成功时 `record_adoption` 在 `data_dir()/source-backed/adoptions.json` 原子记（项目 × source_id × bytes_sha256，≤4096 条，
满了只清已失效的、仍满就 `adoption_store_full`），写回类 / 历史类 / 同步 / specfix / invalidate / 老 GET 入口经
`app._refuse_adopted_source` 命中即拒——**不看请求带没带 policy**；原件字节变了记录不再命中；记录文件**存在但损坏 / 版本不认识**是「状态未知」，fail closed：PNG 写回类入口一律拒、新采用被拒（绝不覆盖损坏文件），恢复方法是确认无人使用后删除 `data_dir()/source-backed/adoptions.json`（日志里有路径）。池键、out_dir、witness、
`_validated_artifact_probe` 随 `render_policy` 与精确策略永不共享。看护：`tests/test_script_backed_policy.py`。

## 成功启动上下文与 Conda 激活（P09，EX07 / EX11，AC-11 / AC-19）

- **成功事实只有一份登记**：`runconfig` 的 `launches[脚本]`，由 `app._finalize_script_attempt`（已有的成功收口）经
  `launchctx.record_success`（`runconfig.commit_success` 事务，与磁盘面板默认配置同一次写入）写；失败 / 部分结果 / 回执 `partial` /
  依据不全都不写，依据不全还清掉旧记录。记录只含引用与摘要：解释器身份、启动方式引用（`direct` 或 `conda`：conda 路径 + prefix）、
  Conda 环境版本 `env_state` 与环境代（direct 记录在 Conda 环境里同样记；取**起会话那一刻**的值，不取收口时的）、cwd 模式、运行配置**引用**、脚本 sha1 + 回执自报的 helper /
  数据文件 sha256、数据绑定修订。**不存**激活后的环境变量、argv 值、敏感输入、答案原文。记录里的路径只用来对账，**永不当可执行路径**。
  记录的消费端（再次打开时沿用上次成功配置，`launchctx.verify`）**不在本期**，见 #982。
- **激活一次、捕获环境、直接启动**：对 Conda 环境，`launcher.activated_delta` 只跑一次
  `conda run --no-capture-output --prefix <p> <python 绝对路径> -c <把 os.environ 写进 0600 结果文件>`（不带 `--`：conda run 的命令部分是
  argparse REMAINDER，老 conda 24.5.0 不认 `--`；`activate.d` / `deactivate.d` 往 stdout 打印什么都不影响，结果走文件），取相对当前环境的
  **增量**，缓存在**进程内存**里（键 `(prefix, env_state, conda_exe)`，环境一变重新捕获，**绝不持久化**：不进登记、记录、回执、日志、
  诊断，日志只记变量个数）。所有「用目标解释器起子进程」的地方都**直接**起目标解释器（我们的直接子进程，取消 / 超时 / 协议 / 回执 pid
  与没有 Conda 时一样），只把 env 换成「调用方原来的 env + 激活增量」：worker（`pool.EngineWorker`）、预检探针（`launcher.activate_env`）、
  依赖门、`deprepair.worker_self_test`、`runtime.probe_packages`（app 注入 `env_for`）。
- **启动方式是解释器路径的纯函数**：`launcher.launcher_for_interpreter(python)`——关开关 → 现场检测（Conda 环境 + 找得到 conda；Windows 同样，见下），
  不读脚本、项目、成功记录、来源标签，**没有** ContextVar 之类的“范围”，所以任何线程、任何入口对同一条解释器给出同一个 env。成功记录里的
  launcher 只作为事实写入（回执、记录照记），**不驱动决策**。行为变化：以前裸起成功过的 Conda 脚本，今后会带上激活环境（这本来就是用户对
  Conda 环境的预期）。`TAVOTTO_CONDA_ACTIVATION=0` 关闭。**Windows 同样默认开（ADR 0142，2026-10-21 修订）**，见下一条。
- **Windows 的捕获（ADR 0142）**：与 POSIX 同构——只换三处。①conda 可执行只认安装根的 `Scripts\conda.exe`（`CONDA_EXE`、`PATH` 上 `condabin\conda.bat` 的所在根也按「上两级」推出安装根，但**不执行** `.bat` / `.cmd`：不经 cmd.exe、不拼 shell）；
  常见安装位置另含 `%LOCALAPPDATA%` / `%ProgramData%` 下的 miniconda3 / anaconda3 / miniforge3，以及安装程序写的注册表卸载项 / PEP 514 InstallPath（仅作候选，仍须过安装根检查；ADR 0142 决策 7）；可信根下的环境若该根的 conda 可执行不可用，`activate_env`/`prewarm` 抛 `conda_launcher_unavailable` 而不裸起（决策 8），路径比较一律 `normcase`。②捕获脚本写成 `probe-results/capture-*.py`（`conda run` 在 Windows 把命令写进临时 `.bat`，
  多行 `-c` 会被 cmd 截断），argv 里只剩路径；结果仍是 UTF-8 JSON 结果文件，不读 stdout，所以控制台代码页（chcp 936 / 65001）不影响。③超时收尾用 `taskkill /PID <conda> /T /F` 收整棵树。
  失败仍是 `LauncherUnavailable`（`conda_launcher_unavailable`），不缓存。**合入门槛**（清单见 ADR 0142）：第 1/2/4/5/6/8 项已于 2026-10-21 Win11 真机通过；第 7 项以「PATH 只留 System32、无 `CONDA_EXE`」近似通过（真「开始菜单」启动未验）；第 3 项（ROOT）改由 #1282 承接。
- **捕获失败一律 fail-closed 且不缓存**：Conda 环境捕获失败 / 超时，任何调用点都抛 `LauncherUnavailable`（稳定码 `conda_launcher_unavailable`），
  不退回裸起。体检 / 预检遇到它返回「不可用：conda_launcher_unavailable」，**不写**任何 verdict / facts 缓存（`launcher.is_unavailable`），
  不作废记住的解释器；项目已选定 / 记住 / 显式选择的解释器把稳定码交给用户，默认链条与系统候选只跳过这一个继续试后面的
  （`pool.select_worker_python`）。`plan_for` 写进环境错误，`_execute` 零执行收场（`deprepair.decide_environment_pinned` 把它转成 `WorkerError`）。
  **只激活用户自己的 conda 安装里的环境**：安装根本身（base）或它的 `envs/<名字>`；安装根来自 `CONDA_EXE` / `PATH` 上的 conda
  与常见安装位置（`launcher._STANDARD_ROOTS`；Windows 另含安装程序写的注册表卸载项 / PEP 514 InstallPath，仅作候选）。须确实是安装根：`conda-meta/` + 可执行的 conda 可执行文件（POSIX `bin/conda`，Windows `Scripts\conda.exe`，不认 `.bat` / `.cmd`）。激活会 source prefix
  自己的 `activate.d`；任何凭登记 / 路径形状的推断（environments.txt、项目标记）都可能被项目目录冒充，所以不用——其它位置的
  Conda 环境（`conda create -p` 到任意目录、克隆项目自带的 `.venv/conda-meta/`）一律直接启动（与 P09 之前一致），
  直接启动失败也不当成对环境的结论（`unlocated_conda_prefix`）。
  计划 / 钉下的决定 / 热会话比的是完整启动上下文 `launcher.context_key`（直接启动 ↔ 激活、conda、prefix、环境版本）。
  conda 可执行文件的候选：prefix 所属安装根的 conda 可执行文件（POSIX `bin/conda`，Windows `Scripts\conda.exe`），再是 `CONDA_EXE` / `PATH`（不读 `conda-meta/history`、不读 environments.txt：
  都是项目能伪造或会过期的线索）。不会被激活的 Conda 形状的解释器（找不到 conda / 不在用户 conda 安装里）照常裸起；**但裸起失败不是对环境的
  结论**：`pool._has_matplotlib` / `projectenv.probe_environment` 此时抛 / 返回 `conda_launcher_unavailable`（瞬时，不缓存、不作废记住的
  解释器、不静默换别的）。
- **桌面拥有的 native 会话**（`desktopproc.start`，P06）没有用户 shell 替它激活，与 safe 档走同一个 `activate_env`；激活在**拿到 native 租约之后**、spawn 之前（装包期间连 conda / activate.d 都不跑），
  失败 = 不 spawn、放租约，`RunError(conda_launcher_unavailable)`。捕获前后 `prefix_state` 不一致（捕获期间环境被改）的结果不用也不缓存。终端 native 继承用户已激活的 shell，不改。
- **增量是双向的**：激活 `unset` 掉的变量在增量里记成 `None`，`launcher.merge_env` 启动时 `pop` 它（所有启动点都经这一处，含 `desktopproc`）；
  `_VOLATILE_ENV` 永不删。捕获超时的收尾**叶子先死**（先杀后代、让还活着的 conda 收尸，再杀 conda 并 wait），不留僵尸。
- **捕获的环境键与时机**：缓存键 `(prefix, prefix_state, conda_exe)`，`prefix_state` 含 `conda-meta/history` / 目录 / `state`（`conda env config vars`）
  与 `etc/conda/activate.d`、`deactivate.d` 的文件清单及各自 (size, mtime_ns)。池锁之外预热（`pool.acquire` 里 `launcher.prewarm`），预检的预算
  传进捕获（`timeout`），排在别人的捕获后面也只等自己的预算（单飞锁带超时）；捕获解释器用 `-I -S` 起，只读 `os.environ`，不执行环境里的
  sitecustomize / `.pth`；结果文件放 `probe-results/`（0600），启动时清过期残留并在过了过期线后再扫一次（`launcher.startup`），退出时清在途的。
- **按解释器缓存的探针结论**（`depplan` 目标事实 / 静态索引、`projectenv` 系统候选体检、`userenvs` 体检、`pool` 的 `import matplotlib` 结论与选定的
  渲染解释器）都按 `launcher.context_key(python)`（`direct` 或 `conda|exe|prefix|env_state`）分区。
- **限制**：只捕获环境变量。激活脚本里环境变量以外的副作用（启动 license 服务、`ulimit`、shell 函数）不会在每次启动时重放，
  `deactivate.d` 不再执行——与 IDE 的通行做法一致。需要激活的解释器只走 Python 池（`WorkerdWorker` 抛 `WorkerdUnavailable` 回退）。

看护：`tests/test_conda_launch_context.py`（模拟 conda + `TAVOTTO_TEST_CONDA_PREFIX` / `TAVOTTO_TEST_OLD_CONDA_EXE` 指向真 conda 环境时的真 conda 用例，
未设则 skip）。

## 写入根绊线（#898，ADR 0141）

MCP 来源的 safe 运行在 worker 里多装一道**运行期**写入检查（`engine/writetripwire.py`）：脚本 build 期间，写打开与改动 / 删除文件的审计事件按真实 cwd 解析后必须落在白名单内，否则
`PermissionError`，build 以 `write_outside_roots` 收场（脚本即使吞掉异常也一样）。它补的是 `_check_argv_scope` 静态证明的洞（`os.chdir` 之后的相对 token）；**不是沙盒**。

- 与捕获 / 守卫的关系：现有守卫（真实图库的 `Path.unlink` / `write_text` 跳过、删除 / 改名账本）**一字不动**，绊线装在 `ledger.install()` 之后，不改任何一条守卫的语义；它也不属于 `safe_file_operation_blocked` 那一族，不触发 0127 的回退。
- 配置经 `execspec.worker_argv` 的 `--write-roots`，缺省（GUI / CLI）argv 逐字节不变；池键 `_worker_key` / `_worker_base` 并入 `execspec.write_roots_key`。
- 窗口 = 脚本 build；窗口外（导出、override、预览）是引擎自己的写，不拦。脚本留下的线程 / `atexit` 在窗口外，是盲区。
- 盲区清单与白名单见 `docs/rules/backend/execution-entries.md`「写入根绊线」与 ADR 0141；用例 `tests/test_mcp_write_tripwire.py` 同时钉「挡得住的」和「挡不住的」。

## 速查表原要点（#815 精简时迁入，原文照搬）

速查表那一格为让 Codex 自动拼接留出余量而收成索引；下面是当时的全文，与上文同等有效。

- 捕获与显式 cax 宿主三条入口同源；selected 队列替换不退役共享 worker；`safe_spec()` / `worker_argv()` 唯一出处；argv 非空只经 `safe_spec(argv,run_config)`、公开投影只带个数与引用、缺引用显式拒绝不回落空 argv（T03）；argparse 表单 schema 只读源码、是建议不是门槛（T07）；首开问不问由 `workdir.resolve_mode` 定，不猜不就近；数据改指只在原路径打不开之后、只读、用户亲手指认（ADR 0106）

速查表看护列精简时挪出的用例（仍是本主题的看护）：`tests/test_workerd_pool.py`、`test_first_open_workdir.py`、`test_zero_capture.py`、`test_savefig_capture_params.py`、`test_colorbar_capture.py`。
