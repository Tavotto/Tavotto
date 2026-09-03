# 实验室 self-hosted runner

Tavotto 的 CI 分两层。**GitHub 托管 runner 负责跨平台正确性门禁**（PR、
Windows/macOS 真产物冒烟、CodeQL），这一层不会因为本文档而改变。本文档描述的
是新增的第二层：一台实验室里的 Linux 长期 runner，专门做**发行资格验证**
（Release Qualification）——那些临时 runner 很难可靠完成的事。

它的职责与不该碰的东西同样重要，先说边界。

---

## 1. 安全边界（先读这一节）

Tavotto 是**公开仓库**。self-hosted runner 是一台长期存在、有磁盘、有网络的
真机。这两件事放在一起，只有一条不可协商的规矩：

> **绝不让 `pull_request` 触发的 job 跑在 self-hosted runner 上。**

公开仓库的 PR 里是**不可信代码**——任何人都能开 PR。让它在长期 runner 上执行，
等于把这台机器交给互联网。

「fork 的 PR 默认拿不到 secrets」**不构成安全依据**：攻击者要的是这台机器本身
（它的磁盘、它的内网位置、它上面缓存的一切），不是仓库的 secret。

因此 `lab-ci.yml` 的触发只有三种，全部只有维护者能造成：

| 触发 | 为什么可信 |
|---|---|
| `push` 到 `main` | 代码已经过 review 并合入 |
| `schedule` | 跑的是 `main` 的当前状态 |
| `workflow_dispatch` | 需要仓库写权限；且任意 ref **必须**先经 trust-check |

### 手动触发时的 SHA 校验

`workflow_dispatch` 允许传 `ref`。这个 ref **不会**被直接送到 self-hosted：

```
workflow_dispatch(ref)
        │
        ▼
GitHub 托管机上的 trust-check
        ├─ 解析成精确 SHA
        ├─ 验证：是 origin/main 的祖先，或有 tag 指向它
        └─ 输出 SHA
                │
                ▼
        self-hosted runner
                └─ 只 checkout 这个 SHA
```

**不能把分支名一路传下去**：分支是可移动的引用，验证之后、执行之前它仍可能
被指向别处。验证的产物必须是一个不可变的 SHA。

验证不通过就直接失败，没有降级路径。

### runner 自身不该持有的东西

这台 VM 上**不应该**存在：

- SSH 私钥（除了管理员登录用的，且那把不该有仓库权限）
- 云生产环境凭据
- Apple 签名证书 / SignPath 私钥 / PyPI token
- 用户真实科研数据
- 实验室共享盘凭据
- 任何与 CI 无关的 secret

对应地，workflow 侧也做了限制：`lab-ci.yml` 与 `release.yml` 的
`lab_release_gate` 都只有 `contents: read`。它们只是测试，不建 Release、
不发 PyPI、不推代码。发行签名那些能力**留在 GitHub 托管机上**。

### 更安全的备用形态

仓库级 runner 没有 runner group 概念。若组织升级到支持 runner group，
**强烈建议**改成：

```yaml
runs-on:
  group: tavotto-trusted
  labels: [tavotto-lab]
```

并把该 group 限制到 `Tavotto/Tavotto` 以及明确的 trusted workflow。

如果连这一步都无法可靠限制 public repo 对 runner 的访问，更安全的形态是：

```
private Tavotto/ci-infra 仓库
        └── 持有 self-hosted runner
                └── 只 checkout 已验证的 Tavotto main/tag SHA
```

即：runner 注册在一个私有仓库上（公开仓库的 PR 天然够不到它），由该仓库的
workflow 主动去拉取并验证 Tavotto 的可信 commit。代价是多一个仓库要维护。

**不要为了方便在代码里降低这条边界。**

---

## 2. 机器要求

| 项 | 推荐 | 最低 | 说明 |
|---|---|---|---|
| 系统 | Ubuntu 24.04 LTS | Ubuntu 22.04 | 与 corpus 基线绑定，换大版本要重建视觉基线 |
| CPU | 16 vCPU | 8 | 低于 8 时 benchmark 的并发假设不再成立 |
| 内存 | 32 GiB | 16 | |
| 磁盘 | 150 GiB SSD | 100 GiB | 持久化基线 + 一次性 venv + 构建缓存 |
| GPU | **不需要** | | 渲染全走 Agg |

它是实验室服务器上的一台**独立 VM**：

```
宿主机 / 实验室网络
        └── VM
             └── GitHub Runner
```

**不要**把宿主文件系统直接挂给 runner。

### 网络

CI 需要的出站：GitHub、PyPI、npm、crates.io、nodejs.org、静态资源 CDN。

