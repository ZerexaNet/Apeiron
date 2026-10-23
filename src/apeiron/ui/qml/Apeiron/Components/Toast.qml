import QtQuick
import QtQuick.Controls.Material
import QtQuick.Layouts

/* Material 3 snackbar used for non-blocking notifications. */
Rectangle {
    id: toast
    property string level: "info"
    property string title: ""
    property string message: ""
    property bool shown: false

    width: Math.min(parent ? parent.width - 64 : 480, 560)
    height: layout.implicitHeight + 24
    radius: 8
    color: level === "error" ? (Theme.dark ? "#8C1D18" : "#BA1A1A")
         : level === "success" ? (Theme.dark ? "#1B3A1F" : "#146C2E")
         : (Theme.dark ? "#303030" : "#303030")
    opacity: shown ? 1 : 0
    visible: opacity > 0
    z: 100
    anchors.horizontalCenter: parent ? parent.horizontalCenter : undefined
    anchors.bottom: parent ? parent.bottom : undefined
    anchors.bottomMargin: 28
    Accessible.role: Accessible.AlertMessage
    Accessible.name: toast.title + " " + toast.message

    Behavior on opacity { NumberAnimation { duration: 180 } }

    Timer {
        id: hideTimer
        interval: 5000
        onTriggered: toast.shown = false
    }

    function show(level, title, message) {
        toast.level = level
        toast.title = title
        toast.message = message
        toast.shown = true
        hideTimer.restart()
    }

    ColumnLayout {
        id: layout
        anchors.fill: parent
        anchors.margins: 12
        spacing: 4
        Label {
            text: toast.title
            color: "white"
            font.weight: Font.Medium
            Layout.fillWidth: true
            elide: Text.ElideRight
        }
        Label {
            text: toast.message
            color: "#e6e1e5"
            wrapMode: Text.WordWrap
            visible: toast.message.length > 0
            Layout.fillWidth: true
            maximumLineCount: 4
            elide: Text.ElideRight
        }
    }

    MouseArea {
        anchors.fill: parent
        onClicked: toast.shown = false
    }
}
