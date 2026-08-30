# HPLC Analyzer

リリース版は[GitHub Releases](https://github.com/mshibagaki/HPLC_Analyzer/releases)とSemVer tagで管理します。ソースディレクトリ名にはバージョン番号を含めません。最新の公開Stable版と現在の開発版は、後述の「バージョン番号とRelease運用」を参照してください。

要求の実装状況と残作業は[`REQUIREMENTS_STATUS.md`](REQUIREMENTS_STATUS.md)、外部・実機律速は[`VALIDATION_BLOCKERS.md`](VALIDATION_BLOCKERS.md)を正本として管理しています。

島津 GCsolution / LCsolution / PACsolution のASCIIクロマトグラム、およびPACsolutionの`.gcd`を、元データを保持したまま管理・重ね描き・積分・定量・作図し、研究室内の測定履歴を共有データベースへ集約する研究用デスクトップソフトです。

提供されたASCII実データに加え、`rawdata`内の6組の`.gcd` / `.TXT`で、GCD直接読込の取得日時、全強度点、時間軸、ベンダーピーク表の主要値がASCII出力と一致することを検証しています。

v1.2.4では、Windows 7 SP1 32-bit / Core 2実機で確認したlegacy依存セット（Python 3.8.10 x86、NumPy 1.20.3、Pillow 9.5.0、PySide2 5.15.2.1、Qt 5.15.2、Matplotlib 3.7.5、PyInstaller 5.13.2）を固定しました。各native依存を別プロセスで順番にimportし、どれか1つでも異常終了した場合はPyInstallerへ進みません。Windows 7では画面表示だけをピーク保持型のmin/max envelopeで間引く軽量描画を標準にし、解析・CSV・PNG・SVG・PDF・A4レポートは常に元データを使います。Windows 11 64-bit版は高品質描画と専用の新しい依存セットを維持し、両版の`.hplcproj`とプリセット形式は共通です。

## 実験的なPyQtGraph表示（開発版）

Windows 11向けのQt 6環境では、グラフ上部の「PyQtGraph表示（実験的・今回のみ）」で試験表示に切り替えられます。標準は引き続きMatplotlibで、この選択は保存されません。

- 通常表示・概要＋詳細・Y1/Y2の上下2画面、ホイールズーム、ツールバーのパン／戻る／進む／ホーム、軸リセットに対応します。表示範囲と履歴は従来画面と共有します。2画面では時間軸を共有し、自動ズームとパンは操作した段の縦軸だけを変更します。ズーム方向を明示指定した場合は従来画面と同じ動作です。
- 上下2画面のB%表示は、両方に表示する／両方とも非表示の共通切り替えです。ONでは選択中のクロマトグラムの同じグラジエントと濃度目盛りを両段に表示します。選択対象が非表示またはグラジエント未登録なら両段とも表示しません。通常のMatplotlib描画・図保存でも同じ動作です。
- 縦線ポインターモードでは、プロット内のクリックで線を追加できます。既存の線はモードOFFでもクリックして選択でき、グラフにフォーカスがある状態でDeleteを押すと削除できます。追加・削除はUndo／Redoとプロジェクト保存に対応します。
- 2画面の縦線追加先はクリックした段です。別の段にある縦線は選択しません。
- 手動積分では、選択中の表示クロマトグラム上を左ドラッグして範囲を指定できます。上下2画面では選択対象と同じ段で操作してください。既存のベースライン設定・時間シフト補正・積分計算を使用し、ピークリスト、Undo／Redo、プロジェクト保存へ反映します。範囲内のデータ点が足りない場合は警告し、ピークを追加しません。
- 積分範囲修正では、ピークリストの対象を選んで修正モードにし、グラフを左ドラッグします。上下2画面では対象と同じ段で操作してください。既存ピークのID・備考・ベースライン設定を保持して範囲を再計算し、確定後は修正モードを解除します。Undo／Redo・保存に対応し、データ点不足の場合は元の範囲へ戻して警告します。
- ピーク分割では、ピークリストの対象を選んで分割モードにし、対象クロマトグラム上をクリックします。上下2画面では対象と同じ段で操作してください。最寄りの有効な測定点で分割し、親の直線ベースライン・備考・積分由来を両方の子ピークへ引き継ぎます。分割モードは連続操作のため維持され、Undo／Redo・保存に対応します。分割できない範囲では警告し、元のピークを保持します。
- トレース移動では、対象クロマトグラムを選び、X方向・Y方向・両方向のいずれかを選んでグラフを左ドラッグします。上下2画面では対象と同じ段で操作してください。ドラッグ中は選択トレースだけを仮移動し、確定時に時間シフトと縦オフセット、ピーク再計算、Undo／Redo、保存へ反映します。クリックだけでは変更せず、Escape、フォーカス／表示／対象／方向／モード変更や描画失敗では元へ戻します。
- テキストラベルは、追加モードで配置先をクリックすると既存の設定ダイアログが開きます。表示済みの文字ボックスは左ドラッグで移動し、ダブルクリックで文字・対象・軸・位置・フォント・色の変更または削除ができます。上下2画面では指定軸の段に表示し、画面を拡大縮小しても文字とボックスの画面上の大きさは固定です。Escape、フォーカス／表示切替や描画失敗では未確定の移動を元へ戻します。追加・移動・編集・削除はUndo／Redoと保存に対応します。
- フラクションモードでは、詳細グラフ内を左ドラッグして範囲を指定できます。ドラッグ中は仮表示し、同じ段でボタンを離すと既存の時間区切り設定で確定します。逆向きのドラッグ、Undo／Redo、範囲クリア、プロジェクト保存に対応します。範囲は従来どおりプロジェクト共通で、上下2画面では確定後に上段へ描画します。
- 手動積分・積分範囲修正・フラクションの未確定範囲は、Escape、グラフ外への移動、フォーカス移動、リサイズ、表示・選択・操作モード変更で取り消します。積分範囲修正中に対象ピークの内容が変わった場合も取り消します。クリックだけでは変更せず、ドラッグ中のホイール操作は無効です。
- ツールバーの矩形ズームは、選択中のX、Y、X+Y（自動はX+Y）へ適用されます。上下2画面ではドラッグした側のY軸だけを変更し、Home／Back／Forwardの表示履歴にも入ります。Escape、フォーカス移動、表示変更で未確定の範囲を破棄します。
- ツールバーのサブプロット／図の詳細設定は、試験表示中も既存の「軸・ラベル設定」を開きます。軸文字列、X目盛間隔、軸・目盛・凡例・保持時間ラベルのフォント／サイズ／色をプロジェクトへ保存し、Undo／Redoできます。キャンセル時は変更しません。
- 保存された軸タイトルのフォント／サイズ／色、目盛ラベルのフォント／サイズ／色、X軸の手動主副目盛間隔、凡例のフォント／サイズ／色と6種類の位置を試験表示にも適用します。「グラフ右外」の凡例は専用の右列へ配置します。
- 表示画面のコピー・印刷は表示中の描画を使用します。PNG/SVG/PDFの図保存・解析レポートは従来のMatplotlib経路を維持します。
- PyQtGraph未導入・初期化／描画エラー時も従来画面へ戻ります。Win7では選択できず、依存追加もしません。

これは移行途中の試験表示です。試験表示中の通常再描画では、Matplotlib側は軸と表示範囲の骨格だけを保持し、非表示のデータ線・B%線・積分表示・縦線・注釈・概要線・凡例は構築しません。PNG/SVG/PDF保存時だけ高品質Matplotlib図を一時的に再構築し、試験表示を閉じた場合や描画エラー時は完全なMatplotlib画面へ戻します。PyQtGraphのデータ線は元配列を変更せず、画面専用のピーク保持min/max envelopeへ減らします。系列IDと軸構成が同じ再描画ではdetail/overviewアイテムを再利用し、B%・積分表示・縦線・フラクション・注釈も内容が同じなら保持します。内容変更時は非系列アイテムだけを入れ替え、系列の追加・削除・軸変更時は全体を安全に再構築します。実機でのDPI・日本語フォント・ポインター・印刷を含む最終確認は引き続き必要です。

## 開発者向けクイックスタート

このリポジトリでは、Windows 11版とWindows 7版で使用するPython、Qt、依存パッケージ、ビルドするOSが異なります。アプリケーションのソースは共通ですが、両環境の仮想環境や依存パッケージを混在させないでください。

以下のビルド・起動・テストコマンドは、`README.md`と`app.py`があるリポジトリ直下をカレントディレクトリとして実行します。

最短の作業経路は次のとおりです。

- Windows 11で開発する場合：`build_windows11.bat`を1回実行し、その後は`run_source_windows11.bat`でソースを起動します。
- Windows 11版を配布する場合：`build_windows11.bat`が生成したWindows 11 x64用セットアップEXEを配布します。
- Windows 7版を作る場合：Windows 11で`build_all_windows.bat`を実行して搬入用ZIPを作り、Windows 7 SP1 32-bit実機へ移して`build_windows7_offline.bat`を実行します。
- Windows 7版を配布する場合：Windows 7実機で生成されたWindows 7 x86用セットアップEXEを配布します。

既にビルド済みのアプリを利用するだけなら、開発環境は不要です。対象OS用のセットアップEXEを実行し、スタートメニューの`HPLC Analyzer`から起動してください。

| 目的 | Windows 11版 | Windows 7版 |
| --- | --- | --- |
| 対象OS | Windows 11 64-bit | Windows 7 SP1 32-bit（build 7601） |
| ビルドするPC | Windows 11 64-bit | Windows 7 SP1 32-bit実機 |
| Python | Python 3.11 x64 | Python 3.8.10 x86 |
| Qt | PySide6 / Qt 6 | PySide2 / Qt 5 |
| requirements | `requirements-win11.txt` | `requirements-win7-bootstrap.txt`、`requirements-win7.txt` |
| ビルド入口 | `build_windows11.bat` | `build_windows7_offline.bat`（`build_windows7.bat`も同じ処理） |
| 配布物 | Windows 11 x64用セットアップEXE | Windows 7 x86用セットアップEXE |

### Windows 11版を開発・起動する

前提として、Windows 11 64-bit PCへPython 3.11 x64とInno Setup 6をインストールします。初回はリポジトリ内の`build_windows11.bat`を実行してください。このバッチは`.venv-win11-x64`を作成し、`requirements-win11.txt`の依存導入、パッケージ検証、全テスト、PyInstallerによるEXE生成、Inno SetupによるセットアップEXE生成を順番に実行します。初回の依存導入にはインターネット接続が必要です。

```bat
build_windows11.bat
```

一度ビルドして仮想環境を作成した後、ソースからGUIを起動する場合は次を実行します。

```bat
run_source_windows11.bat
```

生成された単体EXEを確認する場合は、次を起動します。

```text
dist\windows11-x64\HPLC_Analyzer.exe
```

他のPCへは、単体EXEではなく次のセットアップEXEを配布するのが標準です。配布先にPython、Qt、Inno Setup、ソースコード、インターネット接続は不要です。

```text
dist\installers\HPLC_Analyzer_Setup_1.2.4_Windows11_x64.exe
```

### Windows 7版をビルド・起動する

Windows 7版の実行ファイルをWindows 10/11上で生成してはいけません。まずWindows 11 PCで`build_all_windows.bat`を実行し、Windows 11版セットアップEXEとWindows 7搬入用の完全オフラインビルドZIPを用意します。

```bat
build_all_windows.bat
```

生成された次のZIPをUSB等でWindows 7 SP1 32-bit実機へ移し、`C:\HPLC_Build\HPLC_Analyzer`などの短いローカルディスクパスへ展開します。USB、`Program Files`、ネットワーク共有上から直接ビルドしないでください。

```text
dist\offline\HPLC_Analyzer_1.2.4_Windows7_Offline_Build.zip
```

展開先のWindows 7実機で次を実行します。必要なPython、wheel、Inno Setup、VC++ x86ランタイムはオフライン資材へ同梱され、pipは`--no-index`で動作します。

```bat
build_windows7_offline.bat
```

バッチは、同梱資材のSHA-256、OSとCPUアーキテクチャ、Pythonと固定依存、native依存の個別import、全テスト、通常版／Debug版EXE、両EXEの起動スモークテストを検証します。すべて成功した場合だけセットアップEXEを生成します。

生成後は通常版を解析に使用し、通常版が起動しない場合だけDebug版で起動時エラーを確認します。

```text
dist\windows7-x86\HPLC_Analyzer.exe
dist\windows7-x86\HPLC_Analyzer_Debug.exe
```

他のWindows 7 PCへ配布するファイルは次のセットアップEXEです。Windows 7版セットアップには必要なアプリファイルとVC++ 2015-2019 x86ランタイムが含まれます。

```text
dist\installers\HPLC_Analyzer_Setup_1.2.4_Windows7_x86.exe
```

Windows 7版は、NumPy 1.20.3を含む`requirements-win7.txt`の固定バージョンを前提とします。NumPy 1.24.4は対象の古いCore 2実機で`0xc000001d`となることが確認されているため、通常の依存更新やWindows 11側の依存との共通化を行わないでください。また、対象PCにはWindows 7 SP1、SHA-2対応、KB2533623相当のDLLローダー更新が必要です。詳細なオフラインビルド手順と障害時の確認方法は`README_Windows7_Offline.txt`を参照してください。

### 共通のテスト

開発中に現在の環境でテストだけを実行する場合は、対象OS用の仮想環境を有効にしてリポジトリ直下で次を実行します。GUIを表示できないビルド検証では、各ビルドバッチが`QT_QPA_PLATFORM=offscreen`を設定して同じテストを実行します。

```text
python -m unittest discover -s tests -v
```

Windows 7互換性は、Windows 11でテストが通ることだけでは確認できません。Windows 7関連の変更は、Windows 7 SP1 32-bit実機上でビルド、通常版／Debug版の起動、ASCII読込、保存、図出力まで確認してください。

## 主な機能

### データ管理と表示

- CP932/Shift-JIS系の島津ASCII、およびPACsolution GCDを複数読み込み
- ディレクトリ内のTXT/GCDを相対パス順でプレビューし、作業ディレクトリ用ラベルを付けて一括読み込み（サブフォルダー検索は任意、途中キャンセル可）。選択したディレクトリは自動登録され、ラベルは測定グループへ暗黙コピーしない
- 同じディレクトリに拡張子だけが異なる同名のGCD/TXTがある場合は、拡張子を除くファイル名を大文字・小文字を区別せず比較してGCDを優先し、TXTをスキップした件数を読み込み結果に表示（TXTしかない測定と、別ディレクトリの同名ファイルは従来どおり対象）
- 複数の作業ディレクトリをプロジェクトへ登録し、明示的な再読み込みで新規TXT/GCDだけを差分追加（GCD/TXT優先規則は一括読み込みと共通、既取込hashはskip、同一pathの内容変更は自動置換せず保留）
- Raw Intensity（µV）を保持し、AU/VからAU/mAUへ換算
- 表示ラベル、短縮ラベル、サンプル名、ID、グループ、反復、タグを保存
- 元ファイルのフルパス、元ディレクトリ、SHA-256を記録
- クロマトグラム一覧にRun ID、独立したTimestamp、Columnを表示し、Run共有値の編集を同一Runの全波長へ同期
- 複数クロマトグラムの重ね描き、個別表示、色、時間シフト、縦オフセット
- クロマトグラムごとの縦軸1/2配置（親ディレクトリに`ch2`だけを含む新規データは縦軸2、`ch1`または両方を含む／どちらも含まない場合は縦軸1へ初期配置）
- 選択データの%Bを独立した0–100%軸に表示し、凡例へのクロマトグラム名付加を切り替え可能
- 主目盛グリッド線の表示／非表示を切替
- 未指定の既定色は280 nmを青系、214 nmを赤系で割り当て（手動指定色を優先）
- 左表の「すべて表示」「すべて非表示」
- 左表の「上へ」「下へ」でクロマトグラムの表示・凡例順を変更
- 左表の波長セルへ数値を直接入力でき、凡例を原則 `ラベル_波長` の形式で表示
- 「凡例設定」でRun ID、ラベル、タイムスタンプ、波長、カラム等の表示項目・順序・区切り文字を設定
- 左表上で `Shift + ホイール` による横スクロール
- 長い元ファイルパスは右側の末尾を優先表示
- 1画面表示と、全体図＋操作用拡大図の2画面表示を切替
- Y軸1を上段、Y軸2を下段へ分けた共有X軸の2パネル表示へ切替
- グラフ部分と操作・ピーク表部分の境界をドラッグして表示高さを変更
- 描画品質を「高品質／軽量」から選択。Windows 7は軽量、Windows 11は高品質が既定
- 画面用Figure/Canvasはscreen surface interface経由で生成し、解析・Project保存・A4レポート描画から分離
- Windows 11 / Qt 6の画面描画はPyQtGraphが既定。チェックボックスでMatplotlibへ切り替えでき、選択はアプリ設定として再起動後も保持
- 軽量描画は画面だけをpixel幅に応じてmin/max間引きし、非表示データを描画対象から除外
- パン／連続ズーム中は再描画を抑制し、操作終了時に現在の表示範囲を正式再描画

Windows 11ビルドは`requirements-win11-pyqtgraph.txt`を通常のビルド手順で導入し、Qt 6ではPyQtGraphを画面描画の既定にします。表示trace、Y1/Y2割当、凡例、B%系列、peak overlay、vertical marker、fraction region、free textはbackend-neutral sceneとして構成され、pointer入力、hit-target、軸別pan、home/back/forward履歴、overviewの全体・詳細窓と表示範囲はbackend-neutral `ScreenViewState`が正本です。PyQtGraphが未導入、Qt 6初期化または描画に失敗した場合は、理由を画面へ表示してMatplotlibへ自動復帰します。画面描画の選択はプロジェクトではなくアプリ設定へ保存されます。

Matplotlibは次の責務のため削除しません。

- PNG / SVG / PDFの図出力、現在画面のクリップボードコピーと印刷に使う高品質Figureの再構築
- `report.py`の独立したFigureによるA4解析レポートとレポート印刷
- PyQtGraph未導入・初期化／描画失敗時のWindows 11フォールバック画面
- PySide2 / Qt 5固定のWindows 7画面描画（PyQtGraphはWindows 7依存へ追加しない）

Matplotlib画面へ切り替えた場合も同じsceneと`ScreenViewState`を使用します。Windows 7の固定依存、offline build、軽量描画設定は変更しません。

```text
python scripts\benchmark_screen_renderers.py --traces 8 --points 100000 --repeats 3 --output renderer-benchmark.json
python scripts\benchmark_integrated_screen.py --traces 8 --points 100000 --repeats 3 --output integrated-screen-benchmark.json
python scripts\benchmark_integrated_screen.py --traces 8 --points 100000 --repeats 3 --decorated --output integrated-decorated-benchmark.json
python scripts\benchmark_integrated_screen.py --input sample_data --repeats 3 --decorated --output integrated-file-benchmark.json
python scripts\benchmark_integrated_screen.py --input path\to\run1.gcd path\to\exports --recursive --repeats 3
python scripts\probe_pyqtgraph_parity.py
```

`build_windows11.bat`は通常requirementsに続けて`requirements-win11-pyqtgraph.txt`を導入し、`scripts/verify_windows11_x64.py --packages`が固定版の同梱を検証します。この追加requirementsはWindows 7 offline buildには含めません。consumerはproduction sceneのstatic要素、Qt snapshot、navigationと編集eventを扱い、失敗時は保存済みの選択を破棄せずMatplotlibへ戻ります。

結果JSONには環境、workload、各回の描画時間、中央値、元配列SHA-256を記録します。統合benchmarkは実際の`MainWindow._plot()`を両経路で測り、試験表示中のMatplotlib線数とnative scene要素数も記録します。`--input`にはGCD/TXTファイルまたはディレクトリを複数指定でき、productionの探索・parserを使用します。読込不能なsidecar TXTは黙って混ぜず、ファイル名と理由を`skipped_inputs`へ残します。元ファイルや生成JSONは変更・自動追跡しません。採用には、代表workloadで明確な速度改善があり、ズーム・二軸・gradient・annotation・snapshotを再現でき、Win7 offline buildまたは明示的なplatform別fallbackを維持できることを要求します。

Win11の隔離probeでは、primary trace、Y2軸、split panel、zoom/pan、integration region、retention/fixed-size text、vertical marker、curve picking、snapshotを再現できました。gradientはcustom AxisItemとViewBoxを追加し、Y2と同時にB%用の第三独立scaleとして共有X軸上へ重ねられることも確認済みです。

### 移動・ズーム

- Matplotlibツールバーによるズームとパン
- パン開始位置がx軸ならXのみ、第1y軸なら第1y軸のみ、第2y軸なら第2y軸のみ、プロット内ならX＋両y軸をドラッグ移動
- マウスホイールによるカーソル中心ズーム
- カーソルがx軸上ならXのみ、第1y軸上なら第1y軸のみ、第2y軸上なら第2y軸のみ、プロット内ならX＋両y軸を自動ズーム
- 必要に応じて、従来の固定Xのみ、Yのみ、X+Yモードへ切替
- 選択クロマトグラムだけをX、Y、X+Y方向へドラッグ移動
- グラフのダブルクリックで直前のビューへ戻る
- 表示範囲が変わった瞬間にx軸主目盛・副目盛を自動更新
- x軸目盛は「表示範囲に合わせて自動」と「主・副目盛を数値指定」を切替
- 全体表示、X軸のみ全体表示、Y軸のみ全体表示を個別ボタンで実行
- 全体表示後も時間シフト、オフセット、軸ラベルなどを維持
- 専用アイコンと「縦線ポインター」表示を持つポインターモード（上部ツールバーと「移動・ズーム」の両方から切替）
- ポインターモードのクリックで縦の参照線を保存し、線を選択してDeleteキーで削除
- 再描画後のツールバー履歴と操作モードを再同期し、パン／ズームが反応しなくなる問題を抑制

### 積分とピーク検出

- ドラッグによる手動積分
- 範囲両端、両端近傍中央値、開始側水平、手動2点、ゼロの各ベースライン
- グラフ上のドラッグによる積分範囲修正と、クリック位置によるピーク分割
- フラクション範囲をドラッグ選択し、指定した時間間隔で区切り線を表示・保存
- 分割後は保持時間が長い側のピークを次の対象として自動選択し、分割モードを維持して連続分割
- 手動積分・分割中の縦カーソルと、ズーム/パンの自動解除
- 保持時間、ピーク高さ、秒積分面積（µV·sec / mAU·sec）、FWHM、%Area、ピーク頂点のA/B/C/D比率
- 積分リストを保持時間順に自動整列
- 時間シフト後の保持時間に対応する%Bを再計算
- 自動ピーク検出（S/N、プロミネンス、平滑化幅、ピーク幅、間隔、境界、最大数を設定可能）
- 非破壊ピークフィッティング基盤（対称Gaussian、右テーリングEMG、自動モデル選択、RMSE・R²・AIC）
- 選択ピークのフィットモデルを指定し、結果保存・曲線重ね描き・Undo/Redo
- 自動検出結果を編集可能な候補として追加し、手動/自動を記録
- 積分リストの複数選択、全選択、一括削除。選択後は`Delete`キーでも削除可能
- 積分リストの備考欄へ、ピーク同定結果などを直接入力
- 積分、分割、範囲修正、削除、自動検出、時間シフト等のUndo/Redo
- `Ctrl+Z`でUndo、`Ctrl+Y`でRedo（最大50履歴）

### 作図と出力

- 積分範囲・保持時間線・ベースラインの表示ON/OFF
- 積分の開始・終了線はグレー、ピーク位置線はピーク色で区別
- 保持時間ラベルの表示ON/OFF。保持時間ラベルの既定色は黒
- Shift／Ctrlで複数選択したクロマトグラムすべてに保持時間ラベルを表示
- 凡例位置プリセット、ドラッグ移動、%B軸と重ならない右外配置
- X軸、縦軸1/2、グラジエント軸の任意ラベル
- 別ウィンドウで軸タイトル、目盛、凡例、保持時間ラベルのフォント・サイズ・色を設定
- グラフ上へ自由なテキストボックスを配置し、ドラッグ移動、ダブルクリックによる文字・座標・対象クロマトグラム・基準軸・フォント・サイズ・色の再編集
- テキストボックスの文字と枠は、ズームに依存しない画面固定サイズで表示
- 軸、目盛、凡例、保持時間ラベル、新規テキストラベルの既定フォントはArial。操作画面はWindows／Qtの標準UIフォントを使用
- 自動軸ラベルはUI言語にかかわらず英語を標準使用
- `ファイル → 図を出力`でPNG / SVG / PDFを選択（初期値PNG）。画面スペース確保のため表示群には出力操作を置かない
- 現在の表示画面をクリップボードへ画像コピー、またはOS印刷ダイアログから印刷
- ピーク表CSV、選択クロマトグラムCSV、表示中クロマトグラムCSV一括出力
- サンプル・測定条件・カラム・試料情報一覧CSV
- サンプル情報、測定条件、保持時間ラベル付きクロマトグラム、ピーク表をまとめたA4縦の解析レポートPDF
- 解析レポートの対象を「すべて／現在表示中／現在選択中」から出力・印刷時に選択
- レポートごとに積分範囲、ベースライン、保持時間、B %、グラジエント、定量値の掲載有無を選択
- ピーク表を省スペース化し、1ページ目20件・続きページ40件まで掲載
- 解析レポートPDFの初期出力先にも、環境設定の既定保存先を使用
- OSの印刷ダイアログを使ったA4解析レポートの直接印刷
- 軽量描画中でもPNG / SVG / PDF / A4レポートは元データから高品質出力

### 条件と保存

- サンプル、測定条件、試料情報に分けた詳細入力画面
- 詳細・定量条件の名前付きプリセットと一括適用
- 条件／グラジエントプリセットは、適用前に現在値・プリセット値・適用後の差分を確認可能
- プリセット一覧は新しい順（既定）、最近使った順、最近更新した順、名前順へ切替でき、名前で絞り込み可能
- `設定 → プリセット管理…`で条件／グラジエントプリセットを名前変更・複製・削除（確認付き）し、version付きJSONで全件／選択中をimport/export。同名はskip・置換・別名保持を明示選択
- 条件プリセットとグラジエントプリセットをソフト全体で記憶し、新規・別プロジェクトへ引き継ぎ
- v1.1.4以前のWindows設定にあるプリセットを初回起動時に版番号非依存の`presets.json`へ自動移行し、アプリ更新後も引き継ぎ
- 条件／グラジエントプリセットの保存名は、現在読み込んでいるプリセット名を初期表示して編集可能
- 条件プリセットはクロマトグラムのラベル名を保存・反映しない
- 一括画面の条件セルを直接編集し、全行の型・許容値を確認してからatomicに適用。Run単位の値は同一Runの行へ同期し、波長・AU/V・縦軸はクロマトグラムごとに独立
- 一括画面はShift/Ctrlで複数セルを選択し、矩形範囲をCtrl+C/Ctrl+VのTSVとして表計算ソフトとコピー／貼り付け可能。不正値や同一Run内の矛盾があれば貼り付け前に中止
- 一括画面のGradient列または専用ボタンから、選択Runの時間プログラム、A/B/C/D比率、流量、溶媒名・組成を確認・編集可能
- 複数クロマトグラムを選択して現在行のRunへ明示的に統合、または各クロマトグラムを独立Runへ分離（共有条件の優先を確認し、Undo/Redo可能）
- 一括画面は現在セルの行にある「詳細・定量条件」も同じダイアログから編集可能
- 空欄プリセットは既存値を消去しない
- A/B/C/D液と時間ごとの割合・流量プログラム
- B/C/D入力時にAを `100 − (B+C+D)` で自動補完
- グラジエントプログラムと溶媒組成の名前付きプリセット
- 設定画面でASCII読み込み開始フォルダと既定のデータ保存先を個別指定
- 新規プロジェクトは現在の画面を置き換えるか、独立した別ウィンドウで開くかを選択可能
- 新規保存時に `YYYYMMDD_Title_Column_Condition_Author` の5項目を確認し、統一形式のファイル名を自動提案
- `.hplcproj`に元GCDまたはASCII、解析結果、表示条件、各種プリセットを一体保存
- 日本語/英語UI切替

### 研究室共通データベース

- 研究室で共有する1つのSQLiteファイルへ、プロジェクト保存と同時に自動登録
- 安定したプロジェクトIDで同一プロジェクトを識別し、再保存時は重複追加せず既存記録を更新
- プロジェクト概要、測定者、カラム、条件、各クロマトグラムのサンプル情報・測定条件、グラジエントプログラムを保存（積分ピーク結果はDBへ保存しない）
- `研究室DB → データベースを開く`から、プロジェクト／クロマトグラム・条件／グラジエントの3一覧を閲覧
- グラジエントは1プログラムを時系列で1行に表示し、同一プログラムの名称・溶媒・使用プロジェクトを集約して重複表示を抑制
- 各一覧を測定日の新しい順で表示
- 全列を対象にした検索と、3一覧のUTF-8 BOM付きCSV一括出力
- DB更新に失敗した場合も`.hplcproj`の保存は維持し、警告後に`現在のプロジェクトを再同期`可能

## 自動ピーク検出

`設定 → 環境設定`で以下を調整できます。

- S/Nしきい値
- 最小プロミネンス（µV）
- 平滑化幅（min）
- 最小/最大ピーク幅（min）
- 最小ピーク間隔（min）
- 積分境界（ピーク高さに対する%）
- 最大検出数

検出器は差分のMADからノイズを推定し、局所プロミネンスと幅の条件を満たす正のピークを候補として追加します。自動検出は最終結果を確定するものではありません。候補を確認し、必要に応じて範囲、分割、ベースラインを修正してください。誤検出が多い場合は、追加された行を全選択して削除するか、`Ctrl+Z`で検出操作全体を戻せます。

## 単位換算と吸光係数定量

ASCIIおよびGCDのIntensityはµVとして扱います。

```text
Absorbance (mAU) = Raw Intensity (µV) × AU/V × 10^-3
Absorbance (AU)  = Raw Intensity (µV) × AU/V × 10^-6
```

ベースライン補正後の秒積分面積を `Area_mAU_sec`、流量を `Q_mL_min`、モル吸光係数を `epsilon_M-1_cm-1`、セル光路長を `l_cm` とすると、一定流量では次式です。

```text
Amount (nmol) = Area_mAU_sec × Q_mL_min × 1000 / (60 × epsilon × l_cm)
```

グラジエント表の全行に流量が入力されている場合は、流量を時間補間し、秒単位の時間軸で吸光度×流量/60を数値積分します。面積値はv1.1.3以前のµV·min / mAU·min表示の60倍になりますが、%Areaと算出物質量は変わりません。画面内の `ヘルプ → 定量方法・計算式`でも式・単位・前提条件を確認できます。

## 基本操作

1. `設定 → 環境設定`で、読み込み開始フォルダ、データ保存先、自動検出条件を指定します。研究室DBを使う場合は、全PCで研究室共有フォルダ上の同じ`.sqlite3`ファイルを指定します。
2. `ファイル → クロマトグラムを読み込む`で1つ以上のGCDまたはTXTを選びます。ディレクトリ単位なら`ファイル → ディレクトリを一括読み込み`で対象、作業ディレクトリ用ラベル、読み込み順を確認できます。新規データのグループ初期値は空白です。継続的にファイルが増える場所は`ファイル → 作業ディレクトリを管理`で複数登録し、`作業ディレクトリを再読み込み`を実行すると新規ファイルだけを追加できます。同一SHA-256は重複追加せず、同じパスの内容が変わったファイルは既存解析を守るため自動置換しません。
3. 左表で表示、ラベル、波長、グループ、縦軸、AU/V、時間シフト、縦オフセットを調整します。
4. `詳細・定量条件`でサンプル、測定条件、試料情報を設定します。複数データは`条件の一括入力・プリセット`の表から直接編集でき、Shift/Ctrlでの複数選択とCtrl+C/Ctrl+Vに対応します。条件／グラジエントプリセットは名前で絞り込み、作成・使用・更新日時または名前で並べ替え、`内容・差分…`から適用前に確認できます。入力エラーがあれば表やProjectへ一部適用せず、該当セルを表示します。
5. `グラジエント`でA–D液の実組成と時系列プログラムを入力します。一括画面ではGradient列をダブルクリックして、選択Runの内容を確認・編集できます。
6. 表示群で単位、凡例、積分表示、保持時間ラベル、%Bを設定し、`軸・ラベル設定`で目盛間隔と各ラベルの書式を設定します。
7. 移動・ズーム群で移動方向、ホイールズームの自動／固定方向、縦線ポインター、全体表示を操作します。
8. 積分群で手動積分、範囲修正、分割、または自動ピーク検出を行います。
9. 必要に応じてピーク備考を入力し、同定結果を記録します。ピーク行は複数選択してまとめて削除でき、誤操作は `Ctrl+Z`で戻せます。
10. 自由ラベルは`編集 → テキストラベルを追加`または表示群の同ボタンから配置し、ドラッグで移動、ダブルクリックで編集します。
11. `ファイル → A4解析レポートPDFを出力`または`解析レポートを印刷`で結果をまとめます。
12. `名前を付けて保存`で日付・タイトル・カラム・測定条件・測定者を確認します。`YYYYMMDD_Title_Column_Condition_Author.hplcproj`が初期ファイル名になります。
13. 保存と同時に研究室DBが更新されます。DB内容は`研究室DB → データベースを開く`で確認できます。

## プロジェクトファイル

`.hplcproj`はZIPコンテナで、以下を一体保存します。

- 変更していない元GCDまたはASCIIのコピー
- 元ファイルの絶対パス、元ディレクトリ、ファイルハッシュ
- ラベル、サンプル情報、測定・定量条件
- グラジエントプログラムと溶媒組成
- 手動/自動の積分範囲、ベースライン、計算結果、ピーク備考
- 表示、作図、自動検出の設定
- 名前付き条件プリセットとグラジエントプリセット
- クロマトグラム色、時間シフト、任意軸ラベル、自由テキストボックス
- v1で安定化したプロジェクトIDと命名用メタデータ

元GCDまたはASCIIが移動・削除されてもプロジェクトを開けます。元の絶対パスは追跡情報として残ります。v0.4.0以前を含む旧プロジェクトも読み込め、最初にv1形式で保存した時点で安定したプロジェクトIDが付与されます。

v1系列では`.hplcproj`の基本フィールドとプロジェクトIDを維持します。今後のv1.xはv1.0.0で保存したファイルを読み込める方針です。v1.1.4で追加した秒単位の面積と、旧v1.x向けの分単位互換値はv1.2.4でも保持します。一般的な互換性と同様に、古いアプリが将来追加された機能を完全に再現できることまでは保証しません。

### Run IDとデータ項目の所属（schema 104以降）

1回の物理的な測定を`Run`、その測定から得た波長別などの各信号を`Dataset`として保存します。`Dataset.run_id`は必ず同じプロジェクト内の`Run.id`を参照し、Runの検索はID索引から行います。

- **Runが正本**：表示ラベル／短縮ラベル、測定日時、サンプル名／ID／グループ／反復／タグ／コメント、装置名、メソッド名、流量、カラム名／温度、注入量、セル光路長、分析対象物名・安定ID・別名・出典、214/280 nmのモル吸光係数と単位、分子量、溶媒組成、グラジエント、グラジエントプリセット名
- **Datasetが正本**：波長、AU/V、元ファイル追跡情報と元データ、表示設定、積分ピーク

セル光路長と214/280 nmのモル吸光係数は同じ測定内で共有するためRunに置き、検出波長とAU/Vはチャンネルごとに異なり得るためDatasetに置きます。同じRun IDを持つDatasetの表示ラベルと短縮ラベルは自動的に同期します。

schema 102以前のプロジェクトは、ラベル、時刻、元ファイル名が同じでも自動的にまとめず、旧Dataset 1件につきRun 1件を決定的なIDで作成します。schema 103で同じRunを共有しながらDatasetラベルが異なる場合は、Runに保存済みのラベル、なければ先頭Datasetのラベルを正本として同期します。移行では元データ、追跡情報、積分範囲と科学計算値を変更しません。新形式の保存時も各Datasetの従来`label`、`short_label`、`measurement`欄へRunの正本値を投影するため、Runを認識しない旧v1アプリは従来項目を読み取れます。Runと互換欄の値が食い違う場合はRunを正本として扱います。

schema 105ではProject固有の`work_directories`を追加しました。schema 104以前からの移行では空配列を追加するだけで、既存Dataset、Run、raw bytes、解析値を変更しません。

schema 106以降、新規RunのIDは`YYYYMMDD_HHMMSS_プロジェクト内連番_label`です（例：`20260829_143052_1_SampleA`）。日時は測定日時を使い、不明・解釈不能なら`unknown-datetime_1_SampleA`とします。取り込み日時で代用したり、IDから測定条件を書き戻したりはしません。通常の取り込みは引き続きDatasetごとに独立Runを作成し、日時やラベルが同じでも自動統合しません。

Run ID列を直接編集すると、同じRunに属する全クロマトグラムのIDが一括で変更されます。空のIDと同一プロジェクト内の別Runとの重複は拒否します（前後の空白は除去、大文字・小文字は区別）。別プロジェクトの同一IDとは連動しません。改名と「選択を同一Runへ」による統合は別操作で、重複IDへの改名では統合されません。改名はUndo/Redoに対応し、凡例のRun IDにも反映されます。

IDは生成時の値を保持し、ラベル・測定日時の編集や並べ替えでは自動変更しません。プロジェクトの`next_run_number`に次の番号を保存し、削除・統合・Undoでも巻き戻しません。分離は新規Runとして採番し、Redoは分離時のIDを復元します。手動IDが生成候補と重なる場合は番号を進めて衝突を避けます。旧プロジェクトの既存IDは維持し、連番は既存Run件数の次から開始します。旧アプリで再保存すると、この新しい採番カウンターが失われる可能性があります。

プリセットはプロジェクトにも保存されますが、ソフト側にも記憶されます。別のプロジェクトを開いた場合や新規プロジェクトを作成した場合も、保存済みプリセットを利用できます。v1.1.5以降は、従来のWindows設定を初回起動時にユーザーのアプリ設定フォルダー内の`presets.json`へ自動移行し、以後は両方へ同期します。v1.2.4を更新インストールまたはアンインストールしても、このユーザー設定ファイルは削除しません。

`presets.json` format 2では、条件／グラジエントの値を従来どおりのpayloadに保ったまま、stable ID、作成・更新・最終利用日時を`preset_metadata`へ分離して保存します。format 1の既存presetは内容と名前を維持し、判明しない作成日時を推測しません。preset名を変更してもstable IDは維持されます。Project内のpreset snapshotにはApplication側の利用履歴metadataを保存しません。

### 設定データの保存場所と責務

- **Application settings（QSettings）**：UI言語用の設定枠、読み込み／保存／直近フォルダー、研究室DBパス、画面描画品質、図の出力形式、命名用の測定者を保存します。Windows上の従来の保存先とキー名を維持します。
- **Persistent preset data（`presets.json`）**：条件プリセットとグラジエントプリセットの正本です。旧版のQSettings内プリセット値は、移行元および`presets.json`を読めない場合のfallbackとして残します。
- **Project-specific settings（`.hplcproj`）**：解析条件、表示状態、クロマトグラム、積分結果、注釈など、そのプロジェクト固有の状態を保存します。画面描画品質はApplication settingsであり、Projectには保存しません。

Application settingsのキー、既定値、型変換、不正値fallback、保存処理は`hplc_app/settings_store.py`へ集約しています。設定が欠損・破損している場合や一時的に保存できない場合も、安全な既定値で起動し、Projectやプリセットを削除しません。

画面言語はApplication settingの`ui/language`が正規値です。一度Englishへ変更すると次回起動、新規Project、別Projectを開いた後もEnglishを維持します。既存`.hplcproj`の`ui_language`は旧版との互換性のためそのまま保存しますが、Projectを開くだけでApplication言語を切り替えません。画面言語の変更だけではProjectをdirtyにしません。

## 研究室共通データベースの設定

1. 管理者が研究室共有フォルダに`HPLC_Lab_Database.sqlite3`を置く場所を決めます。ファイルはまだ存在しなくても構いません。
2. 各PCで`設定 → 環境設定 → 研究室共通データベース`から同じパスを指定します。
3. プロジェクトを保存すると、DBファイルが作成または更新されます。
4. `研究室DB → データベースを開く`で内容を確認します。

SQLiteは単一ファイル内でトランザクション更新し、別PCの書き込み中は最大15秒待機します。OneDrive等が各PCで別コピーを同期するフォルダではなく、全員が同じ実体へアクセスするファイルサーバー／共有フォルダを使用してください。定期的にDBファイルをバックアップしてください。

## セットアップEXEでインストールする

配布先PCへは、対象OSに合う次のセットアップEXEを1本だけコピーします。Python、Qt、Inno Setup、ソースコード、インターネット接続は配布先PCには不要です。

- Windows 11 64-bit：`HPLC_Analyzer_Setup_1.2.4_Windows11_x64.exe`
- Windows 7 SP1 32-bit：`HPLC_Analyzer_Setup_1.2.4_Windows7_x86.exe`

セットアップEXEをダブルクリックし、日本語または英語を選んで画面に従います。アプリ本体、README、ライセンス、3つのサンプルデータがインストールされ、スタートメニューへ登録されます。デスクトップショートカットはセットアップ画面で選択できます。Windows 7版には通常版、診断用デバッグ版、Windows 7対応のMicrosoft Visual C++ 2015-2019 x86ランタイムが含まれ、必要な場合だけランタイムを先に導入します。

同じ系列の新しいセットアップEXEを実行すると、同じ製品として更新インストールされます。更新前にHPLC Analyzerを終了してください。プリセット、読み込み／保存先、研究室DBパス等のユーザー設定、`.hplcproj`、元GCD・ASCII、研究室DBはインストールフォルダー外にあるため、更新やアンインストールでは削除されません。アンインストールはWindowsの「プログラムと機能」から実行します。

Release前のclean install、旧版からのupgrade、uninstall、reinstallでは、専用fixtureとbyte比較を使ってこの保持契約を確認します。手順と記録様式は[Installer Upgrade Test](../.github/INSTALLER_UPGRADE_TEST.md)および[Evidence Template](../.github/INSTALLER_UPGRADE_EVIDENCE.md)にあります。Windows 11とWindows 7の両方が必須で、Windows 7の合格にはWindows 7 SP1 32-bit / Core 2実機での記録が必要です。

v1.1.5以前の単体EXEはインストーラーの管理対象ではないため、自動削除されません。混同を避ける場合は、v1.2.4の起動確認後に旧EXEを手動で整理してください。

Windows 7版セットアップは必要なアプリファイルとVC++ランタイムを内包し、完全オフラインで動作します。ただし、Windows 7 SP1、SHA-2対応等のOS自体の必要な更新は事前に適用してください。Windows 7はサポート終了OSなので、ネットワークから隔離した測定PCへ搬入する前にセットアップEXEを別PCでウイルススキャンしてください。

コード署名証明書は同梱していないため、生成したHPLC AnalyzerのセットアップEXEは「発行元不明」と表示される場合があります。外部配布する場合は、組織のコード署名証明書で署名するか、信頼できる経路でSHA-256値を併記してください。同梱したオフライン資材は`win7_offline/MANIFEST.sha256`で転送前後の完全性を検証します。

## Windows 11 64-bit用インストーラーを作る

ビルドするWindows 11 PCに限り、Python 3.11 x64とInno Setup 6が必要です。Inno Setupは[公式ページ](https://jrsoftware.org/isdl.php)からインストールします。v1.2.4作成時の検証対象はInno Setup 6.7.3です。

1. Python 3.11 x64をインストールし、Python Launcherも有効にします。
2. Inno Setup 6をインストールします。
3. `build_windows11.bat`をダブルクリックします。
4. 依存パッケージと全テスト、EXEのx64形式、インストーラー設定、生成Setup EXEを順に自動検証します。

生成物：

- `dist\windows11-x64\HPLC_Analyzer.exe`
- `dist\installers\HPLC_Analyzer_Setup_1.2.4_Windows11_x64.exe`

## Windows 7 SP1 32-bit実機で完全オフラインビルドする

Windows 7版は、Windows 11上では生成しません。v1.2.4一式をUSBでWindows 7 SP1 32-bit実機へ移し、その実機上でビルドします。Python 3.8.10 x86、NumPy 1.20.3、Pillow 9.5.0、PySide2/Qt 5.15.2.1 x86、Matplotlib 3.7.5、PyInstaller 5.13.2、Inno Setup 6.7.3、VC++ 14.29 x86を同梱済みで、Windows 7 PCのインターネット接続は不要です。NumPy 1.24.4 win32は対象Core 2 6300で`0xc000001d`（Illegal Instruction）となるため使用しません。Pillow 10.4.0もNumPy 1.20.3との組み合わせで`numpy.typing.NDArray`を要求するため使用しません。

1. ZIPをWindows 7 PCのローカルディスク上に展開します。推奨先は`C:\HPLC_Build\HPLC_Analyzer`です。USB、`Program Files`、ネットワーク共有から直接ビルドしないでください。
2. `build_windows7_offline.bat`をダブルクリックします。従来名の`build_windows7.bat`も同じ処理を呼びます。
3. PythonやVC++を導入する前に、`REQUIRED_WHEELS.txt`に固定した全wheelの存在と、同梱資材すべてのSHA-256を検証します。続けてWindows 7 SP1 build 7601、32-bit、KB2533623相当のDLLローダーAPIを検証します。
4. 同梱Pythonへ正規化済み絶対パスを渡します。wheel内METADATAをPython 3.8 win32条件で再帰解析し、Matplotlibの条件付き依存を含む完全な依存閉包を確認してから、固定wheelを`.venv-win7-x86`へ`--no-index`で導入します。同一版のPythonが既存の場合は、版と32-bitを検証してそのインタープリターを利用します。ネットワーク取得は行いません。
5. NumPy、Pillow、shiboken2、PySide2、QtCore、QtGui、QtWidgets、Matplotlib、本体GUI、GUIスモークテスト、PyInstallerを1項目ずつ別プロセスで確認します。異常終了時はテスト名、終了コード、Python版／bitness、対象パッケージ版とEvent ID 1000確認コマンドを表示し、PyInstallerを開始しません。
6. 全テスト、通常版／Debug版のx86形式、両EXEの`--startup-smoke-test`をWindows 7上で実行します。
7. 両EXEの起動確認後だけ、同梱Inno SetupでセットアップEXEを生成し、コンテナを検証します。

生成物：

- `dist\HPLC_Analyzer.exe`
- `dist\HPLC_Analyzer_Debug.exe`
- `dist\installers\HPLC_Analyzer_Setup_1.2.4_Windows7_x86.exe`

通常版が起動しない場合は、スタートメニューの`HPLC Analyzer (Debug)`を起動すると、通常版では見えない起動時エラーを確認できます。通常の解析には`HPLC Analyzer`を使用してください。

詳しい手順は`README_Windows7_Offline.txt`にも記載しています。Windows 7 SP1にOS更新、SHA-2対応、KB2533623相当が未導入の場合は、先に適用して再起動してください。Python導入が失敗した場合、画面にはインストーラー終了コード、実際に確認した絶対パス、`.build-tools\python-3.8.10-install.log`の場所が表示されます。

## Windows 11版とWindows 7搬入用ZIPを準備する

Windows 11 PCで`build_all_windows.bat`を実行すると、Windows 11 x64版セットアップと、Windows 7へ搬入する完全オフラインビルドZIPを生成します。Windows 7の実行ファイル自体はWindows 7実機でのみ生成します。

- `dist\installers\HPLC_Analyzer_Setup_1.2.4_Windows11_x64.exe`
- `dist\offline\HPLC_Analyzer_1.2.4_Windows7_Offline_Build.zip`

同梱済み資材を再取得する必要はありません。将来Win7用wheelを更新する場合だけ、ネット接続したWindows 11 PCで`prepare_windows7_offline_wheels.bat`を実行します。この補助バッチは`pip download --platform win32 --python-version 3.8 --implementation cp --abi cp38`で依存を解決し、完全性検証とmanifest再生成まで行います。Windows 11版のPython依存パッケージを初めて導入する場合も、Windows 11ビルドPCのインターネット接続が必要です。

v1.1.1でWindows上のテスト終了時に`HPLC_Lab_Database.sqlite3`を削除できず`WinError 32`となった問題は、テスト用SQLite接続を明示的に閉じることで修正しています。過去の`.venv-win7`は64-bit版と混在しないよう再利用しません。

## Versioning

HPLC Analyzerは、`MAJOR.MINOR.PATCH`形式のSemantic Versioningに近いルールでバージョンを管理します。

```text
1.2.4
│ │ └─ PATCH
│ └─── MINOR
└───── MAJOR
```

- **PATCH**：バグ修正や小規模な改善など、既存機能・プロジェクト形式との互換性を維持する変更で増やします。例：`v1.2.4`から`v1.2.5`。
- **MINOR**：後方互換性を維持した新機能の追加で増やします。Run ID、アップデーター、互換性を保ったデータ項目追加など、複数の基盤機能をまとめる場合も対象です。例：`v1.2.4`から`v1.3.0`。
- **MAJOR**：既存のプロジェクト形式、保存データ、操作方法、または外部連携との互換性を壊す大規模変更で増やします。例：`v1.x`から`v2.0.0`。

最新の公開Stable版は`v1.2.4`、現在のmain開発版は`1.3.0-dev.1`です。開発版はtagや公開Releaseを意味しません。機能凍結後は`1.3.0-rc.N`、全Release gateを通す最終Stable候補では`1.3.0`へ進め、検証済みのStable候補commitへだけ`v1.3.0`tagを付けます。

公開GitHub ReleasesのmetadataだけをHTTPSで確認する更新通知を用意しています。`ヘルプ → 更新を確認…`から手動確認でき、環境設定で起動後のStable版自動確認を無効化できます。確認はGUI thread外で行い、自動確認時の最新版・offline・proxy・rate limit・壊れた応答は通常操作へ通知しません。Stable確認ではdraft/prereleaseを除外し、SemVerと公式repository配下のRelease URLを検証します。現段階ではApplication UIからinstallerのdownloadや起動は行いません。

installer更新用の非実行型検証基盤では、公式repositoryのRelease asset URL、download上限、専用一時directory、安全なfilename、SHA-256 manifestとの一致、Windows Authenticode状態と署名者thumbprintを検証できます。bounded chunk単位の進捗通知と協調的なキャンセルにも対応し、キャンセル時はUpdaterが作成した途中ファイルを残しません。Qt workerと日英progress/cancel/error dialogもsynthetic fixtureで検証していますが、正式な署名identityが設定されていない現段階ではend-userの更新操作へ接続していません。この検証に成功してもinstaller起動許可は常にfalseです。

Release assetはversionから決まるWindows 11 installer名と`SHA256SUMS.txt`が各1件だけ存在する場合に限って選択します。重複、欠落、別host、query付きURL、上限超過は曖昧な候補として拒否します。署名者policyは明示された40桁certificate thumbprintだけを受け付け、未設定、署名無効、identity不一致では将来の起動許可を返しません。production thumbprintは証明書契約・本人確認完了後に別途設定します。

Windows 7版とWindows 11版は同じアプリケーションバージョンを使用します。依存パッケージやビルド環境は異なりますが、OSごとに別のアプリケーションバージョン番号は付けません。`.hplcproj`互換性を壊す可能性がある変更は、バージョン番号だけで判断せず、移行・後方読込処理と両OS間の互換性確認を伴う必要があります。

Pull Requestと`main`更新では[Level 2 CI](../.github/CI_POLICY.md)がsource契約とWindows offscreen testを検証します。CI成功は、Windows 11の正式installer実機確認やWindows 7 SP1 32-bit/Core 2での完全offline build・実機確認の代替にはなりません。

現在の外部律速、未取得の実機証跡、自動テスト済み範囲、並行して進められる作業は[External blockers and deferred physical validation](VALIDATION_BLOCKERS.md)に集約します。

Application versionの機械可読な正規値は`hplc_app/version.py`の`APP_VERSION`だけです。リリース時は最初にこの1行を更新し、`python scripts/read_version.py`と`python scripts/read_version.py --format windows`が成功することを確認してください。build batchはこの値を読み取り、GUI、project/preset/DB、installer metadata、成果物名、Windows 7 offline archiveへ自動的に伝播します。取得不能またはSemVerとして不正な場合、buildは停止します。その後、READMEの「現在の安定版」や成果物例、`README_Windows7_Offline.txt`等のリリース文書を確認します。ただし、リリース履歴、互換性説明、例示中にある過去のversion番号は履歴情報なので、一括置換しません。

正式なRelease成果物名は、正規バージョンから自動生成する次の3種類です。pre-releaseやbuild metadataを含む場合も、SemVer文字列を省略せず名前へ残します。

- `HPLC_Analyzer_Setup_<version>_Windows11_x64.exe`
- `HPLC_Analyzer_Setup_<version>_Windows7_x86.exe`
- `HPLC_Analyzer_<version>_Windows7_Offline_Build.zip`

Windows EXEとinstallerの文字列版（FileVersion / ProductVersion）はSemVerを保持します。Windowsの固定数値版は`MAJOR.MINOR.PATCH.0`とし、pre-releaseとbuild metadataは数値へ入れません。各数値要素はWindows version resourceの制約に合わせて0〜65535です。通常版とWindows 7 Debug版は同じProductVersionを使い、FileDescription、元のファイル名、Debug flagで用途を区別します。

Windows 7 Debug版は、通常版が起動しない場合に原因を確認するためinstallerへ同梱する診断ツールです。独立したRelease成果物としては公開せず、通常の解析には通常版を使用します。installerの固定AppIdはOS版・更新版を通じて変更しません。

Release候補のsource整合性は、GitHub Releaseに記載するversionとproject schemaを明示して確認します。Application version、installer定義、固定依存、Windows 7 offline manifestとwheel閉包のいずれかが一致しなければ失敗します。

```bat
python scripts\release_consistency.py source --release-version v1.2.4 --project-schema 105
```

Windows 11 installer、Windows 7 installer、Windows 7 Offline Build Kitの署名と最終ファイル名が確定した後、3ファイルだけを置いたRelease用directoryでchecksumを生成します。署名やrenameの前に最終checksumを作ってはいけません。既存の`SHA256SUMS.txt`は誤操作防止のため`--force`なしでは上書きされません。

```bat
python scripts\release_checksums.py write --release-dir dist\release
python scripts\release_consistency.py assets --release-version v1.2.4 --project-schema 105 --release-dir dist\release
```

`assets`検査は、3つの正規artifact名、両installerのversion resource、Offline Build Kit内のApplication versionとproject schema、`SHA256SUMS.txt`の完全一致を確認します。GitHubへuploadした後もclean directoryへ再downloadし、`python scripts\release_checksums.py verify --release-dir <directory>`で再検証します。

RCからStableへ昇格する手順は[Release Process](../.github/RELEASE_PROCESS.md)、実施記録は[Release Checklist](../.github/RELEASE_CHECKLIST.md)、GitHub Release本文は[Release Template](../.github/RELEASE_TEMPLATE.md)を使用します。Windows 11とWindows 7実機、upgrade/data preservation、project互換性、checksumが揃うまでStableにはしません。

## 現在の制限

- ASCIIは今回の3ファイルと同じ `[Chromatogram (Ch1)]` / `R.Time` / `Intensity`構造が対象です。GCDはPACsolution 2.2系のOLE Compound File構造を持ち、`Status`、`Intensity Data`、`Peak Table`ストリームを含む実例で検証しています。別世代・別構造のGCDは推測で読み込まず、明示的なエラーにします。
- GCD直接読込では、実データで確認した`File Property` version 2.32.00のWindows FILETIMEから取得日時を復元します。FILETIMEのUTC値をsource metadataへ保持し、表示とRun IDには取込PCのOS timezoneでの現地時刻を使います。未知versionはoffsetを推測せず、厳密なファイル名日時、ファイル更新時刻の順にfallbackし、採用元をsource metadataへ記録します。sample name、sample ID、method name等の未解析metadataと、Peak Tableの`k'`、理論段数、テーリング、分離度等は、実測ゼロと区別するため空欄にします。元GCD bytesはproject内に変更せず保持します。
- 1ファイル内に複数チャンネルが同居する形式は未対応です。Ch1/Ch2が別ファイルなら縦軸1/2に分けて表示できます。
- ベースラインは指定区間内の直線または水平線です。曲線ベースライン、自動ベースライン追跡、ピーク波形のデコンボリューションは未実装です。
- 自動ピーク検出は正のピークを対象とする候補生成です。データごとの目視確認が必要です。
- 検量線、反復試料の統計、Excel形式への直接出力は未実装です。研究室DB一覧はCSVへ出力できます。
- グラジエントはプログラム値の線形補間です。dwell volume / gradient delayは未補正です。
- v1.1.5までのWindows実機EXE生成と、物理プリンターでのA4レポート印刷は確認済みです。v1.2.4のWindows 7オフライン資材・バッチ・インストーラー設定は自動検証済みですが、このLinux作業環境ではWindows用EXEをコンパイルできません。Windows 11版はWindows 11で、Windows 7版はWindows 7 SP1 32-bit実機で最終ビルドし、インストール・起動・ASCII読込・保存・図出力・アンインストールを確認してください。
- 研究用途の解析補助ソフトです。規制対象の品質試験、診断、臨床判断用としては検証されていません。

## テスト

```text
python -m unittest discover -s tests -v
```

### GCD解析結果を再検証する

GCDの読込は同名のTXTやCDFを参照せず、`.gcd`内のOLEストリームだけから時間軸、µV強度、ベンダーピーク表の主要値を復元します。今回の解析を手元の提供データから再現するには、アプリ本体ディレクトリで次を実行します。

```text
python scripts\verify_gcd_against_ascii.py ..\rawdata
```

各GCD/TXTペアについて、取得日時、点数、ピーク数、時間差、TXT出力時の強度量子化差、丸め後の強度差を表示し、不一致があれば終了コード1で停止します。GCD内では小数を含む64-bit強度をそのまま保持し、整数表記のTXTに合わせるための丸めは読込時には行いません。OLE内のストリーム名・サイズ・先頭バイトを調べる場合は次を使用できます。

```text
python scripts\inspect_gcd.py path\to\sample.gcd
```

テストでは、Win7 legacy wheelの固定・SHA-256・完全依存閉包、別プロセスimport診断、synthetic CFBによるFAT/DIFAT/mini-FATと破損GCDの防御、GCD/ASCII混在import、画面用min/max間引き、解析値の不変性、二軸・%B・積分範囲・zoom/panを回帰対象にしています。Run IDの旧Project移行、Run正本とDataset互換値、共有Runの保存・Undo、Application settingsの既存キー引継ぎ・型変換・不正値fallback・保存失敗、`presets.json`優先の旧プリセット移行、既存のプロジェクト互換、秒単位面積、研究室DB、cyan版アイコン、A4レポート等も引き続き検証します。

## ファイル構成

```text
app.py                    GUI起動
hplc_app/parser.py        対応形式の判定とDataset生成
hplc_app/gcd_parser.py    PACsolution GCD/OLEパーサー
hplc_app/analysis.py      換算・積分・自動ピーク検出・定量
hplc_app/project_io.py    プロジェクト保存
hplc_app/settings_store.py Application settingsの一元管理
hplc_app/preset_store.py  バージョン間で共有するプリセットJSON
hplc_app/naming.py        統一保存名の提案
hplc_app/database.py      研究室共通SQLite DB・CSV出力
hplc_app/exporters.py     CSV出力
hplc_app/report.py        A4解析レポートPDF・印刷ページ生成
hplc_app/gui.py           操作画面、Undo/Redo、プリンター出力
hplc_app/rendering.py     Win7軽量描画用の表示専用min/max間引き
hplc_app/screen_renderer.py 画面用レンダラーsurface interfaceとMatplotlib実装
tests/test_core.py        実データ・数式・保存・レポートのテスト
tests/test_gui.py         表示・操作・Undo/Redo・印刷のGUIテスト
sample_data/              提供された3つのASCII
scripts/verify_windows7_x86.py  Windows 7 x86ビルドの事前・事後検証
scripts/windows7_import_preflight.py  native依存の別プロセスimport診断
scripts/verify_windows7_offline_bundle.py  同梱資材のhash/形式検証
scripts/verify_windows7_wheelhouse.py  Python 3.8 win32依存閉包検証
prepare_windows7_offline_wheels.bat  Win11上のwheel再取得・固定検証
scripts/verify_windows11_x64.py Windows 11 x64ビルドの事前・事後検証
scripts/verify_installer.py     インストーラー設定・生成Setup EXE検証
scripts/inspect_gcd.py          GCD/OLEストリーム調査
scripts/verify_gcd_against_ascii.py  GCDとASCIIの数値照合
scripts/build_installer.bat     Inno Setupの検出と対象別コンパイル
scripts/package_windows7_offline_bundle.py  Win7搬入用ZIP生成
installer/windows11_x64.iss     Windows 11 x64用セットアップ定義
installer/windows7_x86.iss      Windows 7 x86用セットアップ定義
win7_offline/                   Win7用Python・wheel・Inno Setup・VC++
build_windows7_offline.bat      Win7実機用完全オフラインビルド
build_all_windows.bat           Win11版とWin7搬入用ZIPの一括準備
```
