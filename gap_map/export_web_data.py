# -*- coding: utf-8 -*-
"""
F3: データ工場(docs/plan_final_sprint.md §3・§6・F3)。

「地区」×「行き先」の組み合わせごとに、平日・土曜・日曜祝日それぞれの
全便時刻表(乗換1回まで)を事前計算し、Webアプリ用のJSON一式を書き出す。

★最重要の設計原則: このスクリプトは凍結済みの分析資産(transit_core.py の
RAPTORエンジン、build_network.py のGTFS読み込み)を import して使うだけで、
中身は一切変更しない(docs/plan_final_sprint.md 冒頭の前提)。

計算量を抑える工夫(§2の見積りとの整合):
  「地区の代表停留所から出発して、その日1日に乗れる便すべてを1回ずつ試す」処理を
  行き先ごとに繰り返すと(地区数×行き先数×候補時刻)回のRAPTOR探索が要るが、
  RAPTOR探索1回の結果には「その時刻に出発した場合の全停留所への最速到着」が
  すべて含まれている。そこで探索は(地区×曜日タイプ)の組み合わせごとに1回だけ行い、
  その結果を全行き先ぶん使い回す(逆方向=行き先発のときも同様に行き先側で1回で済ませる)。

便数の間引き方針(2026-07-06 に開発者と合意):
  七日町(山交バスの基幹停留所)のように1日100便を超える停留所があり、特に
  乗換ルートは「乗換地点×乗継系統」の組み合わせが多く、全通り出力すると
  1地区あたりのデータ量が計画書の上限(250KB)を大幅に超える。
  最終方針は「利用者に見える違いだけ残す」:
    - 直通便(紙の時刻表の本体)は一切間引かない。全便そのまま出力する
    - 乗換便は、直通の隙間を埋める便だけを残したうえで(GAP_FILL_WINDOW_MIN)、
      出発時刻帯が近い(TRANSFER_GROUP_WINDOW_MIN以内)ものは「最も早く着く1本」
      だけを代表として残す(=RAPTOR本来のパレート最適のみ残すのと同義)。
      乗換地点・系統が違っても到着がほぼ同じなら利用者には同じ選択肢なので、
      代表1本+alt_routes(他に選べた便数)で十分という考え方
  件数上限による均等間引きは採用しない: 時計モード(次のバスまであと○分)が
  「実際は10分おきに来るのに次は45分後」のように誤った案内をするリスクがあるため
  (間引き後も各行は実在の便の集合を代表しており、この問題は生じない)。

日付→ダイヤ種別(date_table)の判定方針:
  9フィードのうち、祝日・お盆等の例外を calendar_dates.txt に実際にコード化して
  いるのは山交バス(山形交通)だけだった(他8フィードは祝日でも平常運行するか、
  逆にお盆は全休運になるなどバラバラ)。山交バスを基準フィードとして「その日に
  走るservice_idの組」を3つの代表日(平日/土曜/日曜祝日)と比較し、一致した方の
  ダイヤ種別を採用する。これにより計画書の検証値(2026-07-20・海の日と
  2026-08-13〜15・お盆が sunday_holiday になること)を正確に再現できる
  (2026-07-06 に開発者と合意した方針)。山交バスの例外にも該当しない日付は、
  曜日+jpholiday判定へフォールバックする(念のための保険で、実際には
  発生しない見込み)。

実行方法: プロジェクトのルートで `python3 gap_map/export_web_data.py`
  (所要時間の目安: ネットワーク構築3回+全地区×全行き先の探索で数分程度)

出力:
  webapp/data/meta.json               … 有効期間・ダイヤ種別・デマンド交通の電話番号
  webapp/data/timetables/{地区ID}.json … 地区ごとの全行き先への時刻表(平日/土曜/日祝)

検算(計画書§6・F3完成条件):
  1. 「山形駅前を含む地区 → 県立中央病院」の平日欄が、第1部PDF
     (timetable_山形交通_山形駅前_県立中央病院.pdf)の便と一致すること
  2. date_table で 2026-07-20・2026-08-13〜15 が sunday_holiday になっていること
  3. 出力サイズが合計10MB・1地区250KB以内であること
  4. 再実行しても同じ出力になること(冪等)

F4-1での追加(docs/plan_f4_ui.md §2。かんたんモードの「翻訳」用の材料):
  - 各便に headsign(バス前面の行き先表示。trip_headsign由来、空なら終点名+方面)
  - 乗換に headsign2(のりかえ後の便の行き先表示)
  - route / route2 はNFKCで半角に正規化して出力(「Ｄ５６・Ｃ４」→「D56・C4」)
"""

