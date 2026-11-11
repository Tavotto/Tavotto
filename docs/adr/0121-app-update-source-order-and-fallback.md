# ADR 0121：应用内更新的来源顺序与回退——国内 COS 镜像优先（大陆环境），GitHub Release 兜底

日期：2026-10-12（2026-11-04、2026-11-05、2026-11-11 修订）· 状态：**Proposed（实现与测试已在分支内；镜像侧由 `Tavotto/infra` 提供，真实 COS 未验）**
相关：[0002 桌面壳](0002-tauri-desktop-shell.md)「应用内更新」一节、[0076 macOS Intel 构建](0076-macos-intel-desktop-build.md)；
规则全文 `docs/rules/frontend/desktop-and-update.md`、`src-tauri/AGENTS.md`「更新通道」；隐私声明 `docs/privacy.md`「Update checks」

## 问题

桌面版应用内更新的清单固定在 `https://github.com/Tavotto/Tavotto/releases/latest/download/latest.json`，安装包也在 GitHub Release 上。
大陆网络下 GitHub 时通时断：用户点「更新」后卡在检查，或检查通了、几十 MB 的包下到一半断。维护者已在国内节点准备镜像：
镜像版 `latest.json`（`signature` 逐字照抄上游，`url` 改写成镜像地址），预期位置 `https://dl.tavotto.cn/updater/latest.json`；镜像关闭 / 过期时该文件不可用。

要求：大陆环境**优先走镜像**，失败回退 GitHub；海外环境相反，镜像同样是后备；回退不能只覆盖「检查」——**下载失败**也要换来源。

## 决策

### 一、插件的多 endpoints 不够，编排放在 Rust 侧（`src-tauri/src/app_update.rs`）

`tauri-plugin-updater` 的多 `endpoints` 只覆盖 check，下载仍直连 check 拿到的那个 `url`。依据（`tauri-plugin-updater` 2.10.1，
`src/updater.rs`，Cargo.lock 钉的版本）：

* `Updater::check`（第 386 行）里 `for url in &self.endpoints`（412）逐个请求，**第一个解析成功的清单就 `break`**（500–501）；
  非 2xx 只记日志并继续下一个（510）。也就是「端点轮换」只存在于 check 这一个函数里；
* check 产出的 `Update` 只带那一个 `download_url` / `signature`（`Update` 结构）；`Update::download`（652）直接
  `.get(self.download_url.clone())`（684），失败或 `verify_signature`（712）不过就返回错误——**没有任何代码会在下载或验签失败后
  回到 endpoints 换下一个重来**。
* 另外：多 endpoints 下「清单合法但缺本机平台」是在循环**之后**的 `get_urls` 里才报 `TargetsNotFound`，不会换端点。

infra 契约 §6.5 的方案 A（多端点）因此不满足 §6.3「下载或验签失败后整体回退」，用方案 B 的变体：自己按来源逐个 check + 下载。

* `tauri.conf.json` 的 `plugins.updater.endpoints` 保持**只有 GitHub 一条**（发布链 `updater_consumer_check.py` 与 `test_update_chain_gates.py` 读它）；
  镜像地址是 `app_update.rs` 里**唯一**的常量 `MIRROR_MANIFEST_URL`（调整只改这里与本文）；
* 壳新增两个命令 `desktop_update_check` / `desktop_update_install`（三处同步：`build.rs`、`capabilities/main.json`、`generate_handler`）。
  前端 `web/src/lib/desktop.ts` 改调它们，**不再直接用 `@tauri-apps/plugin-updater`**，`capabilities/main.json` 也不再放 `updater:default`
  （webview 不能绕过编排直接 check / download）；插件只作为 `UpdaterBuilder` 与验签的提供者；
* 每个来源用一个 `updater_builder().endpoints([那一个]).timeout(…)` 单独 build、单独 check；下载用该来源 check 出的 `Update`。

### 二、来源顺序（`app_update::source_order`，全产品唯一判据）

镜像优先当且仅当任一成立：

1. 系统 IANA 时区是大陆时区：`Asia/Shanghai`、`Asia/Chongqing`、`Asia/Chungking`、`Asia/Harbin`、`Asia/Urumqi`、`Asia/Kashgar`、`PRC`；
2. 系统语言是简体中文（大陆）：`zh-CN`、`zh-Hans-CN`、`zh-Hans`、裸 `zh`（`_`/`-` 与 `.UTF-8` 后缀都认）；
3. 界面语言（前端 `currentLocale()` 经命令参数 `uiLang` 传入）同上。

