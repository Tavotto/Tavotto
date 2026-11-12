# 错题本 · packaging

> 格式与规矩见 `docs/rules/repo/lessons/README.md`。写入只走每日复盘，别直接改。

### L-118 版本敏感缺陷先对照发行档与下界档，本机版本不算证据
- 类: 别这样
- Seen: 2026-09-08, 2026-11-04
- 为什么: 本机 matplotlib 比发行内置低一档，.ttc 字体按脸注册的缺陷只在新档发作，本机怎么试都绿；另一缺陷只在下界档发作。用对应版本的容器几分钟即可复现。
- 证据: （迁移自维护者记录，无编号）, #1672

### L-119 本机打桌面包的构建 venv 别用 Homebrew Python
- 类: 别这样
- Seen: 2026-09-24, 2026-09-26
- 为什么: Homebrew Python 3.13 的 framework minos=26.0，PyInstaller 原样打进 sidecar，14 上起不来。用 python-build-standalone 建 venv，打完跑 scripts/codesign_macos.py 的 scan --expect-min-os。
- 证据: #539

### L-132 冻结包里 sys.executable 不是 Python，起探测子进程走统一入口
- 类: 别这样
- Seen: 2026-11-03
- 为什么: 两处沙盒探测写死 sys.executable -c pass，源码模式全绿，桌面版本体不认 -c，每台 macOS 桌面版探测必败，受保护档永远不可用，用户反馈才发现。用例要放一个不认 -c 的假程序钉冻结态。
- 证据: #1675

### L-144 新增冻结入口或子命令，在产物上真起一次，源码绿不算
- 类: 别这样
- Seen: 2026-10-31
- 为什么: cli.py 从 agent_runner 导入 __main__，PyInstaller 不收这个子模块，0.19.0 到 0.19.2 用 Key 连接的内置 AI 每次启动就 ImportError；源码测试和冒烟一直绿，没人在产物上起过它。现在 smoke_desktop 起 agent-run 与 fix-mcp，新子命令加进去。
- 证据: #1570
