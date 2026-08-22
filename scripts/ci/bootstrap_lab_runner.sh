#!/usr/bin/env bash
# 实验室 runner 的依赖与目录准备。**只做准备，不做注册。**
#
# 刻意不做的事，每一条都有理由：
#
#   * **不注册 GitHub runner**。注册要拿一次性 token，把 token 传给脚本
#     意味着它会落进 shell 历史、CI 日志或某个人的剪贴板。注册那一步
#     留给文档里的手工命令（docs/ci/self-hosted-runner.md）。
#   * **不改防火墙 / 不动 sshd / 不碰 hypervisor**。网络隔离是管理员的
#     决定，一个 bootstrap 脚本不该替他做。文档里写清楚要求即可。
#   * **不删任何未知文件**。它只创建与安装，不清理——清理有 cleanup.py，
#     那里每一次删除都过路径安全断言。
#   * **对已装的东西幂等**。这台机器会被反复 bootstrap（升级、排障、
#     重装 runner），每次都从头装一遍既慢又容易把好的配置覆盖掉。
#
# 用法：
#     sudo ./bootstrap_lab_runner.sh --check          # 只检查，不改任何东西
#     sudo ./bootstrap_lab_runner.sh                  # 装依赖 + 建目录
#     sudo ./bootstrap_lab_runner.sh --user ci-bot    # 指定 runner 用户
set -euo pipefail

RUNNER_USER="${RUNNER_USER:-github-runner}"
STATE_ROOT="${TAVOTTO_CI_STATE_ROOT:-/srv/tavotto-ci}"
CHECK_ONLY=0

usage() {
    sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        --check)      CHECK_ONLY=1 ;;
        --user)       RUNNER_USER="${2:?--user 需要一个用户名}"; shift ;;
        --state-root) STATE_ROOT="${2:?--state-root 需要一个路径}"; shift ;;
        -h|--help)    usage ;;
        *) echo "未知参数：$1（用 --help 看用法）" >&2; exit 2 ;;
    esac
    shift
done

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m警告:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m失败:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 前置检查
[ "$(uname -s)" = "Linux" ] || die "这个脚本只支持 Linux（实验室 runner 是 Ubuntu 24.04）"

if [ "$CHECK_ONLY" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
    die "需要 root（装包与建 /srv 目录）。加 --check 可以无 root 只做检查。"
fi

if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    say "系统：${PRETTY_NAME:-未知}"
    case "${VERSION_ID:-}" in
        24.04) ;;
        *) warn "目标平台是 Ubuntu 24.04；当前是 ${VERSION_ID:-未知}，可能需要手工调整包名" ;;
    esac
fi

CPUS="$(nproc)"
MEM_GIB="$(awk '/MemTotal/ {printf "%.0f", $2/1024/1024}' /proc/meminfo)"
say "规格：${CPUS} vCPU / ${MEM_GIB} GiB"
[ "$CPUS" -ge 8 ]     || warn "建议 ≥ 8 vCPU（推荐 16）；核太少时 benchmark 的并发假设不成立"
[ "$MEM_GIB" -ge 16 ] || warn "建议 ≥ 16 GiB（推荐 32）"

# ---------------------------------------------------------------- 依赖清单
# 只列 CI 真正用得上的。刻意不装 GUI、桌面构建链、数据库之类——
# 这台机器的职责只有 Linux qualification，装得越少，可攻击面与维护面越小。
APT_PACKAGES=(
    build-essential pkg-config libssl-dev
    ca-certificates curl wget git jq unzip zip xz-utils file
    python3 python3-pip python3-venv python3-dev
    fonts-noto-cjk            # corpus 里的中文 case 要它才画得出字
)
# **flock 刻意不在上面这张表里。** Debian/Ubuntu 没有叫 flock 的包，那个二进制
# 来自 util-linux（essential，装不掉也卸不掉）。写进去的后果不是「多装一个包」，
# 是 `apt-get install` 整条命令以 `E: Unable to locate package flock` 失败 ——
# 而它是安装路径的**第一步**，于是这个脚本在 Ubuntu 上从来没有跑完过一次。
# 2026-08-22 配置实验室 runner 时才发现：`--check` 那边查的是**二进制**
# （command -v flock，永远存在），所以检查一路绿灯、安装当场就死。
# 检查那张表里保留 flock 是对的——要确认的本来就是「这台机器上有没有这个命令」。

