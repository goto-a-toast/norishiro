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

2026-07-07追加(開発者指摘への対応。「乗るバス停は最寄りだが、目的地までの
最短ルートを持つとは限らない」。設計C = docs/handover.md §2):
  - 自宅(地区)側の候補停留所選び(build_origins, expand_by_route=True)を、距離だけ
    でなく「最寄り停には無い系統を持つか」でも候補に加えるよう改修(boardable_routes)。
    (施設側は当初「最寄り+同じ場所」のみだったが、2026-07-10に同じ方式へ変更。後述)
  - 各行き先ごとに、door-to-door(家を出る時刻=出発−徒歩分/到着時刻)のパレート最適で
    「一度も最善にならない乗り場」の便を落とす(keep_useful_boards)。これで
    「別系統だが実は役に立たない停」を増やさず、都心の候補爆発を抑える。
  - 行き先ごとに「一番いい乗り場」を1つだけ選ぶ(pick_kantan_board → entry.kantan_board)。
  - 便ごとに実際に使った乗車停留所までの徒歩分を itinerary の board_walk_min に持たせる
    (旧来の「地区に1つ」の集計値は entry.board_walk_min にも残す)。
  - 再生成後、合計サイズ・1地区最大サイズの検算(§検算3)をやり直すこと。

2026-07-08決定(slim方式。docs/handover.md §2。設計Cの実データが~50MBと不十分だった
ことへの結論):
  - 行き(outbound)は build_entry で kantan_board の1停の便だけに絞って保存する。
    かんたん・しっかり とも同じ1停の完全な時刻表を見せる(最寄り≠最短は解決。
    複数停からの選択は将来F10のオンデマンド機能へ)。これで都心の候補爆発が消え~20MB。
  - JSONは整形せずコンパクトに書き出す(separators=(",",":")。人が読まない生成物なので
    可読性より約2割の減量を優先)。
  - 帰り(inbound)の便は間引かない(帰りの時刻表を薄くしない)。
  - 降車バス停は施設名/地区名でなく実際のバス停名(alight)+目的地までの徒歩分
    (alight_walk_min)を出す。目的地の表示名は alight_place(make_itinerary参照)。

2026-07-10追加(開発者指摘への対応。「大郷地区⇔済生館の帰りが平日6:37の1本だけ」):
  - 施設側(帰りの出発)の候補停留所も expand_by_route=True で選ぶ。従来は
    「最寄り+150m以内ののりば」だけだったため、済生館(最寄り=七日町86m)では
    大郷方面の全便が出る本町(施設から149m・最寄り停から207m)が候補から漏れ、
    平日15本の直通が1本も出ていなかった。同じ構図は市街地の施設全般で起こる。
  - 候補追加の判定は系統名でなく「系統名+終点名」(boardable_directions)。系統名だけ
    では方向を区別できず、七日町に停まる同系統の市街地方面の便のせいで本町が
    「新系統なし」と誤判定されていた。
  - あわせて帰りの「乗り場」欄(board)を施設名の統一表示から実際のバス停名に変更
    (乗り場が複数になり、施設名では「どこから乗るのか」わからなくなるため)。
    施設からの徒歩分は board_walk_min にある(UI側は実停名+徒歩分で案内する)。
    board が実停名になったことで keep_useful_boards の間引きが帰りにも自然に効き、
    「一度も最善にならない乗り場」の便は増えない。
  - collapse_transfer_alternatives のグループ化を「直前の便から20分以内」→
    「グループ先頭から20分以内」に修正。前者は乗換候補が数分おきにある市街地で
    1日ぶんが1グループに数珠つなぎされ、帰りの選択肢が代表1本に潰れていた。
  - 帰り(inbound)は build_entry で行単位のパレートフロンティア(frontier_rows)により
    間引く。乗り場拡大で都心どうしのペアが数分おき600便超に爆発したため。
    「施設を出る時刻が同じか早いのに、家に着くのが同じか遅い」= 選ぶ理由のない便だけが
    落ちる。単一の乗り場の時刻表は単調なので1本も落ちない(=2026-07-08の
    「帰りの時刻表を薄くしない」方針と両立。薄くなるのは都心の冗長な並行便だけ)。
