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

# ------------------------------------------------- runner 服务真正生效的环境
# **量的必须是 runner 服务的 PATH，不是任何一个 shell 的 PATH。**
#
# 这台机器上同时存在三份互不相同的 PATH，而只有最后一份说了算：
#
#   root 的 PATH                ← 这个脚本自己所在的 shell
#   $RUNNER_USER 的**登录** PATH ← `sudo -u … -i` 读 ~/.profile 才有的那份，
#                                 rustup 装完把 ~/.cargo/bin 写在这里
#   **runner 服务的 PATH**       ← job 真正跑在里面的那份
#
# 前两份都会给出假答案，方向还相反：以 root 查，一台配置正确的机器被判成
# 「未就绪」；以登录 shell 查（本 PR 的上一版正是这么写的），~/.profile 里有
# cargo 就报 ✓ —— 而 `.env` 没配的话 job 一起来就 command not found，恰恰是
# 这道检查本该逮住的那种错配。
#
# runner 自己的解析顺序（actions/runner，逐层覆盖，后面的赢）：
#
#   1. systemd 给的最小 PATH  —— unit 里没有 Environment=PATH
#   2. <根>/.path             —— runsvc.sh 开头 `export PATH=$(cat .path)`。
#                                该文件由 config.sh 调 env.sh 写下
#                                （`echo $PATH>.path`），存的是**配置那一刻
#                                那个 shell** 的 PATH，未必含 ~/.cargo/bin
#   3. <根>/.env 的 PATH=     —— Runner.Listener 启动时 LoadAndSetEnv 读它，
#                                发生在 runsvc.sh 之后，所以它覆盖 .path
#
# **所以不能只读 .env。** 从登录 shell 跑过 config.sh 的机器，cargo 在 `.path`
# 里、`.env` 里没有，job 照样跑得起来；只认 .env 会把它误报成红。
#
# 2026-08-22 在实验室 runner（tavotto-ci-01，四个实例）上实测：`.path` 是
# `/usr/local/sbin:…:/snap/bin`（解析不出 cargo），`.env` 的 PATH= 带
# `/home/runner/.cargo/bin`，而 job 里的 `cargo build` 是成功的 —— 三层的
# 优先级就是这么验的。

runner_home() { getent passwd "$RUNNER_USER" 2>/dev/null | cut -d: -f6; }

# runner 的 systemd unit。**唯一的发现出处**，FD 那段也用它：两处各写一份
# 迟早分叉，而分叉的表现是「工具按 A 实例的配置查、FD 按 B 实例的进程量」。
# 逐列找 `actions.runner.*` 而不是取 $1 —— 服务异常时 systemctl 会在第一列
# 打一个 ● ，取 $1 会拿到那个圆点。
discover_runner_units() {
    systemctl list-units --type=service --all --no-legend 'actions.runner.*' 2>/dev/null \
        | awk '{ for (i = 1; i <= NF; i++) if ($i ~ /^actions\.runner\./) { print $i; break } }'
}

# unit → runner 根目录（ExecStart 指向根下的 runsvc.sh）
root_of_unit() {
    local p
    p="$(systemctl show -p ExecStart --value "$1" 2>/dev/null \
         | sed -n 's/.*[{ ]path=\([^ ;]*\).*/\1/p' | head -1)"
    [ -n "$p" ] || return 1
    dirname "$p"
}

# `.env` 的解析必须逐字复现 runner 的 LoadAndSetEnv：按**第一个** `=` 切开，
# 键不 trim、注释不作特殊处理、后面的覆盖前面的。于是 `# PATH=/x` 的键是
# `# PATH` 而不是 `PATH` —— 按整键精确匹配，注释行与空行自然落在外面，一条
# 被注释掉的旧 PATH 也不会被当成生效的那条。
env_path_of_root() {
    local f="$1/.env"
    [ -r "$f" ] || return 1
    awk '{ i = index($0, "=")
           if (i > 1 && substr($0, 1, i - 1) == "PATH") { v = substr($0, i + 1); found = 1 } }
         END { if (!found) exit 1; print v }' "$f"
}

