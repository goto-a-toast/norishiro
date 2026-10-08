# -*- coding: utf-8 -*-
"""
空白分析 第2版(2026-10-08)の行き先 = 医療機関の一覧(data/medical.csv)を作る。

第1版は国土数値情報 P04(2014年)の「病院」すべてを行き先にしていたため、眼科だけ・精神科中心・
小児科と産婦人科だけの病院も「最寄りの病院」に数え、近所の内科医院(診療所)は数えていなかった。
開発者との検討(2026-10-08。docs/plan_gap_map.md §7.1)で、2つの問いに分けることにした:

  かかりつけ(kakaritsuke)… 「いつもの通院先(内科)へ通えるか」= 空白判定・指標①②の行き先(主の地図)
      P04-20 の病院・一般診療所のうち、診療科目に「内科」を含むもの(心療内科だけは除く)。
      ただし老人ホームの医務室・健診センター・刑務所など、一般の人がかからない施設の診療所は
      名前で外す(P04 の仕様書に「企業内の施設等を含む」とある。山形市・上山市の44件を目で確認)
  大きな病院(major)       … 「検査・専門の診察・急な体調悪化のときの病院へ通えるか」(補助の地図)
      P04-20 の病院のうち、救急告示病院(P04_009=1。救急病院等を定める省令に基づく告示)

範囲は山形県全域(市境のすぐ外の医療機関も候補にするため。fetch_facilities.py の病院と同じ考え方)。
スーパーは今までどおり data/facilities.csv を使う(このスクリプトは触らない)。

入力: config.P04_2020_GEOJSON(国土数値情報 P04-20 山形県。手動ダウンロード:
      https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-P04-v3_0.html の「山形・令和2年」)
出力: data/medical.csv … name, kind(hospital/clinic), lat, lon, naika, emergency, beds, public,
                         kakaritsuke, major
実行: python3 gap_map/make_medical.py
"""

import json
import re

import pandas as pd

import config

# 一般の人がかからない施設の診療所(名前で見分ける)
NOT_PUBLIC = re.compile("老人|特別養護|医務室|介護|健康管理|社員|職員|保健室|刑務|拘置|自衛隊|学園|事業所|工場|"
                        "ホーム|施設|センター診療所|共済組合.*診療所|健診|検診")
KIND = {1: "hospital", 2: "clinic"}   # P04_001(3=歯科診療所は使わない)


def subjects_of(p: dict) -> list:
    return [x for x in "　".join(filter(None, [p["P04_004"], p["P04_005"], p["P04_006"]])).split("　") if x]


def has_internal_medicine(subjects: list) -> bool:
    """診療科目に「内科」を含むか(消化器内科なども内科。心療内科は精神科の診療なので除く)"""
    return any("内科" in s and s != "心療内科" for s in subjects)


def load() -> pd.DataFrame:
    g = json.loads(config.P04_2020_GEOJSON.read_text(encoding="utf-8"))
    rows = []
    for f in g["features"]:
        p = f["properties"]
        if p["P04_001"] not in KIND:
            continue
        lon, lat = f["geometry"]["coordinates"]
        name = p["P04_002"] or ""
        rows.append({"name": name, "kind": KIND[p["P04_001"]], "lat": lat, "lon": lon,
                     "naika": has_internal_medicine(subjects_of(p)),
                     "emergency": p["P04_001"] == 1 and p["P04_009"] == 1,
                     "beds": p["P04_008"], "public": not NOT_PUBLIC.search(name)})
    df = pd.DataFrame(rows)
    df["kakaritsuke"] = df["naika"] & df["public"]
    df["major"] = df["emergency"]
    return df


def main():
    df = load()
    df.to_csv(config.MEDICAL_CSV, index=False)
    print(f"→ {config.MEDICAL_CSV}: 病院 {int((df.kind == 'hospital').sum())}・診療所 {int((df.kind == 'clinic').sum())}")
    print(f"  かかりつけ(内科のある病院・診療所): {int(df.kakaritsuke.sum())}か所"
          f"(病院 {int((df.kakaritsuke & (df.kind == 'hospital')).sum())}・診療所 {int((df.kakaritsuke & (df.kind == 'clinic')).sum())})")
    print(f"  大きな病院(救急告示): {int(df.major.sum())}か所")
    print(f"  一般の人がかからない施設として外した内科の診療所: {int((df.naika & ~df.public).sum())}か所")


if __name__ == "__main__":
    main()
