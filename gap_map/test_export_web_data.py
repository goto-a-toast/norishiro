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
    StopIndex, boardable_directions, build_origins,
    frontier_rows, keep_useful_boards, pick_kantan_board,
    make_itinerary, build_entry, collapse_transfer_alternatives,
    board_options_for, alight_options_for, _slim_to_board, MAX_BOARD_OPTIONS,
)

R_EARTH_M = 6371000
M_PER_DEG_LAT = R_EARTH_M * 3.141592653589793 / 180  # build_network.haversine_mと同じ式の逆算


def meters_to_lat_deg(m: float) -> float:
    """赤道付近(経度差0)における「南北にm メートル」を緯度の度数に変換する簡易近似"""
    return m / M_PER_DEG_LAT


def make_stop_pattern(stop_id: str, route_name: str, terminal: str = "ELSEWHERE") -> Pattern:
    """stop_id → terminal と停まる、1系統ぶんのPattern(1本のTrip)を作る。
    終点は遠方(緯度90度)に置き、乗車候補としては絶対に拾われない終点役"""
    trip = Trip(trip_id=f"{stop_id}-{terminal}-trip", route_name=route_name,
                arrivals=[0, 10], departures=[0, 10])
    return Pattern(stop_ids=(stop_id, terminal), trips=[trip])


def make_network() -> Network:
    """代表点(緯度0・経度0)の近くに5停留所を置く(距離はすべて代表点からの直線距離):
      near      : 約100m。系統R1(終点ELSEWHERE)のみ → これが最寄り停になる
      sameplace : 約120m(near からは約20m。SAME_PLACE_M=150以内)。系統R1のみ
                  (「のりば違い」を想定。系統が同じでも同じ場所なので候補に入るべき)
      diffroute : 約400m(near からは約300m。SAME_PLACE_M超・MAX_WALK_TO_STOP_M以内)。
                  系統R2のみ → 最寄り停(R1のみ)には無い系統なので候補に入るべき
      redundant : 約500m(near からは約400m)。系統R1・終点も同じ → 最寄り停と系統も
                  方向も重複でしかないので、遠いなら候補に入らないべき(データ量爆発対策)
      revdir    : 約600m。系統R1だが終点がOTHERWAY(=同じ系統の別方向)→ 系統名は
                  最寄り停と同じでも方向が違うので候補に入るべき(2026-07-10 済生館の
                  「本町が漏れて帰りが1本になる」実害への対応)
    """
    stops_def = {
        "near": (meters_to_lat_deg(100), "R1", "ELSEWHERE"),
        "sameplace": (meters_to_lat_deg(120), "R1", "ELSEWHERE"),
        "diffroute": (meters_to_lat_deg(400), "R2", "ELSEWHERE"),
        "redundant": (meters_to_lat_deg(500), "R1", "ELSEWHERE"),
        "revdir": (meters_to_lat_deg(600), "R1", "OTHERWAY"),
    }
    patterns = []
    stop_routes = {"ELSEWHERE": [], "OTHERWAY": []}
    stops = {"ELSEWHERE": {"name": "ELSEWHERE", "lat": 90.0, "lon": 0.0},
             "OTHERWAY": {"name": "OTHERWAY", "lat": 90.0, "lon": 10.0}}
    for stop_id, (lat, route, terminal) in stops_def.items():
        patterns.append(make_stop_pattern(stop_id, route, terminal))
        idx = len(patterns) - 1
        stop_routes[stop_id] = [(idx, 0)]
        stop_routes[terminal].append((idx, 1))
        stops[stop_id] = {"name": stop_id, "lat": lat, "lon": 0.0}
    return Network(patterns=patterns, stop_routes=stop_routes, stops=stops, footpaths={})


def test_boardable_directions_returns_route_and_terminal_pairs():
    network = make_network()
    assert boardable_directions(network, "near") == {("R1", "ELSEWHERE")}
    assert boardable_directions(network, "diffroute") == {("R2", "ELSEWHERE")}
    # 同じ系統R1でも終点が違えば別の「方向」として区別される
    assert boardable_directions(network, "revdir") == {("R1", "OTHERWAY")}
    # 終点(ELSEWHERE)では乗れないので空集合
    assert boardable_directions(network, "ELSEWHERE") == set()


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
    assert "redundant" not in chosen_ids    # 系統も方向も重複するだけの遠い停は候補に入らない
    assert "revdir" in chosen_ids           # 同じ系統でも別方向(終点違い)の停は候補に入る
                                            # (2026-07-10 済生館→本町の取りこぼしへの対応)


