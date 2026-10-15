# -*- coding: utf-8 -*-
"""かんたんモードの「🚏 いつも乗るバス停から」で使う、バス停さがしの小さな索引を作る。

背景(2026-10-15 開発者指摘「県立中央病院にいて地区を選ぶと、県立中央病院バス停の時刻表が無い」):
  地区の時刻表は地区の代表点1点から歩ける(800m以内の)停だけで作っている。そのため、人が住む区画の
  近くで便も多いのに、どの地区の時刻表にも乗り場として出てこない停がある(174停。かみのやま温泉駅前・
  遊学館前など)。そこで「いつも乗るバス停」を選んでもらい、その停を出発点にして時刻表を端末で作る
  (わが家と同じ計算。webapp/engine/)。このスクリプトは、その「選ぶ」ための一覧だけを作る。

入力(どれも読むだけ。時刻表本体・配布ネットワークは変えない):
  - webapp/data/network/{weekday,saturday,sunday_holiday}.json … 配布ネットワーク(停・便)
  - gtfs_*/translations.txt(language=ja-Hrkt)… 停の読みがな。山形市・上山市の市営バスにだけある
    (山交バスには無い。同じ名前の停なら市営バスの読みを使う)
  - webapp/data/mesh_index.json … 人が住む500m区画の中心(「2市とその周辺」の判定)
  - data/N03-…geojson … 市町村の境界(候補に添える市町村名)

出力: webapp/data/stop_search.json(コミット対象・約120KB。画面1でバス停をさがすときだけ読む)
  {"stops": [{"key": URLに使う名前, "name": 停名, "kana": [読み…], "city": 市町村,
              "hint": 同じ名前の停を見分ける一言(無ければ ""),
              "weekday"/"saturday"/"sunday_holiday": その停から乗れる便の数,
              "lat", "lon": 代表ののりば(平日の便がいちばん多いのりば)の座標}]}
  ★座標は丸めない(配布ネットワークと同じ値)。出発点がのりばと1cmでもずれると、
    「のりばまで あるいて0分」が変わり、工場との照合がずれうるため

決め方:
  - 同じ名前ののりばは、500m以内でつながるものを1つの停にまとめる(七日町=のりば4か所・約200m)。
    離れた場所の同じ名前(市役所前=山形市と上山市 など)は別の停にして、hint で見分ける
  - 対象: 山形市・上山市の中の停と、人が住む区画の中心から1km以内の停(わが家の登録と同じ線。
    HOME_AREA_M)。便が1本も無い停は出さない(選んでも時刻表が空になるため)
  - 便の数は「その停で乗れる便」(終点で降りるだけの便は数えない)。のりばが複数あっても1便は1回

使い方(プロジェクトルートから。GTFS のある環境で):
  python3 gap_map/make_stop_search.py
配布ネットワークを作り直したら(docs/runbook_regenerate.md ⑤)、これも作り直す。
"""
import csv
import json
import math
import re
from collections import defaultdict
from pathlib import Path

import config

PROJECT_ROOT = Path(__file__).parent.parent
NETWORK_DIR = PROJECT_ROOT / "webapp" / "data" / "network"
MESH_INDEX_JSON = PROJECT_ROOT / "webapp" / "data" / "mesh_index.json"
OUT_JSON = PROJECT_ROOT / "webapp" / "data" / "stop_search.json"

DAY_TYPES = ["weekday", "saturday", "sunday_holiday"]
CLUSTER_M = 500    # 同じ名前ののりばを1つの停にまとめる距離(のりばどうしがこれ以内でつながれば同じ停)
AREA_M = 1000      # 2市の外の停は、人が住む区画の中心からこの距離以内なら出す(kantan の HOME_AREA_M と同じ)

# 配布ネットワーク(export_network.py の serialize)の便1件の並び
T_OP, T_TRIPID = 3, 4


