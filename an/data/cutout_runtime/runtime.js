/**
 * an cutout runtime — Phase 2B
 *
 * Consumes the CutoutSceneJSON contract produced by an.adapters.cutout.compile
 * and renders into a PixiJS canvas. Exposes a small global API so a headless
 * driver (Playwright in Phase 2C) can inject a scene and step through frames.
 *
 * Globals (the only public surface):
 *   window.anLoadScene(sceneJsonObject) → builds the scene tree, registers
 *       animations + timeline. Returns true on success.
 *   window.anSetTime(t) → seeks to time t (seconds) and re-evaluates poses.
 *   window.anCanvasReady() → resolves when the canvas is sized and PixiJS
 *       is initialized (so Playwright knows it's safe to screenshot).
 *   window.anRuntimeVersion → '0.1.0'
 *
 * The runtime is deliberately self-contained: no module loader, no build step.
 * The HTML loads PixiJS first, then this file; PixiJS is available as window.PIXI.
 */
(function () {
    'use strict';

    const RUNTIME_VERSION = '0.1.0';
    const NS = window;

    NS.anRuntimeVersion = RUNTIME_VERSION;

    let app = null;        // PixiJS Application
    let scene = null;      // current CutoutSceneJSON
    let nodeIndex = {};    // path → PIXI.DisplayObject
    let visualIndex = {};  // path → { container, visual: PIXI.DisplayObject }
    let pixiReady = false;

    // ------------------------------------------------------------------------
    // Easing — mirror an/adapters/cutout/easing.py for consistency
    // ------------------------------------------------------------------------

    const EASINGS = {
        linear: t => t,
        ease: t => (t < 0.5 ? 2 * t * t : 1 - 2 * (1 - t) ** 2),
        ease_in: t => t * t,
        ease_out: t => 1 - (1 - t) ** 2,
        ease_in_out: t => (t < 0.5 ? 2 * t * t : 1 - 2 * (1 - t) ** 2),
        step: t => (t < 1 ? 0 : 1),
    };

    function cubicBezier(cx1, cy1, cx2, cy2, t) {
        if (t <= 0) return 0;
        if (t >= 1) return 1;
        const bx = u =>
            3 * (1 - u) ** 2 * u * cx1 + 3 * (1 - u) * u * u * cx2 + u ** 3;
        const dbx = u =>
            3 * (1 - u) ** 2 * cx1 -
            6 * (1 - u) * u * cx1 +
            6 * (1 - u) * u * cx2 -
            3 * u * u * cx2 +
            3 * u * u;
        const by = u =>
            3 * (1 - u) ** 2 * u * cy1 + 3 * (1 - u) * u * u * cy2 + u ** 3;
        let u = t;
        for (let i = 0; i < 8; i++) {
            const f = bx(u) - t;
            const fp = dbx(u);
            if (Math.abs(fp) < 1e-12) break;
            u = Math.max(0, Math.min(1, u - f / fp));
        }
        return by(u);
    }

    function applyEasing(spec, t) {
        if (spec == null) return t;
        if (typeof spec === 'string') {
            const fn = EASINGS[spec];
            if (!fn) throw new Error('unknown easing: ' + spec);
            return fn(t);
        }
        if (Array.isArray(spec) && spec.length === 4) {
            return cubicBezier(spec[0], spec[1], spec[2], spec[3], t);
        }
        throw new Error('unsupported easing spec');
    }

    // ------------------------------------------------------------------------
    // Channel evaluation
    // ------------------------------------------------------------------------

    function evaluateChannel(channel, t) {
        const kfs = channel.keyframes;
        if (!kfs || kfs.length === 0) return null;
        if (kfs.length === 1) return kfs[0].value;
        const last = kfs[kfs.length - 1];
        if (t >= last.time) return last.value;
        if (t < kfs[0].time) return kfs[0].value;
        // Linear scan; fine for v0.1 (channels are short).
        let i = 0;
        for (; i < kfs.length - 1; i++) {
            if (kfs[i].time <= t && t < kfs[i + 1].time) break;
        }
        const a = kfs[i];
        const b = kfs[i + 1];
        const span = b.time - a.time;
        if (span <= 0) return b.value;
        const u = (t - a.time) / span;
        // Validated for every segment (a typo'd easing name must raise on a
        // swap channel too), but APPLIED only to numeric values.
        const eased = applyEasing(a.easing, u);
        if (typeof a.value === 'number' && typeof b.value === 'number') {
            return a.value + (b.value - a.value) * eased;
        }
        // Non-numeric (viseme codes, swap keys): snap on TIME, never on the
        // eased or raw parameter. The value is `a` for exactly
        // [a.time, b.time): easing cannot move the boundary (the old
        // eased-snap rule let an overshooting cubic bezier show the SECOND
        // key early, or flap A->B->A within one segment), and time has no
        // intermediate arithmetic (a raw-u snap is one float division away
        // from wrong: (t - a.time) / span can round up to 1.0 while
        // t < b.time). Mirror of an/adapters/cutout/channel.py::evaluate —
        // that function is the spec, and tests/test_cutout_channel_parity.py
        // pins the identity.
        return t >= b.time ? b.value : a.value;
    }

    // ------------------------------------------------------------------------
    // Scene → PIXI tree
    // ------------------------------------------------------------------------

    function buildSceneTree(node, parent, pathPrefix) {
        const path = pathPrefix ? pathPrefix + '/' + node.name : node.name;
        const container = new PIXI.Container();
        container.name = path;
        applyTransform(container, node.transform);
        nodeIndex[path] = container;

        if (node.visual) {
            const visual = makeVisual(node.visual);
            container.addChild(visual);
            visualIndex[path] = { container, visual };
        }

        for (const child of node.children || []) {
            buildSceneTree(child, container, path);
        }

        parent.addChild(container);
        return container;
    }

    function makeVisual(visualSpec) {
        if (visualSpec.kind === 'ellipse') {
            return makeEllipse(visualSpec);
        }
        if (visualSpec.kind === 'mouth') {
            // Procedural drawn mouth, initialized to the rest viseme. Its
            // swap vocabulary is DECLARED on the object (an#87): _anDrawSets
            // maps a set name to the redraw function, so the generic swap
            // path applies `viseme` here the same way it swaps textures on a
            // sprite — the set name is convention, not control flow.
            const g = new PIXI.Graphics();
            drawMouthShape(g, 'X');
            g._anDrawSets = {
                viseme: { keys: Object.keys(VISEME_SHAPES).sort(), apply: drawMouthShape },
            };
            return g;
        }
        if (visualSpec.kind === 'eye') {
            return makeEye(visualSpec);
        }
        if (visualSpec.kind === 'svg_sprite') {
            return makeSvgSprite(visualSpec);
        }
        // sprite without textures + everything else falls back to a rect.
        return makeRect(visualSpec);
    }

    // Phase 11b: build a Sprite from a pre-loaded SVG texture. The texture
    // is registered under `visualSpec.asset_id` by the asset preloader.
    function refitToBox(sprite) {
        // No-op unless the sprite was built with fit='contain' (only those
        // carry _anFitBox), so the stretch path is untouched.
        const box = sprite._anFitBox;
        const tex = sprite.texture;
        if (!box || !tex || !tex.orig || !(tex.orig.width > 0) || !(tex.orig.height > 0)) {
            return;
        }
        const k = Math.min(box[0] / tex.orig.width, box[1] / tex.orig.height);
        sprite.scale.set(k, k);
    }

    function makeSvgSprite(visualSpec) {
        const tex = (PIXI.Assets && PIXI.Assets.get)
            ? PIXI.Assets.get(visualSpec.asset_id)
            : null;
        const sprite = tex ? new PIXI.Sprite(tex) : new PIXI.Sprite(PIXI.Texture.WHITE);
        // Fit policy (an#74). 'contain' scales BOTH axes by one factor, so the
        // art keeps the shape it was drawn with; the box may be left with slack
        // on one axis and that slack is the correct rendering. The default
        // stays 'stretch' so a stored scene without the field is unchanged.
        //
        // Sizing by sprite.width/height is what made this a stretch: PixiJS
        // turns each into an INDEPENDENT axis scale, so the box's aspect ratio
        // always won and the art's was never consulted. Measured on this repo's
        // own rig, that distorted arm_l by 3.929x.
        const boxW = visualSpec.width || 64;
        const boxH = visualSpec.height || 64;
        if (visualSpec.fit === 'contain' && tex && tex.orig
                && tex.orig.width > 0 && tex.orig.height > 0) {
            const k = Math.min(boxW / tex.orig.width, boxH / tex.orig.height);
            sprite.scale.set(k, k);
            // Remembered so a texture swap re-fits rather than inheriting the
            // previous texture's scale — the box is the invariant, not the scale.
            sprite._anFitBox = [boxW, boxH];
        } else {
            sprite.width = boxW;
            sprite.height = boxH;
        }
        const ax = visualSpec.anchor_x != null ? visualSpec.anchor_x : 0.5;
        const ay = visualSpec.anchor_y != null ? visualSpec.anchor_y : 0.5;
        sprite.anchor.set(ax, ay);
        // Stash the node's swap-set projection ({set: {KEY: asset_id}}) on
        // the sprite so a swap channel can find its textures without
        // re-walking the scene graph. `viseme` is just one such set (an#87).
        if (visualSpec.asset_sets) {
            sprite._anAssetSets = visualSpec.asset_sets;
        }
        sprite._anAssetId = visualSpec.asset_id;
        return sprite;
    }

    function makeRect(visualSpec) {
        const g = new PIXI.Graphics();
        const color = parseColor(visualSpec.color || '#888888');
        g.beginFill(color, 1.0);
        const w = visualSpec.width || 50;
        const h = visualSpec.height || 50;
        const ax = visualSpec.anchor_x != null ? visualSpec.anchor_x : 0.5;
        const ay = visualSpec.anchor_y != null ? visualSpec.anchor_y : 0.5;
        g.drawRect(-w * ax, -h * ay, w, h);
        g.endFill();
        return g;
    }

    function makeEllipse(visualSpec) {
        const g = new PIXI.Graphics();
        const color = parseColor(visualSpec.color || '#888888');
        const rx = (visualSpec.width || 50) / 2;
        const ry = (visualSpec.height || 50) / 2;
        g.beginFill(color, 1.0);
        g.drawEllipse(0, 0, rx, ry);
        g.endFill();
        return g;
    }

    function makeEye(visualSpec) {
        // White eye-ball + dark pupil, both ellipses. The compiler stamps
        // visualSpec with width/height for the eye-ball; pupil sizes derive.
        const g = new PIXI.Graphics();
        const w = visualSpec.width || 10;
        const h = visualSpec.height || 8;
        // Eye white
        g.beginFill(0xffffff, 1.0);
        g.lineStyle(0.6, 0x222222, 0.6);
        g.drawEllipse(0, 0, w / 2, h / 2);
        g.endFill();
        // Pupil
        g.lineStyle(0);
        g.beginFill(parseColor(visualSpec.color || '#1a1a1a'), 1.0);
        g.drawEllipse(0, 0, w / 4, h / 3);
        g.endFill();
        return g;
    }

    function parseColor(s) {
        if (typeof s !== 'string') return 0x888888;
        const hex = s.startsWith('#') ? s.slice(1) : s;
        return parseInt(hex.padEnd(6, '0').slice(0, 6), 16);
    }

    function applyTransform(displayObject, t) {
        if (!t) return;
        displayObject.x = t.x || 0;
        displayObject.y = t.y || 0;
        displayObject.rotation = t.rotation || 0;
        displayObject.scale.x = t.scale_x != null ? t.scale_x : 1;
        displayObject.scale.y = t.scale_y != null ? t.scale_y : 1;
        displayObject.skew.x = t.skew_x || 0;
        displayObject.skew.y = t.skew_y || 0;
        displayObject.pivot.x = t.pivot_x || 0;
        displayObject.pivot.y = t.pivot_y || 0;
        // Containers DO have alpha, and it cascades to children — which is the
        // semantics wanted: fading a character fades its parts. (Per-part
        // compositing, so overlapping parts show a seam mid-fade; a flattened
        // group fade would need a render-to-texture pass per node per frame.)
        displayObject.alpha = t.alpha != null ? t.alpha : 1;
    }

    // ------------------------------------------------------------------------
    // Pose application
    // ------------------------------------------------------------------------

    // Shallowest target first, then lexicographic. Object key order is
    // insertion order, i.e. a function of channel emission order, which is not
    // a contract — and the golden-frame work downstream needs a frame's pose
    // application to be deterministic. Depth-first ordering also makes the more
    // specific target win for any property that ever cascades.
    function poseKeysInApplicationOrder(pose) {
        return Object.keys(pose).sort(function (a, b) {
            const da = (a.split('::')[0].match(/\//g) || []).length;
            const db = (b.split('::')[0].match(/\//g) || []).length;
            return da !== db ? da - db : (a < b ? -1 : a > b ? 1 : 0);
        });
    }

    function applyPose(pose) {
        for (const key of poseKeysInApplicationOrder(pose)) {
            const [target, prop] = key.split('::');
            const node = nodeIndex[target];
            if (!node) {
                // The sibling silence of the one above: a mistyped target path
                // used to animate nothing, quietly. Listing the known paths is
                // what makes the typo obvious — they are usually one character
                // apart.
                throw new Error(
                    'animation targets unknown node ' + JSON.stringify(target) +
                    '. Known: ' + JSON.stringify(Object.keys(nodeIndex).sort())
                );
            }
            applyProperty(node, prop, pose[key]);
        }
    }

    // Mouth-shape table for viseme rendering. Each entry describes a mouth
    // drawn with two bezier arcs (top & bottom lip). w/h define the bounding
    // box, lipColor outlines, fillColor fills the mouth interior.
    // Mirrors the lookup in an/adapters/cutout/compile.py docstrings.
    const VISEME_SHAPES = {
        // closed: thin line, slight smile
        X: { w: 22, h: 3,  open: 0.0, smile: 0.05 },
        A: { w: 22, h: 4,  open: 0.05, smile: 0.10 },
        // mid open
        B: { w: 22, h: 9,  open: 0.4, smile: 0.0 },
        C: { w: 24, h: 14, open: 0.7, smile: 0.0 },
        D: { w: 26, h: 20, open: 1.0, smile: 0.0 },  // wide open
        // rounded
        E: { w: 20, h: 17, open: 0.85, smile: -0.05 },
        F: { w: 14, h: 14, open: 0.9, smile: -0.10 },  // tight rounded "ooh"
        G: { w: 22, h: 7,  open: 0.2, smile: 0.0, teeth: true },
        H: { w: 18, h: 6,  open: 0.15, smile: 0.0, tongue: true },
    };
    const _LIP_COLOR  = 0x6b2b2b;
    const _MOUTH_FILL = 0x2a1010;
    const _TEETH_COLOR = 0xfafafa;
    const _TONGUE_COLOR = 0xb04848;

    function drawMouthShape(g, visemeCode) {
        // Loud on an unknown code, like every other bad swap key (an#87).
        // The old `|| VISEME_SHAPES.X` fallback silently drew the closed
        // mouth for typos AND for lowercase codes (the sprite path used to
        // upper-case, this one never did) — the compiler now normalises case
        // at emission and validates codes, so anything unknown arriving here
        // is a hand-written scene's mistake and deserves a diagnosis.
        const s = VISEME_SHAPES[visemeCode];
        if (!s) {
            throw new Error(
                'unknown mouth shape ' + JSON.stringify(visemeCode) +
                '. Known: ' + JSON.stringify(Object.keys(VISEME_SHAPES).sort())
            );
        }
        const w = s.w, h = s.h;
        const halfW = w / 2;
        const halfH = h / 2;
        const smile = (s.smile || 0) * h * 1.5; // pixels of upturn at corners
        g.clear();

        // Build the mouth as a quad-arc lens shape:
        //   top lip:    (-halfW, +smile) → quad → (+halfW, +smile)  with control above
        //   bottom lip: (+halfW, +smile) → quad → (-halfW, +smile)  with control below
        // Mouth-fill inside, outlined in lip color.
        g.lineStyle(1.0, _LIP_COLOR, 1.0);
        g.beginFill(_MOUTH_FILL, 1.0);
        g.moveTo(-halfW, smile);
        // top arc — control point pulled UP by some fraction of openness
        g.quadraticCurveTo(0, -halfH * 0.6 - smile * 0.2, +halfW, smile);
        // bottom arc — control point pushed DOWN by openness amount
        g.quadraticCurveTo(0, +halfH * (0.5 + 0.5 * s.open), -halfW, smile);
        g.endFill();

        if (s.teeth) {
            g.lineStyle(0);
            g.beginFill(_TEETH_COLOR, 1.0);
            g.drawRect(-halfW * 0.7, -1.5, w * 0.7, 2.5);
            g.endFill();
        }
        if (s.tongue) {
            g.lineStyle(0);
            g.beginFill(_TONGUE_COLOR, 1.0);
            g.drawEllipse(0, halfH * 0.2, w * 0.18, h * 0.18);
            g.endFill();
        }
    }

    // The ONE swap implementation (an#87). A property outside applyProperty's
    // static switch names a swap SET; the node's visual child declares which
    // sets it can apply — `_anAssetSets` ({set: {KEY: asset_id}}, texture
    // swap) or `_anDrawSets` ({set: redrawFn}, procedural redraw). `viseme`
    // is just a conventional set name carried by mouths.
    //
    // The value domain is as loud as the target and property domains: an
    // unknown key THROWS naming node, set, and the known keys — the old
    // viseme path silently kept the previous texture, which is the defect
    // class an#87 closes. Compiled scenes never reach the throw (the
    // compiler validates and drops with a warning); a hand-written scene
    // gets a diagnosis instead of a frozen mouth.
    function unknownSwapKey(node, prop, key, known) {
        return new Error(
            'unknown key ' + JSON.stringify(key) + ' for swap set ' +
            JSON.stringify(prop) + ' on ' + JSON.stringify(node.name) +
            '. Known keys: ' + JSON.stringify(known)
        );
    }

    function applySwap(child, node, prop, value) {
        const key = String(value);
        if (child._anDrawSets && child._anDrawSets[prop]) {
            // A drawn set declares {keys, apply}: the same loud unknown-key
            // error as a texture set, naming node, set and known keys, before
            // the redraw function's own backstop can fire.
            const drawn = child._anDrawSets[prop];
            if (drawn.keys.indexOf(key) < 0) {
                throw unknownSwapKey(node, prop, key, drawn.keys);
            }
            drawn.apply(child, key);
            return;
        }
        const map = child._anAssetSets[prop];
        const assetId = map[key];
        if (assetId === undefined) {
            throw unknownSwapKey(node, prop, key, Object.keys(map).sort());
        }
        const tex = PIXI.Assets.get(assetId);
        if (!tex) {
            throw new Error(
                'swap set ' + JSON.stringify(prop) + ' on ' +
                JSON.stringify(node.name) + ' resolves key ' +
                JSON.stringify(key) + ' to texture ' + JSON.stringify(assetId) +
                ', which is not loaded.'
            );
        }
        child.texture = tex;
        // Re-fit: under 'contain' the scale belongs to the texture, not to
        // the sprite, so a swap must recompute it. Without this every key
        // after the first inherits the previous texture's scale — silently,
        // and only visible as art that is subtly the wrong size on some
        // frames.
        refitToBox(child);
    }

    function applyProperty(node, prop, value) {
        switch (prop) {
            case 'x': node.x = value; break;
            case 'y': node.y = value; break;
            case 'rotation':
            case 'rotation_rad': node.rotation = value; break;
            case 'scale_x': node.scale.x = value; break;
            case 'scale_y': node.scale.y = value; break;
            case 'skew_x': node.skew.x = value; break;
            case 'skew_y': node.skew.y = value; break;
            case 'pivot_x': node.pivot.x = value; break;
            case 'pivot_y': node.pivot.y = value; break;
            case 'alpha': node.alpha = value; break;
            default: {
                // Not a transform: the property names a swap set (an#87).
                // Apply it if this node's visual declares the set; otherwise
                // throw. Loud, not silent — "forward compat" was the stated
                // reason for ignoring unknown properties once, but silence
                // meant a channel rendered as nothing with no diagnostic.
                const child = (node.children || []).find(
                    c => c._anAssetSets || c._anDrawSets
                );
                const sets = child
                    ? Object.assign({}, child._anDrawSets, child._anAssetSets)
                    : {};
                if (sets[prop]) {
                    applySwap(child, node, prop, value);
                    break;
                }
                throw new Error(
                    'unknown animated property ' + JSON.stringify(prop) +
                    ' on ' + JSON.stringify(node.name) + '. The runtime applies: ' +
                    'x, y, rotation, rotation_rad, scale_x, scale_y, skew_x, ' +
                    'skew_y, pivot_x, pivot_y, alpha — plus this node\'s swap ' +
                    'sets: ' + JSON.stringify(Object.keys(sets).sort()) + '.'
                );
            }
        }
    }

    // ------------------------------------------------------------------------
    // Timeline evaluation
    // ------------------------------------------------------------------------

    // Port of `an/adapters/cutout/clip.py::_wrap_time` — that function is the spec,
    // and this must stay bit-identical to it. Three modes:
    //   once      clamp; past `duration` the last frame holds
    //   loop      t % duration  (at exactly t == duration this is 0, so the FIRST
    //             keyframe renders at the period boundary, not the last)
    //   ping_pong bounce over period 2*duration; t == duration is the apex, inclusive
    function wrapTime(t, duration, mode) {
        if (t < 0) return 0;
        if (mode === 'loop') return duration > 0 ? t % duration : 0;
        if (mode === 'ping_pong') {
            if (duration <= 0) return 0;
            const period = 2 * duration;
            const phase = t % period;
            return phase <= duration ? phase : period - phase;
        }
        return Math.min(t, duration);  // 'once', and the default for anything unknown
    }

    function evaluateTimeline(t) {
        const pose = {};
        for (const track of scene.timeline.tracks || []) {
            for (const placed of track.clips || []) {
                const anim = scene.animations[placed.animation_id];
                if (!anim) continue;
                // The window a placement occupies may be WIDENED by `placed.duration`,
                // but the clip still loops against its OWN natural duration — that
                // asymmetry is exactly what makes looping observable, and Python does
                // the same (`_evaluate_clip` wraps against `clip.duration`, never the
                // placement override). Getting it backwards silently breaks every loop.
                const clipDur = anim.duration || 0;
                const windowDur = placed.duration != null ? placed.duration : clipDur;
                const speed = placed.speed != null ? placed.speed : 1;
                const effDur = windowDur / speed;
                if (placed.start_time <= t && t <= placed.start_time + effDur) {
                    const localT = wrapTime(
                        (t - placed.start_time) * speed, clipDur, anim.loop_mode
                    );
                    for (const ch of anim.channels) {
                        const v = evaluateChannel(ch, localT);
                        if (v != null) {
                            pose[ch.target + '::' + ch.property] = v;
                        }
                    }
                }
            }
        }
        return pose;
    }

    // ------------------------------------------------------------------------
    // Public API
    // ------------------------------------------------------------------------

    // Phase 11b: preload SVG textures declared in scene.assets.textures.
    // Returns a Promise that resolves once all assets are GPU-ready.
    async function preloadAssets(sceneJson) {
        if (!PIXI.Assets) return;
        const textures = (sceneJson.assets && sceneJson.assets.textures) || {};
        // .sort() is a determinism CONTRACT, not tidiness. Object key order here
        // is JSON-document order, i.e. a function of the compiler's emission
        // order, which is not a contract — and this array is the argument to
        // PIXI.Assets.load, whose scheduling it decides. Never observed to move
        // a pixel; an unwritten invariant is one refactor from being false.
        const aliases = Object.keys(textures).sort();
        if (!aliases.length) return;
        for (const alias of aliases) {
            const src = textures[alias].src || textures[alias];
            try {
                PIXI.Assets.add(alias, src);
            } catch (e) {
                // already-registered alias — ignore on hot reload.
            }
        }
        await PIXI.Assets.load(aliases);
    }

    NS.anLoadScene = async function (sceneJson) {
        if (!window.PIXI) {
            throw new Error('PixiJS not loaded');
        }
        await preloadAssets(sceneJson);
        scene = sceneJson;
        nodeIndex = {};
        visualIndex = {};

        const meta = scene.meta || {};
        const width = meta.width || 1920;
        const height = meta.height || 1080;
        const bg = parseColor(meta.background || '#ffffff');

        // Reloading a scene (which `an preview` does on every file change) needs a
        // FRESH canvas element, and this is fiddlier than it looks — an#6:
        //
        //  - `destroy(true, …)` removes <canvas id="stage"> from the document. The
        //    lookup below then returned null and PixiJS, given `view: null`, quietly
        //    made its own detached canvas. Nothing threw; the preview just went
        //    blank on the first edit and never came back.
        //  - Simply keeping the old element (`destroy(false, …)`) does not work
        //    either: its WebGL context is gone with the renderer and cannot be
        //    re-acquired, so the next `new PIXI.Application({view: sameCanvas})`
        //    dies with "Invalid value of `0` passed to checkMaxIfStatementsInShader".
        //
        // So: destroy, then put a brand-new canvas where the old one was, keeping
        // its id and position so the page's CSS and any external lookups still work.
        let canvas = document.getElementById('stage');
        if (!canvas) {
            // Fail loudly. The original bug was invisible precisely because PixiJS
            // treats a missing view as "make me one".
            throw new Error(
                'anLoadScene: no <canvas id="stage"> in the document — the runtime ' +
                'renders into it and will not silently create a detached one.'
            );
        }
        if (app) {
            const parent = canvas.parentNode;
            const next = canvas.nextSibling;
            app.destroy(true, { children: true, texture: true, baseTexture: true });
            app = null;
            const fresh = document.createElement('canvas');
            fresh.id = 'stage';
            fresh.className = canvas.className;
            parent.insertBefore(fresh, next);
            canvas = fresh;
        }
        // Supersample factor, injected by the render path before anLoadScene.
        // The `autoDensity` key below is LOAD-BEARING and is the whole
        // plumbing finding. Set it true and Pixi sets the canvas CSS size to
        // the LOGICAL size, so Chromium composites the k-times backbuffer down
        // before the screenshot -- a blind downscale with no filter choice and
        // no record that it happened. The literal is spelled ONCE in this file
        // on purpose: `an/bench/mutations.py` pins it, exactly as it pins the
        // multisampling flag below, so a reformat fails loudly at the lever
        // rather than producing a "mutation" that changes nothing.
        // Both keys are new; the engine default is RESOLUTION: 1, applied
        // silently, so neither could be relied on before.
        const resolution = Math.max(1, (NS.anSupersample | 0) || 1);
        app = new PIXI.Application({
            view: canvas,
            width: width,
            height: height,
            backgroundColor: bg,
            antialias: true,
            resolution: resolution,
            autoDensity: false,
            autoStart: false,
            preserveDrawingBuffer: true,
        });

        const root = new PIXI.Container();
        // Center the scene so transforms in [-w/2..w/2] are visible by default.
        root.x = width / 2;
        root.y = height / 2;
        app.stage.addChild(root);
        // Index the centered root under the path "root" so camera channels
        // (compiled by Python) can target it for scale animations etc.
        root.name = 'root';
        nodeIndex['root'] = root;

        if (scene.scene) {
            // The Python compiler's top-level node is a synthetic "root"
            // container that just holds the entities. Skip indexing it (so
            // path keys start at the entity name like 'charlie/head/mouth',
            // matching the channel.target strings the compiler emits). Do
            // NOT apply its transform — it's a logical container, and the
            // outer `root` already centers the scene to canvas center.
            for (const child of (scene.scene.children || [])) {
                buildSceneTree(child, root, '');
            }
        }

        app.render();
        pixiReady = true;
        return true;
    };

    NS.anSetTime = function (t) {
        if (!app || !scene) return false;
        const pose = evaluateTimeline(t);
        applyPose(pose);
        app.render();
        return true;
    };

    // Blinks are COMPILED channels since an#88 — see compile.py's
    // `_add_face_clips` / `_blink_placements`. This file used to run a post-pose pass that matched
    // eye nodes by regex and forced scale.y every frame, which is why an
    // authored eye scale_y could never reach the screen. The phase-per-entity
    // fact that pass owned now lives in the compiled scene's meta.blink_phases.

    // ------------------------------------------------------------------------
    // Determinism probe (an#37).
    //
    // Reports; it does not judge. The Python side owns the verdict
    // (`an/determinism.py`) so the rule is testable without a browser, and so a
    // future rule change is a Python diff rather than a runtime re-stage.
    //
    // What it watches and why: the vendored PixiJS carries 4 `Math.random`, 2
    // `Date.now`, 6 `performance.now` and 3 `requestAnimationFrame` calls, and
    // `NoiseFilter`'s default seed is `Math.random()`. All dormant today,
    // because the app is created with `autoStart:false` and driven by explicit
    // `app.render()` calls, and because nothing attaches a filter. Both facts
    // are accidents of the current code with nothing asserting them — adding a
    // grain filter in a later wave would randomise every frame with nothing
    // going red.
    // ------------------------------------------------------------------------

    function _filteredNodePaths() {
        const out = [];
        for (const path of Object.keys(nodeIndex).sort()) {
            const n = nodeIndex[path];
            if (n && n.filters && n.filters.length) out.push(path);
        }
        return out;
    }

    NS.anDeterminismReport = function () {
        const stage = app ? app.stage : null;
        const shared = (window.PIXI && PIXI.Ticker) ? PIXI.Ticker.shared : null;
        return {
            page: (window.location && window.location.pathname) || null,
            runtime_version: RUNTIME_VERSION,
            pixi_version: (window.PIXI && PIXI.VERSION) || null,
            auto_start: !!(app && app.ticker && app.ticker.started),
            shared_ticker_started: !!(shared && shared.started),
            stage_filter_count: (stage && stage.filters) ? stage.filters.length : 0,
            filtered_node_paths: _filteredNodePaths(),
            node_count: Object.keys(nodeIndex).length,
        };
    };

    NS.anCanvasReady = function () {
        return pixiReady;
    };

    // Signal load completion via a known DOM marker (Playwright can wait on it)
    document.addEventListener('DOMContentLoaded', function () {
        const marker = document.createElement('meta');
        marker.name = 'an-runtime-loaded';
        marker.content = RUNTIME_VERSION;
        document.head.appendChild(marker);
    });
})();
