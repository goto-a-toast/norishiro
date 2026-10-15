# -*- coding: utf-8 -*-
"""make_stop_search.py(バス停さがしの索引)の小さなテスト。GTFS が無くても動く"""
import make_stop_search as ms


def _net(stops, patterns, stop_ids=None):
    return {"stops": stops, "patterns": patterns,
            "stop_ids": stop_ids or [f"x:{i}" for i in range(len(stops))],
            "operators": [{"name": "山交バス"}, {"name": "べにちゃんバス(山形市)"}]}


def test_kata_to_hira():
    assert ms.kata_to_hira("ヤマザワマエ") == "やまざわまえ"
    assert ms.kata_to_hira("山形駅前") == "山形駅前"


def test_cluster_platforms_splits_far_same_name():
    # 同じ名前ののりば: 0と1は約100m(同じ停)、2は約11km 先(別の停)
    stops = [["七日町", 38.2527, 140.3365, None], ["七日町", 38.2522, 140.3376, None],
             ["七日町", 38.35, 140.3365, None]]
    assert ms.cluster_platforms([0, 1, 2], stops) == [[0, 1], [2]]


def test_build_counts_boardable_trips_and_hints():
    # 停0〜1が「市役所前」(離れた2か所)、停2が終点。終点では乗れないので便を数えない
    stops = [["市役所前", 38.25, 140.34, None], ["市役所前", 38.15, 140.27, None],
             ["終点", 38.26, 140.35, None], ["便なし", 38.25, 140.341, None]]
    pats = [[[0, 2], [[0, 0, 0, 0, "t1", [], []], [0, 0, 0, 0, "t2", [], []]]],
            [[1, 2], [[0, 0, 0, 1, "t3", [], []]]]]
    nets = {dt: _net(stops, pats) for dt in ms.DAY_TYPES}
    meshes = [[38.25, 140.34, 0], [38.15, 140.27, 0]]
    city = lambda lat, lon: "山形市" if lat > 38.2 else "上山市"   # noqa: E731
    out = ms.build(nets, {"by_id": {"x:0": "しやくしょまえ"}, "by_name": {}}, meshes, city,
                   {"山形市", "上山市"})
    by_key = {e["key"]: e for e in out}
    assert set(by_key) == {"市役所前(山形市)", "市役所前(上山市)"}   # 終点・便なしは出さない
    assert by_key["市役所前(山形市)"]["weekday"] == 2
    assert by_key["市役所前(上山市)"]["weekday"] == 1
    # 読みがなは同じ名前の停どうしで共有する(山交バスの停も市営バスの読みで探せる)
    assert by_key["市役所前(上山市)"]["kana"] == ["しやくしょまえ"]
    # 座標は代表ののりばの値そのまま(丸めない)
    assert (by_key["市役所前(山形市)"]["lat"], by_key["市役所前(山形市)"]["lon"]) == (38.25, 140.34)
