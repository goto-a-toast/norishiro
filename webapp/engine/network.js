// -*- coding: utf-8 -*-
// gap_map/export_network.py が書き出したネットワークJSONを、
// raptor.js が探索できる形(transit_core.Network と同じ構造)に展開する。
//
// ★添字の約束(ここが崩れるとPython版と答えがずれる):
//   停留所の添字は、元のstop_idを文字列順に並べて振ってある。Python版は同着の
//   タイブレークで stop_id の文字列順に走査するので、JS側は「添字の数値順」に
//   並べるだけで同じ順序になる。raptor.js の cmpStop がその前提で書かれている。

"use strict";

// 配布形式(export_network.py の serialize)の trips 1件の並び
const T_BASE = 0, T_ROUTE = 1, T_HEADSIGN = 2, T_OP = 3, T_TRIPID = 4, T_DEP = 5, T_ARR = 6;

function inflateNetwork(data) {
  if (data.format !== 1) {
    throw new Error(`知らない形式のネットワークです(format=${data.format})`);
  }
  const nStops = data.stops.length;

  // 停留所。添字をそのまま stop_id として使う(上記の約束)
  const stops = {};
  for (let i = 0; i < nStops; i++) {
    const [name, lat, lon, platform] = data.stops[i];
    stops[i] = { name, lat, lon, platform_code: platform, gtfs_stop_id: data.stop_ids?.[i] ?? null };
  }

  const patterns = [];
  const stopRoutes = {};
  data.patterns.forEach((p, patternIdx) => {
    const stopIds = p[0];
    const trips = p[1].map((t) => {
      const base = t[T_BASE];
      return {
        trip_id: t[T_TRIPID],
        route_name: data.routes[t[T_ROUTE]],
        headsign: data.headsigns[t[T_HEADSIGN]],
        op: t[T_OP],
        departures: t[T_DEP].map((d) => d + base),
        arrivals: t[T_ARR].map((a) => a + base),
      };
    });
    patterns.push({ stop_ids: stopIds, trips });
    stopIds.forEach((sid, pos) => {
      (stopRoutes[sid] ??= []).push([patternIdx, pos]);
    });
  });

  const footpaths = {};
  for (const [sid, pairs] of Object.entries(data.footpaths ?? {})) {
    footpaths[Number(sid)] = pairs.map(([o, w]) => [o, w]);
  }

  return { patterns, stop_routes: stopRoutes, stops, footpaths,
           operators: data.operators ?? [] };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { inflateNetwork };
}
