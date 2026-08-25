# HPLC Analyzer vX.Y.Z

Verified commit: `FULL_40_CHARACTER_SHA`  
Final RC tag: `vX.Y.Z-rc.N`  
Stable tag: `vX.Y.Z`

## Highlights

- 日本語で主要な改善を簡潔に記載します。

## Added

- 追加機能を記載します。

## Changed

- 互換性を維持した変更、操作変更、build/release変更を記載します。

## Fixed

- 修正した不具合と影響範囲を記載します。

## Compatibility

### Windows 11 x64

- Build/test environment:
- Physical-machine verification:
- Installer/upgrade result:

### Windows 7 SP1 32-bit

- Physical Core 2 test machine:
- Python 3.8.10 x86 / PySide2 5.15.2.1 / NumPy 1.20.3 / Matplotlib 3.7.5:
- Fully offline build/install result:

### Project compatibility

- Backward loading/migration:
- Windows 11→Windows 7 project result:
- Windows 7→Windows 11 project result:
- Raw data and scientific result invariance:

### Formats and schemas

| Contract | Version |
|---|---|
| Application | `X.Y.Z` |
| Project format major | `N` |
| Project schema | `N` |
| Preset format | `N` |
| Lab database schema | `N` |

## Known issues

- Issue / severity / impact / workaround / approver acceptanceを記載します。なければ「None」と記載します。

## Upgrade notes

- Upgrade前のbackup対象（`.hplcproj`、settings/presets、lab SQLite database）を記載します。
- Project/preset/DB schemaが変わる場合、旧版へdowngradeすると新しい版で保存・移行したデータを正しく開けない可能性があることを明記します。
- 更新インストールとdata preservationの検証結果を記載します。
- v1.3.0にはupdaterは含まれません。installerを使用して更新します。

## Security and signing

- Code-signing status: `Unsigned` / `Signed (identity and timestamp)`
- Unsignedの場合: Windowsの警告が表示される可能性があります。GitHub ReleaseのSHA-256と照合してください。

## Assets and checksums

| Asset | Size | SHA-256 |
|---|---:|---|
| `HPLC_Analyzer_Setup_X.Y.Z_Windows11_x64.exe` | | |
| `HPLC_Analyzer_Setup_X.Y.Z_Windows7_x86.exe` | | |
| `HPLC_Analyzer_X.Y.Z_Windows7_Offline_Build.zip` | | |
| `SHA256SUMS.txt` | | |

Verification evidence: `LINK`  
Release approver: `NAME / DATE`
