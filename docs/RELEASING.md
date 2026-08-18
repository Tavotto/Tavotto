# 发版

一条流水线 `release.yml`，推 `v*` tag 触发，三个 job：

| job | 做什么 |
|---|---|
| `build` | 构建前端 → 打 wheel + sdist → 核对版本 → `twine check` → 干净环境装一遍冒烟 |
| `github_release` | 建 GitHub Release，挂上产物 |
| `pypi` | 发到 PyPI（需开闸，见下） |

后两个 job 用的是 `build` 上传的**同一份** artifact——GitHub Release 上的 wheel
与 PyPI 上的必须字节一致，否则「检查更新」装到的和 `pip install` 装到的会是两个
不同的东西。

> **为什么 PyPI 发布不是单独一个工作流**：Release 是本工作流用 `GITHUB_TOKEN`
> 建的，而 GitHub 明确规定 `GITHUB_TOKEN` 触发的事件**不会**再触发新的工作流运行
> （防递归）。`on: release: published` 那条链根本不会响——实测过，v0.1.1 发布时
> 独立的 publish 工作流一次都没被触发。

## 一次性设置：PyPI Trusted Publishing

Trusted Publishing 用 OIDC 换短时凭据，仓库里不存任何 API token。PyPI 校验
「哪个仓库 + 哪个工作流文件 + 哪个 environment」，三者任一改名都要同步改这里的
配置，否则鉴权直接失败。

### 1. PyPI（正式）

登录 <https://pypi.org/manage/account/publishing/>，因为项目还不存在，
添加一个 **pending publisher**：

| 字段 | 值 |
|---|---|
| PyPI Project Name | `magplot` |
| Owner | `erwanjun` |
| Repository name | `magplot` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

### 2. TestPyPI（演练用，强烈建议先做）

在 <https://test.pypi.org/manage/account/publishing/> 重复一遍，
**Environment name 填 `testpypi`**。两边是完全独立的账号与配置。

### 3. 开闸

配好之前自动发布是关着的（否则每发一个 Release 都会红一次）。准备好了就在
Settings → Secrets and variables → Actions → **Variables** 加一条
`PYPI_PUBLISH_ENABLED = true`。

手动 Run workflow 不受此限——没开闸也能先演练。

### 4. 给正式发布加一道人工确认（可选但推荐）

Settings → Environments → `pypi` → 勾 **Required reviewers** 填自己。
之后每次发 PyPI 都会停下来等你点一下。

> PyPI 上的项目名一旦被占用就再也拿不回来，**同名文件永远不能重传**——
> 版本号发错了只能作废该版本再发一个新号。这道确认值得加。

## 演练

Actions → **Release** → Run workflow，填 tag（如 `v0.1.1`）、pypi 选 `testpypi`。
成功后验证能装：

```sh
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ magplot
```

（`--extra-index-url` 是必要的：TestPyPI 上没有 flask / pymupdf 这些依赖。）

## 发一个新版本

1. 改 `src/magplot/__init__.py` 里的 `__version__`（版本号唯一出处）。
2. 写 `docs/release-notes/vX.Y.Z.md`（见下）。
3. 提交、打 tag、推送：

   ```sh
   git commit -am "0.2.0"
   git tag -a v0.2.0 -m "Magplot 0.2.0"
   git push origin main v0.2.0
   ```

tag 与 `__version__` 对不上时 `build` job 直接失败，不会发出错版本。

**发之前确认 CI 是绿的**——`release.yml` 不重跑测试，它只负责构建与分发。

## Release notes

`docs/release-notes/<tag>.md` 存在就作为 Release 正文，缺失则退回自动生成
（一串提交标题，用户看不出该不该升级，会在 Actions 里留一条 warning）。
用英文写，与 README 一致。

**按症状和触发条件写，不要按提交写**。用户是带着「我这边坏了」来找的，
要能对上号：

