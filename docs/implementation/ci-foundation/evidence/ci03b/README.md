# evidence/ci03b — `package` 冒烟实例隔离的本机证据（2026-09-16，macOS，worktree）

- `local_runs/build_wheel.log`：`python -m build --wheel`（`tavotto-0.14.0-py3-none-any.whl`）。
- `local_runs/real_wheel_stderr.txt` / `real_wheel_result.json` / `real_wheel_server.log`：干净 venv 装 wheel 之后
  `scripts/ci/package_smoke.py --python <venv>/bin/python` 一次通过（rc 0，`ready_seconds` 2.33，就绪三道 + 两条请求 + 终止全记在 JSON 里；
  `server.log` 里产品打的 `#dnonce=` 已抹）。
- `occupy_then_run.py`：把租来的端口先占住再起真产品的驱动（`--launch` 模板用）；
  `local_runs/real_wheel_occupied_*`：第一次 `lease_lost`（产品自己打「端口 P 被占用，改用 P+1」并在 P+1 上服务）、第二次换号 `ready`，rc 0。
- `mutations_round1.json`：改结构之前那一轮（24/27；M18 / M26 / M27 存活的原因与处置见文档 §6.1）。
- `mutations.json`：最终树的一轮（脚本 vs `tests/test_package_smoke.py`，28/28，含 M28 = 不装 SIGTERM 处理器）。
- `mutate.py` + `mutations_spec_script.json` / `mutations_spec_ci.json`：变异驱动与两份清单（先断言目标串恰好一次 → 变异 → 清 `__pycache__` → pytest 退出码判 → 还原核 md5）。
- `macos_getfqdn_trace.txt`：PR #376 首跑与诊断 run 的 macOS 失败摘录（桩的追踪停在「已 bind（还没 listen）」，轮询序列 refused ×1 → connect timed out ×5）——文档 §9。
- `mutations_ci.json`：ci.yml vs `TestPackageSmokeIsolation`（18/18，每条记着哪几条用例红）。

主文档：[`../../CI03B_PACKAGE_SMOKE_ISOLATION.md`](../../CI03B_PACKAGE_SMOKE_ISOLATION.md)。
