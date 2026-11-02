"""load_profiles.py - builds the 15-minute load profile per building and for the whole cluster
(Tier 1 / Tier 2 / Tier 3 split, diversity applied only to the maximum-demand figure).

Inputs:  equipment.csv
Outputs: cluster_load_profiles_15min.csv, load_summary.json, cluster_stacked_load_profile.png
Run: python load_profiles.py
"""

import numpy as np
import pandas as pd
import json
from pathlib import Path
import matplotlib.pyplot as plt

BASE = Path(__file__).resolve().parent

# 1. Read the equipment list
csv_filename = BASE / "equipment.csv"
df_equip = pd.read_csv(csv_filename)

# Strip any leading/trailing whitespace from column headers
df_equip.columns = df_equip.columns.str.strip()

# Case-insensitive column resolver to prevent KeyError
def find_col(possible_names):
    for col in df_equip.columns:
        if col.lower() in [p.lower() for p in possible_names]:
            return col
    raise KeyError(f"None of {possible_names} found in columns: {list(df_equip.columns)}")

col_bldg   = find_col(["Building", "Facility"])
col_name   = find_col(["Equipment_Name", "Equipment_name", "Equipment"])
col_power  = find_col(["Rated_Power_W", "Rated_power_W", "Power_W"])
col_qty    = find_col(["Quantity", "Qty"])
col_start  = find_col(["Start_Hour", "Start_hour"])
col_end    = find_col(["End_Hour", "End_hour"])
col_duty   = find_col(["Duty_Cycle", "Duty_cycle"])
col_tier   = find_col(["Critical_Tier", "Critical_tier", "Tier"])

# 2. Time Discretization (96 steps across 24h at 15-minute resolution)
periods = 96
hours = np.linspace(0, 24, periods, endpoint=False)
dt = 24.0 / periods  # 0.25 h

target_buildings = ["Fire_Station", "Water_Pump", "Health_Clinic", "Telecom_Tower"]

unknown = set(df_equip[col_bldg].str.strip()) - set(target_buildings)
if unknown:
    print(f"WARNING: buildings in CSV not in target list (ignored): {sorted(unknown)}")
known_tiers = {"tier_1", "tier_2", "tier_3"}
bad = set(df_equip[col_tier].astype(str).str.strip().str.lower()) - known_tiers
if bad:
    print(f"WARNING: unrecognised tiers (ignored): {sorted(bad)}")

# Separate Tier 1 and Tier 2 tracking arrays per building
bldg_t1 = {bldg: np.zeros(periods) for bldg in target_buildings}
bldg_t2 = {bldg: np.zeros(periods) for bldg in target_buildings}

# 3. Generate Load Profiles per Asset
for _, row in df_equip.iterrows():
    tier = str(row[col_tier]).strip()
    if tier.lower() == "tier_3":
        continue  # Tier 3 is sheddable upon islanding

    bldg = str(row[col_bldg]).strip()
    p_eff_kw = (float(row[col_power]) * float(row[col_qty]) * float(row[col_duty])) / 1000.0
    start = float(row[col_start])
    end = float(row[col_end])

    # Operational mask for intermittent load
    if start <= end:
        active_mask = (hours >= start) & (hours < end)
    else:
        active_mask = (hours >= start) | (hours < end)

    load_kw = np.where(active_mask, p_eff_kw, 0.0)

    if bldg in target_buildings:
        if tier.lower() == "tier_1":
            bldg_t1[bldg] += load_kw
        elif tier.lower() == "tier_2":
            bldg_t2[bldg] += load_kw

# 4. Cluster Aggregation
# Diversity factors are NOT applied to the energy profile
# (energy must be conservative for battery sizing). Diversity is only used for the separate
# after-diversity maximum-demand figure below (used for cable / CT / inverter checks).
DIVERSITY_T2 = {"Fire_Station": 0.85, "Health_Clinic": 0.90}

df_profiles = pd.DataFrame(index=hours)

for bldg in target_buildings:
    df_profiles[bldg] = bldg_t1[bldg] + bldg_t2[bldg]

df_profiles["Tier_1_Critical_kW"] = sum(bldg_t1.values())
df_profiles["Tier_2_Daytime_kW"] = sum(bldg_t2.values())

df_profiles["Cluster_Total_kW"] = (
    df_profiles["Fire_Station"] +
    df_profiles["Water_Pump"] +
    df_profiles["Health_Clinic"] +
    df_profiles["Telecom_Tower"]
)

# 5. Export CSV
output_csv = BASE / "cluster_load_profiles_15min.csv"
df_profiles.to_csv(output_csv, index_label="Hour")
print(f"Saved: {output_csv}")