def haversine_m(lat1, lon1, lat2, lon2):
    """2点間の距離(m)。並べ替えと「同じ停か」の判定だけに使う"""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def kata_to_hira(s: str) -> str:
    """カタカナをひらがなにそろえる(画面の検索と同じ規則。ヴ等の特別な字はそのまま)"""
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def load_readings(feed_dirs) -> dict:
    """GTFS の translations.txt から 停ID(フィード名:stop_id)→読みがな を集める。
    record_id で書くフィードと field_value(停名)で書くフィードの両方に対応する。
    戻り値: {"by_id": {停ID: 読み}, "by_name": {停名: 読み}}"""
    by_id, by_name = {}, {}
    for d in feed_dirs:
        path = Path(d) / "translations.txt"
        if not path.exists():
            continue   # 山交バスなど、読みがなを配っていないフィード
        feed = Path(d).name.replace("gtfs_", "", 1)
        with path.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                if (row.get("table_name") != "stops" or row.get("field_name") != "stop_name"
                        or row.get("language") != "ja-Hrkt"):
                    continue
                yomi = kata_to_hira((row.get("translation") or "").strip())
                if not yomi:
                    continue
                if row.get("record_id"):
                    by_id[f"{feed}:{row['record_id']}"] = yomi
                elif row.get("field_value"):
                    by_name[row["field_value"]] = yomi
    return {"by_id": by_id, "by_name": by_name}


def boardable_trips(net: dict) -> list:
    """のりば(添字)ごとに「そこで乗れる便」の集合を作る(終点の停は乗れないので除く)。
    あわせて便ID→運行主体の添字も返す"""
    trips = [set() for _ in net["stops"]]
    trip_op = {}
    for stop_ids, ts in net["patterns"]:
        for t in ts:
            trip_op[t[T_TRIPID]] = t[T_OP]
            for sid in stop_ids[:-1]:
                trips[sid].add(t[T_TRIPID])
    return trips, trip_op


def cluster_platforms(idxs: list, stops: list) -> list:
    """同じ名前ののりばを、CLUSTER_M 以内でつながるまとまりに分ける(単連結)。並びは決定的"""
    groups = []
    for i in idxs:
        lat, lon = stops[i][1], stops[i][2]
        hit = [g for g in groups
               if any(haversine_m(lat, lon, stops[j][1], stops[j][2]) <= CLUSTER_M for j in g)]
        merged = [i]
        for g in hit:
            merged += g
            groups.remove(g)
        groups.append(sorted(merged))
    return sorted(groups)


class CityFinder:
    """点が どの市町村の中か(N03 行政区域)。shapely の当たり判定を使う"""

    def __init__(self, geojson_path: Path):
        from shapely.geometry import shape
        from shapely.strtree import STRtree
        feats = json.loads(Path(geojson_path).read_text(encoding="utf-8"))["features"]
        self.geoms = [shape(f["geometry"]) for f in feats]
        self.names = [f["properties"].get("N03_004") or "" for f in feats]
        self.tree = STRtree(self.geoms)

    def city(self, lat: float, lon: float) -> str:
        from shapely.geometry import Point
        p = Point(lon, lat)
        for k in self.tree.query(p):
            if self.geoms[k].covers(p):
                return self.names[k]
        return ""


def make_hints(entries: list, near_name) -> None:
    """同じ名前の停が2つ以上あるとき、見分ける一言(hint)と URL の名前(key)を決める。
    市町村 → バス会社 → 「となりの停」のちかく の順に、見分けられるまで足していく"""
    by_name = defaultdict(list)
    for e in entries:
        by_name[e["name"]].append(e)
    for name, group in by_name.items():
        if len(group) == 1:
            group[0]["hint"], group[0]["key"] = "", name
            continue
        steps = [lambda e: e["city"],
                 lambda e: "・".join(e["_ops"]),
                 lambda e: f"「{near_name(e)}」のちかく"]
        for n in range(1, len(steps) + 1):
            hints = ["・".join(s(e) for s in steps[:n] if s(e)) for e in group]
            if len(set(hints)) == len(hints):
                break
        for e, h in zip(group, hints):
            e["hint"] = h
            e["key"] = f"{name}({h})"


