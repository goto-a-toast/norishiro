// -*- coding: utf-8 -*-
// かんたんモード本体。
// ★重要な設計方針(docs/plan_final_sprint.md §1): このJSは計算をしない。
// Python側(gap_map/export_web_data.py)が事前計算したJSON(../data/*.json)を
// 読んで表示するだけ。「今日のダイヤ種別」の判定もmeta.json の date_table を
// 引くだけで、祝日・お盆の判定ロジックはここには一切書かない。

let districts = [];
let destinations = [];
let meta = null;
const timetableCache = {};
const state = { city: "山形市", category: "hospital", did: null, fid: null };

// ===============================================================
// 時刻のヘルパー
// ===============================================================
function hmToMin(hm) {
  const [h, m] = hm.split(":").map(Number);
  return h * 60 + m;
}

function dateKey(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function dayTypeOf(d) {
  return meta.date_table[dateKey(d)] || null;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ===============================================================
// データ取得
// ===============================================================
async function getTimetable(did) {
  if (!timetableCache[did]) {
    timetableCache[did] = await fetch(`../data/timetables/${did}.json`).then((r) => r.json());
  }
  return timetableCache[did];
}

function firstBoardName(entry) {
  for (const dt of ["weekday", "saturday", "sunday_holiday"]) {
    const rows = entry.outbound[dt];
    if (rows && rows.length) return rows[0].board;
  }
  return null;
}

// ===============================================================
// 画面切り替え・ステップ表示・もどるボタン
// ===============================================================
function showScreen(n) {
  document.querySelectorAll(".screen").forEach((el) => { el.hidden = true; });
  document.getElementById(`screen${n}`).hidden = false;
  document.querySelectorAll(".step").forEach((el) => {
    el.classList.toggle("current", Number(el.dataset.step) === n);
  });
  document.getElementById("back-btn").hidden = n === 1;
}

// ===============================================================
// 画面1: 地区をえらぶ
// ===============================================================
function renderScreen1() {
  showScreen(1);
  const grid = document.getElementById("district-grid");
  grid.innerHTML = "";
  districts
    .filter((d) => d.municipality === state.city)
    .forEach((d) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "district-btn";
      btn.innerHTML = `${escapeHtml(d.name)}<span class="kana">${escapeHtml(d.kana)}</span>`;
      btn.addEventListener("click", () => { location.hash = d.id; });
      grid.appendChild(btn);
    });
}

// ===============================================================
// 画面2: いきたい場所をえらぶ
// ===============================================================
async function renderScreen2(did) {
  showScreen(2);
  const district = districts.find((d) => d.id === did);
  document.getElementById("s2-district-name").textContent = district ? district.name : "";
  const timetable = await getTimetable(did);
  renderFacilityList(timetable);
}

function bestOutboundMinutes(entry) {
  // 施設一覧の並べ替え用に、平日の直通・乗換をあわせた最短所要時間(分)を求める。
  // 平日に便が無ければ土曜・日祝も見る(表示用の目安なので曜日はこだわらない)
  let best = Infinity;
  for (const dt of ["weekday", "saturday", "sunday_holiday"]) {
    const rows = entry.outbound[dt] || [];
    for (const r of rows) {
      const t = hmToMin(r.arr) - hmToMin(r.dep);
      if (t < best) best = t;
    }
    if (isFinite(best)) break;
  }
  return best;
}

function renderFacilityList(timetable) {
  const list = document.getElementById("facility-list");
  list.innerHTML = "";

  const items = destinations
    .filter((f) => f.category === state.category)
    .map((f) => {
      const entry = timetable.to[f.id];
      const hasEntry = entry && !entry.unreachable;
      const minMin = hasEntry ? bestOutboundMinutes(entry) : Infinity;
      return { f, reachable: hasEntry && isFinite(minMin), minMin };
    });

  if (items.length === 0) {
    list.innerHTML = '<p class="no-facility-note">このカテゴリの行き先はありません</p>';
    return;
  }

  items.sort((a, b) => (a.reachable ? a.minMin : Infinity) - (b.reachable ? b.minMin : Infinity));

  items.forEach(({ f, reachable, minMin }) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "facility-btn" + (reachable ? "" : " disabled");
    btn.innerHTML =
      `<span class="facility-name">${escapeHtml(f.name)}</span>` +
      `<span class="facility-time">${reachable ? "バスで約" + minMin + "分" : "バスでは行けません"}</span>`;
    if (reachable) {
      btn.addEventListener("click", () => { location.hash = `${state.did}/${f.id}`; });
    } else {
      btn.disabled = true;
    }
    list.appendChild(btn);
  });
}

