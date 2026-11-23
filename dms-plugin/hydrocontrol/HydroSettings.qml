import QtQuick
import qs.Common
import qs.Modules.Plugins

PluginSettings {
    id: root
    pluginId: "hydroControl"

    SelectionSetting {
        settingKey: "pillMetric"
        label: "Bar reading"
        description: "What the bar shows. It turns amber above 78 °C and red above 88 °C."
        options: [
            { label: "CPU temperature", value: "cpu" },
            { label: "CPU package power", value: "power" },
            { label: "Both", value: "both" }
        ]
        defaultValue: "cpu"
    }

    SelectionSetting {
        settingKey: "pillInterval"
        label: "Bar refresh"
        description: "How often the bar reads while the flyout is closed. Open, it reads every second. Every client shares one EC read, so this sets how often the daemon wakes for the bar alone."
        options: [
            { label: "2 seconds", value: "2000" },
            { label: "5 seconds", value: "5000" },
            { label: "10 seconds", value: "10000" }
        ]
        defaultValue: "5000"
    }
}
