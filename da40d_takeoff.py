"""
da40d_takeoff — DA40-D Takeoff Distance over 50 ft Obstacle
=============================================================
Digitised three-panel AFM chart (Section 5, ALTIMETER SETTING 1013.25 hPa).

Public API
----------
pressure_altitude_ft(elevation_ft, qnh_hpa)
    Convert field elevation + QNH to pressure altitude.

DA40D_TakeoffChart.from_wpd_files(left, middle, right)
    Load chart from three WebPlotDigitizer JSON exports.

chart.tod_m(pressure_altitude_ft, oat_c, mass_kg, wind_kts) -> float
    Takeoff distance over 50 ft obstacle in metres.

chart.tod_breakdown(...)  -> dict
    Same computation, returns intermediate panel values.

chart.tod_from_elevation(elevation_ft, qnh_hpa, oat_c, mass_kg, wind_kts) -> float
    Convenience wrapper: accepts field elevation + QNH instead of PA.

Wind convention
---------------
  wind_kts > 0  headwind  (reduces distance)  valid 0 … +20 kts
  wind_kts < 0  tailwind  (increases distance) valid 0 …  −10 kts
  wind_kts = 0  calm

Input limits (raise OutOfRangeError if exceeded)
-------------------------------------------------
  pressure_altitude_ft  :    0 … 10 000 ft
  elevation_ft          :  −1 000 … 14 000 ft   (only via tod_from_elevation)
  qnh_hpa               :  900 … 1 100 hPa       (only via tod_from_elevation)
  oat_c                 :  −35 … +50 °C
  mass_kg               :  750 … 1 150 kg
  wind_kts              :  −10 … +20 kts
"""

from __future__ import annotations

import json
import re
from bisect import bisect_left
from typing import Dict, List, Tuple

__all__ = [
    "OutOfRangeError",
    "pressure_altitude_ft",
    "DA40D_TakeoffChart",
]

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

Point = Tuple[float, float]   # (x, y)

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class OutOfRangeError(ValueError):
    """Raised when an input is outside the chart's valid range."""


# ---------------------------------------------------------------------------
# Pressure altitude
# ---------------------------------------------------------------------------

# ISA sea-level lapse rate:  1 hPa ≈ 27 ft
_FT_PER_HPA = 27.0

#: Valid QNH range accepted by :func:`pressure_altitude_ft`.
QNH_MIN_HPA  = 900.0
QNH_MAX_HPA  = 1100.0


def pressure_altitude_ft(elevation_ft: float, qnh_hpa: float) -> float:
    """
    Convert field elevation and QNH altimeter setting to pressure altitude.

    Uses the standard ISA approximation (1 hPa ≈ 27 ft), accurate to within
    a few feet for the altitude and pressure ranges relevant to takeoff
    performance calculations.

    Parameters
    ----------
    elevation_ft : float
        Aerodrome elevation above MSL [ft].  Negative values (below MSL) are
        accepted without restriction.
    qnh_hpa : float
        QNH altimeter setting [hPa].  Must be in the range 900 … 1 100 hPa.

    Returns
    -------
    float
        Pressure altitude [ft].

    Raises
    ------
    OutOfRangeError
        If *qnh_hpa* is outside 900 … 1 100 hPa.

    Examples
    --------
    >>> pressure_altitude_ft(1500, 1013.25)   # standard day → PA = elevation
    1500.0
    >>> pressure_altitude_ft(1500, 993.25)    # low pressure → PA > elevation
    2040.0
    """
    if not (QNH_MIN_HPA <= qnh_hpa <= QNH_MAX_HPA):
        raise OutOfRangeError(
            f"qnh_hpa={qnh_hpa} outside valid range "
            f"{QNH_MIN_HPA} … {QNH_MAX_HPA} hPa"
        )
    return elevation_ft + (1013.25 - qnh_hpa) * _FT_PER_HPA


# ---------------------------------------------------------------------------
# Input validation limits
# ---------------------------------------------------------------------------

_PA_MIN, _PA_MAX       =     0.0, 10_000.0   # ft
_OAT_MIN, _OAT_MAX     =   -35.0,     50.0   # °C
_MASS_MIN, _MASS_MAX   =   750.0,  1_150.0   # kg
_WIND_MIN, _WIND_MAX   =   -10.0,     20.0   # kts  (negative = tailwind)


