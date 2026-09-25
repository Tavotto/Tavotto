# ADR 0076：macOS Intel 桌面版——按架构各发一个 dmg，在原生 Intel runner 上构建

日期：2026-09-23 · 状态：**Accepted**
相关：[0002 Tauri 桌面壳](0002-tauri-desktop-shell.md)（更新通道、macOS 签名）、
[0063 私有完整 Python](0063-private-python-provisioning.md)（`macos-x86_64` 基础解释器，另一份锁、`enabled` 仍为 false，不在本 ADR 范围）；
`packaging/runtime-lock.json` 的 `macos-x86_64` 目标、`docs/support-matrix.json`、`docs/RELEASING.md`。

## 背景

到 v0.16.0 为止，macOS 桌面版只发 Apple Silicon。原因不是依赖：`macos-x86_64` 的锁早就在，
科学栈（numpy / scipy / pandas / matplotlib / pillow / contourpy / kiwisolver / fonttools）
和 PyMuPDF 在 PyPI 上都有 cp313 的 macOS x86_64 wheel（ADR 0072 起 PyMuPDF 换成 RenderCore 的 pikepdf / pypdfium2，
它们也有，但 pikepdf 的 x86_64 wheel 要求 macOS 15，于是 Intel 桌面版的最低系统是 15.0，见 ADR 0072 §2），python-build-standalone 也发
`x86_64-apple-darwin`。卡住的是**验证**：CI 只有 Apple Silicon runner，Intel 目标从没被构建和冒烟过，
按「空转的门禁比没有门禁更坏」只能标 `shipped: false`、对外不写支持。

GitHub 现在有托管的 Intel 镜像（`macos-15-intel`、`macos-26-intel`）。2026-09-23 的一次性探测
（分支 `probe/intel-mac`，run 35826063142）在两个镜像上原样跑了 ci.yml 的 macOS 打包腿：
原生 i7-8700B（非 Rosetta）、`macos-x86_64` runtime 构建自检（版本核对 + seaborn/scipy/PDF 真实渲染）、
PyInstaller 318 个 Mach-O 全部 x86_64、`smoke_app --expect-source bundled --expect-runtime`
两次（干净环境 / 中文+空格路径）全部通过。

## 裁决

| 问题 | 裁决 | 落点 |
|---|---|---|
| 一个包还是两个 | **按架构各一个 dmg**，不做 universal2。科学栈 wheel 分架构发布，把两份 `.so` 硬拼成 universal2 没有验证过；内置 runtime 的「import + 画真图」闸必须在它要跑的架构上执行 | `desktop-tauri.yml` build 矩阵 |
| 在哪里构建 | Intel 腿 `runs-on: macos-26-intel`，**原生**构建与冒烟，步骤与 Apple Silicon 腿逐条相同（签名、公证、签完名的 .app 从中文+空格路径冒烟、更新包重做） | 同上；`tests/test_runtime_build.py::test_every_shipped_macos_target_has_a_native_desktop_leg` 要求 x86_64 腿跑在 `*-intel` 上 |
| 架构的期望值从哪来 | 矩阵行的 `arch` 字段（这条腿**该**产出什么），不再是 `platform.machine()`（runner 自报什么）——后者在 `macos-latest` 换架构时会恒真 | 架构核对与签名验收两步 |
| 文件名 | Apple Silicon **沿用历史名**（`Tavotto-<ver>-macOS.dmg`、`Tavotto.app.tar.gz`）：官网链接、下载量统计、已装用户的更新 URL 都认它。Intel 加 `-Intel`：`Tavotto-<ver>-macOS-Intel.dmg`、`Tavotto-Intel.app.tar.gz` | job 级 `MAC_SUFFIX` |
| 产物清单角色 | 新增 `macos-intel-installer` / `macos-intel-updater`，各自「恰好一个」；不是把 `macos-installer` 放宽成两个。认不出架构的 dmg / 更新包直接失败，不落进 `*) continue` | `scripts/ci/artifact_manifest.py`；`tests/test_update_chain_gates.py` 把那段 shell `case` 抠出来交给 bash 跑 |
| 更新清单 | `latest.json` 增加 `darwin-x86_64`，`--require` 三平台。两个 macOS 包都用**精确名**匹配：宽模式 `\.app\.tar\.gz$` 会同时认下两个包，靠先后顺序决定谁赢 | `scripts/make_updater_manifest.py`、`scripts/ci/updater_consumer_check.py` |
| 支持口径 | `runtime-lock` 的 `macos-x86_64.shipped = true`；support-matrix 的 `macos-x86_64-desktop` 为 `supported` | `tests/test_support_matrix.py` |

## 后果

- **合并队列不构建 Intel。** ci.yml 的 macOS 打包腿仍只有 Apple Silicon 一档（加 Intel 要连带
  cache-seed 的同 os 种子腿）。Intel 这条链第一次执行在发版前的 `release.yml` 演练
  （`publish=false`）——那一步本来就是「正式 tag 不承担首测」的解药，发 Intel 版之前必须先跑它。
  哪天 Intel 专属的回归变多，再把它加进合并资格档。
- **nightly 的线上更新清单检查会预期红**，直到第一个带 Intel 包的版本发出去：它按同一份
  三平台硬要求核线上 `latest.json`，而线上那份还没有 `darwin-x86_64`。刻意不按版本号开关
  （版本常量迟早说谎）。
- **runner 有有效期。** GitHub 曾宣布 `macos-15-intel` 是最后一个 x86_64 镜像、支持到 2027-08，
  之后又出了 `macos-26-intel`，退役日期未公布。它退役时要么换自备 Intel 机器（进 runner 信任区的
  规矩），要么把这一档降回 unsupported——不许在没有原生验证的情况下继续发。
- 维护成本是一条矩阵腿与一次公证，不是一套新流程；`tauri.conf.json` 的
  `minimumSystemVersion` 仍是 11.0，两个架构同一个下限。
