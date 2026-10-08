// -*- coding: utf-8 -*-
// 案D 段階3: 「わが家」から行き先までの時刻表を端末の中で作る。
//
// gap_map/export_web_data.py(時刻表の工場)のうち、**1地点ぶん**の処理の移植。
// 工場は「地区の代表点」から全行き先の答えを書き下して配っている(地区ファイル)。
// ここでは同じ手順を、利用者の家の位置を出発点にして端末で行い、地区ファイルと
// **まったく同じ形**の答え(1行き先ぶんのエントリ)を返す。画面(kantan/app.js)は
// どちらから来た答えかを区別せずに表示できる。
//
// ★いちばん大事な約束: 地区の代表点を「わが家」として計算させたとき、地区ファイル
//   (Python の答え)と1件の食い違いもないこと。gap_map/verify_timetable_parity.js が
//   全地区×全行き先×3ダイヤ×行き帰りを突き合わせる(docs/plan_stage3_kantan.md §5)。
//   関数名は工場と対応させてある(build_origins → buildOrigins など)。
//
// ★工場と答えをそろえるために気をつけたところ:
//   1. 四捨五入は Python の round()(0.5は偶数側)= raptor.js の pyRound()
//   2. 並べ替えはすべて「同点なら元の順番を保つ」(Python の sorted と同じ。JSの sort も同じ性質)
//   3. 徒歩圏の停を近い順に並べるとき、同じ距離の停は工場が停を見る順番(stopOrder)に置く
//   4. 距離の式は工場と同じ haversine(地球半径6371km)。座標は丸めない値を使う

