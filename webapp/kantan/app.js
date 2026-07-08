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
// ---------------- GPSで近い地区をさがす(画面1) ----------------
// 地区の「代表点」(districts.json の lat/lon = 人口最大メッシュの中心)との
// 直線距離で近い順に3地区を候補として出し、利用者に選んでもらう。
// 代表点方式なので地区境界の近くでは隣の地区が上に来ることがある——
// だから自動で決めず、必ず「候補から選ぶ」形にする(誤判定への保険)。
// ※これは表示のための距離の並べ替えだけで、経路の計算はしない(設計原則の範囲内)

// 2点間のおおよその距離(m)。ヒュベニではなく簡易式で十分(候補の並べ替え用)
function distanceM(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const x = (lon2 - lon1) * Math.PI / 180 * Math.cos(((lat1 + lat2) / 2) * Math.PI / 180);
  const y = (lat2 - lat1) * Math.PI / 180;
  return Math.round(R * Math.sqrt(x * x + y * y));
}

function distanceWord(m) {
  return m < 950 ? `約${Math.round(m / 100) * 100}m` : `約${(m / 1000).toFixed(1)}km`;
}

function setupGeoButton() {
  const btn = document.getElementById("geo-btn");
  const result = document.getElementById("geo-result");
  if (!("geolocation" in navigator)) {
    btn.hidden = true;   // 使えない端末ではボタンごと出さない(一覧選択で完結)
    return;
  }
  btn.addEventListener("click", () => {
    result.textContent = "位置をしらべています…";
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const { latitude, longitude } = pos.coords;
        const near = districts
          .map((d) => ({ d, dist: distanceM(latitude, longitude, d.lat, d.lon) }))
          .sort((a, b) => a.dist - b.dist)
          .slice(0, 3);
        result.innerHTML = '<p class="geo-note">ちかい じゅんに ならべました。おすまいの地区をえらんでください</p>';
        near.forEach(({ d, dist }) => {
          const b = document.createElement("button");
          b.type = "button";
          b.className = "district-btn geo-candidate";
          b.innerHTML =
            `${escapeHtml(d.name)}<span class="kana">${escapeHtml(d.kana)} ・ ${escapeHtml(distanceWord(dist))}</span>`;
          b.addEventListener("click", () => { location.hash = d.id; });
          result.appendChild(b);
        });
      },
      () => {
        result.textContent = "位置情報が つかえませんでした。下の一覧から えらんでください";
      },
      { timeout: 10000, maximumAge: 60000 }
    );
  });
}

// 市タブの見た目を state.city に合わせる(画面3から「もどる」で戻ったとき、
// 表示中だった地区の市に自動で合わせるため。plan_f4_ui.md §3 画面1)
function syncCityTabs() {
  document.querySelectorAll(".city-tab").forEach((t) => {
    t.setAttribute("aria-selected", String(t.dataset.city === state.city));
  });
}