def _validate(pa_ft: float, oat_c: float, mass_kg: float, wind_kts: float) -> None:
    checks = [
        (pa_ft,    _PA_MIN,   _PA_MAX,   "pressure_altitude_ft", "ft"),
        (oat_c,    _OAT_MIN,  _OAT_MAX,  "oat_c",                "°C"),
        (mass_kg,  _MASS_MIN, _MASS_MAX, "mass_kg",              "kg"),
        (wind_kts, _WIND_MIN, _WIND_MAX, "wind_kts",             "kts"),
    ]
    for value, lo, hi, name, unit in checks:
        if not (lo <= value <= hi):
            raise OutOfRangeError(
                f"{name}={value} outside valid range {lo} … {hi} {unit}"
            )


# ---------------------------------------------------------------------------
# Interpolation helpers
# ---------------------------------------------------------------------------

def _lerp(x: float, x0: float, y0: float, x1: float, y1: float) -> float:
    if x1 == x0:
        return y0
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def _interp_sorted(pts: List[Point], x: float) -> float:
    """Piecewise-linear interpolation over sorted [(x0,y0), …]; clamped."""
    if x <= pts[0][0]:
        return pts[0][1]
    if x >= pts[-1][0]:
        return pts[-1][1]
    i = bisect_left([p[0] for p in pts], x)
    return _lerp(x, pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1])


def _interp_2d(lines: list, key_val: float, x: float) -> float:
    """
    Bilinear interpolation across a list of curve objects
    ``[{"entry": float, "pts": List[Point]}, …]`` sorted by ``entry``.

    1. Find the two lines bracketing *key_val*.
    2. For each, interpolate at *x*.
    3. Interpolate the two results by *key_val*.
    """
    keys = [l["entry"] for l in lines]
    if key_val <= keys[0]:
        return _interp_sorted(lines[0]["pts"], x)
    if key_val >= keys[-1]:
        return _interp_sorted(lines[-1]["pts"], x)
    i = bisect_left(keys, key_val)
    d0 = _interp_sorted(lines[i - 1]["pts"], x)
    d1 = _interp_sorted(lines[i]["pts"], x)
    t = (key_val - keys[i - 1]) / (keys[i] - keys[i - 1])
    return d0 + t * (d1 - d0)


# ---------------------------------------------------------------------------
# Left panel  —  baseline distance = f(PA [ft], OAT [°C])
# ---------------------------------------------------------------------------

def _parse_altitude_ft(name: str) -> float:
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*ft\s*$", name)
    if not m:
        raise ValueError(f"Cannot parse altitude from dataset name: {name!r}")
    return float(m.group(1))


def _build_left(wpd: dict) -> Dict[float, List[Point]]:
    curves: Dict[float, List[Point]] = {}
    for ds in wpd["datasetColl"]:
        alt = _parse_altitude_ft(ds["name"])
        pts = sorted(
            [(float(d["value"][0]), float(d["value"][1])) for d in ds["data"]],
        )
        curves[alt] = pts
    return dict(sorted(curves.items()))


class _LeftChart:
    def __init__(self, curves: Dict[float, List[Point]]) -> None:
        self._curves = curves
        self._alts   = list(curves.keys())

    def query(self, pa_ft: float, oat_c: float) -> float:
        alts = self._alts
        if pa_ft <= alts[0]:
            return _interp_sorted(self._curves[alts[0]], oat_c)
        if pa_ft >= alts[-1]:
            return _interp_sorted(self._curves[alts[-1]], oat_c)
        i  = bisect_left(alts, pa_ft)
        d0 = _interp_sorted(self._curves[alts[i - 1]], oat_c)
        d1 = _interp_sorted(self._curves[alts[i]],     oat_c)
        return _lerp(pa_ft, alts[i - 1], d0, alts[i], d1)


# ---------------------------------------------------------------------------
# Middle panel  —  mass correction
# ---------------------------------------------------------------------------

