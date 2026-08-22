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
# 也读不回来（`/proc/<pid>/environ` 是 exec 那一刻的快照，只反映 .path 那层）。
#
# **亚秒精度在这条路上拿不到，这一点写在明处。**
#
# 曾经用 `/proc/<pid>` 目录的 mtime 换亚秒精度，实测证明那个量的**语义是错的**：
# 起一个进程、**等 5 秒再第一次** stat 它的 /proc 目录，拿到的是「此刻」而不是
# 进程创建时刻——那是 inode 被实例化的时间，不是进程起来的时间。
#     T0（进程创建）      = 1787400324.471660887
#     5 秒后首次 stat     = 1787400329.477145631   ← 与当时的 now 只差 4ms
# 它一旦实例化就不再漂（连采三次一模一样），所以长期运行的进程看上去「碰巧对」
# ——只是碰巧：那个 inode 恰好在进程刚起来时就被谁访问过。
# **一个语义错的精确值，比一个诚实的粗略值坏得多。**
#
# 语义正确的来源都只有整秒：systemd 的 *Timestamp 字段实测
# `date -d … +%s.%N` 出来是 .000000000；Monotonic 那几个有微秒，但换算回墙钟
# 要靠 /proc/stat 的 btime，而 btime 本身又是整秒。不同来源之间还能差出约一秒
# （systemd 记的是它 exec 的时刻，内核 starttime 记的是进程创建，实测这台机器上
# 两者差 0.62s）。
#
# 所以判据**刻意做成保守的**：`文件 mtime >= 服务启动秒 - 1`。宁可多报一次
# 「要重启」（假红，重启一次即消），也不放过一次同秒内的修改（假绿，会让
# --check 拿着新 PATH 去探测，而跑着的 listener 用的还是旧值）。
STALE_SLACK_SEC=1   # 覆盖「整秒 + 不同来源之间约一秒」的合计不确定度

service_start_epoch() {
    local t
    t="$(systemctl show -p ExecMainStartTimestamp --value "$1" 2>/dev/null)"
    [ -n "${t:-}" ] || return 1
    date -d "$t" +%s 2>/dev/null
}

# 比较本身单独拿出来：它不碰 stat、不碰 systemctl，因此在任何平台上都能直接
# 喂数跑一遍——判据的核心不该只能在 Linux 上验。
# 参数：$1 = 文件 mtime（秒），$2 = 服务启动（秒）。
_file_is_stale() {
    case "$1" in ''|*[!0-9]*) return 1 ;; esac
    case "$2" in ''|*[!0-9]*) return 1 ;; esac
    [ "$1" -ge "$(($2 - STALE_SLACK_SEC))" ]
}

config_is_newer_than_service() {
    local unit="$1" f="$2" ft st
    st="$(service_start_epoch "$unit")" || return 1
    ft="$(stat -c %Y "$f" 2>/dev/null)" || return 1
    _file_is_stale "$ft" "$st"
}

# **两个文件都要查新鲜度，不只是这次选中的那个。** 管理员把 `.env` 里的 PATH=
# 删掉或注释掉之后，解析会回退到 `.path`——可「删掉那一行」本身就是一次修改，
# 而跑着的 listener 手里还是删之前的那个 `.env` PATH。只查选中的 `.path`，
# 这种情况会被判成「没改过」，于是拿 `.path` 的 PATH 当成 job 会用的那个。
stale_config_of_root() {
    local unit="$1" root="$2" f
    for f in "$root/.env" "$root/.path"; do
        [ -e "$f" ] || continue
        if config_is_newer_than_service "$unit" "$f"; then
            printf '%s\n' "$f"
            return 0
        fi
    done
    return 1
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
        stale="$(stale_config_of_root "$unit" "$root")" || stale=""
        if [ -n "$stale" ]; then
            printf 'bad\t%s\t%s\n' "$unit" \
                "$stale 比服务的启动时间新——改了配置没重启，跑着的 listener 还是旧 PATH"
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
# 而且**每份不同的服务 PATH 都要查**。原先这里 `head -1` 只取第一份，理由写的是
# 「安装路径只是给人看的提示」——可它末尾照样打「准备完成」。实例之间配置不同
# 时，第一份找得到 cargo 就报已装，而 job 落到另一份上照旧 command not found：
# 又一次「答案的覆盖面与它宣称的不一致」，正是这个 PR 从头在修的那种错。
cargo_report() {
    if [ -n "$(probe_cmd 'command -v -- cargo' || true)" ]; then
        echo "  $(probe_cmd 'cargo --version') 已装（$(probe_label)）"
        return 0
    fi
    if [ -n "$(as_runner_login 'command -v -- cargo' || true)" ]; then
        # **装了、但没接进服务 PATH。** 这条与「没装」的修法完全不同，混成一句的
        # 后果是运维照着提示又装一遍 rustup，然后 job 照旧 command not found。
        warn "cargo 装在 $(as_runner_login 'command -v -- cargo')，但**不在 runner 服务的
    PATH 上**（这份服务 PATH 来自 ${SERVICE_SRC:-未确定}）——job 一起来就
    command not found，而错误信息与真实原因毫不相干。把它所在目录加进
    ${SERVICE_ROOT:-~$RUNNER_USER/actions-runner}/.env 的 PATH= 那行，
    见 docs/ci/self-hosted-runner.md 的「工具链」一节。"
        return 1
    fi
    warn "cargo 缺失。用 runner 用户装：
    sudo -u $RUNNER_USER sh -c 'curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --component clippy,rustfmt'
    装完记得把 ~/.cargo/bin 加进 runner 服务的 PATH（~/actions-runner/.env 的
    PATH= 那行）——systemd 的最小 PATH 与 config.sh 存下的 .path 都不含它，
    漏了的话 workerd 那几步会直接 command not found。"
    return 1
}
if [ "$PROBE_KIND" = service ]; then
    while IFS="$(printf '\t')" read -r spath ssrc sroot scount; do
        [ -n "$spath" ] || continue
        SERVICE_PATH="$spath"; SERVICE_SRC="$ssrc"; SERVICE_ROOT="$sroot"
        SERVICE_COUNT="${scount:-1}"
        cargo_report || true
    done <<EOF
$SERVICE_PATHS
EOF
else
    cargo_report || true
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

# systemd 眼里**配置**的软上限（unit 与全部 drop-in 合并后的结果），与
# /proc 里那个**正在跑**的值是两回事。
configured_nofile() { systemctl show -p LimitNOFILESoft --value "$1" 2>/dev/null; }

# 「这个值够不够」的**唯一判据**。`LimitNOFILE=infinity` 时 /proc 写的是
# `unlimited`、systemctl 写的是 `infinity`，拿它们做数值比较会报
# "integer expression expected" 并走进失败分支——于是脚本用一个 65536 的
# drop-in 去**降低**一个本来就够用的设置，而且要等重启才发作。
# 读坏了的值当「不够」，而不是原样丢进 `[ -ge ]` 再炸一次。
_nofile_ge() {
    case "$1" in
        unlimited|infinity) return 0 ;;
        ''|*[!0-9]*)        return 1 ;;
    esac
    [ "$1" -ge "$NOFILE_MIN" ]
}

