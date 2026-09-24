# -*- coding: utf-8 -*-
"""案D(ブラウザで計算する方式)の「配るデータの大きさ」を実測する。

背景(2026-09-24 開発者質問):
  いまは「62単位 × 38行き先」の答えを全部書き下して配っている(webapp/data/timetables
  = 約55MB)。代わりに「計算の材料(=ネットワーク)」を配ってブラウザで解けば、
  ① 出発点が地区代表点に丸められる制約(docs/plan_f10_stop_select.md §6.7)が消え、
  ② データ量も減るはず——という仮説を、**実測して確かめる**ためのスクリプト。

  このスクリプトは読み取り専用(--out を付けたときだけファイルを書く)。

使い方(GTFSのある環境=Macで。プロジェクトルートから):
  python3 gap_map/estimate_browser_engine.py                 … 平日だけ測る(速い)
  python3 gap_map/estimate_browser_engine.py --all-day-types … 3ダイヤ種別すべて
  python3 gap_map/estimate_browser_engine.py --out /tmp/net.json … 実物を書き出す
"""
import argparse
import gzip
import json
from pathlib import Path

import config
from build_network import build_network
from region import REGION

PROJECT_ROOT = Path(__file__).parent.parent
TIMETABLES_DIR = PROJECT_ROOT / "webapp" / "data" / "timetables"


def serialize(network) -> dict:
    """ブラウザに配る形に詰め直す。transit_core.Network と同じ中身だが、
    ①停留所IDを添字に置き換え ②時刻は先頭からの差分 にして小さくする。
    JSのRAPTORはこれを読んでそのまま探索できる(構造は transit_core と同じ)"""
    stop_ids = list(network.stops.keys())
    idx = {sid: i for i, sid in enumerate(stop_ids)}

    stops = [[network.stops[s]["name"], round(network.stops[s]["lat"], 5),
              round(network.stops[s]["lon"], 5)] for s in stop_ids]

    routes = {}          # 系統名 → 添字(同じ文字列を何千回も書かない)
    patterns = []
    for p in network.patterns:
        trips = []
        for t in p.trips:
            base = t.departures[0]
            # 到着・出発とも「先頭停留所の出発時刻からの差分」にする(1〜2桁に収まる)
            dep = [d - base for d in t.departures]
            arr = [a - base for a in t.arrivals]
            ri = routes.setdefault(t.route_name, len(routes))
            trips.append([base, ri, dep, arr])
        patterns.append([[idx[s] for s in p.stop_ids], trips])

    foot = {}
    for sid, lst in (network.footpaths or {}).items():
        # 徒歩分は分に丸めた整数(export_network.py と同じ扱い。理由はそちらのコメント)
        foot[str(idx[sid])] = [[idx[o], round(w)] for o, w in lst if o in idx]

    return {"stops": stops, "routes": list(routes.keys()),
            "patterns": patterns, "footpaths": foot}


def report(label, obj):
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    gz = gzip.compress(raw, 6)
    print(f"  {label:14s} そのまま {len(raw)/1024/1024:7.2f}MB / "
          f"gzip後 {len(gz)/1024/1024:6.2f}MB")
    return len(raw), len(gz)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all-day-types", action="store_true",
                    help="平日・土曜・日祝の3つとも測る(既定は平日だけ)")
    ap.add_argument("--out", help="ネットワークJSONの書き出し先(付けなければ書かない)")
    args = ap.parse_args()

    cur = sum(f.stat().st_size for f in TIMETABLES_DIR.glob("*.json")) if TIMETABLES_DIR.exists() else 0
    print(f"いまの方式(答えを全部書き下す): {cur/1024/1024:.1f}MB "
          f"({len(list(TIMETABLES_DIR.glob('*.json')))}ファイル)\n")

    dates = REGION["reference_dates"]
    targets = dates.items() if args.all_day_types else [("weekday", dates["weekday"])]

    total_raw = total_gz = 0
    for day_type, ref_date in targets:
        print(f"[{day_type}] {ref_date} のネットワークを組み立て中…")
        net = build_network(config.GTFS_FEED_DIRS, ref_date)
        n_trips = sum(len(p.trips) for p in net.patterns)
        n_times = sum(len(p.stop_ids) * len(p.trips) for p in net.patterns)
        print(f"  停留所 {len(net.stops):,} / パターン {len(net.patterns):,} / "
              f"便 {n_trips:,} / 発着時刻 {n_times:,}")
        data = serialize(net)
        raw, gz = report("配るデータ", data)
        total_raw += raw; total_gz += gz
        if args.out:
            Path(args.out).write_text(json.dumps(data, ensure_ascii=False,
                                                 separators=(",", ":")), encoding="utf-8")
            print(f"  → {args.out} に書き出しました")

    if len(list(targets)) > 1 or args.all_day_types:
        print(f"\n3ダイヤ種別の合計: そのまま {total_raw/1024/1024:.2f}MB / "
              f"gzip後 {total_gz/1024/1024:.2f}MB")
    print(f"\n結論: いま {cur/1024/1024:.1f}MB → 案D {total_gz/1024/1024:.2f}MB(gzip後)"
          f" = {cur/total_gz:.1f}分の1" if total_gz else "")
    print("※ GitHub Pages は gzip を自動で付けて配るので、利用者の通信量は gzip後の値")


if __name__ == "__main__":
    main()
