# -*- coding: utf-8 -*-
"""
F2: 行き先マスタを作る(docs/plan_final_sprint.md §3・F2)。

「行き先」= かんたんモードで高齢者が選ぶ目的地(病院・スーパー・駅や市役所などの「まちなか」)。
凍結済みの data/facilities.csv(M2)・output/access_mesh.csv(M4)は import/読み込みのみで
改変しない(第2部の分析資産は凍結済み)。

自動抽出のやり方:
  各メッシュ(817件)の中心から一番近い施設(直線距離)を求め、「何件のメッシュから
  一番近いか」を数える。この数が多い施設ほど、多くの住民にとって「実際に使われる
  最寄り施設」である可能性が高いので、これを頻度順の目安にする。
  (注: access_mesh.csv の hospital_name/super_name 列は「施設名の文字列」なので、
  同名の店舗が複数ある場合にどの店舗が選ばれたか分からない。ここでは施設1件ずつを
  区別できるよう、facilities.csv の行(緯度経度)を単位に数え直している)

手動追加:
  - 基幹病院(頻度上位に含まれていても「抽出理由」を基幹病院に上書きする)
  - 駅・市役所・まちなかの代表施設(facilities.csvに無いのでGTFS停留所の座標を転用)

display_name(店舗名の曖昧さ解消):
  OSMのスーパー名は「ヤマザワ」「ヨークベニマル」のように支店名が付いていない
  ことが多い。同名店舗が複数ヒットした場合は、一番近いバス停名から地名を推定して
  括弧書きで補う(例: 「ヤマザワ(漆山)?」)。バス停名がそのまま店舗名を示している
  場合(例: 「ヨークベニマル南館店前」)は確信度が高いので「?」を付けない。
  この自動推定は不確かなので、正しい支店名が分かったら人間が display_name 列を
  直接書き換えてよい(再実行しても消えない)。

出力:
  data/facilities_master.csv    … 行き先マスタ(人間が display_name・kana・採否を編集する。
                                    再実行しても編集済みの列は上書きしない)
  webapp/data/destinations.json … 採否列が「採用」の行だけを書き出す

実行方法: プロジェクトのルートで `python3 gap_map/make_destinations.py`

検算(計画書F2の完成条件):
  - 候補(facilities_master.csvの行数)が30〜45件
  - 県立中央病院・大学病院・主要ヤマザワ・両市の駅と市役所を含む
  - かな列が全行埋まっている
"""

import csv
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import config

PROJECT_ROOT = Path(__file__).parent.parent
FACILITIES_MASTER_CSV = config.DATA_DIR / "facilities_master.csv"
DESTINATIONS_JSON = PROJECT_ROOT / "webapp" / "data" / "destinations.json"

# 候補に採るしきい値(このスクリプト内だけの調整値。人間の採否判断はCSV側で行う)
HOSPITAL_MIN_COUNT = 5
SUPER_MIN_COUNT = 15

# 基幹病院・まちなかスポットは地域固有なので region.py が管理する
# (全国展開キットR1。山形の既定値は従来とまったく同じ。他地域では region.json に書くか、
#  空のままなら「頻度上位の病院・スーパーだけ」で行き先マスタが作られる)
from region import REGION

# 基幹病院(頻度上位に入っていても「抽出理由」をこちらで上書きする)
CORE_HOSPITALS = set(REGION["core_hospitals"])

# 駅・市役所・まちなかの手動追加分(facilities.csvに無いので、GTFS停留所の座標を転用する。
# 出典stop(memo)は各フィードのstops.txtで目視確認したもの)
MANUAL_TOWN_FACILITIES = [
    (t["name"], t["municipality"], t["lat"], t["lon"], t["memo"])
    for t in REGION["town_spots"]
]

