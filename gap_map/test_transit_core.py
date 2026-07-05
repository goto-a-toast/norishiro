# -*- coding: utf-8 -*-
"""
transit_core.py(RAPTORエンジン本体)の単体テスト。
GTFSを一切介さない、手作りの小さなネットワークで検証する
(計画書M3の完成条件1「手作りの3停留所・2便のミニ時刻表」に対応)。

実行方法: `python3 -m pytest gap_map/test_transit_core.py -v`
"""

from transit_core import Leg, Network, Pattern, Trip, raptor_search, reconstruct_path


def make_simple_network():
    """A→B→C と停まる路線に、2本の便(Trip1が先、Trip2が後)がある
    ミニネットワークを作る。時刻はすべて分(0時からの経過分)"""
    trip1 = Trip(trip_id="T1", route_name="1号線",
                 arrivals=[600, 610, 620], departures=[600, 610, 620])
    trip2 = Trip(trip_id="T2", route_name="1号線",
                 arrivals=[630, 640, 650], departures=[630, 640, 650])
    pattern = Pattern(stop_ids=("A", "B", "C"), trips=[trip1, trip2])

    stop_routes = {
        "A": [(0, 0)], "B": [(0, 1)], "C": [(0, 2)],
    }
    stops = {
        "A": {"name": "A停留所", "lat": 0, "lon": 0},
        "B": {"name": "B停留所", "lat": 0, "lon": 0},
        "C": {"name": "C停留所", "lat": 0, "lon": 0},
    }
    return Network(patterns=[pattern], stop_routes=stop_routes, stops=stops, footpaths={})


def test_direct_ride_catches_the_first_departing_trip():
    """10:00にAへ着けるなら、10:00発のTrip1に乗ってB(10:10)・C(10:20)に着くはず"""
    network = make_simple_network()
    result = raptor_search(network, {"A": 600}, max_transfers=0)
    assert result["B"]["arrival"] == 610
    assert result["C"]["arrival"] == 620
    assert result["B"]["leg"].trip_id == "T1"


def test_missed_trip_catches_the_next_one():
    """10:05にAへ着くと10:00発のTrip1には間に合わないので、10:30発のTrip2に乗るはず"""
    network = make_simple_network()
    result = raptor_search(network, {"A": 605}, max_transfers=0)
    assert result["C"]["arrival"] == 650
    assert result["C"]["leg"].trip_id == "T2"


def test_zero_transfer_search_does_not_reach_beyond_direct_trip():
    """乗換なし(max_transfers=0)では、徒歩でしか行けない停留所には到達しないこと"""
    network = make_simple_network()
    network.footpaths["C"] = [("D", 5)]  # Cから徒歩5分でDへ行ける
    result = raptor_search(network, {"A": 600}, max_transfers=0)
    assert "D" not in result


def test_one_transfer_reaches_stop_via_walk_and_second_trip():
    """C(到着620)から徒歩5分+乗換3分=628分でDに着き、D発630分のTrip3に乗ってEに着くはず"""
    network = make_simple_network()
    network.footpaths["C"] = [("D", 5)]

    trip3 = Trip(trip_id="T3", route_name="2号線", arrivals=[630, 645], departures=[630, 645])
    pattern2 = Pattern(stop_ids=("D", "E"), trips=[trip3])
    network.patterns.append(pattern2)
    network.stop_routes["D"] = [(1, 0)]
    network.stop_routes["E"] = [(1, 1)]
    network.stops["D"] = {"name": "D停留所", "lat": 0, "lon": 0}
    network.stops["E"] = {"name": "E停留所", "lat": 0, "lon": 0}

    result = raptor_search(network, {"A": 600}, max_transfers=1)
    assert result["D"]["arrival"] == 620 + 3 + 5   # 628(徒歩+乗換のバッファ)
    assert result["E"]["arrival"] == 645

    legs = reconstruct_path(result, "E")
    assert [leg.kind for leg in legs] == ["ride", "walk", "ride"]
    assert legs[0].trip_id == "T1"       # A→B→Cの直通便
    assert legs[1].from_stop == "C" and legs[1].to_stop == "D"   # 徒歩の乗換
    assert legs[2].trip_id == "T3"       # D→Eの便


