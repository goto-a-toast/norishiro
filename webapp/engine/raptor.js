// -*- coding: utf-8 -*-
// gap_map/transit_core.py(凍結資産のRAPTORエンジン)のJavaScript移植。
//
// ★この移植のいちばん大事な約束: **Python版と1件の食い違いもなく同じ答えを出すこと**。
// この作品は「検算できない計算結果を配らない」を設計原則にしている(plan_gap_map.md §1)。
// ブラウザで計算する以上、その計算がPython版と一致することを機械で確かめ続ける必要がある。
// 照合は gap_map/test_engine_parity.py が自動で行う(乱数で作ったネットワークと問い合わせを
// 両方のエンジンに与え、到着時刻と経路が完全一致することを確認する)。
//
// ★移植で特に注意した2点(ここを間違えると答えがずれる):
//   1. Python の round() は「0.5を偶数側に丸める」(銀行家丸め)。JSのMath.round()とは
//      違う(Math.round(4.5)=5 だが Python の round(4.5)=4)。徒歩分の丸めで効くので
//      pyRound() を用意した
//   2. 走査順の固定。Python版は sorted() で「パターン番号は数値順」「停留所IDは
//      文字列順」に走査する。同着のときにどちらの経路が勝つかがこの順序で決まるため、
//      JS側も同じ順序にそろえる(JSの既定のsortは文字列比較なので数値順は明示が必要)

"use strict";

// Python の round() と同じ丸め(0.5は偶数側)
function pyRound(x) {
  const f = Math.floor(x);
  const d = x - f;
  if (d > 0.5) return f + 1;
  if (d < 0.5) return f;
  return f % 2 === 0 ? f : f + 1;
}

// 停留所IDの並べ替え。Python版は sorted() で stop_id の文字列順に走査し、
// その順序が同着時のタイブレークを決めるので、JS側も同じ順序にそろえる。
// 配布形式(network.js)では stop_id が数値の添字になっているが、その添字は
// 「元のstop_idを文字列順に並べて振った」ものなので、数値順に並べれば同じ順序になる
function cmpStop(a, b) {
  if (typeof a === "number" && typeof b === "number") return a - b;
  return cmpStr(String(a), String(b));
}

// Python の文字列比較(コードポイント順)に合わせる。
// JSの既定比較はUTF-16の符号単位順で、BMP内(日本語を含む)では同じ結果になるが、
// 絵文字などの追加面の文字で食い違うため、コードポイントで比べる
function cmpStr(a, b) {
  const A = Array.from(a);
  const B = Array.from(b);
  const n = Math.min(A.length, B.length);
  for (let i = 0; i < n; i++) {
    const ca = A[i].codePointAt(0);
    const cb = B[i].codePointAt(0);
    if (ca !== cb) return ca - cb;
  }
  return A.length - B.length;
}

// パターン内の便は先頭停留所の出発時刻の昇順に並んでいる前提。
// 位置 pos から earliest_time 以降に乗れる、いちばん早い便を返す(無ければ null)
function earliestBoardableTrip(pattern, pos, earliestTime) {
  for (const trip of pattern.trips) {
    if (trip.departures[pos] >= earliestTime) return trip;
  }
  return null;
}

// 1つの停車パターンを停留所の並び順に走査する(RAPTORのコア処理)。
// roundUpdates は Map(stop_id → [到着時刻, leg])
function scanPattern(pattern, boardingTimes, boardingLeg, roundUpdates) {
  let currentTrip = null;
  let boardPos = null;
  let boardStopId = null;
  let boardPrevLeg = null;

  for (let pos = 0; pos < pattern.stop_ids.length; pos++) {
    const stopId = pattern.stop_ids[pos];

    // (1) この停留所で、もっと早い便に乗れないか確認する
    if (boardingTimes.has(stopId)) {
      const earliestTime = boardingTimes.get(stopId);
      const candidate = earliestBoardableTrip(pattern, pos, earliestTime);
      if (candidate !== null &&
          (currentTrip === null || candidate.departures[pos] < currentTrip.departures[boardPos])) {
        currentTrip = candidate;
        boardPos = pos;
        boardStopId = stopId;
        boardPrevLeg = boardingLeg.get(stopId) ?? null;
      }
    }

    // (2) 乗車中なら、この停留所への到着を記録する(乗った本人の停留所は除く)
    if (currentTrip !== null && pos > boardPos) {
      const arrival = currentTrip.arrivals[pos];
      const prev = roundUpdates.get(stopId);
      if (prev === undefined || arrival < prev[0]) {
        roundUpdates.set(stopId, [arrival, {
          kind: "ride",
          from_stop: boardStopId,
          to_stop: stopId,
          depart: currentTrip.departures[boardPos],
          arrive: arrival,
          trip_id: currentTrip.trip_id,
          route_name: currentTrip.route_name,
          prev: boardPrevLeg,
        }]);
      }
    }
  }
}