其余 [GitHub, 镜像]。**两个来源永远都在列表里。** 香港 / 澳门 / 台北时区与 `zh-TW` / `zh-HK` / `zh-Hant-*` / `zh-SG` 不算：那里直连 GitHub 不受限。
理由：时区是「人在哪」的最稳信号（不受界面语言影响，海外访问大陆的人不会被误导向镜像）；简中语言是「人是谁」的信号（大陆留学生 / 出差时区在海外，
但他们的网络环境常常仍受限，镜像优先对他们无害——镜像不通就回退，代价是多一次 ≤10 s 的失败）。规则取并集因为误判的代价不对称：
大陆用户走了 GitHub 优先 = 每次都卡一个必超时的来源；海外用户走了镜像优先 = 最多多一次快速失败。

### 三、「无更新」是否再问后备？——不问（与 infra 契约 §6.2 一致）

第一个**答上来**的来源说「没有更新」，就是最终答案，**不再问另一个来源**。答不上来（超时 / 非 2xx / 清单坏 / 缺本平台）才换下一个。
权衡：

* 流量与等待：启动时就自动 check，若「无更新」也要再问，大陆用户每次启动都要多等一个几乎必超时的 GitHub（≤10 s，且之后每次启动都重复）；
* 隐私：多问一次 = 多对一个第三方暴露一次 IP / 时间点（出网清单最小化）；
* 镜像滞后：代价是镜像同步晚于 GitHub 的那段时间内，大陆用户晚一点收到提示。这是可接受的——镜像同步是发布流程的一步（infra 侧），
  滞后不是安全问题（用户只是还没看到新版），而「手动下载」入口（Releases 链接）一直在界面上。

镜像报告了更新就用镜像，**不强求与 GitHub 版本一致**（镜像滞后时用户拿到的是稍旧的一版，下次启动再追）。
infra 契约 §6.2 对此的表述是「镜像 version ≤ 本机 = 已最新，不算回退条件」，与本节同向，无分歧。

### 四、下载与回退语义（`app_update::fetch`）

用户按下「下载并安装」后，在**同一次**动作里按顺序尝试（2026-11-04 起另有后台下载，见 §八）：

1. 先用 check 时的来源下载（**2026-11-05 修订**：大陆环境下载先走 GitHub，不再是 check 的来源，见 §九；其余环境不变）。失败类别：网络 / 非 2xx / **验签不过** / **停滞** → 换下一个来源，**重新 check + 重新下载**；
2. 回退来源（凡是与 check 不同的来源，含 §九 的 GitHub）必须报出**同一个版本号**——用户看到并确认的版本与真正装上去的必须是同一版。回退来源报「更新的版本」或「无更新」都记
   `version_mismatch` 并放弃该来源，不下载（不悄悄装别的版本）；
3. 安装本身失败（`install_failed`）不换来源——那不是来源的错；
4. 全部失败 → `all_sources_failed`，附逐来源的 `{source, stage, kind}`（闭集枚举，**不带原始错误文本**，原文只写 stderr/app.log）。

**验签始终用 `tauri.conf.json` 里内置的公钥**（`Update::download` 返回前验）。来源只决定「从哪下」，不决定「信谁」：镜像被入侵、
过期、或清单与包对不上，都只会让验签失败并回退，不会装上未签名的包。清单里的 `url` 不限定域名（镜像改写它是设计的一部分），信任全在签名。

### 四·补、镜像清单的取舍与冷却（infra 契约 §6.2–6.3，参考实现 `consume.py::decide_updater`，向量提交 a7f1000）

* 镜像来源的 check 结果还要过 `app_update::decide_updater`（对插件解析出的 `raw_json` + 本机平台键）：非法 JSON / 缺 `version`
  （`MAJOR.MINOR.PATCH`）或 `platforms` / 无本机平台键 / 条目缺 `signature` 或 `url` / **包地址不在 `{镜像根}/releases/<version>/` 下**
  → 本来源视为不可用（前三类归 `bad_manifest`，最后一类归 `not_on_mirror`），换下一个来源；拉取失败或任何非 200（含 404 = 镜像撤下）由插件的
  check 错误覆盖。向量 `src-tauri/testdata/updater-vectors.json`（复制自 infra a7f1000）11 条全过（`the_infra_contract_vectors_all_pass`）。
