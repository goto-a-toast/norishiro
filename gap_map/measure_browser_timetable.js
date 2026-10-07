// -*- coding: utf-8 -*-
// 案D 段階3-1: 「わが家から時刻表を作る」計算が端末で間に合うかを測る(読み取り専用)。
//
// docs/plan_stage3_kantan.md §5 の関門:
//   38施設ぶんの行き(画面2の目安に要る)を、パソコン1秒以内 / 中くらいのスマホ想定3秒以内
//   (スマホ想定 = パソコンの実測×4。Chrome の「CPU 4倍遅く」と同じ考え方)
//
// 測るのは export_web_data.py の scan_from_origin の「重い部分」だけを素直に移植したもの:
//   build_origins(別系統の停も候補) → 乗れる便を1本ずつ試す → 直通しない行き先だけ RAPTOR
// 表示用の整形(make_itinerary)・乗り場の絞り込みなどは軽いので測らない。
// ★これは計測用の下書き。Python 版との完全一致は 3-2 で別に作って照合する
//   (ここでは停の並び順の同点処理などを詰めていない)
//
// 使い方(プロジェクトルートから):
//   node gap_map/measure_browser_timetable.js                 # 平日・全地区
//   node gap_map/measure_browser_timetable.js saturday        # ダイヤ種別を指定
//   node gap_map/measure_browser_timetable.js weekday 38.2490 140.3274   # 任意の地点
//   node gap_map/measure_browser_timetable.js weekday meshes  # メッシュ817点の中心(行きだけ)
//     家は代表点とは限らない。停の集まる駅前などで重くならないかを、住む場所の全体で確かめる

"use strict";

const fs = require("fs");
const path = require("path");
const { performance } = require("perf_hooks");

const ROOT = path.resolve(__dirname, "..");
const { raptorSearch, reconstructPath } = require(path.join(ROOT, "webapp/engine/raptor.js"));
const { inflateNetwork } = require(path.join(ROOT, "webapp/engine/network.js"));

const MAX_TRANSFERS_WEB = 1;   // export_web_data.py と同じ
const SAME_PLACE_M = 150;      // export_web_data.py と同じ

// compute_access.haversine_m_vec と同じ式
function haversineM(lat1, lon1, lat2, lon2) {
  const r = 6371000, rad = Math.PI / 180;
  const p1 = lat1 * rad, p2 = lat2 * rad;
  const dp = (lat2 - lat1) * rad, dl = (lon2 - lon1) * rad;
  const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * r * Math.asin(Math.sqrt(a));
}

// compute_access.nearby_stops と同じ: 徒歩圏の停を [停, 徒歩分(丸める前)] の近い順で
function nearby(net, lat, lon) {
  const c = net.config;
  const hits = [];
  for (const key of Object.keys(net.stops)) {
    const s = net.stops[key];
    const d = haversineM(lat, lon, s.lat, s.lon);
    if (d <= c.max_walk_to_stop_m) hits.push([Number(key), (d * c.walk_detour) / c.walk_speed_m_per_min]);
  }
  return hits.sort((a, b) => a[1] - b[1]);
}

// export_web_data.boardable_directions
function boardableDirections(net, sid) {
  const out = new Set();
  for (const [pi, pos] of net.stop_routes[sid] ?? []) {
    const p = net.patterns[pi];
    if (pos === p.stop_ids.length - 1) continue;
    const terminal = net.stops[p.stop_ids[p.stop_ids.length - 1]].name;
    for (const t of p.trips) out.add(`${t.route_name}\u0000${terminal}`);
  }
  return out;
}

// export_web_data.build_origins(expand_by_route=True)
function buildOrigins(net, lat, lon) {
  const hits = nearby(net, lat, lon);
  if (!hits.length) return [];
  const [nearestId, nearestWalk] = hits[0];
  const n = net.stops[nearestId];
  const chosen = [[nearestId, nearestWalk]];
  let covered = boardableDirections(net, nearestId);
  const same = new Set([nearestId]);
  for (const [sid, w] of hits.slice(1)) {
    const s = net.stops[sid];
    if (haversineM(n.lat, n.lon, s.lat, s.lon) <= SAME_PLACE_M) {
      chosen.push([sid, w]);
      same.add(sid);
      for (const d of boardableDirections(net, sid)) covered.add(d);
    }
  }
  for (const [sid, w] of hits.slice(1)) {
    if (same.has(sid)) continue;
    const fresh = [...boardableDirections(net, sid)].filter((d) => !covered.has(d));
    if (fresh.length) {
      chosen.push([sid, w]);
      for (const d of fresh) covered.add(d);
    }
  }
  return chosen;
}