> **Scripts calling `plt.close(fig)` produced an empty figure on matplotlib ≥ 3.11.**
> Symptom: double-clicking a panel does nothing, or the element tree comes up empty.
> Trigger: your script closes the figure after saving — the normal pattern when one
> script produces several panels.

`docs/release-notes/v0.1.1.md` 是范例。

## Codex 插件的更新提醒

插件（`codex-plugin/`）随 Magplot 一起发。装了它的用户**不会自动收到更新**——
Codex 不管这件事，所以插件自己每 24 小时查一次清单，有新版就在交接结果里
附一句提醒（只提醒，不下载、不安装）。

发版时这一步是自动的（`release.yml` 的「生成 Codex 插件清单与安装包」），
产出两份挂到 Release：

* `codex-plugin.json` —— 版本清单。**文件名不能改**：插件拉的是
  `releases/latest/download/codex-plugin.json`（`update_check.DEFAULT_URL`）。
* `codex-plugin-<版本>.zip` —— 插件安装包，清单的 `download_url` 指向它。

要手动做一次（或者本地看看长什么样）：

```bash
python scripts/make_plugin_manifest.py --tag v0.7.1 \
  --out out/codex-plugin.json --zip out/codex-plugin-0.7.1.zip
```

**发插件新版的完整流程**：

1. 改 `codex-plugin/.codex-plugin/plugin.json` 的 `version`
   （版本号只有这一处；`tests/test_codex_plugin.py` 盯着它与 `magplot.__version__` 一致）；
2. 正常打 tag 发版——上面那步会自动生成清单与 zip；
3. 用户下次调用插件时看到提醒，执行
   `codex plugin marketplace upgrade magplot` 并重载 Codex。

改 `min_magplot_version`（`scripts/make_plugin_manifest.py` 里的常量）之前想清楚：
那个值会让本机 Magplot 更老的用户看到「去升级 Magplot」的提示。当前是 `0.7.0`
——第一个带 `magplot open` 的版本，没有它交接根本无从谈起。

排障与用户侧开关（`MAGPLOT_UPDATE_URL` / `MAGPLOT_DISABLE_UPDATE_CHECK`）见
`docs/handoff-protocol.md`。

## 本地自检

上传不可撤销，本地先过一遍：

```sh
python scripts/build_frontend.py
python -m build
python -m twine check --strict dist/*     # 元数据 + PyPI 的 README 渲染
```

## 独立应用（.dmg / .exe）

桌面发行**只有一条链路**（v0.3.0 起）：`desktop-tauri.yml`。旧 PyInstaller
直发链（`desktop.yml` + Inno Setup + 免安装 zip）已退役删除，git 历史可找回。
两个平台的桌面产物都是 Tauri 真窗口——不再存在「启动后开浏览器」的桌面包，
也不再有两条链同名 dmg 互相覆盖的问题（v0.2.0 发布时踩过：两条链前后两秒
dispatch，后 attach 的旧链 dmg 顶掉了 Tauri dmg）。

### Tauri 桌面壳（desktop-tauri.yml）

真正的桌面窗口（不再开系统浏览器）：Tauri 2 壳 + `magplot --desktop-sidecar`
后端（127.0.0.1 动态端口 + 一次性 nonce 认证），架构与安全模型见
`docs/adr/0002-tauri-desktop-shell.md`。

手动触发（Actions → **Desktop apps (Tauri)** → 填 tag，需 tag 含 `src-tauri/`）。
本地构建：`python scripts/build_desktop.py`（版本同步 → 前端 → PyInstaller
sidecar → Tauri bundler）。CI 门禁打的是最终产物：sidecar 真二进制过
`scripts/smoke_desktop.py` 全链路（认证/项目/渲染/导出/退出无孤儿），Windows
上另对 Tauri 真 .exe 做启动-探活-退出探针。

