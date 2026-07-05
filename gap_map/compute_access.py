# -*- coding: utf-8 -*-
"""
メッシュ×施設(病院・スーパー)の到達しやすさを計算し、output/access_mesh.csv を作る。
詳しい定義は docs/plan_gap_map.md §7 を参照。

指標①(最短所要時間): そのメッシュから最寄りの病院/スーパーそれぞれへ、
  平日午前(config.DEPART_TIMESの30分刻み)のどこかに出発して公共交通+徒歩で
  行くときの最短所要時間。徒歩だけで行ける場合はその時間を使う。

指標②(通院可能性): 「11:00までに病院に着き、90分滞在して17:00までに帰宅できるか」
  (Yes/No)と、できる場合の拘束時間(分)。

歩ける範囲の考え方(本スクリプトの前提):
  「バス停まで歩ける上限」(config.MAX_WALK_TO_STOP_M)と同じ距離を、
  「施設まで直接歩く場合」にもそのまま使う。歩行モデルは1つに統一し、
  この距離を超えたら(バスでも施設まで届かなければ)「到達不能」として扱う。

実行方法: プロジェクトのルートで `python3 gap_map/compute_access.py`
"""

import math
import pickle

import numpy as np
import pandas as pd

import config
import transit_core

DEPART_MINUTES = [int(t[:2]) * 60 + int(t[3:]) for t in config.DEPART_TIMES]
HOSPITAL_ARRIVE_BY_MIN = int(config.HOSPITAL_ARRIVE_BY[:2]) * 60 + int(config.HOSPITAL_ARRIVE_BY[3:])
HOME_RETURN_BY_MIN = int(config.HOME_RETURN_BY[:2]) * 60 + int(config.HOME_RETURN_BY[3:])

UNREACHABLE = "到達不能"


def haversine_m_vec(lat1: float, lon1: float, lats2: np.ndarray, lons2: np.ndarray) -> np.ndarray:
    """1点(lat1,lon1)から、複数点(lats2,lons2の配列)への直線距離(m)をまとめて計算する
    (build_network.haversine_m と同じ式をnumpyでベクトル化したもの)"""
    r = 6371000
    p1 = math.radians(lat1)
    p2 = np.radians(lats2)
    dp = np.radians(lats2 - lat1)
    dl = np.radians(lons2 - lon1)
    a = np.sin(dp / 2) ** 2 + math.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def walk_minutes(dist_m) -> float:
    """直線距離(m)を徒歩時間(分)に変換する(§5の式)"""
    return dist_m * config.WALK_DETOUR / config.WALK_SPEED_M_PER_MIN


def nearby_stops(lat: float, lon: float, stop_ids: list, stop_lats: np.ndarray,
                  stop_lons: np.ndarray, max_dist_m: float) -> list:
    """1点(lat,lon)から max_dist_m 以内にある停留所を、近い順の
    [(stop_id, 徒歩分), ...] で返す"""
    dists = haversine_m_vec(lat, lon, stop_lats, stop_lons)
    idx = np.where(dists <= max_dist_m)[0]
    hits = [(stop_ids[i], walk_minutes(dists[i])) for i in idx]
    hits.sort(key=lambda x: x[1])
    return hits


def load_network() -> transit_core.Network:
    with open(config.NETWORK_PKL, "rb") as f:
        return pickle.load(f)


