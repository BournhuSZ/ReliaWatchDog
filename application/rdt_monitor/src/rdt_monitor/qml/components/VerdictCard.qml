import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Panel {
    id: card
    required property var status
    property bool compact: false
    title: "Latest inspection"
    implicitHeight: compact ? 144 : 292
    ColumnLayout {
        anchors.fill: parent
        spacing: 8
        Label {
            visible: card.compact
            Layout.fillWidth: true
            text: card.status.verdictLabel
            color: card.theme.ink(card.status.verdictTone)
            font.pixelSize: 22
            font.bold: true
        }
        Rectangle {
            visible: !card.compact
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: 156
            Layout.preferredHeight: 156
            radius: width / 2
            color: card.theme.colors.verdictSurface
            border.width: 2
            border.color: card.theme.ink(card.status.verdictTone)
            Column {
                anchors.centerIn: parent
                spacing: 10
                width: parent.width - 16
                Label {
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    text: ({normal: "✓", review: "!", defect: "×", neutral: "—"})[card.status.verdictTone]
                    color: card.theme.ink(card.status.verdictTone)
                    font.family: "Segoe UI Symbol"
                    font.pixelSize: 30
                }
                Label {
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    text: card.status.verdictLabel.toUpperCase()
                    color: card.theme.ink(card.status.verdictTone)
                    font.pixelSize: card.status.verdictTone === "normal" || card.status.verdictTone === "defect" ? 20 : 13
                    font.bold: true
                }
            }
        }
        Label {
            Layout.fillWidth: true
            text: card.status.resultCycle ? "Cycle " + card.status.resultCycle + " · Frame " + card.status.resultFrame : "No completed inspection"
            color: card.theme.secondary
            font.pixelSize: 12
        }
        RowLayout {
            Layout.fillWidth: true
            Label { text: "Score  " + card.status.score; color: card.theme.text }
            Item { Layout.fillWidth: true }
            Label { text: card.status.evidence; color: card.theme.ink(card.status.evidenceTone); font.pixelSize: 12 }
        }
        Item { Layout.fillHeight: true; visible: !card.compact }
    }
}
