# -*- coding: utf-8 -*-
"""
GTFSデータから「高齢者向け 大活字・往復ペア時刻表」のA4縦PDFを作る汎用スクリプト。

使い方の例:
  # バス停名を検索して候補を見る(PDFは作らない)
  python3 make_pair_timetable.py --feed 山交 --search 病院

  # 往復ペア時刻表のPDFを作る(バス停名は部分一致でOK)
  python3 make_pair_timetable.py --feed 山交 --board 山形駅前 --alight 県立中央病院

  # フィードを再ダウンロードしたいとき
  python3 make_pair_timetable.py --feed 上山 --board 温泉駅前 --alight ヤマザワ --refresh

仕組み:
  1. yamagata_gtfs_feeds.csv(step1で作成)からフィードを選んでダウンロード
  2. calendar.txt の曜日パターンごとに便をグループ化(平日/土曜/日祝など)
     → パターンが複数あれば1ページずつ分けてPDFにする
  3. calendar_dates.txt の例外から「祝日は日曜ダイヤ」「年末年始運休」等の注記を自動生成
  4. HTMLに流し込み、timetable.css のデザインを当てて
     ヘッドレスChrome(--headless --print-to-pdf)でPDF化する
"""

import argparse
import io
import re
import subprocess
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

# macOS上のChrome本体の場所(WindowsやLinuxではパスを変える)
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
FEEDS_CSV = Path("yamagata_gtfs_feeds.csv")

DAY_COLS = ["monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday"]
DAY_KANJI = "月火水木金土日"


# ===============================================================
# コマンドライン引数
# ===============================================================
def parse_args():
    p = argparse.ArgumentParser(
        description="GTFSから大活字・往復ペア時刻表のPDFを作る")
    p.add_argument("--feed", required=True,
                   help="事業者名またはフィード名の一部(例: 山交、上山)")
    p.add_argument("--board", help="乗車バス停名(部分一致)")
    p.add_argument("--alight", help="降車バス停名(部分一致)")
    p.add_argument("--search", metavar="キーワード",
                   help="バス停名を検索して候補を表示するだけで終了")
    p.add_argument("--out", help="出力PDFファイル名(省略時は自動で命名)")
    p.add_argument("--refresh", action="store_true",
                   help="ダウンロード済みでもGTFSを取り直す")
    args = p.parse_args()
    if not args.search and not (args.board and args.alight):
        p.error("--search を使うか、--board と --alight の両方を指定してください")
    return args


# ===============================================================
# フィードの選択とダウンロード
# ===============================================================
def choose_feed(keyword: str) -> pd.Series:
    """山形県フィード一覧CSVから、名前が部分一致するフィードを1つ選ぶ"""
    if not FEEDS_CSV.exists():
        raise SystemExit(f"{FEEDS_CSV} がありません。先に step1_feeds_list.py を実行してください")
    feeds = pd.read_csv(FEEDS_CSV, dtype=str)
    hits = feeds[feeds["事業者名"].str.contains(keyword, regex=False)
                 | feeds["フィード名"].str.contains(keyword, regex=False)]
    if hits.empty:
        raise SystemExit(f"「{keyword}」に一致するフィードがありません")
    if len(hits) > 1:
        print(f"「{keyword}」には複数のフィードが一致します。もう少し具体的に指定してください:")
        print(hits[["事業者名", "フィード名"]].to_string(index=False))
        raise SystemExit(1)
    row = hits.iloc[0]
    print(f"フィード: {row['フィード名']}({row['事業者名']}) 有効期限 {row['データ有効期限']}")
    return row


def download_gtfs(feed_row: pd.Series, refresh: bool) -> Path:
    """GTFSのzipをダウンロードして gtfs_<事業者名>/ に解凍する(2回目以降は再利用)"""
    out_dir = Path("gtfs_" + re.sub(r"[^\w]", "", feed_row["事業者名"]))
    if (out_dir / "stops.txt").exists() and not refresh:
        print(f"ダウンロード済みの {out_dir}/ を使います(取り直すには --refresh)")
        return out_dir
    print(f"ダウンロード中: {feed_row['ダウンロードURL']}")
    res = requests.get(feed_row["ダウンロードURL"], timeout=180)
    res.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
        zf.extractall(out_dir)
    print(f"→ {out_dir}/ に解凍しました")
    return out_dir