class FacilityIndex:
    """病院・スーパーそれぞれについて、
      - 「歩いて行ける最寄り停留所」一覧(last-mileの徒歩用)
      - 任意の地点からの直線距離・徒歩時間を計算する機能
    をまとめたヘルパー"""

    def __init__(self, facilities: pd.DataFrame, category: str,
                 stop_ids: list, stop_lats: np.ndarray, stop_lons: np.ndarray):
        self.rows = facilities[facilities["category"] == category].reset_index(drop=True)
        self.lats = self.rows["lat"].to_numpy()
        self.lons = self.rows["lon"].to_numpy()
        # 施設ごとの「歩いて行ける最寄り停留所」一覧(全メッシュ・全出発時刻で使い回す)
        self.nearby_stops_per_facility = [
            nearby_stops(lat, lon, stop_ids, stop_lats, stop_lons, config.MAX_WALK_TO_STOP_M)
            for lat, lon in zip(self.lats, self.lons)
        ]
        # 逆引き: 停留所stop_id → [(施設番号, 徒歩分), ...](到達計算の高速化用)
        self.facilities_near_stop = {}
        for fac_idx, hits in enumerate(self.nearby_stops_per_facility):
            for sid, wm in hits:
                self.facilities_near_stop.setdefault(sid, []).append((fac_idx, wm))

    def nearest_by_distance(self, lat: float, lon: float) -> tuple:
        """直線距離で一番近い施設の(施設名, 徒歩分)を返す(空白判定の「徒歩15分」チェック用)"""
        if len(self.rows) == 0:
            return None, None
        dists = haversine_m_vec(lat, lon, self.lats, self.lons)
        i = int(np.argmin(dists))
        return self.rows.iloc[i]["name"], walk_minutes(dists[i])

    def direct_walk_candidates(self, lat: float, lon: float) -> list:
        """メッシュ中心から、直接歩いて行ける(MAX_WALK_TO_STOP_M以内の)施設の
        [(施設名, 徒歩分), ...] を返す(バスに乗らない候補)"""
        if len(self.rows) == 0:
            return []
        dists = haversine_m_vec(lat, lon, self.lats, self.lons)
        idx = np.where(dists <= config.MAX_WALK_TO_STOP_M)[0]
        return [(self.rows.iloc[i]["name"], walk_minutes(dists[i])) for i in idx]

    def best_from_result(self, result: dict) -> tuple:
        """raptor_searchの結果(到達済み停留所の一覧)から、その中で一番早く
        「施設に着ける」組み合わせを探し、(施設名, 到着時刻, 経路の最後の区間の到着停留所)を返す。
        見つからなければ (None, None, None)"""
        best_name, best_arrival, best_stop = None, None, None
        for stop_id, entry in result.items():
            for fac_idx, walk_min in self.facilities_near_stop.get(stop_id, []):
                arrival = entry["arrival"] + round(walk_min)
                if best_arrival is None or arrival < best_arrival:
                    best_arrival = arrival
                    best_name = self.rows.iloc[fac_idx]["name"]
                    best_stop = stop_id
        return best_name, best_arrival, best_stop


def compute_indicator1(mesh_lat: float, mesh_lon: float, mesh_stops: list,
                        network: transit_core.Network, facility_index: "FacilityIndex") -> tuple:
    """指標①: そのメッシュから施設への最短所要時間(分)を求める。
    戻り値: (所要時間(分, 到達不能ならNone), 施設名)"""
    best_min = None
    best_name = None

    # (a) 直接歩いて行ける場合(バス不要)。出発時刻に関係なく一定の候補
    for name, walk_min in facility_index.direct_walk_candidates(mesh_lat, mesh_lon):
        dur = round(walk_min)
        if best_min is None or dur < best_min:
            best_min, best_name = dur, name

    # (b) バスを使う場合。複数の出発時刻を試し、一番短い所要時間を採用する
    if mesh_stops:
        for depart_min in DEPART_MINUTES:
            initial_stops = {sid: depart_min + round(wm) for sid, wm in mesh_stops}
            result = transit_core.raptor_search(
                network, initial_stops, config.MAX_TRANSFERS, config.MIN_TRANSFER_MIN)
            name, arrival, _ = facility_index.best_from_result(result)
            if arrival is not None:
                dur = arrival - depart_min
                if best_min is None or dur < best_min:
                    best_min, best_name = dur, name

    return best_min, best_name


