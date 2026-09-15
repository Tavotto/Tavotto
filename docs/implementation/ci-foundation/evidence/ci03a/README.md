# evidence/ci03a/ · CI03a 的本机证据

全部产自 worktree（本机 macOS arm64 Mac mini 12 核，Python 3.13.11 / matplotlib 3.11.2 / pytest 9.1.1，与 CI00 的 `pytest/local_run.json`
同一台机器），日期 2026-09-16（UTC 09-15 晚）。三组长跑（A / B / C）是实现 commit `064bc541` 的树；collection 层的证据与负例是收尾之后的树
（多 1 条用例，4686）。**没有一条真实 CI run**：不能 push。解读在上一级的 [`CI03A_PYTEST_SHARDS.md`](../../CI03A_PYTEST_SHARDS.md)。

| 路径 | 内容 | 产出方式 |
|---|---|---|
| `driver.sh` / `driver.log` | 三组跑的驱动与起止时刻、退出码（A 全量 → B s1、s2 顺序 → C c1 ∥ c2 并发） | `nohup driver.sh &`（这个起法的坑见文档 §3.4） |
| `full.log` / `junit_full.csv` / `full.time` | A：全量（不带 `--shard`）的 pytest 输出、junit（裁成 classname / name / time / outcome）、`/usr/bin/time -l` | `driver.sh` |
| `s1.*` / `s2.*` / `m1.json` / `m2.json` | B：两片**顺序**各跑一遍（各自新进程）的输出 / junit CSV / time / manifest | 同上 |
| `c1.*` / `c2.*` / `c1_manifest.json` / `c2_manifest.json` | C：两片**同时**跑（两进程并发）的输出 / junit CSV / time / manifest | 同上 |
| `compare_sets.py` / `compare_sets.json` | 集合级比对（主语 `(classname, name)` 集合）：s1 ∪ s2 == full、s1 ∩ s2 = ∅、c1 == s1、c2 == s2、计数逐片相加、并发 outcome 逐条等于顺序、manifest 的 selected_files == junit 出现的文件、nodeids_* == testcase 数；14 条 check 全 true，退出码 0 | `compare_sets.py <runs_dir> compare_sets.json`（当时读的是 junit.xml 原件，CSV 是同一份数据的裁剪） |
| `timing_table.json` | 五次跑的 real / user / sys / 峰值 RSS / junit 用例时间合计 / 摘要行，并发对顺序的差 | 从 `*.time` 与 junit 算 |
| `collect_full.txt` / `collect_shard1.txt` / `collect_shard2.txt` / `collect_m1.json` / `collect_m2.json` | collection 层：全集 4686 条 nodeid、两片各自的 `--collect-only`（首行是 conftest 打的分片摘要）与 manifest | `.venv/bin/python -m pytest --collect-only [--shard=K/2 --shard-manifest=…]` |
| `compare_collect.py` / `compare_collect.txt` | collection 层并集比对，退出码 0 | `compare_collect.py collect_full.txt collect_shard1.txt collect_shard2.txt` |
| `collect_identity.txt` | 负例 D⑤：装钩子前后 `--collect-only` 输出（去掉摘要行墙钟后缀）与 stderr 的 md5、`cmp` 退出码、原始 diff（只差墙钟） | 一次性命令 |
| `negative_cli_cases.txt` | 负例 D② / D③ / D④ / D⑥ / D⑦ / D⑧ 的 ERROR 行与退出码 | 一次性命令 |
| `negative_d1_union_check.txt` | 负例 D①a（丢一个文件 → rc 4）与 D①b（再拿掉并集检查 → rc 0、静默漏 46 条） | 对 `tests/support/shard.py` 的真实变异，跑完按备份还原并核 md5 |
| `sigint_a_foreground.txt` / `sigint_b_nohup.txt` / `sigint_c_foreground.txt` / `sigint_c_nohup.txt` | §3.4 的双向验证：同一条信号用例前台 rc 0 / nohup 后台 rc 1；`signal.getsignal(SIGINT)` 前台 `default_int_handler` / nohup 后台 `1`（SIG_IGN） | 一次性命令（写在文档 §3.4） |
| `mutations.json` / `mutations_summary.txt` | 单元测试的变异反证 22 条（M01…M21 + M01b），每条的目标串 / 预期红 / 实际红 / 退出码 | `mutate.py`（会话 scratchpad，不进仓库——与 CI01 同一做法；顺序：断言目标串恰好一次 → 变异 → 清 `__pycache__` → pytest → 还原核 md5；每条的 `target_file` / `old` / `new` 串原样记在 JSON 里，可复现） |
| `mutations_ci.json` / `mutations_ci.txt` | ci.yml / nightly.yml 合同测试的变异反证 10 条（每条的变异内容写在 `id` 里；收尾的 C11 / C12 两条在文档 §4 里，未入 JSON） | `mutate_ci.py`（会话 scratchpad，不进仓库） |
