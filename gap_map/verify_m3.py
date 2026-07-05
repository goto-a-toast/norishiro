# -*- coding: utf-8 -*-
"""
M3(build_network.py + transit_core.py)の3段階検証を行うスクリプト。

  検証1(正解照合): 山形駅前→県立中央病院を平日8:00発で検索し、
                    時刻表PDFの結果(N52・C2 / T20・D15系統、乗車約27分)と突き合わせる
  検証2(手計算可能な小例): 上山市営バスだけのミニネットワークで、
                          かみのやま温泉駅前→ヤマザワ前(乗換0回)
  検証3(乗換1回の実例): 上山市内→県立中央病院のような乗換が必要なODペアの
                        経路・待ち時間をレビューできる形で表示する

実装は (a)乗換なし探索→検証1・検証2 → (b)乗換1回→検証3 の順で段階的に進める。

実行方法: プロジェクトのルートで `python3 gap_map/verify_m3.py`
"""

from pathlib import Path

import build_network
import config
import transit_core

PROJECT_ROOT = Path(__file__).parent.parent


def fmt_time(minutes: int) -> str:
    """分(0時からの経過分)を'H:MM'形式にする"""
    return f"{minutes // 60}:{minutes % 60:02d}"


def stop_ids_by_name(network, name: str) -> list:
    """停留所名(完全一致)から、該当するstop_id(のりば違い含む全部)を返す"""
    return [sid for sid, info in network.stops.items() if info["name"] == name]


def format_itinerary(network, legs: list) -> str:
    """Legのリストを、人が読める経路の文章にする(検証3のレビュー用表示にも使う)"""
    if not legs:
        return "  (経路なし)"
    lines = []
    for leg in legs:
        board_info = network.stops[leg.from_stop]
        alight_info = network.stops[leg.to_stop]
        board_name = board_info["name"]
        alight_name = alight_info["name"]
        if leg.kind == "ride":
            lines.append(
                f"  [乗車 {leg.route_name}] {board_name} {fmt_time(leg.depart)}発 "
                f"→ {alight_name} {fmt_time(leg.arrive)}着"
                f"  (trip_id={leg.trip_id})"
            )
        else:
            lines.append(
                f"  [徒歩] {board_name} {fmt_time(leg.depart)} "
                f"→ {alight_name} {fmt_time(leg.arrive)}"
                f"  ({leg.arrive - leg.depart}分、乗換バッファ込み)"
            )
    return "\n".join(lines)


# ===============================================================
# 検証1: 山形駅前→県立中央病院(平日8:00発、乗換なし)
# ===============================================================
def verify1():
    print("=" * 70)
    print("検証1: 山形駅前 → 県立中央病院(平日8:00発、乗換なし探索)")
    print("=" * 70)

    network = build_network.build_network(
        [PROJECT_ROOT / "gtfs_山形交通"], config.TARGET_DATE)

    board_ids = stop_ids_by_name(network, "山形駅前")
    dest_ids = set(stop_ids_by_name(network, "県立中央病院"))
    print(f"山形駅前のstop_id(のりば数={len(board_ids)}): {board_ids}")
    print(f"県立中央病院のstop_id: {sorted(dest_ids)}")

    result = transit_core.raptor_search(
        network, {sid: 8 * 60 for sid in board_ids}, max_transfers=0)

    reached = [d for d in dest_ids if d in result]
    if not reached:
        print("\n★到達不能でした。バグの疑いがあります★")
        return False

    dest = min(reached, key=lambda d: result[d]["arrival"])
    arrival = result[dest]["arrival"]
    legs = transit_core.reconstruct_path(result, dest)

    print(f"\n8:00発で最速の便: 到着 {fmt_time(arrival)}"
          f"(出発8:00からの所要 {arrival - 8 * 60}分)")
    print(format_itinerary(network, legs))

    ride_legs = [leg for leg in legs if leg.kind == "ride"]
    route_names = {leg.route_name for leg in ride_legs}
    ride_minutes = sum(leg.arrive - leg.depart for leg in ride_legs)
    board_stop = ride_legs[0].from_stop if ride_legs else None
    platform = network.stops[board_stop].get("platform_code") if board_stop else None

    print("\n--- 時刻表PDFとの突き合わせ ---")
    print(f"  使われた系統: {route_names}  (PDFの想定: 'Ｎ５２・Ｃ２' または 'Ｔ２０・Ｄ１５')")
    print(f"  乗車時間: {ride_minutes}分  (PDFの想定: 約27分)")
    print(f"  乗車したのりば: {platform}番のりば  (PDFの想定: 3番のりば)")

    ok = (
        len(ride_legs) == 1
        and any(code in "".join(route_names) for code in ["Ｎ５２", "Ｃ２", "Ｔ２０", "Ｄ１５"])
        and abs(ride_minutes - 27) <= 3
        and platform == "３"
    )
    print(f"\n検証1: {'PASS' if ok else 'FAIL(要確認)'}")
    return ok


