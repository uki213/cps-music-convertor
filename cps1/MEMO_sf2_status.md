# CPS-1 SF2 生成 — 進捗メモ（2026-10-27 作成、2026-10-29 更新）

## 🎯 現状（2026-10-29 時点）
- **FM パート: 鳴っている**（keyRange 修正 + ADSR rate 修正の成果）
- **PCM（ドラム）パート: 鳴る**（ユーザー確認済み、リズムも正しい）
- **PCM の音色がオリジナルと合わない**（ユーザー指摘・未解決）
  - ユーザーの要望: ノートオンからノートオフまで、サンプル先頭から
    ワンショット再生（ループなし）— 現行実装はこれで OK
  - 推定原因: MIDI の program → サンプルの割当（音色対応）がずれている
    可能性。以下「PCM 音色ズレ調査（2026-10-29）」を参照
- 一時ファイル 80 本を整理済み（cps1\ 直下は本体・ツール・ドキュメントのみ）

## PCM 音色ズレ調査（2026-10-28 夜〜2026-10-29）
ユーザー報告: 「PCM は鳴るが、PCM 単体（wav）で聞いた音と違う。
RPM リズムは合っている → 音色の割当がずれていると思う」。

### 検証済み（問題なし）
1. **SF2 キット割当**（`verify_kit_map.py`）: 95/95 全てのノート N →
   PCM0NN サンプル → `wav/sf2ce/NNN.wav` とバイト同一。ループなし。
   **SF2 側は正しい。**
2. **コーデック**（`check_codec.py`）: WAV は fmt=1（非圧縮）16bit 7576Hz、
   SF2 smpl とバイト同一。**コーデック不適合ではない。**
3. **ADPCM 復号アルゴリズム**: MAME の okiadpcm.cpp と対比し、
   実質同一（stepval≈16・1.1^step、ニブル gating、常時 +stepval/8、
   clamp ±2048）。**波形生成自体は正しく再現されている。**
4. **サンプルレート**: MAME 設定 1M/132 ≈ 7575.76 Hz と実測 7576 Hz が一致。

### 重要: 「オフバイワン」の疑いは誤りだった（2026-10-29 で解消）
初期に「変換器のサンプルテーブル読み込みが MAME より 1 エントリずれて
おり、全ドラムが隣のサンプルで鳴っている」との仮説を立てたが、
**VGMTrans（既知の正解）の実装と突き合わせて誤りだと判明した。**

- `D:\works\vgmtrans-master\src\main\formats\CPS\CPS1Instr.cpp` L52-53:
  `rgn->sampNum = readByte(instrOff+1) - 1;`
  コメント: *"subtract 1 to account for the first OKIM6295 sample ptr
  always being null"* → **ゲーム ROM の楽器テーブルのバイト値は
  1 始まり**（エントリ 0 が null ）。
- VGMTrans のサンプルテーブルはオフセット 0 から `i = (off-8)/8` で
  読込み（`sampColl` ヘッダ 8, 0x400-8）→ 変換器の `decode_pcm_samples`
  （オフセット 8 開始、`idx = off//8 - 1`）と**完全に一致**。
- したがって変換器の対応（楽器テーブルバイト値 −1 = サンプルインデックス、
  サンプル N = ノート 24+N、wav = NNN.wav）は **VGMTrans と同等で正しい**。
- MAME の sample N（N*8）= 変換器 sample N とは 1 ずれるが、
  MAME の sample 0 は null であり、ゲームが 0 を発行することはない
  （ROM テーブルのバイト値が 1 始まりのため）。実効的には一致。
- 結論: **オフバイワン修正は行わない**（行えば逆にズレが生じる）。

### sf2ce の program → sample 割当（`dump_mapping.py` 出力）
prog 0-6 が sample 0,10,17,11,12,13,14（ゲーム固有の割当）
prog 7 → sample 0、prog 8 → 1、…、prog 18 → 11、prog 19 → 16 …
（以降ほぼ恒等。VGMTrans と同じ −1 方式。）

### 未解決の残課題
1. **MIDI 化時点での program/note がシーケンス本来の PCM 音色と
   一致しているか**（`ch9_prog_note.py` は未実行で削除済み。
   必要なら再作成: `01_Japan (Ryu) I.mid` の ch9 program change /
   note 番号の実効リストを確認し、`dump_mapping.py` の
   program→note 表と突き合わせる）
