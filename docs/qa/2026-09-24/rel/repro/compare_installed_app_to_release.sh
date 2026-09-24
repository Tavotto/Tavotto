#!/bin/bash
# REL-01/REL-03 桌面身份绑定（只读）：已安装的 /Applications/Tavotto.app 与发布资产 Tavotto.app.tar.gz
# （v0.16.0，SHA256SUMS 里的 af900af2…）逐文件对比；原样解包的那份再验一次签名。
# 用法：bash compare_installed_app_to_release.sh <Tavotto.app.tar.gz> <解包目录>
set -u
TARBALL="$1"; OUT="$2"
echo "tarball sha256: $(shasum -a 256 "$TARBALL" | cut -d' ' -f1)"
rm -rf "$OUT"; mkdir -p "$OUT"
tar -xzf "$TARBALL" -C "$OUT"
REL="$OUT/Tavotto.app"; INS="/Applications/Tavotto.app"
echo "release main exe:   $(shasum -a 256 "$REL/Contents/MacOS/Tavotto" | cut -d' ' -f1)"
echo "installed main exe: $(shasum -a 256 "$INS/Contents/MacOS/Tavotto" | cut -d' ' -f1)"
echo "-- pristine release app: codesign --verify --deep --strict"
codesign --verify --deep --strict "$REL" 2>&1; echo "pristine_verify_exit=$?"
echo "-- __pycache__ dirs under tavotto/engine in release vs installed"
echo "release:   $(find "$REL/Contents/Resources/sidecar/Tavotto/_internal/tavotto/engine" -name '__pycache__' -type d | wc -l | tr -d ' ')"
echo "installed: $(find "$INS/Contents/Resources/sidecar/Tavotto/_internal/tavotto/engine" -name '__pycache__' -type d | wc -l | tr -d ' ')"
echo "-- file-set diff (installed minus release), relative paths"
( cd "$REL" && find . -type f | sort ) > "$OUT/rel.lst"
( cd "$INS" && find . -type f | sort ) > "$OUT/ins.lst"
comm -13 "$OUT/rel.lst" "$OUT/ins.lst"
echo "-- files in release missing from installed"
comm -23 "$OUT/rel.lst" "$OUT/ins.lst"
echo "-- content diffs among common files (sha256 mismatch)"
comm -12 "$OUT/rel.lst" "$OUT/ins.lst" | while read -r f; do
  a=$(shasum -a 256 "$REL/$f" | cut -d' ' -f1); b=$(shasum -a 256 "$INS/$f" | cut -d' ' -f1)
  [ "$a" != "$b" ] && echo "DIFF $f"
done
echo "done"
