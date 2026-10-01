# ADR 0112：自动测速选源——私有 Python 归档 GitHub → npmmirror，PyPI 官方源 → TUNA

日期：2026-09-29 · 状态：**Accepted**（用户 2026-09-29 拍板「自动测速选源」）
修订：[0063 私有完整 Python 的供应](0063-private-python-provisioning.md) §六（「联网只有一条路」：来源只有锁里那一个地址）与
§十（「私有镜像不在本轮」）
相关：[0019 受控依赖修复](0019-controlled-dependency-repair.md)、[0061 联合依赖准备](0061-joint-dependency-preparation.md)（§五 代事务）、
[0064 无系统 Python 的目标资格](0064-no-system-python-target-qualification.md)；issue #737（「问 pip 而不是自己解析 pip 配置」）；
PR #743 / ADR 0111（桌面安装包附带私有 Python 归档、PyPI 网络类失败换镜像——与本 ADR 互补，见 §四）

## 实测（阿里云华东，Windows Server 2025，无代理，main 493a1310，2026-09-29）

| 环节 | 来源 | 实测速度 | 结局 |
| --- | --- | --- | --- |
| 私有 Python 归档（pbs cpython-3.13.15 windows install_only，47 MB） | GitHub（锁里的 url） | 约 20–40 KB/s | 16:54 → 17:15 共约 21 分钟，最终成功（证书修复有效） |
| 同一个文件 | npmmirror `registry.npmmirror.com/-/binary/python-build-standalone/20260814/<同名>` | 11 MB/s | — |
| 受管环境 `pip install "matplotlib<3.12,>=3.8" "numpy<3,>=1.24" adjusttext` | 默认 PyPI（files.pythonhosted.org） | 约 20 KB/s | 15 分钟撞 `dependency_install_timeout`，这一代标 incomplete；**app.log 里一个字都没有**（只在 environment.json 的 incomplete_reason） |
| 同样的 wheel | TUNA `pypi.tuna.tsinghua.edu.cn/simple` | 13 MB/s | — |
| — | mirrors.aliyun.com | 这台机器上同样慢 | 不当默认回退 |

两处的共同形状：连得上、不报错、只是慢到等不起。旧行为对「慢」没有任何判据——私有 Python 硬等 21 分钟，pip 硬等满 15 分钟后失败，
而且失败不进日志，事后只能靠 environment.json 猜。

## 用户的决定（2026-09-29，「自动测速选源」）

1. 私有 Python 归档：先试 GitHub（锁里的 url）；失败、或测得的速度持续慢到不合理，自动换 npmmirror 上的同一个文件。锁里的
   sha256 / size 仍是唯一信任锚；镜像地址从锁推导；续传或重下都行，但 hash 校验在最终字节上。
2. PyPI：用户配过 pip 源（`pip config list` / `PIP_INDEX_URL` 等）就用用户的、不自动换；什么都没配时，官方源太慢或超时就自动换
   TUNA 重试一次（`--index-url` 传给这一次，不写用户的 pip 配置）；进度 / 日志要说出用了哪个源。
3. 安装失败与换源要进 app.log（INFO / WARNING，带原因码与来源）。
4. 15 分钟总超时可以不改，但回退要落在预算之内（或者按每次尝试计）——由实现方裁决并写下来（§二）。

## 决策

### 一、私有 Python 归档：来源队列 + 测速（本 ADR 的 PR A）

* **镜像地址只从锁推导，不逐目标另写一份**：锁的 `python.mirrors` 是 `[{name, base}]`；一个目标的镜像地址 =
  `base + release + "/" + 主地址的文件名`（文件名按主地址原样，`%2B` 不解码不重编码；唯一出处 `privatepython.mirror_urls`）。
  推导成立的前提由 `validate_lock` 钉住：配了镜像时每个目标的 url 必须是 `github.com/astral-sh/python-build-standalone/releases/download/<release>/<文件名>`，
  镜像 base 必须是 https、以 `/` 结尾。换版本只改 url，镜像跟着走。2026-09-29 逐目标核过 npmmirror 上五个文件的 size 与锁一致，
  Windows 那份（47,131,996 B）另核过 sha256 `4ca61e4b…70c0bd` 与锁一致。
* **镜像不是信任来源**：不管字节从哪来，整份 sha256 与锁一致才改正式名；**hash 不符不换源、不重试**（ADR 0063 §四 原样）——
  镜像篡改就停在 `private_python_hash_mismatch`，解释器执行计数 0、无目录、无 `.part`。
* **换源的三种理由**（`privatepython._download` 的来源队列：主地址 → 镜像）：传输层失败用完 `DOWNLOAD_ATTEMPTS` 次（按来源计）、
  HTTP 4xx / 5xx、**太慢**。每个来源从零开始下（不续传：判据的主语是最终落盘的整份字节，与来源无关；丢掉的字节 ≤ 测速量程）。
