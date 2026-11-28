# 错题本 · backend

> 格式与规矩见 `docs/rules/repo/lessons/README.md`。写入只走每日复盘，别直接改。

### L-028 开发脚本造假项目前先 setdefault TAVOTTO_DATA_DIR → docs/rules/backend/project-system.md

### L-048 用例的预期路径也要问解释器，不用 sysconfig 自算
- 类: 别这样
- 触发: tests/test_dependency_plan.py, tests/test_runtime_build.py
- Seen: 2026-09-22
- 为什么: 用 sysconfig 按 base 算出的目录在 hosted CI 存在，在 Ubuntu 系统 Python 上真目录叫 dist-packages，实验室才红；hosted CI 绿证明不了发行版布局。
- 证据: （迁移自维护者记录，无编号）

### L-050 预计算查找表的裁剪条件照抄前面每一步的否定
- 类: 别这样
- 触发: src/tavotto/glyphplan.py, tests/test_glyph_plan.py
- Seen: 2026-09-01
- 为什么: 字形分层判据是分步的，按语义写「primary 与 cjk 都没有」会让下标字符前后端判成不同层；只在极少码位上发作，测试全绿。要补专挑分歧输入的用例。
- 证据: （迁移自维护者记录，无编号）

### L-051 联调服务器与演示实例同时隔离数据目录和配置目录 → docs/rules/backend/project-system.md

### L-054 固定进度 id 上「异步起加轮询」读到的是上一次的终态
- 类: 别这样
- 触发: src/tavotto/engine/deprepair.py, src/tavotto/engine/prepsession.py
- Seen: 2026-09-21
- 为什么: 第二次重建时线程还没发出 preparing，轮询就读到上次留下的 done 返回，被测事务根本没被观测，探针为空而用例全绿。改用同步入口或比旧时间戳新的终态。
- 证据: #461

### L-058 别断言「被测对象会不会打印某句话」，让夹具自报状态
- 类: 别这样
- 触发: tests/bridge/
- Seen: 2026-08-27
- 为什么: matplotlib 3.10.8 打印 non-interactive 警告而 3.11.1 不打，CI 捕到空串判红，看着像被测对象坏了。代理量在版本升级时无声失效；应由夹具报 RAN/HOOKED，并给基准加正向对照。
- 证据: #186

### L-059 刻度：get_ticklabels 含视区外标签，set_ticks 会悄悄扩视区
- 类: 别这样
- 触发: src/tavotto/engine/tickmodel.py
- Seen: 2026-08-31
- 为什么: get_ticklabels 返回 locator 全部标签，LogLocator 大半在子图外，隐形孪生轴同样有文字有位置；set_ticks 对整组共享轴 set_view_interval(min,max) 只扩不缩。判「画没画」用 _update_ticks 返回加可见性；set_ticks 前快照并还回视区。
- 证据: （迁移自维护者记录，无编号）

### L-060 依赖 matplotlib 行为的注释先用探针复测，对照只改一个变量
- 类: 照这样
- 触发: src/tavotto/engine/overrides.py, src/tavotto/engine/scriptadjust.py
- Seen: 2026-08-21, 2026-11-06
- 为什么: 两条规则建立在「update_scalarmappable 会覆盖显式颜色」的注释上，20 行探针证明不会，规则比现实更严、吞掉了能用的编辑；另一次测 Arc 不能填色，探针同时换了 fill 与类，结论也错。11-06 导出脚本沿用早期 spike 的裁切结论，保存链早已变，现版本自带示例真跑才暴露 tight 图幅偏差。引用旧结论前重测当前调用链。
- 证据: （迁移自维护者记录，无编号）, #1790

### L-062 测试宽限项的量纲要与被测量同源，单跑和全量各跑一次
- 类: 照这样
- 触发: tests/test_layout_timeline.py, src/tavotto/engine/versionstore.py
- Seen: 2026-09-03
- 为什么: 版本时间线字节预算只累加条目，漏记 JSON 外壳，用例的 11 字节宽限掩盖了差额，整模块跑绿、单跑红。上限的主语是磁盘文件大小而不是条目之和；两个方向都跑。
- 证据: （迁移自维护者记录，无编号）

