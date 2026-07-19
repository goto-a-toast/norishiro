# -*- coding: utf-8 -*-
"""R3: 他の市区町村でデータ一式を作るための対話式ウィザード(docs/plan_region_kit.md §3)。

つかいかた(プロジェクトのルートで):
    python3 gap_map/setup_region.py            … 質問に答えて data/region.json を作る
    python3 gap_map/setup_region.py --run      … 設定済みの地域で、生成工程を順に実行する

なにをするものか:
  このプロジェクトは「山形市・上山市」用に作られたが、地域固有の設定は
  data/region.json に外出ししてある(R1・R2)。このウィザードは、
  プログラミングに不慣れな人でも region.json を作れるように、
  日本語の質問と説明で1歩ずつ案内する。

  質問に答えると:
    1. data/region.json ができる(対象市町村・フィード・地区分け方式・分析日)
    2. 手動でダウンロードが必要なデータ(N03・人口メッシュ・P04)の
       ファイル名と入手先を具体的に案内し、置けたかを確認する
    3. 実行すべきコマンドの一覧を順に表示する(--run なら1工程ずつ実行してくれる)

設計メモ:
  - 対話部分(input)と、判定・組み立てのロジックは分けてある
    (ロジック側は test_setup_region.py で単体テストする)
  - 山形の既定値に戻したいときは data/region.json を消すだけ(region.pyの仕組み)
"""
import datetime
import json
import subprocess
import sys
from pathlib import Path

import config  # noqa: F401  (DATA_DIRの位置決めに使う)

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
REGION_JSON = DATA_DIR / "region.json"
OPERATORS_MASTER_CSV = DATA_DIR / "operators_master.csv"

# 都道府県名 → JISコード(2桁)。N03・P04のファイル名に使われている
PREF_CODES = {
    "北海道": "01", "青森県": "02", "岩手県": "03", "宮城県": "04", "秋田県": "05",
    "山形県": "06", "福島県": "07", "茨城県": "08", "栃木県": "09", "群馬県": "10",
    "埼玉県": "11", "千葉県": "12", "東京都": "13", "神奈川県": "14", "新潟県": "15",
    "富山県": "16", "石川県": "17", "福井県": "18", "山梨県": "19", "長野県": "20",
    "岐阜県": "21", "静岡県": "22", "愛知県": "23", "三重県": "24", "滋賀県": "25",
    "京都府": "26", "大阪府": "27", "兵庫県": "28", "奈良県": "29", "和歌山県": "30",
    "鳥取県": "31", "島根県": "32", "岡山県": "33", "広島県": "34", "山口県": "35",
    "徳島県": "36", "香川県": "37", "愛媛県": "38", "高知県": "39", "福岡県": "40",
    "佐賀県": "41", "長崎県": "42", "熊本県": "43", "大分県": "44", "宮崎県": "45",
    "鹿児島県": "46", "沖縄県": "47",
}


# ===============================================================
# ロジック(テスト対象。inputは使わない)
# ===============================================================
def primary_mesh_codes_from_bbox(min_lat, max_lat, min_lon, max_lon) -> list:
    """緯度経度の範囲を覆う1次メッシュ(4桁)の一覧を返す。
    1次メッシュ = 緯度 2/3度 × 経度 1度 のマス目。コードの前2桁=緯度×1.5、
    後2桁=経度−100(meshcode.py の変換式の逆算)。
    e-Statの人口メッシュはこの1次メッシュ単位で配布されるので、
    「どのファイルをダウンロードすべきか」がこれで分かる"""
    codes = []
    lat_idx = int(min_lat * 1.5)
    while lat_idx <= int(max_lat * 1.5):
        lon_idx = int(min_lon) - 100
        while lon_idx <= int(max_lon) - 100:
            codes.append(f"{lat_idx:02d}{lon_idx:02d}")
            lon_idx += 1
        lat_idx += 1
    return codes


def _walk_coords(obj, out: list):
    """GeoJSONのcoordinates(何重にも入れ子のリスト)から (lon, lat) を全部集める"""
    if isinstance(obj, (list, tuple)):
        if len(obj) >= 2 and all(isinstance(v, (int, float)) for v in obj[:2]):
            out.append((float(obj[0]), float(obj[1])))
        else:
            for item in obj:
                _walk_coords(item, out)


