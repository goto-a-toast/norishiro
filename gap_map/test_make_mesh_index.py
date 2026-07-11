# -*- coding: utf-8 -*-
"""make_mesh_index の単体テスト(GTFS/実データ不要)。
手作りの小さなメッシュ表で、索引の書式・件数・sub_id優先・冪等性を検証する。

実行: python -m pytest gap_map/test_make_mesh_index.py -v
"""
import json

import pandas as pd

import make_mesh_index
from meshcode import meshcode_to_center

# 実在の形式のメッシュコード(山形市周辺の4次メッシュ)を使う
MESH_A = "574022894"
MESH_B = "574022893"
MESH_C = "574012994"


def _setup(tmp_path, monkeypatch, with_sub: bool):
    """一時ディレクトリに入力CSVとdistricts.jsonを作り、モジュール定数を差し替える"""
    data_dir = tmp_path / "data"
    web_dir = tmp_path / "webapp" / "data"
    data_dir.mkdir()
    web_dir.mkdir(parents=True)

    pd.DataFrame({
        "meshcode": [MESH_A, MESH_B, MESH_C],
        "district_id": ["d15", "d15", "d01"],
    }).to_csv(data_dir / "mesh_districts.csv", index=False)

    if with_sub:
        pd.DataFrame({
            "meshcode": [MESH_A, MESH_B, MESH_C],
            "district_id": ["d15", "d15", "d01"],
            # d15はサブ分割済み、d01は対象外(sub_id空)
            "sub_id": ["d15a", "d15b", ""],
        }).to_csv(data_dir / "mesh_subdistricts.csv", index=False)

    districts = [
        {"id": "d01", "name": "地区1", "lat": 38.0, "lon": 140.0},
        {"id": "d15", "name": "東沢地区", "lat": 38.2, "lon": 140.4,
         "sub": [{"id": "d15a", "name": "東沢地区(にし)"},
                 {"id": "d15b", "name": "東沢地区(ひがし)"}]},
    ]
    (web_dir / "districts.json").write_text(json.dumps(districts, ensure_ascii=False),
                                            encoding="utf-8")

    monkeypatch.setattr(make_mesh_index, "MESH_DISTRICTS_CSV", data_dir / "mesh_districts.csv")
    monkeypatch.setattr(make_mesh_index, "MESH_SUBDISTRICTS_CSV", data_dir / "mesh_subdistricts.csv")
    monkeypatch.setattr(make_mesh_index, "DISTRICTS_JSON", web_dir / "districts.json")
    monkeypatch.setattr(make_mesh_index, "OUT_JSON", web_dir / "mesh_index.json")
    return web_dir / "mesh_index.json"


def test_index_without_sub_uses_district_ids(tmp_path, monkeypatch):
    out_path = _setup(tmp_path, monkeypatch, with_sub=False)
    make_mesh_index.main()
    out = json.loads(out_path.read_text(encoding="utf-8"))
    assert out["districts"] == ["d01", "d15"]
    assert len(out["meshes"]) == 3
    # 各メッシュの座標が meshcode_to_center の5桁丸めと一致する
    lat, lon = meshcode_to_center(MESH_A)
    hit = [m for m in out["meshes"] if m[0] == round(lat, 5) and m[1] == round(lon, 5)]
    assert len(hit) == 1 and out["districts"][hit[0][2]] == "d15"


def test_index_with_sub_prefers_sub_id_and_falls_back(tmp_path, monkeypatch):
    out_path = _setup(tmp_path, monkeypatch, with_sub=True)
    make_mesh_index.main()
    out = json.loads(out_path.read_text(encoding="utf-8"))
    # sub_idがある行はサブ地区ID、無い行は親IDになる
    assert set(out["districts"]) == {"d01", "d15a", "d15b"}
    ids = {out["districts"][m[2]] for m in out["meshes"]}
    assert ids == {"d01", "d15a", "d15b"}


def test_idempotent_output(tmp_path, monkeypatch):
    out_path = _setup(tmp_path, monkeypatch, with_sub=True)
    make_mesh_index.main()
    first = out_path.read_bytes()
    make_mesh_index.main()
    assert out_path.read_bytes() == first


def test_unknown_id_fails(tmp_path, monkeypatch):
    out_path = _setup(tmp_path, monkeypatch, with_sub=True)
    # districts.json からd15のsubを消す → 索引のd15a/d15bが未知IDになり停止するはず
    districts = [{"id": "d01", "name": "地区1"}, {"id": "d15", "name": "東沢地区"}]
    make_mesh_index.DISTRICTS_JSON.write_text(json.dumps(districts), encoding="utf-8")
    import pytest
    with pytest.raises(SystemExit):
        make_mesh_index.main()