import json
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

import jpholiday
import numpy as np
import pandas as pd

import config
import transit_core
from build_network import build_network, haversine_m, service_ids_running_on
from compute_access import nearby_stops as _nearby_stops_impl
from compute_access import walk_minutes

PROJECT_ROOT = Path(__file__).parent.parent
WEBAPP_DATA_DIR = PROJECT_ROOT / "webapp" / "data"
TIMETABLES_DIR = WEBAPP_DATA_DIR / "timetables"
META_JSON = WEBAPP_DATA_DIR / "meta.json"
DISTRICTS_JSON = WEBAPP_DATA_DIR / "districts.json"
DESTINATIONS_JSON = WEBAPP_DATA_DIR / "destinations.json"

# 3つの代表日(この日のネットワークを、同じダイヤ種別の全日付の代表として使う)
REFERENCE_DATES = {
    "weekday": "20260610",         # 分析確定版と同じ基準日(水・祝日の影響なし)
    "saturday": "20260613",        # 直近の土曜(例外の影響なし)
    "sunday_holiday": "20260614",  # 直近の日曜(例外の影響なし)
}
DAY_TYPE_LABELS = {"weekday": "平日", "saturday": "土曜", "sunday_holiday": "日曜・祝日"}

# date_tableの収録範囲。山交バス・上山市営バスのcalendar.txt end_dateが
# 2026-09-30でもっとも早く切れるため、これが9フィード全体の実質的な有効期限になる
DATE_TABLE_START = "20260701"
VALID_UNTIL = "20260930"

# 基準フィード(山交バス)。calendar_dates.txtに祝日・お盆の例外を実際にコード化している
# 唯一のフィードなので、date_typeの判定はこのフィードの運行パターンを基準にする
REFERENCE_FEED_DIR = config.GTFS_FEED_DIRS[0]
assert REFERENCE_FEED_DIR.name == "gtfs_山形交通", "基準フィードが山交バスである前提が崩れています"

# Webは乗換1回までを扱う(計画書§5「乗換1回経路の紙面化は展望送り。WebのJSONと画面では
# 乗換1回を扱う」)。M4の指標①・②で使うconfig.MAX_TRANSFERS(=2)とは別の、
# F3固有の設計値なのでここで独立に定める
MAX_TRANSFERS_WEB = 1

# デマンド交通・相談窓口(docs/demand_transport_memo.md より転記。F3/F7で使う)
# 上山市営デマンド交通の対象地区(西郷・本庄・東・宮生・中川(一部)・中山・山元)は
# 昔ながらの行政区の単位で、本システムの地区(小学校区の近似)とは1対1に対応しない。
# 名前が直接一致する西郷第一地区・中川地区(中川は「一部」該当)のみ対象に含め、
# 残りの3地区(上山・南・宮川)は市の窓口にフォールバックする。
# ※ 公開前に市の担当課へ地区対応の最終確認が必要(demand_transport_memo.mdの警告どおり)
DEMAND_PHONE = [
    {"name": "スマイルグリーン号(大郷明治デマンド型乗合タクシー・予約: 山交ハイヤー)",
     "tel": "023-681-3809", "districts": ["大郷地区", "明治地区"]},
    {"name": "上山市営予約制乗合タクシー", "tel": "023-632-2850",
     "districts": ["西郷第一地区", "中川地区"]},
    {"name": "山形市 公共交通課(バスの相談窓口)", "tel": "023-641-1212",
     "districts": "山形市のその他全地区"},
    {"name": "上山市 市政戦略課(バスの相談窓口)", "tel": "023-672-1111",
     "districts": "上山市のその他全地区"},
]


