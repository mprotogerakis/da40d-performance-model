"""
da40d_landing — DA40-D Landing Distance over 50 ft Obstacle
============================================================
Digitised three-panel AFM chart (Section 5, ALTIMETER SETTING 1013.25 hPa).
Flaps LDG configuration.

Public API
----------
pressure_altitude_ft(elevation_ft, qnh_hpa)
    Convert field elevation + QNH to pressure altitude.
    (Re-exported from da40d_takeoff for convenience.)

DA40D_LandingChart.from_wpd_files(left, middle, right)
    Load chart from three WebPlotDigitizer JSON exports.

chart.ldg_m(pressure_altitude_ft, oat_c, mass_kg, wind_kts) -> float
    Landing distance over 50 ft obstacle in metres.

chart.ldg_breakdown(...)  -> dict
    Same computation, returns intermediate panel values.

chart.ldg_from_elevation(elevation_ft, qnh_hpa, oat_c, mass_kg, wind_kts) -> float
    Convenience wrapper: accepts field elevation + QNH instead of PA.

Wind convention
---------------
  wind_kts > 0  headwind  (reduces distance)  valid 0 … +20 kts
  wind_kts = 0  calm
  Tailwind is not modelled by this chart (AFM only shows headwind
  correction for landing distance).

Input limits (raise OutOfRangeError if exceeded)
-------------------------------------------------
  pressure_altitude_ft  :    0 … 10 000 ft
  elevation_ft          :  −1 000 … 14 000 ft   (only via ldg_from_elevation)
  qnh_hpa               :  900 … 1 100 hPa       (only via ldg_from_elevation)
  oat_c                 :  −35 … +50 °C
  mass_kg               :  750 … 1 150 kg
  wind_kts              :    0 … +20 kts  (headwind only)
"""

from __future__ import annotations

import json
import re
from bisect import bisect_left
from typing import Dict, List, Tuple

from da40d_takeoff import (
    OutOfRangeError,
    pressure_altitude_ft,
    QNH_MIN_HPA,
    QNH_MAX_HPA,
    _lerp,
    _interp_sorted,
    _interp_2d,
    _parse_altitude_ft,
    _build_left,
    _LeftChart,
    _MID_X_LEFT,
    _MID_MASS_LEFT,
    _MID_X_RIGHT,
    _MID_MASS_RIGHT,
    _mid_x_to_mass,
    _build_middle,
    _MiddleChart,
    _RGT_X_ZERO,
    _RGT_X_MAX,
    _RGT_KTS_MAX,
    _rgt_x_to_kts,
)

__all__ = [
    "OutOfRangeError",
    "pressure_altitude_ft",
    "DA40D_LandingChart",
]

# ---------------------------------------------------------------------------
# Input validation limits
# ---------------------------------------------------------------------------

_PA_MIN,   _PA_MAX   =     0.0, 10_000.0   # ft
_OAT_MIN,  _OAT_MAX  =   -35.0,     50.0   # °C
_MASS_MIN, _MASS_MAX =   750.0,  1_150.0   # kg
_WIND_MIN, _WIND_MAX =     0.0,     20.0   # kts  (headwind only)


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
# Right panel  —  wind correction (headwind only)
# ---------------------------------------------------------------------------

def _build_right_landing(wpd: dict) -> list:
    """
    Build fan-line list from the landing right-panel WPD JSON.

    All datasets are treated as headwind lines (the landing chart shows
    only a headwind correction axis, 0 … 20 kts).
    """
    lines: list = []
    for ds in wpd["datasetColl"]:
        raw = [(float(d["value"][0]), float(d["value"][1])) for d in ds["data"]]
        if not raw:
            continue
        pts   = sorted([(_rgt_x_to_kts(x), y) for x, y in raw])
        entry = pts[0][1]   # distance at ~0 kts
        lines.append({"entry": entry, "pts": pts})
    return sorted(lines, key=lambda l: l["entry"])


