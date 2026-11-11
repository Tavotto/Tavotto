#!/usr/bin/env bash
# 让 apt 用一个此刻连得上的 Ubuntu 镜像（posix-e2e 的 `playwright install --with-deps` 前调用）。
#
# 为什么：`--with-deps` 自己跑 apt-get update，只认 runner 默认的 azure.archive.ubuntu.com；
# 2026-11-11 azure 在 runner 上解析失败一个多小时，posix-e2e 三片全红在装依赖、一条用例没跑。
# 做法与 install_tauri_linux_deps.sh 换镜像同一套改写（.list / .sources / apt-mirrors.txt）：
# 依次探 InRelease，第一个通的就用；azure 通则什么都不改；全不通也不改、交给 apt 自己报错。
# 只换主机名，不换签名：apt 照旧按 Ubuntu 归档密钥校验索引。
set -uo pipefail

codename="$(. /etc/os-release 2>/dev/null && echo "${VERSION_CODENAME:-noble}")"
chosen=""
for host in azure.archive.ubuntu.com archive.ubuntu.com mirrors.edge.kernel.org; do
  if curl -fsS --max-time 15 -o /dev/null "http://$host/ubuntu/dists/${codename:-noble}/InRelease"; then
    chosen="$host"
    break
  fi
  echo "::warning::Ubuntu 镜像 $host 此刻连不上"
done

if [ -z "$chosen" ]; then
  echo "::warning::三个 Ubuntu 镜像都连不上，保持 runner 默认，交给 apt 报错"
  exit 0
fi
[ "$chosen" = azure.archive.ubuntu.com ] && exit 0

echo "::warning::apt 改用 $chosen"
sudo find /etc/apt -type f \( -name '*.list' -o -name '*.sources' -o -name 'apt-mirrors.txt' \) -exec \
  sed -i -E "s#//(azure\.archive\.ubuntu\.com|archive\.ubuntu\.com|mirrors\.edge\.kernel\.org)/#//$chosen/#g" {} +
