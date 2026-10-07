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
    print(f"フィード: {row['フィード名']}({row['事業者名']})")
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
# ダイヤ(=1ページ)の決め方: 実際の運行日から数える
# ===============================================================
# 以前は calendar.txt の曜日列だけでページを分けていた。しかし事業者によっては
# 「曜日列はほぼ空で、実際の運行日は calendar_dates.txt に書く」形式のフィードがある
# (2026-10 の山交バス新版。曜日列のままだと「月曜」と「火〜金曜」に分かれ、
# しかも「8月1日〜10月25日は臨時ダイヤ」という誤った注記が出た)。
# そこで、期間内の1日ずつについて「この2つのバス停を通る便のうち、その日に
# 走るもの」を数え、走る便がまったく同じ日を1つのダイヤにまとめる。
# この2停に関係しない便の違い(他路線の季節便など)はページを増やさない。

DAY_CATEGORIES = ["平日", "土曜", "日曜・祝日"]   # ページの並び順


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


def day_category(d, jpholiday) -> str:
    """日付を「平日/土曜/日曜・祝日」に分類する(祝日は曜日より優先)"""
    if d.weekday() == 6 or (jpholiday and jpholiday.is_holiday(d)):
        return "日曜・祝日"
    return "土曜" if d.weekday() == 5 else "平日"


def active_services(calendar: pd.DataFrame, cal_dates: pd.DataFrame | None, d) -> set:
    """その日に走る service_id の集合 = (calendar.txtの曜日・期間) − 取消 + 追加"""
    ds = d.strftime("%Y%m%d")
    col = DAY_COLS[d.weekday()]
    running = set(calendar.loc[(calendar["start_date"] <= ds) & (calendar["end_date"] >= ds)
                               & (calendar[col] == "1"), "service_id"])
    if cal_dates is not None:
        today = cal_dates[cal_dates["date"] == ds]
        running -= set(today.loc[today["exception_type"] == "2", "service_id"])
        running |= set(today.loc[today["exception_type"] == "1", "service_id"])
    return running


def pair_trip_services(stop_times, trips, ids_a, ids_b) -> dict:
    """2つのバス停の両方を通る便(行き・帰りどちらの向きでも)→ service_id の対応表"""
    st = stop_times[stop_times["stop_id"].isin(ids_a | ids_b)]
    hit_a = set(st.loc[st["stop_id"].isin(ids_a), "trip_id"])
    hit_b = set(st.loc[st["stop_id"].isin(ids_b), "trip_id"])
    both = trips[trips["trip_id"].isin(hit_a & hit_b)]
    return dict(zip(both["trip_id"], both["service_id"]))


