import QtQuick
import QtQuick.Controls

Button {
    id: control
    required property var theme
    property string kind: "neutral"
    implicitHeight: theme.controlHeight
    horizontalPadding: 12
    hoverEnabled: true
    contentItem: Text {
        text: control.text
        font: control.font
        color: !control.enabled ? control.theme.colors.disabledText : control.kind === "start" ? control.theme.colors.startText : control.kind === "stop" ? control.theme.colors.stopText : control.theme.text
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: Rectangle {
        radius: 6
        color: !control.enabled ? control.theme.colors.disabledSurface : control.kind === "start" ? (control.down ? control.theme.colors.startPressed : control.hovered ? control.theme.colors.startHover : control.theme.colors.start) : control.kind === "stop" ? (control.hovered || control.down ? control.theme.colors.stopHover : control.theme.colors.stop) : control.down ? control.theme.colors.buttonPressed : control.hovered ? control.theme.colors.buttonHover : control.theme.colors.button
        border.width: control.visualFocus ? 2 : 1
        border.color: control.visualFocus ? control.theme.focus : !control.enabled ? control.theme.border : control.kind === "start" ? control.theme.colors.startBorder : control.kind === "stop" ? control.theme.colors.stopBorder : control.theme.border
    }
}
