// -*- coding: utf-8 -*-
// しっかりモード本体(F5。docs/plan_final_sprint.md F5)。
// 対象: 家族・支援者。かんたんモードと同じ webapp/data/ のJSONを読むだけで、
// このJSも一切計算をしない(表示・並べ替え・絞り込みのみ)。
//
// かんたんモードとの違い:
//  - 全便の詳細(到着時刻・のりば・のりつぎ・別経路の本数=alt_routes)を表で見せる
//  - ダイヤ種別(平日/土曜/日祝)を自分で切り替えられる
//  - 「乗車バス停での絞り込み」= 地区JSONに入っている各便の乗車停を使った
//    クライアント側フィルタ(新規計算なし。plan_final_sprint.md §5の要点)

let districts = [];
let destinations = [];
let meta = null;
const timetableCache = {};

// 表示状態。boardFilter は方向ごとの「このバス停から乗る便だけ表示」(null=すべて)
const state = { did: null, fid: null, dayType: "weekday", boardFilter: { outbound: null, inbound: null } };

// ===============================================================
// 共通ヘルパー(かんたんモードと同じ流儀)
// ===============================================================
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// 段(ごぜん/ひる/ごご/よる)+12時間表記(第1部 make_pair_timetable.py の clock_text() 移植)
function timeWord(hm) {
  const [h0, m] = hm.split(":");
  const h = Number(h0);
  if (h >= 24) return `深夜${h - 24}:${m}`;
  if (h < 11) return `ごぜん${h}:${m}`;
  if (h < 13) return `ひる${h}:${m}`;
  if (h < 18) return `ごご${h - 12}:${m}`;
  return `よる${h - 12}:${m}`;
}

function hmToMin(hm) {
  const [h, m] = hm.split(":");
  return Number(h) * 60 + Number(m);
}

function platformText(p) {
  return String(p ?? "").normalize("NFKC").trim();
}

// 『◯◯行き』表記(すでに「行き/ゆき」で終わるheadsignには重ねない)
function headsignLabel(hs) {
  const s = String(hs ?? "").trim();
  return /(行き|ゆき)$/.test(s) ? `『${s}』` : `『${s}行き』`;
}

function dateJa(iso) {
  const [y, m, d] = String(iso).split("-").map(Number);
  return `${y}年${m}月${d}日`;
}

// きょうの日付キー(端末のローカル日付。toISOString()はUTCになり日本の夜に
// 日付がずれるので使わない — かんたんモードの dateKey() と同じ流儀)
function todayKey() {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

async function getTimetable(did) {
  if (!timetableCache[did]) {
    timetableCache[did] = await fetch(`../data/timetables/${did}.json`).then((r) => r.json());
  }
  return timetableCache[did];
}

function operatorOf(idx) {
  return Number.isInteger(idx) && Array.isArray(meta.operators) ? meta.operators[idx] : null;
}

// ===============================================================
// 選択UI(プルダウン・ダイヤ種別タブ)
// ===============================================================
function fillSelectors() {
  const dSel = document.getElementById("district-select");
  for (const city of ["山形市", "上山市"]) {
    const og = document.createElement("optgroup");
    og.label = city;
    districts.filter((d) => d.municipality === city).forEach((d) => {
      const opt = document.createElement("option");
      opt.value = d.id;
      opt.textContent = `${d.name}(${d.kana})`;
      og.appendChild(opt);
    });
    dSel.appendChild(og);
  }
  const fSel = document.getElementById("facility-select");
  const catLabel = { hospital: "病院", supermarket: "スーパー", town: "まちなか" };
  for (const cat of ["hospital", "supermarket", "town"]) {
    const og = document.createElement("optgroup");
    og.label = catLabel[cat];
    destinations.filter((f) => f.category === cat).forEach((f) => {
      const opt = document.createElement("option");
      opt.value = f.id;
      opt.textContent = f.name;
      og.appendChild(opt);
    });
    fSel.appendChild(og);
  }
  dSel.addEventListener("change", () => { location.hash = `${dSel.value}/${state.fid}`; });
  fSel.addEventListener("change", () => { location.hash = `${state.did}/${fSel.value}`; });

  document.querySelectorAll(".dt-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      state.dayType = tab.dataset.dt;
      state.boardFilter = { outbound: null, inbound: null };
      render();
    });
  });
  document.getElementById("print-btn").addEventListener("click", () => window.print());
}