# 正式名 → かな(全角括弧付きの法人格などは読み飛ばさず全部読む)。
# 自信のないものには末尾に「?」を付ける
KANA_BY_NAME = {
    # ---- 病院 ----
    "山形県立総合療育訓練センター": "やまがたけんりつそうごうりょういくくんれんせんたー",
    "（医）二本松会上山病院": "にほんまつかいかみのやまびょういん",
    "山形厚生病院": "やまがたこうせいびょういん",
    "山形県立中央病院": "やまがたけんりつちゅうおうびょういん",
    "国立大学法人山形大学医学部附属病院": "こくりつだいがくほうじんやまがただいがくいがくぶふぞくびょういん",
    "公立学校（共済）東北中央病院": "こうりつがっこうきょうさいとうほくちゅうおうびょういん",
    "山形徳洲会病院": "やまがたとくしゅうかいびょういん",
    "（独）国立病院機構山形病院": "こくりつびょういんきこうやまがたびょういん",
    "みゆき会病院": "みゆきかいびょういん",
    "若宮病院": "わかみやびょういん",
    "（医）小白川至誠堂病院": "こじらかわしせいどうびょういん?",
    "矢吹病院": "やぶきびょういん",
    "（医）横山厚生会横山病院": "よこやまこうせいかいよこやまびょういん",
    "（医）篠田好生会千歳篠田病院": "しのだこうせいかいちとせしのだびょういん",
    "（福）恩賜財団済生会山形済生病院": "おんしざいだんさいせいかいやまがたさいせいびょういん",
    "（医）篠田好生会天童温泉篠田病院": "しのだこうせいかいてんどうおんせんしのだびょういん",
    "（医）松柏会至誠堂総合病院": "しょうはくかいしせいどうそうごうびょういん?",
    "井出眼科病院": "いでがんかびょういん",
    "吉岡病院": "よしおかびょういん",
    "山形市立病院済生館": "やまがたしりつびょういんさいせいかん",
    # ---- スーパー(表示名に地名の推定を加えた行は、その読みも括弧書きで加える) ----
    "推名商店": "おしなしょうてん?",
    "コストコ上山倉庫店": "こすとこかみのやまそうこてん",
    "ヤマザワあさひ町店": "やまざわあさひまちてん",
    "ヤマザワ 成沢店": "やまざわなりさわてん",
    "ヤマザワ松見町": "やまざわまつみちょう?",
    "関食品": "せきしょくひん",
    "ベル 山辺店": "べるやまのべてん",
    "片桐食料品店": "かたぎりしょくりょうひんてん",
    # ---- 駅・公共 ----
    "山形駅": "やまがたえき",
    "山形市役所": "やまがたしやくしょ",
    "かみのやま温泉駅": "かみのやまおんせんえき",
    "上山市役所": "かみのやましやくしょ",
    "上山城": "かみのやまじょう",
}

# 曖昧な同名店舗の display_name / kana 上書き。(正式名, 緯度, 経度)をキーにして
# facilities.csv の該当行だけを狙い撃ちする(同名の別店舗まで巻き込まないため)
AMBIGUOUS_OVERRIDES = {
    ("フー屋", 38.141332, 140.274253): ("フー屋(南中学校前)?", "ふーや(みなみちゅうがっこうまえ)?"),
    ("ヨークベニマル", 38.152350, 140.288251): ("ヨークベニマル(ヨークタウン)?", "よーくべにまる(よーくたうん)?"),
    ("ヤマザワ", 38.311513, 140.343485): ("ヤマザワ(漆山)?", "やまざわ(うるしやま)?"),
    ("セブン-イレブン", 38.192026, 140.296163): ("セブン-イレブン(みはらしの丘)?", "せぶんいれぶん(みはらしのおか)?"),
    ("業務スーパー", 38.280659, 140.357715): ("業務スーパー(落合)?", "ぎょうむすーぱー(おちあい)?"),
    ("ヤマザワ", 38.325719, 140.364256): ("ヤマザワ(長岡)?", "やまざわ(ながおか)?"),
    ("ヨークベニマル", 38.238729, 140.301940): ("ヨークベニマル南館店", "よーくべにまるなんかんてん"),
    ("イオン", 38.226576, 140.306254): ("イオン山形南店", "いおんやまがたみなみてん"),
    ("ヨークベニマル", 38.287809, 140.318036): ("ヨークベニマル(嶋)?", "よーくべにまる(しま)?"),
    ("セブンイレブン", 38.151325, 140.265431): ("セブンイレブン(河崎温泉)?", "せぶんいれぶん(かわさきおんせん)?"),
    ("クックス-81", 38.246220, 140.364792): ("クックス-81(小白川)?", "くっくすはちじゅういち(こじらかわ)?"),
}


