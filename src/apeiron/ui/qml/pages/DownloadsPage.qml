import QtQuick
import QtQuick.Controls.Material
import QtQuick.Layouts
import Apeiron.Components 1.0

Item {
    id: page

    ScrollView {
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth

        ColumnLayout {
            width: Math.min(page.width - 64, 1000)
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            anchors.topMargin: 28
            spacing: 16

            PageHeader {
                title: qsTr("Downloads")
                subtitle: qsTr("Every file is written to a temporary file, verified, then moved into place atomically.")
                Button {
                    text: qsTr("Refresh")
                    flat: true
                    onClicked: App.bootstrap()
                }
            }

            Card {
                visible: App.status.kind === "download"
                Label { text: qsTr("Current transfer"); font.pixelSize: 16; font.weight: Font.Medium }
                ProgressBar {
                    Layout.fillWidth: true
                    from: 0
                    to: 100
                    value: App.status.percent !== undefined ? App.status.percent : 0
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        text: (App.status.currentFile !== undefined ? App.status.currentFile : "")
                        elide: Text.ElideMiddle
                        Layout.fillWidth: true
                        color: Theme.onSurfaceVariant
                    }
                    Label {
                        text: (App.status.completedTasks || 0) + " / " + (App.status.totalTasks || 0)
                        color: Theme.onSurfaceVariant
                    }
                    Label {
                        text: Math.round((App.status.speedBps || 0) / 1024) + " KiB/s"
                        color: Theme.onSurfaceVariant
                    }
                }
            }

            Card {
                Label { text: qsTr("Task history"); font.pixelSize: 16; font.weight: Font.Medium }
                EmptyState {
                    visible: App.downloads.length === 0
                    title: qsTr("No download tasks")
                    description: qsTr("Installing a version or importing a modpack creates tasks here.")
                }
                Repeater {
                    model: App.downloads
                    delegate: ColumnLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: 4

                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                text: modelData.destination.split(/[\\/]/).pop()
                                Layout.fillWidth: true
                                elide: Text.ElideMiddle
                                font.pixelSize: 13
                            }
                            Badge {
                                text: modelData.status
                                positive: modelData.status === "completed"
                                warning: modelData.status === "failed"
                            }
                            Label {
                                text: modelData.category
                                color: Theme.onSurfaceVariant
                                font.pixelSize: 11
                            }
                            Label {
                                text: Math.round(modelData.bytesDone / 1024) + " KiB"
                                color: Theme.onSurfaceVariant
                                font.pixelSize: 11
                            }
                        }
                        ProgressBar {
                            Layout.fillWidth: true
                            from: 0
                            to: Math.max(1, modelData.bytesTotal)
                            value: modelData.bytesDone
                            visible: modelData.status === "running"
                        }
                        Label {
                            visible: modelData.error.length > 0
                            text: modelData.error
                            color: Theme.error
                            font.pixelSize: 11
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }
                }
            }

            Item { Layout.preferredHeight: 24 }
        }
    }
}
