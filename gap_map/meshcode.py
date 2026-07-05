# -*- coding: utf-8 -*-
"""
標準地域メッシュ(JIS X 0410)のメッシュコード ⇔ 緯度経度 変換。

メッシュコードの桁の意味(4次=500mメッシュの場合、全部で9桁):

    PP QQ  r c  r c  n
    └┬┘ └┬┘ └┬┘ └┬┘ └┬┘
    1次    2次   3次  4次
  (4桁)  (+2桁) (+2桁)(+1桁)

  ・1次メッシュ(4桁 PPQQ):   約80km四方。南端緯度=PP÷1.5、西端経度=QQ+100
  ・2次メッシュ(+2桁 rc):    約10km四方。1次メッシュを縦横8等分した位置(r=縦, c=横)
  ・3次メッシュ(+2桁 rc):    1km四方。   2次メッシュを縦横10等分した位置
  ・4次メッシュ(+1桁 n):     500m四方。  3次メッシュを縦横2等分した位置を
                              1つの数字にまとめたもの(1=南西 2=南東 3=北西 4=北東)

詳しい説明は docs/plan_gap_map.md の §6.1 を参照。
"""

# ---------------------------------------------------------------
# 各次数のメッシュ1つ分の大きさ(緯度・経度、単位は度)
# ---------------------------------------------------------------
# 1次メッシュ: 高さ40分(=2/3度)・幅1度
UNIT1_LAT = 2 / 3
UNIT1_LON = 1.0

# 2次メッシュ: 1次メッシュを縦横8等分
UNIT2_LAT = UNIT1_LAT / 8
UNIT2_LON = UNIT1_LON / 8

# 3次メッシュ(1km): 2次メッシュを縦横10等分
UNIT3_LAT = UNIT2_LAT / 10
UNIT3_LON = UNIT2_LON / 10

# 4次メッシュ(500m): 3次メッシュを縦横2等分
UNIT4_LAT = UNIT3_LAT / 2
UNIT4_LON = UNIT3_LON / 2

# 4次メッシュの枝番(n)と、3次メッシュ内での位置(縦r・横c)の対応
# n: 1=南西 2=南東 3=北西 4=北東
_N_TO_RC = {1: (0, 0), 2: (0, 1), 3: (1, 0), 4: (1, 1)}
_RC_TO_N = {rc: n for n, rc in _N_TO_RC.items()}


def mesh_unit(level: int) -> tuple[float, float]:
    """そのメッシュ次数1つ分の大きさ(緯度の高さ, 経度の幅)を度で返す"""
    return {
        1: (UNIT1_LAT, UNIT1_LON),
        2: (UNIT2_LAT, UNIT2_LON),
        3: (UNIT3_LAT, UNIT3_LON),
        4: (UNIT4_LAT, UNIT4_LON),
    }[level]


def meshcode_to_bounds(meshcode) -> tuple[float, float, float, float]:
    """メッシュコードから、そのマスの範囲(南端緯度, 西端経度, 北端緯度, 東端経度)を返す。

    コードの桁数で次数を自動判定する(4桁=1次, 6桁=2次, 8桁=3次, 9桁=4次)。
    """
    code = str(meshcode)
    lat = int(code[0:2]) / 1.5
    lon = int(code[2:4]) + 100.0
    level = 1

    if len(code) >= 6:
        r, c = int(code[4]), int(code[5])
        lat += r * UNIT2_LAT
        lon += c * UNIT2_LON
        level = 2

    if len(code) >= 8:
        r, c = int(code[6]), int(code[7])
        lat += r * UNIT3_LAT
        lon += c * UNIT3_LON
        level = 3

    if len(code) >= 9:
        r, c = _N_TO_RC[int(code[8])]
        lat += r * UNIT4_LAT
        lon += c * UNIT4_LON
        level = 4

    unit_lat, unit_lon = mesh_unit(level)
    return lat, lon, lat + unit_lat, lon + unit_lon


def meshcode_to_center(meshcode) -> tuple[float, float]:
    """メッシュコードから、そのマスの中心点(緯度, 経度)を返す
    (=南西端 + マスの半分。§6.1で「中心点」と定義されている計算)"""
    south, west, north, east = meshcode_to_bounds(meshcode)
    return (south + north) / 2, (west + east) / 2


def latlon_to_meshcode(lat: float, lon: float, level: int = 4) -> str:
    """緯度経度から、指定した次数のメッシュコードを求める(meshcode_to_bounds の逆変換)。

    どの次数でも「その枠の中で何番目のマスか」を整数で求め、
    余りを次の次数の計算に持ち越す、という手順を次数の分だけ繰り返す。
    """
    p = int(lat * 1.5)
    rem_lat = lat * 1.5 - p           # 1次メッシュ内でのあまり(0〜1未満)
    q = int(lon - 100)
    rem_lon = lon - 100 - q
    code = f"{p:02d}{q:02d}"

    if level >= 2:
        r = int(rem_lat * 8)
        c = int(rem_lon * 8)
        rem_lat = rem_lat * 8 - r
        rem_lon = rem_lon * 8 - c
        code += f"{r}{c}"

    if level >= 3:
        r = int(rem_lat * 10)
        c = int(rem_lon * 10)
        rem_lat = rem_lat * 10 - r
        rem_lon = rem_lon * 10 - c
        code += f"{r}{c}"

    if level >= 4:
        r = int(rem_lat * 2)
        c = int(rem_lon * 2)
        code += str(_RC_TO_N[(r, c)])

    return code


if __name__ == "__main__":
    # 簡易動作確認(詳しいテストは test_meshcode.py を参照)
    south, west, north, east = meshcode_to_bounds("57402296")
    print(f"57402296 の南西端: 北緯{south:.6f}度・東経{west:.6f}度")
    print(f"山形駅の緯度経度から逆変換した3次メッシュ: "
          f"{latlon_to_meshcode(38.2484, 140.3278, level=3)}")