// GPSで近い地区の候補を3つ出す(かんたんモード画面1と同じ考え方。
// 代表点との距離での並べ替えだけで、経路の計算はしない。
// 境界近くの誤判定に備えて自動で決めず候補から選んでもらう)
function distanceM(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const x = (lon2 - lon1) * Math.PI / 180 * Math.cos(((lat1 + lat2) / 2) * Math.PI / 180);
  const y = (lat2 - lat1) * Math.PI / 180;
  return Math.round(R * Math.sqrt(x * x + y * y));
}

function setupGeoButton() {
  const btn = document.getElementById("geo-btn");
  const result = document.getElementById("geo-result");
  if (!("geolocation" in navigator)) { btn.hidden = true; return; }
  btn.addEventListener("click", () => {
    result.textContent = "位置をしらべています…";
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const { latitude, longitude } = pos.coords;
        const near = districts
          .map((d) => ({ d, dist: distanceM(latitude, longitude, d.lat, d.lon) }))
          .sort((a, b) => a.dist - b.dist)
          .slice(0, 3);
        result.innerHTML = "";
        near.forEach(({ d, dist }) => {
          const b = document.createElement("button");
          b.type = "button";
          b.className = "board-chip";
          const km = dist < 950 ? `約${Math.round(dist / 100) * 100}m` : `約${(dist / 1000).toFixed(1)}km`;
          b.textContent = `${d.name}(${km})`;
          b.addEventListener("click", () => { location.hash = `${d.id}/${state.fid}`; });
          result.appendChild(b);
        });
      },
      () => { result.textContent = "位置情報がつかえませんでした。プルダウンからえらんでください"; },
      { timeout: 10000, maximumAge: 60000 }
    );
  });
}

function syncDayTypeTabs() {
  document.querySelectorAll(".dt-tab").forEach((t) => {
    t.setAttribute("aria-selected", String(t.dataset.dt === state.dayType));
  });
}

// ===============================================================
// 描画
// ===============================================================
async function render() {
  const district = districts.find((d) => d.id === state.did);
  const facility = destinations.find((f) => f.id === state.fid);
  if (!district || !facility) return;
  document.getElementById("district-select").value = state.did;
  document.getElementById("facility-select").value = state.fid;
  syncDayTypeTabs();

  document.getElementById("pair-title").textContent = `${district.name} → ${facility.name}`;

  const timetable = await getTimetable(state.did);
  const entry = timetable.to[state.fid];
  const content = document.getElementById("content");

  // きょうのダイヤ種別の注記(選んでいる種別が今日と違うときは注意を出す)
  const todayType = meta.date_table[todayKey()] || null;
  const dtName = meta.day_types[state.dayType];
  document.getElementById("daytype-note").textContent = todayType
    ? (todayType === state.dayType
        ? `「${dtName}」ダイヤを表示しています(きょうは${meta.day_types[todayType]}です)`
        : `※「${dtName}」ダイヤを表示中。きょうは「${meta.day_types[todayType]}」です`)
    : "※きょうはこの時刻表の有効期間外の日です";

  if (!entry || entry.unreachable) {
    content.innerHTML = '<p class="unreachable-note">この地区からこの行き先へは、バスでは行けません</p>';
    renderPhoneBox(district, []);
    document.getElementById("validity-note").textContent =
      `この時刻表は ${dateJa(meta.valid_until)} まで有効です`;
    return;
  }

  content.innerHTML = "";
  content.appendChild(directionSection("outbound", "行き", entry, district, facility));
  content.appendChild(directionSection("inbound", "帰り", entry, district, facility));

  document.getElementById("validity-note").textContent =
    `この時刻表は ${dateJa(meta.valid_until)} まで有効です`;
  renderPhoneBox(district, collectOperators(entry));
}

