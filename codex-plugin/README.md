# Tavotto 的 Codex 插件

让 Codex 画的 matplotlib 图**在 Codex 里就能用鼠标改完、体检完、导出投稿**：

```
用户: 用这个 CSV 画温度与去除率的折线图，要 error bar，适合论文
  ↓
Codex + 本插件的技能: 写 figures/fig_removal_rate.py → 跑 → Fig1_removal_rate.pdf
  ↓
Codex 调 tavotto_open_figure: 在对话里开出一块交互画布
  ↓
用户: 拖图例、改字号线宽、调刻度 → 跑出版规范预检 → 导出真矢量 PDF
  ↓（需要拼版、加标注、多图排版时）
tavotto open: 交给 Tavotto 桌面窗口接着排
```

分工三层，边界很清楚：

| 层 | 是什么 | 负责什么 |
| --- | --- | --- |
| **技能** `skills/tavotto-figure/` | 一份约定 + 模板 | 教 Codex 写出「Tavotto 接得住」的脚本；数据、坐标、图形结构归代码 |
| **MCP server** `mcp/` | 本地 stdio 进程 | 引擎会话、override、出版规范预检、真矢量导出。**没有 UI 的 host 里这六个工具就能走完整条流程** |
| **MCP App 画布** `mcp/widget/canvas.html` | Codex 内嵌 iframe | 用鼠标改图。复用 Tavotto 前端**同一份**画布代码，不是另一套实现 |

改动一律是 **override**（`gid + prop + value`），**你的 Python 源码一个字都不会被动**。

## 安装

本仓库同时是一个 Codex 插件市场（仓库根的 `.agents/plugins/marketplace.json`）：

```bash
# 从 GitHub 装（一行）
codex plugin marketplace add Tavotto/Tavotto && codex plugin add tavotto@tavotto

# 更新
codex plugin marketplace upgrade tavotto

# 本地开发时指向工作副本
codex plugin marketplace add /path/to/tavotto && codex plugin add tavotto@tavotto
```

装完（以及每次升级插件、装好引擎之后）**必须新开一个 Codex 会话/线程**：
已经开着的会话**不会**重新加载 MCP 工具——`codex plugin list` 显示
installed/enabled 只说明插件装上了，不代表当前会话拿得到工具，也不代表
MCP server 健康（健康与否用下面的 `--health` 查）。CLI 里用
`$tavotto-figure` 显式调用技能，或者直接说「画张图」让它隐式命中；
MCP 工具由 Codex 按需调用（也可以直接说「用 Tavotto 打开这张图」）。

还需要机器上有 Tavotto 本体：

* 桌面版（推荐）：<https://github.com/Tavotto/Tavotto/releases>
* 命令行版：`pipx install tavotto`

**只装桌面版就够了**——不需要另外装 Python/Conda，也不需要配任何环境变量。
插件会按下面的顺序找到 Tavotto 的命令行入口，前面的赢：

1. `TAVOTTO_CLI` 环境变量（高级覆盖）
2. PATH 里的 `tavotto`（pip / pipx 装的）
3. **安装清单** `install.json`（桌面版装完就有，记着 CLI 的绝对路径）
4. **已知安装位置**里的 `tavotto-cli`（清单丢了照样能找到）
5. Windows 上 HKCU 记着的安装位置（只当补充）
6. 当前解释器里的 `tavotto` 模块

桌面安装包里带的 `tavotto-cli` 是一个 **console 版**命令行，与界面共用同一份
运行时。装出来的 `Tavotto.exe` 是 GUI 程序，**不能当命令行调**（没有真终端时
它的 stdout 会落进日志文件，调用方拿不到那行 JSON）——所以才有这一个。

自检：`tavotto doctor --json`（不起界面、不联网）。完整协议、错误码与排障见
[`../docs/handoff-protocol.md`](../docs/handoff-protocol.md)。

### MCP server 对环境的要求与交接**不一样**

交接只要能**执行** `tavotto open`，上面那条 `tavotto-cli` 完全够用。但 MCP
server 是一个 Python 模块——它要 `import tavotto.engine.*` 在进程内驱动渲染
引擎，而 `tavotto-cli` 是打包成单文件的可执行程序，**给不出解释器**。

