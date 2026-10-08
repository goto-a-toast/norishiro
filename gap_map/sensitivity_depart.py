# -*- coding: utf-8 -*-
"""
交通空白マップの感度分析: 「家を出る時刻のいちばん早い時刻」を変えると、確定数値がどう変わるか。

分析確定版(2026-07-05・git tag gap-map-analysis-v1)は、家を出る時刻を 7:00〜10:30 の30分おきで
試している(config.DEPART_TIMES)。そのため、通院に使える朝のバスが7時すぎの1本しかない地区では、
「7:00ちょうどに家を出て、その便に間に合うか」で判定が分かれる。
例: 山寺地区は山形市内行きの朝の便が 7:01(山寺駅前)・7:02(芦沢口)・7:03(追分)の1本だけで、
山寺駅前のそば(徒歩0分)は通院できる(Yes)、芦沢口(徒歩3分)・追分(徒歩5分)のそばは1〜2分間に合わず
通院できない(No)=隠れ空白、になった(2026-10-08 開発者の質問で判明。確定の隠れ空白9メッシュ・571人の
うち8メッシュ・568人が山寺地区)。

★凍結した分析(compute_access.py・output/access_mesh.csv)には手を入れない(開発者決定・案C)。
  このスクリプトは compute_access.py の関数を import して、試す出発時刻だけを差し替えて計算し直し、
  結果を別のファイルに書く。分析日のネットワーク(data/network.pkl)もそのまま使う。
★最初に「確定版と同じ出発時刻」で計算し直して、output/access_mesh.csv と全メッシュ一致することを
  確かめる(この再計算の仕組みが確定版と同じ答えを出す証拠)。一致しなければ止まる。

使い方(プロジェクトルートから):
  python3 gap_map/sensitivity_depart.py                 # いちばん早い出発を 6:30 にした場合
  python3 gap_map/sensitivity_depart.py --earliest 06:00
出力: output/sensitivity_depart_0630.csv(メッシュごとの確定版との比較)と、画面に集計
"""

import argparse

import numpy as np
import pandas as pd

import compute_access as ca
import config

DISTRICTS_CSV = config.DATA_DIR / "mesh_districts.csv"


def hm_to_min(hm: str) -> int:
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


