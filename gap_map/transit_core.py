# -*- coding: utf-8 -*-
"""
到達時間計算エンジン(簡易RAPTOR)。

★設計上の最重要ルール(docs/plan_gap_map.md §1):
  このファイルは「バス停」と「時刻」だけを知っていて、
  メッシュ・施設・人口・GTFSファイルの読み方など、他の概念やモジュールには
  一切依存しない。GTFSを読んでこのファイルが扱える形(Network)に変換するのは
  build_network.py の仕事。

RAPTORとは(概要):
  「乗り換え回数0回で行ける所→1回で行ける所→2回で…」と、範囲を輪のように
  広げていく探索方法。1ラウンド進むごとに「乗換1回」増える。

このファイルが扱うデータの形:
  Trip    : 1本の便。停車パターン内での各停留所の到着・出発時刻(分)を持つ
  Pattern : 停車パターン1つ分。停まる停留所の並び(stop_ids)と、そこを通る
            便(Trip)の一覧(出発時刻の昇順に並んでいること)
  Network : Pattern一覧、「どの停留所がどのパターンの何番目か」の索引、
            停留所の情報(名前・緯度経度)、徒歩で乗り換えられる停留所のペア(footpaths)
  Leg     : 経路の1区間(乗車 or 徒歩)の記録。「直前の区間(prev)」を作成した
            その瞬間の状態でLeg自身に埋め込み、連結リストにしておく。
            (あとで「直前の区間は何だったか」を停留所IDだけで引き直すと、
            同じ停留所が別ラウンド・別経路でさらに良い時刻に更新されたときに
            誤って未来の情報と接続してしまう。経路ごとに完結した連結リストに
            しておくことで、この食い違いを防ぐ)
"""

from dataclasses import dataclass, field


@dataclass
class Trip:
    """1本の便。arrivals/departures はパターンのstop_idsと同じ並び・同じ長さで、
    各停留所での到着・出発時刻を「0時からの分」で持つ"""
    trip_id: str
    route_name: str
    arrivals: list       # list[int]
    departures: list     # list[int]


@dataclass
class Pattern:
    """停車パターン1つ分(停まる停留所の並びが完全に同じ便のグループ)。
    trips は departures[0](先頭停留所の出発時刻)の昇順に並んでいる必要がある"""
    stop_ids: tuple       # tuple[str, ...]
    trips: list           # list[Trip]


@dataclass
class Network:
    """エンジンが探索に使う、前処理済みのネットワーク全体"""
    patterns: list                     # list[Pattern]
    stop_routes: dict                  # dict[str stop_id, list[tuple[int pattern_idx, int position]]]
    stops: dict                        # dict[str stop_id, dict(name, lat, lon, ...)]
    footpaths: dict = field(default_factory=dict)  # dict[str stop_id, list[tuple[str other_stop_id, float walk_min]]]


@dataclass
class Leg:
    """経路の1区間。kind="ride"(乗車)または"walk"(徒歩)。
    prev には、この区間に乗る/歩く直前の区間(Leg)がそのまま入っている
    (出発点ならNone)。reconstruct_path() はこの prev を辿るだけでよい"""
    kind: str
    from_stop: str
    to_stop: str
    depart: int             # 分
    arrive: int              # 分
    trip_id: str = None
    route_name: str = None
    prev: "Leg" = None


