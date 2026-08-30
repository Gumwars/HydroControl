import QtQuick
import qs.Common
import qs.Modules.Plugins
import qs.Widgets

PluginSettings {
    id: root
    pluginId: "kbctrl"

    StyledText {
        width: parent.width
        text: "kbctrl — command overrides"
        font.pixelSize: Theme.fontSizeLarge
        font.weight: Font.Bold
        color: Theme.surfaceText
    }
    StyledText {
        width: parent.width
        text: "The widget shells out to `python3 -m kbctrl.ctl`. Override the interpreter and module path here if the daemon lives elsewhere."
        font.pixelSize: Theme.fontSizeSmall
        color: Theme.surfaceVariantText
        wrapMode: Text.WordWrap
    }

    StringSetting {
        settingKey: "pythonBin"
        label: "Python binary"
        description: "Interpreter used to run kbctrl.ctl"
        placeholder: "/usr/bin/python3"
        defaultValue: "/usr/bin/python3"
    }

    StringSetting {
        settingKey: "pythonPath"
        label: "kbctrl module path"
        description: "Directory containing the kbctrl package (PYTHONPATH)"
        placeholder: "/home/gumwars/kbctrl"
        defaultValue: "/home/gumwars/kbctrl"
    }
}