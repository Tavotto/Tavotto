#!/bin/sh
# 把 Apple 发的 Developer ID 证书打成 CI 用的 .p12，并写进 GitHub Secrets。
#
# 前置（只有你本人能做）：
#   1. scripts/ 里的 CSR 已生成（见下方 KEY 路径同目录的 developerID.csr）
#   2. 在 https://developer.apple.com/account/resources/certificates/add
#      选 "Developer ID Application"、上传该 CSR、下载 .cer 放进同一目录
#
# 用法： scripts/setup_macos_signing.sh [证书目录]（缺省 ~/tavotto-signing）
#
# 私钥与 .p12 只留在这个目录里，绝不进版本库。
set -eu

DIR="${1:-$HOME/tavotto-signing}"
KEY="$DIR/developerID.key"
REPO="Tavotto/Tavotto"

[ -f "$KEY" ] || { echo "找不到私钥 $KEY" >&2; exit 1; }

CER=$(find "$DIR" -maxdepth 1 -name "*.cer" | head -1)
[ -n "$CER" ] || {
  echo "在 $DIR 里没找到 .cer——" >&2
  echo "请先到 developer.apple.com 用 $DIR/developerID.csr 申请" >&2
  echo "「Developer ID Application」证书，下载后放进该目录。" >&2
  exit 1
}
echo "证书: $CER"

# 证书本身（Apple 发的是 DER）
openssl x509 -inform DER -in "$CER" -out "$DIR/cert.pem"

SUBJECT=$(openssl x509 -in "$DIR/cert.pem" -noout -subject)
case "$SUBJECT" in
  *"Developer ID Application"*) ;;
  *) echo "这不是 Developer ID Application 证书：$SUBJECT" >&2
     echo "（Apple Development 证书不能用于对外分发与公证）" >&2
     exit 1 ;;
esac

# 带上 Apple 的中间 CA：签名链不完整时 codesign 会在别的机器上验不过
: > "$DIR/chain.pem"
for u in DeveloperIDG2CA.cer DeveloperIDCA.cer; do
  if curl -fsSL -o "$DIR/_ca.cer" "https://www.apple.com/certificateauthority/$u" &&
     openssl x509 -inform DER -in "$DIR/_ca.cer" -out "$DIR/_ca.pem" 2>/dev/null; then
    cat "$DIR/_ca.pem" >> "$DIR/chain.pem"
    echo "中间 CA: $u"
  fi
done
rm -f "$DIR/_ca.cer" "$DIR/_ca.pem"

P12_PASS=$(openssl rand -base64 24 | tr -d '\n/+=' | cut -c1-24)
openssl pkcs12 -export \
  -inkey "$KEY" -in "$DIR/cert.pem" -certfile "$DIR/chain.pem" \
  -out "$DIR/developerID.p12" -passout "pass:$P12_PASS"
chmod 600 "$DIR/developerID.p12"

# 签名身份的准确写法：codesign --sign 要的就是这个 CN
IDENTITY=$(openssl x509 -in "$DIR/cert.pem" -noout -subject |
           sed -n 's/.*CN *= *\([^,/]*\).*/\1/p')
[ -n "$IDENTITY" ] || { echo "解析不出签名身份" >&2; exit 1; }
echo "签名身份: $IDENTITY"

base64 -i "$DIR/developerID.p12" | gh secret set MACOS_CERTIFICATE --repo "$REPO"
printf '%s' "$P12_PASS"  | gh secret set MACOS_CERTIFICATE_PASSWORD --repo "$REPO"
printf '%s' "$IDENTITY"  | gh secret set MACOS_SIGN_IDENTITY --repo "$REPO"

echo
echo "✓ 已写入 MACOS_CERTIFICATE / MACOS_CERTIFICATE_PASSWORD / MACOS_SIGN_IDENTITY"
echo
echo "还差一个（需要你在 https://appleid.apple.com 生成 App 专用密码）："
echo "  printf '<App 专用密码>' | gh secret set APPLE_APP_PASSWORD --repo $REPO"
echo
echo "都齐了之后跑： gh workflow run 'Desktop apps' --repo $REPO -f tag=<tag>"
