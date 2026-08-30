import QtQuick
import Quickshell
import qs.Common
import qs.Services
import qs.Widgets
import qs.Modules.Plugins

PluginComponent {
    id: root
    layerNamespacePlugin: "kbctrl"

    readonly property string procId: "kbctrl.cmd" + Math.random().toString(36).slice(2)

    // ── synced state (daemon status) ──────────────────────────────────────
    property bool loading: true
    property string connState: "…"          // "connected" | "no hardware" | "daemon off"
    property bool hwConnected: false
    property int brightness: 25
    property int lbBrightness: 25
    property string lbColorHex: "#FFFFFF"
    property var effect: ({})
    property var keyColors: ({})            // "r,c" -> [r,g,b]
    property var layout: []                 // rows of [label, r, c, w]

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
    property var recent: []                 // [{label, r, c}] most-recent first

    // ── popout section ────────────────────────────────────────────────────
    property int section: 0                 // 0=effects 1=keys 2=lightbar

    // ── static data (mirrors the web prototype's model) ───────────────────
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
    readonly property var effectParams: ({
        "breathing": ["speed", "brightness", "color"],
        "wave": ["speed", "brightness", "color", "direction", "reactive"],
        "random": ["speed", "brightness", "reactive"],
        "rainbow": ["speed", "brightness", "direction"],
        "ripple": ["speed", "brightness", "color", "reactive"],
        "marquee": ["speed", "brightness", "color", "direction"],
        "raindrop": ["speed", "brightness", "color", "direction"],
        "aurora": ["speed", "brightness", "direction"],
        "fireworks": ["speed", "brightness", "reactive"]
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
            return "Contacting kbctrld…";
        if (hwConnected)
            return effectLabel.toLowerCase() + "  ·  " + Math.round(lbBrightness) + "%";
        if (connState === "daemon off")
            return "no actions";
        return effectLabel.toLowerCase();
    }

    // ── plumbing ──────────────────────────────────────────────────────────

    function ctlArgs(args) {
        const py = pluginData.pythonBin || "/usr/bin/python3";
        const pypath = pluginData.pythonPath || "/home/gumwars/kbctrl";
        return ["/usr/bin/env", "PYTHONPATH=" + pypath, py, "-m", "kbctrl.ctl"].concat(args);
    }

    function run(args, cb) {
        Proc.runCommand(procId, ctlArgs(args), (out, code) => {
            if (cb)
                cb(out, code);
        });
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
        run(["status"], (out, code) => {
            loading = false;
            let res = {};
            try { res = JSON.parse(out.trim()); } catch (e) {}
            if (code !== 0 || !res.ok) {
                connState = "daemon off";
                hwConnected = false;
                return;
            }
            hwConnected = res.connected;
            connState = res.connected ? "connected" : "no hardware";
            brightness = res.brightness ?? 25;
            lbBrightness = (res.lightbar && res.lightbar.brightness) ?? 25;
            if (res.lightbar && res.lightbar.color) {
                let c = res.lightbar.color;
                lbColorHex = "#" + c.map(v => v.toString(16).padStart(2, "0")).join("");
            }
            effect = res.effect || {};
            keyColors = res.key_colors || {};
            layout = res.layout || [];
            if (effect.name) {
                effName = effect.name;
                effSpeed = effect.speed ?? 5;
                effBrightness = effect.brightness ?? 25;
                effColorIdx = effect.color_idx ?? 8;
                effDirIdx = effect.dir_idx ?? 1;
                effReactive = effect.reactive ?? false;
            }
        });
    }

    function apply(cfg, msg) {
        run(["apply", JSON.stringify(cfg)], (out, code) => {
            let res = {};
            try { res = JSON.parse(out.trim()); } catch (e) {}
            if (code === 0 && res.ok) {
                if (msg)
                    ToastService.showInfo(msg);
                refresh();
            } else {
                ToastService.showError("kbctrl", res.error || res.status || "apply failed");
            }
        });
    }

    function applyEffect(save) {
        apply({
            effect: {
                name: effName, speed: effSpeed, brightness: effBrightness,
                color_idx: effColorIdx, dir_idx: effDirIdx, reactive: effReactive,
                save: save
            }
        }, save ? effectLabel + " applied & saved to firmware"
                 : effectLabel + " applied to the keyboard");
    }

    function applyKeys(save) {
        apply({ key_colors: keyColors, brightness, flash_save: save },
            save ? "Key colors pushed & saved to firmware" : "Key colors pushed to keyboard");
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
            ToastService.showError("kbctrl", "Invalid lightbar color — use 6 hex digits");
            return;
        }
        apply({ lightbar: { brightness: lbBrightness, color: rgb } },
            lbBrightness === 0 ? "Lightbar off"
                : "Lightbar " + lbColorHex + " at " + lbBrightness);
    }

    function restorePalette() {
        keyColors = {};
        recent = [];
        selKey = "";
        apply({ restore_palette: true }, "Palette reset — all keys unlit");
    }

    // ── per-key helpers ───────────────────────────────────────────────────

    function keyColor(r, c) {
        const v = keyColors[r + "," + c];
        return v ? v : [0, 0, 0];
    }

    function workingRgb(r, c) {
        if (selKey === r + "," + c)
            return [pickR, pickG, pickB];
        return keyColor(r, c);
    }

    function isLit(r, c) {
        const v = keyColor(r, c);
        return !!(v && (v[0] || v[1] || v[2]));
    }

    function labelAt(r, c) {
        for (const row of layout)
            for (const k of row)
                if (k[1] === r && k[2] === c)
                    return k[0];
        return "?";
    }

    function selR() { return selKey ? parseInt(selKey.split(",")[0]) : -1; }
    function selC() { return selKey ? parseInt(selKey.split(",")[1]) : -1; }

    function editingLabel() {
        return selKey ? labelAt(selR(), selC()) : "Select a key to edit — or use Set all keys.";
    }

    function editingCoords() {
        return selKey ? "· row " + selR() + ", col " + selC() : "";
    }

    function setPickers(r, c) {
        selKey = r + "," + c;
        const v = keyColors[selKey];
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
        recent = [{ label: labelAt(r, c), r, c }]
            .concat(recent.filter(it => !(it.r === r && it.c === c))).slice(0, 12);
        ToastService.showInfo(labelAt(r, c) + " key set to " + hexOf([pickR, pickG, pickB]));
    }

    function setAllKeys() {
        const rgb = [pickR, pickG, pickB];
        let all = {};
        for (const row of layout)
            for (const k of row)
                all[k[1] + "," + k[2]] = rgb;
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
            out.push({ label, r, c, w });
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

    Component.onCompleted: refresh()

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
                            text: "Keyboard lighting"
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
                        { label: "Lightbar", icon: "lightbulb" }
                    ]
                    delegate: Rectangle {
                        required property var modelData
                        required property int index
                        readonly property bool isSel: root.section === index
                        width: (parent.width - 32 - 8) / 3
                        height: 32
                        radius: 9
                        color: isSel ? Theme.primaryContainer : "transparent"

                        Row {
                            anchors.centerIn: parent
                            spacing: 7
                            DankIcon {
                                name: modelData.icon
                                size: 16
                                color: isSel ? Theme.primary : Theme.surfaceVariantText
                            }
                            StyledText {
                                text: modelData.label
                                font.pixelSize: 13
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
                                ? "kbctrld is not reachable over the socket. Nothing can be applied until it's running again."
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
                                            width: rowBody.width - 34 - 20 - 24
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
                                            readonly property bool isSel: root.selKey === modelData.r + "," + modelData.c
                                            height: 24
                                            radius: 12
                                            color: Theme.surfaceContainer
                                            border.color: isSel ? Theme.primary : Theme.outline
                                            border.width: isSel ? 1 : 1

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
                                                    color: root.isLit(modelData.r, modelData.c)
                                                        ? Qt.rgba(root.keyColor(modelData.r, modelData.c)[0] / 255, root.keyColor(modelData.r, modelData.c)[1] / 255, root.keyColor(modelData.r, modelData.c)[2] / 255, 1)
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
                                            readonly property string keyId: isGap ? "" : (modelData.r + "," + modelData.c)
                                            readonly property bool isSel: keyId === root.selKey
                                            readonly property bool lit: !isGap && root.isLit(modelData.r, modelData.c)
                                            readonly property var rgb: isGap ? [0, 0, 0] : root.workingRgb(modelData.r, modelData.c)
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
                                            color: root.isLit(root.selR(), root.selC())
                                                ? Qt.rgba(root.keyColor(root.selR(), root.selC())[0] / 255, root.keyColor(root.selR(), root.selC())[1] / 255, root.keyColor(root.selR(), root.selC())[2] / 255, 1)
                                                : Theme.withAlpha(Theme.onSurface, 0.05)
                                            StyledText {
                                                anchors.centerIn: parent
                                                text: root.isLit(root.selR(), root.selC()) ? root.hexOf(root.keyColor(root.selR(), root.selC())) : "—"
                                                isMonospace: true
                                                font.pixelSize: 11
                                                font.weight: Font.DemiBold
                                                color: root.isLit(root.selR(), root.selC())
                                                    ? root.fgColor(root.keyColor(root.selR(), root.selC())[0], root.keyColor(root.selR(), root.selC())[1], root.keyColor(root.selR(), root.selC())[2])
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
                }
            }
        }
    }
}