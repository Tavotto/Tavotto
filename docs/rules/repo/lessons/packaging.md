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
