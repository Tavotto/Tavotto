#!/bin/bash
# REL-01 桌面腿（只读）：检查已安装的 Tavotto.app / Tavotto Beta.app 的身份与发布卫生。
# 只读：不启动 app、不打开用户数据目录、不写 /Applications。
# 用法：bash docs/qa/2026-09-24/rel/repro/inspect_desktop_apps.sh
set -u
for app in "/Applications/Tavotto.app" "/Applications/Tavotto Beta.app"; do
  echo "################ $app"
  if [ ! -d "$app" ]; then echo "MISSING"; continue; fi
  plist="$app/Contents/Info.plist"
  for key in CFBundleIdentifier CFBundleShortVersionString CFBundleVersion LSMinimumSystemVersion CFBundleExecutable; do
    printf '%s = ' "$key"; /usr/libexec/PlistBuddy -c "Print :$key" "$plist" 2>&1
  done
  echo "-- Contents/MacOS:"; ls -l "$app/Contents/MacOS"
  exe_name=$(/usr/libexec/PlistBuddy -c "Print :CFBundleExecutable" "$plist" 2>/dev/null)
  echo "-- sha256 of Contents/MacOS/* (main + sidecars):"
  find "$app/Contents/MacOS" -maxdepth 1 -type f -exec shasum -a 256 {} \;
  echo "-- lipo archs of main exe:"; lipo -archs "$app/Contents/MacOS/$exe_name" 2>&1
  echo "-- codesign -dv:"; codesign -dv --verbose=2 "$app" 2>&1 | grep -v '^Executable='
  echo "-- codesign --verify --deep --strict:"; codesign --verify --deep --strict "$app" 2>&1; echo "verify_exit=$?"
  echo "-- spctl --assess --type execute:"; spctl --assess --type execute -vv "$app" 2>&1; echo "spctl_exit=$?"
  echo "-- entitlements (get-task-allow = debug build marker):"
  codesign -d --entitlements - --xml "$app" 2>/dev/null | plutil -p - 2>&1 | head -30
  echo "-- debug/test-build markers in main exe (strings, counts):"
  for pat in "TAVOTTO_TEST_" "__tavotto_test" "devtools" "open_devtools" "tauri-plugin-devtools" "localhost:5173" "http://localhost:1420" "debug_assertions" "RUST_BACKTRACE"; do
    n=$(strings -a "$app/Contents/MacOS/$exe_name" 2>/dev/null | grep -c -F -- "$pat")
    echo "  $pat: $n"
  done
  echo "-- tauri.conf / resources listing (top):"; ls "$app/Contents/Resources" | head -30
  echo "-- bundled runtime present:"; ls -d "$app"/Contents/Resources/*/_internal/runtime/bin/python3* "$app"/Contents/MacOS/*/_internal/runtime/bin/python3* 2>/dev/null | head
done