// ===============================================================
// 画面3: 時刻表(時計モード・行き/帰り・音声・印刷)
// ===============================================================
async function renderScreen3(did, fid) {
  showScreen(3);
  const district = districts.find((d) => d.id === did);
  const facility = destinations.find((f) => f.id === fid);
  document.getElementById("s3-district-name").textContent = district ? district.name : "";
  document.getElementById("s3-facility-name").textContent = facility ? facility.name : "";

  const timetable = await getTimetable(did);
  const entry = timetable.to[fid];

  if (!entry || entry.unreachable) {
    document.getElementById("clock-box").innerHTML =
      '<div class="clock-main">この行き先へはバスで行けません</div>';
    document.getElementById("board-walk-note").textContent = "";
    document.getElementById("outbound-table").innerHTML = "";
    document.getElementById("inbound-table").innerHTML = "";
    document.getElementById("day-type-note").textContent = "";
    document.getElementById("validity-note").textContent = "";
    document.getElementById("speak-btn").hidden = true;
    return;
  }

  const now = new Date();
  const dayType = dayTypeOf(now);

  renderClock(entry, dayType, now);

  const boardName = firstBoardName(entry) || "最寄り";
  document.getElementById("board-walk-note").textContent =
    entry.board_walk_min != null
      ? `「${boardName}」バス停から 歩いて約${entry.board_walk_min}分`
      : "";

  renderTimeTable("outbound-table", entry.outbound, dayType);
  renderTimeTable("inbound-table", entry.inbound, dayType);

  document.getElementById("day-type-note").textContent = dayType
    ? `※きょうは「${meta.day_types[dayType]}」ダイヤです(自動判定)`
    : "※本日はこの時刻表の有効期間外です";
  document.getElementById("validity-note").textContent =
    `この時刻表は ${meta.valid_until} まで有効です`;

  setupSpeakButton(entry, dayType, now);
}

function renderClock(entry, dayType, now) {
  const box = document.getElementById("clock-box");
  if (!dayType) {
    box.innerHTML = '<div class="clock-main">きょうのダイヤ情報がありません</div>';
    return;
  }
  const nowMin = now.getHours() * 60 + now.getMinutes();
  const rows = entry.outbound[dayType] || [];
  const next = rows.find((r) => hmToMin(r.dep) >= nowMin);

  if (next) {
    const wait = hmToMin(next.dep) - nowMin;
    box.innerHTML =
      `<div class="clock-main">つぎのバスは ${next.dep} (あと${wait}分)</div>` +
      `<div class="clock-sub">${escapeHtml(next.board)}バス停から</div>`;
    return;
  }

  // 本日の便が無ければ、翌日の始発を調べる(date_tableで翌日のダイヤ種別を引くだけ)
  const tomorrow = new Date(now.getTime() + 24 * 60 * 60 * 1000);
  const tDayType = dayTypeOf(tomorrow);
  const tRows = tDayType ? (entry.outbound[tDayType] || []) : [];
  box.innerHTML = tRows.length
    ? `<div class="clock-main">本日の便は終わりました</div>` +
      `<div class="clock-sub">あしたの始発は ${tRows[0].dep} です</div>`
    : '<div class="clock-main">本日の便は終わりました</div>';
}

