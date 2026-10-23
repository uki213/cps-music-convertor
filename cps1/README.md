# CPS-1 サウンドROMからMIDIへの変換

ゲーム情報XMLに記載された曲コードとROMを読み込み、Standard MIDI Format 0の
ファイルを生成します。現在の変換器は、CPS-1 V4.25形式のシーケンスを対象とします。

- ROMファイル: `roms/<archive>/`
- ゲーム情報XML: `games/`
- 生成するMIDIファイル: `midi/<archive>/`
- YM2151音色ライブラリ: `voices/<archive>.vm5`
- MUCOM形式の音色定義テキスト: `voices/<archive>.muc`
- YM2151 OPM形式の音色定義テキスト: `voices/<archive>.opm`

## 使い方

ワークスペースのルートフォルダーから実行します。

```powershell
python .\cps1\convert_to_midi.py .\cps1\games\sf2ce.xml
```

出力先を変更する場合は `--output-dir` を指定します。

```powershell
python .\cps1\convert_to_midi.py .\cps1\games\sf2ce.xml --output-dir .\exports\sf2ce
```

YM2151音色はMA-5用VM5ライブラリとして `cps1/voices/sf2ce.vm5` にも出力されます。
別の場所へ書き出す場合は `--vm5-output` を指定します。

```powershell
python .\cps1\convert_to_midi.py .\cps1\games\sf2ce.xml --vm5-output .\exports\sf2ce.vm5
```

MUCOM形式の音色定義テキストも `cps1/voices/sf2ce.muc` に出力されます。
出力先を変更する場合は `--muc-output` を指定します。

```powershell
python .\cps1\convert_to_midi.py .\cps1\games\sf2ce.xml --muc-output .\exports\sf2ce.muc
```

YM2151向けのOPM形式テキストも `cps1/voices/sf2ce.opm` に出力されます。
別の場所へ書き出す場合は `--opm-output` を指定します。

```powershell
python .\cps1\convert_to_midi.py .\cps1\games\sf2ce.xml --opm-output .\exports\sf2ce.opm
```

## XMLとROM

XMLの `<romlist>` には `archive`、`format-version`、`tables` 属性を指定します。
`tables` はサウンドプログラムROM内のシーケンス情報テーブルの位置です。
コードROMは `roms/<archive>/` から読み込み、`offset` で指定した位置へ配置します。
曲名と曲コードは `<titlelist>` から取得します。

## 変換内容と制限

現在はSF2CEのCPS-1 V4.25 YM2151/MSM6295ドライバーを対象にしています。分解能は
48 PPQN、ノートオンのベロシティは127です。出力MIDIは単一トラックのSMFフォーマット0で、
元のトラックごとのMIDIチャンネルとイベントを保持します。8本のYM2151トラックと最大4本の
MSM6295トラックをMIDIへ変換します。複数のMSM6295トラックは統合して1本のPCMトラックに
まとめ、MIDIチャンネル10で出力します。テンポ、音量、パン、プログラム変更、移調、
ピッチベンド、タイによる音程変化を出力します。ループは有限ループを展開し、
無限ループは1周分を出力します。
元のPCMトラックは重なる場合があり、実データでは最大4音の同時発音を確認しています。
その重なりを保つため、統合後のPCMトラックも必要に応じてポリフォニックになります。

SMFフォーマット0の先頭にGM System On SysExを出力します。YM2151の各MIDIチャンネル
（チャンネル1～8）には、VM5のユーザー音色を選ぶため、ノートやプログラムチェンジより前に
バンクMSB=0、LSB=1を送ります。プログラム番号はVM5内の対応する音色番号です。
このバンク指定はSMAF変換ツール側がMA-5ユーザー音色バンクを認識することを前提とします。
SMAF変換後の音色適用は、利用する変換ツールと再生環境でも確認してください。

MSM6295のPCM波形はサンプルテーブルに従って切り出し、MSM6295 ADPCMをデコードして
モノラル・16-bit PCM WAVとして `wav/<archive>/` に出力します。既定のサンプルレートは
7,576 Hzです。WAVファイル名は `024.wav` のようなMIDIノート番号で、各波形には
ノート24から順に個別の番号を割り当てます。PCMトラックのMIDIノート番号も対応する
波形のWAVファイル名と同じ番号になります。WAV出力先を変えるには `--wav-dir` を指定します。

MIDI音源はゲーム本来のYM2151音色やMSM6295サンプルを自動では再現しません。
PCMトラックを元の音色で鳴らすには、出力されたWAVをファイル名のノート番号に対応する
キーへ割り当てるサンプラーを使用してください。XML内の `<titlelist>` の項目のうち、
シーケンス形式を持つ効果音はMIDI化します。ボイスなど、シーケンスヘッダーがない
サンプル再生項目はスキップします。ビブラートなど、意味が確定していないドライバー命令は
再現しません。

VM5はMA-5音源向けの音色形式です。YM2151とはアルゴリズム、LFO、オペレーター設定に
差があるため、VM5への変換結果は元音色の近似であり、YM2151と同じ音になることを
保証しません。アルゴリズム、音量、エンベロープ、LFO等はMA-5側の範囲へ近似し、
YM2151のフィードバックと基本的なオペレーター設定を移します。127個のプログラム枠を
収録し、各プログラム番号はMIDIのプログラム番号に合わせています。

MUCOM形式のテキストは、各プログラムを `@番号:{` 形式の4オペレーター音色定義として
出力します。MUCOM定義で表現できないYM2151固有のDT2とAM/LFO設定は出力されず、
音色は近似になります。

OPM形式のテキストはYM2151音色ライブラリで一般的な `LFO`、`CH`、`M1`/`M2`/`C1`/`C2`
項目を使い、4オペレーターのエンベロープ、DT1/DT2、アルゴリズム、フィードバックを
出力します。CPS-1固有データからLFO、パン、AMS/PMS感度の対応は確定できていないため、
それらは中立値で出力します。オペレーターの出力レベルはCPS-1の音量値から換算した
近似値です。したがって、OPMテキストはYM2151向けですが、全パラメーターの完全な復元では
ありません。

別のCPS-1ゲームに使う場合は、ROMのファイル名・オフセットに加えて、そのゲームの
サウンドドライバー版とテーブル位置を確認し、XMLへ正しく指定してください。
V4.25以外のバージョンには対応していません。