# 服务 PATH 与它的出处（两个字段，制表符分隔）。两个文件都读不到就**失败**
# —— 那时没有可信答案，退回去查别人的 PATH 只会把假绿印成 ✓。
service_path_of_root() {
    local root="$1" p
    if p="$(env_path_of_root "$root")"; then
        printf '%s\t%s\n' "$p" "$root/.env"
        return 0
    fi
    if [ -r "$root/.path" ]; then
        printf '%s\t%s\n' "$(cat "$root/.path")" "$root/.path"
        return 0
    fi
    return 1
}

# 谁来执行探测。`--check` 明确允许无 root（见上面的前置检查），于是调用方
# 可能是**第三个**账号：既不是 root、也不是 $RUNNER_USER。那时 PATH 仍然读得到
# （.env/.path 是普通文件），但「这个二进制 $RUNNER_USER 有没有权限执行」验不了。
# **说清楚验不了，比按调用方自己的 PATH 查完再标成 runner 的强** —— 管理员装了
# 而 runner 没装是假绿，反过来是假红，两个方向都错。
if [ "$(id -u)" -eq 0 ] && id "$RUNNER_USER" >/dev/null 2>&1; then
    PROBE_MODE=sudo
elif [ "$(id -un)" = "$RUNNER_USER" ]; then
    PROBE_MODE=self
else
    PROBE_MODE=foreign
fi
PROBE_KIND=unresolved
SERVICE_PATH=""
SERVICE_SRC=""
SERVICE_ROOT=""
SERVICE_COUNT=1   # 共用这一份 PATH 的 runner 实例数

# 标签由**决定探测方式的那两个变量**算出来，不另写一份文案 ——
# 「按 X 的 PATH 查」与实际查的对象分头演进，正是这道检查最初骗人的方式。
probe_label() {
    local who
    case "$PROBE_MODE" in
        sudo|self) who="以 $RUNNER_USER 的身份" ;;
        *)         who="以当前用户 $(id -un) 的身份，验不了 $RUNNER_USER 的执行权限" ;;
    esac
    local also=""
    # **点名一个文件、而那份配置其实由 N 个实例共用**，会让运维只改其中一个，
    # 剩下几个照旧坏着——而 job 落在哪个实例上是调度决定的。
    [ "${SERVICE_COUNT:-1}" -gt 1 ] && \
        also="$(printf '，另 %d 个实例同配置' "$((SERVICE_COUNT - 1))")"
    case "$PROBE_KIND" in
        service) printf '按 runner 服务的 PATH，来自 %s%s，%s' "$SERVICE_SRC" "$also" "$who" ;;
        login)   printf '按 %s 的登录 PATH —— 不是服务 PATH，%s' "$RUNNER_USER" "$who" ;;
        *)       printf '未能确定 runner 服务的 PATH' ;;
    esac
}

# 在服务 PATH 下执行。`env -i` 清干净 —— 继承调用方的环境等于又把「谁在跑
# 这个脚本」混进判据里。
run_in_service_path() {
    case "$PROBE_MODE" in
        sudo) sudo -u "$RUNNER_USER" env -i PATH="$SERVICE_PATH" \
                   HOME="$(runner_home)" LANG=C.UTF-8 sh -c "$1" 2>/dev/null ;;
        *)    env -i PATH="$SERVICE_PATH" HOME="${HOME:-/}" LANG=C.UTF-8 \
                   sh -c "$1" 2>/dev/null ;;
    esac
}

# 登录 shell。**只用来回答「这东西到底装没装」**，永远不作为 ✓ 的判据 ——
# 它存在的唯一理由是让 ✗ 能分清「根本没装」和「装了但没接进服务 PATH」，
# 这两种情况的修法完全不同。
as_runner_login() {
    case "$PROBE_MODE" in
        sudo) sudo -u "$RUNNER_USER" -i sh -lc "$1" 2>/dev/null ;;
        self) sh -lc "$1" 2>/dev/null ;;
        *)    return 1 ;;
    esac
}

probe_cmd() {
    case "$PROBE_KIND" in
        service) run_in_service_path "$1" ;;
        login)   as_runner_login "$1" ;;
        *)       return 1 ;;
    esac
}

