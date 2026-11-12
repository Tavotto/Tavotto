# ADR 0166：证据机——在一次性容器里跑长时兼容证据与新手安装，不是 runner

日期：2026-11-03 · 状态：**Accepted**（维护者 2026-11-03 裁决：闲置的大陆服务器拿来跑兼容测试长跑（Guardian / S0）
与 Ubuntu 24.04 新手安装测试；「每次一个新容器、不经过 GitHub」，补本 ADR）· **到期**：2027-02-02（机器租期）
相关：`docs/ci/compat-guardian.md` §1 / §7（Guardian 只在 hosted 临时 runner 上跑的理由）、
[0131 Agent 验证入口](0131-agent-verification-entry.md)（「不在 self-hosted runner 上跑任何东西」）、
`docs/rules/ci/runner-trust-zones.md`（CI04）、`docs/rules/ci/windows-lab.md`（Windows Lab：Guardian 不迁过去）；
规则全文 `docs/rules/ci/evidence-box.md`，运维 `docs/ci/evidence-box.md`，执行器 `scripts/evidencebox/box.py`。

## 动机

兼容性证据被 hosted runner 的预算卡着：Guardian explore 每晚 `--limit 24`、全候选池一周才扫一次（4 片 × 60 分钟）；
S0 科研语料 16 个 case 只能手动 dispatch，TeX 那几个光装环境就 40 分钟。新手安装这条路只在 Windows 上有 nightly，
Ubuntu 24.04（系统 Python 3.12、PEP 668 拒绝直接 `pip install`）、国内网络下按 README 原样装一遍，没有任何证据。

维护者手上有一台闲置的大陆服务器（阿里云华南，4 vCPU / 8 GB / 40 GB，Ubuntu 24.04 + Docker），租期到 2027-02-02。

它不能直接挂成 self-hosted runner：Guardian 执行的是科研脚本，Python 层的 Safe 只有目录隔离、**不是**沙盒
（compat-guardian.md §1）。长期运行、多次复用的机器上，同 UID 的进程能读写上一轮留下的一切。§7 因此规定 Guardian
永不进 self-hosted。这一点不变：证据机**不是** runner。

## 决定

1. **不是 runner，不接 GitHub。** 不注册 runner、不监听任何 workflow 事件、机器上没有 GitHub token 或任何凭据；
   `runner-trust-zones.md` 的标签闭集与 `actionlint.yaml` 一字不变。结论是观察证据，不进 required checks、不进合并队列。
   源码经公开地址匿名取回；证据只留在本机，由维护者 `scp` 取回——不上传任何云存储。
2. **只跑 main。** `box.py` 先把控制检出对齐 GitHub 上的 `origin/main`，只接受 `origin/main` 可达的完整 SHA。
   没有 Windows Lab 那种「维护者自己的提交」例外：Guardian 夹具只有合入 main 才算评审过（§1）。
   任务是闭集 `guardian-suites` / `guardian-explore` / `research-s0` / `novice-u2404`，没有跑任意分支或任意脚本的入口。
   Guardian 自己的「只收登记 case id」「夹具字节 == HEAD」不变。
3. **两段式容器：联网准备，断网执行。**
   * **准备**：在一次性容器里，以 root、联网执行 main 上与 workflow 同一组的安装步骤（`pip install -e .[dev,ci]`、
     runtime lock 钉的科学栈、批准字体、S0 的上游源文件按 SHA-256 取回与科研依赖、TeX），成功后 `docker commit` 成执行镜像。
     pip / apt 走国内镜像，版本由 runtime lock 钉死，换的只是来源。
   * **执行**：每次一个新容器，`--network none`、非 root（10001）、`--cap-drop ALL`、`no-new-privileges`、根文件系统只读、
     `/tmp` 是 tmpfs、限 pids / 内存 / CPU，跑完 `--rm`。唯一可写的宿主路径是本次的证据目录。
     这样补上了「目录隔离不是 OS 隔离」缺的那一层：同一台机器上，前一轮与后一轮彼此看不见、执行期发不出网络请求。
4. **信任级不放宽。** 容器共享宿主内核，不当作「非确定性 / 外部来源候选的 OS 级隔离证明」。§7 那条照旧：
   这类候选在证据机上也只做静态检查。证据机跑的东西与 hosted 完全同一信任级（已合入的夹具、
   已评审模板对已评审父本的确定性派生），区别只在预算：
   * explore：全候选池按 `--shard K/3` 切三片并行，`--max-seconds` 3 小时，seed 按日期每天换一组（hosted 周扫按 ISO 周号取 seed）；
   * S0：16 个 case 按日期轮换，每晚一个，回执的 `workflow` 为 null（非 Actions 环境，`compat_provenance` 已支持这种配对）；
   * guardian-suites：与 observation job 同一组 case、同一套退出码口径（只有 0 / 4 不算失败）。
5. **新手安装（`novice-u2404`）**：全新 `ubuntu:24.04` 容器，普通用户，按 README「pip, Linux and servers」与
   「Using Tavotto in Codex (plugin)」原样走一遍。装的是 **PyPI 上已发布的版本**，不执行仓库代码，
   所以与 SHA 无关。要联网、要 sudo，因此不套执行阶段那组限制，但同样是一次性容器、跑完即删。
   不调 OpenAI / Anthropic 官方模型（与 `windows-lab.md` 的地区政策同一条），只验证到装好、连通、MCP 能列出工具为止。
6. **到期即退。** 什么都不依赖它：没有用户面服务、没有发布链环节、不是门禁。机器到期或被收回，
   规则文件与执行器标成退役即可，hosted 那一套原样还在。

## 不做

- 不注册 runner，不改任何 workflow，不把证据机结论接进 Gate；
- 不跑 PR 分支、fork、用户提交的脚本、非确定性候选；
- 不在机器上放凭据，不发企业微信，不上传 COS 或任何云存储（维护者 2026-11-03：「会消耗我的 COS 存储」）；
- 不做发版巡检或用户面服务（机器只租到 2027-02-02，到期会断）；
- 不调官方模型。

## 1.0 纪律

只加 CI 侧工具与文档，产品能力、产品代码、发行物零改动。

## 看护

`tests/test_evidencebox.py`：执行容器的 argv 必须带齐那组隔离参数、不得挂 Docker socket 或 `--privileged`；
只接受 main 可达的完整 SHA；每个容器都带三个硬关开关；`guardian-suites` 的 case 集与
`compat-guardian.yml` observation job 一致；执行器里没有凭据、没有主机地址。
