// -*- coding: utf-8 -*-
// 案D 段階3-2: 端末で作る時刻表(webapp/engine/timetable.js)が、工場(export_web_data.py)の
// 地区ファイル(webapp/data/timetables/*.json)と**完全に一致する**かを全件で突き合わせる。
//
// 地区の代表点を「わが家」として JS に計算させ、地区ファイルの全行き先・3ダイヤ・行き帰りの
// 全便・全項目(のりばの座標まで)を比べる。1件でも違えば、違った場所を出して失敗で終わる。
// docs/plan_stage3_kantan.md §5「3-2 移植と照合」の関門。
//
// 前提: 配布ネットワーク(webapp/data/network/)と地区ファイルが同じフィードから作られていること
// (どちらかだけ再生成したら、もう一方も再生成してから回す)。
//
// 使い方(プロジェクトルートから):
//   node gap_map/verify_timetable_parity.js            # 全地区・全行き先
//   node gap_map/verify_timetable_parity.js d24 d19    # 地区を指定
//   node gap_map/verify_timetable_parity.js --home d24 # その地区は「わが家1軒」の入口
//                                                      #  (buildHomeTimetable)でも照合する
//   node gap_map/verify_timetable_parity.js --points points.jsonl
//       代表点以外の地点(メッシュ中心)で照合する。points.jsonl は
//       gap_map/export_point_timetables.py が工場の関数で作る。わが家1軒の入口
//       (buildHomeTimetable)を使う
//
// 済生病院(f20)の行き帰りは、工場がシャトル入りの別ネットワークで計算している
// (restricted_feeds)。端末側も配布ネットワークの restricted に書かれた専用ファイルで
// 同じように計算して比べる(段階3-3。2026-10-07)。
//
// 速さのための工夫: 帰り(施設 → 家)は、工場と同じく「施設ごとに1回、全地区をまとめて」計算する。
// 便ごとの答えは行き先どうしで独立なので、1軒ずつ計算しても同じになる(--home で確かめられる)。

"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const DATA = path.join(ROOT, "webapp/data");
const { inflateNetwork } = require(path.join(ROOT, "webapp/engine/network.js"));
const TT = require(path.join(ROOT, "webapp/engine/timetable.js"));

function readJson(p) { return JSON.parse(fs.readFileSync(p, "utf8")); }

// 配布ネットワーク3ダイヤぶんと、行き先専用のネットワーク(シャトル入り等)を読む。
// restricted は buildHomeTimetable にそのまま渡せる形 [{facilities, nets: {ダイヤ種別: ネットワーク}}]
function loadNets() {
  const load = (name) => TT.prepareNetwork(inflateNetwork(readJson(path.join(DATA, "network", name))));
  const nets = {};
  for (const dt of TT.DAY_TYPES) nets[dt] = load(`${dt}.json`);
  const restricted = nets.weekday.restricted.map((_r, i) => ({
    facilities: nets.weekday.restricted[i].facilities,
    nets: Object.fromEntries(TT.DAY_TYPES.map((dt) => [dt, load(nets[dt].restricted[i].file)])),
  }));
  return { nets, restricted };
}

// 比べやすい形にそろえる: 座標表の番号(bp/ap/p)を座標に戻し、キーの順番を並べ替える。
// 工場は座標の無い便に bp を付けない。端末側の board_ll: null も「無い」にそろえる
function canonRow(r, pts) {
  const o = { ...r };
  if ("bp" in o) { o.board_ll = pts[o.bp]; delete o.bp; }
  if ("ap" in o) { o.alight_ll = pts[o.ap]; delete o.ap; }
  if (o.board_ll == null) delete o.board_ll;
  if (o.alight_ll == null) delete o.alight_ll;
  if (o.transfer) {
    const t = { ...o.transfer };
    if ("atp" in t) { t._at_ll = pts[t.atp]; delete t.atp; }
    if ("offp" in t) { t._off_ll = pts[t.offp]; delete t.offp; }
    if (t._at_ll == null) delete t._at_ll;
    if (t._off_ll == null) delete t._off_ll;
    o.transfer = t;
  }
  for (const key of ["board_options", "alight_options"]) {
    if (!Array.isArray(o[key])) continue;
    o[key] = o[key].map((x) => {
      const y = { ...x };
      if ("p" in y) { y._ll = pts[y.p]; delete y.p; }
      if (y._ll == null) delete y._ll;
      return y;
    });
  }
  return o;
}

