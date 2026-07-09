# -*- coding: utf-8 -*-
"""
交通空白マップのFolium地図(output/gap_map.html)を作る。詳しい構成は
docs/plan_gap_map.md §8 が土台だが、本ファイルは以下の点をユーザー指示で調整している:

  - レイヤ1「病院への所要時間」: 5段階(〜15/〜30/〜45/〜60/60分超)の色分け。
    到達不能メッシュはこの層では「危険を意味する色(赤系)」にはせず、
    中立のグレーで表示する(=このレイヤでは「対象外」の扱い)
  - レイヤ2「最寄りバス停までの距離帯」(新設・既定OFF): 到達不能の理由(§7の
    MAX_WALK_TO_STOP_M=800mモデル)を、300m/500m/800m/圏外の4段階で可視化する。
    到達不能メッシュはちょうど「圏外」に該当する
  - レイヤ3「通院可能性」: 11時病院着・90分滞在・17時帰宅(指標②)が
    できるか(Yes/No)を状態色(good/critical)で表示。「①は60分以内なのに
    ②はNo」という隠れ空白は、塗りは同じcriticalのまま、警告色(warning)の
    太枠で強調する
  - レイヤ4: 施設マーカー(病院・スーパー、表示切替可)

色は dataviz スキルの手順に従い、1色調・明度単調のオーダー(ordinal)ランプとして
事前に検証済み(node scripts/validate_palette.js ... --ordinal で全項目PASS)。

実行方法: プロジェクトのルートで `python3 gap_map/make_map.py`
出力先: output/gap_map.html
"""

import math
import pickle

import folium
import numpy as np
import pandas as pd

import config
import meshcode

UNREACHABLE = "到達不能"

# ---------------------------------------------------------------
# 色(dataviz skillで検証済みのordinalランプ。1色調・明度単調)
# ---------------------------------------------------------------
# レイヤ1: 病院への所要時間(青、5段階。淡い=近い/濃い=遠い)
HOSPITAL_COLORS = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#0d366b"]
HOSPITAL_LABELS = ["〜15分", "〜30分", "〜45分", "〜60分", "60分超"]
HOSPITAL_UNREACHABLE_COLOR = "#c3c2b7"   # 中立グレー(§palette.md「Border」ink)

# レイヤ2: 最寄りバス停までの距離帯(緑、4段階。淡い=近い/濃い=遠い)
STOP_DIST_COLORS = ["#1fcb8e", "#179669", "#0f6144", "#072c1f"]
STOP_DIST_LABELS = ["〜300m", "〜500m", "〜800m", "圏外(800m超)"]

# レイヤ3: 通院可能性(Yes/No)。2値の状態を表すのでstatus色(good/critical)を使う。
# 「隠れ空白」(①は60分以内なのにNo)は塗りはcriticalのまま、warning色の太枠で強調する
VISIT_OK_COLOR = "#0ca30c"          # good
VISIT_NG_COLOR = "#d03b3b"          # critical
HIDDEN_GAP_BORDER_COLOR = "#fab219"  # warning(隠れ空白の強調枠)

# 施設マーカー(カテゴリ=identityなので、カテゴリカル色の系統でdarkpurple/orangeを使う。
# folium.Iconのcolorは固定の名前付きパレットのみ対応のため、任意の16進数は指定できない)


def hospital_bucket(time_min) -> int:
    """病院所要時間(分)を0〜4の5段階に分類する(-1は到達不能)"""
    if time_min is None or time_min == UNREACHABLE:
        return -1
    t = float(time_min)
    if t <= 15:
        return 0
    if t <= 30:
        return 1
    if t <= 45:
        return 2
    if t <= 60:
        return 3
    return 4


def stop_dist_band(dist_m: float) -> int:
    """最寄り停留所までの距離(m)を0〜3の4段階に分類する"""
    if dist_m <= 300:
        return 0
    if dist_m <= 500:
        return 1
    if dist_m <= 800:
        return 2
    return 3


