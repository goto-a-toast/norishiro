# -*- coding: utf-8 -*-
"""
F1: 地区マスタを作る(docs/plan_final_sprint.md §3・F1)。

「地区」= かんたんモードで高齢者が選ぶ出発地。小学校区をベースにする。

  - 山形市: 国土数値情報 A27(小学校区ポリゴン・平成28年版)の36学区。
            各メッシュの中心点がどの学区ポリゴンに入るかで割り当てる
  - 上山市: A27に未収録のため、国土数値情報 P29(学校・点データ)の
            小学校5校への「最寄り割り当て」で学区相当の地区を作る(近似)

入力:
  data/target_meshes.csv            … 対象817メッシュ(M1の凍結出力)
  data/A27-16_06_GML/shape/A27-16_06.{shp,dbf} … 小学校区ポリゴン
  data/P29-21_06_GML/P29-21_06.{shp,dbf}       … 学校点データ

出力:
  data/mesh_districts.csv           … メッシュ→地区の対応表(検算・後工程用)
  data/districts_master.csv         … 地区マスタ(人間が表示名・かなを編集する。
                                        再実行しても編集済みの列は上書きしない)
  webapp/data/districts.json        … Webアプリが読む地区マスタ(masterから生成)

実行方法: プロジェクトのルートで `python3 gap_map/make_districts.py`

検算(計画書F1の完成条件):
  - 地区数が2市合計で35〜50
  - 全817メッシュがいずれかの地区に属する
  - 地区別人口の合計が276,482人に一致(取りこぼしなし)
"""

import csv
import json
import struct
from pathlib import Path

import pandas as pd
from shapely.geometry import MultiPolygon, Point, Polygon

import config
from build_network import haversine_m
from fetch_facilities import read_dbf_records, read_shp_points

PROJECT_ROOT = Path(__file__).parent.parent

A27_SHP = config.DATA_DIR / "A27-16_06_GML" / "shape" / "A27-16_06.shp"
A27_DBF = config.DATA_DIR / "A27-16_06_GML" / "shape" / "A27-16_06.dbf"
P29_SHP = config.DATA_DIR / "P29-21_06_GML" / "P29-21_06.shp"
P29_DBF = config.DATA_DIR / "P29-21_06_GML" / "P29-21_06.dbf"

MESH_DISTRICTS_CSV = config.DATA_DIR / "mesh_districts.csv"
DISTRICTS_MASTER_CSV = config.DATA_DIR / "districts_master.csv"
DISTRICTS_JSON = PROJECT_ROOT / "webapp" / "data" / "districts.json"

# 市町村コード(JIS)。target_meshes.csv の municipality 列と対応させる
CITY_CODE = {"山形市": "06201", "上山市": "06207"}
P29_ELEMENTARY = "16001"   # P29の学校分類コード: 16001 = 小学校


# ===============================================================
# Shapefileのポリゴン読み込み
# (fetch_facilities.read_shp_points は点専用なので、ポリゴン版をここに置く。
#  geopandasを使わない理由は read_shp_points のコメントと同じ=環境の事情)
# ===============================================================
def _ring_is_clockwise(ring: list) -> bool:
    """リング(座標列)が時計回りかどうか(shoelace公式の符号で判定)。
    Shapefileの仕様では、外周リング=時計回り、穴(中抜き)=反時計回り"""
    s = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        s += (x2 - x1) * (y2 + y1)
    return s > 0


def read_shp_polygons(path) -> list:
    """Shapefile(.shp、shape_type=5のポリゴン)を読み、レコードごとに
    shapelyのPolygon/MultiPolygonを返す(dbfのレコード順と同じ並び)。

    ポリゴンレコードの中身(仕様書どおり):
      shape_type(4B) + bbox(32B) + パーツ数(4B) + 点数(4B)
      + 各パーツの開始位置(4B×パーツ数) + 座標(16B×点数)
    パーツ=リング。外周リングと穴を回転方向で見分けて組み立てる。
    """
    with open(path, "rb") as f:
        data = f.read()

    shape_type = struct.unpack("<i", data[32:36])[0]
    if shape_type != 5:
        raise ValueError(f"Polygon型(shape_type=5)以外は未対応です: shape_type={shape_type}")

    geoms = []
    offset = 100  # ファイルヘッダーは固定100バイト
    while offset < len(data):
        _, content_words = struct.unpack(">ii", data[offset:offset + 8])
        offset += 8
        content = data[offset:offset + content_words * 2]
        offset += content_words * 2

        n_parts, n_points = struct.unpack("<ii", content[36:44])
        parts = struct.unpack(f"<{n_parts}i", content[44:44 + 4 * n_parts])
        pts_start = 44 + 4 * n_parts
        xy = struct.unpack(f"<{2 * n_points}d", content[pts_start:pts_start + 16 * n_points])
        points = list(zip(xy[0::2], xy[1::2]))

        # パーツ境界でリングに分割する
        rings = []
        for i, start in enumerate(parts):
            end = parts[i + 1] if i + 1 < n_parts else n_points
            ring = points[start:end]
            if len(ring) >= 4:   # 閉じたリングは最低4点(始点=終点を含む)
                rings.append(ring)

        # 外周リング(時計回り)と穴(反時計回り)に仕分けて組み立てる
        outers = [r for r in rings if _ring_is_clockwise(r)]
        holes = [r for r in rings if not _ring_is_clockwise(r)]
        polys = []
        for outer in outers:
            shell = Polygon(outer)
            my_holes = [h for h in holes if shell.contains(Point(h[0]))]
            polys.append(Polygon(outer, my_holes))
        if not polys:            # 回転方向が仕様と逆のデータへの保険
            polys = [Polygon(r) for r in rings]
        geoms.append(polys[0] if len(polys) == 1 else MultiPolygon(polys))
    return geoms


