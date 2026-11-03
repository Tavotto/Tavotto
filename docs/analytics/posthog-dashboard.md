# PostHog 主看板、告警与周报订阅（离线定义）

> **线上未创建。** 本文与 [`posthog-manifest.json`](posthog-manifest.json) 是在没有 PostHog 访问的会话里写的
> 离线定义：没有调用任何 PostHog 接口，没有创建或修改任何看板、insight、告警或订阅。manifest 里所有
> `remote_id` / `url` 为 `null`，`status` 为 `not_created` 或 `pending_event`。查询形状按 PostHog 公开的
> 查询 schema 手写，**没有在线上校验过**；第一次落地时以线上报错和样本核对为准（见下文「手工落地步骤」）。

配套文件：

* [`telemetry-events.md`](telemetry-events.md) —— 事件契约（真相是 `src/tavotto/engine/telemetry.py` 的 `EVENTS`）
* [`yc-metrics.md`](yc-metrics.md) / [`yc-dashboard.json`](yc-dashboard.json) —— 受治理的指标口径（28 项历史定义）
* [`tests/test_posthog_manifest.py`](../../tests/test_posthog_manifest.py) —— manifest 的结构门禁

## 目标项目与已知状态

项目 567339（Default project，时区 UTC，`https://us.posthog.com/project/567339/`）。以下是 2026-10-08 的**只读观察**
（来自恢复与诊断 Prompt Pack §6.1），不是本会话核对的结果，落地时要重新核对组织、项目、时区、授权：

* 只有 Your starter dashboard（ID 2014147）和 8 个默认 insight，用的是 `$pageview` / `$autocapture`，与 Tavotto
  自定义事件对不上。**本方案不碰它们**，是否归档由负责人定。
* 普通 Alerts / Subscriptions 数量为 0；没有 Feature Flags、Surveys、Experiments；Session recording 未开。
* 账单未验证（缺 billing:read），所以「免费」只是公开政策，不是此账号的实测。

## 主看板：四组、20 张图

逻辑名即 manifest 的唯一键。「无数据」表示依赖的 `export_result` 在契约里，但 v3 客户端发布前线上没有（见后文）。

### 使用与激活

| 逻辑名 | 回答什么 | 查询 |
| --- | --- | --- |
| `a1_started_installs_weekly` | 滚动 7 日有多少安装发过 `app_started`（也是断流告警的数据源） | Trends |
| `a2_engaged_installs_weekly` | 每个 UTC 自然周有多少安装做了有效使用 | HogQL |
| `a3_weekly_successful_exporters` | 滚动 7 日有多少安装有 `export_completed`（受治理指标 WSE） | Trends |
| `a4_activation_direct_path` | `app_started → export_completed`，7 日；含不经图内编辑的直接布局/导出路径 | Funnel |
| `a5_activation_deep_edit_path` | `app_started → figure_opened → figure_edit_completed → export_completed`，7 日 | Funnel |
| `a6_observed_installs_ci_sensitivity` | 新观察到的安装数，含/不含疑似 CI 两列对照 | HogQL |

有效使用事件集合沿用 `yc-dashboard.json` 的 `engaged_events`；`app_started` 单独不算。

### 关键操作健康

| 逻辑名 | 回答什么 | 状态 |
| --- | --- | --- |
| `b1_export_completed_by_version` | `export_completed` 次数与安装数按版本；**不是失败率** | 可建 |
| `b2_export_result_outcomes` | 导出终态 done / partial / failed / cancelled 分布 | 无数据（待 v3 客户端） |
| `b3_export_done_ratio` | done 占全部终态的比例（A/B） | 无数据（待 v3 客户端） |
| `b4_export_failed_installs_by_version` | 导出失败的受影响安装，按版本 | 无数据（待 v3 客户端） |
| `b5_document_saved_outcomes` | 保存 ok / conflict / failed × 手动 / 自动，次数 | 可建 |
| `b6_document_saved_affected_installs` | 保存失败、冲突各影响多少安装，对照有保存事件的安装数 | 可建 |

两个不能用错的口径：

* `export_completed` 在 **done 或 partial** 时发送，`pdf` / `png` 表示该格式真的写出。它**不等于「请求的文件全部成功」**，
  也没有 failed / cancelled，所以现有数据**推不出导出失败率**。`b1` 是当前数据下的回退，只能看次数和安装数趋势。
* `document_saved` 的 `conflict` 与 `failed` 分开看；自动保存和重试会放大次数，所以 `b5`（次数）与 `b6`（受影响安装）一起读。
  `package_action` 的失败只在 `d3` 里作为版本相关故障出现，不单独成图。

### 引导与回访

