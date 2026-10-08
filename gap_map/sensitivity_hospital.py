# -*- coding: utf-8 -*-
"""
★第1版(gap-map-analysis-v1)の時代の感度分析。この結果で第2版の行き先が決まった(docs/plan_gap_map.md §7.1)。

交通空白マップの感度分析: 行き先の病院を「内科のある病院」だけにすると、確定数値がどう変わるか。

確定版(git tag gap-map-analysis-v1)は、国土数値情報 P04(医療機関・2014年)の「病院」68か所を
すべて行き先にしている。その中には眼科だけ(井出眼科病院)・精神科が中心(若宮病院ほか)・
小児科と産婦人科だけ(横山病院ほか)・療育施設の病院もあり、指標①で「最寄りの病院」がこれらに
なっているメッシュが 144件・85,419人(2市の人口の31%)あった(2026-10-08 開発者の質問で判明)。
お年寄りの「いつもの通院」の行き先としては不自然なので、診療科目に「内科」を含む病院だけに絞って
計算し直し、影響の大きさを参考として出す(開発者決定。確定数値は変えない)。

「内科のある病院」の判定: P04 の診療科目(P04_004〜006)のどれかに「内科」という文字を含む
(消化器内科・呼吸器内科なども内科として数える)。ただし「心療内科」だけの病院は精神科の診療なので
内科に数えない。病床数は P04 に無いため使えない(「総合病院」は法律上の区分も今は無い)。

★凍結した分析(compute_access.py・output/access_mesh.csv)には手を入れない。
  sensitivity_depart.py の recompute()(確定版と同じ答えを出すことを照合済み)に病院の一覧だけを渡す。

使い方(プロジェクトルートから):
  python3 gap_map/sensitivity_hospital.py
出力: output/sensitivity_hospital_naika.csv(メッシュごとの確定版との比較)と、画面に集計
"""

import pandas as pd

import compute_access as ca
import config
from fetch_facilities import read_dbf_records
from sensitivity_depart import recompute, summarize


def hospital_subjects() -> dict:
    """P04 の病院名 → 診療科目のリスト"""
    out = {}
    for r in read_dbf_records(config.P04_DBF):
        if r["P04_001"] != "1":   # 1 = 病院
            continue
        subj = "　".join([r["P04_004"], r["P04_005"], r["P04_006"]]).split("　")
        out[r["P04_002"]] = [x for x in subj if x]
    return out


def has_internal_medicine(subjects: list) -> bool:
    """診療科目に「内科」を含むか(心療内科は除く)"""
    return any("内科" in s and s != "心療内科" for s in subjects)


def main():
    facilities = pd.read_csv(config.FACILITIES_CSV)
    subjects = hospital_subjects()
    is_hosp = facilities["category"] == "hospital"
    naika = facilities["name"].map(lambda n: has_internal_medicine(subjects.get(n, [])))
    unknown = facilities[is_hosp & ~facilities["name"].isin(subjects)]
    if len(unknown):
        raise SystemExit(f"★P04 に診療科目が見つからない病院: {unknown['name'].tolist()}")
    dropped = facilities[is_hosp & ~naika]
    print(f"病院 {int(is_hosp.sum())}か所のうち、内科のない {len(dropped)}か所を外す:")
    for n in dropped["name"]:
        print(f"  {n} … {'・'.join(subjects[n])}")
    alt_facilities = facilities[~is_hosp | naika].reset_index(drop=True)

    meshes = pd.read_csv(config.TARGET_MESHES_CSV)
    base_minutes = list(ca.DEPART_MINUTES)
    print("\n[1/2] 確定版と同じ条件(病院68か所)で計算…")
    base = recompute(base_minutes, facilities)   # 第1版の病院68か所(2026-10-08 第2版で既定が変わったため明示)
    print(f"[2/2] 内科のある病院 {int((is_hosp & naika).sum())}か所だけで計算…")
    alt = recompute(base_minutes, alt_facilities)

    s0, s1 = summarize(base, meshes), summarize(alt, meshes)
    t0 = pd.to_numeric(base["time_to_hospital_min"], errors="coerce")
    t1 = pd.to_numeric(alt["time_to_hospital_min"], errors="coerce")
    s0["①病院までの中央値(分)"], s1["①病院までの中央値(分)"] = float(t0.median()), float(t1.median())
    s0["①60分超・到達不能の人口"] = int(meshes.loc[(t0.isna() | (t0 > 60)).values, "population"].sum())
    s1["①60分超・到達不能の人口"] = int(meshes.loc[(t1.isna() | (t1 > 60)).values, "population"].sum())
    print("\n=== 確定版(病院68か所) と 内科のある病院だけ の比較 ===")
    print(f"{'':28}{'確定版':>12}{'内科のある病院':>14}")
    for k in s0:
        v0, v1 = s0[k], s1[k]
        f = (lambda v: f"{v:,.1f}") if isinstance(v0, float) else (lambda v: f"{v:,}")
        print(f"{k:28}{f(v0):>12}{f(v1):>14}")

    districts = pd.read_csv(config.DATA_DIR / "mesh_districts.csv", dtype={"meshcode": str})
    out = (base.merge(alt, on="meshcode", suffixes=("_確定", "_内科"))
           .assign(meshcode=lambda x: x["meshcode"].astype(str))
           .merge(meshes[["meshcode", "population", "population_65plus"]].astype({"meshcode": str}), on="meshcode")
           .merge(districts[["meshcode", "source_school"]], on="meshcode", how="left"))
    out["①の増え(分)"] = (pd.to_numeric(out["time_to_hospital_min_内科"], errors="coerce")
                        - pd.to_numeric(out["time_to_hospital_min_確定"], errors="coerce"))
    hosp_changed = out[out["hospital_name_確定"] != out["hospital_name_内科"]]
    print(f"\n①の最寄り病院が替わったメッシュ: {len(hosp_changed)}件・人口 {hosp_changed['population'].sum():,}人"
          f"(①の増え 中央値 {hosp_changed['①の増え(分)'].median():.0f}分・最大 {hosp_changed['①の増え(分)'].max():.0f}分)")
    print(hosp_changed.groupby(["hospital_name_確定", "hospital_name_内科"]).agg(
        メッシュ=("meshcode", "size"), 人口=("population", "sum")).sort_values("人口", ascending=False).head(12).to_string())

    gap_changed = out[out["is_gap_確定"] != out["is_gap_内科"]]
    print(f"\n空白の判定が変わったメッシュ: {len(gap_changed)}件・人口 {gap_changed['population'].sum():,}人"
          f"(空白になった {int((gap_changed['is_gap_内科']).sum())}件 / 空白でなくなった {int((~gap_changed['is_gap_内科']).sum())}件)")
    if len(gap_changed):
        print(gap_changed.groupby("source_school").agg(メッシュ=("meshcode", "size"), 人口=("population", "sum"))
              .sort_values("人口", ascending=False).to_string())
    visit_changed = out[out["hospital_visit_ok_確定"] != out["hospital_visit_ok_内科"]]
    print(f"\n③通院可能性が変わったメッシュ: {len(visit_changed)}件・人口 {visit_changed['population'].sum():,}人")

    path = config.OUTPUT_DIR / "sensitivity_hospital_naika.csv"
    out.to_csv(path, index=False)
    print(f"\n→ {path}(全メッシュの確定版との比較)")


if __name__ == "__main__":
    main()
