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
import os
from pathlib import Path

import make_subdistricts as ms

PROJECT_ROOT = Path(__file__).parent.parent
TIMETABLES_GLOB = str(PROJECT_ROOT / "webapp" / "data" / "timetables" / "*.json")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--far-dist", type=float, default=1200)
    ap.add_argument("--far-pop", type=float, default=150)
    ap.add_argument("--far-ratio", type=float, default=0.10)
    ap.add_argument("--max-k", type=int, default=6)
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
    for d in districts:
        if d["id"] in targets:
            # 分割の計算だけする(書き出しはしない)
            n = len(ms.split_district(mesh, d))
            detail.append((n, d["id"], d["name"], int((mesh["district_id"] == d["id"]).sum())))
            units += n
        else:
            units += 1

    for n, did, name, mc in sorted(detail, key=lambda x: (-x[0], x[1])):
        print(f"  {name}({did}) メッシュ{mc:3d} → サブ地区{n}個")

    print(f"\n分割する地区: {len(detail)} / {len(districts)}")
    print(f"時刻表の総数: {len(files)}個 → {units}個({units/len(files):.1f}倍)")
    print(f"データ量の見込み: 約{units*avg/1024/1024:.0f}MB(いま {cur_bytes/1024/1024:.0f}MB)")
    print("\n※ 1個あたりの大きさは今の平均からの外挿。分割後の地区は範囲が狭くなり")
    print("   乗り場候補が減るぶん、実際はこれより小さくなる見込み")
    print("※ このスクリプトは何も書き換えていない(実際に分割するのは")
    print("   python3 gap_map/make_subdistricts.py --apply ...)")


if __name__ == "__main__":
    main()
