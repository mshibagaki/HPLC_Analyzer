# 配布と更新の運用

このファイルは、HPLC Analyzerを実際に配布・運用するときの手順書です。読み手は2種類います。

- **利用者**：測定PCや解析PCでHPLC Analyzerを使う人。「[利用者がダウンロードするファイル](#利用者がダウンロードするファイル)」から「[アンインストール](#アンインストール)」までが対象です。
- **配布担当**：リリース資産を生成し、利用者へ届ける人。「[配布担当の手順](#配布担当の手順1リリースあたり)」以降が対象です。

ビルドそのものの詳細は`README.md`、リリース手順の正本は`.github/RELEASE_PROCESS.md`と`.github/RELEASE_CHECKLIST.md`、外部・実機律速は`VALIDATION_BLOCKERS.md`にあります。このファイルはそれらを運用の順序でつなぎ直したもので、規則を上書きしません。

## 現在の状態

**現時点で、利用者がダウンロードできる公開ファイルはありません。**

| 項目 | 状態 |
|---|---|
| GitHub Releases | 0件 |
| SemVer tag | 0件（`v1.2.4`を含め未作成） |
| リポジトリ公開設定 | private |
| `hplc_app/version.py`の`APP_VERSION` | `1.3.0-dev.1`（開発版。tagやReleaseを意味しません） |

`README.md`が記載する「最新の公開Stable版は`v1.2.4`」は、設計と履歴の記述であり、ダウンロードできる資産の存在を意味しません。したがって今日時点で機能する配布経路は、配布担当が手元で生成したセットアップEXEを共有フォルダーやUSBで直接渡す方法だけです。

公開の配布チャネルは未決定で、Issue #190で追跡します。以下の手順は、そのチャネルが決まった後の正規の運用として記述しています。

## 利用者がダウンロードするファイル

正規のRelease資産は3種類とチェックサム1件ですが、**利用者が触るのは自分のOSに合うセットアップEXE 1本だけ**です。

| ファイル | 対象 | 用途 |
|---|---|---|
| `HPLC_Analyzer_Setup_<version>_Windows11_x64.exe` | Windows 11 64-bitの利用者 | この1本をダウンロードして実行 |
| `HPLC_Analyzer_Setup_<version>_Windows7_x86.exe` | Windows 7 SP1 32-bitの利用者 | この1本をダウンロードして実行 |
| `HPLC_Analyzer_<version>_Windows7_Offline_Build.zip` | 配布担当のみ | Windows 7実機でEXEを生成するためのビルド資材。**利用者には渡しません** |
| `SHA256SUMS.txt` | 全員 | 転送前後の完全性確認 |

`<version>`は`hplc_app/version.py`の`APP_VERSION`から自動生成されます。配布先PCにPython、Qt、Inno Setup、ソースコード、インターネット接続は必要ありません。

Windows 7版のZIPをセットアップEXEと取り違えないでください。ZIPはビルド材料であり、これを利用者のPCへ展開してもアプリケーションはインストールされません。

## インストール手順

1. 対象PCのOSに合うセットアップEXEを1本だけコピーします。
2. `SHA256SUMS.txt`がある場合は、実行前にSHA-256を照合します。
3. セットアップEXEをダブルクリックし、日本語または英語を選び、ライセンスに同意して画面に従います。
4. 完了画面でHPLC Analyzerを起動できます。

インストーラーの動作：

- 管理者権限を要求し、既定で`C:\Program Files\HPLC Analyzer`へインストールします。
- アプリ本体、`README.txt`、ライセンス、第三者ライセンス表記、サンプルデータ3件を配置します。
- スタートメニューに`HPLC Analyzer`と`HPLC Analyzer README`を登録します。デスクトップショートカットは既定OFFで、セットアップ画面で選べます。
- 起動中のHPLC Analyzerがある場合は終了を求めます。

OS別の注意：

- **Windows 11版はx64専用**です。32-bit環境では実行できません。
- **Windows 7版はSP1かつ32-bit専用**です。64-bit Windowsでは実行を拒否します。Windows 7 64-bit環境は現在の配布対象に含みません。
- Windows 7版はスタートメニューに`HPLC Analyzer (Debug)`も登録します。これは通常版が起動しない場合に起動時エラーを確認するための診断用で、通常の解析には使いません。
- Windows 7版はMicrosoft Visual C++ 2015-2019 x86ランタイムを内包し、対象PCのバージョンが14.29未満または未導入の場合だけ自動的に導入します。完全オフラインで完結します。
- Windows 7 SP1側のOS更新（SHA-2対応、KB2533623相当）は事前に適用してください。
- コード署名証明書が未取得のため、セットアップEXEは「発行元不明」と表示されます。ネットワークから隔離した測定PCへ搬入する前に、別PCでウイルススキャンしてください。

## 更新手順

**アンインストールは不要です。新しいセットアップEXEを上書き実行するだけです。**

1. HPLC Analyzerを終了します。
2. 新しい`HPLC_Analyzer_Setup_<新version>_Windows11_x64.exe`または`..._Windows7_x86.exe`を実行します。
3. 画面に従います。同一の`AppId`をOS版・バージョンを通じて固定しているため、同じ製品として更新インストールされます。

### 更新・アンインストールで保持されるデータ

利用者のデータはすべてインストールフォルダーの外にあるため、更新でもアンインストールでも削除されません。

| データ | 保存場所 |
|---|---|
| 条件プリセット、グラジエントプリセット | `%APPDATA%\Research Tools\HPLC Analyzer\presets.json` |
| 画面言語、読み込み／保存／直近フォルダー、研究室DBパス、画面描画品質、図の出力形式、更新確認設定 | Application settings（QSettings、組織`Research Tools` / アプリ`HPLC Analyzer`） |
| `.hplcproj`プロジェクト、元GCD・ASCIIデータ、研究室共通データベース | 利用者が指定した任意の場所 |

この保持契約は、リリース前にclean install、旧版からのupgrade、uninstall、reinstallの各操作でbyte比較により確認します。手順は`.github/INSTALLER_UPGRADE_TEST.md`、記録様式は`.github/INSTALLER_UPGRADE_EVIDENCE.md`です。Windows 11とWindows 7の両方が必須です。

プロジェクト形式やスキーマが変わるリリースでは、更新前のバックアップ手順と、新版で保存したファイルが旧版で開けない可能性の警告をリリースノートに明記します。この条件は`.github/RELEASE_PROCESS.md`が定めます。

### アプリ内の更新確認

`ヘルプ → 更新を確認…`で、公開GitHub ReleasesのmetadataだけをHTTPSで確認します。

- 新しいStable版があれば「公式Releaseページを開きますか？」と尋ね、既定ブラウザーでReleaseページを開きます。
- **アプリケーションはインストーラーのダウンロードも起動も行いません。** 更新は常に、Releaseページから対象OSのセットアップEXEを手動でダウンロードして実行する操作です。検証用のダウンロード基盤は実装済みですが、署名identityが未取得のため利用者の操作には接続していません。
- 起動時の自動確認は既定ONで、環境設定でOFFにできます。自動確認では、最新版・オフライン・proxy・rate limit・壊れた応答のいずれも通常操作へ通知しません。
- draftとprereleaseは除外し、SemVerと公式リポジトリ配下のRelease URLを検証します。

**リポジトリがprivateである間、この確認は必ず失敗します。** 未認証でGitHub APIを参照するため、privateリポジトリでは404となり、手動確認では「更新情報を確認できませんでした」と表示されます。公開チャネルが決まるまでは、利用者へ配布時にバージョンを口頭・文書で伝える運用にしてください。ネットワークから隔離したWindows 7測定PCでは、自動確認をOFFにすることを推奨します。

### アンインストール

Windowsの「プログラムと機能」から`HPLC Analyzer <version>`を選んで実行します。上記のユーザーデータは削除されません。

v1.1.5以前に配布した単体EXEはインストーラーの管理対象ではないため、自動削除されません。混同を避ける場合は、新版の起動確認後に手動で整理してください。

## 配布担当の手順（1リリースあたり）

1. `hplc_app/version.py`の`APP_VERSION`を更新します。これが唯一の機械可読な正本で、GUI、プロジェクト／プリセット／DB、installer metadata、成果物名、Windows 7 offline archiveへ自動的に伝播します。`python scripts\read_version.py`と`python scripts\read_version.py --format windows`が成功することを確認します。
2. Windows 11 PCで`build_all_windows.bat`を実行し、Windows 11 x64セットアップEXEとWindows 7搬入用オフラインビルドZIPを生成します。
3. ZIPをUSBでWindows 7 SP1 32-bit実機へ移し、ローカルディスクへ展開して`build_windows7_offline.bat`を実行します。**Windows 7版の実行ファイルはWindows 7実機でのみ生成できます。**
4. 署名と最終ファイル名が確定してから、3ファイルだけを置いたRelease用ディレクトリでチェックサムと整合性を確認します。

   ```bat
   python scripts\release_consistency.py source --release-version v<version> --project-schema <schema>
   python scripts\release_checksums.py write --release-dir dist\release
   python scripts\release_consistency.py assets --release-version v<version> --project-schema <schema> --release-dir dist\release
   ```

5. `.github/RELEASE_CHECKLIST.md`の物理ゲートを実施します。clean install、旧版からのupgrade、uninstall、reinstall、データ保持のbyte比較を**Windows 11とWindows 7の両方**で記録します。Windows 7の合格にはWindows 7 SP1 32-bit / Core 2実機での記録が必要です。
6. 全ゲート通過後、annotated tagを作成し、GitHub Releaseを公開します。RCは pre-release フラグをONにします。**tagの作成・push・Release公開には人間の承認が必要です。**
7. 公開後、clean directoryへ再ダウンロードして`python scripts\release_checksums.py verify --release-dir <directory>`で再検証します。

RCからStableへ昇格する手順、Stableゲートの一覧、証跡様式は`.github/RELEASE_PROCESS.md`が正本です。CI成功は、Windows 11実機確認やWindows 7完全オフラインビルド・実機確認の代替にはなりません。

## 未決定事項と外部律速

| 項目 | 状態 | 運用への影響 |
|---|---|---|
| 配布チャネル | 未決定（Issue #190） | 公開ダウンロード先が存在せず、アプリ内更新確認も機能しない |
| コード署名identity | 外部取得待ち | セットアップEXEが「発行元不明」と表示される |
| Updaterのinstaller起動 | 署名identity待ちで常に無効 | 更新は常に手動ダウンロード＋実行 |
| Windows 11 / Windows 7実機ゲート | 証跡未取得 | Stable公開の前提条件が未達 |
| Stable Release公開承認 | 人間の承認が必要 | tagとReleaseは自動化しない |

各項目の必要証跡は`VALIDATION_BLOCKERS.md`が正本です。CIやoffscreenテストの結果でこれらを通過扱いにしないでください。

## 関連文書

- `README.md` — 機能、ビルド手順、バージョン運用
- `README_Windows7_Offline.txt` — Windows 7完全オフラインビルドの実機手順
- `.github/RELEASE_PROCESS.md` — RCからStableまでのリリース手順の正本
- `.github/RELEASE_CHECKLIST.md` — リリース証跡の記録様式
- `.github/RELEASE_TEMPLATE.md` — GitHub Release本文のテンプレート
- `.github/INSTALLER_UPGRADE_TEST.md` / `.github/INSTALLER_UPGRADE_EVIDENCE.md` — 更新時のデータ保持検証
- `VALIDATION_BLOCKERS.md` — 外部・実機律速の正本
