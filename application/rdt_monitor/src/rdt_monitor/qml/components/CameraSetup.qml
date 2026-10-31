import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Popup {
    id: popup
    required property var theme
    required property var setup
    objectName: "cameraSetupPopup"
    property bool advanced: false
    visible: setup.status.open
    modal: true
    focus: true
    padding: 16
    closePolicy: Popup.CloseOnEscape
    onOpened: advanced = false
    onClosed: { if (setup.status.open) setup.cancel() }
    background: Rectangle { color: popup.theme.background; border.color: popup.theme.border; radius: 8 }
    contentItem: ColumnLayout {
        spacing: 8
        RowLayout {
            Layout.fillWidth: true
            Label { text: "FROZEN CAMERA SETUP / UNQUALIFIED"; color: popup.theme.heading; font.bold: true; font.pixelSize: 18 }
            Item { Layout.fillWidth: true }
            ActionButton { objectName: "cancelCameraSetup"; theme: popup.theme; text: "Cancel"; onClicked: setup.cancel() }
        }
        Label { Layout.fillWidth: true; text: "Capture continues separately. Check both markers, the fixed cyan inspection crop and test cycles after remounting. Marker adjustment does not align the inspected surface."; wrapMode: Text.WordWrap; color: popup.theme.secondary; font.pixelSize: 12 }
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                RowLayout {
                    Rectangle { width: 8; height: 16; color: popup.theme.colors.startMarker }
                    ActionButton { objectName: "setupSelectStart"; theme: popup.theme; text: (setup.status.role === 0 ? "● " : "") + "Start marker"; onClicked: setup.selectRole(0) }
                    Rectangle { width: 8; height: 16; color: popup.theme.colors.positionMarker }
                    ActionButton { objectName: "setupSelectPosition"; theme: popup.theme; text: (setup.status.role === 1 ? "● " : "") + "Position marker"; onClicked: setup.selectRole(1) }
                    ActionButton { objectName: "setupClearRegion"; theme: popup.theme; text: "Clear"; onClicked: setup.clearRegion() }
                }
                RegionCanvas {
                    objectName: "setupCanvas"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    theme: popup.theme
                    source: setup.status.imageSource
                    region: setup.status.region
                    regionColor: popup.theme.colors[setup.status.role === 0 ? "startMarker" : "positionMarker"]
                    regionLabel: setup.status.role === 0 ? "Start marker" : "Position marker"
                    backgroundRegions: setup.status.backgroundRegions
                    onRegionSelected: function(role) { setup.selectRole(role) }
                    onRegionEdited: function(left, top, width, height) { setup.setRegion(left, top, width, height) }
                    Accessible.name: "Frozen camera marker region editor"
                }
            }
            ColumnLayout {
                Layout.preferredWidth: 300
                Layout.minimumWidth: 300
                Layout.maximumWidth: 300
                Layout.fillHeight: true
                RowLayout {
                    ActionButton { objectName: "setupBasic"; theme: popup.theme; text: (popup.advanced ? "" : "● ") + "Basic"; onClicked: popup.advanced = false }
                    ActionButton { objectName: "setupAdvanced"; theme: popup.theme; text: (popup.advanced ? "● " : "") + "Advanced"; onClicked: popup.advanced = true }
                }
                ScrollView {
                    id: scroll
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    contentWidth: availableWidth
                    clip: true
                    ColumnLayout {
                        width: scroll.availableWidth
                        spacing: 8
                        ColumnLayout {
                            visible: !popup.advanced
                            Layout.fillWidth: true
                            Repeater {
                                model: [{key:"specimenId",label:"Actual specimen serial"},{key:"name",label:"Name (optional)"},{key:"batch",label:"Batch (optional)"},{key:"family",label:"UUT family (optional)"},{key:"articleNumber",label:"Article number (12 digits)"}]
                                ColumnLayout {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    Label { text: modelData.label; color: popup.theme.secondary }
                                    EntryField {
                                        objectName: "setup" + modelData.key
                                        theme: popup.theme
                                        Layout.fillWidth: true
                                        maximumLength: 128
                                        text: String(setup.status.values[modelData.key] || "")
                                        Accessible.name: modelData.label
                                        onTextEdited: setup.editField(modelData.key, text)
                                    }
                                }
                            }
                            Label { Layout.fillWidth: true; text: setup.status.values.recipeName || "Unsaved counting-only setup"; wrapMode: Text.WordWrap; color: popup.theme.heading; font.bold: true }
                            Label { Layout.fillWidth: true; text: setup.status.recipeFile; wrapMode: Text.WrapAnywhere; color: popup.theme.secondary; font.pixelSize: 12 }
                            ActionButton { objectName: "setupLoadRecipe"; theme: popup.theme; text: "Load recipe"; onClicked: setup.chooseRecipe() }
                            ActionButton { objectName: "setupRefreshReferences"; theme: popup.theme; text: "Use frozen marker appearance"; enabled: setup.status.values.recognition === "appearance"; onClicked: setup.refreshReferences() }
                            Label { Layout.fillWidth: true; text: setup.status.bound ? "UNQUALIFIED INSPECTION · Check the cyan trained ROI. Model and inspection geometry require a new trained family recipe to change." : "COUNTING ONLY · No defect detector is selected."; wrapMode: Text.WordWrap; color: popup.theme.secondary; font.pixelSize: 12 }
                        }
                        ColumnLayout {
                            visible: popup.advanced
                            Layout.fillWidth: true
                            Label { text: "Selected marker · X / Y / W / H (%)"; color: popup.theme.text }
                            GridLayout {
                                Layout.fillWidth: true
                                columns: 2
                                Repeater {
                                    model: ["X", "Y", "Width", "Height"]
                                    ColumnLayout {
                                        required property string modelData
                                        required property int index
                                        Layout.fillWidth: true
                                        Label { text: modelData; color: popup.theme.secondary }
                                        EntryField {
                                            property bool edited: false
                                            objectName: "setupCoordinate" + index
                                            theme: popup.theme
                                            Layout.fillWidth: true
                                            Layout.minimumWidth: 0
                                            implicitWidth: 80
                                            text: (Number(setup.status.coordinates[index]) * 100).toFixed(4)
                                            inputMethodHints: Qt.ImhFormattedNumbersOnly
                                            Accessible.name: "Selected marker " + modelData + " percent"
                                            onTextEdited: edited = true
                                            onEditingFinished: {
                                                if (!edited) return
                                                edited = false
                                                let values = setup.status.coordinates.slice()
                                                values[index] = Number(text) / 100
                                                setup.setRegion(values[0], values[1], values[2], values[3])
                                            }
                                        }
                                    }
                                }
                            }
                            Label { text: "Recognition"; color: popup.theme.secondary }
                            RowLayout {
                                ActionButton { theme: popup.theme; text: (setup.status.values.recognition === "appearance" ? "● " : "") + "Appearance"; onClicked: setup.editField("recognition", "appearance") }
                                ActionButton { theme: popup.theme; text: (setup.status.values.recognition === "brightness" ? "● " : "") + "Brightness"; onClicked: setup.editField("recognition", "brightness") }
                            }
                            Repeater {
                                model: [{key:"stable",label:"Stable frames (1-120)"},{key:"gap",label:"Maximum gap (s)"},{key:"timeout",label:"Cycle timeout (s)"},{key:"brightness",label:"Legacy brightness (0-255)"},{key:"threshold",label:"Detector threshold"},{key:"margin",label:"Inclusive review margin"}]
                                ColumnLayout {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    visible: setup.status.bound || (modelData.key !== "threshold" && modelData.key !== "margin")
                                    Label { text: modelData.label; color: popup.theme.secondary }
                                    EntryField {
                                        objectName: "setup" + modelData.key
                                        theme: popup.theme
                                        Layout.fillWidth: true
                                        enabled: modelData.key !== "brightness" || setup.status.values.recognition === "brightness"
                                        text: String(setup.status.values[modelData.key])
                                        inputMethodHints: Qt.ImhFormattedNumbersOnly
                                        Accessible.name: modelData.label
                                        onTextEdited: setup.editField(modelData.key, Number(text))
                                    }
                                }
                            }
                            Label { text: "Recipe name"; color: popup.theme.secondary }
                            EntryField { objectName: "setupRecipeName"; theme: popup.theme; Layout.fillWidth: true; maximumLength: 128; text: setup.status.values.recipeName || ""; onTextEdited: setup.editField("recipeName", text) }
                            Label { text: "Purpose notes"; color: popup.theme.secondary }
                            TextArea {
                                Layout.fillWidth: true
                                implicitHeight: 80
                                text: setup.status.values.notes || ""
                                color: popup.theme.text
                                wrapMode: TextEdit.WordWrap
                                selectByMouse: true
                                background: Rectangle { color: popup.theme.colors.raised; border.width: parent.activeFocus ? 2 : 1; border.color: parent.activeFocus ? popup.theme.focus : popup.theme.border; radius: 6 }
                                onTextChanged: { if (activeFocus && text !== setup.status.values.notes) setup.editField("notes", text) }
                            }
                            Label { Layout.fillWidth: true; text: setup.status.detector; wrapMode: Text.WrapAnywhere; color: popup.theme.secondary; font.pixelSize: 12 }
                        }
                    }
                }
                ActionButton { objectName: "setupSaveRevision"; theme: popup.theme; Layout.fillWidth: true; text: "Save new recipe revision"; enabled: setup.status.canSave; onClicked: setup.chooseRevision() }
            }
        }
        Label { Layout.fillWidth: true; visible: !!setup.status.error; text: setup.status.error; wrapMode: Text.WrapAnywhere; maximumLineCount: 2; elide: Text.ElideRight; color: popup.theme.colors.review }
        RowLayout {
            Layout.fillWidth: true
            CheckBox { objectName: "confirmCameraSetup"; Layout.fillWidth: true; text: "Markers, inspection crop and test cycles checked"; checked: setup.status.confirmed; enabled: setup.status.canConfirm; onToggled: setup.setConfirmed(checked) }
            ActionButton { objectName: "useCameraSetup"; theme: popup.theme; kind: "start"; text: "Use for this run"; enabled: setup.status.canUse; onClicked: setup.accept() }
        }
    }
}
