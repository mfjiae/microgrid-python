"""pvgis.py - shared PVGIS loader. PVGIS hourly timestamps are UTC; this converts to local standard time.

Note: row 20230101:0030 has H_sun = 66.9 deg and P = 421 W/kWp, which only fits ~10:30 in Sydney.
AEST = UTC+10. Daylight saving is ignored (the load profile is in standard clock hours).

Inputs:  PVGIS hourly CSV (Timeseries_-34.874_150.601_..._2023_2023.csv)
Outputs: load_pvgis_local() returns a DataFrame: t_local, date, hour, kWh_per_kWp, T2m
"""
import io
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
PVGIS_FILE = BASE / "Timeseries_-34.874_150.601_E5_1kWp_crystSi_14_35deg_-179deg_2023_2023.csv"
UTC_OFFSET_H = 10


def load_pvgis_local(path=PVGIS_FILE):
    """Hourly PV output per 1 kWp in LOCAL time, whole local days only.

    Columns: t_local (start of hour), date, hour, kWh_per_kWp, T2m.
    """
    lines = Path(path).read_text().splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("time,"))
    raw = pd.read_csv(io.StringIO("\n".join(lines[start:])), nrows=8760)
    t_utc = pd.to_datetime(raw["time"], format="%Y%m%d:%H%M")
    t_local = (t_utc + pd.Timedelta(hours=UTC_OFFSET_H)).dt.floor("h")
    df = pd.DataFrame({"t_local": t_local, "kWh_per_kWp": raw["P"].values / 1000.0, "T2m": raw["T2m"].values})
    df["date"] = df["t_local"].dt.date
    df["hour"] = df["t_local"].dt.hour
    counts = df.groupby("date")["hour"].count()
    full = counts[counts == 24].index
    df = df[df["date"].isin(full)].sort_values("t_local").reset_index(drop=True)
    return df


if __name__ == "__main__":
    d = load_pvgis_local()
    daily = d.groupby("date")["kWh_per_kWp"].sum()
    print(f"{len(daily)} full local days, {daily.index[0]} -> {daily.index[-1]}")
    print(f"Worst day {daily.idxmin()} = {daily.min():.3f} kWh/kWp; annual {daily.sum():.0f} kWh/kWp")
    print(d.head(12))
