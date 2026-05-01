/**
 * an cutout runtime — Phase 2B
 *
 * Consumes the CutoutSceneJSON contract produced by an.adapters.cutout.compile
 * and renders into a PixiJS canvas. Exposes a small global API so a headless
 * driver (Playwright in Phase 2C) can inject a scene and step through frames.
 *
 * Globals (the only public surface):
 *   window.animaLoadScene(sceneJsonObject) → builds the scene tree, registers
 *       animations + timeline. Returns true on success.
 *   window.animaSetTime(t) → seeks to time t (seconds) and re-evaluates poses.
 *   window.animaCanvasReady() → resolves when the canvas is sized and PixiJS
 *       is initialized (so Playwright knows it's safe to screenshot).
 *   window.animaRuntimeVersion → '0.1.0'
 *
 * The runtime is deliberately self-contained: no module loader, no build step.
 * The HTML loads PixiJS first, then this file; PixiJS is available as window.PIXI.
 */
(function () {
    'use strict';

    const RUNTIME_VERSION = '0.1.0';
    const NS = window;

    NS.animaRuntimeVersion = RUNTIME_VERSION;

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
        return eased >= 0.5 ? b.value : a.value;
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
        if (visualSpec.kind === 'sprite' && visualSpec.texture_id) {
            // For Phase 2B we don't load real textures; fall back to a colored
            // rect so the pipeline runs without art assets.
            return makeRect(visualSpec);
        }
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

    NS.animaLoadScene = function (sceneJson) {
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

        if (scene.scene) {
            buildSceneTree(scene.scene, root, '');
        }

        app.render();
        pixiReady = true;
        return true;
    };

    NS.animaSetTime = function (t) {
        if (!app || !scene) return false;
        const pose = evaluateTimeline(t);
        applyPose(pose);
        app.render();
        return true;
    };

    NS.animaCanvasReady = function () {
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