2. **波形そのものが違う**（音色割当が正しいはずなのにユーザーが
   「違う」と感じる）場合の候補:
   - ADPCM 初期状態（MAME okiadpcm の初期 index/step）の微差
   - volume table の差（MAME `s_volume_table[16]` と変換器の減衰表）
   - ノート長がサンプル長より短い場合のトリム挙動
   - 検証方法: 同一 program の「変換 wav」と「MAME で録音した音」
     を相関比較する
3. 実効的な検証には **MAME の録音（MIDI 化ではなく実際の Z80 シーケンス
   再生）** が正解の基準になる

## 🎯 現状（2026-10-28 深夜、バグ 9/10 特定・修正）
- **FM パート: 鳴っている**（keyRange 修正 + ADSR rate 修正の成果）
- **PCM（ドラム）パート: 鳴るようになった**（preset=0/7 両方にキット登録）
- **PCM の波形がオリジナルと違う**（ユーザー指摘）
  - **ループバグ修正済み**（SHDR loopstart/loopend = end → ワンショット）
  - **pbag genndx バグ修正済み**（1始まり → 0始まり、SGM 準拠）
  - **「波形が違う」の本質原因**: CPS-1 の PCM はドラム 1 打ではなく
    **連続音声ストリーム**（PCM024.wav = 595ms のトーン）で、
    MIDI 化するとノート長が中央値 99ms（95% が PCM より短い）になり、
    各トリガーが PCM の先頭のみを再生するため、元の音と波形が異なる。
    これは **MIDI 化アプローチの根本的な制約**（SF2 のバグではない）。
  - 要: ユザーが再確認

## バグ 9（2026-10-28 特定・修正）— pbag genndx のオフセット
- **症状**: ch9/prog7（実 MIDI が使うキット）がサイレント、ch9/prog0 は鳴る
- **原因**: `load_pbag` は各 zone の generator 数を `this_genndx − prev_genndx`
  で算出。最後に 1 個追加の terminator を読み、**最後の zone** に
  `terminator − last_zone` の generator を与える。
  我々は genndx を `1,2,...,129`、terminator=129 と書いていたため、
  **最後の zone（kit7）が `129−129 = 0` 個の generator** → SILENT。
- **証拠**: SGM（正常）の pbag は `0,1,2,...,1970`、terminator=1974
  → 最後の zone が `1974−1970 = 4` 個の generator を確保。
- **修正**: pbag の genndx を 0 始まり（`idx`）に変更、terminator は
  `preset_zone_count`（129）のまま。これで kit7 zone = 1 gen。
- **再検証**: ch9/prog0 と ch9/prog7 の両方が peak=32768 で鳴る
  （PCM024.wav の最大値と一致）。"Discarding invalid global zone" 警告なし。

## バグ 8（2026-10-28 特定・修正）— SHDR ループ
- **症状**: PCM がループして「波形がノイズ状」
- **原因**: SHDR の loopstart/loopend = 末尾で永久ループ
- **修正**: loopstart = loopend = end（ワンショット）に設定
- **検証**: 全 222 レコード `ls==le==end` を確認

## バグ 7（2026-10-27 特定・修正）— preset 0/7 二重登録
- **症状**: ch9 鳴らない
- **原因**: kit 0 と kit 7 が同じ pbag index を共有し、
  fluidsynth の `load_phdr`（delta ベース zone 割り当て）で preset 0 が
  0 zone、preset 7 が 2 zone（うち 1 つは "invalid global zone" で破棄）
  になる
- **修正**: kit preset 0/7 の bagidx を fm_count / fm_count+1 に分離

## バグ 6（2026-10-27 特定・修正）— FM zone 末尾の keyRange
- 2 つ目の zone に GEN_KEY_RANGE（0,0）を誤って追加していた
- 修正: 2 つ目の zone は GEN_INSTRUMENT のみ

## バグ 5（2026-10-27 特定・修正）— keyRange ビットシフト誤り
- `127<<4` ではなく `127<<8` が必要
- 修正: `(127 << 8) | 0`

## バグ 4（2026-10-27 特定・修正）— FM エンベロープの rate index
- AR/DR/RR/RH の rate index が逆だった
- 修正: make_sf2.py の FM アタック/ディケイ/リリース処理を修正