# WPD x-axis anchors (derived from digitised data):
#   x_wpd ≈ 53.56  →  1 150 kg  (MTOW, left edge)
#   x_wpd ≈ 79.40  →    750 kg  (right edge)
_MID_X_LEFT,  _MID_MASS_LEFT  = 53.56, 1_150.0
_MID_X_RIGHT, _MID_MASS_RIGHT = 79.40,   750.0


def _mid_x_to_mass(x: float) -> float:
    return _MID_MASS_LEFT + (x - _MID_X_LEFT) * (
        _MID_MASS_RIGHT - _MID_MASS_LEFT
    ) / (_MID_X_RIGHT - _MID_X_LEFT)


def _build_middle(wpd: dict) -> list:
    fan_lines = []
    for ds in wpd["datasetColl"]:
        raw = [(float(d["value"][0]), float(d["value"][1])) for d in ds["data"]]
        if not raw:
            continue
        # convert x → mass, sort ascending (750 … 1150 kg)
        pts = sorted([(_mid_x_to_mass(x), y) for x, y in raw])
        # baseline = distance at MTOW (1 150 kg); extrapolate if needed
        m0, d0 = pts[-1]
        m1, d1 = pts[-2]
        baseline = (
            d0 + (d1 - d0) * (_MID_MASS_LEFT - m0) / (m1 - m0)
            if m1 != m0 else d0
        )
        fan_lines.append({"entry": baseline, "pts": pts})

    return sorted(fan_lines, key=lambda fl: fl["entry"])


class _MiddleChart:
    def __init__(self, fan_lines: list) -> None:
        self._lines = fan_lines

    def query(self, baseline_m: float, mass_kg: float) -> float:
        return _interp_2d(self._lines, baseline_m, mass_kg)


# ---------------------------------------------------------------------------
# Right panel  —  wind correction
# ---------------------------------------------------------------------------

# WPD x-axis anchors for the right panel:
#   x_wpd ≈  83.2  →   0 kts  (left edge)
#   x_wpd ≈ 112.8  →  20 kts  (right edge, max headwind)
_RGT_X_ZERO  =  83.2
_RGT_X_MAX   = 112.8
_RGT_KTS_MAX =  20.0


def _rgt_x_to_kts(x: float) -> float:
    return (x - _RGT_X_ZERO) * _RGT_KTS_MAX / (_RGT_X_MAX - _RGT_X_ZERO)


def _build_right(wpd: dict) -> Tuple[list, list]:
    hw_lines: list = []
    tw_lines: list = []
    for ds in wpd["datasetColl"]:
        raw = [(float(d["value"][0]), float(d["value"][1])) for d in ds["data"]]
        if not raw:
            continue
        pts   = sorted([(_rgt_x_to_kts(x), y) for x, y in raw])
        entry = pts[0][1]   # distance at ~0 kts
        line  = {"entry": entry, "pts": pts}
        if ds["name"].startswith("Headwind"):
            hw_lines.append(line)
        elif ds["name"].startswith("Tailwind"):
            tw_lines.append(line)

    hw_lines.sort(key=lambda l: l["entry"])
    tw_lines.sort(key=lambda l: l["entry"])
    return hw_lines, tw_lines


class _RightChart:
    def __init__(self, hw_lines: list, tw_lines: list) -> None:
        self._hw = hw_lines
        self._tw = tw_lines

    def query(self, corrected_m: float, wind_kts: float) -> float:
        """wind_kts > 0 = headwind, < 0 = tailwind, 0 = calm."""
        if wind_kts == 0.0:
            return corrected_m
        lines    = self._hw if wind_kts > 0 else self._tw
        abs_wind = abs(wind_kts)
        return _interp_2d(lines, corrected_m, abs_wind)


# ---------------------------------------------------------------------------
# Public chart class
# ---------------------------------------------------------------------------