check_cmd() {
    local cmd="$1" why="$2"
    if command -v "$cmd" >/dev/null 2>&1; then
        printf '  \033[32m✓\033[0m %-12s %s\n' "$cmd" "$($cmd --version 2>&1 | head -1 | cut -c1-56)"
    else
        printf '  \033[31m✗\033[0m %-12s 缺失 — %s\n' "$cmd" "$why"
        return 1
    fi
}

# ---------------------------------------------------------------- 检查模式
if [ "$CHECK_ONLY" -eq 1 ]; then
    say "检查模式：不修改任何东西"
    MISSING=0
    for spec in "git:版本与元数据" "python3:全部 CI 脚本" "node:前端" "pnpm:前端" \
                "cargo:workerd 门禁" "curl:下载" "jq:报告处理" "flock:服务端互斥"; do
        check_cmd "${spec%%:*}" "${spec#*:}" || MISSING=$((MISSING + 1))
    done
    echo
    if [ -d "$STATE_ROOT" ]; then
        OWNER="$(stat -c '%U' "$STATE_ROOT")"
        printf '  持久化根 %s 存在，属主 %s\n' "$STATE_ROOT" "$OWNER"
        [ "$OWNER" = "$RUNNER_USER" ] || warn "属主不是 $RUNNER_USER —— runner 会写不进去"
        df -h "$STATE_ROOT" | awk 'NR==2 {printf "  磁盘：%s 可用 / %s（已用 %s）\n", $4, $2, $5}'
    else
        printf '  \033[31m✗\033[0m 持久化根 %s 不存在\n' "$STATE_ROOT"
        MISSING=$((MISSING + 1))
    fi
    if id "$RUNNER_USER" >/dev/null 2>&1; then
        printf '  \033[32m✓\033[0m 用户 %s 存在\n' "$RUNNER_USER"
    else
        printf '  \033[31m✗\033[0m 用户 %s 不存在\n' "$RUNNER_USER"
        MISSING=$((MISSING + 1))
    fi
    echo
    [ "$MISSING" -eq 0 ] && { say "检查通过"; exit 0; }
    die "$MISSING 项未就绪；去掉 --check 重跑以安装"
fi

# ---------------------------------------------------------------- 安装
say "安装 apt 依赖（已装的会自动跳过）"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq "${APT_PACKAGES[@]}"

say "确保 runner 用户存在：$RUNNER_USER"
if id "$RUNNER_USER" >/dev/null 2>&1; then
    echo "  已存在，不改动其属性"
else
    # **不给 sudo**。CI job 不需要 root；给了的话，任何一条 workflow 里的
    # 命令都能改这台机器的系统状态，而 workflow 是随代码走的。
    useradd --create-home --shell /bin/bash "$RUNNER_USER"
    echo "  已创建（刻意不加入 sudo 组）"
fi

say "准备持久化根：$STATE_ROOT"
# 布局与 scripts/ci/_common.py 的 LAYOUT 保持一致。两边分叉的话，
# preflight 会在一台「看起来已经配好」的机器上报缺目录。
for sub in cache locks upgrade/state upgrade/projects baselines/perf baselines/visual reports tmp; do
    install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0755 "$STATE_ROOT/$sub"
done
chown "$RUNNER_USER:$RUNNER_USER" "$STATE_ROOT"
echo "  已建 8 个子目录，属主 $RUNNER_USER"

say "Node 与 pnpm"
if command -v node >/dev/null 2>&1 && node --version | grep -qE '^v(2[2-9]|[3-9][0-9])'; then
    echo "  node $(node --version) 已满足（要求 ≥ 22）"
else
    warn "node 缺失或版本过低。**不自动装**：装法（NodeSource / nvm / 官方 tarball）
    取决于这台机器的运维约定，脚本替你选一种反而会和现状打架。
    见 docs/ci/self-hosted-runner.md 的 Node 一节。"
fi
if command -v pnpm >/dev/null 2>&1; then
    echo "  pnpm $(pnpm --version) 已装"
