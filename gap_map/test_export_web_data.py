# -*- coding: utf-8 -*-
"""
export_web_data.py の boardable_routes / build_origins の単体テスト。
GTFSを介さない、手作りの小さなネットワークで検証する(test_transit_core.pyと同じ方針)。

検証したいこと(2026-07-07 開発者指摘への対応):
  「乗るバス停は最寄りだが、目的地までの最短ルートを持つとは限らない」問題を、
  build_originsが「最寄り停には無い系統を持つ停」を距離で足切りせず候補に
  加えることで緩和できているか。同時に、「近いだけで系統が同じ停」は
  以前どおり候補から増えないこと(データ量爆発を防ぐ側の要件)も確認する。

実行方法: `python3 -m pytest gap_map/test_export_web_data.py -v`
"""

from transit_core import Network, Pattern, Trip
from export_web_data import (
    StopIndex, boardable_routes, build_origins,
    frontier_rows, keep_useful_boards, pick_kantan_board,
)

R_EARTH_M = 6371000
M_PER_DEG_LAT = R_EARTH_M * 3.141592653589793 / 180  # build_network.haversine_mと同じ式の逆算


def meters_to_lat_deg(m: float) -> float:
    """赤道付近(経度差0)における「南北にm メートル」を緯度の度数に変換する簡易近似"""
    return m / M_PER_DEG_LAT


def make_stop_pattern(stop_id: str, route_name: str) -> Pattern:
    """stop_id → ELSEWHERE と停まる、1系統ぶんのPattern(1本のTrip)を作る。
    ELSEWHEREは遠方(緯度90度)に置き、乗車候補としては絶対に拾われない終点役"""
    trip = Trip(trip_id=f"{stop_id}-trip", route_name=route_name,
                arrivals=[0, 10], departures=[0, 10])
    return Pattern(stop_ids=(stop_id, "ELSEWHERE"), trips=[trip])


def make_network() -> Network:
    """代表点(緯度0・経度0)の近くに4停留所を置く(距離はすべて代表点からの直線距離):
      near      : 約100m。系統R1のみ → これが最寄り停になる
      sameplace : 約120m(near からは約20m。SAME_PLACE_M=150以内)。系統R1のみ
                  (「のりば違い」を想定。系統が同じでも同じ場所なので候補に入るべき)
      diffroute : 約400m(near からは約300m。SAME_PLACE_M超・MAX_WALK_TO_STOP_M以内)。
                  系統R2のみ → 最寄り停(R1のみ)には無い系統なので候補に入るべき
      redundant : 約500m(near からは約400m)。系統R1のみ → 最寄り停と同じ系統の
                  重複でしかないので、遠いなら候補に入らないべき(データ量爆発対策)
    """
    stops_def = {
        "near": (meters_to_lat_deg(100), "R1"),
        "sameplace": (meters_to_lat_deg(120), "R1"),
        "diffroute": (meters_to_lat_deg(400), "R2"),
        "redundant": (meters_to_lat_deg(500), "R1"),
    }
    patterns = []
    stop_routes = {"ELSEWHERE": []}
    stops = {"ELSEWHERE": {"name": "ELSEWHERE", "lat": 90.0, "lon": 0.0}}
    for stop_id, (lat, route) in stops_def.items():
        patterns.append(make_stop_pattern(stop_id, route))
        idx = len(patterns) - 1
        stop_routes[stop_id] = [(idx, 0)]
        stop_routes["ELSEWHERE"].append((idx, 1))
        stops[stop_id] = {"name": stop_id, "lat": lat, "lon": 0.0}
    return Network(patterns=patterns, stop_routes=stop_routes, stops=stops, footpaths={})


def test_boardable_routes_returns_the_stops_own_routes():
    network = make_network()
    assert boardable_routes(network, "near") == {"R1"}
    assert boardable_routes(network, "diffroute") == {"R2"}
    # 終点(ELSEWHERE)では乗れないので空集合
    assert boardable_routes(network, "ELSEWHERE") == set()


def test_build_origins_expands_to_new_route_stop_but_skips_redundant_one():
    """expand_by_route=True(自宅側)のとき: 別系統を持つ停は遠くても候補に入り、
    系統が重複するだけの遠い停は入らない"""
    network = make_network()
    stop_index = StopIndex(network)
    origins = build_origins(network, stop_index, [("d01", 0.0, 0.0, "テスト地区")],
                            expand_by_route=True)
    chosen_ids = [stop_id for stop_id, _walk_min, _name in origins["d01"]]

    assert chosen_ids[0] == "near"          # 1件目は必ず最寄り
    assert "sameplace" in chosen_ids        # 同じ場所は系統が同じでも候補に入る
    assert "diffroute" in chosen_ids        # 別系統を持つ停は遠くても候補に入る
    assert "redundant" not in chosen_ids    # 系統が重複するだけの遠い停は候補に入らない


