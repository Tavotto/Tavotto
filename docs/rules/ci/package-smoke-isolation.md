# package job 的冒烟按实例隔离（CI03b）

> 原文出自 `.github/AGENTS.md`「门禁纪律」（2026-09-18 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`.github/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **`package` job 的冒烟按实例隔离（CI03b，2026-09-16）**：venv 在 `${{ runner.temp }}/smoke-venv`（两步共用
  `$VENV`，探 `bin` / `Scripts` 那套照旧），起服务那一步是 `python scripts/ci/package_smoke.py --python "$BIN/python"
  --workdir "${{ runner.temp }}/smoke-run"`（step 级 `timeout-minutes: 5`；失败时 `package-smoke-logs-<os>-<python>` 收走
  workdir）。原来的 `/tmp/smoke` + `--port 5199 … &` + `sleep 8` + 两条 curl 三样都不许回来
  （`tests/test_merge_queue_workflows.py::TestPackageSmokeIsolation`）。脚本的判据主语：**端口**是向系统租的
  （产品 `--port` 不接受 0，**不改**——端口冲突用例要的正是「被占用就顺延」），租约在真 bind 之前被抢时产品不会报错退出，
  而是顺延（「端口 P 被占用，改用 Q」）或退 0（「已在 … 运行」），脚本按日志里的占用类文案认出来换号重试；**就绪**
  = `/api/version` 200 + JSON **且**应答者持有本实例 data dir 里那枚凭据（`smoke_app.adopt_session_credentials` +
  `/api/session/ping` 200）——只看公共端点的 200，租约丢失时那个 200 可能来自隔壁实例；**终止** = 进程不存在
  （POSIX 整组 SIGTERM → SIGKILL → `killpg(pgid, 0)` ESRCH，组里剩人再升级；Windows `taskkill /T /F`），
  不是「发了信号」。data / config 由脚本放在 workdir 下（每次尝试一套），yml 里**不再**另设 `TAVOTTO_*_DIR`。
  被起的进程可换（`--launch` 模板，单测用 `tests/support/stub_http_server.py` 的十一种 `--fail-mode`）。
  设计、本机实测（真 wheel 就绪 2.3 s vs 原来盲等 8 s）、负例与已知边界：
  `docs/implementation/ci-foundation/CI03B_PACKAGE_SMOKE_ISOLATION.md`。
