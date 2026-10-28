import QtQuick
import QtQuick.Controls.Material

/* Small status chip used for verification state, loader type and task status. */
Rectangle {
    id: badge
    property string text: ""
    property bool positive: false
    property bool warning: false
    implicitWidth: label.implicitWidth + 16
    implicitHeight: 22
    radius: 11
    color: positive ? Qt.rgba(Material.accent.r, Material.accent.g, Material.accent.b, 0.16)
         : warning ? Qt.rgba(Theme.error.r, Theme.error.g, Theme.error.b, 0.16)
         : Theme.divider

    Label {
        id: label
        anchors.centerIn: parent
        text: badge.text
        font.pixelSize: 11
        font.weight: Font.Medium
        color: badge.positive ? Material.accent : badge.warning ? Theme.error : Theme.onSurfaceVariant
    }
}