def read_gtfs_file(gtfs_dir: Path, name: str) -> pd.DataFrame | None:
    """GTFSのファイルを読む。無い場合(calendar_dates.txt等は任意)は None"""
    path = gtfs_dir / name
    return pd.read_csv(path, dtype=str) if path.exists() else None


# ===============================================================
# バス停の検索
# ===============================================================
def search_stops(stops: pd.DataFrame, keyword: str) -> list[str]:
    """名前にキーワードを含むバス停名の一覧(重複なし)を返す"""
    hit = stops[stops["stop_name"].str.contains(keyword, regex=False, na=False)]
    return sorted(hit["stop_name"].unique())


def find_stop(stops: pd.DataFrame, keyword: str) -> tuple[str, set]:
    """部分一致でバス停を1つに特定し、(バス停名, stop_idの集合) を返す。
    同じ名前で複数のりば(stop_id)がある場合はまとめて扱う"""
    names = search_stops(stops, keyword)
    if not names:
        raise SystemExit(f"「{keyword}」を含むバス停が見つかりません")
    exact = [n for n in names if n == keyword]
    if exact:                      # 完全一致があればそれを優先
        names = exact
    if len(names) > 1:
        print(f"「{keyword}」には複数のバス停が一致します。もう少し具体的に指定してください:")
        for n in names:
            print(f"  {n}")
        raise SystemExit(1)
    name = names[0]
    ids = set(stops.loc[stops["stop_name"] == name, "stop_id"])
    return name, ids


# ===============================================================
# 「曜日タイプ」ごとのグループ化
# ===============================================================
# 注意: service_idは「平日」「毎日」「月〜土」のように重なって運行することがある。
# そのため service_id 単位ではなく、「その曜日に実際に走る便の集合」が同じ曜日を
# 1つのダイヤ(=1ページ)としてまとめる。
# 例: 月〜金の顔ぶれが同じ → 「平日」ページ(平日+毎日+月〜土 の全便が載る)

def days_label(days: list[int]) -> str:
    """曜日番号のリスト(0=月〜6=日)を「平日」「土曜」等のラベルにする"""
    table = {
        tuple(range(7)): "毎日",
        tuple(range(5)): "平日",
        tuple(range(6)): "月〜土",
        (5,): "土曜",
        (6,): "日曜",
        (5, 6): "土・日",
    }
    t = tuple(days)
    return table.get(t, "・".join(DAY_KANJI[w] for w in days) + "曜")


def build_daytype_groups(calendar: pd.DataFrame):
    """calendar.txt から曜日ごとの運行 service_id 集合を求め、
    同じ集合になる曜日をまとめてグループ化する。
    返り値: (グループのリスト, 曜日ごとの通常運行集合 normal[0〜6])"""
    svc_days = {
        r["service_id"]: [int(r[c] == "1") for c in DAY_COLS]
        for _, r in calendar.iterrows()
    }
    # normal[w] = 曜日wに走る service_id の集合(月=0 … 日=6)
    normal = [frozenset(s for s, days in svc_days.items() if days[w])
              for w in range(7)]

    daysets: dict[frozenset, list[int]] = {}
    for w in range(7):
        if normal[w]:  # その曜日に走る便が1本もなければページは作らない
            daysets.setdefault(normal[w], []).append(w)

    groups = [{"days": ws, "service_ids": set(sids), "label": days_label(ws)}
              for sids, ws in daysets.items()]
    groups.sort(key=lambda g: g["days"][0])  # 平日 → 土曜 → 日曜 の順
    return groups, normal


