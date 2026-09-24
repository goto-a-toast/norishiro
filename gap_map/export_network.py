# -*- coding: utf-8 -*-
"""案D(ブラウザで計算する方式)のために、探索の材料=ネットワークを書き出す。

いまの方式は「62単位 × 38行き先」の答えを全部書き下している(約55MB)。こちらは
材料だけを配り、端末のJS(webapp/engine/raptor.js)が探索する。実測で 3ダイヤ種別の
合計 gzip後0.16MB(いまの334分の1)。出発点が利用者の実際の位置になるので、
「地区の代表点に丸められる」制約(docs/plan_f10_stop_select.md §6.7)が消える。

★凍結資産には触らない: build_network.py / transit_core.py は import して使うだけ。
  headsign は export_web_data.py と同じやり方で trips.txt から読み直す
  (凍結資産のdiffを0行に保つための既存の設計をそのまま踏襲)。

★添字の決め方(パリティの要): 停留所の添字は**元のstop_idの文字列順**に振る。
  Python版は同着のタイブレークで stop_id の文字列順に走査するので、
  「添字の数値順 = Python版の文字列順」にしておけば、JS側は数値順に並べるだけで
  まったく同じ答えになる。この不変条件が崩れると経路の選ばれ方がずれる。

使い方(GTFSのある環境=Macで。プロジェクトルートから):
  python3 gap_map/export_network.py            … webapp/data/network/ に書き出す
  python3 gap_map/export_network.py --dry-run  … 大きさを測るだけ(書き込まない)
"""
import argparse
import gzip
import json
from pathlib import Path

import config
import export_web_data as ew
from build_network import build_network
from region import REGION

PROJECT_ROOT = Path(__file__).parent.parent
OUT_DIR = PROJECT_ROOT / "webapp" / "data" / "network"

# 配るJSONの形式。読む側(webapp/engine/network.js)と必ず同じ番号にする
FORMAT_VERSION = 1


def serialize(network, headsigns: dict) -> dict:
    """transit_core.Network を、配る形に詰め直す。

    trips の1件は次の並び(短くするため配列にしている):
      [先頭停留所の出発時刻, 系統の添字, 行き先表示の添字, 運行主体の添字 or null,
       元のtrip_id, 出発時刻の差分リスト, 到着時刻の差分リスト]
    差分はすべて「先頭停留所の出発時刻」からの差(1〜2桁に収まる)。
    """
    # ★stop_idの文字列順で添字を振る(上記の不変条件)
    stop_ids = sorted(network.stops.keys())
    idx = {sid: i for i, sid in enumerate(stop_ids)}

    stops = []
    for sid in stop_ids:
        info = network.stops[sid]
        platform = info.get("platform_code")
        if not isinstance(platform, str) or not platform.strip():
            platform = None      # stops.txtに列が無い/空欄の行は NaN が入るため
        stops.append([info["name"], round(info["lat"], 5), round(info["lon"], 5), platform])

    routes, hs_list = {}, {}
    patterns = []
    for pat in network.patterns:
        trips = []
        for t in pat.trips:
            base = t.departures[0]
            ri = routes.setdefault(t.route_name, len(routes))
            hi = hs_list.setdefault(headsigns.get(t.trip_id, ""), len(hs_list))
            trips.append([base, ri, hi, ew.operator_index(t.trip_id), t.trip_id,
                          [d - base for d in t.departures],
                          [a - base for a in t.arrivals]])
        patterns.append([[idx[s] for s in pat.stop_ids], trips])

    footpaths = {}
    for sid, lst in (network.footpaths or {}).items():
        pairs = [[idx[o], round(w, 1)] for o, w in lst if o in idx]
        if pairs:
            footpaths[str(idx[sid])] = pairs

    return {
        "format": FORMAT_VERSION,
        "stop_ids": stop_ids,          # 検算用(画面には出さない)。Python版との突き合わせに使う
        "stops": stops,                # [表示名, 緯度, 経度, のりば番号]
        "routes": list(routes.keys()),
        "headsigns": list(hs_list.keys()),
        "operators": ew.build_operators(),
        "patterns": patterns,
        "footpaths": footpaths,
    }


def sizes(obj):
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return raw, gzip.compress(raw, 6)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="大きさを測るだけ(書き込まない)")
    args = ap.parse_args()

    if not args.dry_run:
        OUT_DIR.mkdir(parents=True, exist_ok=True)

    total_raw = total_gz = 0
    for day_type, ref_date in REGION["reference_dates"].items():
        print(f"[{day_type}] {ref_date} のネットワークを組み立て中…")
        net = build_network(config.GTFS_FEED_DIRS, ref_date)
        headsigns = ew.build_headsign_map(net)
        data = serialize(net, headsigns)
        raw, gz = sizes(data)
        total_raw += len(raw); total_gz += len(gz)
        n_trips = sum(len(p.trips) for p in net.patterns)
        print(f"  停留所{len(net.stops):,} / パターン{len(net.patterns):,} / 便{n_trips:,}"
              f" / 行き先表示{len(data['headsigns'])}種")
        print(f"  そのまま {len(raw)/1024/1024:.2f}MB / gzip後 {len(gz)/1024/1024:.2f}MB")
        if not args.dry_run:
            out = OUT_DIR / f"{day_type}.json"
            out.write_bytes(json.dumps(data, ensure_ascii=False,
                                       separators=(",", ":")).encode("utf-8"))
            print(f"  → {out.relative_to(PROJECT_ROOT)}")

    print(f"\n合計: そのまま {total_raw/1024/1024:.2f}MB / gzip後 {total_gz/1024/1024:.2f}MB")
    if args.dry_run:
        print("(--dry-run のため書き込んでいない)")


if __name__ == "__main__":
    main()
