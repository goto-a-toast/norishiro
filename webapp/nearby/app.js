// -*- coding: utf-8 -*-
// 案D(ブラウザで計算する方式)の実験ページ。
//
// これまでの3つの顔は「地区の代表点 → 38施設」の答えを全部書き下したJSON(約55MB)を
// 表示するだけだった。そのため代表点から離れて住む人には最寄りのバス停が出せない
// (住民の3人に2人が該当。docs/plan_f10_stop_select.md §6.7)。
// このページは配布したネットワーク(3ダイヤ種別あわせて gzip後0.18MB)を読み、
// **利用者のいる場所そのもの**を出発点にして、端末の中でRAPTORを回す。
//
// ★計算そのものは webapp/engine/raptor.js(Python版 transit_core.py の移植)。
//   Python版と同じ答えを出すことは実データで自動照合している
//   (gap_map/verify_engine_parity.py。到着時刻18,747件・経路8,508件が完全一致)。
//   このファイルは「入力を作る」「結果を翻訳して見せる」だけで、探索の中身は持たない。

"use strict";

const state = { origin: null, net: null, dayType: null, meta: null, dests: [], cat: "hospital" };

// 測位の誤差の合格ライン(かんたん・しっかりと同じ考え方)。
// パソコンはIPアドレスから推定した位置を返すことがあり、数km〜数十kmずれる
const GEO_ACC_STOP_M = 300;

function $(id) { return document.getElementById(id); }
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// 2点間のおおよその距離(m)。Python版 haversine の簡易版(候補の絞り込み用)
function distM(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const x = (lon2 - lon1) * Math.PI / 180 * Math.cos(((lat1 + lat2) / 2) * Math.PI / 180);
  const y = (lat2 - lat1) * Math.PI / 180;
  return R * Math.sqrt(x * x + y * y);
}

// 徒歩分。定数はネットワークJSONに同梱されたPython側の値を使う(書き写さない)
function walkMinutes(meters) {
  const c = state.net.config;
  return (meters * c.walk_detour) / c.walk_speed_m_per_min;
}

