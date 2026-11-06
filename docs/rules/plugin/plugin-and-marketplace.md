# 插件本体与市场

> 2026-09-25 迁自 `codex-plugin/AGENTS.md`「插件本体与市场」（#608：Codex 自动拼接的 32 KiB 上限），
> 正文一字未改。速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**，
> 改规则改这里，并同步那一行。
> 看护用例（`codex-plugin/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`tests/test_codex_plugin.py`、`tests/test_install_locate.py`、`tests/test_plugin_update_check.py`
> `codex-plugin/AGENTS.md`「必守要点」压缩前原文（2026-10-24，与正文同等有效）：插件版本 == `tavotto.__version__`；路径规则镜像 `engine/locate.py`、一个 pathlib 都不用；更新检查四条底线；脚本与产物同目录、死图退出码 4；跑脚本的 `MPLCONFIGDIR` 是引擎判据的镜像

<!-- agents-route
sheet: codex-plugin/AGENTS.md
order: 30
改到: `.codex-plugin/plugin.json`、`.agents/plugins/marketplace.json`、路径镜像、`update_check.py`、`scripts/handoff.py`
主题: 插件本体与市场 → `plugin-and-marketplace.md`
必守要点: 插件版本 == `tavotto.__version__`；路径规则镜像 `engine/locate.py`、一个 pathlib 都不用；脚本与产物同目录
-->

- **Codex 插件在 `codex-plugin/`**，市场清单在仓库根 `.agents/plugins/marketplace.json`
  （仓库即市场根）。**已不再是 skills-only**：2026-08-18 起同时带一个本地 stdio
  MCP server 与内嵌画布（2026-09-02 起七个工具，含 `tavotto_refresh_project`；2026-09-13 起八个，加 `tavotto_normalize_figure`；2026-09-21 起加 `tavotto_session_state`，画布的取件通道）；交接这条路一字未改。**仍不做 `.app.json`**（需要
  OpenAI 侧注册的托管 App id）。pyproject 的 `exclude` 显式挡住 `codex-plugin/`
  进 wheel/sdist。插件版本 == `tavotto.__version__`（`tests/test_codex_plugin.py` 看护）。
- **插件里那份路径规则是 `engine/locate.py` 的镜像**（插件 import 不到 tavotto，
  这份重复无法避免）。能避免的是两边悄悄漂开：
  `tests/test_install_locate.py::test_plugin_mirrors_the_locator` 在
  Windows/macOS/Linux × 有无环境变量 × 空格与中文的矩阵上逐条比对两侧输出，
  改一边必须同步另一边。两侧都**一个 pathlib 都不用**（`Path()` 按 `os.name`
  分派，在 macOS 上连构造一条 Windows 路径都做不到）。
- **插件自己的更新检查在 `codex-plugin/.../scripts/update_check.py`**：
  每 24 小时一次（失败 1 小时后可重试）、1.5 秒超时、缓存落
  `config_dir()/codex-plugin-update.json`（**绝不往插件目录写**——那儿归 Codex
  管、可能只读、升级时整个被换掉）。四条底线：不阻塞出图、**不污染 stdout**
  （调用方读的是最后一行 JSON）、插件和 MCP 进程绝不自动下载或执行（桌面版在用户开着后台自动下载时可以更新已接入的插件，ADR 0170 裁决 6；关闭：后台自动下载开关或 `TAVOTTO_DISABLE_UPDATE_CHECK=1`）、**插件版本 ≠ Tavotto 版本**
  （当前版本只从 plugin.json 读，`min_tavotto_version` 比的是 `tavotto open`
  回报的那个版本）。清单由 `scripts/make_plugin_manifest.py` 在 **release.yml**
  生成。**MCP 服务端也复用它**（`tavotto_mcp/updatenotice.py`）：`tavotto_health` 的结果带 `update`，
  `tavotto_export` 成功结果每个进程最多带一次；调用线程只读缓存（`check(allow_network=False)`，
  不写缓存），到期由 daemon 线程刷新，下次调用才看到；版本用 MCP 进程自己的
  `_version()`（桌面版 `tavotto-cli mcp` 里没有 plugin.json）；`how` = `desktop`（frozen）/
  `codex_plugin`（有 `.codex-plugin/plugin.json`）/ 判不出不给命令。桌面包把整个
  `skills/tavotto-figure` 收进 `<_MEIPASS>/skills/tavotto-figure`（`packaging/tavotto.spec`），找不到
  `update_check.py` 就静默不提示。默认地址在 GitHub Release；后台线程问不到时读国内镜像的 `index.json`
  （`fetch_with_mirror`，读法照 tavotto-infra 的 consumer-contract §2），靠「发版时插件与 Tavotto 同号」拿版本，只提醒不下载。
  交接路径同步跑在出图路上，继续用 `fetch`，1.5 秒不翻倍；用户配了 `TAVOTTO_UPDATE_URL` 不回退。用户关了检查
  （`TAVOTTO_NO_UPDATE_CHECK`、`TAVOTTO_DISABLE_UPDATE_CHECK`、设置里的 `auto_check`）就不起后台线程，只读已有缓存。
  清单——**不能挪进 desktop-tauri.yml 的 updater-manifest**，那个 job 没配
  minisign 私钥就整个跳过，插件的更新通道会跟着悄悄停而且全绿。
- **技能的第一条硬约定：脚本与产物同目录、且必须先落成文件**（禁 `python -c` 出图）
  ——「stem ↔ 产出它的脚本」是图能不能双击进去改的全部依据。自检不靠祈祷：
  `scripts/handoff.py` 读 `tavotto open --json` 的 `registry.parameterizable`，
  为 false 时**退出码 4**。图出来了但只是死图，那不是成功。
- **`handoff.py` 跑脚本不丢用户的 matplotlib 配置**（`script_env`，#733 同 PR）：`MPLCONFIGDIR` 按
  `mplconfigdir_for` 定，是引擎 `runtime._owned_mplconfigdir`（#735）的镜像——用户已有 matplotlib 目录就沿用
  （他的 matplotlibrc / stylelib 决定图长什么样，交接跑出来的必须与终端里一样），没有才指到配置目录下的
  `mpl-cache`；Linux 上配置与缓存两个 XDG 目录分别判断，只有配置目录时用 `mpl-userconfig`（符号链接指回他的配置）。
  `tests/test_codex_plugin.py::test_script_env_mpl_rule_mirrors_the_engine` 逐格对拍两侧。
