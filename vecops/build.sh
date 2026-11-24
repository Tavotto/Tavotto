#!/usr/bin/env bash
# 把路径查找器内核编成 web/src/lib/vecops/vecops.wasm（ADR 0185 §五 M2）。
#
#   vecops/build.sh           重建并覆盖提交进仓库的 .wasm
#   vecops/build.sh --check   重建到临时目录，和提交的那份逐字节比对（CI 跑这个）
#
# 逐字节可重建靠四件事：rust-toolchain.toml 钉死编译器版本；Cargo.lock 钉死依赖（--locked）；
# --remap-path-prefix 把本机路径（仓库位置、cargo 缓存）抹成固定前缀，产物里不留机器相关的字符串；
# rustc-wrap.sh 把 Cargo 混进了宿主平台的 -C metadata 换成平台无关的值。
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dest="$here/../web/src/lib/vecops/vecops.wasm"
cargo_home="${CARGO_HOME:-$HOME/.cargo}"
target="$(mktemp -d)"
trap 'rm -rf "$target"' EXIT

export RUSTFLAGS="--remap-path-prefix=$here=/vecops --remap-path-prefix=$cargo_home=/cargo -C target-feature=-reference-types"
export CARGO_TARGET_DIR="$target"
# 宿主平台不进符号名（见 rustc-wrap.sh），macOS / Linux / Windows 编出同一份字节
export RUSTC_WRAPPER="$here/rustc-wrap.sh"
# 时间戳与本地化不进产物，但钉住免得以后的工具链悄悄带上
export SOURCE_DATE_EPOCH=0 LC_ALL=C
(cd "$here" && cargo build --locked --release --target wasm32-unknown-unknown ${CARGO_OFFLINE:+--offline})
built="$target/wasm32-unknown-unknown/release/tavotto_vecops.wasm"

if [[ "${1:-}" == "--check" ]]; then
  if cmp -s "$built" "$dest"; then
    echo "vecops.wasm 与源码一致（$(wc -c < "$dest" | tr -d ' ') 字节）"
  else
    echo "::error::web/src/lib/vecops/vecops.wasm 与 vecops/ 源码重建的不一致：改了 vecops/ 之后要跑 vecops/build.sh 并提交新的 .wasm" >&2
    shasum -a 256 "$built" "$dest" >&2 || true
    exit 1
  fi
else
  cp "$built" "$dest"
  echo "已写入 $dest（$(wc -c < "$dest" | tr -d ' ') 字节）"
fi
