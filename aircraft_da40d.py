"""
aircraft_da40d -- DA40D aircraft adapter for host applications
===============================================================

Provides a concrete DA40D performance model that can be plugged into a
host application's generic aircraft interface.
"""

from __future__ import annotations

import logging
import os
from bisect import bisect_left as _bisect_left

log = logging.getLogger(__name__)

__all__ = [
    "DA40DPerformance",
    "_isa_temp_c",
    "_tas_from_chart",
    "_fuel_gph_da40d",
    "_roc_formula",
]

_BASE_TAS_ISA_STD = {
    60:  [(0, 108.5), (4000, 110.0), (8000, 112.5), (12000, 115.0), (16000, 117.5)],
    70:  [(0, 118.0), (4000, 120.0), (8000, 122.5), (12000, 125.0), (16000, 127.5)],
    80:  [(0, 124.5), (4000, 127.0), (8000, 130.5), (12000, 134.0), (16000, 137.0)],
    90:  [(0, 131.0), (4000, 133.0), (6000, 136.0), (8000, 134.5), (10000, 132.0), (14000, 131.5), (16000, 131.5)],
    100: [(0, 136.0), (4000, 139.0), (6000, 141.0), (8000, 139.5), (10000, 135.5), (14000, 134.0), (16000, 133.5)],
}
_TEMP_COEFF_KT_PER_C = -0.15
_JETA1_DENSITY_KG_L = 0.80


def _isa_temp_c(pressure_altitude_ft):
    return 15.0 - 1.98 * (pressure_altitude_ft / 1000.0)


def _interp_curve(points, x):
    xs = [p[0] for p in points]
    if x <= xs[0]:
        return points[0][1]
    if x >= xs[-1]:
        return points[-1][1]
    i = _bisect_left(xs, x)
    x0, y0 = points[i - 1]
    x1, y1 = points[i]
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def _tas_isa_std(pressure_altitude_ft, load_pct):
    loads = sorted(_BASE_TAS_ISA_STD.keys())
    if load_pct <= loads[0]:
        return _interp_curve(_BASE_TAS_ISA_STD[loads[0]], pressure_altitude_ft)
    if load_pct >= loads[-1]:
        return _interp_curve(_BASE_TAS_ISA_STD[loads[-1]], pressure_altitude_ft)
    hi_idx = _bisect_left(loads, load_pct)
    lo_load, hi_load = loads[hi_idx - 1], loads[hi_idx]
    tas_lo = _interp_curve(_BASE_TAS_ISA_STD[lo_load], pressure_altitude_ft)
    tas_hi = _interp_curve(_BASE_TAS_ISA_STD[hi_load], pressure_altitude_ft)
    return tas_lo + (tas_hi - tas_lo) * (load_pct - lo_load) / (hi_load - lo_load)


def _tas_from_chart(pressure_altitude_ft, oat_c, load_pct):
    base = _tas_isa_std(pressure_altitude_ft, load_pct)
    delta_isa = oat_c - _isa_temp_c(pressure_altitude_ft)
    return base + _TEMP_COEFF_KT_PER_C * delta_isa


def _fuel_gph_da40d(power_pct, is_climb=False):
    if is_climb:
        return 7.7
    p = max(60.0, min(100.0, power_pct))
    return 4.0 + (7.7 - 4.0) / (100.0 - 60.0) * (p - 60.0)


def _roc_formula(h_ft, temp_2m_c, terrain_elev_ft, mass_kg, roc_sl_isa=740.0):
    t_isa_surface = 15.0 - 0.001981 * terrain_elev_ft
    isa_dev = temp_2m_c - t_isa_surface
    return max(
        0.0,
        roc_sl_isa
        * (1.0 - 0.00007 * (h_ft - 4000.0))
        * (1.0 - 0.01 * isa_dev)
        * (975.0 / mass_kg)
    )


