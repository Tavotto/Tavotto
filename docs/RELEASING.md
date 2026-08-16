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

## 本地自检

上传不可撤销，本地先过一遍：

```sh
python scripts/build_frontend.py
python -m build
python -m twine check --strict dist/*     # 元数据 + PyPI 的 README 渲染
```

## 独立应用（.dmg / .exe）

`desktop.yml`，手动触发（Actions → **Desktop apps** → 填 tag）。产物：

| 平台 | 产物 | 说明 |
|---|---|---|
| macOS | `Magplot-X.Y.Z-macOS.dmg` | 拖进 Applications；配了 secret 则已签名 + 公证 |
| Windows | `Magplot-X.Y.Z-Windows-Setup.exe` | Inno Setup，装到用户目录、不弹 UAC |
| Windows | `Magplot-X.Y.Z-Windows-portable.zip` | 免安装，给禁止运行未签名安装程序的环境 |

**包里不含 matplotlib**，这是设计决定而不是遗漏：渲染的是用户自己的脚本，
它们要 import 用户自己那套依赖，我们塞任何科学栈进去都满足不了，还要多背
一两百 MB。独立应用靠 `pool.find_worker_python()` 找用户已有的环境
（`packaging/magplot.spec` 文件头有完整说明）。

打包配置从 tag 检出，所以**只能构建含 `packaging/` 的 tag**（v0.1.2 起）。

### macOS 签名与公证的一次性设置

不配下面这些 secret 时，流水线照常出包，只是未签名（adhoc），用户首次打开要
右键 → 打开。配齐后自动变成签名 + 公证 + 装订，双击即开。

1. 加入 [Apple Developer Program](https://developer.apple.com/programs/)（$99/年）。
2. 在钥匙串里创建 **Developer ID Application** 证书，导出为 .p12（设个密码）。
3. 在 <https://appleid.apple.com> 生成一个 **App-Specific Password**（公证用）。
4. 仓库 Settings → Secrets and variables → Actions → Secrets 添加：

   | Secret | 值 |
   |---|---|
   | `MACOS_CERTIFICATE` | `base64 -i cert.p12` 的输出 |
   | `MACOS_CERTIFICATE_PASSWORD` | 导出 .p12 时设的密码 |
   | `MACOS_SIGN_IDENTITY` | 如 `Developer ID Application: Your Name (TEAMID)` |
   | `APPLE_ID` | 你的 Apple ID 邮箱 |
   | `APPLE_APP_PASSWORD` | 上一步的 App-Specific Password |
   | `APPLE_TEAM_ID` | 10 位 Team ID |

验证签名是否真的生效：下载 dmg 后 `codesign -dv Magplot.app`，
`Signature=adhoc` 表示没签上，正式签名会显示 `TeamIdentifier`。

### Windows 签名

暂未做。用户首次运行会看到 SmartScreen「Windows 已保护你的电脑」，
需点「更多信息 → 仍要运行」。要消除需买代码签名证书（OV 约 $200-400/年，
或 Azure Trusted Signing）。

### 改图标

`assets/icon/icon.svg` 是唯一出处；改完在 macOS 上跑
`python scripts/build_icons.py` 重新生成 `.icns` / `.ico` 并提交
（CI 机器上没有 SVG 渲染器，产物进版本库）。
