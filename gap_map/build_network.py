# -*- coding: utf-8 -*-
"""
GTFSフィード群を読み込み、transit_core.py(RAPTORエンジン)がすぐ使える
Network(停車パターン・停留所・徒歩乗換表)に前処理するモジュール。
詳しい方針は docs/plan_gap_map.md §3・§6.4 を参照。

やっていること:
  1. 各フィードのstops/stop_times/trips/calendar/calendar_datesを読む
  2. 指定した1日(TARGET_DATE)に実際に走るservice_idを求め、その日のtripsだけ残す
  3. stop_id・trip_idに「フィード名:」の接頭辞を付ける
     (フィードをまたいでIDが重複しても衝突しないように。§3の最重要ルール)
  4. 停車パターン(stop_idの並びが完全に同じ便のグループ)ごとにtripをまとめる
  5. 停留所間の徒歩乗換表(config.TRANSFER_WALK_M以内のペア)を作る

実行方法: プロジェクトのルートで `python3 gap_map/build_network.py`
  → data/network.pkl に保存される(pickle)
"""

import math
import pickle
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

import config
from transit_core import Network, Pattern, Trip

DAY_COLS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def to_minutes(hhmmss: str) -> int:
    """'06:52:00' → 412分(0時からの経過分)。深夜便の'25:10'等もそのまま扱える"""
    h, m = hhmmss.split(":")[:2]
    return int(h) * 60 + int(m)


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    """2地点の緯度経度から直線距離(メートル)を求める(ハバサイン公式)。
    step5_overpass_shops.py の haversine_km と同じ式(単位だけメートルにしている)"""
    r = 6371000  # 地球の半径[m]
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def service_ids_running_on(calendar: pd.DataFrame, cal_dates: pd.DataFrame | None,
                            date_str: str) -> set:
    """calendar.txt(曜日パターン+有効期間)とcalendar_dates.txt(例外)から、
    指定日(date_str, 'YYYYMMDD'形式)に実際に走るservice_idの集合を求める。
    make_pair_timetable.py の考え方(曜日パターン+例外の重ね合わせ)を、
    「1日だけ」判定できるように簡略化して移植したもの(§4)"""
    weekday = datetime.strptime(date_str, "%Y%m%d").weekday()   # 0=月〜6=日
    day_col = DAY_COLS[weekday]

    active = calendar[
        (calendar[day_col] == "1")
        & (calendar["start_date"] <= date_str)
        & (calendar["end_date"] >= date_str)
    ]
    service_ids = set(active["service_id"])

    if cal_dates is not None and not cal_dates.empty:
        today = cal_dates[cal_dates["date"] == date_str]
        removed = set(today.loc[today["exception_type"] == "2", "service_id"])
        added = set(today.loc[today["exception_type"] == "1", "service_id"])
        service_ids = (service_ids - removed) | added

    return service_ids


def _feed_prefix(feed_dir: Path) -> str:
    """フィードのディレクトリ名(例: gtfs_山形交通)から接頭辞(例: 山形交通:)を作る"""
    return feed_dir.name.removeprefix("gtfs_") + ":"


def load_feed(feed_dir: Path, target_date: str):
    """1フィード分のGTFSを読み、
      (接頭辞付きstop_id → 停留所情報 の辞書,
       停車パターン(stop_idタプル) → その日のTripのリース の辞書)
    を返す"""
    prefix = _feed_prefix(feed_dir)

    stops = pd.read_csv(feed_dir / "stops.txt", dtype=str)
    stop_times = pd.read_csv(feed_dir / "stop_times.txt", dtype=str)
    trips = pd.read_csv(feed_dir / "trips.txt", dtype=str)
    calendar = pd.read_csv(feed_dir / "calendar.txt", dtype=str)
    cal_dates_path = feed_dir / "calendar_dates.txt"
    cal_dates = pd.read_csv(cal_dates_path, dtype=str) if cal_dates_path.exists() else None
    routes = pd.read_csv(feed_dir / "routes.txt", dtype=str)

    # その日に走るservice_idだけに便を絞り込む
    service_ids = service_ids_running_on(calendar, cal_dates, target_date)
    use_trips = trips[trips["service_id"].isin(service_ids)]
    print(f"  [{feed_dir.name}] {target_date}に走るtrips: {len(use_trips)}本"
          f"(全{len(trips)}本中)")

    # 系統名(表示用): route_long_nameが無ければroute_short_nameを使う(既存コードと同じ考え方)
    route_names = routes.set_index("route_id")
    route_name_map = route_names["route_long_name"].fillna(route_names["route_short_name"])
    trip_route_map = use_trips.set_index("trip_id")["route_id"]

    # 停留所情報(接頭辞付き)。platform_codeがあれば参考情報として持たせる
    stops_dict = {}
    for _, row in stops.iterrows():
        stops_dict[prefix + row["stop_id"]] = {
            "name": row["stop_name"],
            "lat": float(row["stop_lat"]),
            "lon": float(row["stop_lon"]),
            "platform_code": row.get("platform_code"),
        }

    # その日に走る便のstop_timesだけ残し、停車順に並べる
    st = stop_times[stop_times["trip_id"].isin(use_trips["trip_id"])].copy()
    st["stop_sequence"] = st["stop_sequence"].astype(int)
    st = st.sort_values(["trip_id", "stop_sequence"])

    # 停車パターン(stop_idの並びが完全に同じtripのグループ)ごとにTripをまとめる
    pattern_trips = defaultdict(list)
    for trip_id, g in st.groupby("trip_id", sort=False):
        stop_ids = tuple(prefix + s for s in g["stop_id"])
        arrivals = [to_minutes(t) for t in g["arrival_time"]]
        departures = [to_minutes(t) for t in g["departure_time"]]
        route_id = trip_route_map.get(trip_id)
        route_name = route_name_map.get(route_id, route_id)
        pattern_trips[stop_ids].append(Trip(
            trip_id=prefix + trip_id, route_name=route_name,
            arrivals=arrivals, departures=departures,
        ))

    return stops_dict, pattern_trips