# ===============================================================
# calendar_dates.txt(例外日)から注記を自動生成
# ===============================================================
def analyze_exceptions(cal_dates: pd.DataFrame | None, groups: list[dict],
                       normal: list[frozenset]) -> list[str]:
    """例外日を分類して人向けの注記文を作る。

    考え方: 日付ごとに「その日に実際に走る service_id の集合」を
        (通常の集合 − 取り消し) ∪ 追加
    で計算し、それがどの曜日ダイヤと同じかを比べる。
    ・別の曜日ダイヤと一致       → 「祝日は日曜ダイヤ」「8月13日〜15日は日曜ダイヤ」等
    ・空集合(全便取り消し)     → 「運休日: 12月31日〜1月3日」
    ・どのダイヤとも一致しない   → 「臨時ダイヤの日があります」
    """
    if cal_dates is None or cal_dates.empty:
        return []

    # 日付ごとに「追加された便」「取り消された便」を集める
    by_date: dict[str, dict] = {}
    for _, r in cal_dates.iterrows():
        d = by_date.setdefault(r["date"], {"add": set(), "remove": set()})
        d["add" if r["exception_type"] == "1" else "remove"].add(r["service_id"])

    # 「service_idの集合 → ダイヤのグループ」の逆引き表
    set2group = {frozenset(g["service_ids"]): g for g in groups}

    swap_dates: dict[str, list] = {}   # 一致したダイヤのラベル → 日付リスト
    closed_dates = []                  # 全便運休の日
    other_dates = []                   # どのダイヤとも一致しない日
    for d_str, x in sorted(by_date.items()):
        d = datetime.strptime(d_str, "%Y%m%d").date()
        running = frozenset((normal[d.weekday()] - x["remove"]) | x["add"])
        if running == normal[d.weekday()]:
            continue                   # 例外はあるが結果的に通常どおり
        if not running:
            closed_dates.append(d)
        elif running in set2group:
            swap_dates.setdefault(set2group[running]["label"], []).append(d)
        else:
            other_dates.append(d)

    notes = []
    try:
        import jpholiday  # 日本の祝日判定(pip install jpholiday)
    except ImportError:
        jpholiday = None

    for label, dates in swap_dates.items():
        holidays = [d for d in dates if jpholiday and jpholiday.is_holiday(d)]
        rest = [d for d in dates if d not in holidays]
        # 祝日が3日以上そのダイヤに切り替わっていれば「祝日は○○ダイヤ」とまとめる
        if len(holidays) >= 3:
            notes.append(f"祝日は「{label}」ダイヤで運行します")
            for g in groups:               # ページの見出しも「日曜・祝日」にする
                if g["label"] == label and label != "毎日":
                    g["label"] = f"{label}・祝日"
        else:
            rest = dates                   # まとめずに全日付を列挙する
        if rest:
            notes.append(f"{summarize_dates(rest)}は「{label}」ダイヤで運行します")

    if closed_dates:
        notes.append("運休日: " + summarize_dates(closed_dates))
    if other_dates:
        notes.append(f"{summarize_dates(other_dates)}は臨時ダイヤです(事業者にご確認ください)")
    return notes


def summarize_dates(dates: list) -> str:
    """連続した日付を「12月31日〜1月3日」のような範囲表記にまとめる"""
    dates = sorted(dates)
    ranges, start, prev = [], dates[0], dates[0]
    for d in dates[1:]:
        if (d - prev).days > 1:
            ranges.append((start, prev))
            start = d
        prev = d
    ranges.append((start, prev))
    def f(d):
        return f"{d.month}月{d.day}日"
    return "、".join(f(a) if a == b else f"{f(a)}〜{f(b)}" for a, b in ranges)


# ===============================================================
# 時刻ペアの抽出
# ===============================================================
def to_minutes(hhmmss: str) -> int:
    """'06:52:00' → 412分(0時からの経過分)。GTFSの深夜表記 25:10 等もそのまま扱える"""
    h, m = hhmmss.split(":")[:2]
    return int(h) * 60 + int(m)