function directionSection(dir, label, entry, district, facility) {
  const rows = (entry[dir] && entry[dir][state.dayType]) || [];
  const sec = document.createElement("section");
  sec.className = `direction-block ${dir === "outbound" ? "outbound" : "return"}`;

  const fromTo = dir === "outbound"
    ? `${district.name} → ${facility.name}`
    : `${facility.name} → ${district.name}`;
  sec.innerHTML =
    `<div class="direction-header"><span class="direction-label">${label}</span>` +
    `<span class="direction-sub">${escapeHtml(fromTo)}</span></div>`;

  if (rows.length === 0) {
    sec.insertAdjacentHTML("beforeend", '<p class="no-service-note">この曜日の運行はありません</p>');
    return sec;
  }

  // 乗車バス停での絞り込み。行き(outbound)は board_options(同じバスが通る家の近くの
  // 実在の停)から実際の停を選べる(2026-07-08 開発者要望「具体的な停留所を選択したい」)。
  // 帰り(inbound)は乗り場が施設で1つなので従来どおり board で絞る。
  const isOutbound = dir === "outbound";
  const boardList = isOutbound
    ? [...new Set(rows.flatMap((r) =>
        (r.board_options && r.board_options.length ? r.board_options : [{ stop: r.board }])
          .map((o) => o.stop)))]
    : [...new Set(rows.map((r) => r.board))];
  const active = state.boardFilter[dir];
  if (boardList.length > 1) {
    const bf = document.createElement("div");
    bf.className = "board-filter no-print";
    bf.innerHTML = '<span class="board-filter-label">のるバス停で絞り込み:</span>';
    const chips = [["すべて", null], ...boardList.map((b) => [b, b])];
    chips.forEach(([text, value]) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "board-chip";
      chip.textContent = text;
      chip.setAttribute("aria-pressed", String(active === value));
      chip.addEventListener("click", () => {
        state.boardFilter[dir] = value;
        render();
      });
      bf.appendChild(chip);
    });
    sec.appendChild(bf);
  }

  // 選択に応じて表示行を作る。行きで実在停を選んだときは、その停の発車時刻・徒歩分に
  // つけ替える(同じバスなので到着は不変。乗車時間だけ計算し直す)
  let shown;
  if (active && isOutbound) {
    shown = [];
    for (const r of rows) {
      const opt = (r.board_options || []).find((o) => o.stop === active);
      if (!opt) continue;
      const wait = r.transfer ? r.transfer.wait_min : 0;
      shown.push({ ...r, board: active, dep: opt.dep, board_walk_min: opt.walk_min,
                   ride_min: hmToMin(r.arr) - hmToMin(opt.dep) - wait });
    }
    shown.sort((a, b) => hmToMin(a.dep) - hmToMin(b.dep));
  } else if (active) {
    shown = rows.filter((r) => r.board === active);
  } else {
    shown = rows;
  }

  // 詳細テーブル本体
  const hasPlatform = shown.some((r) => r.platform);
  const hasOp = Array.isArray(meta.operators) && shown.some((r) => Number.isInteger(r.op));
  const wrap = document.createElement("div");
  wrap.className = "tt-wrap";
  const head =
    "<tr><th>発</th><th>着</th>" +
    "<th>のるバス停</th>" + (hasPlatform ? "<th>のりば</th>" : "") +
    "<th>行き先表示(前面)</th><th>番号</th>" + (hasOp ? "<th>運行</th>" : "") +
    "<th>のりつぎ</th><th>おりるバス停</th><th>補足</th></tr>";
  const body = shown.map((r) => {
    let transferCell = "直通";
    if (r.transfer) {
      const t = r.transfer;
      const op2 = operatorOf(t.op2);
      transferCell =
        `「${escapeHtml(t.at)}」で のりかえ(待ち${t.wait_min}分)<br>` +
        `<span class="sub">→ ${escapeHtml(headsignLabel(t.headsign2))} 番号: ${escapeHtml(t.route2)}` +
        (op2 ? ` / ${escapeHtml(op2.name)}` : "") + "</span>";
    }
    const notes = [];
    // おりるバス停は実停名(列「おりるバス停」)。目的地まで歩くなら補足に徒歩分を出す
    if (r.alight_walk_min >= 1 && r.alight_place) {
      notes.push(`${escapeHtml(r.alight_place)}まで徒歩${r.alight_walk_min}分`);
    }
    if (r.alt_routes) notes.push(`ほかに同時間帯の経路が${r.alt_routes}本`);
    const op = operatorOf(r.op);
    return (
      "<tr>" +
      `<td class="dep">${timeWord(r.dep)}</td>` +
      `<td class="arr">${timeWord(r.arr)}</td>` +
      `<td>${escapeHtml(r.board)}</td>` +
      (hasPlatform ? `<td>${r.platform ? escapeHtml(platformText(r.platform)) + "番" : ""}</td>` : "") +
      `<td>${escapeHtml(headsignLabel(r.headsign))}</td>` +
      `<td class="num">${escapeHtml(r.route)}</td>` +
      (hasOp ? `<td>${op ? escapeHtml(op.name) : ""}</td>` : "") +
      `<td>${transferCell}</td>` +
      `<td>${escapeHtml(r.alight)}</td>` +
      `<td class="note">${notes.join("・")}</td>` +
      "</tr>"
    );
  }).join("");
  wrap.innerHTML = `<table class="tt">${head}${body}</table>`;
  sec.appendChild(wrap);
  return sec;
}

