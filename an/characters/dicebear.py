"""DiceBear HTTP API client + best-effort post-processing.

DiceBear (https://www.dicebear.com) hosts deterministic SVG avatar
generators. We pin to API ``9.x`` for stability — DiceBear pins styles to
API versions and 9.x is supported through 2028 (research §4.1, caveats).

The styles we care about — ``adventurer``, ``lorelei``, ``avataaars`` —
emit SVGs with internal groups but the group naming is style-specific and
not stable across versions. Rather than parse it heuristically (which
would silently break on a future style version), we wrap the DiceBear SVG
in a :func:`wrap_dicebear_for_an` envelope: the original SVG becomes a
single ``head`` part, and the rest of the rig is filled in from defaults.
That gives a usable cutout puppet immediately, at the cost of less
articulation (you can't, e.g., blink an avataaars-style avatar — the eyes
are baked into the head).

>>> # The wrapping is offline and deterministic.
>>> wrapped = wrap_dicebear_for_an('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80"><circle cx="40" cy="40" r="35"/></svg>', name='maya')
>>> 'id="head"' in wrapped
True
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from typing import Optional


#: Pinned major. NOTE: 9.x and 10.x are both Active with End of Life "None" —
#: the April 2028 date sometimes cited is the EOL of the DEPRECATED 5.x-8.x line,
#: not of this one. The pin is right; the reason previously given for it was not.
DICEBEAR_API_VERSION = "9.x"
#: The default avatar style. CC0 1.0 — no attribution duty falls on anyone who
#: renders with stock settings.
#:
#: The previous default (`adventurer`) is CC BY 4.0, so every character created
#: with default flags carried an undischarged attribution obligation, recorded
#: nowhere. `lorelei` is not merely "a CC0 one": it is the only CC0 *human* style
#: shaped like a head-and-shoulders bust, which is what the rig needs —
#: `wrap_dicebear_for_an` pastes the whole avatar in as the single `head` part,
#: so the other CC0 human styles (`notionists`, `open-peeps`) render half-body
#: characters and would put a torso on a torso. It is also by the same artist as
#: `adventurer`, so the demo art barely shifts. `pixel-art` is the CC0 fallback.
#:
#: All 27 styles stay requestable; only the default moves. See
#: `an/characters/licenses.py` for the per-style table.
DICEBEAR_DEFAULT_STYLE = "lorelei"

# Styles that ship with the API. Listed here so the CLI can offer a useful
# completion / error message; not exhaustive (DiceBear adds new styles).
DICEBEAR_STYLES: tuple[str, ...] = (
    "adventurer",
    "adventurer-neutral",
    "avataaars",
    "avataaars-neutral",
    "big-ears",
    "big-ears-neutral",
    "big-smile",
    "bottts",
    "bottts-neutral",
    "croodles",
    "croodles-neutral",
    "fun-emoji",
    "icons",
    "identicon",
    "initials",
    "lorelei",
    "lorelei-neutral",
    "micah",
    "miniavs",
    "notionists",
    "notionists-neutral",
    "open-peeps",
    "personas",
    "pixel-art",
    "pixel-art-neutral",
    "shapes",
    "thumbs",
)


def fetch_dicebear(
    seed: str,
    *,
    style: str = DICEBEAR_DEFAULT_STYLE,
    api_version: str = DICEBEAR_API_VERSION,
    timeout_s: float = 10.0,
    extra_params: Optional[dict[str, str]] = None,
) -> str:
    """Fetch an avatar SVG from DiceBear's HTTP API.

    Returns the SVG string. Raises :class:`RuntimeError` if the API call
    fails (network error, HTTP error, non-SVG response).

    The URL pattern is::

        https://api.dicebear.com/<api_version>/<style>/svg?seed=<seed>

    Pass ``extra_params`` to forward style-specific options (e.g.
    ``backgroundColor=transparent``).
    """
    base = f"https://api.dicebear.com/{api_version}/{style}/svg"
    params: dict[str, str] = {"seed": seed}
    if extra_params:
        params.update(extra_params)
    url = f"{base}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            data = resp.read()
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(f"DiceBear request failed: {e}") from e
    text = data.decode("utf-8", errors="replace")
    if "<svg" not in text:
        raise RuntimeError(
            f"unexpected DiceBear response (no <svg> in body): {text[:200]!r}"
        )
    return text


def wrap_dicebear_for_an(
    avatar_svg: str,
    *,
    name: str,
    canvas_size: int = 1024,
    head_size: int = 600,
) -> str:
    """Wrap a DiceBear avatar SVG into the canonical ``an`` cutout layout.

    The avatar becomes the ``head`` part; ``torso``, ``arm_l``, ``arm_r``,
    ``leg_l``, ``leg_r`` are filled with simple colored rounded-rects so the
    character is immediately renderable as a stick-figure-with-real-face.
    The user can later replace any part by dropping a hand-drawn SVG into
    ``parts/<part>.svg``.

    ``head_size`` is the head's width in canvas pixels; the rest of the rig
    scales accordingly.
    """
    cx = canvas_size / 2
    feet_y = int(canvas_size * 0.96)
    torso_top = feet_y - int(canvas_size * 0.55)
    torso_w = int(canvas_size * 0.30)
    torso_h = int(canvas_size * 0.32)
    head_y = torso_top - head_size
    arm_w = int(canvas_size * 0.07)
    arm_h = int(canvas_size * 0.30)
    leg_w = int(canvas_size * 0.10)
    leg_h = int(canvas_size * 0.28)

    body_color = "#7a8fb5"
    accent_color = "#5b6f96"

    # Find the inner SVG element so we can embed it. We don't parse — we
    # rely on the outer <svg ...> being intact and just nest it with a
    # transform that places & scales it.
    head_x = cx - head_size / 2
    head_block = (
        f'<g id="head">'
        f'<g transform="translate({head_x:.1f} {head_y:.1f}) '
        f'scale({head_size / 80.0:.4f})">'
        # DiceBear avatars use viewBox 0 0 80 80 by default. The scale
        # factor above maps that to head_size px. If the actual viewBox
        # differs, the avatar will simply look smaller/larger; cosmetic.
        f"{_strip_xml_decl(avatar_svg)}"
        "</g>"
        "</g>"
    )
    torso_x = cx - torso_w / 2
    torso_block = (
        f'<g id="torso"><rect x="{torso_x:.1f}" y="{torso_top}" '
        f'width="{torso_w}" height="{torso_h}" rx="20" ry="20" '
        f'fill="{body_color}" stroke="{accent_color}" stroke-width="3"/></g>'
    )
    arm_l_block = (
        f'<g id="arm_l"><rect x="{torso_x - arm_w - 5:.1f}" y="{torso_top + 10}" '
        f'width="{arm_w}" height="{arm_h}" rx="10" ry="10" '
        f'fill="{body_color}" stroke="{accent_color}" stroke-width="3"/></g>'
    )
    arm_r_block = (
        f'<g id="arm_r"><rect x="{torso_x + torso_w + 5:.1f}" y="{torso_top + 10}" '
        f'width="{arm_w}" height="{arm_h}" rx="10" ry="10" '
        f'fill="{body_color}" stroke="{accent_color}" stroke-width="3"/></g>'
    )
    leg_top = torso_top + torso_h
    leg_l_block = (
        f'<g id="leg_l"><rect x="{cx - leg_w - 8:.1f}" y="{leg_top}" '
        f'width="{leg_w}" height="{leg_h}" rx="6" ry="6" '
        f'fill="{accent_color}"/></g>'
    )
    leg_r_block = (
        f'<g id="leg_r"><rect x="{cx + 8:.1f}" y="{leg_top}" '
        f'width="{leg_w}" height="{leg_h}" rx="6" ry="6" '
        f'fill="{accent_color}"/></g>'
    )

    skeleton = (
        f'<g id="skeleton" fill="none">'
        f'<circle id="root" cx="{cx}" cy="{feet_y}" r="3"/>'
        f'<circle id="neck" cx="{cx}" cy="{torso_top}" r="3"/>'
        f'<circle id="shoulder_l" cx="{torso_x:.1f}" cy="{torso_top + 10}" r="3"/>'
        f'<circle id="shoulder_r" cx="{torso_x + torso_w:.1f}" cy="{torso_top + 10}" r="3"/>'
        f"</g>"
    )
    illustration = (
        f'<g id="illustration">'
        f"{leg_l_block}{leg_r_block}"
        f"{torso_block}"
        f"{arm_l_block}{arm_r_block}"
        f"{head_block}"
        f"</g>"
    )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'viewBox="0 0 {canvas_size} {canvas_size}" '
        f'width="{canvas_size}" height="{canvas_size}" '
        f'data-an-character="{name}">'
        f"{skeleton}"
        f"{illustration}"
        "</svg>"
    )


def _strip_xml_decl(svg: str) -> str:
    """Remove an XML declaration if present, so the SVG can be inlined."""
    s = svg.lstrip()
    if s.startswith("<?xml"):
        end = s.find("?>")
        if end != -1:
            s = s[end + 2 :].lstrip()
    return s