# ===============================================================
# 検証2: 上山市営バスだけのミニネットワーク(乗換0回)
# ===============================================================
def verify2():
    print("\n" + "=" * 70)
    print("検証2: 上山市営バスだけのミニネットワーク "
          "かみのやま温泉駅前 → ヤマザワ前(乗換なし)")
    print("=" * 70)

    network = build_network.build_network(
        [PROJECT_ROOT / "gtfs_上山市"], config.TARGET_DATE)
    print(f"(ミニネットワークの規模: 停留所{len(network.stops)}件・"
          f"パターン{len(network.patterns)}件・停車パターン内trips合計"
          f"{sum(len(p.trips) for p in network.patterns)}本)")

    board_ids = stop_ids_by_name(network, "かみのやま温泉駅前")
    dest_ids = set(stop_ids_by_name(network, "ヤマザワ前"))

    result = transit_core.raptor_search(
        network, {sid: 6 * 60 for sid in board_ids}, max_transfers=0)

    reached = [d for d in dest_ids if d in result]
    if not reached:
        print("\n★到達不能でした。バグの疑いがあります★")
        return False

    dest = min(reached, key=lambda d: result[d]["arrival"])
    legs = transit_core.reconstruct_path(result, dest)
    print(f"\n6:00発で最初に乗れる便: 到着 {fmt_time(result[dest]['arrival'])}")
    print(format_itinerary(network, legs))

    print("\n--- GTFS生データとの突き合わせ(かみのやま温泉駅前→ヤマザワ前の全便) ---")
    import pandas as pd
    stop_times = pd.read_csv(PROJECT_ROOT / "gtfs_上山市" / "stop_times.txt", dtype=str)
    trips = pd.read_csv(PROJECT_ROOT / "gtfs_上山市" / "trips.txt", dtype=str)
    stops = pd.read_csv(PROJECT_ROOT / "gtfs_上山市" / "stops.txt", dtype=str)
    board_raw = stops.loc[stops.stop_name == "かみのやま温泉駅前", "stop_id"].tolist()
    alight_raw = stops.loc[stops.stop_name == "ヤマザワ前", "stop_id"].tolist()
    st = stop_times[stop_times.stop_id.isin(board_raw + alight_raw)].copy()
    st["stop_sequence"] = st["stop_sequence"].astype(int)
    for trip_id, g in st.groupby("trip_id"):
        g = g.sort_values("stop_sequence")
        print(f"  trip={trip_id}: " +
              " → ".join(f"{r.stop_id}@{r.departure_time}" for r in g.itertuples()))

    print("\n上の生データ一覧と見比べて、6:00以降で最初にかみのやま温泉駅前から"
          "ヤマザワ前へ着く便が、上の探索結果と一致するか確認してください。")
    return True


# ===============================================================
# 検証3: 乗換1回が必要な例(上山市内→県立中央病院)
# ===============================================================
def verify3():
    print("\n" + "=" * 70)
    print("検証3: 上山市内(ヤマザワ前) → 県立中央病院(乗換1回まで許可)")
    print("=" * 70)

    network = build_network.build_network(config.GTFS_FEED_DIRS, config.TARGET_DATE)

    board_ids = stop_ids_by_name(network, "ヤマザワ前")
    dest_ids = set(stop_ids_by_name(network, "県立中央病院"))
    print(f"出発: ヤマザワ前(上山市) のstop_id: {board_ids}")

    result = transit_core.raptor_search(
        network, {sid: 7 * 60 for sid in board_ids},
        max_transfers=1, min_transfer_min=config.MIN_TRANSFER_MIN)

    reached = [d for d in dest_ids if d in result]
    if not reached:
        print("\n★到達不能でした★")
        return False

    dest = min(reached, key=lambda d: result[d]["arrival"])
    legs = transit_core.reconstruct_path(result, dest)
    print(f"\n7:00発で最速: 到着 {fmt_time(result[dest]['arrival'])}"
          f"(所要 {result[dest]['arrival'] - 7 * 60}分)")
    print(format_itinerary(network, legs))

    n_rides = sum(1 for leg in legs if leg.kind == "ride")
    n_walks = sum(1 for leg in legs if leg.kind == "walk")
    print(f"\n乗車区間数: {n_rides}  徒歩(乗換)区間数: {n_walks}")
    print("上の経路・乗換地点・待ち時間が現実的かどうか、レビューをお願いします。")
    return True


if __name__ == "__main__":
    print("### (a) 乗換なし探索の検証 ###\n")
    ok1 = verify1()
    ok2 = verify2()

    if not (ok1 and ok2):
        print("\n乗換なし探索(検証1・検証2)が通っていないため、"
              "検証3(乗換1回)には進みません。")
    else:
        print("\n\n### (b) 乗換1回探索の検証 ###\n")
        verify3()
