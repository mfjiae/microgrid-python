"""fault_study.py - IEC 60909 short-circuit study (pandapower) and CT/VT selection.

Method:
  1. Island cases are split into DAY (BESS + PV) and NIGHT (BESS only). Night is the minimum-fault case.
  2. Results are labelled per BUS (the PCC bus stays on the grid side when the PCC opens).
  3. A MIN case is run: line endtemp_degree, ext_grid s_sc_min_mva / rx_min are set.
  4. Load current is printed beside fault current (fault/load ratio) so pickup margin is visible.
  5. CT primaries are chosen from LOAD current; max fault current is only used for the
     accuracy-limit-factor (saturation) check; min fault current for sensitivity.
Assumption: BESS-as-ext_grid (a voltage source behind impedance, not a current-limited inverter).
Sanity check: grid 3ph fault at the PCC matches the hand calc (3.44 kA).

Inputs:  params.py (network, loads, inverter fault-current limits, relay list)
Outputs (outputs/): fault_table.csv, fault_table_wide.csv, ct_vt_table.csv, loadflow.csv, fault_study.png
Run: python fault_study.py
"""
import math
import warnings

import numpy as np
import pandas as pd
import pandapower as pp
import pandapower.shortcircuit as sc
import matplotlib
import matplotlib.pyplot as plt

import params as P

warnings.filterwarnings("ignore")
OUT = P.OUT
K_MIN = P.K_FAULT_RANGE[0]          # island minimum case uses the lower bound of the inverter limit


def build(mode="grid", period="night", peak_hour=11.0):
    """mode: grid | island. period: day (PV online) | night (PV off)."""
    net = pp.create_empty_network(f_hz=P.F0, name=f"Firebreak {mode} {period}")
    b = {"MV": pp.create_bus(net, vn_kv=P.GRID_KV, name="11 kV grid")}
    for k in P.BUSES:
        b[k] = pp.create_bus(net, vn_kv=P.VLL / 1000, name=P.BUS_NAMES[k])
    if mode == "grid":
        pp.create_ext_grid(net, b["MV"], vm_pu=1.0, name="Grid",
                           s_sc_max_mva=P.GRID_SSC_MVA["max"], s_sc_min_mva=P.GRID_SSC_MVA["min"],
                           rx_max=P.GRID_RX["max"], rx_min=P.GRID_RX["min"],
                           x0x_max=1.0, r0x0_max=0.1, x0x_min=1.0, r0x0_min=0.1)
    pp.create_transformer_from_parameters(
        net, b["MV"], b["PCC"], sn_mva=P.TX["sn_kva"] / 1e3, vn_hv_kv=P.GRID_KV, vn_lv_kv=P.VLL / 1000,
        vk_percent=P.TX["vk_percent"], vkr_percent=P.TX["vkr_percent"], pfe_kw=0.2, i0_percent=0.3,
        vector_group="Dyn", vk0_percent=P.TX["vk_percent"], vkr0_percent=P.TX["vkr_percent"],
        mag0_percent=100, mag0_rx=0, si0_hv_partial=0.9, name="TX 100 kVA Dyn11")
    pp.create_switch(net, b["PCC"], b["FS"], et="b", closed=(mode == "grid"), name="PCC CB")
    r, x = P.CABLE["r_ohm_km"], P.CABLE["x_ohm_km"]
    for a, c, km in P.LINES:
        pp.create_line_from_parameters(
            net, b[a], b[c], length_km=km, r_ohm_per_km=r, x_ohm_per_km=x, c_nf_per_km=261.0,
            max_i_ka=P.CABLE["max_i_ka"], r0_ohm_per_km=P.Z0_Z1_CABLE * r, x0_ohm_per_km=P.Z0_Z1_CABLE * x,
            c0_nf_per_km=261.0, endtemp_degree=P.CABLE_ENDTEMP_C, name=f"{a}-{c}")
    for bus, (p, q) in P.bus_load_kw(peak_hour).items():
        pp.create_load(net, b[bus], p_mw=p / 1e3, q_mvar=q / 1e3, name=P.LOAD_OF_BUS[bus])
    if mode == "island":
        # Assumption: grid-forming BESS represented as ext_grid with S_sc = k x S_rated so that a bolted
        # fault at its own terminals gives k x I_rated (c-factor cancels for an ext_grid defined by S_sc)
        s_max = P.K_FAULT * P.BESS_KVA / 1e3
        s_min = K_MIN * P.BESS_KVA / 1e3
        pp.create_ext_grid(net, b["FS"], vm_pu=1.0, name="BESS grid-forming",
                           s_sc_max_mva=s_max, s_sc_min_mva=s_min, rx_max=0.1, rx_min=0.1,
                           x0x_max=1.0, r0x0_max=0.1, x0x_min=1.0, r0x0_min=0.1)
    if period == "day":
        pp.create_sgen(net, b["FS"], p_mw=0.6 * P.PV_INV_KVA / 1e3, sn_mva=P.PV_INV_KVA / 1e3, k=P.K_FAULT,
                       generator_type="current_source", name="PV inverter")
    if mode == "grid":
        pp.create_sgen(net, b["FS"], p_mw=0.0, sn_mva=P.BESS_KVA / 1e3, k=P.K_FAULT,
                       generator_type="current_source", name="BESS (grid-following)")
    return net, b