def build_date_groups(calendar, cal_dates, trip_service: dict, start, end):
    """期間内の日付を「走る便の顔ぶれ」でまとめ、ページ一覧と注記を返す。

    返り値: (pages, notes)
      pages: [{"label": 見出し, "service_ids": その日に走るservice_idの集合}, ...]
             (build_pairs にそのまま渡せる。同じグループの日は2停の便が完全に同じ)
      notes: 人向けの注記文のリスト

    手順:
      1. 1日ずつ、2停を通る便のうちその日に走るもの(=顔ぶれ)を求める
      2. 平日/土曜/日曜・祝日 それぞれで、いちばん日数の多い顔ぶれをそのダイヤとする
      3. それ以外の日は、別のダイヤと同じなら「◯日は△△ダイヤ」、便が無ければ運休日、
         7日以上続く別の顔ぶれなら別ページ、少数なら「臨時ダイヤ」と注記する
    """
    try:
        import jpholiday  # 日本の祝日判定(pip install jpholiday)
    except ImportError:
        jpholiday = None

    from datetime import timedelta
    by_key: dict[frozenset, list] = {}     # 顔ぶれ → 日付のリスト
    services_of: dict[frozenset, set] = {}  # 顔ぶれ → 代表日の service_id 集合
    d = start
    while d <= end:
        running = active_services(calendar, cal_dates, d)
        key = frozenset(t for t, sid in trip_service.items() if sid in running)
        by_key.setdefault(key, []).append(d)
        services_of.setdefault(key, running)
        d += timedelta(days=1)

    def cat(day):
        return day_category(day, jpholiday)

    # 2. 種別ごとの「ふつうのダイヤ」= その種別の日をいちばん多く占める顔ぶれ
    main_of: dict[str, frozenset] = {}
    for c in DAY_CATEGORIES:
        counts = {k: sum(1 for x in ds if cat(x) == c) for k, ds in by_key.items()}
        best = max(counts, key=counts.get, default=None)
        if best is not None and counts[best] > 0:
            main_of[c] = best

    # 同じ顔ぶれが複数の種別のふつうのダイヤなら1ページにまとめる(例: 土曜と日祝が同じ)
    pages, page_of_key = [], {}
    for c in DAY_CATEGORIES:
        k = main_of.get(c)
        if k is None or not k:
            continue                       # この種別は2停を通る便が無い
        if k in page_of_key:
            page_of_key[k]["cats"].append(c)
        else:
            page_of_key[k] = {"cats": [c], "service_ids": services_of[k], "key": k}
            pages.append(page_of_key[k])
    for pg in pages:
        pg["label"] = ("毎日" if len(pg["cats"]) == 3
                       else "土曜・日曜・祝日" if pg["cats"] == ["土曜", "日曜・祝日"]
                       else "・".join(pg["cats"]))

    # 3. ふつうのダイヤどおりでない日を分類する
    notes, closed, irregular = [], [], []
    swap: dict[str, list] = {}
    no_service = [c for c in DAY_CATEGORIES if not main_of.get(c)]
    for k, ds in by_key.items():
        odd = [x for x in ds if main_of.get(cat(x)) != k]
        if not odd:
            continue
        if not k:                          # 2停を通る便が1本も無い日
            closed += [x for x in odd if cat(x) not in no_service]
        elif k in page_of_key:             # 別の種別のダイヤで走る日(祝日に平日ダイヤ等)
            swap.setdefault(page_of_key[k]["label"], []).extend(odd)
        elif len(odd) >= 7:                # まとまった期間の別ダイヤ → 独立したページ
            weekdays = sorted({x.weekday() for x in odd})
            pg = {"label": f"{days_label(weekdays)}({summarize_dates(odd)})",
                  "service_ids": services_of[k], "key": k, "cats": []}
            page_of_key[k] = pg
            pages.append(pg)
        else:
            irregular += odd

    # 平日に当たる祝日が日祝ダイヤで走るなら、平日のページを見た人にも分かるように書く
    hol_key = main_of.get("日曜・祝日")
    weekday_hols = [x for x in by_key.get(hol_key, [])
                    if x.weekday() < 5 and jpholiday and jpholiday.is_holiday(x)]
    if weekday_hols and hol_key in page_of_key and page_of_key[hol_key]["label"] != "毎日":
        notes.append(f"祝日は「{page_of_key[hol_key]['label']}」ダイヤで運行します"
                     f"(この期間では{summarize_dates(weekday_hols)})")
    for label, ds in swap.items():
        notes.append(f"{summarize_dates(ds)}は「{label}」ダイヤで運行します")
    if no_service and pages:
        notes.append("・".join(no_service) + "はこの区間のバスは運行していません")
    if closed:
        notes.append("運休日: " + summarize_dates(closed))
    if irregular:
        notes.append(f"{summarize_dates(irregular)}は臨時ダイヤです(事業者にご確認ください)")
    return pages, notes


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

    # ページ分けに使う期間: きょう(過去のダイヤは配る紙に要らない)〜フィードの終わり。
    # すでに期限切れのフィードなら、フィードの全期間で数える
    feed_start = datetime.strptime(start_raw, "%Y%m%d").date()
    feed_end = datetime.strptime(end_raw, "%Y%m%d").date()
    span_start = max(feed_start, datetime.now().date())
    if span_start > feed_end:
        span_start = feed_start
    trip_service = pair_trip_services(stop_times, trips, ids_a, ids_b)
    groups, notes = build_date_groups(calendar, cal_dates, trip_service, span_start, feed_end)
    print(f"  ダイヤの数え方: {span_start}〜{feed_end} の実際の運行日から"
          f"{len(groups)}種類に分けました")

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