# ===============================================================
# 地区の元データ読み込み
# ===============================================================
def load_yamagata_school_districts() -> list:
    """A27から山形市の小学校区を [(学校名, ポリゴン), ...] で返す"""
    geoms = read_shp_polygons(A27_SHP)
    records = read_dbf_records(A27_DBF)
    assert len(geoms) == len(records), "A27のshpとdbfの件数が一致しません"
    return [(rec["A27_007"], geom) for rec, geom in zip(records, geoms)
            if rec["A27_005"] == CITY_CODE["山形市"]]


def load_kaminoyama_schools() -> list:
    """P29から上山市の小学校を [(学校名, lat, lon), ...] で返す"""
    points = read_shp_points(P29_SHP)
    records = read_dbf_records(P29_DBF)
    assert len(points) == len(records), "P29のshpとdbfの件数が一致しません"
    return [(rec["P29_004"], lat, lon) for rec, (lon, lat) in zip(records, points)
            if rec["P29_001"] == CITY_CODE["上山市"] and rec["P29_003"] == P29_ELEMENTARY]


# ===============================================================
# メッシュ→地区の割り当て
# ===============================================================
def assign_meshes(meshes: pd.DataFrame) -> pd.DataFrame:
    """各メッシュに (municipality, source_school) を割り当てて返す"""
    yamagata = load_yamagata_school_districts()
    kaminoyama = load_kaminoyama_schools()
    print(f"山形市の学区ポリゴン: {len(yamagata)}件 / 上山市の小学校: {len(kaminoyama)}校")

    schools = []
    n_fallback = 0
    for row in meshes.itertuples():
        if row.municipality == "山形市":
            pt = Point(row.lon, row.lat)
            hit = [name for name, geom in yamagata if geom.contains(pt)]
            if hit:
                schools.append(hit[0])
            else:
                # 市境の際などでどのポリゴンにも入らないメッシュは、最寄りの学区へ
                # (shapelyのdistanceは度単位の近似だが、隣接学区の判定には十分)
                n_fallback += 1
                schools.append(min(yamagata, key=lambda ng: ng[1].distance(pt))[0])
        elif row.municipality == "上山市":
            # 上山市はA27未収録のため「最寄りの小学校」で学区を近似(計画書§3)
            schools.append(min(
                kaminoyama,
                key=lambda s: haversine_m(row.lat, row.lon, s[1], s[2]))[0])
        else:
            raise ValueError(f"想定外の市町村です: {row.municipality}")

    if n_fallback:
        print(f"※どの学区ポリゴンにも入らず最寄り割り当てにした山形市メッシュ: {n_fallback}件")
    out = meshes.copy()
    out["source_school"] = schools
    return out


def auto_district_name(school_name: str) -> str:
    """学校名から地区名の初期値を作る(例: 金井小学校 → 金井地区)。
    住民感覚と合わない場合は districts_master.csv の display_name を人間が直す"""
    return school_name.replace("小学校", "") + "地区"


# ===============================================================
# 地区マスタの組み立てと出力
# ===============================================================
def build_master(meshes: pd.DataFrame) -> pd.DataFrame:
    """地区ごとに代表点(人口最大メッシュの中心)を決め、地区マスタ表を作る"""
    rows = []
    for (municipality, school), g in meshes.groupby(["municipality", "source_school"]):
        # 代表点 = その地区で人口が最大のメッシュの中心(同数なら小さいメッシュコード)
        top = g.sort_values(["population", "meshcode"],
                            ascending=[False, True]).iloc[0]
        rows.append({
            "municipality": municipality,
            "source_school": school,
            "name": auto_district_name(school),
            "display_name": "",   # 人間が上書きする列(空なら name を使う)
            "kana": "",           # 人間が記入する列(F4の音声読み上げ・ふりがなに使う)
            "population": int(g["population"].sum()),
            "mesh_count": len(g),
            "rep_meshcode": int(top["meshcode"]),
            "lat": round(float(top["lat"]), 6),
            "lon": round(float(top["lon"]), 6),
        })

    # idは並び順から機械的に振る(山形市→上山市、学校名順。再実行しても同じidになる)
    rows.sort(key=lambda r: (r["municipality"] != "山形市", r["source_school"]))
    for i, r in enumerate(rows, start=1):
        r["id"] = f"d{i:02d}"
    df = pd.DataFrame(rows)
    return df[["id", "municipality", "source_school", "name", "display_name",
               "kana", "population", "mesh_count", "rep_meshcode", "lat", "lon"]]