function renderScreen1() {
  showScreen(1);
  syncCityTabs();
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

// かんたんモードが「行き」で実際に見せる便(=一番いい乗り場 kantan_board の便)だけを返す。
// 画面2の所要時間・のりかえ判定も、画面3で見せる停とそろえるためにこれを使う
function kantanOutbound(entry, dt) {
  const rows = (entry.outbound && entry.outbound[dt]) || [];
  return entry.kantan_board ? rows.filter((r) => r.board === entry.kantan_board) : rows;
}

function bestOutboundMinutes(entry) {
  // 施設一覧の並べ替え用に、平日の直通・乗換をあわせた最短所要時間(分)を求める。
  // 平日に便が無ければ土曜・日祝も見る(表示用の目安なので曜日はこだわらない)
  let best = Infinity;
  for (const dt of ["weekday", "saturday", "sunday_holiday"]) {
    const rows = kantanOutbound(entry, dt);
    for (const r of rows) {
      const t = hmToMin(r.arr) - hmToMin(r.dep);
      if (t < best) best = t;
    }
    if (isFinite(best)) break;
  }
  return best;
}

// 「のりかえなし/のりかえ1回」の判定(plan_f4_ui.md §3 画面2)。
// 代表ダイヤ(平日→無ければ土曜→日祝)に直通の便が1本でもあれば「のりかえなし」、
// 全便乗換なら「のりかえ1回」。判定といっても JSON を見るだけで計算はしない
function transferNoteOf(entry) {
  for (const dt of ["weekday", "saturday", "sunday_holiday"]) {
    const rows = kantanOutbound(entry, dt);
    if (rows.length === 0) continue;
    return rows.some((r) => !r.transfer) ? "のりかえなし" : "のりかえ1回";
  }
  return "";
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
    if (reachable) {
      const note = transferNoteOf(timetable.to[f.id]);
      btn.innerHTML =
        `<span class="facility-name">${escapeHtml(f.name)}</span>` +
        `<span class="facility-meta"><span class="facility-time">バスで約${minMin}分</span>` +
        (note ? `<span class="facility-transfer${note === "のりかえ1回" ? " has-transfer" : ""}">${note}</span>` : "") +
        `</span>`;
      btn.addEventListener("click", () => { location.hash = `${state.did}/${f.id}`; });
    } else {
      btn.innerHTML =
        `<span class="facility-name">${escapeHtml(f.name)}</span>` +
        `<span class="facility-meta"><span class="facility-time">バスでは行けません</span></span>`;
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

// かんたんモードは行き先ごとに「一番いい乗り場」1つだけを見せる(見慣れないバス停を
// 混ぜない。設計C)。データ工場が entry.kantan_board にその停名を入れているので、
// 行き(outbound)はその停の便だけに絞る。帰り(inbound)は乗り場欄が施設名で統一
// されているので絞り込まない。kantan_board が無い古いデータでは全便を出す(後方互換)
function rowsFor(dir, showType) {
  let rows = (s3.entry && s3.entry[dir] && s3.entry[dir][showType]) || [];
  if (dir === "outbound" && s3.entry && s3.entry.kantan_board) {
    rows = rows.filter((r) => r.board === s3.entry.kantan_board);
  }
  return rows;
}

function s3Rows(dir) {
  return rowsFor(dir, s3.showType);
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

  renderPhoneBox(district, collectOperators());
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
    const tRows = tType ? rowsFor("outbound", tType) : [];
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
  // 0分(バス停がすぐそこ)のときは「約0分」という変な表示をしない。
  // r.board_walk_min はこの便が実際に使う乗車停留所までの徒歩分(便によって
  // 乗る停留所が変わることがあるため、地区共通の値ではなく便ごとの値を使う)
  let walk = "";
  if (dir === "outbound" && r.board_walk_min >= 1) {
    walk = ` <span class="walk-note">あるいて約${r.board_walk_min}分</span>`;
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
  // r.alight は実際に降りるバス停名(標識・車内アナウンスと照合できる)。
  // r.alight_place(施設名/地区名)まで歩く分を添えて「どこで降りればよいか」を明示する。
  // alight_walk_min が無い/0の古いデータでは目的地名だけ添える(後方互換)
  let placeNote = "";
  if (r.alight_place) {
    placeNote = r.alight_walk_min >= 1
      ? ` <span class="walk-note">(${escapeHtml(r.alight_place)}まで あるいて約${r.alight_walk_min}分)</span>`
      : ` <span class="walk-note">(${escapeHtml(r.alight_place)}のすぐ近く)</span>`;
  }
  steps.push(`「${escapeHtml(r.alight)}」で おりる${placeNote} <span class="ride-note">${rideNote}</span>` +
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

  // 乗る停留所 → 目的地 を見出しに添える。降車側は「目的地(施設名/地区名)」を出す
  // (実際の降車停 r.alight は便ごとに変わりうるので、見出しは安定した目的地名を使い、
  //  どの停で降りるかは各便のステップ③で実停名を見せる)
  const boards = [...new Set(rows.map((r) => r.board))];
  const places = [...new Set(rows.map((r) => r.alight_place || r.alight))];
  if (boards.length === 1 && places.length === 1) {
    odpair.textContent = `${boards[0]} → ${places[0]}`;
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
// 3種類の連絡先を出し分ける(2026-07-07 開発者指示による改訂):
//  1. この時刻表のバスの運行主体(便レコードの op / op2 → meta.operators。
//     山交バスの便に市役所の番号だけが出る不自然さを防ぐ。電話が確認済みの
//     運行主体のみ表示。opがまだ無い古いデータでは自動的に出ない=後方互換)
//  2. よやくして のるバス(デマンド交通。対象地区のみ)
//  3. 市のバス相談窓口(常に出す。ただし同じ番号が上に出ていれば重複させない)

// 表示中の時刻表(行き・帰りの全ダイヤ種別)に出てくる運行主体を集める
function collectOperators() {
  if (!Array.isArray(meta.operators)) return [];
  const idx = new Set();
  for (const dir of ["outbound", "inbound"]) {
    for (const dt of ["weekday", "saturday", "sunday_holiday"]) {
      // 行きは かんたんモードが実際に見せる停の便だけを見る(見せない停の運行主体は出さない)
      for (const r of rowsFor(dir, dt)) {
        if (Number.isInteger(r.op)) idx.add(r.op);
        if (r.transfer && Number.isInteger(r.transfer.op2)) idx.add(r.transfer.op2);
      }
    }
  }
  return [...idx].sort((a, b) => a - b).map((i) => meta.operators[i]).filter(Boolean);
}

function renderPhoneBox(district, operators = []) {
  const box = document.getElementById("phone-box");
  box.innerHTML = "";
  if (!district || !Array.isArray(meta.demand_phone)) return;

  const lines = [];
  const seenTel = new Set();

  // 1. 運行主体(電話が確認済みのものだけ。同じ番号は1回)
  for (const op of operators) {
    if (!op || !op.tel || seenTel.has(op.tel)) continue;
    seenTel.add(op.tel);
    const name = op.desk ? `${op.name}(${op.desk})` : op.name;
    lines.push({ label: "この時刻表のバス", name, tel: op.tel });
  }

  // 2. デマンド交通(対象地区のみ)
  const demand = meta.demand_phone.find(
    (p) => Array.isArray(p.districts) && p.districts.includes(district.name)
  );
  if (demand) {
    seenTel.add(demand.tel);
    lines.push({ label: "よやくして のるバス", name: demand.name, tel: demand.tel });
  }

  // 3. 市の相談窓口(同じ番号がまだ出ていなければ)
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
      if (s3.sel.dir === "outbound" && ride.board_walk_min >= 1) {
        parts.push(`バス停までは、あるいて約${ride.board_walk_min}分です。`);
      }
      if (ride.transfer) {
        const hs2Word = String(ride.transfer.headsign2).replace(/(行き|ゆき)$/, "");
        parts.push(`${ride.transfer.at}で おりて、${hs2Word}行きに、のりかえてください。`);
      }
      // どこで降りるかを必ず音声でも案内する(実際のバス停名。開発者指摘2026-07-08)。
      // 目的地まで歩くときは徒歩分も添える
      if (ride.alight) {
        parts.push(`${ride.alight}で、おりてください。`);
        if (ride.alight_place && ride.alight_walk_min >= 1) {
          parts.push(`そこから、${ride.alight_place}まで、あるいて約${ride.alight_walk_min}分です。`);
        }
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
  // 表示する地区の市を state.city に反映しておく
  // (画面1に戻ったとき、市タブがその地区の市になるように)
  if (did) {
    const d = districts.find((x) => x.id === did);
    if (d) state.city = d.municipality;
  }
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
      renderScreen1(); // タブの見た目は renderScreen1 内の syncCityTabs() が合わせる
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
  setupGeoButton();
  window.addEventListener("hashchange", route);
  await route();
}

init();
