import QtQuick
import QtQuick.Controls.Material
import QtQuick.Layouts

/*
  Material 3 style navigation rail: 80dp wide, icon + label items, a pill
  indicator for the active destination and full keyboard navigation.
*/
ColumnLayout {
    id: rail
    spacing: 4
    property int currentIndex: 0
    property var items: []
    signal activated(int index)

    Layout.preferredWidth: 88
    Layout.fillHeight: true

    Item { Layout.preferredHeight: 12 }

    Repeater {
        model: rail.items
        delegate: Item {
            id: entry
            required property int index
            required property var modelData
            Layout.fillWidth: true
            Layout.preferredHeight: 64
            Accessible.role: Accessible.PageTab
            Accessible.name: modelData.label
            Accessible.selected: rail.currentIndex === index

            Rectangle {
                id: pill
                anchors.horizontalCenter: parent.horizontalCenter
                y: 6
                width: active ? 56 : 40
                height: 32
                radius: 16
                color: active ? Theme.primaryContainer
                              : (hover.hovered ? Material.rippleColor : "transparent")
                Behavior on width { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
                Behavior on color { ColorAnimation { duration: 120 } }
            }

            Label {
                id: icon
                anchors.centerIn: pill
                text: modelData.icon
                font.pixelSize: 18
                color: active ? Theme.onPrimaryContainer : Theme.onSurfaceVariant
            }

            Label {
                anchors.top: pill.bottom
                anchors.topMargin: 2
                anchors.horizontalCenter: parent.horizontalCenter
                text: modelData.label
                font.pixelSize: 11
                color: active ? Theme.onSurface : Theme.onSurfaceVariant
                elide: Text.ElideRight
                width: parent.width - 8
                horizontalAlignment: Text.AlignHCenter
            }

            readonly property bool active: rail.currentIndex === index

            HoverHandler { id: hover }

            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: rail.activated(entry.index)
            }

            Keys.onReturnPressed: rail.activated(entry.index)
            Keys.onSpacePressed: rail.activated(entry.index)
            focus: rail.currentIndex === index
        }
    }

    Item { Layout.fillHeight: true }
}
