# 分析パイプラインの仕組み化・設計書(plan_pipeline)

- 作成日: 2026-07-08
- 目的: 交通空白分析を「一度きりの手作業」から「誰の端末でも1コマンドで再現・更新できる仕組み」へ。
  あわせて、この作品の核である **「地区を選んで、その地区の交通空白がひと目で分かる」** を
  データ面から支える。
- 背景: 2026-07-08、Windows端末で `gap_map.html` を見ようとしたが**存在せず・再生成もできなかった**。
  原因は「生の入力データの入手」と「通し実行」が仕組み化されておらず、`output/`・`data/` が
  gitignoreで端末を離れると再現できないこと。ここを根治する。
- 関連: [[handover]] §7(ファイル役割)・§8(環境メモ)、[[plan_gap_map]](分析仕様の正)、
  [[plan_final_sprint]](F6/F8など)。凍結ルール([[handover]] §9)は本作業でも順守する。

---

## 1. 現状のパイプライン(棚卸し)

データの流れ(左=生の入力 → 右=成果物)。★=交通空白の核心、●=Web成果物。

```
【生の入力データ】            【スクリプト(gap_map/)】         【中間・成果物】
GTFS 9フィード ───────────→ download_gtfs.py ──────────────→ gtfs_◯◯/
  (api.gtfs-data.jp)          build_network.py ─────────────→ data/network.pkl
e-Stat 500mメッシュ人口 ┐
国土数値情報 N03(境界)┴──→ prepare_meshes.py ─────────────→ data/target_meshes.csv
国土数値情報 P04(病院)┐
OSM Overpass(スーパー)┴─→ fetch_facilities.py ────────────→ data/facilities.csv
国土数値情報 A27(学区)┐
国土数値情報 P29(学校)┴──→ make_districts.py ──────────────→ webapp/data/districts.json ●
                                                              data/districts_master.csv(人手編集)
network.pkl + target_meshes
  + facilities ─────────────→ compute_access.py ★───────────→ output/access_mesh.csv ★
access_mesh + facilities ───→ make_map.py ───────────────────→ output/gap_map.html ★
access_mesh + target_meshes → analyze_demographics.py ───────→ 高齢化率(43.2%等)
access_mesh ────────────────→ make_destinations.py ──────────→ webapp/data/destinations.json ●
districts + destinations
  + network ────────────────→ export_web_data.py ────────────→ webapp/data/timetables/*.json ● + meta.json ●
(検証)                       verify_m3.py / test_*.py ─────── 検算1〜3・単体テスト
```

### 1.1 各段の入出力と自動化の状況

| # | スクリプト | 入力 | 出力 | 自動/手動 |
|---|---|---|---|---|
| A | `download_gtfs.py` | `yamagata_gtfs_feeds.csv`(取得先一覧) | `gtfs_◯◯/`(9フィード) | **自動**(api.gtfs-data.jp・CC BY・認証不要) |
| B | `prepare_meshes.py` | e-Statメッシュ人口 + N03境界 | `data/target_meshes.csv` | エンジンは自動 / **入力DLが手動** |
| C | `fetch_facilities.py` | P04病院 + OSM Overpass | `data/facilities.csv` | OSMは自動 / **P04 DLが手動** |
| D | `make_districts.py` | A27学区 + P29学校 + target_meshes | `districts.json` ほか | エンジンは自動 / **入力DLが手動** |
| E | `build_network.py` | GTFS 9フィード | `data/network.pkl` | **自動** |
| F | `compute_access.py` ★ | network.pkl + target_meshes + facilities | `output/access_mesh.csv` ★ | **自動** |
| G | `make_map.py` | access_mesh + facilities | `output/gap_map.html` | **自動** |
| H | `analyze_demographics.py` | access_mesh + target_meshes | 高齢化率(標準出力) | **自動** |
| I | `make_destinations.py` | access_mesh | `destinations.json` | **自動**(採否は人手列) |
| J | `export_web_data.py` | districts + destinations + network | `webapp/data/*` | **自動** |

### 1.2 生の入力データ一覧(config.py / make_districts.py が指すパス)

