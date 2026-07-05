# -*- coding: utf-8 -*-
"""
追加タスク: OpenStreetMapのOverpass APIで、山形市内の
スーパー(shop=supermarket)とコンビニ(shop=convenience)を取得してCSVに保存。
さらに「ヤマザワ」の件数と、成沢地区(成沢西・蔵王成沢)周辺の店舗を確認する。

Overpass APIとは:
  OpenStreetMap(みんなで作る地図)のデータを、専用の問い合わせ言語
  (Overpass QL)で検索できる無料のAPI。

実行方法:  python3 step5_overpass_shops.py
"""

import math

import requests
import pandas as pd

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Overpass APIは「誰が使っているか」を示すUser-Agentがないと拒否(406エラー)する
# ことがあるので、名乗りを入れておく(マナーとしても推奨されている)
HEADERS = {"User-Agent": "yamagata-gtfs-study/1.0 (learning script)"}

# ---------------------------------------------------------------
# 1: Overpass QLのクエリを組み立てる
# ---------------------------------------------------------------
# ・area["name"="山形市"] … 「山形市」という行政区域を検索範囲にする
# ・node/way             … 店は「点(node)」か「建物の輪郭(way)」で登録されている
# ・shop~"^(supermarket|convenience)$" … shopタグが2種類のどちらかに一致
# ・out center           … wayの場合は建物の中心座標を返してもらう
query = """
[out:json][timeout:90];
area["name"="山形市"]["boundary"="administrative"]->.yamagata;
(
  node["shop"~"^(supermarket|convenience)$"](area.yamagata);
  way["shop"~"^(supermarket|convenience)$"](area.yamagata);
);
out center tags;
"""

# Overpassは data= にクエリ文字列を入れてPOSTで送る
res = requests.post(OVERPASS_URL, data={"data": query}, headers=HEADERS, timeout=120)
res.raise_for_status()
elements = res.json()["elements"]
print(f"取得した店舗数: {len(elements)} 件")

# ---------------------------------------------------------------
# 2: JSONを表(DataFrame)に整形する
# ---------------------------------------------------------------
rows = []
for el in elements:
    tags = el.get("tags", {})  # 店名などの属性は tags の中に入っている
    # nodeは lat/lon を直接持つが、wayは center の中に入っている
    lat = el.get("lat") or el.get("center", {}).get("lat")
    lon = el.get("lon") or el.get("center", {}).get("lon")
    rows.append({
        "店舗名": tags.get("name", "(名前なし)"),
        "種別": "スーパー" if tags["shop"] == "supermarket" else "コンビニ",
        "ブランド": tags.get("brand", ""),
        "緯度": lat,
        "経度": lon,
        "住所(番地)": tags.get("addr:full", tags.get("addr:quarter", "")),
    })

df = pd.DataFrame(rows)
print("\n=== 種別ごとの件数 ===")
print(df["種別"].value_counts())

df.to_csv("yamagata_shops.csv", index=False, encoding="utf-8-sig")
print("\n→ yamagata_shops.csv に保存しました")

# ---------------------------------------------------------------
# 3: 「ヤマザワ」を含む店舗を数える
# ---------------------------------------------------------------
# str.contains() は「文字列に○○を含むか」を行ごとに判定する。
# 店名かブランド名のどちらかに入っていればカウントする
is_yamazawa = df["店舗名"].str.contains("ヤマザワ") | df["ブランド"].str.contains("ヤマザワ")
yamazawa = df[is_yamazawa]
print(f"\n=== 「ヤマザワ」を含む店舗: {len(yamazawa)} 件 ===")
print(yamazawa[["店舗名", "種別", "緯度", "経度"]].to_string(index=False))

# ---------------------------------------------------------------
# 4: 成沢地区(成沢西・蔵王成沢)周辺の店舗を確認
# ---------------------------------------------------------------
# まずOSMから「成沢」を名前に含む地名(place)ノードを取って、地区の中心座標にする
place_query = """
[out:json][timeout:60];
area["name"="山形市"]["boundary"="administrative"]->.yamagata;
node["place"]["name"~"成沢"](area.yamagata);
out;
"""
pres = requests.post(OVERPASS_URL, data={"data": place_query}, headers=HEADERS, timeout=90)
pres.raise_for_status()
places = pres.json()["elements"]
print("\n=== 「成沢」を含む地名 ===")
for p in places:
    print(f"  {p['tags'].get('name')}  (緯度 {p['lat']:.4f}, 経度 {p['lon']:.4f})")


def haversine_km(lat1, lon1, lat2, lon2):
    """2地点の緯度経度から距離(km)を計算する定番の公式(ハバサイン公式)"""
    r = 6371  # 地球の半径 [km]
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# 各店舗について「いちばん近い成沢の地名ポイントまでの距離」を計算する
def min_dist_to_narusawa(row):
    return min(
        haversine_km(row["緯度"], row["経度"], p["lat"], p["lon"]) for p in places
    )

if places:
    df["成沢までの距離km"] = df.apply(min_dist_to_narusawa, axis=1)
    # 半径1.5km以内を「成沢地区周辺」とみなして抽出
    nearby = df[df["成沢までの距離km"] <= 1.5].sort_values("成沢までの距離km")
    print(f"\n=== 成沢地区周辺(1.5km以内)の店舗: {len(nearby)} 件 ===")
    if len(nearby) > 0:
        print(nearby[["店舗名", "種別", "成沢までの距離km"]].round(2).to_string(index=False))
    else:
        print("1.5km以内に店舗は見つかりませんでした")
else:
    print("「成沢」の地名ノードがOSMに見つからなかったため、距離判定はスキップします")