# ===============================================================
# 日付→ダイヤ種別(date_table)
# ===============================================================
def build_date_table() -> dict:
    calendar = pd.read_csv(REFERENCE_FEED_DIR / "calendar.txt", dtype=str)
    cal_dates_path = REFERENCE_FEED_DIR / "calendar_dates.txt"
    cal_dates = pd.read_csv(cal_dates_path, dtype=str) if cal_dates_path.exists() else None

    ref_signatures = {
        day_type: frozenset(service_ids_running_on(calendar, cal_dates, d))
        for day_type, d in REFERENCE_DATES.items()
    }

    table = {}
    d = datetime.strptime(DATE_TABLE_START, "%Y%m%d").date()
    end = datetime.strptime(VALID_UNTIL, "%Y%m%d").date()
    n_fallback = 0
    while d <= end:
        date_str = d.strftime("%Y%m%d")
        sig = frozenset(service_ids_running_on(calendar, cal_dates, date_str))
        day_type = next((k for k, v in ref_signatures.items() if v == sig), None)
        if day_type is None:
            n_fallback += 1
            if d.weekday() == 6 or jpholiday.is_holiday(d):
                day_type = "sunday_holiday"
            elif d.weekday() == 5:
                day_type = "saturday"
            else:
                day_type = "weekday"
        table[d.isoformat()] = day_type
        d += timedelta(days=1)

    if n_fallback:
        print(f"※ 山交バスの例外パターンに一致しない日付が{n_fallback}件あり、"
              f"曜日+祝日判定にフォールバックしました")
    return table


# ===============================================================
# 便の列挙・経路の組み立て
# ===============================================================
def board_events(network: transit_core.Network, stop_id: str) -> list:
    """stop_idから乗れる便を (出発時刻(分), pattern_idx, 乗車位置, Trip) のタプルで、
    出発時刻の昇順で返す(乗換なしでどこまで届くかを調べるため、tripそのものを持たせる)"""
    events = []
    for pattern_idx, pos in network.stop_routes.get(stop_id, []):
        pattern = network.patterns[pattern_idx]
        if pos == len(pattern.stop_ids) - 1:
            continue  # 終点では乗れない
        for trip in pattern.trips:
            events.append((trip.departures[pos], pattern_idx, pos, trip))
    events.sort(key=lambda e: e[0])
    return events


def fmt_hm(total_min: float) -> str:
    """0時からの分をHH:MM形式にする(25:10のような深夜表記もそのまま許す)"""
    h, m = divmod(int(round(total_min)), 60)
    return f"{h:02d}:{m:02d}"


def hm_to_min(hm: str) -> int:
    """HH:MM形式を0時からの分に戻す(fmt_hmの逆)"""
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


# ===============================================================
# 行き先表示(headsign)と系統名の正規化(F4-1。docs/plan_f4_ui.md §1 R1・R2、§2)
# ===============================================================
def normalize_text(name):
    """全角英数字を半角に統一する(NFKC正規化。「Ｄ５６・Ｃ４」→「D56・C4」)。
    日本語の文字はそのまま。系統名とheadsignの両方に使う(翻訳ルールR2)"""
    if not isinstance(name, str):
        return name
    return unicodedata.normalize("NFKC", name).strip()


_RAW_HEADSIGNS = None   # trips.txtのtrip_headsign(接頭辞付きtrip_id→行き先)。初回だけ読む


def load_raw_headsigns() -> dict:
    """9フィードのtrips.txtから trip_id(接頭辞付き)→trip_headsign の対応表を作る。
    接頭辞の規則は build_network._feed_prefix と同じ(「フィード名:」)。
    ※凍結資産に headsign を持たせるのではなく、このスクリプトが自分で
      trips.txt を読み直す(凍結資産のdiffを0行に保つための設計)"""
    global _RAW_HEADSIGNS
    if _RAW_HEADSIGNS is None:
        _RAW_HEADSIGNS = {}
        for feed_dir in config.GTFS_FEED_DIRS:
            trips = pd.read_csv(feed_dir / "trips.txt", dtype=str)
            if "trip_headsign" not in trips.columns:
                continue
            prefix = feed_dir.name.removeprefix("gtfs_") + ":"
            for trip_id, headsign in zip(trips["trip_id"], trips["trip_headsign"]):
                if isinstance(headsign, str) and headsign.strip():
                    _RAW_HEADSIGNS[prefix + trip_id] = normalize_text(headsign)
    return _RAW_HEADSIGNS


