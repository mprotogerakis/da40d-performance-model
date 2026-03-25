# da40d-performance-model

Python models for the **DA40-D takeoff and landing distance over a 50 ft obstacle**
charts (AFM Section 5, altimeter setting 1013.25 hPa).

The three-panel nomograms have been digitised with
[WebPlotDigitizer](https://automeris.io/WebPlotDigitizer/) and wrapped in a
clean Python API with input validation and inline documentation.

> **⚠ DISCLAIMER — READ BEFORE USE**
>
> This software is provided for **educational and informational purposes only**.
> It is **not approved for operational flight planning** and must **not** be used
> as a substitute for the official Diamond DA40-D Aircraft Flight Manual (AFM) or
> any other approved performance document.
>
> Performance values are derived from digitised chart data and are subject to
> digitisation errors (typically ±3 %). Always verify results against the
> official AFM. The authors accept **no liability** whatsoever for decisions
> made on the basis of this software.
>
> Diamond Aircraft Industries holds all rights to the original performance data.

---

## Quick start

### Takeoff distance

```python
from da40d_takeoff import DA40D_TakeoffChart, pressure_altitude_ft

chart = DA40D_TakeoffChart.from_wpd_files(
    "wpd_project.json",
    "wpd_project_middle.json",
    "wpd_project_right.json",
)

# AFM example: 4 000 ft PA / 22 °C / 1 070 kg / 10 kts headwind → 600 m
dist = chart.tod_m(
    pressure_altitude_ft=4000,
    oat_c=22,
    mass_kg=1070,
    wind_kts=10,          # positive = headwind, negative = tailwind
)
print(f"TOD over 50 ft: {dist:.0f} m")   # → 619 m  (AFM: 600 m, Δ≈3 %)

# With field elevation + QNH instead of pressure altitude
dist2 = chart.tod_from_elevation(
    elevation_ft=1321,
    qnh_hpa=1013.0,
    oat_c=22,
    mass_kg=1070,
    wind_kts=10,
)
```

### Landing distance

```python
from da40d_landing import DA40D_LandingChart

chart = DA40D_LandingChart.from_wpd_files(
    "landing_da40d_left.json",
    "landing_da40d_middle.json",
    "landing_da40d_right.json",
)

# AFM example: 2 000 ft PA / 15 °C / 1 000 kg / 10 kts headwind → 500 m
dist = chart.ldg_m(
    pressure_altitude_ft=2000,
    oat_c=15,
    mass_kg=1000,
    wind_kts=10,          # headwind only; tailwind not modelled by this chart
)
print(f"LDG over 50 ft: {dist:.0f} m")   # → 527 m  (AFM: 500 m, Δ≈5 %)

# With field elevation + QNH instead of pressure altitude
dist2 = chart.ldg_from_elevation(
    elevation_ft=656,
    qnh_hpa=1013.0,
    oat_c=15,
    mass_kg=1000,
    wind_kts=10,
)
```

## Requirements

Python 3.8+, no external dependencies.

---

## API reference

### `pressure_altitude_ft(elevation_ft, qnh_hpa) → float`

Converts field elevation and QNH to pressure altitude using the standard ISA
approximation (1 hPa ≈ 27 ft).

| Parameter | Type | Range | Description |
|---|---|---|---|
| `elevation_ft` | float | any | Aerodrome elevation above MSL [ft] |
| `qnh_hpa` | float | 900 … 1 100 hPa | QNH altimeter setting |

Raises `OutOfRangeError` if QNH is outside its valid range.

---

### Takeoff — `DA40D_TakeoffChart`

#### `DA40D_TakeoffChart.from_wpd_files(left, middle, right)`

Loads the takeoff chart from three WebPlotDigitizer JSON files. Returns a
`DA40D_TakeoffChart` instance.

#### `chart.tod_m(pressure_altitude_ft, oat_c, mass_kg, wind_kts=0) → float`

Returns the takeoff distance over a 50 ft obstacle in metres.

| Parameter | Type | Valid range | Description |
|---|---|---|---|
| `pressure_altitude_ft` | float | 0 … 10 000 ft | Pressure altitude |
| `oat_c` | float | −35 … +50 °C | Outside air temperature |
| `mass_kg` | float | 750 … 1 150 kg | Takeoff mass |
| `wind_kts` | float | −10 … +20 kts | Wind component (+ headwind / − tailwind) |

#### `chart.tod_breakdown(...) → dict`

Same inputs as `tod_m`. Returns a dict with intermediate panel values:

```python
{
    "baseline_m":  832.9,   # left panel  — distance at MTOW
    "corrected_m": 693.2,   # middle panel — mass-corrected
    "final_m":     618.6,   # right panel  — wind-corrected TOD
}
```

#### `chart.tod_from_elevation(elevation_ft, qnh_hpa, oat_c, mass_kg, wind_kts=0) → float`

Convenience wrapper: calls `pressure_altitude_ft()` internally, then `tod_m()`.

---

### Landing — `DA40D_LandingChart`

#### `DA40D_LandingChart.from_wpd_files(left, middle, right)`

Loads the landing chart from three WebPlotDigitizer JSON files. Returns a
`DA40D_LandingChart` instance.

#### `chart.ldg_m(pressure_altitude_ft, oat_c, mass_kg, wind_kts=0) → float`

Returns the landing distance over a 50 ft obstacle in metres.

| Parameter | Type | Valid range | Description |
|---|---|---|---|
| `pressure_altitude_ft` | float | 0 … 10 000 ft | Pressure altitude |
| `oat_c` | float | −35 … +50 °C | Outside air temperature |
| `mass_kg` | float | 750 … 1 150 kg | Landing mass |
| `wind_kts` | float | 0 … +20 kts | Headwind component (tailwind not modelled) |

#### `chart.ldg_breakdown(...) → dict`

Same inputs as `ldg_m`. Returns a dict with intermediate panel values:

```python
{
    "baseline_m":  803.4,   # left panel  — distance at MTOW
    "corrected_m": 721.5,   # middle panel — mass-corrected
    "final_m":     527.0,   # right panel  — wind-corrected LDG distance
}
```

#### `chart.ldg_from_elevation(elevation_ft, qnh_hpa, oat_c, mass_kg, wind_kts=0) → float`

Convenience wrapper: calls `pressure_altitude_ft()` internally, then `ldg_m()`.

---

### `OutOfRangeError`

Subclass of `ValueError`. Raised by all public functions when an input falls
outside its documented valid range.

---

## How the model works

Both charts share the same three-panel structure, chained left → middle → right:

```
Left panel
  input : pressure altitude [ft], OAT [°C]
  output: baseline distance [m]  (bilinear interpolation over altitude curves)
        ↓
Middle panel
  input : baseline distance [m], mass [kg]
  output: mass-corrected distance [m]  (fan-line interpolation)
        ↓
Right panel
  input : mass-corrected distance [m], wind component [kts]
  output: final distance [m]
```

The takeoff right panel has separate headwind (0 … +20 kts) and tailwind
(0 … −10 kts) fan lines. The landing right panel models headwind only
(0 … +20 kts), consistent with the AFM chart.

Interpolation is piecewise-linear throughout; no polynomial fitting.

## Accuracy

**Takeoff** — AFM example: 4 000 ft / 22 °C / 1 070 kg / 10 kts HW → 600 m

| Panel | Computed | AFM | Error |
|---|---|---|---|
| Left   | 832.9 m | ~840 m | −0.8 % |
| Middle | 693.2 m | ~670 m | +3.4 % |
| Final  | 618.6 m | 600 m  | +3.1 % |

**Landing** — AFM example: 2 000 ft / 15 °C / 1 000 kg / 10 kts HW → 500 m

| Panel | Computed | AFM | Error |
|---|---|---|---|
| Left   | 803.4 m | ~800 m | +0.4 % |
| Middle | 721.5 m | ~700 m | +3.1 % |
| Final  | 527.0 m | 500 m  | +5.4 % |

The errors are consistent with typical WebPlotDigitizer digitisation
accuracy on a scanned chart.

## Files

| File | Description |
|---|---|
| `da40d_takeoff.py` | Takeoff model implementation + public API |
| `da40d_landing.py` | Landing model implementation + public API |
| `wpd_project.json` | WPD export — takeoff left panel (OAT / altitude) |
| `wpd_project_middle.json` | WPD export — takeoff middle panel (mass correction) |
| `wpd_project_right.json` | WPD export — takeoff right panel (wind correction) |
| `landing_da40d_left.json` | WPD export — landing left panel (OAT / altitude) |
| `landing_da40d_middle.json` | WPD export — landing middle panel (mass correction) |
| `landing_da40d_right.json` | WPD export — landing right panel (wind correction) |

## License

Code: MIT License.

The digitised chart data (`wpd_project*.json`, `landing_da40d*.json`) is derived
from the Diamond DA40-D AFM and remains the intellectual property of Diamond
Aircraft Industries GmbH. It is reproduced here solely for non-commercial,
educational purposes under the assumption of fair use. If you represent Diamond
Aircraft Industries and have concerns, please open an issue.