function hm(min) {
  const m = ((min % (24 * 60)) + 24 * 60) % (24 * 60);
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

// 「ごぜん10:20」の表記(第1部PDF・かんたんモードと同じ段のことば)
function timeWord(min) {
  const h = Math.floor(min / 60), m = min % 60;
  const mm = String(m).padStart(2, "0");
  if (h >= 24) return `深夜${h - 24}:${mm}`;
  if (h < 11) return `ごぜん${h}:${mm}`;
  if (h < 13) return `ひる${h}:${mm}`;
  if (h < 18) return `ごご${h - 12}:${mm}`;
  return `よる${h - 12}:${mm}`;
}

// 全角英数字を半角にそろえる(「Ｎ５２・Ｃ２」→「N52・C2」)。工場側でも正規化して
// いるが、古い配布ファイルが残っていても画面が崩れないよう、表示のときにも通す
function nfkc(s) {
  return String(s ?? "").normalize("NFKC");
}

function headsignLabel(hs) {
  const s = String(hs ?? "").trim();
  if (!s) return "";
  return /(行き|ゆき)$/.test(s) ? `『${s}』` : `『${s}行き』`;
}

// ---------------- 出発地のまわりのバス停 ----------------
// 徒歩圏(config.max_walk_to_stop_m)にある停を全部拾い、「そこに立てる時刻」を作る。
// これが代表点方式との決定的な違い: 出発点が利用者の実際の位置になる
function nearbyStops(lat, lon, departMin) {
  const c = state.net.config;
  const init = new Map();
  const walks = new Map();
  const stops = state.net.stops;
  for (const key of Object.keys(stops)) {
    const s = stops[key];
    const d = distM(lat, lon, s.lat, s.lon);
    if (d > c.max_walk_to_stop_m) continue;
    const w = Math.round(walkMinutes(d));
    const idx = Number(key);
    init.set(idx, departMin + w);
    walks.set(idx, w);
  }
  return { init, walks };
}

// 行き先(施設)の徒歩圏にある停と、そこから施設までの徒歩分
function destinationStops(dest) {
  const c = state.net.config;
  const out = new Map();
  const stops = state.net.stops;
  for (const key of Object.keys(stops)) {
    const s = stops[key];
    const d = distM(dest.lat, dest.lon, s.lat, s.lon);
    if (d <= c.max_walk_to_stop_m) out.set(Number(key), Math.round(walkMinutes(d)));
  }
  return out;
}

// 出発時刻を少しずつ遅らせて、続けて乗れる便を何本か出す
function findTrips(lat, lon, dest, startMin, maxTrips = 4) {
  const destStops = destinationStops(dest);
  if (destStops.size === 0) return { trips: [], reason: "行き先の近くにバス停がありません" };

  // 同じバスを何度も出さないよう、便の並び(trip_idの組)をキーにしてまとめる。
  // 出発時刻を1分ずつ遅らせて探すと、同じバスでも「家を出る時刻」が少しずつ遅くなる
  // (=待たずに済む乗り方が見つかる)ので、後から見つかったものほど良い。
  // 上書きしていけば、各便について「いちばん遅く家を出られる乗り方」が残る
  const byTrip = new Map();
  let depart = startMin;
  for (let n = 0; n < 60 && byTrip.size <= maxTrips; n++) {
    const { init, walks } = nearbyStops(lat, lon, depart);
    if (init.size === 0) {
      return { trips: [], reason: "いまいる場所の徒歩圏にバス停がありません" };
    }

    const res = raptorSearch(state.net, init, 1, state.net.config.min_transfer_min);

    // 施設に「いちばん早く着く」停を選ぶ(バス到着+施設までの徒歩)
    let best = null;
    for (const [stopIdx, walk] of destStops) {
      const r = res.get(stopIdx);
      if (!r) continue;
      const total = r.arrival + walk;
      if (best === null || total < best.total) best = { total, stopIdx, walk, arrival: r.arrival };
    }
    if (best === null) break;

    const legs = reconstructPath(res, best.stopIdx);
    const rides = legs.filter((l) => l.kind === "ride");
    if (rides.length === 0) break;   // 歩きだけで着く場合はここでは扱わない

    const firstRide = rides[0];
    const boardWalk = walks.get(Number(firstRide.from_stop)) ?? 0;
    const homeDepart = firstRide.depart - boardWalk;
    byTrip.set(rides.map((r) => r.trip_id).join("|"),
               { legs, rides, best, homeDepart, boardWalk });

    depart = homeDepart + 1;   // 次はこれより1分あとに家を出る場合を探す
  }

  // 最後に見つかった便は、まだ「いちばん遅く家を出られる乗り方」に育っていない
  // 可能性があるので、1本余分に探しておいて手前までを確定ぶんとして出す
  const all = [...byTrip.values()];
  const trips = (all.length > maxTrips ? all.slice(0, maxTrips) : all)
    .sort((a, b) => a.homeDepart - b.homeDepart);
  return { trips, reason: trips.length ? null : "この時間からは行けませんでした" };
}

// ---------------- 表示 ----------------
function renderTrips(dest, result) {
  const panel = $("result-panel");
  const box = $("results");
  panel.hidden = false;
  box.innerHTML = "";
  $("result-note").textContent =
    `${state.meta.day_types[state.dayType]}ダイヤ / ${esc(dest.name)}へ`;

  if (!result.trips.length) {
    box.innerHTML = `<p class="warn">${esc(result.reason || "見つかりませんでした")}</p>`;
    return;
  }

  for (const t of result.trips) {
    const total = t.best.total - t.homeDepart;
    const steps = [];
    steps.push(`<li>いまいる場所から「${esc(state.net.stops[t.rides[0].from_stop].name)}」まで` +
               ` あるいて約${t.boardWalk}分（${timeWord(t.rides[0].depart)}発）</li>`);
    t.rides.forEach((r, i) => {
      const info = state.net.tripInfo[r.trip_id] || {};
      const op = Number.isInteger(info.op) ? (state.net.operators[info.op] || {}).name : null;
      steps.push(
        `<li>正面に <span class="headsign">${esc(headsignLabel(info.headsign))}</span> と出ているバスに のる` +
        `<span class="small">（${esc(nfkc(r.route_name || ""))}${op ? " / " + esc(op) : ""}）</span><br>` +
        `「${esc(state.net.stops[r.to_stop].name)}」で おりる（${timeWord(r.arrive)}着）</li>`);
      if (i < t.rides.length - 1) {
        const wait = t.rides[i + 1].depart - r.arrive;
        steps.push(`<li>のりかえ（${wait}分まち）</li>`);
      }
    });
    if (t.best.walk >= 1) {
      steps.push(`<li>そこから ${esc(dest.name)} まで あるいて約${t.best.walk}分` +
                 `（${timeWord(t.best.total)}着）</li>`);
    }
    box.insertAdjacentHTML("beforeend",
      `<div class="trip">
         <div class="trip-head">
           <span class="time">${timeWord(t.homeDepart)}</span> に家を出る →
           <span class="time">${timeWord(t.best.total)}</span> 着
           <span class="small">（ぜんぶで${total}分${t.rides.length > 1 ? " / のりかえ1回" : " / のりかえなし"}）</span>
         </div>
         <ol class="steps">${steps.join("")}</ol>
       </div>`);
  }
}

// ---------------- 出発地の決め方 ----------------
function setOrigin(lat, lon, label, note = "") {
  state.origin = { lat, lon, label };
  $("origin-result").innerHTML =
    `<b>出発地:</b> ${esc(label)}<span class="small">（${lat.toFixed(5)}, ${lon.toFixed(5)}）</span>` +
    (note ? `<br><span class="warn">${esc(note)}</span>` : "");
  $("search-btn").disabled = false;
}

function setupGeo() {
  $("geo-btn").addEventListener("click", () => {
    if (!("geolocation" in navigator)) {
      $("origin-result").innerHTML = '<span class="warn">この端末では位置情報が使えません</span>';
      return;
    }
    $("origin-result").textContent = "位置をしらべています…";
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const { latitude, longitude, accuracy } = pos.coords;
        // 誤差が大きい測位(パソコンのIP推定など)では、近いバス停を選び間違える
        const note = accuracy > GEO_ACC_STOP_M
          ? `※測位の誤差が約${Math.round(accuracy)}mあります。パソコンではおおよその場所しか`
            + `分からないことがあります（結果がずれている可能性があります）`
          : "";
        setOrigin(latitude, longitude, "いまいる場所", note);
      },
      () => { $("origin-result").innerHTML = '<span class="warn">位置情報が使えませんでした。住所でさがしてください</span>'; },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 });
  });
}