// export_web_data.board_events
function boardEvents(net, sid) {
  const ev = [];
  for (const [pi, pos] of net.stop_routes[sid] ?? []) {
    const p = net.patterns[pi];
    if (pos === p.stop_ids.length - 1) continue;
    for (const t of p.trips) ev.push([t.departures[pos], pi, pos, t]);
  }
  return ev.sort((a, b) => a[0] - b[0]);
}

// scan_from_origin の計算部分。戻り値は件数と RAPTOR を回した回数
function scan(net, originStops, targets) {
  const stopToTargets = new Map();
  for (const [tid, list] of Object.entries(targets)) {
    for (const [sid, w] of list) {
      if (!stopToTargets.has(sid)) stopToTargets.set(sid, []);
      stopToTargets.get(sid).push([tid, w]);
    }
  }
  const events = [];
  for (const [sid] of originStops) {
    for (const e of boardEvents(net, sid)) events.push([...e, sid]);
  }
  events.sort((a, b) => a[0] - b[0]);

  const tids = Object.keys(targets);
  const directSeen = Object.fromEntries(tids.map((t) => [t, new Set()]));
  const transferSeen = Object.fromEntries(tids.map((t) => [t, new Set()]));
  let rows = 0, raptorCalls = 0;

  for (const [departMin, pi, pos, trip, originStop] of events) {
    const p = net.patterns[pi];
    const directHit = new Map();
    for (let later = pos + 1; later < p.stop_ids.length; later++) {
      for (const [tid, w] of stopToTargets.get(p.stop_ids[later]) ?? []) {
        const total = trip.arrivals[later] + Math.round(w);
        if (!directHit.has(tid) || total < directHit.get(tid)) directHit.set(tid, total);
      }
    }
    for (const tid of directHit.keys()) {
      if (directSeen[tid].has(trip.trip_id)) continue;
      directSeen[tid].add(trip.trip_id);
      rows++;
    }
    const need = tids.filter((t) => !directHit.has(t));
    if (!need.length) continue;
    raptorCalls++;
    const res = raptorSearch(net, new Map([[originStop, departMin]]), MAX_TRANSFERS_WEB,
                             net.config.min_transfer_min);
    for (const tid of need) {
      let best = null;
      for (const [sid, w] of targets[tid]) {
        const r = res.get(sid);
        if (r && (best === null || r.arrival + Math.round(w) < best[0])) best = [r.arrival + Math.round(w), sid];
      }
      if (!best) continue;
      const rides = reconstructPath(res, best[1]).filter((l) => l.kind === "ride");
      if (rides.length < 2) continue;
      const key = rides[0].trip_id + "|" + rides[1].trip_id;
      if (transferSeen[tid].has(key)) continue;
      transferSeen[tid].add(key);
      rows++;
    }
  }
  return { events: events.length, raptorCalls, rows };
}

function measurePoint(net, facilityTargets, dests, lat, lon) {
  const t0 = performance.now();
  const origins = buildOrigins(net, lat, lon);
  const out = origins.length ? scan(net, origins, facilityTargets) : { events: 0, raptorCalls: 0, rows: 0 };
  const outMs = performance.now() - t0;

  // 帰り: 画面3で選んだ1施設ぶん(施設の乗り場 → わが家)。いちばん重い施設を測る
  const home = { home: nearby(net, lat, lon) };
  let inMax = 0;
  for (const f of dests) {
    const t1 = performance.now();
    const fo = buildOrigins(net, f.lat, f.lon);
    if (fo.length && home.home.length) scan(net, fo, home);
    inMax = Math.max(inMax, performance.now() - t1);
  }
  return { origins: origins.length, ...out, outMs, inMax };
}