* 镜像来源的 HTTP client **不跟随重定向到别的主机**（`reqwest::redirect::Policy::custom`，check 与下载共用）。
* 冷却：镜像在本进程里 check 或下载出过问题 → 30 分钟内顺序里镜像挪到最后（钉死的单来源不动），且**同一次更新不再回头试镜像**
  （契约 §6.3）。状态只在内存里，进程重启即清空。

### 五、超时

* check：单来源总超时 10 s（含连接）；
* 下载：连接超时 10 s；**停滞超时 30 s**（这么久没收到任何字节放弃该来源，含「连上了但服务端不发 body」）；**不设下载总时长**，慢网下大包本来就久；
* 实现：check 用 reqwest 的 `timeout`，下载用 `select!` 竞速一个停滞看门狗（`tokio::time`，每 ≤1 s 比对最近一次收到字节的时刻）。

### 五·补、下载慢时试探另一个来源（2026-11-04 修订；**2026-11-05：只适用于非大陆环境**，大陆环境见 §九）

用户反馈应用内更新慢：包 157–204 MB，而停滞看门狗只管「一个字节都不来」——来源在发、但只有几十 KB/s 时，
永远不会换到另一个来源。修订（`app_update::download_watched`，判据 `SLOW_RULE` / `is_slow` / `should_switch`）：

* **判慢**：第一个字节后先等 15 s（起速），之后按最近 10 s 的速度估剩余时间，**超过 3 分钟**算慢（不知道总长时：低于 256 KB/s）。
* **试探而不是直接换**：慢且顺序里还有可用的下一个来源（钉死单来源不试探；冷却中的镜像不试探）时，原来源**继续下**，
  同时对下一个来源重新 check（必须同一个版本，否则不试探）并开始下载；它出字节后量 8 s，比速：
  试探来源**从它的进度下完**比原来源下完剩下的**少两成以上**才换（总长未知：快一半以上），换时丢掉原来那一路；
  否则丢掉试探那一路，原来源继续。试探期间就下完的直接用。每次更新只试探一次。
  这样换来源永远不会让这次下载比不换更慢，代价最多是一段试探的流量。
* **失败语义不变**：试探期间原来源失败 / 停滞 → 直接交给试探那一路；试探来源失败只记一笔（原来源也失败时一并报）、
  这次更新不再用它；慢**不是故障**，不触发镜像冷却。换过去时前端收到新的 `Started`，进度归零（与 §六 回退时一致）。
* 隐私：多出来的只是对另一个来源的一次 check + 一段下载，与 §四 回退同一类请求；`docs/privacy.md`「Update checks」已补一句。
* 看护：`app_update_flow_tests.rs` 的 `a_slow_download_*` 四条（换 / 不换 / 别的版本不换 / 钉死单来源不试探），
  与 `app_update.rs` 的判据单测；四条都对 `should_switch` / `is_slow` 恒假反证过。

### 六、界面

沿用现有更新 UI（设置 → 检查更新 / 启动时的更新提示框），**不新增横幅、不新增主按钮**。来源只出现在「技术详情」折叠区：
「更新来源：国内镜像 / GitHub」，以及失败时逐来源的 `来源 · 阶段 · 原因` 行。进度条在换来源重下时由新的 `Started` 事件归零。
遥测 `update_completed` 的属性白名单不变（不加来源——不扩事件表）。

### 七、e2e-update

`TAVOTTO_E2E_RUN_UPDATE=1` 的 headless 触发口走**同一份** `app_update::run_check` / `run_install`；新增 `TAVOTTO_E2E_UPDATE_SOURCE=mirror|github`
把来源**钉死成单个且不回退**（否则坏掉的镜像会被 GitHub 悄悄兜住，CI 假绿），让 CI 能分别验两条来源；不设 = 与用户相同的自动顺序。退出码 40/41/42 不变。

### 八、后台静默下载 + 退出时安装（2026-11-04 修订）

