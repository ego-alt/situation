"""Patterson cylindrical projection (Patterson, Šavrič & Jenny, 2015): a
rectangular world map that keeps the shapes of the middle latitudes, where most
news happens, without Mercator's swollen poles. Used both to draw the map and to
place story markers on it, so the two always line up. Output is in map units:
x 0..WIDTH, y 0..HEIGHT, from TOP (the Arctic islands) to BOTTOM (the map drops
Antarctica)."""
import math

C1, C2, C3, C4 = 1.0148, 0.23185, -0.14499, 0.02406
TOP, BOTTOM = 84.0, -60.0

WIDTH = 1000.0


def _raw(lon: float, lat: float) -> tuple[float, float]:
    phi = math.radians(max(min(lat, TOP), BOTTOM))
    return math.radians(lon), C1 * phi + C2 * phi ** 5 + C3 * phi ** 7 + C4 * phi ** 9


_X_MAX = math.pi
_Y_TOP = _raw(0, TOP)[1]
_Y_BOTTOM = _raw(0, BOTTOM)[1]
SCALE = WIDTH / (2 * _X_MAX)
HEIGHT = round((_Y_TOP - _Y_BOTTOM) * SCALE, 1)


def project(lon: float, lat: float) -> tuple[float, float]:
    x, y = _raw(lon, lat)
    return round((x + _X_MAX) * SCALE, 2), round((_Y_TOP - y) * SCALE, 2)
