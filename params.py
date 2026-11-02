"""params.py - single source of truth for the Firebreak microgrid parameters.

Every other script imports from here. Running it exports params.json and params.m so that the
Simulink model uses the same numbers.

Inputs:  design_params.json (from size_system.py), load_summary.json and cluster_load_profiles_15min.csv
         (from load_profiles.py), equipment.csv
Outputs (outputs/): params.json, params.m
Run: python params.py
"""
import json
import math
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
OUT = BASE / "outputs"
OUT.mkdir(exist_ok=True)

_design = json.loads((BASE / "design_params.json").read_text())
_loads = json.loads((BASE / "load_summary.json").read_text())
LOAD_PROFILE = pd.read_csv(BASE / "cluster_load_profiles_15min.csv")   # 96 x 15 min, kW

# ----------------------------------------------------------------------------- system
F0 = 50.0                     # Hz
VLL = 400.0                   # V, LV feeder line-line
VLN = VLL / math.sqrt(3)      # 230.9 V
S_BASE = 100e3                # VA, per-unit base for the microgrid

# ----------------------------------------------------------------------------- sizing
D = _design["design"]
PV_KWP = D["PV_kWp_installed"]
BESS_KWH = D["Battery_kWh_installed"]
PV_INV_KVA = D["PV_inverter_kVA"]
BESS_KVA = D["BESS_PCS_kVA"]
ETA_RT = _design["assumptions"]["eta_roundtrip"]
DOD = _design["assumptions"]["depth_of_discharge"]
DESIGN_DAY = _design["design_day"]

# ----------------------------------------------------------------------------- inverters
K_FAULT = 1.2                 # inverter fault current limit, pu of rated (IBR range 1.1-1.5 pu)
K_FAULT_RANGE = (1.1, 1.5)    # sensitivity
BESS_Z_PU = (0.005, 0.03)     # R, X of GFM output impedance (voltage-controlled) on BESS base
BESS_CHARGE_KW_GRID = 10.0    # grid-connected charging power to restore SoC
SOC_MIN = 0.0                 # SoC is expressed as fraction of USABLE energy (DoD already applied)

# ----------------------------------------------------------------------------- network
GRID_KV = 11.0
GRID_SSC_MVA = {"max": 50.0, "min": 20.0}       # 11 kV source fault level, rural (confirm with DNSP)
GRID_RX = {"max": 0.1, "min": 0.1}
TX = dict(sn_kva=100.0, vk_percent=4.0, vkr_percent=1.5, vector_group="Dyn11")   # pole-top
CABLE = dict(std_type="NAYY 4x150 SE", r_ohm_km=0.208, x_ohm_km=0.080, max_i_ka=0.270)  # pandapower std type
Z0_Z1_CABLE = 4.0             # Z0 ~ 4 x Z1 for 4-core LV cable with full-size neutral
CABLE_ENDTEMP_C = 80.0        # IEC 60909 min-case conductor end temperature

# Buses, in feeder order. BESS + PV sit at the fire-station main switchboard (MSB).
BUSES = ["PCC", "FS", "WP", "HC", "TT"]
BUS_NAMES = {"PCC": "PCC 400 V (TX LV)", "FS": "Fire station MSB", "WP": "Water pump",
             "HC": "Health clinic", "TT": "Telecom tower"}
LOAD_OF_BUS = {"FS": "Fire_Station", "WP": "Water_Pump", "HC": "Health_Clinic", "TT": "Telecom_Tower"}
PF_OF_LOAD = {"Fire_Station": 0.95, "Water_Pump": 0.85, "Health_Clinic": 0.95, "Telecom_Tower": 0.98}
LINES = [("FS", "WP", 0.3), ("WP", "HC", 0.4), ("HC", "TT", 0.5)]    # km

# Relays: name -> (from bus, to bus, description). Relay sits at the 'from' end.
RELAYS = {"R_PCC": ("PCC", "FS", "PCC interface breaker"),
          "R1": ("FS", "WP", "Feeder head FS->WP"),
          "R2": ("WP", "HC", "WP->HC"),
          "R3": ("HC", "TT", "HC->TT")}
CB_OPEN_S = 0.06              # breaker opening time (3 cycles)
CB_CLOSE_S = 0.08             # breaker closing time

