import QtQuick
import QtQuick.Controls

TextField {
    id: control
    required property var theme
    implicitHeight: theme.controlHeight
    selectByMouse: true
    color: enabled ? theme.text : theme.colors.disabledText
    placeholderTextColor: theme.secondary
    selectionColor: theme.colors.accentSurface
    selectedTextColor: theme.text
    background: Rectangle {
        radius: 6
        color: control.enabled ? control.theme.colors.raised : control.theme.colors.disabledSurface
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus ? control.theme.focus : control.theme.border
    }
}
