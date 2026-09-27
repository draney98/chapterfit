"""Audio cleanup, kept apart from chapter alignment.

Importing this package does not load numpy. The Quality tab imports
``stages`` itself and shows an install note when the quality extra is
missing.
"""

from __future__ import annotations

__all__ = ["quality_status"]


def quality_status() -> tuple[bool, str]:
    """Whether the DSP extra imports, and a short reason when it does not."""
    missing = []
    for name in (
        "numpy",
        "scipy",
        "soundfile",
        "noisereduce",
        "pedalboard",
        "pyloudnorm",
        "sounddevice",
    ):
        try:
            __import__(name)
        except ImportError:
            missing.append(name)
    if missing:
        return False, (
            "Audio cleanup needs the quality extra: "
            "python3 -m pip install -e '.[quality]' "
            f"(missing {', '.join(missing)})"
        )
    return True, ""
