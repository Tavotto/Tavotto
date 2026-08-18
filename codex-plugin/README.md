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