function renderTimeTable(containerId, directionData, dayType) {
  const container = document.getElementById(containerId);
  container.innerHTML = "";

  if (!dayType) {
    container.innerHTML = '<p class="no-service-note">ダイヤ情報がありません</p>';
    return;
  }
  const rows = directionData[dayType] || [];
  if (rows.length === 0) {
    container.innerHTML = '<p class="no-service-note">本日の運行はありません</p>';
    return;
  }

  rows.forEach((r) => {
    const row = document.createElement("div");
    row.className = "time-row";

    let html =
      `<div class="time-main">` +
      `<span class="dep-time">${r.dep}</span>` +
      `<span class="arr-time">→ ${r.arr}着</span>` +
      `<span class="route-name">${escapeHtml(r.route)}</span>` +
      `</div>`;

    html += `<div class="stop-line">のる: ${escapeHtml(r.board)}`;
    if (r.platform) html += `<span class="platform-badge">${escapeHtml(r.platform)}番のりば</span>`;
    html += ` → おりる: ${escapeHtml(r.alight)}`;
    if (r.transfer) html += `<span class="transfer-badge">のりかえ1回</span>`;
    html += `</div>`;

    if (r.transfer) {
      html += `<div class="stop-line">「${escapeHtml(r.transfer.at)}」でのりかえ(${escapeHtml(r.transfer.route2)})</div>`;
    }
    if (r.alt_routes) {
      html += `<div class="alt-note">ほかに${r.alt_routes}通りのルートがあります</div>`;
    }

    row.innerHTML = html;
    container.appendChild(row);
  });
}

function setupSpeakButton(entry, dayType, now) {
  const btn = document.getElementById("speak-btn");
  if (!("speechSynthesis" in window)) {
    btn.hidden = true;   // 対応外端末ではボタンごと隠す(本体機能には影響しない)
    return;
  }
  btn.hidden = false;
  btn.onclick = () => {
    const nowMin = now.getHours() * 60 + now.getMinutes();
    const rows = dayType ? (entry.outbound[dayType] || []) : [];
    const next = rows.find((r) => hmToMin(r.dep) >= nowMin);
    const text = next
      ? `つぎのバスは${next.dep}です。${next.board}バス停から乗ってください。`
      : "本日の便は終わりました。";
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = "ja-JP";
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utter);
  };
}

// ===============================================================
// ルーティング(#地区ID/施設ID 形式。QRコードの飛び先にもなる)
// ===============================================================
function parseHash() {
  const raw = location.hash.replace(/^#/, "");
  if (!raw) return { did: null, fid: null };
  const [did, fid] = raw.split("/");
  return { did: did || null, fid: fid || null };
}

async function route() {
  const { did, fid } = parseHash();
  state.did = did;
  state.fid = fid;
  if (did && fid) {
    await renderScreen3(did, fid);
  } else if (did) {
    await renderScreen2(did);
  } else {
    renderScreen1();
  }
}

// ===============================================================
// 初期化
// ===============================================================
function wireStaticHandlers() {
  document.querySelectorAll(".city-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      state.city = tab.dataset.city;
      document.querySelectorAll(".city-tab").forEach((t) => t.setAttribute("aria-selected", String(t === tab)));
      renderScreen1();
    });
  });

  document.querySelectorAll(".cat-tab").forEach((tab) => {
    tab.addEventListener("click", async () => {
      state.category = tab.dataset.cat;
      document.querySelectorAll(".cat-tab").forEach((t) => t.setAttribute("aria-selected", String(t === tab)));
      const timetable = await getTimetable(state.did);
      renderFacilityList(timetable);
    });
  });

  document.getElementById("back-btn").addEventListener("click", () => {
    const { did, fid } = parseHash();
    location.hash = did && fid ? did : "";
  });

  document.getElementById("print-btn").addEventListener("click", () => window.print());
}

async function init() {
  [districts, destinations, meta] = await Promise.all([
    fetch("../data/districts.json").then((r) => r.json()),
    fetch("../data/destinations.json").then((r) => r.json()),
    fetch("../data/meta.json").then((r) => r.json()),
  ]);
  document.getElementById("app").hidden = false;
  wireStaticHandlers();
  window.addEventListener("hashchange", route);
  await route();
}

init();
