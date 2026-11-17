#!/bin/sh
# HEXA SIGN installer for CardputerZero
# 使い方: cd ~/hexa-sign && sh install.sh   (sudo は付けずに実行)
set -e
DIR=$(cd "$(dirname "$0")" && pwd)
U=$(id -un)
if [ "$U" = "root" ]; then
  echo "sudo を付けずに実行してください (sh install.sh)"; exit 1
fi
APPL=/usr/share/APPLaunch
VENV="$HOME/nfc"
NEED_REBOOT=0

echo "== Python 環境 ($VENV)"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install -q "nfc-eink[cli] @ git+https://github.com/alt-core/nfc-eink.git" qrcode
"$VENV/bin/python" -c "import nfc_eink, nfc, qrcode, PIL, numpy; print('   nfc-eink / nfcpy / qrcode OK')"

echo "== RC-S380 を sudo なしで使う (udev)"
sudo tee /etc/udev/rules.d/60-hexasign-rcs380.rules >/dev/null <<'EOF'
# Sony RC-S380/S (06c1) / RC-S380/P (06c3) — nfcpy から使う
SUBSYSTEM=="usb", ATTRS{idVendor}=="054c", ATTRS{idProduct}=="06c1", GROUP="plugdev", MODE="0660"
SUBSYSTEM=="usb", ATTRS{idVendor}=="054c", ATTRS{idProduct}=="06c3", GROUP="plugdev", MODE="0660"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=054c || true
# カーネルの port100 ドライバがリーダーを掴むと nfcpy から開けない
if modinfo port100 >/dev/null 2>&1; then
  echo "blacklist port100" | sudo tee /etc/modprobe.d/hexasign-blacklist-port100.conf >/dev/null
  sudo modprobe -r port100 2>/dev/null || true
fi

echo "== 権限 (画面・キーボード・リーダー)"
for g in video input plugdev; do
  if ! id -nG "$U" | tr ' ' '\n' | grep -qx "$g"; then
    sudo usermod -aG "$g" "$U"
    NEED_REBOOT=1
  fi
done

echo "== コマンド"
chmod +x "$DIR/hexasign" "$DIR/hexasign.py"
sudo ln -sf "$DIR/hexasign" /usr/local/bin/hexasign
mkdir -p "$HOME/hexasign-images" "$HOME/.config/hexasign"
[ -f "$HOME/.config/hexasign/presets.json" ] || cp "$DIR/presets.example.json" "$HOME/.config/hexasign/presets.json"

if [ -d "$APPL/applications" ]; then
  echo "== ランチャー (APPLaunch)"
  REF="$APPL/share/images/camera1.png"
  [ -f "$REF" ] || REF=64
  "$VENV/bin/python" "$DIR/make_icon.py" /tmp/hexasign.png "$REF"
  sudo install -m 644 /tmp/hexasign.png "$APPL/share/images/hexasign.png"
  sudo tee "$APPL/applications/hexasign.desktop" >/dev/null <<EOF
[Desktop Entry]
Type=Application
Name=HEXA SIGN
Comment=EZ Sign (NFC e-paper) writer
Exec=/usr/local/bin/hexasign
Icon=share/images/hexasign.png
Terminal=false
Categories=Utility;
StartupNotify=false
EOF
  NEED_REBOOT=1
fi

echo "== 確認"
"$VENV/bin/python" "$DIR/hexasign.py" render status -o /tmp/hexasign-test.png >/dev/null && echo "   描画 OK (/tmp/hexasign-test.png)"
if lsusb 2>/dev/null | grep -qi "054c:06c[13]"; then
  echo "   RC-S380 を検出"
else
  echo "   ※ RC-S380 が見つかりません (USB に挿してから使ってください)"
fi
echo "== done"
echo "リーダーなしで画面の流れを試す: hexasign --fake"
if [ "$NEED_REBOOT" = 1 ]; then
  echo ""
  echo "*** アイコン追加と権限の反映のため、再起動してください: sudo reboot"
fi
