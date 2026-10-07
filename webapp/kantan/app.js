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

// データ(JSON)は毎回サーバーに「変わっていないか」を確かめてから使う(変わって
// いなければ通信はごく小さい)。公開サイトはファイルを最大10分ブラウザに保存させるため、
// 再生成の直後に古い時刻表が使われ、新しい画面と食い違うことがあった(2026-10-07 実例:
// 地図ボタンがのりばでなく停の真ん中を指した)
const DATA_FETCH = { cache: "no-cache" };

let districts = [];
let destinations = [];
let meta = null;
const timetableCache = {};
const state = { city: "山形市", category: "hospital", did: null, fid: null,
                geoFix: null };   // 最後にGPSで測った位置 {lat, lon, at}(画面3の距離表示に使う)

// ---------------- 対策1(広い地区): 索引データの遅延読み込み ----------------
// mesh_index.json  … 817メッシュの中心座標と地区ID。GPSの地区判定をポリゴン精度にする
// stops_index.json … 停留所名→座標。「いまの場所からバス停まで およそ◯km」の正直表示に使う
// どちらも無い環境(再生成前)では null を返し、従来動作にフォールバックする
let meshIndexCache;   // undefined=未取得 / null=取得失敗 / object=取得済み
let stopsIndexCache;

// ===============================================================
// わが家から しらべる(案D 段階3。docs/plan_stage3_kantan.md §3)
// ---------------------------------------------------------------
// 家の場所を この端末(ブラウザの保存領域)に おぼえておき、地区の代表点のかわりに
// 家を出発点にして、端末の中で時刻表を作る(webapp/engine/timetable.js)。
// 作られる答えは地区ファイルと同じ形なので、画面2・3は地区と同じ部品で表示する。
// その計算が工場(Python)と同じ答えを出すことは、全地区・メッシュ817地点で照合済み
// (gap_map/verify_timetable_parity.js)。この画面のファイル自身は計算をしない。
// 位置は端末の外に送らない(地図ボタンで外に出るのはバス停の座標だけ、は今までどおり)
// ===============================================================
const HOME_ID = "home";                    // URL(#home/f09)で「わが家」を表す名前
const HOME_KEY = "norishiro.home.v1";      // 保存領域の名前
const HOME_NAME = "わが家";
// 家として登録してよい場所: 人の住むメッシュの中心からこの距離以内(=山形市・上山市の中)
const HOME_AREA_M = 1000;
const ENGINE_VER = "20261008a";            // engine/ のファイルの版(古いものを使わせないため)

// 保存領域は、プライベートブラウズ・設定で止められているときなどに使えない(読み書きで例外)。
// 使えなくても地区の一覧で今までどおり使えるよう、失敗は「登録なし」として扱う
function loadHome() {
  try {
    const v = JSON.parse(localStorage.getItem(HOME_KEY));
    return v && Number.isFinite(v.lat) && Number.isFinite(v.lon) ? v : null;
  } catch (e) {
    return null;
  }
}
function saveHome(h) {
  try { localStorage.setItem(HOME_KEY, JSON.stringify(h)); return loadHome() !== null; } catch (e) { return false; }
}
function clearHome() {
  try { localStorage.removeItem(HOME_KEY); } catch (e) { /* 使えない環境では何もしない */ }
}
function storageUsable() {
  try {
    localStorage.setItem(HOME_KEY + ".test", "1");
    localStorage.removeItem(HOME_KEY + ".test");
    return true;
  } catch (e) {
    return false;
  }
}

// 計算に渡す「家」(名前は帰りの便の「わが家まで あるいて約◯分」に出る)
function homePoint(h) {
  return { lat: h.lat, lon: h.lon, name: HOME_NAME };
}

// 画面の上での「わが家」= 地区のかわり。電話番号欄(デマンド交通・市の窓口)は地区ごとの
// 情報なので、登録のときにメッシュから引いた地区(h.did)を親として持たせる
function homeDistrict() {
  const h = loadHome();
  if (!h) return null;
  const real = h.did ? findRealDistrict(h.did) : null;
  return { id: HOME_ID, name: HOME_NAME, kana: "わがや", isHome: true,
           municipality: real ? real.municipality : "山形市",
           lat: h.lat, lon: h.lon, parent: real ? (real.parent || real) : null };
}

// ---- 端末での計算の窓口。Web Worker(裏の流れ)で計算し、画面を固めない ----
// Worker が使えない環境(ファイルを直接開いたとき等)では、この画面の中で同じ計算をする
let homeWorker;            // undefined=まだ / null=使えない / Worker
let homeCalcLocal = null;  // Worker が使えないときの予備(HomeCalc)
let homeReqId = 0;
const homePending = new Map();

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const el = document.createElement("script");
    el.src = src;
    el.onload = resolve;
    el.onerror = () => reject(new Error(`${src} を読み込めませんでした`));
    document.head.appendChild(el);
  });
}

async function homeCallLocal(op, args) {
  if (!homeCalcLocal) {
    for (const f of ["raptor.js", "network.js", "timetable.js", "home_calc.js"]) {
      await loadScript(`../engine/${f}?v=${ENGINE_VER}`);
    }
    homeCalcLocal = new HomeCalc("../data/network/");   // eslint-disable-line no-undef
  }
  return homeCalcLocal[op](...args);
}

function homeCall(op, args) {
  if (homeWorker === undefined) {
    try {
      homeWorker = new Worker(`../engine/worker.js?v=${ENGINE_VER}`);
      homeWorker.onmessage = (e) => {
        const p = homePending.get(e.data.id);
        if (!p) return;
        homePending.delete(e.data.id);
        if (e.data.ok) p.resolve(e.data.result); else p.reject(new Error(e.data.error));
      };
      // Worker の読み込みに失敗したら、待っている頼みごとを画面の中の計算に回す
      homeWorker.onerror = (ev) => {
        if (ev && ev.preventDefault) ev.preventDefault();
        homeWorker = null;
        for (const [id, p] of homePending) {
          homePending.delete(id);
          homeCallLocal(p.op, p.args).then(p.resolve, p.reject);
        }
      };
    } catch (e) {
      homeWorker = null;
    }
  }
  if (!homeWorker) return homeCallLocal(op, args);
  return new Promise((resolve, reject) => {
    const id = ++homeReqId;
    homePending.set(id, { resolve, reject, op, args });
    homeWorker.postMessage({ id, op, args });
  });
}

async function getMeshIndex() {
  if (meshIndexCache === undefined) {
    meshIndexCache = await fetch("../data/mesh_index.json", DATA_FETCH)
      .then((r) => (r.ok ? r.json() : null)).catch(() => null);
  }
  return meshIndexCache;
}

async function getStopsIndex() {
  if (stopsIndexCache === undefined) {
    stopsIndexCache = await fetch("../data/stops_index.json", DATA_FETCH)
      .then((r) => (r.ok ? r.json() : null)).catch(() => null);
  }
  return stopsIndexCache;
}

// GPSの測位が新しい(10分以内)ならその位置を返す
function geoFixFresh() {
  const g = state.geoFix;
  return g && Date.now() - g.at < 10 * 60 * 1000 ? g : null;
}

// ---------------- 測位の誤差(coords.accuracy)の合格ライン ----------------
// パソコンにはGPSが無い。近くのWiFiの電波から推定できないとき(有線のみ・位置情報
// サービスがオフなど)は、IPアドレスから推定した位置が返り、数km〜数十kmずれる
// (2026-09-24 開発者報告「PCで現在地を調べると全然違う場所が表示される」)。
// 誤差の大きい位置で「いちばん近いバス停」を出すと、自信満々に嘘をつくことになる。
// そこで用途ごとに必要な精度を決め、足りないときは出さずに正直に伝える
const GEO_ACC_STOP_M = 300;       // バス停をえらぶのに要る精度(停どうしは100〜300m)
const GEO_ACC_DISTRICT_M = 3000;  // 地区をえらぶのに要る精度(学区の広さの目安)

// 測位の誤差(m)。分からないときは Infinity(=信用しない)
function geoAccOf(fix) {
  const a = fix && fix.acc;
  return Number.isFinite(a) ? a : Infinity;
}

