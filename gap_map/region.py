# -*- coding: utf-8 -*-
"""地域設定の読み込み(全国展開キットR1。docs/plan_region_kit.md §3)。

このプロジェクトを山形市・上山市以外の地域でも使えるように、
「どの市町村を対象にするか」「どのGTFSフィードを読むか」などの地域固有の設定を
1つのファイル(data/region.json)にまとめる。

しくみ(最重要ルール: 山形版の動作は1バイトも変えない):
  - data/region.json が **無ければ** このファイルの YAMAGATA_DEFAULTS(=現行の山形設定)を
    そのまま使う。既存の環境では region.json が無いので、何も変わらない
  - data/region.json が **あれば** その値で上書きする(書いたキーだけ上書き。
    書かなかったキーは山形の既定値のまま)
  - region.json は R3 の対話式ウィザード(setup_region.py)が質問に答えると作ってくれる。
    手で書いてもよい

検算の期待値("expected")について:
  山形版の検算(総人口276,482人・空白15,418人など)は分析確定時に固定した値。
  他の地域では当然値が違うので、region.json に expected が無い(または該当キーが無い)
  ときは「厳密一致チェック」ではなく「観測値の表示」に切り替える(region_expected() が
  None を返したら、呼び出し側は答え合わせをスキップして観測値だけ出す)。
"""
import copy
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
REGION_JSON = PROJECT_ROOT / "data" / "region.json"

