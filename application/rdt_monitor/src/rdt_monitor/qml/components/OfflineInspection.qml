import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Popup {
    id: popup
    required property var theme
    required property var offline
    objectName: "offlinePopup"
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    padding: 16
    onClosed: offline.reset()
    background: Rectangle { color: popup.theme.background; border.color: popup.theme.border; radius: 8 }
    contentItem: ColumnLayout {
        spacing: 10
        RowLayout {
            Layout.fillWidth: true
            Label { text: "OFFLINE IMAGE INSPECTION"; color: popup.theme.heading; font.bold: true; font.pixelSize: 18 }
            Item { Layout.fillWidth: true }
            ActionButton { objectName: "offlineClose"; theme: popup.theme; text: "Close"; onClicked: popup.close() }
        }
        Label { Layout.fillWidth: true; text: "UNQUALIFIED / OFFLINE · Independent of live verdicts, counters and evidence. Scores are not probabilities."; wrapMode: Text.WordWrap; color: popup.theme.secondary; font.pixelSize: 12 }
        RowLayout {
            Layout.fillWidth: true
            ActionButton { theme: popup.theme; text: "Open image"; enabled: !offline.status.busy; onClicked: offline.chooseImage() }
            Label { Layout.fillWidth: true; text: offline.status.imagePath || "No image selected"; elide: Text.ElideMiddle; color: popup.theme.text; ToolTip.visible: imageHover.hovered; ToolTip.text: text; HoverHandler { id: imageHover } }
            ActionButton { theme: popup.theme; text: "Load model"; enabled: !offline.status.busy; onClicked: offline.chooseModel() }
            Label { Layout.preferredWidth: 200; text: offline.status.modelPath || "No trusted export selected"; elide: Text.ElideMiddle; color: popup.theme.text; ToolTip.visible: modelHover.hovered; ToolTip.text: text; HoverHandler { id: modelHover } }
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12
            RegionCanvas {
                objectName: "offlineCanvas"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumWidth: 280
                theme: popup.theme
                source: offline.status.imageSource
                region: offline.status.region
                editable: !offline.status.busy
                onRegionEdited: function(left, top, width, height) { offline.setRegion(left, top, width, height) }
            }
            ColumnLayout {
                Layout.preferredWidth: 280
                Layout.minimumWidth: 280
                Layout.maximumWidth: 280
                Layout.fillHeight: true
                spacing: 8
                ScrollView {
                id: scroll
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: availableWidth
                ColumnLayout {
                    width: scroll.availableWidth
                    spacing: 10
                    Label { text: "Inspection ROI · % of image"; color: popup.theme.text }
                    GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    Repeater {
                        model: ["X", "Y", "Width", "Height"]
                        RowLayout {
                            required property string modelData
                            required property int index
                            Layout.fillWidth: true
                            Label { text: modelData === "Width" ? "W" : modelData === "Height" ? "H" : modelData; color: popup.theme.secondary; Layout.preferredWidth: 18 }
                            EntryField {
                                objectName: "offlineCoordinate" + index
                                theme: popup.theme
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                implicitWidth: 80
                                enabled: !offline.status.busy && offline.status.hasImage
                                text: offline.status.region.length === 4 ? (offline.status.region[index] * 100).toFixed(2) : "0.00"
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                Accessible.name: "Offline ROI " + modelData + " percent"
                                onEditingFinished: {
                                    let values = offline.status.region.length === 4 ? offline.status.region.slice() : [0,0,0,0]
                                    values[index] = Number(text) / 100
                                    offline.setRegion(values[0], values[1], values[2], values[3])
                                }
                            }
                        }
                    }
                    }
                    RowLayout {
                        ActionButton { theme: popup.theme; text: "Full image"; enabled: offline.status.hasImage && !offline.status.busy; onClicked: offline.setRegion(0,0,1,1) }
                        ActionButton { theme: popup.theme; text: "Clear"; enabled: !offline.status.busy; onClicked: offline.clearRegion() }
                    }
                    Label { text: "Unqualified threshold"; color: popup.theme.secondary }
                    EntryField {
                        objectName: "offlineThreshold"
                        theme: popup.theme
                        Layout.fillWidth: true
                        enabled: !offline.status.busy
                        text: offline.status.threshold.toFixed(3)
                        inputMethodHints: Qt.ImhFormattedNumbersOnly
                        Accessible.name: "Offline unqualified threshold"
                        onEditingFinished: offline.setThreshold(Number(text))
                    }
                    Switch { text: "Heatmap"; checked: offline.status.heatmap; onToggled: offline.setHeatmap(checked) }
                }
                }
                    Label { Layout.fillWidth: true; text: offline.status.assessment; wrapMode: Text.WordWrap; color: popup.theme.ink(offline.status.tone); font.bold: true }
                    Label { text: "Score: " + offline.status.score; color: popup.theme.text }
                    ActionButton { objectName: "offlineInspect"; theme: popup.theme; kind: "start"; Layout.fillWidth: true; text: "Inspect image"; enabled: offline.status.canInspect; onClicked: offline.inspect() }
                    ActionButton { objectName: "offlineSave"; theme: popup.theme; Layout.fillWidth: true; text: "Save PNG snapshot"; enabled: offline.status.canSave; onClicked: offline.chooseSnapshot() }
                    Label { Layout.fillWidth: true; text: "Manual snapshots are not qualified inspection evidence. Heatmap intensity does not confirm damage."; wrapMode: Text.WordWrap; color: popup.theme.secondary; font.pixelSize: 12 }
            }
        }
        Label { Layout.fillWidth: true; visible: !!offline.status.error; text: offline.status.error; wrapMode: Text.WrapAnywhere; maximumLineCount: 3; elide: Text.ElideRight; color: popup.theme.colors.review }
    }
}
