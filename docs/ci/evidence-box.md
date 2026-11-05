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

`install-cron` 写 `/etc/cron.d/tavotto-evidence`：每天 18:30 UTC（02:30 CST）取回 main（cron 按本机时区解释时刻，`install-cron` 换算成本地时刻再写，机器上是 `30 2 * * *`），用 **main 上的** `box.py` 跑 `nightly`。控制检出是 blobless，`git show` 要现去 GitHub 取内容：取回失败重试三次（间隔 60 s），先写 `.new` 再 `mv`，不会把本机副本截成空文件；三次都失败就在 `nightly.log` 记一行 `nightly skipped`，当晚不跑。

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
- PyPI 官方源（files.pythonhosted.org，Fastly）约 1 MB/min：`novice-u2404` 先照 README 原样装、限时 15 分钟
  （实测两次都超时），再换阿里云镜像走完后半段（约 200 s）。准备阶段直接走阿里云镜像。
- `raw.githubusercontent.com` 取字体会卡住不动：`/srv/tavotto-evidence/cache/fonts/` 是批准字体的持久缓存，
  每次准备先放进去、装完存回来；fetch_fonts 对每个文件重核 sha256，缓存被改过也进不了镜像。
  新机器上第一次可以从维护者本机 `scp` 一份已过 `fetch_fonts.py --check` 的字体进去。
- 2026-11-05 实测 raw.githubusercontent.com 连续全部被重置，`github.com/.../raw/` 也跳到那里；新批准的字体因此进不了缓存，准备失败。
  准备容器现在先跑 `scripts/evidencebox/mirror_fill.py`，从 jsDelivr 补缓存里缺的文件。只换来源，每个文件仍按仓库钉的 sha256 核过才落盘；
  补不到不算失败，之后的 fetch 走原来源并决定成败。
- S0 的上游源码同样走 raw.githubusercontent：`cache/research-sources/` 是持久缓存，准备时先放进去、
  实测这台机器约五分之一请求能通（秒级被重置），所以 `fetch` 最多 40 轮、间隔 5 s，**每一轮**都把已取到的存回，下一轮 / 下一晚只补缺的；`fetch` 逐个重核 cohort 里钉的 sha256，对不上就报错。

## 首轮实测（2026-11-03，main 348c9af96）

| 任务 | 结果 |
| --- | --- |
| guardian-suites | 准备 856 s（镜像 3.28 GB）；执行 2 分 24 秒：engine 6/6 passed（退出 0）、V2 退出 4，两份报告 `validate` 通过 |
| guardian-explore（3 片、各 300 s 冒烟） | 每片执行约 20 个候选、各有差异（退出 1）；三片合并 triage：5 个簇、无新的可复现家族 |
| novice-u2404 | README 原样 pipx 15 分钟超时；镜像 192 s 装好；README 两条 Codex 命令、`tavotto codex install` / `doctor` 全过；插件 MCP 列出 10 个工具 |
| 第一轮 nightly（2026-11-04 02:56 CST 手动起，main 87c340c37） | guardian-suites 退出 0（2 分 24 秒）；guardian-explore 3 片各退出 1、合并 279 条发现 / 40 簇、无 unknown；第 1 片 364 个候选（62 有差异 / 263 无差异 / 39 只做静态）跑完池子就停，用时 1 小时 14 分、没用满 3 h；research-s0 准备时 `fetch` 被 raw.githubusercontent 重置连接 → 加上述缓存与逐轮存回 |