所以：

| 你装的 | 交接（`tavotto open`） | MCP 工具与内嵌画布 |
| --- | --- | --- |
| `pipx install tavotto` | ✅ | ✅ |
| 桌面版 + pipx | ✅ | ✅ |
| **只有桌面版** | ✅ | ❌ 报 `desktop_only`——一条命令补上（见下） |
| 都没装 | ❌ | ❌ 报 `tavotto_missing` |

启动器（`mcp/server.py`）按固定优先级找一个**验证过能
`import tavotto.engine`** 的解释器，前面的赢：

1. 启动它的那个 `python3` 本身
2. `TAVOTTO_MCP_PYTHON`（显式指给 MCP 的；指错了会指名道姓地报
   `engine_unavailable`，不会悄悄换别的）
3. `TAVOTTO_WORKER_PYTHON` / 设置里指定的渲染解释器（装了 tavotto 才算数）
4. **插件自管环境**（下面 `--provision` 建的那个）
5. 从 `tavotto` 命令行反推（pip/pipx console script 的 shebang）
6. PATH 里的 `python3` / `python`

每个候选都真的跑一遍 import 才算数——桌面版的 frozen `tavotto-cli`
永远不会被误当成解释器。

### 只装了桌面版：一条命令补齐引擎（零手工配置）

```bash
python3 <插件目录>/mcp/server.py --provision
```

它在 **Tavotto 用户配置目录**下建一个插件专属 venv（`mcp-runtime/venv`），
装与插件同版本的 `tavotto`（钉版本，可复现）。**不碰**系统 Python、Conda、
用户 site-packages 或 shell 配置；删掉 `mcp-runtime` 目录即卸载。离线环境
用 `--from /path/to/tavotto-x.y.z-py3-none-any.whl`（或源码目录）。
装完**新开一个 Codex 会话**。

### 健康检查

```bash
python3 <插件目录>/mcp/server.py --health
```

一行 JSON 说清：引擎找没找到（以及 resolver 每一步的结论与耗时）、画布产物
在不在、桌面版装没装。它能区分开在 `codex plugin list` 里长得一模一样的
几种状态：插件装了但没引擎（`desktop_only` / `tavotto_missing`）、显式指的
解释器用不了（`engine_unavailable`）、一切就绪但**当前会话还没重载工具**
（health 是绿的，那就新开会话）。

「只有桌面版」这一格**绝不会被说成「没装 Tavotto」**——你明明装了，缺的只是
一个 Python 环境，两者可以共存。启动器找不到可用解释器时不会静默退出，而是起
一个**降级 server**：握手正常（`serverInfo.version` 固定为 `0`，这是「引擎
不在」的显性信号），tools/list **只列真的可用的 `tavotto_health`**——不可用
的六个工具不会被伪装成可用；对着旧会话里记住的工具名调用会得到结构化错误
（code + 缺什么 + 恢复步骤），**绝不会**返回「画布已打开」之类的成功。

定位规则本身**不在这里重复**：启动器直接调用
`skills/tavotto-figure/scripts/handoff.py` 的 `find_tavotto()`（它是
`src/tavotto/engine/locate.py` 的镜像，两侧有跨平台矩阵测试比对）。

### 内嵌画布与桌面窗口是两条隔离的路

* **Codex 内嵌画布**只跑在 MCP server 里：画布资源
  `ui://tavotto/canvas/v1.html` 经 `resources/read` 交给 host，widget 与
  引擎的一切往来走 MCP `tools/call`。它**从不**打开浏览器、从不唤起桌面
  窗口——那两样不是内嵌画布的替代品，插件也绝不会拿它们冒充。
* **桌面窗口**只在你明确要求交接时出现（`tavotto open` / 技能的
  handoff）。它有自己的就绪判据与失败上报（见
  [`../docs/handoff-protocol.md`](../docs/handoff-protocol.md)），
  桌面进程崩溃时你会拿到 `launch_failed` + 信号/日志路径，不会拿到假成功。