function canonEntry(e, pts) {
  const o = { ...e };
  for (const dir of ["outbound", "inbound"]) {
    if (!o[dir]) continue;
    o[dir] = Object.fromEntries(Object.entries(o[dir]).map(([dt, rows]) => [dt, rows.map((r) => canonRow(r, pts))]));
  }
  return o;
}

function stable(v) {
  if (Array.isArray(v)) return v.map(stable);
  if (v && typeof v === "object") {
    return Object.fromEntries(Object.keys(v).sort().map((k) => [k, stable(v[k])]));
  }
  return v;
}

// 最初に食い違った場所を「outbound.weekday[3].dep」のような道筋で返す
function firstDiff(a, b, where = "") {
  if (JSON.stringify(a) === JSON.stringify(b)) return null;
  if (a && b && typeof a === "object" && typeof b === "object" && Array.isArray(a) === Array.isArray(b)) {
    if (Array.isArray(a) && a.length !== b.length) {
      // 便の数が違うときは、どの便が多い/少ないかを出す
      const ka = new Set(a.map((x) => JSON.stringify([x.dep, x.board, x.arr])));
      const kb = new Set(b.map((x) => JSON.stringify([x.dep, x.board, x.arr])));
      return `${where}: 便の数 工場${a.length} / 端末${b.length}` +
        ` 工場だけ=${[...ka].filter((k) => !kb.has(k)).slice(0, 3).join(" ")}` +
        ` 端末だけ=${[...kb].filter((k) => !ka.has(k)).slice(0, 3).join(" ")}`;
    }
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    for (const k of keys) {
      const d = firstDiff(a[k], b[k], `${where}${Array.isArray(a) ? `[${k}]` : `.${k}`}`);
      if (d) return d;
    }
  }
  return `${where}: 工場=${JSON.stringify(a)?.slice(0, 200)} / 端末=${JSON.stringify(b)?.slice(0, 200)}`;
}

function countRows(entry) {
  let n = 0;
  for (const dir of ["outbound", "inbound"]) for (const rows of Object.values(entry[dir] || {})) n += rows.length;
  return n;
}

// 地点ファイル(工場の関数で作った答え。1行に1地点)と、わが家1軒の入口の答えを比べる
async function verifyPoints(file) {
  const readline = require("readline");
  const facilities = readJson(path.join(DATA, "destinations.json"));
  const { nets, restricted } = loadNets();
  let nPoints = 0, nPairs = 0, nRows = 0, nBad = 0;
  const t0 = Date.now();
  const lines = readline.createInterface({ input: fs.createReadStream(file, "utf8"), crlfDelay: Infinity });
  for await (const line of lines) {
    if (!line.trim()) continue;
    const py = JSON.parse(line);
    nPoints++;
    const tt = TT.buildHomeTimetable(nets, py.home, facilities, undefined, restricted);
    for (const f of facilities) {
      const theirs = stable(canonEntry(py.to[f.id], py.pts));
      nPairs++;
      nRows += countRows(theirs);
      const diff = firstDiff(theirs, stable(canonEntry(tt.to[f.id], tt.pts)));
      if (diff) { nBad++; if (nBad <= 30) console.log(`  ${py.id}(${py.home.lat},${py.home.lon})→${f.id} ${diff}`); }
    }
    if (nPoints % 50 === 0) console.log(`  …${nPoints}地点(${((Date.now() - t0) / 1000).toFixed(0)}秒・食い違い${nBad}組)`);
  }
  console.log(`\n照合(地点): ${nPoints}地点 × 行き先${facilities.length} = ${nPairs}組` +
              ` / 工場の便 ${nRows.toLocaleString()}件 / ${((Date.now() - t0) / 1000).toFixed(0)}秒`);
  console.log(nBad ? `★食い違い ${nBad}組` : "→ すべて一致");
  process.exit(nBad ? 1 : 0);
}

