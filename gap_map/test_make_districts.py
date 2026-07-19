# -*- coding: utf-8 -*-
"""make_districts の単体テスト(全国展開キットR2。GTFS/実データ不要)。

地区分けの3方式(a27_polygon / p29_nearest_school / municipality)と、
N03からの市町村コード自動取得、地区名の初期値、idの並び順を、
手作りの小さなフィクスチャで検証する。

実行: python -m pytest gap_map/test_make_districts.py -v
"""
import json

import pandas as pd
from shapely.geometry import Polygon

import make_districts as md


# ===============================================================
# 地区名の初期値
# ===============================================================
def test_auto_district_name_school_and_municipality():
    assert md.auto_district_name("金井小学校") == "金井地区"
    assert md.auto_district_name("西郷第一小学校") == "西郷第一地区"
    # municipality方式では市町村名がそのまま入ってくる → そのまま地区名にする
    assert md.auto_district_name("天童市") == "天童市"


# ===============================================================
# N03からの市町村コード自動取得(旧: CITY_CODE直書き)
# ===============================================================
def _fake_n03(tmp_path):
    n03 = {"features": [
        {"properties": {"N03_004": "山形市", "N03_007": "06201"}},
        {"properties": {"N03_004": "上山市", "N03_007": "06207"}},
        {"properties": {"N03_004": "天童市", "N03_007": "06210"}},
    ]}
    p = tmp_path / "n03.geojson"
    p.write_text(json.dumps(n03, ensure_ascii=False), encoding="utf-8")
    return p


def test_municipality_code_reads_n03(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "N03_GEOJSON", _fake_n03(tmp_path))
    monkeypatch.setattr(md, "_city_codes", None)   # キャッシュを空にして読み直させる
    assert md.municipality_code("山形市") == "06201"
    assert md.municipality_code("天童市") == "06210"


def test_municipality_code_unknown_name_stops_with_guidance(tmp_path, monkeypatch):
    import config
    import pytest
    monkeypatch.setattr(config, "N03_GEOJSON", _fake_n03(tmp_path))
    monkeypatch.setattr(md, "_city_codes", None)
    with pytest.raises(SystemExit):
        md.municipality_code("存在しない市")


# ===============================================================
# メッシュ→地区の割り当て(3方式)
# ===============================================================
def _meshes(municipality, coords):
    return pd.DataFrame({
        "meshcode": [f"m{i}" for i in range(len(coords))],
        "municipality": [municipality] * len(coords),
        "population": [100] * len(coords),
        "lat": [c[0] for c in coords],
        "lon": [c[1] for c in coords],
    })


def test_assign_municipality_method_one_district_per_city(monkeypatch):
    """municipality方式: 学区データを一切読まず、市町村名がそのまま地区になる"""
    monkeypatch.setitem(md.REGION, "district_methods", {})   # 設定なし→全部municipality
    meshes = _meshes("天童市", [(38.36, 140.37), (38.37, 140.38)])
    out = md.assign_meshes(meshes)
    assert list(out["source_school"]) == ["天童市", "天童市"]


def test_assign_p29_nearest_school(monkeypatch):
    """p29_nearest_school方式: 2校のうち近い方の学校名が割り当たる"""
    monkeypatch.setitem(md.REGION, "district_methods", {"テスト市": "p29_nearest_school"})
    monkeypatch.setattr(md, "municipality_code", lambda name: "99999")
    monkeypatch.setattr(md, "load_elementary_schools", lambda code: [
        ("北小学校", 38.30, 140.30),
        ("南小学校", 38.20, 140.30),
    ])
    meshes = _meshes("テスト市", [(38.29, 140.30), (38.21, 140.30)])
    out = md.assign_meshes(meshes)
    assert list(out["source_school"]) == ["北小学校", "南小学校"]


def test_assign_a27_polygon_with_fallback(monkeypatch):
    """a27_polygon方式: ポリゴン内は所属学区、どこにも入らない点は最寄り学区へ"""
    monkeypatch.setitem(md.REGION, "district_methods", {"テスト市": "a27_polygon"})
    monkeypatch.setattr(md, "municipality_code", lambda name: "99999")
    west = Polygon([(140.0, 38.0), (140.1, 38.0), (140.1, 38.1), (140.0, 38.1)])
    east = Polygon([(140.2, 38.0), (140.3, 38.0), (140.3, 38.1), (140.2, 38.1)])
    monkeypatch.setattr(md, "load_school_polygons", lambda code: [
        ("西小学校", west), ("東小学校", east),
    ])
    meshes = _meshes("テスト市", [
        (38.05, 140.05),   # 西ポリゴンの中
        (38.05, 140.25),   # 東ポリゴンの中
        (38.05, 140.35),   # どちらにも入らない → 最寄り(東)へフォールバック
    ])
    out = md.assign_meshes(meshes)
    assert list(out["source_school"]) == ["西小学校", "東小学校", "東小学校"]


def test_assign_empty_a27_stops_with_guidance(monkeypatch):
    """A27にその市町村の学区が無いときは、代替方式の案内つきで止まる"""
    import pytest
    monkeypatch.setitem(md.REGION, "district_methods", {"テスト市": "a27_polygon"})
    monkeypatch.setattr(md, "municipality_code", lambda name: "99999")
    monkeypatch.setattr(md, "load_school_polygons", lambda code: [])
    with pytest.raises(SystemExit):
        md.assign_meshes(_meshes("テスト市", [(38.0, 140.0)]))