# ===============================================================
# 頻度上位の算出(直線距離で「一番近いメッシュの数」)
# ===============================================================
def haversine_m_vec(lat1: float, lon1: float, lats2: np.ndarray, lons2: np.ndarray) -> np.ndarray:
    r = 6371000
    p1 = math.radians(lat1)
    p2 = np.radians(lats2)
    dp = np.radians(lats2 - lat1)
    dl = np.radians(lons2 - lon1)
    a = np.sin(dp / 2) ** 2 + math.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def count_nearest_mesh(rows: pd.DataFrame, meshes: pd.DataFrame) -> np.ndarray:
    """施設の各行について、「直線距離で一番近い」メッシュの件数を数える"""
    lats = rows["lat"].to_numpy()
    lons = rows["lon"].to_numpy()
    counts = np.zeros(len(rows), dtype=int)
    for mesh in meshes.itertuples():
        d = haversine_m_vec(mesh.lat, mesh.lon, lats, lons)
        counts[int(np.argmin(d))] += 1
    return counts


# ===============================================================
# 候補の組み立て
# ===============================================================
def build_candidates() -> pd.DataFrame:
    facilities = pd.read_csv(config.FACILITIES_CSV)
    meshes = pd.read_csv(config.TARGET_MESHES_CSV)

    rows = []

    for category, min_count, label in [
        ("hospital", HOSPITAL_MIN_COUNT, "病院"),
        ("supermarket", SUPER_MIN_COUNT, "スーパー"),
    ]:
        cat_rows = facilities[facilities["category"] == category].reset_index(drop=True)
        cat_rows = cat_rows[cat_rows["name"] != "(名前なし)"].reset_index(drop=True)
        counts = count_nearest_mesh(cat_rows, meshes)
        cat_rows["nearest_mesh_count"] = counts
        top = cat_rows[cat_rows["nearest_mesh_count"] >= min_count]
        for r in top.itertuples():
            reason = "頻度上位"
            if r.name in CORE_HOSPITALS:
                reason = "基幹病院"
            rows.append({
                "正式名": r.name,
                "カテゴリ": label,
                "抽出理由": reason,
                "lat": r.lat,
                "lon": r.lon,
                "出典": "ksj_p04" if category == "hospital" else "osm",
                "_nearest_mesh_count": r.nearest_mesh_count,
            })

    for name, muni, lat, lon, source in MANUAL_TOWN_FACILITIES:
        rows.append({
            "正式名": name,
            "カテゴリ": "駅・公共",
            "抽出理由": "駅・公共",
            "lat": lat,
            "lon": lon,
            "出典": source,
            "_nearest_mesh_count": None,
        })

    df = pd.DataFrame(rows)

    # display_name・kana の初期値
    display_names = []
    kanas = []
    for r in df.itertuples():
        key = (r.正式名, round(r.lat, 6), round(r.lon, 6))
        override = None
        for (name, lat, lon), (dn, kn) in AMBIGUOUS_OVERRIDES.items():
            if r.正式名 == name and abs(r.lat - lat) < 1e-4 and abs(r.lon - lon) < 1e-4:
                override = (dn, kn)
                break
        if override:
            display_names.append(override[0])
            kanas.append(override[1])
        else:
            display_names.append("")   # 空なら正式名をそのまま使う(F1と同じ方式)
            kanas.append(KANA_BY_NAME.get(r.正式名, ""))

    df["display_name"] = display_names
    df["kana"] = kanas
    df["採否"] = ""   # 人間が記入する列

    missing_kana = df[df["kana"] == ""]
    if len(missing_kana):
        print(f"※ かな未設定の行が{len(missing_kana)}件あります(KANA_BY_NAMEに追記してください):")
        print(missing_kana["正式名"].tolist())

    # idは並び順から機械的に振る(病院→スーパー→駅・公共、名前順。再実行しても同じidになる)
    cat_order = {"病院": 0, "スーパー": 1, "駅・公共": 2}
    df = df.sort_values(
        by=["カテゴリ", "正式名"],
        key=lambda col: col.map(cat_order) if col.name == "カテゴリ" else col,
    ).reset_index(drop=True)
    df.insert(0, "id", [f"f{i:02d}" for i in range(1, len(df) + 1)])

    return df[["id", "正式名", "display_name", "kana", "カテゴリ", "抽出理由",
               "採否", "lat", "lon", "出典", "_nearest_mesh_count"]]


