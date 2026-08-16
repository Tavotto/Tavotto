#!/bin/sh
# 启动 Magplot 排版工具（自动创建虚拟环境并安装依赖）
cd "$(dirname "$0")" || exit 1
[ -d .venv ] || python3 -m venv .venv
# 源码树以 editable 安装：改 Python 代码即时生效，无需重装。
# 前端走 web/dist（pnpm build）；打包时才由 scripts/build_frontend.py 拷进包内。
.venv/bin/python -c "import magplot" 2>/dev/null || .venv/bin/pip install -e .
exec .venv/bin/magplot "$@"