* **「太慢」的判据**：只在还有下一个来源、且此前没有来源因慢被放弃时测。开始收字节满 `SLOW_GRACE_S = 20 s` 之后，任一时刻按
  **全程平均速度**估的剩余时间（总量按锁里的 `size`）超过 `SLOW_ETA_S = 180 s` 就放弃这个来源。取值理由：
  * 实测机器上 GitHub 估剩余约 20 分钟，20 s 内判出；换到 npmmirror 几秒下完。
  * 3 分钟 = Windows 归档要 ≥ 约 260 KB/s、Linux x86_64（118 MB）要 ≥ 约 660 KB/s 才不换——正常宽带远高于此，误换的代价只是
    从镜像重下同一份（hash 照验）。
  * 平均而不是瞬时：TCP 慢启动与偶发抖动不该触发；读用 `read1(64 KiB)`，20 KB/s 的线路上也能每几秒判一次（1 MiB 一读要等 50 s）。
  * 完全卡死（一个字节都不来）由既有的 socket 超时 `NETWORK_TIMEOUT_S = 30 s` 兜，算传输层失败。
* **慢总比没有好**：因慢被放弃的来源排到队尾，后面的来源都失败时**不再测速**地再试一次；只有一个来源（镜像未配）时不测速——
  放弃了也没处可去，行为与修订前相同。
* **最终错误码**：任一来源的传输失败根是证书校验 → `private_python_tls`；每个来源都是 HTTP 失败 → `private_python_source_unavailable`；
  其余 → `private_python_offline`。闭集不变。
* **说出来源**：每次换源一条 WARNING（`下载源 <主机> 放弃：<too_slow（x KB/s，预计还要 N s）| HTTP 404 | transport（类型）>；改用 <主机>`），
  完成一条 INFO（`下载完成：来源 <主机>`），供应失败一条 WARNING（`供应失败：<code>（来源 <主机>）`）；主机名是锁推得出的就按
  `logsafe.known` 明文进诊断包。进度载荷 `private_python.source_host` 报**此刻真在下**的主机（`privatepython.downloading_from`），
  账 `ledger.json` 多一个 `downloaded_from`。
* 不变的：代理仍只从环境变量来、证书仍经 `tlstrust` 平台原生校验、不读 pip / uv / 项目配置（AST 钉）、授权仍绑在计划上、`enabled` 不翻。

### 二、PyPI：用户没配源时，官方源太慢 / 超时 → TUNA 重试一次（PR B）

* **用户配没配源，问 pip，不复刻 pip 的配置发现**（#737 的结构性结论）：在目标解释器里跑 `python -m pip config list`，只看会作用于
  `pip install` 的节（`global` / `install` / `:env:`），键按 pip 的规范化（去掉开头的 `--`、`_` 转 `-`）比较；`index-url` /
  `extra-index-url` / `no-index` / `find-links` 任一出现即「配过」。问不出来按「配过」处理（宁可不换源）。
  **2026-10-01 修订（#767，维护者裁决）**：不再读 `pip config list`——它把每条都打印、打印顺序不是覆盖顺序，`config get`
  又不认 `PIP_CONFIG_FILE`，自己从中推「生效的是哪个」两轮都推错。改为在目标解释器里跑一段探测（`PIP_OPTIONS_PROBE`，与
  插件逐字相同、严格同源对），让 pip 自己把 `pip install` 的选项解析一遍（`create_command('install').parse_args([])`），
  index-url 不是 PyPI 默认、或有 extra-index-url / no-index / find-links 即「配过」；导入失败 / pip 太旧 / 超时 = 问不出来。
* 配过：一个字节不改，照旧按用户的源装。没配：官方源**太慢**（测速判据在 PR B 里写明）或网络类失败 / 超时 → 用
  `--index-url https://pypi.tuna.tsinghua.edu.cn/simple` 重试**一次**；不写用户的 pip 配置；mirrors.aliyun.com 不当默认（实测同样慢）。
* **预算**：15 分钟总超时（`INSTALL_TIMEOUT_S`）不变，**两次尝试共用**；第一次（官方源）最多用到给 TUNA 留出保底的那一刻为止，
  第二次拿剩下的——回退落在预算之内，用户最坏也是 15 分钟见结论，不是 30 分钟。
* 进度与日志说出这次装包用的是哪个源（官方 / 用户配置 / TUNA）。

### 三、失败与换源进 app.log

安装失败（单包修复、联合准备、重建、包管理四个线程入口）一条 WARNING：稳定 code + 来源；换源一条 WARNING；私有 Python 的见 §一。
code 与主机名按 `logsafe.known` 明文，pip 原文照旧先 `_sanitize`（index 地址与凭据不出门，ADR 0019 §十二）。

### 四、与 ADR 0111 / PR #743 的关系

#743 让桌面安装包附带私有 Python 归档（有它就不用下载），并在 pip **网络类失败**时换镜像重试一次。本 ADR 补的是它明确不做的
「私有 Python 归档本身的下载镜像」（非桌面安装、Linux、安装包里没有这一份时），以及它不覆盖的「连得上但慢 / 撞总超时」。
两者落地后的顺序：包内归档 → 缓存 → 主地址 → 镜像；pip：用户的源 / 官方源 → （没配源且慢 / 网络失败 / 超时）TUNA 一次。

## 不做的事

* 续传（Range）：丢掉的字节最多是测速量程那 20 s，归档 25–120 MB，不值一个状态机（ADR 0063 §十 的理由仍成立）。
* 同时向多个来源发请求「谁快用谁」：对 CDN 与用户带宽都是浪费，且让「用的是哪一份字节」变得不确定。
* 镜像列表、轮换、按地区选源：两处各只有一个备用来源（npmmirror / TUNA），实测就够；要加第二个镜像 = 改锁 / 改常量 + 这份 ADR。
* 把 TUNA 写进用户的 pip 配置：用户的配置只属于用户（ADR 0019）。
