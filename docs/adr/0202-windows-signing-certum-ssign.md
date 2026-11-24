# ADR 0202：Windows 安装包签名改用维护者自购的 Certum 云证书 + ssign，在 GitHub 托管 runner 上签

日期：2026-11-23 · 状态：**Accepted（实现随本 ADR 同一 PR；`vars.CERTUM_SIGNING_ENABLED` 打开前发版行为与从前一字不差）**
相关：`docs/code-signing-policy.md`（对外政策）、`.github/workflows/desktop-tauri.yml`（签名与验签）、
`.github/workflows/release-publish.yml`（Release 正文的签名披露）、[0198 高级版闭源扩展](0198-closed-source-pro-extension.md)（Pro 组件的 Windows 签名）、
`docs/rules/ci/release-chain.md`。

## 问题

Windows 安装包一直没有 Authenticode 签名。原计划的 SignPath Foundation 开源订阅始终没有启用（`vars.SIGNPATH_ENABLED`
从未为 true），0.19 起每个正式版都靠 `vars.WINDOWS_UNSIGNED_WAIVER_TAG` 点名豁免才发出去，用户首次运行都看到 SmartScreen 警告。
2026-11-23 维护者自购了 Certum「Standard Code Signing in the Cloud」证书（SimplySign，云端 HSM，私钥不可导出），要求开源版与高级版都用它签，
并且不为签名额外买机器。

约束：

1. 私钥在 Certum 的 HSM 里，签名要先用 SimplySign 账号邮箱 + 手机 App 上的 6 位 TOTP 码登录；GitHub 托管 runner 每次都是新机器。
2. 种子（TOTP seed）加邮箱 = 能以**维护者本人的名义**签任何文件，直到在 Certum 重新生成二维码。它和 minisign 更新包私钥同等敏感。
3. 发行链上的更新包、provenance、产物清单都必须算在**签过名的**安装包字节上（`desktop-tauri.yml` 现有顺序）。
4. `tests/test_update_chain_gates.py` 的点名闭集禁止被 pattern 下载匹配到的上传带 `if:`、禁止重名——`desktop-tauri-nsis` 只能由 build 矩阵那一步上传。

## 决定

### 一、工具：ssign 0.1.11（从 crates.io 源码按锁文件编）

