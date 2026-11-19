"""Colour schemes for the dashboard: page theme plus chart colours, one place each.

The page theme itself lives in .streamlit/config.toml; ACTIVE names the scheme that
file matches. DASHBOARD_SCHEME (an environment variable) overrides the chart colours
for previews, alongside --theme.* flags for the page. Every series trio was checked
for colour-blind and normal-vision separation and >= 3:1 contrast on its background.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Scheme:
    """One look for the whole dashboard."""

    name: str
    base: str  # Streamlit base theme: "light" or "dark"
    background: str  # page and chart background
    panel: str  # cards and inputs
    text: str  # body text, chart labels
    border: str
    primary: str  # selected filters, buttons
    weekly: str  # WPSR line, the primary series
    monthly: str  # PSM line
    jodi: str  # the benchmark, kept neutral
    outlier: str  # outlier rings: a colour no line uses
    grid: str
    axis: str
    shade: str  # special periods (export ban, JODI mirrors PSM)
    annotation: str  # labels on shaded periods


SCHEMES = {
    s.name: s
    for s in [
        Scheme(
            name="Amber Obsidian Autumn",
            base="light",
            background="#E1E1E1",
            panel="#EBEBEA",
            text="#1D1816",
            border="#C9C9C7",
            primary="#DC3C0C",
            weekly="#DC3C0C",
            monthly="#691F0C",
            jodi="#7A7A78",
            outlier="#1D1816",
            grid="rgba(122, 122, 120, 0.3)",
            axis="#7A7A78",
            shade="rgba(211, 192, 178, 0.6)",
            annotation="#691F0C",
        ),
        Scheme(
            name="Midnight",
            base="dark",
            background="#0F1419",
            panel="#161C24",
            text="#E6E8EB",
            border="#2A323C",
            primary="#2EC4B6",
            weekly="#2EC4B6",
            monthly="#B79CFF",
            jodi="#6E7681",
            outlier="#E6E8EB",
            grid="rgba(139, 148, 158, 0.18)",
            axis="#3A434E",
            shade="rgba(183, 156, 255, 0.10)",
            annotation="#B79CFF",
        ),
        Scheme(
            name="Paper & Ink",
            base="light",
            background="#F7F4EE",
            panel="#FFFDF8",
            text="#1B1F24",
            border="#DDD6CA",
            primary="#A4282B",
            weekly="#A4282B",
            monthly="#2B5A8C",
            jodi="#8A8782",
            outlier="#1B1F24",
            grid="rgba(27, 31, 36, 0.10)",
            axis="#B8B0A3",
            shade="rgba(43, 90, 140, 0.08)",
            annotation="#2B5A8C",
        ),
        Scheme(
            name="Arctic",
            base="light",
            background="#F4F6F8",
            panel="#FFFFFF",
            text="#14213D",
            border="#DCE1E8",
            primary="#2F62E0",
            weekly="#2F62E0",
            monthly="#E5604E",
            jodi="#6B7A90",
            outlier="#14213D",
            grid="rgba(20, 33, 61, 0.08)",
            axis="#C3CAD5",
            shade="rgba(229, 96, 78, 0.08)",
            annotation="#C2412F",
        ),
        Scheme(
            name="Graphite & Lime",
            base="dark",
            background="#18181B",
            panel="#222226",
            text="#F4F4F5",
            border="#34343A",
            primary="#C6F432",
            weekly="#C6F432",
            monthly="#5AB0F0",
            jodi="#6F6F78",
            outlier="#F4F4F5",
            grid="rgba(244, 244, 245, 0.08)",
            axis="#3F3F46",
            shade="rgba(90, 176, 240, 0.10)",
            annotation="#5AB0F0",
        ),
    ]
}
ACTIVE = "Paper & Ink"

# Light background tints for the directional-bias tile, keyed by prep.VERDICTS.
# Translucent, so the same values read as soft tints on light and dark schemes.
BIAS_TINT = {
    "Biased high": "rgba(214, 69, 65, 0.14)",
    "Biased low": "rgba(214, 69, 65, 0.14)",
    "No significant bias": "rgba(46, 160, 67, 0.14)",
}


def active() -> Scheme:
    """The scheme in use: DASHBOARD_SCHEME if set (for previews), else ACTIVE."""
    return SCHEMES[os.environ.get("DASHBOARD_SCHEME", ACTIVE)]


def theme_flags(scheme: Scheme) -> list[str]:
    """`streamlit run` flags that give the page this scheme's theme."""
    return [
        f"--theme.base={scheme.base}",
        f"--theme.primaryColor={scheme.primary}",
        f"--theme.backgroundColor={scheme.background}",
        f"--theme.secondaryBackgroundColor={scheme.panel}",
        f"--theme.textColor={scheme.text}",
        f"--theme.borderColor={scheme.border}",
    ]