# 6. Sanity checks
total_daily_energy = df_profiles["Cluster_Total_kW"].sum() * dt
t1_energy = df_profiles["Tier_1_Critical_kW"].sum() * dt
peak_power = df_profiles["Cluster_Total_kW"].max()
base_power = df_profiles["Tier_1_Critical_kW"].min()
t2_energy = df_profiles["Tier_2_Daytime_kW"].sum() * dt

print("\n--- Verification checks ---")
# Single quotes inside the f-string so it runs on Python < 3.12
print(f"Cluster Peak Demand:         {peak_power:.2f} kW ({'OK' if 1 <= peak_power < 100 else 'CHECK'}: expect single/low tens of kW)")
peak_div = max(sum(bldg_t1[b][k] + bldg_t2[b][k] * DIVERSITY_T2.get(b, 1.0) for b in target_buildings)
               for k in range(periods))
connected_kw = {b: round(float((df_equip[df_equip[col_bldg].str.strip() == b][col_power]
                                * df_equip[df_equip[col_bldg].str.strip() == b][col_qty]).sum()) / 1000, 3)
                for b in target_buildings}
peak_per_bldg = {b: round(float(df_profiles[b].max()), 3) for b in target_buildings}
print(f"Peak after diversity:        {peak_div:.2f} kW (max-demand figure, not used for energy)")
print(f"Connected kW per building:   {connected_kw}")
print(f"Overnight Base Load:         {base_power:.2f} kW")
print(f"Total 24h Islanded Energy:   {total_daily_energy:.2f} kWh/day")
print(f"Tier 1 Battery Base Energy:  {t1_energy:.2f} kWh/day")
print(f"Total Tier 2 Energy:         {t2_energy:.2f} kWh/day")

# Overnight Tier 1 energy for the battery window (18:00 -> 06:00, no PV)
night = (hours >= 18) | (hours < 6)
t1_overnight = df_profiles["Tier_1_Critical_kW"].values[night].sum() * dt
print(f"Tier 1 Overnight (18-06):    {t1_overnight:.2f} kWh")
print(f"Overnight / peak ratio:      {base_power / peak_power:.2f}")

# Summary exported for params.py and run_all.py
summary = {
    "peak_cluster_kW": round(float(peak_power), 3),
    "tier1_base_kW": round(float(base_power), 3),
    "tier1_daily_kWh": round(float(t1_energy), 3),
    "tier1_overnight_18_06_kWh": round(float(t1_overnight), 3),
    "cluster_daily_kWh": round(float(total_daily_energy), 3),
    "tier2_daily_kWh": round(float(t2_energy), 3),
    "peak_after_diversity_kW": round(float(peak_div), 3),
    "peak_kW_per_building": peak_per_bldg,
    "connected_kW_per_building": connected_kw,
}
with open(BASE / "load_summary.json", "w") as f:
    json.dump(summary, f, indent=2)
print("Saved: load_summary.json")

# 7. Stacked 24-hour plot
fig, ax = plt.subplots(figsize=(11, 6))

cum_load = np.zeros(periods)
colors = {
    "Telecom_Tower": "#1f77b4",
    "Health_Clinic": "#2ca02c",
    "Fire_Station":  "#d62728",
    "Water_Pump":    "#9467bd"
}

for bldg in target_buildings:
    ax.fill_between(
        hours,
        cum_load,
        cum_load + df_profiles[bldg],
        alpha=0.45,
        label=bldg.replace("_", " "),
        color=colors.get(bldg, None)
    )
    cum_load += df_profiles[bldg]

ax.plot(hours, df_profiles["Cluster_Total_kW"], color="black", lw=2, label="Cluster Total Demand (kW)")
ax.plot(hours, df_profiles["Tier_1_Critical_kW"], color="crimson", lw=2, ls="--", label="Tier 1 Critical Baseline (Overnight)")

ax.set_title("24-Hour Microgrid Load Profiles: Individual Buildings & Cluster Total", fontsize=12)
ax.set_xlabel("Hour of Day (00:00 - 24:00)", fontsize=10)
ax.set_ylabel("Power Demand (kW)", fontsize=10)
ax.set_xlim(0, 24)
ax.margins(x=0)
ax.set_xticks(range(0, 25, 2))
ax.grid(True, linestyle=":", alpha=0.6)
ax.legend(loc="upper right")

plt.tight_layout()
output_plot = BASE / "cluster_stacked_load_profile.png"
plt.savefig(output_plot, dpi=300)
print(f"Saved: {output_plot}")
if __name__ == "__main__" and plt.get_backend().lower() != "agg":
    plt.show()