def build_pairs(stop_times, trips, service_ids, board_ids, alight_ids):
    """指定ダイヤ(service_ids)の便から、乗車→降車の時刻ペアと使用路線を返す。

    循環線では同じバス停を1つの便が2回通ることがあるため、
    「乗車の並び順 < 降車の並び順」の組み合わせのうち
    いちばん停留所数が少ないもの(=すぐ着く乗り方)を採用する。

    戻り値には実際に使われた乗車stop_id(のりば)の集合も含める。
    同じバス停名でものりば(stop_id)が複数あるフィードで、
    「実際に使われているのりば」を後から特定するために使う。
    """
    use_trips = trips[trips["service_id"].isin(service_ids)]
    st = stop_times[stop_times["stop_id"].isin(board_ids | alight_ids)].copy()
    st = st.merge(use_trips[["trip_id", "route_id"]], on="trip_id")
    st["stop_sequence"] = st["stop_sequence"].astype(int)

    pairs, route_ids, used_board_ids = [], set(), set()
    for _, g in st.groupby("trip_id"):
        boards  = g[g["stop_id"].isin(board_ids)]
        alights = g[g["stop_id"].isin(alight_ids)]
        candidates = [
            (b["departure_time"], a["arrival_time"],
             a["stop_sequence"] - b["stop_sequence"], b["route_id"], b["stop_id"])
            for _, b in boards.iterrows()
            for _, a in alights.iterrows()
            if b["stop_sequence"] < a["stop_sequence"]
        ]
        if candidates:
            dep, arr, _, rid, bsid = min(candidates, key=lambda c: c[2])
            pairs.append({"dep": dep, "arr": arr})
            route_ids.add(rid)
            used_board_ids.add(bsid)
    return (sorted(pairs, key=lambda p: to_minutes(p["dep"])),
            route_ids, used_board_ids)


def ride_minutes(pairs: list[dict]) -> int:
    """全便の平均乗車時間(分)を四捨五入して返す"""
    total = sum(to_minutes(p["arr"]) - to_minutes(p["dep"]) for p in pairs)
    return round(total / len(pairs))


def boarding_platform_label(stops: pd.DataFrame, used_board_ids: set) -> str | None:
    """実際に使われた乗車stop_id(のりば)から「◯番のりば」の表示文字列を作る。

    のりばが複数stop_idに分かれていても、この区間で実際に使う便が
    全て同じplatform_codeなら「3番のりば」のように断定して表示できる。
    のりばが複数に割れている/platform_code列が無い/情報が無い場合は
    誤表示を避けるため None を返す(=表示しない)"""
    if not used_board_ids or "platform_code" not in stops.columns:
        return None
    used = stops[stops["stop_id"].isin(used_board_ids)]
    codes = used["platform_code"]
    if codes.isna().any():
        return None
    uniq = codes.unique()
    if len(uniq) != 1:
        return None
    code = unicodedata.normalize("NFKC", uniq[0])
    return f"{code}番のりば" if re.fullmatch(r"\d+", code) else code


def format_route_line(route_names: list[str]) -> str:
    """路線名のリストから、系統番号ヘッダーの表示文字列を作る。

    「Ｄ５５・Ｃ４」のような英数字の系統コードは全角→半角に統一し、
    「・」で区切られた個々のコードを取り出して重複なく並べ、
    「つぎの番号のバスに のってください: N52 / C2」のように
    役割つきで表示する(初見でも意味が分かるように)。
    系統コードらしくない(路線名がそのまま入っている)場合は、
    従来どおりの路線名表示にフォールバックする"""
    codes = []
    for name in route_names:
        name = unicodedata.normalize("NFKC", name)
        for part in name.split("・"):
            part = part.strip()
            if part and part not in codes:
                codes.append(part)
    if codes and all(re.fullmatch(r"[A-Za-z0-9]{1,4}", c) for c in codes):
        shown = codes[:6]
        suffix = " ほか" if len(codes) > 6 else ""
        return "つぎの番号のバスに のってください: " + " / ".join(shown) + suffix
    suffix = " ほか" if len(codes) > 3 else ""
    return "、".join(codes[:3]) + suffix


