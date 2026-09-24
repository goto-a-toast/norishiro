# -*- coding: utf-8 -*-
"""JS移植版エンジン(webapp/engine/raptor.js)が、Python版(transit_core.py)と
**1件の食い違いもなく同じ答えを出す**ことを確かめる照合テスト。

なぜ要るのか(docs/plan_gap_map.md §1「検算できない計算結果を配らない」):
  案D(ブラウザで計算する方式)では、利用者の端末が経路を計算する。その答えが
  Python版とずれていたら、検算済みの数字を配るという作品の前提が崩れる。
  そこで、乱数で作ったネットワークと問い合わせを両方のエンジンに与え、
  ①全停留所への最早到着時刻 ②復元した経路(区間の種類・停留所・時刻・便ID)
  が完全に一致することを、テストのたびに機械で確かめる。

  実データ(GTFS)での照合は gap_map/verify_engine_parity.py(Macで実行)が行う。
  こちらはGTFS不要で、CIでも動く。

実行: python -m pytest gap_map/test_engine_parity.py -v
必要: node(JSを動かすため)。無い環境ではスキップする
"""
import json
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

import transit_core as tc

PROJECT_ROOT = Path(__file__).parent.parent
RAPTOR_JS = PROJECT_ROOT / "webapp" / "engine" / "raptor.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node が無いのでJS側を動かせない")


def make_random_network(seed: int):
    """乱数で小さなネットワークを作る(同じseedなら必ず同じもの)。
    わざと意地悪にする: 同着・追い越し無し・徒歩で環になる接続・孤立停留所"""
    rnd = random.Random(seed)
    n_stops = rnd.randint(5, 14)
    stop_ids = [f"S{i:02d}" for i in range(n_stops)]
    stops = {s: {"name": f"停留所{s}", "lat": 38.0 + rnd.random(), "lon": 140.0 + rnd.random()}
             for s in stop_ids}

    patterns = []
    for p in range(rnd.randint(2, 7)):
        k = rnd.randint(2, min(6, n_stops))
        # 路線同士が停留所を共有しやすいようにする(共有しないと乗換が起きず、
        # いちばん確かめたい「乗換のある経路」を試せないため)
        start = rnd.randrange(max(1, n_stops - k + 1))
        seq = stop_ids[start:start + k]
        if rnd.random() < 0.5:
            seq = list(reversed(seq))          # 逆向きの路線
        if rnd.random() < 0.3:
            seq = rnd.sample(stop_ids, k)      # ときどき飛び飛びの路線も混ぜる
        trips = []
        base = rnd.randint(300, 600)
        for t in range(rnd.randint(1, 5)):
            # 先頭の出発時刻は昇順(パターン内の便は昇順に並んでいる前提)
            base += rnd.randint(1, 40)
            dep, arr, now = [], [], base
            for i in range(k):
                arr.append(now)
                now += rnd.choice([0, 0, 1])       # 停車時間(0が多い)
                dep.append(now)
                now += rnd.randint(1, 12)          # 次の停留所まで
            trips.append(tc.Trip(trip_id=f"p{p}t{t}", route_name=f"R{p}",
                                 arrivals=arr, departures=dep))
        patterns.append(tc.Pattern(stop_ids=tuple(seq), trips=trips))

    stop_routes = {}
    for idx, pat in enumerate(patterns):
        for pos, sid in enumerate(pat.stop_ids):
            stop_routes.setdefault(sid, []).append((idx, pos))

    footpaths = {}
    for _ in range(rnd.randint(0, n_stops)):
        a, b = rnd.sample(stop_ids, 2)
        # 徒歩分の値は2種類を必ず混ぜる:
        #  ・.5 ちょうど … Pythonの銀行家丸めとJSのMath.roundの違いを炙り出す
        #  ・3.49 や 4.55 のような値 … 小数1桁に丸めると分への丸め結果が変わる値。
        #    2026-09-24の実データ照合で、配布形式が小数1桁に丸めていたために
        #    ここで1分ずれる不具合が見つかった。乱数の候補がすべて「1桁に丸めても
        #    変わらない値」だったため、乱数テストは素通りしていた
        w = rnd.choice([0.5, 1.5, 2.5, 3.5, 4.5, 1.2, 2.7, 6.0,
                        3.49, 4.55, 2.55, 6.55, 0.55, 1.44, 7.45,
                        round(rnd.uniform(0.1, 8.0), 2)])
        footpaths.setdefault(a, []).append((b, w))
        footpaths.setdefault(b, []).append((a, w))

    return tc.Network(patterns=patterns, stop_routes=stop_routes, stops=stops,
                      footpaths=footpaths)


