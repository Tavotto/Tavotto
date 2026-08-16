#!/bin/sh
# 把 dist/Magplot.app 打成可拖拽安装的 .dmg。
#
# 用 hdiutil（macOS 自带）而不是 create-dmg 之类的第三方工具：CI 上少一个依赖，
# 而且这里只需要「一个 .app + 一个 Applications 快捷方式」这种最朴素的版式。
#
# 用法： scripts/make_dmg.sh <dist 目录> <输出 dmg 路径>
set -eu

DIST="${1:?用法: make_dmg.sh <dist 目录> <输出 dmg>}"
OUT="${2:?用法: make_dmg.sh <dist 目录> <输出 dmg>}"
APP="$DIST/Magplot.app"

[ -d "$APP" ] || { echo "找不到 $APP" >&2; exit 1; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"   # 拖拽安装的目标

rm -f "$OUT"
hdiutil create \
  -volname "Magplot" \
  -srcfolder "$STAGE" \
  -ov -format UDZO \
  "$OUT" >/dev/null

echo "✓ $OUT  $(du -h "$OUT" | cut -f1)"