class DA40D_TakeoffChart:
    """
    DA40-D takeoff distance over 50 ft obstacle (AFM Section 5).

    Load once, query as often as needed.  All heavy parsing happens in
    :meth:`from_wpd_files`; individual queries are cheap interpolations.

    Parameters
    ----------
    See :meth:`from_wpd_files`.

    Notes
    -----
    The underlying chart is valid for:

    * Altimeter setting 1013.25 hPa (pressure altitude)
    * Paved, level, dry runway (no grass/slope correction)
    * Flaps 0° (clean configuration per DA40-D AFM)
    """

    def __init__(
        self,
        left:   _LeftChart,
        middle: _MiddleChart,
        right:  _RightChart,
    ) -> None:
        self._left   = left
        self._middle = middle
        self._right  = right

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_wpd_files(
        cls,
        left_path:   str,
        middle_path: str,
        right_path:  str,
    ) -> "DA40D_TakeoffChart":
        """
        Create a chart instance from three WebPlotDigitizer JSON exports.

        Parameters
        ----------
        left_path : str
            Path to the left-panel WPD JSON  (OAT / pressure altitude →
            baseline distance).
        middle_path : str
            Path to the middle-panel WPD JSON  (baseline distance / mass →
            corrected distance).
        right_path : str
            Path to the right-panel WPD JSON  (corrected distance / wind →
            final TOD).

        Returns
        -------
        DA40D_TakeoffChart

        Raises
        ------
        FileNotFoundError
            If any of the three paths cannot be opened.
        ValueError
            If a WPD file cannot be parsed (e.g. wrong panel exported).
        """
        with open(left_path)   as f: wpd_l = json.load(f)
        with open(middle_path) as f: wpd_m = json.load(f)
        with open(right_path)  as f: wpd_r = json.load(f)

        left   = _LeftChart(_build_left(wpd_l))
        middle = _MiddleChart(_build_middle(wpd_m))
        hw, tw = _build_right(wpd_r)
        right  = _RightChart(hw, tw)
        return cls(left, middle, right)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def tod_m(
        self,
        pressure_altitude_ft: float,
        oat_c:      float,
        mass_kg:    float,
        wind_kts:   float = 0.0,
    ) -> float:
        """
        Takeoff distance over 50 ft obstacle.

        Parameters
        ----------
        pressure_altitude_ft : float
            Pressure altitude [ft].  Range: 0 … 10 000 ft.
            Use :func:`pressure_altitude_ft` to convert from QNH + elevation.
        oat_c : float
            Outside air temperature [°C].  Range: −35 … +50 °C.
        mass_kg : float
            Takeoff mass [kg].  Range: 750 … 1 150 kg.
        wind_kts : float, optional
            Wind component along the runway [kts].
            Positive = headwind (0 … +20 kts).
            Negative = tailwind (−10 … 0 kts).
            Default: 0 (calm).

        Returns
        -------
        float
            TOD over 50 ft obstacle [m].

        Raises
        ------
        OutOfRangeError
            If any parameter is outside its valid range.

        Examples
        --------
        >>> chart.tod_m(4000, 22, 1070, wind_kts=10)   # AFM example → ~600 m
        618.6
        """
        _validate(pressure_altitude_ft, oat_c, mass_kg, wind_kts)
        baseline  = self._left.query(pressure_altitude_ft, oat_c)
        corrected = self._middle.query(baseline, mass_kg)
        return self._right.query(corrected, wind_kts)

    def tod_breakdown(
        self,
        pressure_altitude_ft: float,
        oat_c:      float,
        mass_kg:    float,
        wind_kts:   float = 0.0,
    ) -> dict:
        """
        Same as :meth:`tod_m` but returns all three intermediate values.

        Returns
        -------
        dict with keys:
            ``baseline_m``   — left panel output (distance at MTOW) [m]
            ``corrected_m``  — middle panel output (mass-corrected) [m]
            ``final_m``      — right panel output (wind-corrected TOD) [m]

        Raises
        ------
        OutOfRangeError
            If any parameter is outside its valid range.
        """
        _validate(pressure_altitude_ft, oat_c, mass_kg, wind_kts)
        baseline  = self._left.query(pressure_altitude_ft, oat_c)
        corrected = self._middle.query(baseline, mass_kg)
        final     = self._right.query(corrected, wind_kts)
        return {
            "baseline_m":  round(baseline,  1),
            "corrected_m": round(corrected, 1),
            "final_m":     round(final,     1),
        }

    def tod_from_elevation(
        self,
        elevation_ft: float,
        qnh_hpa:      float,
        oat_c:        float,
        mass_kg:      float,
        wind_kts:     float = 0.0,
    ) -> float:
        """
        Convenience wrapper: derives pressure altitude from field elevation
        and QNH, then calls :meth:`tod_m`.

        Parameters
        ----------
        elevation_ft : float
            Aerodrome elevation above MSL [ft].
        qnh_hpa : float
            QNH altimeter setting [hPa].  Range: 900 … 1 100 hPa.
        oat_c : float
            Outside air temperature [°C].  Range: −35 … +50 °C.
        mass_kg : float
            Takeoff mass [kg].  Range: 750 … 1 150 kg.
        wind_kts : float, optional
            Wind component [kts].  Positive = headwind, negative = tailwind.

        Returns
        -------
        float
            TOD over 50 ft obstacle [m].

        Raises
        ------
        OutOfRangeError
            If QNH is out of range, or if the derived pressure altitude falls
            outside 0 … 10 000 ft, or if any other parameter is out of range.

        Examples
        --------
        >>> chart.tod_from_elevation(1321, 1013, 22, 1070, wind_kts=10)
        """
        pa = pressure_altitude_ft(elevation_ft, qnh_hpa)
        return self.tod_m(pa, oat_c, mass_kg, wind_kts)