class _LdgRightChart:
    def __init__(self, lines: list) -> None:
        self._lines = lines

    def query(self, corrected_m: float, wind_kts: float) -> float:
        """wind_kts >= 0 (headwind); 0 = calm, 20 = max headwind."""
        if wind_kts == 0.0:
            return corrected_m
        return _interp_2d(self._lines, corrected_m, wind_kts)


# ---------------------------------------------------------------------------
# Public chart class
# ---------------------------------------------------------------------------


class DA40D_LandingChart:
    """
    DA40-D landing distance over 50 ft obstacle (AFM Section 5).

    Load once, query as often as needed.  All heavy parsing happens in
    :meth:`from_wpd_files`; individual queries are cheap interpolations.

    Notes
    -----
    The underlying chart is valid for:

    * Altimeter setting 1013.25 hPa (pressure altitude)
    * Paved, level, dry runway (no grass/slope correction)
    * Flaps LDG configuration
    * Headwind correction only (0 … 20 kts)
    """

    def __init__(
        self,
        left:   _LeftChart,
        middle: _MiddleChart,
        right:  _LdgRightChart,
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
    ) -> "DA40D_LandingChart":
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
            final landing distance).

        Returns
        -------
        DA40D_LandingChart

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
        right  = _LdgRightChart(_build_right_landing(wpd_r))
        return cls(left, middle, right)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def ldg_m(
        self,
        pressure_altitude_ft: float,
        oat_c:    float,
        mass_kg:  float,
        wind_kts: float = 0.0,
    ) -> float:
        """
        Landing distance over 50 ft obstacle.

        Parameters
        ----------
        pressure_altitude_ft : float
            Pressure altitude [ft].  Range: 0 … 10 000 ft.
            Use :func:`pressure_altitude_ft` to convert from QNH + elevation.
        oat_c : float
            Outside air temperature [°C].  Range: −35 … +50 °C.
        mass_kg : float
            Landing mass [kg].  Range: 750 … 1 150 kg.
        wind_kts : float, optional
            Headwind component along the runway [kts].
            Range: 0 … +20 kts.  Default: 0 (calm).

        Returns
        -------
        float
            Landing distance over 50 ft obstacle [m].

        Raises
        ------
        OutOfRangeError
            If any parameter is outside its valid range.

        Examples
        --------
        >>> chart.ldg_m(2000, 15, 1000, wind_kts=10)   # AFM example → 500 m
        """
        _validate(pressure_altitude_ft, oat_c, mass_kg, wind_kts)
        baseline  = self._left.query(pressure_altitude_ft, oat_c)
        corrected = self._middle.query(baseline, mass_kg)
        return self._right.query(corrected, wind_kts)

    def ldg_breakdown(
        self,
        pressure_altitude_ft: float,
        oat_c:    float,
        mass_kg:  float,
        wind_kts: float = 0.0,
    ) -> dict:
        """
        Same as :meth:`ldg_m` but returns all three intermediate values.

        Returns
        -------
        dict with keys:
            ``baseline_m``   — left panel output (distance at MTOW) [m]
            ``corrected_m``  — middle panel output (mass-corrected) [m]
            ``final_m``      — right panel output (wind-corrected LDG distance) [m]

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

    def ldg_from_elevation(
        self,
        elevation_ft: float,
        qnh_hpa:      float,
        oat_c:        float,
        mass_kg:      float,
        wind_kts:     float = 0.0,
    ) -> float:
        """
        Convenience wrapper: derives pressure altitude from field elevation
        and QNH, then calls :meth:`ldg_m`.

        Parameters
        ----------
        elevation_ft : float
            Aerodrome elevation above MSL [ft].
        qnh_hpa : float
            QNH altimeter setting [hPa].  Range: 900 … 1 100 hPa.
        oat_c : float
            Outside air temperature [°C].  Range: −35 … +50 °C.
        mass_kg : float
            Landing mass [kg].  Range: 750 … 1 150 kg.
        wind_kts : float, optional
            Headwind component [kts].  Range: 0 … +20 kts.

        Returns
        -------
        float
            Landing distance over 50 ft obstacle [m].

        Raises
        ------
        OutOfRangeError
            If QNH is out of range, or if the derived pressure altitude falls
            outside 0 … 10 000 ft, or if any other parameter is out of range.
        """
        pa = pressure_altitude_ft(elevation_ft, qnh_hpa)
        return self.ldg_m(pa, oat_c, mass_kg, wind_kts)


# ---------------------------------------------------------------------------
# CLI smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    chart = DA40D_LandingChart.from_wpd_files(
        "landing_da40d_left.json",
        "landing_da40d_middle.json",
        "landing_da40d_right.json",
    )

    print("=== AFM example  (expected: 500 m / 1640 ft) ===")
    bd = chart.ldg_breakdown(2000, 15, 1000, wind_kts=10)
    print(f"  baseline  : {bd['baseline_m']:.1f} m")
    print(f"  corrected : {bd['corrected_m']:.1f} m")
    print(f"  final LDG : {bd['final_m']:.1f} m")
    print()

    print("=== pressure_altitude_ft ===")
    from da40d_takeoff import pressure_altitude_ft as pa_ft
    for elev, qnh in [(0, 1013.25), (1500, 993.25), (2000, 1030.0)]:
        pa = pa_ft(elev, qnh)
        print(f"  elev={elev:5.0f} ft  QNH={qnh:.2f} hPa  →  PA={pa:.0f} ft")
    print()

    print("=== ldg_from_elevation ===")
    d = chart.ldg_from_elevation(656, 1013.0, 15, 1000, wind_kts=10)
    print(f"  elev=656 ft / QNH=1013 / 15°C / 1000 kg / 10kts HW  →  {d:.0f} m")
    print()

    print("=== Validation ===")
    for kwargs, label in [
        (dict(pressure_altitude_ft=10001, oat_c=15,  mass_kg=1000, wind_kts=0),  "PA too high"),
        (dict(pressure_altitude_ft=2000,  oat_c=55,  mass_kg=1000, wind_kts=0),  "OAT too high"),
        (dict(pressure_altitude_ft=2000,  oat_c=15,  mass_kg=1200, wind_kts=0),  "mass too high"),
        (dict(pressure_altitude_ft=2000,  oat_c=15,  mass_kg=1000, wind_kts=21), "headwind too high"),
        (dict(pressure_altitude_ft=2000,  oat_c=15,  mass_kg=1000, wind_kts=-5), "tailwind (not modelled)"),
    ]:
        try:
            chart.ldg_m(**kwargs)
            print(f"  {label}: no error (unexpected)")
        except OutOfRangeError as e:
            print(f"  {label}: OutOfRangeError ✓  ({e})")

    print()
    print("=== Performance table ===")
    print(f"{'Bedingung':<50} {'LDG [m]':>8}")
    print("-" * 60)
    cases = [
        (0,    15, 1150,   0, "MSL / ISA / MTOW / calm"),
        (2000, 15, 1000,  10, "2000 ft / 15°C / 1000 kg / 10 kts HW  ← AFM"),
        (4000, 22, 1150,   0, "4000 ft / 22°C / MTOW / calm"),
        (4000, 22, 1070,  10, "4000 ft / 22°C / 1070 kg / 10 kts HW"),
        (6000, 30,  900,   0, "6000 ft / 30°C /  900 kg / calm"),
        (8000, 35, 1150,  20, "8000 ft / 35°C / MTOW   / 20 kts HW"),
    ]
    for pa, oat, mass, wind, label in cases:
        d = chart.ldg_m(pa, oat, mass, wind)
        print(f"  {label:<48} {d:>8.0f}")
