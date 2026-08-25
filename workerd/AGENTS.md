# workerd/ — Rust supervisor 规则（2026-08-18，与 Python 池并行）

仓库级路由与不变量在根 `AGENTS.md`。架构、协议与错误码的完整版在
`docs/adr/0004-workerd-supervisor.md`，改动前先读。

- crate 在仓库根的 **`workerd/`**（不进 `src-tauri/`，壳保持薄）；`workerd/target/`
  进 .gitignore；pyproject 的 `exclude` 显式挡住它进 wheel/sdist
  （sdist 的 `include=["tests"]` 是 gitignore 风格模式，会把 `workerd/tests/` 收进去）。
- **Rust 是机制层，Python 是策略层**：解释器优先级（`pool._prioritized_candidates()`）、
  内置 runtime 的 `-B`/env、超时档位、会话与队列上限**全部留在 Python**，
  Flask 把完整 spawn 规格（argv/env/log_path/握手期限）交给 workerd。
  **别在 Rust 里重写探测或渲染**——那是制造第二个权威。
- `pool.py` 的 Python 实现**一行没删**：找不到二进制或 `TAVOTTO_WORKERD=0` 就原路走它，
  它同时是 workerd 的参考实现。**pytest 的 conftest 默认把开关钉成 `0`**，
  否则 `cargo build` 之后整套既有用例会悄悄换一条控制面跑。
- `workerd/src/patchspec.rs` + `pyfloat.rs` 必须**逐字节复现** `engine/patchspec.py`，
  硬验收是同一份 `tests/golden/patch_vectors.json`。已知坑：Python 的浮点 repr
  （`1e+22`/`1e-07`、`-0.0` 保号、`1.0` 补 `.0`）、int 与 float 是两个值
  （serde_json 必须开 `arbitrary_precision`）、转义表照抄 `ESCAPE_DCT`。
- **「起来了」= hello 握过手**，不是「进程对象还在」：Windows 关进程比 POSIX
  慢得多，握手早已失败（写管道 EINVAL）而 `poll()` 还回 None，只看后者会把
  正在退出的进程当成就绪的 workerd——重启计数一次都不加，起来就崩的二进制
  于是无限重启，每次渲染白等一轮 spawn + 握手，还永远退不到 Python 池。
  半启动的那条要先 kill 再重启（否则每次泄漏一个子进程）。
- 语义要点：generation 每 (re)spawn +1 且**上一代的迟到响应一律丢弃**；
  per (会话, stem) 的 render 队列里至多一条、新的顶掉旧的（回 `queue_superseded`）；
  export 一条都不合并；队列有界，满了立即拒绝；取消在飞 = **杀进程**
  （协议层没有协作中断）；淘汰 = kill，不等锁。
- 验证：`cd workerd && cargo test && cargo clippy --all-targets -- -D warnings && cargo fmt --check`。
- 桌面产物必须自带 workerd（`build_desktop.py` 先 cargo build，`tavotto.spec`
  收进 `_internal/`），少了它渲染回退到 Python 池——功能全在、只是慢、零报错。
  冒烟用 `--expect-control-plane workerd` 盯着（见 `.github/AGENTS.md`）。
