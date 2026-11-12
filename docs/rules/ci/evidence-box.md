# 证据机：一次性容器里的长时兼容证据与新手安装（ADR 0166）

> 这里是这一主题规则的**唯一全文**；`.github/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 架构决策 `docs/adr/0166-evidence-box.md`；运维手册 `docs/ci/evidence-box.md`。
> 看护用例：`tests/test_evidencebox.py`

<!-- agents-route
sheet: .github/AGENTS.md
order: 136
改到: `scripts/evidencebox/`
主题: 证据机（ADR 0166）→ `evidence-box.md`
必守要点: 不是 runner、无凭据；只跑 origin/main；执行容器断网、非 root、根只读、即删；信任级同 hosted
-->

## 定位

一台租到 2027-02-02 的大陆服务器（Ubuntu 24.04 + Docker），给 hosted runner 预算之外的两类证据：
Guardian 全候选池 / S0 科研语料的长跑，以及 Ubuntu 24.04 新手按 README 原样安装。

- **不是 GitHub runner。** 不注册、不监听事件、无 token、无凭据；`runner-trust-zones.md` 的标签闭集不变。
  结论是观察证据，不进 Gate、不进合并队列。机器到期即退，什么都不依赖它。
- **与 Windows Lab 不是一回事。** Lab 只跑维护者代码，但它是常驻、会复用的真机，所以 Guardian 不迁过去
  （`windows-lab.md`）。证据机靠「每次一个新容器」补上隔离，才能跑 Guardian。

## 可信度

- 只执行 `origin/main` 可达的完整 SHA；控制检出每次先从 GitHub 取回 main，取不到就拒绝，不拿旧 main 判。
  **没有**「维护者自己的提交」例外（Guardian 夹具只有合入 main 才算评审过，`docs/ci/compat-guardian.md` §1）。
- 任务闭集：`guardian-suites` / `guardian-explore` / `research-s0` / `novice-u2404`。新增任务改 `box.py` 的 `TASKS`、
  本节与看护用例，三处同一个 PR。
- cron 执行的是**刚取回的 main** 上的 `box.py`（`git show origin/main:…`），不是本机留着的旧副本。取不回 main 就当晚不跑，也不退回旧副本。

## 容器纪律

- **准备**（联网、root、一次性容器 → `docker commit`）只跑 main 上与 workflow 同一组的安装步骤；改 workflow 的安装步骤时，
  `box.py` 的 `GUARDIAN_PREPARE` / `research_prepare` 跟着改。
- 准备阶段可以从 jsDelivr 补缓存缺的文件（`mirror_fill.py`），但只认钉在完整提交上的地址，并按仓库钉的 sha256 核过才落盘。
- **执行**（`exec_argv`）必须同时带：`--network none`、`--user 10001:10001`、`--cap-drop ALL`、
  `--security-opt no-new-privileges`、`--read-only`、tmpfs `/tmp`、`--pids-limit`、`--memory`、`--cpus`、`--rm`；
  只挂本次证据目录。**不许**挂 `docker.sock`、`--privileged`、`--network host`、挂宿主其它目录。
- 每个容器都带 `TAVOTTO_NO_TELEMETRY=1`、`TAVOTTO_NO_UPDATE_CHECK=1`、`TAVOTTO_NO_DIAG_UPLOAD=1`。
- `novice-u2404` 例外：要联网与 sudo，所以不套执行组限制；但它只装 PyPI 上已发布的版本、不执行仓库代码、
  只挂本次证据目录、跑完即删。

## 信任级不放宽

容器共享宿主内核，**不是** compat-guardian.md §7 所说的「OS 级隔离证明」。非确定性 / 外部来源候选在这里也只做静态检查；
证据机跑的与 hosted 同一信任级，多出来的只是预算（全候选池、更长 `--max-seconds`、每天换 seed、S0 每晚轮换一个 case）。

## 结果

- 退出码口径与对应 workflow 相同：guardian-suites 只有 0 / 4 不算失败；explore 1 = 有差异（交 triage）；S0 非 0 原样保留。
- 证据只留在机器上（`/srv/tavotto-evidence/runs/`，保留最近 60 份），维护者 `scp` 取回；**不上传任何云存储**、不发通知。
- 引用证据机结论时写明「证据机（ADR 0166），非 CI」；它不替代 hosted 的任何一条。
- 不调 OpenAI / Anthropic 官方模型（与 `windows-lab.md` 的地区政策同一条）。