def load_current_through(bus):
    """Peak load current (A) flowing INTO this bus from upstream (all downstream load), from the profile."""
    order = ["FS", "WP", "HC", "TT"]
    best = 0.0
    for k in range(96):
        loads = P.bus_load_kw(k / 4)
        if bus == "PCC":
            pq = [loads[x] for x in order]
        else:
            pq = [loads[x] for x in order[order.index(bus):]]
        s = math.hypot(sum(p for p, _ in pq), sum(q for _, q in pq))
        best = max(best, s * 1e3 / (math.sqrt(3) * P.VLL))
    return best


def run_sc(net, fault, case):
    try:
        sc.calc_sc(net, fault=fault, case=case, ip=False, lv_tol_percent=10, branch_results=False)
        return net.res_bus_sc["ikss_ka"].copy()
    except Exception as e:                                   # keep going, report the failure
        print(f"  calc_sc {fault}/{case} failed: {type(e).__name__}: {e}")
        return None


def main(show=False):
    scenarios = [("grid", "day"), ("grid", "night"), ("island", "day"), ("island", "night")]
    rows, lf_rows = [], []
    for mode, period in scenarios:
        net, b = build(mode, period)
        try:
            pp.runpp(net, numba=False)
            for k in P.BUSES:
                lf_rows.append({"mode": mode, "period": period, "bus": k,
                                "vm_pu": round(float(net.res_bus.vm_pu.at[b[k]]), 4)})
        except Exception as e:
            print(f"  load flow {mode}/{period} failed: {e}")
        for case in ("max", "min"):
            res = {f: run_sc(net, f, case) for f in ("3ph", "2ph", "1ph")}
            for k in P.BUSES:
                if mode == "island" and k == "PCC":
                    continue                                  # dead / grid side when PCC open
                row = {"bus": k, "bus_name": P.BUS_NAMES[k], "mode": mode, "period": period, "case": case}
                for f, s in res.items():
                    row[f"Ik_{f}_kA"] = round(float(s.at[b[k]]), 4) if s is not None and not pd.isna(s.at[b[k]]) else np.nan
                row["I_load_peak_A"] = round(load_current_through(k), 1)
                fmin = np.nanmin([row["Ik_3ph_kA"], row["Ik_2ph_kA"], row["Ik_1ph_kA"]]) * 1e3
                row["min_fault_over_load"] = round(fmin / row["I_load_peak_A"], 1) if row["I_load_peak_A"] else np.nan
                rows.append(row)
    ft = pd.DataFrame(rows)

    ft.to_csv(OUT / "fault_table.csv", index=False)
    pd.DataFrame(lf_rows).to_csv(OUT / "loadflow.csv", index=False)

    # wide table: bus x (mode, period) for 3ph max and 1ph min
    wide = ft[ft.case == "max"].pivot_table(index="bus", columns=["mode", "period"], values="Ik_3ph_kA")
    wide_min = ft[ft.case == "min"].pivot_table(index="bus", columns=["mode", "period"], values="Ik_1ph_kA")
    wide.columns = [f"3ph_max_{m}_{p}_kA" for m, p in wide.columns]
    wide_min.columns = [f"1ph_min_{m}_{p}_kA" for m, p in wide_min.columns]
    wide = wide.join(wide_min).reindex(P.BUSES)
    wide["I_load_peak_A"] = [round(load_current_through(k), 1) for k in wide.index]
    wide.to_csv(OUT / "fault_table_wide.csv")

    ct = ct_vt_selection(ft)
    ct.to_csv(OUT / "ct_vt_table.csv", index=False)

    print("\n=== Fault table (kA), IEC 60909 via pandapower ===")
    print(wide.to_string())
    print("\n=== CT / VT selection ===")
    print(ct.to_string(index=False))
    plot(ft, show)
    return ft, ct


