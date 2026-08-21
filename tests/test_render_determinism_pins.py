"""The rasteriser pins are only worth anything if the render path still uses them.

an#31. `DETERMINISTIC_CHROMIUM_ARGS` existing proves nothing — the failure this
guards is the *quiet* one: the constant survives a refactor while the launch
call goes back to a literal, or `headless=` is dropped as redundant because the
default happens to be headless today. Either way the pixels move and no test
notices, because none of the rendering tests compare against a committed
baseline yet.

So these assertions read the launch call itself, via the AST, rather than
trusting the constant. They need no browser: this file is deliberately outside
the browser gate so it runs on every push, which is the point — the main CI
cannot otherwise see anything about rendering at all.
"""

import ast
from pathlib import Path

import pytest

from an.adapters.cutout import render as render_mod
from an.adapters.cutout.render import DETERMINISTIC_CHROMIUM_ARGS

#: Each flag with the reason it is pinned, so a failure says why it mattered.
REQUIRED_FLAGS = {
    "--no-sandbox": "the pre-existing flag; also a Playwright default",
    "--disable-gpu": "pins software rasterisation (GPU vs software = 1.9% of pixels)",
    "--enable-unsafe-swiftshader": "Chrome 137 removed the automatic WebGL fallback",
    "--force-color-profile=srgb": "pins the screenshot path's colour management",
}

#: Flags measured and deliberately rejected. Adding one silently re-baselines
#: every committed frame, so their absence is as much a contract as the
#: presence of the four above.
REJECTED_FLAGS = {
    "--use-angle=swiftshader": "moves 1.55% of pixels by up to 58/255",
    "--use-gl=swiftshader": "an ignored legacy value",
    "--disable-frame-rate-limit": "measured 1.05x here; the 2.3x is a canvas-2D artefact",
}


def _launch_call() -> ast.Call:
    """The `p.chromium.launch(...)` call node in the render path."""
    tree = ast.parse(Path(render_mod.__file__).read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "launch"
    ]
    assert len(calls) == 1, f"expected exactly one launch call, found {len(calls)}"
    return calls[0]


@pytest.mark.parametrize("flag", sorted(REQUIRED_FLAGS))
def test_the_flag_is_pinned(flag):
    assert flag in DETERMINISTIC_CHROMIUM_ARGS, (
        f"{flag} is pinned because {REQUIRED_FLAGS[flag]}; removing it "
        f"re-baselines every committed frame"
    )


@pytest.mark.parametrize("flag", sorted(REJECTED_FLAGS))
def test_the_rejected_flag_stays_out(flag):
    assert flag not in DETERMINISTIC_CHROMIUM_ARGS, (
        f"{flag} was measured and rejected: {REJECTED_FLAGS[flag]}"
    )


def test_the_launch_call_actually_passes_the_pinned_args():
    """The constant is not the contract — passing it is."""
    call = _launch_call()
    args_kw = next((k for k in call.keywords if k.arg == "args"), None)
    assert args_kw is not None, "the launch call passes no `args=` at all"
    names = {n.id for n in ast.walk(args_kw.value) if isinstance(n, ast.Name)}
    assert "DETERMINISTIC_CHROMIUM_ARGS" in names, (
        "the launch call builds its args from something other than "
        "DETERMINISTIC_CHROMIUM_ARGS — the pin is decorative"
    )


def test_headless_is_explicit():
    """Relying on Playwright's default means a default change swaps the binary.

    Full Chromium renders on the real GPU and differs from headless by 1.91%.
    """
    call = _launch_call()
    headless = next((k for k in call.keywords if k.arg == "headless"), None)
    assert headless is not None, "launch() does not pass `headless=` explicitly"
    assert isinstance(headless.value, ast.Constant) and headless.value.value is True, (
        "`headless=` must be the literal True, not a variable or a default"
    )