# 三态。**「跑着的不够」不等于「没配」**：管理员刚把 LimitNOFILE 调高、还没重启
# 时，/proc 里还是旧值而配置里已经是新值。这时候写我们自己的 drop-in，会按字典序
# 排在他后面**把他刚配好的值顶掉**（systemd.unit(5)：.d 下的文件按文件名字典序
# 加载，后面的赋值赢），而且同样要等重启才发作——用一个待生效的配置换掉另一个，
# 纯属帮倒忙。这一档只提醒重启。
nofile_state() {
    # **ok 必须两边都够。** 只看运行态的话：`daemon-reload` 已经载入一个低于阈值
    # 的新值、而服务还没重启时，跑着的进程仍然够 → 报 ok，下一次**普通重启**就掉
    # 下去，随后 lab_preflight 拦下每个 job。那时谁也不会想到是 bootstrap 说过 ok。
    if _nofile_ge "$1" && _nofile_ge "$2"; then echo ok
    elif _nofile_ge "$2"; then echo pending
    else echo needs_dropin; fi
}

# 这个脚本开头就写着「不删任何未知文件」。原先它 `cat > "$dropin/limits.conf"`
# ——而 `limits.conf` 正是管理员给 drop-in 起名时最顺手的那个词（限额之外的
# 加固、环境变量都常放在里面）。截断掉的后果要等下次重启才发作，那时谁也想不到
# 是 bootstrap 干的。改成：文件名带自己的名字（systemd 按字典序加载 .d 下所有
# *.conf，多一个文件不冲突），并且**只覆盖确认是自己写的那一份**。
# **以「再问一次系统」为准，不以「我写了一个文件」为准。** systemd.unit(5)：
# .d 下的文件按文件名字典序加载，**后面的赋值赢**。已经存在一个 99-local.conf
# 把 LimitNOFILE 设低时，我们写的 90-… 一个字节没错却根本不生效，而脚本会报
# 「已写 drop-in」。把名字排得更靠后是军备竞赛（别人还能再往后一格），正解是
# 写完 daemon-reload 再问一次 systemd，不生效就如实报出来交给人处理。
#
# daemon-reload **不是重启**：它只让 systemd 重读 unit 文件，不打断在跑的 job。
verify_nofile_effective() {
    local unit="$1" now
    systemctl daemon-reload 2>/dev/null || true
    now="$(configured_nofile "$unit")"
    _nofile_ge "$now" && return 0
    warn "写完 drop-in 之后 $unit 的配置值仍是 ${now:-未知}（要求 ≥ ${NOFILE_MIN}）。
    .d 下按文件名字典序加载、后面的赢——下面这些文件都设了 LimitNOFILE，
    排在 $NOFILE_DROPIN_NAME **后面**的那个才是说了算的那份：
$(systemctl cat "$unit" 2>/dev/null \
    | awk '/^# \//{f=$2} /^[[:space:]]*LimitNOFILE=/{print "        " f ": " $0}')
    把它改掉（或挪到 $NOFILE_DROPIN_NAME 前面）之后重跑本脚本。"
    return 1
}

