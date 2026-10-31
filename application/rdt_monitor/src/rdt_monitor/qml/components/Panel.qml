import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: panel
    required property var theme
    property string title: ""
    property real contentPadding: 12
    default property alias content: body.data
    color: theme.surface
    border.color: theme.border
    radius: theme.radius
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: panel.contentPadding
        spacing: 8
        Label {
            Layout.fillWidth: true
            visible: !!panel.title
            text: panel.title.toUpperCase()
            color: panel.theme.secondary
            font.pixelSize: 11
            font.bold: true
            elide: Text.ElideRight
        }
        Item { id: body; Layout.fillWidth: true; Layout.fillHeight: true }
    }
}
