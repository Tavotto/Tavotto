# 用户反馈批次 2026-09-06

来源：产品所有者亲手试用后的一批反馈（原话见各条「反馈」栏）。
处理方式：集成分支 `feat/user-feedback-2026-09-06`，每条反馈由一个独立子 Agent
在各自的 `uf/NN-*` 分支上复现 → 修复 → 反证，再串行合回集成分支。
本文件是这一批的台账：每条反馈的原话、根因、处置、验证与遗留。

状态取值：`待处理` / `已修复` / `部分完成` / `不是缺陷` / `需用户拍板`。

| # | 反馈（原话） | 分支 | 状态 |
| --- | --- | --- | --- |
| 1 | 在新手教学案例中，我双击示例图片，并不能进入图内编辑。 | `uf/01-tutorial-dblclick` | 待处理 |
| 2 | 对于散点图而言，在选中时，仍然是一个很大的矩形框将其包裹，而不是所有散点的圆形轮廓出现被选中蓝色框。 | `uf/02-scatter-outline` | 待处理 |
| 3 | 目前对于图内中文无法正常渲染，需要增加其适配性。 | `uf/03-cjk-fonts` | 待处理 |
| 4 | 导出功能要增加 eps 和 Tiff 格式。 | `uf/04-eps-tiff-export` | 待处理 |
| 5 | 我目前电脑上明明安装了 Tavotto 的 codex 插件，为什么在编码 Agent 里面还是显示插件市场登记失败未登记。 | `uf/05-codex-marketplace` | 已修复 |
| 6 | 导出中的原图尺寸导出还不好用，我目前已经选中了一个原图，但是还是显示「先选中一张图，才能按原图尺寸导出。」我希望这里做的更好一点，可以直接预览目前的几个图片，用户直接点击就可以。 | `uf/06-original-size-picker` | 待处理 |
| 7 | 目前 Tavotto 里面的图标非常不统一，太丑了，参考 morphicons.com 来统一图标。 | `uf/07-icon-unify` | 待处理 |
| 8 | （原话为空，用户没有写完） | — | 待用户补充 |

说明：第 4、6、7 条属于 1.0 收敛纪律里的「扩大产品能力」，由产品所有者明确
要求，按其决定执行；改动范围限定在反馈点本身，不趁机重写相邻模块。

## 逐条记录

（各条由对应子 Agent 的报告整理而来，合回集成分支时补齐。）

### 5. 插件市场「登记失败」——判据没错，问的那个进程跑不起 codex

- **判定**：不是用户没登记。本机 `codex plugin marketplace list --json` 与
  `codex plugin list -m tavotto --json` 都说 tavotto 已登记、已安装、已启用
  （codex-cli 0.151.0，快照仍在 legacy-local 通道）。
- **根因**：`engine/codexinstall.py` 的市场/插件状态探测用裸 `_run([codex, …])`
  起子进程，继承调用方环境。桌面壳从 Finder 启动时 PATH 只有
  `/usr/bin:/bin:/usr/sbin:/sbin`（`ps -Ewwp` 实测），`/opt/homebrew/bin/codex`
  是 `#!/usr/bin/env node` 的 npm shim，子进程里找不到 node → 退出 127 →
  state=unknown → 界面显示「插件市场登记 失败」。`src/tavotto/AGENTS.md`
  早规定 CLI 子进程一律走 `ai_agents.spawn_env()`，这个模块漏了。
- **处置**：8 处 codex 调用收口到 `_codex_run()`，环境用 `spawn_env(codex)`；
  「问不到」两档的 detail 明说「这不等于没登记 / 没装」，认出缺 node 时给处方；
  zh-CN / en-US 两份 `*_state_unknown` 文案同步去掉「多半是 Codex 本身没启动好」。
- **用例**：`tests/test_codex_install_cli.py` +2（最小 PATH 下 npm shim 仍可问到；
  unknown 提示点名缺 node）。反证：env 改回 None → 红；hint 不认 node → 红。
- **验证**：ruff 全过；相关 pytest rc 0；`pnpm test` 2586 全过；`pnpm build` 过；
  修后最小 PATH 下真 codex doctor 7 步全绿。
- **用户侧**：修复合入前，从终端启动桌面版或直接在终端跑 `tavotto codex doctor`
  即为绿。市场快照换 plugin-stable 通道需自己跑
  `codex plugin marketplace upgrade tavotto`，未替用户执行。
- **遗留**：只在 macOS 复现；Windows 快捷方式启动的 PATH 未验。
  `plugin_python()` 在最小 PATH 下取到 `/usr/bin/python3` 的问题不在本条范围。
