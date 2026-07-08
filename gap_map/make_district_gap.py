# -*- coding: utf-8 -*-
"""G4(第2層): 地区ごとの「交通空白のようす」を集計して webapp/data/district_gap.json を作る。

この作品の強み=「地区を選ぶと、その地区の交通空白がひと目で分かる」をデータ面で支える。
凍結済みの分析結果(output/access_mesh.csv)を、メッシュ→地区の対応(data/mesh_districts.csv)で
地区ごとにまとめ直すだけ。**新しい空白判定はしない**(is_gap・hospital_visit_ok 等は
compute_access.py の結果をそのまま集計。数値の一貫性を守る。docs/plan_gap_map.md の定義に従う)。

入力(いずれも分析成果物。この端末に無い場合はSSD等からコピーする):
  - data/mesh_districts.csv   … meshcode, district_id, ...(make_districts.py の出力)
  - output/access_mesh.csv    … meshcode, population, time_to_hospital_min,
                                 hospital_visit_ok, is_gap ...(compute_access.py の出力)
  - data/target_meshes.csv    … meshcode, population, population_65plus ...(prepare_meshes.py)

出力:
  - webapp/data/district_gap.json  … {district_id: {指標...}}

実行(プロジェクトルートで):
  python gap_map/make_district_gap.py

注意: かんたんモードには空白を出さない(開発者方針 2026-07-08)。この出力を使うのは
  しっかりモード・空白マップ側のみ。
"""
import json
from pathlib import Path

import pandas as pd

import config

UNREACHABLE = "到達不能"   # compute_access.py と同じ(time_to_hospital_min の到達不能を表す文字列)

DISTRICT_GAP_JSON = Path(__file__).parent.parent / "webapp" / "data" / "district_gap.json"
MESH_DISTRICTS_CSV = config.DATA_DIR / "mesh_districts.csv"


def _to_bool(series: pd.Series) -> pd.Series:
    """CSVに文字列で入った真偽値("True"/"False"等)を bool に直す"""
    return series.astype(str).str.strip().str.lower().isin(["true", "1", "yes"])


def aggregate_district_gap(mesh_districts: pd.DataFrame, access: pd.DataFrame,
                           meshes: pd.DataFrame, gap_threshold_min: int = None) -> dict:
    """メッシュ単位の3表を meshcode で結合し、地区(district_id)ごとに集計して
    {district_id: 指標dict} を返す(純粋関数。テストしやすいようファイル入出力から分離)。

    指標:
      population         … 地区の総人口
      aging_rate         … 高齢化率(65歳以上/人口。年齢が秘匿のメッシュは分母から除外)
      mesh_count         … 地区の(人口>0)メッシュ数
      gap_mesh_count     … 交通空白メッシュ数(is_gap=True)
      gap_population     … 空白メッシュの人口合計
      gap_ratio          … gap_population / population
      unreachable_mesh_count … 60分以内にどの病院にも着けないメッシュ数
      hidden_gap_mesh_count / hidden_gap_population
                         … 隠れ空白(①60分以内で行けるが②通院が成立しない)
      has_gap / has_hidden_gap … 表示の出し分け用の真偽値
    """
    if gap_threshold_min is None:
        gap_threshold_min = config.GAP_THRESHOLD_MIN

    # meshcode を共通キーに(型ゆれ防止で文字列化して結合)
    md = mesh_districts.copy()
    ac = access.copy()
    me = meshes.copy()
    for df in (md, ac, me):
        df["meshcode"] = df["meshcode"].astype(str)

    # access に地区IDと年齢内訳をくっつける
    df = ac.merge(md[["meshcode", "district_id"]], on="meshcode", how="inner")
    df = df.merge(me[["meshcode", "population_65plus"]], on="meshcode", how="left")

    # 判定列を素直な型に整える
    df["is_gap"] = _to_bool(df["is_gap"])
    df["visit_no"] = df["hospital_visit_ok"].astype(str).str.strip().str.lower() == "no"
    time_num = pd.to_numeric(df["time_to_hospital_min"], errors="coerce")  # 到達不能→NaN
    df["unreachable"] = df["time_to_hospital_min"].astype(str) == UNREACHABLE
    df["population"] = pd.to_numeric(df["population"], errors="coerce").fillna(0)
    df["population_65plus"] = pd.to_numeric(df["population_65plus"], errors="coerce")
    # 隠れ空白 = 60分以内(到達可能)なのに通院不可・人口>0
    df["hidden_gap"] = (~df["unreachable"]) & (time_num <= gap_threshold_min) \
        & df["visit_no"] & (df["population"] > 0)

    result = {}
    for did, g in df.groupby("district_id"):
        pop = float(g["population"].sum())
        # 高齢化率: 年齢が秘匿(NaN)のメッシュは分母からも除く(analyze_demographics.py と同じ方針)
        aged = g.dropna(subset=["population_65plus"])
        denom = float(aged["population"].sum())
        aging = round(float(aged["population_65plus"].sum()) / denom, 3) if denom > 0 else None
        gap_pop = float(g.loc[g["is_gap"], "population"].sum())
        result[str(did)] = {
            "population": int(round(pop)),
            "aging_rate": aging,
            "mesh_count": int((g["population"] > 0).sum()),
            "gap_mesh_count": int(g["is_gap"].sum()),
            "gap_population": int(round(gap_pop)),
            "gap_ratio": round(gap_pop / pop, 3) if pop > 0 else 0,
            "unreachable_mesh_count": int(g["unreachable"].sum()),
            "hidden_gap_mesh_count": int(g["hidden_gap"].sum()),
            "hidden_gap_population": int(round(float(g.loc[g["hidden_gap"], "population"].sum()))),
            "has_gap": bool(g["is_gap"].any()),
            "has_hidden_gap": bool(g["hidden_gap"].any()),
        }
    return result


def main():
    for path in (MESH_DISTRICTS_CSV, config.ACCESS_MESH_CSV, config.TARGET_MESHES_CSV):
        if not Path(path).exists():
            raise SystemExit(
                f"入力が見つかりません: {path}\n"
                "  これは分析成果物です。SSD等から data/・output/ をこの端末にコピーするか、\n"
                "  分析環境で make_districts.py / compute_access.py / prepare_meshes.py を実行してください。")

    mesh_districts = pd.read_csv(MESH_DISTRICTS_CSV)
    access = pd.read_csv(config.ACCESS_MESH_CSV)
    meshes = pd.read_csv(config.TARGET_MESHES_CSV)

    gap = aggregate_district_gap(mesh_districts, access, meshes)

    DISTRICT_GAP_JSON.parent.mkdir(parents=True, exist_ok=True)
    DISTRICT_GAP_JSON.write_text(
        json.dumps(gap, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # 検算: 全地区の空白人口合計が確定値(15,418人)と一致するか(docs/handover.md §4.1)
    total_gap = sum(v["gap_population"] for v in gap.values())
    total_hidden = sum(v["hidden_gap_population"] for v in gap.values())
    print(f"→ {DISTRICT_GAP_JSON}  地区数: {len(gap)}")
    print(f"  検算: 空白人口合計 = {total_gap:,}人(確定値 15,418人)")
    print(f"  検算: 隠れ空白人口合計 = {total_hidden:,}人(確定値 571人)")
    print("  ※対象2市の全地区を集計対象にした場合の一致を確認すること"
          "(隣接市のメッシュは district_id が無いので自然に除外される)")


if __name__ == "__main__":
    main()