此前「只在用户按下按钮之后下载」。现在桌面版有一个偏好 **「后台下载新版本」（默认开，设置 → 更新里可关）**，开着时：

* 启动查到新版 → **不弹** `UpdateNoticeDialog`，前端立刻调新命令 `desktop_update_download`：走同一个 `fetch`（失败换来源、
  镜像冷却、内置公钥验签**一个字都没动**；2026-11-05 起大陆环境的来源顺序与「不试探」见 §九，其余环境来源顺序与 §五·补的慢速试探不变），下好的包（含 `Update` 句柄）放进 `UpdateState.ready` 槽，**不安装**。
* 下好 → 顶栏出「新版已就绪」胶囊（形态同 `BuildMismatchChip`，点开一句话 + 「现在重启」）。
* **安装只发生在两个时刻**：用户点「现在重启」（`desktop_update_install` 发现槽里有**同一版本**的包就直接装它，**不再请求包地址**；
  版本不同则照旧现下现装）；或退出应用（`RunEvent::Exit` 里，在既有的 `shutdown_sidecar()` + `mark_clean()` **之后**调
  `install_ready_on_exit`）。Windows 上插件的 install 会起 NSIS（passive）并自行结束进程，`updater_builder()` 挂的
  `on_before_exit → mark_clean` 照旧生效。
* **后台下载失败 → 静默**（不弹窗、不出错误胶囊）；「⋯」菜单圆点与设置页的手动「下载并安装」仍在。关掉偏好 = 回到此前的行为
  （启动弹询问框、点了才下）。
* 「用户看到的版本 == 装上的版本」：槽按版本取（`take_ready`），`run_check` 查到别的版本 / 没有新版时作废旧包；退出时装的是槽里那一版。

**为什么 macOS 也不下好就立刻替换 .app**：sidecar 按需 `import` 模块、渲染 worker 之后还会从包里起进程，运行中换掉包会让同一会话混用
新旧版本的代码。所以两个平台都放到退出时装（这也同时满足「运行中的会话不变」的可复现纪律）。

**代价（接受）**：下好的包字节常驻内存直到安装（Windows 约 157 MB，macOS 更小）；后台下载期间 `pending` 锁一直被持有，
设置页的「检查更新」不会撞 `busy` 报错——前端据 `desktopPhase` 直接显示后台下载的进度。退出时安装失败只记日志，
下次启动会重新查；退出时装包前端记不到，所以遥测 `update_completed` 对这条路径**没有**记录（白名单不扩，不另造事件）。

**偏好存哪**：后端用户配置 `config.json` 的 `updates.auto_download`（`engine/updater.py`），经桌面也放行的 `/api/update/settings`
读写，值由桌面下的停用响应 `/api/update/check` 带回。**不放 localStorage**：桌面 Flask 端口可能变（`port_memory`），localStorage 按
origin 隔离，端口一变「已关闭」会悄悄复位成开。

### 九、检查与下载的来源拆开：大陆环境下载先走 GitHub（2026-11-05 修订，用户拍板）

**动机**：镜像的带宽与流量成本由维护者承担，而后台静默下载是每个大陆用户每个新版本都会发生的大流量；检查清单很小、且必须避免 GitHub
每次超时，所以只把**下载**挪回 GitHub，检查不动。

**检查**：不变。`source_order` 一个字不改，大陆仍是镜像先答，「第一个答上来的来源说了算」「无更新即终局」照旧（§二、§三）。

**「大陆环境」**＝`run_check` 拿到的**原始**来源顺序里镜像在前、且有两个来源（`Pending::mainland`）。冷却（§四·补）只改 `Pending::order`，
不改这件事——镜像冷却中的大陆用户仍然是大陆用户。e2e 钉死单来源（§七）不算大陆，不试探、不改道，与此前一致。非大陆环境：下载计划
完全是 §四–§五·补 的原样（`Plan::from_pending`），也不读写下面的记忆文件。

**下载计划**（`app_update::plan_download` / `plan_for` 是唯一判据；`fetch` 按 `Plan { seq, pace }` 执行）：

| 场景 | 来源顺序 | 步调 |
| --- | --- | --- |
| 非大陆（含冷却、含单来源） | check 的来源，然后按 check 顺序 | `Probe`：慢则试探，更快才换（§五·补） |
| 大陆·后台 | `[GitHub, 镜像]`；失败累计 / 超期时 `[镜像, GitHub]` | `Steady`：**慢不换来源**，只有失败 / 停滞才换（停滞仍是 §五 的 30 s） |
| 大陆·手动（含救援页） | `[GitHub, 镜像]` | `Trial`：见下 |