建议**禁止这台 VM 主动访问其它实验室服务器**；管理员的 SSH 登录单独放行。
具体规则由管理员按实验室策略配置——`bootstrap_lab_runner.sh` 刻意不碰防火墙。

> **本实验室的实际网络限制**
>
> 现网环境下 `github.com`（`20.205.243.166`）的 22 与 443 **均不可达**，
> 但同网段的 `api.github.com`、`codeload.github.com`、
> `objects.githubusercontent.com`、`pipelines.actions.githubusercontent.com`
> 与 **`ssh.github.com:443`** 都通。
>
> 后果与既有对策：
>
> - runner 的注册与取 job 只用 api + pipelines，**不受影响**；
> - `actions/checkout` 走 HTTPS 会卡死 → 用 SSH 通路（见下面第 5 节）；
> - 下载 runner 二进制或 `actions/python-versions` 包**不能用**
>   `releases/download/…`（那是 github.com），改走
>   `https://api.github.com/repos/OWNER/REPO/releases/assets/<id>`
>   配 `Accept: application/octet-stream`；
> - `actions/setup-python` 无法现下 Python，必须预置进 tool cache（第 6 节）。
>
> 曾经试过找一个可用的 `github.com` IP：把 `api.github.com/meta` 里的 40 个
> git 段逐个实测过，**没有一个可用**。其中 `140.82.121.4` 会偶发回 200，
> 复测时连 TLS 握手都做不到——**那是伪造响应，绝不能拿来钉 hosts**，
> 否则会得到一个时好时坏、无法排查的 CI。

---

## 3. 准备依赖与目录

```bash
# 先只检查，不改任何东西
sudo ./scripts/ci/bootstrap_lab_runner.sh --check

# 装依赖 + 建目录（幂等，可反复跑）
sudo ./scripts/ci/bootstrap_lab_runner.sh --user github-runner
```

这个脚本**只做准备，不做注册**——注册要一次性 token，把它传给脚本意味着它会
落进 shell 历史或日志。注册留给下面的手工步骤。

它同样**不改防火墙、不动 sshd、不删任何文件**。

### 装完字体之后要清 matplotlib 的字体缓存

```bash
sudo -u github-runner rm -rf ~github-runner/.cache/matplotlib
```

matplotlib 把「这台机器有哪些字体」缓存成 `<cachedir>/fontlist-v*.json`，
**只按自己的格式版本号判失效，不看字体目录变没变**
（`font_manager._load_fontmanager`）。新装的字体包**不会**让它过期：包装上了、
`fc-list` 也查得到，matplotlib 仍然看不见——表现是渲染用例继续红，而字体明明
在，两条线索指向完全不同的方向。

`<cachedir>` 在 Linux 上是 `$XDG_CACHE_HOME/matplotlib`（默认
`~/.cache/matplotlib`），**除非设了 `MPLCONFIGDIR`**：走内置 runtime 的那条链路
会把它改道到 `<data_dir>/cache/mpl`（`engine/runtime.child_env()`），那份要另外
清一次。清错目录的表现与不清一模一样。

bootstrap 脚本刻意不替你删——它不删任何文件（见脚本头部的「刻意不做的事」），
所以这一步留在文档里。

### 专用用户

```
runner user: github-runner        # 不加入 sudo 组
```

**CI job 不以 root 运行，runner 用户也不需要 sudo。**给了的话，任何一条
workflow 里的命令都能改这台机器的系统状态，而 workflow 是随代码走的。

### 持久化根目录

```
TAVOTTO_CI_STATE_ROOT=/srv/tavotto-ci
```

布局（由 bootstrap 建出，与 `scripts/ci/_common.py` 的 `LAYOUT` 同源）：

```
/srv/tavotto-ci/
├── cache/                 # 跨 run 的构建缓存
├── locks/                 # flock 互斥
├── upgrade/
│   ├── state/             # 升级测试的持久化用户状态
│   └── projects/
├── baselines/
│   ├── perf/              # 性能滚动基线（rolling.json + previous）
│   └── visual/            # 预留
├── reports/               # 各环节的 JSON 报告
└── tmp/                   # 一次性 venv 与解包产物（按保留期回收）
```

属主必须是 runner 用户，否则 preflight 会直接拦下。

**只有这几类东西允许跨 run 保留**：缓存、基线、升级 fixture 状态、历史
benchmark、已审阅的 golden 数据。**工作目录本身每次都清理**（`actions/checkout`
的 `clean: true`）。checkout workspace、随机 venv、来路不明的进程、token、
测试 secret 一律不得持久化。

