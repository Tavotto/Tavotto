# 发版

**一条流水线 `release.yml` 是唯一入口**，推 `v*` tag 或手动 dispatch 触发，
七个 job：

| job | 做什么 |
|---|---|
| `trust` | ref → 精确 SHA，验证它**可达于 origin/main**（发布只接受已合并进受保护 main 的提交），从源码读出版本号并与 tag 核对；之后所有 job 只认它输出的 SHA |
| `build` | 构建前端 → 打 wheel + sdist → 核对版本 → `twine check` → 干净环境装一遍冒烟 → **产出这条腿的产物清单** |
| `desktop` | `workflow_call` 调 `desktop-tauri.yml`：Windows NSIS + macOS 签名公证 dmg，各自**产出自己那条腿的产物清单** |
| `lab_release_gate` | `workflow_call` 调 `_lab-qualification.yml`（`mode: release`）：实验室 runner 上的 exact-artifact 发行资格验证——**这一关不过，什么都不发** |
| `validate_artifacts` | 合并三条腿的清单并**逐条核对 sha256 与 source_sha** → provenance → SBOM → SHA256SUMS → Codex 插件清单 → release notes。**演练也跑这一整段** |
| `github_release` | 建 GitHub Release，**一次挂全部**（只在 `publish=true`） |
| `pypi` | 发到 PyPI（只在 `publish=true`，且需开闸，见下） |

```
tag push / workflow_dispatch
        │
      trust ──┬──► build ────────────────┐
              ├──► desktop（workflow_call）┤
              └──► lab_release_gate ──────┴──► validate_artifacts
                   （workflow_call）                  │
                                          publish=true 才继续
                                                 ├─ github_release
                                                 └─ pypi
```

## `publish=false`：正式 tag 不该承担「第一次测这条链」

**推荐流程：**

```bash
# 1. main 上定好版本，拿到精确 SHA
git rev-parse origin/main

# 2. 在那个 SHA 上跑一次完整演练（默认就是 publish=false）
gh workflow run release.yml --ref main -f ref=<那个 SHA>

# 3. 演练全绿之后，在**同一个 SHA** 上打正式 tag
git tag -a v1.0.0 <那个 SHA> -m "v1.0.0" && git push origin v1.0.0
```

演练会在指定 SHA 上走完整条链——wheel/sdist、Windows 与 macOS 最终产物
（含签名与公证）、发行资格验证、SBOM、checksum、provenance、updater 清单、
产物清单校验——**唯独不建 Release、不发 PyPI、不打 tag**。

**为什么非要有这一档**：v0.9.0 与 v0.9.1 两个正式 tag 都是在发布链上第一次
被执行时炸掉的（一个是 SBOM 把 glob 当文件名，一个是汇总步骤自己挂掉），
而 tag ruleset 是 immutable——它们至今改不动也删不掉，仓库里躺着两个没有
Release 的 tag。完整经过见
`docs/audit/2026-08-22-v1-release-process-audit.md` §5–6。

## 产物清单是下游唯一的文件名出处

三条构建腿各产出一份 `artifact-manifest-*.json`（role / path / sha256 /
platform），`validate_artifacts` 合并成一份并校验：

- 每个必须角色**恰好一个**（两个 wheel 谁都不会报错，而用户装到的和我们
  验过的不是同一个）；
- **`source_sha` 必须全都一样**——「同一个 tag」证明不了「同一个 commit」，
  这是唯一能挡住两条腿分叉的地方；
- 清单里**不许出现通配符**（`artifact_manifest.build` 直接拒绝）。

SBOM、SHA256SUMS、provenance、updater 清单、Release 附件、PyPI 校验一律
读这份清单，不再各自猜文件名——#63 那个 bug（`dist/*.whl` 喂给只认单个
路径的 syft）就是七处各猜一次里的一处。

发布 job 用的是 `build` 上传的**同一份** artifact，且在挂上去之前**再校验
一次哈希**：下载 artifact 再上传是一次真实的搬运，而「Release 上挂的与
发行资格验证过的不是同一个东西」是这条链上最不能接受的失败。

