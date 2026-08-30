import QtQuick
import qs.Common
import qs.Modules.Plugins
import qs.Widgets

PluginSettings {
    id: root
    pluginId: "hydroc"

    StyledText {
        width: parent.width
        text: "HydroControl"
        font.pixelSize: Theme.fontSizeLarge
        font.weight: Font.Bold
        color: Theme.surfaceText
    }
    StyledText {
        width: parent.width
        text: "The widget talks to hydroc-server over loopback HTTP (:8781). Override the API base here if the daemon lives elsewhere."
        font.pixelSize: Theme.fontSizeSmall
        color: Theme.surfaceVariantText
        wrapMode: Text.WordWrap
    }

    StringSetting {
        settingKey: "apiBase"
        label: "API base"
        description: "hydroc-server base URL"
        placeholder: "http://127.0.0.1:8781"
        defaultValue: "http://127.0.0.1:8781"
    }
}
