import QtQuick

QtObject {
    required property var colors
    readonly property color background: colors.background
    readonly property color surface: colors.surface
    readonly property color border: colors.border
    readonly property color text: colors.text
    readonly property color heading: colors.heading
    readonly property color secondary: colors.secondary
    readonly property color focus: colors.focus
    readonly property int gap: 12
    readonly property int radius: 8
    readonly property int controlHeight: 36
    function ink(tone) { return colors[tone] || colors.neutral }
    function tint(tone) { return colors[tone + "Surface"] || colors.neutralSurface }
    function outline(tone) { return colors[tone + "Border"] || colors.border }
}