def merge_human_edits(master: pd.DataFrame) -> pd.DataFrame:
    """既存の districts_master.csv があれば、人間が編集した display_name / kana を
    引き継ぐ(市町村+学校名で対応付け)。再実行で人手の作業を消さないための仕組み"""
    if not DISTRICTS_MASTER_CSV.exists():
        return master
    old = pd.read_csv(DISTRICTS_MASTER_CSV, dtype=str).fillna("")
    edits = {(r["municipality"], r["source_school"]): (r["display_name"], r["kana"])
             for _, r in old.iterrows()}
    kept = 0
    for i, row in master.iterrows():
        key = (row["municipality"], row["source_school"])
        if key in edits and any(edits[key]):
            master.loc[i, ["display_name", "kana"]] = edits[key]
            kept += 1
    if kept:
        print(f"既存マスタから人間の編集(表示名・かな)を{kept}地区分引き継ぎました")
    return master


def write_outputs(meshes: pd.DataFrame, master: pd.DataFrame) -> None:
    # メッシュ→地区の対応表(地区idを付けて保存)
    id_map = {(r["municipality"], r["source_school"]): r["id"]
              for _, r in master.iterrows()}
    mesh_out = meshes.copy()
    mesh_out["district_id"] = [
        id_map[(r.municipality, r.source_school)] for r in meshes.itertuples()]
    mesh_out[["meshcode", "district_id", "municipality", "source_school",
              "population"]].to_csv(MESH_DISTRICTS_CSV, index=False)
    print(f"→ {MESH_DISTRICTS_CSV} に{len(mesh_out)}件")

    # 人間が編集するマスタCSV(この後gitにコミットして編集内容を残す)
    master.to_csv(DISTRICTS_MASTER_CSV, index=False,
                  quoting=csv.QUOTE_NONNUMERIC)
    print(f"→ {DISTRICTS_MASTER_CSV} に{len(master)}地区")

    # Webアプリ用JSON(表示名が空なら自動生成名を使う)
    DISTRICTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    records = []
    for _, r in master.iterrows():
        records.append({
            "id": r["id"],
            "name": r["display_name"] or r["name"],
            "kana": r["kana"],
            "municipality": r["municipality"],
            "lat": r["lat"],
            "lon": r["lon"],
            "source_school": r["source_school"] + "区",
        })
    DISTRICTS_JSON.write_text(
        json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {DISTRICTS_JSON} に{len(records)}地区")


def main():
    meshes = pd.read_csv(config.TARGET_MESHES_CSV)
    print(f"対象メッシュ: {len(meshes)}件(target_meshes.csv)")

    assigned = assign_meshes(meshes)
    master = merge_human_edits(build_master(assigned))
    write_outputs(assigned, master)

    # ===== 検算(計画書F1の完成条件)=====
    print("\n=== 検算(F1完成条件) ===")
    n_districts = len(master)
    total_pop = master["population"].sum()
    total_mesh = master["mesh_count"].sum()
    # 期待値は地域設定に持つ(山形: 総人口276,482人。期待値の無い地域は観測値のみ表示)
    from region import region_expected, is_default_region
    if is_default_region():
        print(f"地区数: {n_districts}(条件: 2市合計で35〜50)"
              f" → {'OK' if 35 <= n_districts <= 50 else 'NG'}")
    else:
        print(f"地区数: {n_districts} … 多すぎ/少なすぎでないか目視確認してください")
    print(f"所属メッシュ合計: {total_mesh}(条件: 全{len(meshes)}メッシュ)"
          f" → {'OK' if total_mesh == len(meshes) else 'NG'}")
    want_pop = region_expected("total_population")
    if want_pop is not None:
        print(f"地区別人口の合計: {total_pop:,}人(条件: {want_pop:,}人に一致)"
              f" → {'OK' if total_pop == want_pop else 'NG'}")
    else:
        print(f"地区別人口の合計: {total_pop:,}人 … 対象市町村の実際の人口と"
              f"桁が合っているか目視確認してください")

    print("\n=== 地区一覧(表示名・かなの目視確認用) ===")
    with pd.option_context("display.max_rows", None, "display.unicode.east_asian_width", True):
        print(master[["id", "municipality", "name", "population",
                      "mesh_count", "rep_meshcode"]].to_string(index=False))
    print("\n※ data/districts_master.csv の display_name(学区名が住民感覚と合わない場合)と"
          "\n   kana(全行必須。F4の音声読み上げに使う)を人間が記入・確認してください。"
          "\n   記入後にもう一度このスクリプトを実行すると districts.json に反映されます。")


if __name__ == "__main__":
    main()