## 发行资格验证只有一份定义

`_lab-qualification.yml` 是唯一定义，`lab-ci.yml`（push/schedule）与
`release.yml`（发布链）都 `uses:` 它，`mode` 只决定阈值、基线和跑哪几档。

从前它在两个文件里各有一份手抄的 shell，两处的差别实测只有
「`$LAB_MODE` vs 字面量 release」和一处换行——修一个 bug（#61）必须同时
改两处，而抄两遍的代价不是多打字，是**总有一天只改了一处**。

## 桌面链不再由 tag 触发，也不再等待任何东西

`desktop-tauri.yml` 现在**只能被 `workflow_call` 调用**（或手动 dispatch
单独构建，那时不挂 Release）。它拿的是 `trust` 已经验过的精确 SHA，
构建完只把产物传成 workflow artifact；挂 Release 归 `github_release` 一家。

从前它由同一个 tag 并行触发，构建完轮询 `gh release view` 最长 190 分钟
等 release.yml 建出 Release。那不是「等太久」的问题：lab gate 的**排队**
时间本身没有上界，而两条腿各自 checkout tag 意味着 wheel 与桌面产物**没有
任何机制保证来自同一个 commit**。

桌面链保留**发行签名门禁**：`publish=true` 的构建缺任一签名、公证或
updater 私钥直接失败，不再是 warning 后继续；第三方 Actions 在所有
secret-bearing 工作流里一律钉死到 commit SHA。

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
| PyPI Project Name | `tavotto` |
| Owner | `Tavotto` |
| Repository name | `tavotto` |
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
            --extra-index-url https://pypi.org/simple/ tavotto
```

（`--extra-index-url` 是必要的：TestPyPI 上没有 flask / pymupdf 这些依赖。）

## 发一个新版本

1. 改 `src/tavotto/__init__.py` 里的 `__version__`（版本号唯一出处）。
2. 写 `docs/release-notes/vX.Y.Z.md`（见下）。
3. 提交、打 tag、推送：

   ```sh
   git commit -am "0.2.0"
   git tag -a v0.2.0 -m "Tavotto 0.2.0"
   git push origin main v0.2.0
   ```

4. 发布链绿了之后同步网站 `/try`（在 `Tavotto_website` 仓库）：

   ```sh
   TAVOTTO_REPO=<发布 SHA 所在的那棵树> pnpm sync-playground
   TAVOTTO_REPO=<发布 SHA 所在的那棵树> pnpm check-playground
   ```

   **这两条命令必须显式带 `TAVOTTO_REPO`**，理由见下面一节。

tag 与 `__version__` 对不上时 `build` job 直接失败，不会发出错版本。

**发之前确认 CI 是绿的**——`release.yml` 的 `lab_release_gate` 会对候选 wheel
重跑全量 + slow 用例、升级验收与视觉回归（exact artifact），但那是**发行资格
验证**，不是替代日常 CI：tag 只应打在 CI 已经全绿、且已合并进 main 的提交上
（`trust` job 会硬校验 main 可达性，够不着直接拒）。

## 网站 `/try`：同步与复核必须指名读的是哪棵树

浏览器 playground 的产物由本仓库构建（`scripts/build_browser_playground.py`），
由网站仓库 `Tavotto_website` 分发（`public/try/`，见 ADR 0007）。两个脚本
`pnpm sync-playground` 与 `pnpm check-playground` 都用 `TAVOTTO_REPO` 定位产品
仓库，**默认 `../Tavotto`——那是主工作区，而主工作区停在谁的分支上没有任何
机制保证**。发布 SHA 与主工作区 HEAD 是两个不同的事实。

**复核 playground 必须带 `TAVOTTO_REPO` 指向发布 SHA 所在的那棵树**：

```sh
# 先自证这棵树就是发布 SHA（trust job 认的那个），别凭印象
git -C <发布树> rev-parse HEAD