"""

import json
import statistics
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
STOPS_INDEX_JSON = WEBAPP_DATA_DIR / "stops_index.json"   # 対策1: 停留所名→座標の索引
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

# 運行主体の連絡先(2026-07-07 追加。かんたんモード/しっかりモードの
# 「バスの相談窓口」欄に、実際にその画面に出ているバスの運行主体を出すため)。
# キーはフィード名(= trip_id の「◯◯:」接頭辞。gtfs_◯◯ ディレクトリ名から)。
# ★tel は市・事業者の公式ページで人間が確認してから記入すること
# (None のままなら画面には運行主体の名前だけ出て、電話番号は出ない)。
# agency.txt の記載は生成時にログへ出すので、記入内容と突き合わせて確認する
OPERATOR_CONTACT = {
    # 2026-07-07 Web調査(検索結果スニペット経由。★印は公開前に公式ページを
    # 人の目で開いて最終確認すること — 特に山交・天童・南陽)
    # ★山交バス案内センター: 複数ソース一致だが公式ページの直接閲覧は未実施
    "山形交通": {"name": "山交バス", "desk": "案内センター", "tel": "023-632-7272"},
    # 上山市公式 soshiki/3/shieibasu20231002.html(市政戦略課が市営バス所管)
    "上山市":   {"name": "上山市営バス", "desk": "上山市 市政戦略課", "tel": "023-672-1111"},
    # 山形市公式 kurashi/kotsu/…/1002674.html(べにちゃんバス=公共交通課)
    "山形市":   {"name": "べにちゃんバス(山形市)", "desk": "山形市 公共交通課", "tel": "023-641-1212"},
    # ★天童市: 担当課の直通番号が未確認のため未記入(代表023-654-1111も未確認)。
    #   確認後に記入する
    "天童市":   {"name": "天童市営バス", "desk": "天童市役所", "tel": None},
    # 山辺町公式 soshiki/4/otoiawase2024.html(町民生活課 生活環境係)
    "山辺町":   {"name": "山辺町営バス", "desk": "山辺町 町民生活課", "tel": "023-667-1109"},
    # 中山町公式 soshiki/3/choebus2.html(総合政策課)
    "中山町":   {"name": "中山町営バス(中山ふれあい号)", "desk": "中山町 総合政策課", "tel": "023-662-4271"},
    # 東根市公式 section008/shiminbus/(生活環境課。代表番号+内線2171)
    "東根市":   {"name": "東根市営バス", "desk": "東根市 生活環境課", "tel": "0237-42-1111"},
    # ★南陽市: 0238-40-8992(社会教育課)と代表0238-40-3211の2情報が交錯。
    #   公式ページで確認できるまで未記入
    "南陽市":   {"name": "南陽市営バス", "desk": "南陽市役所", "tel": None},
    # 寒河江市公式 kurashi/koutsu/(企画戦略課)
    "寒河江市": {"name": "寒河江市営バス", "desk": "寒河江市 企画戦略課", "tel": "0237-85-1413"},
}

# フィード名 → operators配列の添字(便レコードの "op"/"op2" はこの添字で運行主体を指す。
# 文字列を毎行に持たせるよりJSONが小さく済む)。順序は config.GTFS_FEED_DIRS で固定
FEED_NAMES = [d.name.removeprefix("gtfs_") for d in config.GTFS_FEED_DIRS]
FEED_INDEX = {name: i for i, name in enumerate(FEED_NAMES)}


def operator_index(trip_id: str):
    """trip_id(「フィード名:元trip_id」形式)から運行主体の添字を返す。
    未知の接頭辞なら None(将来フィード構成が変わったときに黙って誤表示しないため)"""
    feed = trip_id.split(":", 1)[0]
    return FEED_INDEX.get(feed)


def build_operators() -> list:
    """meta.json の operators 配列を作る。表示名・窓口・電話は OPERATOR_CONTACT
    (人間が確認して記入)を正とし、agency.txt の正式名称・電話はログに出して
    突き合わせ確認の材料にする"""
    operators = []
    for feed_dir, feed in zip(config.GTFS_FEED_DIRS, FEED_NAMES):
        contact = OPERATOR_CONTACT.get(feed, {"name": feed, "desk": None, "tel": None})
        agency_path = feed_dir / "agency.txt"
        if agency_path.exists():
            ag = pd.read_csv(agency_path, dtype=str)
            names = "、".join(ag.get("agency_name", pd.Series(dtype=str)).dropna())
            phones = "、".join(ag.get("agency_phone", pd.Series(dtype=str)).dropna()) or "(記載なし)"
            print(f"  [agency.txt照合] {feed}: 正式名称={names} / 電話={phones}"
                  f" → 表示={contact['name']} / 記入電話={contact['tel'] or '未記入'}")
        operators.append({"feed": feed, "name": contact["name"],
                          "desk": contact["desk"], "tel": contact["tel"]})
    return operators


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


def boardable_directions(network: transit_core.Network, stop_id: str) -> set:
    """その停留所から乗れる「系統名+終点名」の集合。build_originsで
    「最寄り停には無い系統(方向)を持つ停」を見分けるのに使う(2026-07-07 開発者指摘への対応)。

    系統名だけで判定すると方向を区別できない(2026-07-10 開発者指摘で判明した実害:
    済生館の最寄り=七日町には系統Ｊ６０・Ｃ１の「市街地方面」の便しか停まらないのに、
    系統名が一致するため「石橋方面」の全便が出る本町が候補から漏れ、大郷地区への帰りが
    平日15本→1本になっていた)。終点名を組にすることで同じ系統でも方向(・行き先違い)を
    区別する。逆に、同方向ののりば違いや途中折返し便まで別扱いにはならない程度の粗さに保つ"""
    directions = set()
    for pattern_idx, pos in network.stop_routes.get(stop_id, []):
        pattern = network.patterns[pattern_idx]
        if pos == len(pattern.stop_ids) - 1:
            continue  # 終点では乗れない
        terminal = network.stops[pattern.stop_ids[-1]]["name"]
        directions.update((trip.route_name, terminal) for trip in pattern.trips)
    return directions


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
                    board_name: str, alight_place: str, headsigns: dict,
                    board_walk_min: float = None, alight_walk_min: float = None,
                    board_options: list = None, alight_options: list = None) -> dict:
    """reconstruct_pathで得たLegのリストから、画面表示用の1便ぶんの辞書を作る。
    headsign(バス前面の行き先表示)が主役、系統名(route)は半角に正規化した
    確認情報という位置づけ(翻訳ルール。docs/plan_f4_ui.md §1・§2)。
    board_walk_min: この便が実際に使う乗車停留所までの徒歩分(2026-07-07 追加。
    最寄り停以外から出る便もbuild_originsが候補にするようになったため、
    「地区に1つ」ではなく便ごとに正しい徒歩分を持たせる)。

    降車の表示について(2026-07-08 開発者指摘「どこで降りるか分からない時刻表は不安」):
      "alight" には行き先の表示名(施設名/地区名)ではなく、実際に降りる
      **バス停名**を出す。施設名/地区名は "alight_place" に別に持ち、
      降車停から目的地までの徒歩分は "alight_walk_min" に持つ。
      車内アナウンスや停留所標識と照合できる実停名を主役にし、
      「◯◯まで徒歩N分」で目的地との関係を補う(行きの board_walk_min と対称)。
    alight_place: 行き先の表示名(施設名/地区名)。
    alight_walk_min: 降車停から目的地(施設/地区の代表点)までの徒歩分。

    乗り場の候補について(2026-07-08 開発者指摘「地区内に停留所が多いとき、同じバスが
    近くの停にも停まるのに1停しか案内しないのは違和感」):
      board_options に「この同じバス(1本目の便)が通る、家の近くの停留所」を
      発車時刻つきで一覧で持つ([{stop, dep, walk_min}, ...]。徒歩が近い順)。
      board(主停)もこの一覧の1つ。かんたんモードは主停+残りを「同じバス」として併記し、
      しっかりモードはこの一覧から実在の停を選ばせる。1停しか無ければ board のみ。"""
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
            "op2": operator_index(second.trip_id),    # のりかえ後の便の運行主体(meta.operatorsの添字)
        }

    total_min = final_arrival - dep
    ride_min = round(total_min - (transfer["wait_min"] if transfer else 0))

    board_stop_info = network.stops.get(first.from_stop, {})
    platform = board_stop_info.get("platform_code")
    if not isinstance(platform, str):
        # stops.txtにplatform_code列が無い/空欄の行はpandasがfloat NaNを返す
        # (json.dumpsするとNaNという不正なJSONトークンになるため、必ずNoneに変換する)
        platform = None

    # 実際に降りるバス停名。最後に乗ったバスの終点(=最終rideのto_stop)で降りる。
    # network.stopsに無い場合だけ、保険として行き先の表示名(alight_place)を使う
    alight_stop_id = ride_legs[-1].to_stop
    alight_stop_name = network.stops.get(alight_stop_id, {}).get("name") or alight_place
    return {
        "dep": fmt_hm(dep),
        "arr": fmt_hm(final_arrival),
        "board": board_name,
        "board_walk_min": round(board_walk_min) if board_walk_min is not None else None,
        "platform": platform,
        "alight": alight_stop_name,             # 実際に降りるバス停名(R5。標識と照合できる名前)
        "alight_place": alight_place,           # 目的地(施設/地区)の表示名。徒歩案内の文脈に使う
        "alight_walk_min": round(alight_walk_min) if alight_walk_min is not None else None,
        "headsign": headsigns[first.trip_id],   # バス前面の行き先表示(R1の主役)
        "route": normalize_text(first.route_name),
        "op": operator_index(first.trip_id),    # 運行主体(meta.operatorsの添字)
        "ride_min": ride_min,
        "transfer": transfer,
        # 同じバスが通る家の近くの停一覧(徒歩が近い順)。board もこの中の1つ。
        # Noneは「候補計算をしていない」= 主停のみ(帰りinboundなど)
        "board_options": board_options,
        # 帰り(inbound)で、同じバスが通る家の近くで「降りられる」停一覧(到着時刻つき)。
        # alight もこの中の1つ。行きoutboundはNone(降車=施設で1か所のため)
        "alight_options": alight_options,
    }


# board_options に載せる乗車停の上限(家の近い順)。都心の地区は徒歩圏に停が非常に多く
# (15停以上)、全部載せるとデータが膨れる(実測: 上限なしで合計32MB・d19が3MB)。
# 利用者が「近い停で乗る」のに必要なのは手近な数件なのでこの数で足りる
MAX_BOARD_OPTIONS = 6


def board_options_for(network: transit_core.Network, pattern, trip,
                      up_to_pos: int, near_home: dict) -> list:
    """1本目の便(pattern/trip)が「乗車位置より前=近所」で通る停のうち、家の徒歩圏内
    (near_home)にある停を、発車時刻つきで返す(2026-07-08 開発者指摘への対応)。
    同じバスなのでどの停で乗ってもよい。徒歩が近い順に並べ、近い MAX_BOARD_OPTIONS 件に絞る。
    near_home: {stop_id: 徒歩分}(その地区の徒歩圏内の全停。build_originsの候補ではなく
      「全部」。同じ路線が続けて通る近隣停を漏らさず拾うため)。
    up_to_pos: 降車(または乗換)の位置。これより前の停だけが乗車候補になる"""
    opts = []
    seen = set()
    for p in range(up_to_pos):
        sid = pattern.stop_ids[p]
        if sid in near_home and sid not in seen:
            seen.add(sid)
            opts.append({
                "stop": network.stops[sid]["name"],
                "dep": fmt_hm(trip.departures[p]),
                "walk_min": round(near_home[sid]),
            })
    opts.sort(key=lambda o: o["walk_min"])
    return opts[:MAX_BOARD_OPTIONS]


def alight_options_for(network: transit_core.Network, pattern, trip,
                       from_pos: int, home_walks: dict) -> list:
    """帰りの便(pattern/trip)が「乗車位置より後=家に近づいてから」通る停のうち、
    家の徒歩圏内(home_walks)にある停を、到着時刻つきで返す(2026-07-08 開発者要望
    「帰りも近いバス停の表示・選択を」)。同じバスなのでどの停で降りてもよい。
    徒歩が近い順に並べ、近い MAX_BOARD_OPTIONS 件に絞る。
    home_walks: {stop_id: 徒歩分}(その地区の徒歩圏内の降車候補停。targets[tid]由来)。
    from_pos: 乗車位置。これより後の停だけが降車候補になる"""
    opts = []
    seen = set()
    for p in range(from_pos + 1, len(pattern.stop_ids)):
        sid = pattern.stop_ids[p]
        if sid in home_walks and sid not in seen:
            seen.add(sid)
            opts.append({
                "stop": network.stops[sid]["name"],
                "arr": fmt_hm(trip.arrivals[p]),
                "walk_min": round(home_walks[sid]),
            })
    opts.sort(key=lambda o: o["walk_min"])
    return opts[:MAX_BOARD_OPTIONS]


# 乗換1回の便を「補完」として載せるかどうかの判定に使う時間幅(計画書§6
# 「直通が存在する時間帯は直通を優先。乗換1回は直通が無い時間帯の補完として載せる」)。
# この時間内に直通があれば、その乗換ルートは載せない(基幹停留所では乗換の
# 組み合わせが膨大になり、全部載せると1地区250KBの上限を大きく超えるため)
GAP_FILL_WINDOW_MIN = 30


def scan_from_origin(network: transit_core.Network, origin_stops: list,
                      targets: dict, headsigns: dict,
                      board_name_override: str = None, near_home: dict = None,
                      alight_home: bool = False) -> dict:
    """origin_stops(徒歩圏内の乗り場すべて)のどれかから出発できる全便を1本ずつ試し、
    各行き先(targets)への時刻表を返す(直通優先・乗換1回は直通が無い時間帯の補完)。

    山形駅前のように、同じ場所でも「のりば」(=stop_id)が複数に分かれている
    拠点があり、路線によって発着するのりばが違う。最寄りの1つのりばだけを見ると
    別のりばから出る便を取りこぼすため、徒歩圏内の全のりばをそれぞれ出発点として試す。

    origin_stops: [(stop_id, 徒歩分, 表示名), ...]
    targets     : {行き先id: [(alight_stop_id, 徒歩分, 表示名), ...]}
    board_name_override: "board"欄に使う固定の表示名(Noneなら実際に乗った停留所名。
      かつてinboundで施設名の統一表示に使っていたが、2026-07-10に実停名へ変更し現在は未使用)
    near_home: {stop_id: 徒歩分}。指定すると各便に board_options(同じバスが通る家の
      近くの停一覧)を付ける。行き(outbound)でのみ渡す。帰り(inbound)はNone
    alight_home: Trueにすると各便に alight_options(同じバスが家の近くで降りられる停一覧)を
      付ける。帰り(inbound)= targetsが家の地区側のときにTrueにする
    戻り値 : {行き先id: [itinerary辞書, ...](出発時刻の昇順)}
    """
    # 逆引き: stop_id -> [(行き先id, 徒歩分, 表示名), ...](乗換なし到達の判定に使う)
    stop_to_targets = {}
    for tid, stops_with_walk in targets.items():
        for stop_id, walk_min, name in stops_with_walk:
            stop_to_targets.setdefault(stop_id, []).append((tid, walk_min, name))

    # trip_id -> (pattern, trip)。board_options を作るのに、便が通る停の並びが要る
    # (near_home指定時=行きのみ構築。乗換便の1本目のパターンを引くのにも使う)
    trip_lookup = {}
    if near_home is not None:
        for pat in network.patterns:
            for t in pat.trips:
                trip_lookup[t.trip_id] = (pat, t)

    # 停留所ごとの徒歩分(2026-07-07 追加。build_originsが最寄り以外の停も候補にする
    # ようになったため、便ごとに「実際に歩いた停」の徒歩分を引けるようにする)
    origin_walk_min = {stop_id: walk_min for stop_id, walk_min, _name in origin_stops}

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
        direct_hit = {}   # 行き先id -> (到着時刻, 降車停留所, 降車位置)
        for later_pos in range(pos + 1, len(pattern.stop_ids)):
            sid = pattern.stop_ids[later_pos]
            for tid, walk_min, _name in stop_to_targets.get(sid, []):
                total = trip.arrivals[later_pos] + round(walk_min)
                if tid not in direct_hit or total < direct_hit[tid][0]:
                    direct_hit[tid] = (total, sid, later_pos)

        for tid, (arrival, alight_stop, alight_pos) in direct_hit.items():
            if trip.trip_id in direct_seen[tid]:
                continue
            direct_seen[tid].add(trip.trip_id)
            leg = transit_core.Leg(
                kind="ride", from_stop=origin_stop, to_stop=alight_stop,
                depart=depart_min, arrive=trip.arrivals[alight_pos],
                trip_id=trip.trip_id, route_name=trip.route_name, prev=None)
            stops_with_walk = targets[tid]
            # 降車停の「実徒歩分」と「行き先の表示名」を取り出す(実停名はmake_itinerary側で引く)
            alight_walk, alight_place = next(
                (w, name) for sid, w, name in stops_with_walk if sid == alight_stop)
            board_name = board_name_override or network.stops[origin_stop]["name"]
            # 同じバスが降車位置より前に通る、家の近くの停一覧(near_home指定=行きのみ)
            b_opts = (board_options_for(network, pattern, trip, alight_pos, near_home)
                      if near_home is not None else None)
            # 同じバスが乗車位置より後に通る、家の近くで降りられる停一覧(alight_home=帰りのみ)
            a_opts = None
            if alight_home:
                home_walks = {sid: w for sid, w, _n in stops_with_walk}
                a_opts = alight_options_for(network, pattern, trip, pos, home_walks)
            direct_rows[tid].append(
                (depart_min, make_itinerary([leg], arrival, network, board_name, alight_place,
                                            headsigns, board_walk_min=origin_walk_min[origin_stop],
                                            alight_walk_min=alight_walk, board_options=b_opts,
                                            alight_options=a_opts)))

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

            alight_walk, alight_place = next(
                (w, name) for sid, w, name in stops_with_walk if sid == alight_stop)
            board_name = board_name_override or network.stops[path[0].from_stop]["name"]
            # 1本目の便が乗換地点より前に通る、家の近くの停一覧(near_home指定=行きのみ)
            b_opts = None
            if near_home is not None:
                leg1 = ride_legs[0]
                pat, t = trip_lookup.get(leg1.trip_id, (None, None))
                if pat is not None:
                    board_pos = pat.stop_ids.index(leg1.from_stop)
                    transfer_pos = pat.stop_ids.index(leg1.to_stop, board_pos + 1)
                    b_opts = board_options_for(network, pat, t, transfer_pos, near_home)
            transfer_rows[tid].append(
                (depart_min, make_itinerary(path, arrival, network, board_name, alight_place,
                                            headsigns, board_walk_min=origin_walk_min[origin_stop],
                                            alight_walk_min=alight_walk, board_options=b_opts)))

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
        # 「一度も最善にならない乗り場」の便をまるごと落とす(残す停の便は間引かない)。
        # これで都心の候補爆発(実測1地区4.9MB)を、有用な停だけに絞れる(設計C)
        merged = keep_useful_boards(merged)
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
    # グループの区切りは「グループ先頭からの経過」で判定する。直前の便からの間隔で
    # つなぐと、乗換候補が数分おきにある市街地では朝から晩まで1グループに数珠つなぎ
    # されてしまい、1日ぶんの帰り便が代表1本に潰れる(2026-07-10 大郷⇔済生館で実害)
    groups = [[rows[0]]]
    for row in rows[1:]:
        if hm_to_min(row["dep"]) - hm_to_min(groups[-1][0]["dep"]) <= TRANSFER_GROUP_WINDOW_MIN:
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
# 乗り場の絞り込み(2026-07-07 開発者指摘「最寄り≠最短」への対応。設計C)
# ===============================================================
def _leave_home_min(row: dict) -> int:
    """その便に乗るために「家(地区代表点)を出る時刻」(0時からの分)。
    出発時刻からバス停までの徒歩分を引く。徒歩分が無い便は0扱い"""
    return hm_to_min(row["dep"]) - (row.get("board_walk_min") or 0)


def frontier_rows(rows: list) -> list:
    """door-to-doorの観点でパレート最適(=他のどの便にも完全には負けていない)便だけを返す。
    比較軸は「家を出る時刻(=出発時刻−徒歩分。遅いほど良い)」と「到着時刻(早いほど良い)」。
    これにより『最寄りより少し遠いが速い停』の便は残り、どの停発であっても
    『家を出る時刻が同じか早く、到着が同じか遅い』だけの便(=乗る理由のない便)は落ちる。
    「一番いい乗り場」の判定(build_entry)と、無駄な停の切り捨て(keep_useful_boards)の
    両方の土台になる"""
    # 文字列→分の変換を先に1回だけ済ませる(内側の総当たり比較で毎回パースしないため)
    metrics = [(_leave_home_min(r), hm_to_min(r["arr"])) for r in rows]
    kept = []
    for i, (lr, ar) in enumerate(metrics):
        dominated = any(
            j != i and ls >= lr and as_ <= ar and (ls > lr or as_ < ar)
            for j, (ls, as_) in enumerate(metrics)
        )
        if not dominated:
            kept.append(rows[i])
    return kept


def keep_useful_boards(rows: list) -> list:
    """『どの時間帯でも他の停に負けていて、一度もパレート最適にならない乗り場』の便を
    まるごと落とす。残す停(=一度でも最善になる停)については、その停の時刻表を
    間引かずに全便残す(しっかりモードの「乗車バス停で絞り込み」が正しく全便を出せるように、
    かつ かんたんモードで選ばれた1停の時計モードが嘘をつかないように)。
    乗り場が1種類のときは自然に全便が残る(行き・帰りとも実停名なので、帰りにも効く)"""
    if len(rows) <= 1:
        return rows
    useful = {r["board"] for r in frontier_rows(rows)}
    return [r for r in rows if r["board"] in useful]


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


def build_origins(network: transit_core.Network, stop_index: StopIndex, points: list,
                   expand_by_route: bool = False) -> dict:
    """points: [(id, lat, lon, 表示名), ...] から、各地点の出発点(乗る側)バス停一覧を作る。

    候補は最大3種類:
      1. 最寄りの1停留所(必ず1件目)
      2. そのすぐそば(SAME_PLACE_M以内=同じ場所の別のりば)
      3. (expand_by_route=Trueのときだけ)「最寄り停には無い系統(route_name)を持つ」
         徒歩圏内の他の停留所。2026-07-07 開発者指摘「最寄りだからといって目的地まで
         最短経路を持つとは限らない」への対応。最寄り停の系統網羅性が低い場合に、
         別系統を持つ少し先の停を候補から漏らさないようにする。

    expand_by_true は「家(地区)側」でだけ使う。利用者が乗り場を意識するのは自宅側で
    あり、施設側(帰りの出発)は施設の最寄り停で十分なため、施設側は 1・2 のみに保つ
    (施設側まで広げると inbound のデータが不必要に膨れる)。

    3.は「距離」でなく「その停が持つ系統がすでに候補でカバー済みか」で足切りする
    (最寄り停と同じ系統が重複するだけの近隣停留所を増やさない)が、これだけでは
    都心拠点で候補が過剰になるため、実際に目的地への時刻表を作る段階(scan_from_origin
    → keep_useful_boards)で「どの時間帯でも他の停に負けている停」をさらに落とす。
    戻り値: {id: [(stop_id, 徒歩分, 表示名), ...]}(1件目が必ず最寄り)"""
    origins = {}
    for pid, lat, lon, name in points:
        hits = stop_index.nearby(lat, lon, config.MAX_WALK_TO_STOP_M)
        if not hits:
            origins[pid] = []
            continue
        nearest_id, nearest_walk = hits[0]
        n_lat, n_lon = network.stops[nearest_id]["lat"], network.stops[nearest_id]["lon"]

        chosen = [(nearest_id, nearest_walk, name)]
        covered = boardable_directions(network, nearest_id)
        same_place_ids = {nearest_id}

        # (2) 同じ場所(のりば違い)。系統が重複していても「同じ場所」なので無条件で追加
        for stop_id, walk_min in hits[1:]:
            d = haversine_m(n_lat, n_lon, network.stops[stop_id]["lat"], network.stops[stop_id]["lon"])
            if d <= SAME_PLACE_M:
                chosen.append((stop_id, walk_min, name))
                same_place_ids.add(stop_id)
                covered = covered | boardable_directions(network, stop_id)

        # (3) 別系統(方向)を持つ停だけを追加する(近いだけで系統も方向も同じ停は増やさない)
        if expand_by_route:
            for stop_id, walk_min in hits[1:]:
                if stop_id in same_place_ids:
                    continue
                directions = boardable_directions(network, stop_id)
                new_directions = directions - covered
                if new_directions:
                    chosen.append((stop_id, walk_min, name))
                    covered = covered | new_directions

        origins[pid] = chosen
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
    # 施設側(帰りの出発)も「最寄り停に無い系統を持つ停」を候補に含める(2026-07-10)。
    # 最寄り+150m以内だけだと、済生館(最寄り=七日町)のように帰りの便が別の通りの停
    # (本町)から出る施設で、その方面の全便を取りこぼす。過剰分は keep_useful_boards が落とす
    facility_origins = build_origins(network, stop_index, facility_points, expand_by_route=True)

    district_points = [(d["id"], d["lat"], d["lon"], d["name"]) for d in districts]
    district_targets = build_targets(stop_index, district_points)     # 降車側(全徒歩圏内)
    # 自宅(地区)側は「最寄り停に無い系統を持つ停」も候補に含める(最寄り≠最短への対応)。
    # 過剰分は scan_from_origin の keep_useful_boards が「一度も最善にならない停」を落とす
    district_origins = build_origins(network, stop_index, district_points, expand_by_route=True)
    # 各地区の徒歩圏内の「全停」(build_originsの候補ではなく全部)。同じバスが続けて通る
    # 近隣停を board_options で漏らさず拾うために使う(2026-07-08 開発者指摘)
    district_near = {
        d["id"]: {sid: walk for sid, walk in stop_index.nearby(
            d["lat"], d["lon"], config.MAX_WALK_TO_STOP_M)}
        for d in districts
    }

    district_board = {}
    outbound = {}
    for d in districts:
        stops = district_origins[d["id"]]
        if not stops:
            district_board[d["id"]] = None
            outbound[d["id"]] = {f["id"]: [] for f in destinations}
            continue
        district_board[d["id"]] = (stops[0][0], stops[0][1])   # 1件目が必ず最寄り
        outbound[d["id"]] = scan_from_origin(network, stops, facility_targets, headsigns,
                                             near_home=district_near[d["id"]])

    inbound = {}
    for f in destinations:
        stops = facility_origins[f["id"]]
        if not stops:
            inbound[f["id"]] = {d["id"]: [] for d in districts}
            continue
        # 行き先発(inbound)も「乗り場」欄は実際のバス停名を出す(2026-07-10。乗り場が
        # 複数になったため、施設名の統一表示では「どこから乗るのか」わからない。
        # 施設からの徒歩分は各便の board_walk_min)。alight_home=Trueで、家の近くで
        # 降りられる停一覧(alight_options)を各便に付ける(帰りの近隣停の表示・選択用)
        inbound[f["id"]] = scan_from_origin(network, stops, district_targets, headsigns,
                                             alight_home=True)

    return {"outbound": outbound, "inbound": inbound, "district_board": district_board}


def _slim_to_board(rows: list, kantan_board: str) -> list:
    """行きの便を「kantan_board(featured停)を通るバス」だけに絞り、その便の主停・発車時刻を
    kantan_board にそろえる(かんたんモードが featured停の時刻表として読めるように)。
    board_options は残す(かんたんは近隣停を併記、しっかりは実在停を選択するのに使う)。
    board_options が無い古い便は board 一致で従来どおり絞る(後方互換)"""
    kept = []
    for r in rows:
        opts = r.get("board_options")
        if opts is None:
            if r["board"] == kantan_board:
                kept.append(r)
            continue
        match = next((o for o in opts if o["stop"] == kantan_board), None)
        if match is None:
            continue   # この便は featured 停に停まらない(別路線)→ slimでは落とす
        r = dict(r)
        r["board"] = kantan_board
        r["dep"] = match["dep"]
        r["board_walk_min"] = match["walk_min"]
        # 主停を変えたので乗車時間も合わせ直す(到着−この停の発車−乗換待ち)
        wait = r["transfer"]["wait_min"] if r["transfer"] else 0
        r["ride_min"] = hm_to_min(r["arr"]) - hm_to_min(match["dep"]) - wait
        kept.append(r)
    return kept


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
        # 帰りは行単位のパレートフロンティアで間引く(2026-07-10。施設側の乗り場拡大で、
        # 都心どうしのペアが数分おき600便超に爆発したため)。「施設を出る時刻(=発車−徒歩分)が
        # 同じか早いのに、家に着くのが同じか遅い」= どの時点で施設を出るとしても選ぶ理由が
        # ない便だけを落とす。単一の乗り場の時刻表は単調(遅く出る便は遅く着く)なので
        # 1本も落ちない(大郷⇔済生館で検証済み)。落ちるのは都心の冗長な並行便だけ
        inbound[day_type] = frontier_rows(rows_in) if len(rows_in) > 1 else rows_in
        outbound[day_type] = rows_out
        if rows_out or rows_in:
            any_reachable = True

    if not any_reachable:
        return {"unreachable": True}

    entry = {"board_walk_min": board_walk_min, "outbound": outbound, "inbound": inbound}
    # slim方式(2026-07-08)+ 乗り場の複数案内(同2026-07-08 開発者指摘): 行きは
    # 「一番いい乗り場(kantan_board)を通るバス」の便だけに絞る。ただし各便は
    # board_options(その同じバスが通る家の近くの停一覧)を保持しているので、
    # かんたんモードは kantan_board を主にしつつ近隣停を併記でき、しっかりモードは
    # board_options から実在の停を選べる。「1路線に絞る」ことでデータは~20MB台に収まり、
    # 「地区に停が多いとき1停しか出ない」違和感は board_options で解消する。
    # ※pick_kantan_board は全乗り場(board_options)を見て選ぶため、必ず絞り込む前に呼ぶ
    kantan_board = pick_kantan_board(outbound)
    if kantan_board is not None:
        entry["kantan_board"] = kantan_board
        for day_type, rows in outbound.items():
            slimmed = _slim_to_board(rows, kantan_board)
            if rows and not slimmed:
                # ★2026-07-10 バグ修正: kantan_board がこのダイヤ種別では1本も
                # 便を持たない(全種別をカバーする停が存在しないペア)。
                # この種別だけ停を選び直して絞り込む。旧実装はここで空にして
                # しまい「平日だけ0便」の誤った時刻表を出していた
                fallback = pick_kantan_board({day_type: rows})
                slimmed = _slim_to_board(rows, fallback) if fallback else rows
                if not slimmed:   # 想定外の保険(選び直した停でも空なら絞らない)
                    slimmed = rows
            outbound[day_type] = slimmed
    direct_dist_m = haversine_m(district["lat"], district["lon"], facility["lat"], facility["lon"])
    if direct_dist_m <= config.MAX_WALK_TO_STOP_M:
        entry["direct_walk_min"] = round(walk_minutes(direct_dist_m))
    return entry


# かんたんモードで「最寄り停」から「少し遠い停」へ乗り場を切り替える価値があると
# みなす door-to-door 短縮の下限(分)。これ未満の差なら、速さは同等とみなして
# 徒歩が短い(=より近く、なじみのある)停を優先する。開発者の当初の要望
# 「普段使わない停を乗り場に出さないで」と「最寄り≠最短」の折り合いをとるための値
KANTAN_SWITCH_GAIN_MIN = 5


def pick_kantan_board(outbound: dict):
    """かんたんモードで見せる「一番いい乗り場」を1つ選ぶ(設計C)。

    各乗り場について、door-to-door(家を出てから目的地に着くまで=到着−(出発−徒歩分))の
    「ふだんの所要時間」= パレート最適な便での中央値を代表値とする(1本だけ速い外れ値の
    停に引っぱられないよう中央値を使う)。この代表値が最も小さい停=ふだん一番速く着ける停を
    選ぶ。ただし最寄り停との差が KANTAN_SWITCH_GAIN_MIN 未満なら「速さは同等」とみなし、
    徒歩が短い(=近い)方を優先する(普段使わない遠い停をむやみに出さない)。
    最寄り停が一番速ければ当然そのまま選ばれる。
    outbound: {day_type: [itinerary,...]}。行きの便が1本も無ければ None"""
    totals = {}    # board -> door-to-doorのリスト(全ダイヤ種別のパレート最適便)
    walk = {}      # board -> 徒歩分
    coverage = {}  # board -> その停を通る便があるダイヤ種別の集合(2026-07-10 バグ修正)
    served = {day_type for day_type, rows in outbound.items() if rows}
    for day_type, rows in outbound.items():
        for r in frontier_rows(rows):
            arr = hm_to_min(r["arr"])
            opts = r.get("board_options")
            if opts:
                # 同じバスが通る家の近くの停すべてを候補にする(近隣停を featured に選べる)
                for o in opts:
                    b = o["stop"]
                    totals.setdefault(b, []).append(arr - (hm_to_min(o["dep"]) - o["walk_min"]))
                    walk.setdefault(b, o["walk_min"])
                    coverage.setdefault(b, set()).add(day_type)
            else:
                b = r["board"]
                totals.setdefault(b, []).append(arr - _leave_home_min(r))
                walk.setdefault(b, r.get("board_walk_min") or 0)
                coverage.setdefault(b, set()).add(day_type)
    if not totals:
        return None

    # ★2026-07-10 バグ修正: 運行のある全ダイヤ種別をカバーする停を優先する。
    # 従来は全種別まぜこぜの中央値だけで選んでいたため、土日しか走らない
    # コミュニティバスの停(例: 上山のあずま町)が選ばれると、_slim_to_board が
    # 平日の便を全部落とし「平日0便」という誤った時刻表になっていた
    # (実測: 14ペアで平日13〜22便が消失)。全種別をカバーする停が無いペアは
    # build_entry 側の種別別フォールバックが受け持つ
    full_coverage = {b for b, c in coverage.items() if c >= served}
    candidates = {b: t for b, t in totals.items() if b in full_coverage} or totals

    typical = {b: statistics.median(v) for b, v in candidates.items()}
    fastest_time = min(typical.values())
    # 「一番速い停」と同等(差がKANTAN_SWITCH_GAIN_MIN以内)の停をすべて候補にし、
    # その中で徒歩が短い(=近くてなじみのある)停を選ぶ。これにより、
    # ・遠い停が明確に速いときだけ乗り場を切り替え、
    # ・速さが同等なら近い停を優先する(普段使わない停をむやみに出さない)
    eligible = [b for b in typical if typical[b] <= fastest_time + KANTAN_SWITCH_GAIN_MIN]
    return min(eligible, key=lambda b: (walk[b], b))


# ===============================================================
# メイン
# ===============================================================
# ===============================================================
# 対策1(広い地区): 停留所名→座標の索引(stops_index.json)
# GPS利用時に「いまの場所からこのバス停まで およそ◯km」を正直に表示するための
# 小さなデータ。時刻表JSONに実際に現れる停留所名だけを収録する(約400件・約20KB)
# ===============================================================
def collect_stop_names(to: dict, used: set):
    """1地区ぶんの出力(to)を走査して、画面に出うる停留所名を集める。
    alight_place は施設名(バス停ではない)なので集めない"""
    for entry in to.values():
        if entry.get("unreachable"):
            continue
        for direction in ("outbound", "inbound"):
            for rows in entry.get(direction, {}).values():
                for r in rows:
                    used.add(r["board"])
                    used.add(r["alight"])
                    if r.get("transfer"):
                        used.add(r["transfer"]["at"])
                    for opts in (r.get("board_options"), r.get("alight_options")):
                        for o in opts or []:
                            used.add(o["stop"])


def _cluster_stop_points(pts: list, radius_m: float = 1000) -> list:
    """同名停の座標を「たがいに1km以内でつながる」かたまりに分ける(決定的)。
    のりば違い・フィード重複は同じかたまりに、別の町の同名停は別のかたまりになる"""
    pts = sorted(pts)
    parent = list(range(len(pts)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            if haversine_m(pts[i][0], pts[i][1], pts[j][0], pts[j][1]) <= radius_m:
                parent[find(i)] = find(j)
    groups = {}
    for i in range(len(pts)):
        groups.setdefault(find(i), []).append(pts[i])
    return sorted(groups.values())


def build_stops_index(networks: dict, used_names: set) -> dict:
    """停留所名→座標 の索引を作る。同名の停(のりば違い・フィード重複)は
    座標の平均を代表点にする(表示は「およそ◯m/km」の丸めなので十分)。
    ただし別の町に同じ名前の停がある場合(例: 七日町が山形市と他市に存在)、
    全部を平均すると「どちらでもない空中の一点」になり距離表示が大きく狂う
    (2026-07-12 開発者報告「七日町が24km」)。そこで1km超離れたものは別の
    かたまりとして [[lat,lon],...] の複数座標で出力し、JS側が一番近いものを使う。
    3ダイヤ種別のどれかにしか現れない停に備え、全ネットワークのunionから引く"""
    points = {}
    for network in networks.values():
        for info in network.stops.values():
            name = info["name"]
            if name in used_names:
                points.setdefault(name, []).append((float(info["lat"]), float(info["lon"])))
    index = {}
    n_multi = 0
    for name in sorted(points):   # 名前順=冪等
        centers = []
        for c in _cluster_stop_points(points[name]):
            centers.append([round(sum(p[0] for p in c) / len(c), 5),
                            round(sum(p[1] for p in c) / len(c), 5)])
        index[name] = centers[0] if len(centers) == 1 else centers
        if len(centers) > 1:
            n_multi += 1
            print(f"  同名停「{name}」は{len(centers)}か所の別地点として収録しました")
    if n_multi:
        print(f"  (複数地点の停名: {n_multi}件。JSは一番近い地点までの距離を表示)")
    return index


def flatten_districts(districts: list) -> list:
    """計算対象の地区リストを作る。サブ地区(親エントリの "sub" 配列。
    make_subdistricts.py が生成)があれば親と同格の計算対象として追加する。
    親のJSONも残す(旧URL・QR・GPS不使用時のフォールバック先)"""
    flat = []
    for d in districts:
        flat.append(d)
        for s in d.get("sub", []) or []:
            flat.append({**s, "municipality": d.get("municipality"),
                         "parent_id": d["id"]})
    return flat


def main():
    print("地区・行き先マスタを読み込み中...")
    districts_raw = json.loads(DISTRICTS_JSON.read_text(encoding="utf-8"))
    districts = flatten_districts(districts_raw)
    destinations = json.loads(DESTINATIONS_JSON.read_text(encoding="utf-8"))
    n_sub = len(districts) - len(districts_raw)
    print(f"  地区: {len(districts_raw)}件"
          + (f"+サブ地区{n_sub}件" if n_sub else "")
          + f" / 行き先: {len(destinations)}件(採否=採用の分のみ)")

    print("\nmeta.jsonを作成中(date_table)...")
    date_table = build_date_table()
    meta = {
        "generated": date.today().isoformat(),
        "valid_until": f"{VALID_UNTIL[:4]}-{VALID_UNTIL[4:6]}-{VALID_UNTIL[6:]}",
        "day_types": DAY_TYPE_LABELS,
        "date_table": date_table,
        "demand_phone": DEMAND_PHONE,
        "operators": build_operators(),
    }
    WEBAPP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    # JSONは整形せずコンパクトに書く(空白・改行を省く。slim方式の一部。
    # 静的サイトが読むだけのデータで人が直接編集しないため、可読性より約2割の減量を優先)
    META_JSON.write_text(
        json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"→ {META_JSON} ({len(date_table)}日分)")
    print(f"  検算1: 2026-07-20 = {date_table.get('2026-07-20')}(期待値 sunday_holiday)")
    obon = [date_table.get(f"2026-08-{d}") for d in (13, 14, 15)]
    print(f"  検算1: 2026-08-13〜15 = {obon}(期待値 全てsunday_holiday)")

    per_daytype = {}
    networks = {}   # 対策1: stops_index の座標解決に使う(3ダイヤ種別ぶん保持)
    for day_type, ref_date in REFERENCE_DATES.items():
        print(f"\n[{day_type}] {ref_date}のネットワークを構築中...")
        network = build_network(config.GTFS_FEED_DIRS, ref_date)
        networks[day_type] = network
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
    used_stop_names = set()   # 対策1: 画面に出うる停留所名を集める
    for d in districts:
        to = {}
        for f in destinations:
            entry = build_entry(d, f, per_daytype)
            to[f["id"]] = entry
            if entry.get("unreachable"):
                n_unreachable += 1
        collect_stop_names(to, used_stop_names)
        text = json.dumps({"district": d["id"], "to": to}, ensure_ascii=False,
                          separators=(",", ":"))
        (TIMETABLES_DIR / f"{d['id']}.json").write_text(text, encoding="utf-8")
        size = len(text.encode("utf-8"))
        total_bytes += size
        if size > max_bytes:
            max_bytes, max_district = size, d["id"]

    # 対策1: 停留所座標の索引を書き出す
    stops_index = build_stops_index(networks, used_stop_names)
    STOPS_INDEX_JSON.write_text(
        json.dumps(stops_index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    unresolved = sorted(used_stop_names - set(stops_index))
    print(f"\n停留所座標の索引: {len(stops_index)}件 → {STOPS_INDEX_JSON}"
          f"({STOPS_INDEX_JSON.stat().st_size / 1024:.1f} KB)")
    print(f"  検算: 座標を解決できなかった停留所名 {len(unresolved)}件"
          + ("" if not unresolved else f" ★NG 例: {unresolved[:5]}"))

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