# ----------------------------------------------------------------------------- standards
# AS/NZS 4777.2:2020, Region Australia A (mainland NEM default; confirm with Endeavour Energy)
AS4777 = {
    # Table 4.1 (limit V, trip delay s, max disconnect s)
    "V<<": (70.0, 1.0, 2.0), "V<": (180.0, 10.0, 11.0), "V>": (265.0, 1.0, 2.0), "V>>": (275.0, 0.0, 0.2),
    # Table 4.2 Australia A
    "F<": (47.0, 1.0, 2.0), "F>": (52.0, 0.0, 0.2),
    # Table 4.4 Australia A continuous operation range
    "f_LLCO": 49.75, "f_ULCO": 50.25,
    # Table 4.5 Australia A: f_Pmax 48, f_stop-ch 49, f_transition 50.75, f_Pmin 52
    "f_Pmin": 52.0, "f_transition": 50.75, "f_stop_ch": 49.0, "f_Pmax": 48.0,
    # Cl 4.3 active anti-islanding: disconnect within 2 s of grid disruption
    "active_AI_max_s": 2.0,
    # Cl 4.5.4.1 / Table 4.8: cease generation outside 180-260 V within 0.2 s; restore within 0.4 s
    "V_cont": (180.0, 260.0), "cease_s": 0.2, "restore_s": 0.4,
    # Cl 4.5.6 ROCOF withstand +/-4 Hz/s for 0.25 s
    "rocof_withstand": 4.0,
    # Cl 4.7 reconnection: V in AS 60038 utilisation limits and f in 47.5-50.15 Hz for >= 60 s
    "reconnect_f": (47.5, 50.15), "reconnect_hold_s": 60.0,
    # AS 60038 utilisation limits 205-253 V: inferred from AS/NZS 4777.2 App. I notes
    # ("203 V is 2 V below the lowest utilization voltage"; "255 V is 2 V above the highest")
    "reconnect_V": (205.0, 253.0),
    # Cl 3.3.4.2 default power rate limit WGra = 16.67 % of rated per minute
    "WGra_pct_per_min": 16.67,
}
# Steady-state supply envelope used for the voltage-limit checks: AS 60038 230 V +10 % / -6 %
V_SUPPLY_BAND = (216.2, 253.0)

# Loss-of-mains (LoM) interface relay at the PCC
LOM = {
    "uv_fast": (0.80, 0.15),   # 27 fast: V < 0.80 pu for 0.15 s -> island (critical-load priority; allowed for
                               # multiple-mode inverters per 4777.2 text under Table 4.2)
    "rocof": (1.0, 0.5),       # 81R: |df/dt| > 1.0 Hz/s for 0.5 s (typical LoM setting)
    "afd_hz_per_s": 0.8,       # active frequency drift rate when no grid reference (active AI method, Cl 4.3)
}
# Resynchronisation (sync-check, ANSI 25) windows (tighter than the IEEE 1547-2018 Table 5
# limits for <=500 kVA: 0.3 Hz / 10 % / 20 deg)
SYNC = {"dV_pu": 0.05, "df_hz": 0.10, "dtheta_deg": 10.0, "hold_s": 0.10, "timeout_s": 180.0}

# Islanded-operation control
CTRL = {
    "droop_f_hz_per_pu": 0.5,      # P-f droop: 0.5 Hz at rated power (1 %)
    "droop_v_pu_per_pu": 0.04,     # Q-V droop
    "tau_p_s": 0.5,                # power-measurement filter / virtual inertia
    "ki_f": 0.15,                  # secondary frequency restoration gain (1/s)
    "ki_v": 0.5,
    "transfer_s": 0.10,            # BESS GFL -> GFM transfer after PCC opens
    "tier2_shed_soc": 0.50, "tier2_restore_soc": 0.60,   # island Tier 2 shedding on SoC (usable)
    "pump_window_h": (10.0, 14.0),                      # pump is PV-direct / daytime-schedulable
    "soc_high_curtail": 0.98,      # BESS raises island frequency to curtail PV via 4777.2 f-W
}

# Protection: CTI 0.25 s for static relays without field calibration (IEEE 242-2001 cl 15.5 Table 15-1)
CTI_S = 0.25
IEC_CURVES = {"SI": (0.14, 0.02), "VI": (13.5, 1.0), "EI": (80.0, 2.0), "LTI": (120.0, 1.0)}  # IEC 60255-151
CT_STD_PRIMARY = [5, 10, 15, 20, 25, 30, 40, 50, 60, 75, 100, 150, 200, 250, 300, 400, 500, 600, 800]  # A
CT = {"secondary_A": 1.0, "class": "5P20", "ALF": 20, "Rct_ohm": 0.5, "rated_burden_VA": 2.5,
      "lead_ohm": 0.2, "relay_burden_VA": 0.1, "load_margin": 1.25}  # typical LV CT data
VT = {"primary_V": 400.0, "secondary_V": 110.0, "class": "0.5/3P"}


# ----------------------------------------------------------------------------- helpers
def bus_load_kw(hour, tier2_on=True, pump_on=True):
    """Return {bus: (P_kW, Q_kvar)} at a clock hour (0-24) from the 15-min profile."""
    k = int((hour % 24) * 4) % 96
    row = LOAD_PROFILE.iloc[k]
    out = {}
    # Tier split per building is not stored per bus; Tier 1 per building comes from equipment.csv
    for bus, b in LOAD_OF_BUS.items():
        p_tot = float(row[b])
        p_t1 = TIER1_KW[b]
        p_t2 = max(p_tot - p_t1, 0.0)
        if b == "Water_Pump":
            p = p_t1 + (p_t2 if pump_on else 0.0)
        else:
            p = p_t1 + (p_t2 if tier2_on else 0.0)
        pf = PF_OF_LOAD[b]
        out[bus] = (p, p * math.tan(math.acos(pf)))
    return out


