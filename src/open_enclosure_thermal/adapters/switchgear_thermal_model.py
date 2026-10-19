"""Optional bridge to the MPL-2.0 ``switchgear-thermal-model`` package (Alliander).

This module does not copy upstream code. It only maps identified parameters to the upstream
``Switchgear`` fields and, if the package is installed, runs it.
"""

from __future__ import annotations

from ..identification import CurrentExponentFit, FirstOrderFit
from ..schema import ResultStatus

USABLE = (ResultStatus.success, ResultStatus.warning)


def switchgear_parameters(fit: FirstOrderFit, exponent: CurrentExponentFit, rated_current_A: float) -> dict:
    """Map an identified first-order fit + current exponent to upstream ``Switchgear`` arguments.

    ``fit`` must come from a run at ``rated_current_A``; the exponent must be identified (>= 2 current levels).
    """
    if fit.status not in USABLE:
        raise ValueError(f"fit {fit.run}/{fit.sensor} is {fit.status.value}; parameters not usable")
    if exponent.status not in USABLE or exponent.exponent_n is None:
        raise ValueError(f"current exponent for {exponent.sensor} not identified")
    lo, hi = exponent.current_range_A
    if not lo <= rated_current_A <= hi:
        raise ValueError(f"rated current {rated_current_A:g} A outside identified range [{lo:g}, {hi:g}] A")
    cur = fit.validity.get("current_A")
    if cur and abs(cur[0] - rated_current_A) > 1e-6 * rated_current_A:
        raise ValueError(f"fit run current {cur[0]:g} A differs from rated current {rated_current_A:g} A")
    return {
        "rated_current": rated_current_A,
        "measured_temperature_rise": fit.value("rise_inf_K"),
        "temp_rise_exp": exponent.exponent_n.value,
        "thermal_time_constant": fit.value("tau_min"),
    }


def run_upstream(params: dict, datetime_index, load_A, ambient_C):
    """Run upstream ``switchgear_temp`` (requires ``pip install switchgear-thermal-model``)."""
    try:
        from switchgear_thermal_model.schemas import InputProfile
        from switchgear_thermal_model.switchgear import Switchgear
        from switchgear_thermal_model.thermal_model import switchgear_temp
    except ImportError as err:  # pragma: no cover - optional dependency
        raise ImportError("optional dependency missing: pip install switchgear-thermal-model") from err
    profile = InputProfile.create(datetime_index=datetime_index, load_profile=load_A,
                                  ambient_temperature_profile=ambient_C)
    return switchgear_temp(profile, Switchgear(**params))