def build_footpaths(stops: dict) -> dict:
    """停留所間の徒歩乗換表(config.TRANSFER_WALK_M以内のペア)を作る。

    全停留所の総当たりだと件数が多いフィードで重くなるため、緯度経度を
    格子(約0.005度四方)に区切り、自分と隣接する格子の停留所だけを比較する(§6.4)。
    """
    grid_size = 0.005
    buckets = defaultdict(list)
    for stop_id, info in stops.items():
        key = (int(info["lon"] / grid_size), int(info["lat"] / grid_size))
        buckets[key].append(stop_id)

    footpaths = defaultdict(list)
    checked_pairs = set()
    for stop_id, info in stops.items():
        gx, gy = int(info["lon"] / grid_size), int(info["lat"] / grid_size)
        nearby_stop_ids = [
            sid
            for dx in (-1, 0, 1) for dy in (-1, 0, 1)
            for sid in buckets.get((gx + dx, gy + dy), [])
        ]
        for other_id in nearby_stop_ids:
            if other_id == stop_id:
                continue
            pair_key = tuple(sorted((stop_id, other_id)))
            if pair_key in checked_pairs:
                continue
            checked_pairs.add(pair_key)

            other = stops[other_id]
            dist_m = haversine_m(info["lat"], info["lon"], other["lat"], other["lon"])
            if dist_m <= config.TRANSFER_WALK_M:
                walk_min = dist_m * config.WALK_DETOUR / config.WALK_SPEED_M_PER_MIN
                footpaths[stop_id].append((other_id, walk_min))
                footpaths[other_id].append((stop_id, walk_min))

    return dict(footpaths)


def build_network(feed_dirs: list, target_date: str) -> Network:
    """feed_dirs(GTFSディレクトリのリスト)から、指定日のNetworkを組み立てる。

    feed_dirsを絞れば「このフィードだけのミニネットワーク」も作れる
    (M3の検証2で、上山市営バスだけのネットワークを作るのに使う)。
    """
    all_stops = {}
    all_pattern_trips = defaultdict(list)

    for feed_dir in feed_dirs:
        stops_dict, pattern_trips = load_feed(feed_dir, target_date)
        all_stops.update(stops_dict)
        for stop_ids, trip_list in pattern_trips.items():
            all_pattern_trips[stop_ids].extend(trip_list)

    patterns = []
    stop_routes = defaultdict(list)
    for stop_ids, trip_list in all_pattern_trips.items():
        trip_list.sort(key=lambda t: t.departures[0])   # 便を出発時刻の昇順に整列
        pattern_idx = len(patterns)
        patterns.append(Pattern(stop_ids=stop_ids, trips=trip_list))
        for pos, stop_id in enumerate(stop_ids):
            stop_routes[stop_id].append((pattern_idx, pos))

    print("徒歩乗換表を作成中...")
    footpaths = build_footpaths(all_stops)

    return Network(patterns=patterns, stop_routes=dict(stop_routes),
                    stops=all_stops, footpaths=footpaths)


def main():
    print(f"対象日: {config.TARGET_DATE}")
    print("GTFSフィードを読み込み中...")
    network = build_network(config.GTFS_FEED_DIRS, config.TARGET_DATE)

    n_trips = sum(len(p.trips) for p in network.patterns)
    n_footpath_pairs = sum(len(v) for v in network.footpaths.values()) // 2
    print(f"\n停留所数: {len(network.stops)}")
    print(f"停車パターン数: {len(network.patterns)}")
    print(f"その日のtrips合計: {n_trips}")
    print(f"徒歩乗換ペア数: {n_footpath_pairs}")

    config.NETWORK_PKL.parent.mkdir(exist_ok=True)
    with open(config.NETWORK_PKL, "wb") as f:
        pickle.dump(network, f)
    print(f"\n→ {config.NETWORK_PKL} に保存しました")


if __name__ == "__main__":
    main()
