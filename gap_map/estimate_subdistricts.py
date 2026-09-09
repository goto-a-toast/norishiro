# -*- coding: utf-8 -*-
"""サブ地区の分割基準を変えたときの「時刻表の数」と「データ量」を、
何も書き込まずに見積もる(--apply の前の判断材料)。

背景(docs/plan_f10_stop_select.md §6.7):
  家側(行き)の乗り場候補は「地区の代表点から徒歩 config.MAX_WALK_TO_STOP_M(=800m)
  以内」に限られるため、代表点から離れて住む人の最寄り停は時刻表に載らない
  (実測: 住民メッシュ616のうち、自分の最寄り停が自分の地区の時刻表にあるのは
  206=33.4%だけ)。これを直すには代表点を増やす=サブ地区を増やすしかないが、
  時刻表の数がそのまま増えてデータ量に効くため、着工前に数を実測する。

使い方(mesh_districts.csv が要る環境=Mac/Windowsで。プロジェクトルートから):
  python3 gap_map/estimate_subdistricts.py                      … 既定(1200m/150人/10%/最大6)
  python3 gap_map/estimate_subdistricts.py --far-dist 1500 --max-k 4
  引数は make_subdistricts.py の --far-dist / --far-pop / --far-ratio / --max-k と同じ意味。

このスクリプトは読み取り専用。ファイルは1つも書き換えない
(実際に分割するのは make_subdistricts.py --apply)。
"""
import argparse
import glob
import json
import math
import os
from pathlib import Path

import config
import make_subdistricts as ms
from meshcode import latlon_to_meshcode, meshcode_to_center

PROJECT_ROOT = Path(__file__).parent.parent
WEBAPP_DATA = PROJECT_ROOT / "webapp" / "data"
TIMETABLES_GLOB = str(WEBAPP_DATA / "timetables" / "*.json")


# ===============================================================
# 「どれだけ直るか」の見込み(カバー率)
# ---------------------------------------------------------------
# カバーできている = 「そのメッシュの住民の最寄り停が、自分の地区(サブ地区)の
# 時刻表に乗り場として載っている」。時刻表の乗り場候補は代表点から
# config.MAX_WALK_TO_STOP_M(=800m)以内なので、
#   「メッシュの最寄り停 ←→ 自分の代表点」の距離が800m以内か
# で近似する。この近似は実測よりやや甘く出る(実データでの検証: 近似37.8%に対し、
# 時刻表から数えた実測は33.4%。便の有無や工場の絞り込みまでは見ないため)。
# 分割前後を同じ物差しで比べる目的なので、差の大きさの目安として使う
# ===============================================================
def _dist_m(lat1, lon1, lat2, lon2):
    x = (lon2 - lon1) * math.pi / 180 * math.cos((lat1 + lat2) / 2 * math.pi / 180)
    y = (lat2 - lat1) * math.pi / 180
    return 6371000 * math.sqrt(x * x + y * y)


def _stop_dist(stops, name, lat, lon):
    """停留所名からその停までの距離(m)。同名の停が複数あるときは一番近いもの"""
    v = stops.get(name)
    if not v:
        return None
    pts = v if isinstance(v[0], list) else [v]
    return min(_dist_m(lat, lon, p[0], p[1]) for p in pts)


def load_mesh_nearest_stops():
    """karte.json から {メッシュコード: そのメッシュの最寄り停名} を作る。
    カルテはメッシュ単位で計算しているので、住民から見た本当の最寄り停が分かる"""
    path = WEBAPP_DATA / "karte.json"
    if not path.exists():
        return {}
    karte = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for m in karte.get("meshes", []):
        if m.get("nearest_stop_name"):
            out[latlon_to_meshcode(m["lat"], m["lon"])] = m["nearest_stop_name"]
    return out