function main() {
  const args = process.argv.slice(2);
  const pi = args.indexOf("--points");
  if (pi >= 0) return verifyPoints(args[pi + 1]);
  const homeMode = args.includes("--home");
  const wanted = new Set(args.filter((a) => !a.startsWith("--")));

  const districtsRaw = readJson(path.join(DATA, "districts.json"));
  // export_web_data.flatten_districts と同じ(サブ地区も同格の計算対象)
  const districts = districtsRaw.flatMap((d) => [d, ...((d.sub || []).map((s) => ({ ...s, parent_id: d.id })))]);
  const targetsD = wanted.size ? districts.filter((d) => wanted.has(d.id)) : districts;
  const facilities = readJson(path.join(DATA, "destinations.json"));
  const { nets, restricted } = loadNets();
  const cfg = nets.weekday.config;
  const special = new Set(restricted.flatMap((r) => r.facilities));

  // 1つのネットワークで、地区 → 行き先(行き)と 行き先 → 地区(帰り)をまとめて計算する
  // (工場の compute_day_type_schedules と同じまとめ方)
  function computeGroup(net, facs, outbound, inbound, homeBoard) {
    const facilityTargets = Object.fromEntries(facs.map((f) => [f.id, TT.buildTargets(net, f.lat, f.lon, f.name)]));
    const districtTargets = Object.fromEntries(targetsD.map((d) => [d.id, TT.buildTargets(net, d.lat, d.lon, d.name)]));
    for (const d of targetsD) {
      const origins = TT.buildOrigins(net, d.lat, d.lon, d.name);
      if (homeBoard) homeBoard[d.id] = origins.length ? origins[0].slice(0, 2) : null;
      const res = origins.length
        ? TT.scanFromOrigin(net, origins, facilityTargets, new Map(TT.nearbyStops(net, d.lat, d.lon)), false)
        : {};
      outbound[d.id] = { ...(outbound[d.id] || {}), ...res };
    }
    for (const f of facs) {
      const fo = TT.buildOrigins(net, f.lat, f.lon, f.name);
      inbound[f.id] = fo.length ? TT.scanFromOrigin(net, fo, districtTargets, null, true) : {};
    }
  }

  const perDayAll = {};
  for (const dt of TT.DAY_TYPES) {
    process.stdout.write(`[${dt}] 計算中…`);
    const t0 = Date.now();
    const outbound = {}, inbound = {}, homeBoard = {};
    // 乗り場までの徒歩(homeBoard)は、工場と同じく通常のネットワークで決める
    computeGroup(nets[dt], facilities.filter((f) => !special.has(f.id)), outbound, inbound, homeBoard);
    for (const r of restricted) {
      computeGroup(r.nets[dt], facilities.filter((f) => r.facilities.includes(f.id)), outbound, inbound, null);
    }
    perDayAll[dt] = { outbound, inbound, homeBoard };
    console.log(` ${((Date.now() - t0) / 1000).toFixed(1)}秒`);
  }

  let nPairs = 0, nRows = 0, nBad = 0;
  const bad = [];
  for (const d of targetsD) {
    const file = readJson(path.join(DATA, "timetables", `${d.id}.json`));
    for (const f of facilities) {
      const perDay = {};
      for (const dt of TT.DAY_TYPES) {
        const p = perDayAll[dt];
        perDay[dt] = { outbound: (p.outbound[d.id] || {})[f.id] || [], inbound: (p.inbound[f.id] || {})[d.id] || [],
                       homeBoard: p.homeBoard[d.id] };
      }
      const mine = stable(canonEntry(TT.buildEntry(d, f, perDay, cfg), []));
      const theirs = stable(canonEntry(file.to[f.id], file.pts));
      nPairs++;
      nRows += countRows(theirs);
      const diff = firstDiff(theirs, mine);
      if (diff) { nBad++; if (bad.length < 30) bad.push(`${d.id}→${f.id} ${diff}`); }
    }
  }

  console.log(`\n照合: 地区${targetsD.length} × 行き先${facilities.length} = ${nPairs}組 / 工場の便 ${nRows.toLocaleString()}件`);
  if (nBad) {
    console.log(`★食い違い ${nBad}組(先頭${bad.length}件):`);
    for (const b of bad) console.log("  " + b);
  } else {
    console.log("→ すべて一致");
  }

  // わが家1軒の入口(buildHomeTimetable = 画面が実際に呼ぶ関数)でも同じ答えになるか
  let homeBad = 0;
  if (homeMode) {
    for (const d of targetsD) {
      const t0 = Date.now();
      const tt = TT.buildHomeTimetable(nets, d, facilities, undefined, restricted);
      const file = readJson(path.join(DATA, "timetables", `${d.id}.json`));
      let bad1 = 0;
      for (const f of facilities) {
        const diff = firstDiff(stable(canonEntry(file.to[f.id], file.pts)), stable(canonEntry(tt.to[f.id], tt.pts)));
        if (diff) { bad1++; if (bad1 <= 3) console.log(`  [わが家] ${d.id}→${f.id} ${diff}`); }
      }
      homeBad += bad1;
      console.log(`[わが家の入口] ${d.id} ${d.name}: ${bad1 ? `★食い違い${bad1}組` : "一致"}` +
                  `(${((Date.now() - t0) / 1000).toFixed(1)}秒)`);
    }
  }
  process.exit(nBad || homeBad ? 1 : 0);
}

main();
