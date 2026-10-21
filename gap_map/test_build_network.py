# -*- coding: utf-8 -*-
"""build_network.py が GTFS の pickup_type / drop_off_type(「この停では乗れない/降りられない」)を
読み分けることのテスト(2026-10-21 追加)。手作りの小さなGTFSフィードを一時ディレクトリに書いて使う。

実行方法: `python3 -m pytest gap_map/test_build_network.py -v`
"""
from build_network import build_network, pickup_drop_limits

# 2026-06-10(水)に走る便が3本ある小さなフィード:
#   X1・X2 … 高速バス。Y1 → Y2 → S1 → S2。山形側(Y1・Y2)は乗るだけ、仙台側(S1・S2)は降りるだけ
#             (始発の「降りられない」と終点の「乗れない」も実データと同じく入れておく)
#   L1     … 同じ停の並びを走る、制限のない便
STOP_TIMES = """trip_id,arrival_time,departure_time,stop_id,stop_sequence,pickup_type,drop_off_type
X1,10:00:00,10:00:00,Y1,1,0,1
X1,10:10:00,10:10:00,Y2,2,0,1
X1,11:00:00,11:00:00,S1,3,1,0
X1,11:10:00,11:10:00,S2,4,1,0
X2,12:00:00,12:00:00,Y1,1,0,1
X2,12:10:00,12:10:00,Y2,2,0,1
X2,13:00:00,13:00:00,S1,3,1,0
X2,13:10:00,13:10:00,S2,4,1,0
L1,09:00:00,09:00:00,Y1,1,0,0
L1,09:10:00,09:10:00,Y2,2,0,0
L1,10:00:00,10:00:00,S1,3,0,0
L1,10:10:00,10:10:00,S2,4,0,0
"""


def write_feed(feed_dir):
    feed_dir.mkdir()
    (feed_dir / "stops.txt").write_text(
        "stop_id,stop_name,stop_lat,stop_lon\n"
        "Y1,山交ビル,38.25,140.33\nY2,県庁前,38.26,140.34\n"
        "S1,広瀬通一番町,38.26,140.87\nS2,仙台駅前,38.26,140.88\n", encoding="utf-8")
    (feed_dir / "routes.txt").write_text(
        "route_id,route_short_name,route_long_name\nR1,,仙台~山形\nR2,,各停\n", encoding="utf-8")
    (feed_dir / "trips.txt").write_text(
        "route_id,service_id,trip_id\nR1,平日,X1\nR1,平日,X2\nR2,平日,L1\n", encoding="utf-8")
    (feed_dir / "calendar.txt").write_text(
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
        "平日,1,1,1,1,1,0,0,20260101,20261231\n", encoding="utf-8")
    (feed_dir / "stop_times.txt").write_text(STOP_TIMES, encoding="utf-8")


def test_pickup_drop_limits_ignores_first_drop_and_last_pickup():
    """始発の「降りられない」と終点の「乗れない」は探索に関係ないので数えない"""
    no_pickup, no_drop = pickup_drop_limits(["0", "0", "1", "1"], ["1", "1", "0", "0"])
    assert no_pickup == {2}
    assert no_drop == {1}


def test_pickup_drop_limits_treats_blank_and_other_codes_as_allowed():
    """空欄(NaN)・0・2(電話予約)・3(運転士に申し出)は乗り降りできるものとして扱う"""
    nan = float("nan")
    no_pickup, no_drop = pickup_drop_limits([nan, "2", "3", "0"], ["0", nan, "2", "3"])
    assert no_pickup == frozenset() and no_drop == frozenset()


def test_default_build_ignores_limits(tmp_path):
    """既定(第2部の空白分析と同じ)では制限を読まず、同じ停の並びの3便が1つのパターンになる"""
    write_feed(tmp_path / "gtfs_テスト")
    net = build_network([tmp_path / "gtfs_テスト"], "20260610")
    assert len(net.patterns) == 1
    assert len(net.patterns[0].trips) == 3
    assert not net.patterns[0].no_pickup and not net.patterns[0].no_drop


def test_respect_pickup_drop_splits_patterns_by_limits(tmp_path):
    """制限を読むと、高速バス2便と制限のない1便が別のパターンに分かれる"""
    write_feed(tmp_path / "gtfs_テスト")
    net = build_network([tmp_path / "gtfs_テスト"], "20260610", respect_pickup_drop=True)
    by_trips = {tuple(t.trip_id for t in p.trips): p for p in net.patterns}
    assert set(by_trips) == {("テスト:L1",), ("テスト:X1", "テスト:X2")}
    express = by_trips[("テスト:X1", "テスト:X2")]
    assert express.no_pickup == {2} and express.no_drop == {1}
    local = by_trips[("テスト:L1",)]
    assert not local.no_pickup and not local.no_drop
