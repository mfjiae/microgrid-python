% params.m  exported by params.py - edit params.py and re-run
% Inputs: params.py. Outputs: the variables below (system, sizing, Tier 1 load, AS/NZS 4777.2 limits, sync and droop settings).
F0 = 50.0; VLL = 400.0; S_BASE = 100000.0;
PV_KWP = 35.2; BESS_KWH = 130.0; PV_INV_KVA = 30.0; BESS_KVA = 30.0;
ETA_RT = 0.9; DOD = 0.9; K_FAULT = 1.2; BESS_CHARGE_KW_GRID = 10.0;
TIER1_KW = struct('Fire_Station', 0.4, 'Water_Pump', 0.012, 'Health_Clinic', 0.588, 'Telecom_Tower', 1.5);
TIER1_TOTAL_KW = 2.500;
UV2 = (70.0, 1.0, 2.0); UV1 = (180.0, 10.0, 11.0); OV1 = (265.0, 1.0, 2.0); OV2 = (275.0, 0.0, 0.2);  % [V, delay s, max s] AS/NZS 4777.2 T4.1
UF = (47.0, 1.0, 2.0); OF = (52.0, 0.0, 0.2);  % Australia A, T4.2
RECONNECT_HOLD_S = 60.0; RECONNECT_F = [47.5 50.15];
SYNC_DV = 0.05; SYNC_DF = 0.1; SYNC_DTHETA = 10.0;
DROOP_F = 0.5; DROOP_V = 0.04;
LOAD = readmatrix('cluster_load_profiles_15min.csv');  % 96 x 15-min, kW