def test_build_origins_without_expand_keeps_only_nearest_and_sameplace():
    """expand_by_route=False のとき: 別系統を持つ停でも候補にしない(最寄り+同じ場所のみ)。
    ※2026-07-10から施設側もexpand_by_route=Trueで呼ぶため既定では使わなくなったが、
    フラグの切り分けが正しいことは引き続き確認する"""
    network = make_network()
    stop_index = StopIndex(network)
    origins = build_origins(network, stop_index, [("f01", 0.0, 0.0, "テスト施設")])
    chosen_ids = {stop_id for stop_id, _walk_min, _name in origins["f01"]}

    assert chosen_ids == {"near", "sameplace"}


def test_collapse_transfer_groups_by_window_from_group_start():
    """乗換便の集約は「グループ先頭から20分」で区切る。「直前の便から20分」でつなぐと、
    乗換候補が数分おきにある市街地で1日ぶんが1グループに数珠つなぎされ、帰りの
    選択肢が代表1本に潰れる(2026-07-10 大郷⇔済生館で実害があった)"""
    def t_row(dep, arr):
        return {"dep": dep, "arr": arr, "board": "b", "board_walk_min": 1, "transfer": None}

    # 15分おきに一日中ある乗換候補(数珠つなぎなら全部1グループになってしまう並び)
    rows = [t_row(f"{h:02d}:{m:02d}", f"{h + 1:02d}:{m:02d}")
            for h in range(8, 18) for m in (0, 15, 30, 45)]
    out = collapse_transfer_alternatives(rows)
    # 20分窓なら1時間に2〜3グループでき、朝の1本だけに潰れない
    assert len(out) >= 20
    # 各グループの代表は残り、まとめられた分は alt_routes に件数が残る
    assert any(r.get("alt_routes") for r in out)


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


def test_pick_kantan_board_prefers_stop_that_serves_every_day_type():
    """2026-07-10 バグ修正: 土日しか便の無い停(コミュニティバス等)がいくら速くても、
    運行のある全ダイヤ種別をカバーする停を優先する。従来は全種別まぜこぜの中央値で
    速いB停を選び、_slim_to_board が平日を空にして「平日0便」になっていた"""
    outbound = {
        "weekday": [row("08:00", "08:40", "A", 3)],                # Aは毎日走る
        "saturday": [row("08:00", "08:20", "B", 3),                # Bは土曜だけ(速い)
                     row("09:00", "09:40", "A", 3)],
        "sunday_holiday": [],
    }
    assert pick_kantan_board(outbound) == "A"


def test_pick_kantan_board_falls_back_to_fastest_when_no_stop_serves_all():
    """全ダイヤ種別をカバーする停がひとつも無いペアでは従来どおり速い停を選ぶ
    (種別ごとの補完は build_entry 側のフォールバックが受け持つ)"""
    outbound = {
        "weekday": [row("08:00", "08:40", "A", 3)],
        "saturday": [row("08:00", "08:20", "B", 3)],
        "sunday_holiday": [],
    }
    assert pick_kantan_board(outbound) == "B"


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
def test_build_entry_prunes_dominated_inbound_rows():
    """帰り(inbound)は行単位のパレートフロンティアで間引く(2026-07-10。施設側の
    乗り場拡大による都心ペアの爆発対策)。「施設を出る時刻が同じか早いのに家に着くのが
    同じか遅い」便だけが落ち、単一の乗り場の単調な時刻表は1本も落ちない"""
    district = {"id": "d01", "lat": 0.0, "lon": 0.0}
    facility = {"id": "f01", "lat": 10.0, "lon": 10.0}
    in_rows = [
        row("08:00", "08:30", "honcho", 3),     # 施設を07:57に出て08:30着
        row("08:05", "08:28", "nanoka", 8),     # 07:57に出て08:28着 → 上の便を支配(落ちる相手)
        row("09:00", "09:30", "honcho", 3),     # 単調な後続便は残る
    ]
    empty = {"district_board": {}, "outbound": {}, "inbound": {}}
    per_daytype = {
        "weekday": {
            "district_board": {"d01": ("honcho", 3)},
            "outbound": {},
            "inbound": {"f01": {"d01": in_rows}},
        },
        "saturday": empty,
        "sunday_holiday": empty,
    }
    entry = build_entry(district, facility, per_daytype)
    kept = [(r["dep"], r["board"]) for r in entry["inbound"]["weekday"]]
    assert kept == [("08:05", "nanoka"), ("09:00", "honcho")]


def test_build_entry_slims_outbound_to_kantan_board():
    """build_entry は行き(outbound)を pick_kantan_board が選んだ1停の便だけに絞る。
    帰り(inbound)は間引かない(フロンティアに残る便はすべて保持)。
    かんたん・しっかり とも同じ1停の時刻表を見せる"""
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


