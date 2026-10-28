import QtQuick
import QtQuick.Controls.Material
import QtQuick.Layouts

/* A Material 3 "elevated" surface: tonal background, 12dp radius, 2dp shadow. */
Rectangle {
    id: card
    property int padding: 20
    property color tone: Theme.surfaceContainer
    default property alias items: inner.data

    Layout.fillWidth: true
    implicitHeight: inner.implicitHeight + padding * 2
    radius: 12
    color: tone
    border.width: 1
    border.color: Theme.divider

    ColumnLayout {
        id: inner
        anchors.fill: parent
        anchors.margins: card.padding
        spacing: 12
    }
}