def bbox_of_municipalities(n03_path: Path, names: list) -> tuple:
    """N03(行政区域GeoJSON)から、対象市町村の範囲(min_lat, max_lat, min_lon, max_lon)を
    求める。見つからない市町村名があれば分かるように SystemExit で止まる"""
    n03 = json.loads(Path(n03_path).read_text(encoding="utf-8"))
    found = set()
    coords = []
    for feature in n03.get("features", []):
        muni = feature.get("properties", {}).get("N03_004")
        if muni in names:
            found.add(muni)
            _walk_coords(feature.get("geometry", {}).get("coordinates", []), coords)
    missing = [n for n in names if n not in found]
    if missing:
        raise SystemExit(f"N03に見つからない市町村があります: {missing}\n"
                         f"  名前の表記(「市」「町」「村」まで含める)を確認してください")
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return min(lats), max(lats), min(lons), max(lons)


def find_data_files(data_dir: Path) -> dict:
    """data/ の下から、地図・統計データを名前のパターンで探す。
    見つかったものは region.json に自動記入し、無いものはダウンロード案内を出す"""
    def rel(p):
        return str(p.relative_to(data_dir)).replace("\\", "/")
    found = {}
    n03 = sorted(data_dir.glob("**/N03*.geojson"))
    if n03:
        found["n03_geojson"] = rel(n03[0])
    p04 = sorted(data_dir.glob("**/P04*MedicalInstitution.shp"))
    if p04:
        found["p04_dir"] = rel(p04[0].parent)
        found["p04_shp"] = p04[0].name
        found["p04_dbf"] = p04[0].with_suffix(".dbf").name
    pop = sorted(data_dir.glob("tblT001101H*/tblT001101H*.txt"))
    if pop:
        found["pop_mesh_files"] = [rel(p) for p in pop]
    a27 = sorted(data_dir.glob("**/A27*.shp"))
    if a27:
        found["a27_shp"] = rel(a27[0])
        found["a27_dbf"] = rel(a27[0].with_suffix(".dbf"))
    p29 = sorted(data_dir.glob("**/P29*.shp"))
    if p29:
        found["p29_shp"] = rel(p29[0])
        found["p29_dbf"] = rel(p29[0].with_suffix(".dbf"))
    return found


def next_weekday_dates(target_date: str) -> dict:
    """分析日(平日)から、土曜・日曜の代表日(その週末)を機械的に決める"""
    d = datetime.date(int(target_date[:4]), int(target_date[4:6]), int(target_date[6:8]))
    sat = d + datetime.timedelta(days=(5 - d.weekday()) % 7 or 7)
    sun = sat + datetime.timedelta(days=1)
    return {"weekday": target_date,
            "saturday": sat.strftime("%Y%m%d"),
            "sunday_holiday": sun.strftime("%Y%m%d")}


def feeds_valid_until(feed_dirs: list) -> str:
    """各フィードの calendar.txt の end_date の最小値 = 全体の実質的な有効期限。
    読めないフィードは飛ばす(1つも読めなければ空文字)"""
    import pandas as pd
    ends = []
    for d in feed_dirs:
        cal = Path(d) / "calendar.txt"
        if cal.exists():
            try:
                df = pd.read_csv(cal, dtype=str)
                if "end_date" in df.columns and len(df):
                    ends.append(df["end_date"].max())
            except Exception:
                pass
    return min(ends) if ends else ""


def build_region_config(answers: dict, found_files: dict) -> dict:
    """質問の答え+見つかったファイルから region.json の中身を組み立てる。

    大事な点: 山形の既定値のうち「別の地域に引き継いではいけないもの」
    (基幹病院・まちなかスポット・検算の期待値など)は、空でも必ず明示的に
    書き出す(書かないと region.py が山形の値で埋めてしまうため)"""
    ref_dates = next_weekday_dates(answers["target_date"])
    cfg = {
        "region_name": answers["region_name"],
        "prefecture": answers["prefecture"],
        "target_municipalities": answers["municipalities"],
        "gtfs_feed_dirs": answers["feed_dirs"],
        "gtfs_feeds_csv": answers.get("gtfs_feeds_csv", ""),
        "reference_feed": answers["reference_feed"],
        "target_date": answers["target_date"],
        "reference_dates": ref_dates,
        "date_table_start": answers["target_date"][:6] + "01",
        "valid_until": answers.get("valid_until", ""),
        "district_methods": answers["district_methods"],
        # 山形の値を引き継いではいけないもの(空でも明示的に書く)
        "core_hospitals": answers.get("core_hospitals", []),
        "town_spots": answers.get("town_spots", []),
        "expected": {},
    }
    cfg.update(found_files)   # 見つかった地図・統計データのパス
    return cfg


