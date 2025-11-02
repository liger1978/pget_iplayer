"""Colour handling for progress output."""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Iterable, Tuple

RESET = "\033[0m"


@dataclass(frozen=True)
class ColourStyle:
    tqdm_name: str
    ansi_code: str


COLOUR_STYLES: Tuple[ColourStyle, ...] = (
    # Muted emerald – for active/highlight state
    ColourStyle("#1a4d41", "\033[38;2;26;77;65m"),
    # Soft olive – for success/ok state
    ColourStyle("#657253", "\033[38;2;101;114;83m"),
    # Dusty teal – alternative accent
    ColourStyle("#4e7f7b", "\033[38;2;78;127;123m"),
    # Cool slate-grey blue
    ColourStyle("#5a6b7d", "\033[38;2;90;107;125m"),
    # Slate blue
    ColourStyle("#475d7b", "\033[38;2;71;93;123m"),
    # Soft off-white / chalk – for backgrounds or highlighting text on dark
    ColourStyle("#f5f5f5", "\033[38;2;245;245;245m"),
    # Pale champagne – for light accent or highlighting selection
    ColourStyle("#e8ddcf", "\033[38;2;232;221;207m"),
    # Warm mid-neutral – for secondary text
    ColourStyle("#606060", "\033[38;2;96;96;96m"),
    # Deep charcoal – for primary text on light background or background on dark console
    ColourStyle("#2e2e2e", "\033[38;2;46;46;46m"),
    # Warm taupe – for subtle backgrounds or blocks
    ColourStyle("#8f8173", "\033[38;2;143;129;115m"),
    # Cognac amber – for warnings or emphasis
    ColourStyle("#b37537", "\033[38;2;179;117;55m"),
    # Muted burgundy – for errors or critical/high state
    ColourStyle("#7a2f3b", "\033[38;2;122;47;59m"),
)

_FALLBACK_STYLE = ColourStyle("white", "")


def cycle_colours() -> Iterable[ColourStyle]:
    """Yield an infinite sequence of colour styles."""
    palette = COLOUR_STYLES if COLOUR_STYLES else (_FALLBACK_STYLE,)
    return itertools.cycle(palette)


__all__ = ["ColourStyle", "COLOUR_STYLES", "RESET", "cycle_colours"]
