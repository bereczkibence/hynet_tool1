"""Synthetic two-bus example created for Tool1; powers in MW/MVAr."""
import numpy as np

def case():
    ppc = {}
    ppc["version"] = "2"
    ppc["baseMVA"] = 100.0
    ppc["bus"] = np.array([
        [10, 3, 0, 0, 0, 0, 1, 1, 0, 12.66, 1, 1.1, 0.9],
        [40, 1, 0.5, 0.1, 0, 0, 1, 1, 0, 12.66, 1, 1.1, 0.9],
    ])
    ppc["gen"] = np.array([[10, 0.5, 0, 100, -100, 1, 100, 1, 100, 0]])
    ppc["branch"] = np.array([[10, 40, 0.01, 0.03, 0, 10, 10, 10, 0, 0, 1]])
    return ppc
