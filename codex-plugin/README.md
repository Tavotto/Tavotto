# Magplot 的 Codex 插件

让 Codex（CLI 与桌面应用）生成的 matplotlib 图**天然能进 Magplot 继续用鼠标改**：

```
用户: 用这个 CSV 画温度与去除率的折线图，要 error bar，适合论文
  ↓
Codex + 本插件: 写 figures/fig_removal_rate.py → 跑 → Fig1_removal_rate.pdf
  ↓
自动交接: magplot open → Magplot 桌面窗口打开这张图
  ↓
用户: 拖图例、改字号、调线宽、拼版 → 导出矢量 PDF（不用再问 AI 一句）
```

插件本身只有 skills，没有 MCP server——Codex 本来就能跑 Python、读写文件，
缺的是「怎么写才接得上」的约定和最后那一跳交接。

## 安装

本仓库同时是一个 Codex 插件市场（仓库根的 `.agents/plugins/marketplace.json`）：

```bash
# 从 GitHub 装（一行）
codex plugin marketplace add erwanjun/magplot && codex plugin add magplot@magplot

# 更新
codex plugin marketplace upgrade magplot

# 本地开发时指向工作副本
codex plugin marketplace add /path/to/magplot && codex plugin add magplot@magplot
```

发进官方插件目录（让用户在 ChatGPT / Codex 里直接搜到）的路线与缺口清单在
[`../docs/codex-plugin-distribution.md`](../docs/codex-plugin-distribution.md)。

装完**新开一个会话**。CLI 里用 `$magplot-figure` 显式调用，或者直接说「画张图」
让它隐式命中；Codex 桌面应用共用同一份插件目录，装一次两边都有。

还需要机器上有 Magplot 本体（交接的落点）：

* 桌面版（推荐）：<https://github.com/erwanjun/magplot/releases>
* 命令行版：`pipx install magplot`

**只装桌面版就够了**——不需要另外装 Python/Conda，也不需要配任何环境变量。
插件会按下面的顺序找到 Magplot 的命令行入口，前面的赢：

1. `MAGPLOT_CLI` 环境变量（高级覆盖）
2. PATH 里的 `magplot`（pip / pipx 装的）
3. **安装清单** `install.json`（桌面版装完就有，记着 CLI 的绝对路径）
4. **已知安装位置**里的 `magplot-cli`（清单丢了照样能找到）
5. Windows 上 HKCU 记着的安装位置（只当补充）
6. 当前解释器里的 `magplot` 模块

桌面安装包里带的 `magplot-cli` 是一个 **console 版**命令行，与界面共用同一份
运行时。装出来的 `Magplot.exe` 是 GUI 程序，**不能当命令行调**（没有真终端时
它的 stdout 会落进日志文件，调用方拿不到那行 JSON）——所以才有这一个。

自检：`magplot doctor --json`（不起界面、不联网）。完整协议、错误码与排障见
[`../docs/handoff-protocol.md`](../docs/handoff-protocol.md)。

## 插件自己的更新

Codex 不会替插件检查更新，所以插件自己查：每 24 小时最多一次，1.5 秒超时，
网络不通就用上次的答案、不报错也不拖慢出图。有新版时交接结果里多一个
`update` 字段，同时往 stderr 写一句人话——**stdout 永远只有那一行 JSON**。

**只提醒，不下载、不安装。** 看到提醒后自己执行：

```bash
codex plugin marketplace upgrade magplot   # 然后重载 Codex
```

显式查一次（忽略缓存）：

```bash
python3 skills/magplot-figure/scripts/update_check.py --json --force
```

两个开关：`MAGPLOT_UPDATE_URL`（换清单地址，自建分发/内网镜像用）、
`MAGPLOT_DISABLE_UPDATE_CHECK=1`（完全关掉，一个包都不发）。

## 结构

```
codex-plugin/
├── .codex-plugin/plugin.json          # 插件清单（Codex 认的唯一入口）
├── assets/magplot.svg                 # composer 图标 / logo
└── skills/magplot-figure/
    ├── SKILL.md                       # 约定 + 模板 + 交接 + 「之后就收手」
    ├── agents/openai.yaml             # 显示名与默认提示
    ├── references/compatibility.md    # 能鼠标改什么 / 必须回代码改什么
    └── scripts/handoff.py             # 登记 →（必要时）跑脚本 → 唤起 Magplot
```

交接的真正实现在 Magplot 主体里（`magplot open`，见
`src/magplot/engine/handoff.py`）：路径解析、注册表合并、唤起桌面还是浏览器
全在那边裁决，插件不做第二套判断。

插件里唯一的一处「判断」是**怎么找到那条命令行**（上面那六步）。它是
`src/magplot/engine/locate.py` 的镜像——插件跑在用户机器上，import 不到
magplot，这份重复无法避免；能避免的是两边悄悄漂开，所以
`tests/test_install_locate.py::test_plugin_mirrors_the_locator` 在一整张环境
矩阵上逐条比对两侧的输出。改一边必须同步另一边。