cd ../Tavotto_website
TAVOTTO_REPO=<发布树> pnpm sync-playground
TAVOTTO_REPO=<发布树> pnpm check-playground
```

**为什么不能省**：v0.12.0 发版时主工作区停在落后 main 五个 PR 的提交上，
两条后果都实测发生过（issue #148）：

1. `sync-playground` 把一份来自非发布祖先的陈旧产物拷进了 `public/try/`
   （指纹 `fcfb77bc`，而发布树建出来是 `53b8a6ab`）——只因为指纹对不上才发现；
2. 同步纠正之后 `check-playground` 仍报 `playground stale`，因为它是从**主工作区**
   算的源指纹。照着这条红去「重做同步」，重做又把错版本拷回来——**成环**。

两个脚本现在开跑前都打印读到的路径、这个路径的来源（`TAVOTTO_REPO` 还是默认值）
与那棵树的 HEAD，并在该 commit 不可达于 `origin/main` 时告警；`check` 的 `FAIL`
文本里也带着同样三样。**看到 stale 先读那三行**：先确认路径与 commit 是你要的
那棵树，再谈重建与重新同步。实现见网站仓库 `scripts/lib/product-repo.mjs`。

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

插件（`codex-plugin/`）随 Tavotto 一起发。装了它的用户**不会自动收到更新**——
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
   （版本号只有这一处；`tests/test_codex_plugin.py` 盯着它与 `tavotto.__version__` 一致）；
2. 正常打 tag 发版——上面那步会自动生成清单与 zip；
3. 用户下次调用插件时看到提醒，执行
   `codex plugin marketplace upgrade tavotto` 并重载 Codex。

改 `min_tavotto_version`（`scripts/make_plugin_manifest.py` 里的常量）之前想清楚：
那个值会让本机 Tavotto 更老的用户看到「去升级 Tavotto」的提示。当前是 `0.7.0`
——第一个带 `tavotto open` 的版本，没有它交接根本无从谈起。

排障与用户侧开关（`TAVOTTO_UPDATE_URL` / `TAVOTTO_DISABLE_UPDATE_CHECK`）见
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

真正的桌面窗口（不再开系统浏览器）：Tauri 2 壳 + `tavotto --desktop-sidecar`
后端（127.0.0.1 动态端口 + 一次性 nonce 认证），架构与安全模型见
`docs/adr/0002-tauri-desktop-shell.md`。

手动触发（Actions → **Desktop apps (Tauri)** → 填 tag，需 tag 含 `src-tauri/`）。
本地构建：`python scripts/build_desktop.py`（版本同步 → 前端 → PyInstaller
sidecar → Tauri bundler）。CI 门禁打的是最终产物：sidecar 真二进制过
`scripts/smoke_desktop.py` 全链路（认证/项目/渲染/导出/退出无孤儿），Windows
上另对 Tauri 真 .exe 做启动-探活-退出探针。

| 平台 | 产物 | 说明 |
|---|---|---|
| macOS | `Tavotto-X.Y.Z-macOS.dmg` | Tauri .app（内嵌 sidecar）；**含内置渲染 runtime**；**仅 arm64**；签名 + 公证复用下述同一套 secret 与流程 |
| Windows | `Tavotto-X.Y.Z-Windows-Setup.exe` | NSIS（收集时改成与 wheel/dmg 一致的命名），装到用户目录；**含内置渲染 runtime**；SignPath 启用后由 SignPath Foundation 证书签名 |

macOS 签名注意：sidecar 是 `.app` 里 `Resources/sidecar/` 下的 PyInstaller
onedir，签名必须继续「签**所有**嵌套 Mach-O、自内向外」——只签壳本体公证会
Invalid（教训同旧链路）。内置 runtime 进来之后这件事从「几十个」变成
「五百多个」（解释器 + numpy/scipy/pandas 的全部扩展模块），且它们全都躺在
`Contents/Resources` 下、**不被 `codesign --deep` 识别为嵌套代码**——所以签名与
验收都走 `scripts/codesign_macos.py`（读魔数找 Mach-O、深度降序自内向外、
只给可执行文件挂 entitlements、最后逐个 `--verify` 一遍）。

**Flask 主进程里始终不含 matplotlib**：科学栈只存在于 worker 那一侧。
这条边界一破，包大小与依赖关系立刻失控（`packaging/tavotto.spec` 文件头有完整说明）。

打包配置从 tag 检出，所以只能构建含 `src-tauri/` 的 tag（v0.2.0 起）。
免安装 zip 随旧链一起退役：它本质是浏览器模式的 PyInstaller 目录，与「桌面
产物一律真窗口」冲突；确有需要时从历史 tag 走旧链构建。

### 内置渲染 runtime（macOS 与 Windows）

两个平台的安装包都**自带一套 Tavotto 私有的 Python 渲染环境**，
用户不需要先装 Python，首次渲染也不联网：

```
Windows: Tavotto.exe → _internal\runtime\python.exe  → engine\worker.py → 用户的脚本
macOS:   Tavotto.app → …/_internal/runtime/bin/python3.13 → engine/worker.py → 用户的脚本
```

| 东西 | 在哪 |
|---|---|
| 版本锁（CPython 下载地址 + SHA-256、科学栈的完整传递闭包，**按平台/架构分层**） | `packaging/runtime-lock.json`（schema 2） |
| 构建脚本 | `scripts/build_worker_runtime.py` |
| 定位与校验（唯一出处） | `src/tavotto/engine/runtime.py` |
| 「这份 runtime 配不配得上这次构建」的唯一判据 | `build_worker_runtime.check_runtime_dir()`（spec 与 build_desktop 共用） |
| 签名与验收 | `scripts/codesign_macos.py` |
| 产物 | 仓库根的 `runtime/`（**不进 Git**，300 MiB 上下） |

**两个平台的上游发行版不同，理由也不同：**

| 平台 | 上游 | 为什么是它 |
|---|---|---|
| Windows | 官方 [embeddable 发行版](https://docs.python.org/3/using/windows.html#the-embeddable-package) | Python 官方就把它定位成「应用私有的运行时，第三方包由安装程序一起提供」 |
| macOS | [python-build-standalone](https://github.com/astral-sh/python-build-standalone)（`install_only`） | 官方 macOS 安装器装的是 `/Library/Frameworks` 下的固定路径，**不可重定位**，嵌不进 `.app`；Homebrew / Conda 是用户自己的环境，我们不碰。pbs 的 prefix 由解释器自身路径推导，挪到哪都能跑，而且是逐个可 codesign 的普通 Mach-O——公证要求每个嵌套二进制都签得到名 |

**三个目标的闭包目前逐字相同**，这是刻意维持的：同版本的 matplotlib/numpy 才能
保证同一个脚本在 Windows 和 macOS 上画出同一张图（`tests/test_runtime_build.py`
里有一条用例盯着）。哪天解析结果真的分叉了，不要硬凑——如实记下来并在发布说明里讲清楚。

**架构范围（如实记录，别扩大）**：目前只发 **macOS arm64**。`macos-x86_64`
在锁文件里标着 `shipped: false`——版本锁着是为了「要发时不用临时定版本」，但
CI 的 macOS runner 只有 Apple Silicon 一档，因此那个目标**既没构建过也没冒烟过**。
真要发 Intel 版，先有 Intel runner（或带 Rosetta 的机器）跑完整的 import +
真实绘图冒烟，把 `shipped` 改成 true，再改 README——**在那之前 README 里不许
出现「支持 Intel」**。同理，目前**不产出 universal2**：科学栈的 wheel 是分架构
发布的，把两份 .so 硬拼成 universal2 没有验证过，不能凭「应该可以」就发。

发行流水线里这条链路是这样护住的（**两个平台同一套**）：

1. `desktop-tauri.yml` 先跑 `scripts/build_worker_runtime.py`（目标按 runner 的
   平台/架构自动挑）。脚本自己会校验 CPython 归档的 SHA-256（macOS 那份的期望值
   取自 pbs 上游发布的 `SHA256SUMS`）、按 `.dist-info` 核对装出来的版本、
   **用刚装好的解释器逐个 import 并画一张真图**——任何一步不过就失败在构建机上，
   而不是留到用户电脑上。
2. sidecar 构建带 `TAVOTTO_REQUIRE_RUNTIME=1`。此时 `tavotto.spec` 会再确认三件事：
   清单 schema 对得上、**平台/架构与本次构建一致**、冒烟状态是 `passed`。
   第二条挡的是最贵的一种错——Windows 的 runtime 被打进 `.app`，用户那边的症状是
   「渲染环境不可用」而构建全程绿灯。第三条挡的是 `--allow-skip-smoke` 产出的
   中间件混进安装包（那份一个 import 都没跑过）。
3. NSIS / `.app` 经 `tauri.conf.json` 的 `bundle.resources` 把整个 sidecar 目录
   （含 `_internal/runtime`）收走——第 2 条保证了它此刻一定在。
4. 打包后跑 `scripts/smoke_app.py --expect-source bundled --expect-runtime`：
   真启动、真渲染两次（冷 + 热）、真导出两次（含覆盖），并断言用的是**内置**
   解释器、runtime 本身 `expected` 且 `valid`、控制面确实是 workerd。
   脚本会把 `TAVOTTO_WORKER_PYTHON`、Conda、`PYTHONHOME`/`PYTHONPATH`、活动 venv
   一律从子进程环境摘掉——**验的是「一台干净电脑上装完即可用」**。
5. macOS 额外再来一遍：签完名之后，把 `.app` 用 `ditto` 拷到一个**中文 + 空格**
   的路径，重验签名，再对 `.app` 里的 sidecar 跑一次同样的 smoke_app。
   前面那次打的是 `dist/Tavotto`（PyInstaller 裸产物），这一次打的才是用户拿到
   的东西——hardened runtime 会不会拦下内置解释器加载 numpy 的 .dylib、
   签名有没有把某个扩展模块弄坏，只有真跑一次才知道。

> **曾经的坑**：macOS 这条腿上一度有一步「现建 worker-env 再设
> `TAVOTTO_WORKER_PYTHON`」。代价是整条门禁失去意义——借来的解释器让冒烟一路绿灯，
> 而「内置 runtime 根本没打进安装包」这件事没有任何一处会发现。
> `tests/test_runtime_build.py::test_macos_ci_no_longer_fakes_a_worker_env`
> 盯着它别被人顺手加回来。

同一套门禁在 `ci.yml` 的 `windows-exe-smoke` 里对每个 PR 都跑一遍
（还额外验中文 + 空格路径、以及 `TAVOTTO_WORKER_PYTHON` 仍然优先）。

**换版本怎么办**（升 CPython 补丁版或某个科学包）：

```sh
python scripts/build_worker_runtime.py --list-targets