else
    warn "pnpm 缺失；装好 node 之后 npm i -g pnpm@11"
fi

say "Rust"
if command -v cargo >/dev/null 2>&1; then
    echo "  $(cargo --version) 已装"
else
    warn "cargo 缺失。用 runner 用户装：
    sudo -u $RUNNER_USER sh -c 'curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --component clippy,rustfmt'
    装完记得把 ~/.cargo/bin 加进 runner 服务的 PATH——systemd 的最小 PATH 不含它，
    漏了的话 workerd 那几步会直接 command not found。"
fi

say "文件描述符上限"
# **量的必须是 runner 服务，不是这个 shell。** 旧实现读 `ulimit -n`，那是
# `sudo` 起的 root shell 的限制，与 job 真正跑在里面的那个进程毫无关系：
# 两个数可以一个 65536 一个 1024，方向还任意。而 lab_preflight 的
# 「文件描述符上限」是**硬阻断**（soft < 4096 直接拦下整个 lab job），
# 所以量错对象的代价不是提示不准，是这个脚本说完「准备完成」之后，
# 那台机器一个 lab job 都跑不起来。
NOFILE_MIN=4096   # 与 scripts/ci/lab_preflight.py 的判据同源（有用例对拍）
nofile_of_service() {
    local pid; pid="$(systemctl show -p MainPID --value "$1" 2>/dev/null || true)"
    [ -n "${pid:-}" ] && [ "$pid" != "0" ] || return 1
    awk '/open files/ {print $4}' "/proc/$pid/limits" 2>/dev/null
}
mapfile -t RUNNER_UNITS < <(systemctl list-units --type=service --all --no-legend \
                            'actions.runner.*' 2>/dev/null | awk '{print $1}')
if [ "${#RUNNER_UNITS[@]}" -eq 0 ]; then
    warn "还没注册 GitHub runner，跳过 FD 上限检查。注册之后重跑本脚本
    （或手工按下面的写法配 LimitNOFILE=65536）。"
else
    NOFILE_FIXED=0
    for unit in "${RUNNER_UNITS[@]}"; do
        cur="$(nofile_of_service "$unit" || echo 0)"
        if [ "${cur:-0}" -ge "$NOFILE_MIN" ]; then
            printf '  \033[32m✓\033[0m %-52s soft=%s\n' "$unit" "$cur"
            continue
        fi
        dropin="/etc/systemd/system/${unit}.d"
        install -d -m 0755 "$dropin"
        cat > "$dropin/limits.conf" <<CONF
# 由 scripts/ci/bootstrap_lab_runner.sh 写入。
# soak 会同时开多个 worker 与 HTTP 连接；systemd 默认的 soft=1024 撞上限时
# 表现是随机的 "Too many open files"，与真实的句柄泄漏几乎分不开。
[Service]
LimitNOFILE=65536
CONF
        printf '  \033[33m→\033[0m %-52s soft=%s，已写 drop-in\n' "$unit" "${cur:-未知}"
        NOFILE_FIXED=$((NOFILE_FIXED + 1))
    done
    if [ "$NOFILE_FIXED" -gt 0 ]; then
        warn "$NOFILE_FIXED 个 runner 服务写了 LimitNOFILE drop-in，**需要重启才生效**。
    本脚本刻意不替你重启——正在跑的 job 会被打断。等它们空闲时：
        sudo systemctl daemon-reload
        sudo systemctl restart ${RUNNER_UNITS[*]}"
    fi
fi

echo
say "准备完成。接下来的两件事**刻意留给手工**："
cat <<EOF

  1) 注册 GitHub runner（需要一次性 token，不要写进任何脚本或文件）：
     见 docs/ci/self-hosted-runner.md 的「注册」一节。
     标签必须包含：self-hosted, linux, x64, tavotto-lab

  2) 网络隔离（由管理员按实验室策略配置，本脚本绝不代劳）：
     放行 GitHub / PyPI / npm / crates 等必要出站；
     禁止这台 VM 主动访问其它实验室服务器。

  验证：sudo -u $RUNNER_USER TAVOTTO_CI_STATE_ROOT=$STATE_ROOT \\
            python3 scripts/ci/lab_preflight.py --mode nightly
EOF