def _tier1_by_building():
    eq = pd.read_csv(BASE / "equipment.csv")
    eq.columns = eq.columns.str.strip()
    t1 = eq[eq["Critical_Tier"].str.strip().str.lower() == "tier_1"]
    s = (t1["Rated_Power_W"] * t1["Quantity"] * t1["Duty_Cycle"]).groupby(t1["Building"].str.strip()).sum() / 1000
    return {b: float(s.get(b, 0.0)) for b in LOAD_OF_BUS.values()}


TIER1_KW = _tier1_by_building()


def i_rated(kva):
    return kva * 1e3 / (math.sqrt(3) * VLL)


def as_dict():
    return {
        "F0": F0, "VLL": VLL, "S_BASE": S_BASE,
        "PV_KWP": PV_KWP, "BESS_KWH": BESS_KWH, "PV_INV_KVA": PV_INV_KVA, "BESS_KVA": BESS_KVA,
        "ETA_RT": ETA_RT, "DOD": DOD, "DESIGN_DAY": DESIGN_DAY, "K_FAULT": K_FAULT,
        "BESS_CHARGE_KW_GRID": BESS_CHARGE_KW_GRID, "GRID_SSC_MVA": GRID_SSC_MVA, "TX": TX, "CABLE": CABLE,
        "LINES": LINES, "TIER1_KW": TIER1_KW, "PF_OF_LOAD": PF_OF_LOAD, "AS4777": AS4777,
        "LOM": LOM, "SYNC": SYNC, "CTRL": CTRL, "CTI_S": CTI_S, "CB_OPEN_S": CB_OPEN_S,
        "load_summary": _loads, "sizing": D,
    }


def export():
    d = as_dict()
    (OUT / "params.json").write_text(json.dumps(d, indent=2, default=str))
    m = ["% params.m  exported by params.py - edit params.py and re-run",
         "% Inputs: params.py. Outputs: the variables below (system, sizing, Tier 1 load, AS/NZS 4777.2 limits, sync and droop settings).",
         f"F0 = {F0}; VLL = {VLL}; S_BASE = {S_BASE};",
         f"PV_KWP = {PV_KWP}; BESS_KWH = {BESS_KWH}; PV_INV_KVA = {PV_INV_KVA}; BESS_KVA = {BESS_KVA};",
         f"ETA_RT = {ETA_RT}; DOD = {DOD}; K_FAULT = {K_FAULT}; BESS_CHARGE_KW_GRID = {BESS_CHARGE_KW_GRID};",
         f"TIER1_KW = struct({', '.join(f'{chr(39)}{k}{chr(39)}, {v}' for k, v in TIER1_KW.items())});",
         f"TIER1_TOTAL_KW = {sum(TIER1_KW.values()):.3f};",
         f"UV2 = {AS4777['V<<']}; UV1 = {AS4777['V<']}; OV1 = {AS4777['V>']}; OV2 = {AS4777['V>>']};  % [V, delay s, max s] AS/NZS 4777.2 T4.1",
         f"UF = {AS4777['F<']}; OF = {AS4777['F>']};  % Australia A, T4.2",
         f"RECONNECT_HOLD_S = {AS4777['reconnect_hold_s']}; RECONNECT_F = [{AS4777['reconnect_f'][0]} {AS4777['reconnect_f'][1]}];",
         f"SYNC_DV = {SYNC['dV_pu']}; SYNC_DF = {SYNC['df_hz']}; SYNC_DTHETA = {SYNC['dtheta_deg']};",
         f"DROOP_F = {CTRL['droop_f_hz_per_pu']}; DROOP_V = {CTRL['droop_v_pu_per_pu']};",
         "LOAD = readmatrix('cluster_load_profiles_15min.csv');  % 96 x 15-min, kW"]
    (OUT / "params.m").write_text("\n".join(m) + "\n")
    return d


if __name__ == "__main__":
    export()
    print(f"PV {PV_KWP} kWp ({PV_INV_KVA} kVA inverter) | BESS {BESS_KWH} kWh ({BESS_KVA} kVA PCS)")
    print(f"Tier 1 per building (kW): {TIER1_KW}  total {sum(TIER1_KW.values()):.2f} kW")
    print(f"BESS rated current {i_rated(BESS_KVA):.1f} A -> fault limit {K_FAULT*i_rated(BESS_KVA):.1f} A")
    print("Saved outputs/params.json, outputs/params.m")