# 只换包版本：先改 runtime-lock.json 的 top_level/packages，再逐个目标重解析闭包
python scripts/build_worker_runtime.py --resolve --target windows-amd64
python scripts/build_worker_runtime.py --resolve --target macos-arm64
python scripts/build_worker_runtime.py --resolve --target macos-x86_64

# 连 CPython 一起换
python scripts/build_worker_runtime.py --resolve --target windows-amd64 \
    --python-version 3.13.16        # 下载、重算 sha256、核对 _pth 名字
python scripts/build_worker_runtime.py --resolve --target macos-arm64 \
    --pbs-release 20260814          # 从上游 SHA256SUMS 取校验和
```

`--resolve` 只改锁文件，不构建。**三个目标要一起换**，否则跨平台版本会漂移
（那条用例会红）。改完提交锁文件，让 CI 去验实际能不能用。
**别手写闭包**——手写迟早漏一个传递依赖，而漏掉的那个会在用户机器上以
ModuleNotFoundError 的形式出现。

**第三方许可证**：构建脚本会把 CPython 与每个包的许可证收进
`runtime/licenses/`，并生成 `THIRD-PARTY-NOTICES.md` 索引，随安装包一起分发。
新增依赖时不需要额外做什么，但**要确认新包的许可证允许再分发**。

macOS 的 pbs 发行版把 OpenSSL、SQLite、libffi、libedit、Tcl/Tk、zlib、bzip2、XZ
静态链接进 CPython，**全部是宽松许可**（Apache-2.0 / MIT / BSD / 公有领域）；
行编辑用的是 **libedit 而不是 GNU readline**（实测
`readline._READLINE_LIBRARY_VERSION == "EditLine wrapper"`），因此桌面分发
不引入任何 copyleft 义务。来源与说明写进
`runtime/licenses/cpython/UPSTREAM-BUILD.md` 随包发出。
**换上游或换 flavor 时必须重新确认这一条**，别默认它还成立。

### 应用内更新的一次性设置（更新器签名密钥）

桌面版的「软件内直接更新」靠一对 **minisign 密钥**（与 macOS 代码签名、
Windows 代码签名都是两回事）：公钥写死在 `src-tauri/tauri.conf.json` 的
`plugins.updater.pubkey`，私钥只放 GitHub Secrets。

**没配这对密钥时发行链照常出安装包**，只是这一版进不了自动更新——构建会打
一条 warning，`updater-manifest` job 也会如实跳过。

> **改名不换密钥。** 这对密钥是 Magplot 时代生成的，本机那份仍叫
> `~/magplot-updater.key`，Actions 里的 secret 也没动。下面写的是新名，
> 只是本地文件名的约定——`mv ~/magplot-updater.key ~/tavotto-updater.key`
> （连 `.pub` 一起）即可，**密钥内容一个字节都不许换**：壳里烧的是旧公钥，
> 换了等于 0.7.0 及更早的用户再也收不到更新。

1. 生成一对（**私钥丢了就没法给已发出去的用户推更新**，请妥善保存）：

   ```sh
   pnpm dlx @tauri-apps/cli@2.11.4 signer generate -w ~/tavotto-updater.key
   ```

2. 仓库 Settings → Secrets and variables → Actions 加两条：

   | Secret | 值 |
   |---|---|
   | `TAURI_SIGNING_PRIVATE_KEY` | `~/tavotto-updater.key` 的**全部内容** |
   | `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | 生成时设的口令（没设就留空/不建） |