# 生成工程の一覧(--run の実行順。説明は「今なにをしているか」を1〜2行で)
PIPELINE_STEPS = [
    ("prepare_meshes.py",
     "国勢調査の500mメッシュ人口とN03(市町村の境界)を突き合わせて、\n"
     "  対象市町村の「人が住んでいるマス目」の一覧(target_meshes.csv)を作ります"),
    ("make_districts.py",
     "マス目を「地区」(かんたんモードで選ぶ出発地)にまとめます。\n"
     "  方式は region.json の district_methods(学区ポリゴン/最寄り小学校/市町村1地区)"),
    ("fetch_facilities.py",
     "病院(国土数値情報P04)とスーパー(OpenStreetMap)の場所一覧を作ります。\n"
     "  ※インターネット接続が必要です(OpenStreetMapに問い合わせるため)"),
    ("compute_access.py",
     "いよいよ計算本体。各マス目から病院・スーパーへ「バス+徒歩で何分か」を\n"
     "  自作エンジンで計算します(いちばん時間がかかる工程。数分〜数十分)"),
    ("make_destinations.py",
     "計算結果から「行き先マスタ」(かんたんモードの行き先ボタン)の候補を作ります。\n"
     "  ★終わったら data/facilities_master.csv の採否(adopt列)とかなを人間が確認"),
    ("make_district_gap.py",
     "地区ごとの交通空白のようす(しっかりモードの表示用)を集計します"),
    ("make_mesh_index.py",
     "GPSで「いまいる場所の地区」を正しく判定するための索引を作ります"),
    ("export_web_data.py",
     "仕上げ。全地区×全行き先の時刻表JSONを生成します(数十分かかります)。\n"
     "  終わったら webapp/ をブラウザで開いて動作確認できます"),
    ("export_karte_data.py",
     "モビリティ・カルテ(住所→移動のようすカード)のデータを作ります"),
]


# ===============================================================
# 対話部分(ここから下はinputを使う。ロジックは上の関数に寄せてある)
# ===============================================================
def _ask(prompt: str, default: str = "") -> str:
    tail = f"(空Enterで {default})" if default else ""
    ans = input(f"  → {prompt}{tail}: ").strip()
    return ans or default


def _say(text: str):
    print(text)