**检查与下载来源不同怎么办**：`fetch` 对任何不是 check 来源的来源都**重新 check**（`check_one`），报出的版本必须与用户看到的相同——
GitHub 比镜像新（镜像滞后）、或还没有这个版本，都记 `version_mismatch` 并**跳过它**，直接用 check 来源（镜像）下载，绝不悄悄换版本。
这一步不算「GitHub 坏了」（不进失败计数）。重新 check 用 §五 的 10 s `CHECK_TIMEOUT`：GitHub 被丢包时手动下载要多等这一段才能转镜像（已知代价）。
镜像在冷却中时不再回头试它，但 check 刚从镜像拿到的那个包不算「回头」（`skip` 里 `s != pending.source`）。

**大陆·手动 `Trial`**（`TRIAL_RULE`，纯函数 `trial_verdict`）：GitHub 开始下载后——① 8 s 内一个字节都没有 → 转镜像；② 出第一个字节后量 8 s，
按这段平均速度估剩余时间，**超过 2 分钟**（总长未知：低于 256 KB/s）→ 转镜像；否则一直留在 GitHub，只判这一次。「转」＝去重新 check 镜像（同版本）并下载，
**它一出字节就换**（丢掉 GitHub 那一路，前端收到新的 `Started`，进度归零）；镜像 check 不过 / 下载失败 / 不出字节，GitHub 那一路继续下完
（避免「GitHub 慢但能下完、镜像坏了」反而失败）。GitHub 自己失败 / 停滞照旧落到镜像。

**大陆·后台的改道记忆**（`src-tauri/src/update_source_memory.rs`，纯函数 `observe` / `prefer_mirror` / `outcome_of` / `record`）：
后台只在启动时下一次，所以「连续失败」与「超期」要跨重启。落一个小文件 `app_config_dir()/update-source.json`（与 `desktop-port`、`session-state.json` 同处；
**只有** `{version, first_seen, tries, failures}`，无路径无标识符）。读不到 = 没有记录，写不进 = 这次不记，一律静默，最坏只是多试几次 GitHub。

* 阈值：**同一版本上后台走 GitHub 连续失败 3 次**（`FAILURES_BEFORE_MIRROR`），或 **新版发布超过 3 天**（`OVERDUE_AFTER`）且这台机器上后台**至少试过一次**
  → 之后后台改走镜像（`[镜像, GitHub]`）。「试过」在每次后台下载**开始时**就记（`tries+1`，先落盘），所以被退出打断、从不报错的慢下载也能累计到超期。
  「至少试过一次」是为了隔了很久才打开应用的人：新版早过了 3 天，但这台机器没试过，不该一上来绕过 GitHub。
* 「失败」＝这一轮里 GitHub 那一环出过问题（check 失败 / 下载失败 / 停滞 / 验签不过），不论最后是不是镜像兜住了；**GitHub 报了别的版本不算**（那是镜像滞后）。
  一次 GitHub 下成功 → 整条记录删除（连续计数清零）。镜像下成功**不**清记录：下好的包只在内存里，重启后要继续走镜像，不能重头再试三次 GitHub。
  换版本 → 新记录，从头计。镜像冷却中不改道（仍先 GitHub），冷却见 §四·补。
* **发布时间**取清单已有的 `pub_date`（插件解析成 `Update::date`）；**取不到时回退为「本机第一次见到这个版本」的时刻**（`first_seen`）。
  回退只会让超期判得更晚（不会比真实发布更早），是保守方向：最坏是多试几天 GitHub，不会把流量提前压给镜像。
* 手动下载**不读也不写**这份记忆（用户在场，自己看得见慢，`Trial` 已兜住）。

**不变的**：「回退来源必须同一版本」「换来源进度归零」「内置公钥验签」「镜像 `not_on_mirror` / 冷却」「钉死单来源不试探」；`tauri.conf.json` 的 endpoints
仍只有 GitHub 一条；遥测白名单不动。**生效范围**：只对包含本改动的客户端之后的升级生效（已安装的旧客户端按旧行为升级到它）。