def test_build_entry_keeps_weekday_when_kantan_board_runs_only_on_weekend():
    """2026-07-10 バグ修正の保険側: 全ダイヤ種別をカバーする停が無く、選ばれた停に
    便が無いダイヤ種別は、その種別だけ停を選び直して絞る(平日を空にしない)"""
    district = {"id": "d01", "lat": 0.0, "lon": 0.0}
    facility = {"id": "f01", "lat": 10.0, "lon": 10.0}
    week_rows = [row("08:00", "08:40", "平日停", 3), row("10:00", "10:40", "平日停", 3)]
    sat_rows = [row("08:00", "08:20", "土曜停", 3)]   # 速い→kantan_boardは土曜停になる
    per_daytype = {
        "weekday": {"district_board": {"d01": ("平日停", 3)},
                    "outbound": {"d01": {"f01": week_rows}}, "inbound": {}},
        "saturday": {"district_board": {"d01": ("土曜停", 3)},
                     "outbound": {"d01": {"f01": sat_rows}}, "inbound": {}},
        "sunday_holiday": {"district_board": {}, "outbound": {}, "inbound": {}},
    }
    entry = build_entry(district, facility, per_daytype)
    assert entry["kantan_board"] == "土曜停"
    # 旧実装はここが [] になり「平日0便」の誤った時刻表を出していた
    assert [r["board"] for r in entry["outbound"]["weekday"]] == ["平日停", "平日停"]
    assert [r["board"] for r in entry["outbound"]["saturday"]] == ["土曜停"]


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


# ===============================================================
# 対策1(広い地区): stops_index と地区の平坦化
# ===============================================================
from types import SimpleNamespace

from export_web_data import collect_stop_names, build_stops_index, flatten_districts


def test_collect_stop_names_walks_all_fields():
    to = {
        "f01": {"unreachable": True},
        "f02": {
            "board_walk_min": 3,
            "outbound": {"weekday": [{
                "board": "A停", "alight": "B停", "alight_place": "施設X",
                "transfer": {"at": "C停"},
                "board_options": [{"stop": "A停"}, {"stop": "D停"}],
                "alight_options": None,
            }]},
            "inbound": {"weekday": [{
                "board": "B停", "alight": "A停", "transfer": None,
                "board_options": None,
                "alight_options": [{"stop": "E停"}],
            }]},
        },
    }
    used = set()
    collect_stop_names(to, used)
    # alight_place(施設名)は集めない。unreachableは飛ばす
    assert used == {"A停", "B停", "C停", "D停", "E停"}


def test_build_stops_index_averages_same_name_and_sorts():
    net1 = SimpleNamespace(stops={
        "s1": {"name": "A停", "lat": 38.0, "lon": 140.0},
        "s2": {"name": "A停", "lat": 38.001, "lon": 140.001},  # のりば違い
        "s3": {"name": "使わない停", "lat": 39.0, "lon": 141.0},
    })
    net2 = SimpleNamespace(stops={
        "s4": {"name": "B停", "lat": 38.5, "lon": 140.5},
    })
    index = build_stops_index({"weekday": net1, "saturday": net2}, {"A停", "B停"})
    assert list(index) == ["A停", "B停"]           # 名前順=冪等
    assert index["A停"] == [38.0005, 140.0005]     # 同名は座標平均
    assert index["B停"] == [38.5, 140.5]
    assert "使わない停" not in index


def test_build_stops_index_keeps_distant_same_name_stops_apart():
    """2026-07-12 開発者報告「七日町が24km」の修正: 別の町にある同名停を平均すると
    「どちらでもない空中の一点」になる。1km超離れた同名停は複数座標で出力し、
    近い(1km以内の)のりば違いだけを平均する"""
    net = SimpleNamespace(stops={
        "s1": {"name": "七日町", "lat": 38.255, "lon": 140.340},   # 山形市
        "s2": {"name": "七日町", "lat": 38.256, "lon": 140.341},   # 山形市(のりば違い)
        "s3": {"name": "七日町", "lat": 38.670, "lon": 140.335},   # 遠くの別の町(約46km北)
    })
    index = build_stops_index({"weekday": net}, {"七日町"})
    v = index["七日町"]
    assert isinstance(v[0], list) and len(v) == 2          # 2か所の別地点
    assert v[0] == [38.2555, 140.3405]                     # 山形市側は2のりばの平均
    assert v[1] == [38.67, 140.335]
    # 再実行しても同じ並び(冪等)
    assert build_stops_index({"weekday": net}, {"七日町"})["七日町"] == v


def test_flatten_districts_appends_subs_with_parent_info():
    districts = [
        {"id": "d01", "name": "地区1", "municipality": "山形市"},
        {"id": "d15", "name": "東沢地区", "municipality": "山形市",
         "sub": [{"id": "d15a", "name": "東沢地区(にし)", "lat": 1, "lon": 2},
                 {"id": "d15b", "name": "東沢地区(ひがし)", "lat": 3, "lon": 4}]},
    ]
    flat = flatten_districts(districts)
    assert [d["id"] for d in flat] == ["d01", "d15", "d15a", "d15b"]  # 親も残す
    sub = flat[2]
    assert sub["municipality"] == "山形市" and sub["parent_id"] == "d15"