## 插件自己的更新

Codex 不会替插件检查更新，所以插件自己查：每 24 小时最多一次，1.5 秒超时，
网络不通就用上次的答案、不报错也不拖慢出图。有新版时交接结果里多一个
`update` 字段，同时往 stderr 写一句人话——**stdout 永远只有那一行 JSON**。

**只提醒，不下载、不安装。** 看到提醒后自己执行：

```bash
codex plugin marketplace upgrade tavotto   # 然后重载 Codex
```

显式查一次（忽略缓存）：

```bash
python3 skills/tavotto-figure/scripts/update_check.py --json --force
```

两个开关：`TAVOTTO_UPDATE_URL`（换清单地址，自建分发/内网镜像用）、
`TAVOTTO_DISABLE_UPDATE_CHECK=1`（完全关掉，一个包都不发）。

## 怎么用

### 打开画布

让 Codex 调 `tavotto_open_figure`，给它一个图库目录、脚本或产物路径：

```
用 Tavotto 打开 figures/Fig1_kinetics.pdf
```

支持 UI 的 host 会开出一块全屏画布（复杂编辑放不进 inline 卡片）；不支持的 host
（Codex CLI 等）则拿到同一份结构化数据，接着用工具改图。

### 改图

画布里直接拖、直接改：图例位置、图内文字（内容/字号/字体/颜色/粗细/位置）、
线宽颜色线型 marker、刻度朝向与字号、子图位置大小、箭头端点、多图拼版与 (a)(b) 标签、
对齐分布。每一次改动都会调 `tavotto_apply_overrides`，画布用**服务端返回的**
manifest/SVG 更新——iframe 里不保存业务状态。

不用画布也可以，直接让 Codex 调：

```
把图例挪到右下角，刻度改成朝内，线宽统一到 0.75pt
```

> **patches 是全量列表语义**：每次都发完整的一份，列表里没有的 `(gid, prop)` 会自动
> 恢复成脚本原始值。这也是撤销的基础，**不要发增量**。

### 跑预检

`tavotto_preflight` 按出版规范体检：页面宽度与比例、**最终有效字号**（按面板缩放折算，
不是原始 matplotlib 字号）、字体与中文 fallback、刻度朝向、封闭坐标轴、图例边框、
线宽档位、色系、位图 DPI、越界与重叠、隐藏对象、过期渲染、未应用的 override。

结果分四档：

| 等级 | 含义 |
| --- | --- |
| `errors` | **默认阻止导出**；要带着它导出必须用户明确要求 |
| `warnings` | 放行，但会摆出来 |
| `not_verifiable` | **我们查不了**（如外部位图内部的文字字号），需要人工确认；写进 proof |
| `suggestions` | 建议（误差棒、置信带、色系语义这类，绝不替你裁决） |

### 导出

`tavotto_export` **先跑一遍预检**，有 `errors` 且没有 `explicit_confirm` 时一张图都不出。
PDF/SVG 是真矢量，PNG 按给定 dpi 栅格化，同时写一份 proof report（规范身份 + 全部检查
结果 + 是否强制导出）。缺省落在 `<项目>/tavottofile/export/`——与 Tavotto 画布导出同一个
目录规则。

### 想彻底确认

`tavotto_verify_replay` 起一个一次性 worker 从零重放同一组 patches，把两份 manifest
逐元素比几何。它回答的是「你现在看到的 == 重开这张图会得到的」。会重跑一遍脚本
（heavy 的图是分钟级），所以不是每次都要。

### 回到 Tavotto 桌面窗口

多图拼版、画布标注、版本历史、写回原始文件这些留在 Tavotto 本体。跑技能自带的：

```
python3 skills/tavotto-figure/scripts/handoff.py figures/fig_removal_rate.py
```

## 哪些改动必须回 Python 代码

鼠标改的是 artist 属性；**需要重建图形结构的一律回代码**：