## 原因の全体像（なぜ最初「全く音が鳴らなかった」か）
「全く音が出ない」は 3 つの独立バグが重なった結果：
1. **構造バグ 4 件**（下の「2026-10-27 追記」）→ ファイルがロードされない/壊れる
2. **keyRange ジェネレータのビットシフト誤り**（`127<<4` ではなく `127<<8` が必要）
   → レージョンが無効になり、どんなノートもサンプルにマッチしなかった（完全サイレントの主因）
3. **FM エンベロープの rate index が逆だった** → 発音（AR）が事実上終わらずサイレント

3 を全部直して初めて音が鳴るようになった。

---

## （過去の進捗・詳細は下記を参照）


## 2026-10-27 追記：GM 参考ファイル（SGM-V2.01.sf2）との構造比較で 4 つの構造バグを特定・修正
ユーザー提供の既知の正解 GM サウンドフォント
（`C:\Users\niimi\OneDrive\Desktop\SGM-V2.01.sf2`）のバイナリと
生成ファイルを比較したところ、**VLC がファイルを拒否する原因は構造バグ**
と判明。SoundFont 2.01 仕様（FluidSynth `gen.h` の generator テーブル、
および参考ファイルの実測値）に照らして次の 4 点を修正した。

1. **トップレベルの `hdrl` チャンクを削除**（最有力の原因）。
   SF2 2.01 には `hdrl` チャンクは存在しない。仕様上のトップレベルは
   `sfbk → LIST INFO → LIST sdta → LIST pdta` のみ。参考ファイルにも `hdrl` なし。
2. **INFO サブチャンク `nm04`（非標準）→ 標準の `ifil / INAM / isng` に変更**。
   `ifil` = SF2 バージョン（major=2, minor=1）。
3. **generator `overridingRootKey` の ID を 59 → 58 に修正**。
   58 = OVERRIDEROOTKEY（int16）。59 は FluidSynth 独自（非標準）の `GEN_PITCH`。
   参考ファイルの `igen` でも 58 は使用されているが 59 は一度も出現しない。
4. **チャンクサイズの 2 バイト（ワード）アラインメント未処理を修正**。
   `isng`（`CPS1\0`、5 バイト＝奇数）で INFO チャンク全体が 43 バイト（奇数）
   となり、以降の `sdta`/`pdta` 配置がずれていた。奇数ペイロードにはパディング
   バイトを 1 つ追加（size フィールド自体は実バイト数を維持）するよう変更。

修正後の生成ファイルの構造は参考ファイルと完全に一致することを確認済み：
```
LIST INFO   [ifil, INAM, isng]
LIST sdta   [smpl]
LIST pdta   [phdr, pmod, pbag, pgen, inst, ibag, imod, igen, shdr]
```
レコードサイズも参考ファイルと一致（phdr=38 / inst=22 / shdr=46 / pbag=4 /
pmod=10 / pgen=4 / ibag=4 / imod=10 / igen=4）。
**generator レコードは 4 バイト（u16 id + i16 value）** であることも確認済み
（参考ファイルの pgen/igen のサイズが 4 で割り切れること、実測 id 値とも整合）。

> 注: これらは「ファイル構造」の修正。FM サンプルのサイレント問題
> （124/127 peak<10）は別件であり、未だに修正されていない。
> 音が出るか否かは VLC/他 host で実際に再生して確認が必要。

## 構成（2026-10-28 最終）
- FM 音色 127（`.opm` パラメータを YM2151 シミュレータで C4 = MIDI 60 基準にレンダリング、
  44.1 kHz、**preset bank 0**、preset number 0-126、programChange 0-126）
- PCM 音色（`wav/<archive>/NNN.wav` を **GM キット = bank 128** の
  **preset 0 と preset 7** に keynum 単位の region（95 個）として登録、
  両 preset が同一の kit instrument 127 を指す。sample はワンショット（非ループ））

### bank / program 設定（GM 準拠）
| チャンネル | bank (CC0/CC32) | program (CC36) | 備考 |
|-----------|-----------------|----------------|------|
| ch0-7 (FM) | 0 / 1 | 0-126 | YM2151 FM（C4 基準） |
| ch9 (PCM) | 128（自動解決） | 0 と 7 両方 | GM キット、preset 0 (Standard Kit) と 7 (Acoustic Kit) に同一 kit instrument を登録 |

