# -*- coding: utf-8 -*-
"""山形済生病院の無料シャトルバスを、手作りのGTFSにする(2026-10-07 開発者要望)。

出典: 山形済生病院「山形済生病院シャトルバス時刻表」(令和8年10月からの時刻表)
      https://www.ameria.org/8239128ea1810b24b254343b3ca55bf4fed22265.pdf
      https://www.ameria.org/access/(交通・アクセス)
      ※オープンデータではない(病院の案内)。応募・公開の前に病院の了解を取り、出典を明記する

案内に書かれた条件:
  - 無料。土曜・日曜・祝日・10/15(創立記念日)・年末年始(12/29〜1/3)は運休
  - 「病院行きのシャトルバスでは途中下車できません」
  → 市内の停どうしの移動には使えない。そこで便を「乗り場→病院」「病院→降り場」の
    **2停だけの便**に分けて書く(エンジンが市内どうしの区間に使うことがなくなる)。
    さらに、このフィードは済生病院への行き・帰りの計算にだけ使う
    (region.py の restricted_feeds。export_web_data.py が別のネットワークで計算する)

時刻表が変わったら: 下の LOOP_TRIPS / YAMANOBE_TRIPS を直して実行し直す
    python3 gap_map/make_saisei_shuttle.py
→ feeds_manual/済生病院シャトル/ に GTFS が書き出される(git に入れる)。

停留所の位置(STOPS)の確かさ:
  正確   … 山形済生病院(既存GTFSの停)、山形市役所前6番のりば(山交バスのGTFSの6番)
  ほぼ   … 北山形駅西口(栄屋分店。OpenStreetMap)、山辺(スマイルやまのべ)・
            鮨洗 簡易郵便局(OpenStreetMap)
  要確認 … 山交ビル(花屋前)=近くの「山交ビル前」停、山形駅(交番前)=山形駅前のりばの中ほど、
            長町(長町酒店)=近くの「長町」停
  外した … おーばん山辺店(位置が見つからない。1分後に停まるスマイルやまのべで乗れる)
"""
import csv
from datetime import date, timedelta
from pathlib import Path

import jpholiday

PROJECT_ROOT = Path(__file__).parent.parent
OUT_DIR = PROJECT_ROOT / "feeds_manual" / "済生病院シャトル"

START = date(2026, 10, 1)    # 令和8年10月からの時刻表
END = date(2027, 3, 31)      # 終わりは案内に無い。年度末までとし、変わったら作り直す

# 停留所: id → (表示名, 緯度, 経度)。表示名は画面に出るので、目印を括弧で添える
STOPS = {
    "hospital": ("山形済生病院(シャトルバス)", 38.28464, 140.33501),
    "shiyakusho": ("山形市役所前(シャトルバス・6番のりば)", 38.25502, 140.34033),
    "yamako": ("山交ビル(シャトルバス・花屋前)", 38.24693, 140.33199),
    "eki": ("山形駅(シャトルバス・交番前)", 38.24890, 140.32850),
    "kitayamagata": ("北山形駅西口(シャトルバス・栄屋分店前)", 38.26505, 140.33167),
    "yamanobe": ("山辺(シャトルバス・スマイルやまのべ)", 38.29174, 140.26720),
    "susuarai": ("鮨洗 簡易郵便局(シャトルバス)", 38.29055, 140.27958),
    "nagamachi": ("長町(シャトルバス・長町酒店)", 38.28880, 140.33459),
}

# 【山形市内循環ルート】病院 → 市役所 → 山交ビル → 山形駅 → 北山形駅西口 → 病院。
# None は「通らない」。①は山交ビル始発、⑨は山形駅止まり
LOOP_STOPS = ["hospital", "shiyakusho", "yamako", "eki", "kitayamagata", "hospital"]
LOOP_TRIPS = [
    [None, None, "7:30", "7:33", "7:41", "7:49"],
    ["8:00", "8:13", "8:18", "8:21", "8:29", "8:37"],
    ["10:00", "10:13", "10:18", "10:21", "10:29", "10:37"],
    ["11:00", "11:13", "11:18", "11:21", "11:29", "11:37"],
    ["12:00", "12:13", "12:18", "12:21", "12:29", "12:37"],
    ["14:00", "14:13", "14:18", "14:21", "14:29", "14:37"],
    ["16:00", "16:13", "16:18", "16:21", "16:29", "16:37"],
    ["17:00", "17:13", "17:18", "17:21", "17:29", "17:37"],
    ["17:50", "18:03", "18:08", "18:11", None, None],
]

# 【山辺ルート】①病院行き ②病院発(おーばん山辺店は位置不明のため外した)
YAMANOBE_TRIPS = [
    ("to", [("yamanobe", "9:11"), ("susuarai", "9:12"), ("nagamachi", "9:32"), ("hospital", "9:37")]),
    ("from", [("hospital", "15:00"), ("nagamachi", "15:05"), ("susuarai", "15:25"), ("yamanobe", "15:26")]),
]

