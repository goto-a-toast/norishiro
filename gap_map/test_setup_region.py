# -*- coding: utf-8 -*-
"""setup_region(R3 対話式ウィザード)の単体テスト(GTFS/実データ不要)。
対話部分(input)は使わず、ロジック関数(1次メッシュ番号の計算・N03からの範囲取得・
データファイル探索・region.json の組み立て)を手作りフィクスチャで検証する。

実行: python -m pytest gap_map/test_setup_region.py -v
"""
import json

import setup_region as sr


# ===============================================================
# 1次メッシュ番号の計算(e-Statのダウンロード案内に使う)
# ===============================================================
def test_primary_mesh_codes_yamagata():
    """山形市・上山市の範囲(北緯38.1〜38.4・東経140.2〜140.5)は1次メッシュ5740に収まる"""
    assert sr.primary_mesh_codes_from_bbox(38.1, 38.4, 140.2, 140.5) == ["5740"]


def test_primary_mesh_codes_spanning_two_meshes():
    """緯度2/3度の境界(北緯38.6666…)をまたぐと2枚になる(5740と5840)"""
    codes = sr.primary_mesh_codes_from_bbox(38.5, 38.8, 140.2, 140.5)
    assert codes == ["5740", "5840"]


def test_primary_mesh_codes_spanning_longitude():
    """経度1度の境界をまたぐと横にも増える"""
    codes = sr.primary_mesh_codes_from_bbox(38.1, 38.4, 139.9, 140.1)
    assert codes == ["5739", "5740"]


# ===============================================================
# N03からの範囲取得
# ===============================================================
def _fake_n03(tmp_path):
    n03 = {"features": [
        {"properties": {"N03_004": "天童市"},
         "geometry": {"type": "Polygon", "coordinates":
                      [[[140.33, 38.33], [140.42, 38.33], [140.42, 38.40], [140.33, 38.40]]]}},
        {"properties": {"N03_004": "山形市"},
         "geometry": {"type": "MultiPolygon", "coordinates":
                      [[[[140.25, 38.15], [140.45, 38.15], [140.45, 38.30]]]]}},
    ]}
    p = tmp_path / "n03.geojson"
    p.write_text(json.dumps(n03, ensure_ascii=False), encoding="utf-8")
    return p


def test_bbox_of_municipalities(tmp_path):
    min_lat, max_lat, min_lon, max_lon = sr.bbox_of_municipalities(_fake_n03(tmp_path), ["天童市"])
    assert (min_lat, max_lat) == (38.33, 38.40)
    assert (min_lon, max_lon) == (140.33, 140.42)


def test_bbox_missing_municipality_stops(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        sr.bbox_of_municipalities(_fake_n03(tmp_path), ["存在しない市"])


# ===============================================================
# データファイル探索
# ===============================================================
def test_find_data_files(tmp_path):
    (tmp_path / "N03-XX_GML").mkdir()
    (tmp_path / "N03-XX_GML" / "N03-23_06.geojson").write_text("{}")
    (tmp_path / "P04-XX" / "P04-XX").mkdir(parents=True)
    (tmp_path / "P04-XX" / "P04-XX" / "P04-20_10-g_MedicalInstitution.shp").write_bytes(b"")
    (tmp_path / "tblT001101H5740").mkdir()
    (tmp_path / "tblT001101H5740" / "tblT001101H5740.txt").write_text("")
    found = sr.find_data_files(tmp_path)
    assert found["n03_geojson"] == "N03-XX_GML/N03-23_06.geojson"
    assert found["p04_dir"] == "P04-XX/P04-XX"
    assert found["p04_shp"] == "P04-20_10-g_MedicalInstitution.shp"
    assert found["pop_mesh_files"] == ["tblT001101H5740/tblT001101H5740.txt"]
    assert "a27_shp" not in found       # 無いものは入らない(=ダウンロード案内側に回る)


# ===============================================================
# region.json の組み立て
# ===============================================================
def _answers():
    return {
        "region_name": "天童市", "prefecture": "山形県",
        "municipalities": ["天童市"], "feed_dirs": ["gtfs_天童市"],
        "reference_feed": "天童市", "target_date": "20260610",
        "valid_until": "20260930",
        "district_methods": {"天童市": "municipality"},
    }


def test_build_region_config_required_keys_and_no_yamagata_leak():
    """組み立てたregion.jsonに必要キーが揃い、山形の値(基幹病院・まちなか・期待値)が
    別地域へ漏れないよう空で明示されていること"""
    cfg = sr.build_region_config(_answers(), {"n03_geojson": "n03.geojson"})
    assert cfg["target_municipalities"] == ["天童市"]
    assert cfg["district_methods"] == {"天童市": "municipality"}
    assert cfg["core_hospitals"] == [] and cfg["town_spots"] == []
    assert cfg["expected"] == {}
    assert cfg["n03_geojson"] == "n03.geojson"
    # region.load() に通したとき、山形の確定値が混ざらないことまで確認する
    import region
    import json as _json
    from pathlib import Path
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "region.json"
        p.write_text(_json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        r = region.load(p)
        assert r["core_hospitals"] == []
        assert r["expected"] == {}
        assert r["target_municipalities"] == ["天童市"]


def test_build_region_config_reference_dates_follow_target():
    """土曜・日祝の代表日は分析日のあとの週末が自動で選ばれる(2026-06-10=水曜→13土・14日)"""
    cfg = sr.build_region_config(_answers(), {})
    assert cfg["reference_dates"] == {"weekday": "20260610",
                                      "saturday": "20260613",
                                      "sunday_holiday": "20260614"}
    assert cfg["date_table_start"] == "20260601"


def test_feeds_valid_until_uses_min_end_date(tmp_path):
    for name, end in [("gtfs_A", "20261231"), ("gtfs_B", "20260930")]:
        d = tmp_path / name
        d.mkdir()
        (d / "calendar.txt").write_text(
            "service_id,start_date,end_date\ns1,20260401," + end + "\n")
    assert sr.feeds_valid_until([tmp_path / "gtfs_A", tmp_path / "gtfs_B"]) == "20260930"


def test_pipeline_steps_scripts_exist():
    """--run が実行する工程のスクリプトが全部実在すること(タイプミス検知)"""
    from pathlib import Path
    for script, desc in sr.PIPELINE_STEPS:
        assert (Path(sr.__file__).parent / script).exists(), script
        assert desc.strip()   # 説明文が空でないこと(「説明付き」の要件)