// メッシュ中心(住む場所)ごとの行き38施設。帰りは施設側の乗り場で重さがほぼ決まるので
// 代表点の計測で足りる(地点を変えても 帰り1施設の最大 はほぼ同じだった)
function measureMeshes(net, facilityTargets, dests) {
  const idx = JSON.parse(fs.readFileSync(path.join(ROOT, "webapp/data/mesh_index.json"), "utf8"));
  const [lat0, lon0] = idx.meshes[0];
  measurePoint(net, facilityTargets, [], lat0, lon0);   // JIT の空回し
  const res = idx.meshes.map(([lat, lon]) => ({ lat, lon, ...measurePoint(net, facilityTargets, [], lat, lon) }));
  res.sort((a, b) => b.outMs - a.outMs);
  console.log("いちばん重いメッシュ10点(行き38施設):");
  for (const r of res.slice(0, 10)) {
    console.log(`  ${r.lat.toFixed(5)},${r.lon.toFixed(5)}  乗り場${r.origins} RAPTOR${r.raptorCalls}回  ${r.outMs.toFixed(0)}ms`);
  }
  const outs = res.map((r) => r.outMs).sort((a, b) => a - b);
  const pct = (q) => outs[Math.min(outs.length - 1, Math.floor(outs.length * q))];
  console.log(`\nメッシュ${outs.length}点: 中央値 ${pct(0.5).toFixed(0)}ms / 95% ${pct(0.95).toFixed(0)}ms / ` +
              `最大 ${outs[outs.length - 1].toFixed(0)}ms(スマホ想定×4: 最大 ${(outs[outs.length - 1] * 4 / 1000).toFixed(1)}秒)` +
              ` / 1秒超 ${outs.filter((x) => x > 1000).length}点`);
}

function main() {
  const dayType = process.argv[2] || "weekday";
  const net = inflateNetwork(JSON.parse(fs.readFileSync(path.join(ROOT, `webapp/data/network/${dayType}.json`), "utf8")));
  const dests = JSON.parse(fs.readFileSync(path.join(ROOT, "webapp/data/destinations.json"), "utf8"));
  const tLoad = performance.now();
  const facilityTargets = Object.fromEntries(dests.map((f) => [f.id, nearby(net, f.lat, f.lon)]));
  const prepMs = performance.now() - tLoad;

  let points;
  if (process.argv[3] === "meshes") {
    measureMeshes(net, facilityTargets, dests);
    return;
  }
  if (process.argv[3]) {
    points = [{ id: "point", name: "指定地点", lat: Number(process.argv[3]), lon: Number(process.argv[4]) }];
  } else {
    const districts = JSON.parse(fs.readFileSync(path.join(ROOT, "webapp/data/districts.json"), "utf8"));
    points = districts.flatMap((d) => [d, ...(d.sub || [])]);
  }

  // JIT の立ち上がりを測定から外すため、最初の地点で1回空回しする
  measurePoint(net, facilityTargets, dests, points[0].lat, points[0].lon);

  console.log(`ダイヤ: ${dayType} / 施設側の下ごしらえ ${prepMs.toFixed(1)}ms / 地点 ${points.length}`);
  console.log("id     乗り場 便の候補 RAPTOR回数   行き38施設(ms)  帰り1施設の最大(ms)  名前");
  const results = points.map((p) => ({ p, r: measurePoint(net, facilityTargets, dests, p.lat, p.lon) }));
  results.sort((a, b) => b.r.outMs - a.r.outMs);
  for (const { p, r } of results) {
    console.log(`${p.id.padEnd(6)} ${String(r.origins).padStart(5)} ${String(r.events).padStart(7)} ` +
                `${String(r.raptorCalls).padStart(9)} ${r.outMs.toFixed(0).padStart(14)} ` +
                `${r.inMax.toFixed(0).padStart(18)}  ${p.name}`);
  }
  const outs = results.map((x) => x.r.outMs).sort((a, b) => a - b);
  const ins = results.map((x) => x.r.inMax).sort((a, b) => a - b);
  const med = (a) => a[Math.floor(a.length / 2)];
  console.log(`\n行き38施設: 中央値 ${med(outs).toFixed(0)}ms / 最大 ${outs[outs.length - 1].toFixed(0)}ms` +
              `(スマホ想定×4: 最大 ${(outs[outs.length - 1] * 4 / 1000).toFixed(1)}秒)`);
  console.log(`帰り1施設 : 中央値 ${med(ins).toFixed(0)}ms / 最大 ${ins[ins.length - 1].toFixed(0)}ms` +
              `(スマホ想定×4: 最大 ${(ins[ins.length - 1] * 4 / 1000).toFixed(1)}秒)`);
}

main();