* 数据本身、单位换算、拟合、筛选
* 坐标范围与刻度尺度（`set_xlim/ylim`、对数坐标、刻度定位器）
* 加/删曲线、加/删子图、`sharex` 关系、colorbar 方向
* `annotate()` 的箭头端点（注释机制每帧重定位，拖完下一帧就弹回）

完整清单见 [`skills/tavotto-figure/references/compatibility.md`](skills/tavotto-figure/references/compatibility.md)。
画布遇到给不出结构化控件的属性时会**明说「这条得回代码改」**，不会造一个点了没用的开关。

## 出版规范 profile

规范是一份**版本化的 JSON**，Python 与 TypeScript 读的是同一个文件：
`src/tavotto/profiles/publication.json`（随 Tavotto 的 wheel 分发）。

默认 `lab-publication-v1`：

| 项 | 值 |
| --- | --- |
| 单栏 / 双栏宽 | 80 mm / 150 mm（容差 0.5 mm） |
| 允许比例 | 16:9、4:3、1:1 |
| 默认字号 | 9 pt |
| 最终有效字号下限 | **8.5 pt**（严格）；**必须大于 8 pt**（绝对下限） |
| 位图最低 DPI | 300 |
| 矢量格式 | PDF、SVG |
| 拉丁字体 | Times New Roman（+ 明确的中文 fallback） |
| 线宽档位 | 0.5 / 0.75 / 1.0 / 1.5 pt |
| 坐标轴 | 封闭、刻度朝内、主刻度为主、标题写成 `Title (unit)` |
| 图例 | 无边框 |
| 色系 | Scientific colour maps，按 sequential / diverging / categorical 语义选 |

**期刊自定义尺寸**不用新建 profile，给一份覆盖即可（只覆盖点名的键，其余继承）：

```json
{ "profile_id": "lab-publication-v1",
  "journal": { "widths_mm": { "double": 178 } } }
```

`tavotto_open_figure` / `tavotto_preflight` / `tavotto_export` 都接 `profile_id` 与
`journal`。覆盖结果会写进 proof report——「这张图按哪套规矩过的检」必须能读回来。

要整套换掉（企业/期刊自带一份规范文件）：设 `TAVOTTO_PROFILES_FILE` 指向你的 JSON。

## 结构

```
codex-plugin/
├── .codex-plugin/plugin.json          # 插件清单（Codex 认的唯一入口）
├── .mcp.json                          # MCP server 声明（本地 stdio）
├── assets/tavotto.svg                 # composer 图标 / logo
├── mcp/
│   ├── server.py                      # 启动器：找到装着 tavotto 的解释器再交棒
│   ├── tavotto_mcp/                   # 协议 + 引擎桥（纯标准库 + tavotto 本体）
│   │   ├── rpc.py                     #   JSON-RPC over stdio（stdout 归协议独占）
│   │   ├── server.py                  #   initialize / tools / resources
│   │   ├── bridge.py                  #   会话、路径范围、渲染、预检、导出
│   │   └── widget.py                  #   ui:// 资源
│   └── widget/canvas.html             # 内嵌画布（构建产物，见下）
└── skills/tavotto-figure/
    ├── SKILL.md                       # 约定 + 模板 + 交接
    ├── agents/openai.yaml             # 显示名与默认提示
    ├── references/compatibility.md    # 能鼠标改什么 / 必须回代码改什么
    └── scripts/handoff.py             # 登记 →（必要时）跑脚本 → 唤起 Tavotto
```

交接的真正实现在 Tavotto 主体里（`tavotto open`，见
`src/tavotto/engine/handoff.py`）：路径解析、注册表合并、唤起桌面还是浏览器
全在那边裁决，插件不做第二套判断。

插件里唯一的一处「判断」是**怎么找到那条命令行**（上面那六步）。它是
`src/tavotto/engine/locate.py` 的镜像——插件跑在用户机器上，import 不到
tavotto，这份重复无法避免；能避免的是两边悄悄漂开，所以
`tests/test_install_locate.py::test_plugin_mirrors_the_locator` 在一整张环境
矩阵上逐条比对两侧的输出。改一边必须同步另一边。

## 本地开发