// 誤差の言い方。「約3km」のように丸めて出す(細かく出すと正確そうに見えてしまう)
function accWord(m) {
  if (!Number.isFinite(m)) return "どのくらいかも わからないほど";
  return m < 950 ? `約${Math.max(100, Math.round(m / 100) * 100)}m` : `約${Math.round(m / 1000)}km`;
}

// 地区IDから地区を探す(サブ地区=親エントリの sub 配列も対象。
// サブ地区には親の市名と親への参照を持たせて返す)
function findDistrict(did) {
  if (did === HOME_ID) return homeDistrict();
  return findRealDistrict(did);
}

function findRealDistrict(did) {
  for (const d of districts) {
    if (d.id === did) return d;
    for (const s of d.sub || []) {
      if (s.id === did) return { ...s, municipality: d.municipality, parent: d };
    }
  }
  return null;
}

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
  // わが家: 全行き先の「行き」を端末で計算する(帰りは画面3で選んだ行き先だけ。getEntry)
  if (did === HOME_ID) {
    const h = loadHome();
    if (!h) throw new Error("わが家が とうろく されていません");
    return homeCall("outbound", [homePoint(h), destinations]);
  }
  if (!timetableCache[did]) {
    timetableCache[did] = await fetch(`../data/timetables/${did}.json`, DATA_FETCH).then((r) => r.json());
  }
  return timetableCache[did];
}

// 画面3で使う「1つの行き先のエントリ」と、のりばの座標表 pts。
// わが家なら、選んだ行き先の帰りを足して端末で計算する
async function getEntry(did, fid) {
  if (did === HOME_ID) {
    const h = loadHome();
    if (!h) throw new Error("わが家が とうろく されていません");
    const r = await homeCall("entry", [homePoint(h), destinations, fid]);
    return { entry: r.to[fid], pts: r.pts };
  }
  const t = await getTimetable(did);
  return { entry: t.to[fid], pts: Array.isArray(t.pts) ? t.pts : null };
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
// mesh_index.json(住民のいる500mメッシュ全部の中心と地区ID)があれば、
// 「一番近いメッシュの地区」で判定する。地区の形をメッシュがタイルしているので、
// 東沢地区のような広い学区でも正しい地区が候補に出る(対策1)。
// 索引が無い環境では従来どおり地区の代表点との距離で代用する。
// どちらでも自動で決めず、必ず「候補から選ぶ」形にする(誤判定への保険)。
// ※これは表示のための距離の並べ替えだけで、経路の計算はしない(設計原則の範囲内)

// 2点間のおおよその距離(m)。ヒュベニではなく簡易式で十分(候補の並べ替え用)
function distanceM(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const x = (lon2 - lon1) * Math.PI / 180 * Math.cos(((lat1 + lat2) / 2) * Math.PI / 180);
  const y = (lat2 - lat1) * Math.PI / 180;
  return Math.round(R * Math.sqrt(x * x + y * y));
}

function distanceWord(m) {
  // 100m未満を「約0m」と書かないよう、いちばん小さい表示は「約100m」にそろえる
  // (GPSの誤差もこの程度はあるため、それ以上細かく書くと正確そうに見えて かえって誤解を生む)
  return m < 950 ? `約${Math.max(100, Math.round(m / 100) * 100)}m` : `約${(m / 1000).toFixed(1)}km`;
}

// 停留所名から「いまの場所までの直線距離(m)」を出す。索引が無い/その停が索引に
// 無いときは null。同じ名前のバス停が遠くの別の町にもあるとき、索引は複数の座標
// ([[lat,lon],...] 形式)を持つので一番近いものを使う(2026-07-12「七日町が24km」の修正)。
// これは距離の表示と並べ替えのためだけで、経路の計算はしない(設計原則の範囲内)
function stopDistanceM(name, fix, idx) {
  if (!fix || !idx || !idx[name]) return null;
  const v = idx[name];
  const pts = Array.isArray(v[0]) ? v : [v];
  let best = Infinity;
  for (const [slat, slon] of pts) {
    best = Math.min(best, distanceM(fix.lat, fix.lon, slat, slon));
  }
  return isFinite(best) ? best : null;
}

// ---------------- バス停の場所を地図アプリで開く(2026-10-07 開発者要望) ----------------
// 画面の中に地図は描かない(外部ライブラリを読み込まない設計原則)。Google マップの公開URL形式
// (キー不要)へのリンクにして、スマートフォンでは地図アプリが開き、そのまま道案内にも使える。
// 外へ出ていくのは「バス停の座標」だけで、利用者の位置は送らない。
// 同じ名前のバス停が離れた場所に複数あるとき(七日町など)は、基準点(いまの場所/
// 地区の代表点/行き先の施設)にいちばん近いものを選ぶ
function stopPoint(name, idx, ref) {
  if (!idx || !idx[name]) return null;
  const v = idx[name];
  const pts = Array.isArray(v[0]) ? v : [v];
  if (pts.length === 1 || !ref) return pts[0];
  let best = pts[0];
  let bestD = Infinity;
  for (const p of pts) {
    const d = distanceM(ref.lat, ref.lon, p[0], p[1]);
    if (d < bestD) { bestD = d; best = p; }
  }
  return best;
}

// exactIdx: 時刻表の便が持つのりばの番号(bp/ap)。データ工場が実際に乗る/降りるのりばの
// 座標を入れているので、七日町(のりば6か所・約205m)のような停でも乗る場所を指せる。
// 番号が無い(古いデータ/利用者が別の停を選んだ)ときだけ停名の索引で近いものを探す
function stopMapLinkHtml(name, ref, exactIdx) {
  const exact = Number.isInteger(exactIdx) && s3.pts ? s3.pts[exactIdx] : null;
  const pt = exact || stopPoint(name, s3.mapIndex, ref);
  if (!pt) return "";   // 座標が無い停(データ再生成前など)ではリンクを出さない
  const url = `https://www.google.com/maps/search/?api=1&query=${pt[0].toFixed(5)},${pt[1].toFixed(5)}`;
  return `<a class="map-link" href="${url}" target="_blank" rel="noopener">` +
    `🗺 「${escapeHtml(name)}」の ばしょを 地図でみる</a>`;
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
      async (pos) => {
        const { latitude, longitude, accuracy } = pos.coords;
        state.geoFix = { lat: latitude, lon: longitude, at: Date.now(), acc: accuracy };
        // 誤差が地区の広さを超えるときは候補を出さない(パソコンでIPから推定した
        // 位置など。ちがう町の地区を「ちかい じゅんに」と出すほうが有害)
        if (geoAccOf(state.geoFix) > GEO_ACC_DISTRICT_M) {
          result.innerHTML =
            `<p class="geo-note geo-coarse">いまいる場所が ${escapeHtml(accWord(accuracy))}` +
            ` ずれているため、ちかい地区を おしらせできません。<br>` +
            `パソコンでは おおよその場所しか わからないことがあります。` +
            `下の一覧から おすまいの地区を えらんでください</p>`;
          return;
        }
        const near = await nearestDistricts(latitude, longitude, 3);
        result.innerHTML = '<p class="geo-note">ちかい じゅんに ならべました。おすまいの地区をえらんでください</p>';
        // 地区は選べるがバス停には足りない精度のとき、先に断っておく
        if (geoAccOf(state.geoFix) > GEO_ACC_STOP_M) {
          result.innerHTML +=
            `<p class="geo-note geo-coarse">(いまいる場所は ${escapeHtml(accWord(accuracy))}` +
            ` ずれているかもしれません。ちがっていたら 下の一覧から えらんでください)</p>`;
        }
        near.forEach(({ d, dist }) => {
          const b = document.createElement("button");
          b.type = "button";
          b.className = "district-btn geo-candidate";
          b.innerHTML =
            `${escapeHtml(d.name)}<span class="kana">${escapeHtml(d.kana)} ・ ${escapeHtml(distanceWord(dist))}</span>`;
          b.addEventListener("click", () => { location.hash = d.id; });
          result.appendChild(b);
        });
        // ここで出せるのは「地区」まで。バス停は行き先が決まらないと選べない
        // (行き先によって、通るバスも乗るバス停も変わるため)。次に何が起きるかを
        // 先に伝えておく(2026-08-22 開発者指摘「地区のままですよ」への対応)
        const next = document.createElement("p");
        next.className = "geo-note geo-next-note";
        next.textContent =
          "このあと 行き先をえらぶと、いまいる場所から いちばん近いバス停を お知らせします";
        result.appendChild(next);
      },
      () => {
        result.textContent = "位置情報が つかえませんでした。下の一覧から えらんでください";
      },
      { timeout: 10000, maximumAge: 60000 }
    );
  });
}

