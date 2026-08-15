"""Declare ``an``'s cutout-animation production genre to nw, as ``planned``.

This module exists so a host that catalogs the federation's genres — the unified
reelee AV connector, or reelee-web's ``GET /api/genres`` — can show animation as an
honest **"coming soon"** card instead of omitting it entirely. ``nw`` supports
exactly that: a ``status="planned"`` :class:`nw.Genre` is *declared for discovery,
not yet ready*, so :meth:`nw.Genre.is_ready` returns ``False`` and a picker renders
it disabled without any special-casing.

What is deliberately **not** here: any engine. ``transform_names=()`` and
``strategy_names=()`` because ``an`` has neither an nw Transform pipeline nor a
registered ``nw.renderers`` strategy today. Declaring names that don't resolve
would make an ``available`` genre fail ``is_ready()`` — the wiring bug muvid#3's
review caught — and it would also pre-commit an architecture question that is
still open (see below). A planned genre with no engine is the truthful shape.

**Import-safe and opt-in.** The only import is ``nw``. ``an/__init__.py`` does NOT
import this module, so ``import an`` stays nw-free and ``an`` gains no hard
dependency; a host imports ``an.genre`` explicitly (and skips gracefully when nw
isn't installed). Same arrangement as ``muvid.genre`` and ``braidio.genre``.

**The open question this does not answer.** *How* ``an`` renders under nw is
undecided: the leading proposal is that ``an`` registers an ``nw.renderers``
:class:`Strategy` whose ``plan()`` returns ``Plan(calls=())`` — the free/local
pattern ``nw/renderers/still.py`` already ships, where no paid call is planned and
all the work happens in ``materialize()``. Until that is blessed, this module
promises visibility, not a contract.

**On the slug.** ``cutout_animation`` is underscore-separated to match
``music_video`` and ``commentary_weave``. A genre slug becomes a persisted
identifier (muvid#4 documents how unrenamable that makes it once real projects
exist on disk), so it is worth matching the convention that already has data
behind it rather than the one hyphenated outlier.
"""

from __future__ import annotations

from nw import Genre, register_genre

CUTOUT_ANIMATION_SLUG = "cutout_animation"

CUTOUT_ANIMATION = Genre(
    slug=CUTOUT_ANIMATION_SLUG,
    title="Animation (cutout)",
    description=(
        "A written scene becomes an animated short: characters, dialogue and "
        "camera moves rendered as 2D cutout animation, with speech synthesized "
        "and mouths lip-synced to it. Runs locally — no paid generation. "
        "Not yet wired into nw's plan/execute pipeline."
    ),
    status="planned",
    transform_names=(),
    strategy_names=(),
)

register_genre(CUTOUT_ANIMATION)

__all__ = ["CUTOUT_ANIMATION", "CUTOUT_ANIMATION_SLUG"]
