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
// 地区ごとの交通空白のようす(G5)。webapp/data/district_gap.json(任意)。
// 無ければ {} のままで、パネルは何も出さない(後方互換)。かんたんモードには出さない
let districtGap = {};
const timetableCache = {};

// ---------------- 対策1(広い地区): 索引データの遅延読み込み ----------------
// mesh_index.json  … 817メッシュの中心座標と地区ID(GPSの地区判定をポリゴン精度に)
// stops_index.json … 停留所名→座標(「いまの場所から約◯m」の正直表示)
// 無い環境(再生成前)では null になり、従来動作にフォールバックする
let meshIndexCache;
let stopsIndexCache;
let stopsIndex = null;   // render() が geoFix の鮮度を見て入れる

async function getMeshIndex() {
  if (meshIndexCache === undefined) {
    meshIndexCache = await fetch("../data/mesh_index.json")
      .then((r) => (r.ok ? r.json() : null)).catch(() => null);
  }
  return meshIndexCache;
}

async function getStopsIndex() {
  if (stopsIndexCache === undefined) {
    stopsIndexCache = await fetch("../data/stops_index.json")
      .then((r) => (r.ok ? r.json() : null)).catch(() => null);
  }
  return stopsIndexCache;
}

function geoFixFresh() {
  const g = state.geoFix;
  return g && Date.now() - g.at < 10 * 60 * 1000 ? g : null;
}

// 地区IDから地区を探す(サブ地区=親エントリの sub 配列も対象)
function findDistrict(did) {
  for (const d of districts) {
    if (d.id === did) return d;
    for (const s of d.sub || []) {
      if (s.id === did) return { ...s, municipality: d.municipality, parent: d };
    }
  }
  return null;
}

// 表示状態。boardFilter は方向ごとの「このバス停から乗る便だけ表示」(null=すべて)
const state = { did: null, fid: null, dayType: "weekday", boardFilter: { outbound: null, inbound: null },
                geoFix: null };   // 最後にGPSで測った位置 {lat, lon, at}

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

