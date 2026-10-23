import QtQuick
import QtQuick.Controls.Material
import QtQuick.Layouts

ColumnLayout {
    id: empty
    property string title: ""
    property string description: ""
    property string actionText: ""
    signal action()

    spacing: 10
    Layout.alignment: Qt.AlignCenter
    Layout.fillWidth: true
    Layout.topMargin: 40
    Layout.bottomMargin: 40

    Label {
        text: "\u25CB"
        font.pixelSize: 40
        color: Theme.onSurfaceVariant
        opacity: 0.5
        Layout.alignment: Qt.AlignHCenter
    }
    Label {
        text: empty.title
        font.pixelSize: 17
        font.weight: Font.Medium
        Layout.alignment: Qt.AlignHCenter
    }
    Label {
        text: empty.description
        color: Theme.onSurfaceVariant
        wrapMode: Text.WordWrap
        horizontalAlignment: Text.AlignHCenter
        Layout.maximumWidth: 460
        Layout.alignment: Qt.AlignHCenter
    }
    Button {
        text: empty.actionText
        visible: empty.actionText.length > 0
        highlighted: true
        Layout.alignment: Qt.AlignHCenter
        Layout.topMargin: 6
        onClicked: empty.action()
    }
}