class DA40DPerformance:
    """DA40D adapter that satisfies the host aircraft interface by convention."""

    def __init__(self, power_pct: float = 65.0, data_dir: str = "") -> None:
        if not (60.0 <= power_pct <= 100.0):
            raise ValueError(f"power_pct={power_pct} outside valid range 60 … 100")
        self._power_pct = power_pct
        self._data_dir = data_dir or os.path.dirname(os.path.abspath(__file__))
        self._chart = None
        self._chart_err = False
        self._ldg_chart = None
        self._ldg_chart_err = False

    @property
    def name(self) -> str:
        return "DA40D"

    @property
    def vy_kt(self) -> float:
        return 73.0

    @property
    def supports_fuel(self) -> bool:
        return True

    @property
    def tracks_fuel(self) -> bool:
        return True

    @property
    def supports_takeoff_landing(self) -> bool:
        return True

    @property
    def fuel_density_kg_l(self) -> float:
        return _JETA1_DENSITY_KG_L

    @property
    def mtow_kg(self) -> float:
        return 1150.0

    @property
    def display_label(self) -> str:
        return f"Power {self._power_pct:.0f}% · DA40D"

    def cruise_tas_kt(self, alt_ft: float, oat_c: float) -> float:
        return max(60.0, _tas_from_chart(alt_ft, oat_c, self._power_pct))

    def climb_tas_kt(self, alt_ft: float, oat_c: float) -> float:
        return 73.0 * (1.0 + 0.018 * alt_ft / 1000.0)

    def fuel_flow_gph(self, is_climb: bool) -> float:
        return _fuel_gph_da40d(self._power_pct, is_climb)

    def roc_ft_min(self, alt_ft: float, oat_c: float, terrain_elev_ft: float, mass_kg: float) -> float:
        return _roc_formula(alt_ft, oat_c, terrain_elev_ft, mass_kg, roc_sl_isa=740.0)

    def _get_chart(self):
        if self._chart is not None or self._chart_err:
            return self._chart
        try:
            from da40d_takeoff import DA40D_TakeoffChart

            self._chart = DA40D_TakeoffChart.from_wpd_files(
                os.path.join(self._data_dir, "wpd_project.json"),
                os.path.join(self._data_dir, "wpd_project_middle.json"),
                os.path.join(self._data_dir, "wpd_project_right.json"),
            )
            log.info("DA40D Takeoff-Chart geladen.")
        except Exception as exc:
            log.warning("DA40D Takeoff-Chart konnte nicht geladen werden: %s", exc)
            self._chart_err = True
        return self._chart

    def tod_m(self, elev_ft: float, qnh_hpa: float, oat_c: float, mass_kg: float, headwind_kt: float) -> float:
        chart = self._get_chart()
        if chart is None:
            return 0.0
        try:
            return chart.tod_from_elevation(
                elevation_ft=elev_ft,
                qnh_hpa=qnh_hpa,
                oat_c=oat_c,
                mass_kg=max(750.0, min(1150.0, mass_kg)),
                wind_kts=max(-10.0, min(20.0, headwind_kt)),
            )
        except Exception:
            return 0.0

    def _get_landing_chart(self):
        if self._ldg_chart is not None or self._ldg_chart_err:
            return self._ldg_chart
        try:
            from da40d_landing import DA40D_LandingChart

            self._ldg_chart = DA40D_LandingChart.from_wpd_files(
                os.path.join(self._data_dir, "landing_da40d_left.json"),
                os.path.join(self._data_dir, "landing_da40d_middle.json"),
                os.path.join(self._data_dir, "landing_da40d_right.json"),
            )
            log.info("DA40D Landing-Chart geladen.")
        except Exception as exc:
            log.warning("DA40D Landing-Chart konnte nicht geladen werden: %s", exc)
            self._ldg_chart_err = True
        return self._ldg_chart

    def ldg_m(self, elev_ft: float, qnh_hpa: float, oat_c: float, mass_kg: float, headwind_kt: float) -> float:
        chart = self._get_landing_chart()
        if chart is None:
            return 0.0
        try:
            if headwind_kt < 0:
                # AFM chart has no tailwind axis. Use 0-kt chart value with a conservative
                # fallback penalty: +10% distance per 2 kt tailwind.
                base = chart.ldg_from_elevation(
                    elevation_ft=elev_ft,
                    qnh_hpa=qnh_hpa,
                    oat_c=oat_c,
                    mass_kg=max(750.0, min(1150.0, mass_kg)),
                    wind_kts=0.0,
                )
                return base * (1.0 + 0.10 * (-headwind_kt) / 2.0)
            return chart.ldg_from_elevation(
                elevation_ft=elev_ft,
                qnh_hpa=qnh_hpa,
                oat_c=oat_c,
                mass_kg=max(750.0, min(1150.0, mass_kg)),
                wind_kts=min(20.0, headwind_kt),
            )
        except Exception:
            return 0.0
