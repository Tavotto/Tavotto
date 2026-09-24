#!/bin/sh
# 启动 Tavotto 排版工具（自动创建虚拟环境并安装依赖）
cd "$(dirname "$0")" || exit 1
[ -d .venv ] || python3 -m venv .venv
# 源码树以 editable 安装：改 Python 代码即时生效，无需重装。
# 前端走 web/dist（pnpm build）；打包时才由 scripts/build_frontend.py 拷进包内。
.venv/bin/python -c "import tavotto" 2>/dev/null || .venv/bin/pip install -e .
# 批准字体不进 git（ADR 0072）：RenderCore 是默认后端，缺了它文字与导出都不可用。
# 已齐（sha256 与 allowlist 一致）就什么都不做；缺就按 allowlist 取，取不到直接停下，不带病启动。
.venv/bin/python scripts/fetch_fonts.py --check >/dev/null 2>&1 || .venv/bin/python scripts/fetch_fonts.py || exit 1
exec .venv/bin/tavotto "$@"
