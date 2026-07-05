# -*- coding: utf-8 -*-
"""
交通空白メッシュ(is_gap)の人口・高齢者人口を市町村別に集計し、
空白メッシュ群の高齢化率と対象2市全体の高齢化率を比較する。

年齢階級別人口はe-Statメッシュ人口の65歳以上・75歳以上列
(prepare_meshes.py で target_meshes.csv に追加済み)を使う。
一部の低人口メッシュは秘匿により年齢内訳が欠損しているため、
高齢化率の計算では「年齢内訳がある行の合計」を分母にする(集計対象を明記して表示する)。

実行方法: プロジェクトのルートで `python3 gap_map/analyze_demographics.py`
"""

import pandas as pd

import config


def main():
    meshes = pd.read_csv(config.TARGET_MESHES_CSV)
    access = pd.read_csv(config.ACCESS_MESH_CSV)
    df = meshes.merge(access[["meshcode", "is_gap"]], on="meshcode")

    n_missing_age = df["population_65plus"].isna().sum()
    missing_pop = df.loc[df["population_65plus"].isna(), "population"].sum()
    print(f"※ {n_missing_age}件のメッシュは秘匿等により年齢内訳が欠損(人口計{missing_pop}人、"
          f"全体の{missing_pop / df['population'].sum() * 100:.1f}%)。"
          "以下の集計では年齢内訳がある行のみを合計する。\n")

    # ---------------------------------------------------------------
    # 1. 空白メッシュの人口・高齢者人口を市別に集計
    # ---------------------------------------------------------------
    gap = df[df["is_gap"]]
    summary = gap.groupby("municipality")[
        ["population", "population_65plus", "population_75plus"]
    ].sum()
    summary.loc["合計"] = summary.sum()
    summary = summary.rename(columns={
        "population": "総人口", "population_65plus": "65歳以上", "population_75plus": "75歳以上",
    })
    print("=== 1. 空白メッシュの人口・高齢者人口(市別) ===")
    print(summary.to_string())

    # ---------------------------------------------------------------
    # 2. 空白メッシュ群の高齢化率 vs 対象2市全体の高齢化率
    # ---------------------------------------------------------------
    def aging_rate(sub: pd.DataFrame) -> tuple:
        valid = sub[sub["population_65plus"].notna()]
        total = valid["population"].sum()
        elderly = valid["population_65plus"].sum()
        elderly75 = valid["population_75plus"].sum()
        return total, elderly, elderly75, elderly / total * 100, elderly75 / total * 100

    gap_total, gap_65, gap_75, gap_rate65, gap_rate75 = aging_rate(gap)
    all_total, all_65, all_75, all_rate65, all_rate75 = aging_rate(df)

    print("\n=== 2. 高齢化率の比較(空白メッシュ vs 対象2市全体) ===")
    print(f"{'':12}{'総人口':>10}{'65歳以上':>10}{'高齢化率(65+)':>14}{'75歳以上':>10}{'高齢化率(75+)':>14}")
    print(f"{'空白メッシュ':12}{gap_total:>10,.0f}{gap_65:>10,.0f}{gap_rate65:>13.1f}%{gap_75:>10,.0f}{gap_rate75:>13.1f}%")
    print(f"{'対象2市全体':12}{all_total:>10,.0f}{all_65:>10,.0f}{all_rate65:>13.1f}%{all_75:>10,.0f}{all_rate75:>13.1f}%")
    print(f"\n差(空白メッシュ − 対象2市全体): "
          f"65歳以上 {gap_rate65 - all_rate65:+.1f}ポイント、"
          f"75歳以上 {gap_rate75 - all_rate75:+.1f}ポイント")


if __name__ == "__main__":
    main()
