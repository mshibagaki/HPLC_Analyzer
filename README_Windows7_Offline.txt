HPLC Analyzer 1.2.4 - Windows 7 SP1 32-bit 完全オフラインビルド

このフォルダーには、Windows 7実機でビルドするために必要なPython、wheel、
PySide2/Qt、NumPy、Pillow、Matplotlib、PyQtGraph、PyInstaller、Inno Setup、VC++ランタイムが
すべて含まれています。Windows 7 PCをインターネットへ接続する必要はありません。

重要な固定依存（勝手に更新しないでください）

Python 3.8.10 x86 / NumPy 1.20.3 / Pillow 9.5.0
PySide2 5.15.2.1 / Qt 5.15.2 / Matplotlib 3.7.5 / PyQtGraph 0.13.3 / PyInstaller 5.13.2
importlib-resources 6.4.5 / zipp 3.20.2

NumPy 1.24.4 win32は対象Core 2 6300実機でimport時に0xc000001d
(Illegal Instruction)となるため使用しません。Pillow 10.4.0もNumPy 1.20.3との
組み合わせではnumpy.typing.NDArrayエラーになるため使用しません。

手順

1. このZIPをUSBでWindows 7 SP1 32-bit PCへ移します。
2. USBから直接実行せず、ZIP全体をPCの内蔵ディスクへコピーして展開します。
3. 展開先は C:\HPLC_BUILD\HPLC_Analyzer 等の短い英数字パスを
   推奨します。Program Files内、USB上、ネットワーク共有上ではビルドしません。
4. build_windows7_offline.bat をダブルクリックします。
5. VC++ランタイムの導入で管理者確認が出た場合は許可します。
6. バッチが、資材のSHA-256、Windows 7 SP1 32-bit、Python/Qt、全テスト、
   生成EXEのx86形式、通常版とDebug版の実起動を順番に検証します。

依存wheelの事前検証

- PythonやVC++を導入する前に、REQUIRED_WHEELS.txtの全wheelが存在するか確認します。
- 全wheelとインストーラーのSHA-256をMANIFEST.sha256と照合します。
- Python 3.8で必要となるimportlib-resources 6.4.5とzipp 3.20.2、および
  Python 3.8 / NumPy 1.20対応のPyQtGraph 0.13.3 universal wheelを同梱しています。
- Python導入後、pipを実行する前にwheel内METADATAを読み、Python 3.8 win32条件で
  Matplotlibを含む全依存が固定wheelだけで解決できるか再帰検証します。
- pipは必ず --no-index --find-links だけで実行され、ネット接続を行いません。

PyInstaller前の正常表示例

[OK] Python 3.8.10 (32-bit)
NumPy OK 1.20.3
Pillow OK 9.5.0
shiboken2 OK 5.15.2.1
PySide2 OK 5.15.2.1
QtCore OK 5.15.2
QtWidgets OK PySide2.QtWidgets
PyQtGraph OK 0.13.3
Matplotlib OK 3.7.5
HPLC GUI import OK
[OK] HPLC Analyzer GUI startup smoke test passed.
PyInstaller OK 5.13.2
[OK] Every Windows 7 dependency and the HPLC GUI passed in isolation.

各項目は別のpython.exeプロセスで確認します。native crashを含め1項目でも失敗した
場合は、その時点で停止し、PyInstallerやインストーラー生成へ進みません。

Python導入時の表示

- 導入先は、バッチの場所から作成した絶対パスで表示されます。
- Pythonインストーラーの終了コードを必ず表示します。
- python.exeが作成されなかった場合は、実際に確認したパスと
  .build-tools\python-3.8.10-install.log の場所を表示します。
- 同一版Pythonがすでにインストール済みの場合は、Python 3.8.10 32-bit
  であることを検証してから、そのPythonでローカル仮想環境を作成します。

生成物

dist\HPLC_Analyzer.exe
dist\HPLC_Analyzer_Debug.exe
dist\installers\HPLC_Analyzer_Setup_1.2.4_Windows7_x86.exe

通常使用ではSetup EXEを配布・インストールしてください。起動しない場合は
HPLC_Analyzer_Debug.exeをコマンドプロンプトから実行すると診断表示を確認できます。

画面描画

- Windows 7ではMatplotlibが初期値です。
- グラフ上部の「PyQtGraph画面描画」を選ぶとPyQtGraph 0.13.3へ切り替わり、
  選択はアプリ設定として次回起動後も残ります。Projectファイルには入りません。
- PyQtGraphのimport、初期化、または描画に失敗した場合は、理由を表示して
  Projectと表示範囲を保ったままMatplotlibへ戻ります。
- PNG/SVG/PDF、クリップボード、印刷、A4レポート、3D図は引き続き
  Matplotlib経路です。
- PyQtGraphをWindows 7の既定へ変更する判断は、物理Core 2実機で長時間操作、
  両Y軸、概要・詳細、全編集モード、フォールバックを確認した後に行います。

失敗した場合

1. コマンド画面を閉じる前に、最後の[ERROR]、テスト名、終了コード、Python版、
   bitness、対象パッケージ版を写真またはテキストで保存します。
2. .build-tools\python-3.8.10-install.log がある場合は削除せず保存します。
3. native crashの詳細は次のコマンドをコマンドプロンプトで実行して保存します。
   wevtutil qe Application /q:"*[System[(EventID=1000)]]" /c:5 /rd:true /f:text
4. 生成途中のHPLC_Analyzer_Debug.exeがある場合はコマンドプロンプトから実行し、
   表示された内容を保存します。
5. 本物のプリセットはテスト用設定領域とは分離され、ビルドテストでは削除・上書き
   しません。

前提条件

- Windows 7 Service Pack 1、32-bit版（build 7601）
- Windows 7の更新が適用済みであること
- 特にKB2533623相当のDLLローダー更新とSHA-2対応が必要です
- ローカルディスクに十分な空き容量があること（目安2 GB以上）

このビルドバッチはpipの --no-index を強制し、ネットワークからパッケージを
取得しません。途中で失敗した場合は画面が一時停止するため、最後の[ERROR]部分を
記録してください。