def network_to_json(net):
    return {
        "patterns": [{"stop_ids": list(p.stop_ids),
                      "trips": [{"trip_id": t.trip_id, "route_name": t.route_name,
                                 "arrivals": t.arrivals, "departures": t.departures}
                                for t in p.trips]} for p in net.patterns],
        "stop_routes": {k: [list(v) for v in vs] for k, vs in net.stop_routes.items()},
        "stops": net.stops,
        "footpaths": {k: [list(v) for v in vs] for k, vs in (net.footpaths or {}).items()},
    }


def legs_to_plain(legs):
    return [[l.kind, l.from_stop, l.to_stop, l.depart, l.arrive, l.trip_id, l.route_name]
            for l in legs]


RUNNER_JS = r"""
const path = require("path");
const { raptorSearch, reconstructPath } = require(process.argv[2]);
const cases = JSON.parse(require("fs").readFileSync(process.argv[3], "utf8"));
const out = [];
for (const c of cases) {
  const res = raptorSearch(c.network, c.initial_stops, c.max_transfers, c.min_transfer_min);
  const arrivals = {};
  for (const [sid, v] of res) arrivals[sid] = v.arrival;
  const paths = {};
  for (const sid of Object.keys(c.network.stops)) {
    paths[sid] = reconstructPath(res, sid).map(
      (l) => [l.kind, l.from_stop, l.to_stop, l.depart, l.arrive, l.trip_id ?? null, l.route_name ?? null]);
  }
  out.push({ arrivals, paths });
}
console.log(JSON.stringify(out));
"""


