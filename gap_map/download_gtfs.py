# -*- coding: utf-8 -*-
"""9フィード(config.GTFS_FEED_DIRS が指す事業者)のGTFS ZIPを一括取得して展開する。

export_web_data.py / estimate_stop_level.py を新しい環境(この環境=Windows等)で
動かす前に、まずこれを実行してプロジェクトルートに gtfs_◯◯/ を作る。
GTFSデータはCC BY 4.0で認証不要のため、通常のインターネット接続があれば動く
(このリポジトリの開発コンテナはプロキシ制限で失敗したが、一般的なPC環境なら問題ない)。

実行方法(プロジェクトルートで):
    python3 gap_map/download_gtfs.py
"""
import csv
import io
import urllib.request
import zipfile
from pathlib import Path

from region import REGION   # 地域設定(全国展開キットR1。無ければ山形の既定値)

PROJECT_ROOT = Path(__file__).parent.parent
FEEDS_CSV = PROJECT_ROOT / REGION["gtfs_feeds_csv"]

# 「gtfs_◯◯」に対応する事業者名(取得先一覧CSVの「事業者名」列と突き合わせる)。
# region の gtfs_feed_dirs から機械的に導くので、二重管理は不要になった
TARGET_OPERATORS = [d.removeprefix("gtfs_") for d in REGION["gtfs_feed_dirs"]]


def main():
    with open(FEEDS_CSV, encoding="utf-8-sig") as f:
        rows = {r["事業者名"]: r for r in csv.DictReader(f)}

    for name in TARGET_OPERATORS:
        if name not in rows:
            print(f"※ {name}: yamagata_gtfs_feeds.csv に見つからない。手動確認が必要")
            continue
        url = rows[name]["ダウンロードURL"]
        out_dir = PROJECT_ROOT / f"gtfs_{name}"
        if out_dir.exists() and any(out_dir.iterdir()):
            print(f"{name}: gtfs_{name}/ が既にあるのでスキップ")
            continue
        print(f"{name}: ダウンロード中... ({url[:70]}...)")
        with urllib.request.urlopen(url, timeout=60) as resp:
            data = resp.read()
        out_dir.mkdir(exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(out_dir)
        n_files = len(list(out_dir.iterdir()))
        print(f"  → gtfs_{name}/ に展開完了({len(data) // 1024}KB, {n_files}ファイル)")

    print("\n完了。次は python3 gap_map/export_web_data.py を実行してください。")


if __name__ == "__main__":
    main()