def build_headsign_map(network: transit_core.Network) -> dict:
    """networkに載っている全便の trip_id→行き先表示(headsign) を確定させる。
    trip_headsignが空の便は「終点停留所名+方面」で代替する(翻訳ルールR1の保険。
    2026-07-06時点では9フィードとも記入率100%なので、代替は将来のフィード更新で
    記入率が下がったときにだけ発動する。発動したら件数をログに出して検知する)"""
    raw = load_raw_headsigns()
    headsigns = {}
    n_fallback = 0
    for pattern in network.patterns:
        terminal_name = network.stops[pattern.stop_ids[-1]]["name"]
        for trip in pattern.trips:
            hs = raw.get(trip.trip_id)
            if not hs:
                hs = terminal_name + "方面"
                n_fallback += 1
            headsigns[trip.trip_id] = hs
    if n_fallback:
        print(f"  ※ trip_headsignが空で終点名から代替した便: {n_fallback}本")
    return headsigns


def make_itinerary(path: list, final_arrival: int, network: transit_core.Network,
                    board_name: str, alight_name: str, headsigns: dict) -> dict:
    """reconstruct_pathで得たLegのリストから、画面表示用の1便ぶんの辞書を作る。
    headsign(バス前面の行き先表示)が主役、系統名(route)は半角に正規化した
    確認情報という位置づけ(翻訳ルール。docs/plan_f4_ui.md §1・§2)"""
    ride_legs = [leg for leg in path if leg.kind == "ride"]
    first = ride_legs[0]
    dep = first.depart

    transfer = None
    if len(ride_legs) >= 2:
        second = ride_legs[1]
        transfer_stop_id = second.from_stop
        walk_between = next(
            (leg for leg in path if leg.kind == "walk" and leg.to_stop == transfer_stop_id), None)
        arrive_at_transfer = walk_between.arrive if walk_between else first.arrive
        transfer = {
            "at": network.stops[transfer_stop_id]["name"],
            "wait_min": round(second.depart - arrive_at_transfer),
            "headsign2": headsigns[second.trip_id],   # のりかえ後の便の行き先表示(R4)
            "route2": normalize_text(second.route_name),
        }

    total_min = final_arrival - dep
    ride_min = round(total_min - (transfer["wait_min"] if transfer else 0))

    board_stop_info = network.stops.get(first.from_stop, {})
    platform = board_stop_info.get("platform_code")
    if not isinstance(platform, str):
        # stops.txtにplatform_code列が無い/空欄の行はpandasがfloat NaNを返す
        # (json.dumpsするとNaNという不正なJSONトークンになるため、必ずNoneに変換する)
        platform = None
    return {
        "dep": fmt_hm(dep),
        "arr": fmt_hm(final_arrival),
        "board": board_name,
        "platform": platform,
        "alight": alight_name,
        "headsign": headsigns[first.trip_id],   # バス前面の行き先表示(R1の主役)
        "route": normalize_text(first.route_name),
        "ride_min": ride_min,
        "transfer": transfer,
    }


# 乗換1回の便を「補完」として載せるかどうかの判定に使う時間幅(計画書§6
# 「直通が存在する時間帯は直通を優先。乗換1回は直通が無い時間帯の補完として載せる」)。
# この時間内に直通があれば、その乗換ルートは載せない(基幹停留所では乗換の
# 組み合わせが膨大になり、全部載せると1地区250KBの上限を大きく超えるため)
GAP_FILL_WINDOW_MIN = 30


