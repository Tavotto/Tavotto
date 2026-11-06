# 错题本 · ci

> 格式与规矩见 `docs/rules/repo/lessons/README.md`。写入只走每日复盘，别直接改。

### L-091 浅克隆里 git 返回 128 是「看不见」，不是「不是祖先」
- 类: 别这样
- Seen: 2026-08-27, 2026-08-28
- 为什么: 断言基线是 HEAD 祖先的用例在完整克隆恒绿、CI 浅克隆恒红，差点锁死仓库。历史关系只能 --unshallow，--depth=1 只给存在性；验收必须在真浅克隆里跑，错误措辞也要复核。
- 证据: #184

### L-092 PR 上没有 CI run，先看是不是 CONFLICTING，再用全长 SHA 查
- 类: 别这样
- Seen: 2026-08-27, 2026-09-07
- 为什么: 冲突中的 PR 不派发 workflow，重推无用；我却开了 30 分钟计时实验去测一个 O(1) 字段能答的问题。head_sha 传缩写会静默返回 0。
- 证据: #177, #299, #300

### L-093 判 CI 卡死前先看 steps 进度，runnerName 为 null 不算数
- 类: 别这样
- Seen: 2026-10-08
- 为什么: runnerName 在 job 跑完前恒为 null；把进行中的 backend-fast 判成卡死取消重跑，白丢约 40 分钟。单片 40 至 50 分钟是正常的。
- 证据: #811

### L-094 按根因分 PR，能本地探测的别等 CI 一轮
- 类: 照这样
- Seen: 2026-08-22
- 为什么: strict 策略下每多一个 PR 就多一轮完整 CI 加 update-branch；同根因拆成两个 PR 多烧一轮。进实验室直接跑 preflight，40 分钟回路变 40 秒。
- 证据: #52, #53, #54, #55

### L-095 改英文文案或导航名，推前自跑 chromium-en
- 类: 别这样
- Seen: 2026-09-30
- 为什么: chromium-en 与 webkit 两个 project 只在合并组或 full-ci 跑，PR CI 全绿也会进队列后被踢，拖慢后面所有 PR 两小时。
- 证据: #763

### L-096 碰 Windows 路径/进程/安装的 PR，开 PR 时就打 full-ci 并看 Windows 腿 → docs/rules/ci/ci-lanes.md

### L-097 改 e2e 等待或前置步骤，推前对照 workers=1 与 workers=3
- 类: 照这样
- Seen: 2026-10-15
- 为什么: 给 addCard 加一步等待，单 worker 重复 9 次全绿，三 worker 却确定性挂在后续步骤超时；只跑一种配置会把坏改动推上 CI，Windows 腿要一小时才知道。
- 证据: #1100

### L-098 入队判据必须区分「没有」与「没取到」，永远输出一句话
- 类: 照这样
- Seen: 2026-09-07
- 为什么: 取数调用返回 EOF 且 stdout 为空，与「非 SUCCESS 项为空」同形，差一步就在没拿到重腿状态时按下合并；日志含转义序列时 gh run view 也静默返回空。
- 证据: #299

### L-099 读 CI 结论按 workflow 最新一轮列全 job，裸名状态不算结论 → docs/rules/ci/gate-discipline.md

### L-100 偶发红或无关红先读一层根因，别加等待、标 flaky 或直接重跑 → docs/rules/ci/gate-discipline.md

### L-101 守 checkout 的门禁放在平台无关的层，别依赖被守的路径
- 类: 照这样
- Seen: 2026-09-07
- 为什么: 文件名是一段含换行的 TypeScript 源码，POSIX 全绿，Windows 的 git checkout 直接退 128，那台机器上什么都没跑。判变异只认命令退出码，别管道 tail。
- 证据: `tests/test_tracked_paths_are_windows_safe.py`

### L-102 新门禁成为阻断项前，先在 main 上手动跑绿一次
- 类: 照这样
- Seen: 2026-08-22, 2026-09-01
- 为什么: 发版时 lab 门禁首次真正执行，四层各自烂掉（标签无 runner 可领、脚本装包死在第一步、pytest 升级改了输出格式、认证后没跑过）；被平台 skip 的用例改坏也不响。判跑过没有看 jobs 非空。
- 证据: （迁移自维护者记录，无编号）

### L-103 只信默认分支的检查，拆成先落内容、后接线两个 PR
- 类: 照这样
- Seen: 2026-08-27
- 为什么: 从默认分支取判定输入的 job 与那些文件同一个 PR 落地，首次执行取输入 404，判定逻辑一次没跑，必需 Gate 永红。别用 bootstrap 回退：权限类判据允许 PR 自带 policy 等于放行自己。
- 证据: #183, #185

### L-104 新门禁四种空形状：判据钉错层、诱饵排前、分支没跟、报而不阻断
- 类: 别这样
- Seen: 2026-08-22
- 为什么: 27 条变异里抓到 6 条自己刚写的空门禁：断言只压最外层调用；诱饵排在真值之前（诱饵必须能赢）；纯函数判对了但分支照旧；打了 ✗ 退出码却是 0。每加一条用例立刻变异一次。
- 证据: #53

