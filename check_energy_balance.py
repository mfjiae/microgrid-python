"""check_energy_balance.py - year-round islanding readiness at the installed PV/BESS size.

a) Every evening of 2023: island at 17:00 with a full battery until 09:00 next day -> min SoC.
b) Continuous island for the whole year (worst case: grid never returns) -> Tier 1 unserved energy.
Uses real 2023 PVGIS data in local time.

Inputs:  params.py (installed PV/BESS size, Tier 1 load, efficiency, DoD), pvgis.py (2023 hourly PV)
Outputs (outputs/): energy_balance_year.csv, energy_balance_summary.json, energy_balance.png
Run: python check_energy_balance.py
"""
import json
import math

import numpy as np
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt

import params as P
from pvgis import load_pvgis_local

OUT = P.OUT
E_US = P.BESS_KWH * P.DOD
ETA = math.sqrt(P.ETA_RT)


def year_readiness():
    pv = load_pvgis_local()
    days = sorted(pv.date.unique())
    pv15 = np.repeat(pv.kWh_per_kWp.values, 4) * P.PV_KWP
    pv15 = np.minimum(pv15, P.PV_INV_KVA)
    t1 = np.full(96, sum(P.TIER1_KW.values()))
    load_t1 = np.tile(t1, len(days))
    dt = 0.25

    def run(i0, i1, soc0=1.0):
        soc = soc0 * E_US; mn = soc; uns = 0.0
        for k in range(i0, i1):
            net = (pv15[k] - load_t1[k]) * dt
            if net >= 0:
                soc = min(E_US, soc + net * ETA)
            else:
                sup = min(-net, soc * ETA); soc -= sup / ETA; uns += -net - sup
            mn = min(mn, soc)
        return mn / E_US, uns

    ev = []
    for i in range(len(days) - 2):
        i0 = i * 96 + 68; i1 = (i + 1) * 96 + 36                 # 17:00 -> 09:00 next day
        mn, u = run(i0, i1)
        ev.append({"date": str(days[i]), "min_SoC_overnight": mn, "unserved_kWh": u})
    ev = pd.DataFrame(ev)
    # continuous island all year
    soc = E_US; trace = []; uns = 0.0
    for k in range(len(pv15)):
        net = (pv15[k] - load_t1[k]) * dt
        if net >= 0:
            soc = min(E_US, soc + net * ETA)
        else:
            sup = min(-net, soc * ETA); soc -= sup / ETA; uns += -net - sup
        trace.append(soc / E_US)
    return ev, np.array(trace), uns, days


def main(show=False):
    ev, trace, uns, days = year_readiness()
    ev.to_csv(OUT / "energy_balance_year.csv", index=False)
    e_t1 = sum(P.TIER1_KW.values()) * 24 * len(days)
    print(f"\n=== Year-round readiness, installed {P.PV_KWP} kWp / {P.BESS_KWH} kWh ===")
    print(f"Evening islanding (17:00 -> 09:00, full battery): worst min SoC {ev.min_SoC_overnight.min()*100:.1f} % "
          f"on {ev.loc[ev.min_SoC_overnight.idxmin(), 'date']}; unserved on {int((ev.unserved_kWh > 1e-6).sum())} of {len(ev)} nights")
    print(f"Continuous island ALL of 2023 (grid never returns): Tier 1 unserved {uns:.1f} kWh of {e_t1:.0f} kWh "
          f"({100*uns/e_t1:.2f} %), min SoC {trace.min()*100:.1f} %")
    summary = {"worst_evening_min_soc": float(ev.min_SoC_overnight.min()),
               "worst_evening_date": ev.loc[ev.min_SoC_overnight.idxmin(), "date"],
               "nights_with_unserved": int((ev.unserved_kWh > 1e-6).sum()), "nights": len(ev),
               "continuous_island_unserved_kWh": float(uns), "continuous_island_unserved_pct": float(100 * uns / e_t1),
               "continuous_island_min_soc": float(trace.min())}
    (OUT / "energy_balance_summary.json").write_text(json.dumps(summary, indent=2))

    fig, ax = plt.subplots(2, 1, figsize=(12, 7))
    x = pd.to_datetime(ev.date)
    ax[0].bar(x, ev.min_SoC_overnight * 100, width=1.0, color="tab:blue")
    ax[0].set_ylabel("min SoC overnight (%)"); ax[0].set_ylim(0, 100)
    ax[0].set_title("Islanding readiness: island at 17:00 every evening of 2023 (full battery) -> lowest SoC by 09:00")
    tt = pd.date_range(pd.Timestamp(days[0]), periods=len(trace), freq="15min")
    ax[1].plot(tt, trace * 100, lw=0.6, color="k")
    ax[1].set_ylabel("SoC (%)"); ax[1].set_ylim(0, 105)
    ax[1].set_title(f"Stress test: continuous island for all of 2023, Tier 1 unserved {uns:.0f} kWh ({100*uns/e_t1:.2f} %)")
    plt.tight_layout(); plt.savefig(OUT / "energy_balance.png", dpi=150)
    if show:
        plt.show()
    plt.close(fig)
    return summary


if __name__ == "__main__":
    main(show=matplotlib.get_backend().lower() != "agg")
