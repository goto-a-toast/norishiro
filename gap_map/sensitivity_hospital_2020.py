# -*- coding: utf-8 -*-
"""
★第1版(gap-map-analysis-v1)の時代の感度分析。この結果で第2版の行き先が決まった(docs/plan_gap_map.md §7.1)。

交通空白マップの感度分析: 医療機関データを2020年版にし、「行き先」の基準を変えると数値がどう変わるか。

確定版(git tag gap-map-analysis-v1)は国土数値情報 P04 の2014年版の「病院」すべてを行き先にしている。
2026-10-08 の開発者との検討で、どの基準が「通院の空白」を表すのにふさわしいかを数字で比べることになった:

  2020病院すべて … 基準は確定版と同じで、データだけ2020年版(版の違いの影響を切り分ける)
  A 救急告示病院 … 「大きな病院(救急も受ける総合的な病院)へ通えるか」。P04_009=1
  D 内科の診療所+病院 … 「かかりつけの内科へ通えるか」(いつもの通院にいちばん近い)。
                       診療科目に「内科」を含む(心療内科だけは除く)病院と診療所。ただし老人ホームの
                       医務室・健診センター・刑務所など、一般の人がかからない施設の診療所は名前で外す
                       (P04 は「企業内の施設等を含む」と仕様書にある)

★凍結した分析(compute_access.py・output/access_mesh.csv)・data/facilities.csv には手を入れない。
  sensitivity_depart.py の recompute()(確定版と同じ答えを出すことを照合済み)に行き先の一覧だけを渡す。
  スーパーは確定版の一覧のまま(病院の指標①②と空白判定だけが変わる)。

使い方(プロジェクトルートから。2020年版は data/P04-20_06/ に置く):
  python3 gap_map/sensitivity_hospital_2020.py
出力: output/sensitivity_hospital_2020.csv と、画面に集計
"""

import json
import re

import pandas as pd

import compute_access as ca
import config
from sensitivity_depart import recompute, summarize

P04_2020_GEOJSON = config.P04_2020_GEOJSON

# 一般の人がかからない施設の診療所(名前で見分ける。2026-10-08 に山形市・上山市の44件を目で確認)
NOT_PUBLIC = re.compile("老人|特別養護|医務室|介護|健康管理|社員|職員|保健室|刑務|拘置|自衛隊|学園|事業所|工場|"
                        "ホーム|施設|センター診療所|共済組合.*診療所|健診|検診")


def load_p04_2020() -> pd.DataFrame:
    g = json.loads(P04_2020_GEOJSON.read_text(encoding="utf-8"))
    rows = []
    for f in g["features"]:
        p = f["properties"]
        subj = [x for x in "　".join(filter(None, [p["P04_004"], p["P04_005"], p["P04_006"]])).split("　") if x]
        lon, lat = f["geometry"]["coordinates"]
        rows.append({"name": p["P04_002"], "kind": p["P04_001"], "lat": lat, "lon": lon,
                     "naika": any("内科" in s and s != "心療内科" for s in subj),
                     "emergency": p["P04_009"] == 1, "beds": p["P04_008"],
                     "public": not NOT_PUBLIC.search(p["P04_002"] or "")})
    return pd.DataFrame(rows)


def with_hospitals(facilities: pd.DataFrame, hospitals: pd.DataFrame) -> pd.DataFrame:
    """確定版の施設一覧のうち病院だけを差し替える(スーパーはそのまま)"""
    sup = facilities[facilities["category"] != "hospital"]
    h = hospitals.assign(category="hospital", source="ksj_p04_2020")[["name", "category", "lat", "lon", "source"]]
    return pd.concat([sup, h], ignore_index=True)


def main():
    facilities = pd.read_csv(config.FACILITIES_CSV)
    p04 = load_p04_2020()
    variants = {
        "2020病院すべて": p04[p04["kind"] == 1],
        "A救急告示病院": p04[(p04["kind"] == 1) & p04["emergency"]],
        "D内科の診療所+病院": p04[p04["kind"].isin([1, 2]) & p04["naika"] & p04["public"]],
    }
    for k, v in variants.items():
        print(f"{k}: 県全体 {len(v)}か所")

    meshes = pd.read_csv(config.TARGET_MESHES_CSV)
    minutes = list(ca.DEPART_MINUTES)
    print("\n[確定版] 計算…")
    results = {"確定版": recompute(minutes, facilities)}   # 第1版の病院(第2版で既定が変わったため明示)
    for k, v in variants.items():
        print(f"[{k}] 計算…")
        results[k] = recompute(minutes, with_hospitals(facilities, v))

    summ = {}
    for k, df in results.items():
        s = summarize(df, meshes)
        t = pd.to_numeric(df["time_to_hospital_min"], errors="coerce")
        s["①病院までの中央値(分)"] = float(t.median())
        summ[k] = s
    print("\n=== 比較 ===")
    keys = list(summ["確定版"].keys())
    print(f"{'':26}" + "".join(f"{k:>14}" for k in summ))
    for key in keys:
        cells = []
        for k in summ:
            v = summ[k][key]
            cells.append(f"{v:,.1f}" if isinstance(v, float) else f"{v:,}")
        print(f"{key:26}" + "".join(f"{c:>14}" for c in cells))

    # 空白の判定が変わったメッシュを地区ごとに
    districts = pd.read_csv(config.DATA_DIR / "mesh_districts.csv", dtype={"meshcode": str})
    base = results["確定版"].assign(meshcode=lambda x: x["meshcode"].astype(str))
    out = base[["meshcode", "is_gap", "time_to_hospital_min", "hospital_visit_ok"]].rename(
        columns=lambda c: c if c == "meshcode" else f"{c}_確定版")
    for k in variants:
        r = results[k].assign(meshcode=lambda x: x["meshcode"].astype(str))
        out = out.merge(r[["meshcode", "is_gap", "time_to_hospital_min", "hospital_visit_ok", "hospital_name"]]
                        .rename(columns=lambda c, k=k: c if c == "meshcode" else f"{c}_{k}"), on="meshcode")
    out = (out.merge(meshes[["meshcode", "population"]].astype({"meshcode": str}), on="meshcode")
              .merge(districts[["meshcode", "source_school"]], on="meshcode", how="left"))
    for k in variants:
        ch = out[out["is_gap_確定版"] != out[f"is_gap_{k}"]]
        became = ch[ch[f"is_gap_{k}"]]
        left = ch[~ch[f"is_gap_{k}"]]
        print(f"\n[{k}] 空白になった {len(became)}メッシュ・{became['population'].sum():,}人 / "
              f"空白でなくなった {len(left)}メッシュ・{left['population'].sum():,}人")
        for label, sub in [("空白になった", became), ("空白でなくなった", left)]:
            if len(sub):
                top = sub.groupby("source_school")["population"].agg(["size", "sum"]).sort_values("sum", ascending=False).head(8)
                print(f"  {label}(地区別・上位): " + "、".join(f"{i}{int(r['sum'])}人" for i, r in top.iterrows()))
    path = config.OUTPUT_DIR / "sensitivity_hospital_2020.csv"
    out.to_csv(path, index=False)
    print(f"\n→ {path}")


if __name__ == "__main__":
    main()