// 近い地区の候補を作る。索引があれば「地区の最寄りメッシュまでの距離」で、
// 無ければ従来の「代表点までの距離」で近い順に n 地区(初出のみ)
async function nearestDistricts(lat, lon, n) {
  const idx = await getMeshIndex();
  if (idx && Array.isArray(idx.meshes)) {
    const best = new Map();   // 地区ID → 最寄りメッシュまでの距離
    for (const [mlat, mlon, di] of idx.meshes) {
      const dist = distanceM(lat, lon, mlat, mlon);
      const id = idx.districts[di];
      if (!best.has(id) || dist < best.get(id)) best.set(id, dist);
    }
    return [...best.entries()]
      .sort((a, b) => a[1] - b[1])
      .map(([id, dist]) => ({ d: findDistrict(id), dist }))
      .filter((x) => x.d)   // districts.json 側に無いIDは念のため飛ばす
      .slice(0, n);
  }
  // フォールバック: 代表点との距離(索引が未生成の環境)
  return districts
    .map((d) => ({ d, dist: distanceM(lat, lon, d.lat, d.lon) }))
    .sort((a, b) => a.dist - b.dist)
    .slice(0, n);
}

// ---------------- わが家の欄(画面1のいちばん上) ----------------
// notice: 欄の上に出す一言(「わが家が とうろく されていません」など)
function renderHomeRow(notice = "") {
  const row = document.getElementById("home-row");
  if (!row) return;
  // 位置情報か保存領域が使えない端末では欄ごと出さない(地区の一覧で完結する)
  if (!("geolocation" in navigator) || !storageUsable()) { row.hidden = true; return; }
  row.hidden = false;
  const warn = notice ? `<p class="home-warn">${escapeHtml(notice)}</p>` : "";
  const h = loadHome();
  if (h) {
    row.innerHTML = warn +
      `<button type="button" id="home-go-btn" class="home-btn">🏠 わが家から しらべる</button>` +
      `<div class="home-sub">` +
      `<button type="button" id="home-reset-btn" class="home-small-btn">わが家の場所を とりなおす</button>` +
      `<button type="button" id="home-clear-btn" class="home-small-btn">わが家を けす</button></div>`;
    row.querySelector("#home-go-btn").addEventListener("click", () => { location.hash = HOME_ID; });
    row.querySelector("#home-reset-btn").addEventListener("click", registerHome);
    row.querySelector("#home-clear-btn").addEventListener("click", confirmClearHome);
  } else {
    row.innerHTML = warn +
      `<button type="button" id="home-set-btn" class="home-btn home-set-btn">📍 いまいる場所を わが家にする</button>` +
      `<p class="home-note">おうちに いるときに 1回だけ おしてください。` +
      `つぎからは 「わが家から しらべる」で、家のそばの バス停の 時刻表が でます。` +
      `場所は この端末の中だけに のこり、どこにも おくりません</p>`;
    row.querySelector("#home-set-btn").addEventListener("click", registerHome);
  }
}

// 「けす」は押しまちがいに備えて、もう1回たしかめる(ブラウザの確認ダイアログは使わない)
function confirmClearHome() {
  const row = document.getElementById("home-row");
  row.innerHTML =
    `<p class="home-warn">わが家の場所を けしますか?(つぎからは もういちど とうろくが ひつようです)</p>` +
    `<div class="home-sub">` +
    `<button type="button" id="home-clear-yes" class="home-small-btn">けす</button>` +
    `<button type="button" id="home-clear-no" class="home-small-btn">やめる</button></div>`;
  row.querySelector("#home-clear-yes").addEventListener("click", () => { clearHome(); renderHomeRow(); });
  row.querySelector("#home-clear-no").addEventListener("click", () => renderHomeRow());
}

// いまいる場所を測って、わが家として おぼえる。誤差の大きい測位・2市の外では登録しない
function registerHome() {
  const row = document.getElementById("home-row");
  row.innerHTML = `<p class="home-note">いまいる場所を しらべています…</p>`;
  navigator.geolocation.getCurrentPosition(
    async (pos) => {
      const { latitude: lat, longitude: lon, accuracy } = pos.coords;
      // バス停をえらべる精度が要る(家の近くのどの停から乗るかが変わるため)
      if (!(Number.isFinite(accuracy) && accuracy <= GEO_ACC_STOP_M)) {
        renderHomeRow(`いまいる場所が ${accWord(accuracy)} ずれているため、わが家に できませんでした。` +
          `パソコンでは おおよその場所しか わからないことがあります。スマートフォンで おためしください`);
        return;
      }
      const near = await nearestDistricts(lat, lon, 1);
      if (!near.length || near[0].dist > HOME_AREA_M) {
        renderHomeRow("いまいる場所は 山形市・上山市の外のようです。この時刻表は 山形市・上山市の中から つかえます");
        return;
      }
      if (!saveHome({ lat, lon, did: near[0].d.id, at: Date.now() })) {
        renderHomeRow("この端末では 場所を おぼえておけませんでした。下の一覧から 地区を えらんでください");
        return;
      }
      location.hash = HOME_ID;
    },
    () => renderHomeRow("位置情報が つかえませんでした。下の一覧から 地区を えらんでください"),
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }
  );
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
  renderHomeRow();
  const grid = document.getElementById("district-grid");
  grid.innerHTML = "";
  districts
    .filter((d) => d.municipality === state.city)
    .forEach((d) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "district-btn";
      btn.innerHTML = `${escapeHtml(d.name)}<span class="kana">${escapeHtml(d.kana)}</span>`;
      // サブ地区(対策2・広い地区の分割)がある地区は、どのあたりに住んでいるかを
      // ひとつだけ確認してから進む(GPS経由ならこのステップは出ない)
      btn.addEventListener("click", () => {
        if (Array.isArray(d.sub) && d.sub.length) {
          renderSubChoice(d);
        } else {
          location.hash = d.id;
        }
      });
      grid.appendChild(btn);
    });
}

// 広い地区のサブ地区選択(画面1の中間ステップ)。
// 「わからない」を必ず用意して、従来どおり親地区(代表点)の時刻表にも行けるようにする
function renderSubChoice(parent) {
  const grid = document.getElementById("district-grid");
  grid.innerHTML = "";

  const q = document.createElement("p");
  q.className = "instruction sub-question";
  q.textContent = `${parent.name}の どのあたりに おすまいですか?`;
  grid.appendChild(q);

  parent.sub.forEach((s) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "district-btn";
    btn.innerHTML = `${escapeHtml(s.name)}<span class="kana">${escapeHtml(s.kana)}</span>`;
    btn.addEventListener("click", () => { location.hash = s.id; });
    grid.appendChild(btn);
  });

  const dunno = document.createElement("button");
  dunno.type = "button";
  dunno.className = "district-btn sub-dunno";
  dunno.innerHTML = `わからない・どこでもよい<span class="kana">${escapeHtml(parent.name)}ぜんたいの時刻表へ</span>`;
  dunno.addEventListener("click", () => { location.hash = parent.id; });
  grid.appendChild(dunno);

  const back = document.createElement("button");
  back.type = "button";
  back.className = "district-btn sub-back";
  back.textContent = "← 地区のいちらんに もどる";
  back.addEventListener("click", renderScreen1);
  grid.appendChild(back);
}

// ===============================================================
// 画面2: いきたい場所をえらぶ
// ===============================================================
async function renderScreen2(did) {
  showScreen(2);
  const district = findDistrict(did);
  document.getElementById("s2-district-name").textContent = district ? district.name : "";
  const list = document.getElementById("facility-list");
  const noneBox = document.getElementById("s2-none-box");
  if (noneBox) noneBox.hidden = true;
  if (did === HOME_ID) list.innerHTML = '<p class="calc-note">わが家からの じかんを けいさんしています…</p>';
  let timetable;
  try {
    timetable = await getTimetable(did);
  } catch (e) {
    list.innerHTML = `<p class="no-facility-note">時刻表を つくれませんでした(${escapeHtml(e.message)})。` +
      `もどって 地区から えらんでください</p>`;
    return;
  }
  if (state.did !== did) return;   // 計算しているあいだに別の画面へ移った
  renderFacilityList(timetable);
}

