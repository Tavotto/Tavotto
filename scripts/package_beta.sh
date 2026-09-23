#!/bin/sh
# beta 分支专用（不合进 main）：把 build_desktop.py 产出的 Tavotto Beta.app
# 重新 adhoc 签名、验收、冒烟，再打成给测试者的 dmg。
#
# 为什么要重签：本机没有 Developer ID 时 Tauri 只给主程序落一个 adhoc 签名，
# 资源没被封进去（codesign: "code has no resources but signature indicates they
# must be present"）。这样的包经微信 / 浏览器传过去带上隔离标记后，macOS 报
# 「已损坏，无法打开」且不给「仍要打开」。自内向外逐个 adhoc 重签之后签名是
# 完整有效的，Gatekeeper 只剩「未公证」这一条，测试者能在系统设置里放行。
#
# 用法：
#   .venv-build/bin/python scripts/build_desktop.py --bundles app   # 只出 .app，dmg 由本脚本打
#   scripts/package_beta.sh
# 别让 Tauri 打 dmg：它按卷名「Tavotto Beta」挂载，本机已经开着一个同名卷
# （测试时双击过的 beta dmg）就会 bundle_dmg.sh 失败。本脚本挂载用临时目录，不撞名。
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv-build/bin/python"
SRC="$ROOT/src-tauri/target/release/bundle/macos/Tavotto Beta.app"
VERSION="$("$PY" -c 'import tavotto;print(tavotto.__version__)')"
OUT="$ROOT/dist-beta"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

[ -d "$SRC" ] || { echo "找不到 $SRC，先跑 build_desktop.py" >&2; exit 1; }

APP="$WORK/stage/Tavotto Beta.app"
mkdir -p "$WORK/stage"
ditto "$SRC" "$APP"

"$PY" "$ROOT/scripts/codesign_macos.py" sign --app "$APP" --identity - \
  --entitlements "$ROOT/packaging/entitlements.plist"
"$PY" "$ROOT/scripts/codesign_macos.py" verify --app "$APP" --expect-arch arm64

# 与 CI 的「最终 .app 冒烟」同一条：中文 + 空格路径、ditto 拷过去再验签名再跑
SMOKE="$WORK/我的 应用 目录"
mkdir -p "$SMOKE"
ditto "$APP" "$SMOKE/Tavotto Beta.app"
codesign --verify --deep --strict "$SMOKE/Tavotto Beta.app"
"$PY" "$ROOT/scripts/smoke_app.py" \
  --exe "$SMOKE/Tavotto Beta.app/Contents/Resources/sidecar/Tavotto/Tavotto" \
  --figures "$ROOT/examples/runtime_check" --workdir "$WORK/冒烟 数据" \
  --expect-source bundled --expect-runtime --expect-control-plane workerd \
  --expect-packages numpy,pandas,scipy,seaborn,PIL,matplotlib

ln -s /Applications "$WORK/stage/Applications"
cp "$ROOT/docs/beta/README-性能测试版.md" "$WORK/stage/先看我-安装说明.md"

rm -rf "$OUT"
mkdir -p "$OUT"
DMG="$OUT/Tavotto-Beta-$VERSION-arm64.dmg"
hdiutil create -volname "Tavotto Beta" -srcfolder "$WORK/stage" -ov -format ULMO "$DMG" >/dev/null
cp "$ROOT/docs/beta/README-性能测试版.md" "$OUT/"
(cd "$OUT" && shasum -a 256 "$(basename "$DMG")" > SHA256SUMS.txt)

# 出货前再从 dmg 里取一次验签：镜像打包不许把签名弄坏
MNT="$(mktemp -d)"
hdiutil attach -nobrowse -readonly -mountpoint "$MNT" "$DMG" >/dev/null
codesign --verify --deep --strict "$MNT/Tavotto Beta.app" && echo "✓ dmg 里的 .app 签名完整"
hdiutil detach "$MNT" >/dev/null
echo "产物：$OUT"
ls -la "$OUT"