def recompute(depart_minutes: list) -> pd.DataFrame:
    """compute_access.main() のメッシュごとの計算を、出発時刻だけ差し替えて行う。
    is_gap の判定式も main() と同じ(確定版との一致で確かめる)"""
    ca.DEPART_MINUTES = depart_minutes   # compute_indicator1 / compute_hospital_visit が参照する
    network = ca.load_network()
    meshes = pd.read_csv(config.TARGET_MESHES_CSV)
    facilities = pd.read_csv(config.FACILITIES_CSV)
    stop_ids = list(network.stops.keys())
    stop_lats = np.array([network.stops[s]["lat"] for s in stop_ids])
    stop_lons = np.array([network.stops[s]["lon"] for s in stop_ids])
    hospital_index = ca.FacilityIndex(facilities, "hospital", stop_ids, stop_lats, stop_lons)

    rows = []
    for mesh in meshes.itertuples():
        mesh_stops = ca.nearby_stops(mesh.lat, mesh.lon, stop_ids, stop_lats, stop_lons,
                                     config.MAX_WALK_TO_STOP_M)
        hosp_min, _ = ca.compute_indicator1(mesh.lat, mesh.lon, mesh_stops, network, hospital_index)
        visit_ok, visit_total = ca.compute_hospital_visit(mesh.lat, mesh.lon, mesh_stops, network,
                                                          hospital_index)
        _, walk_direct_min = hospital_index.nearest_by_distance(mesh.lat, mesh.lon)
        walkable_by_foot = walk_direct_min is not None and walk_direct_min <= config.WALKABLE_FACILITY_MIN
        is_gap = (mesh.population > 0 and not walkable_by_foot
                  and (hosp_min is None or hosp_min > config.GAP_THRESHOLD_MIN or visit_ok == "No"))
        rows.append({"meshcode": mesh.meshcode, "time_to_hospital_min": hosp_min,
                     "hospital_visit_ok": visit_ok, "visit_total_min": visit_total, "is_gap": is_gap})
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame, meshes: pd.DataFrame) -> dict:
    """確定数値と同じ集計(analyze_demographics.py と同じく、高齢化率は年齢内訳のある行で計算)"""
    d = df.merge(meshes[["meshcode", "population", "population_65plus"]], on="meshcode")

    def aging(sub):
        valid = sub[sub["population_65plus"].notna()]
        return valid["population_65plus"].sum() / valid["population"].sum() * 100

    gap = d[d["is_gap"]]
    hosp = pd.to_numeric(d["time_to_hospital_min"], errors="coerce")
    hidden = d[(hosp <= config.GAP_THRESHOLD_MIN) & (d["hospital_visit_ok"] == "No")]
    return {
        "空白メッシュ人口": int(gap["population"].sum()),
        "空白の割合(%)": gap["population"].sum() / d["population"].sum() * 100,
        "空白の高齢化率(%)": aging(gap),
        "隠れ空白メッシュ数": len(hidden),
        "隠れ空白人口": int(hidden["population"].sum()),
        "通院できる(Yes)メッシュ数": int((d["hospital_visit_ok"] == "Yes").sum()),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--earliest", default="06:30", help="家を出る時刻のいちばん早い時刻(既定 06:30)")
    args = ap.parse_args()

    base_minutes = list(ca.DEPART_MINUTES)
    first = hm_to_min(args.earliest)
    alt_minutes = sorted(set(range(first, base_minutes[0], 30)) | set(base_minutes))
    fmt = lambda ms: ", ".join(f"{m // 60}:{m % 60:02d}" for m in ms)

    meshes = pd.read_csv(config.TARGET_MESHES_CSV)
    frozen = pd.read_csv(config.ACCESS_MESH_CSV)

    print(f"[1/2] 確定版と同じ出発時刻({fmt(base_minutes)})で計算し直して、確定版と照合…")
    base = recompute(base_minutes)
    cmp = frozen.merge(base, on="meshcode", suffixes=("_frozen", "_re"))
    # 確定版は到達不能を文字列「到達不能」で書いている。数値にそろえて(到達不能=NaN)比べる
    t0 = pd.to_numeric(cmp["time_to_hospital_min_frozen"], errors="coerce")
    t1 = pd.to_numeric(cmp["time_to_hospital_min_re"], errors="coerce")
    # 地図①の色分け(15/30/45/60分。make_map.py)。到達不能は -1
    bucket = lambda t: t.apply(lambda v: -1 if pd.isna(v) else sum(v > b for b in (15, 30, 45, 60)))
    # ★照合の条件: 確定数値と地図の色に効く部分(通院可能性・空白判定・①の色分け)が全メッシュで一致すること。
    #   ①の分数そのものは5メッシュで確定版と食い違う(72分→79分。いずれも60分超で色・数値は同じ)。
    #   凍結の翌日(2026-07-06)に transit_core.py の走査順を sorted() で固定した(F3 の「毎回同じ出力」の
    #   ため)。確定版の計算はその前で、Python の文字列ハッシュの乱数で順番が変わる状態だった。
    #   乗換の徒歩で到着時刻が同じラウンド内に更新される順番に結果が左右されるため、分数がずれうる
    same = ((bucket(t0) == bucket(t1))
            & (cmp["hospital_visit_ok_frozen"] == cmp["hospital_visit_ok_re"])
            & (cmp["is_gap_frozen"] == cmp["is_gap_re"]))
    if len(cmp) != len(frozen) or not same.all():
        print(cmp.loc[~same].head(10).to_string())
        raise SystemExit(f"★確定版と一致しないメッシュが {int((~same).sum())}件。感度分析を中止します")
    n_min_diff = int((~((t0 == t1) | (t0.isna() & t1.isna()))).sum())
    print(f"  → {len(cmp)}メッシュすべて、通院可能性・空白判定・①の色分けが確定版と一致"
          f"(①の分数だけ違うメッシュ: {n_min_diff}件。上のコメント参照)")

    print(f"[2/2] いちばん早い出発を {args.earliest} にして計算({fmt(alt_minutes)})…")
    alt = recompute(alt_minutes)

    s0, s1 = summarize(base, meshes), summarize(alt, meshes)
    print(f"\n=== 確定版(7:00〜) と {args.earliest}〜 の比較 ===")
    print(f"{'':24}{'確定版(7:00〜)':>16}{args.earliest + '〜':>14}")
    for k in s0:
        v0, v1 = s0[k], s1[k]
        f = (lambda v: f"{v:,.1f}") if isinstance(v0, float) else (lambda v: f"{v:,}")
        print(f"{k:24}{f(v0):>16}{f(v1):>14}")

    # メッシュごとの変化(地区名つき)
    districts = pd.read_csv(DISTRICTS_CSV, dtype={"meshcode": str})
    out = (base.merge(alt, on="meshcode", suffixes=("_確定", "_" + args.earliest.replace(":", "")))
           .assign(meshcode=lambda x: x["meshcode"].astype(str))
           .merge(meshes[["meshcode", "population", "population_65plus"]].astype({"meshcode": str}), on="meshcode")
           .merge(districts[["meshcode", "district_id", "source_school"]], on="meshcode", how="left"))
    alt_sfx = "_" + args.earliest.replace(":", "")
    changed = out[(out["hospital_visit_ok_確定"] != out["hospital_visit_ok" + alt_sfx])
                  | (out["is_gap_確定"] != out["is_gap" + alt_sfx])]
    path = config.OUTPUT_DIR / f"sensitivity_depart_{args.earliest.replace(':', '')}.csv"
    out.to_csv(path, index=False)
    print(f"\n判定が変わったメッシュ: {len(changed)}件・人口 {changed['population'].sum():,}人")
    if len(changed):
        by = changed.groupby("source_school").agg(メッシュ数=("meshcode", "size"), 人口=("population", "sum"))
        print(by.sort_values("人口", ascending=False).to_string())
    print(f"\n→ {path}(全メッシュの確定版との比較)")


if __name__ == "__main__":
    main()
