import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Panel {
    id: summary
    required property var status
    readonly property bool dense: height < 120
    title: dense ? "Run yield · Unqualified" : "Run summary · Unqualified"
    readonly property int total: status.passed + status.failed + status.reviewed
    readonly property string formula: "Yield = Pass / (Pass + Fail + Review). Pass means no defect detected with saved evidence. Pending and uninspected cycles are excluded. Totals apply to the current run."
    ColumnLayout {
        anchors.fill: parent
        spacing: 4
        RowLayout {
            Layout.fillWidth: true
            Label { text: summary.dense ? "Pass " + summary.status.passed + " · Fail " + summary.status.failed + " · Review " + summary.status.reviewed : "Yield · Pass / inspected"; color: summary.dense ? summary.theme.text : summary.theme.secondary; font.pixelSize: 11; Layout.fillWidth: true }
            Label {
                objectName: "yieldRate"
                text: summary.total > 0 && Number.isFinite(summary.status.yieldRate) ? summary.status.yieldRate.toFixed(1) + "%" : "—"
                font.pixelSize: summary.dense ? 21 : 23
                font.bold: true
                color: summary.theme.heading
                Accessible.name: "Current run yield: " + text + ". " + summary.formula
                ToolTip.visible: yieldHover.hovered
                ToolTip.text: summary.formula
                HoverHandler { id: yieldHover }
            }
        }
        RowLayout {
            visible: !summary.dense
            Layout.fillWidth: true
            spacing: 10
            Repeater {
                model: [
                    {label: "Pass", count: summary.status.passed, tone: "normal"},
                    {label: "Fail", count: summary.status.failed, tone: "defect"},
                    {label: "Review", count: summary.status.reviewed, tone: "review"}
                ]
                RowLayout {
                    required property var modelData
                    spacing: 4
                    Rectangle { width: 7; height: 7; color: summary.theme.ink(modelData.tone) }
                    Label { text: modelData.label + " " + modelData.count; color: summary.theme.text; font.pixelSize: 11 }
                }
            }
            Item { Layout.fillWidth: true }
        }
        Rectangle {
            objectName: "yieldChart"
            Layout.fillWidth: true
            Layout.preferredHeight: 8
            color: summary.theme.colors.raised
            clip: true
            Accessible.role: Accessible.Graphic
            Accessible.name: summary.total ? "Inspection distribution: Pass " + summary.status.passed + ", Fail " + summary.status.failed + ", Review " + summary.status.reviewed : "No completed inspections"
            Row {
                anchors.fill: parent
                Repeater {
                    model: [
                        {count: summary.status.passed, tone: "normal"},
                        {count: summary.status.failed, tone: "defect"},
                        {count: summary.status.reviewed, tone: "review"}
                    ]
                    Rectangle {
                        required property var modelData
                        width: summary.total ? parent.width * modelData.count / summary.total : 0
                        height: parent.height
                        color: summary.theme.ink(modelData.tone)
                    }
                }
            }
            ToolTip.visible: chartHover.hovered
            ToolTip.text: summary.formula
            HoverHandler { id: chartHover }
        }
        Label {
            visible: !summary.dense
            Layout.fillWidth: true
            text: summary.status.inspected + " inspected · " + summary.status.pending + " pending · " + summary.status.uninspected + " uninspected"
            color: summary.theme.secondary
            font.pixelSize: 10
            elide: Text.ElideRight
            ToolTip.visible: totalsHover.hovered
            ToolTip.text: text
            HoverHandler { id: totalsHover }
        }
    }
}
