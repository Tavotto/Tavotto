#!/usr/bin/env bash
# 用法：bash docs/qa/2026-09-24/sci/repro/vitest.sh <web/ 下的测试文件...>
# 与 web/package.json 的 test 脚本同一个 NODE_OPTIONS（缺它 localStorage 是 undefined）。
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/../../../../.." && pwd)"
cd "$ROOT/web"
NODE_OPTIONS=--no-experimental-webstorage exec pnpm exec vitest run "$@"
