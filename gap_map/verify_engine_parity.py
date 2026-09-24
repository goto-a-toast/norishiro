# -*- coding: utf-8 -*-
"""**実データ(GTFS)**で、JS版エンジンがPython版と同じ答えを出すことを確かめる。

gap_map/test_engine_parity.py は乱数で作った小さなネットワークでの照合(GTFS不要)。
こちらは本物の9フィードのネットワークで、出発停留所を多数試して突き合わせる。
案D(ブラウザで計算する方式)に進んでよいかを判断する最後の関門。

確かめること(1件でも食い違ったら失敗):
  ① 全停留所への最早到着時刻
  ② 復元した経路(区間の種類・停留所・発着時刻・便ID・系統名)
  ③ 配布形式(export_network.py)を通しても①②が変わらないこと
     (停留所の添字は「元のstop_idの文字列順」に振ってある、という不変条件込み)

使い方(GTFSのある環境=Macで。プロジェクトルートから。node が要る):
  python3 gap_map/verify_engine_parity.py                  … 平日・出発地60か所
  python3 gap_map/verify_engine_parity.py --origins 200    … もっと厳しく
  python3 gap_map/verify_engine_parity.py --all-day-types  … 3ダイヤ種別すべて
"""
import argparse
import json
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

import config
import export_network as en
import export_web_data as ew
import transit_core as tc
from build_network import build_network
from region import REGION

PROJECT_ROOT = Path(__file__).parent.parent
ENGINE_DIR = PROJECT_ROOT / "webapp" / "engine"

