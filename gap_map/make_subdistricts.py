# -*- coding: utf-8 -*-
"""対策2(広い地区の位置精度改善): 広い地区をサブ地区に分割する。

背景(docs/handover.md §5・広い地区の限界):
  地区の時刻表は「代表点=人口最大メッシュの中心」1点から事前計算される。
  東沢地区(32メッシュ)のような広い学区では、代表点から2km以上離れて住む
  住民にとって、表示される乗り場も徒歩分も実態と合わない。
  そこで「代表点から遠くに住む人口」が多い地区だけをサブ地区(2〜3個)に分割し、
  サブ代表点ごとに既存パイプライン(export_web_data.py)で事前計算する。

使い方(分析データのある環境=Mac/Windowsで。mesh_districts.csv が必要):
  1. python3 gap_map/make_subdistricts.py            … 採点表を表示するだけ(何も書かない)
  2. 採点表を見て開発者が対象を確認し、
     python3 gap_map/make_subdistricts.py --apply    … 基準該当の地区を分割して書き出す
     python3 gap_map/make_subdistricts.py --apply --districts d15,d40
                                                     … 対象を明示指定したいとき
  3. data/subdistricts_master.csv の display_name / kana を人間が確認・修正
     (自動初期値「東沢地区(ひがし)」のままでも動く。再実行しても編集は消えない)
  4. python3 gap_map/make_subdistricts.py --apply    … 修正を districts.json に反映
  5. python3 gap_map/make_mesh_index.py → export_web_data.py の順で再生成

出力(--apply時):
  data/subdistricts_master.csv   … サブ地区マスタ(人間が表示名・かなを編集。コミット対象)
  data/mesh_subdistricts.csv     … mesh_districts.csv + sub_id 列(mesh_index.json の入力)
  webapp/data/districts.json     … 分割した親エントリに "sub":[...] を内包(それ以外は不変)

設計メモ:
  - 分割基準: 「親代表点から2km超に住む人口」が 300人以上 かつ 地区人口の20%以上
  - クラスタリング: 人口重み付きk-means(k=2。分割後もどちらかのクラスタが基準を
    超えるなら3。決定的な初期化=人口最大メッシュ+最遠点なので再実行しても同じ結果)
  - サブ代表点 = クラスタ内の人口最大メッシュの中心(make_districts.py と同じ定義)
  - 名前の初期値 = 親名(方角)。例: 東沢地区(ひがし)。人間が地元の呼び名に直す前提
"""
import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import config
from meshcode import meshcode_to_center

PROJECT_ROOT = Path(__file__).parent.parent
MESH_DISTRICTS_CSV = config.DATA_DIR / "mesh_districts.csv"
MESH_SUBDISTRICTS_CSV = config.DATA_DIR / "mesh_subdistricts.csv"
SUBDISTRICTS_MASTER_CSV = config.DATA_DIR / "subdistricts_master.csv"
DISTRICTS_JSON = PROJECT_ROOT / "webapp" / "data" / "districts.json"

FAR_DIST_M = 2000     # 「遠い」とみなす親代表点からの距離
FAR_POP_MIN = 300     # 分割基準: 遠くに住む人口がこの人数以上
FAR_RATIO_MIN = 0.20  # 分割基準: かつ地区人口のこの割合以上
MAX_K = 3             # サブ地区の最大数


def haversine_m(lat1, lon1, lat2, lon2):
    """2点間距離(m)。numpy配列も可"""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371000 * 2 * np.arcsin(np.sqrt(a))


def load_meshes() -> pd.DataFrame:
    if not MESH_DISTRICTS_CSV.exists():
        raise SystemExit("data/mesh_districts.csv がありません。先に "
                         "python3 gap_map/make_districts.py を実行してください")
    mesh = pd.read_csv(MESH_DISTRICTS_CSV, dtype={"meshcode": str})
    mesh["population"] = mesh["population"].astype(float)
    centers = mesh["meshcode"].map(meshcode_to_center)
    mesh["lat"] = centers.map(lambda t: t[0])
    mesh["lon"] = centers.map(lambda t: t[1])
    return mesh


def score_districts(mesh: pd.DataFrame, districts: list) -> pd.DataFrame:
    """全地区の「代表点から遠くに住む人口」を採点する(分割対象の判断材料)"""
    rep = {d["id"]: (d["lat"], d["lon"]) for d in districts}
    rows = []
    for did, g in mesh.groupby("district_id"):
        if did not in rep:
            continue
        dist = haversine_m(g["lat"].to_numpy(), g["lon"].to_numpy(), *rep[did])
        pop = g["population"].to_numpy()
        total = pop.sum()
        far_pop = pop[dist > FAR_DIST_M].sum()
        rows.append({"district_id": did,
                     "population": int(total),
                     "mesh_count": len(g),
                     "far_pop_2km": int(far_pop),
                     "far_ratio": far_pop / total if total else 0.0})
    df = pd.DataFrame(rows).sort_values("far_pop_2km", ascending=False)
    df["対象"] = (df["far_pop_2km"] >= FAR_POP_MIN) & (df["far_ratio"] >= FAR_RATIO_MIN)
    return df