3. 公钥（`~/tavotto-updater.key.pub` 的内容）要与 `tauri.conf.json` 里的
   `plugins.updater.pubkey` 一致。**换密钥 = 老版本的用户再也收不到更新**
   （他们壳里烧的是旧公钥），只能引导手动下载一次，非必要不要换。

4. **发版前核一次配对**（配错了极其安静：CI 全绿、资产齐全，只有用户那边
   「更新失败」）：

   ```sh
   printf probe > /tmp/probe.bin
   pnpm dlx @tauri-apps/cli@2.11.4 signer sign -f ~/tavotto-updater.key -p "" /tmp/probe.bin
   python scripts/check_updater_key.py --sig /tmp/probe.bin.sig
   ```

   对不上就别发——按那份配置发出去的更新，用户下载完校验失败、装不上。

发出去之后自查：Release 资产里应当有 `latest.json`、`Tavotto.app.tar.gz(.sig)`、
`Tavotto_<ver>_x64-setup.nsis.zip(.sig)`。少了 `latest.json`，壳那边的表现是
**一直显示「已是最新版本」**——用户停在旧版本上而 CI 全绿，这条要盯。

### macOS 签名与公证的一次性设置

不配下面这些 secret 时，流水线照常出包，只是未签名（adhoc），用户首次打开要
右键 → 打开。配齐后自动变成签名 + 公证 + 装订，双击即开。