| 平台 | 产物 | 说明 |
|---|---|---|
| macOS | `Magplot-X.Y.Z-macOS.dmg` | Tauri .app（内嵌 sidecar）；签名 + 公证复用下述同一套 secret 与流程 |
| Windows | `Magplot-X.Y.Z-Windows-Setup.exe` | NSIS（收集时改成与 wheel/dmg 一致的命名），装到用户目录；**含内置渲染 runtime**；SignPath 启用后由 SignPath Foundation 证书签名 |

macOS 签名注意：sidecar 是 `.app` 里 `Resources/sidecar/` 下的 PyInstaller
onedir，签名必须继续「按 `file` 判断签**所有** Mach-O、自底向上」——只签壳
本体公证会 Invalid（教训同旧链路）。

**Flask 主进程里始终不含 matplotlib**：科学栈只存在于 worker 那一侧。
这条边界一破，包大小与依赖关系立刻失控（`packaging/magplot.spec` 文件头有完整说明）。

打包配置从 tag 检出，所以只能构建含 `src-tauri/` 的 tag（v0.2.0 起）。
免安装 zip 随旧链一起退役：它本质是浏览器模式的 PyInstaller 目录，与「桌面
产物一律真窗口」冲突；确有需要时从历史 tag 走旧链构建。

### Windows 内置渲染 runtime

Windows 安装包**自带一套 Magplot 私有的 Python 渲染环境**，
用户不需要先装 Python，首次渲染也不联网：

```
Magplot.exe → _internal\runtime\python.exe → engine\worker.py → 用户的图表脚本
```

| 东西 | 在哪 |
|---|---|
| 版本锁（CPython 下载地址 + SHA-256、科学栈的完整传递闭包） | `packaging/runtime-lock.json` |
| 构建脚本 | `scripts/build_worker_runtime.py` |
| 定位与校验（唯一出处） | `src/magplot/engine/runtime.py` |
| 产物 | 仓库根的 `runtime/`（**不进 Git**，200 MiB 上下） |

发行流水线里这条链路是这样护住的：

1. `desktop-tauri.yml` 先跑 `scripts/build_worker_runtime.py`。脚本自己会校验
   CPython 压缩包的 SHA-256、按 `.dist-info` 核对装出来的版本、**逐个 import 并
   画一张真图**——任何一步不过就失败在构建机上，而不是留到用户电脑上。
2. sidecar 构建带 `MAGPLOT_REQUIRE_RUNTIME=1`：忘了构建 runtime 会当场失败，
   而不是安静地产出一个装完不能渲染的包。
3. NSIS 经 `tauri.conf.json` 的 `bundle.resources` 把整个 sidecar 目录
   （含 `_internal\runtime`）收走——第 2 条保证了它此刻一定在。
4. 打包后跑一次 `scripts/smoke_app.py --expect-source bundled`：真启动 .exe、
   真渲染、真导出，并断言用的是**内置**解释器而不是构建机上碰巧装着的 Python；
   sidecar 全链路冒烟（smoke_desktop.py）在 Windows 上也刻意不给
   `MM_WORKER_PYTHON`，渲染腿必须走内置 runtime。

同一套门禁在 `ci.yml` 的 `windows-exe-smoke` 里对每个 PR 都跑一遍
（还额外验中文 + 空格路径、以及 `MM_WORKER_PYTHON` 仍然优先）。

**换版本怎么办**（升 CPython 补丁版或某个科学包）：

```sh
# 只换包版本：先改 runtime-lock.json 的 top_level/packages，再重解析闭包
python scripts/build_worker_runtime.py --resolve

# 连 CPython 一起换：脚本会下载、重算 sha256、核对 _pth 名字
python scripts/build_worker_runtime.py --resolve --python-version 3.13.16
```

`--resolve` 只改锁文件，不构建。改完提交锁文件，让 Windows CI 去验实际能不能用。
**别手写闭包**——手写迟早漏一个传递依赖，而漏掉的那个会在用户机器上以
ModuleNotFoundError 的形式出现。

