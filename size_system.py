"""size_system.py - PV/BESS sizing for the Firebreak microgrid.

Sweeps PV (kWp) and battery (kWh nominal) and finds, for each PV size, the smallest battery that
serves the Tier 1 critical load with ZERO unserved energy through the worst observed islanding
windows in the PVGIS 2023 record.

Method:
  - PVGIS timestamps are UTC, shifted to local standard time (AEST = UTC+10, no DST). Partial
    first/last local days are dropped.
  - Sizing windows are the worst observed 1-, 3- and 7-day windows (rolling sum of daily yield).
    3 x worst day is kept only as a stress test.
  - Unserved energy is counted over the WHOLE window, not just the last day.
  - Islanding starts at 17:00 the evening before the window with a full battery and runs to
    09:00 the morning after the window (covers the last night).
  - Selection is an explicit rule (DESIGN_PV_KWP + governing window). Inverter ratings are output.

Inputs:  cluster_load_profiles_15min.csv (from load_profiles.py), PVGIS 2023 CSV (via pvgis.py)
Outputs: sizing_table.csv, design_params.json, sizing_results.png
Run: python size_system.py
"""
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt

from pvgis import load_pvgis_local

BASE = Path(__file__).resolve().parent

# ---------------- Assumptions and design choices ----------------
LOAD_COL = "Tier_1_Critical_kW"     # battery basis = Tier 1 (pump is PV-direct, daytime-schedulable)
ETA_RT = 0.90                       # round-trip efficiency (split as sqrt each way)
DOD = 0.90                          # usable fraction of nominal capacity
SAFETY = 1.15                       # margin applied to the chosen sizes
ISLAND_START_H = 17.0               # islanding begins 17:00 local the evening before the window
ISLAND_END_H = 9.0                  # ...and is judged until 09:00 the morning after the window
WINDOWS = (1, 3, 7)                 # worst observed N-day windows searched
PV_SWEEP = np.arange(0, 61, 5.0)    # kWp
BATT_STEP = 5.0                     # kWh resolution of the result
BATT_MAX = 400.0
DESIGN_PV_KWP = 30.0                # design PV size, 30 kWp before margin
CEC_MIN_INV_RATIO = 0.75            # CEC Guidelines Issue 14 (2022) cl 4.4.1: inverter AC >= 75% of array
MODULE_WP = 440                     # module rating used for the panel count (assumption)
dt = 0.25                           # h (15 min)

# ---------------- Data ----------------
load_day = pd.read_csv(BASE / "cluster_load_profiles_15min.csv")[LOAD_COL].values      # 96 steps
pv = load_pvgis_local()                                                                    # hourly, local
daily = pv.groupby("date")["kWh_per_kWp"].sum()
days = list(daily.index)
pv15 = np.repeat(pv["kWh_per_kWp"].values, 4)       # kW per kWp at 15-min (energy-preserving hold)
load15 = np.tile(load_day, len(days))
assert len(pv15) == len(load15)


def simulate(pv_kwp, batt_kwh, p_pv_per_kwp, p_load, soc0_frac=1.0):
    """Energy-balance battery model. Returns (unserved kWh per step, SoC kWh per step, curtailed kWh per step)."""
    usable = batt_kwh * DOD
    eta = math.sqrt(ETA_RT)
    soc = usable * soc0_frac
    n = len(p_load)
    unserved = np.zeros(n); soc_log = np.zeros(n); curtailed = np.zeros(n)
    gen = p_pv_per_kwp * pv_kwp
    for k in range(n):
        net = gen[k] - p_load[k]
        if net >= 0:
            room = (usable - soc) / eta
            charge = min(net * dt, room)
            soc += charge * eta
            curtailed[k] = net * dt - charge
        else:
            need = -net * dt
            supplied = min(need, soc * eta)
            soc -= supplied / eta
            unserved[k] = need - supplied
        soc_log[k] = soc
    return unserved, soc_log, curtailed


def window_slice(first_day_idx, n_days):
    """15-min index range: 17:00 the day before the window -> 09:00 the day after."""
    i0 = (first_day_idx - 1) * 96 + int(ISLAND_START_H * 4)
    i1 = (first_day_idx + n_days) * 96 + int(ISLAND_END_H * 4)
    i0 = max(i0, 0); i1 = min(i1, len(load15))
    return slice(i0, i1)