### L-063 后台探测写缓存要带世代，reset 后丢弃旧探测结果
- 类: 照这样
- 触发: src/tavotto/engine/probe.py
- Seen: 2026-09-21
- 为什么: 上一文件的后台探测在本文件 reset 之后才结束，把重置前答案写回，单跑绿、按 CI 顺序全量跑必红；真缺陷在产品代码，用户改设置后 reset 同样会被覆盖。
- 证据: #475

### L-064 后台线程投递的用例，reset 要 join，复现要拖宽窗口
- 类: 照这样
- 触发: src/tavotto/engine/pool.py, tests/test_thread_leak_gate.py, src/tavotto/engine/ai_bridge.py, tests/test_msix_migrate.py
- Seen: 2026-09-20, 2026-11-27
- 为什么: 线程在 fixture teardown 与 monkeypatch 撤销后才投递，调用到真函数；空闲机器量不到，CI 每个 session 漏一条。11-27 前面用例留下的 AI 心跳线程按全局路径建库，Windows 合并组红；夹具开头先停掉进程级后台线程。
- 证据: #440, #2319

### L-066 .part 搬正式名失败时，先查正式名上是否已有校验合格的副本
- 类: 照这样
- 触发: src/tavotto/engine/privatepython.py, src/tavotto/engine/updater.py
- Seen: 2026-09-21
- 为什么: Windows 上 os.replace 到别的进程正打开的文件抛 WinError 5；两进程竞争在 POSIX 恒绿、Windows 确定性红，把并发正常结局当成 write_failed。
- 证据: #467

### L-067 改 worker 钩子、包装 stdlib 或解析用户代码，在 3.10 与 3.14 两端各跑一遍
- 类: 照这样
- 触发: src/tavotto/engine/worker.py, src/tavotto/engine/importscan.py, src/tavotto/engine/notebookscript.py
- Seen: 2026-10-14, 2026-11-11
- 为什么: 3.10 的 Path.mkdir 内部调 is_dir，被包装当成脚本观察；本机 3.13 全绿，CI 3.10 两条豁免用例红。11-11 symtable 在 3.14 起对顶层 await、靠后的 future 导入直接 SyntaxError（3.13 放行），3.14 分片红 4 条；ast / symtable / compile 每个小版本都在变，建两端 venv 只要几十秒。
- 证据: #1024, #1838

### L-121 给同步步骤设时间预算，要罩住每个会阻塞的调用，超支项下次仍重试
- 类: 别这样
- 触发: src/tavotto/engine/migrate.py, src/tavotto/engine/msixmigrate.py
- Seen: 2026-11-02
- 为什么: 启动时迁移旧数据的 8 秒预算只在步骤之间查剩余，SQLite backup 遇源库独占锁会一直等，应用打不开；预算耗尽跳过的历史库随后被启动流程新建成空库，下次迁移见目标已存在就跳过，旧数据永久丢失。评审报两条 P1。
- 证据: #1603

### L-126 新 worktree 先补齐 gitignore 的内置字体，大片同类红先单跑看 stderr
- 类: 别这样
- 触发: scripts/fetch_fonts.py
- Seen: 2026-11-03, 2026-11-23
- 为什么: git worktree add 不带被忽略的 src/tavotto/resources/fonts，约 170 条导出、公式、字体用例假红（backend_unavailable、child_failed），看着像改坏了。先跑 scripts/fetch_fonts.py 或从主工作区拷。11-23 新 worktree 30 条后端用例红、main 上同样红，被当成本机环境问题放过，补字体后全过：「main 上也红」不等于环境问题。
- 证据: #1927

### L-128 带锁的不变式用确定性交错反证，多线程跑几轮没红不算证据
- 类: 别这样
- 触发: src/tavotto/rendercore/hbshaper.py, tests/test_rendercore_userfonts.py
- Seen: 2026-11-04
- 为什么: 字体 LRU 的插入与淘汰分成两段锁，子代理写明没做反证仍被接受；合入后 3.14 分片偶发 count 3，把 #1699 踢出队列，24 小时内红过 5 个 PR。查、改、维持上限放进同一临界区；用放锁钩子或 Event 强制交错，撤掉修复必红。
- 证据: #1671, #1699, #1705

### L-129 性能压测要跨过池或缓存上限，同一对象改多少次都量不到悬崖
- 类: 别这样
- 触发: src/tavotto/engine/pool.py, tests/test_pool_lru_lease.py
- Seen: 2026-11-03
- 为什么: 同一张图编辑 400 次不变慢，于是一直没发现 4 个脚本轮流时每次都冷启动（15 倍），由一份用户反馈才暴露。凡有容量上限，压测取上限加一个并轮转；前端主动补渲染邻居也会制造这种轮转。
- 证据: #1639