def scan_from_origin(network: transit_core.Network, origin_stops: list,
                      targets: dict, headsigns: dict,
                      board_name_override: str = None) -> dict:
    """origin_stops(徒歩圏内の乗り場すべて)のどれかから出発できる全便を1本ずつ試し、
    各行き先(targets)への時刻表を返す(直通優先・乗換1回は直通が無い時間帯の補完)。

    山形駅前のように、同じ場所でも「のりば」(=stop_id)が複数に分かれている
    拠点があり、路線によって発着するのりばが違う。最寄りの1つのりばだけを見ると
    別のりばから出る便を取りこぼすため、徒歩圏内の全のりばをそれぞれ出発点として試す。

    origin_stops: [(stop_id, 徒歩分, 表示名), ...]
    targets     : {行き先id: [(alight_stop_id, 徒歩分, 表示名), ...]}
    board_name_override: "board"欄に使う固定の表示名(行き先発=inboundのときは
      施設名を使いたいのでこちらを指定する。Noneなら実際に乗った停留所名を使う)
    戻り値 : {行き先id: [itinerary辞書, ...](出発時刻の昇順)}
    """
    # 逆引き: stop_id -> [(行き先id, 徒歩分, 表示名), ...](乗換なし到達の判定に使う)
    stop_to_targets = {}
    for tid, stops_with_walk in targets.items():
        for stop_id, walk_min, name in stops_with_walk:
            stop_to_targets.setdefault(stop_id, []).append((tid, walk_min, name))

    events = []   # (depart_min, pattern_idx, pos, trip, origin_stop_id)
    for stop_id, _walk_min, _name in origin_stops:
        events.extend((depart_min, pattern_idx, pos, trip, stop_id)
                      for depart_min, pattern_idx, pos, trip in board_events(network, stop_id))
    events.sort(key=lambda e: e[0])

    direct_rows = {tid: [] for tid in targets}     # tid -> [(出発分, itinerary), ...]
    direct_seen = {tid: set() for tid in targets}  # tid -> {trip_id, ...}(直通の重複防止)
    transfer_rows = {tid: [] for tid in targets}
    transfer_seen = {tid: set() for tid in targets}  # tid -> {(第1便, 第2便), ...}

    for depart_min, pattern_idx, pos, trip, origin_stop in events:
        pattern = network.patterns[pattern_idx]

        # (a) 乗換なし: この便に乗ったまま届く行き先を拾う。徒歩圏内に複数の候補停留所が
        # あるとき、最初に通る停留所が必ずしも合計時間最短とは限らない(バス到着は
        # 停車順に増えていくが、後の停留所の方が施設まで近く徒歩時間が短いことがある)。
        # そのため「バス到着+徒歩」の合計が一番小さい停留所を選ぶ
        direct_hit = {}   # 行き先id -> (到着時刻, 降車停留所)
        for later_pos in range(pos + 1, len(pattern.stop_ids)):
            sid = pattern.stop_ids[later_pos]
            for tid, walk_min, _name in stop_to_targets.get(sid, []):
                total = trip.arrivals[later_pos] + round(walk_min)
                if tid not in direct_hit or total < direct_hit[tid][0]:
                    direct_hit[tid] = (total, sid)

        for tid, (arrival, alight_stop) in direct_hit.items():
            if trip.trip_id in direct_seen[tid]:
                continue
            direct_seen[tid].add(trip.trip_id)
            leg = transit_core.Leg(
                kind="ride", from_stop=origin_stop, to_stop=alight_stop,
                depart=depart_min, arrive=trip.arrivals[pattern.stop_ids.index(alight_stop, pos + 1)],
                trip_id=trip.trip_id, route_name=trip.route_name, prev=None)
            stops_with_walk = targets[tid]
            alight_name = next(name for sid, _, name in stops_with_walk if sid == alight_stop)
            board_name = board_name_override or network.stops[origin_stop]["name"]
            direct_rows[tid].append(
                (depart_min, make_itinerary([leg], arrival, network, board_name, alight_name,
                                            headsigns)))

        # (b) 乗換あり(最大1回): この便で直通しない行き先だけRAPTORで調べる。
        #     直通がすでにある行き先まで毎回調べると計算・出力とも無駄が大きい
        need_transfer = [tid for tid in targets if tid not in direct_hit]
        if not need_transfer:
            continue
        result = transit_core.raptor_search(
            network, {origin_stop: depart_min}, MAX_TRANSFERS_WEB, config.MIN_TRANSFER_MIN)

        for tid in need_transfer:
            stops_with_walk = targets[tid]
            best = None
            for stop_id, walk_min, _ in stops_with_walk:
                if stop_id in result:
                    arrival = result[stop_id]["arrival"] + round(walk_min)
                    if best is None or arrival < best[0]:
                        best = (arrival, stop_id)
            if best is None:
                continue
            arrival, alight_stop = best
            path = transit_core.reconstruct_path(result, alight_stop)
            ride_legs = [leg for leg in path if leg.kind == "ride"]
            if len(ride_legs) < 2:
                continue  # 乗換なしならこの便自体が(a)で拾えているはず

            key = (ride_legs[0].trip_id, ride_legs[1].trip_id)
            if key in transfer_seen[tid]:
                continue
            transfer_seen[tid].add(key)

            alight_name = next(name for sid, _, name in stops_with_walk if sid == alight_stop)
            board_name = board_name_override or network.stops[path[0].from_stop]["name"]
            transfer_rows[tid].append(
                (depart_min, make_itinerary(path, arrival, network, board_name, alight_name,
                                            headsigns)))

    result_by_target = {}
    for tid in targets:
        d_rows = sorted(direct_rows[tid], key=lambda x: x[0])
        d_times = [dm for dm, _ in d_rows]
        # 乗換ルートは、近くに直通が無い(=直通の隙間を埋める)ものだけを残す
        kept_transfer = [
            row for dm, row in sorted(transfer_rows[tid], key=lambda x: x[0])
            if not any(abs(dm - dt) <= GAP_FILL_WINDOW_MIN for dt in d_times)
        ]
        # 直通便(紙の時刻表の本体)は間引かず全便残す。乗換便だけ、利用者から見て
        # 「同じ選択肢」に見えるもの(出発時刻帯が近い)を代表1本にまとめる
        merged = [row for _, row in d_rows] + collapse_transfer_alternatives(kept_transfer)
        merged.sort(key=lambda row: row["dep"])
        result_by_target[tid] = merged
    return result_by_target


