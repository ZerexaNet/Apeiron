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
            width: Math.min(page.width - 64, 980)
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            anchors.topMargin: 28
            spacing: 16

            PageHeader {
                title: qsTr("Accounts")
                subtitle: qsTr("Microsoft accounts are verified with Xbox Live. Offline profiles are local identities only.")
            }

            Card {
                Label { text: qsTr("Add an account"); font.pixelSize: 16; font.weight: Font.Medium }
                RowLayout {
                    spacing: 8
                    TextField {
                        id: offlineName
                        Layout.fillWidth: true
                        placeholderText: qsTr("Player name (3-16 letters, digits, underscore)")
                        onAccepted: addOffline.clicked()
                    }
                    Button {
                        id: addOffline
                        text: qsTr("Create offline profile")
                        highlighted: true
                        onClicked: {
                            App.createOfflineAccount(offlineName.text)
                            offlineName.text = ""
                        }
                    }
                    Button {
                        text: qsTr("Sign in with Microsoft")
                        onClicked: App.beginMicrosoftLogin()
                    }
                }
                Label {
                    text: qsTr("Offline profiles never prove game ownership and cannot join servers that require authentication.")
                    color: Theme.onSurfaceVariant
                    font.pixelSize: 11
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
            }

            Card {
                visible: App.deviceCode.status === "waiting"
                Label { text: qsTr("Microsoft sign-in"); font.pixelSize: 16; font.weight: Font.Medium }
                Label {
                    text: qsTr("Open the link below and enter this code:")
                    color: Theme.onSurfaceVariant
                }
                Label {
                    text: App.deviceCode.userCode || ""
                    font.pixelSize: 32
                    font.letterSpacing: 6
                    font.weight: Font.DemiBold
                    color: Material.accent
                }
                Label {
                    text: App.deviceCode.verificationUri || ""
                    color: Material.accent
                    textFormat: Text.PlainText
                    onLinkActivated: Qt.openUrlExternally(link)
                }
                RowLayout {
                    Button {
                        text: qsTr("Open browser")
                        onClicked: Qt.openUrlExternally(App.deviceCode.verificationUri)
                    }
                    Button {
                        text: qsTr("Cancel")
                        flat: true
                        onClicked: App.cancelMicrosoftLogin()
                    }
                    BusyIndicator { running: true; implicitWidth: 24; implicitHeight: 24 }
                }
                Label {
                    text: App.deviceCode.message || ""
                    color: Theme.onSurfaceVariant
                    font.pixelSize: 11
                    wrapMode: Text.WordWrap
                }
            }

            Card {
                Label { text: qsTr("Saved accounts"); font.pixelSize: 16; font.weight: Font.Medium }
                Label {
                    visible: App.accounts.length === 0
                    text: qsTr("No accounts yet.")
                    color: Theme.onSurfaceVariant
                }
                Repeater {
                    model: App.accounts
                    delegate: RowLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: 12

                        Rectangle {
                            width: 40; height: 40; radius: 20
                            color: modelData.verified
                                   ? Qt.rgba(Material.accent.r, Material.accent.g, Material.accent.b, 0.18)
                                   : Theme.divider
                            Label {
                                anchors.centerIn: parent
                                text: modelData.name.substring(0, 1).toUpperCase()
                                font.weight: Font.DemiBold
                                color: modelData.verified ? Material.accent : Theme.onSurfaceVariant
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Label { text: modelData.name; font.weight: Font.Medium }
                            Label {
                                text: modelData.kind === "microsoft"
                                      ? qsTr("Microsoft account · verified")
                                      : qsTr("Offline profile · unverified local identity")
                                font.pixelSize: 11
                                color: Theme.onSurfaceVariant
                            }
                        }
                        Badge {
                            text: modelData.verified ? qsTr("Verified") : qsTr("Offline")
                            positive: modelData.verified
                        }
                        Button {
                            text: qsTr("Use")
                            flat: true
                            onClicked: App.selectAccount(modelData.id, App.selectedInstanceId)
                        }
                        Button {
                            text: qsTr("Remove")
                            flat: true
                            onClicked: App.removeAccount(modelData.id)
                        }
                    }
                }
            }

            Card {
                Label { text: qsTr("Credentials"); font.pixelSize: 16; font.weight: Font.Medium }
                Label {
                    text: qsTr("Storage backend: %1").arg(App.about.credentialBackend)
                    color: Theme.onSurfaceVariant
                }
                Label {
                    text: App.about.credentialSecure
                          ? qsTr("Tokens are protected by the operating system credential store.")
                          : qsTr("No OS credential service is available. Tokens are stored in a protected file; this is less secure.")
                    color: App.about.credentialSecure ? Theme.onSurfaceVariant : Theme.error
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                Label {
                    text: qsTr("Apeiron never asks for your Microsoft password and never stores it.")
                    color: Theme.onSurfaceVariant
                    font.pixelSize: 11
                }
            }

            Item { Layout.preferredHeight: 24 }
        }
    }
}
