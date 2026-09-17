# 匿名用量统计（2026-08-20，默认关闭）

> 原文出自 `src/tavotto/AGENTS.md`「匿名用量统计（2026-08-20，默认关闭）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

事件契约与指标定义在 `docs/analytics/`（`telemetry-events.md` / `yc-metrics.md` /
`yc-dashboard.json`），隐私承诺在 `docs/privacy.md`，代理部署在
`services/telemetry_proxy/README.md`。改动前先读。

- **`engine/telemetry.py` 纯标准库**（Flask 父进程 import，边界同 updater）。
  **不许引入 posthog / requests / httpx / 任何分析 SDK**——为一个「失败无所谓」
  的可选功能，让主进程多出一条起不来的可能性，这笔账划不来。worker 子进程
  对遥测**一无所知**，一行都别加进去。
- **同意是三档**（`unset` / `enabled` / `disabled`）。「没设置」不是同意：
  unset 时一个字节不发、**连 install_id 都不生成**。`ever_enabled` 让「关掉
  再打开」不再重发 telemetry_enabled——那不是一个新用户。改采集范围要同时
  升 `CONSENT_VERSION` 并退回 unset 重新问：当初同意的不是这一版。
- **`TAVOTTO_NO_TELEMETRY=1` 是硬开关**，压过任何已保存的同意，并且**不弹**
  首启询问。它与 `TAVOTTO_NO_UPDATE_CHECK` 是**两个独立开关**，谁都不代管
  对方（合成一个 = 想关用量统计就得连安全更新提醒一起关）。conftest 把它钉成
  1、四条 workflow 的顶层 env 也钉成 1、smoke/bench 脚本各自注入——**CI 绝不
  产生真实的产品事件**，一台每天跑几十次的机器足以让「有多少人在用」失真。
- **distinct_id 是本机随机 UUIDv4**，不从任何机器信息推导（没有 MAC、
  machine GUID、主机名、用户名）。它是**假名不是身份**，所以指标文档里一律
  写 "opted-in anonymous install"，绝不写 user。诊断包按键名 + **按值**两道
  抹掉它（`_PSEUDONYM_KEYS` + `_redact_text`）；开关状态不抹，那对排障有用。
- **白名单是结构性防线，不是自觉**：`EVENTS` 表里没有任何一个属性接受
  dict / list，值只能是 bool / 有界 int / 短枚举 / 受控版本串。文件名、路径、
  脚本、提示词、图内文字**在结构上就发不出去**。客户端与代理各有一份表，
  靠 `test_client_and_proxy_contracts_match` 逐条对拍（与 patchspec ↔ Rust
  同一套纪律）——**刻意不做 schema 编译器**，两张显式的表加一条对拍用例
  比一套机制好读也更难错。
- **`capture()` 永不抛、永不阻塞**：有界内存队列（满了丢）+ 一条懒起的守护
  线程 + 2–4 秒超时。**没有落盘队列**——能把用户几周前的行为攒起来择机上传的
  队列，与「本地优先」是冲突的，还必然在磁盘上留一份行为记录。断网 = 丢事件，
  这是自觉的取舍。
- **成功边界埋点，不是点击埋点**：`export_completed` 在 `/api/export` 文件
  全部写完之后、正常响应之前（服务端）；`ai_assistant_invoked` 在
  `engine_ai.run()` 真的回了 session 之后（服务端）；pip/pipx 的
  `update_completed` 在升级命令成功之后。前端记的是「点了」，点了还会失败。
- **编辑埋点只有一个调用点**：`documentStore.pushHistory`（commit 与 endTxn
  的共同漏斗）。一次拖动 = 一条事务 = 一条历史 = **一个事件**，不是 120 次
  pointermove；预览平面不 commit，因此天然不产生事件。`edit_kind` 由**历史
  标签的 key**（开发者写死的稳定标识）经闭表映射，查不到落 `other`——
  标签**文案**绝不能用，它被翻译过、还插值了用户的文件名与属性名。
- **CONSENT_VERSION 现在是 2**（2026-09-02，Session 22 加了九条：刷新 / 接入状态 /
  教程三条 / 多选栏 / 保存 / 恢复 / 包操作）。再加事件仍照这条纪律：两侧 `EVENTS`
  表都登记、`tests/test_telemetry_proxy.py` 的样例表补一条、文档三处（events /
  privacy / README）一起改、范围扩大就再升版。`package_action` **没有包名**、
  `project_refresh_completed` 只发桶不发条数、教程只发 step id 与流程版本——
  「能不能带这个字段」的判据是「它能不能承载用户内容」，不是「方不方便分析」。
- **前端只发服务端推断不出来的那几条**，经 `/api/telemetry/event`，
  校验走**同一份** `engine/telemetry.validate`。`web/src/lib/telemetry.ts` 只缓存
  「现在发不发」+ 转发 + 分类，同意态与 install_id 一律不进前端
  （`public_settings()` 是唯一出口，**不含 install_id**）。
- **内嵌 Codex 画布不发遥测**：widget 打包了同一份前端代码，但没人调
  `setTelemetryEnabled`，`captureTelemetry` 是 no-op。它没有 sidecar 会话、
  也没有自己的同意上下文，继承一个只会让人意外——这是决定，不是疏漏。
- **代理 `services/telemetry_proxy/` 不进 wheel/sdist**（pyproject 的 exclude），
  也不给 Tavotto 加任何运行时依赖。应用里**没有也不该有** PostHog 密钥或地址
  （`test_no_posthog_key_or_direct_host_anywhere_in_the_client` 看护）：开源桌面
  应用里嵌的东西一律是公开的。提供商特有的 JSON 只在 `posthog.py` 一个文件里
  （与 `pdfbackend/` 同一条边界思路）。
- **发行量与用户是两回事**：`scripts/collect_distribution_metrics.py` 从 CI 发
  `github_release_asset_snapshot` / `pypi_daily_downloads` / `github_repo_snapshot`，
  distinct_id 是常量 `distribution_metrics`，**绝不混进用户队列**。GitHub 的
  `download_count` 是**累计计数器**，发的是快照、区间量靠做差；资产身份是
  `asset_id` 不是文件名（资产会被删掉重传）。**更新包与签名不算安装量**
  （`Tavotto.app.tar.gz` / `*-setup.nsis.zip` / `latest.json` 全是更新器自己拉的，
  算进去会让「装过的人」随老用户升级不断膨胀）。分类规则从真实发布工作流推出，
  不靠「.exe 就是安装包」的直觉。**桌面遥测丢事件必须无声，采集器丢数据必须
  有人看见**——所以采集器失败就让 workflow 红。
- **部署顺序**：先发代理（它拒绝不认识的事件与 schema_version）→ 验 PostHog
  收得到 → 配采集器 → 再发客户端。反过来的话新事件会被静默 400，而且全绿。
- 验证：`tests/test_telemetry.py`、`tests/test_telemetry_api.py`、
  `tests/test_export_endpoint.py` 末节、`tests/test_telemetry_proxy.py`、
  `tests/test_distribution_metrics.py`、`web` 的 `lib/telemetry.test.ts` /
  `store/telemetryStore.test.ts` / `components/TelemetryConsentDialog.test.tsx` /
  `components/SettingsTelemetry.test.tsx`。