# 乗換ルートの集約: この時間内に出発するものは「利用者には同じ選択肢」とみなし、
# 最も早く着く1本だけを代表として残す(＝RAPTOR本来のパレート最適のみ残すのと同義)
TRANSFER_GROUP_WINDOW_MIN = 20


def collapse_transfer_alternatives(rows: list) -> list:
    """乗換ルートの一覧(出発時刻の昇順)を、出発時刻帯が近いものごとにグループ化し、
    各グループの中で最も早く到着する1本だけを残す(乗換地点・系統が違っても、
    利用者から見れば到着がほぼ同じなら同じ選択肢のため)。他にも選べる便があった
    場合は alt_routes に件数を残す"""
    if not rows:
        return rows
    groups = [[rows[0]]]
    for row in rows[1:]:
        if hm_to_min(row["dep"]) - hm_to_min(groups[-1][-1]["dep"]) <= TRANSFER_GROUP_WINDOW_MIN:
            groups[-1].append(row)
        else:
            groups.append([row])

    out = []
    for group in groups:
        best = min(group, key=lambda r: hm_to_min(r["arr"]))
        if len(group) > 1:
            best = dict(best)
            best["alt_routes"] = len(group) - 1
        out.append(best)
    return out


# ===============================================================
# 地区・行き先の下ごしらえ
# ===============================================================
class StopIndex:
    """network.stopsをnumpy配列化して、compute_access.nearby_stops をそのまま使い回す
    ための薄いラッパー(凍結済みのM4ロジックをimportして使うだけ、が方針)"""

    def __init__(self, network: transit_core.Network):
        self.stop_ids = list(network.stops.keys())
        self.stop_lats = np.array([network.stops[s]["lat"] for s in self.stop_ids])
        self.stop_lons = np.array([network.stops[s]["lon"] for s in self.stop_ids])

    def nearby(self, lat: float, lon: float, max_dist_m: float) -> list:
        return _nearby_stops_impl(lat, lon, self.stop_ids, self.stop_lats, self.stop_lons, max_dist_m)


def build_targets(stop_index: StopIndex, points: list) -> dict:
    """points: [(id, lat, lon, 表示名), ...] から、各地点の徒歩圏内バス停一覧を作る。
    降車側(alight)で使う。降りた後どのバス停からでも歩いて行ければよいので、
    徒歩圏内(config.MAX_WALK_TO_STOP_M)のバス停は全部候補にする。
    戻り値: {id: [(stop_id, 徒歩分, 表示名), ...]}(徒歩圏内バス停が無ければ空リスト)"""
    targets = {}
    for pid, lat, lon, name in points:
        hits = stop_index.nearby(lat, lon, config.MAX_WALK_TO_STOP_M)
        targets[pid] = [(stop_id, walk_min, name) for stop_id, walk_min in hits]
    return targets


# 出発点(乗る側)で「同じ場所の別のりば」とみなす距離。山形駅前のように、
# 同名の停留所でものりばがstop_id単位で分かれていることがあり、最寄りの1つだけを
# 見ると別のりばから出る便を取りこぼす。ただしこれをMAX_WALK_TO_STOP_M(800m)全体に
# 広げると、都心部では山交ビル・錦町・山形駅西口のような別々のバス停群まで
# 出発候補に巻き込んでしまい、1日の候補本数が現実離れして膨れ上がる
# (実測: ある地区で19停留所・1日3567便を出発候補にしてしまっていた)。
# そこで「最寄り停留所からこの距離以内」という、のりば違い専用のごく短い距離を別に設ける
SAME_PLACE_M = 150