def test_same_stop_transfer_needs_minimum_transfer_time():
    """同じ停留所での乗換にも、min_transfer_min分のバッファが必要なこと。
    C到着620分ちょうどに出るTrip4には間に合わず、次の630分発に乗るはず"""
    network = make_simple_network()
    trip_too_soon = Trip(trip_id="T4", route_name="3号線",
                          arrivals=[620, 625], departures=[620, 625])
    trip_ok = Trip(trip_id="T5", route_name="3号線",
                   arrivals=[630, 635], departures=[630, 635])
    pattern2 = Pattern(stop_ids=("C", "F"), trips=[trip_too_soon, trip_ok])
    network.patterns.append(pattern2)
    network.stop_routes["C"].append((1, 0))
    network.stop_routes["F"] = [(1, 1)]
    network.stops["F"] = {"name": "F停留所", "lat": 0, "lon": 0}

    result = raptor_search(network, {"A": 600}, max_transfers=1, min_transfer_min=3)
    assert result["F"]["arrival"] == 635
    legs = reconstruct_path(result, "F")
    assert legs[-1].trip_id == "T5"


def test_later_improvement_does_not_corrupt_an_earlier_reconstructed_path():
    """回帰テスト: 同じラウンド内で、ある停留所(C)への到着が「別の経路」で
    あとから更に良い時刻に更新されても、その停留所を「乗換の踏み台」として
    先に使っていた別の経路(C→Eの区間)の復元結果が、後からの改善で
    書き換わってしまわないこと。

    ネットワーク:
      Pattern1: A→B(0分発・10分着)
      Pattern2: B→C(バス、13分発・15分着)  ← 同ラウンドでCへの「より良い」到着
      Pattern3: C→E(バス、20分発・25分着)  ← Cの徒歩乗換(18分)を踏み台に使う
      footpath: B→C(徒歩5分)              ← 乗換バッファ3分+徒歩5分で18分に到着

    Cへは「徒歩で18分」と「Pattern2のバスで15分」の両方で到達できるが、
    Eへの経路(Pattern3)は徒歩到着(18分)を踏み台にして20分発の便に乗ったもの。
    Cの到着時刻がPattern2の発見(15分)で更新されても、Eへの経路の
    「Cに18分に着いた」という事実は変わらないはず"""
    trip_ab = Trip(trip_id="AB", route_name="1号線", arrivals=[0, 10], departures=[0, 10])
    pattern_ab = Pattern(stop_ids=("A", "B"), trips=[trip_ab])

    trip_bc = Trip(trip_id="BC", route_name="2号線", arrivals=[13, 15], departures=[13, 15])
    pattern_bc = Pattern(stop_ids=("B", "C"), trips=[trip_bc])

    trip_ce = Trip(trip_id="CE", route_name="3号線", arrivals=[20, 25], departures=[20, 25])
    pattern_ce = Pattern(stop_ids=("C", "E"), trips=[trip_ce])

    network = Network(
        patterns=[pattern_ab, pattern_bc, pattern_ce],
        stop_routes={
            "A": [(0, 0)], "B": [(0, 1), (1, 0)], "C": [(1, 1), (2, 0)], "E": [(2, 1)],
        },
        stops={s: {"name": s, "lat": 0, "lon": 0} for s in "ABCE"},
        footpaths={"B": [("C", 5)], "C": [("B", 5)]},
    )

    result = raptor_search(network, {"A": 0}, max_transfers=1, min_transfer_min=3)

    # Cへは、Pattern2(バス)経由の15分の方が、徒歩経由の18分より早く着く
    assert result["C"]["arrival"] == 15
    assert reconstruct_path(result, "C")[-1].trip_id == "BC"

    # Eへの経路は、あくまで「Cに徒歩で18分に着いた」ことを踏み台にした結果のまま
    # (Cの15分への更新に巻き込まれて、Eの所要時間が縮んだりしない)
    legs_e = reconstruct_path(result, "E")
    assert [leg.kind for leg in legs_e] == ["ride", "walk", "ride"]
    assert legs_e[1].to_stop == "C" and legs_e[1].arrive == 18
    assert legs_e[2].depart == 20 and legs_e[2].arrive == 25
    assert result["E"]["arrival"] == 25