# ===============================================================
# 地区マスタの組み立て(idの並び順は地域設定の市町村順)
# ===============================================================
def test_build_master_orders_by_region_municipality_order(monkeypatch):
    monkeypatch.setitem(md.REGION, "target_municipalities", ["天童市", "東根市"])
    meshes = pd.DataFrame({
        "meshcode": [574022891, 574022892],
        "municipality": ["東根市", "天童市"],
        "population": [100, 200],
        "lat": [38.4, 38.36],
        "lon": [140.4, 140.37],
        "source_school": ["東根市", "天童市"],
    })
    master = md.build_master(meshes)
    # 地域設定の並び順(天童市→東根市)でidが振られる
    assert list(master["id"]) == ["d01", "d02"]
    assert list(master["municipality"]) == ["天童市", "東根市"]
    assert list(master["name"]) == ["天童市", "東根市"]   # municipality方式の地区名初期値


def test_build_master_yamagata_order_unchanged(monkeypatch):
    """山形の既定値では従来どおり 山形市→上山市・学校名順(id互換の確認)"""
    meshes = pd.DataFrame({
        "meshcode": [574022891, 574022892, 574022893],
        "municipality": ["上山市", "山形市", "山形市"],
        "population": [100, 200, 300],
        "lat": [38.15, 38.25, 38.26],
        "lon": [140.27, 140.33, 140.34],
        "source_school": ["宮川小学校", "金井小学校", "出羽小学校"],
    })
    master = md.build_master(meshes)
    assert list(master["source_school"]) == ["出羽小学校", "金井小学校", "宮川小学校"]
    assert list(master["id"]) == ["d01", "d02", "d03"]


# ===============================================================
# 2026-07-19 バグ修正: 再実行でサブ地区分割が消えないこと
# ===============================================================
def _tmp_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(md, "MESH_DISTRICTS_CSV", tmp_path / "mesh_districts.csv")
    monkeypatch.setattr(md, "DISTRICTS_MASTER_CSV", tmp_path / "districts_master.csv")
    monkeypatch.setattr(md, "DISTRICTS_JSON", tmp_path / "districts.json")


def test_write_outputs_preserves_existing_subdistricts(tmp_path, monkeypatch):
    """make_subdistricts.py --apply が注入した sub 配列は、make_districts.py を
    再実行しても消えない(id+source_school が一致する地区に引き継ぐ)。
    source_school が変わった地区(=別物になった地区)には引き継がない"""
    _tmp_outputs(tmp_path, monkeypatch)
    sub = [{"id": "d01a", "name": "金井地区(にし)", "kana": "かない(にし)",
            "lat": 38.3, "lon": 140.3}]
    (tmp_path / "districts.json").write_text(json.dumps([
        {"id": "d01", "name": "金井地区", "kana": "かない", "municipality": "山形市",
         "lat": 38.3, "lon": 140.3, "source_school": "金井小学校区", "sub": sub},
        {"id": "d02", "name": "旧地区", "kana": "きゅう", "municipality": "山形市",
         "lat": 38.2, "lon": 140.2, "source_school": "旧小学校区",
         "sub": [{"id": "d02a", "name": "旧(きた)", "kana": "きゅう(きた)",
                  "lat": 38.2, "lon": 140.2}]},
    ], ensure_ascii=False), encoding="utf-8")

    master = pd.DataFrame([
        {"id": "d01", "municipality": "山形市", "source_school": "金井小学校",
         "name": "金井地区", "display_name": "", "kana": "かない",
         "population": 100, "mesh_count": 1, "rep_meshcode": 574022891,
         "lat": 38.3, "lon": 140.3},
        {"id": "d02", "municipality": "山形市", "source_school": "別小学校",   # 学区が変わった
         "name": "別地区", "display_name": "", "kana": "べつ",
         "population": 200, "mesh_count": 1, "rep_meshcode": 574022892,
         "lat": 38.2, "lon": 140.2},
    ])
    meshes = pd.DataFrame({
        "meshcode": [574022891, 574022892],
        "municipality": ["山形市", "山形市"],
        "source_school": ["金井小学校", "別小学校"],
        "population": [100, 200],
    })
    md.write_outputs(meshes, master)
    out = json.loads((tmp_path / "districts.json").read_text(encoding="utf-8"))
    d01 = next(d for d in out if d["id"] == "d01")
    assert d01["sub"] == sub                       # 一致する地区は引き継ぐ
    d02 = next(d for d in out if d["id"] == "d02")
    assert "sub" not in d02                        # 学区が変わった地区には付けない


def test_write_outputs_reproduces_committed_districts_json(tmp_path, monkeypatch):
    """実リポジトリの districts_master.csv+既存 districts.json から、コミット済みの
    districts.json(サブ地区21件入り)を丸ごと再現できること=山形の再実行無変更の証明"""
    _tmp_outputs(tmp_path, monkeypatch)
    committed = (md.PROJECT_ROOT / "webapp" / "data" / "districts.json").read_text(encoding="utf-8")
    (tmp_path / "districts.json").write_text(committed, encoding="utf-8")

    master = pd.read_csv(md.PROJECT_ROOT / "data" / "districts_master.csv")
    for col in ("display_name", "kana"):
        master[col] = master[col].fillna("")
    meshes = pd.DataFrame({
        "meshcode": master["rep_meshcode"],
        "municipality": master["municipality"],
        "source_school": master["source_school"],
        "population": master["population"],
    })
    md.write_outputs(meshes, master)
    regenerated = (tmp_path / "districts.json").read_text(encoding="utf-8")
    assert regenerated == committed
