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

from transit_core import Network, Pattern, Trip, Leg
from export_web_data import (
    StopIndex, boardable_routes, build_origins,
    frontier_rows, keep_useful_boards, pick_kantan_board,
    make_itinerary, build_entry,
    board_options_for, alight_options_for, _slim_to_board, MAX_BOARD_OPTIONS,
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


# ===============================================================
# 降車の表示(2026-07-08 開発者指摘「どこで降りるか分からない時刻表は不安」)
# ===============================================================
def _alight_network() -> Network:
    """降車停の実名を引くための最小ネットワーク(乗る停Oと降りる停A)"""
    stops = {
        "O": {"name": "八日町一丁目", "lat": 0.0, "lon": 0.0, "platform_code": None},
        "A": {"name": "済生館前", "lat": 0.0, "lon": 0.0, "platform_code": None},
    }
    return Network(patterns=[], stop_routes={}, stops=stops, footpaths={})


def test_make_itinerary_uses_real_stop_name_not_place_name_for_alight():
    """降車は行き先の表示名(施設名/地区名)ではなく、実際に降りるバス停名を出す。
    施設名/地区名と徒歩分は alight_place / alight_walk_min に別に持つ"""
    network = _alight_network()
    leg = Leg(kind="ride", from_stop="O", to_stop="A", depart=390, arrive=398,
              trip_id="山形交通:t1", route_name="N52")
    headsigns = {"山形交通:t1": "県立中央病院ゆき"}
    it = make_itinerary([leg], 398, network, "八日町一丁目", "山形市立病院済生館",
                        headsigns, board_walk_min=2, alight_walk_min=3)
    assert it["alight"] == "済生館前"              # 実際に降りるバス停名(標識と照合できる)
    assert it["alight_place"] == "山形市立病院済生館"  # 目的地の表示名は別フィールド
    assert it["alight_walk_min"] == 3
    assert it["board"] == "八日町一丁目"


def test_make_itinerary_alight_falls_back_to_place_when_stop_unknown():
    """降車stop_idがnetwork.stopsに無い保険ケースでは、行き先の表示名にフォールバックする"""
    network = _alight_network()
    leg = Leg(kind="ride", from_stop="O", to_stop="UNKNOWN", depart=390, arrive=398,
              trip_id="山形交通:t1", route_name="N52")
    headsigns = {"山形交通:t1": "県立中央病院ゆき"}
    it = make_itinerary([leg], 398, network, "八日町一丁目", "みゆき会病院",
                        headsigns, alight_walk_min=0)
    assert it["alight"] == "みゆき会病院"


# ===============================================================
# slim方式(2026-07-08 開発者決定): 行きは「一番いい乗り場」1停の便だけを保存する
# ===============================================================
def test_build_entry_slims_outbound_to_kantan_board():
    """build_entry は行き(outbound)を pick_kantan_board が選んだ1停の便だけに絞る。
    帰り(inbound)は触らない。かんたん・しっかり とも同じ1停の時刻表を見せる"""
    district = {"id": "d01", "lat": 0.0, "lon": 0.0}
    facility = {"id": "f01", "lat": 10.0, "lon": 10.0}   # 遠方=直接徒歩(direct_walk)は付かない
    out_rows = [
        row("08:00", "08:50", "near", 3),    # door-to-door 53分
        row("08:02", "08:30", "fast", 10),   # 38分(明確に速い→fastが選ばれる)
        row("09:02", "09:30", "fast", 10),
    ]
    empty = {"district_board": {}, "outbound": {}, "inbound": {}}
    per_daytype = {
        "weekday": {
            "district_board": {"d01": ("near", 3)},
            "outbound": {"d01": {"f01": out_rows}},
            "inbound": {},
        },
        "saturday": empty,
        "sunday_holiday": empty,
    }
    entry = build_entry(district, facility, per_daytype)
    assert entry["kantan_board"] == "fast"
    # 行きは fast の便だけ(near便は落ちる)。帰りは空のまま
    assert [r["board"] for r in entry["outbound"]["weekday"]] == ["fast", "fast"]
    assert entry["outbound"]["saturday"] == []


# ===============================================================
# board_options(2026-07-08 開発者指摘「同じバスが近くの停にも停まる」)
# ===============================================================
def test_board_options_for_lists_near_stops_sorted_by_walk():
    """降車位置より前で、家の徒歩圏内にある停を、発車時刻つき・徒歩が近い順に返す"""
    stops = {s: {"name": s.upper(), "lat": 0.0, "lon": 0.0} for s in ("a", "b", "c", "z")}
    net = Network(patterns=[], stop_routes={}, stops=stops, footpaths={})
    pat = Pattern(stop_ids=("a", "b", "c", "z"), trips=[])
    trip = Trip(trip_id="t", route_name="R", arrivals=[0, 5, 8, 20], departures=[0, 5, 8, 20])
    near = {"a": 10, "b": 3, "c": 6}   # z は徒歩圏外
    opts = board_options_for(net, pat, trip, up_to_pos=3, near_home=near)  # 位置3(z)は対象外
    assert [o["stop"] for o in opts] == ["B", "C", "A"]   # 徒歩が近い順
    assert opts[0]["dep"] == "00:05"                       # Bは位置1=departures[1]=5分
    assert opts[0]["walk_min"] == 3


def test_alight_options_for_lists_home_stops_after_boarding():
    """帰り: 乗車位置より後で、家の徒歩圏内にある停を、到着時刻つき・徒歩が近い順に返す"""
    stops = {s: {"name": s.upper(), "lat": 0.0, "lon": 0.0} for s in ("f", "x", "y", "z")}
    net = Network(patterns=[], stop_routes={}, stops=stops, footpaths={})
    pat = Pattern(stop_ids=("f", "x", "y", "z"), trips=[])
    trip = Trip(trip_id="t", route_name="R", arrivals=[0, 30, 34, 40], departures=[0, 30, 34, 40])
    home = {"y": 4, "z": 9}   # x(施設寄り)は家の徒歩圏外
    # from_pos=0(施設fで乗車)。以降で家の徒歩圏内=y,z
    opts = alight_options_for(net, pat, trip, from_pos=0, home_walks=home)
    assert [o["stop"] for o in opts] == ["Y", "Z"]   # 徒歩が近い順
    assert opts[0]["arr"] == "00:34"                  # Yは位置2=arrivals[2]=34分
    assert opts[0]["walk_min"] == 4


def test_board_options_for_capped():
    """近隣停が多い都心を想定し、近い順に MAX_BOARD_OPTIONS 件までに絞る"""
    n = MAX_BOARD_OPTIONS + 4
    ids = [f"s{i}" for i in range(n)] + ["z"]
    stops = {s: {"name": s, "lat": 0.0, "lon": 0.0} for s in ids}
    net = Network(patterns=[], stop_routes={}, stops=stops, footpaths={})
    pat = Pattern(stop_ids=tuple(ids), trips=[])
    trip = Trip(trip_id="t", route_name="R",
                arrivals=list(range(n + 1)), departures=list(range(n + 1)))
    near = {f"s{i}": i + 1 for i in range(n)}
    opts = board_options_for(net, pat, trip, up_to_pos=n, near_home=near)
    assert len(opts) == MAX_BOARD_OPTIONS


def test_slim_to_board_reprojects_dep_to_featured_stop():
    """featured停(kantan_board)を通る便を残し、その便の発車時刻・徒歩分を featured停に
    つけ替える。主停が別でも board_options に featured停があれば拾う"""
    rows = [
        {"board": "far", "dep": "08:02", "arr": "08:30", "board_walk_min": 9,
         "ride_min": 28, "transfer": None,
         "board_options": [{"stop": "far", "dep": "08:02", "walk_min": 9},
                           {"stop": "near", "dep": "08:05", "walk_min": 3}]},
        {"board": "x", "dep": "09:00", "arr": "09:20", "board_walk_min": 1,
         "ride_min": 20, "transfer": None,
         "board_options": [{"stop": "x", "dep": "09:00", "walk_min": 1}]},  # near無し→落ちる
    ]
    kept = _slim_to_board(rows, "near")
    assert len(kept) == 1
    assert kept[0]["board"] == "near"
    assert kept[0]["dep"] == "08:05"          # featured停の発車時刻につけ替え
    assert kept[0]["board_walk_min"] == 3
    assert kept[0]["ride_min"] == 25          # 到着08:30 − 発車08:05