def wizard():
    _say("=" * 62)
    _say(" 全国展開ウィザード: あなたの地域のバス時刻表データを作ります")
    _say("=" * 62)
    _say("")
    _say("このウィザードは、質問に答えるだけで地域設定(data/region.json)を")
    _say("作ります。各ステップで「今なにを設定しているか」を説明します。")
    _say("途中でやめても大丈夫(もう一度実行すればやり直せます)。")
    _say("山形版に戻したいときは data/region.json を削除するだけです。")
    _say("")

    # ---- ステップ1: 地域 ----
    _say("--- ステップ1/5: 対象の地域 ---")
    _say("どの市町村の時刻表を作るかを決めます。複数の市町村をまとめて")
    _say("1つのアプリにできます(山形版は山形市+上山市の2市)。")
    prefecture = _ask("都道府県名(例: 山形県)")
    while prefecture not in PREF_CODES:
        _say(f"  「{prefecture}」は都道府県名として見つかりません(「県」「都」「府」まで入れてください)")
        prefecture = _ask("都道府県名(例: 山形県)")
    munis_raw = _ask("対象の市町村名(複数はカンマ区切り。例: 天童市,東根市)")
    municipalities = [m.strip() for m in munis_raw.split(",") if m.strip()]
    region_name = _ask("この地域の呼び名(ログや画面の説明に使います)", "・".join(municipalities))

    # ---- ステップ2: GTFSフィード ----
    _say("")
    _say("--- ステップ2/5: バスの時刻表データ(GTFS) ---")
    _say("GTFSは全国のバス事業者が公開している時刻表の標準形式です。")
    _say("「GTFSデータリポジトリ」(https://gtfs-data.jp/)で地域名を検索し、")
    _say("ZIPをダウンロード→展開して、このプロジェクトの直下に")
    _say("「gtfs_事業者名」というフォルダ名で置いてください(例: gtfs_天童市)。")
    gtfs_dirs = sorted(p.name for p in PROJECT_ROOT.glob("gtfs_*") if p.is_dir())
    if gtfs_dirs:
        _say(f"いま見つかっているフォルダ: {', '.join(gtfs_dirs)}")
    else:
        _say("※まだ gtfs_◯◯ フォルダが見つかりません。先に置いてから再実行してください")
        return
    feeds_raw = _ask("使うフォルダ名(カンマ区切り。順番がそのまま画面の事業者の並びになります)",
                     ",".join(gtfs_dirs))
    feed_dirs = [f.strip() for f in feeds_raw.split(",") if f.strip()]
    bad = [f for f in feed_dirs if not (PROJECT_ROOT / f).is_dir()]
    if bad:
        _say(f"※見つからないフォルダがあります: {bad} 。置いてから再実行してください")
        return
    _say("")
    _say("ダイヤ種別(平日/土曜/日祝)の自動判定は、祝日の例外を calendar_dates.txt に")
    _say("いちばん丁寧に書いている事業者を「基準」にします(山形版は山交バス)。")
    reference_feed = _ask("基準にするフィード(gtfs_を除いた名前)",
                          feed_dirs[0].removeprefix("gtfs_"))

    # ---- ステップ3: 地図・統計データ ----
    _say("")
    _say("--- ステップ3/5: 地図・統計データ(手動ダウンロード) ---")
    pref_code = PREF_CODES[prefecture]
    _say("3つのデータが必要です。data/ の下に置いてください(フォルダ名はZIPのまま展開でOK):")
    _say(f" (1) N03 行政区域(市町村の境界): 国土数値情報のサイトで「N03 {prefecture}」を")
    _say(f"     ダウンロード(GeoJSON形式)。https://nlftp.mlit.go.jp/ksj/ → 行政区域(N03)")
    _say(f" (2) P04 医療機関: 同じサイトで「P04 {prefecture}」(Shapefile形式)")
    _say(f" (3) 国勢調査500mメッシュ人口: e-Stat 統計GIS(https://www.e-stat.go.jp/gis)で")
    _say(f"     「国勢調査 2020年 4次メッシュ(500m) 人口及び世帯」→ 該当の1次メッシュ番号")
    _say(f"     ※必要な番号はN03を置いたあとに自動計算して案内します")
    input("  データを置いたら Enter を押してください(あとで置く場合もEnterで進めます)... ")

    found = find_data_files(DATA_DIR)
    if "n03_geojson" in found:
        _say(f"  ✓ N03: data/{found['n03_geojson']}")
        try:
            bbox = bbox_of_municipalities(DATA_DIR / found["n03_geojson"], municipalities)
            codes = primary_mesh_codes_from_bbox(*bbox)
            need_files = [f"tblT001101H{c}" for c in codes]
            _say(f"  → 対象地域を覆う1次メッシュ番号: {', '.join(codes)}")
            _say(f"    e-Statでダウンロードするファイル: {', '.join(need_files)}(zipを展開してdata/へ)")
        except SystemExit as e:
            _say(f"  ※{e}")
    else:
        _say("  ✗ N03がまだ見つかりません(置いたら再実行してください)")
    _say(f"  {'✓' if 'p04_shp' in found else '✗'} P04(医療機関)")
    _say(f"  {'✓' if 'pop_mesh_files' in found else '✗'} 500mメッシュ人口"
         + (f": {len(found.get('pop_mesh_files', []))}ファイル" if 'pop_mesh_files' in found else ""))

    # ---- ステップ4: 地区分けの方式 ----
    _say("")
    _say("--- ステップ4/5: 地区分けの方式 ---")
    _say("「地区」は、かんたんモードで高齢者が最初に選ぶ出発地のまとまりです。")
    _say("市町村ごとに次の3方式から選べます:")
    _say("  1 = 小学校区ポリゴン(A27)で分ける … いちばん正確。A27にその市町村が")
    _say("      収録されている場合のみ(収録は市町村によりまちまち)")
    _say("  2 = 最寄りの小学校(P29)で近似   … A27に無い市町村向け")
    _say("  3 = 市町村ぜんたいで1地区        … 学区データが無くてもOKな代替。")
    _say("      広すぎる場合はあとで自動分割(make_subdistricts.py)できます")
    if "a27_shp" not in found:
        _say("※A27がdata/に無いので、いまは 2 か 3 を選んでください(A27を置けば1も可)")
    district_methods = {}
    method_map = {"1": "a27_polygon", "2": "p29_nearest_school", "3": "municipality"}
    for m in municipalities:
        c = _ask(f"{m} の方式(1/2/3)", "3")
        district_methods[m] = method_map.get(c, "municipality")

    # ---- ステップ5: 分析日 ----
    _say("")
    _say("--- ステップ5/5: 分析する日 ---")
    _say("時刻表と到達時間は「特定の1日」のダイヤで計算します。フィードの有効期間内で、")
    _say("祝日でない平日(できれば水曜)を選んでください(週末ダイヤの影響を避けるため)。")
    target_date = _ask("分析日(YYYYMMDD形式。例: 20260610)")
    while len(target_date) != 8 or not target_date.isdigit():
        target_date = _ask("YYYYMMDDの8桁で入れてください(例: 20260610)")
    valid_until = feeds_valid_until([PROJECT_ROOT / f for f in feed_dirs])
    if valid_until:
        _say(f"  → フィードのcalendar.txtから、有効期限は {valid_until} と読み取れました")
    else:
        valid_until = _ask("時刻表の有効期限(YYYYMMDD。calendar.txtのend_dateの最小値)", target_date)

    # ---- 書き出し ----
    answers = {
        "region_name": region_name, "prefecture": prefecture,
        "municipalities": municipalities, "feed_dirs": feed_dirs,
        "reference_feed": reference_feed, "target_date": target_date,
        "valid_until": valid_until, "district_methods": district_methods,
    }
    cfg = build_region_config(answers, found)
    REGION_JSON.parent.mkdir(parents=True, exist_ok=True)
    REGION_JSON.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    _say("")
    _say(f"✓ 地域設定を書き出しました: {REGION_JSON}")

    ensure_operator_rows(feed_dirs)

    _say("")
    _say("=== つぎにやること ===")
    _say("以下のコマンドを上から順に実行します(このウィザードに任せるなら --run):")
    _say("")
    for script, desc in PIPELINE_STEPS:
        _say(f"  python3 gap_map/{script}")
    _say("")
    _say("1工程ずつ説明つきで実行するには: python3 gap_map/setup_region.py --run")
    _say("(事業者の表示名・電話番号は data/operators_master.csv をあとで直せます)")


