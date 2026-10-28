import QtQuick
import QtQuick.Controls.Material
import QtQuick.Layouts
import Apeiron.Components 1.0

Item {
    id: page
    signal navigate(int index)

    readonly property var selected: App.selectedInstance
    readonly property bool hasSelection: selected && selected.id !== undefined

    ScrollView {
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth

        ColumnLayout {
            width: Math.min(page.width - 64, 1000)
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            anchors.topMargin: 28
            spacing: 18

            PageHeader {
                title: qsTr("Welcome to Apeiron")
                subtitle: qsTr("An independent launcher for Minecraft: Java Edition, with experimental Bedrock support on Windows.")
                Badge {
                    text: App.status.kind === "running" ? qsTr("Game running") : qsTr("Ready")
                    positive: App.status.kind === "running"
                }
            }

            Card {
                visible: page.hasSelection
                RowLayout {
                    spacing: 20
                    Rectangle {
                        width: 72; height: 72; radius: 16
                        color: Qt.rgba(Material.accent.r, Material.accent.g, Material.accent.b, 0.14)
                        Label {
                            anchors.centerIn: parent
                            text: "⛏"
                            font.pixelSize: 32
                            color: Material.accent
                        }
                    }
                    ColumnLayout {
                        spacing: 4
                        Layout.fillWidth: true
                        Label {
                            text: page.hasSelection ? page.selected.name : ""
                            font.pixelSize: 20
                            font.weight: Font.Medium
                        }
                        Label {
                            text: page.hasSelection
                                  ? page.selected.mcVersion + " · " + page.selected.loaderLabel
                                  : ""
                            color: Theme.onSurfaceVariant
                        }
                        RowLayout {
                            spacing: 8
                            Badge { text: qsTr("Memory") + " " + (page.hasSelection ? page.selected.memoryMb : 0) + " MB" }
                            Badge {
                                text: page.hasSelection && page.selected.installed ? qsTr("Installed") : qsTr("Not installed")
                                positive: page.hasSelection && page.selected.installed
                            }
                        }
                    }
                    Item { Layout.fillWidth: true }
                    ColumnLayout {
                        spacing: 8
                        Button {
                            text: App.status.kind === "running" ? qsTr("Stop") : qsTr("Launch")
                            highlighted: true
                            enabled: page.hasSelection && App.busy.length === 0
                            onClicked: App.status.kind === "running" ? App.stopGame() : App.launchInstance(page.selected.id)
                            Accessible.name: qsTr("Launch the selected instance")
                        }
                        Button {
                            text: qsTr("Install files")
                            flat: true
                            visible: page.hasSelection && !page.selected.installed
                            onClicked: App.installInstance(page.selected.id)
                        }
                    }
                }
            }

            Card {
                visible: !page.hasSelection
                EmptyState {
                    title: qsTr("No instance selected")
                    description: qsTr("Create your first instance to download the game files and start playing.")
                    actionText: qsTr("Open library")
                    onAction: page.navigate(1)
                }
            }

            Card {
                Label {
                    text: qsTr("Quick actions")
                    font.pixelSize: 16
                    font.weight: Font.Medium
                }
                GridLayout {
                    columns: page.width > 900 ? 3 : 1
                    columnSpacing: 12
                    rowSpacing: 12
                    Layout.fillWidth: true

                    Button {
                        text: qsTr("Create instance")
                        Layout.fillWidth: true
                        onClicked: page.navigate(1)
                    }
                    Button {
                        text: qsTr("Manage accounts")
                        Layout.fillWidth: true
                        onClicked: page.navigate(2)
                    }
                    Button {
                        text: qsTr("Import modpack")
                        Layout.fillWidth: true
                        onClicked: page.navigate(4)
                    }
                }
            }

            Card {
                Label {
                    text: qsTr("Recent activity")
                    font.pixelSize: 16
                    font.weight: Font.Medium
                }
                Label {
                    visible: App.recentLaunches.length === 0
                    text: qsTr("No launches yet.")
                    color: Theme.onSurfaceVariant
                }
                Repeater {
                    model: App.recentLaunches
                    delegate: RowLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        Label {
                            text: modelData.startedAt.replace("T", " ")
                            color: Theme.onSurfaceVariant
                            font.pixelSize: 12
                            Layout.preferredWidth: 160
                        }
                        Label {
                            text: modelData.status
                            Layout.fillWidth: true
                        }
                        Label {
                            text: modelData.duration + " s"
                            color: Theme.onSurfaceVariant
                            font.pixelSize: 12
                        }
                    }
                }
            }

            Card {
                Label {
                    text: qsTr("Platform support")
                    font.pixelSize: 16
                    font.weight: Font.Medium
                }
                Repeater {
                    model: App.about.support
                    delegate: RowLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        Label {
                            text: modelData.platform
                            Layout.preferredWidth: 120
                            font.weight: Font.Medium
                        }
                        Label {
                            text: qsTr("Java Edition") + (modelData.java ? " ✓" : " ✗")
                            Layout.fillWidth: true
                        }
                        Label {
                            text: qsTr("Bedrock") + (modelData.bedrock ? " ✓" : " ✗")
                            Layout.preferredWidth: 140
                            color: modelData.bedrock ? Theme.onSurface : Theme.onSurfaceVariant
                        }
                    }
                }
            }

            Item { Layout.preferredHeight: 24 }
        }
    }
}
