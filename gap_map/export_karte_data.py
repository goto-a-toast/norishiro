# -*- coding: utf-8 -*-
"""M8-1: モビリティ・カルテ(出口C)のデータ工場。docs/plan_gap_map.md §13参照。

住所やGPSから「一番近いメッシュ」を選ぶと、そのメッシュの「移動のようす」カードが
出せるように、メッシュ1つぶんの情報をあらかじめ全部 webapp/data/karte.json に
書き出しておく(Webは計算しない原則。ブラウザは距離の並べ替えだけをする)。

凍結済みの分析結果(output/access_mesh.csv)を、メッシュ→地区の対応
(make_mesh_index.load_mesh_table。サブ地区分割があれば自動でそちらを使う)で
読み替えるだけ。**新しい空白判定はしない**(is_gap は compute_access.py の結果を
そのまま使う。総合評価(A〜E)のE判定もこれと必ず一致させる)。

入力(いずれも分析成果物。この端末に無い場合はSSD等からコピーするか、
分析環境で make_districts.py → compute_access.py を実行する):
  - output/access_mesh.csv    … meshcode, population, nearest_stop_name,
                                 walk_to_stop_min, time_to_hospital_min, hospital_name,
                                 time_to_super_min, super_name, hospital_visit_ok,
                                 visit_total_min, is_gap(compute_access.pyの出力)
  - data/mesh_districts.csv または data/mesh_subdistricts.csv
                                 … メッシュ→地区(サブ地区)の対応

出力:
  - webapp/data/karte.json    … {"meta": {...}, "meshes": [...]}(§13.3)

実行(プロジェクトルートで):
  python gap_map/export_karte_data.py
"""
import json
from pathlib import Path

import pandas as pd

import config
from make_mesh_index import load_mesh_table
from meshcode import meshcode_to_center

KARTE_JSON = Path(__file__).parent.parent / "webapp" / "data" / "karte.json"
UNREACHABLE = "到達不能"   # compute_access.py / make_district_gap.py と同じ表記


def _to_bool(series: pd.Series) -> pd.Series:
    """CSVに文字列で入った真偽値("True"/"False"等)を bool に直す"""
    return series.astype(str).str.strip().str.lower().isin(["true", "1", "yes"])


def _to_minutes(series: pd.Series) -> pd.Series:
    """所要時間の列を数値化する。「到達不能」は None(欠測)にする(999分等で埋めない)"""
    return pd.to_numeric(series.astype(str).replace(UNREACHABLE, None), errors="coerce")


def _none_if_nan(v):
    """pandasのNaNをJSONにそのまま書くと不正な値(NaNリテラル)になるため None にする
    (2026-07-12 make_mesh_index.py で直した同種のバグを、ここでは最初から避ける)"""
    return None if pd.isna(v) else v


def grade_of(is_gap: bool, hospital_min) -> str:
    """病院所要時間から総合評価A〜Eを1つ選ぶ(§13.3)。

    E判定は既存の空白判定(is_gap)をそのまま使う(空白マップの空白メッシュと
    必ず一致させる。§13.4完成条件(c))。A〜Dの境界(config.KARTE_GRADE_BINS)は
    §8の地図の色分け(15/30/45/60分)と揃えている。hospital_min が None
    (バスでは病院に着けないが、is_gapがFalse=直線徒歩15分以内で行けるケース)は
    最も行きやすいAとする"""
    if is_gap:
        return "E"
    if hospital_min is None:
        return "A"
    for max_min, letter in zip(config.KARTE_GRADE_BINS, "ABCD"):
        if hospital_min <= max_min:
            return letter
    return "D"   # 保険。is_gap=Falseなら60分以内のはず(compute_access.pyの定義より)


def _weighted_avg(values: pd.Series, weights: pd.Series):
    """人口重み付き平均(小数1桁)。値が全部欠測なら None"""
    mask = values.notna() & (weights > 0)
    if not mask.any():
        return None
    return round(float((values[mask] * weights[mask]).sum() / weights[mask].sum()), 1)


def _avg_block(g: pd.DataFrame) -> dict:
    return {
        "hospital_min": _weighted_avg(g["hospital_min_num"], g["population"]),
        "super_min": _weighted_avg(g["super_min_num"], g["population"]),
        "walk_to_stop_min": _weighted_avg(g["walk_to_stop_min_num"], g["population"]),
    }


