# vecops/ — 画布路径查找器内核（ADR 0185 §五 M2）

仓库级路由与不变量在根 `AGENTS.md`；为什么用 vectorcraft、为什么编成 WASM 在 `docs/adr/0185-vector-editing.md`。

- **几何不在这里写**。布尔、分割、偏移、轮廓化描边全部来自 `vendor/` 里随源码内联的 vectorcraft
  （MIT OR Apache-2.0，来历与改动见 `THIRD_PARTY_NOTICES.md`）。`vendor/*/src` 逐字不改；要改上游行为就换
  上游提交并整目录替换，`Cargo.toml` 之外的差异一律不许有。`src/` 只是平铺 `f64` 的 C ABI（编码写在
  `src/lib.rs` 开头），坏输入回 0、不 panic。
- **提交进仓库的 `web/src/lib/vecops/vecops.wasm` 必须与源码逐字节一致**。改了 `src/`、`vendor/`、
  `Cargo.lock` 或 `rust-toolchain.toml`，跑 `vecops/build.sh` 再提交 .wasm；CI 的 workerd job 跑
  `vecops/build.sh --check`。可重建靠钉死的工具链、`--locked`、`--remap-path-prefix` 与 `rustc-wrap.sh`
  （Cargo 的 `-C metadata` 哈希混进了宿主平台，不换的话 macOS 与 Linux 编出来字节不同）。
- ABI 改了：`src/lib.rs` 的 `ABI_VERSION` 加一，前端 `web/src/lib/vecops/kernel.ts` 的 `VECOPS_ABI` 同步改。
- 验证：`cargo fmt --check && cargo clippy --locked --all-targets --no-deps -- -D warnings && cargo test --locked`
  （`--no-deps`：vendor 里的上游代码按上游自己的 lint 配置写，不按我们的 clippy 门槛审）。
