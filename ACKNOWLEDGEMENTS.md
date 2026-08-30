# 謝辞 / Acknowledgements

HPLC Analyzer は、研究室のメンバーが実際の解析のために作った道具や手法を取り込んで
できています。ここに、機能の原型を提供してくださった方を記録します。

This project incorporates tools and methods that lab members built for their own
analysis work. This file records the people whose work became a feature.

---

## 3D クロマトグラム表示 / 3D chromatogram view

**吉川さん / Yoshikawa**

複数のクロマトグラムを 3 次元に並べて比較する図の作成手法と、その Google Colab
実装（2024-01-17 初版、2025-04-11 更新版）を提供してくださいました。系列の並べ方、
軸・目盛・線幅・アスペクト比の設定項目、単色とグラデーションの色設計、視点調整と
画像出力の流れは、この実装の設計をそのまま引き継いでいます。

HPLC Analyzer 本体への移植にあたって、データ源を CSV からプロジェクト内の
クロマトグラムに変え、描画を Matplotlib に置き換えていますが、**機能の設計は
吉川さんによるもの**です。

Yoshikawa contributed the method for arranging multiple chromatograms into a
single 3D figure, together with its Google Colab implementation (first version
2024-01-17, revised 2025-04-11). The series arrangement, the axis / tick /
line-width / aspect-ratio settings, the solid-and-gradient color design, and the
viewpoint-then-export workflow are all carried over from that implementation.

The port into HPLC Analyzer changes the data source from CSV to the
chromatograms already loaded in a project, and changes the drawing backend, but
**the design of the feature is Yoshikawa's**.

---

## 記載の方針 / How this file is maintained

- 機能の原型・手法・アルゴリズムを提供してくださった方をここに記載します。
- 第三者ソフトウェアのライセンス表記は `THIRD_PARTY_NOTICES.txt` が担当します。
  この 2 つは目的が違うので、混ぜないでください。
- 氏名の表記（姓のみ／フルネーム／所属の記載）は、本人の希望を確認してから
  変更してください。確認が取れていない場合は姓のみに留めます。

- This file credits people who contributed the original design, method, or
  algorithm behind a feature.
- Third-party software license notices belong in `THIRD_PARTY_NOTICES.txt`.
  The two files have different purposes; do not merge them.
- Confirm with the person before changing how their name is written. Until
  confirmed, use the family name only.
