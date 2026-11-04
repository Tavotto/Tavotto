# ADR 0121：应用内更新的来源顺序与回退——国内 COS 镜像优先（大陆环境），GitHub Release 兜底

日期：2026-10-12 · 状态：**Proposed（实现与测试已在分支内；镜像侧由 `Tavotto/infra` 提供，真实 COS 未验）**
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

用户按下「下载并安装」后，在**同一次**动作里按顺序尝试，不在后台自动下载：

1. 先用 check 时的来源下载。失败类别：网络 / 非 2xx / **验签不过** / **停滞** → 换下一个来源，**重新 check + 重新下载**；
2. 回退来源必须报出**同一个版本号**——用户看到并确认的版本与真正装上去的必须是同一版。回退来源报「更新的版本」或「无更新」都记
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

### 五·补、下载慢时试探另一个来源（2026-11-04 修订）

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

* 启动查到新版 → **不弹** `UpdateNoticeDialog`，前端立刻调新命令 `desktop_update_download`：走同一个 `fetch`（来源顺序、失败换来源、
  §五·补的慢速试探、镜像冷却、内置公钥验签**一个字都没动**），下好的包（含 `Update` 句柄）放进 `UpdateState.ready` 槽，**不安装**。
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

## 与既有承诺的冲突（已同步更新的面向用户文档）

| 位置 | 原承诺 | 现状 |
| --- | --- | --- |
| `docs/privacy.md`「Update checks」 | 只写 GitHub Releases | （2026-11-04 再改：默认后台下载、可关、退出时安装，见 §八）加：桌面版按顺序联系 `dl.tavotto.cn`（维护者运营，位于中国大陆）与 GitHub；镜像只收到标准 HTTPS 请求元数据（IP、UA、时间），无任何标识符 / 遥测 / 项目内容；顺序规则；下载仍只在用户确认之后；验签不依赖来源 |
| `README.md` / `README.zh-CN.md`「Everything runs on your machine」 | 「每天一次去 GitHub Releases」 | 加桌面版镜像一句 |
| `docs/rules/frontend/desktop-and-update.md`、`src-tauri/AGENTS.md`、`docs/adr/0002` 末节 | 前端经插件检查 / 下载 | 改为经壳自有命令；不再放 `updater:default` |
| 网站（`Tavotto/Tavotto_website`）隐私页 / 下载页 | 同 README | **仓库外**，需另行同步（本分支不动） |

## 后果与未决

* 发版流程多一步：镜像的 `latest.json` + 安装包同步（infra 负责）。**镜像没同步时客户端自然落到 GitHub 或判「无更新」**——前者无害，后者是 §三 接受的滞后。
* 镜像清单 `signature` 必须与上游**逐字相同**；若 infra 重新签名，会因公钥不符而全部回退到 GitHub（不会装错，但镜像等于白建）。
* 未验证（NOT_RUN）：§八 的真机退出时安装（macOS 就地替换 / Windows NSIS passive）；真实 COS（`dl.tavotto.cn`）上的清单 / 包；真机（含 Windows NSIS passive、macOS 公证后的 .app.tar.gz）上的完整更新；
  中国大陆真实网络下的体验。实现的状态机用「本地假 HTTP 服务 + 测试密钥签名的包」端到端验过（`src-tauri/src/app_update_flow_tests.rs`），安装步骤在测试里不执行。
* 备案 / 主体：`dl.tavotto.cn` 的域名与云资源归属由维护者另行处理，客户端只认这个常量。