### L-105 workflow 里 & 起的并行测试片要先 set -m
- 类: 照这样
- Seen: 2026-09-16, 2026-09-20
- 为什么: 非交互 shell 的后台命令继承 SIG_IGN 的 SIGINT，Python 不装 KeyboardInterrupt，Ctrl-C 用例 90 s 超时，CI 上也会中；(trap - INT) 救不回。本机模拟 runner 要先复位 SIGINT 再起驱动。
- 证据: #434

### L-106 conftest 注册的 pytest 选项一律写 --opt=值
- 类: 照这样
- Seen: 2026-09-16
- 为什么: 预解析把下一个 token 当路径找 conftest：路径已存在又无测试路径时 tests/conftest.py 没加载，rc 4 unrecognized arguments。第一次跑永远绿、第二次才红，CI 的新 RUNNER_TEMP 撞不上，只能静态合同钉。
- 证据: 无编号）

### L-107 新增或大改 e2e spec，入队前先加 full-ci 让 PR 跑一次 Linux e2e
- 类: 照这样
- Seen: 2026-10-21, 2026-11-04
- 为什么: PR 上 posix-e2e 全是 skipped，合并队列才第一次在 Linux 真跑：示例图 bbox_inches=tight 在 Linux 比随附的 macOS PDF 宽 1.8 mm，用例比错对象，出队改重排一轮数小时。尺寸断言以引擎渲染回来的 manifest size_mm 为准。
- 证据: #1223, #1736

### L-108 改 scripts/ 或加 subprocess 用例后，本地跑三个仓库级扫描用例 → docs/rules/ci/gate-discipline.md

### L-109 只在夜跑执行的断言，期望值要和引擎实跑对拍，别靠推理写死
- 类: 别这样
- Seen: 2026-08-22, 2026-08-26
- 为什么: 夜跑里的状态断言从没执行过，期望值是推出来的；脚本目标的该状态结构性不可达，首次执行即红，连红一周多。判据改成取值集合并加普通 CI 对拍用例。
- 证据: #125, #180

### L-110 延迟 rerun 前先核 PR head 仍等于该 run 的 headSha
- 类: 照这样
- Seen: 2026-09-28
- 为什么: 后台等旧 run 结束再 rerun，期间 PR 已推新 head，重跑按 concurrency 取消了新 head 的 CI，PR 显示一排红。
- 证据: #695

### L-111 skip 不是绿：评估判据看它在哪些环境真执行过
- 类: 照这样
- Seen: 2026-09-05
- 为什么: 9 条用例在托管 runner 全 skip，只有科学栈机器真跑且从没绿过，lab-ci 连续 60 次失败无人知晓，发布演练才发现。skip 数要单列，环境假设要当场测量。
- 证据: #288

### L-112 工作流里的工具版本钉到本仓库验证过的补丁版
- 类: 照这样
- Seen: 2026-09-23
- 为什么: pnpm 只写主版本号，补丁漂移后 webServer 进程树不随父进程退出，每个 run 跑完测试仍挂死 20 到 65 分钟。认「Cleaning up orphan processes」后有遗留进程。
- 证据: #508

### L-113 入队前验按队列次序累积 merge 出的树，不只是分支
- 类: 照这样
- Seen: 2026-08-27
- 为什么: 并行 PR 的 hunk 原样插进重排后的文件，合并态可能不合规；两两验过不等于组合验过，新增文件也不在全仓格式化的覆盖集里。
- 证据: #175, #177, #186

### L-114 合并组里裸 cancelled 先核 job 时长是否贴 timeout-minutes
- 类: 照这样
- Seen: 2026-09-13
- 为什么: Windows 腿 32 到 42 分钟顶着 45 分钟上限，被取消后聚合门禁红、被踢出队列，易误读为 flaky；套件变长要回头量时长再定上限。快速失败会取消其余腿（ci-lanes.md）：summary 有 `root_failures` 时 cancelled 是收尾，无 failure 才按本条核。
- 证据: #340, #345, #346

### L-134 碰上游私有 API 写成新旧都认，CI 装的是当天最新版
- 类: 别这样
- Seen: 2026-11-03
- 为什么: pip 26.3 把 pip._internal.configuration.kinds 换成 Kind，测试注入的 sitecustomize 启动即挂，15 个后端分片同时红、合并组连踢；本地 pip 25.3 全绿。顺序也有讲究：旧版的 Kind 是空别名。
- 证据: #1639, #1658

### L-135 改 scripts/ci 后，设上 GITHUB_STEP_SUMMARY 与 GITHUB_OUTPUT 再跑一遍
- 类: 别这样
- Seen: 2026-11-04
- 为什么: 只有 CI 才有的变量打开了写摘要分支，错误路径缺新键 KeyError，本机整文件全绿，CI 三个平台后端分片全红。新加输出键要覆盖错误路径。
- 证据: #1746

### L-149 验字段未泄漏先解析结构，别在整个 JSON 里搜短数字或随机片段
- 类: 别这样
- Seen: 2026-11-06
- 为什么: 端口未泄漏的判据在整个序列化 JSON 里搜五位数字，随机 native_id 偶然含相同片段，误红把无关导出 PR 踢出队列。断言公开字段集合与敏感字段缺席；若必须比内容，构造可区分的夹具，别让随机合法值决定结论。
- 证据: #1790