| 逻辑名 | 回答什么 |
| --- | --- |
| `c1_tutorial_funnel_by_version` | `tutorial_started → tutorial_completed`，按 `tutorial_version` 拆分 |
| `c2_tutorial_steps_by_step_id` | 当前版本（`tutorial_version = 3`，对应 `web/src/store/onboardingStore.ts` 的 `ONBOARDING_FLOW_VERSION`）各步骤到达的安装数，用来看跳步/流失 |
| `c3_export_return_weekly` | 首次导出后每周再导出（「完成有用操作的留存」，W1–W4） |
| `c4_engaged_d7_return` | 启动队列的第 7 天（精确日）有效使用回访 |

教程跳过的步骤不发事件，`tutorial_completed` 也不证明每一步都做过，所以 `c1` 到达完成的比例和 `c2` 的步骤数要对照看；
换 `tutorial_version` 后改 `c2` 的过滤值，旧版本不并入新版本。启动回访与有用操作回访分开命名：`c4` 的队列是启动，回访是有效使用；`c3` 的队列和回访都是导出。

### 版本与分发

| 逻辑名 | 回答什么 |
| --- | --- |
| `d1_version_adoption` | 每周启动的安装按版本堆叠（`app_version` 不是 build 标识） |
| `d2_platform_distribution_mix` | 近 30 日有效使用安装的平台 × 分发方式构成 |
| `d3_version_received_faults` | 各版本收到的保存失败 / 冲突 / 包操作失败的受影响安装，带样本是否足够的标记 |
| `d4_installer_downloads_30d` | 桌面安装包 30 日下载（公开分发计数，**不是安装、不是用户**，与上面所有人群分开） |

PyPI、wheel/sdist、更新轮询等其余下载口径仍留在 `yc-dashboard.json`，不进主看板：它们不影响本看板要做的决策，
而且 PyPI 混有 CI 和镜像之外的自动化。

## 与受治理指标的对账

| 受治理 id | 处理 |
| --- | --- |
| `weekly_successful_exporters` | 复用，`a3`；口径不变 |
| `activation_funnel` | 复用并拆成 `a4`（直接）/ `a5`（深度编辑）两条路径 |
| `activation_rate_7d` | 部分复用：类型化漏斗不是严格的「首次观察」队列，`a4` 的说明里写明；严格口径仍是 yc-metrics E 节 |
| `engaged_wau` | **改了定义**，`a2`：由滚动 7 日改为 UTC 自然周且不含未结束周。原因：类型化 Trends 无法对多个事件做去重并集，HogQL 做滚动 WAU 要逐日展开 7 日窗口，且滚动值最后一点含不完整窗口。两者数值不可直接对比 |
| `retention_engagement` | **改了定义**，`c4`：固定为精确第 7 个 UTC 日并按周聚合队列，且只纳入 D7 当日已过完的队列（yc-metrics G1 没规定精确日还是滚动窗口）。D1 / D30 不进最小集合 |
| `retention_core_value` | 复用，`c3` |
| `platform_mix` | 复用并加 `distribution` 维度，`d2` |
| `github_installer_downloads_30d` | 复用，`d4`。MAX 聚合对重复上报幂等，所以未再按 `snapshot_key` 去重，与原口径等价 |
| `opted_in_install` 排除规则 | 复用，见下文纪律 5；新增 `a6` 做敏感性对照 |
| `wse_wow_growth`、`stickiness`、`activation_rate_24h`、`time_to_first_export`、`export_intensity_*`、`ai_adoption`、`preflight_clean_rate`、`engaged_mau`、`github_*_downloads_*`、`pypi_downloads_30d`、`github_stars` | **不进主看板**。它们是投资人汇总卡或次要深度指标，仍在 `yc-dashboard.json` 里受治理；`preflight_clean_rate` 的 `errors` 还是图表检查结果而不是运行时异常，放进「关键操作健康」会被误读 |

`yc-metrics.md` 里 “product counts are a lower bound” 一类说法对**计数**成立的程度有限，对**比例**则可能有偏
（观察范围由是否同意、能否送达决定，方向未知）。本方案在比例类图里写「可能有偏，方向未知，不称保守下界」。
`yc-metrics.md` 本次没有改动，措辞对齐留给口径负责人决定。

## 分析纪律（Prompt Pack §6.5 十条）如何落实

1. **distinct_id 是匿名安装，不是人**：所有图的人群叫 opted-in anonymous install；「首次观察」不是首次安装。
2. **明确时区、窗口、分母、排除、起点**：每张 insight 在 manifest 里必填 `timezone`（UTC）、`window`、`denominator`、`exclusions`、`data_start`；
   D7 用精确日并只含已成熟队列（`c4`）；类型化漏斗的起点日截止到「今天 −7 日」（`a4` / `a5` / `c1`）。
