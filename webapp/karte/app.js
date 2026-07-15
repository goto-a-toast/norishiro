// -*- coding: utf-8 -*-
// モビリティ・カルテ本体(M8-2。docs/plan_gap_map.md §13)。
// ★重要な設計方針: このJSは計算をしない。Python側(gap_map/export_karte_data.py)が
// 事前計算した webapp/data/karte.json を読み、「一番近いメッシュ」を距離の並べ替えだけで
// 選んで表示する(GPSの地区判定と同じ原則)。A〜Eの評価・平均値もすべてJSON側で確定済み。
//
// じゅうしょの検索は国土地理院の住所検索API(無料・キー不要)をブラウザから直接呼ぶ。
// じゅうしょ・座標はどこにも保存しない(このページのメモリ上だけで使う)。

let karteData = null;    // 遅延fetch(webapp/data/karte.json)
let districtsData = null; // 遅延fetch(webapp/data/districts.json)
let metaData = null;      // 遅延fetch(webapp/data/meta.json。有効期限の表示にだけ使う)

async function getKarte() {
  if (!karteData) {
    karteData = await fetch("../data/karte.json").then((r) => r.json());
  }
  return karteData;
}

async function getDistricts() {
  if (!districtsData) {
    districtsData = await fetch("../data/districts.json").then((r) => r.json());
  }
  return districtsData;
}