def compute_hospital_visit(mesh_lat: float, mesh_lon: float, mesh_stops: list,
                            network: transit_core.Network,
                            hospital_index: "FacilityIndex") -> tuple:
    """指標②: 「11:00までに病院へ着き、90分滞在して17:00までに帰宅できるか」を判定する。
    戻り値: (Yes/No, 拘束時間(分。家を出てから帰るまでの合計。できない場合はNone))

    行き: 出発時刻を1つずつ試し、11:00までに病院へ着ける中で一番遅い(=無駄なく出発できる)
          出発を採用する。
    帰り: (行きの到着時刻+滞在時間)を新しい出発時刻として、今度は病院最寄りの停留所から
          自宅メッシュの最寄り停留所へ向けて、同じエンジンで「もう一度順方向に」探索する。
    """
    if not mesh_stops:
        return "No", None

    # ---- 行き: 11:00までに着ける中で、一番遅く出発できる(=無駄のない)組み合わせを探す ----
    best_go_depart_min = None
    best_go_arrival = None
    best_go_hospital_stop = None
    for depart_min in DEPART_MINUTES:
        if depart_min > HOSPITAL_ARRIVE_BY_MIN:
            continue
        initial_stops = {sid: depart_min + round(wm) for sid, wm in mesh_stops}
        result = transit_core.raptor_search(
            network, initial_stops, config.MAX_TRANSFERS, config.MIN_TRANSFER_MIN)
        _, arrival, hospital_stop = hospital_index.best_from_result(result)
        if arrival is not None and arrival <= HOSPITAL_ARRIVE_BY_MIN:
            if best_go_depart_min is None or depart_min > best_go_depart_min:
                best_go_depart_min = depart_min
                best_go_arrival = arrival
                best_go_hospital_stop = hospital_stop

    if best_go_depart_min is None:
        return "No", None

    # ---- 帰り: (到着+滞在時間)を出発時刻として、病院→自宅メッシュへ向けて探索する ----
    return_depart_min = best_go_arrival + config.HOSPITAL_STAY_MIN
    # 病院最寄り停留所から、乗換の最小時間ぶんを空けて出発できると考える
    hospital_board_stops = {best_go_hospital_stop: return_depart_min + config.MIN_TRANSFER_MIN}
    result = transit_core.raptor_search(
        network, hospital_board_stops, config.MAX_TRANSFERS, config.MIN_TRANSFER_MIN)

    best_home_arrival = None
    for stop_id, walk_min in mesh_stops:
        if stop_id in result:
            arrival = result[stop_id]["arrival"] + round(walk_min)
            if best_home_arrival is None or arrival < best_home_arrival:
                best_home_arrival = arrival

    if best_home_arrival is None or best_home_arrival > HOME_RETURN_BY_MIN:
        return "No", None

    visit_total_min = best_home_arrival - best_go_depart_min
    return "Yes", visit_total_min


