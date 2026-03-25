# da40d-performance-model

DA40D-Leistungsmodell als eigenständiges Python-Modul für Host-Anwendungen
(aktuell vfrnav).

Das Repo enthält:

- die digitisierte DA40D Takeoff-/Landing-Chart-Engine (50 ft obstacle)
- einen Host-Adapter (`aircraft_da40d.py`) für ein generisches Aircraft-Interface

## Scope (Stand heute)

### 1) Chart-Modelle

- `da40d_takeoff.py`: TOD aus 3-Panel-Nomogramm
- `da40d_landing.py`: LDG aus 3-Panel-Nomogramm
- Datenbasis: WebPlotDigitizer-Exporte (`wpd_project*.json`, `landing_da40d*.json`)

### 2) Host-Adapter

`aircraft_da40d.py` stellt `DA40DPerformance` bereit mit:

- `cruise_tas_kt(...)`
- `climb_tas_kt(...)`
- `fuel_flow_gph(...)`
- `roc_ft_min(...)`
- `tod_m(...)`
- `ldg_m(...)`
- Metadaten: `mtow_kg=1150`, `fuel_density_kg_l=0.80` (Jet A-1)

Damit kann ein Host zwischen unterschiedlichen Aircraft-Typen über eine
einheitliche Schnittstelle umschalten.

## Nutzung im Host (vfrnav)

Standardmäßig lädt `vfrnav` dieses Repo aus dem Schwesterpfad:

`../da40d-performance_model`

Alternativ kann der Pfad explizit gesetzt werden:

```bash
export VFRNAV_DA40D_MODEL_DIR=/absolute/path/to/da40d-performance_model
```

## Quick Start (direkte Chart-Nutzung)

### Takeoff

```python
from da40d_takeoff import DA40D_TakeoffChart

chart = DA40D_TakeoffChart.from_wpd_files(
    "wpd_project.json",
    "wpd_project_middle.json",
    "wpd_project_right.json",
)

tod = chart.tod_from_elevation(
    elevation_ft=1321,
    qnh_hpa=1013.0,
    oat_c=22,
    mass_kg=1070,
    wind_kts=10,  # +headwind / -tailwind
)
print(round(tod, 1))
```

### Landing

```python
from da40d_landing import DA40D_LandingChart

chart = DA40D_LandingChart.from_wpd_files(
    "landing_da40d_left.json",
    "landing_da40d_middle.json",
    "landing_da40d_right.json",
)

ldg = chart.ldg_from_elevation(
    elevation_ft=656,
    qnh_hpa=1013.0,
    oat_c=15,
    mass_kg=1000,
    wind_kts=10,  # headwind-axis chart
)
print(round(ldg, 1))
```

## Wichtige Verhaltensdetails

- Landing-Chart enthält nur Headwind-Achse.
- Im Adapter wird Tailwind für Landing konservativ approximiert:
  0-kt-Basiswert plus `+10 %` pro `2 kt` Tailwind.
- Interpolation ist durchgehend linear (keine polynomialen Fits).

## Dateien

- `aircraft_da40d.py`: Adapter für Host-Interface
- `da40d_takeoff.py`: Takeoff-Modell
- `da40d_landing.py`: Landing-Modell
- `wpd_project.json`, `wpd_project_middle.json`, `wpd_project_right.json`: Takeoff-Daten
- `landing_da40d_left.json`, `landing_da40d_middle.json`, `landing_da40d_right.json`: Landing-Daten

## Anforderungen

- Python 3.8+
- keine externen Python-Abhängigkeiten

## Sicherheitshinweis

Nur für Analyse/Entwicklung. Kein zugelassenes Flugplanungswerkzeug. Ergebnisse
müssen gegen AFM/POH verifiziert werden.

## Lizenz

Code: MIT.

Die digitalisierten Chart-Daten sind aus DA40D-AFM-Diagrammen abgeleitet; Rechte
an den Originaldaten liegen beim Hersteller.
