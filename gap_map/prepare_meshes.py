# -*- coding: utf-8 -*-
"""
e-Statの人口メッシュCSVを読み込み、対象市町村(config.TARGET_MUNICIPALITIES)に
含まれるメッシュだけを抽出して data/target_meshes.csv を作る。

手順(詳しくは docs/plan_gap_map.md §6.3 を参照):
  1. 人口メッシュCSVを読み、人口>0のメッシュだけ残す
  2. 各メッシュの中心点が、N03行政区域(市町村境界)の
     対象市町村ポリゴンに入っているかを判定する(shapelyのcontains)
  3. target_meshes.csv(meshcode, lat, lon, population, municipality)を書き出す

実行方法: プロジェクトのルートで `python3 gap_map/prepare_meshes.py`
"""

import json

import pandas as pd
from shapely.geometry import Point, shape

import config
import meshcode


def load_target_municipality_polygons() -> dict:
    """N03行政区域のGeoJSONを読み、対象市町村(config.TARGET_MUNICIPALITIES)の
    ポリゴンだけを {市町村名: shapelyポリゴン} の辞書にして返す"""
    with open(config.N03_GEOJSON, encoding="utf-8") as f:
        geojson = json.load(f)

    polygons = {}
    for feature in geojson["features"]:
        name = feature["properties"]["N03_004"]  # 市町村名の列
        if name in config.TARGET_MUNICIPALITIES:
            polygons[name] = shape(feature["geometry"])

    missing = set(config.TARGET_MUNICIPALITIES) - polygons.keys()
    if missing:
        raise SystemExit(f"N03データに市町村が見つかりません: {missing}")
    return polygons


def find_municipality(lat: float, lon: float, polygons: dict) -> str | None:
    """緯度経度の点が、対象市町村のどれかのポリゴンに含まれていれば市町村名を返す。
    どの市町村にも含まれていなければ None(=対象外のメッシュ)"""
    # shapelyの点はPoint(経度, 緯度)の順で作ることに注意(x=経度, y=緯度)
    point = Point(lon, lat)
    for name, polygon in polygons.items():
        if polygon.contains(point):
            return name
    return None


def load_population_meshes() -> pd.DataFrame:
    """config.POP_MESH_FILES のe-Statメッシュ人口CSVを全部読み込んで結合し、
    人口>0のメッシュだけ残して返す(列: meshcode, population, population_65plus, population_75plus)"""
    frames = []
    for path in config.POP_MESH_FILES:
        # 1行目=列コード(KEY_CODE, T001101001, ...)、2行目=列の説明文(スキップする)
        # 文字コードはshift_jis
        df = pd.read_csv(path, encoding="shift_jis", skiprows=[1], dtype=str)
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)

    # T001101001=人口(総数)、T001101019=65歳以上人口(総数)、T001101022=75歳以上人口(総数)
    # (列の並び順はヘッダー2行目の説明文で確認済み。数値に変換できない行はNaNになる)
    df["population"] = pd.to_numeric(df["T001101001"], errors="coerce")
    df["population_65plus"] = pd.to_numeric(df["T001101019"], errors="coerce")
    df["population_75plus"] = pd.to_numeric(df["T001101022"], errors="coerce")
    df = df[df["population"] > 0].copy()
    df = df.rename(columns={"KEY_CODE": "meshcode"})
    return df[["meshcode", "population", "population_65plus", "population_75plus"]]


def main():
    print("① 人口メッシュCSVを読み込み中...")
    df = load_population_meshes()
    print(f"   人口>0のメッシュ: {len(df)}件")

    print("② 市町村ポリゴン(N03)を読み込み中...")
    polygons = load_target_municipality_polygons()
    print(f"   対象市町村: {list(polygons.keys())}")

    print("③ 各メッシュの中心点が対象市町村に入っているか判定中...(少し時間がかかります)")
    centers = df["meshcode"].map(meshcode.meshcode_to_center)
    df["lat"] = centers.map(lambda t: t[0])
    df["lon"] = centers.map(lambda t: t[1])
    df["municipality"] = [
        find_municipality(lat, lon, polygons)
        for lat, lon in zip(df["lat"], df["lon"])
    ]
    df = df[df["municipality"].notna()].copy()

    df = df[["meshcode", "lat", "lon", "population",
              "population_65plus", "population_75plus", "municipality"]]
    df.to_csv(config.TARGET_MESHES_CSV, index=False)

    print(f"\n→ {config.TARGET_MESHES_CSV} に {len(df)}件を書き出しました")
    print("市町村別メッシュ数:")
    print(df["municipality"].value_counts().to_string())
    from region import region_expected
    _want = region_expected("total_population")
    print(f"\n人口合計: {df['population'].sum():,.0f}人 "
          + (f"(検算目安: {_want:,}人前後)" if _want is not None
             else "… 対象市町村の実際の人口と桁が合っているか目視確認してください"))
    n_missing_age = df["population_65plus"].isna().sum()
    print(f"65歳以上人口合計: {df['population_65plus'].sum():,.0f}人 "
          f"(秘匿等により年齢内訳が欠損しているメッシュ: {n_missing_age}件)")
    print(f"75歳以上人口合計: {df['population_75plus'].sum():,.0f}人")


if __name__ == "__main__":
    main()