# 配置改了却没重启，跑着的那个 listener 手里还是旧环境。**`.env` 的 PATH 是
# Runner.Listener 启动时一次性读进内存的**，改文件不会传导到已经在跑的进程，
# 也读不回来（`/proc/<pid>/environ` 是 exec 那一刻的快照，只反映 .path 那层，
# 实测确认过）。所以「文件里配好了」不等于「下一个 job 能用上」——这与最初那个
# 假绿是同一个形状，只是错在时间维度上。
#
# 判据用 mtime 比服务启动时间：改在后面 = 现在跑着的服务还没吃到这份配置。
# 取不到时间就不判（宁可不报，也不报错的）。
config_is_newer_than_service() {
    local unit="$1" f="$2" started fmtime
    started="$(systemctl show -p ActiveEnterTimestamp --value "$unit" 2>/dev/null)"
    [ -n "$started" ] || return 1
    started="$(date -d "$started" +%s 2>/dev/null)" || return 1
    [ -n "$started" ] || return 1
    fmtime="$(stat -c %Y "$f" 2>/dev/null)" || return 1
    [ "$fmtime" -gt "$started" ]
}

# 解析出 job 真正会用的那个 PATH。多个 runner 实例各有自己的根（实验室那台
# 有四个），逐个解析后按 PATH 去重 —— 通常配得一样，不一样就每份都要查。
#
# **解析不了的实例不许静默丢掉。** 一个 unit 的根反推不出来、或者它的
# .env/.path 都读不到时，早先这里是 `continue`：只要另外三个实例读得到且通过，
# --check 就报「检查通过」，而 job 完全可能被调度到那个没验过的实例上。
# 沉默地少验一个，与按错的账号验是同一类错——都是**答案的覆盖面与它宣称的
# 不一致**。所以失败的实例照样进这张表，只是打上 bad 标记，由调用方算进未就绪。
SERVICE_ROWS="$(
    for unit in $(discover_runner_units); do
        root="$(root_of_unit "$unit")"
        if [ -z "${root:-}" ]; then
            printf 'bad\t%s\t%s\n' "$unit" "反推不出 runner 根目录（ExecStart 读不到）"
            continue
        fi
        line="$(service_path_of_root "$root")"
        if [ -z "${line:-}" ]; then
            printf 'bad\t%s\t%s\n' "$unit" "$root 下的 .env 与 .path 都读不到"
            continue
        fi
        src="$(printf '%s' "$line" | cut -f2)"
        if config_is_newer_than_service "$unit" "$src"; then
            printf 'bad\t%s\t%s\n' "$unit" \
                "$src 比服务的启动时间新——改了配置没重启，跑着的 listener 还是旧 PATH"
            continue
        fi
        printf 'ok\t%s\t%s\n' "$line" "$root"
    done
)"
SERVICE_BAD="$(printf '%s\n' "$SERVICE_ROWS" | sed -n 's/^bad\t//p')"
SERVICE_PATHS="$(
    printf '%s\n' "$SERVICE_ROWS" | sed -n 's/^ok\t//p' | awk -F'\t' '
        NF >= 3 { if (!($1 in cnt)) { order[++n] = $1; src[$1] = $2; root[$1] = $3 }
                  cnt[$1]++ }
        END { for (i = 1; i <= n; i++) { p = order[i]
                  printf "%s\t%s\t%s\t%d\n", p, src[p], root[p], cnt[p] } }'
)"

if [ -n "$SERVICE_PATHS" ]; then
    PROBE_KIND=service
elif [ -n "$SERVICE_BAD" ]; then
    # runner 注册着，只是没有一个实例的配置读得出来。**这不是「还没注册」**，
    # 不能降级去查登录 PATH —— 那会把一台坏掉的机器报成「只差注册」。
    PROBE_KIND=unresolved
elif [ "$PROBE_MODE" = foreign ]; then
    # 读不到配置，又没有身份去查 —— 这时**没有**可信答案。
    PROBE_KIND=unresolved