# ===============================================================
# 人口重み付きk-means(決定的。再実行しても同じ結果になる)
# ===============================================================
def weighted_kmeans(g: pd.DataFrame, k: int) -> np.ndarray:
    """g(lat/lon/population/meshcode)を k 個に分ける。戻り値は各行のクラスタ番号。
    初期中心 = 人口最大メッシュ → そこから最遠のメッシュ → 既存中心から最遠…
    (farthest-point法。乱数を使わないので冪等)。同値の順序はメッシュコード昇順で固定"""
    g = g.sort_values("meshcode").reset_index(drop=True)
    lat, lon = g["lat"].to_numpy(), g["lon"].to_numpy()
    pop = g["population"].to_numpy()

    top = g.sort_values(["population", "meshcode"], ascending=[False, True]).index[0]
    centers = [(lat[top], lon[top])]
    while len(centers) < k:
        dmin = np.min([haversine_m(lat, lon, c0, c1) for c0, c1 in centers], axis=0)
        centers.append((lat[int(np.argmax(dmin))], lon[int(np.argmax(dmin))]))

    assign = np.zeros(len(g), dtype=int)
    for _ in range(100):
        d = np.stack([haversine_m(lat, lon, c0, c1) for c0, c1 in centers])
        new_assign = np.argmin(d, axis=0)
        # 空クラスタは「一番遠い点」を割り当てて維持する(退化の防止)
        for ci in range(k):
            if not (new_assign == ci).any():
                far = int(np.argmax(np.min(d, axis=0)))
                new_assign[far] = ci
        if (new_assign == assign).all() and _ > 0:
            break
        assign = new_assign
        centers = []
        for ci in range(k):
            m = assign == ci
            w = pop[m].sum() or 1.0
            centers.append((float((lat[m] * pop[m]).sum() / w),
                            float((lon[m] * pop[m]).sum() / w)))
    return assign


def cluster_needs_more_split(g: pd.DataFrame) -> bool:
    """クラスタ単体を1つの地区とみなして、まだ分割基準を超えるか(k=3への昇格判定)"""
    top = g.sort_values(["population", "meshcode"], ascending=[False, True]).iloc[0]
    dist = haversine_m(g["lat"].to_numpy(), g["lon"].to_numpy(),
                       float(top["lat"]), float(top["lon"]))
    pop = g["population"].to_numpy()
    far = pop[dist > FAR_DIST_M].sum()
    return far >= FAR_POP_MIN and far / (pop.sum() or 1) >= FAR_RATIO_MIN


def direction_word(dlat_m: float, dlon_m: float) -> tuple[str, str]:
    """親代表点から見たサブ地区の方角(漢字表示は使わず、ひらがなで統一)"""
    if abs(dlon_m) >= abs(dlat_m):
        return ("ひがし", "ひがし") if dlon_m > 0 else ("にし", "にし")
    return ("きた", "きた") if dlat_m > 0 else ("みなみ", "みなみ")


def split_district(mesh: pd.DataFrame, district: dict) -> list:
    """1地区をサブ地区に分割してマスタ行のリストを返す"""
    g = mesh[mesh["district_id"] == district["id"]].copy()
    k = 2
    assign = weighted_kmeans(g, k)
    if any(cluster_needs_more_split(g[assign == ci]) for ci in range(k)) and MAX_K >= 3:
        k = 3
        assign = weighted_kmeans(g, k)
    g["cluster"] = assign

    # 人口の多いクラスタから a, b, c を振る(同数ならメッシュコード最小で固定)
    order = sorted(range(k), key=lambda ci: (-g.loc[g["cluster"] == ci, "population"].sum(),
                                             g.loc[g["cluster"] == ci, "meshcode"].min()))
    rows = []
    for rank, ci in enumerate(order):
        c = g[g["cluster"] == ci]
        top = c.sort_values(["population", "meshcode"], ascending=[False, True]).iloc[0]
        # 方角: クラスタの人口重心が親代表点から見てどちらか(おおよそのメートル換算)
        w = c["population"].sum() or 1.0
        clat = (c["lat"] * c["population"]).sum() / w
        clon = (c["lon"] * c["population"]).sum() / w
        dlat_m = (clat - district["lat"]) * 111000
        dlon_m = (clon - district["lon"]) * 111000 * math.cos(math.radians(district["lat"]))
        dir_ja, dir_kana = direction_word(dlat_m, dlon_m)
        rows.append({
            "sub_id": f"{district['id']}{'abc'[rank]}",
            "parent_id": district["id"],
            "municipality": district["municipality"],
            "name": f"{district['name']}({dir_ja})",
            "display_name": "",   # 人間が上書きする列(空なら name を使う)
            "kana": f"{district.get('kana', '')}({dir_kana})",  # 初期値。人間が直してよい
            "population": int(c["population"].sum()),
            "mesh_count": len(c),
            "rep_meshcode": str(top["meshcode"]),
            "lat": round(float(top["lat"]), 6),
            "lon": round(float(top["lon"]), 6),
            "_meshcodes": list(c["meshcode"]),
        })
    return rows


