# -*- coding: utf-8 -*-
"""
ステップ4: 上山市営バスのGTFSデータをダウンロード・解凍し、
バス停を1つ選んで平日の時刻表を表示するスクリプト。

GTFSの基本構造(zipの中に入っているテキストファイル):
  - stops.txt      … バス停の一覧(ID・名前・緯度経度)
  - stop_times.txt … 「どの便が・どのバス停に・何時に着くか」の一覧
  - trips.txt      … 便(トリップ)の一覧。路線IDと運行日IDを持つ
  - calendar.txt   … 運行日パターン(月〜日のどの曜日に走るか)
  - routes.txt     … 路線の一覧(路線名など)

実行方法:  python3 step4_timetable.py
"""

import io
import zipfile
from pathlib import Path

import requests
import pandas as pd

# ---------------------------------------------------------------
# 4-1: APIで上山市のフィード情報を探して、zipのURLを取得
# ---------------------------------------------------------------
res = requests.get("https://api.gtfs-data.jp/v2/files", timeout=60)
res.raise_for_status()
feeds = res.json()["body"]

# リスト内包表記: 全フィードから「事業者名が上山市」のものだけ取り出す
kaminoyama = [f for f in feeds if f["organization_name"] == "上山市"][0]
print(f"対象フィード: {kaminoyama['feed_name']}({kaminoyama['organization_name']})")
print(f"有効期間: {kaminoyama['file_from_date']} 〜 {kaminoyama['file_to_date']}")

# ---------------------------------------------------------------
# 4-2: zipをダウンロードして gtfs_kaminoyama フォルダに解凍
# ---------------------------------------------------------------
zip_res = requests.get(kaminoyama["file_url"], timeout=120)
zip_res.raise_for_status()

out_dir = Path("gtfs_kaminoyama")
# io.BytesIO はダウンロードしたバイト列を「ファイルのように」扱うための道具。
# 一度ディスクに保存しなくても、そのままzipとして開ける
with zipfile.ZipFile(io.BytesIO(zip_res.content)) as zf:
    zf.extractall(out_dir)
print(f"\n解凍したファイル: {sorted(p.name for p in out_dir.iterdir())}")

# ---------------------------------------------------------------
# 4-3: 必要なファイルをpandasで読み込む
# ---------------------------------------------------------------
# GTFSはただのCSVなので read_csv で読める。IDは数字に見えても文字列として
# 扱うのが安全(先頭の0が消えたりするのを防ぐため dtype=str を指定)
stops      = pd.read_csv(out_dir / "stops.txt", dtype=str)
stop_times = pd.read_csv(out_dir / "stop_times.txt", dtype=str)
trips      = pd.read_csv(out_dir / "trips.txt", dtype=str)
calendar   = pd.read_csv(out_dir / "calendar.txt", dtype=str)
routes     = pd.read_csv(out_dir / "routes.txt", dtype=str)

print(f"\nバス停数: {len(stops)} / 時刻データ数: {len(stop_times)} / 便数: {len(trips)}")

# ---------------------------------------------------------------
# 4-4: 「平日に運行する」service_id を calendar.txt から特定する
# ---------------------------------------------------------------
# calendar.txt には monday〜sunday の列があり、"1"ならその曜日に運行する。
# 月〜金がすべて"1"の行を「平日ダイヤ」とみなす
weekday_cols = ["monday", "tuesday", "wednesday", "thursday", "friday"]
is_weekday = (calendar[weekday_cols] == "1").all(axis=1)  # 5列とも"1"ならTrue
weekday_service_ids = calendar[is_weekday]["service_id"]
print(f"\n平日運行の service_id: {list(weekday_service_ids)}")

# ---------------------------------------------------------------
# 4-5: バス停を1つ選ぶ(ここでは「発着回数が最も多いバス停」を自動で選ぶ)
# ---------------------------------------------------------------
busiest_stop_id = stop_times["stop_id"].value_counts().idxmax()
stop_row = stops[stops["stop_id"] == busiest_stop_id].iloc[0]
print(f"\n選んだバス停: {stop_row['stop_name']}(stop_id={busiest_stop_id})")

# ---------------------------------------------------------------
# 4-6: 平日ダイヤ×選んだバス停 で時刻表を作る
# ---------------------------------------------------------------
# merge() は2つの表を共通の列(キー)でつなげる操作(Excelの VLOOKUP に近い)。
# stop_times に trips をつなげて、各時刻データに service_id と route_id を付ける
tt = stop_times.merge(trips, on="trip_id")

# 「選んだバス停」かつ「平日の service_id」の行だけに絞り込む
tt = tt[(tt["stop_id"] == busiest_stop_id) & (tt["service_id"].isin(weekday_service_ids))]

# routes をつなげて路線名も表示できるようにする
tt = tt.merge(routes, on="route_id")
# 路線名は route_long_name か route_short_name のどちらかに入っていることが多い
tt["路線名"] = tt["route_long_name"].fillna(tt["route_short_name"])

# 出発時刻順に並べ替えて、見やすい列だけにする
timetable = (
    tt[["departure_time", "路線名", "trip_headsign"]]
    .rename(columns={"departure_time": "発車時刻", "trip_headsign": "行き先"})
    .sort_values("発車時刻")
    .reset_index(drop=True)
)

print(f"\n=== {stop_row['stop_name']} 平日時刻表({len(timetable)}本) ===")
print(timetable.to_string())