### 2026-10-28 追記：3 つの致命的なサイレントバグ（構造バグとは別件）
5. **keyRange ジェネレータ（id 10）のビットシフト誤り**（完全サイレントの主因）
   - 誤: `(127 << 4) | 0` → lo=240, hi=7（不可能な範囲、どのノートもマッチしない）
   - 正: `(127 << 8) | 0` → lo=0, hi=127（全ノート対応）
6. **FM エンベロープ（ADSR）の rate index が逆だった**
   - rate 表（AR/DR/SL）は **index 0 = 最長（101s）… index 15 = 最短（3ms）** の昇順配列
   - OPL の rate value は **15 = 最速、0 = 無効（即時）**
   - 誤: `AR_TIMES_MS[ar]` → AR=13（速い発音）を 25s（遅い）として扱い、発音が 1 秒では終わらずサイレント
   - 正: `AR_TIMES_MS[15 - ar]`、`ar=0 → 即時ゲイン`
   - 修正後、FM サンプルの peak は 30000 台（以前は 124/127 が peak<10）
7. **PCM キットが bank 128 / preset 7 のみ登録だった** → fluidsynth のドラム
   チャンネル（ch9）は **preset 0**（GM Standard Kit）をまず参照するため
   preset 0 にないとサイレント。SGM-V2.01.sf2 は preset 0 にキッドを置いている
   - 修正: PCM キットを **preset 0 と preset 7 の両方**に登録（同じ kit
     instrument 127 を指す 2 つの phdr レコード）
   - 追加: shdr 末尾の terminator レコードを 1 つ追加（fluidsynth は sample
     数を 1 引いて読むため必須）
   - 修正後、ch9 のみテストで peak=32768、full MIDI にもドラムが入る
8. **SHDR の loopstart/loopend が全サンプルで「永久ループ」になっていた**
   - 誤: `loopstart = start`、`loopend = end` → 全サンプル（FM 含む）がループ
   - ワンショットのはずのドラムが先頭（最大音量点）から何度も再生され
     「ノイズ」に聞こえる原因
   - 修正: `loopstart = loopend = end`（ループ領域ゼロ長 → 非ループ）
   - 検証: shdr 全 222 レコードで `ls == le == end` を確認

### 2026-10-27 追記（旧・FM サイレント問題の解決）
> 上記「既知の疑わしい点」の 1〜3 は、2 と 5 が正体（rate index 逆 + keyRange ビット誤り）。
> 3 の「PCM キットの bag 連鎖」は ibag terminator + delta index の修正で解決済み。

## 解決済みだった疑わしい点（履歴）
1. **FM サンプルがほぼサイレント**（124/127 が peak < 10）→ ADSR rate index 修正で解決
2. **pbag 指針が壊れている可能性** → ibag terminator + delta index 修正で解決済み
3. **PCM キットの bag 連鎖** → 1 instrument / 95 bag（keynum 各 1 個）構成は fluidsynth で動作確認済み
4. VLC は SF2 サポートが不安定 → fluidsynth を検証基準に切り替え

## 再現コマンド
```
cd d:\works\cps-music-convertor\cps1
python make_sf2.py games\sf2ce.xml --duration 1.0
# SF2 キット割当の検証（95/95 確認済み）:
python verify_kit_map.py
# program → sample 割当の確認:
python dump_mapping.py
# MAME のサンプルテーブル解釈との比較:
python compare_tables.py
# fluidsynth 再生（ドラム検証用）:
C:\Users\niimi\AppData\Local\Temp\fluidsynth\fluidsynth-v2.6.1-win10-x64-cpp11\bin\fluidsynth.exe -F out.wav -r 44100 voices\sf2ce.sf2 "midi\sf2ce\01_Japan (Ryu) I.mid"
```

## 次の手（2026-10-29 時点・最重要: PCM 音色のズレ）
- [ ] MIDI の ch9 が実際に発行している program change / note の
      実効リストを抽出し、`dump_mapping.py` の program→note 表と突き合わせ、
      音色割当がシーケンス本来の割当と一致しているか確認
- [ ] 割当が正しくても「音色が違う」なら、ADPCM 初期状態・volume table・
      トリム挙動の差を MAME（okiadpcm.cpp / okim6295.cpp）と突合
- [ ] 基準音を MAME で録音（Z80 シーケンス実再生）し、
      変換 wav と相関比較してズレの正体を特定
