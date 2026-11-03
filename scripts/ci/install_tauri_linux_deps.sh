#!/usr/bin/env bash
# 装 Tauri 在 Linux 上的系统依赖（ci.yml 的 desktop-shell 与 cache-seed 的 src-tauri 腿共用）。
#
# 为什么不是一句 apt-get install：Ubuntu 镜像（azure.archive.ubuntu.com）偶发极慢——
# 10-20 以来 57 次里约 12% 慢到 7–16 分钟、约 7% 吃满 job 的 20 分钟被取消。所以：
#   1. 命中 .deb 缓存（调用方恢复到 $1，key 含 ImageOS / ImageVersion / 包列表哈希）：
#      直接装本地 .deb，不跑 apt-get update、不碰网络。
#   2. 未命中或本地装不上：每次尝试都有总时限，超时就换下一个镜像再来；先 --download-only
#      落进 $1（Post 步骤把它存成缓存），再装——慢下载早失败、早重试，不拖到 job 超时。
# 缓存不是信任来源：.deb 仍由 apt 按签名过的索引校验（网络路径），本地路径装不上就退回网络路径。
set -euo pipefail

deb_dir="${1:?用法：install_tauri_linux_deps.sh <.deb 目录>}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pkgs=()
while IFS= read -r line; do
  case "$line" in '' | '#'*) ;; *) pkgs+=("$line") ;; esac
done <"$here/tauri-linux-apt-packages.txt"
((${#pkgs[@]} > 0)) || { echo "包列表为空" >&2; exit 1; }

mkdir -p "$deb_dir"
shopt -s nullglob

# ── 路径一：精确命中，只用本地 .deb ───────────────────────────────────────────
if [ "${APT_CACHE_HIT:-}" = "true" ]; then
  debs=("$deb_dir"/*.deb)
  if ((${#debs[@]} > 0)) \
    && sudo apt-get install -y --no-install-recommends --no-download "${debs[@]}"; then
    echo "已用缓存的 ${#debs[@]} 个 .deb 装好，未访问网络"
    exit 0
  fi
  echo "::warning::缓存命中但本地 .deb 装不上，退回网络路径"
fi

# restore-keys 可能带回旧镜像的 .deb（同一个包的旧版本）。路径一会把目录里的 .deb 一起装，
# 同包两个版本会装不上，所以网络路径装完后只留「版本 == 现在装着的版本」的那些——存下去的缓存
# 恰好是这一组依赖。
prune_stale_debs() {
  local f pkg ver
  for f in "$deb_dir"/*.deb; do
    pkg="$(dpkg-deb --field "$f" Package)"
    ver="$(dpkg-deb --field "$f" Version)"
    if [ "$(dpkg-query --show --showformat='${Version}' "$pkg" 2>/dev/null || true)" != "$ver" ]; then
      rm -f "$f"
    fi
  done
}

# ── 路径二：网络，限时 + 重试 + 换镜像 ────────────────────────────────────────
apt_opts=(
  -o Acquire::Retries=3
  -o Acquire::http::Timeout=30
  -o Acquire::https::Timeout=30
  -o "Dir::Cache::archives=$deb_dir"
)
# 第一次用 runner 默认镜像；之后依次换到下面两个。
mirrors=("" archive.ubuntu.com mirrors.edge.kernel.org)
# 每个镜像一份总预算（update + download 合计）。3 × 180 = 540 s，加上本地安装与剪枝的余量，
# 必须小于 ci.yml 安装步的 timeout-minutes: 12（720 s），否则第三个镜像还没试完 step 就被砍。
attempt_seconds="${TAURI_APT_ATTEMPT_SECONDS:-180}"

for mirror in "${mirrors[@]}"; do
  if [ -n "$mirror" ]; then
    echo "::warning::换镜像到 $mirror 再试"
    sudo find /etc/apt -type f \( -name '*.list' -o -name '*.sources' \) -exec \
      sed -i -E "s#//(azure\.archive\.ubuntu\.com|archive\.ubuntu\.com|mirrors\.edge\.kernel\.org)/#//$mirror/#g" {} +
  fi
  # update 与下载共用这个镜像的预算：下载只拿 update 用剩的时间；超时是 timeout 的退出码 124，
  # 一并当作本次尝试失败。
  start=$SECONDS
  if sudo timeout "$attempt_seconds" apt-get "${apt_opts[@]}" update \
    && remaining=$((attempt_seconds - (SECONDS - start))) && ((remaining > 0)) \
    && sudo timeout "$remaining" apt-get "${apt_opts[@]}" install -y \
      --no-install-recommends --download-only "${pkgs[@]}"; then
    # 此刻 .deb 全在 $deb_dir 里，安装不再需要网络。
    sudo apt-get "${apt_opts[@]}" install -y --no-install-recommends --no-download "${pkgs[@]}"
    prune_stale_debs
    exit 0
  fi
  echo "::warning::这次尝试失败或超过 ${attempt_seconds}s（镜像：${mirror:-runner 默认}）"
done

echo "::error::所有镜像都没能在限时内装好 Tauri 的 Linux 系统依赖" >&2
exit 1
