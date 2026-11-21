#!/usr/bin/env bash
# build.sh 的 RUSTC_WRAPPER：把 Cargo 算的 `-C metadata=<哈希>` 换成只跟包名 + 版本有关的值。
# Cargo 的这个哈希混进了宿主平台（`rustc -vV` 的 host 行），它又进了符号名、影响 LTO 后函数的排列——
# 不换的话 macOS 与 Linux 编出来的 .wasm 大小相同、字节不同。依赖图里每个包只出现一次，包名 + 版本足够区分。
set -euo pipefail
rustc="$1"; shift
args=()
for a in "$@"; do
  case "$a" in
    metadata=*) args+=("metadata=${CARGO_PKG_NAME:-x}-${CARGO_PKG_VERSION:-0}") ;;
    *) args+=("$a") ;;
  esac
done
exec "$rustc" "${args[@]}"