**接受的代价**：大陆用户会在后台对 GitHub 发请求（`docs/privacy.md`「Update checks」已写明）；GitHub 在大陆不稳时，一个版本最多要多等 3 次启动才改道镜像
（或 3 天）；手动下载在 GitHub 被丢包时要多等一个 check 超时。**未验证（NOT_RUN）**：真实大陆网络下 GitHub 的实际表现与阈值是否合适、Windows 真机。

### 十、第三个下载来源：国内自建边缘节点（2026-11-11 修订，维护者决定）

**动机**：`dl.tavotto.cn` 按流量计费；11-09 至 11-11 实测，CDN 流量约一半是 updater 包（`nsis.zip` / `app.tar.gz`）——大陆用户后台先走 GitHub，
经常失败后落到镜像（§九）。维护者买了一台国内轻量服务器（5 Mbps、每月 500 GB 流量、3 年），只用来分担**后台**下载（用户不等，慢点无所谓）。
手动下载（用户在等）不走它，仍是 §九 的 `[GitHub, 镜像]` + `Trial`。

**来源**：`Source::Edge`（serde / `Source::parse` 名 `edge`），清单 `https://up.tavotto.cn/updater/latest.json`（常量 `EDGE_MANIFEST_URL`，与 `MIRROR_MANIFEST_URL` 并列；
`Endpoints` 多 `edge` / `edge_root`）。**tauri.conf.json 的 endpoints 仍只有 GitHub 一条。**

**顺序**：check 一个字不变（`source_order` / `detect_order` 只出镜像 / GitHub，节点**不参与 check**，也不影响「第一个答上来的说了算」）。
`plan_for` 只在「大陆·后台」插入节点，仍是 `Pace::Steady`：

| 场景 | 来源顺序 | 步调 |
| --- | --- | --- |
| 非大陆（含冷却、含单来源）· 手动 / 后台 | 不变（§四–§五·补） | `Probe` |
| 大陆·手动（含救援页） | `[GitHub, 镜像]`，不走节点 | `Trial` |
| 大陆·后台（默认） | `[GitHub, 节点, 镜像]` | `Steady` |
| 大陆·后台（GitHub 连续失败 3 次 / 超期，§九 记忆） | `[节点, 镜像, GitHub]` | `Steady` |

节点是「第三个下载来源」：节点在 GitHub 与镜像之间，意思是 GitHub 不行先试便宜的节点（流量已买断上限），节点不行才花 CDN 的钱。
§九 的「改走镜像」在大陆·后台里现在读作「改为节点（再镜像）优先」，判据（3 次 / 3 天）与记忆文件一个字不变。

**契约**（服务器端另行实现，本仓库只认这些）：`GET https://up.tavotto.cn/updater/latest.json` 是 Tauri 格式清单，与镜像清单同版本、`signature` 逐字相同，
只是每个平台的 `url` 改为 `https://up.tavotto.cn/releases/<version>/<文件名>`。`decide_updater`（§四·补）对节点同样适用（根换成 `https://up.tavotto.cn`，包地址不在
`{节点根}/releases/<version>/` 下 = `not_on_mirror`）；节点同样 pin host，重定向到别的主机不跟随。验签用内置公钥——节点只决定「从哪下」，不决定「信谁」。

**404 即不可用，关停不需要发版**：节点关闭 / 当月流量快用完 / 还没同步到时，服务器让清单回 404（或干脆连不上），客户端把它当作 check 失败，`fetch` 换下一个来源；
节点落后一个版本同理（`fetch` 对非 check 来源重新 check 且要求同版本，不符 = `version_mismatch`，跳过）。所以**关停节点 = 服务器删掉清单**，已安装的客户端无需任何改动。
带宽只有 5 Mbps，多人同时下会慢，但只要在出字节就不放弃（`Steady`；30 s 停滞看门狗照旧）。

**失败记账**：节点失败**不**触发镜像的 `Cooldown`（那是镜像专用的，`trip` 仍只对 `Source::Mirror`）；节点没有自己的进程内冷却——后台一次启动只下一次，
一次多问一个 404 清单的代价可以忽略，不值得多一份状态。`update_source_memory::outcome_of` 不用改：它只认「赢的是不是 GitHub」，节点赢 = 镜像赢——
GitHub 先失败、节点兜住记 `GithubFailed`（计数 +1，第 3 次起改节点优先）；改道后节点直接赢、GitHub 没试 / 只报别的版本记 `Neutral`（不加不清）。判据不被节点破坏（单测钉住）。