# ===============================================================
# HTMLの組み立て
# ===============================================================
def clock_text(hhmmss: str) -> tuple[str, str]:
    """'13:20:00' → ('ごご', '1:20') のように、段のラベルと12時間表記に変換する。
    11:00〜12:59は「ごぜん11時」「ごご0時」の言い方で迷いやすい時間帯なので、
    独立した「ひる」の段にして 11:20 / 12:00 とそのまま表示する。
    18:00以降は「ごご7:20」のように2桁目が消えて「ごぜん7:22」と
    読み間違えやすいため、独立した「よる」の段にする"""
    h, m = hhmmss.split(":")[:2]
    h = int(h) % 24  # GTFSでは深夜便が「25:10」等になることがあるので24で割った余りに
    if h < 11:
        return "ごぜん", f"{h}:{m}"
    if h < 13:
        return "ひる", f"{h}:{m}"
    if h < 18:
        return "ごご", f"{h - 12}:{m}"
    return "よる", f"{h - 12}:{m}"


def count_rows(pairs: list[dict], per_row: int) -> int:
    """時刻セルが「1行に per_row 個」入るとき、全体で何行になるかを数える。
    ごぜん/ひる/ごご/よるの段ごとに切り上げで行数が決まる"""
    counts = {"ごぜん": 0, "ひる": 0, "ごご": 0, "よる": 0}
    for p in pairs:
        ampm, _ = clock_text(p["dep"])
        counts[ampm] += 1
    return sum(-(-n // per_row) for n in counts.values())  # -(-n//d) は切り上げ割り算


def is_dense(out_pairs: list[dict], in_pairs: list[dict]) -> bool:
    """1ページに収まるかを行数で判定する。
    通常サイズ(32pt・4個/行)で入るのは合計8行まで。それを超えたら
    縮小表示(24pt・5個/行)に切り替える"""
    return count_rows(out_pairs, 4) + count_rows(in_pairs, 4) > 8


def time_rows(pairs: list[dict]) -> str:
    """時刻ペアを「ごぜん」「ひる」「ごご」「よる」の段の行に変換する"""
    groups: dict[str, list[str]] = {"ごぜん": [], "ひる": [], "ごご": [], "よる": []}
    for p in pairs:
        ampm, text = clock_text(p["dep"])
        groups[ampm].append(text)

    rows = []
    for label, times in groups.items():
        if not times:  # その時間帯の便が1本もなければ段を作らない
            continue
        cells = "\n".join(
            f'<div class="time-cell"><div class="time-dep">{t}</div></div>'
            for t in times
        )
        rows.append(
            f'<div class="time-row">'
            f'<div class="ampm-label">{label}</div>'
            f'<div class="times">{cells}</div>'
            f'</div>'
        )
    return "\n".join(rows)


def direction_block(css_class: str, label: str, board: str, alight: str,
                    pairs: list[dict], dense: bool,
                    board_platform: str | None) -> str:
    """「行き」または「帰り」のブロック1つ分のHTMLを作る"""
    if not pairs:
        return ""
    dense_class = " dense" if dense else ""
    platform_html = (f'<span class="platform-badge">{board_platform}</span>'
                      if board_platform else "")
    return f"""
    <section class="direction-block {css_class}{dense_class}">
      <div class="direction-header">
        <span class="direction-label">{label}</span>
        <span class="stops-line">
          <span class="stop-label">のる</span>{board}{platform_html}
          →
          <span class="stop-label">おりる</span>{alight}
        </span>
      </div>
      <div class="ride-time">乗車約{ride_minutes(pairs)}分</div>
      {time_rows(pairs)}
    </section>"""


def circled_number(n: int) -> str:
    """1→①、2→②…の丸数字。20を超えたら「(21)」のような表記にフォールバックする"""
    return chr(0x2460 + n - 1) if 1 <= n <= 20 else f"({n})"


def page_footer_html(page_labels: list[str], current_index: int) -> str:
    """複数ダイヤ(複数ページ)のとき、下部に
    「① 平日 / ② 土曜 / ③ 日曜・祝日 (全3枚)」のように
    今見ているページと全体構成を表示する。1ページのみのとき(毎日運行)は表示しない。
    今のページ番号は他より強調して分かるようにする"""
    if len(page_labels) <= 1:
        return ""
    items = []
    for i, label in enumerate(page_labels, start=1):
        text = f"{circled_number(i)} {label}"
        if i == current_index:
            text = f'<span class="page-current">{text}</span>'
        items.append(text)
    return (f'<div class="page-footer">' + " / ".join(items)
            + f" (全{len(page_labels)}枚)</div>")


def render_page(service_label, stop_a, stop_b, out_pairs, in_pairs,
                route_text, notes, validity_text,
                board_platform_out=None, board_platform_in=None,
                page_labels=None, page_index=None) -> str:
    """ダイヤ1種類分(=1ページ)のHTMLを作る"""
    badge = "毎日運行" if service_label == "毎日" else f"{service_label}ダイヤ"
    # 行数が多いページは自動で小さめのセルに切り替える(1ページに収めるため)
    dense = is_dense(out_pairs, in_pairs)
    notes_html = "".join(f"<li>{n}</li>" for n in notes)
    notes_block = f'<ul class="notes">{notes_html}</ul>' if notes else ""
    footer_html = (page_footer_html(page_labels, page_index)
                   if page_labels is not None else "")
    return f"""
  <div class="page">
    <header>
      <h1>バスの時刻表 <span class="service-days">{badge}</span></h1>
      <div class="pair">{stop_a} ⇔ {stop_b}</div>
      <div class="route-name">{route_text}</div>
    </header>
    {direction_block("outbound", "行き", stop_a, stop_b, out_pairs, dense, board_platform_out)}
    {direction_block("return", "帰り", stop_b, stop_a, in_pairs, dense, board_platform_in)}
    {notes_block}
    <p class="validity-note">{validity_text}</p>
    <div class="reserve-space">
      (予備スペース: QRコード・デマンド交通の電話番号を後で配置)
    </div>
    {footer_html}
  </div>"""


# ===============================================================
# メイン処理
# ===============================================================
def main():
    args = parse_args()
    feed_row = choose_feed(args.feed)
    gtfs_dir = download_gtfs(feed_row, args.refresh)

    stops      = read_gtfs_file(gtfs_dir, "stops.txt")
    stop_times = read_gtfs_file(gtfs_dir, "stop_times.txt")
    trips      = read_gtfs_file(gtfs_dir, "trips.txt")
    calendar   = read_gtfs_file(gtfs_dir, "calendar.txt")
    cal_dates  = read_gtfs_file(gtfs_dir, "calendar_dates.txt")
    routes     = read_gtfs_file(gtfs_dir, "routes.txt")
    feed_info  = read_gtfs_file(gtfs_dir, "feed_info.txt")

    # --search: バス停の候補を表示して終了
    if args.search:
        names = search_stops(stops, args.search)
        print(f"\n「{args.search}」を含むバス停: {len(names)}件")
        for n in names:
            print(f"  {n}")
        return

    stop_a, ids_a = find_stop(stops, args.board)
    stop_b, ids_b = find_stop(stops, args.alight)
    print(f"乗車: {stop_a}({len(ids_a)}のりば) / 降車: {stop_b}({len(ids_b)}のりば)")
    for name, ids in [(stop_a, ids_a), (stop_b, ids_b)]:
        if len(ids) <= 1:
            continue
        if "platform_code" not in stops.columns or stops["platform_code"].dropna().empty:
            print(f"  ※「{name}」は{len(ids)}か所のstop_idがありますが、"
                  "このフィードにはのりば情報(platform_code)が無いため、"
                  "紙面での「◯番のりば」表示はできません")
        else:
            codes = stops.loc[stops["stop_id"].isin(ids), "platform_code"]
            print(f"  「{name}」は{len(ids)}か所ののりばに分かれています"
                  f"(platform_code: {sorted(codes.dropna().unique().tolist())}"
                  + (f"、うち{codes.isna().sum()}件は情報なし" if codes.isna().any() else "")
                  + ") → 実際に使う便から自動判定します")

    if calendar is None or calendar.empty:
        raise SystemExit("calendar.txt が無いフィードにはまだ対応していません")
    groups, normal = build_daytype_groups(calendar)
    notes = analyze_exceptions(cal_dates, groups, normal)

    # 欄外の日付: feed_info.txt が無ければ calendar.txt の期間で代用
    def fmt_date(yyyymmdd):
        return f"{int(yyyymmdd[:4])}年{int(yyyymmdd[4:6])}月{int(yyyymmdd[6:8])}日"
    if feed_info is not None and "feed_start_date" in feed_info.columns:
        start_raw = feed_info.iloc[0]["feed_start_date"]
        end_raw   = feed_info.iloc[0]["feed_end_date"]
    else:
        start_raw = calendar["start_date"].min()
        end_raw   = calendar["end_date"].max()
    validity_text = f"{fmt_date(start_raw)}現在のダイヤ / 有効期限 {fmt_date(end_raw)}"

    # ダイヤ(曜日パターン)ごとに1ページ分のデータを集める。
    # ページ番号表示(①②③…)には全体のページ数とラベルが要るので、
    # 先に全ページ分のデータを集めてから最後にまとめてレンダリングする
    page_data = []
    all_route_ids = set()
    for g in groups:
        out_pairs, rids1, board_out = build_pairs(stop_times, trips, g["service_ids"], ids_a, ids_b)
        in_pairs,  rids2, board_in  = build_pairs(stop_times, trips, g["service_ids"], ids_b, ids_a)
        if not out_pairs and not in_pairs:
            continue
        all_route_ids |= rids1 | rids2
        route_names = routes[routes["route_id"].isin(rids1 | rids2)]
        name_col = route_names["route_long_name"].fillna(route_names["route_short_name"])
        uniq = list(dict.fromkeys(name_col))       # 順序を保って重複を除く
        route_text = format_route_line(uniq)
        platform_out = boarding_platform_label(stops, board_out)
        platform_in  = boarding_platform_label(stops, board_in)
        print(f"  [{g['label']}] 行き{len(out_pairs)}本"
              + (f"({platform_out}発)" if platform_out else "")
              + f" / 帰り{len(in_pairs)}本"
              + (f"({platform_in}発)" if platform_in else "")
              + (" (行数が多いため縮小表示)" if is_dense(out_pairs, in_pairs) else ""))
        page_data.append({
            "label": g["label"], "out_pairs": out_pairs, "in_pairs": in_pairs,
            "route_text": route_text,
            "platform_out": platform_out, "platform_in": platform_in,
        })

    if not page_data:
        raise SystemExit(f"「{stop_a}」から「{stop_b}」へ直通する便が見つかりません。"
                         "別のバス停の組み合わせを試してください")

    page_labels = [d["label"] for d in page_data]
    pages = [
        render_page(d["label"], stop_a, stop_b, d["out_pairs"], d["in_pairs"],
                    d["route_text"], notes, validity_text,
                    d["platform_out"], d["platform_in"],
                    page_labels, i)
        for i, d in enumerate(page_data, start=1)
    ]

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<link rel="stylesheet" href="timetable.css">
</head>
<body>
{"".join(pages)}
</body>
</html>"""

    # 出力ファイル名(未指定なら「timetable_事業者_乗車_降車.pdf」)
    safe = lambda s: re.sub(r"[^\w]", "", s)
    out_pdf = args.out or f"timetable_{safe(feed_row['事業者名'])}_{safe(stop_a)}_{safe(stop_b)}.pdf"
    out_html = Path(out_pdf).with_suffix(".html")
    out_html.write_text(html, encoding="utf-8")

    # --virtual-time-budget: 埋め込みフォント等の読み込みが終わるまで印刷を待たせる。
    # これが無いとフォント読込中(文字が一時的に不可視の状態)で印刷されて
    # 「枠だけで文字が無いPDF」ができることがある
    result = subprocess.run(
        [CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
         "--virtual-time-budget=10000",
         f"--print-to-pdf={Path(out_pdf).resolve()}", out_html.resolve().as_uri()],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"ChromeでのPDF変換に失敗しました:\n{result.stderr}")
    print(f"→ {out_pdf} を生成しました({len(pages)}ページ、{out_html} も確認用に保存)")


if __name__ == "__main__":
    main()
