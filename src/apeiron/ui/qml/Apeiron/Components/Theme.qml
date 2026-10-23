pragma Singleton
import QtQuick

/*
  Design tokens approximating the Material 3 baseline colour roles.

  Qt Quick Controls' Material style implements Material 2: it exposes
  `foreground`, `background`, `accent`, `elevation`, ... but not the Material 3
  semantic roles (`onSurface`, `surfaceContainer`, `primaryContainer`).  Apeiron
  therefore maps the tokens it uses onto explicit, documented values so the UI
  stays consistent instead of relying on properties that do not exist.
*/
QtObject {
    id: theme

    // "system" | "light" | "dark" — bound from the application settings.
    property string mode: "system"

    readonly property bool dark: mode === "dark"
                                 || (mode === "system"
                                     && Qt.styleHints.colorScheme === Qt.ColorScheme.Dark)

    readonly property color surface: dark ? "#141218" : "#FEF7FF"
    readonly property color surfaceContainer: dark ? "#211F26" : "#F3EDF7"
    readonly property color surfaceContainerHigh: dark ? "#2B2930" : "#ECE6F0"
    readonly property color onSurface: dark ? "#E6E0E9" : "#1D1B20"
    readonly property color onSurfaceVariant: dark ? "#CAC4D0" : "#49454F"
    readonly property color outline: dark ? "#938F99" : "#79747E"

    readonly property color primary: dark ? "#AAC7FF" : "#415F91"
    readonly property color onPrimary: dark ? "#0A305F" : "#FFFFFF"
    readonly property color primaryContainer: dark ? "#284777" : "#D6E3FF"
    readonly property color onPrimaryContainer: dark ? "#D6E3FF" : "#001B3E"

    readonly property color error: dark ? "#FFB4AB" : "#BA1A1A"
    readonly property color success: dark ? "#7ADFA0" : "#146C2E"

    readonly property color divider: Qt.rgba(onSurface.r, onSurface.g, onSurface.b, 0.10)
    readonly property color scrim: Qt.rgba(0, 0, 0, 0.4)
}