NOFILE_DROPIN_MARKER="由 scripts/ci/bootstrap_lab_runner.sh 写入"
NOFILE_DROPIN_NAME="90-tavotto-nofile.conf"
write_nofile_dropin() {
    local dropin="$1" conf="$1/$NOFILE_DROPIN_NAME"
    install -d -m 0755 "$dropin" || return 1
    if [ -e "$conf" ] && ! grep -qF "$NOFILE_DROPIN_MARKER" "$conf" 2>/dev/null; then
        warn "$conf 已存在，但不是本脚本写的——**不覆盖**。
    请自己确认它里面的 LimitNOFILE ≥ ${NOFILE_MIN}，或者把它挪开后重跑。"
        return 1
    fi
    # **重定向失败不会让 set -e 生效**——这个函数是作为 `if` 的条件调用的。
    # 目标被设了 immutable、文件系统只读、磁盘满，这一句都会失败，而函数照旧
    # 走到 return 0，调用方于是打印「已写 drop-in」。写完再回读一次：部分写入
    # 与「写进去了但不是那个内容」也一并挡掉。
    if ! cat > "$conf" <<CONF
# $NOFILE_DROPIN_MARKER
# soak 会同时开多个 worker 与 HTTP 连接；systemd 默认的 soft=1024 撞上限时
# 表现是随机的 "Too many open files"，与真实的句柄泄漏几乎分不开。
[Service]
LimitNOFILE=65536
CONF
    then
        warn "写不进 $conf —— 权限、immutable 属性、只读文件系统或磁盘满都会这样。"
        return 1
    fi
    if ! grep -qF "LimitNOFILE=65536" "$conf" 2>/dev/null; then
        warn "$conf 写完却读不回 LimitNOFILE=65536。"
        return 1
    fi
    # 旧版本写的是 limits.conf。**它压过新文件，不是被新文件取代**——排序按
    # 字节走，字母在数字之后（'l'=0x6C > '9'=0x39），所以 limits.conf 排在
    # 90-tavotto-nofile.conf **后面**、最后加载、说了算。真机实测过这个顺序。
    # **仍然不删**：管理员可能把自己的值写进了那个文件（marker 还留着）,
    # 删掉就是把他的设定悄悄降下来。据实说明，由 verify 去判最终是否达标。
    if [ -e "$dropin/limits.conf" ] \
       && grep -qF "$NOFILE_DROPIN_MARKER" "$dropin/limits.conf" 2>/dev/null; then
        echo "      （$dropin/limits.conf 是本脚本旧版本写的，且排在 $NOFILE_DROPIN_NAME"
        echo "        之后——最终生效的是它。值够就没事；不够的话下面会报出来。）"
    fi
    return 0
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
    NOFILE_PENDING=0
    NOFILE_FAILED=0
    for unit in "${RUNNER_UNITS[@]}"; do
        cur="$(nofile_of_service "$unit" || echo 0)"
        want="$(configured_nofile "$unit")"
        case "$(nofile_state "$cur" "$want")" in
        ok)
            printf '  \033[32m✓\033[0m %-52s soft=%s\n' "$unit" "$cur"
            continue ;;
        pending)
            printf '  \033[33m→\033[0m %-52s soft=%s，但配置里已是 %s\n' \
                "$unit" "$cur" "$want"
            printf '      只差一次重启，**不写 drop-in**（写了会把这份配置顶掉）。\n'
            NOFILE_PENDING=$((NOFILE_PENDING + 1))
            continue ;;
        esac
        if write_nofile_dropin "/etc/systemd/system/${unit}.d" \
           && verify_nofile_effective "$unit"; then
            printf '  \033[33m→\033[0m %-52s soft=%s，已写 drop-in 并确认生效\n' \
                "$unit" "${cur:-未知}"
            NOFILE_FIXED=$((NOFILE_FIXED + 1))
        else
            # **拒绝写入 / 写了没生效都要传到退出码。** 只打一行红字然后照旧走到
            # 「准备完成」、exit 0 的话，自动化会把这台机器当成配好了，而它的 FD
            # 上限仍在 preflight 阈值之下——每个 lab job 都会被拦下。
            printf '  \033[31m✗\033[0m %-52s soft=%s，drop-in 没写成或没生效\n' \
                "$unit" "${cur:-未知}"
            NOFILE_FAILED=$((NOFILE_FAILED + 1))
        fi
    done
    if [ "$NOFILE_PENDING" -gt 0 ]; then
        warn "$NOFILE_PENDING 个 runner 服务的 LimitNOFILE 已经配好、但还没生效。
    等它们空闲时重启即可（本脚本刻意不替你重启——正在跑的 job 会被打断）。"
    fi
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

if [ "${NOFILE_FAILED:-0}" -gt 0 ]; then
    die "$NOFILE_FAILED 个 runner 服务的文件描述符上限没配好（见上面的 ✗）。
    修好之前**这台机器不能当作已就绪**：lab_preflight 的 FD 检查是硬阻断，
    它会拦下每一个 lab job。"
fi
