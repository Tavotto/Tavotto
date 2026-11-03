#!/usr/bin/env bash
# 新手视角：全新 Ubuntu 24.04，按 README「pip, Linux and servers」+「Using Tavotto in Codex (plugin)」原样走一遍。
# 在一次性容器里以 root 启动；root 只做「新手会 sudo 做的事」，其余全在普通用户 xb 下。
set -u
export DEBIAN_FRONTEND=noninteractive
LOG=/out/steps.tsv
: >"$LOG"
step() { # step <名字> <用户:root|xb> <命令...>
  local name=$1 who=$2; shift 2
  local t0=$(date +%s.%N) rc
  echo "===== [$name] ($who) $*" | tee -a /out/full.log
  if [ "$who" = root ]; then bash -lc "$*" >>/out/full.log 2>&1; rc=$?
  else su - xb -c "export TAVOTTO_NO_TELEMETRY=1 TAVOTTO_NO_UPDATE_CHECK=1 TAVOTTO_NO_DIAG_UPLOAD=1; $*" >>/out/full.log 2>&1; rc=$?; fi
  local dt=$(echo "$(date +%s.%N) - $t0" | bc)
  printf "%s\t%s\t%.1fs\n" "$name" "$rc" "$dt" | tee -a "$LOG"
  return $rc
}
# 必需步骤：失败计入最终退出码（Codex #1682 P2）。只有两处用裸 step：
# pip-direct-pep668（就是要看 24.04 拒绝直接 pip）与 pipx-install-readme（官方源超时后切镜像）。
FAILED=()
need() { step "$@" || FAILED+=("$1"); }

apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq bc sudo curl >/dev/null 2>&1
useradd -m -s /bin/bash xb && echo "xb ALL=(ALL) NOPASSWD:ALL" >/etc/sudoers.d/xb

# 0. 新手常见第一反应：直接 pip（24.04 的 PEP 668 会拒）
need apt-python root "apt-get install -y python3 python3-pip"
step pip-direct-pep668 xb "pip install 'tavotto[worker]' || pip3 install 'tavotto[worker]'"

# 1. README: pipx install "tavotto[worker]"
need apt-pipx root "apt-get install -y pipx"
need pipx-ensurepath xb "pipx ensurepath"
# README 原文，官方 PyPI。大陆到 files.pythonhosted.org（Fastly）实测约 1 MB/min（2026-11-03），
# 给 15 分钟：超时本身就是一条证据（新手照 README 装不上），记下后换国内镜像把后半段走完。
if ! step pipx-install-readme xb "timeout 900 pipx install 'tavotto[worker]'"; then
  need pipx-uninstall-partial xb "pipx uninstall tavotto || true; rm -rf ~/.local/share/pipx/venvs/tavotto"
  need pipx-install-cn-mirror xb "PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ pipx install 'tavotto[worker]'"
fi
need tavotto-on-path xb "command -v tavotto && tavotto doctor"

# 2. 起引擎、拿首页
need serve xb "mkdir -p ~/figs && (nohup tavotto --no-browser --figures ~/figs >~/serve.log 2>&1 &) ; for i in \$(seq 1 90); do curl -fsS -o /dev/null http://127.0.0.1:5089/ && echo up after \$i s && exit 0; sleep 1; done; cat ~/serve.log; exit 1"

# 4. Codex CLI（新手照 OpenAI 文档：npm i -g @openai/codex）
need apt-node root "apt-get install -y nodejs npm git"
need npm-codex root "npm install -g @openai/codex && codex --version"
# 4a. README 原文两行
need readme-marketplace xb "codex plugin marketplace add Tavotto/Tavotto --sparse .agents/plugins"
need readme-plugin-add xb "codex plugin add tavotto@tavotto"
need codex-doctor xb "tavotto codex doctor"
# 4b. 另一条：从干净的 ~/.codex 交给 tavotto codex install
need reset-codex xb "rm -rf ~/.codex"
need tavotto-codex-install xb "tavotto codex install"
need codex-doctor-2 xb "tavotto codex doctor"

# 5. 插件的 MCP stdio 握手 + 列工具（不调模型）
cat >/home/xb/mcp_probe.py <<'PY'
import json, subprocess, sys
from pathlib import Path
# 照插件 codex.mcp.json：cwd = 插件根，command ./mcp/launch，args ./mcp/server.py（pipx 装的引擎没有内置 MCP 服务端）
roots = sorted((Path.home() / ".codex/plugins/cache/tavotto/tavotto").glob("*/mcp/server.py"))
if not roots: raise SystemExit("plugin not installed: no mcp/server.py under ~/.codex/plugins/cache")
root = roots[-1].parent.parent
print("plugin root:", root)
p = subprocess.Popen(["./mcp/launch", "./mcp/server.py"], cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr, text=True)
def rpc(i, method, params=None):
    msg = {"jsonrpc": "2.0", "method": method, **({"id": i} if i is not None else {}), "params": params or {}}
    p.stdin.write(json.dumps(msg) + "\n"); p.stdin.flush()
    if i is None: return None
    while True:
        line = p.stdout.readline()
        if not line: raise SystemExit("mcp closed")
        r = json.loads(line)
        if r.get("id") == i: return r
r = rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "u2404-probe", "version": "0"}})
print("server:", r.get("result", {}).get("serverInfo"))
rpc(None, "notifications/initialized")
t = rpc(2, "tools/list")
names = [x["name"] for x in t.get("result", {}).get("tools", [])]
print(len(names), "tools:", ", ".join(names))
p.terminate()
sys.exit(0 if names else 1)
PY
chown xb /home/xb/mcp_probe.py
need mcp-tools xb "timeout 120 /usr/bin/python3 ~/mcp_probe.py"

if [ ${#FAILED[@]} -gt 0 ]; then
  echo "FAILED: ${FAILED[*]}" | tee -a /out/full.log
  exit 1
fi
echo DONE | tee -a /out/full.log
