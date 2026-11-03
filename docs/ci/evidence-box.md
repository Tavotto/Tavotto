# 证据机运维手册

规则全文 `docs/rules/ci/evidence-box.md`，决策 `docs/adr/0166-evidence-box.md`。
连接参数只在维护者本机 `~/.ssh/config`（别名 `tavotto-evidence-box`），不进仓库。

## 机器

阿里云华南，4 vCPU / 8 GB / 40 GB，Ubuntu 24.04，Docker（`/etc/docker/daemon.json` 配了国内镜像加速）。
**租期到 2027-02-02**；到期前把本文件、规则文件、ADR 0166 状态改成「已退役」，`scripts/evidencebox/` 删除。

## 初始化（一次）

```sh
ssh tavotto-evidence-box 'mkdir -p /srv/tavotto-evidence'
scp scripts/evidencebox/box.py scripts/evidencebox/novice_u2404.sh tavotto-evidence-box:/srv/tavotto-evidence/
ssh tavotto-evidence-box 'python3 /srv/tavotto-evidence/box.py doctor && python3 /srv/tavotto-evidence/box.py sync \
  && python3 /srv/tavotto-evidence/box.py install-cron'
```

`install-cron` 写 `/etc/cron.d/tavotto-evidence`：每天 18:30 UTC（02:30 CST）取回 main，用 **main 上的** `box.py` 跑 `nightly`。

## 日常

```sh
ssh tavotto-evidence-box 'python3 /srv/tavotto-evidence/box.py reports --last 20'
ssh tavotto-evidence-box 'python3 /srv/tavotto-evidence/box.py run guardian-suites'
ssh tavotto-evidence-box 'python3 /srv/tavotto-evidence/box.py run guardian-explore --seed 7 --shards 3 --max-seconds 7200'
ssh tavotto-evidence-box 'python3 /srv/tavotto-evidence/box.py run research-s0 --case research_cantera_sofc'
ssh tavotto-evidence-box 'python3 /srv/tavotto-evidence/box.py run novice-u2404'
scp -r tavotto-evidence-box:/srv/tavotto-evidence/runs/<run> ~/TavottoEvidence/
```

`--sha` 只接受 `origin/main` 可达的完整 SHA，缺省是 main 头。同一时刻只跑一个任务（`/srv/tavotto-evidence/lock`）。

## nightly 做什么

1. `guardian-suites`：与 `compat-guardian.yml` observation job 同一组 case；
2. `guardian-explore`：全候选池切 3 片并行，3 小时预算，seed = 当天日期；
3. `research-s0`：16 个 case 按日期轮换一个（TeX 镜像数 GB，跑完即删）；
4. 周日加跑 `novice-u2404`；
5. `clean --keep 60`：证据目录留 60 份，镜像与源码检出只留当前 main。

结果汇总在 `runs/<stamp>-nightly.json`；单次任务的 `meta.json` 写 SHA、起止时间、退出码。

## 盘

40 GB 盘，系统与旧数据约 20 GB。guardian 镜像约 3 GB，S0 带 TeX 的镜像约 6 GB（跑完即删）。
`doctor` 剩余不到 8 GB 时提示，不到 4 GB 时失败。

## 已知情况（2026-11-03 实测）

- 大陆到 GitHub 偶有 TLS 断连或 130 s 连不上，`box.py` 对 clone / fetch 重试三次。
- `ubuntu:24.04` 默认 apt 源在大陆很慢；`novice-u2404` 故意不换源（照新手的真实环境）。
- PyPI 官方源下载慢；准备阶段走阿里云 PyPI 镜像，`novice-u2404` 不换源。