def merge_human_edits(master: pd.DataFrame) -> pd.DataFrame:
    """既存の facilities_master.csv があれば、人間が編集した 正式名/display_name/kana/採否 を
    引き継ぐ(カテゴリ+緯度経度で対応付け)。緯度経度は同じ物理施設を指す不変のキーとして使う
    (正式名は人間が「イオン」→「イオンモール山形南」のように書き換えること自体があるため、
    キーには使えない。以前はこれをキーに使っていて、正式名を修正した行の編集が次回実行で
    消えてしまう不具合があった)。再実行で人手の作業を消さないための仕組み"""
    if not FACILITIES_MASTER_CSV.exists():
        return master
    old = pd.read_csv(FACILITIES_MASTER_CSV, dtype=str).fillna("")
    edits = {}
    for _, r in old.iterrows():
        key = (r["カテゴリ"], round(float(r["lat"]), 5), round(float(r["lon"]), 5))
        edits[key] = (r["正式名"], r["display_name"], r["kana"], r["採否"])

    kept = 0
    for i, row in master.iterrows():
        key = (row["カテゴリ"], round(float(row["lat"]), 5), round(float(row["lon"]), 5))
        if key in edits and any(edits[key]):
            master.loc[i, ["正式名", "display_name", "kana", "採否"]] = edits[key]
            kept += 1
    if kept:
        print(f"既存マスタから人間の編集(正式名・表示名・かな・採否)を{kept}件分引き継ぎました")
    return master


def write_outputs(master: pd.DataFrame) -> None:
    out = master.drop(columns=["_nearest_mesh_count"])
    out.to_csv(FACILITIES_MASTER_CSV, index=False, quoting=csv.QUOTE_NONNUMERIC)
    print(f"→ {FACILITIES_MASTER_CSV} に{len(out)}件")

    cat_code = {"病院": "hospital", "スーパー": "supermarket", "駅・公共": "town"}
    accepted = master[master["採否"] == "採用"]
    DESTINATIONS_JSON.parent.mkdir(parents=True, exist_ok=True)
    records = []
    for r in accepted.itertuples():
        records.append({
            "id": r.id,
            "name": r.display_name or r.正式名,
            "kana": r.kana,
            "category": cat_code[r.カテゴリ],
            "lat": r.lat,
            "lon": r.lon,
        })
    DESTINATIONS_JSON.write_text(
        json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {DESTINATIONS_JSON} に{len(records)}件(採否=採用の行のみ)")


def main():
    master = merge_human_edits(build_candidates())
    write_outputs(master)

    # ===== 検算(計画書F2完成条件) =====
    print("\n=== 検算(F2完成条件) ===")
    n = len(master)
    print(f"候補件数: {n}(条件: 30〜45件) → {'OK' if 30 <= n <= 45 else 'NG'}")

    must_include = ["山形県立中央病院", "国立大学法人山形大学医学部附属病院", "山形駅",
                     "かみのやま温泉駅", "山形市役所", "上山市役所"]
    for name in must_include:
        hit = (master["正式名"] == name).any()
        print(f"  {name}: {'含む' if hit else '含まない'}")
    yamazawa_hit = master["正式名"].str.contains("ヤマザワ", na=False).any()
    print(f"  主要ヤマザワを含む: {'含む' if yamazawa_hit else '含まない'}")

    n_kana_missing = (master["kana"] == "").sum()
    print(f"かな未入力: {n_kana_missing}件 → {'OK' if n_kana_missing == 0 else 'NG'}")

    print("\n=== カテゴリ別・抽出理由別の内訳 ===")
    print(master.groupby(["カテゴリ", "抽出理由"]).size().to_string())

    print("\n=== 候補一覧(display_name・kana・採否の目視確認用) ===")
    with pd.option_context("display.max_rows", None, "display.width", 160,
                            "display.unicode.east_asian_width", True):
        show = master[["id", "正式名", "display_name", "kana", "カテゴリ", "抽出理由", "_nearest_mesh_count"]]
        print(show.to_string(index=False))

    print(f"\n※ {FACILITIES_MASTER_CSV} の display_name(地名の推定に「?」が付いた行は特に)・"
          f"\n   kana(「?」付きの行)・採否(全行必須。「採用」の行だけ destinations.json に出る)を"
          f"\n   人間が記入・確認してください。記入後にもう一度このスクリプトを実行すると反映されます。")


if __name__ == "__main__":
    main()
