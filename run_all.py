"""run_all.py - runs the full analysis pipeline.

Runs the five steps in order, then prints a one-screen summary of the key results.

Inputs:  none directly (each step reads its own files; see the step headers)
Outputs: all step outputs, plus a printed results summary
Usage:
    python run_all.py          # run everything, plots saved to PNG (no pop-up windows)
    python run_all.py --open   # same, then open the four result plots (Windows)
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
OUT = BASE / "outputs"
STEPS = [
    ("load_profiles.py", "Load profiles (equipment.csv -> 15-min cluster profile)"),
    ("size_system.py", "PV/BESS sizing (worst observed 1/3/7-day windows, PVGIS 2023)"),
    ("params.py", "Shared parameters -> outputs/params.json + params.m for the Simulink model"),
    ("fault_study.py", "IEC 60909 fault study (pandapower) + CT/VT ratios"),
    ("check_energy_balance.py", "Year-round islanding readiness check"),
]
PLOTS = [BASE / "cluster_stacked_load_profile.png", BASE / "sizing_results.png",
         OUT / "fault_study.png", OUT / "energy_balance.png"]


def run_steps():
    env = dict(os.environ, MPLBACKEND="Agg")      # save plots, don't block on windows
    OUT.mkdir(exist_ok=True)
    for i, (script, desc) in enumerate(STEPS, 1):
        print(f"\n[{i}/{len(STEPS)}] {script}: {desc}")
        t0 = time.time()
        r = subprocess.run([sys.executable, str(BASE / script)], cwd=BASE, env=env,
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout[-2000:])
            print(r.stderr[-3000:])
            sys.exit(f"\nSTOPPED: {script} failed (exit {r.returncode}). Error shown above.")
        print(f"      OK ({time.time() - t0:.1f} s)")


def summary():
    ld = json.loads((BASE / "load_summary.json").read_text())
    d = json.loads((BASE / "design_params.json").read_text())["design"]
    eb = json.loads((OUT / "energy_balance_summary.json").read_text())
    ft = pd.read_csv(OUT / "fault_table_wide.csv", index_col="bus")
    ct = pd.read_csv(OUT / "ct_vt_table.csv")

    print("\n" + "=" * 64 + "\nRESULTS SUMMARY\n" + "=" * 64)
    print(f"Load:   cluster {ld['cluster_daily_kWh']:.1f} kWh/day, Tier 1 {ld['tier1_daily_kWh']:.1f} kWh/day, "
          f"peak {ld['peak_after_diversity_kW']:.1f} kW (after diversity)")
    print(f"Sizing: design {d['PV_kWp']:.0f} kWp / {d['Battery_kWh']:.0f} kWh "
          f"(governing window: {d['governing_window']})")
    print(f"        installed x1.15 -> {d['PV_kWp_installed']} kWp ({d['n_modules']} modules) / "
          f"{d['Battery_kWh_installed']:.0f} kWh; PV inverter {d['PV_inverter_kVA']:.0f} kVA, "
          f"BESS PCS {d['BESS_PCS_kVA']:.0f} kVA")
    print(f"Faults: grid 3ph at PCC {ft.loc['PCC', '3ph_max_grid_day_kA']:.2f} kA (c_max) | "
          f"island 3ph at FS day {ft.loc['FS', '3ph_max_island_day_kA'] * 1e3:.0f} A, "
          f"night {ft.loc['FS', '3ph_max_island_night_kA'] * 1e3:.0f} A")
    print("CTs:    " + ", ".join(f"{r.relay} {r.CT_ratio}" for r in ct.itertuples()) + f" ({ct.CT_class.iloc[0]})")
    print(f"Energy: {eb['nights'] - eb['nights_with_unserved']}/{eb['nights']} evenings islanded OK, "
          f"worst min SoC {eb['worst_evening_min_soc'] * 100:.1f} % on {eb['worst_evening_date']}; "
          f"year-long island Tier 1 unserved {eb['continuous_island_unserved_kWh']:.0f} kWh")
    print("\nPlots:")
    for p in PLOTS:
        print(f"  {p.relative_to(BASE)}" + ("" if p.exists() else "   (MISSING)"))


if __name__ == "__main__":
    run_steps()
    summary()
    if "--open" in sys.argv and hasattr(os, "startfile"):
        for p in PLOTS:
            if p.exists():
                os.startfile(p)