> 视觉基线**不在**这里，它在仓库的 `tests/acceptance/baselines/`——
> 基线是需要 code review 的资产，放进持久化根就永远不会出现在任何一次
> review 里，谁改了、为什么改都无从追溯。

---

## 4. 工具链

| 工具 | 版本 | 备注 |
|---|---|---|
| Python | ≥ 3.10 | 与 `pyproject.toml` 的 `requires-python` 同源 |
| Node | 22 | 与 `ci.yml` 的 `setup-node` 一致 |
| pnpm | 11 | |
| Rust | stable + clippy + rustfmt | workerd 门禁 |
| Playwright | chromium + 系统依赖 | `npx playwright install-deps chromium` |
| fonts-noto-cjk | | corpus 的中文 case 要它才画得出字 |
| fonts-dejavu-extra | | DejaVu 的斜体脸；缺了 `style=italic` 静默退回 regular |

Rust 装完**必须把 `~/.cargo/bin` 加进 runner 服务的 PATH**：

```
# ~/actions-runner/.env
PATH=/home/github-runner/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin
RUNNER_TOOL_CACHE=/opt/hostedtoolcache
AGENT_TOOLSDIRECTORY=/opt/hostedtoolcache
```

`runsvc.sh` 继承的是 systemd 的最小 PATH，**不含 `~/.cargo/bin`**——漏了这条，
workerd 那几步会直接 `command not found`，而错误信息与真实原因毫不相干。

---

## 5. GitHub 通路（本网络必须）

因为 `github.com` 不可达（第 2 节），`actions/checkout` 必须走 SSH：

```bash
sudo -u github-runner ssh-keygen -t ed25519 -N "" -C "tavotto lab runner" \
     -f ~github-runner/.ssh/id_ed25519_github

sudo -u github-runner tee ~github-runner/.ssh/config <<'EOF'
Host github.com
  HostName ssh.github.com
  Port 443
  User git
  IdentityFile ~/.ssh/id_ed25519_github
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
EOF
sudo -u github-runner chmod 600 ~github-runner/.ssh/config

# 让 checkout 的 HTTPS 形态 URL 自动改走 SSH
sudo -u github-runner git config --global \
     url."git@github.com:".insteadOf "https://github.com/"
```

然后把公钥加为仓库的 **只读 Deploy key**（Settings → Deploy keys）。
只读足够 checkout；将来 CI 若需要往回推（打 tag 之类）要另配凭据。

> 组织若禁用了 deploy key，需要在
> `https://github.com/organizations/<ORG>/settings` 里放开该策略。

验证：

```bash
sudo -u github-runner ssh -T git@github.com
# 期望：Hi Tavotto/Tavotto! You've successfully authenticated...
sudo -u github-runner git clone --depth 1 https://github.com/Tavotto/Tavotto.git /tmp/probe
```

---

## 6. 预置 tool cache

`actions/setup-python` 在本网络下无法现下 Python（它从 `github.com` 拿包）。
必须预先放进 tool cache：

```bash
# 用 api.github.com 的 assets 端点绕开不可达的 github.com
ASSET_ID=$(curl -s "https://api.github.com/repos/actions/python-versions/releases?per_page=100" \
  | jq -r '.[].assets[] | select(.name|test("python-3\\.13\\..*linux-24.04-x64\\.tar\\.gz$")) | .id' | head -1)
curl -sL -H "Accept: application/octet-stream" -o py.tar.gz \
  "https://api.github.com/repos/actions/python-versions/releases/assets/$ASSET_ID"
mkdir -p /tmp/py && tar xzf py.tar.gz -C /tmp/py
sudo AGENT_TOOLSDIRECTORY=/opt/hostedtoolcache bash /tmp/py/setup.sh
sudo chown -R github-runner:github-runner /opt/hostedtoolcache
```

`ci.yml` 的矩阵用 3.10 与 3.13，两个都要预置。Node 22 同理（从
`nodejs.org` 直取，那个域名是通的）。

---

## 7. 注册 runner

```bash
sudo -u github-runner -i
mkdir -p ~/actions-runner && cd ~/actions-runner

# 版本号见 https://api.github.com/repos/actions/runner/releases/latest
ASSET_ID=$(curl -s https://api.github.com/repos/actions/runner/releases/latest \
  | jq -r '.assets[] | select(.name|test("actions-runner-linux-x64-[0-9]")) | .id')
curl -sL -H "Accept: application/octet-stream" -o runner.tar.gz \
  "https://api.github.com/repos/actions/runner/releases/assets/$ASSET_ID"
tar xzf runner.tar.gz && rm runner.tar.gz
sudo ./bin/installdependencies.sh

./config.sh \
  --url https://github.com/Tavotto/Tavotto \
  --token <REGISTRATION_TOKEN> \
  --name tavotto-lab-01 \
  --labels self-hosted,linux,x64,tavotto-lab \
  --work _work --unattended --replace
```