else
    # runner 还没注册（这个脚本刻意不做注册），于是根本还不存在服务 PATH。
    # 与下面 FD 那段同一个处理：降级、说清降到了什么、要求注册后重跑。
    PROBE_KIND=login
fi

check_cmd() {
    local cmd="$1" why="$2" found login
    found="$(probe_cmd "command -v -- $cmd" || true)"
    if [ -n "$found" ]; then
        printf '  \033[32m✓\033[0m %-12s %s\n' "$cmd" \
            "$(probe_cmd "$cmd --version" | head -1 | cut -c1-56)"
        return 0
    fi
    printf '  \033[31m✗\033[0m %-12s 缺失 — %s\n' "$cmd" "$why"
    if [ "$PROBE_KIND" = service ]; then
        login="$(as_runner_login "command -v -- $cmd" || true)"
        if [ -n "$login" ]; then
            printf '      装在 %s，但**不在 runner 服务的 PATH 上** —— job 一起来就\n' "$login"
            printf '      command not found。把它所在目录加进 %s/.env 的 PATH= 那行。\n' "$SERVICE_ROOT"
        fi
    fi
    return 1
}

# 一份服务 PATH 走一遍工具表，返回缺了几项。
check_tools_on() {
    local missing=0 spec
    for spec in "git:版本与元数据" "python3:全部 CI 脚本" "node:前端" "pnpm:前端" \
                "cargo:workerd 门禁" "curl:下载" "jq:报告处理" "flock:服务端互斥"; do
        check_cmd "${spec%%:*}" "${spec#*:}" || missing=$((missing + 1))
    done
    return "$missing"
}

# ---------------------------------------------------------------- 检查模式
if [ "$CHECK_ONLY" -eq 1 ]; then
    say "检查模式：不修改任何东西"
    MISSING=0
    # 解析不了的实例逐条报出来并算进未就绪，**绝不静默少验一个**：job 落在
    # 哪个实例上是调度决定的，只要有一个没验过，「检查通过」就名不副实。
    if [ -n "$SERVICE_BAD" ]; then
        while IFS="$(printf '\t')" read -r bunit breason; do
            [ -n "$bunit" ] || continue
            printf '  \033[31m✗\033[0m %s\n      %s\n' "$bunit" "$breason"
            MISSING=$((MISSING + 1))
        done <<EOF
$SERVICE_BAD
EOF
    fi
    # 残差要摆到显眼处，不能只留在那行标签的括号里——末尾一句干净的「检查通过」
    # 会盖过它。PATH 判断是准的（.env/.path 谁读都一样），没验的是执行权限。
    if [ "$PROBE_MODE" = foreign ] && [ "$PROBE_KIND" != unresolved ]; then
        warn "探测不是以 $RUNNER_USER 跑的：PATH 判断是准的（.env/.path 谁读都一样），
    但「这些二进制 $RUNNER_USER 有没有权限执行」没验。要验就用 sudo 重跑。"
    fi
    if [ "$PROBE_KIND" = unresolved ]; then
        # **不按调用方自己的 PATH 冒充服务 PATH。** 读不到就说读不到，
        # 并且算作未就绪 —— 一句「检查通过」而其实是按管理员的 PATH 算出来的，
        # 比没有这道检查更坏。
        printf '  \033[31m✗\033[0m %-12s 读不到 runner 服务的配置（.env/.path）\n' "工具探测"
        printf '      当前既不是 root 也不是 %s，无法确定 job 会用哪个 PATH。\n' "$RUNNER_USER"
        printf '      用 sudo 重跑，或以 %s 的身份重跑（--user 指对账号）。\n' "$RUNNER_USER"
        MISSING=$((MISSING + 1))
    elif [ "$PROBE_KIND" = login ]; then
        warn "还没注册 GitHub runner，没有服务 PATH 可查。下面按 $RUNNER_USER 的
    **登录** PATH 查「装没装」，那**不是** job 的 PATH：注册完必须重跑本脚本，
    确认 ~/actions-runner/.env 的 PATH= 配好了。"
        printf '  （%s）\n' "$(probe_label)"
        check_tools_on || MISSING=$((MISSING + $?))
    else
        # 每份不同的服务 PATH 各查一遍：job 落在哪个实例上是调度决定的，
        # 只查其中一份等于赌它们配得一样。
        while IFS="$(printf '\t')" read -r spath ssrc sroot scount; do
            [ -n "$spath" ] || continue
            SERVICE_PATH="$spath"; SERVICE_SRC="$ssrc"; SERVICE_ROOT="$sroot"
            SERVICE_COUNT="${scount:-1}"
            printf '  （%s）\n' "$(probe_label)"
            check_tools_on || MISSING=$((MISSING + $?))
        done <<EOF