```bash
# 画布（改了 web/src 就得重跑；CI 有门禁盯着产物是否同步）
python scripts/build_mcp_widget.py
python scripts/build_mcp_widget.py --check

# MCP server 自检（不连 host 也能看到它是活的）
python codex-plugin/mcp/server.py --self-check

# 协议 + 真链路
.venv/bin/python -m pytest tests/test_mcp_server.py tests/test_mcp_roundtrip.py
```

环境变量：

| 变量 | 作用 |
| --- | --- |
| `TAVOTTO_MCP_ROOTS` | 允许打开的项目根（`os.pathsep` 分隔）。**越界一律拒** |
| `TAVOTTO_MCP_WORKSPACE` | 没设 `TAVOTTO_MCP_ROOTS` 时的工作区目录（宿主的 `CODEX_WORKSPACE_ROOT` 等同样认） |
| `TAVOTTO_CLI` | 指向 tavotto 可执行文件（启动器据此找解释器） |
| `TAVOTTO_MCP_WIDGET` | 指向另一份画布 HTML（边改边试） |
| `TAVOTTO_PROFILES_FILE` | 指向另一份出版规范 JSON |

**允许打开的目录怎么定**（按顺序，第一个命中就用它）：`TAVOTTO_MCP_ROOTS` →
宿主传过来的工作区变量（`TAVOTTO_MCP_WORKSPACE` / `CODEX_WORKSPACE_ROOT` /
`CODEX_PROJECT_ROOT` / `CODEX_WORKSPACE_DIR`）→ 进程 cwd，**且它不在插件包
自己的目录里**。装好的插件跑起来时 cwd 正是插件目录（`./mcp/server.py` 要靠
它解析），拿它当边界的话用户工作区里的每张图都会被判成越界。一个都拿不到时
报 `no_workspace_root` 并直说要设哪个变量——不静默放行，也不静默拒绝。

## 已知限制

**Windows 上 `.mcp.json` 里的 `command: python3` 可能不存在。** 官方安装器
装出来的是 `python.exe`，`python3.exe` 只是 Microsoft Store 的执行别名存根
（而 macOS 12.3 起没有 `python`，两边没有一个通用的名字）。清单的字段形状取自
Codex 官方插件装出来的那份，里面没有按平台分支的写法，我们**不猜**——猜错的
下场是清单不合法、插件整个装不上。症状是「插件装上了，但一个工具都看不见」；
对策是把 `.mcp.json` 的 `command` 改成你那个解释器的绝对路径。

（`pipx install tavotto` 那条已经好了：启动器会去读 Windows console script
`.exe` 里嵌着的 shebang，找到 pipx venv 的解释器。）

## 尚未验证的部分

**MCP App 画布在真实 Codex Desktop 里的 iframe 渲染没有实测过。** 已经验证的是：

* MCP 协议层（`initialize` / `tools/list` / `tools/call` / `resources/list` /
  `resources/read`）在真 stdio 上跑通，见 `tests/test_mcp_roundtrip.py`；
* 六个工具在**没有 UI** 的情况下能走完 打开 → 改图 → 预检 → 导出 → 关闭；
* 画布逻辑（会话灌入、拖动→override→manifest 更新、错误透出、不建第二套状态）
  在 vitest 里有用例，见 `web/src/mcp/session.test.ts`；
* 资源声明形状（`text/html;profile=mcp-app`、`_meta.ui.resourceUri`、空 CSP）有断言。

没验证的是「Codex 真的把这块 HTML 塞进 iframe 并跑起来、握手成功、拖动能回到 server」
这一整条。装上插件后如果画布不出现，工具照常可用（open/apply 的返回里会带
`canvas_ui` 说明画布为什么没出现）——那正是 fallback 的设计目的。

技术细节与取舍见 [ADR 0006](../docs/adr/0006-codex-mcp-app-and-publication-profile.md)。
交接那条路（`tavotto open`）见 [ADR 0005](../docs/adr/0005-external-handoff-and-codex-plugin.md)。
发进官方插件目录的路线与缺口清单在
[`../docs/codex-plugin-distribution.md`](../docs/codex-plugin-distribution.md)。
