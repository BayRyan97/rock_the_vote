"""calibration.py — shift a fitted turnout head onto the served election's level.

The turnout head learns from TARGET_GENERAL_YEAR and is served for
SERVE_GENERAL_YEAR. When those are the same cycle type that is nearly free: the
2020 -> 2024 backtest transfers both ranking and level (ED bias +0.5 pt). Across
cycle types it is not — 2022 -> 2024 keeps the ranking (AUC 0.855) and loses the
level completely (ED bias -24.2 pts), because presidential and midterm turnout
differ by ~21 points in this population regardless of who is likely to vote.

So ranking and level are separate problems, and only the level is cycle-specific.
This module fixes the level and nothing else: it solves for one additive offset
in logit space, which is strictly monotone in p and therefore cannot reorder any
two voters. AUC, PR-AUC and every rank-based targeting decision are identical
before and after; what changes is that summing the scores over a list again
estimates a ballot count.

Deliberately numpy-only and free of model imports, so the GTN serving path can
use it without importing CatBoost (same reason the manifest lives in config).
"""
import numpy as np

# p is clipped before the logit so that a saturated 0.0 or 1.0 — CatBoost does
# emit both — cannot put +/-inf into the solve. The bound is well outside the
# range any downstream consumer distinguishes.
_EPS = 1e-6


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, _EPS, 1.0 - _EPS)
    return np.log(p / (1.0 - p))


def _sigmoid(z: np.ndarray) -> np.ndarray:
    # Branch on sign so neither exp() overflows: for z<0 the algebraically equal
    # exp(z)/(1+exp(z)) is the stable form. np.exp(-z) alone warns and returns
    # inf for large negative z.
    out = np.empty_like(z)
    pos = z >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-z[pos]))
    ez = np.exp(z[~pos])
    out[~pos] = ez / (1.0 + ez)
    return out


def solve_offset(p: np.ndarray, target: float, tol: float = 1e-9,
                 max_iter: int = 200) -> float:
    """Return delta with mean(sigmoid(logit(p) + delta)) == target.

    Bisection rather than a Newton step or scipy: the mean is continuous and
    strictly increasing in delta, so bisection cannot fail to converge, and it
    adds no dependency to a package that is otherwise numpy/pandas at this
    layer. 200 iterations bisects [-40, 40] far below float64 resolution.

    Note this matches the MEAN of the shifted probabilities to the target, which
    is the quantity that makes `sum(turnout_prob)` an expected ballot count. It
    is not the same as shifting by logit(target) - logit(mean(p)); that shifts
    the logit of the mean, not the mean of the logits, and lands several points
    off once the distribution is as bimodal as this one.
    """
    if not 0.0 < target < 1.0:
        raise ValueError(f"target base rate must be in (0, 1), got {target}")
    z = _logit(np.asarray(p, dtype=np.float64))
    lo, hi = -40.0, 40.0
    if _sigmoid(z + lo).mean() > target or _sigmoid(z + hi).mean() < target:
        raise ValueError(
            f"target {target} is unreachable by shifting these scores "
            f"(mean {float(np.mean(p)):.4f} spans "
            f"[{_sigmoid(z + lo).mean():.4f}, {_sigmoid(z + hi).mean():.4f}])")
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        if _sigmoid(z + mid).mean() < target:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


def to_base_rate(p: np.ndarray, target: float | None,
                 label: str = "turnout") -> np.ndarray:
    """Shift `p` onto `target`, reporting what moved. None disables the shift."""
    p = np.asarray(p, dtype=np.float64)
    if target is None:
        print(f"  [{label}] no serve base rate configured — level unshifted "
              f"(mean {p.mean():.4f})")
        return p.astype(np.float32)
    delta = solve_offset(p, target)
    out = _sigmoid(_logit(p) + delta).astype(np.float32)
    print(f"  [{label}] level shifted to the served cycle: mean "
          f"{p.mean():.4f} -> {out.mean():.4f} (target {target:.4f}, "
          f"logit offset {delta:+.4f}); ranking unchanged")
    return out