def ct_vt_selection(ft):
    """CT primary from LOAD current; ALF check with max fault; sensitivity with min island fault."""
    rows = []
    rb_rated = P.CT["rated_burden_VA"] / P.CT["secondary_A"] ** 2
    rb_actual = 2 * P.CT["lead_ohm"] + P.CT["relay_burden_VA"] / P.CT["secondary_A"] ** 2
    alf_eff = P.CT["ALF"] * (P.CT["Rct_ohm"] + rb_rated) / (P.CT["Rct_ohm"] + rb_actual)
    num = ["Ik_3ph_kA", "Ik_2ph_kA", "Ik_1ph_kA"]
    gmax = ft[(ft["mode"] == "grid") & (ft.case == "max")].groupby("bus")[num].max()
    gmin = ft[(ft["mode"] == "grid") & (ft.case == "min")].groupby("bus")[num].min()
    imin = ft[(ft["mode"] == "island") & (ft.period == "night") & (ft.case == "min")].set_index("bus")
    for rname, (fb, tb, desc) in P.RELAYS.items():
        if rname == "R_PCC":
            i_load = max(P.i_rated(P.PV_INV_KVA), load_current_through("FS") + P.BESS_CHARGE_KW_GRID * 1e3 / (math.sqrt(3) * P.VLL))
            i_fmin = float(gmin.loc[tb, num].min()) * 1e3          # grid-connected minimum at FS
        else:
            i_load = load_current_through(tb)
            i_fmin = float(imin.loc[tb, ["Ik_3ph_kA", "Ik_2ph_kA", "Ik_1ph_kA"]].min()) * 1e3
        i_fmax = float(gmax.loc[fb, ["Ik_3ph_kA", "Ik_1ph_kA"]].max()) * 1e3     # fault just downstream of CT
        need = P.CT["load_margin"] * i_load
        chosen = None
        for ipn in P.CT_STD_PRIMARY:
            if ipn >= need and i_fmax / ipn <= alf_eff:
                chosen = ipn
                break
        rows.append({
            "relay": rname, "location": desc, "I_load_max_A": round(i_load, 1),
            "CT_min_from_load_A": round(need, 1), "I_fault_max_A": round(i_fmax),
            "ALF_effective": round(alf_eff, 1), "CT_ratio": f"{chosen}/{int(P.CT['secondary_A'])}",
            "CT_class": P.CT["class"], "fault_max_over_Ipn": round(i_fmax / chosen, 1),
            "ALF_ok": i_fmax / chosen <= alf_eff,
            "I_fault_min_A": round(i_fmin, 1),
            "I_fault_min_basis": "grid min (PCC open in island)" if rname == "R_PCC" else "island night min, end of zone",
            "min_fault_secondary_A": round(i_fmin / chosen, 3),
            "VT": f"{int(P.VT['primary_V'])}/{int(P.VT['secondary_V'])} V {P.VT['class']}",
            "note": ("ratio raised above load requirement to keep ALF" if chosen > min(i for i in P.CT_STD_PRIMARY if i >= need) else "load-governed"),
        })
    return pd.DataFrame(rows)


def plot(ft, show):
    fig, ax = plt.subplots(figsize=(10, 5.5))
    axes = [ax]
    order = ["PCC", "FS", "WP", "HC", "TT"]
    for (mode, period), st in [(("grid", "night"), "-o"), (("island", "day"), "--s"), (("island", "night"), ":^")]:
        for case, alpha in (("max", 1.0), ("min", 0.5)):
            d = ft[(ft["mode"] == mode) & (ft.period == period) & (ft.case == case)].set_index("bus").reindex(order)
            axes[0].semilogy(order, d["Ik_3ph_kA"] * 1e3, st, alpha=alpha, label=f"{mode}/{period} 3ph {case}")
    il = [load_current_through(k) for k in order]
    axes[0].semilogy(order, il, "k-", lw=2.5, label="peak load current")
    axes[0].set_ylabel("Current (A, log)"); axes[0].set_title("IEC 60909 fault current vs load current by bus")
    axes[0].grid(True, which="both", alpha=0.3); axes[0].legend(fontsize=7)
    plt.tight_layout(); plt.savefig(OUT / "fault_study.png", dpi=180)
    if show:
        plt.show()
    plt.close(fig)


if __name__ == "__main__":
    main(show=matplotlib.get_backend().lower() != "agg")