`<REGISTRATION_TOKEN>` 在 Settings → Actions → Runners → New self-hosted runner
现取，有效期 1 小时。**不要把它写进任何文件、脚本或本文档。**

标签必须包含 `tavotto-lab`——`lab-ci.yml` 与 `release.yml` 靠它定向。
改标签时**同步改 `.github/actionlint.yaml`**，否则 lint 会开始报未知标签。

### 装成服务

```bash
sudo ./svc.sh install github-runner
sudo ./svc.sh start
sudo ./svc.sh status
```

在 systemd unit 里补上 FD 上限：

```ini
# /etc/systemd/system/actions.runner.*.service 的 [Service] 段
LimitNOFILE=65536
```

soak 会同时开多个 worker 与 HTTP 连接，撞上限的症状是随机的
`Too many open files`，极难与真实的句柄泄漏区分。

### 并发度

**保持每台 runner 的 job 并发为 1**（一个 runner 进程天然如此）。

benchmark 需要稳定、soak 不该与 mutation 抢 CPU、持久化状态不该并发写、
golden 基线不该被两个 job 同时读写。`lab-ci.yml` 另有 GitHub 侧的
`concurrency: lab-qualification` 作为第一道保险。

**单个 job 内部**可以合理并行。资源分配原则：

```
OS / runner 自身预留   ~2 CPU + 4 GiB
普通测试               ≤ 8~10 CPU
Golden render          4~8 workers
Playwright             ~2 workers
Soak                   4~8 并发
Mutation               ~12~14 CPU
Benchmark              独占，禁止与任何重任务并行
```

**不要为了「16 核必须跑满」牺牲测试确定性。**

---

## 8. 仓库变量

Settings → Secrets and variables → Actions → Variables：

| 变量 | 默认 | 作用 |
|---|---|---|
| `LAB_CI_STATE_ROOT` | `/srv/tavotto-ci` | 持久化根 |
| `LAB_PERF_GATE` | `false` | 性能回归是否阻断 |
| `LAB_VISUAL_GATE` | `true` | 视觉回归是否阻断 |
| `LAB_MUTATION_GATE` | `false` | mutation 是否阻断 |

**不新增 secret。**这台 runner 拿不到、也不该拿到 PyPI / Apple / SignPath
的任何凭据。

性能与 mutation 默认不阻断是有意的：没有历史 baseline 就把发行卡死，只会让人
第一时间把这个 job 关掉。等积累几周数据、阈值站得住脚之后再打开。

---

## 9. 日常运维

### 日志

```
~/actions-runner/_diag/                 # runner 自身
$TAVOTTO_CI_STATE_ROOT/reports/*.json   # 各环节报告
```

失败时 workflow 会把 `reports/`、视觉 diff 与 soak metrics 打包成 artifact
（诊断包 14 天，指标 30 天）。**刻意不收** `node_modules`、venv、整个
checkout——几百 MB 起步，对排查毫无帮助。

### 清理

`lab-ci.yml` 每次开跑前会自动跑一次：

```bash
python3 scripts/ci/cleanup.py            # tmp > 2 天、reports > 30 天
python3 scripts/ci/cleanup.py --dry-run  # 只看要删什么
```

`cache/`、`baselines/`、`upgrade/` 永不清理。每一次删除都先过
`assert_within()`（`resolve()` 之后判断是否落在持久化根内，且不等于根本身）——
**绝不执行 `rm -rf "$SOME_VAR"`**。

### 体检

```bash
sudo -u github-runner TAVOTTO_CI_STATE_ROOT=/srv/tavotto-ci \
     python3 scripts/ci/lab_preflight.py --mode nightly
```

### 升级 runner 版本

```bash
sudo ./svc.sh stop
# 按第 7 节重新下载并解包（config 与 .credentials 会保留）
sudo ./svc.sh start
```

### 停用 / 移除

```bash
sudo ./svc.sh stop && sudo ./svc.sh uninstall
./config.sh remove --token <REMOVAL_TOKEN>
```

移除之后记得在仓库 Settings 里确认 runner 已消失，并删掉那把 Deploy key。

---

## 10. 相关文档

- [`release-qualification.md`](release-qualification.md) — 各环节验什么、怎么判、失败了怎么读
- `.github/workflows/lab-ci.yml` — 实验室 CI 本体
- `.github/workflows/release.yml` — 发行链上的 lab gate
- `scripts/ci/` — 全部实现
