import QtQuick
import QtQuick.Controls.Material
import QtQuick.Layouts
import QtQuick.Dialogs
import Apeiron.Components 1.0

Item {
    id: page

    FileDialog {
        id: exportDialog
        title: qsTr("Export instance")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "apeiron.zip"
        nameFilters: [qsTr("Apeiron instance (*.zip)"), qsTr("All files (*)")]
        onAccepted: App.exportInstance(App.selectedInstanceId, selectedFile)
    }

    FileDialog {
        id: importDialog
        title: qsTr("Import instance archive")
        fileMode: FileDialog.OpenFile
        nameFilters: [qsTr("Instance archive (*.zip)"), qsTr("All files (*)")]
        onAccepted: App.importInstance(selectedFile)
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth

            ColumnLayout {
                width: Math.min(page.width - 64, 1100)
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.top: parent.top
                anchors.topMargin: 28
                spacing: 16

                PageHeader {
                    title: qsTr("Game library")
                    subtitle: qsTr("Every instance has its own directory, mods, settings and account.")
                    Button {
                        text: qsTr("New instance")
                        highlighted: true
                        onClicked: createDialog.open()
                    }
                    Button {
                        text: qsTr("Import")
                        flat: true
                        onClicked: importDialog.open()
                    }
                }

                EmptyState {
                    visible: App.instances.length === 0
                    title: qsTr("Your library is empty")
                    description: qsTr("Create an instance to download Minecraft and start playing.")
                    actionText: qsTr("Create instance")
                    onAction: createDialog.open()
                }

                GridLayout {
                    columns: page.width > 1150 ? 3 : 2
                    Layout.fillWidth: true
                    columnSpacing: 14
                    rowSpacing: 14

                    Repeater {
                        model: App.instances
                        delegate: Rectangle {
                            required property var modelData
                            Layout.fillWidth: true
                            Layout.preferredHeight: 168
                            radius: 12
                            color: Theme.surfaceContainer
                            border.width: App.selectedInstanceId === modelData.id ? 2 : 1
                            border.color: App.selectedInstanceId === modelData.id
                                          ? Material.accent
                                          : Theme.divider

                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 16
                                spacing: 6

                                RowLayout {
                                    Layout.fillWidth: true
                                    Label {
                                        text: modelData.name
                                        font.pixelSize: 17
                                        font.weight: Font.Medium
                                        elide: Text.ElideRight
                                        Layout.fillWidth: true
                                    }
                                    Badge {
                                        text: modelData.gameType === "bedrock" ? qsTr("Bedrock") : qsTr("Java")
                                        positive: modelData.gameType === "java"
                                    }
                                }
                                Label {
                                    text: modelData.mcVersion + " · " + modelData.loaderLabel
                                    color: Theme.onSurfaceVariant
                                    font.pixelSize: 13
                                }
                                Label {
                                    text: modelData.lastPlayed.length > 0
                                          ? qsTr("Last played: %1").arg(modelData.lastPlayed.replace("T", " "))
                                          : qsTr("Never launched")
                                    color: Theme.onSurfaceVariant
                                    font.pixelSize: 12
                                }
                                Item { Layout.fillHeight: true }
                                RowLayout {
                                    spacing: 8
                                    Button {
                                        text: qsTr("Launch")
                                        highlighted: true
                                        onClicked: {
                                            App.selectInstance(modelData.id)
                                            App.launchInstance(modelData.id)
                                        }
                                    }
                                    Button {
                                        text: qsTr("Details")
                                        flat: true
                                        onClicked: App.selectInstance(modelData.id)
                                    }
                                }
                            }

                            MouseArea {
                                anchors.fill: parent
                                acceptedButtons: Qt.RightButton
                                onClicked: App.selectInstance(modelData.id)
                            }
                        }
                    }
                }

                Item { Layout.preferredHeight: 32 }
            }
        }

        Rectangle {
            Layout.preferredWidth: 380
            Layout.fillHeight: true
            color: Material.backgroundColor
            border.width: 1
            border.color: Theme.divider
            visible: App.selectedInstanceId.length > 0

            ScrollView {
                anchors.fill: parent
                clip: true
                contentWidth: availableWidth

                ColumnLayout {
                    width: parent.width - 32
                    x: 16
                    y: 16
                    spacing: 12

                    Label {
                        text: App.selectedInstance.name || ""
                        font.pixelSize: 20
                        font.weight: Font.Medium
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                    }
                    Label {
                        text: App.selectedInstance.mcVersion + " · " + App.selectedInstance.loaderLabel
                        color: Theme.onSurfaceVariant
                    }

                    ComboBox {
                        id: accountBox
                        Layout.fillWidth: true
                        model: App.accounts
                        textRole: "name"
                        valueRole: "id"
                        onActivated: App.selectAccount(currentValue, App.selectedInstanceId)
                        currentIndex: {
                            var selected = App.selectedInstance.accountId
                            for (var i = 0; i < App.accounts.length; ++i)
                                if (App.accounts[i].id === selected) return i
                            return -1
                        }
                    }

                    Label { text: qsTr("Memory (MB)"); font.pixelSize: 12; color: Theme.onSurfaceVariant }
                    SpinBox {
                        id: memoryBox
                        Layout.fillWidth: true
                        from: 512
                        to: 32768
                        stepSize: 512
                        value: App.selectedInstance.memoryMb || 4096
                        editable: true
                    }

                    Label { text: qsTr("JVM arguments"); font.pixelSize: 12; color: Theme.onSurfaceVariant }
                    TextField {
                        id: jvmField
                        Layout.fillWidth: true
                        text: App.selectedInstance.jvmArgs || ""
                        placeholderText: "-XX:+UseG1GC"
                    }

                    Label { text: qsTr("Resolution"); font.pixelSize: 12; color: Theme.onSurfaceVariant }
                    RowLayout {
                        TextField {
                            id: widthField
                            text: App.selectedInstance.width || 854
                            validator: IntValidator { bottom: 320; top: 7680 }
                            Layout.fillWidth: true
                        }
                        Label { text: "×" }
                        TextField {
                            id: heightField
                            text: App.selectedInstance.height || 480
                            validator: IntValidator { bottom: 240; top: 4320 }
                            Layout.fillWidth: true
                        }
                    }

                    Button {
                        text: qsTr("Save changes")
                        Layout.fillWidth: true
                        onClicked: App.updateInstance(
                            App.selectedInstanceId,
                            App.selectedInstance.name,
                            accountBox.currentValue || "",
                            jvmField.text,
                            App.selectedInstance.gameArgs || "",
                            memoryBox.value,
                            parseInt(widthField.text),
                            parseInt(heightField.text))
                    }

                    Button {
                        text: qsTr("Install / verify files")
                        Layout.fillWidth: true
                        flat: true
                        onClicked: App.installInstance(App.selectedInstanceId)
                    }

                    Button {
                        text: qsTr("Duplicate")
                        Layout.fillWidth: true
                        flat: true
                        onClicked: App.duplicateInstance(App.selectedInstanceId)
                    }

                    Button {
                        text: qsTr("Backup worlds")
                        Layout.fillWidth: true
                        flat: true
                        onClicked: App.backupInstance(App.selectedInstanceId)
                    }

                    Button {
                        text: qsTr("Export archive")
                        Layout.fillWidth: true
                        flat: true
                        onClicked: exportDialog.open()
                    }

                    Button {
                        text: qsTr("Delete instance")
                        Layout.fillWidth: true
                        flat: true
                        Material.foreground: Theme.error
                        onClicked: deleteDialog.open()
                    }

                    Item { Layout.preferredHeight: 24 }
                }
            }
        }
    }

    Dialog {
        id: deleteDialog
        title: qsTr("Delete instance?")
        implicitWidth: 460
        implicitHeight: 220
        modal: true
        standardButtons: Dialog.Cancel | Dialog.Ok
        anchors.centerIn: parent
        Label {
            text: qsTr("The instance directory and everything inside it will be removed. This cannot be undone.")
            wrapMode: Text.WordWrap
            width: 380
        }
        onAccepted: App.deleteInstance(App.selectedInstanceId)
    }

    Dialog {
        id: createDialog
        title: qsTr("New instance")
        modal: true
        implicitWidth: 500
        implicitHeight: 460
        anchors.centerIn: parent
        standardButtons: Dialog.Cancel | Dialog.Ok

        ColumnLayout {
            width: 400
            spacing: 10

            Label { text: qsTr("Name"); font.pixelSize: 12; color: Theme.onSurfaceVariant }
            TextField {
                id: nameField
                Layout.fillWidth: true
                placeholderText: qsTr("My world")
                text: qsTr("New instance")
            }

            Label { text: qsTr("Minecraft version"); font.pixelSize: 12; color: Theme.onSurfaceVariant }
            ComboBox {
                id: versionBox
                Layout.fillWidth: true
                model: App.versions
                textRole: "id"
                onActivated: App.fetchLoaderVersions(currentText,
                                                     ["vanilla", "fabric", "quilt", "forge", "neoforge"][loaderBox.currentIndex])
            }

            Label { text: qsTr("Mod loader"); font.pixelSize: 12; color: Theme.onSurfaceVariant }
            RowLayout {
                ComboBox {
                    id: loaderBox
                    Layout.fillWidth: true
                    model: [qsTr("Vanilla"), "Fabric", "Quilt", "Forge", "NeoForge"]
                    onActivated: App.fetchLoaderVersions(versionBox.currentText,
                                                          ["vanilla", "fabric", "quilt", "forge", "neoforge"][currentIndex])
                }
                ComboBox {
                    id: loaderVersionBox
                    Layout.fillWidth: true
                    model: loaderBox.currentIndex === 0 ? [] : App.loaders
                    textRole: "version"
                    enabled: loaderBox.currentIndex > 0
                }
            }

            Label {
                text: qsTr("Loader versions are fetched from the projects' official metadata.")
                font.pixelSize: 11
                color: Theme.onSurfaceVariant
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }

        onAccepted: {
            var loaderName = ["vanilla", "fabric", "quilt", "forge", "neoforge"][loaderBox.currentIndex]
            var loaderVersion = loaderBox.currentIndex === 0 ? "" : (loaderVersionBox.currentText || "")
            if (loaderName === "forge" || loaderName === "neoforge") {
                loaderConsent.open()
            } else {
                App.createInstance(nameField.text, versionBox.currentText, loaderName, loaderVersion, "")
            }
        }
        onOpened: {
            if (App.versions.length > 0 && versionBox.currentIndex < 0) versionBox.currentIndex = 0
        }
    }

    Dialog {
        id: loaderConsent
        implicitWidth: 460
        implicitHeight: 240
        title: qsTr("Run loader installer?")
        modal: true
        anchors.centerIn: parent
        standardButtons: Dialog.Cancel | Dialog.Ok
        Label {
            width: 400
            wrapMode: Text.WordWrap
            text: qsTr("Forge and NeoForge installation runs downloaded Java code. Continue only if you trust the loader.")
        }
        onAccepted: App.createInstance(nameField.text, versionBox.currentText,
            ["vanilla", "fabric", "quilt", "forge", "neoforge"][loaderBox.currentIndex],
            loaderVersionBox.currentText, "", true)
    }

}