def merge_human_edits(master: pd.DataFrame) -> pd.DataFrame:
    """既存の subdistricts_master.csv の display_name / kana を sub_id で引き継ぐ"""
    if not SUBDISTRICTS_MASTER_CSV.exists():
        return master
    old = pd.read_csv(SUBDISTRICTS_MASTER_CSV, dtype=str).fillna("")
    edits = {r["sub_id"]: (r["display_name"], r["kana"]) for _, r in old.iterrows()}
    kept = 0
    for i, row in master.iterrows():
        e = edits.get(row["sub_id"])
        if e and any(e):
            if e[0]:
                master.loc[i, "display_name"] = e[0]
            if e[1]:
                master.loc[i, "kana"] = e[1]
            kept += 1
    if kept:
        print(f"既存マスタから人間の編集(表示名・かな)を{kept}件引き継ぎました")
    return master


def apply_outputs(mesh: pd.DataFrame, districts: list, targets: list) -> None:
    all_rows = []
    for d in districts:
        if d["id"] in targets:
            all_rows.extend(split_district(mesh, d))
    master = pd.DataFrame([{k: v for k, v in r.items() if k != "_meshcodes"}
                           for r in all_rows])
    master = merge_human_edits(master)
    master.to_csv(SUBDISTRICTS_MASTER_CSV, index=False, quoting=csv.QUOTE_NONNUMERIC)
    print(f"→ {SUBDISTRICTS_MASTER_CSV} に{len(master)}サブ地区")

    # メッシュ→サブ地区の対応(分割対象外は sub_id 空欄)
    sub_of = {}
    for r in all_rows:
        for mc in r["_meshcodes"]:
            sub_of[mc] = r["sub_id"]
    mesh_out = mesh.copy()
    mesh_out["sub_id"] = mesh_out["meshcode"].map(sub_of).fillna("")
    mesh_out.to_csv(MESH_SUBDISTRICTS_CSV, index=False)
    print(f"→ {MESH_SUBDISTRICTS_CSV} に{len(mesh_out)}件"
          f"(sub_idあり {sum(1 for v in mesh_out['sub_id'] if v)}件)")

    # districts.json の親エントリに sub を内包(他のフィールドは触らない)
    districts_json = json.loads(DISTRICTS_JSON.read_text(encoding="utf-8"))
    by_parent = {}
    for _, r in master.iterrows():
        by_parent.setdefault(r["parent_id"], []).append({
            "id": r["sub_id"],
            "name": r["display_name"] or r["name"],
            "kana": r["kana"],
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
        })
    for d in districts_json:
        if d["id"] in by_parent:
            d["sub"] = by_parent[d["id"]]
        elif "sub" in d:
            del d["sub"]   # 対象から外れた地区の古いsubは消す
    DISTRICTS_JSON.write_text(
        json.dumps(districts_json, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {DISTRICTS_JSON} に sub を反映(親{len(by_parent)}地区)")

    # 検算
    for pid, subs in by_parent.items():
        parent_pop = int(mesh.loc[mesh["district_id"] == pid, "population"].sum())
        sub_pop = int(master.loc[master["parent_id"] == pid, "population"].sum())
        mark = "OK" if parent_pop == sub_pop else "★NG"
        print(f"  検算 {pid}: サブ人口合計 {sub_pop} = 親 {parent_pop} {mark}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="分割を実行して書き出す(無指定なら採点表の表示のみ)")
    ap.add_argument("--districts", default="",
                    help="対象地区IDをカンマ区切りで明示指定(例: d15,d40)")
    args = ap.parse_args()

    mesh = load_meshes()
    districts = json.loads(DISTRICTS_JSON.read_text(encoding="utf-8"))

    score = score_districts(mesh, districts)
    name_of = {d["id"]: d["name"] for d in districts}
    print(f"\n=== 採点表: 代表点から{FAR_DIST_M/1000:.0f}km超に住む人口 "
          f"(基準: {FAR_POP_MIN}人以上かつ{FAR_RATIO_MIN:.0%}以上で分割対象) ===")
    print("地区ID | 地区名 | 人口 | メッシュ数 | 遠い人口 | 割合 | 対象")
    for _, r in score.iterrows():
        print(f"{r['district_id']} | {name_of.get(r['district_id'],'?')} | {r['population']} | "
              f"{r['mesh_count']} | {r['far_pop_2km']} | {r['far_ratio']:.0%} | "
              f"{'★分割' if r['対象'] else ''}")

    if not args.apply:
        print("\n(採点のみ。分割して書き出すには --apply を付けて再実行)")
        return

    if args.districts:
        targets = [x.strip() for x in args.districts.split(",") if x.strip()]
    else:
        targets = list(score.loc[score["対象"], "district_id"])
    if not targets:
        print("\n分割対象がありません(基準該当なし)")
        return
    print(f"\n分割対象: {targets}")
    apply_outputs(mesh, districts, targets)
    print("\n次の手順: subdistricts_master.csv の表示名・かなを確認 → "
          "make_mesh_index.py → export_web_data.py の順で再生成")


if __name__ == "__main__":
    main()
