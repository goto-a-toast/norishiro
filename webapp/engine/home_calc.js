// -*- coding: utf-8 -*-
// 案D 段階3-4: 画面(かんたんモード)が「わが家から」の時刻表を頼む窓口。
//
// 画面ごとに必要な分だけ計算し、計算した行きの便は覚えておく
// (docs/plan_stage3_kantan.md §5.3。画面3の計算が重くならないように):
//   outbound(home, facilities)  … 画面2。全行き先の「行き」を3ダイヤぶん。施設一覧の目安と、
//                                  おすすめの乗り場(3ダイヤを見て選ぶ)に使う
//   entry(home, facilities, fid) … 画面3。選んだ行き先の「帰り」を足して、エントリを完成させる
// どちらも地区ファイルと同じ形 {pts, to} を返すので、画面は地区から来た答えと区別せずに表示できる。
//
// Web Worker(engine/worker.js)の中でも、画面と同じ場所でも動く(Worker が使えない環境の予備)。
// ネットワークの読み込みは最初の1回だけ。済生病院のシャトル入り(restricted)も一緒に読む。

/* global inflateNetwork, TimetableEngine */
(function (root) {
"use strict";

class HomeCalc {
  // base: 配布ネットワークのフォルダ(このファイルを読んだページ/Worker から見た相対パス)
  constructor(base) {
    this.base = base;
    this.ready = null;      // ネットワークを読み終えたら解決する Promise
    this.nets = null;       // {ダイヤ種別: ネットワーク}
    this.restricted = [];   // [{facilities, nets}]
    this.cache = null;      // {key: わが家の位置, raw: 生の答え}
  }

  load() {
    if (!this.ready) this.ready = this._load();
    return this.ready;
  }

  async _load() {
    const E = TimetableEngine;
    const get = async (name) => {
      const r = await fetch(this.base + name, { cache: "no-cache" });
      if (!r.ok) throw new Error(`ネットワークを読み込めませんでした(${name})`);
      return E.prepareNetwork(inflateNetwork(await r.json()));
    };
    const nets = {};
    await Promise.all(E.DAY_TYPES.map(async (dt) => { nets[dt] = await get(`${dt}.json`); }));
    this.restricted = await Promise.all(nets.weekday.restricted.map(async (r, i) => {
      const rn = {};
      await Promise.all(E.DAY_TYPES.map(async (dt) => { rn[dt] = await get(nets[dt].restricted[i].file); }));
      return { facilities: r.facilities, nets: rn };
    }));
    this.nets = nets;
  }

  static key(home) { return `${home.lat},${home.lon}`; }

  // 画面2: 全行き先の行き(帰りは空のまま)。同じ家なら2回目からは計算しない
  async outbound(home, facilities) {
    await this.load();
    const E = TimetableEngine;
    const key = HomeCalc.key(home);
    if (!this.cache || this.cache.key !== key) {
      this.cache = { key, raw: E.computeHomeRaw(this.nets, home, facilities, ["outbound"], this.restricted) };
    }
    return E.assembleHomeTimetable(this.cache.raw, home, facilities, this.nets.weekday.config);
  }

  // 画面3: 選んだ行き先(fid)の帰りを足して、その行き先のエントリを返す
  async entry(home, facilities, fid) {
    await this.outbound(home, facilities);
    const E = TimetableEngine;
    const f = facilities.find((x) => x.id === fid);
    if (!f) throw new Error(`行き先 ${fid} がありません`);
    const raw = this.cache.raw;
    if (!raw.weekday.inbound[fid]) {
      const back = E.computeHomeRaw(this.nets, home, [f], ["inbound"], this.restricted);
      for (const dt of Object.keys(back)) raw[dt].inbound[fid] = back[dt].inbound[fid] || [];
    }
    return E.assembleHomeTimetable(raw, home, [f], this.nets.weekday.config);
  }
}

if (typeof module !== "undefined" && module.exports) module.exports = { HomeCalc };
else root.HomeCalc = HomeCalc;
})(typeof self !== "undefined" ? self : globalThis);
