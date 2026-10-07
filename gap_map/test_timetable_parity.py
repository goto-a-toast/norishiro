"""案D 段階3-2: 端末で作る時刻表(webapp/engine/timetable.js)が地区ファイルと一致することの自動検査。

全件の照合(全62地区・約15万便)は gap_map/verify_timetable_parity.js を直接回す(約20秒)。
ここでは、pytest をいつ回しても崩れに気づけるよう、性質の違う3地区だけを照合する:
  d19 第一地区   … 都心。乗り場が多く、乗換・乗り場の絞り込みがいちばん効く
  d24 第五地区   … 曜日ごとにおすすめ乗り場が変わる組を含む
  d01 みはらしの丘 … 郊外。便が少なく、行けない行き先がある

必要: node と、配布ネットワーク・地区ファイル(webapp/data/)。無い環境ではスキップする。
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
VERIFY_JS = ROOT / "gap_map" / "verify_timetable_parity.js"
DATA = ROOT / "webapp" / "data"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None
    or not (DATA / "network" / "weekday.json").exists()
    or not (DATA / "timetables" / "d19.json").exists(),
    reason="node か webapp/data が無いので照合できない")


def test_home_timetable_matches_district_files():
    # --home d24: 画面が実際に呼ぶ「わが家1軒」の入口でも同じ答えになることを確かめる
    proc = subprocess.run(["node", str(VERIFY_JS), "d19", "d24", "d01", "--home"],
                          capture_output=True, text=True, timeout=600, cwd=ROOT)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-2000:]
    assert "すべて一致" in proc.stdout
