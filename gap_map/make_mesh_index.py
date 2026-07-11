# -*- coding: utf-8 -*-
"""対策1(広い地区の位置精度改善): GPSの地区判定に使うメッシュ索引を生成する。

背景(docs/handover.md §5・広い地区の限界):
  GPSの「近い地区」判定を地区の代表点1点との距離で行うと、東沢地区のような
  広い学区では正しい地区が候補に出ないことがある。地区の形は住民のいる
  500mメッシュ(全817個)がタイルしているので、「一番近いメッシュの地区」で
  判定すればポリゴン相当の精度になる。この索引はそのための小さなデータ
  (約25KB。地区JSON合計37MBに対して無視できる増分)。

入力(優先順):
  1. data/mesh_subdistricts.csv … make_subdistricts.py の出力(sub_id列つき)。
     あればサブ地区IDで索引を作る=GPSがそのままサブ地区判定になる
  2. data/mesh_districts.csv    … make_districts.py の出力(親地区のみ)
  どちらも gitignore 対象なので、実行は分析データのある環境(Mac/Windows)で:
      python3 gap_map/make_districts.py     (mesh_districts.csv が無ければ先に)
      python3 gap_map/make_mesh_index.py

出力: webapp/data/mesh_index.json(コミット対象)
  {"districts": ["d01", "d15a", ...],          … 索引で使う地区IDの一覧
   "meshes": [[lat, lon, 添字], ...]}          … 各メッシュの中心座標と地区(添字)
  座標は小数5桁(≒1m)に丸める。JSは距離の並べ替えだけを行う
  (「Webは計算しない」原則の範囲内。メッシュコード→座標の変換はPython側で済ませる)
"""
import json
from pathlib import Path

import pandas as pd

import config
from meshcode import meshcode_to_center

PROJECT_ROOT = Path(__file__).parent.parent
MESH_SUBDISTRICTS_CSV = config.DATA_DIR / "mesh_subdistricts.csv"  # あれば優先(サブ地区)
MESH_DISTRICTS_CSV = config.DATA_DIR / "mesh_districts.csv"
DISTRICTS_JSON = PROJECT_ROOT / "webapp" / "data" / "districts.json"
OUT_JSON = PROJECT_ROOT / "webapp" / "data" / "mesh_index.json"


def load_mesh_table() -> pd.DataFrame:
    """メッシュ→地区IDの表を読む。sub_id列があればそれを地区IDとして使う
    (分割対象外の地区は sub_id が空なので district_id にフォールバック)"""
    if MESH_SUBDISTRICTS_CSV.exists():
        mesh = pd.read_csv(MESH_SUBDISTRICTS_CSV, dtype=str)
        src = MESH_SUBDISTRICTS_CSV.name
        mesh["use_id"] = mesh.get("sub_id", pd.Series(dtype=str)).fillna("")
        mesh.loc[mesh["use_id"] == "", "use_id"] = mesh["district_id"]
    elif MESH_DISTRICTS_CSV.exists():
        mesh = pd.read_csv(MESH_DISTRICTS_CSV, dtype=str)
        src = MESH_DISTRICTS_CSV.name
        mesh["use_id"] = mesh["district_id"]
    else:
        raise FileNotFoundError(
            "data/mesh_districts.csv がありません。分析データのある環境で "
            "python3 gap_map/make_districts.py を先に実行してください")
    need = {"meshcode", "district_id"}
    assert need <= set(mesh.columns), f"{src} の列が想定外: {list(mesh.columns)}"
    print(f"入力: data/{src}({len(mesh)}メッシュ)")
    return mesh


def known_district_ids() -> set:
    """districts.json にある地区ID(親+sub内包)を集める。索引のID検算用"""
    districts = json.loads(DISTRICTS_JSON.read_text(encoding="utf-8"))
    ids = set()
    for d in districts:
        ids.add(d["id"])
        for s in d.get("sub", []) or []:
            ids.add(s["id"])
    return ids


def main():
    mesh = load_mesh_table()

    # 地区IDの一覧(ソートして添字を固定 → 出力の冪等性)
    id_list = sorted(mesh["use_id"].unique())
    id_index = {did: i for i, did in enumerate(id_list)}

    meshes = []
    for _, row in mesh.sort_values("meshcode").iterrows():   # メッシュコード順=冪等
        lat, lon = meshcode_to_center(row["meshcode"])
        meshes.append([round(lat, 5), round(lon, 5), id_index[row["use_id"]]])

    out = {"districts": id_list, "meshes": meshes}
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")),
                        encoding="utf-8")

    # ---- 検算 ----
    n_in, n_out = len(mesh), len(meshes)
    print(f"検算1: メッシュ件数 入力{n_in} = 出力{n_out}", "OK" if n_in == n_out else "★NG")
    known = known_district_ids()
    unknown = [d for d in id_list if d not in known]
    print(f"検算2: districts.json に無いIDが索引に {len(unknown)}件",
          "OK" if not unknown else f"★NG {unknown[:5]}")
    size_kb = OUT_JSON.stat().st_size / 1024
    print(f"出力: {OUT_JSON}({size_kb:.1f} KB)")
    if unknown:
        raise SystemExit("★索引のIDが districts.json と一致しません。生成順を確認してください"
                         "(make_subdistricts.py → districts.json 反映 → 本スクリプト)")


if __name__ == "__main__":
    main()
