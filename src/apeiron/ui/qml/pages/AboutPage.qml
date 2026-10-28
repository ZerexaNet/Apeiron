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
            width: Math.min(page.width - 64, 860)
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            anchors.topMargin: 28
            spacing: 16

            PageHeader {
                title: qsTr("About Apeiron")
                subtitle: qsTr("Version %1 · %2").arg(App.about.version).arg(App.about.platform)
            }

            Card {
                Label {
                    text: qsTr("Apeiron is an independent third-party Minecraft launcher developed and published by %1.")
                        .arg(App.about.company)
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                    font.pixelSize: 14
                }
                Label {
                    text: qsTr("It is not an official Minecraft product and is not affiliated with, endorsed by, or approved by Mojang Studios or Microsoft. Minecraft and related names, logos and assets belong to their respective owners.")
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                    color: Theme.onSurfaceVariant
                }
                Label {
                    text: qsTr("Contact: %1").arg(App.about.contact)
                    color: Material.accent
                }
            }

            Card {
                Label { text: qsTr("Build information"); font.pixelSize: 16; font.weight: Font.Medium }
                GridLayout {
                    columns: 2
                    columnSpacing: 24
                    rowSpacing: 6
                    Layout.fillWidth: true
                    Label { text: qsTr("Version"); color: Theme.onSurfaceVariant }
                    Label { text: App.about.version }
                    Label { text: qsTr("Platform"); color: Theme.onSurfaceVariant }
                    Label { text: App.about.platform }
                    Label { text: qsTr("Python"); color: Theme.onSurfaceVariant }
                    Label { text: App.about.python }
                    Label { text: qsTr("Qt"); color: Theme.onSurfaceVariant }
                    Label { text: App.about.qt }
                    Label { text: qsTr("Credential store"); color: Theme.onSurfaceVariant }
                    Label { text: App.about.credentialBackend + (App.about.credentialSecure ? " (secure)" : " (fallback)") }
                    Label { text: qsTr("Data directory"); color: Theme.onSurfaceVariant }
                    Label {
                        text: App.about.dataRoot
                        elide: Text.ElideMiddle
                        Layout.fillWidth: true
                    }
                }
            }

            Card {
                Label { text: qsTr("Platform support"); font.pixelSize: 16; font.weight: Font.Medium }
                Repeater {
                    model: App.about.support
                    delegate: RowLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        Label { text: modelData.platform; Layout.preferredWidth: 110; font.weight: Font.Medium }
                        Label { text: modelData.java ? qsTr("Java Edition ✓") : qsTr("Java Edition ✗"); Layout.fillWidth: true }
                        Label { text: modelData.bedrock ? qsTr("Bedrock ✓") : qsTr("Bedrock ✗"); Layout.preferredWidth: 120 }
                        Label { text: modelData.credentialStore; color: Theme.onSurfaceVariant; font.pixelSize: 11 }
                    }
                }
            }

            Card {
                Label { text: qsTr("Licensing"); font.pixelSize: 16; font.weight: Font.Medium }
                Label {
                    text: qsTr("Apeiron is distributed under the Mozilla Public License 2.0. Third-party components and their licences are listed in docs/THIRD_PARTY_LICENSES.md.")
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                    color: Theme.onSurfaceVariant
                }
                Label {
                    text: qsTr("Apeiron does not distribute Minecraft game files. Game files are downloaded from Mojang's official servers after you supply your own account or local profile.")
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                    color: Theme.onSurfaceVariant
                }
            }

            Item { Layout.preferredHeight: 24 }
        }
    }
}
