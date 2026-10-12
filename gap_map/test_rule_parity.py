# -*- coding: utf-8 -*-
"""工場(export_web_data.py)と端末(webapp/engine/timetable.js)の「便を落とす決まり」が、
同じ手作りの便で同じ答えを出すかの検査(2026-10-12 計画B・C)。

実データ全件の照合(gap_map/verify_timetable_parity.js)は、データを作り直してからでないと回せない。
こちらは GTFS も地区ファイルも要らず、決まりを変えた直後に Python と JS のずれに気づける。
必要: node(無ければスキップ)
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from export_web_data import drop_same_bus_earlier_rows, drop_slower_than_walk_rows

ROOT = Path(__file__).parent.parent
ENGINE = ROOT / "webapp" / "engine" / "timetable.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node が無い")


def run_js(func: str, *args):
    """timetable.js の関数を node で呼び、答え(JSON)を返す"""
    code = (f"const T = require({json.dumps(str(ENGINE))});"
            f"const a = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
            f"process.stdout.write(JSON.stringify(T.{func}(...a)));")
    proc = subprocess.run(["node", "-e", code], input=json.dumps(list(args), ensure_ascii=False),
                          capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def row(dep, arr, board, walk, opts=None, transfer=None, headsign="H", route="R", alight="X"):
    r = {"dep": dep, "arr": arr, "board": board, "board_walk_min": walk, "alight": alight,
         "headsign": headsign, "route": route, "platform": "1", "ride_min": 10, "transfer": transfer}
    if opts is not None:
        r["board_options"] = [{"stop": s, "dep": d, "walk_min": w} for s, d, w in opts]
    return r


def tr(wait, walk=0, hs2="H2", r2="R2"):
    return {"at": "T", "wait_min": wait, "walk_min": walk, "headsign2": hs2, "route2": r2}


SAME_BUS_CASES = [
    # 「ひと回り」: 山交ビルを外して保健所前へ付け替え
    [row("16:56", "17:58", "山交ビル", 10, [("山交ビル", "16:56", 10), ("保健所前", "16:54", 17)],
         tr(2, 3, "わくわくランド", "N50"), alight="長岡南町"),
     row("17:15", "17:58", "山交ビル", 10, [("山交ビル", "17:15", 10), ("山形駅前", "17:17", 13)],
         None, "わくわくランド", "N50", alight="長岡南町")],
    # 乗り場ごとに別の行に負ける
    [row("08:00", "09:00", "A", 5, [("A", "08:00", 5), ("B", "08:02", 5)], tr(30)),
     row("08:20", "09:00", "A", 5, [("A", "08:20", 5)], tr(10)),
     row("08:22", "09:00", "B", 5, [("B", "08:22", 5)], tr(10))],
    # 同点は待ちの短い行・先の行
    [row("08:00", "09:00", "A", 5, [("A", "08:00", 5)], tr(10)),
     row("08:00", "09:00", "A", 5, [("A", "08:00", 5)], tr(5)),
     row("08:00", "09:00", "A", 5, [("A", "08:00", 5)], tr(5))],
    # 候補なしの古い形の行・別のバスは比べない
    [row("07:00", "08:00", "A", 3, None, None),
     row("07:10", "08:00", "A", 3, None, None),
     row("07:20", "08:00", "A", 3, None, None, headsign="別のバス")],
]


@pytest.mark.parametrize("rows", SAME_BUS_CASES)
def test_drop_same_bus_earlier_rows_python_and_js_agree(rows):
    assert run_js("dropSameBusEarlierRows", rows) == json.loads(json.dumps(drop_same_bus_earlier_rows(rows)))


SLOWER_CASES = [
    # 中川(きた)→みゆき会病院の型: 06:10(85分)は 07:30 発・07:35 着に負ける → 落ちる
    ([row("06:10", "07:35", "リナワールド前", 13, None, tr(47)),
      row("07:30", "07:35", "金瓶南", 10, None, None),
      row("08:13", "08:18", "金瓶南", 10, None, None)], 21.4),
    # それしか無い早朝の便(全部歩くより遅いが、負かす便が無い)は残る
    ([row("05:00", "06:30", "A", 5, None, tr(40)),
      row("07:30", "07:40", "A", 5, None, None)], 30.0),
    # ちょうど全部歩くのと同じ(遅くはない)便は残る
    ([row("07:00", "07:25", "A", 5, None, None),
      row("07:10", "07:20", "A", 5, None, None)], 30.0),
]


@pytest.mark.parametrize("rows,walk_all", SLOWER_CASES)
def test_drop_slower_than_walk_rows_python_and_js_agree(rows, walk_all):
    assert run_js("dropSlowerThanWalkRows", rows, walk_all) == drop_slower_than_walk_rows(rows, walk_all)


def test_drop_slower_than_walk_rows_examples():
    rows, walk_all = SLOWER_CASES[0]
    assert [r["dep"] for r in drop_slower_than_walk_rows(rows, walk_all)] == ["07:30", "08:13"]
    rows, walk_all = SLOWER_CASES[1]
    assert [r["dep"] for r in drop_slower_than_walk_rows(rows, walk_all)] == ["05:00", "07:30"]
    rows, walk_all = SLOWER_CASES[2]
    assert [r["dep"] for r in drop_slower_than_walk_rows(rows, walk_all)] == ["07:00", "07:10"]
