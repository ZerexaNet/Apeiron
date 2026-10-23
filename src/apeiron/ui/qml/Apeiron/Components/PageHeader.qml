import QtQuick
import QtQuick.Controls.Material
import QtQuick.Layouts

RowLayout {
    property string title: ""
    property string subtitle: ""
    default property alias actions: actionRow.data
    spacing: 12
    Layout.fillWidth: true

    ColumnLayout {
        spacing: 2
        Layout.fillWidth: true
        Label {
            text: parent.parent.title
            font.pixelSize: 26
            font.weight: Font.Medium
            color: Theme.onSurface
        }
        Label {
            text: parent.parent.subtitle
            font.pixelSize: 13
            color: Theme.onSurfaceVariant
            visible: text.length > 0
            wrapMode: Text.WordWrap
        }
    }

    RowLayout {
        id: actionRow
        spacing: 8
    }
}
