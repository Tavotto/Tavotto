# 错题本 · packaging

> 格式与规矩见 `docs/rules/repo/lessons/README.md`。写入只走每日复盘，别直接改。

### L-118 版本敏感缺陷先对照发行档与下界档，本机版本不算证据
- 类: 别这样
- 触发: src/tavotto/rendercore/sourcefont.py, src/tavotto/rendercore/fonts.py, packaging/tavotto.spec
- Seen: 2026-09-08, 2026-11-04
- 为什么: 本机 matplotlib 比发行内置低一档，.ttc 字体按脸注册的缺陷只在新档发作，本机怎么试都绿；另一缺陷只在下界档发作。用对应版本的容器几分钟即可复现。
- 证据: （迁移自维护者记录，无编号）, #1672

### L-119 本机打桌面包的构建 venv 别用 Homebrew Python
- 类: 别这样
- 触发: scripts/build_desktop.py, scripts/codesign_macos.py, packaging/tavotto.spec
- Seen: 2026-09-24, 2026-09-26
- 为什么: Homebrew Python 3.13 的 framework minos=26.0，PyInstaller 原样打进 sidecar，14 上起不来。用 python-build-standalone 建 venv，打完跑 scripts/codesign_macos.py 的 scan --expect-min-os。
- 证据: #539

### L-132 冻结包里 sys.executable 不是 Python，起探测子进程走统一入口
- 类: 别这样
- 触发: src/tavotto/engine/fixtools/cmdsandbox.py, src/tavotto/engine/fixtools/trial.py, tests/test_fixtools_command.py
- Seen: 2026-11-03
- 为什么: 两处沙盒探测写死 sys.executable -c pass，源码模式全绿，桌面版本体不认 -c，每台 macOS 桌面版探测必败，受保护档永远不可用，用户反馈才发现。用例要放一个不认 -c 的假程序钉冻结态。
- 证据: #1675

### L-144 新增冻结入口或子命令，在产物上真起一次，源码绿不算
- 类: 别这样
- 触发: src/tavotto/engine/cli.py, src/tavotto/engine/agent_runner/, scripts/smoke_desktop.py, packaging/tavotto.spec, tests/test_frozen_entrypoints.py
- Seen: 2026-10-31
- 为什么: cli.py 从 agent_runner 导入 __main__，PyInstaller 不收这个子模块，0.19.0 到 0.19.2 用 Key 连接的内置 AI 每次启动就 ImportError；源码测试和冒烟一直绿，没人在产物上起过它。现在 smoke_desktop 起 agent-run 与 fix-mcp，新子命令加进去。
- 证据: #1570

### L-170 测试调用退出路径，os._exit 与 atexit._run_exitfuncs 在模块级 autouse fixture 里一起换掉
- 类: 别这样
- 触发: packaging/rthooks/, tests/test_no_crash_dialog_rthook.py
- Seen: 2026-11-18
- 为什么: 两条 excepthook 用例只有一条换了 atexit._run_exitfuncs，另一条在 pytest 进程里真跑了全部退出清理（关渲染子进程、删临时配置目录），同分片后面的 rendercore 用例段错误；本机缺渲染依赖、那批用例跳过，本地全绿，评审也看不出。逐条打补丁必漏一条；带进程级副作用的新用例先按 CI 分片顺序放在大套件前跑一次。
- 证据: #2027
