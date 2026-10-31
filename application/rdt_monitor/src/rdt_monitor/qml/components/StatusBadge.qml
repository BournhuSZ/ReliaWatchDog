import QtQuick
import QtQuick.Controls

Rectangle {
    id: badge
    required property var theme
    property string text: ""
    property string tone: "neutral"
    implicitWidth: caption.implicitWidth + 18
    implicitHeight: 27
    radius: 6
    color: theme.tint(tone)
    border.color: theme.outline(tone)
    Accessible.name: text
    Label {
        id: caption
        anchors.centerIn: parent
        text: badge.text
        color: badge.theme.ink(badge.tone)
        font.pixelSize: 12
        font.bold: true
    }
}
