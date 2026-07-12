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


def test_split_directions_unique_when_rep_is_at_the_edge():
    """2026-07-12 バグ修正: 代表点が地区の西端にあると、全クラスタの重心が
    代表点より東になり全サブが「(ひがし)」と命名されていた(実データ5地区で発生)。
    方角は「クラスタ同士の真ん中」から見るので、必ず名前が区別できる"""
    rows = ms.split_district(_mesh_df(), DISTRICTS[1])
    names = [r["name"] for r in rows]
    assert len(set(names)) == len(names)          # 重複なし
    assert names == ["東沢地区(にし)", "東沢地区(ひがし)"]
    assert [r["kana"] for r in rows] == ["ひがしざわ(にし)", "ひがしざわ(ひがし)"]


def test_assign_directions_numbers_only_as_last_resort():
    """重心が完全一致する退化ケースだけ番号で区別する(通常は方角で分かれる)"""
    dirs = ms.assign_directions([(38.2, 140.3), (38.2, 140.3)], 38.2)
    assert dirs[0][0] != dirs[1][0]


# ===============================================================
# 2026-07-12 バグ修正: 対象を絞った再実行で、他の分割済み地区が消えないこと
# ===============================================================
def _two_wide_setup():
    """d15と同じ「西に密集+東に散在」の地区をもう1つ(d40)足したフィクスチャ"""
    rows = []
    for i, pop in enumerate([500, 300, 200]):
        rows.append({"meshcode": f"40{i}", "district_id": "d40",
                     "municipality": "上山市", "source_school": "Y小学校区",
                     "population": pop, "lat": 38.10, "lon": WEST_LON + i * 0.002})
    for i, pop in enumerate([100, 100, 100, 100]):
        rows.append({"meshcode": f"50{i}", "district_id": "d40",
                     "municipality": "上山市", "source_school": "Y小学校区",
                     "population": pop, "lat": 38.10, "lon": EAST_LON + i * 0.002})
    mesh = pd.concat([_mesh_df(), pd.DataFrame(rows)], ignore_index=True)
    districts = DISTRICTS + [{"id": "d40", "name": "Y地区", "kana": "わい",
                              "municipality": "上山市", "lat": 38.10, "lon": WEST_LON}]
    return mesh, districts


def test_apply_outputs_rerun_with_subset_keeps_other_splits(tmp_path, monkeypatch):
    """d15とd40を分割済みの状態で --districts d15 だけ再実行しても、
    d40のサブ地区がマスタ・メッシュ対応・districts.json から消えないこと
    (旧実装はここで黙ってd40の分割を全部消していた)"""
    mesh, districts = _two_wide_setup()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    dj = tmp_path / "districts.json"
    dj.write_text(json.dumps(districts, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(ms, "SUBDISTRICTS_MASTER_CSV", data_dir / "subdistricts_master.csv")
    monkeypatch.setattr(ms, "MESH_SUBDISTRICTS_CSV", data_dir / "mesh_subdistricts.csv")
    monkeypatch.setattr(ms, "DISTRICTS_JSON", dj)

    ms.apply_outputs(mesh, districts, ["d15", "d40"])
    ms.apply_outputs(mesh, districts, ["d15"])   # d40を指定しない再実行

    master = pd.read_csv(data_dir / "subdistricts_master.csv", dtype=str)
    assert set(master["parent_id"]) == {"d15", "d40"}
    out = json.loads(dj.read_text(encoding="utf-8"))
    d40 = next(d for d in out if d["id"] == "d40")
    assert [s["id"] for s in d40["sub"]] == ["d40a", "d40b"]
    meshmap = pd.read_csv(data_dir / "mesh_subdistricts.csv", dtype=str).fillna("")
    assert set(meshmap.loc[meshmap["district_id"] == "d40", "sub_id"]) == {"d40a", "d40b"}


def test_apply_outputs_remove_unsplits_explicitly(tmp_path, monkeypatch):
    """分割をやめるのは --remove を明示したときだけ。3つの出力すべてから消える"""
    mesh, districts = _two_wide_setup()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    dj = tmp_path / "districts.json"
    dj.write_text(json.dumps(districts, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(ms, "SUBDISTRICTS_MASTER_CSV", data_dir / "subdistricts_master.csv")
    monkeypatch.setattr(ms, "MESH_SUBDISTRICTS_CSV", data_dir / "mesh_subdistricts.csv")
    monkeypatch.setattr(ms, "DISTRICTS_JSON", dj)

    ms.apply_outputs(mesh, districts, ["d15", "d40"])
    ms.apply_outputs(mesh, districts, ["d15"], remove=["d40"])

    master = pd.read_csv(data_dir / "subdistricts_master.csv", dtype=str)
    assert set(master["parent_id"]) == {"d15"}
    out = json.loads(dj.read_text(encoding="utf-8"))
    assert "sub" not in next(d for d in out if d["id"] == "d40")
    meshmap = pd.read_csv(data_dir / "mesh_subdistricts.csv", dtype=str).fillna("")
    assert (meshmap.loc[meshmap["district_id"] == "d40", "sub_id"] == "").all()