def test_build_origins_without_expand_keeps_only_nearest_and_sameplace():
    """expand_by_route=False(施設側の既定)のとき: 別系統を持つ停でも候補にしない
    (最寄り+同じ場所のみ。inbound肥大を防ぐための挙動)"""
    network = make_network()
    stop_index = StopIndex(network)
    origins = build_origins(network, stop_index, [("f01", 0.0, 0.0, "テスト施設")])
    chosen_ids = {stop_id for stop_id, _walk_min, _name in origins["f01"]}

    assert chosen_ids == {"near", "sameplace"}


# ===============================================================
# 乗り場の絞り込み(設計C。frontier_rows / keep_useful_boards / pick_kantan_board)
# ===============================================================
def row(dep: str, arr: str, board: str, walk: int) -> dict:
    """テスト用の最小itinerary辞書(frontier系が見るのは dep/arr/board/board_walk_min だけ)"""
    return {"dep": dep, "arr": arr, "board": board, "board_walk_min": walk, "transfer": None}


def test_frontier_keeps_genuine_tradeoffs_and_drops_dominated():
    # A: 8:00発・徒歩5分(家を7:55に出る)→ 8:30着
    # B: 8:02発・徒歩12分(家を7:50に出る)→ 8:25着(遅く出て早く着く=Aと両立しないトレードオフ)
    # C: 8:00発・徒歩12分(家を7:48に出る)→ 8:35着(Aに完全に負ける=落ちる)
    a = row("08:00", "08:30", "near", 5)
    b = row("08:02", "08:25", "far", 12)
    c = row("08:00", "08:35", "far", 12)
    front = frontier_rows([a, b, c])
    assert a in front and b in front
    assert c not in front


def test_keep_useful_boards_drops_a_far_and_slow_stop():
    # farslow停は「遠い(徒歩12分)のに遅い」ので、家を出る時刻・到着時刻の両方で
    # near停(徒歩3分)に完全に負ける=乗る理由がない → farslow停の便はまるごと落ちる。
    # (これが52MBの正体だった「別系統だが実は役に立たない遠い停」のケース)
    rows = [
        row("08:00", "08:30", "near", 3),      # 家を7:57に出て8:30着
        row("09:00", "09:30", "near", 3),
        row("08:00", "08:45", "farslow", 12),  # 家を7:48に出て8:45着(nearに完全敗北)
        row("09:00", "09:45", "farslow", 12),
    ]
    kept = keep_useful_boards(rows)
    boards = {r["board"] for r in kept}
    assert boards == {"near"}


def test_keep_useful_boards_keeps_a_far_but_faster_stop():
    # farfast停は遠い(徒歩12分)が、家を出る時刻はnearより早くとも到着が十分早い
    # =トレードオフとして成立するので残る(利用者に本当に速い選択肢を見せる)
    rows = [
        row("08:00", "08:40", "near", 3),      # 家を7:57に出て8:40着
        row("08:00", "08:25", "farfast", 12),  # 家を7:48に出て8:25着(15分早着)
    ]
    kept = keep_useful_boards(rows)
    assert {r["board"] for r in kept} == {"near", "farfast"}


def test_keep_useful_boards_keeps_all_when_single_board():
    # 帰り方向のように乗り場が1種類(施設名で統一)なら、何も落とさず全便残す
    rows = [row("08:00", "08:50", "病院", 4), row("09:00", "09:50", "病院", 4)]
    assert keep_useful_boards(rows) == rows


def test_pick_kantan_board_prefers_clearly_faster_farther_stop():
    # near停は近い(徒歩3分)が door-to-door 53分。fast停は遠い(徒歩10分)が38分で
    # 明確に速い(15分>切替しきい値) → かんたんモードは fast を選ぶ(最寄り≠最短への回答)
    outbound = {
        "weekday": [
            row("08:00", "08:50", "near", 3),   # 家を7:57に出て8:50着=53分
            row("08:02", "08:30", "fast", 10),  # 家を7:52に出て8:30着=38分
            row("09:02", "09:30", "fast", 10),
        ],
        "saturday": [],
        "sunday_holiday": [],
    }
    assert pick_kantan_board(outbound) == "fast"


def test_pick_kantan_board_stays_near_when_gain_is_marginal():
    # far停はほんの少し速いだけ(差がしきい値以内)→ なじみのある近い停を優先する
    # (開発者の当初の要望「普段使わない停を乗り場に出さないで」を尊重)
    outbound = {
        "weekday": [
            row("08:00", "08:40", "near", 3),   # door-to-door 43分
            row("08:00", "08:38", "far", 9),    # door-to-door 47分(近い方が実は速い例)
            row("09:00", "09:41", "far", 9),
        ],
        "saturday": [],
        "sunday_holiday": [],
    }
    assert pick_kantan_board(outbound) == "near"


def test_pick_kantan_board_none_when_no_outbound():
    assert pick_kantan_board({"weekday": [], "saturday": [], "sunday_holiday": []}) is None