// Node(照合スクリプト)からも、ブラウザ(<script> や Web Worker の importScripts で raptor.js の
// 後に読む)からも使う。ブラウザでは画面のプログラム(kantan/app.js など)と関数名がぶつからないよう、
// 外には TimetableEngine という名前1つだけを出す
(function (root) {
"use strict";

/* global pyRound, cmpStr, raptorSearch, reconstructPath */
const TT_ENGINE = (typeof module !== "undefined" && module.exports)
  ? require("./raptor.js")
  : { pyRound, cmpStr, raptorSearch, reconstructPath };

// ---- 工場の定数(export_web_data.py と同じ値。変えるときは両方を変える) ----
const MAX_TRANSFERS_WEB = 1;
const SAME_PLACE_M = 150;
const GAP_FILL_WINDOW_MIN = 30;
const TRANSFER_GROUP_WINDOW_MIN = 20;
const MAX_BOARD_OPTIONS = 6;
const KANTAN_SWITCH_GAIN_MIN = 5;
const KANTAN_MIN_TRIP_SHARE = 0.5;
const KANTAN_FREQUENT_TRIPS = 3;
const DAY_TYPES = ["weekday", "saturday", "sunday_holiday"];   // region.py の reference_dates の順

// ===============================================================
// 小さな道具
// ===============================================================
// build_network.haversine_m / compute_access.haversine_m_vec と同じ式
function haversineM(lat1, lon1, lat2, lon2) {
  const rad = Math.PI / 180;
  const p1 = lat1 * rad, p2 = lat2 * rad;
  const dp = (lat2 - lat1) * rad, dl = (lon2 - lon1) * rad;
  const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(a));
}

// compute_access.walk_minutes(丸める前の徒歩分)
function walkMinutes(net, distM) {
  return (distM * net.config.walk_detour) / net.config.walk_speed_m_per_min;
}

// fmt_hm / hm_to_min
function fmtHm(min) {
  const m = TT_ENGINE.pyRound(min);
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}
function hmToMin(hm) {
  const [h, m] = hm.split(":");
  return Number(h) * 60 + Number(m);
}

// normalize_text(全角英数字を半角に。前後の空白を落とす)
function normalizeText(s) {
  return typeof s === "string" ? s.normalize("NFKC").trim() : s;
}

// stop_latlon(のりばの座標を小数5桁=約1mで)
function stopLatLon(net, sid) {
  const s = net.stops[sid];
  if (!s || s.lat == null || s.lon == null) return null;
  return [Number(s.lat.toFixed(5)), Number(s.lon.toFixed(5))];
}

// Python の `r.get("board_options") or [主停だけ]`(空のリストも「無い」扱い)
function optsOr(r, fallback) {
  return r.board_options && r.board_options.length ? r.board_options : fallback;
}

// statistics.median
function median(values) {
  const v = values.slice().sort((a, b) => a - b);
  const n = v.length;
  return n % 2 ? v[(n - 1) / 2] : (v[n / 2 - 1] + v[n / 2]) / 2;
}

// Python の min(seq, key=...)(最小が複数あれば最初のもの)
function minBy(seq, key) {
  let best = null, bestKey = null;
  for (const x of seq) {
    const k = key(x);
    if (best === null || k < bestKey) { best = x; bestKey = k; }
  }
  return best;
}

// ===============================================================
// 徒歩圏の停・乗り場の候補(StopIndex.nearby / build_origins / build_targets)
// ===============================================================
// 1点から徒歩圏の停を [停の添字, 徒歩分(丸める前)] の近い順で返す。
// 同じ徒歩分の停は工場が停を見る順番(stopOrder)のまま(=Python の安定な並べ替え)
function nearbyStops(net, lat, lon) {
  const max = net.config.max_walk_to_stop_m;
  const hits = [];
  for (const sid of net.stopOrder) {
    const s = net.stops[sid];
    const d = haversineM(lat, lon, s.lat, s.lon);
    if (d <= max) hits.push([sid, walkMinutes(net, d)]);
  }
  return hits.sort((a, b) => a[1] - b[1]);
}

// boardable_directions: その停から乗れる「系統名+終点名」の集合
function boardableDirections(net, sid) {
  const out = new Set();
  for (const [pi, pos] of net.stop_routes[sid] ?? []) {
    const p = net.patterns[pi];
    if (pos === p.stop_ids.length - 1) continue;   // 終点では乗れない
    const terminal = net.stops[p.stop_ids[p.stop_ids.length - 1]].name;
    for (const t of p.trips) out.add(`${t.route_name}\u0000${terminal}`);
  }
  return out;
}

// build_origins(expand_by_route=True): 最寄りの停+同じ場所の別のりば+別系統を持つ停。
// 戻り値: [[停, 徒歩分, 表示名], ...](1件目が必ず最寄り)
function buildOrigins(net, lat, lon, name) {
  const hits = nearbyStops(net, lat, lon);
  if (!hits.length) return [];
  const [nearestId, nearestWalk] = hits[0];
  const n = net.stops[nearestId];
  const chosen = [[nearestId, nearestWalk, name]];
  let covered = boardableDirections(net, nearestId);
  const samePlace = new Set([nearestId]);
  for (const [sid, w] of hits.slice(1)) {
    const s = net.stops[sid];
    if (haversineM(n.lat, n.lon, s.lat, s.lon) <= SAME_PLACE_M) {
      chosen.push([sid, w, name]);
      samePlace.add(sid);
      covered = new Set([...covered, ...boardableDirections(net, sid)]);
    }
  }
  for (const [sid, w] of hits.slice(1)) {
    if (samePlace.has(sid)) continue;
    const fresh = [...boardableDirections(net, sid)].filter((d) => !covered.has(d));
    if (fresh.length) {
      chosen.push([sid, w, name]);
      covered = new Set([...covered, ...fresh]);
    }
  }
  return chosen;
}

// build_targets: 降りる側は徒歩圏の停を全部。[[停, 徒歩分, 表示名], ...]
function buildTargets(net, lat, lon, name) {
  return nearbyStops(net, lat, lon).map(([sid, w]) => [sid, w, name]);
}

// board_events: その停から乗れる便を [発車時刻, パターン, 乗車位置, 便] の発車順で
function boardEvents(net, sid) {
  const ev = [];
  for (const [pi, pos] of net.stop_routes[sid] ?? []) {
    const p = net.patterns[pi];
    if (pos === p.stop_ids.length - 1) continue;
    for (const t of p.trips) ev.push([t.departures[pos], pi, pos, t]);
  }
  return ev.sort((a, b) => a[0] - b[0]);
}

// ===============================================================
// 1便ぶんの表示用データ(make_itinerary / board_options_for / alight_options_for)
// ===============================================================
function boardOptionsFor(net, pattern, trip, upToPos, nearHome) {
  const opts = [];
  const seen = new Set();
  for (let p = 0; p < upToPos; p++) {
    const sid = pattern.stop_ids[p];
    if (nearHome.has(sid) && !seen.has(sid)) {
      seen.add(sid);
      opts.push({ stop: net.stops[sid].name, dep: fmtHm(trip.departures[p]),
                  walk_min: TT_ENGINE.pyRound(nearHome.get(sid)), _ll: stopLatLon(net, sid) });
    }
  }
  opts.sort((a, b) => a.walk_min - b.walk_min);
  return opts.slice(0, MAX_BOARD_OPTIONS);
}

function alightOptionsFor(net, pattern, trip, fromPos, homeWalks) {
  const opts = [];
  const seen = new Set();
  for (let p = fromPos + 1; p < pattern.stop_ids.length; p++) {
    const sid = pattern.stop_ids[p];
    if (homeWalks.has(sid) && !seen.has(sid)) {
      seen.add(sid);
      opts.push({ stop: net.stops[sid].name, arr: fmtHm(trip.arrivals[p]),
                  walk_min: TT_ENGINE.pyRound(homeWalks.get(sid)), _ll: stopLatLon(net, sid) });
    }
  }
  opts.sort((a, b) => a.walk_min - b.walk_min);
  return opts.slice(0, MAX_BOARD_OPTIONS);
}

function tripMeta(net, tripId) {
  return net.tripInfo[tripId] || {};
}

function makeItinerary(net, path, finalArrival, boardName, alightPlace,
                       boardWalkMin, alightWalkMin, boardOptions, alightOptions) {
  const rides = path.filter((l) => l.kind === "ride");
  const first = rides[0];
  const dep = first.depart;

  let transfer = null;
  if (rides.length >= 2) {
    const second = rides[1];
    const transferStop = second.from_stop;
    const walkBetween = path.find((l) => l.kind === "walk" && l.to_stop === transferStop);
    const arriveAtTransfer = walkBetween ? walkBetween.arrive : first.arrive;
    const firstAlightName = net.stops[first.to_stop] ? net.stops[first.to_stop].name : null;
    const m2 = tripMeta(net, second.trip_id);
    transfer = {
      at: net.stops[transferStop].name,
      wait_min: TT_ENGINE.pyRound(second.depart - arriveAtTransfer),
      headsign2: m2.headsign,
      route2: normalizeText(second.route_name),
      op2: Number.isInteger(m2.op) ? m2.op : null,
    };
    if (firstAlightName && firstAlightName !== transfer.at) transfer.off = firstAlightName;
    // 乗り換えで乗る/降りるのりばの座標(工場と同じ一時フィールド。attachStopPoints が atp / offp に)
    transfer._at_ll = stopLatLon(net, transferStop);
    if (transfer.off) transfer._off_ll = stopLatLon(net, first.to_stop);
  }

  const totalMin = finalArrival - dep;
  const rideMin = TT_ENGINE.pyRound(totalMin - (transfer ? transfer.wait_min : 0));
  const boardInfo = net.stops[first.from_stop] || {};
  const platform = typeof boardInfo.platform_code === "string" ? boardInfo.platform_code : null;
  const alightStop = rides[rides.length - 1].to_stop;
  const alightName = (net.stops[alightStop] && net.stops[alightStop].name) || alightPlace;
  const m1 = tripMeta(net, first.trip_id);
  return {
    board_ll: stopLatLon(net, first.from_stop),
    alight_ll: stopLatLon(net, alightStop),
    dep: fmtHm(dep),
    arr: fmtHm(finalArrival),
    board: boardName,
    board_walk_min: boardWalkMin != null ? TT_ENGINE.pyRound(boardWalkMin) : null,
    platform,
    alight: alightName,
    alight_place: alightPlace,
    alight_walk_min: alightWalkMin != null ? TT_ENGINE.pyRound(alightWalkMin) : null,
    headsign: m1.headsign,
    route: normalizeText(first.route_name),
    op: Number.isInteger(m1.op) ? m1.op : null,
    ride_min: rideMin,
    transfer,
    board_options: boardOptions,
    alight_options: alightOptions,
  };
}

// ===============================================================
// 乗れる便を1本ずつ試す(scan_from_origin)
// ===============================================================
// originStops: [[停, 徒歩分, 表示名], ...]
// targets    : {行き先id: [[停, 徒歩分, 表示名], ...]}
// nearHome   : Map(停 → 徒歩分)。行きだけ(board_options を付ける)
// alightHome : true なら帰り(alight_options を付ける)
// 戻り値     : {行き先id: [1便ぶんのデータ, ...](発車順)}
function scanFromOrigin(net, originStops, targets, nearHome = null, alightHome = false) {
  const { pyRound: rnd, raptorSearch: search, reconstructPath: recon } = TT_ENGINE;
  const tids = Object.keys(targets);
  const stopToTargets = new Map();
  for (const tid of tids) {
    for (const [sid, w, name] of targets[tid]) {
      if (!stopToTargets.has(sid)) stopToTargets.set(sid, []);
      stopToTargets.get(sid).push([tid, w, name]);
    }
  }
  const originWalk = new Map(originStops.map(([sid, w]) => [sid, w]));
  const allTargetStops = new Set(stopToTargets.keys());

  const events = [];
  for (const [sid] of originStops) {
    for (const e of boardEvents(net, sid)) events.push([...e, sid]);
  }
  events.sort((a, b) => a[0] - b[0]);

  const directRows = {}, directSeen = {}, transferRows = {}, transferSeen = {};
  for (const tid of tids) {
    directRows[tid] = []; directSeen[tid] = new Set();
    transferRows[tid] = []; transferSeen[tid] = new Set();
  }
  const targetEntry = (tid, sid) => targets[tid].find(([s]) => s === sid);

  for (const [departMin, pi, pos, trip, originStop] of events) {
    const pattern = net.patterns[pi];

    // (a) 乗換なし: このバスに乗ったまま届く行き先(バス到着+徒歩が最小の停で降りる)
    const directHit = new Map();
    for (let later = pos + 1; later < pattern.stop_ids.length; later++) {
      const sid = pattern.stop_ids[later];
      for (const [tid, w] of stopToTargets.get(sid) ?? []) {
        const total = trip.arrivals[later] + rnd(w);
        if (!directHit.has(tid) || total < directHit.get(tid)[0]) directHit.set(tid, [total, sid, later]);
      }
    }
    for (const [tid, [arrival, alightStop, alightPos]] of directHit) {
      if (directSeen[tid].has(trip.trip_id)) continue;
      directSeen[tid].add(trip.trip_id);
      const leg = { kind: "ride", from_stop: originStop, to_stop: alightStop, depart: departMin,
                    arrive: trip.arrivals[alightPos], trip_id: trip.trip_id,
                    route_name: trip.route_name, prev: null };
      const [, alightWalk, alightPlace] = targetEntry(tid, alightStop);
      const bOpts = nearHome ? boardOptionsFor(net, pattern, trip, alightPos, nearHome) : null;
      let aOpts = null;
      if (alightHome) {
        const homeWalks = new Map(targets[tid].map(([s, w]) => [s, w]));
        aOpts = alightOptionsFor(net, pattern, trip, pos, homeWalks);
      }
      directRows[tid].push([departMin, makeItinerary(net, [leg], arrival, net.stops[originStop].name,
        alightPlace, originWalk.get(originStop), alightWalk, bOpts, aOpts)]);
    }

    // (b) 乗換1回: このバスで直通しない行き先だけ RAPTOR で調べる
    const need = tids.filter((t) => !directHit.has(t));
    if (!need.length) continue;
    // 読むのは行き先の停の答えだけなので、最後のラウンドはその停を通る路線だけ調べる
    // (raptor.js の targetStops。答えは変わらず、速さが数分の1になる)
    const result = search(net, new Map([[originStop, departMin]]), MAX_TRANSFERS_WEB,
                          net.config.min_transfer_min, allTargetStops);
    for (const tid of need) {
      let best = null;
      for (const [sid, w] of targets[tid]) {
        const r = result.get(sid);
        if (r === undefined) continue;
        const arrival = r.arrival + rnd(w);
        if (best === null || arrival < best[0]) best = [arrival, sid];
      }
      if (best === null) continue;
      const [arrival, alightStop] = best;
      const path = recon(result, alightStop);
      const rides = path.filter((l) => l.kind === "ride");
      if (rides.length < 2) continue;
      const key = `${rides[0].trip_id}\u0000${rides[1].trip_id}`;
      if (transferSeen[tid].has(key)) continue;
      transferSeen[tid].add(key);
      const [, alightWalk, alightPlace] = targetEntry(tid, alightStop);
      let bOpts = null;
      if (nearHome) {
        const leg1 = rides[0];
        const found = net.tripLookup.get(leg1.trip_id);
        if (found) {
          const [pat, t] = found;
          const boardPos = pat.stop_ids.indexOf(leg1.from_stop);
          const transferPos = pat.stop_ids.indexOf(leg1.to_stop, boardPos + 1);
          bOpts = boardOptionsFor(net, pat, t, transferPos, nearHome);
        }
      }
      transferRows[tid].push([departMin, makeItinerary(net, path, arrival,
        net.stops[path[0].from_stop].name, alightPlace, originWalk.get(originStop), alightWalk,
        bOpts, null)]);
    }
  }

  const out = {};
  for (const tid of tids) {
    const dRows = directRows[tid].slice().sort((a, b) => a[0] - b[0]);
    const dTimes = dRows.map(([dm]) => dm);
    const keptTransfer = transferRows[tid].slice().sort((a, b) => a[0] - b[0])
      .filter(([dm]) => !dTimes.some((dt) => Math.abs(dm - dt) <= GAP_FILL_WINDOW_MIN))
      .map(([, row]) => row);
    let merged = dRows.map(([, row]) => row).concat(collapseTransferAlternatives(keptTransfer));
    merged = keepUsefulBoards(merged);
    merged.sort((a, b) => (a.dep < b.dep ? -1 : a.dep > b.dep ? 1 : 0));
    out[tid] = merged;
  }
  return out;
}

// ===============================================================
// 便の間引き・乗り場の選び方(工場の同名関数と同じ)
// ===============================================================
function collapseTransferAlternatives(rows) {
  if (!rows.length) return rows;
  const groups = [[rows[0]]];
  for (const row of rows.slice(1)) {
    const g = groups[groups.length - 1];
    if (hmToMin(row.dep) - hmToMin(g[0].dep) <= TRANSFER_GROUP_WINDOW_MIN) g.push(row);
    else groups.push([row]);
  }
  return groups.map((group) => {
    let best = minBy(group, (r) => hmToMin(r.arr));
    if (group.length > 1) best = { ...best, alt_routes: group.length - 1 };
    return best;
  });
}

function leaveHomeMin(row) {
  return hmToMin(row.dep) - (row.board_walk_min || 0);
}

function dropSameBusEarlierRows(rows) {
  const lastBus = (r) => (r.transfer ? `${r.transfer.headsign2}\u0000${r.transfer.route2}`
                                     : `${r.headsign}\u0000${r.route}`);
  const leaves = (r) => {
    const opts = optsOr(r, [{ stop: r.board, dep: r.dep, walk_min: r.board_walk_min || 0 }]);
    const out = new Map();
    for (const o of opts) {
      const t = hmToMin(o.dep) - (o.walk_min || 0);
      out.set(o.stop, Math.max(out.has(o.stop) ? out.get(o.stop) : t, t));
    }
    return out;
  };
  const wait = (r) => (r.transfer && r.transfer.wait_min) || 0;

  const groups = new Map();
  rows.forEach((r, i) => {
    const key = `${r.arr}\u0000${r.alight}\u0000${lastBus(r)}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(i);
  });
  const drop = new Set();
  for (const idxs of groups.values()) {
    if (idxs.length < 2) continue;
    const lv = new Map(idxs.map((i) => [i, leaves(rows[i])]));
    for (const i of idxs) {
      for (const j of idxs) {
        if (i === j || drop.has(j)) continue;
        const li = lv.get(i), lj = lv.get(j);
        let all = true, strictly = false;
        for (const [b, t] of li) {
          if (!lj.has(b) || lj.get(b) < t) { all = false; break; }
          if (lj.get(b) > t) strictly = true;
        }
        if (!all) continue;
        if (strictly || wait(rows[j]) < wait(rows[i]) || (wait(rows[j]) === wait(rows[i]) && j < i)) {
          drop.add(i);
          break;
        }
      }
    }
  }
  return rows.filter((_, i) => !drop.has(i));
}

function frontierRows(rows) {
  const metrics = rows.map((r) => [leaveHomeMin(r), hmToMin(r.arr)]);
  return rows.filter((_, i) => {
    const [lr, ar] = metrics[i];
    return !metrics.some(([ls, as], j) => j !== i && ls >= lr && as <= ar && (ls > lr || as < ar));
  });
}

function keepUsefulBoards(rows) {
  if (rows.length <= 1) return rows;
  const useful = new Set(frontierRows(rows).map((r) => r.board));
  return rows.filter((r) => useful.has(r.board));
}

function slimToBoard(rows, kantanBoard) {
  const kept = [];
  for (const r of rows) {
    const opts = r.board_options;
    if (opts == null) {
      if (r.board === kantanBoard) kept.push(r);
      continue;
    }
    const match = opts.find((o) => o.stop === kantanBoard);
    if (!match) continue;
    const wait = r.transfer ? r.transfer.wait_min : 0;
    kept.push({ ...r, board: kantanBoard, board_ll: match._ll ?? null, dep: match.dep,
                board_walk_min: match.walk_min,
                ride_min: hmToMin(r.arr) - hmToMin(match.dep) - wait });
  }
  return kept;
}

function boardTypical(rows, board) {
  const v = [];
  for (const r of frontierRows(rows)) {
    const opts = optsOr(r, [{ stop: r.board, dep: r.dep, walk_min: r.board_walk_min || 0 }]);
    const o = opts.find((x) => x.stop === board);
    if (o) v.push(hmToMin(r.arr) - (hmToMin(o.dep) - (o.walk_min || 0)));
  }
  return v.length ? median(v) : null;
}

// pick_kantan_board。outbound: {ダイヤ種別: [便, ...]}(キーの順番も工場と同じにして渡す)
function pickKantanBoard(outbound) {
  const totals = new Map(), walk = new Map(), coverage = new Map();
  const served = new Set(Object.keys(outbound).filter((dt) => outbound[dt].length));
  const nTrips = new Map();
  for (const rows of Object.values(outbound)) {
    for (const r of rows) {
      const opts = optsOr(r, [{ stop: r.board }]);
      for (const b of new Set(opts.map((o) => o.stop))) nTrips.set(b, (nTrips.get(b) || 0) + 1);
    }
  }
  const add = (b, v, w, dt) => {
    if (!totals.has(b)) totals.set(b, []);
    totals.get(b).push(v);
    if (!walk.has(b)) walk.set(b, w);
    if (!coverage.has(b)) coverage.set(b, new Set());
    coverage.get(b).add(dt);
  };
  for (const [dt, rows] of Object.entries(outbound)) {
    for (const r of frontierRows(rows)) {
      const arr = hmToMin(r.arr);
      if (r.board_options && r.board_options.length) {
        for (const o of r.board_options) add(o.stop, arr - (hmToMin(o.dep) - o.walk_min), o.walk_min, dt);
      } else {
        add(r.board, arr - leaveHomeMin(r), r.board_walk_min || 0, dt);
      }
    }
  }
  if (!totals.size) return null;

  const full = [...totals.keys()].filter((b) => [...served].every((dt) => coverage.get(b).has(dt)));
  let cands = full.length ? full : [...totals.keys()];
  const most = Math.max(...cands.map((b) => nTrips.get(b) || 0));
  const nTypes = Math.max(1, served.size);
  const tooFew = (b) => {
    const n = nTrips.get(b) || 0;
    return n < most * KANTAN_MIN_TRIP_SHARE && n / nTypes < KANTAN_FREQUENT_TRIPS;
  };
  const enough = cands.filter((b) => !tooFew(b));
  if (enough.length) cands = enough;

  const typical = new Map(cands.map((b) => [b, median(totals.get(b))]));
  const fastest = Math.min(...typical.values());
  const eligible = cands.filter((b) => typical.get(b) <= fastest + KANTAN_SWITCH_GAIN_MIN);
  // min(eligible, key=(徒歩, 停名))。停名は Python と同じ文字コード順で比べる
  return eligible.reduce((a, b) => {
    const wa = walk.get(a), wb = walk.get(b);
    if (wa !== wb) return wb < wa ? b : a;
    return TT_ENGINE.cmpStr(b, a) < 0 ? b : a;
  });
}

// ===============================================================
// 1行き先ぶんのエントリ(build_entry)
// ===============================================================
// home: {lat, lon, name}(地区の代表点 or わが家)
// perDay: {ダイヤ種別: {outbound: [便...], inbound: [便...], homeBoard: [停, 徒歩分] or null}}
// cfg   : 配布ネットワークの config(徒歩の速さ・徒歩圏。工場の config.py と同じ値)
function buildEntry(home, facility, perDay, cfg) {
  let boardWalkMin = null;
  const outbound = {}, outboundAll = {}, inbound = {};
  let anyReachable = false;
  for (const dt of DAY_TYPES) {
    if (!perDay[dt]) continue;
    const d = perDay[dt];
    if (boardWalkMin === null && d.homeBoard) boardWalkMin = TT_ENGINE.pyRound(d.homeBoard[1]);
    const rowsOut = d.outbound || [];
    const rowsIn = d.inbound || [];
    inbound[dt] = rowsIn.length > 1 ? frontierRows(rowsIn) : rowsIn;
    outboundAll[dt] = rowsOut;
    outbound[dt] = dropSameBusEarlierRows(rowsOut);
    if (rowsOut.length || rowsIn.length) anyReachable = true;
  }
  if (!anyReachable) return { unreachable: true };

  const entry = { board_walk_min: boardWalkMin, outbound, inbound };
  const kantanBoard = pickKantanBoard(outboundAll);
  if (kantanBoard !== null) {
    entry.kantan_board = kantanBoard;
    const boards = {};
    for (const [dt, rows] of Object.entries(outbound)) {
      if (!rows.length) continue;
      let chosen = kantanBoard;
      const dayBest = pickKantanBoard({ [dt]: outboundAll[dt] });
      if (dayBest !== null && dayBest !== chosen) {
        const tCur = boardTypical(outboundAll[dt], chosen);
        const tDay = boardTypical(outboundAll[dt], dayBest);
        if (tCur === null || (tDay !== null && tCur - tDay > KANTAN_SWITCH_GAIN_MIN)) chosen = dayBest;
      }
      if (!slimToBoard(rows, chosen).length) {
        chosen = pickKantanBoard({ [dt]: outboundAll[dt] });
        if (chosen !== null && !slimToBoard(rows, chosen).length) chosen = null;
      }
      boards[dt] = chosen;
    }
    entry.kantan_boards = boards;
  }
  const directM = haversineM(home.lat, home.lon, facility.lat, facility.lon);
  if (directM <= cfg.max_walk_to_stop_m) {
    entry.direct_walk_min = TT_ENGINE.pyRound(walkMinutes({ config: cfg }, directM));
  }
  return entry;
}

// attach_stop_points: 便の座標(board_ll など)を、共通の座標表 pts への番号に置き換える
function attachStopPoints(to) {
  const pts = [], indexOf = new Map();
  const ref = (ll) => {
    if (ll == null) return null;
    const key = `${ll[0]},${ll[1]}`;
    if (!indexOf.has(key)) { indexOf.set(key, pts.length); pts.push([ll[0], ll[1]]); }
    return indexOf.get(key);
  };
  for (const entry of Object.values(to)) {
    for (const dir of ["outbound", "inbound"]) {
      for (const rows of Object.values(entry[dir] || {})) {
        for (const r of rows) {
          const b = ref(r.board_ll); delete r.board_ll;
          const a = ref(r.alight_ll); delete r.alight_ll;
          for (const key of ["board_options", "alight_options"]) {
            for (const o of r[key] || []) {
              const p = ref(o._ll); delete o._ll;
              if (p !== null) o.p = p;
            }
          }
          if (b !== null) r.bp = b;
          if (a !== null) r.ap = a;
          if (r.transfer) {
            const tp = ref(r.transfer._at_ll); delete r.transfer._at_ll;
            const op = ref(r.transfer._off_ll); delete r.transfer._off_ll;
            if (tp !== null) r.transfer.atp = tp;
            if (op !== null) r.transfer.offp = op;
          }
        }
      }
    }
  }
  return pts;
}

// ===============================================================
// 入口: わが家(または地区の代表点)から、行き先ごとのエントリを作る
// ===============================================================
// 便ID → [パターン, 便](乗換便の1本目の停の並びを引くのに使う)。ネットワークごとに1回
function prepareNetwork(net) {
  if (!net.tripLookup) {
    net.tripLookup = new Map();
    for (const p of net.patterns) for (const t of p.trips) net.tripLookup.set(t.trip_id, [p, t]);
  }
  return net;
}

// 1つのダイヤ種別について、家 → facilities(行き)と facilities → 家(帰り)を計算する。
// dirs: "outbound" だけにすると帰りを計算しない(画面2の目安用。帰りは画面3で選んだ施設だけ)
function computeDayType(net, home, facilities, dirs = ["outbound", "inbound"]) {
  prepareNetwork(net);
  const homeOrigins = buildOrigins(net, home.lat, home.lon, home.name);
  const res = { outbound: {}, inbound: {}, homeBoard: homeOrigins.length ? homeOrigins[0].slice(0, 2) : null };
  if (dirs.includes("outbound")) {
    const facilityTargets = {};
    for (const f of facilities) facilityTargets[f.id] = buildTargets(net, f.lat, f.lon, f.name);
    if (homeOrigins.length) {
      const nearHome = new Map(nearbyStops(net, home.lat, home.lon));
      res.outbound = scanFromOrigin(net, homeOrigins, facilityTargets, nearHome, false);
    }
  }
  if (dirs.includes("inbound")) {
    const homeTargets = buildTargets(net, home.lat, home.lon, home.name);
    for (const f of facilities) {
      const fo = buildOrigins(net, f.lat, f.lon, f.name);
      res.inbound[f.id] = fo.length ? scanFromOrigin(net, fo, { home: homeTargets }, null, true).home : [];
    }
  }
  return res;
}

// わが家から、ダイヤ種別ごとの「生の答え」(エントリにまとめる前の便の一覧)を計算する。
// nets: {ダイヤ種別: 展開済みネットワーク}
// restricted: 行き先専用のネットワーク(済生病院のシャトル入り等)。
//   [{facilities: [行き先id...], nets: {ダイヤ種別: 展開済みネットワーク}}]。
//   工場と同じく、その行き先への行き・帰りだけをこのネットワークで計算する。
//   乗り場までの徒歩(board_walk_min)は工場と同じく通常のネットワークの最寄り停で決める
// 戻り値: {ダイヤ種別: {outbound: {行き先id: [便]}, inbound: {行き先id: [便]}, homeBoard}}
function computeHomeRaw(nets, home, facilities, dirs = ["outbound", "inbound"], restricted = []) {
  const special = new Map();   // 行き先id → {ダイヤ種別: ネットワーク}
  for (const r of restricted) for (const fid of r.facilities) special.set(fid, r.nets);
  const normal = facilities.filter((f) => !special.has(f.id));
  const raw = {};
  for (const dt of DAY_TYPES) {
    if (!nets[dt]) continue;
    raw[dt] = computeDayType(nets[dt], home, normal, dirs);
    for (const f of facilities) {
      if (!special.has(f.id)) continue;
      const rNet = special.get(f.id)[dt];
      if (!rNet) throw new Error(`${f.id} の専用ネットワーク(${dt})がありません`);
      const r = computeDayType(rNet, home, [f], dirs);
      if (dirs.includes("outbound")) raw[dt].outbound[f.id] = r.outbound[f.id] || [];
      if (dirs.includes("inbound")) raw[dt].inbound[f.id] = r.inbound[f.id] || [];
    }
  }
  return raw;
}

// 生の答えを、地区ファイルと同じ {pts, to} にまとめる。
// 生の答えは書き換えない(画面2で計算した行きを画面3でも使い回すため。
// attachStopPoints は便の座標を番号に置き換えるので、まとめる前に写しを作る)
function assembleHomeTimetable(raw, home, facilities, cfg) {
  const to = {};
  for (const f of facilities) {
    const perDay = {};
    for (const dt of Object.keys(raw)) {
      const r = raw[dt];
      perDay[dt] = { outbound: r.outbound[f.id] || [], inbound: r.inbound[f.id] || [], homeBoard: r.homeBoard };
    }
    to[f.id] = JSON.parse(JSON.stringify(buildEntry(home, f, perDay, cfg)));
  }
  const pts = attachStopPoints(to);
  return { pts, to };
}

// まとめて計算する入口(照合スクリプトはこれで地区ファイルと突き合わせる)。戻り値は {pts, to}
function buildHomeTimetable(nets, home, facilities, dirs = ["outbound", "inbound"], restricted = []) {
  const raw = computeHomeRaw(nets, home, facilities, dirs, restricted);
  const cfg = nets[Object.keys(raw)[0]].config;
  return assembleHomeTimetable(raw, home, facilities, cfg);
}

const api = {
  buildHomeTimetable, computeHomeRaw, assembleHomeTimetable, computeDayType, buildEntry,
  attachStopPoints, prepareNetwork, nearbyStops, buildOrigins, buildTargets, scanFromOrigin,
  pickKantanBoard, dropSameBusEarlierRows, frontierRows, haversineM, DAY_TYPES,
};
if (typeof module !== "undefined" && module.exports) module.exports = api;
else root.TimetableEngine = api;
})(typeof self !== "undefined" ? self : globalThis);
