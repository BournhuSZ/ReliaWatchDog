import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: header
    required property var theme
    required property var monitor
    required property var recipeCommand
    required property string timestamp
    readonly property bool wide: width >= 1256
    readonly property real lockX: sessionCard.x + sessionCard.width - 120
    readonly property real lockY: sessionCard.y + 6
    implicitHeight: wide ? 104 : 156

    function focusSpecimen() { specimen.forceActiveFocus(); specimen.selectAll() }

    GridLayout {
        anchors.fill: parent
        columns: header.wide ? 3 : 2
        columnSpacing: header.theme.gap
        rowSpacing: 8
        Item {
            objectName: "brandingArea"
            Layout.columnSpan: header.wide ? 1 : 2
            Layout.fillWidth: true
            Layout.preferredWidth: header.wide ? Math.round(header.width * 0.23) : 0
            Layout.minimumWidth: header.wide ? 180 : 0
            Layout.maximumWidth: header.wide ? 320 : 32767
            Layout.preferredHeight: header.wide ? 104 : 44
            Layout.minimumHeight: header.wide ? 104 : 44
            Layout.maximumHeight: header.wide ? 104 : 44
            RowLayout {
                anchors.top: parent.top
                anchors.left: parent.left
                anchors.right: parent.right
                height: 44
                spacing: 8
                Label { text: "RDT"; font.pixelSize: 32; font.bold: true; color: header.theme.heading }
                Rectangle { width: 1; height: 36; color: header.theme.border }
                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    spacing: 2
                    Label { Layout.fillWidth: true; text: "Reliability Monitor"; color: header.theme.heading; font.pixelSize: 18; font.bold: true; elide: Text.ElideRight }
                    Label { Layout.fillWidth: true; text: "Machine Vision for Product Reliability"; color: header.theme.secondary; font.pixelSize: 12; elide: Text.ElideRight }
                }
            }
            StatusBadge {
                anchors.left: parent.left
                y: 56
                visible: header.wide && (header.monitor.status.collectionActive || header.monitor.collection.status.training)
                theme: header.theme
                text: header.monitor.status.collectionActive ? "Collecting candidates" : "Training job"
                tone: "accent"
            }
        }
        Panel {
            objectName: "specimenToolbox"
            theme: header.theme
            contentPadding: 8
            radius: 3
            Layout.fillWidth: true
            Layout.minimumWidth: 280
            Layout.preferredHeight: 104
            Layout.minimumHeight: 104
            Layout.maximumHeight: 104
            RowLayout {
                anchors.fill: parent
                spacing: 12
                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    Layout.fillHeight: true
                    spacing: 2
                    Label { Layout.fillWidth: true; text: "SPECIMEN & RECIPE"; color: header.theme.secondary; font.pixelSize: 11; font.bold: true; elide: Text.ElideRight }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        Label {
                            objectName: "recipePartLabel"
                            Layout.fillWidth: true; Layout.minimumWidth: 0
                            text: "UUT family: " + header.monitor.status.uutFamily
                            color: header.theme.heading; font.pixelSize: 13; font.bold: true; elide: Text.ElideRight
                            Accessible.name: text
                            ToolTip.visible: partHover.hovered; ToolTip.text: header.monitor.status.recipeMetadata
                            HoverHandler { id: partHover }
                        }
                        Label {
                            objectName: "articleNumberLabel"
                            Layout.preferredWidth: 175; Layout.minimumWidth: 150
                            text: "Article: " + header.monitor.status.articleNumber
                            color: header.theme.heading; font.pixelSize: 12; font.bold: true; elide: Text.ElideRight
                            Accessible.name: text
                            ToolTip.visible: articleHover.hovered; ToolTip.text: text
                            HoverHandler { id: articleHover }
                        }
                    }
                    Label {
                        objectName: "recipeNameLabel"
                        Layout.fillWidth: true; Layout.minimumWidth: 0
                        text: "Recipe: " + header.monitor.status.recipeName
                        color: header.theme.text; font.pixelSize: 12; font.bold: true; elide: Text.ElideRight
                        ToolTip.visible: recipeHover.hovered; ToolTip.text: header.monitor.status.recipeName + " · " + header.monitor.status.recipeMode
                        HoverHandler { id: recipeHover }
                    }
                    Label {
                        objectName: "recipeModelLabel"
                        Layout.fillWidth: true; Layout.minimumWidth: 0
                        text: header.monitor.status.recipeModel
                        color: header.theme.secondary; font.pixelSize: 11; elide: Text.ElideRight
                        ToolTip.visible: modelHover.hovered; ToolTip.text: header.monitor.status.recipeMetadata
                        HoverHandler { id: modelHover }
                    }
                    Item { Layout.fillHeight: true }
                }
                ColumnLayout {
                    Layout.preferredWidth: 180
                    Layout.minimumWidth: 150
                    Layout.maximumWidth: 180
                    Layout.fillHeight: true
                    spacing: 3
                    ActionButton { objectName: "loadRecipeButton"; theme: header.theme; action: header.recipeCommand; text: "Load recipe"; Layout.alignment: Qt.AlignRight; Layout.preferredWidth: 110; Layout.preferredHeight: 24; Layout.minimumHeight: 24; Layout.maximumHeight: 24 }
                    Label { text: "Specimen ID"; color: header.theme.secondary; font.pixelSize: 12 }
                    EntryField {
                        id: specimen
                        objectName: "specimenField"
                        theme: header.theme
                        Layout.fillWidth: true
                        Layout.preferredHeight: 30; Layout.minimumHeight: 30; Layout.maximumHeight: 30
                        maximumLength: 128
                        placeholderText: "Actual serial"
                        text: header.monitor.specimenId
                        enabled: header.monitor.status.canEditSpecimen
                        onTextEdited: header.monitor.setSpecimenId(text)
                        Accessible.name: "Actual specimen ID"
                    }
                    Item { Layout.fillHeight: true }
                }
            }
        }

        Panel {
            id: sessionCard
            objectName: "sessionPanel"
            theme: header.theme
            contentPadding: 8
            radius: 3
            Layout.preferredWidth: 236
            Layout.minimumWidth: 236
            Layout.maximumWidth: 236
            Layout.preferredHeight: 104
            Layout.minimumHeight: 104
            Layout.maximumHeight: 104
            ColumnLayout {
                anchors.fill: parent
                spacing: 2
                RowLayout {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 24
                    Layout.minimumHeight: 24
                    Layout.maximumHeight: 24
                    spacing: 4
                    Label { Layout.fillWidth: true; text: "STATION SESSION"; color: header.theme.secondary; font.pixelSize: 11; font.bold: true; elide: Text.ElideRight }
                    Item { Layout.preferredWidth: 104; Layout.minimumWidth: 104 }
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    Rectangle { objectName: "monitoringIndicator"; width: 18; height: 18; radius: 9; color: header.theme.ink(header.monitor.status.healthTone) }
                    Label {
                        objectName: "headerRunStatus"
                        Layout.fillWidth: true
                        text: header.monitor.status.collectionActive ? "Collecting · " + header.monitor.status.runState : header.monitor.collection.status.training ? "Training · " + header.monitor.status.runState : "Monitoring · " + header.monitor.status.runState
                        color: header.theme.text
                        font.pixelSize: 14
                        font.bold: true
                        elide: Text.ElideRight
                        ToolTip.visible: statusHover.hovered
                        ToolTip.text: text
                        HoverHandler { id: statusHover }
                    }
                }
                Label { objectName: "headerTimestamp"; Layout.fillWidth: true; text: header.timestamp; color: header.theme.secondary; font.pixelSize: 12; elide: Text.ElideRight; Accessible.name: "Workstation local time: " + text }
                Label {
                    objectName: "headerUser"
                    Layout.fillWidth: true
                    text: "Windows user · " + header.monitor.sessionUser
                    color: header.theme.secondary
                    font.pixelSize: 12
                    elide: Text.ElideRight
                    ToolTip.visible: userHover.hovered
                    ToolTip.text: "Windows session user: " + header.monitor.sessionUser
                    Accessible.name: "Windows session user: " + header.monitor.sessionUser
                    HoverHandler { id: userHover }
                }
                Item { Layout.fillHeight: true }
            }
        }
    }
}