### L-130 接外部 CLI 的写入或沙盒行为，目标平台用真 CLI 跑一次，假 CLI 绿不算 → docs/rules/backend/ai-agent-bridge.md

### L-131 override 不读当前合成值或残留状态，写前问冷重放读到的是否同一份 → docs/rules/backend/override-application-and-replay.md

### L-137 worker 给脚本的运行面要和 python script.py 对拍 → docs/rules/backend/worker-protocol-and-lifecycle.md

### L-138 新失败码进路由先问 AI 手上有没有能解决它的工具 → docs/rules/backend/preparation-and-receipts.md

### L-139 加失败分支时问：包里这条记录分得清哪几种原因 → docs/rules/backend/diagnostics.md

### L-140 修静态扫描漏的写法时枚举整族，判不全要有运行时回路 → docs/rules/backend/registry-discovery-and-probe.md

### L-141 看护登记表按写入点枚举，别靠造一份样本走一遍
- 类: 别这样
- 触发: src/tavotto/engine/diagsend.py, tests/test_diag_send.py
- Seen: 2026-10-31
- 为什么: 诊断包新文件没登记，确认页显示成「其他」；已有看护只造无项目的包，按条件才写的文件它看不见。改成用 AST 收全部写入点的字面名，要求都在登记表里。
- 证据: #1543

### L-142 长操作的并发用例把窗口摆出来，别让它在测试里为零
- 类: 别这样
- 触发: src/tavotto/engine/probe.py, src/tavotto/engine/deprepair.py
- Seen: 2026-10-31, 2026-11-02
- 为什么: 首次慢启动持池锁约 10 秒，排队的探测把刚起好的会话关掉，这个窗口只在首次运行出现（#1572）；单包修复持环境锁约 3 分钟，AI 换环境 3 毫秒后就报忙（#1632）。用可控的假启动或假安装摆出重叠，撤掉修复要红。
- 证据: #1572, #1632

### L-147 验「原文件不动」要让导出目标与输入同名、同路径或互为链接
- 类: 别这样
- 触发: src/tavotto/engine/scriptadjust.py, src/tavotto/engine/exportroute.py
- Seen: 2026-11-06
- 为什么: 导出名加后缀仍可能等于用户重新打开的旧导出脚本名，覆盖策略可把原脚本替换掉；正常命名的夹具看不见。验收对目标与所有受保护输入核文件身份，并用重名、硬链接场景断言原字节不变。
- 证据: #1790

### L-148 生成的可编辑产物要验「导出、重开、再编辑、再导出」的第二轮
- 类: 别这样
- 触发: src/tavotto/engine/scriptadjust.py
- Seen: 2026-11-06
- 为什么: show-only 脚本首轮导出正确，重开再导出时两个调整块认同一张图，首块的完成标记使新块被跳过，重放验证拒绝交付；首轮用例全绿。第二轮改不同属性，在不加载产品的子进程核两轮修改都生效。
- 证据: #1790

### L-154 给一类结局配出口前，按脚本常见写法逐个核用户最后落到哪 → docs/rules/backend/preparation-and-receipts.md

### L-156 按图分数换算位置的导出逻辑，夹具要带 bbox_inches="tight" 并覆盖每类挪位置的键
- 类: 别这样
- 触发: src/tavotto/engine/scriptadjust.py, tests/test_script_adjust*, tests/test_figure_copy_anchor*
- Seen: 2026-11-11, 2026-11-25
- 为什么: 导出 .py 的逐键普查夹具不带裁切框，tight 只测了改字号；块里按整张 figsize 换算、引擎按裁出来的那一页换算，挪过文字的图导出连续失败，靠一份用户反馈才暴露。引擎与块的坐标换算是同源对，改一边对拍另一边，有框、无框各跑一遍。11-25 图内复制的落点用请求位置、没换算 tight 输出框，评审报两条 P1。
- 证据: #1847, #2163