3. **启动与有用操作分开命名**：`c4`（启动队列、有效使用回访）与 `c3`（导出回访），并给周回访。
4. **排除 `distribution_metrics`**：产品人群的 HogQL 显式排除；发行量事件只在 `d4`，人群标成 `distribution_metrics`，测试禁止与产品事件混在一张图。
5. **疑似 CI**：沿用 `yc-dashboard.json` 的 `ci_phantom_installs`——全历史（不是窗口内）只有一次 `telemetry_enabled` 且 `distribution = 'source'`。
   这条规则没有来源证据，真实的退出用户或丢事件的 source 用户也会命中，所以一律称**疑似 CI**。不删除原始数据，不排除所有 source 用户；
   `a6` 同时给出含/不含两列。类型化查询的起点不是 `telemetry_enabled`，疑似 CI 安装天然不进入。#440 修复的合入日见 `yc-metrics.md`，历史数量不是实时数量。
6. **优先类型化查询**：20 张里 14 张是 TrendsQuery / FunnelsQuery / RetentionQuery；6 张 HogQL 都是类型化查询表达不了的——
   多事件去重并集（`a2`、`d2`）、全历史排除（`a2` `a6` `c4` `d2` `d3`）、精确日 D7 与跨表联结（`c4`）、资产级差值聚合（`d4`）。
7. **缺失、未知、新协议前单列**：无数据的区间显示「无数据」；`partial` 不并进 done；`export_result` 上线前整段无数据，不是零失败。
8. **opt-in 偏差措辞**：见上文，比例类图不写「保守下界」；大陆网络可达、慢网络可能少报（2026-10-20 实测，见 yc-metrics.md）；macOS desktop v0.16.0 及更早无事件（#439）。
9. **`app_version` 不是 build 标识**：同一版本号可能对应多个未发布提交；`b1` `b4` `d1` `d3` 都在说明里写明。
10. **每张图必填字段**：名称、描述、事件/属性、过滤、窗口、分母、数据起点、缺失说明；`tests/test_posthog_manifest.py` 逐张检查。

另外几条 §6.4 的陷阱已写进对应图的说明：`preflight_completed.errors` 是图表检查结果；`app_started` 不是 UI 就绪或 crash-free；
统计经 sidecar 转发，sidecar 挂掉时连错误事件也发不出，「没有错误事件」不能证明没有 P0。

**数据起点怎么核**：manifest 的 `data_start_value` 全是 `null`、`data_start_verified` 全是 `false`，因为本会话无法查线上。落地时对每个事件跑一次
`SELECT min(timestamp) FROM events WHERE event = '<事件>'` 填回去。CONSENT_VERSION 2 引入的事件（`document_saved`、`package_action`、`tutorial_*`、`recovery_action` 等）
首条时间取决于带该事件的客户端何时发布，不要用同意版本的文档日期代替。

## `export_result`：随 #1103 / CONSENT_VERSION 3 进入事件契约，线上仍无数据

`export_result` 在 #1103 合入后就在 `telemetry.EVENTS` 里，所以 manifest 不再把它登记为 `pending_events`（门禁
`test_pending_status_is_only_for_pending_events` 要求：事件在 EVENTS 里，依赖它的图就不能再标 `pending_event`），
`b2` `b3` `b4` 和对应两个告警的 `status` 为 `not_created`。

**但它在线上没有数据**，直到带 `CONSENT_VERSION` 3 的客户端发布、且用户重新同意（v2 的同意不授权它，ADR 0134）。
所以这几张图在周报里写「尚无数据」，不是 0；上线顺序：代理先于客户端部署 → 客户端发布 → 线上看到第一条 `export_result` →
复核属性表与线上一致。当前数据下的回退是 `b1`（`export_completed`）：它没有 failed / cancelled，也不是「全部成功」，不要用它推失败率。

## 告警草稿（4 个，全部禁用）

PostHog 的普通告警只能挂在 **Trends insight** 上，所以每个告警的数据源是 manifest 里同名的 Trends insight，查询就是它的 `query`。
阈值是**建议值，待真实基线校准，不是 SLO**。普通告警没有最小样本门槛，样本要求靠人工先看分母再判断，不据单次触发宣布 P0。
渠道和收件人一律 `UNSET`；每个告警启用后各占 1 个免费名额（4 个草稿，名额上限按 5 个算；线上实际名额待核）。

