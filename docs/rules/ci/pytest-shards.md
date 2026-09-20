# backend 的 pytest 分片（CI03a）

> 原文出自 `.github/AGENTS.md`「门禁纪律」（2026-09-18 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`.github/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **backend 的 pytest 按文件分 2 片（CI03a，2026-09-16）**：`backend-fast` / `backend-platforms`
  的 matrix 各有一根 `shard: [1, 2]` 轴，命令是 `python -m pytest --shard=K/2 --shard-manifest=… --junitxml … -rs`（两个
  conftest 选项**必须 `=` 形式**：pytest 预解析会把未知选项的下一个 token 当路径去找 conftest，
  空格形式在路径已存在时 rc 4「unrecognized arguments」）。
  分片在**同一进程 collection 之后**做（`tests/conftest.py` → `tests/support/shard.py`）：每个进程
  算出全部两片，自验 **nodeid 集合**的并集 == 全集、两两不交、每片非空、无重复，任一条不成立
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
  权重表 `tests/support/shard_weights.json` 只影响两片平不平衡、不影响覆盖（表坏了是 rc 4 不是
  错分），从 CI 上传的 junit artifact 重算：`python tests/support/shard.py --from-junit …`。
  不许为了并行放宽任何产品断言、不加 `-n auto`。设计、本机实测、负例与已知边界：
  `docs/implementation/ci-foundation/CI03A_PYTEST_SHARDS.md`。