def haversine_m_vec(lat1, lon1, lats2, lons2) -> np.ndarray:
    """compute_access.haversine_m_vec と同じ式(1点→複数点の距離をまとめて計算)"""
    r = 6371000
    p1 = math.radians(lat1)
    p2 = np.radians(lats2)
    dp = np.radians(lats2 - lat1)
    dl = np.radians(lons2 - lon1)
    a = np.sin(dp / 2) ** 2 + math.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def mesh_polygon_coords(mesh_code) -> list:
    """meshcodeから、GeoJSON Polygon用の座標リング([経度,緯度]の順、始点=終点)を作る"""
    south, west, north, east = meshcode.meshcode_to_bounds(mesh_code)
    return [[west, south], [east, south], [east, north], [west, north], [west, south]]


def build_mesh_features(df: pd.DataFrame) -> list:
    """メッシュ1件ごとに、両レイヤ共通で使うGeoJSON Featureを作る
    (properties に population・最寄り停留所・病院情報・バケット番号を持たせる)"""
    features = []
    for row in df.itertuples():
        h_bucket = hospital_bucket(row.time_to_hospital_min)
        d_band = stop_dist_band(row.nearest_stop_dist_m)
        time_display = (UNREACHABLE if row.time_to_hospital_min == UNREACHABLE
                         else f"{row.time_to_hospital_min}分")
        # 隠れ空白: 指標①は60分以内(=到達可能)なのに、指標②(通院可能性)はNo
        hosp_min_numeric = (None if row.time_to_hospital_min == UNREACHABLE
                             else float(row.time_to_hospital_min))
        hidden_gap = (row.hospital_visit_ok == "No"
                      and hosp_min_numeric is not None
                      and hosp_min_numeric <= config.GAP_THRESHOLD_MIN)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [mesh_polygon_coords(row.meshcode)]},
            "properties": {
                "meshcode": str(row.meshcode),
                "population": int(row.population),
                "population_65plus": (int(row.population_65plus)
                                       if pd.notna(row.population_65plus) else "(秘匿)"),
                "municipality": row.municipality,
                "nearest_stop_name": row.nearest_stop_name if pd.notna(row.nearest_stop_name) else "(圏外)",
                "nearest_stop_dist_m": round(row.nearest_stop_dist_m),
                "hospital_name": row.hospital_name if pd.notna(row.hospital_name) else "(到達不能)",
                "time_to_hospital_display": time_display,
                "hospital_bucket": h_bucket,
                "distance_band": d_band,
                "hospital_visit_ok": row.hospital_visit_ok,
                "visit_total_min": (int(row.visit_total_min)
                                     if pd.notna(row.visit_total_min) else "-"),
                "hidden_gap": hidden_gap,
            },
        })
    return features


def add_mesh_layer(fmap, features, color_by, colors, labels, unreachable_color,
                    layer_name, show):
    """メッシュのGeoJSONレイヤを1つ地図に追加する。
    color_by: "hospital_bucket" または "distance_band"(propertiesのキー名)"""
    fg = folium.FeatureGroup(name=layer_name, show=show)

    def style_function(feature):
        bucket = feature["properties"][color_by]
        if bucket == -1:
            fill = unreachable_color
        else:
            fill = colors[bucket]
        return {"fillColor": fill, "color": "#52514e", "weight": 0.3,
                "fillOpacity": 0.75}

    tooltip_fields = ["municipality", "population", "population_65plus", "nearest_stop_name",
                       "nearest_stop_dist_m", "hospital_name", "time_to_hospital_display"]
    tooltip_aliases = ["市町村", "人口(人)", "65歳以上(人)", "最寄り停留所", "停留所まで(m)",
                        "最寄り病院", "病院まで"]

    folium.GeoJson(
        {"type": "FeatureCollection", "features": features},
        style_function=style_function,
        tooltip=folium.GeoJsonTooltip(fields=tooltip_fields, aliases=tooltip_aliases,
                                       sticky=True),
    ).add_to(fg)
    fg.add_to(fmap)