def min_battery(pv_kwp, pv_series, load_series):
    """Smallest battery (multiple of BATT_STEP) with zero unserved energy; bisection (monotone)."""
    lo, hi = 0, int(BATT_MAX / BATT_STEP)
    if simulate(pv_kwp, hi * BATT_STEP, pv_series, load_series)[0].sum() > 1e-6:
        return np.nan
    while lo < hi:
        mid = (lo + hi) // 2
        if simulate(pv_kwp, mid * BATT_STEP, pv_series, load_series)[0].sum() <= 1e-6:
            hi = mid
        else:
            lo = mid + 1
    return lo * BATT_STEP


# ---------------- Find worst observed windows ----------------
win_info = {}
for n in WINDOWS:
    roll = daily.rolling(n).sum()
    # only windows with a day before and after inside the record
    valid = roll.iloc[n:-1]
    end_date = valid.idxmin()
    first_idx = days.index(end_date) - n + 1
    win_info[f"worst_{n}d"] = {"first_day": str(days[first_idx]), "last_day": str(end_date),
                               "yield_kWh_per_kWp": round(float(valid.min()), 3), "first_idx": first_idx, "n": n}
worst_day = days[win_info["worst_1d"]["first_idx"]]
print(f"Local-time daily yield: worst day {worst_day} = {daily[worst_day]:.3f} kWh/kWp")
for k, w in win_info.items():
    print(f"  {k}: {w['first_day']} -> {w['last_day']}  ({w['yield_kWh_per_kWp']} kWh/kWp)")

# stress test: 3 x worst day back-to-back (synthetic)
wd = win_info["worst_1d"]["first_idx"]
prev = pv15[(wd - 1) * 96:(wd) * 96]
stress_pv = np.concatenate([prev[int(ISLAND_START_H * 4):], np.tile(pv15[wd * 96:(wd + 1) * 96], 3),
                            pv15[wd * 96: wd * 96 + int(ISLAND_END_H * 4)]])
stress_load = np.concatenate([load_day[int(ISLAND_START_H * 4):], np.tile(load_day, 3), load_day[:int(ISLAND_END_H * 4)]])

# ---------------- Sweep ----------------
rows = []
for p in PV_SWEEP:
    row = {"PV_kWp": p}
    for k, w in win_info.items():
        sl = window_slice(w["first_idx"], w["n"])
        row[f"Min_Battery_kWh_{k}"] = min_battery(p, pv15[sl], load15[sl])
    row["Min_Battery_kWh_stress_3x_worst_day"] = min_battery(p, stress_pv, stress_load)
    rows.append(row)
table = pd.DataFrame(rows)
obs_cols = [f"Min_Battery_kWh_{k}" for k in win_info]
table["Governing_observed_kWh"] = table[obs_cols].max(axis=1)
table.to_csv(BASE / "sizing_table.csv", index=False)
print("\n", table.to_string(index=False))

# ---------------- Design selection (explicit rule) ----------------
sel = table[table.PV_kWp == DESIGN_PV_KWP].iloc[0]
batt_base = float(sel["Governing_observed_kWh"])
gov_window = sel[obs_cols].astype(float).idxmax().replace("Min_Battery_kWh_", "")
pv_inst = math.ceil(DESIGN_PV_KWP * SAFETY)                    # kWp, rounded up
batt_inst = math.ceil(batt_base * SAFETY / 5.0) * 5.0          # kWh, rounded up to 5
n_modules = math.ceil(pv_inst * 1000 / MODULE_WP)
pv_inv_kva = max(30.0, math.ceil(CEC_MIN_INV_RATIO * n_modules * MODULE_WP / 1000 / 5) * 5)
bess_kva = 30.0
autonomy_h = DOD * batt_inst * math.sqrt(ETA_RT) / float(load_day.min())
print(f"\nDesign rule: PV = {DESIGN_PV_KWP:.0f} kWp (decision); battery = governing worst observed window "
      f"({gov_window}) = {batt_base:.0f} kWh; x{SAFETY} margin, rounded up.")