**界面 / 遥测**：`UpdateSource` 多 `'edge'`；界面「更新来源」与失败记录里它与镜像用同一句文案（zh「国内镜像」/ en「China mirror」），不出现「节点」字样。
遥测白名单不动（不含来源字段）。

**隐私**：桌面版大陆环境后台下载可能联系 `up.tavotto.cn`（维护者运营，位于中国大陆），与镜像同样只收到标准 HTTPS 请求元数据；已同步 `docs/privacy.md`。

**接受的代价**：大陆用户后台多一个来源，GitHub 失败后多问一次节点清单（≤ 10 s check 超时，节点 404 时是毫秒级）；节点 5 Mbps 下大包可能下得很慢，但 `Steady` 不因慢而放弃。
**未验证（NOT_RUN）**：真实 `up.tavotto.cn`（服务器端另一人实现）与大陆真实网络；Windows 真机。**生效范围**：只对包含本改动的客户端之后的升级生效。

## 与既有承诺的冲突（已同步更新的面向用户文档）

| 位置 | 原承诺 | 现状 |
| --- | --- | --- |
| `docs/privacy.md`「Update checks」 | 只写 GitHub Releases | （2026-11-05 再改：大陆环境下载先 GitHub、改道记忆文件，见 §九；2026-11-04 再改：默认后台下载、可关、退出时安装，见 §八）加：桌面版按顺序联系 `dl.tavotto.cn`（维护者运营，位于中国大陆）与 GitHub；镜像只收到标准 HTTPS 请求元数据（IP、UA、时间），无任何标识符 / 遥测 / 项目内容；顺序规则；下载仍只在用户确认之后；验签不依赖来源 |
| `README.md` / `README.zh-CN.md`「Everything runs on your machine」 | 「每天一次去 GitHub Releases」 | 加桌面版镜像一句 |
| `docs/rules/frontend/desktop-and-update.md`、`src-tauri/AGENTS.md`、`docs/adr/0002` 末节 | 前端经插件检查 / 下载 | 改为经壳自有命令；不再放 `updater:default` |
| 网站（`Tavotto/Tavotto_website`）隐私页 / 下载页 | 同 README | **仓库外**，需另行同步（本分支不动） |

## 后果与未决

* 发版流程多一步：镜像的 `latest.json` + 安装包同步（infra 负责）。**镜像没同步时客户端自然落到 GitHub 或判「无更新」**——前者无害，后者是 §三 接受的滞后。
* 镜像清单 `signature` 必须与上游**逐字相同**；若 infra 重新签名，会因公钥不符而全部回退到 GitHub（不会装错，但镜像等于白建）。
* 未验证（NOT_RUN）：§八 的真机退出时安装（macOS 就地替换 / Windows NSIS passive）；真实 COS（`dl.tavotto.cn`）上的清单 / 包；真机（含 Windows NSIS passive、macOS 公证后的 .app.tar.gz）上的完整更新；
  中国大陆真实网络下的体验。实现的状态机用「本地假 HTTP 服务 + 测试密钥签名的包」端到端验过（`src-tauri/src/app_update_flow_tests.rs`），安装步骤在测试里不执行。
* 备案 / 主体：`dl.tavotto.cn` 的域名与云资源归属由维护者另行处理，客户端只认这个常量。

* **Windows 退出时装完不重新拉起应用**：插件在 passive 模式下总给 NSIS 带 `/R`（装完重启），参数在 check 时就定死在 `Update`
  里（私有字段，改不了）。所以退出时安装前，壳设环境变量 `TAVOTTO_UPDATE_NO_RELAUNCH=1`（`app_update::NO_RELAUNCH_ENV`），
  `ShellExecuteW` 起的安装器继承它，`windows/installer.nsi` 的 `.onInstSuccess` 见到它就跳过 `RunAsUser`；「现在重启」不设，照旧重启。
  看护：`tests/test_nsis_template.py::test_update_installed_on_quit_does_not_relaunch_the_app`（变量名与壳常量同源）。