def add_hospital_visit_layer(fmap, features, layer_name, show):
    """レイヤ3「通院可能性」を追加する。塗りはYes=good/No=criticalの状態色。
    「隠れ空白」(①は60分以内なのに②はNo)は同じcritical塗りのまま、
    warning色の太枠で囲んで区別する"""
    fg = folium.FeatureGroup(name=layer_name, show=show)

    def style_function(feature):
        props = feature["properties"]
        fill = VISIT_OK_COLOR if props["hospital_visit_ok"] == "Yes" else VISIT_NG_COLOR
        if props["hidden_gap"]:
            return {"fillColor": fill, "color": HIDDEN_GAP_BORDER_COLOR, "weight": 2.5,
                    "fillOpacity": 0.8}
        return {"fillColor": fill, "color": "#52514e", "weight": 0.3, "fillOpacity": 0.7}

    tooltip_fields = ["municipality", "population", "population_65plus",
                       "hospital_visit_ok", "visit_total_min", "time_to_hospital_display",
                       "hidden_gap"]
    tooltip_aliases = ["市町村", "人口(人)", "65歳以上(人)", "通院可能性(11-17時)",
                        "拘束時間(分)", "①病院まで", "隠れ空白(①OK・②NG)"]

    folium.GeoJson(
        {"type": "FeatureCollection", "features": features},
        style_function=style_function,
        tooltip=folium.GeoJsonTooltip(fields=tooltip_fields, aliases=tooltip_aliases,
                                       sticky=True),
    ).add_to(fg)
    fg.add_to(fmap)


def add_legend(fmap, title: str, colors: list, labels: list, unreachable_label: str = None,
                unreachable_color: str = None, position_top: int = 10):
    """左上に簡易凡例(色の四角+ラベル)を追加する(Foliumに凡例機能が無いため自作)"""
    rows = "".join(
        f'<div style="display:flex;align-items:center;margin:2px 0;">'
        f'<span style="display:inline-block;width:14px;height:14px;background:{c};'
        f'margin-right:6px;border:1px solid #52514e;"></span>{l}</div>'
        for c, l in zip(colors, labels)
    )
    if unreachable_label:
        rows += (
            f'<div style="display:flex;align-items:center;margin:2px 0;">'
            f'<span style="display:inline-block;width:14px;height:14px;background:{unreachable_color};'
            f'margin-right:6px;border:1px solid #52514e;"></span>{unreachable_label}</div>'
        )
    html = f"""
    <div style="position:fixed; top:{position_top}px; right:10px; z-index:9999;
                background:#fcfcfb; padding:8px 10px; border:1px solid #c3c2b7;
                border-radius:4px; font-size:12px; color:#0b0b0b; font-family:sans-serif;">
      <b>{title}</b>
      {rows}
    </div>
    """
    fmap.get_root().html.add_child(folium.Element(html))