// かんたんモードが「行き」で見せる便 = おすすめの乗り場(1か所)から乗る便。
// ★2026-10-07 案A: データは全部の乗り場の便を持つ(しっかりモードで全乗り場を見せる
// ため)ので、ここで entry.kantan_boards[ダイヤ種別] の停から乗る形に付け替える。
// 「◯◯から のる」(rowsFromBoard)と同じ付け替えで、新しい計算はしない。
// kantan_boards が無い古いデータは工場で絞り込み済みなので、そのまま返す
function kantanOutbound(entry, dt, closedOps) {
  let rows = (entry.outbound && entry.outbound[dt]) || [];
  if (closedOps && closedOps.size) rows = withoutClosed(rows, closedOps);
  const kb = entry.kantan_boards ? entry.kantan_boards[dt] : null;
  if (!kb) return rows;
  let picked = rowsFromBoard(rows, kb);
  // その日運休のバス(10/15 の済生病院シャトル等)の停がおすすめだった日は、
  // 全曜日共通のおすすめの乗り場に戻す
  if (!picked.length && entry.kantan_board && entry.kantan_board !== kb) {
    picked = rowsFromBoard(rows, entry.kantan_board);
  }
  return picked.length ? picked : rows;
}

// 指定の日に運休する運行主体(meta.closed_dates。済生病院シャトルの創立記念日・年末年始)の
// 便を外す。ダイヤ種別(平日)の上では走る日なので、日付で見る必要がある
function closedOpsOn(d) {
  const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const out = new Set();
  for (const [op, days] of Object.entries((meta && meta.closed_dates) || {})) {
    if (days.includes(key)) out.add(Number(op));
  }
  return out;
}