**macOS 不带内置 runtime**，这是有意的：那边装 Python 的门槛低得多，而 `.dmg`
还要过公证——多几万个二进制文件会让签名与公证时间失控。runtime 不存在时
spec 自动跳过，macOS 构建与从前完全一样。

**第三方许可证**：构建脚本会把 CPython 与每个包的许可证收进
`runtime/licenses/`，并生成 `THIRD-PARTY-NOTICES.md` 索引，随安装包一起分发。
新增依赖时不需要额外做什么，但**要确认新包的许可证允许再分发**。

### 应用内更新的一次性设置（更新器签名密钥）

桌面版的「软件内直接更新」靠一对 **minisign 密钥**（与 macOS 代码签名、
Windows 代码签名都是两回事）：公钥写死在 `src-tauri/tauri.conf.json` 的
`plugins.updater.pubkey`，私钥只放 GitHub Secrets。

**没配这对密钥时发行链照常出安装包**，只是这一版进不了自动更新——构建会打
一条 warning，`updater-manifest` job 也会如实跳过。

1. 生成一对（**私钥丢了就没法给已发出去的用户推更新**，请妥善保存）：

   ```sh
   pnpm dlx @tauri-apps/cli@2.11.4 signer generate -w ~/magplot-updater.key
   ```

2. 仓库 Settings → Secrets and variables → Actions 加两条：

   | Secret | 值 |
   |---|---|
   | `TAURI_SIGNING_PRIVATE_KEY` | `~/magplot-updater.key` 的**全部内容** |
   | `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | 生成时设的口令（没设就留空/不建） |

3. 公钥（`~/magplot-updater.key.pub` 的内容）要与 `tauri.conf.json` 里的
   `plugins.updater.pubkey` 一致。**换密钥 = 老版本的用户再也收不到更新**
   （他们壳里烧的是旧公钥），只能引导手动下载一次，非必要不要换。

4. **发版前核一次配对**（配错了极其安静：CI 全绿、资产齐全，只有用户那边
   「更新失败」）：

   ```sh
   printf probe > /tmp/probe.bin
   pnpm dlx @tauri-apps/cli@2.11.4 signer sign -f ~/magplot-updater.key -p "" /tmp/probe.bin
   python scripts/check_updater_key.py --sig /tmp/probe.bin.sig
   ```

   对不上就别发——按那份配置发出去的更新，用户下载完校验失败、装不上。

发出去之后自查：Release 资产里应当有 `latest.json`、`Magplot.app.tar.gz(.sig)`、
`Magplot_<ver>_x64-setup.nsis.zip(.sig)`。少了 `latest.json`，壳那边的表现是
**一直显示「已是最新版本」**——用户停在旧版本上而 CI 全绿，这条要盯。

### macOS 签名与公证的一次性设置

不配下面这些 secret 时，流水线照常出包，只是未签名（adhoc），用户首次打开要
右键 → 打开。配齐后自动变成签名 + 公证 + 装订，双击即开。

1. 加入 [Apple Developer Program](https://developer.apple.com/programs/)（$99/年）。
2. 生成私钥与 CSR：

   ```sh
   mkdir -p ~/magplot-signing && chmod 700 ~/magplot-signing && cd ~/magplot-signing
   openssl genrsa -out developerID.key 2048 && chmod 600 developerID.key
   openssl req -new -key developerID.key -out developerID.csr \
     -subj "/emailAddress=<你的邮箱>/CN=<你的名字>/C=CN"
   ```

3. 到 <https://developer.apple.com/account/resources/certificates/add> 选
   **Developer ID Application**（**不是** Apple Development——后者只能在自己
   设备上跑，不能对外分发也过不了公证），上传 `developerID.csr`，
   把下载到的 `.cer` 放回 `~/magplot-signing/`。
4. 一条命令完成打包与写入 secret：

   ```sh
   scripts/setup_macos_signing.sh
   ```

   它会核对证书类型、附上 Apple 中间 CA（链不完整时别的机器验不过）、
   随机生成 .p12 密码，并写入 `MACOS_CERTIFICATE`、
   `MACOS_CERTIFICATE_PASSWORD`、`MACOS_SIGN_IDENTITY`。

5. 公证还需要一个 App 专用密码（<https://appleid.apple.com> → 登录与安全）：

   ```sh
   printf '<App 专用密码>' | gh secret set APPLE_APP_PASSWORD --repo erwanjun/magplot
   ```

`APPLE_ID`（邮箱）与 `APPLE_TEAM_ID`（10 位，可从
`security find-identity -v` 的证书名括号里读到）也要设上，共六个。
私钥和 .p12 只留在 `~/magplot-signing/`，绝不进版本库。

验证签名是否真的生效：下载 dmg 后 `codesign -dvvv Magplot.app`，要看到
`Authority=Developer ID Application: …`。**只看 `codesign --verify` 会被骗**——
PyInstaller 留下的 adhoc 签名同样能通过 verify；流水线里已加了显式断言。

踩过的两个坑（都已在流水线里堵上，改动签名步骤前先读这段）：

1. **`codesign` 只在钥匙串搜索列表里找身份。** 光 `default-keychain -s` 或传
   `--keychain` 都不够（新版 macOS 上后者不可靠），症状是 `no identity found`。
   更坑的是它会**静默假成功**：PyInstaller 留下的 adhoc 签名让随后的
   `codesign --verify` 照样通过。所以流水线里除了用
   `security list-keychains -d user -s` 显式加入，还显式断言
   `Authority=Developer ID Application`。

2. **要签的不只是 `*.dylib` / `*.so`。** 包里还有两个无后缀的 Mach-O——
   `Contents/MacOS/Magplot` 和 `Contents/Frameworks/Python.framework/Versions/*/Python`，
   漏签就公证 Invalid。改成按 `file` 的判断签所有 Mach-O。
   公证失败时流水线会自动打印 `notarytool log`——没有它，`status: Invalid`
   就是个哑谜。

### Windows 签名

Windows 签名通过 SignPath Foundation 的开源项目订阅完成。仓库中的
`signpath/windows-installer.artifact-configuration.xml` 描述上传的 GitHub
Actions ZIP 中应签名的 NSIS 安装包及其产品/版本元数据。

获批并在 SignPath 中创建项目后：

1. 安装 SignPath GitHub App，并允许它访问本仓库。
2. 在仓库中创建以下变量：
   `SIGNPATH_ENABLED=true`、`SIGNPATH_ORGANIZATION_ID`、
   `SIGNPATH_PROJECT_SLUG`、`SIGNPATH_SIGNING_POLICY_SLUG`、
   `SIGNPATH_ARTIFACT_CONFIGURATION_SLUG`。
3. 创建仓库 secret `SIGNPATH_API_TOKEN`。Token 只保存在 GitHub Secrets，
   不写入仓库或日志。
4. 在 SignPath 的 artifact configuration 中导入
   `signpath/windows-installer.artifact-configuration.xml`，并把 slug 填入
   `SIGNPATH_ARTIFACT_CONFIGURATION_SLUG`。
5. 对 tag 运行 `Desktop apps (Tauri)`。工作流会先把未签名安装包作为 GitHub
   Actions artifact 提交签名，再下载签名结果并把它挂到同一个 GitHub Release。

`SIGNPATH_ENABLED` 未开启时，工作流仍可生成测试用的未签名安装包，但会明确标记
为未签名；不要把该产物当作正式发行版。当前配置签名的是下载给用户的 NSIS 外层
安装包；若以后需要对安装包内部的每个 PE 文件做深度签名，应改用 SignPath 支持
深度签名的 MSI 发行链。

### 改图标

`assets/icon/icon.svg` 是唯一出处；改完在 macOS 上跑
`python scripts/build_icons.py` 重新生成 `.icns` / `.ico` 并提交
（CI 机器上没有 SVG 渲染器，产物进版本库）。
