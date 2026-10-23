# backend 的 pytest 分片（CI03a）

> 原文出自 `.github/AGENTS.md`「门禁纪律」（2026-09-18 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`.github/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 看护用例（`.github/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`tests/test_merge_queue_workflows.py::TestGates`、`tests/test_pytest_shard.py`
> `.github/AGENTS.md`「必守要点」压缩前原文（2026-10-23，与正文同等有效）：backend-fast 当前 4 片、backend-platforms 4 片（各自独立约束）；选项必须 `=` 形式；进程内自验并集 == 全集；不带 `--shard` 是 no-op；lab 常规套件 = 同机 N 片并行、片号是变量、每片退出码进控制流；权重表只影响平衡不影响覆盖；不加 `-n auto`

- **backend 的 pytest 按文件分片（CI03a，2026-09-16）**：`backend-fast` / `backend-platforms`
  的 matrix 各有一根 `shard` 轴，命令是 `python -m pytest --shard=K/N --shard-manifest=… --junitxml … -rs`（两个
  conftest 选项**必须 `=` 形式**：pytest 预解析会把未知选项的下一个 token 当路径去找 conftest，
  空格形式在路径已存在时 rc 4「unrecognized arguments」）。**片数两个 job 不必相同**：
  `backend-fast` 2026-10-12 起是 4 片（`shard: [1, 2, 3, 4]`，命令 `--shard=K/4`；#896）：
  2026-10-09 由 2 片改 3 片后（两次 #853 合并组 run 37900187745 / 37906698476 的 3.10 / 3.14
  分片 1 撞 60 分钟），合并组每片仍实测 46–59.5 分钟，分片 1 又普遍比分片 2 慢 15–20 分钟，
  #882 / #860 的分片 1 在 57–60 分钟处被取消；故再加一片。job 上限 90 **暂不收回**，等四片在
  main 合并组上有实测再定。必需检查登记在三个 Gate 名字上（不是具体分片 job 名），加片无需改仓库设置。
  CLA 独立服务测试只用约 37 秒，不是这次容量瓶颈；给常规套件加片、不抬超时。`backend-platforms`
  2026-09-30 起是 3 片（`shard: [1, 2, 3]`，命令 `--shard=K/3`）——套件继续变大，2 片下
  Windows 已经吃到 60 分钟上限（#679 run 36685053740 分片 1 撞点被取消：不是挂死，进度
  一直在走，10%→50% 这一段用了 39 分钟，只是太慢；同日另两组分片分别是 39:41 与
  47:45，余量已经很薄）。上限本身是防挂死的止血阀（2026-08-28 没有上限时堵过合并队列
  8 小时 20 分），2026-10-10 起 `backend-platforms` 的上限是
  90（与 `backend-fast` #894 同档）：Windows 3 片里单片实测 55 分钟，#919 的 windows-latest 2 在 60:16
  被取消（run 38044245065），维护者拍板抬到 90；合并队列的等待上限是 180 分钟
  （`scripts/ci/merge_queue_ruleset.py`，已与线上同步），90 仍在其内且留 ≥30 分钟余量。
  重平衡权重 / 继续加片（#636）仍是把它收回来的路径。**2026-10-21 起 `backend-platforms` 是 4 片**
  （`shard: [1, 2, 3, 4]`，命令 `--shard=K/4`；90 分钟上限不动）：#1219 合入后的 6 个合并组里 Windows
  三片 46–80 分钟、#1219 自己那组 82 分钟；同一文件在不同 runner 上能差一倍，重平衡权重只能把中位最重片
  从 73.6 压到 67 分钟、压不住 runner 快慢，而 runner 排队等待实测 0 分钟、每片固定开销 2–3 分钟，所以加片。
  改任一个 job 的片数
  要同步改：`tests/test_merge_queue_workflows.py::test_pytest_shards_agree_between_the_matrix_and_the_command`
  按 job_id 各自的期望片数、`tests/test_ci_baseline.py` 里对 `live_workflow[...]["matrix"]`
  的断言，以及 `.github/workflows/ci.yml` 里那个 job 的实测评论。
  分片在**同一进程 collection 之后**做（`tests/conftest.py` → `tests/support/shard.py`）：每个进程
  算出全部 N 片，自验 **nodeid 集合**的并集 == 全集、两两不交、每片非空、无重复，任一条不成立
  rc 4——不是静默跑全集，也不是静默跑空集。**不带 `--shard` 时钩子是 no-op**，nightly.yml /
  desktop-tauri.yml 的 pytest 命令没有它，跑的仍是全集，
  `tests/test_merge_queue_workflows.py::TestGates::test_unsharded_pytest_lanes_stay_unsharded` 钉住。
