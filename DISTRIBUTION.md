# 配布チャネル（Distribution channel）

Updated: 2026-08-31 (Issue #190 decision)

この文書は、HPLC Analyzer の利用者が **どこから installer を入手するか** を定めた
唯一の記録です。Release の実行手順そのものは `.github/RELEASE_PROCESS.md` と
`.github/RELEASE_CHECKLIST.md`、未達の外部・物理ゲートは `VALIDATION_BLOCKERS.md`
にあり、この文書がそれらを緩めることはありません。

Issue #190 の決定に伴う文書だけの変更です。installer 定義、固定依存、Application
の挙動、project schema はいずれも変更していません。

---

## 1. 決定（Issue #190）

**採用: A + C。**

- **A — リポジトリ（および Releases）を公開する。** ネットワークに接続した
  Windows 11 PC は、公開された GitHub Releases ページから直接 installer を
  download する。これにより、既に実装済みの `ヘルプ → 更新を確認…` が設計どおり
  機能する。
- **C — ネットワークから隔離した Windows 7 測定 PC へは、オフライン搬入で配布する。**
  ファイルサーバーまたは USB 媒体へ、成果物と `SHA256SUMS.txt` を一緒に置き、
  搬入先で checksum を照合する。

A と C は排他ではありません。C は、隔離された Windows 7 実機が存在する限り、
どのチャネルを選んでも必要です。

**B（private のまま個別にアクセス権を付与）は採用しません。** 署名済みの
利用者だけが asset を取得できる一方、`hplc_app/update_check.py` は無認証要求を
行うため、更新確認は機能しないままになります。認証を伴う更新確認の設計は
存在せず、この Issue の範囲外です。

### 公開の前提として確認済みの事項

- ライセンスは MIT（`LICENSE.txt`）。第三者 license 表記は
  `THIRD_PARTY_NOTICES.txt` に集約済み。
- `AGENTS.md` の規約により、tracked file・commit message・Issue・PR に個人名を
  書かない運用が既に守られている。
- `hplc_app/update_check.py` の `DEFAULT_REPOSITORY` は
  `mshibagaki/HPLC_Analyzer` で、正規のリポジトリ名と一致する。したがって
  `html_url` の prefix 検証は、endpoint が公開された時点でそのまま正しく働く。

### 公開の実行について

リポジトリ可視性の変更、tag の作成、GitHub Release の公開は、いずれも
**人による明示的な承認が必要な外部公開行為** です。この文書は採用するチャネルを
決めただけであり、公開そのものを実行するものではありません。実行条件は
`.github/RELEASE_PROCESS.md` と `VALIDATION_BLOCKERS.md` の物理ゲートに従います。

---

## 2. 利用者に伝える download 先

### Windows 11 x64（ネットワーク接続あり）

```text
https://github.com/mshibagaki/HPLC_Analyzer/releases/latest
```

このページから次を download します。

- `HPLC_Analyzer_Setup_<version>_Windows11_x64.exe`
- `SHA256SUMS.txt`

download 後、PowerShell で checksum を照合してから実行します。

```powershell
Get-FileHash .\HPLC_Analyzer_Setup_<version>_Windows11_x64.exe -Algorithm SHA256
```

表示された hash が `SHA256SUMS.txt` の該当行と一致することを確認します。
インストール手順そのものは `README.md`「セットアップEXEでインストールする」を
参照してください。

### Windows 7 SP1 x86（ネットワークから隔離した測定 PC）

隔離機は Releases ページへ到達できません。ネットワークに接続した PC で次を
download し、ファイルサーバーまたは USB 媒体経由で搬入します。

- `HPLC_Analyzer_Setup_<version>_Windows7_x86.exe`
- 必要な場合は `HPLC_Analyzer_<version>_Windows7_Offline_Build.zip`
- `SHA256SUMS.txt`

搬入前後の手順:

1. 搬入元 PC で checksum を照合する。
2. Windows 7 はサポート終了 OS のため、搬入前に別 PC で setup EXE を
   ウイルススキャンする（`README.md` の既存の注意事項と同じ）。
3. 搬入先の Windows 7 実機で、`certutil` により再度 checksum を照合する。

```bat
certutil -hashfile HPLC_Analyzer_Setup_<version>_Windows7_x86.exe SHA256
```

Release 用ディレクトリ全体をまとめて検証する場合は、リポジトリ同梱の script を
使えます。

```bat
python scripts\release_checksums.py verify --release-dir <directory>
```

### 媒体に置く単位

ファイルサーバーまたは USB 媒体には、`SHA256SUMS.txt` を **常に成果物と同じ
ディレクトリへ同梱** します。checksum ファイルのない配布物は配らないでください。

---

## 3. `ヘルプ → 更新を確認…` の挙動

`hplc_app/update_check.py` は
`https://api.github.com/repos/mshibagaki/HPLC_Analyzer/releases` を **無認証** で
要求し、draft と prerelease を除外し、SemVer と公式リポジトリ配下の Release URL を
検証します。Application UI から installer の download や起動は行いません。

| 環境 | 挙動 | 利用者に伝えること |
|---|---|---|
| Windows 11、ネットワーク接続あり、Releases 公開後 | 設計どおり動作し、最新 Stable 版の有無を通知する | そのまま使用してよい |
| Windows 11、ネットワーク接続あり、Releases 公開前 | 有効な Stable Release が無いため「更新なし」相当の結果になる | 公開まで手動で版を比較する |
| リポジトリが private のまま | endpoint が 404 を返し、手動確認は失敗として表示される | 手動でのバージョン比較（第 4 節） |
| Windows 7 隔離機 | ネットワークが無いため必ず失敗する | 自動確認を無効化する（下記） |

### 起動後の自動確認

設定 `updates/automatic_check`（`hplc_app/settings_store.py:31`、既定 ON）は、
環境設定の「起動後にStable版の更新を自動確認する」で切り替えます。自動確認時の
失敗・offline・proxy・rate limit・壊れた応答は、通常操作へ通知されません。

- **ネットワーク接続のある Windows 11 機: 既定の ON のままにします。**
- **ネットワークから隔離した Windows 7 測定 PC: OFF にします。** 失敗が通知
  されない設計のため実害はありませんが、到達しない endpoint へ起動のたびに
  要求を出す意味がないためです。設置時のセットアップ手順に、この OFF 操作を
  含めてください。

---

## 4. 更新確認が使えない場合の手動比較

private のままの期間、および隔離された Windows 7 機では、次の手順で版を比較します。

1. 使用中の版を `ヘルプ → バージョン情報` で確認する。
2. 配布元（Releases ページ、またはファイルサーバー上の配布ディレクトリ）の
   成果物ファイル名に含まれる SemVer を確認する。成果物名は
   `HPLC_Analyzer_Setup_<version>_Windows11_x64.exe` のように、必ず版を含みます。
3. 配布側の版が新しい場合だけ、第 2 節の手順で download・照合・更新インストール
   します。更新インストールでは、プリセット・ユーザー設定・`.hplcproj`・元データ・
   研究室 DB は削除されません（`README.md` の保持契約）。

---

## 5. この決定が変えないもの

- installer 入力、固定依存、build script
- Application の挙動、project schema、数値・科学的処理
- 署名 identity と、署名済み installer の起動（`VALIDATION_BLOCKERS.md` の
  hard blocker として別に管理）
- Release の公開手順と、それに必要な物理検証の証跡
