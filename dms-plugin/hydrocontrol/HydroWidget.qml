import QtQuick
import Quickshell
import Quickshell.Io
import qs.Common
import qs.Modules.Plugins
import qs.DCommon.Widgets as DC

// HydroControl in the bar: a thin client of hydroc-server, the root daemon on
// 127.0.0.1:8781. Nothing here touches hardware; every change is a request the
// daemon validates exactly as it does for the full app.
//
// The daemon shares one EC read between all clients (DESIGN.md 4.2: sustained
// EC traffic has stopped the fans), so the bar polls slowly and the flyout at
// 1 Hz only while open. Changes carry the token from /run/hydroc/token, which
// the daemon hands to the user at the keyboard; without it this is read-only.
PluginComponent {
    id: root
    layerNamespacePlugin: "hydrocontrol"

    readonly property string baseUrl: "http://127.0.0.1:8781"
    readonly property string pillMetric: pluginData.pillMetric || "cpu"
    readonly property int pillInterval: parseInt(pluginData.pillInterval || "5000")

    // ── what the daemon last told us ──────────────────────────────────────
    property bool online: false
    property bool tokenRefused: false
    readonly property bool readOnly: token.length === 0 || tokenRefused
    property var tel: ({})
    property var hw: ({})            // /api/state .state
    property var presets: ({})
    property var saver: ({})
    property var fan: ({})
    property var gpu: ({})
    property var pkgHist: []
    property string message: ""
    property bool messageBad: false
    property bool busy: false

    readonly property string token: (tokenFile.loaded ? tokenFile.text() : "").trim()

    FileView {
        id: tokenFile
        path: "/run/hydroc/token"
        watchChanges: true
        printErrors: false
        onFileChanged: reload()
    }

    // ── HTTP ──────────────────────────────────────────────────────────────
    function request(method, path, body, done) {
        const xhr = new XMLHttpRequest();
        xhr.onreadystatechange = function () {
            // A reply can land after DMS has torn this instance down (a plugin
            // reload, a bar rebuild); by then `root` is gone.
            if (xhr.readyState !== XMLHttpRequest.DONE || !root)
                return;
            let data = null;
            try {
                data = xhr.responseText ? JSON.parse(xhr.responseText) : null;
            } catch (e) {
                data = null;
            }
            done(xhr.status, data);
        };
        xhr.open(method, baseUrl + path);
        if (method !== "GET") {
            xhr.setRequestHeader("Content-Type", "application/json");
            xhr.setRequestHeader("X-HydroControl-Token", token);
        }
        xhr.send(body === undefined ? null : JSON.stringify(body));
    }

    function get(path, done) {
        request("GET", path, undefined, (status, data) => {
            root.online = status === 200;
            if (status === 200 && data)
                done(data);
        });
    }

    // A refused token is re-read once: the daemon re-issues it when the user
    // at the keyboard changes, and the file watcher may not have caught up.
    function post(path, body, retried) {
        if (!token) {
            say("Read-only — /run/hydroc/token cannot be read", true);
            return;
        }
        busy = true;
        request("POST", path, body, (status, data) => {
            busy = false;
            if (status === 403 && data && data.token === false && !retried) {
                tokenFile.reload();
                Qt.callLater(() => root.post(path, body, true));
                return;
            }
            if (status === 0) {
                root.online = false;
                say("hydroc-server is not running", true);
            } else if (status === 403 && data && data.token === false) {
                root.tokenRefused = true;
                say("Refused — this session's token is not valid", true);
            } else if (data && data.ok === false) {
                const f = (data.failed || [])[0];
                say(f ? `${f.setting}: ${f.error}` : (data.error || "refused"), true);
            } else {
                root.tokenRefused = false;
                say("", false);
            }
            if (data && data.state)
                root.hw = data.state;
            root.refreshAll();
        });
    }

    function say(text, bad) {
        message = text;
        messageBad = bad;
    }

    function apply(settings) {
        post("/api/apply", { settings: settings, persist: true });
    }

    function refreshTelemetry() {
        get("/api/telemetry", d => {
            root.tel = d;
            if (typeof d.package_w === "number") {
                const h = root.pkgHist.slice(-59);
                h.push(d.package_w);
                root.pkgHist = h;
            }
        });
    }

    function refreshAll() {
        get("/api/state", d => root.hw = d.state || {});
        get("/api/presets", d => root.presets = d);
        get("/api/power-saver", d => root.saver = d);
        get("/api/fan", d => root.fan = d);
        get("/api/gpu", d => root.gpu = d);
    }

    // ── derived ───────────────────────────────────────────────────────────
    readonly property var chargeModes: [
        { id: "stationary", name: "Stationary" },
        { id: "balanced", name: "Balanced" },
        { id: "long_haul", name: "Long Haul" }
    ]
    readonly property var presetList: presets.presets || []
    readonly property var nativeList: presets.native || []
    readonly property string activeName: {
        if (presets.native_active) {
            const n = nativeList.find(m => m.id === presets.native_active);
            return n ? n.name : presets.native_active;
        }
        const p = presetList.find(m => m.id === presets.active);
        return p ? p.name : "Custom";
    }
    readonly property int presetIndex: presets.native_active ? -1
        : presetList.findIndex(m => m.id === presets.active)
    readonly property int nativeIndex: nativeList.findIndex(m => m.id === presets.native_active)
    readonly property int chargeIndex: chargeModes.findIndex(m => m.id === hw.charge_profile)
    readonly property int saverIndex: ["auto", "on", "off"].indexOf(saver.mode)
    readonly property var curveNames: Object.keys(fan.presets || {})
    readonly property int curveIndex: {
        const cur = JSON.stringify(hw.fan_curve_cpu || null);
        return curveNames.findIndex(k => JSON.stringify(fan.presets[k].cpu) === cur);
    }
    // CPU temperature limit: entered as the temperature, stored and written as
    // the offset below TjMax (EC 0x0786; TCC-OFFSET-TESTS.md).
    readonly property int tjmax: hw.cpu_tjmax || 100
    readonly property int tccRange: 40
    readonly property bool tccKnown: hw.cpu_tcc_enable !== undefined
    readonly property int tccSet: tjmax - (hw.cpu_tcc_enable ? (hw.cpu_tcc_offset || 0)
                                                               : (hw.cpu_tcc_applied || 0))
    readonly property bool tccApplied: typeof hw.cpu_tcc_applied === "number"
    readonly property real chartMax: Math.max((hw.cpu_pl1 || 0) * 1.25,
                                              ...pkgHist, 20)

    function cap(s) {
        return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
    }
    function num(v, digits) {
        return typeof v === "number" ? v.toFixed(digits || 0) : "—";
    }
    function tempColor(c) {
        if (typeof c !== "number")
            return Theme.surfaceVariantText;
        return c > 88 ? Theme.error : c > 78 ? Theme.warning : Theme.surfaceText;
    }

    readonly property string pillText: {
        if (!online)
            return "off";
        const t = num(tel.cpu_temp_c) + "°";
        const w = num(tel.package_w) + " W";
        return pillMetric === "power" ? w : pillMetric === "both" ? t + " · " + w : t;
    }

    // ── polling ───────────────────────────────────────────────────────────
    Timer {
        interval: root.interactionActive ? 1000 : root.pillInterval
        repeat: true
        running: true
        triggeredOnStart: true
        onTriggered: root.refreshTelemetry()
    }

    Timer {
        interval: 15000
        repeat: true
        running: root.interactionActive
        onTriggered: root.refreshAll()
    }

    onInteractionActiveChanged: {
        if (interactionActive) {
            say("", false);
            refreshAll();
        }
    }

    Component.onCompleted: refreshAll()

    // ── bar ───────────────────────────────────────────────────────────────
    horizontalBarPill: Component {
        Row {
            spacing: Theme.spacingXS
            anchors.verticalCenter: parent.verticalCenter

            DC.DIcon {
                name: "speed"
                size: root.iconSize
                color: root.online ? root.tempColor(root.tel.cpu_temp_c) : Theme.surfaceVariantText
                anchors.verticalCenter: parent.verticalCenter
            }

            DC.StyledText {
                text: root.pillText
                font.pixelSize: root.textSize
                font.features: { "tnum": 1 }
                color: root.online ? root.tempColor(root.tel.cpu_temp_c) : Theme.surfaceVariantText
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }

    verticalBarPill: Component {
        Column {
            spacing: 2
            anchors.horizontalCenter: parent.horizontalCenter

            DC.DIcon {
                name: "speed"
                size: root.iconSize
                color: root.online ? root.tempColor(root.tel.cpu_temp_c) : Theme.surfaceVariantText
                anchors.horizontalCenter: parent.horizontalCenter
            }

            DC.StyledText {
                text: root.online ? root.num(root.tel.cpu_temp_c) + "°" : "off"
                font.pixelSize: Theme.fontSizeSmall
                color: root.online ? root.tempColor(root.tel.cpu_temp_c) : Theme.surfaceVariantText
                anchors.horizontalCenter: parent.horizontalCenter
            }
        }
    }

    // ── flyout ────────────────────────────────────────────────────────────
    popoutWidth: 440

    component Card: DC.StyledRect {
        id: card
        default property alias content: inner.data
        property string title: ""
        property string note: ""
        width: parent ? parent.width : 0
        height: inner.implicitHeight + Theme.spacingM * 2
        radius: Theme.cornerRadius
        color: Theme.surfaceContainerHigh

        Column {
            id: inner
            x: Theme.spacingM
            y: Theme.spacingM
            width: parent.width - Theme.spacingM * 2
            spacing: Theme.spacingS

            Item {
                width: parent.width
                height: titleText.implicitHeight
                visible: card.title.length > 0

                DC.StyledText {
                    id: titleText
                    text: card.title
                    font.pixelSize: Theme.fontSizeSmall
                    font.weight: Font.Medium
                    color: Theme.surfaceVariantText
                }

                DC.StyledText {
                    anchors.right: parent.right
                    text: card.note
                    font.pixelSize: Theme.fontSizeSmall
                    font.features: { "tnum": 1 }
                    color: Theme.surfaceVariantText
                }
            }
        }
    }

    component Reading: Column {
        id: rd
        property string label: ""
        property string value: "—"
        property string unit: ""
        property color valueColor: Theme.surfaceText
        spacing: 0

        Row {
            spacing: 2
            DC.StyledText {
                id: valueText
                text: rd.value
                font.pixelSize: Theme.fontSizeLarge + 6
                font.features: { "tnum": 1 }
                color: rd.valueColor
            }
            DC.StyledText {
                text: rd.unit
                font.pixelSize: Theme.fontSizeSmall
                color: Theme.surfaceVariantText
                anchors.baseline: valueText.baseline
            }
        }
        DC.StyledText {
            text: rd.label
            font.pixelSize: Theme.fontSizeSmall
            color: Theme.surfaceVariantText
        }
    }

    popoutContent: Component {
        PopoutComponent {
            id: pop
            headerText: "HydroControl"
            detailsText: !root.online ? "hydroc-server is not running"
                : root.readOnly ? "Read-only — /run/hydroc/token cannot be read"
                : root.activeName + (root.hw.custom_profile ? "" : " · custom profile off")

            headerActions: Component {
                DC.DActionButton {
                    iconName: "open_in_new"
                    tooltipText: "Open HydroControl"
                    onClicked: {
                        Quickshell.execDetached(["hydrocontrol"]);
                        if (pop.closePopout)
                            pop.closePopout();
                    }
                }
            }

            // The popout takes its height from its content, uncapped, so a
            // long flyout ran off the bottom of the screen. The body is as tall
            // as its content but never taller than the screen leaves room for,
            // and scrolls past that with DMS's own scrollbar.
            DC.DFlickable {
                id: body
                width: parent.width
                readonly property real room: (root.parentScreen ? root.parentScreen.height : 900)
                                             - root.barThickness - pop.headerHeight
                                             - pop.detailsHeight - Theme.spacingL * 4
                height: Math.min(content.implicitHeight, Math.max(240, room))
                contentWidth: width
                contentHeight: content.implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds

            Column {
                id: content
                width: body.width
                spacing: Theme.spacingS
                opacity: root.online ? 1 : 0.5
                enabled: root.online

                // ── live ──────────────────────────────────────────────────
                Card {
                    title: "Live"
                    note: root.online ? `PL1 ${root.num(root.hw.cpu_pl1)} W` : ""

                    Row {
                        width: parent.width
                        Reading {
                            width: parent.width / 3
                            label: "CPU"
                            value: root.num(root.tel.cpu_temp_c)
                            unit: "°C"
                            valueColor: root.tempColor(root.tel.cpu_temp_c)
                        }
                        Reading {
                            width: parent.width / 3
                            label: "GPU"
                            value: root.num(root.tel.gpu_temp_c)
                            unit: "°C"
                            valueColor: root.tempColor(root.tel.gpu_temp_c)
                        }
                        Reading {
                            width: parent.width / 3
                            label: "Package"
                            value: root.num(root.tel.package_w, 1)
                            unit: "W"
                        }
                    }

                    // The trace is the flyout's one moment of colour.
                    DC.DSparkline {
                        width: parent.width
                        height: 56
                        values: root.pkgHist
                        historyLength: 60
                        minimum: 0
                        maximum: root.chartMax
                        lineColor: Theme.primary
                    }

                    DC.StyledText {
                        width: parent.width
                        text: `Fans  ${root.num(root.tel.fan1_rpm)} · ${root.num(root.tel.fan2_rpm)} rpm`
                        font.pixelSize: Theme.fontSizeSmall
                        font.features: { "tnum": 1 }
                        color: Theme.surfaceVariantText
                    }
                }

                // ── mode ──────────────────────────────────────────────────
                Card {
                    title: "Power mode"

                    DC.DButtonGroup {
                        width: parent.width
                        model: root.presetList.map(p => p.name)
                        currentIndex: root.presetIndex
                        enabled: !root.readOnly && !root.busy
                        onSelectionChanged: (index, selected) => {
                            if (selected && index !== root.presetIndex)
                                root.post("/api/preset", { preset: root.presetList[index].id });
                        }
                    }

                    DC.StyledText {
                        text: "The machine's own modes"
                        font.pixelSize: Theme.fontSizeSmall
                        color: Theme.surfaceVariantText
                    }

                    DC.DButtonGroup {
                        width: parent.width
                        size: "small"
                        model: root.nativeList.map(p => p.name)
                        currentIndex: root.nativeIndex
                        enabled: !root.readOnly && !root.busy
                        onSelectionChanged: (index, selected) => {
                            if (selected && index !== root.nativeIndex)
                                root.post("/api/native", { mode: root.nativeList[index].id });
                        }
                    }
                }

                // ── CPU temperature limit ─────────────────────────────────
                // Written only on Set or Enter. The app's first version applied
                // on blur and on its spinner, and fought the user mid-entry; the
                // field here is only refreshed from the hardware while it does
                // not have focus.
                Card {
                    id: tccCard
                    title: "CPU temperature limit"
                    note: root.tccApplied ? `In force ${root.tjmax - root.hw.cpu_tcc_applied} °C` : ""
                    visible: root.tccKnown
                    property string error: ""

                    function set() {
                        const raw = tccField.text.trim(), t = Number(raw), lo = root.tjmax - root.tccRange;
                        if (!/^\d+$/.test(raw) || t < lo || t > root.tjmax) {
                            error = `Enter a whole number from ${lo} to ${root.tjmax} °C`;
                            return;
                        }
                        error = "";
                        if (root.tjmax - t !== root.hw.cpu_tcc_offset)
                            root.apply({ cpu_tcc_enable: true, cpu_tcc_offset: root.tjmax - t });
                    }

                    DC.DToggle {
                        width: parent.width
                        text: root.hw.cpu_tcc_enable ? "Throttle at a set temperature" : "Firmware default"
                        checked: !!root.hw.cpu_tcc_enable
                        enabled: !root.readOnly && !root.busy
                        // Turning it on starts from what the CPU already runs,
                        // so the switch alone changes nothing it does.
                        onToggled: on => {
                            tccCard.error = "";
                            root.apply(on ? { cpu_tcc_enable: true,
                                              cpu_tcc_offset: Math.min(root.tccRange, root.hw.cpu_tcc_applied ?? 5) }
                                          : { cpu_tcc_enable: false });
                        }
                    }

                    Row {
                        width: parent.width
                        spacing: Theme.spacingS
                        visible: !!root.hw.cpu_tcc_enable

                        DC.StyledText {
                            id: tccLabel
                            anchors.verticalCenter: parent.verticalCenter
                            text: "Throttle at"
                            color: Theme.surfaceVariantText
                        }

                        DC.DTextField {
                            id: tccField
                            anchors.verticalCenter: parent.verticalCenter
                            width: parent.width - tccLabel.width - tccUnit.width - tccSetButton.width - Theme.spacingS * 3
                            maximumLength: 3
                            isError: tccCard.error.length > 0
                            enabled: !root.readOnly
                            validator: IntValidator { bottom: 0; top: 999 }
                            onAccepted: tccCard.set()
                            onTextEdited: tccCard.error = ""
                            Component.onCompleted: text = String(root.tccSet)

                            Connections {
                                target: root
                                function onHwChanged() {
                                    if (!tccField.inputFocused)
                                        tccField.text = String(root.tccSet);
                                }
                            }
                        }

                        DC.StyledText {
                            id: tccUnit
                            anchors.verticalCenter: parent.verticalCenter
                            text: "°C"
                            color: Theme.surfaceVariantText
                        }

                        DC.DButton {
                            id: tccSetButton
                            anchors.verticalCenter: parent.verticalCenter
                            text: "Set"
                            variant: "tonal"
                            enabled: !root.readOnly && !root.busy
                            onClicked: tccCard.set()
                        }
                    }

                    DC.StyledText {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        font.pixelSize: Theme.fontSizeSmall
                        readonly property bool mismatch: !!root.hw.cpu_tcc_enable && root.tccApplied
                                                         && root.hw.cpu_tcc_applied !== root.hw.cpu_tcc_offset
                        color: tccCard.error ? Theme.error : mismatch ? Theme.warning : Theme.surfaceVariantText
                        text: tccCard.error ? tccCard.error
                            : mismatch ? `The CPU is running ${root.tjmax - root.hw.cpu_tcc_applied} °C, not the ${root.tjmax - root.hw.cpu_tcc_offset} °C set.`
                            : root.hw.cpu_tcc_enable ? `${root.tjmax - root.tccRange}–${root.tjmax} °C. An offset of −${root.hw.cpu_tcc_offset || 0} below TjMax ${root.tjmax} °C.`
                            : `The firmware's default for the current mode.`
                    }
                }

                // ── battery ───────────────────────────────────────────────
                Card {
                    title: "Battery"
                    note: root.online ? `${root.num(root.tel.capacity_pct)} % · ${root.tel.status || ""}` : ""

                    DC.DButtonGroup {
                        width: parent.width
                        model: root.chargeModes.map(m => m.name)
                        currentIndex: root.chargeIndex
                        enabled: !root.readOnly && !root.busy
                        onSelectionChanged: (index, selected) => {
                            if (selected && index !== root.chargeIndex)
                                root.apply({ charge_profile: root.chargeModes[index].id });
                        }
                    }

                    Column {
                        width: parent.width
                        spacing: Theme.spacingXS
                        visible: typeof root.hw.charge_threshold === "number"

                        DC.DSlider {
                            id: threshold
                            width: parent.width
                            minimum: 20
                            maximum: 100
                            value: root.hw.charge_threshold || 80
                            unit: "%"
                            startIcon: "battery_charging_full"
                            enabled: !root.readOnly
                            onSliderValueChanged: newValue => thresholdApply.restart()

                            // A drag sets the slider's own value; put the
                            // hardware's back whenever the daemon reports it.
                            Connections {
                                target: root
                                function onHwChanged() {
                                    if (!threshold.isDragging && typeof root.hw.charge_threshold === "number")
                                        threshold.value = root.hw.charge_threshold;
                                }
                            }

                            Timer {
                                id: thresholdApply
                                interval: 700
                                onTriggered: {
                                    if (threshold.value !== root.hw.charge_threshold)
                                        root.apply({ charge_threshold: threshold.value });
                                }
                            }
                        }

                        DC.StyledText {
                            width: parent.width
                            wrapMode: Text.WordWrap
                            text: `Charge limit ${threshold.value} %. Above about 75 it does nothing: a fixed 4.175 V per cell stops charging first.`
                            font.pixelSize: Theme.fontSizeSmall
                            color: Theme.surfaceVariantText
                        }
                    }

                    DC.StyledText {
                        text: "Power Saver" + (root.saver.active ? " · engaged" : "")
                        font.pixelSize: Theme.fontSizeSmall
                        color: Theme.surfaceVariantText
                    }

                    DC.DButtonGroup {
                        width: parent.width
                        size: "small"
                        model: ["Auto", "On", "Off"]
                        currentIndex: root.saverIndex
                        enabled: !root.readOnly && !root.busy
                        onSelectionChanged: (index, selected) => {
                            if (selected && index !== root.saverIndex)
                                root.post("/api/power-saver", { mode: ["auto", "on", "off"][index] });
                        }
                    }
                }

                // ── fans ──────────────────────────────────────────────────
                Card {
                    title: "Fans"

                    DC.DButtonGroup {
                        width: parent.width
                        model: ["Firmware", "Curve"]
                        currentIndex: root.hw.fan_mode === "manual" ? 1 : root.hw.fan_mode === "auto" ? 0 : -1
                        enabled: !root.readOnly && !root.busy && !!root.fan.cpu
                        onSelectionChanged: (index, selected) => {
                            if (!selected)
                                return;
                            if (index === 0 && root.hw.fan_mode !== "auto")
                                root.apply({ fan_mode: "auto" });
                            else if (index === 1 && root.hw.fan_mode !== "manual")
                                root.apply({ fan_mode: "manual", fan_curve_cpu: root.fan.cpu,
                                             fan_curve_gpu: root.fan.gpu });
                        }
                    }

                    DC.DButtonGroup {
                        width: parent.width
                        size: "small"
                        visible: root.hw.fan_mode === "manual" && root.curveNames.length > 0
                        model: root.curveNames.map(k => root.cap(k))
                        currentIndex: root.curveIndex
                        enabled: !root.readOnly && !root.busy
                        onSelectionChanged: (index, selected) => {
                            if (!selected || index === root.curveIndex)
                                return;
                            const c = root.fan.presets[root.curveNames[index]];
                            root.apply({ fan_mode: "manual", fan_curve_cpu: c.cpu, fan_curve_gpu: c.gpu });
                        }
                    }

                    DC.StyledText {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        visible: root.hw.fan_mode === "manual" && root.curveIndex < 0
                        text: "A curve edited in the app is running. Pick one here to replace it."
                        font.pixelSize: Theme.fontSizeSmall
                        color: Theme.surfaceVariantText
                    }
                }

                // ── graphics ──────────────────────────────────────────────
                // Shown, not switched: the MUX is a firmware write that needs a
                // reboot, which does not belong one click away in a panel.
                Card {
                    title: "Graphics"
                    visible: !!root.gpu.supported

                    Item {
                        width: parent.width
                        height: Math.max(gpuText.implicitHeight, gpuButton.height)

                        DC.StyledText {
                            id: gpuText
                            anchors.verticalCenter: parent.verticalCenter
                            width: parent.width - gpuButton.width - Theme.spacingS
                            wrapMode: Text.WordWrap
                            text: (root.gpu.label || root.gpu.mode || "—")
                                  + (root.gpu.reboot_pending ? " · reboot to apply" : "")
                            color: root.gpu.reboot_pending ? Theme.warning : Theme.surfaceText
                        }

                        DC.DButton {
                            id: gpuButton
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            text: "Change…"
                            variant: "tonal"
                            onClicked: {
                                Quickshell.execDetached(["hydrocontrol"]);
                                if (pop.closePopout)
                                    pop.closePopout();
                            }
                        }
                    }
                }

                DC.StyledText {
                    width: parent.width
                    visible: root.message.length > 0
                    wrapMode: Text.WordWrap
                    text: root.message
                    font.pixelSize: Theme.fontSizeSmall
                    color: root.messageBad ? Theme.error : Theme.surfaceVariantText
                }
            }
            }
        }
    }
}
