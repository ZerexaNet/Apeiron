import QtQuick
import QtQuick.Controls.Material
import QtQuick.Layouts
import Apeiron.Components 1.0

Item {
    id: page
    property int tab: 0

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 28
        spacing: 14

        PageHeader {
            title: qsTr("Logs & diagnostics")
            subtitle: qsTr("Sensitive values such as tokens are masked by the native logging core before they reach disk.")
            Button { text: qsTr("Refresh"); flat: true; onClicked: App.refreshLogs() }
            Button { text: qsTr("Export diagnostics"); highlighted: true; onClicked: App.exportDiagnostics() }
        }

        TabBar {
            Layout.fillWidth: true
            onCurrentIndexChanged: page.tab = currentIndex
            TabButton { text: qsTr("Game console") }
            TabButton { text: qsTr("Launcher log") }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: 10
            color: Theme.surfaceContainerHigh
            border.width: 1
            border.color: Theme.divider

            Flickable {
                id: flick
                anchors.fill: parent
                anchors.margins: 12
                clip: true
                contentWidth: width
                contentHeight: output.implicitHeight
                boundsBehavior: Flickable.StopAtBounds

                ScrollBar.vertical: ScrollBar {}

                TextArea {
                    id: output
                    width: flick.width
                    readOnly: true
                    wrapMode: TextArea.WrapAnywhere
                    font.family: App.fixedFontFamily
                    font.pixelSize: 12
                    color: Theme.onSurface
                    text: page.tab === 0 ? (App.consoleText.length > 0 ? App.consoleText : qsTr("No game output yet."))
                                        : App.logText
                    background: null
                }

                onContentHeightChanged: if (contentHeight > height) contentY = contentHeight - height
            }
        }

        RowLayout {
            spacing: 8
            Label {
                text: qsTr("Log statistics")
                font.pixelSize: 12
                color: Theme.onSurfaceVariant
            }
            Label {
                text: {
                    var s = App.about.logStats
                    return qsTr("written: %1 · dropped: %2 · rotated: %3")
                        .arg(s.written || 0).arg(s.dropped || 0).arg(s.rotations || 0)
                }
                font.pixelSize: 12
                color: Theme.onSurfaceVariant
            }
            Item { Layout.fillWidth: true }
            Button { text: qsTr("Open log folder"); flat: true; onClicked: App.openPath("logs") }
        }
    }
}