$SERVICE_PATHS
EOF
    fi
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
    # **别把所有失败都说成「重跑就能装上」。** 「装了但不在服务 PATH 上」这一档
    # 重跑一百遍也不会好（cargo 那一支本来就只是 warn），而运维会照着提示反复
    # 装 rustup 然后一遍遍撞同一堵墙——最初那个 bug 的伤害有一半来自这句话。
    die "$MISSING 项未就绪。每条 ✗ 都写了各自的修法：「不在 runner 服务的 PATH 上」
    改对应 .env 的 PATH= 那行、「没重启」重启那个服务、「读不到」查那个实例的根目录
    ——**这几类重跑本脚本一次都不会修**。只有真正缺包的那些，去掉 --check 重跑能装上。"
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

if [ -n "$SERVICE_BAD" ]; then
    warn "有 runner 实例的配置读不出来或已过期，下面按读得出来的那些判断：
$(printf '%s\n' "$SERVICE_BAD" | sed 's/\t/： /; s/^/    /')"
fi

say "Rust"
# 判据与 --check 同一套：看的是 **runner 服务的 PATH**，不是谁的登录 shell。
# 这里第一次解析出来的服务 PATH 用第一份就够（安装路径只是给人看的提示；
# --check 那边才是逐份查的门禁）。
if [ "$PROBE_KIND" = service ]; then
    SERVICE_PATH="$(printf '%s\n' "$SERVICE_PATHS" | head -1 | cut -f1)"
    SERVICE_SRC="$(printf  '%s\n' "$SERVICE_PATHS" | head -1 | cut -f2)"
    SERVICE_ROOT="$(printf '%s\n' "$SERVICE_PATHS" | head -1 | cut -f3)"
    SERVICE_COUNT="$(printf '%s\n' "$SERVICE_PATHS" | head -1 | cut -f4)"
fi
if [ -n "$(probe_cmd 'command -v -- cargo' || true)" ]; then
    echo "  $(probe_cmd 'cargo --version') 已装（$(probe_label)）"
elif [ -n "$(as_runner_login 'command -v -- cargo' || true)" ]; then
    # **装了、但没接进服务 PATH。** 这条与「没装」的修法完全不同，混成一句的
    # 后果是运维照着提示又装一遍 rustup，然后 job 照旧 command not found。
    warn "cargo 装在 $(as_runner_login 'command -v -- cargo')，但**不在 runner 服务的
    PATH 上**（当前服务 PATH 来自 ${SERVICE_SRC:-未确定}）——job 一起来就
    command not found，而错误信息与真实原因毫不相干。把它所在目录加进
    ${SERVICE_ROOT:-~$RUNNER_USER/actions-runner}/.env 的 PATH= 那行，
    见 docs/ci/self-hosted-runner.md 的「工具链」一节。"
else
    warn "cargo 缺失。用 runner 用户装：
    sudo -u $RUNNER_USER sh -c 'curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --component clippy,rustfmt'
    装完记得把 ~/.cargo/bin 加进 runner 服务的 PATH（~/actions-runner/.env 的
    PATH= 那行）——systemd 的最小 PATH 与 config.sh 存下的 .path 都不含它，
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
# 发现走 discover_runner_units（唯一出处，工具探测那边解析服务 PATH 用的
# 是同一张 unit 表）——两处各写一份的话，会出现「工具按 A 实例的配置查、
# FD 按 B 实例的进程量」。
mapfile -t RUNNER_UNITS < <(discover_runner_units)
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