### L-157 统计汇总的用例要有内部缺失位置与跨位置合并的总体
- 类: 别这样
- 触发: src/tavotto/engine/ai_data_recipe.py, tests/test_ai_data_recipe.py
- Seen: 2026-11-11
- 为什么: 数据出图配方只用整齐数据测，评审连报两条 P1：中间某个 x 全缺时 fill_between 把两侧连成一片带；水平汇总跨全部 x 合并，图注的 n 却按单个 x 算。每种汇总各写一条：中间缺一格、跨位置合并、同一标记在两个角色里。
- 证据: #1841

### L-160 给安全闸加生产者边界的规范化，先喂真实生产者的合法输出
- 类: 别这样
- 触发: src/tavotto/rendercore/svgcheck.py, src/tavotto/engine/artifactinspect.py
- Seen: 2026-11-13, 2026-11-16
- 为什么: SVG 闸的 id 语法比 matplotlib 能写出的窄，用例只用无 gid 的示例图，脚本 set_gid 中文名的图整条导出失败，一份用户反馈才暴露。修复初版只按一个错误码触发、对全文替换 #token，评审连报两条 P1（改坏十六进制颜色、重复 id 选择器失配）。性质测试喂 set_gid 的中文、空格、数字名与重复 id；修复按闸可能先报的任一错误码触发，引用只在 href / url(#…) / 选择器 id 这些结构位置改写。
- 证据: #1893, #1962

### L-164 新加安装或修复路径时，写「这条刚确定性失败过再打开卡片」的用例
- 类: 别这样
- 触发: src/tavotto/engine/prepsession.py, web/src/components/PreparationCard.tsx
- Seen: 2026-11-15
- 为什么: 推荐逻辑只看哪条路可用、不看它刚失败过：自检未通过这类重试改变不了的失败码之后，主按钮原样再推荐同一条路，一份用户反馈里点了十次、失败十次。用例只断言单次失败的结局，准备卡与修复卡各自钉死目标。两个入口都要有「失败一次后推荐什么」的用例。
- 证据: #1934

### L-166 判据按这次运行实际发生的事定，产品已测出的事实结构化交给失败卡与 AI
- 类: 别这样
- 触发: src/tavotto/engine/diagexternal.py, src/tavotto/engine/diagdetail.py
- Seen: 2026-11-16
- 为什么: 诊断包收源码按「在不在项目文件夹里」划界，脚本经 sys.path 引入项目外公共代码时一份都没带上，一份用户反馈才暴露。另一份里素材扫描早已测出「扩展名 png、字节是 JPEG」，运行失败却只给笼统 script_error，AI 转去怀疑安全运行；首版路径换算按同名去项目根猜文件，评审用「脚本 chdir 到外部目录」拦下（#1966）。夹具要有项目外 import、chdir 出去两种写法；换算只镜像产品已有的确定规则，换不出就回空。
- 证据: #1953, #1966

### L-174 修复的看护用例经产品真入口断言结果，不断言源码文本
- 类: 别这样
- 触发: src/tavotto/engine/depplan.py, tests/test_launchscript.py
- Seen: 2026-11-19
- 为什么: 给依赖计划接启动脚本时条件写成 `target_kind == "script"`，而那里的 `target_kind` 是安装目标，条件恒假；用例只断言 `inspect.getsource` 里有这行调用，一直绿，修复从没生效，定向复核才抓到。改成真调入口后又暴露拼接 AST 没挪行号、导入扫描照样认错。同名参数在两个模块含义不同，先核语义再用。
- 证据: #2048

### L-180 给不回收的持久存储加上限时，用例按真实节奏写满再走主路径；回收的引用集合扫不全就不删
- 类: 别这样
- 触发: src/tavotto/engine/svgstore.py, tests/test_svgstore.py, tests/test_export_svg.py
- Seen: 2026-11-24
- 为什么: 导出快照存储满额只断言「拒绝新增、旧字节不动」，同一次运行里每次编辑都算 current、永远不判旧，写满后 SVG 导出一直卡死，一份用户反馈才暴露；补回收时评审又报 3 条 P1：并发提交的新引用、目录枚举失败、迁移中断的旧版本文件，都让引用集合看似完整却漏项，会删掉仍被引用的快照。写满用例断言用户还能导出；扫描任一处失败、或与提交不互斥，就整轮不删。
- 证据: #2166

### L-181 矢量核对量画出来的面，「改前就有」的豁免带上位置并按补丁后状态重判 → docs/rules/backend/ai-agent-bridge.md