| データ | 置き場所(config基準) | 出所 | 版 |
|---|---|---|---|
| GTFS 9フィード | `gtfs_◯◯/` | GTFSデータリポジトリ(api.gtfs-data.jp) | download_gtfs.pyが取得 |
| 500mメッシュ人口 | `data/tblT001101H5740/tblT001101H5740.txt` | e-Stat 統計GIS(2020国勢調査・M5740) | 手動DL(認証不要) |
| 行政区域 N03 | `data/N03-20230101_06_GML/N03-23_06_230101.geojson` | 国土数値情報 N03 | 手動DL |
| 医療機関 P04 | `data/P04-14_06_GML/.../P04-14_06-g_MedicalInstitution.{shp,dbf}` | 国土数値情報 P04 | 手動DL |
| 小学校区 A27 | `data/A27-16_06_GML/shape/A27-16_06.{shp,dbf}` | 国土数値情報 A27(H28。山形県版は上山市未収録) | 手動DL |
| 学校 P29 | `data/P29-21_06_GML/P29-21_06.{shp,dbf}` | 国土数値情報 P29(上山市の学区近似に使用) | 手動DL |

---

## 2. 仕組み化の弱点(再現できない3つの穴)

1. **生の政府データの入手が手動**。e-Stat・国土数値情報(N03/P04/A27/P29)を各サイトから
   人がDL・解凍・配置している。手順はhandover §8にあるが「実行できる形」ではない。
2. **全体を通す一括実行が無い**。A〜Jを人が順番に叩く。順序・依存を知らないと再現できない。
3. **生成物がgitignoreで端末に閉じる**。`data/`・`output/` はリポジトリに入らないため、
   別の端末では最初から作り直すしかない(=今日の「地図が見られない」)。

---

## 3. 仕組み化の設計(第1層 = 再現可能パイプライン)

### 3.1 生データ取得の自動化 + 出所の明文化
- `gap_map/download_gov_data.py`(新規): 国土数値情報・e-Stat の**ZIPを取得URLから
  DL→解凍→所定パスに配置**する。国土数値情報は「データセット×都道府県×年度」で
  直リンクZIPがあるので自動化可能。利用規約で自動取得が難しい分は、**正確なDL URLと
  配置先を表示して人手に誘導**(半自動)。
- `docs/data_sources.md`(新規): 全入力データの「出所URL・版・ライセンス・配置先・
  自動可否」を1枚に。**再現性と応募資料の出典表**を兼ねる。

### 3.2 一括実行ドライバ
- `gap_map/run_pipeline.py`(新規): A〜J を正しい順で実行。各段で
  - 入力の有無をチェックし、無ければ「どのデータをどこに置くか」を案内して停止、
  - 出力が既にあれば既定でスキップ(`--force` で再生成)、
  - 最後に**検算**(access_mesh の空白人口=15,418人 等、[[handover]] §4.1 と一致するか)を表示。
- これで **`python gap_map/run_pipeline.py` → 空白マップまで再現**。他自治体版も
  `config.TARGET_MUNICIPALITIES` とGTFS追加だけで回せる(横展開の土台)。

### 3.3 凍結との両立
- 凍結資産(`transit_core.py`・`build_network.py`・第1部エンジン)は**importのみ・改変しない**
  ([[handover]] §9)。ドライバは既存スクリプトを順に呼ぶだけで、分析ロジックは変えない。
- 確定版の再現時は、出力が [[handover]] §4.1 の確定数値と一致することを検算で担保する。

---

## 4. 作品の強みの強化(第2層 = 地区を選んで空白を明示)

現状、空白マップ(地域全体のFolium地図)と、地区ベースの時刻表アプリは**別物**。
これを「地区」で連結し、**地区を選ぶと、その地区の交通空白状況が分かる**ようにする。

- `export_web_data.py`(または新規の小スクリプト)で、`access_mesh.csv` から
  **地区ごとの空白指標**を集計して `webapp/data/districts.json` に付加:
  - その地区(代表メッシュ/所属メッシュ)が 空白か / 到達可能か / 隠れ空白か
  - 地区内の空白人口・高齢化率
- アプリ側:
  - **かんたんモード** 画面1/画面3に、控えめな一言(例「このあたりはバスが少ない地域です」)。
    高齢者を不安にさせない語彙([[plan_f4_ui]] の禁止語彙ルール)に翻訳して出す。
  - **しっかりモード** に地区の空白指標パネル(数値・根拠)。
  - **玄関/空白マップ**(F6の `map.html`)から、地区を選ぶとその地区にズームする導線。
