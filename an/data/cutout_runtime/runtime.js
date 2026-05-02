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
        const eased = applyEasing(a.easing, u);
        if (typeof a.value === 'number' && typeof b.value === 'number') {
            return a.value + (b.value - a.value) * eased;
        }
        // Non-numeric (e.g. viseme codes): snap at the keyframe boundary.
        return eased >= 1.0 ? b.value : a.value;
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
            // Mouth gets initialized to the rest viseme; the viseme channel
            // re-shapes it via setVisemeOnMouth on the parent node.
            const g = new PIXI.Graphics();
            drawMouthShape(g, 'X');
            return g;
        }
        if (visualSpec.kind === 'eye') {
            return makeEye(visualSpec);
        }
        // sprite without textures + everything else falls back to a rect.
        return makeRect(visualSpec);
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
    }

    // ------------------------------------------------------------------------
    // Pose application
    // ------------------------------------------------------------------------

    function applyPose(pose) {
        for (const key of Object.keys(pose)) {
            const [target, prop] = key.split('::');
            const node = nodeIndex[target];
            if (!node) continue;
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
        const s = VISEME_SHAPES[visemeCode] || VISEME_SHAPES.X;
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

    function setVisemeOnMouth(node, visemeCode) {
        // The mouth node's first Graphics child is the mouth visual.
        const g = node.children && node.children.find(c => c instanceof PIXI.Graphics);
        if (!g) return;
        drawMouthShape(g, visemeCode);
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
            case 'viseme': setVisemeOnMouth(node, value); break;
            default:
                // unknown property — ignore silently for forward compat
                break;
        }
    }

    // ------------------------------------------------------------------------
    // Timeline evaluation
    // ------------------------------------------------------------------------

    function evaluateTimeline(t) {
        const pose = {};
        for (const track of scene.timeline.tracks || []) {
            for (const placed of track.clips || []) {
                const naturalDur = placed.duration != null
                    ? placed.duration
                    : (scene.animations[placed.animation_id] || {}).duration || 0;
                const speed = placed.speed != null ? placed.speed : 1;
                const effDur = naturalDur / speed;
                if (placed.start_time <= t && t <= placed.start_time + effDur) {
                    const localT = (t - placed.start_time) * speed;
                    const anim = scene.animations[placed.animation_id];
                    if (!anim) continue;
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

    NS.anLoadScene = function (sceneJson) {
        if (!window.PIXI) {
            throw new Error('PixiJS not loaded');
        }
        scene = sceneJson;
        nodeIndex = {};
        visualIndex = {};

        const meta = scene.meta || {};
        const width = meta.width || 1920;
        const height = meta.height || 1080;
        const bg = parseColor(meta.background || '#ffffff');

        if (app) {
            app.destroy(true, { children: true, texture: true, baseTexture: true });
            app = null;
        }

        const canvas = document.getElementById('stage');
        app = new PIXI.Application({
            view: canvas,
            width: width,
            height: height,
            backgroundColor: bg,
            antialias: true,
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
        applyProceduralBlinks(t);
        app.render();
        return true;
    };

    // ------------------------------------------------------------------------
    // Procedural eye blinks — auto-blinks every ~3-5s. Pure JS, no IR
    // involvement; the runtime owns this so dialogue stays focused on speech.
    // ------------------------------------------------------------------------

    const _BLINK_PERIOD_S = 4.0;   // baseline interval between blinks
    const _BLINK_DUR_S    = 0.14;  // total close+open duration

    function applyProceduralBlinks(t) {
        // Find every */head/<eye> path and squash its scale_y near blink times.
        // For per-character variety, offset each character's blink schedule
        // by a deterministic phase derived from its name.
        for (const path of Object.keys(nodeIndex)) {
            // Match "<entity>/head/(left_eye|right_eye)".
            const m = path.match(/^([^/]+)\/head\/(left_eye|right_eye)$/);
            if (!m) continue;
            const entity = m[1];
            const phase = (_strHash(entity) % 1000) / 1000.0; // 0..1
            const phased_t = t + phase * _BLINK_PERIOD_S;
            const cycle = phased_t % _BLINK_PERIOD_S;
            const node = nodeIndex[path];
            if (cycle < _BLINK_DUR_S) {
                // Sine half-cycle: 1 → 0.05 → 1 over BLINK_DUR_S
                const u = cycle / _BLINK_DUR_S;
                const closed = Math.sin(u * Math.PI);   // 0 → 1 → 0
                node.scale.y = 1.0 - 0.95 * closed;
            } else if (node.scale.y !== 1.0) {
                node.scale.y = 1.0;
            }
        }
    }

    function _strHash(s) {
        let h = 0;
        for (let i = 0; i < s.length; i++) {
            h = ((h << 5) - h) + s.charCodeAt(i);
            h |= 0;
        }
        return Math.abs(h);
    }

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