// 指定した初期停留所群から、全停留所への最早到着時刻を求める。
//   network      : {patterns, stop_routes, stops, footpaths}(Python版と同じ構造)
//   initialStops : Map または オブジェクト {stop_id: その停留所に立てる時刻(分)}
// 戻り値: Map(stop_id → {arrival, leg})
function raptorSearch(network, initialStops, maxTransfers = 0, minTransferMin = 3) {
  const init = initialStops instanceof Map ? initialStops : new Map(Object.entries(initialStops));

  const bestArrival = new Map(init);
  const bestLeg = new Map();
  for (const stopId of init.keys()) bestLeg.set(stopId, null);

  // このラウンドの乗車判断はすべて、ラウンド開始時点の状態だけを根拠にする
  let boardingTimes = new Map(init);
  let boardingLeg = new Map();
  for (const stopId of init.keys()) boardingLeg.set(stopId, null);

  for (let roundNo = 0; roundNo <= maxTransfers; roundNo++) {
    if (boardingTimes.size === 0) break;

    const touched = new Set();
    for (const stopId of boardingTimes.keys()) {
      for (const [patternIdx] of network.stop_routes[stopId] ?? []) touched.add(patternIdx);
    }

    const roundUpdates = new Map();
    for (const patternIdx of [...touched].sort((a, b) => a - b)) {   // 数値順(Python の sorted と同じ)
      scanPattern(network.patterns[patternIdx], boardingTimes, boardingLeg, roundUpdates);
    }

    const newlyByRide = [];
    for (const [stopId, [arrival, leg]] of roundUpdates) {
      if (arrival < (bestArrival.get(stopId) ?? Infinity)) {
        bestArrival.set(stopId, arrival);
        bestLeg.set(stopId, leg);
        newlyByRide.push(stopId);
      }
    }

    if (roundNo === maxTransfers || newlyByRide.length === 0) break;

    const nextBoardingTimes = new Map();
    const nextBoardingLeg = new Map();
    // 同着のタイブレークを実行ごとに変えないため、走査順を文字列順に固定する
    // (Python版の sorted(newly_by_ride) と同じ理由・同じ順序)
    for (const stopId of newlyByRide.slice().sort(cmpStop)) {
      const arrival = bestArrival.get(stopId);
      const rideLeg = bestLeg.get(stopId);

      // (a) 同じ停留所で別の便に乗り換える
      const tSame = arrival + minTransferMin;
      if (tSame < (nextBoardingTimes.get(stopId) ?? Infinity)) {
        nextBoardingTimes.set(stopId, tSame);
        nextBoardingLeg.set(stopId, rideLeg);
      }

      // (b) 徒歩で近くの停留所へ移動して乗り換える
      for (const [otherId, walkMin] of network.footpaths[stopId] ?? []) {
        const tWalk = arrival + minTransferMin + pyRound(walkMin);
        if (tWalk < (nextBoardingTimes.get(otherId) ?? Infinity)) {
          const walkLeg = {
            kind: "walk", from_stop: stopId, to_stop: otherId,
            depart: arrival, arrive: tWalk,
            trip_id: null, route_name: null, prev: rideLeg,
          };
          nextBoardingTimes.set(otherId, tWalk);
          nextBoardingLeg.set(otherId, walkLeg);
          // 徒歩そのものが最速到着になることもあるので反映する
          if (tWalk < (bestArrival.get(otherId) ?? Infinity)) {
            bestArrival.set(otherId, tWalk);
            bestLeg.set(otherId, walkLeg);
          }
        }
      }
    }

    boardingTimes = nextBoardingTimes;
    boardingLeg = nextBoardingLeg;
  }

  const out = new Map();
  for (const [stopId, arrival] of bestArrival) {
    out.set(stopId, { arrival, leg: bestLeg.get(stopId) ?? null });
  }
  return out;
}

// 結果から、指定した停留所までの経路(出発→到着の順)を復元する
function reconstructPath(result, destStopId) {
  const entry = result.get(destStopId);
  if (entry === undefined) return [];
  const legs = [];
  let leg = entry.leg;
  while (leg !== null && leg !== undefined) {
    legs.push(leg);
    leg = leg.prev;
  }
  legs.reverse();
  return legs;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { raptorSearch, reconstructPath, pyRound, cmpStr, cmpStop };
}
