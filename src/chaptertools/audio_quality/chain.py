"""Ordered processing stages. The UI builds its controls from ``spec``."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParamSpec:
    """One control. ``kind`` is ``float``, ``choice``, or ``bool``."""

    key: str
    label: str
    kind: str
    default: float | str | bool
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    unit: str = ""
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class StageAction:
    """A button that is not a parameter, such as learning a noise profile."""

    key: str
    label: str


class Stage:
    """One filter. Subclasses implement ``process`` and set ``spec``."""

    name: str = ""
    title: str = ""
    spec: tuple[ParamSpec, ...] = ()

    def __init__(self) -> None:
        self.bypass = False
        self.params: dict[str, float | str | bool] = {
            item.key: item.default for item in self.spec
        }
        self.extra: dict = {}

    def availability(self) -> tuple[bool, str]:
        """False when an optional dependency is missing. The control stays
        visible and disabled, with the reason as its note."""
        return True, ""

    def enabled_choices(self, key: str) -> tuple[str, ...] | None:
        """Subset of a choice parameter that can be selected, or None for all."""
        return None

    def disabled_note(self, key: str) -> str:
        return ""

    def actions(self) -> tuple[StageAction, ...]:
        return ()

    def reset_param(self, key: str) -> None:
        for item in self.spec:
            if item.key == key:
                self.params[key] = item.default
                return

    def process(self, audio, sr: int):
        raise NotImplementedError

    def snapshot(self) -> dict:
        return {
            "bypass": self.bypass,
            "params": {k: _plain(v) for k, v in self.params.items()},
            "extra": {k: v for k, v in self.extra.items() if _json_safe(v)},
        }

    def load(self, data: dict) -> None:
        if not isinstance(data, dict):
            return
        if "bypass" in data:
            self.bypass = bool(data["bypass"])
        params = data.get("params") or {}
        if isinstance(params, dict):
            for item in self.spec:
                if item.key not in params:
                    continue
                self.params[item.key] = _coerce(item, params[item.key])
        extra = data.get("extra") or {}
        if isinstance(extra, dict):
            self.extra.update({k: v for k, v in extra.items() if _json_safe(v)})


class ProcessingChain:
    """Apply stages in order. Bypassed stages are skipped."""

    def __init__(self, stages: list[Stage]):
        self.stages = list(stages)

    def stage(self, name: str) -> Stage:
        for item in self.stages:
            if item.name == name:
                return item
        raise KeyError(name)

    def process(self, audio, sr: int, *, skip: set[str] | None = None,
                only: set[str] | None = None):
        skip = skip or set()
        for item in self.stages:
            if item.bypass or item.name in skip:
                continue
            if only is not None and item.name not in only:
                continue
            audio = item.process(audio, sr)
        return audio

    def snapshot(self) -> dict:
        return {
            "version": 1,
            "stages": {item.name: item.snapshot() for item in self.stages},
        }

    def apply(self, data: dict) -> None:
        """Overlay a snapshot. Stages and keys that are absent stay as they are."""
        if not isinstance(data, dict):
            return
        stages = data.get("stages", data)
        if not isinstance(stages, dict):
            return
        for item in self.stages:
            blob = stages.get(item.name)
            if isinstance(blob, dict):
                item.load(blob)


def _plain(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, int):
        return value
    return value


def _json_safe(value) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None


def _coerce(spec: ParamSpec, value):
    if spec.kind == "bool":
        return bool(value)
    if spec.kind == "choice":
        text = str(value)
        if spec.choices and text not in spec.choices:
            return spec.default
        return text
    try:
        number = float(value)
    except (TypeError, ValueError):
        return spec.default
    if spec.minimum is not None:
        number = max(spec.minimum, number)
    if spec.maximum is not None:
        number = min(spec.maximum, number)
    return number
