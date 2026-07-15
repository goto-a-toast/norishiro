# -*- coding: utf-8 -*-
"""export_karte_data の単体テスト(GTFS/実データ不要)。
手作りの6メッシュ・2地区のフィクスチャで、A〜Eの評価付け・市/地区平均の算出・
M8-1完成条件(§13.4)の3つの検算(件数一致/平均の再計算一致/E=is_gapの一致)を確認する。

実行: python -m pytest gap_map/test_export_karte_data.py -v
"""
import pandas as pd
import pytest

import config
from export_karte_data import build_karte, grade_of


# 実在の形式のメッシュコード(山形市周辺の4次メッシュ。test_make_mesh_index.pyと同じ地点)
M1, M2, M3, M4 = "574022891", "574022892", "574022893", "574022894"
M5, M6 = "574012991", "574012992"


def _frames():
    # d01: m1(徒歩で行けてバス到達不能=A)/m2(12分=A)/m3(28分=B)/m4(55分=D)
    # d02: m5(is_gap=True=E)/m6(35分=C)
    access = pd.DataFrame({
        "meshcode": [M1, M2, M3, M4, M5, M6],
        "population": [100, 50, 200, 80, 60, 40],
        "municipality": ["山形市"] * 6,
        "nearest_stop_name": ["A停", "B停", "C停", "D停", None, "F停"],
        "walk_to_stop_min": [5, 8, 10, 12, None, 9],
        "time_to_hospital_min": ["到達不能", "12", "28", "55", "到達不能", "35"],
        "hospital_name": ["県立中央病院"] * 4 + [None, "県立中央病院"],
        "time_to_super_min": ["10", "15", "20", "25", "到達不能", "18"],
        "super_name": ["ヤマザワ"] * 6,
        "hospital_visit_ok": ["Yes", "Yes", "Yes", "Yes", "No", "Yes"],
        "visit_total_min": ["180", "150", "200", "250", None, "210"],
        # m1はwalkable_by_footでis_gap=False、m5だけis_gap=True
        "is_gap": [False, False, False, False, True, False],
    })
    mesh_table = pd.DataFrame({
        "meshcode": [M1, M2, M3, M4, M5, M6],
        "use_id": ["d01", "d01", "d01", "d01", "d02", "d02"],
    })
    return access, mesh_table


def test_grade_of_bins_match_config():
    assert grade_of(False, None) == "A"     # 徒歩で行ける(バスは到達不能)
    assert grade_of(False, 12) == "A"
    assert grade_of(False, 15) == "A"       # 境界は含む(<=15)
    assert grade_of(False, 16) == "B"
    assert grade_of(False, 45) == "C"
    assert grade_of(False, 46) == "D"
    assert grade_of(False, 60) == "D"
    assert grade_of(True, 5) == "E"         # is_gap=Trueなら所要時間に関わらずE


def test_build_karte_assigns_grades_and_count():
    access, mesh_table = _frames()
    karte = build_karte(access, mesh_table)
    assert len(karte["meshes"]) == len(access) == 6      # 検算(a)
    grades = [m["grade"] for m in karte["meshes"]]
    assert grades == ["A", "A", "B", "D", "E", "C"]


def test_build_karte_e_grade_matches_is_gap_exactly():
    """検算(c): E評価のメッシュ集合 = is_gap=Trueの集合"""
    access, mesh_table = _frames()
    karte = build_karte(access, mesh_table)
    e_flags = [m["grade"] == "E" for m in karte["meshes"]]
    assert e_flags == list(access["is_gap"])


def test_build_karte_averages_are_population_weighted_and_reproducible():
    """検算(b): meta の市平均・地区平均を karte.json の中身から再計算して一致すること"""
    access, mesh_table = _frames()
    karte = build_karte(access, mesh_table)

    # 市平均(hospital_min): 到達不能(m1,m5)を除く4件の人口加重平均
    # (12*50 + 28*200 + 55*80 + 35*40) / (50+200+80+40) = 12000/370 = 32.432...→32.4
    assert karte["meta"]["city_avg"]["hospital_min"] == pytest.approx(32.4)

    # d01平均: m1(NaN除外)/m2=12/m3=28/m4=55 の人口加重平均
    # (12*50+28*200+55*80)/(50+200+80) = 10600/330 = 32.121...→32.1
    assert karte["meta"]["district_avg"]["d01"]["hospital_min"] == pytest.approx(32.1)
    # d02平均: m5(NaN除外)/m6=35のみ → 35.0
    assert karte["meta"]["district_avg"]["d02"]["hospital_min"] == pytest.approx(35.0)

    # 実際にkarte.jsonの中身(meshes配列)だけから同じ数字を再現できること
    def recompute(did):
        rows = [m for m in karte["meshes"] if m["district_id"] == did and m["hospital_min"] is not None]
        pops = [access.loc[i, "population"] for i, m in enumerate(karte["meshes"])
                if m["district_id"] == did and m["hospital_min"] is not None]
        total_w = sum(pops)
        return round(sum(m["hospital_min"] * w for m, w in zip(rows, pops)) / total_w, 1)

    assert recompute("d01") == karte["meta"]["district_avg"]["d01"]["hospital_min"]
    assert recompute("d02") == karte["meta"]["district_avg"]["d02"]["hospital_min"]


def test_build_karte_no_nan_leaks_into_output():
    """欠測(到達不能・停なし等)はNoneになり、JSONにNaNリテラルが混ざらないこと
    (2026-07-12 make_mesh_index.py の同種バグを踏まないための確認)"""
    access, mesh_table = _frames()
    karte = build_karte(access, mesh_table)
    m1 = karte["meshes"][0]
    assert m1["hospital_min"] is None
    m5 = karte["meshes"][4]
    assert m5["nearest_stop_name"] is None and m5["hospital_name"] is None
    assert m5["visit_total_min"] is None
    import json
    json.dumps(karte)   # NaNが混じっていれば ValueError にはならないが値がNaNのままだと壊れる


def test_build_karte_dedupes_mesh_table_by_meshcode():
    """メッシュ対応表に同じmeshcodeが重複しても、出力の件数が水増しされないこと"""
    access, mesh_table = _frames()
    dup = pd.concat([mesh_table, mesh_table.iloc[[0]]], ignore_index=True)   # m1を重複させる
    karte = build_karte(access, dup)
    assert len(karte["meshes"]) == len(access)


def test_build_karte_keeps_row_without_district_match():
    """メッシュ対応表に無いmeshcode(隣接市等)でも行を落とさず、district_idがNoneになること"""
    access, mesh_table = _frames()
    karte = build_karte(access, mesh_table.iloc[:-1])   # m6の対応行を消す
    assert len(karte["meshes"]) == len(access)
    assert karte["meshes"][5]["district_id"] is None


def test_grade_bins_come_from_config_not_hardcoded():
    """しきい値がconfig.KARTE_GRADE_BINSを参照していること(§13.2決定#6)"""
    assert grade_of(False, config.KARTE_GRADE_BINS[0]) == "A"
    assert grade_of(False, config.KARTE_GRADE_BINS[0] + 1) == "B"
