# -*- coding: utf-8 -*-
"""
病院(国土数値情報P04)とスーパー(OSM Overpass API)のデータを、
出どころの違いを気にしなくてよい共通のCSV形式(data/facilities.csv)に統合する。
詳しい方針は docs/plan_gap_map.md §6.2 を参照。

出力列: name(施設名), category(hospital/supermarket), lat, lon,
        source(ksj_p04/osm、どちらのデータかを検証用に残す)

病院・スーパーとも、対象市町村(config.TARGET_MUNICIPALITIES)だけに絞り込まず、
少し広め(病院は山形県全域、スーパーは対象市町村を覆うbbox)に取得する。
「メッシュから見て一番近い施設」を探すとき、市境のすぐ外にある施設が
候補から漏れてしまわないようにするため(§3の設計方針と同じ考え方)。

実行方法: プロジェクトのルートで `python3 gap_map/fetch_facilities.py`
"""

import json
import struct

import pandas as pd
import requests
from shapely.geometry import shape

import config

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Overpass APIは名乗り(User-Agent)がないと拒否されることがあるので付けておく
HEADERS = {"User-Agent": "yamagata-gtfs-study/1.0 (learning script)"}

# 国土数値情報P04の「医療機関分類」コード。既定では1(病院)のみ採用する
# (2=一般診療所、3=歯科診療所は歯科等が混ざり過剰になるため第1弾では対象外。§6.2)
HOSPITAL_CODES = {"1"} | ({"2", "3"} if config.INCLUDE_CLINICS else set())


# ===============================================================
# 病院データ(国土数値情報P04)
# ===============================================================
def read_shp_points(path) -> list[tuple[float, float]]:
    """Shapefile(.shp、点データ)から座標(経度, 緯度)のリストを読む。

    本来はgeopandasで読むのが簡単だが、geopandasが依存するpyprojの
    ビルドにはPROJという別ソフトが必要で、この環境には入っていない
    (Homebrewが不調なため導入が難しい。§4のr5py見送りと同じ事情)。
    P04は「点(Point)」のみのシンプルな形式なので、Shapefileの仕様書通りに
    自前で読む(shape_type=1のPointのみ対応)。
    """
    with open(path, "rb") as f:
        data = f.read()

    shape_type = struct.unpack("<i", data[32:36])[0]
    if shape_type != 1:
        raise ValueError(f"Point型(shape_type=1)以外は未対応です: shape_type={shape_type}")

    points = []
    offset = 100  # ファイルヘッダーは固定100バイト
    while offset < len(data):
        # レコードヘッダー(8バイト): レコード番号・内容の長さ(共にビッグエンディアン)
        _, content_words = struct.unpack(">ii", data[offset:offset + 8])
        offset += 8
        content = data[offset:offset + content_words * 2]
        # 内容: shape_type(4バイト) + X(8バイト) + Y(8バイト)、リトルエンディアン
        x, y = struct.unpack("<dd", content[4:20])
        points.append((x, y))
        offset += content_words * 2
    return points


def read_dbf_records(path) -> list[dict]:
    """DBF(属性テーブル)を読み、1レコード1辞書のリストにして返す。
    文字コードは国土数値情報の仕様通りshift_jis"""
    with open(path, "rb") as f:
        data = f.read()

    n_records = struct.unpack("<I", data[4:8])[0]
    header_len = struct.unpack("<H", data[8:10])[0]
    record_len = struct.unpack("<H", data[10:12])[0]

    # フィールド定義は32バイト目から、1つ32バイトずつ、0x0Dが来たら終わり
    fields = []
    offset = 32
    while data[offset] != 0x0D:
        name = data[offset:offset + 11].split(b"\x00")[0].decode("ascii")
        flen = data[offset + 16]
        fields.append((name, flen))
        offset += 32

    records = []
    for i in range(n_records):
        start = header_len + i * record_len
        pos = start + 1  # 先頭1バイトは削除フラグなので読み飛ばす
        rec = {}
        for name, flen in fields:
            raw = data[pos:pos + flen]
            rec[name] = raw.decode("shift_jis", errors="replace").strip()
            pos += flen
        records.append(rec)
    return records


