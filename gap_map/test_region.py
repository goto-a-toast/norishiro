# -*- coding: utf-8 -*-
"""region.py(全国展開キットR1)の単体テスト。

最重要ルール「山形版の動作は1バイトも変えない」をテストで固定する:
  - region.json が無ければ、config の値が従来の山形設定と完全一致すること
  - マスタCSV(operators/demand_phone)から復元した辞書が、外出し前の直書き辞書と
    完全一致すること(meta.json のバイト一致の前提)
  - region.json があるときだけ上書きが効き、expected(山形の確定値)は引き継がれないこと

実行: python -m pytest gap_map/test_region.py -v
"""
import json

import region
from export_web_data import load_demand_phone, load_operator_contact


def test_defaults_match_frozen_yamagata_config():
    """region.json が無い環境では、従来の山形設定と完全一致する(1バイトも変えない)"""
    r = region.load()
    assert r["_is_default"] is True
    assert r["target_municipalities"] == ["山形市", "上山市"]
    assert r["target_date"] == "20260610"
    assert r["gtfs_feed_dirs"][0] == "gtfs_山形交通" and len(r["gtfs_feed_dirs"]) == 9
    assert r["reference_feed"] == "山形交通"
    assert r["reference_dates"] == {"weekday": "20260610", "saturday": "20260613",
                                    "sunday_holiday": "20260614"}
    assert r["date_table_start"] == "20260701" and r["valid_until"] == "20260930"
    assert r["n03_geojson"].endswith("N03-23_06_230101.geojson")
    assert len(r["town_spots"]) == 5           # 山形駅・山形市役所・かみのやま温泉駅・上山市役所・上山城
    assert r["expected"]["total_population"] == 276482
    assert r["expected"]["gap_population"] == 15418
    # R2: 地区分け方式の既定値(従来のハードコードと同じ組み合わせ)
    assert r["district_methods"] == {"山形市": "a27_polygon", "上山市": "p29_nearest_school"}
    assert r["a27_shp"].endswith("A27-16_06.shp") and r["p29_shp"].endswith("P29-21_06.shp")


def test_config_uses_region_values():
    """config.py が region 経由でも従来と同じ値になっている(互換レイヤの確認)"""
    import config
    assert config.TARGET_MUNICIPALITIES == ["山形市", "上山市"]
    assert config.TARGET_DATE == "20260610"
    assert [d.name for d in config.GTFS_FEED_DIRS][:3] == ["gtfs_山形交通", "gtfs_上山市", "gtfs_山形市"]
    assert config.N03_GEOJSON.name == "N03-23_06_230101.geojson"
    assert config.P04_SHP.name == "P04-14_06-g_MedicalInstitution.shp"


def test_region_json_overrides_only_written_keys(tmp_path):
    """region.json に書いたキーだけ上書きされ、書かないキーは山形既定値のまま。
    ただし expected(山形の確定値)は別地域に引き継いではいけないので空になる"""
    rj = tmp_path / "region.json"
    rj.write_text(json.dumps({
        "region_name": "天童市",
        "target_municipalities": ["天童市"],
        "gtfs_feed_dirs": ["gtfs_天童市"],
        "reference_feed": "天童市",
    }, ensure_ascii=False), encoding="utf-8")
    r = region.load(rj)
    assert r["_is_default"] is False
    assert r["target_municipalities"] == ["天童市"]
    assert r["reference_feed"] == "天童市"
    assert r["target_date"] == "20260610"        # 書いていないキーは既定値のまま
    assert r["expected"] == {}                    # 山形の確定値は引き継がない


def test_region_expected_none_when_not_set(tmp_path, monkeypatch):
    """別地域(expected未設定)では region_expected() が None を返し、
    呼び出し側は厳密一致チェック→観測値表示に切り替えられる"""
    rj = tmp_path / "region.json"
    rj.write_text(json.dumps({"region_name": "テスト市"}), encoding="utf-8")
    monkeypatch.setattr(region, "REGION", region.load(rj))
    assert region.region_expected("total_population") is None
    assert region.is_default_region() is False


def test_master_csvs_reproduce_frozen_dicts_exactly():
    """マスタCSVから復元した辞書が、外出し前の直書き辞書と完全一致する
    (これが崩れると meta.json のバイト一致が崩れる)"""
    demand = load_demand_phone()
    assert demand == [
        {"name": "スマイルグリーン号(大郷明治デマンド型乗合タクシー・予約: 山交ハイヤー)",
         "tel": "023-681-3809", "districts": ["大郷地区", "明治地区"]},
        {"name": "上山市営予約制乗合タクシー", "tel": "023-632-2850",
         "districts": ["西郷第一地区", "中川地区"]},
        {"name": "山形市 公共交通課(バスの相談窓口)", "tel": "023-641-1212",
         "districts": "山形市のその他全地区"},
        {"name": "上山市 市政戦略課(バスの相談窓口)", "tel": "023-672-1111",
         "districts": "上山市のその他全地区"},
    ]
    ops = load_operator_contact()
    assert len(ops) == 9
    assert ops["山形交通"] == {"name": "山交バス", "desk": "案内センター", "tel": "023-632-7272"}
    assert ops["天童市"] == {"name": "天童市営バス", "desk": "天童市役所", "tel": None}
    assert ops["南陽市"]["tel"] is None          # 未確認の電話は未記入(None)のまま
    assert ops["中山町"]["name"] == "中山町営バス(中山ふれあい号)"


def test_reference_feed_resolution():
    """基準フィードが region の指定から正しく解決される(旧: 直書きassert)"""
    from export_web_data import REFERENCE_FEED_DIR
    assert REFERENCE_FEED_DIR.name == "gtfs_山形交通"