RUNNER_JS = r"""
const { raptorSearch, reconstructPath } = require(process.argv[2]);
const { inflateNetwork } = require(process.argv[3]);
const job = JSON.parse(require("fs").readFileSync(process.argv[4], "utf8"));
const net = inflateNetwork(job.wire);
const out = [];
for (const q of job.queries) {
  const res = raptorSearch(net, new Map(q.initial.map(([i, t]) => [i, t])),
                           q.max_transfers, q.min_transfer_min);
  const arrivals = {};
  for (const [sid, v] of res) arrivals[String(sid)] = v.arrival;
  const paths = {};
  for (const sid of q.check_stops) {
    paths[String(sid)] = reconstructPath(res, sid).map(
      (l) => [l.kind, String(l.from_stop), String(l.to_stop), l.depart, l.arrive,
              l.trip_id ?? null, l.route_name ?? null]);
  }
  out.push({ arrivals, paths });
}
console.log(JSON.stringify(out));
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--origins", type=int, default=60, help="試す出発停留所の数(既定60)")
    ap.add_argument("--all-day-types", action="store_true")
    ap.add_argument("--seed", type=int, default=20260924)
    args = ap.parse_args()

    if shutil.which("node") is None:
        raise SystemExit("node が見つかりません。JS側を動かせないので照合できません")

    dates = REGION["reference_dates"]
    targets = dates.items() if args.all_day_types else [("weekday", dates["weekday"])]

    total_q = total_stops = total_paths = 0
    for day_type, ref_date in targets:
        print(f"\n===== [{day_type}] {ref_date} =====")
        net = build_network(config.GTFS_FEED_DIRS, ref_date)
        headsigns = ew.build_headsign_map(net)
        wire = en.serialize(net, headsigns)

        stop_ids = sorted(net.stops.keys())
        order = {sid: i for i, sid in enumerate(stop_ids)}
        rnd = random.Random(args.seed)
        # 便が実際に停まる停留所から出発地を選ぶ(どこにも行けない停ばかり試しても意味がない)
        served = sorted({s for p in net.patterns for s in p.stop_ids})
        origins = rnd.sample(served, min(args.origins, len(served)))
        print(f"停留所{len(stop_ids):,}(うち便が停まる{len(served):,}) / "
              f"出発地{len(origins)}か所を試す")

        queries, expected = [], []
        for i, origin in enumerate(origins):
            start = rnd.choice([420, 480, 540, 600, 660, 720, 840, 960])   # 7時〜16時
            max_transfers = rnd.choice([0, 1, 2])
            min_transfer = config.MIN_TRANSFER_MIN
            result = tc.raptor_search(net, {origin: start}, max_transfers=max_transfers,
                                      min_transfer_min=min_transfer)
            # 経路の照合は到達できた停留所すべてで行う(多いので上限を設ける)
            reached = sorted(result.keys())
            check = reached if len(reached) <= 400 else rnd.sample(reached, 400)
            expected.append({
                "arrivals": {str(order[s]): v["arrival"] for s, v in result.items()},
                "paths": {str(order[s]): [[l.kind, str(order[l.from_stop]), str(order[l.to_stop]),
                                           l.depart, l.arrive, l.trip_id, l.route_name]
                                          for l in tc.reconstruct_path(result, s)]
                          for s in check},
            })
            queries.append({"initial": [[order[origin], start]],
                            "max_transfers": max_transfers, "min_transfer_min": min_transfer,
                            "check_stops": [order[s] for s in check]})

        with tempfile.TemporaryDirectory() as d:
            runner = Path(d) / "runner.js"
            runner.write_text(RUNNER_JS, encoding="utf-8")
            job = Path(d) / "job.json"
            job.write_text(json.dumps({"wire": wire, "queries": queries}, ensure_ascii=False),
                           encoding="utf-8")
            proc = subprocess.run(["node", str(runner), str(ENGINE_DIR / "raptor.js"),
                                   str(ENGINE_DIR / "network.js"), str(job)],
                                  capture_output=True, text=True)
            if proc.returncode != 0:
                raise SystemExit(f"JS側が異常終了しました:\n{proc.stderr}")
            got = json.loads(proc.stdout)

        ng = 0
        for i, (origin, exp, act) in enumerate(zip(origins, expected, got)):
            name = net.stops[origin]["name"]
            if act["arrivals"] != exp["arrivals"]:
                ng += 1
                diff = sorted((s for s in set(exp["arrivals"]) | set(act["arrivals"])
                               if exp["arrivals"].get(s) != act["arrivals"].get(s)), key=int)
                print(f"  NG 到着時刻 出発={name} 食い違い{len(diff)}停留所 例={diff[:3]}")
                for s in diff[:3]:
                    print(f"     添字{s}({net.stops[stop_ids[int(s)]]['name']}): "
                          f"Python={exp['arrivals'].get(s)} / JS={act['arrivals'].get(s)}")
                # 原因を追えるよう、食い違った停留所への経路を両方出す
                # (どの区間で差がついたかが分かる。徒歩の丸めか、便の選び方かの切り分け)
                s0 = diff[0]
                if s0 in exp["paths"]:
                    print(f"     [経路] Python={exp['paths'][s0]}")
                    print(f"            JS    ={act['paths'].get(s0)}")
            elif act["paths"] != exp["paths"]:
                ng += 1
                diff = [s for s in exp["paths"] if exp["paths"][s] != act["paths"].get(s)]
                print(f"  NG 経路 出発={name} 食い違い{len(diff)}停留所 例={diff[:3]}")
                for s in diff[:2]:
                    print(f"     添字{s}:\n       Python={exp['paths'][s]}\n       JS    ={act['paths'].get(s)}")
            total_q += 1
            total_stops += len(exp["arrivals"])
            total_paths += len(exp["paths"])

        print(f"  → 出発地{len(origins)}か所 / 到着時刻{sum(len(e['arrivals']) for e in expected):,}件 / "
              f"経路{sum(len(e['paths']) for e in expected):,}件 を照合: "
              + ("すべて一致" if ng == 0 else f"★{ng}件が食い違い"))
        if ng:
            raise SystemExit("食い違いがあります。案Dに進む前に原因を突き止めてください")

    print(f"\n合計 {total_q}問い合わせ / 到着時刻{total_stops:,}件 / 経路{total_paths:,}件 が完全一致")
    print("→ JS版エンジンはPython版と同じ答えを出している(案Dの段階1b 通過)")


if __name__ == "__main__":
    main()
