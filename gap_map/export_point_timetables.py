"""案D 段階3-2(後半): 「地区の代表点ではない地点」を家として、工場と同じ手順で時刻表を作る。

端末の時刻表(webapp/engine/timetable.js)は、地区の代表点では地区ファイルと全件一致した
(gap_map/verify_timetable_parity.js)。ただし代表点は62か所しかなく、「ある家の位置でだけ
起きる」ずれ(同じ距離の停が2つある・徒歩分がちょうど x.5 分になる 等)は試せていない。
そこで、人が住むメッシュ(817個)の中心を「家」として、工場の関数をそのまま使って答えを作り、
JS と突き合わせる材料にする。

★工場(export_web_data.py)の関数を import して呼ぶだけで、計算を書き直さない。
  地区ファイル・配布データには何も書き込まない(出力は指定したファイルだけ)。
★済生病院のシャトル(restricted_feeds)は入れない。JS 側も通常の配布ネットワークで計算するので、
  ここでは f20 も含めて「シャトル無し」どうしで比べる(シャトル入りの照合は 3-3)。

使い方(GTFSのある環境=Macで。プロジェクトルートから):
  python3 gap_map/export_point_timetables.py --every 4 --out /tmp/points.jsonl
  node gap_map/verify_timetable_parity.js --points /tmp/points.jsonl

出力は1行に1地点(JSON Lines)。1地点で約1MBあり、817地点を1つのJSONにすると
約760MBになって Node が1つの文字列として読み込めないため
"""
import argparse
import json
import time
from pathlib import Path

import config
import export_web_data as ew
from build_network import build_network

PROJECT_ROOT = Path(__file__).parent.parent
MESH_INDEX_JSON = PROJECT_ROOT / "webapp" / "data" / "mesh_index.json"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--every", type=int, default=1, help="メッシュをN個おきに使う(既定: 全部)")
    ap.add_argument("--offset", type=int, default=0,
                    help="N個おきの何番目から使うか。--every 4 を --offset 0〜3 で4回回すと全部になる"
                         "(全地点を1回で計算するとメモリを大きく使うため分けられるようにした)")
    ap.add_argument("--out", required=True, help="書き出すJSONファイル")
    args = ap.parse_args()

    meshes = json.loads(MESH_INDEX_JSON.read_text(encoding="utf-8"))["meshes"]
    # 地区の代わりに「地点」を渡す。build_entry は id・lat・lon・name だけを使う
    points = [{"id": f"m{i:03d}", "name": f"地点{i}", "lat": lat, "lon": lon}
              for i, (lat, lon, _d) in enumerate(meshes) if i % args.every == args.offset]
    destinations = json.loads(ew.DESTINATIONS_JSON.read_text(encoding="utf-8"))
    print(f"地点 {len(points)} / 行き先 {len(destinations)}")

    per_daytype = {}
    for day_type, ref_date in ew.REFERENCE_DATES.items():
        t0 = time.time()
        network = build_network(config.GTFS_FEED_DIRS, ref_date)
        per_daytype[day_type] = ew.compute_day_type_schedules(
            network, points, destinations, ew.StopIndex(network), ew.build_headsign_map(network))
        print(f"[{day_type}] {time.time() - t0:.0f}秒")

    with open(args.out, "w", encoding="utf-8") as fh:
        for p in points:
            to = {f["id"]: ew.build_entry(p, f, per_daytype) for f in destinations}
            fh.write(json.dumps({"id": p["id"], "home": p, "pts": ew.attach_stop_points(to), "to": to},
                                ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"→ {args.out}")


if __name__ == "__main__":
    main()