def load_hospitals() -> pd.DataFrame:
    """P04医療機関データ(山形県全域)から、病院(HOSPITAL_CODESの分類)だけ抽出する"""
    points = read_shp_points(config.P04_SHP)
    records = read_dbf_records(config.P04_DBF)
    assert len(points) == len(records), "shpとdbfの件数が一致しません(データ破損の疑い)"

    rows = []
    for (lon, lat), rec in zip(points, records):
        if rec["P04_001"] in HOSPITAL_CODES:
            rows.append({
                "name": rec["P04_002"],
                "category": "hospital",
                "lat": lat,
                "lon": lon,
                "source": "ksj_p04",
            })
    return pd.DataFrame(rows)


# ===============================================================
# スーパーデータ(OSM Overpass API)
# ===============================================================
def get_target_bbox() -> tuple[float, float, float, float]:
    """N03行政区域データから、対象市町村(config.TARGET_MUNICIPALITIES)を
    すべて覆う範囲(南端緯度, 西端経度, 北端緯度, 東端経度)を求める。

    市町村名で個別に絞り込むのでなく、この座標範囲(bbox)でOverpassに問い合わせる。
    こうしておけば、対象市町村が増えても(県全域化しても)このコードの変更なしで
    範囲が自動的に広がる(§3・§6.2の設計方針)。
    """
    with open(config.N03_GEOJSON, encoding="utf-8") as f:
        geojson = json.load(f)

    polygons = [
        shape(feature["geometry"])
        for feature in geojson["features"]
        if feature["properties"]["N03_004"] in config.TARGET_MUNICIPALITIES
    ]
    if not polygons:
        raise SystemExit("対象市町村がN03データに見つかりません")

    lons = [p.bounds[0] for p in polygons] + [p.bounds[2] for p in polygons]
    lats = [p.bounds[1] for p in polygons] + [p.bounds[3] for p in polygons]
    return min(lats), min(lons), max(lats), max(lons)


def load_supermarkets() -> pd.DataFrame:
    """OSM Overpass APIから、対象範囲(bbox)内のスーパーを取得する。
    config.INCLUDE_CONVENIENCE を True にするとコンビニも一緒に取る"""
    south, west, north, east = get_target_bbox()
    shop_pattern = "supermarket"
    if config.INCLUDE_CONVENIENCE:
        shop_pattern = "supermarket|convenience"

    query = f"""
    [out:json][timeout:90];
    (
      node["shop"~"^({shop_pattern})$"]({south},{west},{north},{east});
      way["shop"~"^({shop_pattern})$"]({south},{west},{north},{east});
    );
    out center tags;
    """
    res = requests.post(OVERPASS_URL, data={"data": query}, headers=HEADERS, timeout=120)
    res.raise_for_status()
    elements = res.json()["elements"]

    rows = []
    for el in elements:
        tags = el.get("tags", {})
        # nodeはlat/lonを直接持つが、wayはcenterの中に入っている
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        rows.append({
            "name": tags.get("name", "(名前なし)"),
            "category": "supermarket",
            "lat": lat,
            "lon": lon,
            "source": "osm",
        })
    return pd.DataFrame(rows)


# ===============================================================
# メイン処理
# ===============================================================
def main():
    print("① 病院データ(国土数値情報P04・山形県全域)を読み込み中...")
    hospitals = load_hospitals()
    print(f"   病院: {len(hospitals)}件")

    print("② スーパーデータ(OSM Overpass API)を取得中...")
    supermarkets = load_supermarkets()
    print(f"   スーパー: {len(supermarkets)}件")

    df = pd.concat([hospitals, supermarkets], ignore_index=True)
    df.to_csv(config.FACILITIES_CSV, index=False)
    print(f"\n→ {config.FACILITIES_CSV} に{len(df)}件を書き出しました")

    print("\n=== 完成条件チェック(計画書M2) ===")
    has_central = hospitals["name"].str.contains("県立中央病院").any()
    has_yamazawa = supermarkets["name"].str.contains("ヤマザワ", na=False).any()
    print(f"  県立中央病院を含む: {has_central}")
    print(f"  ヤマザワを含む: {has_yamazawa}")


if __name__ == "__main__":
    main()
