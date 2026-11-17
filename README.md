# HEXA SIGN — CardputerZero から EZ Sign を書き換える

公式アプリを使わずに、CardputerZero と Sony RC-S380 で **EZ Sign NFCモデル 4.2インチ (4色)** を書き換えるアプリ。
プロトコルは [alt-core/nfc-eink](https://github.com/alt-core/nfc-eink) を使い、その上に CardputerZero の画面とキーボードで操作できる画面を載せている。

## はじめる

```sh
# CardputerZero で
git clone https://github.com/tsuchicco0113-png/HEXA-SIGN.git ~/hexa-sign
cd ~/hexa-sign && sh install.sh      # sudo は付けない
sudo reboot                          # 初回だけ (権限とアイコンの反映)
```

`install.sh` の内容:
- `~/nfc` の venv に nfc-eink / nfcpy / qrcode を導入 (既存の venv はそのまま使う)
- RC-S380 の udev ルール (`054c:06c1` / `06c3`、group `plugdev`) → **sudo なしで動く**
- `port100` カーネルドライバを無効化 (入っている場合。nfcpy がリーダーを開けなくなるため)
- `video` / `input` / `plugdev` グループに追加、ホーム画面に HEXA SIGN のアイコン

リーダーなしで画面の流れだけ試すには `hexasign --fake`。

## 使い方 (画面)

ホームの六角形を選んで ENTER (1〜4 キーで直接):

| | 画面 | 内容 |
|---|---|---|
| 1 | テンプレ | 営業プレート / 本日の豆 / 名札 / お知らせ / QRコード / フリーテキスト |
| 2 | 画像 | `~/clip` (HEXA CLIP の 📎 で送ったファイル) / `~/Pictures` / `~/hexasign-images` / USBメモリ |
| 3 | クリップ | HEXA CLIP で受け取った文章をそのまま掲示 |
| 4 | 履歴 | 送った絵をもう一度送る |

- テンプレの項目: **←→ で候補から選ぶ** / ENTER で打ち込む / **P で HEXA CLIP の最新を貼る** / S で確認へ
- 本体キーボードでは日本語を打てないので、日本語は「候補から選ぶ」か「スマホで打って HEXA CLIP で送る → P」
- 送る前に 4色にした絵を本体で確認 (色の割合も表示)
- 画像は確認画面で変換を調整できる (↑↓ で項目、←→ で変更。変えると数秒で描き直す)

  | 項目 | 選べるもの |
  |---|---|
  | 色 | 4色 (黒白黄赤) / 単色 (黒白) |
  | ディザ | なし / Floyd-Steinberg / Atkinson |
  | 強さ | 10〜100% (誤差を周りに広げる割合。下げるとざらつきが減り、平たい絵に近づく) |
  | 配置 | 切り抜き (画面いっぱい) / 全体 (余白は白) |
  | 補正 | 実物寄せ (実際のパネルの発色に合わせて明るさを圧縮) / なし |
  | 回転 | 0 / 90 / 180 / 270° |

  V で等倍表示 (方向キーで移動) にすると、ディザの粒の細かさを確認できる。
  JPG は写真向け (Atkinson・切り抜き・補正あり)、PNG はイラスト向け (ディザなし・全体) で始まり、送った設定は次回の初期値になる
- ENTER → EZ Sign をかざす → ブロック送信 (15) → 書き換え (数十秒) の間は **動かさない**
- 完了画面で ENTER → **同じ絵を次の札へ** (何枚も同じ内容にするとき)
- 失敗したら置き直して ENTER (最初から送り直す。表示はいつでも上書きできる)
- 方向キーは F(↑) X(↓) Z(←) C(→)、ESC で戻る (HEXA CLIP と同じ流儀)

候補は `~/.config/hexasign/presets.json` で足せる (`presets.example.json` 参照)。

## 使い方 (コマンド / SSH・cron 用)

```sh
hexasign list                                   # テンプレートと項目の一覧
hexasign tpl status status=準備中 sub="14:00 再開"
hexasign tpl bean name="ケニア AA" roast=中深煎り price=¥600
hexasign send ~/clip/poster.png --mode illust   # まとめ設定: photo / illust / dither / mono
hexasign send photo.jpg --colors mono --dither atkinson --strength 70   # 個別に指定
#   --colors 4|mono  --dither none|floyd-steinberg|atkinson  --strength 10-100
#   --fit cover|fit  --tune / --no-tune  --rotate 1 (90°)
hexasign render qr url=https://example.com -o /tmp/a.png   # 送らずに絵だけ作る
```

コマンドでもリーダーにカードが乗るまで最大60秒待つ。台座に置いたままの札なら cron で時間ごとに切り替えられる。

## ファイル

| ファイル | 役割 |
|---|---|
| `hexasign.py` | 画面アプリ + コマンド |
| `sign_render.py` | テンプレート描画・4色化 (NFC に触らない。PCでもテスト可) |
| `sign_nfc.py` | 送信 (待ち → 認証 → 15ブロック送信 → 書き換え待ち)。nfc-eink の上で進み具合と中断に対応 |
| `hexasign` | 起動スクリプト (`~/nfc/bin/python` で動かす、ランチャーの停止/再開) |
| `tests/test_real_path.py` | 実機の 00D1 応答 (シリアル・UID はダミー)を返す模擬タグで送信処理を通すテスト |

PC で画面を確認: `python hexasign.py --fake --sim /tmp/shots --script "ENTER wait:0.5 shot:tpl ESC"`

## 注意

- 認証は固定PIN (`20 09 12 10`) だけ。ほかの値は送らない (ロックアウトの有無が未確認)
- MIFARE Classic 面 / NDEF 面には触らない
- 確認済みは 4.2インチ 4色版のみ。2色版・7.5インチ版は nfc-eink の変換に任せているが未検証
- 日本語フォントは本体の AlibabaPuHuiTi (字が欠けていれば Noto CJK)。HEXA CLIP の install.sh で入る