# ---------------------------------------------------------------------------
# CLI smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    chart = DA40D_TakeoffChart.from_wpd_files(
        "wpd_project.json",
        "wpd_project_middle.json",
        "wpd_project_right.json",
    )

    print("=== AFM example  (expected: baseline~840 / corrected~670 / TOD 600 m) ===")
    bd = chart.tod_breakdown(4000, 22, 1070, wind_kts=10)
    print(f"  baseline  : {bd['baseline_m']:.1f} m")
    print(f"  corrected : {bd['corrected_m']:.1f} m")
    print(f"  final TOD : {bd['final_m']:.1f} m")
    print()

    print("=== pressure_altitude_ft ===")
    for elev, qnh in [(0, 1013.25), (1500, 993.25), (2000, 1030.0)]:
        pa = pressure_altitude_ft(elev, qnh)
        print(f"  elev={elev:5.0f} ft  QNH={qnh:.2f} hPa  →  PA={pa:.0f} ft")
    print()

    print("=== tod_from_elevation ===")
    d = chart.tod_from_elevation(1321, 1013.0, 22, 1070, wind_kts=10)
    print(f"  elev=1321 ft / QNH=1013 / 22°C / 1070 kg / 10kts HW  →  {d:.0f} m")
    print()

    print("=== Validation ===")
    for kwargs, label in [
        (dict(pressure_altitude_ft=10001, oat_c=15, mass_kg=1000, wind_kts=0),  "PA too high"),
        (dict(pressure_altitude_ft=2000,  oat_c=55, mass_kg=1000, wind_kts=0),  "OAT too high"),
        (dict(pressure_altitude_ft=2000,  oat_c=15, mass_kg=1200, wind_kts=0),  "mass too high"),
        (dict(pressure_altitude_ft=2000,  oat_c=15, mass_kg=1000, wind_kts=21), "headwind too high"),
        (dict(pressure_altitude_ft=2000,  oat_c=15, mass_kg=1000, wind_kts=-11),"tailwind too high"),
    ]:
        try:
            chart.tod_m(**kwargs)
            print(f"  {label}: no error (unexpected)")
        except OutOfRangeError as e:
            print(f"  {label}: OutOfRangeError ✓  ({e})")

    print()
    print("=== Performance table ===")
    print(f"{'Bedingung':<45} {'TOD [m]':>8}")
    print("-" * 55)
    cases = [
        (0,    15, 1150,   0, "MSL / ISA / MTOW / calm"),
        (4000, 22, 1150,   0, "4000 ft / 22°C / MTOW / calm"),
        (4000, 22, 1070,  10, "4000 ft / 22°C / 1070 kg / 10 kts HW  ← AFM"),
        (4000, 22, 1070,  -5, "4000 ft / 22°C / 1070 kg /  5 kts TW"),
        (8000, 35,  900,   0, "8000 ft / 35°C /  900 kg / calm"),
        (9000, 40, 1150,   0, "9000 ft / 40°C / MTOW   / calm"),
    ]
    for pa, oat, mass, wind, label in cases:
        d = chart.tod_m(pa, oat, mass, wind)
        print(f"  {label:<43} {d:>8.0f}")
