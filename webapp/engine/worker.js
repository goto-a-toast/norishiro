// -*- coding: utf-8 -*-
// 案D 段階3-4: 「わが家から」の時刻表を、画面とは別の裏の流れ(Web Worker)で計算する。
// 都心の家では計算に最大0.5秒ほどかかる(スマホでは2秒前後)ので、その間も画面が固まらないようにする。
//
// やりとり: 画面から {id, op: "outbound" | "entry", args: [...]} を受け取り、
//           HomeCalc の同名の関数の答えを {id, ok: true, result} で返す(失敗は {id, ok: false, error})。
// ファイルの場所はこの worker.js から見た相対パス(engine/ の中のファイルと ../data/network/)。

/* global importScripts, HomeCalc */
importScripts("raptor.js?v=20261008a", "network.js?v=20261008a",
              "timetable.js?v=20261008a", "home_calc.js?v=20261008a");

const calc = new HomeCalc("../data/network/");

self.onmessage = async (e) => {
  const { id, op, args } = e.data;
  try {
    if (op !== "outbound" && op !== "entry") throw new Error(`知らない頼みごとです(${op})`);
    const result = await calc[op](...args);
    self.postMessage({ id, ok: true, result });
  } catch (err) {
    self.postMessage({ id, ok: false, error: String(err && err.message || err) });
  }
};