def build_karte(access: pd.DataFrame, mesh_table: pd.DataFrame) -> dict:
    """access_mesh.csv(1メッシュ1行)と メッシュ→地区(サブ地区)対応表 から
    karte.json の中身を組み立てる(純粋関数。ファイルI/Oはmain()側)。

    mesh_table: meshcode, use_id の2列を持つ表(make_mesh_index.load_mesh_table()の戻り値。
    サブ地区分割があれば use_id はサブ地区ID、無ければ地区IDになる)。
    同じ meshcode が複数行あると出力が水増しされるため、最初の行だけを使う"""
    df = access.copy()
    df["meshcode"] = df["meshcode"].astype(str)
    mt = mesh_table[["meshcode", "use_id"]].copy()
    mt["meshcode"] = mt["meshcode"].astype(str)
    mt = mt.drop_duplicates("meshcode", keep="first")

    df = df.merge(mt, on="meshcode", how="left")   # how=left: 対応が無くても行数は減らさない
    assert len(df) == len(access), "メッシュ対応表との結合で行数が変わった(重複の疑い)"

    df["is_gap_bool"] = _to_bool(df["is_gap"])
    df["hospital_min_num"] = _to_minutes(df["time_to_hospital_min"])
    df["super_min_num"] = _to_minutes(df["time_to_super_min"])
    df["walk_to_stop_min_num"] = pd.to_numeric(df["walk_to_stop_min"], errors="coerce")
    df["visit_ok_bool"] = df["hospital_visit_ok"].astype(str).str.strip().str.lower() == "yes"
    df["visit_total_min_num"] = pd.to_numeric(df["visit_total_min"], errors="coerce")

    meshes = []
    for r in df.itertuples():
        lat, lon = meshcode_to_center(r.meshcode)
        meshes.append({
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "district_id": _none_if_nan(r.use_id),
            "nearest_stop_name": _none_if_nan(r.nearest_stop_name),
            "walk_to_stop_min": _none_if_nan(r.walk_to_stop_min_num),
            "hospital_min": _none_if_nan(r.hospital_min_num),
            "hospital_name": _none_if_nan(r.hospital_name),
            "super_min": _none_if_nan(r.super_min_num),
            "super_name": _none_if_nan(r.super_name),
            "visit_ok": bool(r.visit_ok_bool),
            "visit_total_min": _none_if_nan(r.visit_total_min_num),
            "grade": grade_of(bool(r.is_gap_bool), _none_if_nan(r.hospital_min_num)),
        })

    city_avg = _avg_block(df)
    district_avg = {str(did): _avg_block(g) for did, g in df.groupby("use_id") if pd.notna(did)}

    meta = {
        "city_avg": city_avg,
        "district_avg": district_avg,
        "thresholds": {
            "gap_threshold_min": config.GAP_THRESHOLD_MIN,
            "walkable_facility_min": config.WALKABLE_FACILITY_MIN,
            "grade_bins": config.KARTE_GRADE_BINS,
        },
        "grade_labels": config.KARTE_GRADE_LABELS,
    }
    return {"meta": meta, "meshes": meshes}


def main():
    if not config.ACCESS_MESH_CSV.exists():
        raise SystemExit(
            f"入力が見つかりません: {config.ACCESS_MESH_CSV}\n"
            "  これは分析成果物です。SSD等から output/ をこの端末にコピーするか、\n"
            "  分析環境で compute_access.py を実行してください。")

    access = pd.read_csv(config.ACCESS_MESH_CSV)
    mesh_table = load_mesh_table()   # 無ければここでFileNotFoundError(案内メッセージ付き)

    karte = build_karte(access, mesh_table)

    KARTE_JSON.parent.mkdir(parents=True, exist_ok=True)
    KARTE_JSON.write_text(
        json.dumps(karte, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    size_kb = KARTE_JSON.stat().st_size / 1024
    print(f"→ {KARTE_JSON}  メッシュ数: {len(karte['meshes'])}件  ({size_kb:.1f} KB)")

    # ---- 採点表: A〜Eの分布(§13.4「開発者が見て確定」の材料) ----
    grades = pd.Series([m["grade"] for m in karte["meshes"]])
    pop_series = access["population"].reset_index(drop=True)
    print("\n=== 総合評価の分布 ===")
    print("評価 | メッシュ数 | 人口合計 | めやす")
    for g in ["A", "B", "C", "D", "E"]:
        mask = grades == g
        print(f"  {g}  | {int(mask.sum()):>6}件 | {int(pop_series[mask].sum()):>7,}人 | "
              f"{config.KARTE_GRADE_LABELS[g]}")

    # ---- 検算(§13.4 M8-1完成条件) ----
    print("\n=== 検算 ===")
    ok_a = len(karte["meshes"]) == len(access)
    print(f"検算(a) 件数 = access_mesh.csv行数: {len(karte['meshes'])} = {len(access)}",
          "OK" if ok_a else "★NG")

    city_hosp = karte["meta"]["city_avg"]["hospital_min"]
    recompute = _weighted_avg(
        pd.Series([m["hospital_min"] for m in karte["meshes"]]),
        pop_series)
    ok_b = city_hosp == recompute
    print(f"検算(b) 市平均(病院所要)をkarte.jsonから再計算: {recompute} = {city_hosp}",
          "OK" if ok_b else "★NG")

    e_from_grade = sum(1 for m in karte["meshes"] if m["grade"] == "E")
    e_from_is_gap = int(_to_bool(access["is_gap"]).sum())
    ok_c = e_from_grade == e_from_is_gap
    print(f"検算(c) E評価の件数 = is_gap=Trueの件数: {e_from_grade} = {e_from_is_gap}",
          "OK" if ok_c else "★NG")

    if not (ok_a and ok_b and ok_c):
        raise SystemExit("★検算に失敗しました。上のNG項目を確認してください。")


if __name__ == "__main__":
    main()