function withoutClosed(rows, closedOps) {
  return rows.filter((r) => !closedOps.has(r.op) && !(r.transfer && closedOps.has(r.transfer.op2)));
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
  renderNoneBox(timetable);

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

// どの行き先へもバスで行けないとき(近くにバス停が無い家・地区)に、画面2で案内と電話番号を出す。
// 行き先のボタンが全部押せないと、画面3の電話番号欄(デマンド交通・市の窓口)にたどり着けないため
// (2026-10-08 開発者指摘)。分類のタブに関係なく、全部の行き先を見て判定する
function renderNoneBox(timetable) {
  const box = document.getElementById("s2-none-box");
  if (!box) return;   // 古い index.html(ブラウザに残った分)では何もしない
  const anyReachable = destinations.some((f) => {
    const e = timetable && timetable.to ? timetable.to[f.id] : null;
    return e && !e.unreachable && isFinite(bestOutboundMinutes(e));
  });
  if (anyReachable) { box.hidden = true; return; }
  const district = findDistrict(state.did);
  const who = district && district.isHome ? "わが家" : (district ? district.name : "ここ");
  document.getElementById("s2-none-note").textContent =
    `${who}からは、どの行き先へも 時刻表の バスでは 行けません` +
    `(あるいて行ける所に バス停が ないか、行き先へ行く バスが ありません)。` +
    `下の電話番号に ごそうだんください`;
  renderPhoneBox(district, [], "s2-phone-box");
  box.hidden = false;
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
  tomorrowView: false, // きょう運行が無い日に「あしたの時刻表」へ切り替えたか
  timer: null,       // 1分ごとの時計更新タイマー
  stopsIndex: null,  // 停留所名→座標(GPS測位が新しいときだけ読み込む。対策1)
  mapIndex: null,    // 同じ索引。地図リンク用に、測位の有無にかかわらず読み込む
  pts: null,         // 時刻表ファイルの「のりばの座標表」。便の bp/ap がこの番号を指す
  closedOps: null,   // 表示している日に運休する運行主体(meta.closed_dates)
  closedFor: null,   // closedOps がどのダイヤ種別の表示のためのものか
  boardPick: null,   // 利用者が「いまの場所から近いバス停」で選んだ乗車停名
                     // (null = データ工場のおすすめ=kantan_board のまま。F10-L)
  seq: 0,            // renderScreen3の世代番号(連打時に古い処理を打ち切る)
};

// かんたんモードは行き先ごとに「一番いい乗り場」1つだけを見せる(見慣れないバス停を
// 混ぜない。設計C)。データ工場がダイヤ種別ごとに選んだ停(kantan_boards)の便だけを
// kantanOutbound() が選ぶ(2026-10-07 までは工場が絞り込み済みのデータを配っていた)。
// 帰り(inbound)は工場側で完結(2026-07-10から乗り場欄は実停名。施設近くの複数の停の
// 便が混ざるが、どの停から乗るかは各便のステップ①が徒歩分つきで案内する)
function rowsFor(dir, showType) {
  let rows = (s3.entry && s3.entry[dir] && s3.entry[dir][showType]) || [];
  // 表示している日に運休するバス(10/15 の済生病院シャトル等)の便は出さない
  const closed = showType === s3.closedFor ? s3.closedOps : null;
  if (closed && closed.size) rows = withoutClosed(rows, closed);
  if (dir !== "outbound" || !s3.entry) return rows;
  // 利用者が「いまの場所から近いバス停」を選んでいれば、その停から乗る形に付け替える
  // (全部の乗り場の便から選ぶので、おすすめと別の系統でも、その停を通る便はすべて出る)。
  // 選んでいなければ、おすすめの乗り場から乗る便(帰りの降車停はエンジンが選び済み)
  return s3.boardPick ? rowsFromBoard(rows, s3.boardPick) : kantanOutbound(s3.entry, showType, closed);
}

// 1本の便の中から「その名前のバス停で乗るときの発車時刻」を取り出す。
// ふつうは1つだけだが、山形駅前のように同じ名前ののりばが複数ある場所では、
// 1本の便が同じ名前の停を2回通ることがある(実データで47,532便中3便)。
// そのときは「その名前で乗れる最後の発車」を採る(いちばん待たずに乗れて、
// バスに乗っている時間も短い。着く時刻はどちらで乗っても同じ便なので変わらない)
function boardOptionOf(r, stop) {
  const opts = Array.isArray(r.board_options) && r.board_options.length ? r.board_options : null;
  if (!opts) {
    return r.board === stop ? { stop, dep: r.dep, walk_min: r.board_walk_min } : null;
  }
  let best = null;
  for (const o of opts) {
    if (o.stop !== stop) continue;
    if (!best || hmToMin(o.dep) > hmToMin(best.dep)) best = o;
  }
  return best;
}

// 選んだバス停から乗る形に、便の表示を付け替える(新しい計算はしない)。
// 同じバスなので降車側は変わらない。発車時刻と徒歩分をその停の値(board_options に
// 入っている実測値)に差し替え、「のること約◯分」だけ引き算し直す
// (しっかりモードの「乗車バス停で絞り込み」とまったく同じやり方)。
// その停に停まらない便は落とす。board_options が無い古いデータでは停名の一致で判定する
function rowsFromBoard(rows, stop) {
  const out = [];
  for (const r of rows) {
    const opt = boardOptionOf(r, stop);
    if (!opt) continue;
    const wait = r.transfer ? r.transfer.wait_min : 0;
    out.push({
      ...r,
      board: opt.stop,
      // 候補停が持つ「このバスが実際に止まるのりば」の番号を使う。無い古いデータでは
      // 同じ停・同じ発車なら元のまま、それ以外は停名の索引で探す
      bp: Number.isInteger(opt.p) ? opt.p : (opt.stop === r.board && opt.dep === r.dep ? r.bp : undefined),
      dep: opt.dep,
      board_walk_min: opt.walk_min != null ? opt.walk_min : r.board_walk_min,
      ride_min: hmToMin(r.arr) - hmToMin(opt.dep) - wait,
    });
  }
  out.sort((a, b) => hmToMin(a.dep) - hmToMin(b.dep));
  return out;
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

// 表示中の便の選択をやり直す(初期表示と、乗車バス停を選び直したときの両方で使う)。
// 既定は「つぎの便」。本日の便が終わっていたら選択なし(カードが終了案内を出す)。
// 有効期間外の日・あしたの時刻表を見ているときは、始発を選んだ状態にして時計とは
// 連動させない(「本日の便はおわりました」という誤った案内を出さないため)
function resetSelection(now) {
  const rows = s3Rows("outbound");
  const nextIdx = nextOutboundIdx(now);
  if (s3.tomorrowView) {
    s3.sel = rows.length ? { dir: "outbound", idx: 0 } : null;
    s3.manual = true;
  } else if (nextIdx >= 0) {
    s3.sel = { dir: "outbound", idx: nextIdx };
    s3.manual = false;
  } else if (!s3.todayType && rows.length > 0) {
    s3.sel = { dir: "outbound", idx: 0 };
    s3.manual = true;
  } else {
    s3.sel = null;
  }
}

async function renderScreen3(did, fid) {
  showScreen(3);
  const district = findDistrict(did);
  const facility = destinations.find((f) => f.id === fid);
  document.getElementById("s3-district-name").textContent = district ? district.name : "";
  document.getElementById("s3-facility-name").textContent = facility ? facility.name : "";

  if (s3.timer) { clearInterval(s3.timer); s3.timer = null; }
  // 乗車バス停の選択は行き先を変えるたびに白紙に戻す。行けない行き先で下の
  // 早い return を通る場合も残らないよう、いちばん先に消しておく
  s3.boardPick = null;

  // 画面遷移の連打対策: await中に新しいrenderScreen3が始まっていたら、
  // 古い方はここで打ち切る(古いsetIntervalが残り続けるのを防ぐ)
  const seq = ++s3.seq;
  if (did === HOME_ID) {
    document.getElementById("ride-card").innerHTML =
      '<p class="calc-note">わが家からの 時刻表を けいさんしています…</p>';
  }
  let got;
  try {
    got = await getEntry(did, fid);
  } catch (e) {
    if (seq !== s3.seq) return;
    document.getElementById("ride-card").innerHTML =
      `<div class="card-main">時刻表を つくれませんでした</div>` +
      `<div class="card-sub">${escapeHtml(e.message)}。もどって 地区から えらんでください</div>`;
    return;
  }
  if (seq !== s3.seq) return;
  const entry = got.entry;
  s3.pts = got.pts;

  // 行けない施設(画面2ではタップできないが、URL直叩きで来る場合がある)
  const dirBlocks = document.querySelectorAll("#screen3 .direction-block");
  if (!entry || entry.unreachable) {
    document.getElementById("ride-card").innerHTML =
      '<div class="card-main">この行き先へは バスで行けません</div>';
    document.getElementById("chip-hint").hidden = true;
    const nb = nearStopBoxEl();
    if (nb) nb.hidden = true;
    dirBlocks.forEach((el) => { el.hidden = true; }); // 空の行き/帰り枠は出さない
    document.getElementById("day-type-note").textContent = "";
    document.getElementById("validity-note").textContent = "";
    document.getElementById("speak-btn").hidden = true;
    s3.entry = null;   // 前の行き先のデータを読んでしまわないように消す
    s3.sel = null;
    renderPhoneBox(district);
    return;
  }
  dirBlocks.forEach((el) => { el.hidden = false; });

  const now = new Date();
  s3.entry = entry;
  s3.district = district;
  s3.facility = facility;
  // GPSで測ったばかりの位置があれば、停留所までの距離の正直表示に使う(対策1)
  s3.stopsIndex = geoFixFresh() ? await getStopsIndex() : null;
  s3.mapIndex = await getStopsIndex();
  if (seq !== s3.seq) return;
  s3.todayType = dayTypeOf(now);
  s3.closedOps = closedOpsOn(now);
  s3.closedFor = s3.todayType;
  // 有効期間外の日も時刻表は出したままにする(R7)。表示は平日ダイヤで代用し、
  // 「対象外の日です」の注意書きを優先表示する
  s3.showType = s3.todayType || "weekday";
  s3.manual = false;
  s3.tomorrowView = false;

  // 初期選択 = つぎの便(くわしくは resetSelection)
  resetSelection(now);

  document.getElementById("chip-hint").hidden = false;
  renderDirection("outbound");
  renderDirection("inbound");
  renderRideCard(now);
  updateChipSelection();
  renderNearStopBox();

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
    // 「きょうのダイヤに行きの便が1本も無い」と「あったが全部出発した」を区別する
    // (2026-07-12 監査指摘: 運行の無い曜日に「おわりました」と出すのは誤情報)。
    // どちらも、あしたの始発を date_table から引くだけで案内する
    const ranToday = s3Rows("outbound").length > 0;
    const tomorrow = new Date(now.getTime() + 24 * 60 * 60 * 1000);
    const tType = dayTypeOf(tomorrow);
    const tRows = tType ? rowsFor("outbound", tType) : [];
    head =
      `<div class="card-main">${ranToday ? "本日の便は おわりました" : "きょうは 行きのバスの運行が ありません"}</div>` +
      (tRows.length
        ? `<div class="card-sub">あしたの始発は ${timeWord(tRows[0].dep)} です</div>`
        : "");
    // きょう運行が無い日は「あしたの時刻表」への入口を出す(2026-07-12 開発者要望。
    // タップ+1回の明示操作にすることで「きょう乗れる」との誤解を防ぐ)
    if (!ranToday && tRows.length) {
      head += `<button type="button" id="show-tomorrow-btn" class="tomorrow-btn">あしたの じこくひょうを 見る</button>`;
    }
    card.innerHTML = head;
    const tbtn = card.querySelector("#show-tomorrow-btn");
    if (tbtn) tbtn.addEventListener("click", () => showTomorrowTimetable(tType));
    return;
  }

  // あしたの時刻表を表示中は、大きな見出しを出し続ける(誤解防止)+戻る入口
  let banner = "";
  if (s3.tomorrowView) {
    banner =
      `<div class="tomorrow-banner">あしたの じこくひょうです` +
      `<button type="button" id="back-today-btn" class="back-today-btn">きょうに もどる</button></div>`;
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

  card.innerHTML = banner + head + otherDayBoardNote() + rideStepsHtml(ride, s3.sel.dir);
  const bbtn = card.querySelector("#back-today-btn");
  if (bbtn) bbtn.addEventListener("click", () => renderScreen3(s3.district.id, s3.facility.id));
}

// 曜日によって乗る場所が違うときに、ほかの曜日の乗り場を1行で伝える(2026-10-07)。
// データ工場は、平日しか走らないバス(済生病院のシャトル等)が明らかに速ければ、
// その曜日だけおすすめの乗り場を替える。いつもの曜日と違う停に行ってしまわないように、
// 「土曜・日曜・祝日は ◯◯ から のります」と添える。利用者が乗り場を自分で選んだときは出さない
function otherDayBoardNote() {
  const kb = s3.entry && s3.entry.kantan_boards;
  if (!kb || s3.boardPick || !s3.sel || s3.sel.dir !== "outbound") return "";
  // 実際に表示している乗り場(運休日でおすすめを戻した日も正しく比べるため)
  const shown = [...new Set(s3Rows("outbound").map((r) => r.board))];
  const cur = shown.length === 1 ? shown[0] : kb[s3.showType];
  const byBoard = new Map();   // 停名 → [ダイヤ種別の名前, ...]
  for (const dt of ["weekday", "saturday", "sunday_holiday"]) {
    if (dt === s3.showType || !kb[dt] || kb[dt] === cur) continue;
    if (!byBoard.has(kb[dt])) byBoard.set(kb[dt], []);
    byBoard.get(kb[dt]).push(meta.day_types[dt]);
  }
  if (!byBoard.size) return "";
  const lines = [...byBoard.entries()].map(([stop, days]) =>
    `${escapeHtml(days.join("・"))}は「${escapeHtml(stop)}」から のります`);
  return `<div class="day-board-note">※${lines.join("。")}</div>`;
}

// きょう運行の無い日に「あしたの時刻表」へ切り替える(2026-07-12 開発者要望)。
// あしたの始発を選んだ状態にして乗り方まで見せる。時計連動(あと◯分)はしない
function showTomorrowTimetable(tType) {
  s3.tomorrowView = true;
  s3.showType = tType;
  s3.closedOps = closedOpsOn(new Date(Date.now() + 24 * 60 * 60 * 1000));
  s3.closedFor = tType;
  s3.sel = { dir: "outbound", idx: 0 };
  s3.manual = true;
  renderDirection("outbound");
  renderDirection("inbound");
  renderRideCard(new Date());
  updateChipSelection();
  renderNearStopBox();   // 候補と本数は曜日で変わるので出し直す
  document.getElementById("day-type-note").textContent =
    `※あしたの「${meta.day_types[tType]}」ダイヤです(きょうの運行はありません)`;
}

// ===============================================================
// 「いまの場所から いちばん近いバス停」(2026-08-21 開発者要望
//  「最寄りのバス停は目的地に合わせて、現在位置情報から検出できるように」)
// ---------------------------------------------------------------
// 候補にするのは「この行き先へ行くバスが実際に停まるバス停」だけ(=表示中の便の
// board_options)。目的地に合わせた候補なので、選んでも必ずその行き先へ行ける。
// 選ぶと rowsFor() が「その停から乗る形」に表示を付け替える。発車時刻・徒歩分は
// JSONに入っている実測値の付け替えで、新しい計算はしない(設計原則の範囲内)。
// 自動では切り替えない(GPSの誤差に備えて、切り替えは必ず利用者が押す)
// ===============================================================

// この行き先へのバスが停まる、家の近くのバス停一覧(表示中のダイヤ種別の便から作る)。
// 便が1本も無い停は候補にしない(選んだとたん「運行がありません」になるのを防ぐ)
function boardStopCandidates() {
  const base = (s3.entry && s3.entry.outbound && s3.entry.outbound[s3.showType]) || [];
  const map = new Map();   // 停名 → { stop, trips(この日の本数), walk_min, ps(実際に止まるのりばの番号) }
  for (const r of base) {
    const opts = Array.isArray(r.board_options) && r.board_options.length
      ? r.board_options
      : [{ stop: r.board, walk_min: r.board_walk_min, p: r.bp }];
    // 同じ便の中に同じ名前の停が2回出ることがある(のりば違い)。本数は便の数で数える
    const seen = new Set();
    for (const o of opts) {
      if (seen.has(o.stop)) continue;
      seen.add(o.stop);
      const cur = map.get(o.stop);
      if (cur) {
        cur.trips += 1;
        if (Number.isInteger(o.p)) cur.ps.add(o.p);
        if (o.walk_min != null) {
          cur.walk_min = cur.walk_min == null ? o.walk_min : Math.min(cur.walk_min, o.walk_min);
        }
      } else {
        map.set(o.stop, { stop: o.stop, trips: 1, walk_min: o.walk_min != null ? o.walk_min : null,
                          ps: new Set(Number.isInteger(o.p) ? [o.p] : []) });
      }
    }
  }
  return [...map.values()];
}

// 候補を「いまの場所から近い順」に並べる。座標が索引に無い停は出さない(距離を偽らない)。
// 距離は「この行き先へのバスが実際に止まるのりば」(候補停の p)のうち一番近いものまで測り、
// そののりばの番号を p に入れる(地図ボタンが道路の反対側ののりばを指さないように。2026-10-07)
function nearBoardStops(fix) {
  return boardStopCandidates()
    .map((c) => {
      if (fix && s3.pts && c.ps.size) {
        let best = null;
        for (const p of c.ps) {
          const pt = s3.pts[p];
          if (!pt) continue;
          const d = distanceM(fix.lat, fix.lon, pt[0], pt[1]);
          if (!best || d < best.dist) best = { dist: d, p };
        }
        if (best) return { ...c, dist: best.dist, p: best.p };
      }
      return { ...c, dist: stopDistanceM(c.stop, fix, s3.stopsIndex), p: null };
    })
    .filter((c) => c.dist !== null)
    .sort((a, b) => a.dist - b.dist);
}

// いまの場所の「ほんとうの最寄り停」を、索引の全停(約390停)から出す。
// この時刻表の乗り場候補かどうかは問わない。候補よりずっと近い停があるのに
// 黙って遠い停を「いちばん近い」と出すと嘘になるため、正直に併記するのに使う
// (2026-08-22 開発者指摘「山形県立中央病院にいるのに最寄りとして出てこない」)。
// 原因は docs/plan_f10_stop_select.md §6.7 = 家側の事前計算が「地区の代表点1点」
// 基準で、代表点から徒歩800m圏の外にある停は、そもそも時刻表に載っていないこと
function trueNearestStop(fix) {
  const idx = s3.stopsIndex;
  if (!fix || !idx) return null;
  let best = null;
  for (const stop of Object.keys(idx)) {
    const dist = stopDistanceM(stop, fix, idx);
    if (dist !== null && (best === null || dist < best.dist)) best = { stop, dist };
  }
  return best;
}

// データ工場が選んだ「おすすめの乗り場」=「もどす」ボタンの行き先。
// 基本は entry.kantan_board だが、その停に便が無い曜日は工場が別の停の便で埋めている
// (export_web_data.py の _slim_to_board のフォールバック)。そのため
// 「表示中の曜日で実際に使われている停」を優先し、便の無い停の名前を出さない
function recommendedBoard() {
  const kbt = s3.entry && s3.entry.kantan_boards ? s3.entry.kantan_boards[s3.showType] : null;
  if (kbt) {   // 2026-10-07 以降のデータ: ダイヤ種別ごとのおすすめの乗り場
    // その日運休のバスの停だったら(10/15 の済生病院シャトル等)、実際に見せている停を返す
    const shown = [...new Set(kantanOutbound(s3.entry, s3.showType,
      s3.showType === s3.closedFor ? s3.closedOps : null).map((r) => r.board))];
    return shown.length && !shown.includes(kbt) ? shown[0] : kbt;
  }
  const base = (s3.entry && s3.entry.outbound && s3.entry.outbound[s3.showType]) || [];
  const boards = [...new Set(base.map((r) => r.board))];
  const kb = s3.entry && s3.entry.kantan_board ? s3.entry.kantan_board : null;
  if (!boards.length) return kb;
  return kb && boards.includes(kb) ? kb : boards[0];
}

// 乗車バス停を選び直す(null = おすすめに もどす)。画面3の中だけを描き直す
function applyBoardPick(stop) {
  s3.boardPick = stop;
  const now = new Date();
  resetSelection(now);
  renderDirection("outbound");
  renderRideCard(now);
  updateChipSelection();
  renderNearStopBox();
  // 見せている便が変わるので、電話番号欄(運行主体)も出し直す
  renderPhoneBox(s3.district, collectOperators());
}

function nearBoxButton(id, label, cls) {
  return `<button type="button" id="${id}" class="${cls}">${escapeHtml(label)}</button>`;
}

// 画面3の「いちばん近いバス停」欄を返す。index.html が古いまま(ブラウザや
// 配信のキャッシュ)でも機能が消えないよう、無ければJSが作って
// のりかたカードのすぐ下に差し込む
function nearStopBoxEl() {
  let box = document.getElementById("near-stop-box");
  if (!box) {
    const card = document.getElementById("ride-card");
    const screen3 = document.getElementById("screen3");
    if (!screen3) return null;
    box = document.createElement("div");
    box.id = "near-stop-box";
    box.className = "near-stop-box";
    box.setAttribute("aria-live", "polite");
    if (card && card.parentNode === screen3) screen3.insertBefore(box, card);
    else screen3.appendChild(box);
  }
  return box;
}

// すでに位置情報の使用を許してくれている端末では、押さなくても最寄りを出す。
// 許可されていない端末には勝手に聞かない(ボタンのままにする)。
// Permissions API が無い端末(Safariなど)でも、ボタンで従来どおり使える
async function autoDetectIfAllowed() {
  if (!navigator.permissions || !navigator.permissions.query) return;
  let status;
  try {
    status = await navigator.permissions.query({ name: "geolocation" });
  } catch (e) {
    return;   // この端末では判定できない。ボタンのままにする
  }
  if (status.state === "granted" && !geoFixFresh()) measureNearStop();
}

function renderNearStopBox() {
  const box = nearStopBoxEl();
  if (!box) return;
  // 位置情報が使えない端末では欄ごと出さない(一覧の操作だけで完結する)
  if (!("geolocation" in navigator) || !s3.entry) { box.hidden = true; return; }
  // わが家から計算した時刻表は、家のそばの停からの答えなので この欄は要らない
  if (s3.district && s3.district.isHome) { box.hidden = true; return; }

  // きょう(表示中の曜日)に行きの便が1本も無いときは、この欄を出さない
  // (のりかたカードが「きょうは 行きのバスの運行が ありません」と案内する)
  const cands = boardStopCandidates();
  if (cands.length === 0) { box.hidden = true; return; }
  box.hidden = false;

  const destName = s3.facility ? s3.facility.name : "この行き先";

  // まだ測っていないとき。のりかたカードの上に置く欄なので、ボタン1つ+説明1行に抑える
  const fix = geoFixFresh();
  if (!fix) {
    box.innerHTML =
      nearBoxButton("near-stop-btn", "📍 いちばん近いバス停をさがす", "near-btn near-use") +
      `<p class="near-note">${escapeHtml(destName)}へ行くバスが とまるバス停の中から さがします</p>`;
    box.querySelector("#near-stop-btn").addEventListener("click", measureNearStop);
    autoDetectIfAllowed();   // すでに許可されている端末は押さなくても出す
    return;
  }

  // 誤差が大きい測位(パソコンのIP推定など)では、バス停は選び間違える。
  // 「いちばん近い」と言い切らず、正直に伝えて従来の表示のままにする
  if (geoAccOf(fix) > GEO_ACC_STOP_M) {
    box.innerHTML =
      `<p class="near-far">いまいる場所が ${escapeHtml(accWord(geoAccOf(fix)))} ずれているため、` +
      `いちばん近いバス停を おしらせできません。パソコンでは おおよその場所しか ` +
      `わからないことがあります(スマートフォンだと うまくいきます)</p>` +
      nearBoxButton("near-stop-btn", "📍 もういちど しらべる", "near-btn near-remeasure");
    box.querySelector("#near-stop-btn").addEventListener("click", measureNearStop);
    return;
  }

  const list = nearBoardStops(fix);
  if (list.length === 0) {
    // 停留所の座標データが無い(再生成前)環境。できないことは正直に書く
    box.innerHTML =
      `<p class="near-note">バス停の場所のデータが ないため、いちばん近いバス停は しらべられませんでした</p>` +
      nearBoxButton("near-stop-btn", "📍 もういちど しらべる", "near-btn near-remeasure");
    box.querySelector("#near-stop-btn").addEventListener("click", measureNearStop);
    return;
  }

  const nearest = list[0];
  const rec = recommendedBoard();
  const shownBoards = [...new Set(s3Rows("outbound").map((r) => r.board))];
  const isShown = shownBoards.length === 1 && shownBoards[0] === nearest.stop;

  // この時刻表に載っていない、もっと近い停があるか(=代表点方式の限界に当たったか)。
  // 100m以上近いときだけ言う(GPSの誤差の範囲で騒がない)
  const truly = trueNearestStop(fix);
  const uncovered = truly && truly.stop !== nearest.stop && truly.dist + 100 < nearest.dist;

  let html =
    `<p class="near-lead">${uncovered ? "この時刻表で のれるバス停のうち、いちばん近いのは " : "いまいる場所から いちばん近いのは "}` +
    `<span class="near-stop">「${escapeHtml(nearest.stop)}」</span>` +
    `<span class="near-dist">${escapeHtml(distanceWord(nearest.dist))}</span></p>`;
  const nearMap = stopMapLinkHtml(nearest.stop, fix, nearest.p);
  if (nearMap) html += `<p class="map-link-row">${nearMap}</p>`;
  if (uncovered) {
    html +=
      `<p class="near-far">いまいる場所の すぐ近くには「${escapeHtml(truly.stop)}」` +
      `(${escapeHtml(distanceWord(truly.dist))})が ありますが、そのバス停から のる時刻表は ` +
      `まだ ありません。下の電話番号で ごそうだんください</p>`;
  }
  if (isShown) {
    html += `<p class="near-note">いまの時刻表は このバス停の ものです` +
      `(${escapeHtml(destName)}へ この日 ${nearest.trips}本)</p>`;
  } else {
    html += `<p class="near-note">${escapeHtml(destName)}へ行くバスが とまるバス停の中から` +
      ` えらびました(この日 ${nearest.trips}本)</p>` +
      nearBoxButton("near-use-btn", `「${nearest.stop}」から のる`, "near-btn near-use");
  }
  if (nearest.dist > 800 && !uncovered) {
    html +=
      `<p class="near-far">いちばん近くても およそ${escapeHtml(distanceWord(nearest.dist).replace("約", ""))} あります。` +
      `とおい場合は 下の電話番号に ごそうだんください</p>`;
  }
  if (s3.boardPick && rec && s3.boardPick !== rec) {
    html += nearBoxButton("near-reset-btn", `おすすめの「${rec}」に もどす`, "near-btn near-remeasure");
  }
  html += nearBoxButton("near-remeasure-btn", "📍 位置を もういちど しらべる", "near-btn near-remeasure");

  box.innerHTML = html;
  const use = box.querySelector("#near-use-btn");
  if (use) use.addEventListener("click", () => applyBoardPick(nearest.stop));
  const reset = box.querySelector("#near-reset-btn");
  if (reset) reset.addEventListener("click", () => applyBoardPick(null));
  box.querySelector("#near-remeasure-btn").addEventListener("click", measureNearStop);
}

// GPSで位置を測り直して、いちばん近いバス停を出す。
// 画面1の📍を押さずにQR・リンクから直接来た人も、ここだけで使えるようにする
function measureNearStop() {
  const box = nearStopBoxEl();
  if (!box) return;
  box.innerHTML = `<p class="near-lead">位置を しらべています…</p>`;
  const seq = s3.seq;   // 測っている間に別の行き先へ移ったら、結果は捨てる
  navigator.geolocation.getCurrentPosition(
    async (pos) => {
      state.geoFix = { lat: pos.coords.latitude, lon: pos.coords.longitude,
                       at: Date.now(), acc: pos.coords.accuracy };
      const idx = await getStopsIndex();
      if (seq !== s3.seq) return;
      s3.stopsIndex = idx;
      renderNearStopBox();
      renderRideCard(new Date());   // ①の「いまの場所から およそ◯m」も出す
    },
    () => {
      if (seq !== s3.seq) return;
      box.innerHTML =
        `<p class="near-lead">位置情報が つかえませんでした</p>` +
        nearBoxButton("near-stop-btn", "📍 もういちど しらべる", "near-btn");
      box.querySelector("#near-stop-btn").addEventListener("click", measureNearStop);
    },
    // バス停どうしは100〜300mしか離れていないので、地区をえらぶときの測位より
    // 高い精度が要る。「もういちど しらべる」で古い位置が返らないよう maximumAge は0
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }
  );
}

// ①歩く→②乗る→(のりかえ)→③降りる のステップを組み立てる(R1〜R6)
function rideStepsHtml(r, dir) {
  const marks = ["①", "②", "③", "④", "⑤"];
  const steps = [];

  // ① バス停まで歩く。徒歩分は行き=家から/帰り=施設から(2026-07-10から帰りも
  // 実停名+徒歩分。施設の最寄りでない停から乗る便があるため)。
  // 0分(バス停がすぐそこ)のときは「約0分」という変な表示をしない。
  // r.board_walk_min はこの便が実際に使う乗車停留所までの徒歩分(便によって
  // 乗る停留所が変わることがあるため、地区共通の値ではなく便ごとの値を使う)
  // 徒歩分は地区の代表点からの目安。利用者がGPSで乗車バス停を選んだときは、
  // 実測の「いまの場所から およそ◯m」(下の geoNote)のほうが正確なので、
  // 基準の違う2つの数字を並べない(代表点からの目安は出さない)
  // 距離の併記は、バス停をえらべる精度で測れているときだけ(誤差数kmの位置から
  // 「およそ200m」と書くと嘘になる)
  const fixForDist = geoFixFresh();
  const preciseFix = fixForDist && geoAccOf(fixForDist) <= GEO_ACC_STOP_M ? fixForDist : null;
  const gpsPicked = Boolean(s3.boardPick) && !(s3.district && s3.district.isHome) &&
    stopDistanceM(r.board, preciseFix, s3.stopsIndex) !== null;
  let walk = "";
  if (r.board_walk_min >= 1 && !(dir === "outbound" && gpsPicked)) {
    walk = ` <span class="walk-note">あるいて約${r.board_walk_min}分</span>`;
  }
  const platform = r.platform
    ? ` <span class="platform-badge">${escapeHtml(platformText(r.platform))}番のりば</span>`
    : "";
  // 同じバスが通る、家の近くの別の停を①の補足として併記(A案。2026-07-08 開発者指摘
  // 「地区内に停が多いとき、同じバスが近くの停にも停まるのに1停しか出ないのは違和感」)。
  // r.board(主停)以外の board_options を「同じバス」として発車時刻つきで添える。
  // 行き(自宅側)のみ・近い順に最大3件。番号は振らず①の中の小さな注記にする
  let siblingNote = "";
  if (dir === "outbound" && Array.isArray(r.board_options)) {
    const others = r.board_options.filter((o) => o.stop !== r.board).slice(0, 3);
    if (others.length) {
      const list = others.map((o) => `${escapeHtml(o.stop)}(${o.dep}発)`).join("・");
      siblingNote =
        `<div class="sibling-note">同じバスは ${list} にも とまります。` +
        `ちかいバス停で のってください</div>`;
    }
  }
  // 対策1(広い地区): GPSで測ったばかりの位置があれば、この乗車停までの距離を
  // 正直に併記する。「あるいて約◯分」は地区の代表点からの分数なので、
  // 東沢地区のような広い地区では実際の家からの遠さを隠してしまう——
  // その補正がこの1行。遠い(800m=徒歩約17分の目安を超える)ときは注意を出し、
  // 下の電話番号欄(デマンド交通・市の窓口)に誘導する
  // わが家から計算した時刻表は、徒歩分そのものが家からの実際の値なので、この補正は要らない
  let geoNote = "";
  const fromHome = Boolean(s3.district && s3.district.isHome);
  const dist = dir === "outbound" && !fromHome ? stopDistanceM(r.board, preciseFix, s3.stopsIndex) : null;
  if (dist !== null) {
    if (dist > 800) {
      geoNote =
        `<div class="geo-dist far-note">いまの場所から このバス停まで およそ${escapeHtml(distanceWord(dist).replace("約", ""))} あります。` +
        `とおい場合は 下の電話番号に ごそうだんください</div>`;
    } else {
      geoNote =
        `<div class="geo-dist">いまの場所から およそ${escapeHtml(distanceWord(dist).replace("約", ""))}</div>`;
    }
  }
  const boardRef = dir === "outbound" ? (preciseFix || s3.district) : s3.facility;
  const mapLink = stopMapLinkHtml(r.board, boardRef, r.bp);
  steps.push(`「${escapeHtml(r.board)}」バス停へ${walk}${platform}${siblingNote}${geoNote}` +
    (mapLink ? `<div class="map-link-row">${mapLink}</div>` : ""));

  // ② 正面表示(headsign)が主役。系統番号は小さな「かくにん」(R1・R2)
  steps.push(
    `正面に <span class="headsign">${escapeHtml(headsignLabel(r.headsign))}</span> と` +
    `でているバスに のる${confirmLineHtml(r.route)}`
  );

  // のりかえ(R4): どこで降りて、次に何行きに乗るか
  if (r.transfer) {
    steps.push(
      // 降りる停と乗る停が違うときは、歩いて乗り換えることを書く(transfer.off。2026-10-07)
      (r.transfer.off
        ? `「${escapeHtml(r.transfer.off)}」で おりて、「${escapeHtml(r.transfer.at)}」まで あるいて、<br>`
        : `「${escapeHtml(r.transfer.at)}」で おりて、<br>`) +
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
  // 帰り(inbound)は、同じバスが家の近くで降りられる別の停を併記する(行きの
  // 乗り場併記と対称。2026-07-08 開発者要望)。主停(r.alight)以外を近い順に最大3件
  let alightSiblingNote = "";
  if (dir === "inbound" && Array.isArray(r.alight_options)) {
    // 帰りは乗車中なので「どの停が家に近いか」が要る情報。到着時刻でなく徒歩分で見せる
    // (featuredの「着」は徒歩後の家の到着時刻。時刻を混ぜると基準がずれて紛らわしい)
    const others = r.alight_options.filter((o) => o.stop !== r.alight).slice(0, 3);
    if (others.length) {
      const list = others
        .map((o) => (o.walk_min >= 1 ? `${escapeHtml(o.stop)}(あるいて約${o.walk_min}分)` : `${escapeHtml(o.stop)}`))
        .join("・");
      alightSiblingNote =
        `<div class="sibling-note">同じバスは ${list} でも おりられます。` +
        `ちかいバス停で おりてください</div>`;
    }
  }
  steps.push(`「${escapeHtml(r.alight)}」で おりる${placeNote} <span class="ride-note">${rideNote}</span>` +
    ` <span class="arr-note">${timeWord(r.arr)} 着</span>${alightSiblingNote}`);

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

// boxId: 書き込む欄(画面3の電話番号欄が既定。画面2の「どこへも行けない」案内でも使う)
function renderPhoneBox(district, operators = [], boxId = "phone-box") {
  const box = document.getElementById(boxId);
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

  // 2. デマンド交通(対象地区のみ)。meta側の対象一覧は親地区名で書かれているので、
  //    サブ地区(例: 東沢地区(ひがし))は親の名前で照合する
  const demandName = district.parent ? district.parent.name : district.name;
  const demand = meta.demand_phone.find(
    (p) => Array.isArray(p.districts) && p.districts.includes(demandName)
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
      text = s3Rows("outbound").length > 0
        ? "本日の便は、おわりました。"
        : "きょうは、行きのバスの運行が、ありません。";
    } else {
      const parts = [];
      if (!s3.manual && s3.sel.dir === "outbound") {
        const wait = hmToMin(ride.dep) - (now.getHours() * 60 + now.getMinutes());
        parts.push(`つぎのバスは、${timeSpeech(ride.dep)}、あと${wait}分です。`);
      } else if (s3.tomorrowView) {
        parts.push(`あしたのバスは、${timeSpeech(ride.dep)}です。`);
      } else {
        parts.push(`えらんだバスは、${timeSpeech(ride.dep)}です。`);
      }
      const hsWord = String(ride.headsign).replace(/(行き|ゆき)$/, "");
      parts.push(`${ride.board}バス停から、${hsWord}行きに、のってください。`);
      // 徒歩分は行き=家から/帰り=施設から(帰りも実停名+徒歩分。2026-07-10)。
      // GPSで乗車バス停を選んでいるときは、代表点からの目安ではなく実測の距離を読む
      const spFix = geoFixFresh();
      const spDist = s3.sel.dir === "outbound" && s3.boardPick && spFix
                     && geoAccOf(spFix) <= GEO_ACC_STOP_M
        ? stopDistanceM(ride.board, spFix, s3.stopsIndex) : null;
      if (spDist !== null) {
        parts.push(`バス停までは、いまいる場所から、${distanceWord(spDist).replace("約", "およそ")}です。`);
      } else if (ride.board_walk_min >= 1) {
        parts.push(`バス停までは、あるいて約${ride.board_walk_min}分です。`);
      }
      if (ride.transfer) {
        const hs2Word = String(ride.transfer.headsign2).replace(/(行き|ゆき)$/, "");
        parts.push(ride.transfer.off
          ? `${ride.transfer.off}で おりて、${ride.transfer.at}まで あるいて、${hs2Word}行きに、のりかえてください。`
          : `${ride.transfer.at}で おりて、${hs2Word}行きに、のりかえてください。`);
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
  if (did === HOME_ID && !loadHome()) {
    if (s3.timer) { clearInterval(s3.timer); s3.timer = null; }
    renderScreen1();
    renderHomeRow("わが家が とうろく されていません。家に いるときに 「いまいる場所を わが家にする」を おしてください");
    return;
  }
  if (did) {
    const d = findDistrict(did);
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
    fetch("../data/districts.json", DATA_FETCH).then((r) => r.json()),
    fetch("../data/destinations.json", DATA_FETCH).then((r) => r.json()),
    fetch("../data/meta.json", DATA_FETCH).then((r) => r.json()),
  ]);
  document.getElementById("app").hidden = false;
  wireStaticHandlers();
  setupGeoButton();
  window.addEventListener("hashchange", route);
  await route();
}

init();
