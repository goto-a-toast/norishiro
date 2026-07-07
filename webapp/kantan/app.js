// -*- coding: utf-8 -*-
// かんたんモード本体。
// ★重要な設計方針(docs/plan_final_sprint.md §1): このJSは計算をしない。
// Python側(gap_map/export_web_data.py)が事前計算したJSON(../data/*.json)を
// 読んで表示するだけ。「今日のダイヤ種別」の判定もmeta.json の date_table を
// 引くだけで、祝日・お盆の判定ロジックはここには一切書かない。
//
// ★画面3の表示ルールは docs/plan_f4_ui.md §1(翻訳ルールR1〜R8)が仕様。
// 画面に出してよいのは「バス停に立った利用者が自分の目と耳で確かめられる情報」だけ。
// 路線ID・経路数などの計算機の内部語は出さない。
// alt_routes キーは、かんたんモードでは読むこと自体を禁止(R3)。

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
  return h * 60 + m; // GTFSの深夜便「25:10」もそのまま分に直せる
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

// 段(だん)と12時間表記。第1部 make_pair_timetable.py の clock_text() の移植。
// 11:00〜12:59は「ごぜん11時/ごご0時」と迷いやすいので独立した「ひる」の段、
// 18:00以降は「ごご7:20」を「ごぜん7:22」と読み間違えやすいので「よる」の段。
// 24時以降の深夜便(GTFSの25:10表記)は「よる」の段に「深夜1:10」(plan_f4_ui.md R5)
function danOf(hm) {
  const [h0, m] = hm.split(":");
  const h = Number(h0);
  if (h >= 24) return { dan: "よる", disp: `深夜${h - 24}:${m}` };
  if (h < 11) return { dan: "ごぜん", disp: `${h}:${m}` };
  if (h < 13) return { dan: "ひる", disp: `${h}:${m}` };
  if (h < 18) return { dan: "ごご", disp: `${h - 12}:${m}` };
  return { dan: "よる", disp: `${h - 12}:${m}` };
}

// 「ごぜん10:20」のような、段のことばを添えた時刻表記(カードと音声の基本形)
function timeWord(hm) {
  const { dan, disp } = danOf(hm);
  return disp.startsWith("深夜") ? disp : dan + disp;
}