def build_origins(network: transit_core.Network, stop_index: StopIndex, points: list) -> dict:
    """points: [(id, lat, lon, 表示名), ...] から、各地点の出発点(乗る側)バス停一覧を作る。
    最寄りの1停留所 + そのすぐそば(SAME_PLACE_M以内=同じ場所の別のりば)の停留所のみ。
    戻り値: {id: [(stop_id, 徒歩分, 表示名), ...]}(1件目が必ず最寄り)"""
    origins = {}
    for pid, lat, lon, name in points:
        hits = stop_index.nearby(lat, lon, config.MAX_WALK_TO_STOP_M)
        if not hits:
            origins[pid] = []
            continue
        nearest_id, nearest_walk = hits[0]
        n_lat, n_lon = network.stops[nearest_id]["lat"], network.stops[nearest_id]["lon"]
        same_place = [(nearest_id, nearest_walk, name)]
        for stop_id, walk_min in hits[1:]:
            d = haversine_m(n_lat, n_lon, network.stops[stop_id]["lat"], network.stops[stop_id]["lon"])
            if d <= SAME_PLACE_M:
                same_place.append((stop_id, walk_min, name))
        origins[pid] = same_place
    return origins


# ===============================================================
# 1つの曜日タイプ(=1つのネットワーク)について、全地区×全行き先を計算する
# ===============================================================
def compute_day_type_schedules(network: transit_core.Network, districts: list,
                                destinations: list, stop_index: StopIndex,
                                headsigns: dict) -> dict:
    """戻り値:
      {"outbound": {地区id: {行き先id: [itinerary,...]}},
       "inbound":  {行き先id: {地区id: [itinerary,...]}},
       "district_board": {地区id: (stop_id, 徒歩分) or None}}
    """
    facility_points = [(f["id"], f["lat"], f["lon"], f["name"]) for f in destinations]
    facility_targets = build_targets(stop_index, facility_points)      # 降車側(全徒歩圏内)
    facility_origins = build_origins(network, stop_index, facility_points)  # 乗車側(最寄り+同じ場所)

    district_points = [(d["id"], d["lat"], d["lon"], d["name"]) for d in districts]
    district_targets = build_targets(stop_index, district_points)     # 降車側(全徒歩圏内)
    district_origins = build_origins(network, stop_index, district_points)  # 乗車側(最寄り+同じ場所)

    district_board = {}
    outbound = {}
    for d in districts:
        stops = district_origins[d["id"]]
        if not stops:
            district_board[d["id"]] = None
            outbound[d["id"]] = {f["id"]: [] for f in destinations}
            continue
        district_board[d["id"]] = (stops[0][0], stops[0][1])   # 1件目が必ず最寄り
        outbound[d["id"]] = scan_from_origin(network, stops, facility_targets, headsigns)

    inbound = {}
    for f in destinations:
        stops = facility_origins[f["id"]]
        if not stops:
            inbound[f["id"]] = {d["id"]: [] for d in districts}
            continue
        # 行き先発(inbound)は「乗り場」欄に施設名を出す(利用者は施設の建物から
        # 歩くのであって、特定のりば名を意識しないため)
        inbound[f["id"]] = scan_from_origin(network, stops, district_targets, headsigns,
                                             board_name_override=f["name"])

    return {"outbound": outbound, "inbound": inbound, "district_board": district_board}


def build_entry(district: dict, facility: dict, per_daytype: dict) -> dict:
    """1つの(地区, 行き先)ペアぶんのJSON片を組み立てる"""
    did, fid = district["id"], facility["id"]
    board_walk_min = None
    outbound = {}
    inbound = {}
    any_reachable = False
    for day_type in REFERENCE_DATES:
        dt = per_daytype[day_type]
        db = dt["district_board"].get(did)
        if board_walk_min is None and db is not None:
            board_walk_min = round(db[1])
        # 直通・乗換の間引きはscan_from_origin側で完結済み(直通便は間引かない)
        rows_out = dt["outbound"].get(did, {}).get(fid, [])
        rows_in = dt["inbound"].get(fid, {}).get(did, [])
        outbound[day_type] = rows_out
        inbound[day_type] = rows_in
        if rows_out or rows_in:
            any_reachable = True

    if not any_reachable:
        return {"unreachable": True}

    entry = {"board_walk_min": board_walk_min, "outbound": outbound, "inbound": inbound}
    direct_dist_m = haversine_m(district["lat"], district["lon"], facility["lat"], facility["lon"])
    if direct_dist_m <= config.MAX_WALK_TO_STOP_M:
        entry["direct_walk_min"] = round(walk_minutes(direct_dist_m))
    return entry


