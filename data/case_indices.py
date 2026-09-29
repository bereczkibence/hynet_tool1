"""PyPOWER/MatACDC input columns; powers in MW/MVAr, voltages in pu/kV.

AC impedances use baseMVA; DC resistances use baseMVAdc. Converter units
are selected explicitly by ImportOptions; see docs/CUSTOM_NETWORKS.md.
"""
BUS_I, BUS_TYPE, PD, QD, GS, BS, AREA, VM, VA, BASE_KV, ZONE, VMAX, VMIN = range(13)
GEN_BUS, PG, QG, QMAX, QMIN, VG, MBASE, GEN_STATUS, PMAX, PMIN = range(10)
F_BUS, T_BUS, BR_R, BR_X, BR_B, RATE_A, RATE_B, RATE_C, TAP, SHIFT, BR_STATUS = range(11)
ANGMIN, ANGMAX = 11, 12
DC_BUS, DC_AC_BUS, DC_GRID, DC_PD, DC_VM, DC_KV, DC_VMAX, DC_VMIN, DC_CAP = range(9)
CV_BUS, CV_DC_TYPE, CV_AC_TYPE, CV_P, CV_Q, CV_VAC, CV_RTF, CV_XTF, CV_BF, CV_RC, CV_XC, CV_KV, CV_VMAX, CV_VMIN, CV_IMAX, CV_STATUS, LOSS_A, LOSS_B, LOSS_CR, LOSS_CI = range(20)
DC_FROM, DC_TO, DC_R, DC_L, DC_C, DC_RATE_A, DC_RATE_B, DC_RATE_C, DC_STATUS = range(9)
DC_RATIO = 9