// 音声用の「ごぜん10時20分」形式(数字と記号をそのまま読ませない。R8)
function timeSpeech(hm) {
  const { dan, disp } = danOf(hm);
  const label = disp.startsWith("深夜") ? "深夜" : dan;
  const [h, m] = disp.replace("深夜", "").split(":").map(Number);
  return `${label}${h}時` + (m ? `${m}分` : "");
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ===============================================================
// 「翻訳」のヘルパー(docs/plan_f4_ui.md §1)
// ===============================================================

// R1: バスの正面表示(headsign)を『◯◯行き』の形にする。
// すでに「行き/ゆき」で終わっていたら重ねない
function headsignLabel(hs) {
  const s = String(hs ?? "").trim();
  if (!s) return "";
  return /(行き|ゆき)$/.test(s) ? `『${s}』` : `『${s}行き』`;
}

// R2: 系統の文字列から「確認用に出してよい表示」を作る。
// ・「N52」「Z80・C6」のような英数字コードなら → そのまま(照合用の脇役)
// ・「上山市市営バス 市内循環線」のような説明的な路線名なら → 原則出さない。
//   例外として「循環」を含む部分だけは向きの確認に有用なので出してよい
// 出せるものが無ければ null を返す(呼び出し側は行ごと非表示にする)
function routeCodeOf(route) {
  const s = String(route ?? "").trim();
  if (!s) return null;
  const parts = s.split("・").map((p) => p.trim()).filter(Boolean);
  if (parts.length && parts.every((p) => /^[A-Za-z0-9]{1,4}$/.test(p))) {
    return parts.join("・");
  }
  const loop = s.match(/(\S*循環\S*)/);
  return loop ? loop[1] : null;
}

// 「かくにん」の1行(小さく出す脇役)。コードが取れないときは空文字。
// 番号は途中で改行されると読み誤るので改行させない
function confirmLineHtml(route) {
  const code = routeCodeOf(route);
  return code
    ? `<span class="confirm-note">(かくにん) バスの番号: <span class="no-wrap">${escapeHtml(code)}</span></span>`
    : "";
}

// のりば番号の表示用。データには「５」のような全角数字が残っているので
// 表示のときに半角へそろえる(全角英数字は禁止語彙。plan_f4_ui.md §1)
function platformText(p) {
  return String(p ?? "").normalize("NFKC").trim();
}

// meta.valid_until の "2026-09-30" を「2026年9月30日」にする(表示のためだけの整形)
function dateJa(iso) {
  const [y, m, d] = String(iso).split("-").map(Number);
  return `${y}年${m}月${d}日`;
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
// 画面3: 時刻表(F4-2で全面改訂)
// 構成 = のりかたカード(時計モード一体)/行き・帰りの段組時刻チップ/
//        フッター(音声・印刷・ダイヤ注記・電話番号)
// ===============================================================

// 画面3のためだけの状態。renderScreen3のたびに作り直す
const s3 = {
  entry: null,       // 表示中の 地区→施設 のデータ
  district: null,
  facility: null,
  todayType: null,   // きょうの実際のダイヤ種別(有効期間外なら null)
  showType: null,    // 時刻表として表示しているダイヤ種別(有効期間外は平日で代用)
  sel: null,         // 選択中の便 { dir: "outbound"|"inbound", idx: 数字 }
  manual: false,     // 利用者が時刻チップを自分でえらんだか
  timer: null,       // 1分ごとの時計更新タイマー
};

function s3Rows(dir) {
  return (s3.entry && s3.entry[dir] && s3.entry[dir][s3.showType]) || [];
}

// 「つぎの便」= きょうのダイヤで、いまから乗れる最初の行きの便
function nextOutboundIdx(now) {
  if (!s3.todayType || s3.todayType !== s3.showType) return -1;
  const nowMin = now.getHours() * 60 + now.getMinutes();
  return s3Rows("outbound").findIndex((r) => hmToMin(r.dep) >= nowMin);
}

async function renderScreen3(did, fid) {
  showScreen(3);
  const district = districts.find((d) => d.id === did);
  const facility = destinations.find((f) => f.id === fid);
  document.getElementById("s3-district-name").textContent = district ? district.name : "";
  document.getElementById("s3-facility-name").textContent = facility ? facility.name : "";

  if (s3.timer) { clearInterval(s3.timer); s3.timer = null; }

  const timetable = await getTimetable(did);
  const entry = timetable.to[fid];

  // 行けない施設(画面2ではタップできないが、URL直叩きで来る場合がある)
  const dirBlocks = document.querySelectorAll("#screen3 .direction-block");
  if (!entry || entry.unreachable) {
    document.getElementById("ride-card").innerHTML =
      '<div class="card-main">この行き先へは バスで行けません</div>';
    document.getElementById("chip-hint").hidden = true;
    dirBlocks.forEach((el) => { el.hidden = true; }); // 空の行き/帰り枠は出さない
    document.getElementById("day-type-note").textContent = "";
    document.getElementById("validity-note").textContent = "";
    document.getElementById("speak-btn").hidden = true;
    renderPhoneBox(district);
    return;
  }
  dirBlocks.forEach((el) => { el.hidden = false; });

  const now = new Date();
  s3.entry = entry;
  s3.district = district;
  s3.facility = facility;
  s3.todayType = dayTypeOf(now);
  // 有効期間外の日も時刻表は出したままにする(R7)。表示は平日ダイヤで代用し、
  // 「対象外の日です」の注意書きを優先表示する
  s3.showType = s3.todayType || "weekday";
  s3.manual = false;

  // 初期選択 = つぎの便。本日の便が終わっていたら選択なし(カードに終了案内を出す)。
  // 有効期間外の日は「つぎの便」が決められないので、始発の便を選んだ状態にする
  // (「本日の便はおわりました」という誤った案内を出さないため)
  const nextIdx = nextOutboundIdx(now);
  if (nextIdx >= 0) {
    s3.sel = { dir: "outbound", idx: nextIdx };
  } else if (!s3.todayType && s3Rows("outbound").length > 0) {
    s3.sel = { dir: "outbound", idx: 0 };
    s3.manual = true; // 時計とは連動させない(「あと◯分」を出さない)
  } else {
    s3.sel = null;
  }

  document.getElementById("chip-hint").hidden = false;
  renderDirection("outbound");
  renderDirection("inbound");
  renderRideCard(now);
  updateChipSelection();

  // ダイヤ種別の注記(R7)。有効期間外の案内を優先する
  document.getElementById("day-type-note").textContent = s3.todayType
    ? `※きょうは「${meta.day_types[s3.todayType]}」ダイヤです(自動判定)`
    : "※きょうはこの時刻表の対象外の日です。市の窓口にお問い合わせください";
  document.getElementById("validity-note").textContent =
    `この時刻表は ${dateJa(meta.valid_until)} まで有効です`;

  renderPhoneBox(district);
  setupSpeakButton();

  // 時計モード: 1分ごとに「あと◯分」を更新する。
  // 利用者がチップをえらんでいない間は「つぎの便」も自動で進める
  s3.timer = setInterval(() => {
    const t = new Date();
    if (!s3.manual) {
      const idx = nextOutboundIdx(t);
      s3.sel = idx >= 0 ? { dir: "outbound", idx } : null;
      updateChipSelection();
    }
    renderRideCard(t);
  }, 30 * 1000);
}

// ---------------- のりかたカード ----------------
function renderRideCard(now) {
  const card = document.getElementById("ride-card");
  const ride = s3.sel ? s3Rows(s3.sel.dir)[s3.sel.idx] : null;

  // 見出し行(時計モード)
  let head = "";
  if (!ride) {
    // 本日の便はすべて終わった。あしたの始発を date_table から引くだけで案内する
    const tomorrow = new Date(now.getTime() + 24 * 60 * 60 * 1000);
    const tType = dayTypeOf(tomorrow);
    const tRows = tType ? (s3.entry.outbound[tType] || []) : [];
    head =
      `<div class="card-main">本日の便は おわりました</div>` +
      (tRows.length
        ? `<div class="card-sub">あしたの始発は ${timeWord(tRows[0].dep)} です</div>`
        : "");
    card.innerHTML = head;
    return;
  }

  const nowMin = now.getHours() * 60 + now.getMinutes();
  const isNext = !s3.manual && s3.sel.dir === "outbound";
  if (isNext) {
    const wait = hmToMin(ride.dep) - nowMin;
    head =
      `<div class="card-main">つぎのバスは <span class="card-time">${timeWord(ride.dep)}</span>` +
      `<span class="card-wait">(あと${wait}分)</span></div>`;
  } else {
    const dirWord = s3.sel.dir === "outbound" ? "行き" : "帰り";
    head =
      `<div class="card-main"><span class="card-time">${timeWord(ride.dep)}</span> 発の` +
      ` ${dirWord}のバス</div>`;
  }

  card.innerHTML = head + rideStepsHtml(ride, s3.sel.dir);
}

// ①歩く→②乗る→(のりかえ)→③降りる のステップを組み立てる(R1〜R6)
function rideStepsHtml(r, dir) {
  const marks = ["①", "②", "③", "④", "⑤"];
  const steps = [];

  // ① バス停まで歩く。徒歩分数は行き(自宅側)のときだけデータがある。
  // 0分(バス停がすぐそこ)のときは「約0分」という変な表示をしない
  let walk = "";
  if (dir === "outbound" && s3.entry.board_walk_min >= 1) {
    walk = ` <span class="walk-note">あるいて約${s3.entry.board_walk_min}分</span>`;
  }
  const platform = r.platform
    ? ` <span class="platform-badge">${escapeHtml(platformText(r.platform))}番のりば</span>`
    : "";
  steps.push(`「${escapeHtml(r.board)}」バス停へ${walk}${platform}`);

  // ② 正面表示(headsign)が主役。系統番号は小さな「かくにん」(R1・R2)
  steps.push(
    `正面に <span class="headsign">${escapeHtml(headsignLabel(r.headsign))}</span> と` +
    `でているバスに のる${confirmLineHtml(r.route)}`
  );

  // のりかえ(R4): どこで降りて、次に何行きに乗るか
  if (r.transfer) {
    steps.push(
      `「${escapeHtml(r.transfer.at)}」で おりて、<br>` +
      `<span class="headsign">${escapeHtml(headsignLabel(r.transfer.headsign2))}</span> に ` +
      `のりかえ(${r.transfer.wait_min}分 まち)${confirmLineHtml(r.transfer.route2)}`
    );
  }

  // 最後に降りる。ride_min は乗車時間の合計(乗換のときは2本ぶんの合計)
  const rideNote = r.transfer
    ? `(バスにのるのは 合計約${r.ride_min}分)`
    : `(のること約${r.ride_min}分)`;
  steps.push(`「${escapeHtml(r.alight)}」で おりる <span class="ride-note">${rideNote}</span>` +
    ` <span class="arr-note">${timeWord(r.arr)} 着</span>`);

  const lis = steps
    .map((s, i) => `<li><span class="step-mark">${marks[i]}</span><span class="step-body">${s}</span></li>`)
    .join("");
  return `<ol class="ride-steps">${lis}</ol>`;
}

// ---------------- 段組の時刻チップ ----------------
function renderDirection(dir) {
  const rows = s3Rows(dir);
  const table = document.getElementById(`${dir}-table`);
  const odpair = document.getElementById(`${dir}-odpair`);
  const hsNote = document.getElementById(`${dir}-headsign-note`);
  table.innerHTML = "";
  odpair.textContent = "";
  hsNote.textContent = "";

  if (rows.length === 0) {
    table.innerHTML = '<p class="no-service-note">この曜日の運行はありません</p>';
    return;
  }

  // 乗る停留所・降りる停留所が全便で同じなら「◯◯ → ◯◯」を見出しに添える
  const boards = [...new Set(rows.map((r) => r.board))];
  const alights = [...new Set(rows.map((r) => r.alight))];
  if (boards.length === 1 && alights.length === 1) {
    odpair.textContent = `${boards[0]} → ${alights[0]}`;
  }

  // headsignの一括表記(plan_f4_ui.md §3): 全便が同じ行き先表示なら一度だけ書き、
  // チップは時刻だけにする。混在するときはチップの下に小さく添える(6文字で省略)
  const headsigns = [...new Set(rows.map((r) => r.headsign))];
  const uniformHs = headsigns.length === 1;
  if (uniformHs) {
    hsNote.textContent = `どの時刻も ${headsignLabel(headsigns[0])} にのります`;
  }

  // 段(ごぜん/ひる/ごご/よる)ごとにチップを並べる
  const danOrder = ["ごぜん", "ひる", "ごご", "よる"];
  const groups = { "ごぜん": [], "ひる": [], "ごご": [], "よる": [] };
  rows.forEach((r, idx) => {
    groups[danOf(r.dep).dan].push({ r, idx });
  });

  danOrder.forEach((dan) => {
    if (groups[dan].length === 0) return;
    const rowEl = document.createElement("div");
    rowEl.className = "dan-row";
    const label = document.createElement("span");
    label.className = "dan-label";
    label.textContent = dan;
    rowEl.appendChild(label);

    const chips = document.createElement("div");
    chips.className = "chips";
    groups[dan].forEach(({ r, idx }) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "time-chip";
      chip.dataset.dir = dir;
      chip.dataset.idx = String(idx);
      chip.setAttribute("aria-pressed", "false");
      let inner = `<span class="chip-time">${danOf(r.dep).disp}</span>`;
      if (r.transfer) {
        // 乗換便の印。色だけに頼らず「※」の記号を併記する
        inner += `<span class="chip-mark" aria-label="のりかえ1回">※</span>`;
      }
      if (!uniformHs) {
        const hs = String(r.headsign);
        inner += `<span class="chip-headsign">${escapeHtml(hs.length > 6 ? hs.slice(0, 6) + "…" : hs)}</span>`;
      }
      chip.innerHTML = inner;
      chip.addEventListener("click", () => {
        s3.sel = { dir, idx };
        s3.manual = true;
        updateChipSelection();
        renderRideCard(new Date());
      });
      chips.appendChild(chip);
    });
    rowEl.appendChild(chips);
    table.appendChild(rowEl);
  });

  // 乗換便が1本でもあれば、※の意味の凡例を段組の下に出す
  if (rows.some((r) => r.transfer)) {
    const legend = document.createElement("p");
    legend.className = "chip-legend";
    legend.textContent = "※印 = のりかえ1回の便";
    table.appendChild(legend);
  }
}

// 選択中のチップに枠と aria-pressed を付け直す
function updateChipSelection() {
  document.querySelectorAll(".time-chip").forEach((chip) => {
    const on = s3.sel &&
      chip.dataset.dir === s3.sel.dir &&
      Number(chip.dataset.idx) === s3.sel.idx;
    chip.classList.toggle("selected", Boolean(on));
    chip.setAttribute("aria-pressed", String(Boolean(on)));
  });
}

// ---------------- 電話番号欄 ----------------
// meta.json の demand_phone を地区名で引く。該当地区(デマンド交通のある地区)なら
// 予約電話を、それ以外はその市の相談窓口を出す(plan_f4_ui.md §3-7)
function renderPhoneBox(district) {
  const box = document.getElementById("phone-box");
  box.innerHTML = "";
  if (!district || !Array.isArray(meta.demand_phone)) return;

  let entry = meta.demand_phone.find(
    (p) => Array.isArray(p.districts) && p.districts.includes(district.name)
  );
  if (!entry) {
    // 「山形市のその他全地区」のような市単位の窓口にフォールバック
    entry = meta.demand_phone.find(
      (p) => typeof p.districts === "string" && p.districts.startsWith(district.municipality)
    );
  }
  if (!entry) return;

  box.innerHTML =
    `<div class="phone-name">${escapeHtml(entry.name)}</div>` +
    `<a class="phone-tel" href="tel:${escapeHtml(entry.tel)}">☎ ${escapeHtml(entry.tel)}</a>`;
}

// ---------------- 音声 ----------------
// 読み上げは「翻訳後の文」だけ(R8)。系統コードとのりば番号は読まない。
// 時刻は「ごぜん10時20分」形式で組み立てる(数字+記号を読ませない)
function setupSpeakButton() {
  const btn = document.getElementById("speak-btn");
  if (!("speechSynthesis" in window)) {
    btn.hidden = true;   // 対応外端末ではボタンごと隠す(本体機能には影響しない)
    return;
  }
  btn.hidden = false;
  btn.onclick = () => {
    const now = new Date();
    const ride = s3.sel ? s3Rows(s3.sel.dir)[s3.sel.idx] : null;
    let text;
    if (!ride) {
      text = "本日の便は、おわりました。";
    } else {
      const parts = [];
      if (!s3.manual && s3.sel.dir === "outbound") {
        const wait = hmToMin(ride.dep) - (now.getHours() * 60 + now.getMinutes());
        parts.push(`つぎのバスは、${timeSpeech(ride.dep)}、あと${wait}分です。`);
      } else {
        parts.push(`えらんだバスは、${timeSpeech(ride.dep)}です。`);
      }
      const hsWord = String(ride.headsign).replace(/(行き|ゆき)$/, "");
      parts.push(`${ride.board}バス停から、${hsWord}行きに、のってください。`);
      if (s3.sel.dir === "outbound" && s3.entry.board_walk_min >= 1) {
        parts.push(`バス停までは、あるいて約${s3.entry.board_walk_min}分です。`);
      }
      if (ride.transfer) {
        const hs2Word = String(ride.transfer.headsign2).replace(/(行き|ゆき)$/, "");
        parts.push(`${ride.transfer.at}で おりて、${hs2Word}行きに、のりかえてください。`);
      }
      text = parts.join("");
    }
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
  // 画面3を離れるときは時計モードのタイマーを止める
  if (!(did && fid) && s3.timer) { clearInterval(s3.timer); s3.timer = null; }
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