- **lab 的常规套件是同机 N 片并行（2026-09-19 起）**：`_lab-qualification.yml` 的「常规测试套件」
  在同一台 16 核 VM 上按 `for k in $(seq 1 "$N")` 起 N 个 `--shard="$k/$N"` 进程、逐个 `wait`、
  任一片非零整步红。覆盖面与从前的单进程全集相同（N 片的并集就是全集，每个进程自己自验），
  被切开的只有时间：单进程 46 分钟 → 四片并行约 13 分钟（2026-09-19 实测，见
  `docs/ci/release-qualification.md`）。slow / 操作序列 harness 那两条命令仍不带 `--shard`。
  `::test_lab_pytest_runs_every_shard_in_one_step` 钉四件事：片号是变量 `"$k/$N"` 不是字面量、
  `seq 1 "$N"` 循环与 `wait` 在同一个 step、每片退出码进 `rc` 并 `exit "$fail"`、别的命令不带
  `--shard`。同机并行的隔离靠一次实跑证明（本机 4 片、lab 首跑），不是靠推断。
  漏片兜底三层：进程内自验 → 静态合同（轴恰好是 `1..N`、命令里的 N 与轴长度同一个数：
  `::test_pytest_shards_agree_between_the_matrix_and_the_command`）→ matrix 语义（任一片不
  success，`needs.backend-fast.result` 就不是 success，Gate 闭集没动）。**job id 不变**，显示名
  变成 `backend-fast (3.10, 1)`，required contexts 仍只有三个 Gate，仓库设置不用重登记。
  权重表 `tests/support/shard_weights.json` 只影响片间平不平衡、不影响覆盖（表坏了是 rc 4 不是
  错分），从 CI 上传的 junit artifact 重算：`python tests/support/shard.py --from-junit …`。
  不许为了并行放宽任何产品断言、不加 `-n auto`。设计、本机实测、负例与已知边界：
  `docs/implementation/ci-foundation/CI03A_PYTEST_SHARDS.md`。
- **用例级挂死看门狗（#1226，2026-10-21）**：`backend-fast` / `backend-platforms` 的 pytest 步骤设
  `TAVOTTO_TEST_HANG_SECONDS=900`。任一用例（含 setup / teardown）超过它，`tests/conftest.py` 的
  `pytest_runtest_protocol` 包装钩子起的 daemon 计时线程打出 nodeid、所有线程栈、本进程的子进程树，
  `os._exit(1)`——一个点名的红灯，代替「整片跑满 90 分钟被取消、顺带踢走队列里无关的 PR」。同一开关让
  ≥60 秒（`TAVOTTO_TEST_SLOW_NOTE_SECONDS`）的用例结束时写一行 `[slow-test …]`，被取消的 job 也留得下
  时间线。本机不设变量 = 整个机制关闭。机制在 `tests/support/hang_watchdog.py`，合同在
  `tests/test_hang_watchdog.py`（含「ci.yml 两条分片 pytest 步骤都设了、且 300 < 值 ≤ 1800」）。
  不用 `faulthandler.dump_traceback_later(exit=True)`：它全进程只有一个定时器，会和 pytest 内置的
  `faulthandler_timeout` 互相覆盖，也打不出 nodeid。10-20 的那几次取消**不是挂死**而是慢（见 #1226
  的结论评论）：所以 900 秒只管真挂死，慢的那类由 `[slow-test]` 时间线和权重重算处理。
- **用例结束时不许留下 render child（#1285，2026-10-22）**：同一个 pytest 进程里，某条用例经应用 / facade 用了进程级共享的
  `renderhost.shared()` 而没人关，`python -m tavotto.rendercore.renderchild` 就活到整片结束，之后同片里「此刻没有子进程」
  一类断言随分片组成随机红。`tests/conftest.py` 两个 autouse fixture（逻辑在 `tests/support/render_children.py`）：
  `_render_children_reaped` 按**用例**判——本用例期间新建的**非共享** `RenderHost` 起的 child 仍活着就失败，打出 pid 与
  完整命令行，随后只杀登记过的 PID（先于用例建好的、模块 / 会话 fixture 持有的宿主归它的 fixture）；
  `_render_shared_host_reaped_per_module` 按**模块**收进程级共享实例（`shutdown_shared()` 连绑定一起丢）。不按用例收：
  每条都关会让下一条重起 child，test_rendercore_app 实测慢约 20%。按模块收同样保证共享 child 不跨模块活下去。
  `describe_children()` 的 `ps` 带 `-ww`：Linux procps 在 stdout 非终端时把命令行截在 80 列。反证见
  `tests/test_render_children_guard.py`。
