# CPS-1 SF2 生成 — 進捗メモ（2026-10-27 作成、2026-10-28 更新）

## 🚧 現状（2026-10-28 夜、ユーザー確認済み）
- **FM パート: 鳴っている**（keyRange 修正 + ADSR rate 修正の成果）
- **PCM（ドラム）パート: まだ鳴っていない** ← 未解決、次の作業対象
  - MIDI 側は ch9（トラック9）にノート・プログラムチェンジ(7)が含まれることは確認済み
  - SF2 側は PCM キットを **bank 128 / preset 7 (GM Acoustic Kit)** に
    keynum 単位の 95 region として登録済み
  - fluidsynth レンダリングでは全秒 peak=32768 になる（FM の音は混ざっているが、
    ドラムが実際に鳴っているかまで聴覚で確認できていない）
  - 疑い: ch9 のノート番号 ↔ region keynum の紐付け、PCM 用 igen の
    keyRange / overrideRootKey / sample 指定、program change のタイミング
    （`No preset found on channel 9 [bank=128 prog=0]` 警告が program change
    前に一度出るのを確認済み — これは無害のはずだが要検証）
  - 切り分け手順の候補: ch9 のノートだけを抜いた最小 MIDI を作って fluidsynth
    で単独再生 → PCM region のみで鳴るかを 1 音ずつ確認

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
- PCM 音色（`wav/<archive>/NNN.wav` を **GM Acoustic Kit = bank 128 / preset 7** に
  keynum 単位の region（222 個）として登録）

### bank / program 設定（GM 準拠）
| チャンネル | bank (CC0/CC32) | program (CC36) | 備考 |
|-----------|-----------------|----------------|------|
| ch0-7 (FM) | 0 / 1 | 0-126 | YM2151 FM（C4 基準） |
| ch9 (PCM) | 0 / 0 | 7 | GM Acoustic Kit、全ノート番号対応 |

### 2026-10-28 追記：2 つの致命的なサイレントバグ（構造バグとは別件）
5. **keyRange ジェネレータ（id 10）のビットシフト誤り**（完全サイレントの主因）
   - 誤: `(127 << 4) | 0` → lo=240, hi=7（不可能な範囲、どのノートもマッチしない）
   - 正: `(127 << 8) | 0` → lo=0, hi=127（全ノート対応）
6. **FM エンベロープ（ADSR）の rate index が逆だった**
   - rate 表（AR/DR/SL）は **index 0 = 最長（101s）… index 15 = 最短（3ms）** の昇順配列
   - OPL の rate value は **15 = 最速、0 = 無効（即時）**
   - 誤: `AR_TIMES_MS[ar]` → AR=13（速い発音）を 25s（遅い）として扱い、発音が 1 秒では終わらずサイレント
   - 正: `AR_TIMES_MS[15 - ar]`、`ar=0 → 即時ゲイン`
   - 修正後、FM サンプルの peak は 30000 台（以前は 124/127 が peak<10）

### 2026-10-27 追記（旧・FM サイレント問題の解決）
> 上記「既知の疑わしい点」の 1〜3 は、2 と 5 が正体（rate index 逆 + keyRange ビット誤り）。
> 3 の「PCM キットの bag 連鎖」は ibag terminator + delta index の修正で解決済み。

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
- [ ] **PCM（ドラム）パートが鳴らない問題の解消**（最重要・未解決）
  - ch9 のノート番号 ↔ region keynum の紐付けを MIDI の実際の内容と照合
    （`midi_stats.py` / `midi_notes.py` で ch9 のノート番号一覧を確認済みのはず）
  - PCM 用 igen の keyRange / overrideRootKey / sampleID が正しく設定されているか
    SF2 バイナリ側をダンプして検証（`dump_inst.py` 等）
  - 最小 MIDI（ch9 のみ、program change 7 直後のノート 1 音）を作成し
    fluidsynth で単独再生して切り分け
  - SGM-V2.01.sf2 の PCM region（bank 128/preset 7）の igen 設定と比較
- [ ] FM サイレント問題は解決済み（ADSR rate 修正 + keyRange 修正で
      124/127 の peak<10 が解消、ユーザーが FM 鳴りを確認）
- [ ] 構造検証ツール `files/verify_sf2.py` は旧式（`hdrl` 前提）のため
      参考ファイル比較用 `files/verify_final.py` を使って構造確認