def ensure_operator_rows(feed_dirs: list):
    """選んだフィードが operators_master.csv に無ければ、空欄の行を足しておく
    (表示名・電話は人間があとで記入する。既存の行は触らない)"""
    import csv as _csv

    import pandas as pd
    feeds = [f.removeprefix("gtfs_") for f in feed_dirs]
    if OPERATORS_MASTER_CSV.exists():
        df = pd.read_csv(OPERATORS_MASTER_CSV, dtype=str).fillna("")
    else:
        df = pd.DataFrame(columns=["feed", "name", "desk", "tel", "memo"])
    known = set(df["feed"])
    new_rows = [{"feed": f, "name": f, "desk": "", "tel": "",
                 "memo": "ウィザードが追加。表示名・窓口・電話を確認して記入してください"}
                for f in feeds if f not in known]
    if new_rows:
        df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
        df.to_csv(OPERATORS_MASTER_CSV, index=False, quoting=_csv.QUOTE_NONNUMERIC)
        print(f"✓ data/operators_master.csv に {len(new_rows)}事業者の行を足しました"
              f"(表示名・電話番号をあとで記入してください)")


def run_pipeline():
    """--run: 生成工程を1つずつ「説明→確認→実行」で進める"""
    if not REGION_JSON.exists():
        print("※ data/region.json がまだありません。先にウィザード本体")
        print("  (python3 gap_map/setup_region.py)で地域設定を作ってください。")
        print("  ※山形版をそのまま再生成したい場合は、各スクリプトを直接実行してください")
        return
    cfg = json.loads(REGION_JSON.read_text(encoding="utf-8"))
    print(f"=== {cfg.get('region_name', '(名称未設定)')} のデータ生成を始めます ===")
    print("各工程の前に説明を出します。Enter=実行 / s=飛ばす / q=中止")
    for i, (script, desc) in enumerate(PIPELINE_STEPS, 1):
        print()
        print(f"--- 工程 {i}/{len(PIPELINE_STEPS)}: {script} ---")
        print(f"  {desc}")
        ans = input("  Enter=実行 / s=飛ばす / q=中止 > ").strip().lower()
        if ans == "q":
            print("中止しました(ここまでの結果は残っています。--run で再開できます)")
            return
        if ans == "s":
            continue
        result = subprocess.run([sys.executable, str(Path(__file__).parent / script)])
        if result.returncode != 0:
            print(f"※ {script} がエラーで止まりました。表示されたメッセージを確認してください。")
            print("  直したら、もう一度 --run で再開できます(完了済みの工程は s で飛ばせます)")
            return
    print()
    print("=== 全工程が終わりました ===")
    print("ブラウザでの確認: python3 -m http.server 8000 を実行して")
    print("http://localhost:8000/webapp/ を開いてください")


if __name__ == "__main__":
    if "--run" in sys.argv:
        run_pipeline()
    else:
        wizard()
