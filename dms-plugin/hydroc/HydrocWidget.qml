import QtQuick
import Quickshell
import Quickshell.Io
import qs.Common
import qs.Services
import qs.Widgets
import qs.Modules.Plugins

PluginComponent {
    id: root
    layerNamespacePlugin: "hydroc"

    readonly property string apiBase: pluginData.apiBase || "http://127.0.0.1:8781"
    readonly property string _intentPath: (Quickshell.env("XDG_CONFIG_HOME") || (Quickshell.env("HOME") + "/.config")) + "/hydroc/rgb.json"

    // ── synced state ──────────────────────────────────────────────────────
    property bool loading: true
    property string connState: "…"          // "connected" | "no hardware" | "daemon off"
    property bool hwConnected: false
    property int brightness: 25
    property int lbBrightness: 25
    property string lbColorHex: "#FFFFFF"
    property string lbMode: "static"
    property var effect: ({})
    property var keyColors: ({})            // keyId -> [r,g,b]
    property var effectParams: ({})         // effect name -> [params] (from server)
    property var presets: []                // [{id,name,desc,settings}]
    property string activePreset: "custom"
    property var state: ({})                // EC state from /api/state
    property var telemetry: ({})             // from /api/telemetry
    property var health: ({})                // from /api/health

    // ── effect editor state ───────────────────────────────────────────────
    property string effName: "rainbow"
    property int effSpeed: 5
    property int effBrightness: 25
    property int effColorIdx: 8
    property int effDirIdx: 1
    property bool effReactive: false

    // ── per-key editor state ──────────────────────────────────────────────
    property string selKey: ""
    property int pickR: 255
    property int pickG: 0
    property int pickB: 0
    property var recent: []                 // [{label, r, c, keyId}] most-recent first

    // ── popout section ────────────────────────────────────────────────────
    property int section: 0                 // 0=effects 1=keys 2=lightbar 3=power 4=charge 5=system

    // ── static data ───────────────────────────────────────────────────────
    readonly property var effects: ["breathing", "wave", "random", "rainbow", "ripple", "marquee", "raindrop", "aurora", "fireworks"]
    readonly property var effectLabels: ({
        "breathing": "Breathing", "wave": "Wave", "random": "Random", "rainbow": "Rainbow",
        "ripple": "Ripple", "marquee": "Marquee", "raindrop": "Raindrop", "aurora": "Aurora",
        "fireworks": "Fireworks"
    })
    readonly property var effectBlurbs: ({
        "breathing": "One color slowly fades in and out",
        "wave": "A band of light sweeps across the board",
        "random": "Keys flicker through random colors",
        "rainbow": "Full spectrum rolls across the keys",
        "ripple": "Rings expand from the press point",
        "marquee": "A light runs around the edges",
        "raindrop": "Drops fall from the top of each key",
        "aurora": "Flowing ribbons drift over the board",
        "fireworks": "Random bursts of color across the board"
    })
    readonly property var effectAnims: ({
        "breathing": "breathe", "wave": "wave", "random": "random", "rainbow": "rainbow",
        "ripple": "ripple", "marquee": "marquee", "raindrop": "raindrop", "aurora": "aurora",
        "fireworks": "fireworks"
    })
    readonly property var effectSwatches: ({
        "breathing": "#3ee6c4", "wave": "#4cc2ff", "random": "#ff6b6b", "rainbow": "#ff6b6b",
        "ripple": "#7c8cff", "marquee": "#ff9f45", "raindrop": "#6ee7b7", "aurora": "#7cffd4",
        "fireworks": "#ff7ad9"
    })
    readonly property var colorNames: ["none", "red", "orange", "yellow", "green", "blue", "teal", "purple", "random"]
    readonly property var colorRgb: ({
        "red": [255, 77, 77], "orange": [255, 159, 69], "yellow": [255, 210, 63],
        "green": [53, 226, 127], "blue": [76, 194, 255], "teal": [62, 230, 196],
        "purple": [157, 123, 255]
    })
    readonly property var directionNames: ["none", "right", "left", "up", "down"]
    readonly property var rainbowCols: ["#ff6b6b", "#ffd166", "#6ee7b7", "#4cc2ff", "#9d7bff"]
    readonly property var chargeProfiles: [
        { id: "stationary", label: "Stationary", desc: "Trickle — best for a laptop that mostly sits plugged in" },
        { id: "balanced", label: "Balanced", desc: "Long_Life — middle ground" },
        { id: "long_haul", label: "Long Haul", desc: "Standard — full capacity" }
    ]
    readonly property var toggleDefs: [
        { key: "fn_lock", label: "Fn lock", desc: "F-keys act as function keys without holding Fn" },
        { key: "super_key_enable", label: "Super key", desc: "Windows/Super key enabled" },
        { key: "touchpad_toggle_enable", label: "Touchpad toggle", desc: "Fn+F1 toggles the touchpad" },
        { key: "ac_auto_boot", label: "AC auto boot", desc: "Boot when power is connected" },
        { key: "usb_powershare_high", label: "USB power share", desc: "Charge devices while off (exclusive with AC auto boot)" }
    ]

    // Real HYDROC-16 matrix: [label, matrixRow, matrixCol, widthUnits, keyId]
    readonly property var layout: [
        ["Esc", 5, 0, 4, "f0"], ["F1", 5, 1, 4, "f1"], ["F2", 5, 2, 4, "f2"], ["F3", 5, 3, 4, "f3"], ["F4", 5, 4, 4, "f4"], ["F5", 5, 5, 4, "f5"], ["F6", 5, 6, 4, "f6"], ["F7", 5, 7, 4, "f7"], ["F8", 5, 8, 4, "f8"], ["F9", 5, 9, 4, "f9"], ["F10", 5, 10, 4, "f10"], ["F11", 5, 11, 4, "f11"], ["F12", 5, 12, 4, "f12"], ["SnipT", 5, 13, 5, "f13"], ["PrtSc", 5, 14, 5, "f14"], ["Del", 5, 15, 4, "f15"], ["Home", 5, 16, 4, "f16"], ["PgUp", 5, 17, 5, "f17"], ["PgDn", 5, 18, 5, "f18"], ["End", 5, 19, 4, "f19"],
        ["`", 4, 0, 4, "m0_0"], ["1", 4, 1, 4, "m0_1"], ["2", 4, 2, 4, "m0_2"], ["3", 4, 3, 4, "m0_3"], ["4", 4, 4, 4, "m0_4"], ["5", 4, 5, 4, "m0_5"], ["6", 4, 6, 4, "m0_6"], ["7", 4, 7, 4, "m0_7"], ["8", 4, 8, 4, "m0_8"], ["9", 4, 9, 4, "m0_9"], ["0", 4, 10, 4, "m0_10"], ["-", 4, 11, 4, "m0_11"], ["=", 4, 12, 4, "m0_12"], ["BkSp", 4, 14, 6, "m0_13"], ["NmLk", 4, 15, 5, "p0_0"], ["NP/", 4, 16, 4, "p0_1"], ["NP*", 4, 17, 4, "p0_2"], ["NP-", 4, 18, 4, "p0_3"],
        ["Tab", 3, 0, 5, "m1_0"], ["Q", 3, 2, 4, "m1_1"], ["W", 3, 3, 4, "m1_2"], ["E", 3, 4, 4, "m1_3"], ["R", 3, 5, 4, "m1_4"], ["T", 3, 6, 4, "m1_5"], ["Y", 3, 7, 4, "m1_6"], ["U", 3, 8, 4, "m1_7"], ["I", 3, 9, 4, "m1_8"], ["O", 3, 10, 4, "m1_9"], ["P", 3, 11, 4, "m1_10"], ["[", 3, 12, 4, "m1_11"], ["]", 3, 13, 4, "m1_12"], ["\\", 3, 14, 4, "m1_13"], ["NP7", 3, 15, 4, "p1_0"], ["NP8", 3, 16, 4, "p1_1"], ["NP9", 3, 17, 4, "p1_2"], ["NP+", 3, 18, 5, "p1_3"],
        ["Caps", 2, 0, 6, "m2_0"], ["A", 2, 2, 4, "m2_1"], ["S", 2, 3, 4, "m2_2"], ["D", 2, 4, 4, "m2_3"], ["F", 2, 5, 4, "m2_4"], ["G", 2, 6, 4, "m2_5"], ["H", 2, 7, 4, "m2_6"], ["J", 2, 8, 4, "m2_7"], ["K", 2, 9, 4, "m2_8"], ["L", 2, 10, 4, "m2_9"], [";", 2, 11, 4, "m2_10"], ["'", 2, 12, 4, "m2_11"], ["Ent", 2, 14, 6, "m2_12"], ["NP4", 2, 15, 4, "p2_0"], ["NP5", 2, 16, 4, "p2_1"], ["NP6", 2, 17, 4, "p2_2"],
        ["Shift", 1, 0, 7, "m3_0"], ["Z", 1, 3, 4, "m3_1"], ["X", 1, 4, 4, "m3_2"], ["C", 1, 5, 4, "m3_3"], ["V", 1, 6, 4, "m3_4"], ["B", 1, 7, 4, "m3_5"], ["N", 1, 8, 4, "m3_6"], ["M", 1, 9, 4, "m3_7"], [",", 1, 10, 4, "m3_8"], [".", 1, 11, 4, "m3_9"], ["/", 1, 12, 4, "m3_10"], ["Shift", 1, 14, 7, "m3_11"], ["NP1", 1, 15, 4, "p3_0"], ["NP2", 1, 16, 4, "p3_1"], ["NP3", 1, 17, 4, "p3_2"], ["NPEnt", 1, 18, 5, "p3_3"],
        ["Ctrl", 0, 0, 5, "m4_0"], ["Fn", 0, 2, 4, "m4_1"], ["Win", 0, 3, 5, "m4_2"], ["Alt", 0, 4, 5, "m4_3"], ["Space", 0, 7, 12, "m4_4"], ["Alt", 0, 10, 5, "m4_5"], ["Ctrl", 0, 12, 5, "m4_6"], ["←", 0, 13, 4, "m4_7"], ["↑", 0, 14, 4, "m4_8"], ["→", 0, 15, 4, "m4_10"], ["NP0", 0, 16, 5, "p4_0"], ["NP.", 0, 17, 4, "p4_1"], ["↓", 0, 18, 4, "m4_9"]
    ]

    readonly property bool reducedMotion: typeof SettingsData !== "undefined"
        && SettingsData.animationSpeed === SettingsData.AnimationSpeed.None
    readonly property int effectAnimDur: Math.max(300, 2400 - effSpeed * 200)

    readonly property string effectLabel: effectLabels[effName] || effName
    readonly property string pillLabel: loading ? "…"
        : (connState === "daemon off" ? "Daemon off" : effectLabel)

    readonly property color statusColor: {
        if (loading)
            return "transparent";
        if (hwConnected)
            return Theme.success;
        return connState === "daemon off" ? Theme.error : Theme.warning;
    }
    readonly property color statusTint: {
        if (loading)
            return "transparent";
        if (hwConnected)
            return Theme.withAlpha(Theme.success, 0.14);
        return connState === "daemon off" ? Theme.withAlpha(Theme.error, 0.14) : Theme.withAlpha(Theme.warning, 0.14);
    }

    readonly property bool bannerVisible: !loading && connState !== "connected"
    readonly property string statusPillText: loading ? "Loading"
        : (hwConnected ? "Connected" : (connState === "daemon off" ? "Daemon off" : "No hardware"))
    readonly property string detailText: {
        if (loading)
            return "Contacting hydroc-server…";
        if (hwConnected)
            return effectLabel.toLowerCase() + "  ·  " + Math.round(lbBrightness) + "%";
        if (connState === "daemon off")
            return "no actions";
        return effectLabel.toLowerCase();
    }

    // ── keyboard intent (persisted locally; hydroc-server owns the hardware) ──
    JsonAdapter {
        id: rgbIntent
        property int brightness: 25
        property var effect: ({})
        property var key_colors: ({})
        property var lightbar: ({})
    }

    FileView {
        path: root._intentPath
        watchChanges: true
        adapter: rgbIntent
        onFileChanged: root.applyIntent()
    }

    function applyIntent() {
        brightness = rgbIntent.brightness ?? 25;
        effect = rgbIntent.effect || {};
        keyColors = rgbIntent.key_colors || {};
        if (rgbIntent.lightbar) {
            lbBrightness = rgbIntent.lightbar.brightness ?? 25;
            lbMode = rgbIntent.lightbar.mode || "static";
            if (rgbIntent.lightbar.color) {
                let c = rgbIntent.lightbar.color;
                lbColorHex = "#" + c.map(v => v.toString(16).padStart(2, "0")).join("");
            }
        }
        if (effect.name) {
            effName = effect.name;
            effSpeed = effect.speed ?? 5;
            effBrightness = effect.brightness ?? 25;
            effColorIdx = effect.color_idx ?? 8;
            effDirIdx = effect.dir_idx ?? 1;
            effReactive = effect.reactive ?? false;
        }
    }

    function saveIntent() {
        const payload = {
            brightness: brightness,
            effect: {
                name: effName, speed: effSpeed, brightness: effBrightness,
                color_idx: effColorIdx, dir_idx: effDirIdx, reactive: effReactive
            },
            key_colors: keyColors,
            lightbar: { mode: lbMode, brightness: lbBrightness, color: lbHexToRgb(lbColorHex) || [255, 255, 255] }
        };
        writeProc.command = ["sh", "-c", "mkdir -p '" + root._intentPath.replace(/\/[^/]+$/, "") + "' && printf '%s' '" + JSON.stringify(payload).replace(/'/g, "'\\''") + "' > '" + root._intentPath + "'"];
        writeProc.running = true;
    }

    Process {
        id: writeProc
        running: false
    }

    // ── transport (hydroc-server HTTP) ────────────────────────────────────

    Component {
        id: reqComp
        Process {
            property string apiBase: "http://127.0.0.1:8781"
            property string path: ""
            property string httpMethod: "GET"
            property string body: ""
            signal finished(int exitCode, string body)
            property var _lines: []

            function run() {
                _lines = [];
                var args = ["curl", "-s", "--max-time", "5", "-X", httpMethod];
                if (body !== "") {
                    args.push("-H", "Content-Type: application/json");
                    args.push("-d", body);
                }
                args.push(apiBase + path);
                command = args;
                running = false;
                running = true;
            }

            stdout: SplitParser {
                onRead: data => _lines.push(data)
            }
            onExited: code => finished(code, _lines.join("\n"))
        }
    }

    function get(path, cb) {
        const req = reqComp.createObject(root, { apiBase: root.apiBase, path: path });
        req.finished.connect((code, body) => {
            let res = {};
            try { res = JSON.parse(body); } catch (e) {}
            if (cb) cb(code, res, body);
            req.destroy();
        });
        req.run();
    }

    function post(path, payload, cb) {
        const req = reqComp.createObject(root, { apiBase: root.apiBase, path: path, httpMethod: "POST", body: JSON.stringify(payload) });
        req.finished.connect((code, body) => {
            let res = {};
            try { res = JSON.parse(body); } catch (e) {}
            if (cb) cb(code, res, body);
            req.destroy();
        });
        req.run();
    }

    function hasParam(p) {
        const ps = effectParams[effName];
        return ps && ps.indexOf(p) >= 0;
    }

    function fgColor(r, g, b) {
        const l = 0.2126 * r + 0.7152 * g + 0.0722 * b;
        return l > 128 ? Qt.rgba(0, 0, 0, 1) : Qt.rgba(1, 1, 1, 1);
    }

    function hexOf(c) {
        if (!c)
            return "—";
        const h = x => Math.round(x).toString(16).padStart(2, "0").toUpperCase();
        return "#" + h(c[0]) + h(c[1]) + h(c[2]);
    }

    function paintFxTile(ctx, w, h, anim, phase, breathe) {
        ctx.clearRect(0, 0, w, h);
        if (anim === "breathe") {
            ctx.globalAlpha = breathe;
            ctx.fillStyle = "#3ee6c4";
            ctx.fillRect(0, 0, w, h);
        } else if (anim === "wave") {
            const g = ctx.createLinearGradient(0, 0, w * 2, 0);
            g.addColorStop(0, "#4cc2ff");
            g.addColorStop(0.5, "#7c8cff");
            g.addColorStop(1, "#4cc2ff");
            ctx.fillStyle = g;
            ctx.fillRect(-w * phase, 0, w * 2, h);
        } else if (anim === "rainbow") {
            const cols = ["#ff6b6b", "#ffd166", "#6ee7b7", "#4cc2ff", "#9d7bff", "#ff6b6b"];
            const g = ctx.createLinearGradient(0, 0, w * 2, 0);
            cols.forEach((c, i) => g.addColorStop(i / (cols.length - 1), c));
            ctx.fillStyle = g;
            ctx.fillRect(-w * phase, 0, w * 2, h);
        } else if (anim === "marquee") {
            const g = ctx.createLinearGradient(0, 0, w * 3, 0);
            g.addColorStop(0, "#ff9f45");
            g.addColorStop(0.5, "#ffd166");
            g.addColorStop(1, "#ff9f45");
            ctx.fillStyle = g;
            ctx.fillRect(-w * phase * 2, 0, w * 3, h);
        } else if (anim === "raindrop") {
            const g = ctx.createLinearGradient(0, 0, 0, h * 3.2);
            g.addColorStop(0, "#6ee7b7");
            g.addColorStop(0.33, "#3ee6c4");
            g.addColorStop(0.66, "#6ee7b7");
            g.addColorStop(1, "#6ee7b7");
            ctx.fillStyle = g;
            ctx.fillRect(0, -h * phase * 2.2, w, h * 3.2);
        } else if (anim === "aurora") {
            const g = ctx.createLinearGradient(0, 0, 0, h * 2.6);
            g.addColorStop(0, "#7cffd4");
            g.addColorStop(0.33, "#4cc2ff");
            g.addColorStop(0.66, "#9d7bff");
            g.addColorStop(1, "#7cffd4");
            ctx.fillStyle = g;
            ctx.fillRect(0, -h * phase * 1.6, w, h * 2.6);
        } else if (anim === "random") {
            const cols = ["#ff6b6b", "#ffd166", "#6ee7b7", "#4cc2ff", "#9d7bff", "#ff6b6b"];
            const cx = w / 2, cy = h / 2, r = w / 2 - 0.5;
            for (let i = 0; i < cols.length; i++) {
                ctx.beginPath();
                ctx.moveTo(cx, cy);
                ctx.arc(cx, cy, r, phase * 2 * Math.PI + i * 2 * Math.PI / cols.length, phase * 2 * Math.PI + (i + 1) * 2 * Math.PI / cols.length);
                ctx.closePath();
                ctx.fillStyle = cols[i];
                ctx.fill();
            }
        } else if (anim === "ripple") {
            ctx.fillStyle = "#7c8cff";
            ctx.fillRect(0, 0, w, h);
        } else if (anim === "fireworks") {
            ctx.fillStyle = "#ff7ad9";
            ctx.fillRect(0, 0, w, h);
        }
    }

    function refresh() {
        get("/api/rgb/status", (code, res) => {
            loading = false;
            if (code !== 0 || !res || res.keyboard === undefined) {
                connState = "daemon off";
                hwConnected = false;
                return;
            }
            hwConnected = res.keyboard;
            connState = res.keyboard ? "connected" : "no hardware";
        });
        get("/api/rgb/effects", (code, res) => {
            if (code === 0 && res)
                effectParams = res;
        });
        get("/api/state", (code, res) => {
            if (code === 0 && res)
                state = res.state || {};
        });
        get("/api/presets", (code, res) => {
            if (code === 0 && res) {
                presets = res.presets || [];
                activePreset = res.active || "custom";
            }
        });
        get("/api/telemetry", (code, res) => {
            if (code === 0 && res)
                telemetry = res;
        });
        get("/api/health", (code, res) => {
            if (code === 0 && res)
                health = res;
        });
    }

    function toastError(title, res) {
        ToastService.showError(title, res.error || res.status || "request failed");
    }

    function applyEffect(save) {
        post("/api/rgb/effect", {
            name: effName, speed: effSpeed, brightness: effBrightness,
            color_idx: effColorIdx, direction_idx: effDirIdx, reactive: effReactive,
            save: save
        }, (code, res) => {
            if (code === 0 && res && res.ok) {
                saveIntent();
                ToastService.showInfo(save ? effectLabel + " applied & saved to firmware"
                                          : effectLabel + " applied to the keyboard");
                refresh();
            } else {
                toastError("hydroc", res);
            }
        });
    }

    function applyKeys(save) {
        const colors = {};
        for (const kid in keyColors) {
            const v = keyColors[kid];
            colors[kid] = "#" + [v[0], v[1], v[2]].map(x => Math.round(x).toString(16).padStart(2, "0")).join("");
        }
        post("/api/rgb/perkey", { colors: colors, brightness: brightness, save: save }, (code, res) => {
            if (code === 0 && res && res.ok) {
                saveIntent();
                ToastService.showInfo(save ? "Key colors pushed & saved to firmware"
                                          : "Key colors pushed to keyboard");
                refresh();
            } else {
                toastError("hydroc", res);
            }
        });
    }

    function lbHexToRgb(hex) {
        let h = hex.replace("#", "");
        if (h.length !== 6) return null;
        for (let i = 0; i < 6; i++) {
            const c = h.charCodeAt(i);
            if (!(c >= 48 && c <= 57) && !(c >= 65 && c <= 70) && !(c >= 97 && c <= 102)) return null;
        }
        return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
    }

    function applyLightbar() {
        const rgb = lbHexToRgb(lbColorHex);
        if (!rgb) {
            ToastService.showError("hydroc", "Invalid lightbar color — use 6 hex digits");
            return;
        }
        post("/api/rgb/chinbar", {
            mode: lbBrightness === 0 ? "off" : lbMode,
            color: lbColorHex, brightness: lbBrightness, speed: 5
        }, (code, res) => {
            if (code === 0 && res && res.ok) {
                saveIntent();
                ToastService.showInfo(lbBrightness === 0 ? "Lightbar off"
                    : "Lightbar " + lbColorHex + " at " + lbBrightness);
                refresh();
            } else {
                toastError("hydroc", res);
            }
        });
    }

    function restorePalette() {
        let all = {};
        for (const row of layout)
            for (const k of row)
                all[k[4]] = [0, 0, 0];
        keyColors = all;
        recent = [];
        selKey = "";
        applyKeys(false);
        ToastService.showInfo("Palette reset — all keys unlit");
    }

    // ── EC actions ─────────────────────────────────────────────────────────

    function applyPreset(id) {
        post("/api/preset", { preset: id }, (code, res) => {
            if (code === 0 && res && res.ok) {
                ToastService.showInfo("Preset applied: " + (res.preset || id));
                refresh();
            } else {
                toastError("hydroc", res);
            }
        });
    }

    function applyEcSettings(settings, msg) {
        post("/api/apply", { settings: settings, persist: true }, (code, res) => {
            if (code === 0 && res && res.ok) {
                if (msg) ToastService.showInfo(msg);
                refresh();
            } else {
                toastError("hydroc", res);
            }
        });
    }

    function setChargeProfile(id) {
        applyEcSettings({ charge_profile: id }, "Charge profile: " + id);
    }

    function setChargeThreshold(v) {
        applyEcSettings({ charge_threshold: v }, "Charge threshold: " + v + "%");
    }

    function setToggle(key, on) {
        const s = {};
        s[key] = on;
        applyEcSettings(s, (on ? "On: " : "Off: ") + key);
    }

    function setPowerLimit(key, v) {
        const s = {};
        s[key] = v;
        applyEcSettings(s, key.toUpperCase() + ": " + v + " W");
    }

    // ── per-key helpers ───────────────────────────────────────────────────

    function keyColor(kid) {
        const v = keyColors[kid];
        return v ? v : [0, 0, 0];
    }

    function workingRgb(kid) {
        if (selKey === kid)
            return [pickR, pickG, pickB];
        return keyColor(kid);
    }

    function isLit(kid) {
        const v = keyColor(kid);
        return !!(v && (v[0] || v[1] || v[2]));
    }

    function keyIdAt(r, c) {
        for (const row of layout)
            for (const k of row)
                if (k[1] === r && k[2] === c)
                    return k[4];
        return "";
    }

    function labelAt(r, c) {
        for (const row of layout)
            for (const k of row)
                if (k[1] === r && k[2] === c)
                    return k[0];
        return "?";
    }

    function selR() { return selKey ? selKeyRow() : -1; }
    function selC() { return selKey ? selKeyCol() : -1; }
    function selKeyRow() {
        for (const row of layout)
            for (const k of row)
                if (k[4] === selKey) return k[1];
        return -1;
    }
    function selKeyCol() {
        for (const row of layout)
            for (const k of row)
                if (k[4] === selKey) return k[2];
        return -1;
    }

    function editingLabel() {
        return selKey ? labelAt(selR(), selC()) : "Select a key to edit — or use Set all keys.";
    }

    function editingCoords() {
        return selKey ? "· row " + selR() + ", col " + selC() : "";
    }

    function setPickers(r, c) {
        const kid = keyIdAt(r, c);
        if (!kid) return;
        selKey = kid;
        const v = keyColors[kid];
        if (v) { pickR = v[0]; pickG = v[1]; pickB = v[2]; }
    }

    function commitKey() {
        if (!selKey)
            return;
        const r = selR(), c = selC();
        let merged = {};
        Object.assign(merged, keyColors);
        merged[selKey] = [pickR, pickG, pickB];
        keyColors = merged;
        recent = [{ label: labelAt(r, c), r, c, keyId: selKey }]
            .concat(recent.filter(it => !(it.keyId === selKey))).slice(0, 12);
        ToastService.showInfo(labelAt(r, c) + " key set to " + hexOf([pickR, pickG, pickB]));
    }

    function setAllKeys() {
        const rgb = [pickR, pickG, pickB];
        let all = {};
        for (const row of layout)
            for (const k of row)
                all[k[4]] = rgb;
        keyColors = all;
        ToastService.showInfo("All keys set to " + hexOf(rgb));
    }

    function removeRecent(r, c) {
        recent = recent.filter(it => !(it.r === r && it.c === c));
    }

    // ── keyboard matrix layout (real matrix + column gaps) ────────────────

    function buildRow(row) {
        const out = [];
        let prev = -1;
        for (const k of row) {
            const label = k[0], r = k[1], c = k[2], w = k[3];
            if (prev < 0) {
                if (c > 0)
                    out.push({ gap: c });
            } else if (c > prev + 1) {
                out.push({ gap: c - prev - 1 });
            }
            out.push({ label, r, c, w, keyId: k[4] });
            prev = c;
        }
        return out;
    }

    function rowUnit(row, avail) {
        let tot = 0;
        for (const it of buildRow(row))
            tot += it.gap ? it.gap * 0.5 : it.w;
        return tot > 0 ? avail / tot : 0;
    }

    // ── shell ─────────────────────────────────────────────────────────────

    popoutWidth: root.section === 1 ? 760 : 620
    popoutHeight: 660

    Timer {
        interval: 30000
        repeat: true
        running: true
        onTriggered: root.refresh()
    }

    Component.onCompleted: {
        applyIntent();
        refresh();
    }

    // ── bar pill (horizontal) ─────────────────────────────────────────────

    horizontalBarPill: Component {
        Row {
            spacing: 8
            anchors.verticalCenter: parent.verticalCenter

            Rectangle {
                width: 7
                height: 7
                radius: 3.5
                color: root.statusColor
                anchors.verticalCenter: parent.verticalCenter
            }

            DankIcon {
                name: "keyboard"
                size: 14
                color: Theme.surfaceText
                anchors.verticalCenter: parent.verticalCenter
            }

            StyledText {
                text: root.pillLabel
                font.pixelSize: 12
                font.weight: Font.DemiBold
                color: Theme.surfaceText
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }

    verticalBarPill: Component {
        Column {
            spacing: 5
            anchors.horizontalCenter: parent.horizontalCenter

            Rectangle {
                width: 7
                height: 7
                radius: 3.5
                color: root.statusColor
                anchors.horizontalCenter: parent.horizontalCenter
            }

            DankIcon {
                name: "keyboard"
                size: 14
                color: Theme.surfaceText
                anchors.horizontalCenter: parent.horizontalCenter
            }

            StyledText {
                text: root.pillLabel.toLowerCase()
                font.pixelSize: 10
                color: Theme.surfaceText
                anchors.horizontalCenter: parent.horizontalCenter
                horizontalAlignment: Text.AlignHCenter
            }
        }
    }

    // ── popout ────────────────────────────────────────────────────────────

    readonly property real shellH: 620
    readonly property real headerH: 52
    readonly property real tabsH: 44
    readonly property real bannerH: root.bannerVisible ? 42 : 0

    popoutContent: Component {
        PopoutComponent {
            id: pop
            headerText: ""
            detailsText: ""
            showCloseButton: false
            width: parent.width

            // ── header ────────────────────────────────────────────────────
            Item {
                width: parent.width
                height: root.headerH

                Row {
                    anchors.fill: parent
                    anchors.leftMargin: 16
                    anchors.rightMargin: 12
                    spacing: 10

                    Rectangle {
                        width: 30
                        height: 30
                        radius: 9
                        color: Theme.primaryContainer
                        anchors.verticalCenter: parent.verticalCenter
                        DankIcon {
                            anchors.centerIn: parent
                            name: "keyboard"
                            size: 16
                            color: Theme.primary
                        }
                    }

                    Item {
                        width: 14
                        height: 14
                        anchors.verticalCenter: parent.verticalCenter
                        DankSpinner {
                            anchors.centerIn: parent
                            size: 14
                            running: root.loading
                            visible: root.loading
                        }
                        Rectangle {
                            anchors.centerIn: parent
                            width: 10
                            height: 10
                            radius: 5
                            color: root.statusColor
                            visible: !root.loading
                            Rectangle {
                                anchors.fill: parent
                                radius: width / 2
                                color: "transparent"
                                border.color: root.statusColor
                                border.width: 4
                                opacity: 0.25
                            }
                        }
                    }

                    Column {
                        width: parent.width - 30 - 14 - 30 - 10 * 4
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 1

                        StyledText {
                            text: "HydroControl"
                            font.pixelSize: 15
                            font.weight: Font.DemiBold
                            color: Theme.surfaceText
                        }

                        Row {
                            spacing: 7
                            Rectangle {
                                height: 16
                                radius: 8
                                color: root.statusTint
                                visible: !root.loading
                                StyledText {
                                    anchors.centerIn: parent
                                    leftPadding: 7
                                    rightPadding: 7
                                    text: root.statusPillText
                                    font.pixelSize: 10
                                    font.weight: Font.DemiBold
                                    color: root.statusColor
                                }
                            }
                            StyledText {
                                text: root.detailText
                                font.pixelSize: 12
                                color: Theme.surfaceVariantText
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }
                    }

                    Rectangle {
                        width: 30
                        height: 30
                        radius: 15
                        color: closeArea.containsMouse ? Theme.errorHover : "transparent"
                        anchors.verticalCenter: parent.verticalCenter
                        DankIcon {
                            anchors.centerIn: parent
                            name: "close"
                            size: 16
                            color: closeArea.containsMouse ? Theme.error : Theme.surfaceText
                        }
                        MouseArea {
                            id: closeArea
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: { if (pop.closePopout) pop.closePopout(); }
                        }
                    }
                }
            }

            // ── section tabs ──────────────────────────────────────────────
            Row {
                width: parent.width
                height: root.tabsH
                leftPadding: 16
                rightPadding: 16
                spacing: 4
                topPadding: 6

                Repeater {
                    model: [
                        { label: "Effects", icon: "auto_awesome" },
                        { label: "Keys", icon: "keyboard" },
                        { label: "Lightbar", icon: "lightbulb" },
                        { label: "Power", icon: "bolt" },
                        { label: "Charge", icon: "battery_charging_full" },
                        { label: "System", icon: "monitor_heart" }
                    ]
                    delegate: Rectangle {
                        required property var modelData
                        required property int index
                        readonly property bool isSel: root.section === index
                        width: (parent.width - 32 - 20) / 6
                        height: 32
                        radius: 9
                        color: isSel ? Theme.primaryContainer : "transparent"

                        Row {
                            anchors.centerIn: parent
                            spacing: 5
                            DankIcon {
                                name: modelData.icon
                                size: 15
                                color: isSel ? Theme.primary : Theme.surfaceVariantText
                            }
                            StyledText {
                                text: modelData.label
                                font.pixelSize: 12
                                font.weight: isSel ? Font.DemiBold : Font.Normal
                                color: isSel ? Theme.primary : Theme.surfaceVariantText
                            }
                        }

                        MouseArea {
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: root.section = index
                        }
                    }
                }
            }

            // ── degraded-state banner ─────────────────────────────────────
            Item {
                width: parent.width
                height: root.bannerH
                clip: true
                visible: root.bannerVisible

                Rectangle {
                    anchors.fill: parent
                    anchors.leftMargin: 16
                    anchors.rightMargin: 16
                    height: 32
                    radius: 9
                    color: root.connState === "daemon off"
                        ? Theme.withAlpha(Theme.error, 0.13)
                        : Theme.withAlpha(Theme.warning, 0.13)
                    border.color: root.connState === "daemon off"
                        ? Theme.withAlpha(Theme.error, 0.3)
                        : Theme.withAlpha(Theme.warning, 0.3)
                    border.width: 1

                    Row {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.leftMargin: 11
                        anchors.rightMargin: 11
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 9

                        DankIcon {
                            name: root.connState === "daemon off" ? "flash_off" : "info"
                            size: 15
                            color: root.connState === "daemon off" ? Theme.error : Theme.warning
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        StyledText {
                            width: parent.width - 24
                            text: root.connState === "daemon off"
                                ? "hydroc-server is not reachable on :8781. Nothing can be applied until it's running again."
                                : "Keyboard not found — the daemon is up but the keyboard isn't reachable. Settings can't reach the hardware until it reconnects."
                            font.pixelSize: 12
                            color: root.connState === "daemon off" ? Theme.error : Theme.warning
                            wrapMode: Text.WordWrap
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                }
            }

            // ── scroll body ───────────────────────────────────────────────
            DankFlickable {
                width: parent.width
                height: root.shellH - root.headerH - root.tabsH - root.bannerH
                contentWidth: width
                contentHeight: sections.implicitHeight
                clip: true

                Column {
                    id: sections
                    width: parent.width
                    leftPadding: 16
                    rightPadding: 16
                    bottomPadding: 14
                    spacing: 4

                    // ── Effects ───────────────────────────────────────────
                    Column {
                        visible: root.section === 0
                        width: parent.width
                        spacing: 10

                        Column {
                            width: parent.width
                            spacing: 3
                            Repeater {
                                model: root.effects
                                delegate: Item {
                                    required property string modelData
                                    readonly property bool isSel: root.effName === modelData
                                    readonly property string fxAnim: root.effectAnims[modelData]
                                    readonly property color tileBase: root.effectSwatches[modelData]
                                    width: parent.width
                                    height: 54

                                    Rectangle {
                                        anchors.fill: parent
                                        radius: 9
                                        color: isSel ? Theme.surfaceContainerHigh : "transparent"
                                        border.color: isSel ? Theme.outline : "transparent"
                                        border.width: 1
                                        MouseArea {
                                            anchors.fill: parent
                                            hoverEnabled: true
                                            cursorShape: Qt.PointingHandCursor
                                            onClicked: root.effName = modelData
                                        }
                                    }

                                    Row {
                                        anchors.left: parent.left
                                        anchors.leftMargin: 10
                                        anchors.right: parent.right
                                        anchors.rightMargin: 10
                                        anchors.verticalCenter: parent.verticalCenter
                                        spacing: 12

                                        // animated tile
                                        Rectangle {
                                            id: tile
                                            width: 34
                                            height: 34
                                            radius: 8
                                            clip: true
                                            color: "transparent"
                                            border.color: Theme.outlineStrong
                                            border.width: 1

                                            property real phase: 0
                                            property real breatheO: 0.35

                                            Canvas {
                                                id: tileCanvas
                                                anchors.fill: parent
                                                onPaint: root.paintFxTile(getContext("2d"), width, height, fxAnim, tile.phase, tile.breatheO)
                                                Connections {
                                                    target: tile
                                                    function onPhaseChanged() { tileCanvas.requestPaint(); }
                                                    function onBreatheOChanged() { tileCanvas.requestPaint(); }
                                                }
                                            }

                                            NumberAnimation {
                                                running: isSel && ["wave", "rainbow", "marquee", "raindrop", "aurora", "random"].indexOf(fxAnim) >= 0 && !root.reducedMotion
                                                target: tile
                                                property: "phase"
                                                from: 0
                                                to: 1
                                                duration: root.effectAnimDur
                                                loops: Animation.Infinite
                                                easing.type: Easing.Linear
                                            }

                                            SequentialAnimation {
                                                running: isSel && fxAnim === "breathe" && !root.reducedMotion
                                                loops: Animation.Infinite
                                                NumberAnimation { target: tile; property: "breatheO"; to: 1; duration: 1400; easing.type: Easing.InOutSine }
                                                NumberAnimation { target: tile; property: "breatheO"; to: 0.35; duration: 1400; easing.type: Easing.InOutSine }
                                            }

                                            Rectangle {
                                                id: ring1
                                                anchors.centerIn: parent
                                                width: 34
                                                height: 34
                                                radius: 17
                                                color: "transparent"
                                                border.width: 2
                                                border.color: tileBase
                                                scale: 0.3
                                                opacity: 0
                                            }
                                            Rectangle {
                                                id: ring2
                                                anchors.centerIn: parent
                                                width: 34
                                                height: 34
                                                radius: 17
                                                color: "transparent"
                                                border.width: 2
                                                border.color: tileBase
                                                scale: 0.3
                                                opacity: 0
                                            }
                                            SequentialAnimation {
                                                running: isSel && fxAnim === "ripple" && !root.reducedMotion
                                                loops: Animation.Infinite
                                                ParallelAnimation {
                                                    NumberAnimation { target: ring1; property: "scale"; from: 0.3; to: 1.15; duration: 900; easing.type: Easing.OutCubic }
                                                    NumberAnimation { target: ring1; property: "opacity"; from: 0.9; to: 0; duration: 900 }
                                                }
                                            }
                                            SequentialAnimation {
                                                running: isSel && fxAnim === "ripple" && !root.reducedMotion
                                                loops: Animation.Infinite
                                                PauseAnimation { duration: 450 }
                                                ParallelAnimation {
                                                    NumberAnimation { target: ring2; property: "scale"; from: 0.3; to: 1.15; duration: 900; easing.type: Easing.OutCubic }
                                                    NumberAnimation { target: ring2; property: "opacity"; from: 0.9; to: 0; duration: 900 }
                                                }
                                            }

                                            Rectangle {
                                                id: dot1
                                                width: 5
                                                height: 5
                                                radius: 2.5
                                                color: "white"
                                                opacity: 0
                                            }
                                            Rectangle {
                                                id: dot2
                                                width: 4
                                                height: 4
                                                radius: 2
                                                color: "white"
                                                opacity: 0
                                            }
                                            Rectangle {
                                                id: dot3
                                                width: 4
                                                height: 4
                                                radius: 2
                                                color: "white"
                                                opacity: 0
                                            }
                                            SequentialAnimation {
                                                running: isSel && fxAnim === "fireworks" && !root.reducedMotion
                                                loops: Animation.Infinite
                                                ScriptAction { script: { dot1.x = 6 + Math.random() * 20; dot1.y = 6 + Math.random() * 20; } }
                                                ParallelAnimation {
                                                    NumberAnimation { target: dot1; property: "opacity"; from: 0; to: 1; duration: 120; easing.type: Easing.OutQuad }
                                                    NumberAnimation { target: dot1; property: "scale"; from: 0.4; to: 1.6; duration: 240; easing.type: Easing.OutCubic }
                                                }
                                                ParallelAnimation {
                                                    NumberAnimation { target: dot1; property: "opacity"; to: 0; duration: 260 }
                                                    NumberAnimation { target: dot1; property: "scale"; to: 2.6; duration: 260; easing.type: Easing.OutCubic }
                                                }
                                            }
                                            SequentialAnimation {
                                                running: isSel && fxAnim === "fireworks" && !root.reducedMotion
                                                loops: Animation.Infinite
                                                PauseAnimation { duration: 450 }
                                                ScriptAction { script: { dot2.x = 6 + Math.random() * 20; dot2.y = 6 + Math.random() * 20; } }
                                                ParallelAnimation {
                                                    NumberAnimation { target: dot2; property: "opacity"; from: 0; to: 1; duration: 120; easing.type: Easing.OutQuad }
                                                    NumberAnimation { target: dot2; property: "scale"; from: 0.4; to: 1.6; duration: 240; easing.type: Easing.OutCubic }
                                                }
                                                ParallelAnimation {
                                                    NumberAnimation { target: dot2; property: "opacity"; to: 0; duration: 260 }
                                                    NumberAnimation { target: dot2; property: "scale"; to: 2.6; duration: 260; easing.type: Easing.OutCubic }
                                                }
                                            }
                                            SequentialAnimation {
                                                running: isSel && fxAnim === "fireworks" && !root.reducedMotion
                                                loops: Animation.Infinite
                                                PauseAnimation { duration: 900 }
                                                ScriptAction { script: { dot3.x = 6 + Math.random() * 20; dot3.y = 6 + Math.random() * 20; } }
                                                ParallelAnimation {
                                                    NumberAnimation { target: dot3; property: "opacity"; from: 0; to: 1; duration: 120; easing.type: Easing.OutQuad }
                                                    NumberAnimation { target: dot3; property: "scale"; from: 0.4; to: 1.6; duration: 240; easing.type: Easing.OutCubic }
                                                }
                                                ParallelAnimation {
                                                    NumberAnimation { target: dot3; property: "opacity"; to: 0; duration: 260 }
                                                    NumberAnimation { target: dot3; property: "scale"; to: 2.6; duration: 260; easing.type: Easing.OutCubic }
                                                }
                                            }
                                        }

                                        Column {
                                            width: parent.width - 34 - 20 - 24
                                            spacing: 1
                                            anchors.verticalCenter: parent.verticalCenter
                                            StyledText {
                                                text: root.effectLabels[modelData]
                                                font.pixelSize: 14
                                                font.weight: Font.DemiBold
                                                color: Theme.surfaceText
                                            }
                                            StyledText {
                                                text: root.effectBlurbs[modelData]
                                                font.pixelSize: 12
                                                color: Theme.surfaceVariantText
                                                elide: Text.ElideRight
                                            }
                                        }

                                        Rectangle {
                                            id: check
                                            width: 20
                                            height: 20
                                            radius: 10
                                            color: isSel ? Theme.primary : Theme.withAlpha(Theme.onSurface, 0.08)
                                            anchors.verticalCenter: parent.verticalCenter
                                            DankIcon {
                                                anchors.centerIn: parent
                                                name: "check"
                                                size: 12
                                                color: Theme.onPrimary
                                                opacity: isSel ? 1 : 0
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        // parameter sections
                        Row {
                            width: parent.width
                            StyledText {
                                text: "Parameters"
                                font.pixelSize: 11
                                font.weight: Font.DemiBold
                                color: Theme.surfaceVariantText
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            Rectangle {
                                height: 1
                                width: parent.width - 90
                                anchors.verticalCenter: parent.verticalCenter
                                color: Theme.outlineLight
                            }
                        }

                        Column {
                            width: parent.width
                            spacing: 14

                            Column {
                                width: parent.width
                                spacing: 4
                                visible: root.hasParam("speed")
                                Row {
                                    width: parent.width
                                    StyledText { text: "Speed"; font.pixelSize: 13; color: Theme.surfaceText }
                                    Item { width: parent.width - speedVal.width - 80 - 12; height: 1 }
                                    Rectangle {
                                        id: speedVal
                                        height: 18
                                        radius: 9
                                        color: Theme.withAlpha(Theme.onSurface, 0.06)
                                        StyledText {
                                            anchors.centerIn: parent
                                            leftPadding: 7
                                            rightPadding: 7
                                            text: root.effSpeed
                                            isMonospace: true
                                            font.pixelSize: 12
                                            color: Theme.surfaceText
                                        }
                                    }
                                }
                                DankSlider {
                                    width: parent.width
                                    minimum: 0
                                    maximum: 10
                                    value: root.effSpeed
                                    unit: ""
                                    showValue: false
                                    onSliderValueChanged: v => root.effSpeed = v
                                }
                            }

                            Column {
                                width: parent.width
                                spacing: 4
                                visible: root.hasParam("brightness")
                                Row {
                                    width: parent.width
                                    StyledText { text: "Brightness"; font.pixelSize: 13; color: Theme.surfaceText }
                                    Item { width: parent.width - brightVal.width - 80 - 12; height: 1 }
                                    Rectangle {
                                        id: brightVal
                                        height: 18
                                        radius: 9
                                        color: Theme.withAlpha(Theme.onSurface, 0.06)
                                        StyledText {
                                            anchors.centerIn: parent
                                            leftPadding: 7
                                            rightPadding: 7
                                            text: root.effBrightness
                                            isMonospace: true
                                            font.pixelSize: 12
                                            color: Theme.surfaceText
                                        }
                                    }
                                }
                                DankSlider {
                                    width: parent.width
                                    minimum: 0
                                    maximum: 50
                                    value: root.effBrightness
                                    unit: ""
                                    showValue: false
                                    onSliderValueChanged: v => root.effBrightness = v
                                }
                            }

                            Column {
                                width: parent.width
                                spacing: 6
                                visible: root.hasParam("color")
                                Row {
                                    width: parent.width
                                    StyledText { text: "Color"; font.pixelSize: 13; color: Theme.surfaceText }
                                }
                                Flow {
                                    width: parent.width
                                    spacing: 6
                                    Repeater {
                                        model: root.colorNames
                                        delegate: Rectangle {
                                            required property string modelData
                                            readonly property bool isSel: root.colorNames[root.effColorIdx] === modelData
                                            height: 28
                                            radius: 14
                                            color: isSel ? Theme.withAlpha(Theme.primary, 0.1) : "transparent"
                                            border.color: isSel ? Theme.withAlpha(Theme.primary, 0.55) : "transparent"
                                            border.width: 1

                                            Row {
                                                anchors.fill: parent
                                                anchors.leftMargin: 5
                                                anchors.rightMargin: 10
                                                spacing: 7
                                                anchors.verticalCenter: parent.verticalCenter
                                                Rectangle {
                                                    width: 20
                                                    height: 20
                                                    radius: 10
                                                    border.color: Theme.outlineStrong
                                                    border.width: 1
                                                    anchors.verticalCenter: parent.verticalCenter
                                                    color: modelData === "none" ? "transparent"
                                                        : (modelData === "random" ? "transparent"
                                                            : Qt.rgba(root.colorRgb[modelData][0] / 255, root.colorRgb[modelData][1] / 255, root.colorRgb[modelData][2] / 255, 1))
                                                    Canvas {
                                                        anchors.fill: parent
                                                        visible: modelData === "random"
                                                        onPaint: {
                                                            const ctx = getContext("2d");
                                                            const cols = root.rainbowCols;
                                                            const cx = width / 2, cy = height / 2, r = width / 2 - 0.5;
                                                            for (let i = 0; i < cols.length; i++) {
                                                                ctx.beginPath();
                                                                ctx.moveTo(cx, cy);
                                                                ctx.arc(cx, cy, r, i * 2 * Math.PI / cols.length, (i + 1) * 2 * Math.PI / cols.length);
                                                                ctx.closePath();
                                                                ctx.fillStyle = cols[i];
                                                                ctx.fill();
                                                            }
                                                        }
                                                    }
                                                }
                                                StyledText {
                                                    text: modelData
                                                    font.pixelSize: 12
                                                    font.weight: isSel ? Font.DemiBold : Font.Normal
                                                    color: isSel ? Theme.surfaceText : Theme.surfaceVariantText
                                                    anchors.verticalCenter: parent.verticalCenter
                                                }
                                            }

                                            MouseArea {
                                                anchors.fill: parent
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: root.effColorIdx = root.colorNames.indexOf(modelData)
                                            }
                                        }
                                    }
                                }
                            }

                            Column {
                                width: parent.width
                                spacing: 4
                                visible: root.hasParam("direction")
                                Row {
                                    width: parent.width
                                    StyledText { text: "Direction"; font.pixelSize: 13; color: Theme.surfaceText }
                                }
                                DankDropdown {
                                    width: parent.width
                                    dropdownWidth: parent.width
                                    compactMode: true
                                    options: root.directionNames
                                    currentValue: root.directionNames[root.effDirIdx]
                                    onValueChanged: v => root.effDirIdx = root.directionNames.indexOf(v)
                                }
                            }

                            DankToggle {
                                width: parent.width
                                text: "Reactive — respond to keypresses"
                                checked: root.effReactive
                                visible: root.hasParam("reactive")
                                onToggled: v => root.effReactive = v
                            }
                        }

                        // actions
                        Row {
                            width: parent.width
                            spacing: 8
                            DankButton {
                                id: applyBtn
                                text: "Apply"
                                iconName: "bar_chart"
                                backgroundColor: Theme.primary
                                textColor: Theme.onPrimary
                                buttonHeight: 36
                                horizontalPadding: 14
                                enabled: root.hwConnected
                                onClicked: root.applyEffect(false)
                            }
                            DankButton {
                                id: applySaveBtn
                                text: "Apply & Save"
                                iconName: "flash_on"
                                backgroundColor: "transparent"
                                textColor: Theme.surfaceText
                                border.color: Theme.outlineStrong
                                border.width: 1
                                buttonHeight: 36
                                horizontalPadding: 14
                                enabled: root.hwConnected
                                onClicked: root.applyEffect(true)
                            }
                            Item {
                                width: parent.width - applyBtn.width - applySaveBtn.width - resetBtn.width - 8 * 4
                                height: 1
                            }
                            DankButton {
                                id: resetBtn
                                text: "Reset palette"
                                backgroundColor: "transparent"
                                textColor: Theme.primary
                                buttonHeight: 36
                                horizontalPadding: 10
                                onClicked: root.restorePalette()
                            }
                        }

                        StyledText {
                            width: parent.width
                            text: "Apply & Save also flashes the setting to keyboard firmware, so it survives reboot."
                            font.pixelSize: 12
                            color: Theme.surfaceVariantText
                            wrapMode: Text.WordWrap
                        }
                    }

                    // ── Keys ──────────────────────────────────────────────
                    Column {
                        visible: root.section === 1
                        width: parent.width
                        spacing: 10

                        Row {
                            width: parent.width
                            height: 26
                            spacing: 8
                            StyledText {
                                text: "Recently edited"
                                font.pixelSize: 11
                                font.weight: Font.DemiBold
                                color: Theme.surfaceVariantText
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            Flickable {
                                width: parent.width - 120
                                height: 26
                                clip: true
                                contentWidth: stripRow.width
                                contentHeight: 26
                                boundsBehavior: Flickable.StopAtBounds
                                flickableDirection: Flickable.HorizontalFlick
                                Row {
                                    id: stripRow
                                    spacing: 6
                                    Rectangle {
                                        visible: root.recent.length === 0
                                        height: 24
                                        radius: 12
                                        opacity: 0.55
                                        color: Theme.surfaceContainer
                                        border.color: Theme.outline
                                        border.width: 1
                                        StyledText {
                                            anchors.centerIn: parent
                                            leftPadding: 10
                                            rightPadding: 10
                                            text: "No edits yet"
                                            font.pixelSize: 12
                                            color: Theme.surfaceText
                                        }
                                    }
                                    Repeater {
                                        model: root.recent
                                        delegate: Rectangle {
                                            required property var modelData
                                            readonly property bool isSel: root.selKey === modelData.keyId
                                            height: 24
                                            radius: 12
                                            color: Theme.surfaceContainer
                                            border.color: isSel ? Theme.primary : Theme.outline
                                            border.width: 1

                                            Row {
                                                anchors.fill: parent
                                                anchors.leftMargin: 5
                                                anchors.rightMargin: 8
                                                spacing: 6
                                                anchors.verticalCenter: parent.verticalCenter
                                                Rectangle {
                                                    width: 13
                                                    height: 13
                                                    radius: 4
                                                    border.color: Theme.outlineStrong
                                                    border.width: 1
                                                    anchors.verticalCenter: parent.verticalCenter
                                                    color: root.isLit(modelData.keyId)
                                                        ? Qt.rgba(root.keyColor(modelData.keyId)[0] / 255, root.keyColor(modelData.keyId)[1] / 255, root.keyColor(modelData.keyId)[2] / 255, 1)
                                                        : Theme.withAlpha(Theme.onSurface, 0.06)
                                                }
                                                StyledText {
                                                    text: modelData.label
                                                    font.pixelSize: 12
                                                    font.weight: Font.DemiBold
                                                    color: Theme.surfaceText
                                                }
                                                StyledText {
                                                    text: "×"
                                                    font.pixelSize: 14
                                                    color: Theme.surfaceVariantText
                                                    anchors.verticalCenter: parent.verticalCenter
                                                    MouseArea {
                                                        anchors.fill: parent
                                                        anchors.margins: -3
                                                        cursorShape: Qt.PointingHandCursor
                                                        onClicked: root.removeRecent(modelData.r, modelData.c)
                                                    }
                                                }
                                            }
                                            MouseArea {
                                                anchors.fill: parent
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: root.setPickers(modelData.r, modelData.c)
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        Row {
                            width: parent.width
                            spacing: 5
                            StyledText {
                                text: root.selKey ? "Editing: " : ""
                                font.pixelSize: 12
                                color: Theme.surfaceVariantText
                            }
                            StyledText {
                                text: root.editingLabel()
                                font.pixelSize: 12
                                font.weight: Font.DemiBold
                                color: root.selKey ? Theme.surfaceText : Theme.surfaceVariantText
                            }
                            StyledText {
                                text: root.editingCoords()
                                isMonospace: true
                                font.pixelSize: 11
                                color: Theme.surfaceVariantText
                            }
                        }

                        // keyboard matrix
                        Column {
                            id: kbd
                            width: parent.width
                            spacing: 7
                            Repeater {
                                model: root.layout
                                delegate: Row {
                                    id: row
                                    required property var modelData
                                    width: kbd.width
                                    height: 40
                                    spacing: 0
                                    property real unit: root.rowUnit(modelData, kbd.width)
                                    Repeater {
                                        model: root.buildRow(modelData)
                                        delegate: Rectangle {
                                            required property var modelData
                                            readonly property bool isGap: !!modelData.gap
                                            readonly property string keyId: isGap ? "" : modelData.keyId
                                            readonly property bool isSel: keyId === root.selKey
                                            readonly property bool lit: !isGap && root.isLit(keyId)
                                            readonly property var rgb: isGap ? [0, 0, 0] : root.workingRgb(keyId)
                                            width: (isGap ? modelData.gap * 0.5 : modelData.w) * row.unit
                                            height: 40
                                            radius: 5
                                            color: isGap ? "transparent"
                                                : (lit ? Qt.rgba(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255, 1)
                                                    : Theme.withAlpha(Theme.onSurface, 0.05))
                                            border.color: isSel ? Theme.primary : (isGap ? "transparent" : Theme.withAlpha(Theme.outline, 0.4))
                                            border.width: isSel ? 2 : 1

                                            StyledText {
                                                anchors.centerIn: parent
                                                text: modelData.label
                                                font.pixelSize: 11
                                                font.weight: Font.Medium
                                                color: lit ? root.fgColor(rgb[0], rgb[1], rgb[2]) : Theme.surfaceVariantText
                                                horizontalAlignment: Text.AlignHCenter
                                            }
                                            MouseArea {
                                                anchors.fill: parent
                                                enabled: !isGap
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: root.setPickers(modelData.r, modelData.c)
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        // color card
                        Rectangle {
                            width: parent.width
                            radius: 12
                            color: Theme.surfaceContainer
                            border.color: Theme.outline
                            border.width: 1

                            Column {
                                width: parent.width - 24
                                anchors.horizontalCenter: parent.horizontalCenter
                                anchors.topMargin: 14
                                anchors.bottomMargin: 14
                                spacing: 12

                                Row {
                                    width: parent.width
                                    spacing: 10
                                    Column {
                                        width: parent.width / 2 - 5
                                        spacing: 4
                                        StyledText {
                                            text: "Current"
                                            font.pixelSize: 11
                                            font.weight: Font.DemiBold
                                            color: Theme.surfaceVariantText
                                        }
                                        Rectangle {
                                            id: swatchOld
                                            width: parent.width
                                            height: 40
                                            radius: 8
                                            border.color: Theme.outlineStrong
                                            border.width: 1
                                            color: root.isLit(root.selKey)
                                                ? Qt.rgba(root.keyColor(root.selKey)[0] / 255, root.keyColor(root.selKey)[1] / 255, root.keyColor(root.selKey)[2] / 255, 1)
                                                : Theme.withAlpha(Theme.onSurface, 0.05)
                                            StyledText {
                                                anchors.centerIn: parent
                                                text: root.isLit(root.selKey) ? root.hexOf(root.keyColor(root.selKey)) : "—"
                                                isMonospace: true
                                                font.pixelSize: 11
                                                font.weight: Font.DemiBold
                                                color: root.isLit(root.selKey)
                                                    ? root.fgColor(root.keyColor(root.selKey)[0], root.keyColor(root.selKey)[1], root.keyColor(root.selKey)[2])
                                                    : Theme.surfaceVariantText
                                            }
                                        }
                                    }
                                    Column {
                                        width: parent.width / 2 - 5
                                        spacing: 4
                                        StyledText {
                                            text: "New"
                                            font.pixelSize: 11
                                            font.weight: Font.DemiBold
                                            color: Theme.surfaceVariantText
                                        }
                                        Rectangle {
                                            id: swatchNew
                                            width: parent.width
                                            height: 40
                                            radius: 8
                                            border.color: Theme.outlineStrong
                                            border.width: 1
                                            color: Qt.rgba(root.pickR / 255, root.pickG / 255, root.pickB / 255, 1)
                                            StyledText {
                                                anchors.centerIn: parent
                                                text: root.hexOf([root.pickR, root.pickG, root.pickB])
                                                isMonospace: true
                                                font.pixelSize: 11
                                                font.weight: Font.DemiBold
                                                color: root.fgColor(root.pickR, root.pickG, root.pickB)
                                            }
                                        }
                                    }
                                }

                                Column {
                                    width: parent.width
                                    spacing: 8
                                    Row {
                                        width: parent.width
                                        height: 30
                                        spacing: 10
                                        Rectangle {
                                            width: 16
                                            height: 16
                                            radius: 5
                                            color: "#ff4d4d"
                                            border.color: Theme.outlineStrong
                                            border.width: 1
                                            anchors.verticalCenter: parent.verticalCenter
                                        }
                                        DankSlider {
                                            width: parent.width - 16 - 40 - 20
                                            minimum: 0
                                            maximum: 255
                                            value: root.pickR
                                            unit: ""
                                            showValue: false
                                            anchors.verticalCenter: parent.verticalCenter
                                            onSliderValueChanged: v => root.pickR = v
                                        }
                                        StyledText {
                                            width: 40
                                            text: root.pickR
                                            isMonospace: true
                                            font.pixelSize: 12
                                            color: Theme.surfaceText
                                            horizontalAlignment: Text.AlignRight
                                            anchors.verticalCenter: parent.verticalCenter
                                        }
                                    }
                                    Row {
                                        width: parent.width
                                        height: 30
                                        spacing: 10
                                        Rectangle {
                                            width: 16
                                            height: 16
                                            radius: 5
                                            color: "#3ecb6e"
                                            border.color: Theme.outlineStrong
                                            border.width: 1
                                            anchors.verticalCenter: parent.verticalCenter
                                        }
                                        DankSlider {
                                            width: parent.width - 16 - 40 - 20
                                            minimum: 0
                                            maximum: 255
                                            value: root.pickG
                                            unit: ""
                                            showValue: false
                                            anchors.verticalCenter: parent.verticalCenter
                                            onSliderValueChanged: v => root.pickG = v
                                        }
                                        StyledText {
                                            width: 40
                                            text: root.pickG
                                            isMonospace: true
                                            font.pixelSize: 12
                                            color: Theme.surfaceText
                                            horizontalAlignment: Text.AlignRight
                                            anchors.verticalCenter: parent.verticalCenter
                                        }
                                    }
                                    Row {
                                        width: parent.width
                                        height: 30
                                        spacing: 10
                                        Rectangle {
                                            width: 16
                                            height: 16
                                            radius: 5
                                            color: "#4c8dff"
                                            border.color: Theme.outlineStrong
                                            border.width: 1
                                            anchors.verticalCenter: parent.verticalCenter
                                        }
                                        DankSlider {
                                            width: parent.width - 16 - 40 - 20
                                            minimum: 0
                                            maximum: 255
                                            value: root.pickB
                                            unit: ""
                                            showValue: false
                                            anchors.verticalCenter: parent.verticalCenter
                                            onSliderValueChanged: v => root.pickB = v
                                        }
                                        StyledText {
                                            width: 40
                                            text: root.pickB
                                            isMonospace: true
                                            font.pixelSize: 12
                                            color: Theme.surfaceText
                                            horizontalAlignment: Text.AlignRight
                                            anchors.verticalCenter: parent.verticalCenter
                                        }
                                    }
                                }
                            }
                        }

                        // actions
                        Row {
                            width: parent.width
                            spacing: 8
                            DankButton {
                                id: setKeyBtn
                                text: "Set key"
                                iconName: "add"
                                backgroundColor: "transparent"
                                textColor: Theme.surfaceText
                                border.color: Theme.outlineStrong
                                border.width: 1
                                buttonHeight: 36
                                horizontalPadding: 14
                                enabled: root.hwConnected && root.selKey.length > 0
                                onClicked: root.commitKey()
                            }
                            DankButton {
                                id: setAllBtn
                                text: "Set all keys"
                                iconName: "notes"
                                backgroundColor: "transparent"
                                textColor: Theme.surfaceText
                                border.color: Theme.outlineStrong
                                border.width: 1
                                buttonHeight: 36
                                horizontalPadding: 14
                                enabled: root.hwConnected
                                onClicked: root.setAllKeys()
                            }
                            Item {
                                width: parent.width - setKeyBtn.width - setAllBtn.width - pushBtn.width - pushSaveBtn.width - 8 * 5
                                height: 1
                            }
                            DankButton {
                                id: pushBtn
                                text: "Push to keyboard"
                                iconName: "bar_chart"
                                backgroundColor: Theme.primary
                                textColor: Theme.onPrimary
                                buttonHeight: 36
                                horizontalPadding: 14
                                enabled: root.hwConnected
                                onClicked: root.applyKeys(false)
                            }
                            DankButton {
                                id: pushSaveBtn
                                text: "Push & Save"
                                iconName: "flash_on"
                                backgroundColor: "transparent"
                                textColor: Theme.surfaceText
                                border.color: Theme.outlineStrong
                                border.width: 1
                                buttonHeight: 36
                                horizontalPadding: 14
                                enabled: root.hwConnected
                                onClicked: root.applyKeys(true)
                            }
                        }

                        StyledText {
                            width: parent.width
                            text: "Set key commits the color to the working profile. Push sends it to the keyboard; Push & Save also flashes it to firmware so it survives reboot."
                            font.pixelSize: 12
                            color: Theme.surfaceVariantText
                            wrapMode: Text.WordWrap
                        }
                    }

                    // ── Lightbar ───────────────────────────────────────────
                    Column {
                        visible: root.section === 2
                        width: parent.width
                        spacing: 12

                        Column {
                            width: parent.width
                            spacing: 7

                            Rectangle {
                                width: parent.width
                                height: 52
                                radius: 12
                                clip: true
                                border.color: root.lbBrightness === 0 ? Theme.outlineStrong : "transparent"
                                border.width: 1
                                color: root.lbBrightness === 0 ? Theme.withAlpha(Theme.onSurface, 0.05) : "transparent"

                                Item {
                                    anchors.fill: parent
                                    clip: true
                                    visible: root.lbBrightness > 0
                                    Rectangle {
                                        anchors.fill: parent
                                        color: root.lbColorHex
                                    }
                                }

                                Rectangle {
                                    anchors.fill: parent
                                    color: Qt.rgba(0, 0, 0, Math.max(0, Math.min(1, 1 - root.lbBrightness / 100)))
                                    visible: root.lbBrightness > 0
                                }

                                Rectangle {
                                    anchors.top: parent.top
                                    anchors.right: parent.right
                                    anchors.topMargin: 8
                                    anchors.rightMargin: 10
                                    height: 18
                                    radius: 9
                                    color: Theme.withAlpha(Theme.surface, 0.75)
                                    StyledText {
                                        anchors.centerIn: parent
                                        leftPadding: 9
                                        rightPadding: 9
                                        text: root.lbBrightness === 0 ? "Off"
                                            : (root.lbBrightness < 25 ? "Dim" : "On")
                                        font.pixelSize: 10
                                        font.weight: Font.DemiBold
                                        color: Theme.surfaceText
                                    }
                                }
                            }

                            Row {
                                width: parent.width
                                StyledText {
                                    text: "Solid color · brightness"
                                    font.pixelSize: 11
                                    color: Theme.surfaceVariantText
                                }
                                Item { width: parent.width - 80 - 90; height: 1 }
                                StyledText {
                                    width: 90
                                    text: root.lbBrightness + " / 100"
                                    isMonospace: true
                                    font.pixelSize: 11
                                    color: Theme.surfaceVariantText
                                    horizontalAlignment: Text.AlignRight
                                }
                            }
                        }

                        Column {
                            width: parent.width
                            spacing: 4
                            Row {
                                width: parent.width
                                StyledText { text: "Brightness"; font.pixelSize: 13; color: Theme.surfaceText }
                                Item { width: parent.width - lbVal.width - 80 - 12; height: 1 }
                                Rectangle {
                                    id: lbVal
                                    height: 18
                                    radius: 9
                                    color: Theme.withAlpha(Theme.onSurface, 0.06)
                                    StyledText {
                                        anchors.centerIn: parent
                                        leftPadding: 7
                                        rightPadding: 7
                                        text: root.lbBrightness
                                        isMonospace: true
                                        font.pixelSize: 12
                                        color: Theme.surfaceText
                                    }
                                }
                            }
                            DankSlider {
                                width: parent.width
                                minimum: 0
                                maximum: 100
                                value: root.lbBrightness
                                unit: ""
                                showValue: false
                                onSliderValueChanged: v => root.lbBrightness = v
                            }
                        }

                        Column {
                            width: parent.width
                            spacing: 4
                            StyledText { text: "Mode"; font.pixelSize: 13; color: Theme.surfaceText }
                            DankDropdown {
                                width: parent.width
                                dropdownWidth: parent.width
                                compactMode: true
                                options: ["static", "breathing", "wave", "clash", "catchup"]
                                currentValue: root.lbMode
                                onValueChanged: v => root.lbMode = v
                            }
                        }

                        Row {
                            width: parent.width
                            StyledText {
                                text: "Presets"
                                font.pixelSize: 11
                                font.weight: Font.DemiBold
                                color: Theme.surfaceVariantText
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            Rectangle {
                                height: 1
                                width: parent.width - 70
                                anchors.verticalCenter: parent.verticalCenter
                                color: Theme.outlineLight
                            }
                        }

                        Row {
                            width: parent.width
                            spacing: 6
                            Repeater {
                                model: [
                                    { label: "Off", value: 0 },
                                    { label: "Dim", value: 10 },
                                    { label: "Mid", value: 50 },
                                    { label: "Full", value: 100 }
                                ]
                                delegate: Rectangle {
                                    required property var modelData
                                    readonly property bool isSel: root.lbBrightness === modelData.value
                                    height: 30
                                    radius: 15
                                    color: isSel ? Theme.primaryContainer : Theme.surfaceContainer
                                    border.color: isSel ? "transparent" : Theme.outline
                                    border.width: 1
                                    StyledText {
                                        anchors.centerIn: parent
                                        leftPadding: 13
                                        rightPadding: 13
                                        text: modelData.label
                                        font.pixelSize: 12
                                        font.weight: Font.DemiBold
                                        color: isSel ? Theme.primary : Theme.surfaceText
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: root.lbBrightness = modelData.value
                                    }
                                }
                            }
                        }

                        Row {
                            width: parent.width
                            DankTextField {
                                width: parent.width
                                placeholderText: "#FF0000"
                                text: root.lbColorHex
                                leftIconName: "edit"
                                onTextEdited: root.lbColorHex = text
                                onAccepted: root.lbColorHex = text
                            }
                        }

                        Row {
                            width: parent.width
                            DankButton {
                                text: "Apply lightbar"
                                iconName: "bar_chart"
                                backgroundColor: Theme.primary
                                textColor: Theme.onPrimary
                                buttonHeight: 36
                                horizontalPadding: 14
                                enabled: root.hwConnected
                                onClicked: root.applyLightbar()
                            }
                        }

                        StyledText {
                            width: parent.width
                            text: "Solid color + brightness (0 = off, 100 = max). Enter a hex color like FF0000."
                            font.pixelSize: 12
                            color: Theme.surfaceVariantText
                            wrapMode: Text.WordWrap
                        }
                    }

                    // ── Power ──────────────────────────────────────────────
                    Column {
                        visible: root.section === 3
                        width: parent.width
                        spacing: 12

                        Row {
                            width: parent.width
                            StyledText {
                                text: "Presets"
                                font.pixelSize: 11
                                font.weight: Font.DemiBold
                                color: Theme.surfaceVariantText
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            Rectangle {
                                height: 1
                                width: parent.width - 70
                                anchors.verticalCenter: parent.verticalCenter
                                color: Theme.outlineLight
                            }
                        }

                        Column {
                            width: parent.width
                            spacing: 6
                            Repeater {
                                model: root.presets
                                delegate: Rectangle {
                                    required property var modelData
                                    readonly property bool isSel: root.activePreset === modelData.id
                                    width: parent.width
                                    height: 58
                                    radius: 10
                                    color: isSel ? Theme.primaryContainer : Theme.surfaceContainer
                                    border.color: isSel ? Theme.withAlpha(Theme.primary, 0.5) : Theme.outline
                                    border.width: 1

                                    Row {
                                        anchors.left: parent.left
                                        anchors.right: parent.right
                                        anchors.leftMargin: 12
                                        anchors.rightMargin: 12
                                        anchors.verticalCenter: parent.verticalCenter
                                        spacing: 10

                                        Column {
                                            width: parent.width - 40
                                            spacing: 2
                                            StyledText {
                                                text: modelData.name
                                                font.pixelSize: 14
                                                font.weight: Font.DemiBold
                                                color: isSel ? Theme.primary : Theme.surfaceText
                                            }
                                            StyledText {
                                                text: modelData.desc
                                                font.pixelSize: 11
                                                color: Theme.surfaceVariantText
                                                elide: Text.ElideRight
                                            }
                                        }

                                        Rectangle {
                                            width: 20
                                            height: 20
                                            radius: 10
                                            color: isSel ? Theme.primary : Theme.withAlpha(Theme.onSurface, 0.08)
                                            anchors.verticalCenter: parent.verticalCenter
                                            DankIcon {
                                                anchors.centerIn: parent
                                                name: "check"
                                                size: 12
                                                color: Theme.onPrimary
                                                opacity: isSel ? 1 : 0
                                            }
                                        }
                                    }

                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: root.applyPreset(modelData.id)
                                    }
                                }
                            }
                        }

                        Row {
                            width: parent.width
                            StyledText {
                                text: "Custom limits"
                                font.pixelSize: 11
                                font.weight: Font.DemiBold
                                color: Theme.surfaceVariantText
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            Rectangle {
                                height: 1
                                width: parent.width - 100
                                anchors.verticalCenter: parent.verticalCenter
                                color: Theme.outlineLight
                            }
                        }

                        Column {
                            width: parent.width
                            spacing: 14

                            Column {
                                width: parent.width
                                spacing: 4
                                Row {
                                    width: parent.width
                                    StyledText { text: "PL1 — sustained"; font.pixelSize: 13; color: Theme.surfaceText }
                                    Item { width: parent.width - pl1Val.width - 80 - 12; height: 1 }
                                    Rectangle {
                                        id: pl1Val
                                        height: 18
                                        radius: 9
                                        color: Theme.withAlpha(Theme.onSurface, 0.06)
                                        StyledText {
                                            anchors.centerIn: parent
                                            leftPadding: 7
                                            rightPadding: 7
                                            text: (root.state.cpu_pl1 ?? 0) + " W"
                                            isMonospace: true
                                            font.pixelSize: 12
                                            color: Theme.surfaceText
                                        }
                                    }
                                }
                                DankSlider {
                                    width: parent.width
                                    minimum: 35
                                    maximum: 125
                                    step: 5
                                    value: root.state.cpu_pl1 ?? 75
                                    unit: "W"
                                    onSliderDragFinished: v => root.setPowerLimit("cpu_pl1", v)
                                }
                            }

                            Column {
                                width: parent.width
                                spacing: 4
                                Row {
                                    width: parent.width
                                    StyledText { text: "PL2 — boost"; font.pixelSize: 13; color: Theme.surfaceText }
                                    Item { width: parent.width - pl2Val.width - 80 - 12; height: 1 }
                                    Rectangle {
                                        id: pl2Val
                                        height: 18
                                        radius: 9
                                        color: Theme.withAlpha(Theme.onSurface, 0.06)
                                        StyledText {
                                            anchors.centerIn: parent
                                            leftPadding: 7
                                            rightPadding: 7
                                            text: (root.state.cpu_pl2 ?? 0) + " W"
                                            isMonospace: true
                                            font.pixelSize: 12
                                            color: Theme.surfaceText
                                        }
                                    }
                                }
                                DankSlider {
                                    width: parent.width
                                    minimum: 45
                                    maximum: 125
                                    step: 5
                                    value: root.state.cpu_pl2 ?? 90
                                    unit: "W"
                                    onSliderDragFinished: v => root.setPowerLimit("cpu_pl2", v)
                                }
                            }

                            Column {
                                width: parent.width
                                spacing: 4
                                Row {
                                    width: parent.width
                                    StyledText { text: "PL4 — peak"; font.pixelSize: 13; color: Theme.surfaceText }
                                    Item { width: parent.width - pl4Val.width - 80 - 12; height: 1 }
                                    Rectangle {
                                        id: pl4Val
                                        height: 18
                                        radius: 9
                                        color: Theme.withAlpha(Theme.onSurface, 0.06)
                                        StyledText {
                                            anchors.centerIn: parent
                                            leftPadding: 7
                                            rightPadding: 7
                                            text: (root.state.cpu_pl4 ?? 0) + " W"
                                            isMonospace: true
                                            font.pixelSize: 12
                                            color: Theme.surfaceText
                                        }
                                    }
                                }
                                DankSlider {
                                    width: parent.width
                                    minimum: 90
                                    maximum: 250
                                    step: 2
                                    value: root.state.cpu_pl4 ?? 150
                                    unit: "W"
                                    onSliderDragFinished: v => root.setPowerLimit("cpu_pl4", v)
                                }
                            }

                            Column {
                                width: parent.width
                                spacing: 4
                                Row {
                                    width: parent.width
                                    StyledText { text: "GPU offset"; font.pixelSize: 13; color: Theme.surfaceText }
                                    Item { width: parent.width - gpuVal.width - 80 - 12; height: 1 }
                                    Rectangle {
                                        id: gpuVal
                                        height: 18
                                        radius: 9
                                        color: Theme.withAlpha(Theme.onSurface, 0.06)
                                        StyledText {
                                            anchors.centerIn: parent
                                            leftPadding: 7
                                            rightPadding: 7
                                            text: (root.state.gpu_ctgp_offset ?? 0) + " W"
                                            isMonospace: true
                                            font.pixelSize: 12
                                            color: Theme.surfaceText
                                        }
                                    }
                                }
                                DankSlider {
                                    width: parent.width
                                    minimum: 0
                                    maximum: 25
                                    step: 5
                                    value: root.state.gpu_ctgp_offset ?? 0
                                    unit: "W"
                                    onSliderDragFinished: v => root.setPowerLimit("gpu_ctgp_offset", v)
                                }
                            }
                        }

                        StyledText {
                            width: parent.width
                            text: "Every preset arms the custom-profile latch — the profile LED beside the power button stays white. PL4 must be even; odd watts round down."
                            font.pixelSize: 12
                            color: Theme.surfaceVariantText
                            wrapMode: Text.WordWrap
                        }
                    }

                    // ── Charge ─────────────────────────────────────────────
                    Column {
                        visible: root.section === 4
                        width: parent.width
                        spacing: 12

                        Column {
                            width: parent.width
                            spacing: 4
                            StyledText { text: "Charge profile"; font.pixelSize: 13; color: Theme.surfaceText }
                            DankDropdown {
                                width: parent.width
                                dropdownWidth: parent.width
                                compactMode: true
                                options: root.chargeProfiles.map(p => p.label)
                                currentValue: {
                                    const cur = root.state.charge_profile || "stationary";
                                    const p = root.chargeProfiles.find(x => x.id === cur);
                                    return p ? p.label : cur;
                                }
                                onValueChanged: v => {
                                    const p = root.chargeProfiles.find(x => x.label === v);
                                    if (p) root.setChargeProfile(p.id);
                                }
                            }
                            StyledText {
                                width: parent.width
                                text: {
                                    const cur = root.state.charge_profile || "stationary";
                                    const p = root.chargeProfiles.find(x => x.id === cur);
                                    return p ? p.desc : "";
                                }
                                font.pixelSize: 11
                                color: Theme.surfaceVariantText
                                wrapMode: Text.WordWrap
                            }
                        }

                        Column {
                            width: parent.width
                            spacing: 4
                            Row {
                                width: parent.width
                                StyledText { text: "Charge threshold"; font.pixelSize: 13; color: Theme.surfaceText }
                                Item { width: parent.width - thrVal.width - 80 - 12; height: 1 }
                                Rectangle {
                                    id: thrVal
                                    height: 18
                                    radius: 9
                                    color: Theme.withAlpha(Theme.onSurface, 0.06)
                                    StyledText {
                                        anchors.centerIn: parent
                                        leftPadding: 7
                                        rightPadding: 7
                                        text: (root.state.charge_threshold ?? 100) + "%"
                                        isMonospace: true
                                        font.pixelSize: 12
                                        color: Theme.surfaceText
                                    }
                                }
                            }
                            DankSlider {
                                width: parent.width
                                minimum: 50
                                maximum: 100
                                step: 5
                                value: root.state.charge_threshold ?? 100
                                unit: "%"
                                onSliderDragFinished: v => root.setChargeThreshold(v)
                            }
                            StyledText {
                                width: parent.width
                                text: "Note: the threshold does not hold reliably on this chassis — it stops charging briefly, then resumes. Do not treat it as a hard cap."
                                font.pixelSize: 11
                                color: Theme.surfaceVariantText
                                wrapMode: Text.WordWrap
                            }
                        }

                        Row {
                            width: parent.width
                            StyledText {
                                text: "Platform toggles"
                                font.pixelSize: 11
                                font.weight: Font.DemiBold
                                color: Theme.surfaceVariantText
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            Rectangle {
                                height: 1
                                width: parent.width - 120
                                anchors.verticalCenter: parent.verticalCenter
                                color: Theme.outlineLight
                            }
                        }

                        Column {
                            width: parent.width
                            spacing: 2
                            Repeater {
                                model: root.toggleDefs
                                delegate: Rectangle {
                                    required property var modelData
                                    width: parent.width
                                    height: 44
                                    radius: 9
                                    color: Theme.surfaceContainer
                                    border.color: Theme.outline
                                    border.width: 1

                                    Row {
                                        anchors.left: parent.left
                                        anchors.right: parent.right
                                        anchors.leftMargin: 12
                                        anchors.rightMargin: 12
                                        anchors.verticalCenter: parent.verticalCenter
                                        spacing: 10

                                        Column {
                                            width: parent.width - 60
                                            spacing: 1
                                            StyledText {
                                                text: modelData.label
                                                font.pixelSize: 13
                                                font.weight: Font.DemiBold
                                                color: Theme.surfaceText
                                            }
                                            StyledText {
                                                text: modelData.desc
                                                font.pixelSize: 11
                                                color: Theme.surfaceVariantText
                                                elide: Text.ElideRight
                                            }
                                        }

                                        DankToggle {
                                            anchors.verticalCenter: parent.verticalCenter
                                            checked: !!root.state[modelData.key]
                                            onToggled: v => root.setToggle(modelData.key, v)
                                        }
                                    }
                                }
                            }
                        }

                        StyledText {
                            width: parent.width
                            text: "AC auto boot and USB power share are mutually exclusive — the driver refuses both at once."
                            font.pixelSize: 12
                            color: Theme.surfaceVariantText
                            wrapMode: Text.WordWrap
                        }
                    }

                    // ── System ────────────────────────────────────────────
                    Column {
                        visible: root.section === 5
                        width: parent.width
                        spacing: 12

                        Row {
                            width: parent.width
                            StyledText {
                                text: "Live telemetry"
                                font.pixelSize: 11
                                font.weight: Font.DemiBold
                                color: Theme.surfaceVariantText
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            Rectangle {
                                height: 1
                                width: parent.width - 110
                                anchors.verticalCenter: parent.verticalCenter
                                color: Theme.outlineLight
                            }
                        }

                        Grid {
                            width: parent.width
                            columns: 2
                            columnSpacing: 8
                            rowSpacing: 8

                            Rectangle {
                                width: (parent.width - 8) / 2
                                height: 64
                                radius: 10
                                color: Theme.surfaceContainer
                                border.color: Theme.outline
                                border.width: 1
                                Column {
                                    anchors.centerIn: parent
                                    spacing: 2
                                    StyledText {
                                        text: "CPU"
                                        font.pixelSize: 10
                                        font.weight: Font.DemiBold
                                        color: Theme.surfaceVariantText
                                        horizontalAlignment: Text.AlignHCenter
                                    }
                                    StyledText {
                                        text: (root.telemetry.cpu_temp_c ?? "—") + "°C"
                                        font.pixelSize: 18
                                        font.weight: Font.DemiBold
                                        color: Theme.surfaceText
                                        horizontalAlignment: Text.AlignHCenter
                                    }
                                }
                            }
                            Rectangle {
                                width: (parent.width - 8) / 2
                                height: 64
                                radius: 10
                                color: Theme.surfaceContainer
                                border.color: Theme.outline
                                border.width: 1
                                Column {
                                    anchors.centerIn: parent
                                    spacing: 2
                                    StyledText {
                                        text: "GPU"
                                        font.pixelSize: 10
                                        font.weight: Font.DemiBold
                                        color: Theme.surfaceVariantText
                                        horizontalAlignment: Text.AlignHCenter
                                    }
                                    StyledText {
                                        text: (root.telemetry.gpu_temp_c ?? "—") + "°C"
                                        font.pixelSize: 18
                                        font.weight: Font.DemiBold
                                        color: Theme.surfaceText
                                        horizontalAlignment: Text.AlignHCenter
                                    }
                                }
                            }
                            Rectangle {
                                width: (parent.width - 8) / 2
                                height: 64
                                radius: 10
                                color: Theme.surfaceContainer
                                border.color: Theme.outline
                                border.width: 1
                                Column {
                                    anchors.centerIn: parent
                                    spacing: 2
                                    StyledText {
                                        text: "Package"
                                        font.pixelSize: 10
                                        font.weight: Font.DemiBold
                                        color: Theme.surfaceVariantText
                                        horizontalAlignment: Text.AlignHCenter
                                    }
                                    StyledText {
                                        text: (root.telemetry.package_w ?? "—") + " W"
                                        font.pixelSize: 18
                                        font.weight: Font.DemiBold
                                        color: Theme.surfaceText
                                        horizontalAlignment: Text.AlignHCenter
                                    }
                                }
                            }
                            Rectangle {
                                width: (parent.width - 8) / 2
                                height: 64
                                radius: 10
                                color: Theme.surfaceContainer
                                border.color: Theme.outline
                                border.width: 1
                                Column {
                                    anchors.centerIn: parent
                                    spacing: 2
                                    StyledText {
                                        text: "Battery"
                                        font.pixelSize: 10
                                        font.weight: Font.DemiBold
                                        color: Theme.surfaceVariantText
                                        horizontalAlignment: Text.AlignHCenter
                                    }
                                    StyledText {
                                        text: (root.telemetry.capacity_pct ?? "—") + "%"
                                        font.pixelSize: 18
                                        font.weight: Font.DemiBold
                                        color: Theme.surfaceText
                                        horizontalAlignment: Text.AlignHCenter
                                    }
                                }
                            }
                        }

                        Column {
                            width: parent.width
                            spacing: 4
                            Row {
                                width: parent.width
                                StyledText { text: "Fans"; font.pixelSize: 13; color: Theme.surfaceText }
                                Item { width: parent.width - 120; height: 1 }
                                StyledText {
                                    text: (root.telemetry.fan1_rpm ?? 0) + " / " + (root.telemetry.fan2_rpm ?? 0) + " rpm"
                                    isMonospace: true
                                    font.pixelSize: 12
                                    color: Theme.surfaceVariantText
                                }
                            }
                            Row {
                                width: parent.width
                                StyledText { text: "Charge"; font.pixelSize: 13; color: Theme.surfaceText }
                                Item { width: parent.width - 120; height: 1 }
                                StyledText {
                                    text: (root.telemetry.status || "—") + " · " + (root.telemetry.volts_per_cell ?? "—") + " V/cell"
                                    isMonospace: true
                                    font.pixelSize: 12
                                    color: Theme.surfaceVariantText
                                }
                            }
                            Row {
                                width: parent.width
                                StyledText { text: "Cycles"; font.pixelSize: 13; color: Theme.surfaceText }
                                Item { width: parent.width - 120; height: 1 }
                                StyledText {
                                    text: (root.state.charge_cycles ?? "—")
                                    isMonospace: true
                                    font.pixelSize: 12
                                    color: Theme.surfaceVariantText
                                }
                            }
                        }

                        Row {
                            width: parent.width
                            StyledText {
                                text: "Health"
                                font.pixelSize: 11
                                font.weight: Font.DemiBold
                                color: Theme.surfaceVariantText
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            Rectangle {
                                height: 1
                                width: parent.width - 60
                                anchors.verticalCenter: parent.verticalCenter
                                color: Theme.outlineLight
                            }
                        }

                        Column {
                            width: parent.width
                            spacing: 4
                            Repeater {
                                model: root.health.checks || []
                                delegate: Rectangle {
                                    required property var modelData
                                    width: parent.width
                                    height: 34
                                    radius: 8
                                    color: modelData.ok ? Theme.withAlpha(Theme.success, 0.08)
                                        : Theme.withAlpha(Theme.error, 0.08)
                                    border.color: modelData.ok ? Theme.withAlpha(Theme.success, 0.3)
                                        : Theme.withAlpha(Theme.error, 0.3)
                                    border.width: 1

                                    Row {
                                        anchors.left: parent.left
                                        anchors.right: parent.right
                                        anchors.leftMargin: 10
                                        anchors.rightMargin: 10
                                        anchors.verticalCenter: parent.verticalCenter
                                        spacing: 8
                                        DankIcon {
                                            name: modelData.ok ? "check_circle" : "error"
                                            size: 15
                                            color: modelData.ok ? Theme.success : Theme.error
                                            anchors.verticalCenter: parent.verticalCenter
                                        }
                                        StyledText {
                                            text: modelData.name
                                            font.pixelSize: 12
                                            font.weight: Font.DemiBold
                                            color: Theme.surfaceText
                                            anchors.verticalCenter: parent.verticalCenter
                                        }
                                        Item { width: parent.width - 200; height: 1 }
                                        StyledText {
                                            text: modelData.ok ? "ok" : (modelData.error || "failed")
                                            font.pixelSize: 11
                                            color: modelData.ok ? Theme.success : Theme.error
                                            elide: Text.ElideRight
                                            anchors.verticalCenter: parent.verticalCenter
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