> 目录名跟着改名换成了 `~/tavotto-signing`。本机已有 `~/magplot-signing` 的话
> 直接 `mv` 过去即可——证书与私钥本身与产品名无关，不必重新申请。

1. 加入 [Apple Developer Program](https://developer.apple.com/programs/)（$99/年）。
2. 生成私钥与 CSR：

   ```sh
   mkdir -p ~/tavotto-signing && chmod 700 ~/tavotto-signing && cd ~/tavotto-signing
   openssl genrsa -out developerID.key 2048 && chmod 600 developerID.key
   openssl req -new -key developerID.key -out developerID.csr \
     -subj "/emailAddress=<你的邮箱>/CN=<你的名字>/C=CN"
   ```

3. 到 <https://developer.apple.com/account/resources/certificates/add> 选
   **Developer ID Application**（**不是** Apple Development——后者只能在自己
   设备上跑，不能对外分发也过不了公证），上传 `developerID.csr`，
   把下载到的 `.cer` 放回 `~/tavotto-signing/`。
4. 一条命令完成打包与写入 secret：

   ```sh
   scripts/setup_macos_signing.sh
   ```

   它会核对证书类型、附上 Apple 中间 CA（链不完整时别的机器验不过）、
   随机生成 .p12 密码，并写入 `MACOS_CERTIFICATE`、
   `MACOS_CERTIFICATE_PASSWORD`、`MACOS_SIGN_IDENTITY`。

5. 公证还需要一个 App 专用密码（<https://appleid.apple.com> → 登录与安全）：

   ```sh
   printf '<App 专用密码>' | gh secret set APPLE_APP_PASSWORD --repo Tavotto/Tavotto
   ```

`APPLE_ID`（邮箱）与 `APPLE_TEAM_ID`（10 位，可从
`security find-identity -v` 的证书名括号里读到）也要设上，共六个。
私钥和 .p12 只留在 `~/tavotto-signing/`，绝不进版本库。

验证签名是否真的生效：下载 dmg 后 `codesign -dvvv Tavotto.app`，要看到
`Authority=Developer ID Application: …`。**只看 `codesign --verify` 会被骗**——
PyInstaller 留下的 adhoc 签名同样能通过 verify；流水线里已加了显式断言。

踩过的坑（都已在流水线里堵上，改动签名步骤前先读这段）：

1. **`codesign` 只在钥匙串搜索列表里找身份。** 光 `default-keychain -s` 或传
   `--keychain` 都不够（新版 macOS 上后者不可靠），症状是 `no identity found`。
   更坑的是它会**静默假成功**：PyInstaller 留下的 adhoc 签名让随后的
   `codesign --verify` 照样通过。所以流水线里除了用
   `security list-keychains -d user -s` 显式加入，还显式断言
   `Authority=Developer ID Application`。

2. **要签的不只是 `*.dylib` / `*.so`。** 包里还有无后缀的 Mach-O——
   `Contents/MacOS/Tavotto`、sidecar 的 `Tavotto`、内置 runtime 的
   `bin/python3.13`——漏签就公证 Invalid。所以判据是**读魔数**（`scripts/
   codesign_macos.py`），不是看扩展名。

3. **`--deep` 不是签名策略。** Apple 自己把它标为「仅用于救急」，它对
   `Contents/Resources` 下那些**不被识别为嵌套代码**的 Mach-O 根本不去签——
   而内置渲染 runtime 的五百多个 `.so`/`.dylib` 正好全在那儿。
   同理，`codesign --verify --deep` **也验不出**「Resources 里躺着一个没签名的
   .so」：那种文件是被当作*资源*封进签名的，封条本身合法。所以验收必须
   **逐个** `--verify`（`codesign_macos.py verify` 就是干这个的，
   顺带核对每个 Mach-O 的架构）。

4. **顺序必须自内向外。** 先签好每个嵌套二进制，最后再签 `.app`；反过来的话，
   外层签名会被内层的后续改动作废。脚本按路径深度降序排，天然满足。

5. **entitlements 只给可执行文件。** 内置解释器需要
   `disable-library-validation` 才敢加载 numpy/scipy 带的那些 `.dylib`；
   给 `.dylib` 挂 entitlements 是无意义的噪音。

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