| 逻辑名 | 数据源（序列） | 条件 | 最小样本（人工） | 建议阈值 | 状态 |
| --- | --- | --- | --- | --- | --- |
| `alert_export_done_ratio_drop` | `b3`（A/B） | 上一个完整周 done 占比低于阈值 | 终态事件 ≥ 30，安装 ≥ 10 | 0.8 | 待 `export_result` 数据 |
| `alert_export_failed_installs_by_version` | `b4` | 某版本一周内失败安装数高于阈值 | 该版本安装 ≥ 20 | 3 | 待 `export_result` 数据 |
| `alert_document_save_failed_installs` | `b6` 序列 B | 7 日内保存失败的唯一安装数高于阈值 | 序列 A ≥ 20 | 3 | 可建 |
| `alert_ingest_silence` | `a1` | 7 日启动安装数低于 1 | 不适用 | 1 | 可建 |

每个告警在 manifest 里还写明了恢复/去重、观察范围、已知丢事件偏差、频率（daily）、时区（UTC）。
`alert_ingest_silence` 表示「收不到数据」，不是「没故障」，断流要先查代理和采集器 workflow。

## 周报订阅草稿（1 个，禁用）

`weekly_key_ops_version_report`：每周一，建议 01:00 UTC（北京时间 09:00），收件人和渠道 `UNSET`，不使用 PostHog AI 摘要。
PostHog 订阅按看板或单个 insight 发送，无法在看板里选子集，所以草稿的目标是整块主看板（20 张）；只发关键操作与版本两组需要另建一块只含这两组的看板（本次未做）。
预览的内容构成写在 manifest 的 `preview`，实际渲染图和数字没有生成（NOT_RUN）。`export_result` 上线前相关三张图在周报里应写「尚无数据」，不是 0。

## 手工落地步骤

前提：有项目 567339 的编辑授权；确认收件人和发送授权后才启用告警与订阅。

1. **核对环境**：确认组织、项目 567339、时区 UTC；不动 starter dashboard 2014147。
2. **核对数据**：在 SQL 编辑器里抽 5 条真实事件看字段与值（`app_version`、`platform`、`outcome` 等），对每个事件跑数据起点查询并回填 manifest。
   对照 `a3` 与手写 HogQL `count(DISTINCT distinct_id)` 的结果，核对类型化 schema 是否被线上接受。
3. **建看板**：新建「Tavotto 主看板（v2）」，打标签 `tavotto-manifest:tavotto-main-v2`。
4. **逐张建 insight**（按 manifest 里的分组顺序，`pending_event` 的先跳过）：
   * 类型化查询（Trends / Funnel / Retention）：新建 insight → 查询编辑器里选 JSON/源码视图 → 粘贴 manifest 的 `query`；schema 报错时以线上为准，改后回写 manifest 并把 `version` 加 1。
   * HogQL：新建 SQL insight → 粘贴 `query.query`。
   * 名称取 `title`，描述取 `description`，再把 `window`、`denominator`、`data_start`、`missing_data_note` 追加进描述；打标签 `tavotto-manifest:<logical_name>`；加入主看板。
   * 创建后立即把 `remote_id` 与 `url` 回填 manifest，`status` 改为 `created`。
5. **目视检查**：无数据的区间必须显示「无数据」；`a4` `a5` `c1` 的起点区间是否截止到「今天 −7 日」；`c3` 不读平均行。
6. **告警**：先观察至少几周基线，再用真实基线改写阈值（改后去掉「建议值」标注并说明依据）；选渠道和收件人；最后才启用。
7. **订阅**：确认收件人与发送授权后再启用；先发一次预览给自己核对，不要用真实用户邮箱试发。
8. **待事件**：#1103 解锁后按上文顺序建 `b2` `b3` `b4` 与两个告警。

### 重复同步规则

以 `logical_name` 为唯一键。有 `remote_id` 就更新那个资产；`remote_id` 为 `null` 时先按标签 `tavotto-manifest:<logical_name>` 在项目里查找，
找到就认领并回填，找不到才创建，创建后立刻回填 `remote_id` 与 `url`。**绝不按标题新建副本，绝不删除他人资产**；发现同一 `logical_name` 有多个远端资产时停下来人工处理。
`version` 递增表示定义变更。

## 验证

`tests/test_posthog_manifest.py` 守结构：引用的事件在 `telemetry.EVENTS` / 发行量契约，或登记为待发布并带 `depends_on` 与回退说明；
属性存在于该事件的白名单、枚举过滤值在取值集合里；纪律字段齐全；告警与订阅禁用且收件人 `UNSET`；逻辑名唯一；没有任何远端 ID。
它不能证明查询在 PostHog 里跑得通或数字正确，那需要线上访问。它还钉着「线上未创建」（`remote_id` 全为 null、`status` 只有 `not_created` / `pending_event`）：真正落地并回填 `remote_id` 的那个 PR 要同步放宽 `test_nothing_is_created_online`，不要为了过测试而不回填。