# ===============================================================
# メイン
# ===============================================================
def main():
    print("地区・行き先マスタを読み込み中...")
    districts = json.loads(DISTRICTS_JSON.read_text(encoding="utf-8"))
    destinations = json.loads(DESTINATIONS_JSON.read_text(encoding="utf-8"))
    print(f"  地区: {len(districts)}件 / 行き先: {len(destinations)}件(採否=採用の分のみ)")

    print("\nmeta.jsonを作成中(date_table)...")
    date_table = build_date_table()
    meta = {
        "generated": date.today().isoformat(),
        "valid_until": f"{VALID_UNTIL[:4]}-{VALID_UNTIL[4:6]}-{VALID_UNTIL[6:]}",
        "day_types": DAY_TYPE_LABELS,
        "date_table": date_table,
        "demand_phone": DEMAND_PHONE,
    }
    WEBAPP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    META_JSON.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {META_JSON} ({len(date_table)}日分)")
    print(f"  検算1: 2026-07-20 = {date_table.get('2026-07-20')}(期待値 sunday_holiday)")
    obon = [date_table.get(f"2026-08-{d}") for d in (13, 14, 15)]
    print(f"  検算1: 2026-08-13〜15 = {obon}(期待値 全てsunday_holiday)")

    per_daytype = {}
    for day_type, ref_date in REFERENCE_DATES.items():
        print(f"\n[{day_type}] {ref_date}のネットワークを構築中...")
        network = build_network(config.GTFS_FEED_DIRS, ref_date)
        stop_index = StopIndex(network)
        headsigns = build_headsign_map(network)   # F4-1: 行き先表示(バス前面)の対応表
        print(f"  地区{len(districts)}件×行き先{len(destinations)}件を計算中...")
        per_daytype[day_type] = compute_day_type_schedules(network, districts, destinations,
                                                            stop_index, headsigns)
        n_out = sum(len(v) for d in per_daytype[day_type]["outbound"].values() for v in d.values())
        n_in = sum(len(v) for d in per_daytype[day_type]["inbound"].values() for v in d.values())
        print(f"  → outbound便数合計: {n_out} / inbound便数合計: {n_in}")

    print("\n地区ごとのJSONを組み立て中...")
    TIMETABLES_DIR.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    max_bytes = 0
    max_district = None
    n_unreachable = 0
    for d in districts:
        to = {}
        for f in destinations:
            entry = build_entry(d, f, per_daytype)
            to[f["id"]] = entry
            if entry.get("unreachable"):
                n_unreachable += 1
        text = json.dumps({"district": d["id"], "to": to}, ensure_ascii=False, indent=1)
        (TIMETABLES_DIR / f"{d['id']}.json").write_text(text, encoding="utf-8")
        size = len(text.encode("utf-8"))
        total_bytes += size
        if size > max_bytes:
            max_bytes, max_district = size, d["id"]

    print(f"\n=== 検算(F3完成条件) ===")
    print(f"地区×行き先の組み合わせ: {len(districts) * len(destinations)}件"
          f"(うちunreachable: {n_unreachable}件)")
    ok_total = total_bytes <= 10 * 1024 * 1024
    ok_max = max_bytes <= 250 * 1024
    print(f"検算3-a 合計サイズ: {total_bytes/1024/1024:.2f}MB(条件10MB以内) → {'OK' if ok_total else 'NG'}")
    print(f"検算3-b 最大1地区サイズ: {max_bytes/1024:.1f}KB({max_district}。条件250KB以内) → "
          f"{'OK' if ok_max else 'NG'}")
    print("\n※ 検算2(山形駅前を含む地区→県立中央病院がPDF版と一致するか)は"
          "\n   webapp/data/timetables/ の該当ファイルを目視で確認してください。")


if __name__ == "__main__":
    main()
