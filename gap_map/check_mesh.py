# -*- coding: utf-8 -*-
"""指定した場所の500mメッシュが、なぜ交通空白マップに載っていない(載っている)かを調べる。

背景(2026-09-24 開発者質問「交通空白マップでメッシュが漏れているところ(鋳物町など)は
どう理解したら良い?」):
  マップに載るメッシュは prepare_meshes.py が次の2つで絞っている。
    ① e-Statの500mメッシュ人口が「数値として0より大きい」こと
       (population > 0。数値でない記号 "-" や "X"(秘匿)は数値に変換できず同じく除外)
    ② メッシュ中心点が、対象市町村のN03行政区域ポリゴンの内側にあること
  つまり「人が住んでいない区画」(工業団地・田畑・河川敷・山林など)は、
  設計どおり対象外になる。この地図は「移動に困る人がどこに住んでいるか」を
  見るものなので、住民のいない区画を空白と呼ぶ意味がないため。
  このスクリプトは、ある場所がどちらの理由で外れたのかを1件ずつ確かめる。

使い方(プロジェクトルートから。読み取り専用・何も書き換えない):
  python3 gap_map/check_mesh.py --lat 38.2646 --lon 140.3172
  python3 gap_map/check_mesh.py --meshcode 574026813
"""
import argparse
import json
from pathlib import Path

import config
from meshcode import latlon_to_meshcode, meshcode_to_center

PROJECT_ROOT = Path(__file__).parent.parent
KARTE_JSON = PROJECT_ROOT / "webapp" / "data" / "karte.json"
TARGET_MESHES_CSV = config.DATA_DIR / "target_meshes.csv"


def check_karte(code):
    """公開中のカルテ(=マップに載っているメッシュ)にあるか"""
    if not KARTE_JSON.exists():
        return "webapp/data/karte.json が無いので確認できません"
    karte = json.loads(KARTE_JSON.read_text(encoding="utf-8"))
    for m in karte.get("meshes", []):
        if latlon_to_meshcode(m["lat"], m["lon"]) == code:
            return (f"載っている(地区={m['district_id']} / 最寄り停={m['nearest_stop_name']} / "
                    f"評価={m['grade']})")
    return "載っていない"


def check_target_meshes(code):
    """prepare_meshes.py の出力(対象メッシュ一覧)にあるか"""
    if not TARGET_MESHES_CSV.exists():
        return "data/target_meshes.csv が無いので確認できません(分析データのある環境で実行を)"
    import pandas as pd
    df = pd.read_csv(TARGET_MESHES_CSV, dtype={"meshcode": str})
    hit = df[df["meshcode"] == code]
    if hit.empty:
        return "入っていない(=マップの対象外)"
    r = hit.iloc[0]
    return f"入っている(人口{r['population']} / {r['municipality']})"


def check_estat_raw(code):
    """e-Statの生データにその区画の行があるか、人口欄に何が入っているか。
    数値なら採用、"-" や "X"(秘匿)なら数値に変換できず除外される"""
    import pandas as pd
    for path in config.POP_MESH_FILES:
        if not Path(path).exists():
            return f"{path} が無いので確認できません(分析データのある環境で実行を)"
        df = pd.read_csv(path, encoding="shift_jis", skiprows=[1], dtype=str)
        hit = df[df["KEY_CODE"] == code]
        if not hit.empty:
            v = hit.iloc[0]["T001101001"]
            num = pd.to_numeric(v, errors="coerce")
            if pd.isna(num):
                return f"行はあるが人口欄が「{v}」(数値でないため除外される。秘匿や不明の記号)"
            if num <= 0:
                return f"行はあるが人口は {v} 人(0以下なので除外=住んでいる人がいない区画)"
            return f"人口 {v} 人(採用されるはず)"
    return "e-Statの生データに その区画の行が無い(=国勢調査で人口0として扱われている)"


def check_polygon(lat, lon):
    """中心点が対象市町村のポリゴンの内側にあるか"""
    if not Path(config.N03_GEOJSON).exists():
        return "N03データが無いので確認できません(分析データのある環境で実行を)"
    from shapely.geometry import Point, shape
    geo = json.loads(Path(config.N03_GEOJSON).read_text(encoding="utf-8"))
    pt = Point(lon, lat)
    for f in geo["features"]:
        name = f["properties"]["N03_004"]
        if name in config.TARGET_MUNICIPALITIES and shape(f["geometry"]).contains(pt):
            return f"{name} の内側"
    return "対象市町村のポリゴンの内側ではない(市境の外・境界線上など)"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--meshcode")
    args = ap.parse_args()

    if args.meshcode:
        code = args.meshcode
        lat, lon = meshcode_to_center(code)
    elif args.lat is not None and args.lon is not None:
        lat, lon = args.lat, args.lon
        code = latlon_to_meshcode(lat, lon)
        lat, lon = meshcode_to_center(code)
    else:
        raise SystemExit("--lat と --lon、または --meshcode を指定してください")

    print(f"メッシュコード : {code}")
    print(f"中心の緯度経度 : {lat:.5f}, {lon:.5f}")
    print(f"地図で見る     : https://maps.google.com/?q={lat:.5f},{lon:.5f}")
    print()
    print(f"① 公開中のマップ/カルテ : {check_karte(code)}")
    print(f"② 対象メッシュ一覧       : {check_target_meshes(code)}")
    print(f"③ e-Statの生データ       : {check_estat_raw(code)}")
    print(f"④ 市町村ポリゴンの判定   : {check_polygon(lat, lon)}")
    print()
    print("読み方: ③が「人口0」や「行が無い」なら、住民のいない区画として")
    print("        設計どおり除外されている(工業団地・田畑・河川敷など)。")
    print("        ③が数値なのに①②に無いなら、④の市町村判定を疑う。")


if __name__ == "__main__":
    main()