def run_js(cases):
    with tempfile.TemporaryDirectory() as d:
        runner = Path(d) / "runner.js"
        runner.write_text(RUNNER_JS, encoding="utf-8")
        cases_path = Path(d) / "cases.json"
        cases_path.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
        proc = subprocess.run(["node", str(runner), str(RAPTOR_JS), str(cases_path)],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            raise AssertionError(f"JS側が異常終了しました:\n{proc.stderr}")
        return json.loads(proc.stdout)


def build_cases(seeds):
    cases, expected = [], []
    for seed in seeds:
        net = make_random_network(seed)
        rnd = random.Random(seed * 7919)
        stop_ids = sorted(net.stops)
        initial = {s: rnd.randint(280, 520)
                   for s in rnd.sample(stop_ids, rnd.randint(1, min(3, len(stop_ids))))}
        max_transfers = rnd.choice([0, 1, 2])
        min_transfer = rnd.choice([0, 3, 5])

        result = tc.raptor_search(net, dict(initial), max_transfers=max_transfers,
                                  min_transfer_min=min_transfer)
        expected.append({
            "arrivals": {s: v["arrival"] for s, v in result.items()},
            "paths": {s: legs_to_plain(tc.reconstruct_path(result, s)) for s in stop_ids},
        })
        cases.append({"network": network_to_json(net), "initial_stops": initial,
                      "max_transfers": max_transfers, "min_transfer_min": min_transfer})
    return cases, expected


def test_js_matches_python_on_many_random_networks():
    """乱数で作った200通りのネットワーク×問い合わせで、到着時刻と経路が完全一致すること"""
    seeds = list(range(1, 201))
    cases, expected = build_cases(seeds)
    got = run_js(cases)
    assert len(got) == len(expected)
    for seed, exp, act in zip(seeds, expected, got):
        assert act["arrivals"] == exp["arrivals"], f"seed={seed} の到着時刻が食い違う"
        assert act["paths"] == exp["paths"], f"seed={seed} の経路が食い違う"


def test_python_round_is_bankers_rounding():
    """移植でいちばん危ない差: Pythonのround()は0.5を偶数側に丸める。
    JS側の pyRound() が同じ挙動であることを直接確かめる"""
    values = [0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 1.2, 2.7, 6.0, 0.4999, 2.5001]
    expected = [round(v) for v in values]
    with tempfile.TemporaryDirectory() as d:
        runner = Path(d) / "r.js"
        runner.write_text(
            'const {pyRound}=require(process.argv[2]);'
            'console.log(JSON.stringify(JSON.parse(process.argv[3]).map(pyRound)));',
            encoding="utf-8")
        proc = subprocess.run(["node", str(runner), str(RAPTOR_JS), json.dumps(values)],
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout) == expected, "丸めがPythonと違う(Math.roundを使っていないか)"


# ===============================================================
# 配布形式(export_network.py)を経由しても答えが変わらないことの確認
# ---------------------------------------------------------------
# 配布形式では停留所IDが「元のstop_idを文字列順に並べて振った添字」になる。
# Python版は同着のタイブレークで stop_id の文字列順に走査するので、
# 「添字の数値順 = 文字列順」という不変条件が崩れると経路の選ばれ方がずれる。
# ここではその不変条件も含めて、実際に配る形で答えが一致することを確かめる
# ===============================================================
WIRE_RUNNER_JS = r"""
const { raptorSearch, reconstructPath } = require(process.argv[2]);
const { inflateNetwork } = require(process.argv[3]);
const cases = JSON.parse(require("fs").readFileSync(process.argv[4], "utf8"));
const out = [];
for (const c of cases) {
  const net = inflateNetwork(c.wire);
  const res = raptorSearch(net, new Map(c.initial_stops.map(([i, t]) => [i, t])),
                           c.max_transfers, c.min_transfer_min);
  const arrivals = {};
  for (const [sid, v] of res) arrivals[String(sid)] = v.arrival;
  const paths = {};
  for (let i = 0; i < c.wire.stops.length; i++) {
    paths[String(i)] = reconstructPath(res, i).map(
      (l) => [l.kind, String(l.from_stop), String(l.to_stop), l.depart, l.arrive,
              l.trip_id ?? null, l.route_name ?? null]);
  }
  // 行き先表示・のりばが配布形式から正しく読めているかも1件確認する
  const sample = net.patterns[0].trips[0];
  out.push({ arrivals, paths,
             sample: [sample.headsign, sample.route_name, net.stops[0].platform_code] });
}
console.log(JSON.stringify(out));
"""


def test_wire_format_keeps_the_same_answers():
    """export_network.py の配布形式に詰めて、JS側で展開して探索しても、
    Python版と到着時刻・経路が完全一致すること"""
    import export_network as en

    seeds = list(range(1, 121))
    cases, expected = [], []
    for seed in seeds:
        net = make_random_network(seed)
        rnd = random.Random(seed * 104729)
        stop_ids = sorted(net.stops)
        initial = {s: rnd.randint(280, 520)
                   for s in rnd.sample(stop_ids, rnd.randint(1, min(3, len(stop_ids))))}
        max_transfers = rnd.choice([0, 1, 2])
        min_transfer = rnd.choice([0, 3, 5])

        result = tc.raptor_search(net, dict(initial), max_transfers=max_transfers,
                                  min_transfer_min=min_transfer)
        # Python側の答えを「添字」に置き換えてから比べる(JS側は添字で返すため)
        order = {sid: i for i, sid in enumerate(stop_ids)}
        expected.append({
            "arrivals": {str(order[s]): v["arrival"] for s, v in result.items()},
            "paths": {str(order[s]): [[l.kind, str(order[l.from_stop]), str(order[l.to_stop]),
                                       l.depart, l.arrive, l.trip_id, l.route_name]
                                      for l in tc.reconstruct_path(result, s)]
                      for s in stop_ids},
        })
        headsigns = {t.trip_id: f"{t.route_name}行き"
                     for p in net.patterns for t in p.trips}
        wire = en.serialize(net, headsigns)
        cases.append({"wire": wire,
                      "initial_stops": [[order[s], t] for s, t in initial.items()],
                      "max_transfers": max_transfers, "min_transfer_min": min_transfer})

    with tempfile.TemporaryDirectory() as d:
        runner = Path(d) / "runner.js"
        runner.write_text(WIRE_RUNNER_JS, encoding="utf-8")
        cases_path = Path(d) / "cases.json"
        cases_path.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
        proc = subprocess.run(
            ["node", str(runner), str(RAPTOR_JS),
             str(PROJECT_ROOT / "webapp" / "engine" / "network.js"), str(cases_path)],
            capture_output=True, text=True)
        assert proc.returncode == 0, f"JS側が異常終了:\n{proc.stderr}"
        got = json.loads(proc.stdout)

    for seed, exp, act in zip(seeds, expected, got):
        assert act["arrivals"] == exp["arrivals"], f"seed={seed} 配布形式で到着時刻が食い違う"
        assert act["paths"] == exp["paths"], f"seed={seed} 配布形式で経路が食い違う"
    # 行き先表示が配布形式を通って読めていること(R1の主役なので落とせない)
    assert all(a["sample"][0].endswith("行き") for a in got)
