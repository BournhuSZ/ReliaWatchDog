import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Popup {
    id: popup
    required property var theme
    required property var collection
    objectName: "collectionPopup"
    property int stage: 0
    property string lastManifest: ""
    modal: true
    focus: true
    padding: 16
    closePolicy: Popup.CloseOnEscape
    background: Rectangle { color: popup.theme.background; border.color: popup.theme.border; radius: 8 }
    Connections {
        target: collection
        function onChanged() {
            if (collection.status.manifest && collection.status.manifest !== popup.lastManifest) {
                popup.lastManifest = collection.status.manifest
                popup.stage = 1
            }
            if (collection.status.training) popup.stage = 2
        }
    }
    contentItem: ColumnLayout {
        spacing: 8
        RowLayout {
            Layout.fillWidth: true
            Label { text: "COLLECT / REVIEW / TRAIN"; color: popup.theme.heading; font.bold: true; font.pixelSize: 18 }
            Item { Layout.fillWidth: true }
            ActionButton { objectName: "collectionClose"; theme: popup.theme; text: "Close"; onClicked: popup.close() }
        }
        Label { Layout.fillWidth: true; text: "UNQUALIFIED · Candidates are not automatically normal. Review every image and explicitly confirm undamaged before Train. Closing this panel leaves jobs running. Training never activates a live model."; wrapMode: Text.WordWrap; color: popup.theme.secondary; font.pixelSize: 12 }
        RowLayout {
            Layout.fillWidth: true
            ActionButton { objectName: "collectionStage"; theme: popup.theme; text: (popup.stage === 0 ? "● " : "") + "Collection"; onClicked: popup.stage = 0 }
            ActionButton { objectName: "reviewStage"; theme: popup.theme; text: (popup.stage === 1 ? "● " : "") + "Review"; onClicked: popup.stage = 1 }
            ActionButton { objectName: "trainingStage"; theme: popup.theme; text: (popup.stage === 2 ? "● " : "") + "Training"; onClicked: popup.stage = 2 }
            Label { Layout.fillWidth: true; text: collection.status.state; color: popup.theme.secondary; elide: Text.ElideRight }
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12
            RegionCanvas {
                objectName: "trainingCanvas"
                Layout.fillWidth: true
                Layout.fillHeight: true
                theme: popup.theme
                source: collection.status.imageSource
                region: collection.status.region
                regionLabel: "Training ROI"
                emptyText: popup.stage === 0 ? "Connect a camera, choose Use camera frame, then draw a training ROI" : "Load a collection and select a candidate to review"
                editable: collection.status.editable && popup.stage === 0
                onRegionEdited: function(left, top, width, height) { collection.setRegion(left, top, width, height) }
                Accessible.name: "Candidate collection training ROI editor"
            }
            ColumnLayout {
                Layout.minimumWidth: 295
                Layout.maximumWidth: 295
                Layout.preferredWidth: 295
                Layout.fillHeight: true
                Label { Layout.fillWidth: true; text: collection.status.identity; wrapMode: Text.WordWrap; color: popup.theme.text; maximumLineCount: 2; elide: Text.ElideMiddle }
                Label { text: "Saved " + collection.status.saved + " / " + collection.status.target + " · Reviewed " + collection.status.reviewed + " / " + collection.status.samples; color: popup.theme.secondary }
                Label { text: collection.status.values.mode === "manual" ? "Operator-declared capture" : "Visually observed cycles: " + collection.status.observed; color: popup.theme.secondary; font.pixelSize: 12 }
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
                            visible: popup.stage === 0
                            Layout.fillWidth: true
                            Label { Layout.fillWidth: true; text: collection.status.sourceStatus; wrapMode: Text.WordWrap; color: popup.theme.secondary }
                            RowLayout {
                                ActionButton { theme: popup.theme; text: (collection.status.values.mode === "auto" ? "● " : "") + "Auto"; enabled: collection.status.editable; onClicked: collection.editField("mode", "auto") }
                                ActionButton { theme: popup.theme; text: (collection.status.values.mode === "manual" ? "● " : "") + "Manual"; enabled: collection.status.editable; onClicked: collection.editField("mode", "manual") }
                                EntryField { objectName: "collectionTarget"; theme: popup.theme; Layout.fillWidth: true; Layout.minimumWidth: 0; implicitWidth: 70; text: String(collection.status.values.target); enabled: collection.status.editable; onTextEdited: collection.editField("target", Number(text)); Accessible.name: "Candidate target 20 to 30 images" }
                            }
                            Label { Layout.fillWidth: true; text: collection.status.values.mode === "auto" ? "Auto requires counting-only marker synchronization. Model-bound inspection cannot collect training candidates." : "Manual: complete a physical cycle and click Capture cycle once on a fresh frame."; wrapMode: Text.WordWrap; color: popup.theme.secondary; font.pixelSize: 12 }
                            ActionButton { objectName: "collectionConfigure"; theme: popup.theme; text: collection.status.restart ? "Restart synchronization" : "Configure synchronization"; visible: collection.status.values.mode === "auto"; enabled: collection.status.canConfigure; onClicked: collection.configureSynchronization() }
                            ActionButton { objectName: "collectionUseFrame"; theme: popup.theme; text: "Use camera frame / new draft"; enabled: collection.status.canUseFrame; onClicked: collection.useCameraFrame() }
                            Label { text: "Training ROI · % of image"; color: popup.theme.secondary }
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
                                            objectName: "trainingCoordinate" + index
                                            theme: popup.theme
                                            Layout.fillWidth: true
                                            Layout.minimumWidth: 0
                                            implicitWidth: 70
                                            text: (collection.status.coordinates[index] * 100).toFixed(2)
                                            enabled: collection.status.editable
                                            onTextEdited: edited = true
                                            onEditingFinished: {
                                                if (!edited) return
                                                edited = false
                                                let values = collection.status.coordinates.slice()
                                                values[index] = Number(text) / 100
                                                collection.setRegion(values[0], values[1], values[2], values[3])
                                            }
                                        }
                                    }
                                }
                            }
                            ActionButton { theme: popup.theme; text: "Output folder"; enabled: collection.status.editable; onClicked: collection.chooseFolder() }
                            Label { Layout.fillWidth: true; text: collection.status.values.folder; wrapMode: Text.WrapAnywhere; color: popup.theme.secondary; font.pixelSize: 11 }
                        }
                        ColumnLayout {
                            visible: popup.stage === 1
                            Layout.fillWidth: true
                            RowLayout {
                                ActionButton { objectName: "previousCandidate"; theme: popup.theme; text: "Previous"; enabled: collection.status.canReview && collection.status.sample > 1; onClicked: collection.selectSample(collection.status.sample - 1) }
                                Label { text: collection.status.sample + " / " + collection.status.samples; color: popup.theme.text }
                                ActionButton { objectName: "nextCandidate"; theme: popup.theme; text: "Next"; enabled: collection.status.canReview && collection.status.sample < collection.status.samples; onClicked: collection.selectSample(collection.status.sample + 1) }
                            }
                            Switch { text: "ROI crop"; checked: collection.status.crop; enabled: collection.status.canReview; onToggled: collection.setCrop(checked) }
                            Label { text: collection.status.samples ? "Cycle " + collection.status.sampleCycle + " · Frame " + collection.status.sampleFrame : "No candidate selected"; color: popup.theme.text }
                            Label { Layout.fillWidth: true; text: "Inspect every source/crop for damaged specimens, incorrect return pose and bad illumination. Incomplete or changed candidates cannot train."; wrapMode: Text.WordWrap; color: popup.theme.secondary }
                        }
                        ColumnLayout {
                            visible: popup.stage === 2
                            Layout.fillWidth: true
                            ActionButton { theme: popup.theme; text: "Choose training Python"; enabled: collection.status.canChoose; onClicked: collection.choosePython() }
                            Label { Layout.fillWidth: true; text: collection.status.values.python; wrapMode: Text.WrapAnywhere; color: popup.theme.secondary; font.pixelSize: 11 }
                            Label { Layout.fillWidth: true; text: "Uses the existing separate Anomalib environment. Fitting a normal baseline does not validate defect accuracy."; wrapMode: Text.WordWrap; color: popup.theme.secondary; font.pixelSize: 12 }
                            Repeater {
                                model: [{key:"familyName",label:"Family recipe name"},{key:"notes",label:"Purpose notes"},{key:"threshold",label:"Detector threshold"},{key:"margin",label:"Inclusive review margin"}]
                                ColumnLayout {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    Label { text: modelData.label; color: popup.theme.secondary }
                                    EntryField { objectName: "training" + modelData.key; theme: popup.theme; Layout.fillWidth: true; text: String(collection.status.values[modelData.key]); enabled: collection.status.canChoose; maximumLength: modelData.key === "notes" ? 2000 : 128; onTextEdited: collection.editField(modelData.key, modelData.key === "threshold" || modelData.key === "margin" ? Number(text) : text) }
                                }
                            }
                            Label { Layout.fillWidth: true; text: "Needs review: " + (collection.status.values.threshold - collection.status.values.margin).toFixed(3) + " – " + (Number(collection.status.values.threshold) + Number(collection.status.values.margin)).toFixed(3) + " (inclusive). Not a confidence interval."; wrapMode: Text.WordWrap; color: popup.theme.secondary; font.pixelSize: 12 }
                            ActionButton { objectName: "loadTrainingExport"; theme: popup.theme; text: "Load trained model"; enabled: collection.status.canLoadExport; onClicked: collection.chooseExport() }
                            ActionButton { objectName: "inspectTrainingExport"; theme: popup.theme; text: "Inspect export offline"; enabled: collection.status.canExport; onClicked: collection.inspectExport() }
                            Label { Layout.fillWidth: true; text: collection.status.export; wrapMode: Text.WrapAnywhere; color: popup.theme.secondary; font.pixelSize: 11 }
                        }
                    }
                }
                CheckBox { objectName: "confirmUndamaged"; text: "Specimen remained undamaged"; visible: popup.stage !== 0; checked: collection.status.confirmed; enabled: collection.status.canConfirm; onToggled: collection.setConfirmed(checked) }
                ActionButton { objectName: "loadCandidateCollection"; theme: popup.theme; Layout.fillWidth: true; text: "Load collection"; enabled: collection.status.canChoose; onClicked: collection.chooseCollection() }
                ActionButton { objectName: "collectCandidates"; theme: popup.theme; kind: "start"; Layout.fillWidth: true; text: "Collect candidates"; visible: popup.stage === 0; enabled: collection.status.canCollect; onClicked: collection.collect() }
                RowLayout {
                    visible: popup.stage === 0 && collection.status.captureActive
                    Layout.fillWidth: true
                    ActionButton { objectName: "captureCandidateCycle"; theme: popup.theme; Layout.fillWidth: true; text: "Capture cycle"; visible: collection.status.values.mode === "manual"; enabled: collection.status.canCapture; onClicked: collection.captureCycle() }
                    ActionButton { objectName: "stopCandidateCollection"; theme: popup.theme; kind: "stop"; Layout.fillWidth: true; text: "Stop collection"; enabled: collection.status.canStop; onClicked: collection.stopCollection() }
                }
                RowLayout {
                    visible: popup.stage !== 0
                    Layout.fillWidth: true
                    ActionButton { objectName: "trainPatchCore"; theme: popup.theme; kind: "start"; Layout.fillWidth: true; text: "Train PatchCore"; enabled: collection.status.canTrain; onClicked: collection.train() }
                    ActionButton { objectName: "cancelCandidateTraining"; theme: popup.theme; kind: "stop"; Layout.fillWidth: true; text: "Cancel training"; enabled: collection.status.training; onClicked: collection.cancelTraining() }
                }
                ActionButton { objectName: "saveCandidateRecipe"; theme: popup.theme; Layout.fillWidth: true; text: "Save inspection recipe"; visible: popup.stage === 2; enabled: collection.status.canExport; onClicked: collection.chooseInspectionRecipe() }
            }
        }
        ScrollView {
            visible: popup.stage === 2 && !!collection.status.log
            Layout.fillWidth: true
            Layout.preferredHeight: 90
            clip: true
            TextArea { readOnly: true; text: collection.status.log; color: popup.theme.text; wrapMode: TextEdit.WrapAnywhere; selectByMouse: true; background: Rectangle { color: popup.theme.colors.raised; border.color: popup.theme.border } }
        }
        Label { Layout.fillWidth: true; visible: !!collection.status.error; text: collection.status.error; color: popup.theme.colors.review; wrapMode: Text.WrapAnywhere; maximumLineCount: 2; elide: Text.ElideRight }
    }
}
