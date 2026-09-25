# 改过脚本之后的显式刷新（ADR 0041）

> 2026-09-25 迁自 `codex-plugin/AGENTS.md`「改过脚本之后的显式刷新（2026-09-02，ADR 0041）」（#608：Codex 自动拼接的 32 KiB 上限），
> 正文一字未改。速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**，
> 改规则改这里，并同步那一行。

- **`tavotto_refresh_project` 是第七个工具**，也是模型改完 .py 之后该调的那一步；技能与
  README 里不再写「重开会话 / 手动刷新」让 Tavotto 跟上。实现全在 `bridge.refresh_project()`：
  先探 `127.0.0.1:5089/api/version`，可达就委托运行中的 Tavotto（`/api/projects/open
  default=false` → `/api/project/refresh?pj= reason=codex` → `/api/project/readiness`，带
  `session_client` 的本机凭据，前端当场收到 SSE）；不可达就在本进程调**同一份**
  `engine.project_refresh.refresh_project_index()` + `readiness.compute()`。**两条路都不复制
  discover、不 probe、不跑脚本**；可达但刷新失败原样带回它的 code，不退回本地再试。
- **项目只来自授权**：`session_id`（`get_session` 重新校验范围）→ `project_path`（与
  `tavotto_open_figure` 同一套 `check_scope → resolve_target → check_scope`）→ 唯一有会话的项目；
  零个 `no_project`、多个 `ambiguous_project`（错误里列会话 id，不列路径）。`reason` 固定
  `codex`，模型传什么都不透传。结果里**没有绝对路径**（项目短 id 与 `app._project_id` 同一把尺）。
- **本进程那份刷新状态 `_REFRESH_CTX` 按项目缓存**：第一次如实报 `assets.baseline: true`，
  第二次起才是跨轮 diff。测试的 autouse fixture 要清它，并把 `engine_handoff.http_json_status`
  打成不可达——**用例里绝不真的探 5089**，开发机上很可能真开着一个 Tavotto。
- **桌面版的诚实限制**：sidecar 端口不落盘，这条路对桌面用户总是 `delivered: local`，Tavotto
  里的更新靠它自己的 watcher；工具文字里如实说，不许写成「界面已同步」。
- 降级 server 的 `NORMAL_TOOLS` 与 `_BRIDGE_IMPORT` 探测语句都要跟着 bridge 的 import 走
  （`test_bridge_import_probe_matches_the_bridge` / `test_degraded_refresh_tool_is_a_structured_error_too`）。
- 看护：`tests/test_mcp_server.py` 末节十六条（schema / 授权 / 越界 / 空 diff / 新脚本 / readiness /
  不 probe 不跑脚本 / 不可达 → local / 可达委托 / 可达失败 / no_project / 多项目隔离 / 无绝对路径 /
  reason 固定 / 无注册表）+ `test_mcp_resolver.py` 的降级用例。
