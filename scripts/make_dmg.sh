#!/bin/sh
# 把 dist/Magplot.app 打成带品牌安装界面的 .dmg。
#
# 仍只用 macOS 自带工具（hdiutil / osascript / SetFile），CI 上零依赖：
#   1. 暂存目录：.app + Applications 快捷方式 + .background/ 背景图 + 卷图标
#   2. 先建可写 UDRW 镜像并挂载，用 Finder 脚本摆好窗口（背景 / 图标位置 /
#      去工具栏），版式落进 .DS_Store
#   3. 转成压缩只读 UDZO 出货
# 背景图是提交在仓库里的 assets/brand/dmg-background.png（重出跑
# scripts/build_dmg_background.py），图标落点与那张图上的箭头严格同源。
# Finder 脚本失败（无窗口会话等）只降级成朴素版式，绝不让发布链路挂掉。
#
# 用法： scripts/make_dmg.sh <dist 目录> <输出 dmg 路径>
set -eu

DIST="${1:?用法: make_dmg.sh <dist 目录> <输出 dmg>}"
OUT="${2:?用法: make_dmg.sh <dist 目录> <输出 dmg>}"
APP="$DIST/Magplot.app"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BG="$ROOT/assets/brand/dmg-background.png"
ICNS="$ROOT/assets/icon/icon.icns"
VOL="Magplot"

[ -d "$APP" ] || { echo "找不到 $APP" >&2; exit 1; }

STAGE="$(mktemp -d)"
RW="$(mktemp -d)/magplot-rw.dmg"
trap 'rm -rf "$STAGE" "$(dirname "$RW")"' EXIT

cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"   # 拖拽安装的目标
if [ -f "$BG" ]; then
  mkdir "$STAGE/.background"
  cp "$BG" "$STAGE/.background/background.png"
fi

# 可写镜像：留出余量给 .DS_Store
SIZE_KB=$(du -sk "$STAGE" | cut -f1)
hdiutil create -volname "$VOL" -srcfolder "$STAGE" -ov -format UDRW \
  -size "$((SIZE_KB / 1024 + 64))m" "$RW" >/dev/null

MOUNT="/Volumes/$VOL"
# 上一次异常退出可能残留同名卷
[ -d "$MOUNT" ] && hdiutil detach "$MOUNT" -quiet || true
hdiutil attach "$RW" -noautoopen >/dev/null

# Finder 摆版式：窗口 660×400、图标 128、背景图、App 与 Applications 左右对位
# （坐标与 build_dmg_background.py 里的箭头同源：165,190 → 495,190）。
# Finder 认识新卷需要一拍，偶发 "disk not found"——重试而不是一次定生死。
layout_ok=0
for i in 1 2 3; do
  sleep "$i"
  if osascript <<APPLESCRIPT
tell application "Finder"
  tell disk "$VOL"
    open
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set the bounds of container window to {200, 120, 860, 548}
    set opts to the icon view options of container window
    set icon size of opts to 128
    set text size of opts to 13
    set arrangement of opts to not arranged
    set background picture of opts to file ".background:background.png"
    set position of item "Magplot.app" of container window to {165, 190}
    set position of item "Applications" of container window to {495, 190}
    close
    open
    update without registering applications
    delay 1
    close
  end tell
end tell
APPLESCRIPT
  then layout_ok=1; break; fi
  echo "· Finder 版式第 $i 次尝试失败，重试…" >&2
done
[ "$layout_ok" = 1 ] || echo "⚠ Finder 版式设置失败，dmg 将使用朴素版式（不影响安装）" >&2

# 卷图标放在 Finder 版式**之后**：hdiutil -srcfolder 阶段与 Finder 的窗口
# update 都会把它吞掉（实测），最后落位 + Custom icon 属性位才留得住。
# SetFile 缺席（无 Xcode CLT）就跳过，只影响卷图标不影响安装。
if [ -f "$ICNS" ]; then
  cp "$ICNS" "$MOUNT/.VolumeIcon.icns" || true
  command -v SetFile >/dev/null 2>&1 && SetFile -a C "$MOUNT" || true
fi
sync
hdiutil detach "$MOUNT" -quiet || { sleep 2; hdiutil detach "$MOUNT" -force -quiet; }

rm -f "$OUT"
hdiutil convert "$RW" -format UDZO -imagekey zlib-level=9 -o "$OUT" >/dev/null

echo "✓ $OUT  $(du -h "$OUT" | cut -f1)"
