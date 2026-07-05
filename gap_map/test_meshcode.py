# -*- coding: utf-8 -*-
"""
meshcode.py の単体テスト。
docs/plan_gap_map.md §6.1 に書かれている検算済みの数値をそのままテストにしている。

実行方法: プロジェクトのルートで `python3 -m pytest gap_map/test_meshcode.py -v`
"""

import pytest

import meshcode


def test_yamagata_eki_bounds():
    """山形駅(北緯38.2484・東経140.3278)を含む3次メッシュ「57402296」の
    南西端が、計画書に書かれた数値(北緯38.241667度・東経140.325度)と一致すること"""
    south, west, north, east = meshcode.meshcode_to_bounds("57402296")
    assert south == pytest.approx(38.241667, abs=1e-6)
    assert west == pytest.approx(140.325, abs=1e-6)


def test_latlon_to_meshcode_matches_known_code():
    """山形駅の緯度経度から逆変換すると、同じ「57402296」に戻ること"""
    code = meshcode.latlon_to_meshcode(38.2484, 140.3278, level=3)
    assert code == "57402296"


def test_round_trip_center_to_code():
    """中心点を求めて、そこから4次メッシュコードへ逆変換すると、
    元のコードに戻ること(往復一致)"""
    original = "574022961"
    lat, lon = meshcode.meshcode_to_center(original)
    back = meshcode.latlon_to_meshcode(lat, lon, level=4)
    assert back == original


def test_4th_mesh_quadrants_fit_together():
    """3次メッシュ「57402296」を4分割した4つの4次メッシュ(南西/南東/北西/北東)が、
    互いに隣接し、範囲がぴったり組み合わさって元の3次メッシュ全体になること"""
    sw = meshcode.meshcode_to_bounds("574022961")
    se = meshcode.meshcode_to_bounds("574022962")
    nw = meshcode.meshcode_to_bounds("574022963")
    ne = meshcode.meshcode_to_bounds("574022964")
    # 各タプルは (南端緯度, 西端経度, 北端緯度, 東端経度)

    # 南西の東端 == 南東の西端(南側で東西に隣り合う)
    assert sw[3] == pytest.approx(se[1])
    # 南西の北端 == 北西の南端(西側で南北に隣り合う)
    assert sw[2] == pytest.approx(nw[0])

    # 4つを合わせた範囲が、元の3次メッシュ全体の範囲とちょうど一致する
    parent_south, parent_west, parent_north, parent_east = \
        meshcode.meshcode_to_bounds("57402296")
    assert sw[0] == pytest.approx(parent_south)
    assert sw[1] == pytest.approx(parent_west)
    assert ne[2] == pytest.approx(parent_north)
    assert ne[3] == pytest.approx(parent_east)