// ===============================================================
// 電話番号欄(かんたんモードと同じ3段構成: 運行主体/予約/市の窓口)
// ===============================================================
function collectOperators(entry) {
  if (!Array.isArray(meta.operators)) return [];
  const idx = new Set();
  for (const dir of ["outbound", "inbound"]) {
    for (const dt of ["weekday", "saturday", "sunday_holiday"]) {
      for (const r of (entry[dir] && entry[dir][dt]) || []) {
        if (Number.isInteger(r.op)) idx.add(r.op);
        if (r.transfer && Number.isInteger(r.transfer.op2)) idx.add(r.transfer.op2);
      }
    }
  }
  return [...idx].sort((a, b) => a - b).map((i) => meta.operators[i]).filter(Boolean);
}

function renderPhoneBox(district, operators) {
  const box = document.getElementById("phone-box");
  box.innerHTML = "";
  if (!district || !Array.isArray(meta.demand_phone)) return;

  const lines = [];
  const seenTel = new Set();
  for (const op of operators) {
    if (!op || !op.tel || seenTel.has(op.tel)) continue;
    seenTel.add(op.tel);
    const name = op.desk ? `${op.name}(${op.desk})` : op.name;
    lines.push({ label: "この時刻表のバス", name, tel: op.tel });
  }
  const demand = meta.demand_phone.find(
    (p) => Array.isArray(p.districts) && p.districts.includes(district.name)
  );
  if (demand) {
    seenTel.add(demand.tel);
    lines.push({ label: "予約して乗るバス", name: demand.name, tel: demand.tel });
  }
  const cityDesk = meta.demand_phone.find(
    (p) => typeof p.districts === "string" && p.districts.startsWith(district.municipality)
  );
  if (cityDesk && !seenTel.has(cityDesk.tel)) {
    lines.push({ label: "バス全般の相談", name: cityDesk.name, tel: cityDesk.tel });
  }

  box.innerHTML = lines
    .map(
      (l) =>
        `<div class="phone-line"><span class="phone-label">${escapeHtml(l.label)}</span>` +
        `<span class="phone-name">${escapeHtml(l.name)}</span>` +
        `<a class="phone-tel" href="tel:${escapeHtml(l.tel)}">☎ ${escapeHtml(l.tel)}</a></div>`
    )
    .join("");
}

// ===============================================================
// ルーティング(#地区ID/施設ID。かんたんモード画面3と同じ形式)
// ===============================================================
function parseHash() {
  const raw = location.hash.replace(/^#/, "");
  const [did, fid] = raw.split("/");
  return { did: did || null, fid: fid || null };
}

async function route() {
  const { did, fid } = parseHash();
  state.did = did || districts[0].id;
  state.fid = fid || destinations[0].id;
  state.boardFilter = { outbound: null, inbound: null };
  await render();
}

async function init() {
  [districts, destinations, meta] = await Promise.all([
    fetch("../data/districts.json").then((r) => r.json()),
    fetch("../data/destinations.json").then((r) => r.json()),
    fetch("../data/meta.json").then((r) => r.json()),
  ]);
  // 初期のダイヤ種別 = きょう(有効期間外なら平日)
  state.dayType = meta.date_table[todayKey()] || "weekday";
  document.getElementById("app").hidden = false;
  fillSelectors();
  setupGeoButton();
  window.addEventListener("hashchange", route);
  await route();
}

init();
