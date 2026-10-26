# CPS-1 SF2 生成 — 進捗メモ（2026-10-27）

## 現状
`cps1/make_sf2.py` で cps1 の MIDI 用 SF2 を生成できるが、
**生成した SF2 は VLC で使用できなかった**（ユーザー確認済み、2026-10-27）。

## 構成
- FM 音色 127（`.opm` パラメータを YM2151 シミュレータで C4 = MIDI 60 基準にレンダリング、
  44.1 kHz、preset bank 0/LSB 1、preset number 0-126）
- PCM 音色（`wav/<archive>/NNN.wav` を GM 打楽器 preset 8 / bank 0 に
  keynum 単位の region として登録）

## 既知の疑わしい点（VLC 非動作の有力候補）
1. **FM サンプルがほぼサイレント**（検証ツールで 124/127 が peak < 10）。
   エンベロープ/ゲイン計算にバグがあり、音が出ない/ほぼ出ない。
   2026-10-27 時点の `_operator_gain` は未修正のまま VLC 検証済み。
2. **`pbag` 指針が壊れている可能性**: 現在は pbag の各エントリが
   generatorIndex = modIndex = preset 自身（各 preset が1ジェネレータ持つ前提）。
   pgen は 1 エントリ/preset なので整合しているはずだが要確認。
3. **PCM キットの bag 連鎖**: 1 instrument が複数 bag を持ち（ibag の
   連続範囲で判定）、各 bag が sampleID 1 つずつ。
   host が「1 instrument = 1 bag」想定の場合、VLC が最後まで追えない可能性。
   keyswitch ジェネレータ（45）方式への置き換え候補。
4. VLC は SF2 サポートが不安定で、**GM 互換のサンプル GM soundfont と比較
   テストした方がよい**。VLC 単体の問題か、ファイル自体の問題か切り分け未実施。

## 再現コマンド
```
cd d:\works\cps-music-convertor\cps1
python make_sf2.py games\sf2ce.xml --duration 1.0
# 検証:
python <session>/files/verify_sf2.py voices\sf2ce.sf2
```

## 次の手
- [ ] `_operator_gain` を OPL/YM2151 仕様に照らして再検証し、サイレント問題の解消
- [ ] 標準の SF2 生成ライブラリ（sf2tools / sf2pcap）で同じ内容を出力し、
      自前ライタとの差分で構造バグを切り分け
- [ ] 別 host（VSLib / soundfont viewer /Audacity など）で動作確認
