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
            width: Math.min(page.width - 64, 900)
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            anchors.topMargin: 28
            spacing: 16

            PageHeader {
                title: qsTr("Settings")
                subtitle: qsTr("Language, appearance, performance and runtime configuration.")
            }

            Card {
                Label { text: qsTr("Language"); font.pixelSize: 16; font.weight: Font.Medium }
                ComboBox {
                    Layout.fillWidth: true
                    model: App.languageChoices
                    textRole: "label"
                    valueRole: "code"
                    Component.onCompleted: {
                        for (var i = 0; i < model.length; ++i) {
                            if (model[i].code === App.language) { currentIndex = i; break }
                        }
                    }
                    onActivated: App.setLanguage(currentValue)
                }
                Label {
                    text: App.translationState.translated
                          ? qsTr("Effective language: %1").arg(App.effectiveLanguage)
                          : qsTr("Effective language: %1 (translation file missing, showing source text)").arg(App.effectiveLanguage)
                    color: Theme.onSurfaceVariant
                    font.pixelSize: 11
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                Label {
                    text: qsTr("A manual choice is kept across restarts. Choose \"Follow system\" to detect the OS language again.")
                    color: Theme.onSurfaceVariant
                    font.pixelSize: 11
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
            }

            Card {
                Label { text: qsTr("Appearance"); font.pixelSize: 16; font.weight: Font.Medium }
                RowLayout {
                    spacing: 8
                    Repeater {
                        model: [
                            { code: "system", label: qsTr("Follow system") },
                            { code: "light", label: qsTr("Light") },
                            { code: "dark", label: qsTr("Dark") }
                        ]
                        delegate: Button {
                            required property var modelData
                            text: modelData.label
                            checkable: true
                            checked: App.theme === modelData.code
                            onClicked: App.setTheme(modelData.code)
                        }
                    }
                }
            }

            Card {
                Label { text: qsTr("Performance"); font.pixelSize: 16; font.weight: Font.Medium }
                Label { text: qsTr("Default memory (MB)"); font.pixelSize: 12; color: Theme.onSurfaceVariant }
                SpinBox {
                    from: 512; to: 32768; stepSize: 512
                    value: App.settings.memoryMb
                    editable: true
                    onValueModified: App.setSetting("default_memory_mb", value)
                }
                Label { text: qsTr("Concurrent downloads"); font.pixelSize: 12; color: Theme.onSurfaceVariant }
                SpinBox {
                    from: 1; to: 16
                    value: App.settings.concurrency
                    onValueModified: App.setSetting("max_concurrent_downloads", value)
                }
                Label { text: qsTr("Download timeout (seconds)"); font.pixelSize: 12; color: Theme.onSurfaceVariant }
                SpinBox {
                    from: 5; to: 300; stepSize: 5
                    value: Math.round(App.settings.timeout)
                    onValueModified: App.setSetting("download_timeout", value)
                }
                Label { text: qsTr("Download retries"); font.pixelSize: 12; color: Theme.onSurfaceVariant }
                SpinBox {
                    from: 0; to: 10
                    value: App.settings.retries
                    onValueModified: App.setSetting("download_retries", value)
                }
            }

            Card {
                Label { text: qsTr("Java runtime"); font.pixelSize: 16; font.weight: Font.Medium }
                Label {
                    text: qsTr("Detected runtimes")
                    color: Theme.onSurfaceVariant
                    font.pixelSize: 12
                }
                Repeater {
                    model: App.javaRuntimes
                    delegate: RowLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        Label { text: "Java " + modelData.major; Layout.preferredWidth: 80; font.weight: Font.Medium }
                        Label { text: modelData.version; Layout.fillWidth: true; elide: Text.ElideRight }
                        Label {
                            text: modelData.vendor
                            color: Theme.onSurfaceVariant
                            font.pixelSize: 11
                        }
                    }
                }
                Label {
                    visible: App.javaRuntimes.length === 0
                    text: qsTr("No Java runtime found. Install one below.")
                    color: Theme.onSurfaceVariant
                }
                RowLayout {
                    spacing: 8
                    Button { text: qsTr("Install Java 17"); onClicked: App.installJava(17) }
                    Button { text: qsTr("Install Java 21"); onClicked: App.installJava(21) }
                    Button { text: qsTr("Re-scan"); flat: true; onClicked: App.detect_java() }
                }
            }

            Card {
                Label { text: qsTr("Updates and diagnostics"); font.pixelSize: 16; font.weight: Font.Medium }
                RowLayout {
                    spacing: 8
                    Button { text: qsTr("Check for updates"); onClicked: App.checkUpdates() }
                    Button { text: qsTr("Open data folder"); flat: true; onClicked: App.openPath("root") }
                    Button { text: qsTr("Open logs"); flat: true; onClicked: App.openPath("logs") }
                }
                Label {
                    text: qsTr("Version list refresh")
                    color: Theme.onSurfaceVariant
                    font.pixelSize: 12
                }
                RowLayout {
                    CheckBox {
                        text: qsTr("Show snapshots")
                        checked: App.settings.showSnapshots
                        onToggled: App.setSetting("show_snapshots", checked)
                    }
                    Button { text: qsTr("Refresh versions"); flat: true; onClicked: App.load_versions(false) }
                }
            }

            Item { Layout.preferredHeight: 24 }
        }
    }
}