function minToHm(min) {
  return `${String(Math.floor(min / 60)).padStart(2, "0")}:${String(min % 60).padStart(2, "0")}`;
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
      // サブ地区(広い地区の分割。対策2)は親の下にインデントして並べる
      for (const sub of d.sub || []) {
        const so = document.createElement("option");
        so.value = sub.id;
        so.textContent = `　└ ${sub.name}(${sub.kana})`;
        og.appendChild(so);
      }
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

// 近い地区の候補。索引があれば「地区の最寄りメッシュまでの距離」(ポリゴン精度)、
// 無ければ従来の代表点距離で近い順に n 地区(初出のみ)
async function nearestDistricts(lat, lon, n) {
  const idx = await getMeshIndex();
  if (idx && Array.isArray(idx.meshes)) {
    const best = new Map();
    for (const [mlat, mlon, di] of idx.meshes) {
      const dist = distanceM(lat, lon, mlat, mlon);
      const id = idx.districts[di];
      if (!best.has(id) || dist < best.get(id)) best.set(id, dist);
    }
    return [...best.entries()].sort((a, b) => a[1] - b[1])
      .map(([id, dist]) => ({ d: findDistrict(id), dist }))
      .filter((x) => x.d).slice(0, n);
  }
  return districts.map((d) => ({ d, dist: distanceM(lat, lon, d.lat, d.lon) }))
    .sort((a, b) => a.dist - b.dist).slice(0, n);
}

function setupGeoButton() {
  const btn = document.getElementById("geo-btn");
  const result = document.getElementById("geo-result");
  if (!("geolocation" in navigator)) { btn.hidden = true; return; }
  btn.addEventListener("click", () => {
    result.textContent = "位置をしらべています…";
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        const { latitude, longitude } = pos.coords;
        state.geoFix = { lat: latitude, lon: longitude, at: Date.now() };
        const near = await nearestDistricts(latitude, longitude, 3);
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
  const district = findDistrict(state.did);
  const facility = destinations.find((f) => f.id === state.fid);
  if (!district || !facility) return;
  document.getElementById("district-select").value = state.did;
  document.getElementById("facility-select").value = state.fid;
  syncDayTypeTabs();

  document.getElementById("pair-title").textContent = `${district.name} → ${facility.name}`;
  renderGapPanel(district.parent || district);  // 交通空白の集計は地区単位(サブは親で引く)

  // GPSで測ったばかりの位置があれば、乗車停までの距離の正直表示に使う(対策1)
  stopsIndex = geoFixFresh() ? await getStopsIndex() : null;

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

// GPS測位が新しいときだけ、停留所までの直線距離を小さく添える(対策1・広い地区)。
// 800m(徒歩約17分の目安)超は「とおい」と明記して正直に伝える
function geoDistNote(stopName, dir) {
  const fix = geoFixFresh();
  if (dir !== "outbound" || !fix || !stopsIndex || !stopsIndex[stopName]) return "";
  const [slat, slon] = stopsIndex[stopName];
  const dist = distanceM(fix.lat, fix.lon, slat, slon);
  const word = dist < 950 ? `約${Math.round(dist / 100) * 100}m` : `約${(dist / 1000).toFixed(1)}km`;
  const far = dist > 800 ? "・とおい" : "";
  return `<br><span class="sub${far ? " far" : ""}">いまの場所から ${word}${far}</span>`;
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

  // バス停での絞り込み(2026-07-08 開発者要望「具体的な停留所を選択したい」)。
  //  行き(outbound): 乗る停を board_options(同じバスが通る家の近くの実在停)から選ぶ。
  //  帰り(inbound): 降りる停を alight_options(同じバスが家の近くで降りられる実在停)から選ぶ。
  const isOutbound = dir === "outbound";
  const optsKey = isOutbound ? "board_options" : "alight_options";
  const primaryKey = isOutbound ? "board" : "alight";
  const filterLabel = isOutbound ? "のるバス停で絞り込み:" : "おりるバス停で絞り込み:";
  const stopList = [...new Set(rows.flatMap((r) =>
    (r[optsKey] && r[optsKey].length ? r[optsKey] : [{ stop: r[primaryKey] }]).map((o) => o.stop)))];
  const active = state.boardFilter[dir];
  if (stopList.length > 1) {
    const bf = document.createElement("div");
    bf.className = "board-filter no-print";
    bf.innerHTML = `<span class="board-filter-label">${filterLabel}</span>`;
    const chips = [["すべて", null], ...stopList.map((b) => [b, b])];
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

  // 選択に応じて表示行を作る。実在停を選んだら、その停の時刻(行き=発車/帰り=到着)と
  // 徒歩分につけ替える(同じバスなので反対側は不変。乗車時間だけ計算し直す)
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
    // 帰り: 降りる停を選択。「着」は既定と同じ基準(=家に着く時刻=バス降車+徒歩)に
    // そろえる。同じ停を選んでも時刻が変わらないようにするため(o.arrはバス到着なので徒歩を足す)
    shown = [];
    for (const r of rows) {
      const opt = (r.alight_options || []).find((o) => o.stop === active);
      if (!opt) continue;
      const wait = r.transfer ? r.transfer.wait_min : 0;
      const homeArr = hmToMin(opt.arr) + opt.walk_min;
      shown.push({ ...r, alight: opt.stop, arr: minToHm(homeArr), alight_walk_min: opt.walk_min,
                   ride_min: homeArr - hmToMin(r.dep) - wait });
    }
    shown.sort((a, b) => hmToMin(a.arr) - hmToMin(b.arr));
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
      `<td>${escapeHtml(r.board)}${geoDistNote(r.board, dir)}</td>` +
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
// この地区の交通空白のようす(G5)。district_gap.json があるときだけ表示。
// しっかりモード=家族・支援者・行政向けなので、事実の数値で率直に示す
// (かんたんモード=高齢ご本人向けには出さない。開発者方針 2026-07-08)
// ===============================================================
function renderGapPanel(district) {
  const el = document.getElementById("gap-panel");
  if (!el) return;
  const g = districtGap[district.id];
  if (!g) { el.innerHTML = ""; el.hidden = true; return; }   // データ未生成なら何も出さない
  el.hidden = false;

  const pct = (x) => (x == null ? "—" : `${Math.round(x * 1000) / 10}%`);
  const num = (n) => Number(n).toLocaleString("ja-JP");
  const items = [
    `<div class="gap-item"><span class="gap-num">${num(g.population)}人</span>` +
      `<span class="gap-lbl">この地区の人口</span></div>`,
  ];
  if (g.aging_rate != null) {
    items.push(
      `<div class="gap-item"><span class="gap-num">${pct(g.aging_rate)}</span>` +
      `<span class="gap-lbl">高齢化率(65歳以上)</span></div>`);
  }
  items.push(
    `<div class="gap-item"><span class="gap-num">${pct(g.gap_ratio)}</span>` +
    `<span class="gap-lbl">交通空白の人口割合(${num(g.gap_population)}人)</span></div>`);

  // 状況の一言。隠れ空白 > 空白 > おおむね可 の順で出し分け
  let cls = "ok";
  let msg = "この地区は おおむね通院できます";
  if (g.has_hidden_gap) {
    cls = "warn";
    msg = `隠れ空白あり:時刻表上は行けても通院が成立しにくい区画が ${g.hidden_gap_mesh_count} ` +
          `(${num(g.hidden_gap_population)}人)`;
  } else if (g.has_gap) {
    cls = "gap";
    msg = `交通空白の区画が ${g.gap_mesh_count} あります`;
  }

  el.innerHTML =
    `<h3 class="gap-title">この地区の交通状況</h3>` +
    `<div class="gap-grid">${items.join("")}</div>` +
    `<p class="gap-badge ${cls}">${escapeHtml(msg)}</p>` +
    `<p class="gap-src">交通空白マップの分析(分析日 2026-06-10)より。` +
    `<a href="../map.html">地図で見る</a></p>`;
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
  // 交通空白のようす(任意)。ファイルが無い/未生成でも動くよう、失敗は握りつぶす
  districtGap = await fetch("../data/district_gap.json")
    .then((r) => (r.ok ? r.json() : {}))
    .catch(() => ({}));
  // 初期のダイヤ種別 = きょう(有効期間外なら平日)
  state.dayType = meta.date_table[todayKey()] || "weekday";
  document.getElementById("app").hidden = false;
  fillSelectors();
  setupGeoButton();
  window.addEventListener("hashchange", route);
  await route();
}

init();
