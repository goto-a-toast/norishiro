# -*- coding: utf-8 -*-
"""make_subdistricts の単体テスト(GTFS/実データ不要)。
手作りの「西に密集+東に散在」の地区で、採点・クラスタ分割・マスタ生成・
districts.json への sub 内包・人間編集の引き継ぎを検証する。

実行: python -m pytest gap_map/test_make_subdistricts.py -v
"""
import json

import pandas as pd
import pytest

import make_subdistricts as ms

# 東西に約4.4km離れた2つの人口かたまり(緯度38度で経度0.05度≒4.4km)
WEST_LON, EAST_LON = 140.30, 140.35
LAT = 38.20


def _mesh_df():
    rows = []
    # 西側(市街): 人口の多いメッシュ3つ。代表点はここになる
    for i, pop in enumerate([500, 300, 200]):
        rows.append({"meshcode": f"10{i}", "district_id": "d15",
                     "municipality": "山形市", "source_school": "東沢小学校区",
                     "population": pop, "lat": LAT, "lon": WEST_LON + i * 0.002})
    # 東側(山あい): 少人数のメッシュ4つ(合計400人。代表点から4km超)
    for i, pop in enumerate([100, 100, 100, 100]):
        rows.append({"meshcode": f"20{i}", "district_id": "d15",
                     "municipality": "山形市", "source_school": "東沢小学校区",
                     "population": pop, "lat": LAT, "lon": EAST_LON + i * 0.002})
    # 別の狭い地区(分割対象にならない)
    rows.append({"meshcode": "300", "district_id": "d01",
                 "municipality": "山形市", "source_school": "X小学校区",
                 "population": 1000, "lat": 38.25, "lon": 140.33})
    return pd.DataFrame(rows)


DISTRICTS = [
    {"id": "d01", "name": "X地区", "kana": "えっくす", "municipality": "山形市",
     "lat": 38.25, "lon": 140.33},
    {"id": "d15", "name": "東沢地区", "kana": "ひがしざわ", "municipality": "山形市",
     "lat": LAT, "lon": WEST_LON},   # 代表点=西端(人口最大メッシュ)
]


def test_score_marks_wide_low_density_district():
    score = ms.score_districts(_mesh_df(), DISTRICTS)
    row = score[score["district_id"] == "d15"].iloc[0]
    assert row["far_pop_2km"] == 400          # 東側4メッシュが2km超
    assert row["far_ratio"] == pytest.approx(400 / 1400)
    assert bool(row["対象"])                   # 300人以上かつ20%以上
    assert not bool(score[score["district_id"] == "d01"].iloc[0]["対象"])


def test_split_separates_east_and_west_deterministically():
    mesh = _mesh_df()
    rows = ms.split_district(mesh, DISTRICTS[1])
    assert [r["sub_id"] for r in rows] == ["d15a", "d15b"]   # 人口の多い西側が a
    a, b = rows
    assert a["population"] == 1000 and b["population"] == 400
    assert a["rep_meshcode"] == "100"     # クラスタ内の人口最大メッシュ
    assert b["rep_meshcode"] == "200"
    assert "ひがし" in b["name"] and b["name"].startswith("東沢地区")
    assert b["kana"] == "ひがしざわ(ひがし)"
    # 冪等性: もう一度実行しても同じ結果
    rows2 = ms.split_district(mesh, DISTRICTS[1])
    assert [(r["sub_id"], r["population"], r["rep_meshcode"]) for r in rows] == \
           [(r["sub_id"], r["population"], r["rep_meshcode"]) for r in rows2]


def test_apply_outputs_writes_master_meshmap_and_districts_json(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    dj = tmp_path / "districts.json"
    dj.write_text(json.dumps(DISTRICTS, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(ms, "SUBDISTRICTS_MASTER_CSV", data_dir / "subdistricts_master.csv")
    monkeypatch.setattr(ms, "MESH_SUBDISTRICTS_CSV", data_dir / "mesh_subdistricts.csv")
    monkeypatch.setattr(ms, "DISTRICTS_JSON", dj)

    ms.apply_outputs(_mesh_df(), DISTRICTS, ["d15"])

    master = pd.read_csv(data_dir / "subdistricts_master.csv", dtype=str)
    assert list(master["sub_id"]) == ["d15a", "d15b"]

    meshmap = pd.read_csv(data_dir / "mesh_subdistricts.csv", dtype=str).fillna("")
    assert (meshmap.loc[meshmap["district_id"] == "d01", "sub_id"] == "").all()
    assert set(meshmap.loc[meshmap["district_id"] == "d15", "sub_id"]) == {"d15a", "d15b"}

    out = json.loads(dj.read_text(encoding="utf-8"))
    d15 = next(d for d in out if d["id"] == "d15")
    assert [s["id"] for s in d15["sub"]] == ["d15a", "d15b"]
    assert all("lat" in s and "lon" in s and s["kana"] for s in d15["sub"])
    d01 = next(d for d in out if d["id"] == "d01")
    assert "sub" not in d01


def test_apply_outputs_keeps_human_edits(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    dj = tmp_path / "districts.json"
    dj.write_text(json.dumps(DISTRICTS, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(ms, "SUBDISTRICTS_MASTER_CSV", data_dir / "subdistricts_master.csv")
    monkeypatch.setattr(ms, "MESH_SUBDISTRICTS_CSV", data_dir / "mesh_subdistricts.csv")
    monkeypatch.setattr(ms, "DISTRICTS_JSON", dj)

    ms.apply_outputs(_mesh_df(), DISTRICTS, ["d15"])
    # 人間が d15b の表示名・かなを直した想定
    master = pd.read_csv(data_dir / "subdistricts_master.csv", dtype=str).fillna("")
    master.loc[master["sub_id"] == "d15b", ["display_name", "kana"]] = ["妙見寺のほう", "みょうけんじ"]
    master.to_csv(data_dir / "subdistricts_master.csv", index=False)

    ms.apply_outputs(_mesh_df(), DISTRICTS, ["d15"])   # 再実行
    out = json.loads(dj.read_text(encoding="utf-8"))
    d15b = next(s for d in out if d["id"] == "d15"
                for s in d.get("sub", []) if s["id"] == "d15b")
    assert d15b["name"] == "妙見寺のほう" and d15b["kana"] == "みょうけんじ"
