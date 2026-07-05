# -*- coding: utf-8 -*-
"""
M1の成果物(data/target_meshes.csv)が正しく抽出できているかを目で見て確認するための、
簡易な確認用地図。M5で作る本番の gap_map.html(メッシュを四角形で塗り分け、
複数レイヤ切替つき)とは別物で、こちらは「メッシュ中心点に人口で色をつけて
プロットするだけ」の軽い確認スクリプト。

実行方法: プロジェクトのルートで `python3 gap_map/check_meshes_map.py`
出力先: output/check_meshes_map.html
"""

import branca.colormap as cm
import folium
import pandas as pd

import config

OUTPUT_HTML = config.OUTPUT_DIR / "check_meshes_map.html"


def main():
    df = pd.read_csv(config.TARGET_MESHES_CSV)
    print(f"{config.TARGET_MESHES_CSV} を読み込みました: {len(df)}件")

    # 人口の値に応じて色を変えるためのグラデーション(薄黄色→濃い赤)
    colormap = cm.LinearColormap(
        colors=["#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"],
        vmin=df["population"].min(),
        vmax=df["population"].max(),
        caption="メッシュ人口(人)",
    )

    # 地図の中心は対象メッシュの重心あたりに置く
    center_lat = df["lat"].mean()
    center_lon = df["lon"].mean()
    fmap = folium.Map(location=[center_lat, center_lon], zoom_start=12,
                       tiles="OpenStreetMap")

    for _, row in df.iterrows():
        folium.CircleMarker(
            location=[row["lat"], row["lon"]],
            radius=4,
            color=None,
            fill=True,
            fill_color=colormap(row["population"]),
            fill_opacity=0.85,
            tooltip=(f"meshcode: {row['meshcode']}<br>"
                     f"市町村: {row['municipality']}<br>"
                     f"人口: {row['population']:.0f}人"),
        ).add_to(fmap)

    colormap.add_to(fmap)
    config.OUTPUT_DIR.mkdir(exist_ok=True)
    fmap.save(OUTPUT_HTML)
    print(f"→ {OUTPUT_HTML} に保存しました({len(df)}メッシュ)")


if __name__ == "__main__":
    main()
