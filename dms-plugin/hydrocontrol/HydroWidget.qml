import QtQuick
import QtQuick.Layouts
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

    // Built the way DMS builds its own popouts (Modules/DBar/Popouts/
    // BatteryPopoutContent.qml): a hero DCard, a row of grouped stat tiles,
    // then DCards with accent section titles and full-width segmented groups.
    // Nothing of this plugin's own on top of the shell's look.

    function tempRing(c) {
        if (typeof c !== "number")
            return Theme.onSurfaceVariant;
        return c > 88 ? Theme.error : c > 78 ? Theme.warning : Theme.primary;
    }

    // A section: an accent title, an optional note on the right, then content.
    component Section: DC.DCard {
        id: sec
        default property alias body: col.data
        property string heading: ""
        property string note: ""
        width: parent ? parent.width : 0
        height: col.implicitHeight + pad * 2
        restRadius: Theme.cornerRadiusL

        Column {
            id: col
            width: parent.width
            spacing: Theme.spacingS

            Item {
                width: parent.width
                height: headingText.implicitHeight
                visible: sec.heading.length > 0

                DC.StyledText {
                    id: headingText
                    text: sec.heading
                    font.pixelSize: Theme.fontSizeSmall
                    font.weight: Theme.fontWeightMedium
                    color: Theme.primary
                }

                DC.StyledText {
                    anchors.right: parent.right
                    anchors.baseline: headingText.baseline
                    text: sec.note
                    font.pixelSize: Theme.fontSizeSmall
                    font.features: { "tnum": 1 }
                    color: Theme.onSurfaceVariant
                }
            }
        }
    }

    // A small caption under a control.
    component Caption: DC.StyledText {
        width: parent ? parent.width : 0
        wrapMode: Text.WordWrap
        font.pixelSize: Theme.fontSizeSmall
        color: Theme.onSurfaceVariant
    }

    // DMS's StatTile, as BatteryPopoutContent draws it: a small ring with an
    // icon, the figure, the label, corners that join the tiles into a group.
    component StatTile: DC.DCard {
        id: tile
        property string label: ""
        property string value: ""
        property string iconName: ""
        property real ratio: -1
        property color valueColor: Theme.onSurface
        property color ringColor: Theme.primary
        property bool first: false
        property bool last: false
        readonly property real startRadius: first ? Theme.cornerRadiusL : Theme.groupedListInnerRadius
        readonly property real endRadius: last ? Theme.cornerRadiusL : Theme.groupedListInnerRadius
        height: tileColumn.implicitHeight + pad * 2
        restRadius: Theme.groupedListInnerRadius
        topLeftRadius: startRadius
        bottomLeftRadius: startRadius
        topRightRadius: endRadius
        bottomRightRadius: endRadius

        Column {
            id: tileColumn
            width: parent.width
            spacing: Theme.spacingXS

            DC.DRingGauge {
                width: Theme.buttonHeightXS
                height: width
                anchors.horizontalCenter: parent.horizontalCenter
                value: tile.ratio
                strokeWidth: Theme.outlineWidthFocused + Theme.dividerWidth
                trackGap: Theme.spacingXXS
                ringColor: tile.ringColor
                animated: root.interactionActive

                DC.DIcon {
                    anchors.centerIn: parent
                    name: tile.iconName
                    size: Theme.iconSizeSmall
                    color: tile.ringColor
                }
            }

            DC.StyledText {
                width: parent.width
                text: tile.value
                font.pixelSize: Theme.fontSizeMedium
                font.weight: Theme.fontWeightMedium
                font.features: { "tnum": 1 }
                color: tile.valueColor
                horizontalAlignment: Text.AlignHCenter
                elide: Text.ElideRight
            }

            DC.StyledText {
                width: parent.width
                text: tile.label
                font.pixelSize: Theme.fontSizeSmall
                color: Theme.onSurfaceVariant
                horizontalAlignment: Text.AlignHCenter
                elide: Text.ElideRight
            }
        }
    }

    readonly property var presetIcons: ({ office: "eco", balanced: "speed", performance: "bolt" })
    readonly property var nativeIcons: ({ office: "eco", balanced: "speed", beast: "local_fire_department" })

    popoutContent: Component {
        PopoutComponent {
            id: pop
            headerText: ""
            detailsText: ""

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
                spacing: PopoutMetrics.contentGap

                // ── hero: the CPU against its limit, and the power trace ──
                // The flyout's one moment: the ring fills toward TjMax and
                // turns amber above 78 °C and red above 88 °C, the bar pill's
                // rule. The trace under it is the package power, PL1 the scale.
                DC.DCard {
                    width: parent.width
                    height: heroColumn.implicitHeight + pad * 2
                    restRadius: Theme.cornerRadiusL

                    Column {
                        id: heroColumn
                        width: parent.width
                        spacing: Theme.spacingS

                        Row {
                            width: parent.width
                            spacing: Theme.spacingM

                            DC.DRingGauge {
                                id: heroGauge
                                width: Theme.iconButtonSize + Theme.spacingM
                                height: width
                                anchors.verticalCenter: parent.verticalCenter
                                value: typeof root.tel.cpu_temp_c === "number"
                                       ? Math.min(1, root.tel.cpu_temp_c / root.tjmax) : -1
                                trackGap: Theme.spacingXXS
                                ringColor: root.tempRing(root.tel.cpu_temp_c)
                                animated: root.interactionActive

                                DC.DIcon {
                                    anchors.centerIn: parent
                                    name: "memory"
                                    size: Theme.iconSize
                                    color: root.tempRing(root.tel.cpu_temp_c)
                                }
                            }

                            Column {
                                width: parent.width - heroGauge.width - openButton.width - parent.spacing * 2
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: Theme.spacingXXS

                                DC.StyledText {
                                    width: parent.width
                                    text: root.online ? root.num(root.tel.cpu_temp_c) + "°C" : "Offline"
                                    font.pixelSize: Theme.fontSizeXXLarge
                                    font.features: { "tnum": 1 }
                                    color: root.online ? root.tempColor(root.tel.cpu_temp_c) : Theme.onSurfaceVariant
                                }

                                DC.StyledText {
                                    width: parent.width
                                    text: !root.online ? "hydroc-server is not running"
                                        : root.readOnly ? "Read-only, /run/hydroc/token cannot be read"
                                        : `CPU · ${root.activeName} · PL1 ${root.num(root.hw.cpu_pl1)} W`
                                    font.pixelSize: Theme.fontSizeSmall
                                    color: Theme.onSurfaceVariant
                                    elide: Text.ElideRight
                                }
                            }

                            DC.DActionButton {
                                id: openButton
                                anchors.verticalCenter: parent.verticalCenter
                                iconName: "open_in_new"
                                tooltipText: "Open HydroControl"
                                onClicked: {
                                    Quickshell.execDetached(["hydrocontrol"]);
                                    if (pop.closePopout)
                                        pop.closePopout();
                                }
                            }
                        }

                        DC.DSparkline {
                            width: parent.width
                            height: 40
                            visible: root.online
                            values: root.pkgHist
                            historyLength: 60
                            minimum: 0
                            maximum: root.chartMax
                            lineColor: Theme.primary
                        }
                    }
                }

                // ── stats ─────────────────────────────────────────────────
                Row {
                    id: statsRow
                    width: parent.width
                    spacing: Theme.groupedListGap
                    visible: root.online
                    readonly property real tileWidth: (width - spacing * 2) / 3

                    StatTile {
                        width: statsRow.tileWidth
                        first: true
                        label: "GPU"
                        value: root.num(root.tel.gpu_temp_c) + "°C"
                        iconName: "developer_board"
                        ratio: typeof root.tel.gpu_temp_c === "number" ? Math.min(1, root.tel.gpu_temp_c / 100) : -1
                        ringColor: root.tempRing(root.tel.gpu_temp_c)
                        valueColor: root.tempColor(root.tel.gpu_temp_c)
                    }

                    StatTile {
                        width: statsRow.tileWidth
                        label: "Package"
                        value: root.num(root.tel.package_w, 1) + " W"
                        iconName: "bolt"
                        ratio: typeof root.tel.package_w === "number" && root.hw.cpu_pl1
                               ? Math.min(1, root.tel.package_w / root.hw.cpu_pl1) : -1
                    }

                    StatTile {
                        width: statsRow.tileWidth
                        last: true
                        label: "CPU fan"
                        value: root.num(root.tel.fan1_rpm) + " rpm"
                        iconName: "mode_fan"
                        ratio: typeof root.tel.pwm1 === "number" ? root.tel.pwm1 / 255 : -1
                    }
                }

                // ── power mode ────────────────────────────────────────────
                Section {
                    heading: "Power mode"

                    DC.DButtonGroup {
                        width: parent.width
                        fillWidth: true
                        checkEnabled: false
                        model: root.presetList.map(p => ({ text: p.name, icon: root.presetIcons[p.id] || "tune" }))
                        currentIndex: root.presetIndex
                        enabled: !root.readOnly && !root.busy
                        onSelectionChanged: (index, selected) => {
                            if (selected && index !== root.presetIndex)
                                root.post("/api/preset", { preset: root.presetList[index].id });
                        }
                    }

                    DC.DCollapsibleSection {
                        width: parent.width
                        title: "The machine's own modes"
                        expanded: !!root.presets.native_active

                        DC.DButtonGroup {
                            Layout.fillWidth: true
                            fillWidth: true
                            checkEnabled: false
                            size: "small"
                            model: root.nativeList.map(p => ({ text: p.name, icon: root.nativeIcons[p.id] || "tune" }))
                            currentIndex: root.nativeIndex
                            enabled: !root.readOnly && !root.busy
                            onSelectionChanged: (index, selected) => {
                                if (selected && index !== root.nativeIndex)
                                    root.post("/api/native", { mode: root.nativeList[index].id });
                            }
                        }
                    }
                }

                // ── CPU temperature limit ─────────────────────────────────
                // Written only on Set or Enter. The app's first version applied
                // on blur and on its spinner, and fought the user mid-entry; the
                // field here is only refreshed from the hardware while it does
                // not have focus.
                Section {
                    id: tccCard
                    heading: "CPU temperature limit"
                    note: root.tccApplied ? `In force ${root.tjmax - root.hw.cpu_tcc_applied}°C` : ""
                    visible: root.tccKnown
                    property string error: ""

                    function set() {
                        const raw = tccField.text.trim(), t = Number(raw), lo = root.tjmax - root.tccRange;
                        if (!/^\d+$/.test(raw) || t < lo || t > root.tjmax) {
                            error = `Enter a whole number from ${lo} to ${root.tjmax}°C`;
                            return;
                        }
                        error = "";
                        if (root.tjmax - t !== root.hw.cpu_tcc_offset)
                            root.apply({ cpu_tcc_enable: true, cpu_tcc_offset: root.tjmax - t });
                    }

                    Item {
                        width: parent.width
                        height: Math.max(tccToggleLabel.implicitHeight, tccToggle.height)

                        DC.StyledText {
                            id: tccToggleLabel
                            anchors.verticalCenter: parent.verticalCenter
                            text: root.hw.cpu_tcc_enable ? "Throttle at a set temperature" : "Firmware default"
                            color: Theme.onSurface
                        }

                        DC.DToggle {
                            id: tccToggle
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            hideText: true
                            checked: !!root.hw.cpu_tcc_enable
                            enabled: !root.readOnly && !root.busy
                            // Turning it on starts from what the CPU already
                            // runs, so the switch alone changes nothing.
                            onToggled: on => {
                                tccCard.error = "";
                                root.apply(on ? { cpu_tcc_enable: true,
                                                  cpu_tcc_offset: Math.min(root.tccRange, root.hw.cpu_tcc_applied ?? 5) }
                                              : { cpu_tcc_enable: false });
                            }
                        }
                    }

                    Row {
                        spacing: Theme.spacingS
                        visible: !!root.hw.cpu_tcc_enable

                        DC.DTextField {
                            id: tccField
                            anchors.verticalCenter: parent.verticalCenter
                            width: 88
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
                            anchors.verticalCenter: parent.verticalCenter
                            text: "°C"
                            color: Theme.onSurfaceVariant
                        }

                        DC.DButton {
                            anchors.verticalCenter: parent.verticalCenter
                            text: "Set"
                            variant: "tonal"
                            enabled: !root.readOnly && !root.busy
                            onClicked: tccCard.set()
                        }
                    }

                    Caption {
                        readonly property bool mismatch: !!root.hw.cpu_tcc_enable && root.tccApplied
                                                         && root.hw.cpu_tcc_applied !== root.hw.cpu_tcc_offset
                        color: tccCard.error ? Theme.error : mismatch ? Theme.warning : Theme.onSurfaceVariant
                        text: tccCard.error ? tccCard.error
                            : mismatch ? `The CPU is running ${root.tjmax - root.hw.cpu_tcc_applied}°C, not the ${root.tjmax - root.hw.cpu_tcc_offset}°C set`
                            : root.hw.cpu_tcc_enable ? `${root.tjmax - root.tccRange}–${root.tjmax}°C · −${root.hw.cpu_tcc_offset || 0} from TjMax`
                            : "The current mode's own limit"
                    }
                }

                // ── battery ───────────────────────────────────────────────
                Section {
                    heading: "Battery"
                    note: root.online ? `${root.num(root.tel.capacity_pct)}% · ${root.tel.status || ""}` : ""

                    DC.DButtonGroup {
                        width: parent.width
                        fillWidth: true
                        checkEnabled: false
                        model: [{ text: "Stationary", icon: "power" },
                                { text: "Balanced", icon: "balance" },
                                { text: "Long Haul", icon: "battery_full" }]
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

                        Caption {
                            text: `Charge limit ${threshold.value}% · above 75% the 4.175 V cell ceiling stops it first`
                        }
                    }

                    Caption {
                        text: "Power Saver" + (root.saver.active ? " · engaged" : "")
                    }

                    DC.DButtonGroup {
                        width: parent.width
                        fillWidth: true
                        checkEnabled: false
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
                Section {
                    heading: "Fans"
                    note: root.online ? `${root.num(root.tel.fan1_rpm)} · ${root.num(root.tel.fan2_rpm)} rpm` : ""

                    DC.DButtonGroup {
                        width: parent.width
                        fillWidth: true
                        checkEnabled: false
                        model: [{ text: "Firmware", icon: "memory" }, { text: "Curve", icon: "show_chart" }]
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
                        fillWidth: true
                        checkEnabled: false
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

                    Caption {
                        visible: root.hw.fan_mode === "manual" && root.curveIndex < 0
                        text: "A curve edited in the app is running. Pick one here to replace it"
                    }
                }

                // ── graphics ──────────────────────────────────────────────
                // Shown, not switched: the MUX is a firmware write that needs a
                // reboot, which does not belong one click away in a panel.
                Section {
                    heading: "Graphics"
                    visible: !!root.gpu.supported

                    Item {
                        width: parent.width
                        height: Math.max(gpuText.implicitHeight, gpuButton.height)

                        DC.StyledText {
                            id: gpuText
                            anchors.verticalCenter: parent.verticalCenter
                            width: parent.width - gpuButton.width - Theme.spacingS
                            elide: Text.ElideRight
                            text: (root.gpu.label || root.gpu.mode || "—")
                                  + (root.gpu.reboot_pending ? " · reboot to apply" : "")
                            color: root.gpu.reboot_pending ? Theme.warning : Theme.onSurface
                        }

                        DC.DButton {
                            id: gpuButton
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            text: "Change"
                            variant: "tonal"
                            onClicked: {
                                Quickshell.execDetached(["hydrocontrol"]);
                                if (pop.closePopout)
                                    pop.closePopout();
                            }
                        }
                    }
                }

                Caption {
                    visible: root.message.length > 0
                    text: root.message
                    color: root.messageBad ? Theme.error : Theme.onSurfaceVariant
                }
            }
            }
        }
    }
}
