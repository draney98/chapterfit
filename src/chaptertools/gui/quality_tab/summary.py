"""One-line chain summaries. Pure data, so the tab and the tests share them."""

from __future__ import annotations

import math

MINUS = "\u2212"
DOT = "\u00b7"

_GAIN_KEYS = ("low_gain", "box_gain", "presence_gain", "air_gain")


def stage_summary(stage) -> str:
    """What the chain row shows. Disabled stages say ``off``."""
    ok, _reason = stage.availability()
    if stage.name == "enhance" and not ok:
        return "not installed"
    if stage.bypass:
        return "off"
    params = stage.params
    name = stage.name
    if name == "highpass":
        return _with_unit(params["cutoff_hz"], "Hz", _decimals(stage, "cutoff_hz"))
    if name == "declick":
        thr = _num(params["threshold"], _decimals(stage, "threshold"))
        sens = _num(params["sensitivity"], _decimals(stage, "sensitivity"))
        return f"thr {thr} {DOT} sens {sens}"
    if name == "denoise":
        mode = "neural" if params["mode"] == "deepfilter" else str(params["mode"])
        text = f"{mode} {DOT} {_num(params['strength'], _decimals(stage, 'strength'))}"
        if params.get("per_segment"):
            text += f" {DOT} per seg"
        return text
    if name == "enhance":
        return _num(params["strength"], _decimals(stage, "strength"))
    if name == "gate":
        level = _with_unit(params["threshold_db"], "dB", _decimals(stage, "threshold_db"))
        hold = _with_unit(params["hold_ms"], "ms", _decimals(stage, "hold_ms"))
        return f"{level} {DOT} {hold}"
    if name == "eq":
        active = sum(abs(float(params[key])) >= 0.05 for key in _GAIN_KEYS)
        trim = abs(float(params.get("trim_db", 0.0))) >= 0.05
        if active == 0 and trim:
            return _with_unit(params["trim_db"], "dB", _decimals(stage, "trim_db"))
        if active == 0:
            return "flat"
        word = "band" if active == 1 else "bands"
        return f"{active} {word} active"
    if name == "deesser":
        freq = _with_unit(params["frequency_hz"], "Hz", _decimals(stage, "frequency_hz"))
        level = _with_unit(params["threshold_db"], "dB", _decimals(stage, "threshold_db"))
        return f"{freq} {DOT} {level}"
    if name == "compressor":
        level = _with_unit(params["threshold_db"], "dB", _decimals(stage, "threshold_db"))
        ratio = _num(params["ratio"], _decimals(stage, "ratio"))
        return f"{level} {DOT} {ratio}:1"
    if name == "loudness":
        return _with_unit(params["target_lufs"], "LUFS", _decimals(stage, "target_lufs"))
    if name == "limiter":
        return _with_unit(params["ceiling_db"], "dB", _decimals(stage, "ceiling_db"))
    return ""


def format_number(value, decimals: int, unit: str = "", *, ratio: bool = False) -> str:
    text = _num(value, decimals)
    if ratio:
        return f"{text}:1"
    if unit:
        return f"{text} {unit}"
    return text


def parse_number(text: str) -> float:
    cleaned = str(text).replace(MINUS, "-").replace(":1", "").strip()
    token = cleaned.split()[0] if cleaned else ""
    return float(token)


def decimals_for(step) -> int:
    if step is None or float(step) >= 1:
        return 0
    rendered = f"{float(step):.8f}".rstrip("0")
    if "." not in rendered:
        return 0
    return len(rendered.split(".")[1])


def snap(value, spec) -> float:
    step = float(spec.step or 1)
    origin = float(spec.minimum) if spec.minimum is not None else 0.0
    snapped = origin + round((float(value) - origin) / step) * step
    if spec.minimum is not None:
        snapped = max(float(spec.minimum), snapped)
    if spec.maximum is not None:
        snapped = min(float(spec.maximum), snapped)
    return round(snapped, decimals_for(step))


def close(left, right) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) == bool(right)
    try:
        return math.isclose(float(left), float(right), abs_tol=1e-6)
    except (TypeError, ValueError):
        return left == right


def _decimals(stage, key: str) -> int:
    for item in stage.spec:
        if item.key == key:
            return decimals_for(item.step)
    return 0


def _num(value, decimals: int) -> str:
    number = float(value)
    body = f"{abs(number):.{decimals}f}"
    if number < 0:
        return MINUS + body
    return body


def _with_unit(value, unit: str, decimals: int) -> str:
    return f"{_num(value, decimals)} {unit}"
