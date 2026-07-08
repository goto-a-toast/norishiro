# -*- coding: utf-8 -*-
"""make_district_gap.aggregate_district_gap の単体テスト(GTFS/実データ不要)。
手作りの小さな3表(メッシュ→地区・access_mesh・target_meshes)で、地区別集計と
隠れ空白・高齢化率(秘匿メッシュの除外)の計算を検証する。

実行: python -m pytest gap_map/test_make_district_gap.py -v
"""
import numpy as np
import pandas as pd

from make_district_gap import aggregate_district_gap


def _frames():
    # m1,m2,m3 → d01 / m4 → d02
    mesh_districts = pd.DataFrame({
        "meshcode": ["m1", "m2", "m3", "m4"],
        "district_id": ["d01", "d01", "d01", "d02"],
    })
    access = pd.DataFrame({
        "meshcode": ["m1", "m2", "m3", "m4"],
        "population": [100, 50, 200, 80],
        # m1: 30分・通院不可=隠れ空白かつ空白 / m2: 到達不能・空白 /
        # m3: 20分・通院可=空白でない / m4: 70分・通院不可=空白(60分超なので隠れ空白でない)
        "time_to_hospital_min": ["30", "到達不能", "20", "70"],
        "hospital_visit_ok": ["No", "No", "Yes", "No"],
        "is_gap": [True, True, False, True],
    })
    meshes = pd.DataFrame({
        "meshcode": ["m1", "m2", "m3", "m4"],
        "population": [100, 50, 200, 80],
        "population_65plus": [40, np.nan, 50, 30],   # m2は年齢が秘匿(NaN)
    })
    return mesh_districts, access, meshes


def test_d01_aggregation():
    gap = aggregate_district_gap(*_frames())
    d01 = gap["d01"]
    assert d01["population"] == 350
    assert d01["mesh_count"] == 3
    assert d01["gap_mesh_count"] == 2            # m1, m2
    assert d01["gap_population"] == 150          # 100 + 50
    assert d01["gap_ratio"] == round(150 / 350, 3)
    assert d01["unreachable_mesh_count"] == 1    # m2
    assert d01["hidden_gap_mesh_count"] == 1     # m1 のみ(m2は到達不能・m4は別地区)
    assert d01["hidden_gap_population"] == 100
    assert d01["has_gap"] is True
    assert d01["has_hidden_gap"] is True


def test_aging_rate_excludes_secret_mesh():
    # m2(65+がNaN)は分母からも除外。d01の高齢化率 = (40+50)/(100+200) = 0.3
    gap = aggregate_district_gap(*_frames())
    assert gap["d01"]["aging_rate"] == 0.3


def test_d02_single_mesh_gap():
    gap = aggregate_district_gap(*_frames())
    d02 = gap["d02"]
    assert d02["population"] == 80
    assert d02["gap_mesh_count"] == 1
    assert d02["gap_ratio"] == 1.0
    assert d02["hidden_gap_mesh_count"] == 0     # 70分は60分超=隠れ空白でない
    assert d02["has_hidden_gap"] is False
    assert d02["aging_rate"] == round(30 / 80, 3)


def test_string_is_gap_is_accepted():
    # CSVから読むと is_gap は "True"/"False" の文字列になる。これも正しく解釈できること
    md, ac, me = _frames()
    ac["is_gap"] = ["True", "True", "False", "True"]
    gap = aggregate_district_gap(md, ac, me)
    assert gap["d01"]["gap_mesh_count"] == 2