ROUTE_NAME = "済生病院 無料シャトルバス(10/15・年末年始は運休)"


def hhmmss(t: str) -> str:
    h, m = t.split(":")
    return f"{int(h):02d}:{int(m):02d}:00"


def two_stop_trips():
    """途中下車できないので、1本の運行を「乗り場→病院」「病院→降り場」の2停の便に分ける。
    戻り値: [(trip_id, headsign, [(stop_id, 時刻), (stop_id, 時刻)]), ...]"""
    out = []
    for n, times in enumerate(LOOP_TRIPS, start=1):
        visits = [(s, t) for s, t in zip(LOOP_STOPS, times) if t]
        start_is_hospital = visits[0][0] == "hospital"
        end_is_hospital = visits[-1][0] == "hospital"
        if start_is_hospital:   # 病院を出て市内で降りる(帰り)
            h_dep = visits[0][1]
            last = len(visits) - 1 if end_is_hospital else len(visits)
            for s, t in visits[1:last]:
                out.append((f"loop{n}_from_{s}", "山形市内(山形駅・山交ビル方面)",
                            [("hospital", h_dep), (s, t)]))
        if end_is_hospital:     # 市内で乗って病院へ(行き)
            h_arr = visits[-1][1]
            first = 1 if start_is_hospital else 0
            for s, t in visits[first:-1]:
                out.append((f"loop{n}_to_{s}", "山形済生病院", [(s, t), ("hospital", h_arr)]))
    for direction, visits in YAMANOBE_TRIPS:
        if direction == "to":
            h_arr = visits[-1][1]
            for s, t in visits[:-1]:
                out.append((f"yamanobe_to_{s}", "山形済生病院", [(s, t), ("hospital", h_arr)]))
        else:
            h_dep = visits[0][1]
            for s, t in visits[1:]:
                out.append((f"yamanobe_from_{s}", "山辺方面", [("hospital", h_dep), (s, t)]))
    return out


def closed_weekdays() -> list:
    """平日のうち運休の日(祝日・10/15・12/29〜1/3)"""
    days = []
    d = START
    while d <= END:
        if d.weekday() < 5 and (jpholiday.is_holiday(d) or (d.month, d.day) == (10, 15)
                                or (d.month == 12 and d.day >= 29) or (d.month == 1 and d.day <= 3)):
            days.append(d)
        d += timedelta(days=1)
    return days


def write(name: str, header: list, rows: list):
    with open(OUT_DIR / name, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write("agency.txt", ["agency_id", "agency_name", "agency_url", "agency_timezone", "agency_phone"],
          [["saisei", "山形済生病院", "https://www.ameria.org/access/", "Asia/Tokyo", "023-682-1111"]])
    write("stops.txt", ["stop_id", "stop_name", "stop_lat", "stop_lon"],
          [[sid, name, f"{lat:.5f}", f"{lon:.5f}"] for sid, (name, lat, lon) in STOPS.items()])
    write("routes.txt", ["route_id", "agency_id", "route_short_name", "route_long_name", "route_type"],
          [["shuttle", "saisei", "シャトル", ROUTE_NAME, "3"]])
    trips = two_stop_trips()
    write("trips.txt", ["route_id", "service_id", "trip_id", "trip_headsign"],
          [["shuttle", "weekday", tid, hs] for tid, hs, _ in trips])
    write("stop_times.txt", ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"],
          [[tid, hhmmss(t), hhmmss(t), s, i + 1] for tid, _, visits in trips for i, (s, t) in enumerate(visits)])
    write("calendar.txt", ["service_id", "monday", "tuesday", "wednesday", "thursday", "friday",
                           "saturday", "sunday", "start_date", "end_date"],
          [["weekday", 1, 1, 1, 1, 1, 0, 0, START.strftime("%Y%m%d"), END.strftime("%Y%m%d")]])
    closed = closed_weekdays()
    write("calendar_dates.txt", ["service_id", "date", "exception_type"],
          [["weekday", d.strftime("%Y%m%d"), 2] for d in closed])
    write("feed_info.txt", ["feed_publisher_name", "feed_publisher_url", "feed_lang",
                            "feed_start_date", "feed_end_date", "feed_version"],
          [["山形済生病院の時刻表(PDF)から手作業で作成", "https://www.ameria.org/access/", "ja",
            START.strftime("%Y%m%d"), END.strftime("%Y%m%d"), "2026-10"]])
    print(f"→ {OUT_DIR}: 停留所{len(STOPS)}・便{len(trips)}(2停ずつ)・運休日{len(closed)}日")


if __name__ == "__main__":
    main()