def main():
    print("ネットワーク・メッシュ・施設データを読み込み中...")
    network = load_network()
    meshes = pd.read_csv(config.TARGET_MESHES_CSV)
    facilities = pd.read_csv(config.FACILITIES_CSV)

    stop_ids = list(network.stops.keys())
    stop_lats = np.array([network.stops[s]["lat"] for s in stop_ids])
    stop_lons = np.array([network.stops[s]["lon"] for s in stop_ids])

    print("施設の索引(最寄り停留所)を作成中...")
    hospital_index = FacilityIndex(facilities, "hospital", stop_ids, stop_lats, stop_lons)
    super_index = FacilityIndex(facilities, "supermarket", stop_ids, stop_lats, stop_lons)
    print(f"  病院: {len(hospital_index.rows)}件 / スーパー: {len(super_index.rows)}件")

    rows = []
    n = len(meshes)
    for i, mesh in enumerate(meshes.itertuples()):
        if i % 100 == 0:
            print(f"  {i}/{n} メッシュ処理中...")

        mesh_stops = nearby_stops(mesh.lat, mesh.lon, stop_ids, stop_lats, stop_lons,
                                   config.MAX_WALK_TO_STOP_M)
        if mesh_stops:
            nearest_stop_id, walk_to_stop_min = mesh_stops[0]
            nearest_stop_name = network.stops[nearest_stop_id]["name"]
            walk_to_stop_min = round(walk_to_stop_min)
        else:
            nearest_stop_name, walk_to_stop_min = None, None

        hosp_min, hosp_name = compute_indicator1(mesh.lat, mesh.lon, mesh_stops, network, hospital_index)
        super_min, super_name = compute_indicator1(mesh.lat, mesh.lon, mesh_stops, network, super_index)
        visit_ok, visit_total_min = compute_hospital_visit(
            mesh.lat, mesh.lon, mesh_stops, network, hospital_index)

        # 空白判定の「徒歩15分」チェックは、直線距離で一番近い病院に対して行う
        _, walk_direct_min = hospital_index.nearest_by_distance(mesh.lat, mesh.lon)
        walkable_by_foot = walk_direct_min is not None and walk_direct_min <= config.WALKABLE_FACILITY_MIN

        is_gap = (
            mesh.population > 0
            and not walkable_by_foot
            and (hosp_min is None or hosp_min > config.GAP_THRESHOLD_MIN or visit_ok == "No")
        )

        rows.append({
            "meshcode": mesh.meshcode,
            "population": mesh.population,
            "municipality": mesh.municipality,
            "nearest_stop_name": nearest_stop_name,
            "walk_to_stop_min": walk_to_stop_min,
            "time_to_hospital_min": hosp_min if hosp_min is not None else UNREACHABLE,
            "hospital_name": hosp_name,
            "time_to_super_min": super_min if super_min is not None else UNREACHABLE,
            "super_name": super_name,
            "hospital_visit_ok": visit_ok,
            "visit_total_min": visit_total_min,
            "is_gap": is_gap,
        })

    df = pd.DataFrame(rows)
    config.OUTPUT_DIR.mkdir(exist_ok=True)
    df.to_csv(config.ACCESS_MESH_CSV, index=False)
    print(f"\n→ {config.ACCESS_MESH_CSV} に{len(df)}件を書き出しました")

    print("\n=== 完成条件チェック(計画書M4) ===")
    reachable = df[df["time_to_hospital_min"] != UNREACHABLE].copy()
    reachable["time_to_hospital_min"] = reachable["time_to_hospital_min"].astype(float)
    print(f"到達不能メッシュ: {(df['time_to_hospital_min'] == UNREACHABLE).sum()}件 / {len(df)}件")
    print(f"空白メッシュ(is_gap=True): {df['is_gap'].sum()}件")
    print(f"病院所要時間の分布:\n{reachable['time_to_hospital_min'].describe()}")

    print("\n=== 指標②の検証 ===")
    yasoen = df[df["meshcode"] == 574022381]
    if not yasoen.empty:
        print(f"野草園メッシュ(574022381)の hospital_visit_ok = "
              f"{yasoen.iloc[0]['hospital_visit_ok']} (期待値: No)")

    eki_mesh = meshes.assign(
        d2=(meshes["lat"] - 38.2484) ** 2 + (meshes["lon"] - 140.3278) ** 2
    ).nsmallest(5, "d2")["meshcode"]
    near_eki = df[df["meshcode"].isin(eki_mesh)]
    print("山形駅周辺メッシュの hospital_visit_ok:")
    print(near_eki[["meshcode", "hospital_visit_ok", "visit_total_min"]].to_string(index=False))

    print(f"\nhospital_visit_ok の内訳:\n{df['hospital_visit_ok'].value_counts().to_string()}")

    # 「隠れ空白」: 指標①では60分以内(=到達可能)なのに、指標②(通院可能性)ではNo
    # (到達不能の行は文字列"到達不能"のままなのでpd.to_numericでNaN化してから比較する)
    hosp_min_numeric = pd.to_numeric(df["time_to_hospital_min"], errors="coerce")
    hidden_gap_mask = (
        (hosp_min_numeric <= config.GAP_THRESHOLD_MIN)
        & (df["hospital_visit_ok"] == "No")
    )
    hidden = df[hidden_gap_mask].merge(
        meshes[["meshcode", "population_65plus", "population_75plus"]], on="meshcode")
    print(f"\n=== 隠れ空白(指標①は60分以内、指標②はNo): {len(hidden)}件 ===")
    print(f"人口合計: {hidden['population'].sum():,.0f}人")
    print(f"65歳以上合計: {hidden['population_65plus'].sum():,.0f}人")
    print(f"75歳以上合計: {hidden['population_75plus'].sum():,.0f}人")


if __name__ == "__main__":
    main()
