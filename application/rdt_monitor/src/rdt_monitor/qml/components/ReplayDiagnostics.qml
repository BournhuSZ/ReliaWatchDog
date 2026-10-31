import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Popup {
    id: popup
    required property var theme
    required property var monitor
    objectName: "replayPopup"
    property bool editing: false
    property var draft: ({})
    modal: true
    focus: true
    padding: 16
    closePolicy: Popup.CloseOnEscape
    onOpened: editing = false
    onClosed: { editing = false; draft = ({}) }
    background: Rectangle { color: popup.theme.surface; border.color: popup.theme.border; radius: 8 }
    function editSettings() {
        let current = monitor.status.replaySettings
        draft = {start: current.start.slice(), position: current.position.slice(), stable: current.stable, gap: current.gap, timeout: current.timeout, brightness: current.brightness}
        editing = true
    }
    contentItem: ColumnLayout {
        spacing: 10
        RowLayout {
            Layout.fillWidth: true
            Label { text: "REPLAY DIAGNOSTICS"; color: popup.theme.heading; font.bold: true; font.pixelSize: 18 }
            Item { Layout.fillWidth: true }
            ActionButton { objectName: "replayClose"; theme: popup.theme; text: "Close"; onClicked: popup.close() }
        }
        Label { Layout.fillWidth: true; text: "COUNTING ONLY / UNQUALIFIED · Recordings use source time, not live acquisition time. No defect assessment or inspection evidence is produced. Closing this panel leaves replay running; use Stop replay to stop it."; wrapMode: Text.WordWrap; color: popup.theme.secondary; font.pixelSize: 12 }
        ScrollView {
            id: scroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth
            clip: true
            ColumnLayout {
                width: scroll.availableWidth
                spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                    ActionButton { objectName: "chooseRecording"; theme: popup.theme; text: "Choose recording"; enabled: monitor.status.canReplay && !popup.editing; onClicked: monitor.chooseRecording() }
                    Label { Layout.fillWidth: true; text: monitor.status.recordingName; elide: Text.ElideMiddle; color: popup.theme.text; ToolTip.visible: recordingHover.hovered; ToolTip.text: monitor.status.recordingPath; HoverHandler { id: recordingHover } }
                }
                EntryField { id: replayLabel; objectName: "recordingLabel"; theme: popup.theme; Layout.fillWidth: true; placeholderText: "Specimen / replay label"; maximumLength: 128; enabled: monitor.status.canReplay; Accessible.name: "Specimen or replay label" }
                Label { Layout.fillWidth: true; text: "Recording settings: replay-v" + monitor.status.replayRevision + " · legacy brightness recognition. These settings do not modify the live inspection recipe. The synthetic fixture always uses its own known settings."; wrapMode: Text.WordWrap; color: popup.theme.secondary; font.pixelSize: 12 }
                ActionButton { objectName: "editReplaySettings"; theme: popup.theme; text: "Edit recording settings"; visible: !popup.editing; enabled: monitor.status.canReplay; onClicked: popup.editSettings() }
                ColumnLayout {
                    visible: popup.editing
                    enabled: monitor.status.canReplay
                    Layout.fillWidth: true
                    spacing: 8
                    Label { text: "Marker rectangles · X / Y / Width / Height (%)"; color: popup.theme.text }
                    Repeater {
                        model: ["start", "position"]
                        RowLayout {
                            required property string modelData
                            property string regionRole: modelData
                            Layout.fillWidth: true
                            Label { text: regionRole === "start" ? "Start" : "Position"; color: popup.theme.text; Layout.preferredWidth: 60 }
                            Repeater {
                                model: 4
                                EntryField {
                                    required property int index
                                    objectName: regionRole + "ReplayCoordinate" + index
                                    theme: popup.theme
                                    Layout.fillWidth: true
                                    Layout.minimumWidth: 0
                                    implicitWidth: 70
                                    text: popup.draft[regionRole] ? (popup.draft[regionRole][index] * 100).toFixed(2) : ""
                                    Accessible.name: regionRole + " marker " + ["X", "Y", "Width", "Height"][index] + " percent"
                                    inputMethodHints: Qt.ImhFormattedNumbersOnly
                                    onTextEdited: popup.draft[regionRole][index] = Number(text) / 100
                                }
                            }
                        }
                    }
                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2
                        Repeater {
                            model: [{key:"stable",label:"Stable frames"},{key:"gap",label:"Maximum gap (s)"},{key:"timeout",label:"Cycle timeout (s)"},{key:"brightness",label:"Brightness (0-255)"}]
                            ColumnLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                Label { text: modelData.label; color: popup.theme.secondary }
                                EntryField {
                                    objectName: modelData.key + "ReplaySetting"
                                    theme: popup.theme
                                    Layout.fillWidth: true
                                    Layout.minimumWidth: 0
                                    implicitWidth: 80
                                    text: popup.draft[modelData.key] === undefined ? "" : String(popup.draft[modelData.key])
                                    Accessible.name: modelData.label
                                    inputMethodHints: Qt.ImhFormattedNumbersOnly
                                    onTextEdited: popup.draft[modelData.key] = Number(text)
                                }
                            }
                        }
                    }
                }
            }
        }
        Label { Layout.fillWidth: true; visible: !!monitor.status.replayError; text: monitor.status.replayError; wrapMode: Text.WrapAnywhere; maximumLineCount: 3; elide: Text.ElideRight; color: popup.theme.colors.review }
        RowLayout {
            visible: popup.editing
            enabled: monitor.status.canReplay
            ActionButton { objectName: "applyReplaySettings"; theme: popup.theme; text: "Apply settings"; onClicked: { if (monitor.configureReplay(popup.draft)) popup.editing = false } }
            ActionButton { objectName: "cancelReplaySettings"; theme: popup.theme; text: "Cancel edits"; onClicked: { popup.editing = false; popup.draft = ({}) } }
        }
        RowLayout {
            Layout.fillWidth: true
            ActionButton { objectName: "startRecording"; theme: popup.theme; kind: "start"; Layout.fillWidth: true; text: "Start recording"; enabled: monitor.status.canReplay && !!monitor.status.recordingPath && !!replayLabel.text.trim() && !popup.editing; onClicked: { monitor.startRecording(replayLabel.text); if (monitor.status.isReplay) popup.close() } }
            ActionButton { objectName: "startSynthetic"; theme: popup.theme; Layout.fillWidth: true; text: "Start synthetic fixture"; enabled: monitor.status.canReplay && !!replayLabel.text.trim() && !popup.editing; onClicked: { monitor.startFixture(replayLabel.text); if (monitor.status.isReplay) popup.close() } }
            ActionButton { objectName: "stopReplay"; theme: popup.theme; kind: "stop"; Layout.fillWidth: true; text: "Stop replay"; enabled: monitor.status.canStopReplay; onClicked: monitor.stopReplay() }
        }
    }
}