- 注意: 指標の定義は [[plan_gap_map]] の指標①②・is_gap をそのまま使い、**新しい判定を
  勝手に作らない**(数値の一貫性を守る)。

---

## 5. マイルストーン案(第1層→第2層)

| ID | 内容 | 成果物 | 完成条件 |
|---|---|---|---|
| G1 | データ出所の明文化 | `docs/data_sources.md` | 全入力の出所URL・版・配置先・ライセンスが揃う |
| G2 | 生データ取得の(半)自動化 | `download_gov_data.py` | まっさらな環境で、案内に従うと全入力が揃う |
| G3 | 一括実行ドライバ | `run_pipeline.py` | 1コマンドで access_mesh・gap_map まで生成、検算が確定数値と一致 |
| G4 | 地区別の空白指標を出力 | districts.json 拡張 | 各地区に空白/到達/隠れ空白/高齢化率が入る・検算OK |
| G5 | アプリで「この地区の空白」を表示 | kantan/shikkari/map | 地区選択で空白状況が分かる・禁止語彙ゼロ・スマホ崩れなし |

推奨着手順: **G1 → G3 → G2 → G4 → G5**
(まず出所を書き G1、通し実行の骨組み G3 を先に作ると、G2 の自動化の当てどころが明確になる)

---

## 6. 開発者に確認したい点(着手前)

1. 第1層(再現)と第2層(地区×空白)の**優先順位**。今日の痛みの根治は第1層。
2. 生データ取得は「完全自動」まで目指すか、「正確な手順+半自動」で十分か
   (国土数値情報は規約次第で完全自動化が難しい場合がある)。
3. 第2層でかんたんモードに空白の一言を出すか(高齢者を不安にさせない語彙が要る)、
   しっかりモード/地図側だけに留めるか。
4. まず `output/gap_map.html` を今の端末で見たい場合は、G2/G3を待たず、
   **分析データ(data/ 各フォルダ + output/access_mesh.csv)をこの端末にコピー**すれば
   `python gap_map/make_map.py` で地図を出せる(応急策)。

---

## 7. Mac環境での続き(第2層の実データ化)★次にやること

2026-07-08のWindowsセッションで、第2層の**コードは実装・テスト済み**(G4スクリプト+
しっかりモードのG5パネル)。ただし**実データ生成は分析成果物が要る**ため未完。SSDのある
Mac環境で以下を行えば完成する。

**状況(このブランチ `claude/plan-pipeline` の到達点):**
- ✅ `gap_map/make_district_gap.py`(G4)+ 単体テスト(全31件パス)
- ✅ しっかりモードの「この地区の交通状況」パネル(G5)。かんたんには出さない
- ⚠️ `webapp/data/district_gap.json` は**Windows側の見た目確認用サンプル(仮値・未コミット)**。
  Macには存在しない。**本物を生成して置き換える**こと
- ⬜ 空白マップ本体 `webapp/gap_map.html` も未配置(F6の `map.html` から参照)

**Macでの手順:**
1. `git fetch && git checkout <このブランチ or マージ後のmain> && git pull`
2. 分析成果物が揃っているか確認(無ければ分析を再実行 or SSDから復元):
   - `data/mesh_districts.csv`(make_districts.py)
   - `output/access_mesh.csv`(compute_access.py)
   - `data/target_meshes.csv`(prepare_meshes.py)
3. **G4 実データ生成**: `python gap_map/make_district_gap.py`
   → `webapp/data/district_gap.json`。**検算**: 空白人口合計=15,418人・隠れ空白=571人と
   一致するか(標準出力に出る。[[handover]] §4.1)
4. **空白マップ生成・配置**: `python gap_map/make_map.py` → `output/gap_map.html` を
   `webapp/gap_map.html` にコピー(F6の `map.html` がここを iframe 参照する)
5. **確認**: しっかりモードで各地区を開き、実数のパネルが出るか(隠れ空白の地区で赤の一言)。
   `webapp/map.html` に地図が出るか
6. **コミット**: `webapp/data/district_gap.json` と `webapp/gap_map.html` を追加
   (どちらも「生成物だがコミットする」webapp例外)。push
7. 余力があれば第1層(G1〜G3: data_sources.md・取得半自動化・run_pipeline.py)へ

**注意:** かんたんモードには空白を出さない(開発者方針)。指標定義は compute_access の
結果をそのまま使い、新しい判定を作らない([[plan_gap_map]] の is_gap・指標①②)。
