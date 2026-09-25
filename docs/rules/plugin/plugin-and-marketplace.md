# 插件本体与市场

> 2026-09-25 迁自 `codex-plugin/AGENTS.md`「插件本体与市场」（#608：Codex 自动拼接的 32 KiB 上限），
> 正文一字未改。速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**，
> 改规则改这里，并同步那一行。

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
  （调用方读的是最后一行 JSON）、不自动下载执行、**插件版本 ≠ Tavotto 版本**
  （当前版本只从 plugin.json 读，`min_tavotto_version` 比的是 `tavotto open`
  回报的那个版本）。清单由 `scripts/make_plugin_manifest.py` 在 **release.yml**
  生成——**不能挪进 desktop-tauri.yml 的 updater-manifest**，那个 job 没配
  minisign 私钥就整个跳过，插件的更新通道会跟着悄悄停而且全绿。
- **技能的第一条硬约定：脚本与产物同目录、且必须先落成文件**（禁 `python -c` 出图）
  ——「stem ↔ 产出它的脚本」是图能不能双击进去改的全部依据。自检不靠祈祷：
  `scripts/handoff.py` 读 `tavotto open --json` 的 `registry.parameterizable`，
  为 false 时**退出码 4**。图出来了但只是死图，那不是成功。