async function getMeta() {
  if (!metaData) {
    metaData = await fetch("../data/meta.json").then((r) => (r.ok ? r.json() : null)).catch(() => null);
  }
  return metaData;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function dateJa(iso) {
  const [y, m, d] = String(iso).split("-").map(Number);
  return `${y}年${m}月${d}日`;
}

// 2点間のおおよその距離(m)。かんたんモードのGPS判定と同じ簡易式(候補の並べ替え用)
function distanceM(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const x = (lon2 - lon1) * Math.PI / 180 * Math.cos(((lat1 + lat2) / 2) * Math.PI / 180);
  const y = (lat2 - lat1) * Math.PI / 180;
  return Math.round(R * Math.sqrt(x * x + y * y));
}

// 地区IDから地区を探す(サブ地区=親エントリの sub 配列も対象。かんたんモードと同じ)
function findDistrict(districts, did) {
  for (const d of districts) {
    if (d.id === did) return d;
    for (const s of d.sub || []) {
      if (s.id === did) return { ...s, municipality: d.municipality, parent: d };
    }
  }
  return null;
}

// 地区から選ぶ用の一覧。karte.meta.district_avg のキーと必ず一致する単位だけを出す
// (地区がサブ地区に分割済みのときは、親地区は1つもメッシュを持たないため
// district_avg に無い。親を選択肢に出すと平均が見つからず壊れるので、
// サブがあればサブだけ・無ければ親だけ、を返す)
function karteUnits(districts) {
  const units = [];
  for (const d of districts) {
    if (d.sub && d.sub.length) {
      for (const s of d.sub) units.push({ ...s, municipality: d.municipality });
    } else {
      units.push(d);
    }
  }
  return units;
}

// ---------------- じゅうしょ検索(国土地理院API) ----------------
// https://www.gsi.go.jp/johofukyu/johofukyu41022.html(無料・キー不要)。
// 通信の失敗・0件のときは、呼び出し側が「地区から選ぶ」に誘導する
async function searchAddress(query) {
  const url = `https://msearch.gsi.go.jp/address-search/AddressSearch?q=${encodeURIComponent(query)}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error("住所検索APIが利用できません");
  return res.json();  // [{geometry:{coordinates:[lon,lat]}, properties:{title}}, ...]
}

function setupAddressForm() {
  const form = document.getElementById("addr-form");
  const input = document.getElementById("addr-input");
  const result = document.getElementById("addr-result");

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const q = input.value.trim();
    if (!q) return;
    result.innerHTML = "";
    result.textContent = "しらべています…";
    let candidates;
    try {
      candidates = await searchAddress(q);
    } catch (e) {
      result.innerHTML =
        `<p class="find-error">じゅうしょの けんさくが うまくいきませんでした。` +
        `したの「③ 地区から えらぶ」を おためしください。</p>`;
      return;
    }
    if (!candidates || candidates.length === 0) {
      result.innerHTML =
        `<p class="find-error">じゅうしょが 見つかりませんでした。もう少し くわしく` +
        `入れるか、したの「③ 地区から えらぶ」を おためしください。</p>`;
      return;
    }
    result.innerHTML = "";
    const heading = document.createElement("p");
    heading.className = "find-heading";
    heading.textContent = "この中から えらんでください";
    result.appendChild(heading);
    candidates.slice(0, 5).forEach((c) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "find-candidate";
      btn.textContent = c.properties && c.properties.title ? c.properties.title : q;
      btn.addEventListener("click", () => {
        const [lon, lat] = c.geometry.coordinates;
        showCardForPoint(lat, lon);
      });
      result.appendChild(btn);
    });
  });
}

// ---------------- いまいる場所から(GPS) ----------------
function setupGeoButton() {
  const btn = document.getElementById("geo-btn");
  const result = document.getElementById("geo-result");
  if (!("geolocation" in navigator)) {
    btn.hidden = true;   // 使えない端末ではボタンごと出さない(他の方法で完結する)
    return;
  }
  btn.addEventListener("click", () => {
    result.textContent = "位置を しらべています…";
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        result.textContent = "";
        showCardForPoint(pos.coords.latitude, pos.coords.longitude);
      },
      () => {
        result.innerHTML =
          `<p class="find-error">いまいる場所が わかりませんでした。したの` +
          `「③ 地区から えらぶ」を おためしください。</p>`;
      },
      { timeout: 10000 }
    );
  });
}

// ---------------- 地区から選ぶ(保険) ----------------
async function setupDistrictSelect() {
  const districts = await getDistricts();
  const sel = document.getElementById("district-select");
  const units = karteUnits(districts);
  for (const city of ["山形市", "上山市"]) {
    const og = document.createElement("optgroup");
    og.label = city;
    units.filter((u) => u.municipality === city).forEach((u) => {
      const opt = document.createElement("option");
      opt.value = u.id;
      opt.textContent = `${u.name}(${u.kana || ""})`;
      og.appendChild(opt);
    });
    sel.appendChild(og);
  }
  sel.addEventListener("change", () => {
    if (sel.value) showCardForDistrict(sel.value);
  });
}

// ---------------- カードの組み立て ----------------
// 病院・スーパーの所要分を文章にする。到達不能(null)のときは断定を避け、
// 徒歩で行けるとき(A評価)は前向きに、それ以外は下のボタンでの相談に誘導する
function minutesSentence(minutes, grade, placeName, kind) {
  if (minutes !== null) {
    return `${escapeHtml(placeName || kind)}まで、バスと あるいてで およそ${minutes}分です。`;
  }
  if (grade === "A") {
    return `${kind}までは、あるいても15分いないで行けるきょりです。`;
  }
  return `バスでの${kind}は、少し行きにくいようです。下の「じかんひょうを見る」で くわしく しらべてください。`;
}

function visitSentence(visitOk, visitTotalMin) {
  if (visitOk && visitTotalMin !== null) {
    return `ごぜん中に病院へ行き、お昼をはさんで、ゆうがたまでに かえってこられる` +
      `めやすです(いえを出てから かえるまで、あわせて約${visitTotalMin}分)。`;
  }
  return `日帰りの通院は、少し むずかしいようです。よやくして のるバスなど、` +
    `べつの方法も 市の窓口に ごそうだんください。`;
}

function compareSentence(value, cityAvg) {
  if (value === null || cityAvg === null || cityAvg === undefined) return "";
  const diff = Math.round(value - cityAvg);
  if (Math.abs(diff) < 2) return "山形市の平均と、だいたい同じくらいです。";
  return diff > 0
    ? `山形市の平均より、約${diff}分 長いです。`
    : `山形市の平均より、約${Math.abs(diff)}分 みじかいです。`;
}

async function renderMeshCard(mesh, meta, districts) {
  // 中身が揃うまで card.hidden は動かさない(先に見せてしまうと、
  // 有効期限のfetch待ちの間だけ空のカードが一瞬見える不具合になる)
  const district = findDistrict(districts, mesh.district_id);
  const districtName = district ? district.name : "";
  const gradeLabel = meta.grade_labels[mesh.grade] || "";
  const m = await getMeta();
  const validityHtml = m && m.valid_until
    ? `<p class="karte-validity">この情報は ${dateJa(m.valid_until)} まで有効です</p>` : "";

  const card = document.getElementById("karte-card");
  card.innerHTML = `
    <div class="grade-badge grade-${mesh.grade}">
      <span class="grade-letter">${mesh.grade}</span>
      <span class="grade-text">${escapeHtml(gradeLabel)}</span>
    </div>
    <p class="karte-place">${escapeHtml(districtName)}の おうちのあたり</p>
    <p class="karte-note">これは、おうちのまわり(約500m四方)の めやすです。
      1けんごとの正確な数字ではありません。</p>

    <div class="karte-row">
      <h3>🚏 最寄りのバス停</h3>
      <p>${mesh.nearest_stop_name
        ? `「${escapeHtml(mesh.nearest_stop_name)}」バス停まで、あるいて約${mesh.walk_to_stop_min}分です。`
        : "近くに つかえるバス停が 見つかりませんでした。"}</p>
    </div>

    <div class="karte-row">
      <h3>🏥 病院までのめやす</h3>
      <p>${minutesSentence(mesh.hospital_min, mesh.grade, mesh.hospital_name, "病院")}</p>
      <p class="karte-compare">${compareSentence(mesh.hospital_min, meta.city_avg.hospital_min)}</p>
    </div>

    <div class="karte-row">
      <h3>🛒 スーパーまでのめやす</h3>
      <p>${minutesSentence(mesh.super_min, mesh.grade, mesh.super_name, "スーパー")}</p>
      <p class="karte-compare">${compareSentence(mesh.super_min, meta.city_avg.super_min)}</p>
    </div>

    <div class="karte-row">
      <h3>🕐 通院のめやす</h3>
      <p>${visitSentence(mesh.visit_ok, mesh.visit_total_min)}</p>
    </div>

    <button type="button" class="karte-link-btn" id="karte-to-timetable">
      📅 この家からの じかんひょうを 見る
    </button>
    ${validityHtml}
  `;
  card.hidden = false;
  document.getElementById("karte-to-timetable").addEventListener("click", () => {
    location.href = `../kantan/index.html#${encodeURIComponent(mesh.district_id)}`;
  });
  card.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function renderDistrictAvgCard(unitId, meta, districts) {
  const avg = meta.district_avg[unitId];
  const district = findDistrict(districts, unitId);
  const districtName = district ? district.name : "";
  const card = document.getElementById("karte-card");
  if (!avg) {
    card.innerHTML = `<p class="find-error">この地区の データが 見つかりませんでした。</p>`;
    card.hidden = false;
    return;
  }
  card.innerHTML = `
    <p class="karte-place">${escapeHtml(districtName)}の 平均的なめやす</p>
    <p class="karte-note">これは地区ぜんたいの 平均です。おうち1けんごとの
      正確な数字を知りたいときは、①または②で しらべてください。</p>

    <div class="karte-row">
      <h3>🏥 病院までのめやす(地区平均)</h3>
      <p>${avg.hospital_min !== null
        ? `バスと あるいてで、およそ${avg.hospital_min}分です。`
        : "この地区の平均は、算出できませんでした。"}</p>
      <p class="karte-compare">${compareSentence(avg.hospital_min, meta.city_avg.hospital_min)}</p>
    </div>

    <div class="karte-row">
      <h3>🛒 スーパーまでのめやす(地区平均)</h3>
      <p>${avg.super_min !== null
        ? `バスと あるいてで、およそ${avg.super_min}分です。`
        : "この地区の平均は、算出できませんでした。"}</p>
      <p class="karte-compare">${compareSentence(avg.super_min, meta.city_avg.super_min)}</p>
    </div>

    <button type="button" class="karte-link-btn" id="karte-to-timetable">
      📅 この地区の じかんひょうを 見る
    </button>
  `;
  card.hidden = false;
  document.getElementById("karte-to-timetable").addEventListener("click", () => {
    location.href = `../kantan/index.html#${encodeURIComponent(unitId)}`;
  });
  card.scrollIntoView({ behavior: "smooth", block: "start" });
}

// 緯度経度から一番近いメッシュを選び、カードを表示する(住所検索・GPS共通の入口)
async function showCardForPoint(lat, lon) {
  const [karte, districts] = await Promise.all([getKarte(), getDistricts()]);
  let best = null;
  let bestDist = Infinity;
  for (const mesh of karte.meshes) {
    const d = distanceM(lat, lon, mesh.lat, mesh.lon);
    if (d < bestDist) { bestDist = d; best = mesh; }
  }
  if (!best) return;
  await renderMeshCard(best, karte.meta, districts);
}

async function showCardForDistrict(unitId) {
  const [karte, districts] = await Promise.all([getKarte(), getDistricts()]);
  await renderDistrictAvgCard(unitId, karte.meta, districts);
}

async function init() {
  setupAddressForm();
  setupGeoButton();
  await setupDistrictSelect();
  document.getElementById("app").hidden = false;
}

init();
