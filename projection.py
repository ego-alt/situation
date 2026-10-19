"""Equal Earth projection (Šavrič, Patterson & Jenny, 2018): an equal-area
world map. Used both to draw the map and to place story markers on it, so
the two always line up. Output is in map units: x 0..WIDTH, y 0..HEIGHT."""
import math

A1, A2, A3, A4 = 1.340264, -0.081106, 0.000893, 0.003796
M = math.sqrt(3) / 2

WIDTH = 1000.0


def _raw(lon: float, lat: float) -> tuple[float, float]:
    lam, phi = math.radians(lon), math.radians(lat)
    theta = math.asin(M * math.sin(phi))
    t2, t6 = theta * theta, theta ** 6
    x = 2 * math.sqrt(3) * lam * math.cos(theta) / (3 * (A1 + 3 * A2 * t2 + t6 * (7 * A3 + 9 * A4 * t2)))
    y = theta * (A1 + A2 * t2 + t6 * (A3 + A4 * t2))
    return x, y


# The whole globe's width, and the map's height from the pole to 60°S (the
# map drops Antarctica).
_X_MAX = _raw(180, 0)[0]
_Y_TOP = _raw(0, 90)[1]
_Y_BOTTOM = _raw(0, -60)[1]
SCALE = WIDTH / (2 * _X_MAX)
HEIGHT = round((_Y_TOP - _Y_BOTTOM) * SCALE, 1)


def project(lon: float, lat: float) -> tuple[float, float]:
    x, y = _raw(lon, lat)
    return round((x + _X_MAX) * SCALE, 1), round((_Y_TOP - y) * SCALE, 1)