# ===============================================================
# 山形設定(= region.json が無いときの既定値。分析確定版と完全に同じ)
# ===============================================================
YAMAGATA_DEFAULTS = {
    # 地域の呼び名(ログ・ウィザードの表示用)
    "region_name": "山形市・上山市",
    "prefecture": "山形県",

    # 対象市町村(メッシュをどの市町村分に絞るか)
    "target_municipalities": ["山形市", "上山市"],

    # GTFSフィードのディレクトリ名(プロジェクトルート直下)。先頭の gtfs_ を含む。
    # 順序は meta.json の operators 配列の添字(便レコードの op/op2)になるので変えない
    "gtfs_feed_dirs": [
        "gtfs_山形交通", "gtfs_上山市", "gtfs_山形市", "gtfs_天童市", "gtfs_山辺町",
        "gtfs_中山町", "gtfs_東根市", "gtfs_南陽市", "gtfs_寒河江市",
    ],
    # フィード取得先一覧CSV(download_gtfs.py が読む。gtfs-data.jp のURL一覧)
    "gtfs_feeds_csv": "yamagata_gtfs_feeds.csv",

    # ダイヤ種別(平日/土曜/日祝)の判定の基準にするフィード名(gtfs_を除いた名前)。
    # 祝日・お盆の例外を calendar_dates.txt に実際にコード化している事業者を選ぶ
    "reference_feed": "山形交通",

    # 分析する日と、ダイヤ種別ごとの代表日
    "target_date": "20260610",
    "reference_dates": {
        "weekday": "20260610",
        "saturday": "20260613",
        "sunday_holiday": "20260614",
    },
    # date_table(きょうのダイヤ判定表)の収録範囲と有効期限
    "date_table_start": "20260701",
    "valid_until": "20260930",

    # 手動ダウンロードする地図・統計データの置き場所(data/ からの相対パス)
    "pop_mesh_files": ["tblT001101H5740/tblT001101H5740.txt"],
    "n03_geojson": "N03-20230101_06_GML/N03-23_06_230101.geojson",
    "p04_dir": "P04-14_06_GML/P04-14_06_GML",
    "p04_shp": "P04-14_06-g_MedicalInstitution.shp",
    "p04_dbf": "P04-14_06-g_MedicalInstitution.dbf",

    # 地区分け(make_districts.py)の方式を市町村ごとに選ぶ(R2):
    #   a27_polygon        … 国土数値情報A27の小学校区ポリゴン(あれば最良)
    #   p29_nearest_school … 国土数値情報P29の小学校への最寄り割り当て(近似)
    #   municipality       … 市町村ぜんたいで1地区(学区データが無い地域の代替。
    #                        広すぎる地区は make_subdistricts.py の自動分割で割れる)
    # ここに書かれていない市町村は municipality 方式になる
    "district_methods": {"山形市": "a27_polygon", "上山市": "p29_nearest_school"},
    # A27(学区ポリゴン)・P29(学校点)の置き場所(data/ からの相対パス。県ごとにファイル名が変わる)
    "a27_shp": "A27-16_06_GML/shape/A27-16_06.shp",
    "a27_dbf": "A27-16_06_GML/shape/A27-16_06.dbf",
    "p29_shp": "P29-21_06_GML/P29-21_06.shp",
    "p29_dbf": "P29-21_06_GML/P29-21_06.dbf",

    # 行き先マスタ(make_destinations.py)の地域固有リスト
    # core_hospitals: 遠くても必ず行き先に採用する基幹病院
    "core_hospitals": ["山形県立中央病院", "国立大学法人山形大学医学部附属病院",
                       "山形市立病院済生館"],
    # town_spots: 「まちなか」カテゴリの行き先(名前・市町村・座標・目印メモ)
    "town_spots": [
        {"name": "山形駅", "municipality": "山形市",
         "lat": 38.249105, "lon": 140.328658, "memo": "gtfs_山形市:1_01(山形駅前)"},
        {"name": "山形市役所", "municipality": "山形市",
         "lat": 38.255061, "lon": 140.340318, "memo": "gtfs_山形市:42_01(市役所前)"},
        {"name": "かみのやま温泉駅", "municipality": "上山市",
         "lat": 38.152465, "lon": 140.278184, "memo": "gtfs_上山市:1_01(かみのやま温泉駅前)"},
        {"name": "上山市役所", "municipality": "上山市",
         "lat": 38.149764, "lon": 140.267745, "memo": "gtfs_上山市:24_01(市役所前)"},
        {"name": "上山城", "municipality": "上山市",
         "lat": 38.155768, "lon": 140.277004, "memo": "gtfs_山形交通:S060100000600100(上山城口)"},
    ],

    # 検算の期待値(分析確定版で固定した山形の値)。
    # 他地域では region.json にこのキーを書かない → region_expected() が None を返し、
    # 各スクリプトは「答え合わせ」の代わりに観測値の表示に切り替える
    "expected": {
        "total_population": 276482,     # 対象2市の総人口
        "mesh_count": 817,              # 人口>0のメッシュ数
        "gap_population": 15418,        # 空白メッシュの人口合計
        "hidden_gap_population": 571,   # 隠れ空白の人口合計
        "date_checks": [                # date_table の検算(日付: 期待されるダイヤ種別)
            ["2026-07-20", "sunday_holiday"],   # 海の日
            ["2026-08-13", "sunday_holiday"],   # お盆
            ["2026-08-14", "sunday_holiday"],
            ["2026-08-15", "sunday_holiday"],
        ],
    },
}


def load(path: Path = None) -> dict:
    """地域設定を返す。region.json が無ければ山形設定そのもの。
    あれば「書いてあるキーだけ」上書きした辞書を返す(トップレベルのキー単位)。
    "_is_default" キーに「山形既定値のままかどうか」を入れる(検算の切替などに使う)"""
    if path is None:
        path = REGION_JSON
    merged = copy.deepcopy(YAMAGATA_DEFAULTS)
    if Path(path).exists():
        user = json.loads(Path(path).read_text(encoding="utf-8"))
        # 別地域のregion.jsonでは、書かれていない expected(山形の確定値)を
        # 引き継いではいけないので、まず expected を空にしてから上書きする
        merged["expected"] = {}
        for key, value in user.items():
            merged[key] = value
        merged["_is_default"] = False
    else:
        merged["_is_default"] = True
    return merged


# モジュール読み込み時に1回だけ読む(config.py などはこれを参照する)
REGION = load()


def is_default_region() -> bool:
    """山形既定値のまま動いているか(=region.jsonが無いか)。
    山形固有の検算・表示を出すかどうかの判定に使う"""
    return REGION.get("_is_default", True)


def region_expected(key: str):
    """検算の期待値を返す。設定が無ければ None(呼び出し側は観測値表示に切り替える)"""
    return REGION.get("expected", {}).get(key)