[ssign](https://github.com/Le-Syl21/ssign)（MIT，作者与 Certum 无关）直接走 HTTPS 调 SimplySign 云接口：登录、取证书、把 Authenticode
摘要交给云端签、本地组 PKCS#7 并加 RFC 3161 时间戳（`time.certum.pl`）。不需要 SimplySign Desktop、不需要 GUI 或 PKCS#11。

- **审过的版本**：ssign 0.1.11 + ssign-core 0.1.8（2026-11-23 读了 `otp.rs` / `auth.rs` / `client.rs` / `session.rs` / `sign.rs` / `card.rs` 全文与 `main.rs` 的输出语句）。
  结论：种子只在本进程里算出 6 位码，网络只连 `cloudsign.webnotarius.pl`（重定向限死同源）与 `time.certum.pl`；种子与码不写盘、不打日志；
  登录后的会话令牌（约 30 分钟）缓存在 `$XDG_RUNTIME_DIR/ssign/session.json`，Windows 上不查目录属主。**升级 ssign = 重新审这几份文件、改 ADR 这一段与 workflow 里的版本号。**
- **从源码编，不用作者的预编译包**：预编译包出自作者自己的 CI，证明不了它就是审过的源码；`cargo install --version 0.1.11 --locked` 用的是 crates.io 上不可变的源码包与它的锁文件。
- **实测**：2026-11-23 在维护者 Mac 上用一次性 6 位码签了 v0.21.3 的 `Tavotto-0.21.3-Windows-Setup.exe`：
  osslsigncode 2.16 重算摘要一致、时间戳与 CRL 通过；证书链 签名证书 → Certum Code Signing 2021 CA → Certum Trusted Network CA 2 加上该根后通过
  （macOS 自带的 `/etc/ssl/cert.pem` 里没有 CTNCA2，不加时报 unable to get local issuer certificate）；签过名的安装包在 Windows Lab
  （run `20261123T051558Z-5eca56`，Server 2025，Defender 开）静默安装、起壳、冒烟、重装全部通过；同一份文件在 Lab 上 `Get-AuthenticodeSignature` 为 **Valid**，时间戳证书 Certum Timestamp 2026。
  注意 Windows 把省份写成 `S=`（OpenSSL 写 `ST=`），`CERTUM_SIGNER_SUBJECT` 要取 Windows 的原样字符串。
- **后备**：ssign 原生签名哪天在 Windows 上判不过，同一仓库的 `ssign-pkcs11` 模块配 osslsigncode（成熟的 Authenticode 实现）是现成的第二条路，换法只动签名那一步。

### 二、拓扑：签名进 build 矩阵的 Windows 腿，挂要人批准的 `windows-signing` 环境

```
release.yml ─workflow_call→ desktop-tauri.yml::build（matrix）
  windows 腿，environment: windows-signing（仅发行构建且 CERTUM_SIGNING_ENABLED=true）
    ……构建 / 冒烟 / 收集 out\Tavotto-<ver>-Windows-Setup.exe
    装 ssign      （无 secret；cargo 独立 CARGO_HOME / target-dir）
    签名          （step 级 env：CERTUM_EMAIL / CERTUM_OTP；会话令牌落本步私有目录，finally 删）
    验签          （Get-AuthenticodeSignature：Valid + 签名者主题 == vars.CERTUM_SIGNER_SUBJECT + 有时间戳）
    未签名即拦停 / 重打更新包 + minisign / 上传 desktop-tauri-nsis / provenance / 产物清单   ← 全在签过名的字节上
```

- **环境**：仓库环境 `windows-signing`（2026-11-23 建）只放 `CERTUM_EMAIL`、`CERTUM_OTP` 两个 secret；保护规则 = 必须由维护者
  （erwanjun）批准、只放行 `main` 分支与 `v*` tag（正式版是在 main 上 dispatch 的，只放 tag 会把它拦住）。批准发生在 Windows 腿**开始之前**，之后无人值守跑完。
  环境名表达式在其余腿与未使能时求值为空串 = 不挂环境；**不许换成占位名**——GitHub 会把不存在的环境名当场建成无保护的环境。
- **为什么不单开一个只签名的 job**（评审中考虑过、没选）：那样隔离最好（签名 job 不跑仓库代码、不装依赖），但 `desktop-tauri-nsis`、
  更新包、provenance、产物清单都得挪到签名之后的另一个 Windows job，而点名闭集（约束 4）不允许 build 矩阵的上传带条件、也不允许重名，
  等于要改判据。现在这条腿的 job 级 env 本来就带着 minisign 私钥和 Apple 凭据，种子只进**一步**的 env 并不比现状更宽；
  残余风险是：本 job 前面的步骤里某个构建依赖被投毒、留下常驻进程，就能在签名那一步读到种子。维护者若要更强隔离，改成独立 job 并同步改点名判据。
- **开关与豁免**：`vars.CERTUM_SIGNING_ENABLED` 取代 `vars.SIGNPATH_ENABLED`，early gate 与晚期 fail-closed 守卫的语义不变；
  `vars.WINDOWS_UNSIGNED_WAIVER_TAG` 按 tag 点名的豁免**保留**（二维码或种子没到位时，下一版仍可按老办法发未签名版）。开关关着时本 PR 是空操作。
- **签名范围**：只签用户下载的 NSIS 外层安装包，与原 SignPath 配置（只签 `Tavotto-${version}-Windows-Setup.exe`）一致。
  壳、sidecar、workerd 等内层 exe 不签：要签就得在 Tauri 打包**之前**签，是另一种拓扑，另立决定。
- **Release 正文**：`release-publish.yml` 的签名披露按 `vars.CERTUM_SIGNING_ENABLED` 分岔，开着才写「已签名」。

### 三、高级版（0198）

Pro 组件的 Windows 可执行文件用同一张证书、同一个 ssign 版本签。Pro 在私有仓库 `Tavotto/pro` 构建（2026-11-23 维护者确认；那边还没有 workflow）。
GitHub 的环境 secret 按仓库存，那边要存第二份种子：照本仓库建一个同样保护规则（维护者批准、只放 main / `v*`）的签名环境
（组织计划 2026-11-23 查为 enterprise，私有仓库的环境可以设「必须批准」），workflow 等 Pro 发版流程成形时再接。
Pro 的二进制不进本仓库的 workflow 签——本仓库的 artifact 对任何登录用户可下。0198 里「Windows 未签名，沿用发版豁免」一句随本 ADR 改。

## 启用步骤（维护者）

1. 从 Certum 激活页的二维码读出 `otpauth://…`（本机 `zbarimg`，不用在线解码工具），`gh secret set CERTUM_EMAIL / CERTUM_OTP --env windows-signing`。
2. `gh variable set CERTUM_SIGNER_SUBJECT`，值取 Windows 上 `(Get-AuthenticodeSignature <已签文件>).SignerCertificate.Subject` 的原样字符串。
3. `gh variable set CERTUM_SIGNING_ENABLED --body true`，跑一次 `release.yml` 的 `publish=false` 演练，在 Windows 腿开始前批准；
   演练绿（含验签那一步）之后再打正式 tag。**签名路径的第一次执行必须是演练，不能是正式 tag。**
4. 证书 2027-11-22 到期：续期后主题不变则不用改变量；主题变了改 `CERTUM_SIGNER_SUBJECT`。

## 后果

- 好处：零额外机器与费用；签名在公开的托管 runner 上完成，可追溯到具体 run；更新包与清单天然算在签过名的字节上。
- 代价：每次发行（含演练）要维护者在 Windows 腿开始前点一次批准；依赖一个年轻的第三方开源客户端（2026-07 首发），升级要重审。
- 删除：`complete-windows-signing.yml`、`signpath/` 目录、`desktop-tauri.yml` 的 `signing-mode` 输入与 `SIGNPATH_API_TOKEN` 声明。
- 未验（启用前要过）：`aws-lc-sys` 在 `windows-latest` 的 MSVC 上能否编过；
  空串环境名在 GitHub 上确实等于「不挂环境」；环境 secret 能进被 `workflow_call` 调起的 job。三项都由「启用步骤」第 3 步的演练核。
- SmartScreen 信誉要随下载量积累，新证书头几个版本仍可能出现提示。