def main():
    print("データを読み込み中...")
    meshes = pd.read_csv(config.TARGET_MESHES_CSV)
    access = pd.read_csv(config.ACCESS_MESH_CSV)
    facilities = pd.read_csv(config.FACILITIES_CSV)
    with open(config.NETWORK_PKL, "rb") as f:
        network = pickle.load(f)

    df = meshes.merge(access, on=["meshcode", "population", "municipality"])

    # レイヤ2用に、全メッシュの「最寄り停留所までの実際の距離」を(800mの上限なしで)計算する
    stop_ids = list(network.stops.keys())
    stop_lats = np.array([network.stops[s]["lat"] for s in stop_ids])
    stop_lons = np.array([network.stops[s]["lon"] for s in stop_ids])
    nearest_dist = []
    for row in df.itertuples():
        d = haversine_m_vec(row.lat, row.lon, stop_lats, stop_lons)
        nearest_dist.append(d.min())
    df["nearest_stop_dist_m"] = nearest_dist

    print("メッシュのポリゴンを作成中...")
    features = build_mesh_features(df)

    center_lat, center_lon = df["lat"].mean(), df["lon"].mean()
    # 背景地図は1種類だけなので control=False にして、レイヤー切替パネルに
    # 「openstreetmap」という謎のラジオボタンが出ないようにする
    fmap = folium.Map(location=[center_lat, center_lon], zoom_start=12, tiles=None)
    folium.TileLayer("OpenStreetMap", control=False).add_to(fmap)

    print("レイヤ1(病院への所要時間)を追加中...")
    add_mesh_layer(fmap, features, "hospital_bucket", HOSPITAL_COLORS, HOSPITAL_LABELS,
                    HOSPITAL_UNREACHABLE_COLOR, "① 病院への所要時間", show=True)

    print("レイヤ2(最寄りバス停までの距離帯)を追加中...")
    add_mesh_layer(fmap, features, "distance_band", STOP_DIST_COLORS, STOP_DIST_LABELS,
                    STOP_DIST_COLORS[-1], "② 最寄りバス停までの距離帯", show=False)

    print("レイヤ3(通院可能性)を追加中...")
    add_hospital_visit_layer(fmap, features, "③ 通院可能性(11-17時)", show=False)

    print("施設マーカーを追加中...")
    hospital_fg = folium.FeatureGroup(name="④ 病院マーカー", show=True)
    for row in facilities[facilities["category"] == "hospital"].itertuples():
        folium.Marker(
            location=[row.lat, row.lon], tooltip=row.name,
            icon=folium.Icon(color="darkpurple", icon="plus", prefix="fa"),
        ).add_to(hospital_fg)
    hospital_fg.add_to(fmap)

    super_fg = folium.FeatureGroup(name="⑤ スーパーマーカー", show=False)
    for row in facilities[facilities["category"] == "supermarket"].itertuples():
        folium.Marker(
            location=[row.lat, row.lon], tooltip=row.name,
            icon=folium.Icon(color="orange", icon="shopping-cart", prefix="fa"),
        ).add_to(super_fg)
    super_fg.add_to(fmap)

    # レイヤー切替は左上に置く(右上は凡例①〜③で使っており、重なって見えなくなる)
    folium.LayerControl(collapsed=False, position="topleft").add_to(fmap)
    layer_control_css = """
    <style>
      /* レイヤー切替パネル: 見出しをつけて「何のチェックか」わかるようにする */
      .leaflet-control-layers-expanded {
          font-family: sans-serif;
          font-size: 13px;
          max-width: 240px;
      }
      .leaflet-control-layers-expanded::before {
          content: "表示する情報(チェックで切替)";
          display: block;
          font-weight: bold;
          margin-bottom: 4px;
          white-space: nowrap;
      }
      .leaflet-control-layers label { margin: 2px 0; }
    </style>
    """
    fmap.get_root().header.add_child(folium.Element(layer_control_css))
    add_legend(fmap, "① 病院への所要時間", HOSPITAL_COLORS, HOSPITAL_LABELS,
               unreachable_label=UNREACHABLE, unreachable_color=HOSPITAL_UNREACHABLE_COLOR,
               position_top=10)
    add_legend(fmap, "② 停留所までの距離帯", STOP_DIST_COLORS, STOP_DIST_LABELS,
               position_top=170)
    add_legend(fmap, "③ 通院可能性(11-17時)",
               [VISIT_OK_COLOR, VISIT_NG_COLOR], ["Yes(可能)", "No(不可)"],
               position_top=330)
    hidden_gap_note = f"""
    <div style="position:fixed; top:410px; right:10px; z-index:9999;
                background:#fcfcfb; padding:8px 10px; border:1px solid #c3c2b7;
                border-radius:4px; font-size:12px; color:#0b0b0b; font-family:sans-serif;
                max-width:220px;">
      <div style="display:flex;align-items:center;margin:2px 0;">
        <span style="display:inline-block;width:14px;height:14px;background:{VISIT_NG_COLOR};
        border:2.5px solid {HIDDEN_GAP_BORDER_COLOR};margin-right:6px;"></span>
        隠れ空白(①は60分以内なのに②はNo)
      </div>
    </div>
    """
    fmap.get_root().html.add_child(folium.Element(hidden_gap_note))

    config.OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = config.OUTPUT_DIR / "gap_map.html"
    fmap.save(out_path)
    print(f"\n→ {out_path} に保存しました")

    # ---------------------------------------------------------------
    # 集計値
    # ---------------------------------------------------------------
    total_pop = meshes["population"].sum()
    gap_pop = df.loc[df["is_gap"], "population"].sum()

    def is_slow_or_unreachable(v):
        return v == UNREACHABLE or float(v) > config.GAP_THRESHOLD_MIN

    slow_mask = df["time_to_hospital_min"].apply(is_slow_or_unreachable)
    slow_pop = df.loc[slow_mask, "population"].sum()

    print("\n=== 集計値 ===")
    print(f"対象2市(山形市・上山市)総人口: {total_pop:,}人")
    print(f"空白メッシュ(is_gap)人口: {gap_pop:,}人 "
          f"({gap_pop / total_pop * 100:.1f}%)")
    print(f"60分超+到達不能メッシュ人口: {slow_pop:,}人 "
          f"({slow_pop / total_pop * 100:.1f}%)")


if __name__ == "__main__":
    main()