print(f"INSTALLED: PV {n_modules} x {MODULE_WP} W = {n_modules*MODULE_WP/1000:.2f} kWp | BESS {batt_inst:.0f} kWh | "
      f"PV inverter {pv_inv_kva:.0f} kVA | BESS PCS {bess_kva:.0f} kVA | autonomy at Tier 1 base, zero PV: {autonomy_h:.1f} h")

params = {
    "site": {"name": "Nowra NSW", "lat": -34.874, "lon": 150.601, "elevation_m": 23,
             "pvgis_time_basis": "UTC converted to AEST (UTC+10), DST ignored"},
    "worst_windows": {k: {kk: vv for kk, vv in v.items() if kk not in ("first_idx", "n")} for k, v in win_info.items()},
    "design_day": str(worst_day),
    "design_day_yield_kWh_per_kWp": round(float(daily[worst_day]), 3),
    "load_basis": LOAD_COL,
    "assumptions": {"eta_roundtrip": ETA_RT, "depth_of_discharge": DOD, "safety_margin": SAFETY,
                    "island_start_h": ISLAND_START_H, "module_Wp": MODULE_WP},
    "design": {
        "rule": f"PV decision {DESIGN_PV_KWP:.0f} kWp; battery = max over observed worst 1/3/7-day windows; x{SAFETY} margin",
        "PV_kWp": DESIGN_PV_KWP, "Battery_kWh": batt_base, "governing_window": gov_window,
        "PV_kWp_installed": round(n_modules * MODULE_WP / 1000, 2), "n_modules": n_modules,
        "Battery_kWh_installed": batt_inst,
        "PV_inverter_kVA": pv_inv_kva, "BESS_PCS_kVA": bess_kva,
        "autonomy_h_zero_PV": round(autonomy_h, 1),
        "stress_3x_worst_day_kWh_at_design_PV": float(sel["Min_Battery_kWh_stress_3x_worst_day"]),
    },
}
(BASE / "design_params.json").write_text(json.dumps(params, indent=2))
print("Saved: sizing_table.csv, design_params.json")

# ---------------- Plots ----------------
fig, axes = plt.subplots(1, 2, figsize=(13, 5))
for c in obs_cols + ["Min_Battery_kWh_stress_3x_worst_day"]:
    ls = "--" if "stress" in c else "-"
    axes[0].plot(table.PV_kWp, table[c], ls, marker="o", ms=3, label=c.replace("Min_Battery_kWh_", ""))
axes[0].axvline(DESIGN_PV_KWP, color="k", lw=0.8)
axes[0].set_xlabel("PV (kWp)"); axes[0].set_ylabel("Min battery for zero unserved (kWh nominal)")
axes[0].set_title("Battery required vs PV (Tier 1, local-time PVGIS 2023)"); axes[0].legend(); axes[0].grid(alpha=0.3)
w = win_info[f"worst_{gov_window.split('_')[1]}"] if gov_window.startswith("worst") else win_info["worst_7d"]
sl = window_slice(w["first_idx"], w["n"])
_, soc, _ = simulate(pv_inst, batt_inst, pv15[sl], load15[sl])
t = np.arange(len(soc)) * dt
axes[1].plot(t, soc / (batt_inst * DOD) * 100, label="SoC (% of usable)")
ax2 = axes[1].twinx()
ax2.plot(t, pv15[sl] * pv_inst, color="orange", lw=0.8, label="PV (kW)")
ax2.plot(t, load15[sl], color="red", lw=0.8, label="Tier 1 load (kW)")
axes[1].set_xlabel(f"Hours from 17:00, {days[w['first_idx']-1]}"); axes[1].set_ylabel("SoC (%)")
ax2.set_ylabel("kW"); axes[1].set_title(f"Installed {pv_inst} kWp / {batt_inst:.0f} kWh through {gov_window}")
axes[1].legend(loc="upper left"); ax2.legend(loc="upper right")
plt.tight_layout(); plt.savefig(BASE / "sizing_results.png", dpi=200)
print("Saved: sizing_results.png")
if __name__ == "__main__" and matplotlib.get_backend().lower() != "agg":
    plt.show()