# ===============================================================
# 探索本体
# ===============================================================
def raptor_search(network: Network, initial_stops: dict, max_transfers: int = 0,
                   min_transfer_min: int = 3) -> dict:
    """指定した初期停留所群から、全停留所への最早到着時刻を求める。

    引数:
      initial_stops   : {stop_id: その停留所に立てる時刻(分)}
      max_transfers   : 乗換の上限回数(0なら直通のみ探索する)
      min_transfer_min: 乗換1回あたりの最小所要時間(分)。徒歩時間はこれに加算される

    戻り値: {stop_id: {"arrival": 最早到着時刻(分), "leg": 直前の区間(Leg)。
              初期停留所ならNone}}
      経路全体を知りたい場合は reconstruct_path() を使う。
    """
    best_arrival = dict(initial_stops)
    best_leg = {stop_id: None for stop_id in initial_stops}

    # 「その停留所から新しく乗車行動を起こせる時刻」と「なぜそれが可能か(直前の区間)」。
    # このラウンドの間は書き換えない(=このラウンドの乗車判断はすべて、ラウンド開始時点の
    # 状態だけを根拠にする)。これが「同じラウンド内の別経路の改善で、直前区間が入れ替わって
    # しまう」不具合を防ぐポイント
    boarding_times = dict(initial_stops)
    boarding_leg = {stop_id: None for stop_id in initial_stops}

    for round_no in range(max_transfers + 1):
        if not boarding_times:
            break

        # 今回のboarding_timesの停留所が関わる停車パターンだけを調べれば十分
        touched_patterns = set()
        for stop_id in boarding_times:
            for pattern_idx, _ in network.stop_routes.get(stop_id, []):
                touched_patterns.add(pattern_idx)

        round_updates = {}   # stop_id -> (到着時刻, Leg)  ※Leg.prevはこの時点で確定済み
        for pattern_idx in sorted(touched_patterns):
            _scan_pattern(network.patterns[pattern_idx], boarding_times, boarding_leg, round_updates)

        # このラウンドで到着時刻が更新された停留所を確定させる
        newly_by_ride = set()
        for stop_id, (arrival, leg) in round_updates.items():
            if arrival < best_arrival.get(stop_id, float("inf")):
                best_arrival[stop_id] = arrival
                best_leg[stop_id] = leg
                newly_by_ride.add(stop_id)

        # 乗換回数が上限に達した、または何も更新が無かったら、これ以上ラウンドを回さない
        if round_no == max_transfers or not newly_by_ride:
            break

        # 乗換(同じ停留所での乗換 + 徒歩で近くの停留所へ)を反映し、
        # 次ラウンドで使うboarding_times/boarding_legを作る
        next_boarding_times = {}
        next_boarding_leg = {}
        # 2026-07-06: stop_idの文字列setはPythonのハッシュランダム化で反復順が
        # 実行のたびに変わりうる。到着時刻が同着のときにどちらのLegが勝つかが
        # この順序に依存してしまい、export_web_data.py(F3)の「毎回同じ出力になる」
        # という条件を満たせなかった(到着時刻そのものは常に同じ値になり、
        # 影響するのは同着時のタイブレークだけ)。sorted()で走査順を固定して解消する
        for stop_id in sorted(newly_by_ride):
            arrival = best_arrival[stop_id]
            ride_leg = best_leg[stop_id]   # このラウンドで確定した「乗車」区間(prev込みで完成している)

            # (a) 同じ停留所で別の便に乗り換える場合(乗換の最小時間だけ後にずれる)
            t_same = arrival + min_transfer_min
            if t_same < next_boarding_times.get(stop_id, float("inf")):
                next_boarding_times[stop_id] = t_same
                next_boarding_leg[stop_id] = ride_leg

            # (b) 徒歩で近くの停留所へ移動して乗り換える場合
            for other_id, walk_min in network.footpaths.get(stop_id, []):
                t_walk = arrival + min_transfer_min + round(walk_min)
                if t_walk < next_boarding_times.get(other_id, float("inf")):
                    walk_leg = Leg(kind="walk", from_stop=stop_id, to_stop=other_id,
                                   depart=arrival, arrive=t_walk, prev=ride_leg)
                    next_boarding_times[other_id] = t_walk
                    next_boarding_leg[other_id] = walk_leg
                    # 徒歩そのものが「その停留所への最速到着」になることもあるので反映する
                    if t_walk < best_arrival.get(other_id, float("inf")):
                        best_arrival[other_id] = t_walk
                        best_leg[other_id] = walk_leg

        boarding_times = next_boarding_times
        boarding_leg = next_boarding_leg

    return {stop_id: {"arrival": best_arrival[stop_id], "leg": best_leg[stop_id]}
            for stop_id in best_arrival}


def _scan_pattern(pattern: Pattern, boarding_times: dict, boarding_leg: dict,
                   round_updates: dict) -> None:
    """1つの停車パターンを停留所の並び順に走査する(RAPTORのコア処理)。

    やっていること:
      その停留所に到着可能な時刻(boarding_times)が分かっていれば、
      そこから乗れる一番早い便を探して「乗車」する。すでに何かの便に
      乗っている(乗車中の)場合は、後続の停留所への到着時刻を記録していく。
      乗車した瞬間の「直前区間」(boarding_leg)をLegにそのまま埋め込むので、
      あとから他の停留所の状態が変わっても、この区間の由来は変化しない。
    """
    current_trip = None
    board_pos = None
    board_stop_id = None
    board_prev_leg = None

    for pos, stop_id in enumerate(pattern.stop_ids):
        # (1) この停留所でもっと早い便に乗れないか確認する
        if stop_id in boarding_times:
            earliest_time = boarding_times[stop_id]
            candidate = _earliest_boardable_trip(pattern, pos, earliest_time)
            if candidate is not None and (
                current_trip is None or candidate.departures[pos] < current_trip.departures[board_pos]
            ):
                current_trip = candidate
                board_pos = pos
                board_stop_id = stop_id
                board_prev_leg = boarding_leg[stop_id]

        # (2) 今乗車中の便があれば、この停留所への到着を記録する(乗った本人の停留所は除く)
        if current_trip is not None and pos > board_pos:
            arrival = current_trip.arrivals[pos]
            prev_update = round_updates.get(stop_id)
            if prev_update is None or arrival < prev_update[0]:
                leg = Leg(
                    kind="ride",
                    from_stop=board_stop_id, to_stop=stop_id,
                    depart=current_trip.departures[board_pos], arrive=arrival,
                    trip_id=current_trip.trip_id, route_name=current_trip.route_name,
                    prev=board_prev_leg,
                )
                round_updates[stop_id] = (arrival, leg)


def _earliest_boardable_trip(pattern: Pattern, pos: int, earliest_time: int):
    """パターン内のtripsは出発時刻(先頭停留所)の昇順に並んでいる前提で、
    位置posから時刻earliest_time以降に乗れる、一番早い便を返す(無ければNone)。
    便同士が追い越さない(=どの停留所でも順序が変わらない)という前提のGTFSダイヤなら、
    先頭から順に見ていくだけで正しく最速便が見つかる"""
    for trip in pattern.trips:
        if trip.departures[pos] >= earliest_time:
            return trip
    return None


# ===============================================================
# 経路の復元
# ===============================================================
def reconstruct_path(result: dict, dest_stop_id: str) -> list:
    """raptor_searchの結果から、指定した停留所までの経路(Legのリスト、
    出発→到着の順)を復元する。Leg.prev を出発点(None)まで辿るだけでよい"""
    entry = result.get(dest_stop_id)
    if entry is None:
        return []

    legs = []
    leg = entry["leg"]
    while leg is not None:
        legs.append(leg)
        leg = leg.prev
    legs.reverse()
    return legs
