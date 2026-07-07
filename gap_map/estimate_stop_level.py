# -*- coding: utf-8 -*-
"""F10-1: 停留所単位の事前計算(docs/plan_f10_stop_select.md 案A)の実測見積り。

★このスクリプトは読み取り専用(何も生成・変更しない)。
★GTFSフィードと data/mesh_districts.csv が必要なので、実行は開発機(Mac)で:
    python3 gap_map/estimate_stop_level.py

出力: 地区ごとの「徒歩圏内の停留所数(名前で統合)」と、
現行の地区JSON実サイズからの外挿による追加データ総量・生成時間の目安。
docs/plan_f10_stop_select.md §3 の判断基準(150MB・2時間)と突き合わせて
着工可否を判断する材料にする。
"""
from pathlib import Path

import numpy as np
import pandas as pd

import config
from compute_access import haversine_m_vec
from meshcode import meshcode_to_center

PROJECT_ROOT = Path(__file__).parent.parent
MESH_DISTRICTS_CSV = config.DATA_DIR / "mesh_districts.csv"   # make_districts.py の出力
TIMETABLES_DIR = PROJECT_ROOT / "webapp" / "data" / "timetables"

# 判断基準(docs/plan_f10_stop_select.md §3)
LIMIT_TOTAL_MB = 150
LIMIT_HOURS = 2.0
# 現行F3の生成時間の実測値(分)。Macでの体感に合わせて書き換えてよい
CURRENT_RUN_MIN = 5.0


def load_all_stops() -> pd.DataFrame:
    """全フィードの stops.txt を読み、停留所名で「のりば」を統合した一覧を返す。
    (見積り用途なので、名前が同じ停留所は1つと数える。実装時のキー設計は
    F10-2で改めて決める)"""
    frames = []
    for feed_dir in config.GTFS_FEED_DIRS:
        stops = pd.read_csv(feed_dir / "stops.txt", dtype=str)
        stops["stop_lat"] = stops["stop_lat"].astype(float)
        stops["stop_lon"] = stops["stop_lon"].astype(float)
        frames.append(stops[["stop_name", "stop_lat", "stop_lon"]])
    all_stops = pd.concat(frames, ignore_index=True)
    # 同名停留所(のりば違い・フィード重複)は代表1点に統合
    merged = all_stops.groupby("stop_name", as_index=False).first()
    print(f"停留所: 生データ{len(all_stops)}件 → 名前で統合後 {len(merged)}件")
    return merged


def main():
    mesh = pd.read_csv(MESH_DISTRICTS_CSV, dtype={"meshcode": str})
    need_cols = {"meshcode", "district_id"}
    assert need_cols <= set(mesh.columns), f"mesh_districts.csv の列が想定外: {list(mesh.columns)}"
    centers = mesh["meshcode"].map(meshcode_to_center)
    mesh["lat"] = centers.map(lambda t: t[0])
    mesh["lon"] = centers.map(lambda t: t[1])

    stops = load_all_stops()
    stop_lats = stops["stop_lat"].to_numpy()
    stop_lons = stops["stop_lon"].to_numpy()

    rows = []
    total_extra_mb = 0.0
    n_stop_sum = 0
    for did, g in mesh.groupby("district_id"):
        # 地区の全メッシュ中心から徒歩圏(800m)にある停留所の和集合
        within = np.zeros(len(stops), dtype=bool)
        for lat, lon in zip(g["lat"], g["lon"]):
            within |= haversine_m_vec(lat, lon, stop_lats, stop_lons) <= config.MAX_WALK_TO_STOP_M
        n_stops = int(within.sum())

        cur_path = TIMETABLES_DIR / f"{did}.json"
        cur_mb = cur_path.stat().st_size / 1e6 if cur_path.exists() else 0.0
        # 外挿: 停留所別JSONは「現行の地区JSONと同構造・同規模」が上限の目安。
        # 郊外の停留所は便数が少なくもっと小さいので0.7を掛けた控えめな見積りも併記
        extra_mb = n_stops * cur_mb
        rows.append((did, n_stops, cur_mb, extra_mb))
        total_extra_mb += extra_mb
        n_stop_sum += n_stops

    rows.sort(key=lambda r: -r[3])
    print("\n地区ID | 徒歩圏の停留所数 | 現行JSON(MB) | 追加見積り(MB)")
    for did, n_stops, cur_mb, extra_mb in rows:
        print(f"{did}   | {n_stops:4d} | {cur_mb:6.2f} | {extra_mb:8.1f}")

    est_low = total_extra_mb * 0.7
    print(f"\n合計: 停留所別ファイル {n_stop_sum}個")
    print(f"追加データ総量の見積り: {est_low:.0f}〜{total_extra_mb:.0f} MB"
          f"(判断基準: {LIMIT_TOTAL_MB}MB以下)")

    # 生成時間: 現行の探索は(地区×ダイヤ種別)単位。停留所単位にすると
    # 探索回数がおおよそ「平均停留所数」倍になる
    avg_stops = n_stop_sum / max(len(rows), 1)
    est_min = CURRENT_RUN_MIN * avg_stops
    print(f"生成時間の目安: 現行{CURRENT_RUN_MIN:.0f}分 × 平均{avg_stops:.1f}停留所"
          f" ≈ {est_min:.0f}分(判断基準: {LIMIT_HOURS:.0f}時間以下)")

    ok_size = est_low <= LIMIT_TOTAL_MB
    ok_time = est_min <= LIMIT_HOURS * 60
    print("\n判定(控えめ見積りベース):",
          "着工可の範囲" if (ok_size and ok_time) else
          "基準超過 → 案B(主要2〜3停留所への限定)を推奨。docs/plan_f10_stop_select.md §3参照")


if __name__ == "__main__":
    main()