def build(networks: dict, readings: dict, meshes: list, city_of, target_cities) -> list:
    base = networks["weekday"]
    stops = base["stops"]
    # 3つのダイヤで停の並びが同じこと(export_network.py は停を全ダイヤ共通で書き出す)
    for dt, net in networks.items():
        if net["stop_ids"] != base["stop_ids"]:
            raise SystemExit(f"{dt} の停の並びが平日と違います")
    per_dt = {dt: boardable_trips(net) for dt, net in networks.items()}
    # バス会社の名前。「べにちゃんバス(山形市)」のかっこ書きは、停名のかっこと重なって読みにくいので外す
    op_names = [re.sub(r"[(（].*?[)）]", "", o["name"]).strip() for o in base.get("operators", [])]

    # 停名 → 読みがな(同じ名前ののりばの読みを全部集める。山交バスの停も同じ名前なら読める)
    yomi = defaultdict(set)
    for i, sid in enumerate(base["stop_ids"]):
        y = readings["by_id"].get(sid)
        if y:
            yomi[stops[i][0]].add(y)
    for name, y in readings["by_name"].items():
        yomi[name].add(y)

    by_name = defaultdict(list)
    for i, s in enumerate(stops):
        by_name[s[0]].append(i)

    def mesh_dist(lat, lon):
        return min(haversine_m(lat, lon, m[0], m[1]) for m in meshes)

    entries = []
    for name in sorted(by_name):
        for group in cluster_platforms(by_name[name], stops):
            counts = {}
            ops = set()
            for dt in DAY_TYPES:
                trips, trip_op = per_dt[dt]
                u = set().union(*(trips[i] for i in group))
                counts[dt] = len(u)
                ops |= {trip_op[t] for t in u}
            if not any(counts.values()):
                continue   # 便の無い停は選ばせない
            # 代表ののりば = 平日に乗れる便がいちばん多いのりば(同じなら全ダイヤの合計、添字の順)
            rep = min(group, key=lambda i: (-len(per_dt["weekday"][0][i]),
                                            -sum(len(per_dt[dt][0][i]) for dt in DAY_TYPES), i))
            lat, lon = stops[rep][1], stops[rep][2]
            city = city_of(lat, lon)
            if city not in target_cities and min(mesh_dist(stops[i][1], stops[i][2]) for i in group) > AREA_M:
                continue   # 2市の外で、人が住む区画からも遠い停
            entries.append({
                "name": name, "kana": sorted(yomi.get(name, [])), "city": city,
                "weekday": counts["weekday"], "saturday": counts["saturday"],
                "sunday_holiday": counts["sunday_holiday"], "lat": lat, "lon": lon,
                "_ops": sorted({op_names[o] for o in ops if 0 <= o < len(op_names)}),
            })

    def near_name(e):
        """いちばん近い「ほかの名前の停」(同じ名前の停を見分ける目印)"""
        best = min((x for x in entries if x["name"] != e["name"]),
                   key=lambda x: haversine_m(e["lat"], e["lon"], x["lat"], x["lon"]))
        return best["name"]

    make_hints(entries, near_name)
    out = []
    for e in entries:
        out.append({k: e[k] for k in ["key", "name", "kana", "city", "hint",
                                       "weekday", "saturday", "sunday_holiday", "lat", "lon"]})
    return out


def main():
    networks = {dt: json.loads((NETWORK_DIR / f"{dt}.json").read_text(encoding="utf-8")) for dt in DAY_TYPES}
    readings = load_readings(config.GTFS_FEED_DIRS)
    meshes = json.loads(MESH_INDEX_JSON.read_text(encoding="utf-8"))["meshes"]
    finder = CityFinder(config.N03_GEOJSON)
    entries = build(networks, readings, meshes, finder.city, set(config.TARGET_MUNICIPALITIES))

    keys = [e["key"] for e in entries]
    if len(keys) != len(set(keys)):
        raise SystemExit("URL の名前(key)が重なりました。make_hints を見直してください")
    OUT_JSON.write_text(json.dumps({"stops": entries}, ensure_ascii=False, separators=(",", ":")),
                        encoding="utf-8")
    n_kana = sum(1 for e in entries if e["kana"])
    n_dup = sum(1 for e in entries if e["hint"])
    print(f"停 {len(entries)}(読みがなあり {n_kana} / 同じ名前を見分ける一言つき {n_dup})")
    print(f"→ {OUT_JSON}({OUT_JSON.stat().st_size / 1024:.0f}KB)")


if __name__ == "__main__":
    main()
