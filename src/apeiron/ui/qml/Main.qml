import QtQuick
import QtQuick.Controls.Material
import QtQuick.Layouts
import QtQuick.Window
import Apeiron.Components 1.0
import "pages"

ApplicationWindow {
    id: window
    width: 1280
    height: 840
    minimumWidth: 1024
    minimumHeight: 680
    visible: true
    title: qsTr("Apeiron")

    readonly property bool dark: Theme.dark

    Connections {
        target: App
        function onThemeChanged() { Theme.mode = App.theme }
    }

    Material.theme: dark ? Material.Dark : Material.Light
    Material.accent: Material.Blue
    Material.primary: Material.Blue
    Material.roundedScale: Material.MediumScale
    color: Material.backgroundColor

    property int currentPage: 0

    Shortcut { sequence: "Ctrl+1"; onActivated: window.currentPage = 0 }
    Shortcut { sequence: "Ctrl+2"; onActivated: window.currentPage = 1 }
    Shortcut { sequence: "Ctrl+3"; onActivated: window.currentPage = 2 }
    Shortcut { sequence: "Ctrl+4"; onActivated: window.currentPage = 3 }
    Shortcut { sequence: "Ctrl+5"; onActivated: window.currentPage = 4 }
    Shortcut { sequence: "Ctrl+6"; onActivated: window.currentPage = 5 }
    Shortcut { sequence: "Ctrl+7"; onActivated: window.currentPage = 6 }
    Shortcut { sequence: "Ctrl+8"; onActivated: window.currentPage = 7 }
    Shortcut { sequence: "Ctrl+L"; onActivated: window.currentPage = 6 }
    Shortcut { sequence: "Ctrl+Q"; onActivated: App.quit() }

    // A custom app bar: Qt's Material ToolBar pulls in the elevation effect
    // module, which is unavailable in some Qt builds; a tonal Rectangle gives
    // the same Material 3 look without that dependency.
    Rectangle {
        id: appBar
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 64
        color: Theme.surface
        z: 10

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 16
            anchors.rightMargin: 16
            spacing: 12

            Label {
                text: "✦"
                font.pixelSize: 20
                color: Theme.primary
            }
            Label {
                text: qsTr("Apeiron")
                font.pixelSize: 18
                font.weight: Font.DemiBold
            }
            Badge {
                text: qsTr("Independent launcher")
                visible: window.width > 1120
            }

            Item { Layout.fillWidth: true }

            RowLayout {
                spacing: 8
                visible: App.busy.length > 0 || App.status.kind === "running" || App.status.kind === "download"
                Label {
                    text: App.busy.length > 0 ? App.busy : App.status.kind
                    color: Theme.onSurfaceVariant
                    font.pixelSize: 12
                }
                BusyIndicator {
                    running: parent.visible
                    visible: parent.visible
                    implicitWidth: 22
                    implicitHeight: 22
                }
            }

            Button {
                text: qsTr("Stop game")
                visible: App.status.kind === "running"
                flat: true
                onClicked: App.stopGame()
            }

            ToolButton {
                text: "☾"
                font.pixelSize: 16
                onClicked: App.setTheme(window.dark ? "light" : "dark")
                Accessible.name: qsTr("Toggle theme")
            }

            Button {
                id: accountChip
                flat: true
                text: {
                    var accounts = App.accounts
                    for (var i = 0; i < accounts.length; ++i) {
                        if (accounts[i].verified) return accounts[i].name
                    }
                    return accounts.length > 0 ? accounts[0].name : qsTr("No account")
                }
                onClicked: window.currentPage = 2
            }
        }

        Rectangle {
            anchors.bottom: parent.bottom
            width: parent.width
            height: 1
            color: Theme.divider
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.topMargin: 64
        spacing: 0

        NavRail {
            id: nav
            currentIndex: window.currentPage
            onActivated: function (index) { window.currentPage = index }
            items: [
                { icon: "⌂", label: qsTr("Home") },
                { icon: "▤", label: qsTr("Library") },
                { icon: "☺", label: qsTr("Accounts") },
                { icon: "↓", label: qsTr("Downloads") },
                { icon: "◈", label: qsTr("Content") },
                { icon: "⚙", label: qsTr("Settings") },
                { icon: "≣", label: qsTr("Logs") },
                { icon: "ⓘ", label: qsTr("About") }
            ]
        }

        Rectangle {
            Layout.fillHeight: true
            width: 1
            color: Theme.divider
        }

        StackLayout {
            id: stack
            currentIndex: window.currentPage
            Layout.fillWidth: true
            Layout.fillHeight: true

            HomePage { onNavigate: function (index) { window.currentPage = index } }
            LibraryPage {}
            AccountsPage {}
            DownloadsPage {}
            ContentPage {}
            SettingsPage {}
            LogsPage {}
            AboutPage {}
        }
    }

    Toast { id: toast }

    Connections {
        target: App
        function onNotify(level, title, message) { toast.show(level, title, message) }
    }

    Component.onCompleted: {
        Theme.mode = App.theme
        App.bootstrap()
    }
}