def coverage(mesh, reps, nearest_stops, stops):
    """reps: {メッシュコード: (代表点lat, 代表点lon)} → (カバー数, 対象数)"""
    ok = tot = 0
    for code, stop_name in nearest_stops.items():
        rep = reps.get(code)
        if rep is None:
            continue
        tot += 1
        d = _stop_dist(stops, stop_name, rep[0], rep[1])
        if d is not None and d <= config.MAX_WALK_TO_STOP_M:
            ok += 1
    return ok, tot


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--far-dist", type=float, default=1200)
    ap.add_argument("--far-pop", type=float, default=150)
    ap.add_argument("--far-ratio", type=float, default=0.10)
    ap.add_argument("--max-k", type=int, default=6)
    ap.add_argument("--stop", default="県立中央病院",
                    help="この停留所が分割後に乗り場候補に入るかを個別に見る")
    args = ap.parse_args()
    ms.set_thresholds(args.far_dist, args.far_pop, args.far_ratio, args.max_k)

    mesh = ms.load_meshes()
    districts = json.loads(ms.DISTRICTS_JSON.read_text(encoding="utf-8"))
    score = ms.score_districts(mesh, districts)
    targets = set(score[score["対象"]]["district_id"])

    # いまの時刻表1つあたりの大きさ(実測値。見込みの外挿に使う)
    files = glob.glob(TIMETABLES_GLOB)
    if not files:
        raise SystemExit("webapp/data/timetables/ に時刻表がありません")
    cur_bytes = sum(os.path.getsize(f) for f in files)
    avg = cur_bytes / len(files)

    print(f"=== 基準 {args.far_dist:.0f}m / {args.far_pop:.0f}人 / "
          f"{args.far_ratio:.0%} / 1地区あたり最大{args.max_k}個 ===")
    print(f"いまの時刻表: {len(files)}個・合計{cur_bytes/1024/1024:.1f}MB"
          f"(1個あたり平均{avg/1024:.0f}KB)\n")

    units = 0
    detail = []
    new_reps = {}      # 分割後の代表点(サブ地区ごと)
    for d in districts:
        if d["id"] in targets:
            # 分割の計算だけする(書き出しはしない)
            subs = ms.split_district(mesh, d)
            n = len(subs)
            detail.append((n, d["id"], d["name"], int((mesh["district_id"] == d["id"]).sum())))
            units += n
            new_reps[d["id"]] = [(float(r["lat"]), float(r["lon"])) for r in subs]
        else:
            units += 1
            new_reps[d["id"]] = [(float(d["lat"]), float(d["lon"]))]

    for n, did, name, mc in sorted(detail, key=lambda x: (-x[0], x[1])):
        print(f"  {name}({did}) メッシュ{mc:3d} → サブ地区{n}個")

    print(f"\n分割する地区: {len(detail)} / {len(districts)}")
    print(f"時刻表の総数: {len(files)}個 → {units}個({units/len(files):.1f}倍)")
    print(f"データ量の見込み: 約{units*avg/1024/1024:.0f}MB(いま {cur_bytes/1024/1024:.0f}MB)")
    # ---- どれだけ直るかの見込み ----
    nearest_stops = load_mesh_nearest_stops()
    stops_path = WEBAPP_DATA / "stops_index.json"
    if nearest_stops and stops_path.exists():
        stops = json.loads(stops_path.read_text(encoding="utf-8"))
        # メッシュごとに「自分が属する単位の代表点」を割り当てる。
        # サブ地区が複数あるときは、いちばん近い代表点を自分のものとみなす
        def reps_for(rep_map):
            out = {}
            for _, row in mesh.iterrows():
                cands = rep_map.get(row["district_id"])
                if not cands:
                    continue
                out[str(row["meshcode"])] = min(
                    cands, key=lambda p: _dist_m(row["lat"], row["lon"], p[0], p[1]))
            return out

        cur_map = {}
        for d in districts:
            subs = d.get("sub", [])
            cur_map[d["id"]] = ([(s2["lat"], s2["lon"]) for s2 in subs]
                                if subs else [(d["lat"], d["lon"])])
        before = coverage(mesh, reps_for(cur_map), nearest_stops, stops)
        after = coverage(mesh, reps_for(new_reps), nearest_stops, stops)
        print(f"\n--- 住民の最寄り停が時刻表に載る見込み(近似) ---")
        print(f"  いま      : {before[0]}/{before[1]} = {before[0]/before[1]*100:.1f}%")
        print(f"  分割後    : {after[0]}/{after[1]} = {after[0]/after[1]*100:.1f}%"
              f"  ({(after[0]-before[0])/before[1]*100:+.1f}ポイント)")
        # 個別の停(既定は県立中央病院)が、どこかの代表点の徒歩圏に入るか
        if args.stop:
            def reachable(rep_map):
                for cands in rep_map.values():
                    for la, lo in cands:
                        d2 = _stop_dist(stops, args.stop, la, lo)
                        if d2 is not None and d2 <= config.MAX_WALK_TO_STOP_M:
                            return True
                return False
            print(f"  「{args.stop}」から乗る時刻表: "
                  f"いま={'ある' if reachable(cur_map) else 'ない'} → "
                  f"分割後={'できる' if reachable(new_reps) else 'まだできない'}")
        print("  ※この見込みは実測よりやや甘く出ます(実データでの検証: 近似37.8%に対し")
        print("    時刻表から数えた実測は33.4%)。分割前後を同じ物差しで比べる目安です")

    print("\n※ 1個あたりの大きさは今の平均からの外挿。分割後の地区は範囲が狭くなり")
    print("   乗り場候補が減るぶん、実際はこれより小さくなる見込み")
    print("※ このスクリプトは何も書き換えていない(実際に分割するのは")
    print("   python3 gap_map/make_subdistricts.py --apply ...)")


if __name__ == "__main__":
    main()
