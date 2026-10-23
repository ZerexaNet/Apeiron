import QtQuick
import QtQuick.Controls.Material
import QtQuick.Layouts
import QtQuick.Dialogs
import Apeiron.Components 1.0

Item {
    id: page

    property var items: []
    property string kindFilter: "mod"

    function reload() {
        items = App.selectedInstanceId.length > 0 ? App.contentList(App.selectedInstanceId) : []
    }

    Connections {
        target: App
        function onSelectionChanged() { page.reload() }
        function onInstancesChanged() { page.reload() }
    }

    Component.onCompleted: reload()

    FileDialog {
        id: modpackDialog
        title: qsTr("Import modpack")
        fileMode: FileDialog.OpenFile
        nameFilters: [qsTr("Modpacks (*.mrpack *.zip)"), qsTr("All files (*)")]
        onAccepted: App.importModpack(selectedFile)
    }

    FileDialog {
        id: contentDialog
        title: qsTr("Add file")
        fileMode: FileDialog.OpenFile
        nameFilters: [qsTr("Mods and packs (*.jar *.zip)"), qsTr("All files (*)")]
        onAccepted: {
            App.installContent(App.selectedInstanceId, selectedFile, page.kindFilter)
            page.reload()
        }
    }

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
                title: qsTr("Content")
                subtitle: qsTr("Mods, resource packs, shader packs and data packs for the selected instance.")
                Button {
                    text: qsTr("Import modpack")
                    highlighted: true
                    onClicked: modpackDialog.open()
                }
                Button {
                    text: qsTr("Add file")
                    flat: true
                    enabled: App.selectedInstanceId.length > 0
                    onClicked: contentDialog.open()
                }
            }

            Card {
                visible: App.selectedInstanceId.length === 0
                EmptyState {
                    title: qsTr("Select an instance")
                    description: qsTr("Choose an instance in the library to manage its content.")
                }
            }

            Card {
                visible: App.selectedInstanceId.length > 0
                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        text: App.selectedInstance.name || ""
                        font.pixelSize: 16
                        font.weight: Font.Medium
                        Layout.fillWidth: true
                    }
                    Badge { text: App.selectedInstance.loaderLabel || "" }
                }
                TabBar {
                    id: tabs
                    Layout.fillWidth: true
                    onCurrentIndexChanged: {
                        page.kindFilter = ["mod", "resourcepack", "shaderpack", "datapack"][currentIndex]
                        page.reload()
                    }
                    TabButton { text: qsTr("Mods") }
                    TabButton { text: qsTr("Resource packs") }
                    TabButton { text: qsTr("Shaders") }
                    TabButton { text: qsTr("Data packs") }
                }

                Label {
                    visible: page.filtered().length === 0
                    text: qsTr("Nothing installed in this category.")
                    color: Theme.onSurfaceVariant
                }

                Repeater {
                    model: page.filtered()
                    delegate: RowLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        Label {
                            text: modelData.filename
                            Layout.fillWidth: true
                            elide: Text.ElideMiddle
                            color: modelData.enabled ? Theme.onSurface : Theme.onSurfaceVariant
                        }
                        Label {
                            text: Math.round(modelData.size / 1024) + " KiB"
                            color: Theme.onSurfaceVariant
                            font.pixelSize: 11
                        }
                        Switch {
                            checked: modelData.enabled
                            onToggled: {
                                App.toggleContent(App.selectedInstanceId, modelData.id, checked)
                                page.reload()
                            }
                        }
                        Button {
                            text: qsTr("Remove")
                            flat: true
                            onClicked: {
                                App.removeContent(App.selectedInstanceId, modelData.id)
                                page.reload()
                            }
                        }
                    }
                }
            }

            Card {
                visible: App.selectedInstanceId.length > 0
                Label { text: qsTr("Mod loader"); font.pixelSize: 16; font.weight: Font.Medium }
                Label {
                    text: qsTr("Current: %1").arg(App.selectedInstance.loaderLabel || "")
                    color: Theme.onSurfaceVariant
                }
                Label {
                    text: qsTr("Forge and NeoForge installs run the vendor's official installer after verifying its published checksum.")
                    color: Theme.onSurfaceVariant
                    font.pixelSize: 11
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                Button {
                    text: qsTr("Open instance folder")
                    flat: true
                    onClicked: App.openPath("instance")
                }
            }

            Item { Layout.preferredHeight: 24 }
        }
    }

    function filtered() {
        var out = []
        for (var i = 0; i < items.length; ++i) {
            if (items[i].kind === page.kindFilter) out.push(items[i])
        }
        return out
    }
}