// 住所検索は国土地理院のAPI(無料・キー不要)。カルテと同じ使い方で、
// 送るのは住所の文字列だけ。結果の座標は端末の中だけで使う
async function searchAddress(q) {
  const url = "https://msearch.gsi.go.jp/address-search/AddressSearch?q=" + encodeURIComponent(q);
  const res = await fetch(url);
  if (!res.ok) throw new Error("検索に失敗しました");
  return res.json();
}

function setupAddress() {
  const run = async () => {
    const q = $("addr-input").value.trim();
    if (!q) return;
    $("origin-result").textContent = "住所をしらべています…";
    try {
      const cands = await searchAddress(q);
      if (!cands.length) {
        $("origin-result").innerHTML = '<span class="warn">住所が見つかりませんでした</span>';
        return;
      }
      $("origin-result").innerHTML = "<p>この中からえらんでください</p>";
      cands.slice(0, 5).forEach((c) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "btn";
        b.textContent = (c.properties && c.properties.title) || q;
        b.addEventListener("click", () => {
          const [lon, lat] = c.geometry.coordinates;
          setOrigin(lat, lon, b.textContent);
        });
        $("origin-result").appendChild(b);
      });
    } catch (e) {
      $("origin-result").innerHTML = '<span class="warn">住所の検索がうまくいきませんでした</span>';
    }
  };
  $("addr-btn").addEventListener("click", run);
  $("addr-input").addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
}

// ---------------- 行き先の一覧 ----------------
function renderDestOptions() {
  const sel = $("dest-select");
  sel.innerHTML = "";
  state.dests.filter((d) => d.category === state.cat).forEach((d) => {
    const o = document.createElement("option");
    o.value = d.id; o.textContent = d.name;
    sel.appendChild(o);
  });
}

// ---------------- 起動 ----------------
function todayKey(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

async function init() {
  const [meta, dests] = await Promise.all([
    fetch("../data/meta.json").then((r) => r.json()),
    fetch("../data/destinations.json").then((r) => r.json()),
  ]);
  state.meta = meta;
  state.dests = dests;

  const now = new Date();
  // きょうがダイヤ判定表(date_table)に無い日=有効期限を過ぎた日。黙って平日ダイヤを
  // 出すと「きょう乗れる」と誤解されるので、代用であることを画面に出す
  // (かんたん・しっかりモードのR7と同じ扱い)
  state.todayType = meta.date_table[todayKey(now)] || null;
  state.dayType = state.todayType || "weekday";

  const netJson = await fetch(`../data/network/${state.dayType}.json`).then((r) => {
    if (!r.ok) throw new Error("ネットワークデータがありません");
    return r.json();
  });
  state.net = inflateNetwork(netJson);

  $("time-input").value = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
  renderDestOptions();
  document.querySelectorAll(".cat-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      state.cat = tab.dataset.cat;
      document.querySelectorAll(".cat-tab").forEach((t) => t.setAttribute("aria-selected", String(t === tab)));
      renderDestOptions();
    });
  });
  setupGeo();
  setupAddress();

  $("search-btn").addEventListener("click", () => {
    const dest = state.dests.find((d) => d.id === $("dest-select").value);
    const [h, m] = $("time-input").value.split(":").map(Number);
    const result = findTrips(state.origin.lat, state.origin.lon, dest, h * 60 + m);
    renderTrips(dest, result);
  });

  const nStops = Object.keys(state.net.stops).length;
  const nTrips = state.net.patterns.reduce((a, p) => a + p.trips.length, 0);
  $("engine-note").innerHTML = state.todayType
    ? `きょうは「${esc(meta.day_types[state.dayType])}」ダイヤ / 停留所${nStops.toLocaleString()}・`
      + `便${nTrips.toLocaleString()}本をこの端末で探索しています`
      + `<span class="small">（この時刻表は ${esc(meta.valid_until)} まで有効）</span>`
    : `<span class="warn">※きょうはこの時刻表の対象外の日です（${esc(meta.valid_until)} まで有効）。`
      + `下の結果は「平日ダイヤ」で代用した目安で、きょう乗れる便ではありません。`
      + `市の窓口にお問い合わせください</span>`;
  $("app").hidden = false;
}

init().catch((e) => {
  document.body.insertAdjacentHTML("afterbegin",
    `<p style="padding:16px;color:#a83200">読み込みに失敗しました: ${esc(e.message)}<br>` +
    `<span style="font-size:13px">配布用のネットワークがまだ生成されていない可能性があります` +
    `（python3 gap_map/export_network.py を実行してください）</span></p>`);
});
