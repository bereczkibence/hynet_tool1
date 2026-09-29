"""Serializable, unit-aware findings for source-network physics checks."""

from dataclasses import asdict, dataclass, field
import math


@dataclass(frozen=True)
class PhysicsTolerances:
    """Absolute engineering tolerances, independent of IPOPT scaling."""

    power_mw: float = 1e-4
    reactive_mvar: float = 1e-4
    voltage_pu: float = 1e-6
    current_ka: float = 1e-6
    energy_mwh: float = 1e-5
    soc_percent: float = 1e-5
    storage_overlap_mw: float = 1e-5

    def __post_init__(self):
        if any(not math.isfinite(v) or v <= 0 for v in asdict(self).values()):
            raise ValueError("Physics tolerances must be finite and positive.")


@dataclass(frozen=True)
class PhysicsCheck:
    check: str
    element: str
    status: str
    value: float | None
    lower: float | None
    upper: float | None
    violation: float | None
    tolerance: float
    unit: str
    note: str = ""


@dataclass
class PhysicsReport:
    """A partial report never presents absent equipment ratings as verified."""

    checks: list[PhysicsCheck] = field(default_factory=list)

    @property
    def valid(self):
        return bool(self.checks) and not any(c.status == "failed" for c in self.checks)

    @property
    def status(self):
        if not self.valid:
            return "failed"
        return "partial" if any(c.status == "not_checked" for c in self.checks) else "passed"

    def to_dict(self):
        return {
            "status": self.status,
            "passed": sum(c.status == "passed" for c in self.checks),
            "failed": sum(c.status == "failed" for c in self.checks),
            "not_checked": sum(c.status == "not_checked" for c in self.checks),
            "checks": [asdict(c) for c in self.checks],
        }

    def failure_message(self):
        return "; ".join(f"{c.element}: {c.check} ({c.note or c.violation})"
                         for c in self.checks if c.status == "failed")[:600]

    def skipped(self, check, element, unit, note):
        self.checks.append(PhysicsCheck(check, element, "not_checked", None, None,
                                        None, None, 0.0, unit, note))

    def bounded(self, check, element, value, *, lower=None, upper=None, unit, tolerance, note=""):
        lower, upper = finite(lower), finite(upper)
        value = finite(value)
        if value is None or (lower is not None and upper is not None and lower > upper):
            status, violation = "failed", None
            note = note or "Missing/nonfinite result or invalid bounds."
        elif lower is None and upper is None:
            status, violation = "not_checked", None
            note = note or "No finite source limit supplied."
        else:
            violation = max(0.0, lower - value if lower is not None else 0.0,
                            value - upper if upper is not None else 0.0)
            status = "passed" if violation <= tolerance else "failed"
        self.checks.append(PhysicsCheck(check, element, status, value, lower, upper,
                                        violation, tolerance, unit, note))

    def equal(self, check, element, value, expected, *, unit, tolerance):
        if finite(expected) is None:
            value = None
        self.bounded(check, element, value, lower=expected, upper=expected,
                     unit=unit, tolerance=tolerance)


def finite(value):
    """Return a finite float, preserving absence rather than inventing a value."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def source_number(row, key, default=0.0):
    value = finite(row.get(key))
    return default if value is None else value


def source_limit(row, *keys):
    for key in keys:
        value = finite(row.get(key))
        if value is not None:
            return value
    return None


def active_rows(net, name):
    table = getattr(net, name, None)
    if table is not None:
        for idx, row in table.iterrows():
            if bool(row.get("in_service", True)):
                yield int(idx), row


def result_number(results, field, key, scale=1.0):
    value = finite(results.get(field, {}).get(key))
    return math.nan if value is None else value * scale
