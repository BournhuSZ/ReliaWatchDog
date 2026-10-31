import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "theme" as Tokens
import "components" as UI

ApplicationWindow {
    id: root
    required property var monitor
    property bool operationsLocked: false
    property int resizableMinimumWidth: 960
    property int resizableMinimumHeight: 640
    property int resizableMaximumWidth: 16777215
    property int resizableMaximumHeight: 16777215
    property string localTimestamp: Qt.formatDateTime(new Date(), "yyyy-MM-dd HH:mm:ss t")
    readonly property bool compact: height < 860
    visible: true
    width: 1280
    height: 800
    minimumWidth: 960
    minimumHeight: 640
    title: "RDT Reliability Monitor / QML"
    font.pixelSize: 14
    color: appTheme.background
    palette.window: appTheme.background
    palette.windowText: appTheme.text
    palette.base: appTheme.colors.raised
    palette.text: appTheme.text
    palette.button: appTheme.colors.button
    palette.buttonText: appTheme.text
    palette.highlight: appTheme.colors.accentSurface
    palette.highlightedText: appTheme.text
    palette.placeholderText: appTheme.secondary
    palette.toolTipBase: appTheme.surface
    palette.toolTipText: appTheme.text
    palette.disabled.buttonText: appTheme.colors.disabledText
    palette.disabled.text: appTheme.colors.disabledText

    Tokens.Theme { id: appTheme; colors: monitor.themeColors }
    onClosing: function(event) { event.accepted = !operationsLocked && monitor.requestClose() }
    Connections { target: monitor; function onCloseReady() { root.close() } }
    Component.onCompleted: setupHeader.focusSpecimen()
    Timer {
        interval: 1000
        running: root.visible
        repeat: true
        onTriggered: root.localTimestamp = Qt.formatDateTime(new Date(), "yyyy-MM-dd HH:mm:ss t")
    }

    Action { id: loadRecipeAction; objectName: "loadRecipeAction"; text: "&Load recipe…"; shortcut: StandardKey.Open; enabled: !root.operationsLocked && monitor.status.canLoadRecipe; onTriggered: recipeManager.open() }
    Action { id: resultsAction; objectName: "resultsAction"; enabled: !root.operationsLocked; text: "Open &results folder"; onTriggered: monitor.openResults() }
    Action { id: offlineAction; objectName: "offlineAction"; enabled: !root.operationsLocked; text: "&Offline inspection…"; onTriggered: offlineInspection.open() }
    Action { id: collectionAction; objectName: "collectionAction"; enabled: !root.operationsLocked; text: "&Collect / train…"; onTriggered: collectionTraining.open() }
    Action { id: diagnosticsAction; objectName: "diagnosticsAction"; enabled: !root.operationsLocked; text: "&Diagnostics / replay…"; onTriggered: diagnostics.open() }
    Action { id: detailsAction; objectName: "detailsAction"; enabled: !root.operationsLocked; text: "Run &details…"; onTriggered: details.open() }
    Action { id: lightAction; objectName: "lightAction"; enabled: !root.operationsLocked; text: "&Light appearance"; checkable: true; checked: monitor.lightMode; onTriggered: monitor.setLightMode(checked) }
    Action { id: settingsAction; objectName: "settingsAction"; enabled: !root.operationsLocked; text: "&Settings…"; onTriggered: settingsPopup.open() }
    Action {
        id: fixedSizeAction
        objectName: "fixedSizeAction"
        text: "Lock &window size"
        checkable: true
        enabled: !root.operationsLocked
        onTriggered: {
            if (checked) {
                root.resizableMinimumWidth = root.minimumWidth
                root.resizableMinimumHeight = root.minimumHeight
                root.resizableMaximumWidth = root.maximumWidth
                root.resizableMaximumHeight = root.maximumHeight
                root.minimumWidth = root.width
                root.minimumHeight = root.height
                root.maximumWidth = root.width
                root.maximumHeight = root.height
            } else {
                root.minimumWidth = root.resizableMinimumWidth
                root.minimumHeight = root.resizableMinimumHeight
                root.maximumWidth = root.resizableMaximumWidth
                root.maximumHeight = root.resizableMaximumHeight
            }
        }
    }
    Action {
        id: cameraAction
        objectName: "cameraAction"
        text: monitor.status.cameraConnected ? "Disconnect camera" : "Connect camera"
        enabled: !root.operationsLocked && (monitor.status.cameraConnected ? monitor.status.canDisconnect : monitor.status.canConnect)
        onTriggered: {
            if (monitor.status.cameraConnected) monitor.disconnectCamera()
            else monitor.connectCamera(cameraIndex.value)
        }
    }

    menuBar: MenuBar {
        objectName: "applicationMenuBar"
        enabled: !root.operationsLocked
        background: Rectangle { color: appTheme.surface; Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: appTheme.border } }
        Menu {
            objectName: "fileMenu"
            title: "&File"
            MenuItem { action: loadRecipeAction }
            MenuItem { objectName: "resultsMenuItem"; action: resultsAction }
            MenuSeparator {}
            MenuItem { text: "E&xit"; action: Action { enabled: !root.operationsLocked; shortcut: StandardKey.Quit; onTriggered: root.close() } }
        }
        Menu {
            title: "&Edit"
            MenuItem { text: "&Specimen ID"; enabled: monitor.status.canEditSpecimen; onTriggered: { setupHeader.focusSpecimen() } }
            MenuSeparator {}
            MenuItem { action: cameraAction }
        }
        Menu {
            objectName: "viewMenu"
            title: "&View"
            MenuItem { objectName: "settingsMenuItem"; action: settingsAction }
            MenuItem { action: lightAction }
            MenuItem { objectName: "fixedSizeMenuItem"; action: fixedSizeAction }
            MenuSeparator {}
            MenuItem { action: detailsAction }
        }
        Menu {
            objectName: "toolsMenu"
            title: "&Tools"
            MenuItem { objectName: "offlineMenuItem"; action: offlineAction }
            MenuItem { action: collectionAction }
            MenuSeparator {}
            MenuItem { action: diagnosticsAction }
        }
        Menu {
            title: "&Help"
            MenuItem { text: "&About RDT Reliability Monitor…"; onTriggered: aboutDialog.open() }
        }
    }

    ColumnLayout {
        id: workspace
        objectName: "workspace"
        enabled: !root.operationsLocked
        anchors.fill: parent
        anchors.margins: 12
        spacing: 10
        UI.SetupHeader {
            id: setupHeader
            objectName: "setupCards"
            Layout.fillWidth: true
            Layout.preferredHeight: implicitHeight
            Layout.minimumHeight: implicitHeight
            Layout.maximumHeight: implicitHeight
            theme: appTheme
            monitor: root.monitor
            recipeCommand: loadRecipeAction
            timestamp: root.localTimestamp
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: appTheme.gap
            UI.Panel {
                theme: appTheme
                objectName: "cameraPanel"
                Layout.fillWidth: true
                Layout.minimumWidth: 320
                Layout.fillHeight: true
                ColumnLayout {
                    anchors.fill: parent
                    spacing: 6
                    RowLayout {
                        objectName: "cameraHeader"
                        Layout.fillWidth: true
                        Layout.preferredHeight: 28
                        Layout.minimumHeight: 28
                        Layout.maximumHeight: 28
                        spacing: 6
                        Label { Layout.fillWidth: true; text: "CAMERA PREVIEW · " + monitor.status.sourceLabel; color: appTheme.secondary; font.pixelSize: 11; font.bold: true; elide: Text.ElideRight }
                        Label { text: "Camera"; color: appTheme.secondary; font.pixelSize: 12 }
                        SpinBox {
                            id: cameraIndex
                            objectName: "cameraIndex"
                            from: 0; to: 16
                            Layout.preferredWidth: 76
                            Layout.preferredHeight: 28
                            Layout.minimumHeight: 28
                            Layout.maximumHeight: 28
                            enabled: monitor.status.canConnect
                            Accessible.name: "Camera index"
                            background: Rectangle { color: cameraIndex.enabled ? appTheme.colors.raised : appTheme.colors.disabledSurface; radius: 6; border.width: cameraIndex.activeFocus ? 2 : 1; border.color: cameraIndex.activeFocus ? appTheme.focus : appTheme.border }
                        }
                        UI.StatusBadge { theme: appTheme; text: monitor.status.cameraLabel; tone: monitor.status.cameraTone }
                        UI.ActionButton { objectName: "cameraConnectionButton"; theme: appTheme; action: cameraAction; text: monitor.status.cameraConnected ? "Disconnect" : "Connect"; Layout.preferredWidth: 100; Layout.preferredHeight: 28; Layout.minimumHeight: 28; Layout.maximumHeight: 28 }
                    }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    color: appTheme.colors.imageBackground
                    Image {
                        id: cameraImage
                        objectName: "cameraImage"
                        anchors.fill: parent
                        source: monitor.previewSource
                        fillMode: Image.PreserveAspectFit
                        cache: false
                        Accessible.role: Accessible.Graphic
                        Accessible.name: "Camera preview · " + monitor.status.sourceLabel
                        Repeater {
                            model: monitor.status.regions
                            Rectangle {
                                required property var modelData
                                visible: !!monitor.previewSource
                                x: (cameraImage.width - cameraImage.paintedWidth) / 2 + modelData.left * cameraImage.paintedWidth
                                y: (cameraImage.height - cameraImage.paintedHeight) / 2 + modelData.top * cameraImage.paintedHeight
                                width: modelData.width * cameraImage.paintedWidth
                                height: modelData.height * cameraImage.paintedHeight
                                color: "transparent"
                                border.width: 2
                                border.color: appTheme.colors[modelData.role]
                                Rectangle {
                                    width: regionLabel.implicitWidth + 8
                                    height: 20
                                    color: appTheme.colors.overlayBackground
                                    Label { id: regionLabel; anchors.centerIn: parent; text: modelData.label; color: appTheme.colors[modelData.role]; font.pixelSize: 11 }
                                }
                            }
                        }
                    }
                    Column {
                        anchors.centerIn: parent
                        width: Math.min(parent.width - 32, 380)
                        spacing: 10
                        visible: !monitor.previewSource
                        Label { width: parent.width; text: "Ready for a camera source"; font.pixelSize: 18; font.bold: true; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; color: appTheme.heading }
                        Label { width: parent.width; text: "Connect a camera, enter the specimen ID and load a recipe to prepare inspection."; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; color: appTheme.secondary }
                    }
                }
                }
            }
            ColumnLayout {
                Layout.preferredWidth: 316
                Layout.minimumWidth: 316
                Layout.maximumWidth: 316
                Layout.fillHeight: true
                spacing: 10
                ScrollView {
                    id: verdictScroll
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.preferredHeight: inspectionStatus.implicitHeight
                    Layout.maximumHeight: inspectionStatus.implicitHeight
                    Layout.minimumHeight: 64
                    contentWidth: availableWidth
                    clip: true
                    ColumnLayout {
                        id: inspectionStatus
                        width: verdictScroll.availableWidth
                        spacing: 10
                        Rectangle {
                            objectName: "inspectionAlert"
                            visible: !!monitor.status.fault || !!monitor.status.coverageWarning || monitor.status.defects > 0
                            Layout.fillWidth: true
                            implicitHeight: alert.implicitHeight + 16
                            color: monitor.status.defects > 0 ? appTheme.colors.defectSurface : appTheme.colors.reviewSurface
                            border.color: monitor.status.defects > 0 ? appTheme.colors.defectBorder : appTheme.colors.reviewBorder
                            radius: 6
                            Label {
                                id: alert
                                anchors.fill: parent
                                anchors.margins: 8
                                text: (monitor.status.defects > 0 ? "DEFECT ALARM: " + monitor.status.defects + " (latched)   " : "") + monitor.status.coverageWarning + (monitor.status.coverageWarning && monitor.status.fault ? " · " : "") + monitor.status.fault
                                wrapMode: Text.WordWrap
                                color: monitor.status.defects > 0 ? appTheme.colors.defect : appTheme.colors.review
                                Accessible.name: text
                            }
                        }
                        UI.VerdictCard {
                            id: verdictCard
                            Layout.fillWidth: true
                            Layout.preferredHeight: implicitHeight
                            theme: appTheme
                            status: monitor.status
                            compact: root.compact
                        }
                    }
                }
                UI.Panel {
                    theme: appTheme
                    title: "Monitoring control"
                    Layout.fillWidth: true
                    Layout.preferredHeight: root.compact ? 142 : 164
                    Layout.minimumHeight: root.compact ? 142 : 164
                    ColumnLayout {
                        anchors.fill: parent
                        spacing: 6
                        RowLayout {
                            Layout.fillWidth: true
                            Label { Layout.fillWidth: true; text: "Visually observed cycles"; wrapMode: Text.WordWrap; color: appTheme.secondary; font.pixelSize: 12 }
                            Label { objectName: "observedCount"; text: monitor.status.observed; color: appTheme.heading; font.pixelSize: root.compact ? 28 : 34; font.bold: true }
                        }
                        Label { text: "Inspected " + monitor.status.inspected + "  |  Uninspected " + monitor.status.uninspected + "  |  Pending " + monitor.status.pending; font.pixelSize: 11; color: appTheme.secondary }
                        RowLayout {
                            Layout.fillWidth: true
                            UI.ActionButton { objectName: "startInspectionButton"; theme: appTheme; kind: "start"; Layout.fillWidth: true; text: monitor.status.restart ? "Restart" : "Start"; enabled: monitor.status.canStart; onClicked: monitor.startInspection() }
                            UI.ActionButton { objectName: "stopMonitoringButton"; theme: appTheme; kind: "stop"; Layout.fillWidth: true; text: "Stop"; enabled: monitor.status.canStop; onClicked: monitor.stopMonitoring() }
                        }
                    }
                }
                Item { Layout.fillHeight: true }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: root.compact ? 110 : 164
            Layout.minimumHeight: 88
            Layout.maximumHeight: Layout.preferredHeight
            spacing: appTheme.gap
            UI.Panel {
                objectName: "eventPanel"
                theme: appTheme
                Layout.fillWidth: true
                Layout.fillHeight: true
                ColumnLayout {
                    anchors.fill: parent
                    spacing: 4
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                    TabBar {
                        id: logTabs
                        objectName: "logTabs"
                        Layout.fillWidth: true
                        Layout.preferredHeight: 26
                        background: Rectangle { color: "transparent"; border.color: appTheme.border; border.width: 1 }
                        TabButton { objectName: "eventLogTab"; text: "Event log"; font.pixelSize: 12; Accessible.name: "Event log, session only" }
                        TabButton { objectName: "resultLogTab"; text: "Detection results"; font.pixelSize: 12; Accessible.name: "Detection results, session only" }
                    }
                    Label { text: "SESSION ONLY"; color: appTheme.secondary; font.pixelSize: 10 }
                    }
                    ListView {
                        objectName: "eventLog"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: logTabs.currentIndex === 0 ? monitor.eventModel : monitor.resultModel
                        ScrollBar.vertical: ScrollBar {}
                        Label { anchors.centerIn: parent; visible: parent.count === 0; text: logTabs.currentIndex === 0 ? "No events yet" : "No detection results yet"; color: appTheme.secondary; font.pixelSize: 12 }
                        delegate: RowLayout {
                            id: eventRow
                            required property string timestamp
                            required property string message
                            required property string detail
                            required property int cycle
                            required property int frameId
                            required property string severity
                            width: ListView.view.width
                            height: 26
                            spacing: 8
                            Rectangle { width: 3; height: 15; color: severity === "error" ? appTheme.colors.defect : severity === "warning" ? appTheme.colors.review : appTheme.border }
                            Label { text: timestamp; color: appTheme.secondary; font.pixelSize: 11; Layout.preferredWidth: eventRow.width < 420 ? 70 : 85; elide: Text.ElideRight }
                            Label { text: cycle > 0 ? "Cycle " + cycle : "—"; color: appTheme.secondary; font.pixelSize: 11; Layout.preferredWidth: 65; elide: Text.ElideRight }
                            Label { text: message; color: appTheme.text; font.pixelSize: 12; Layout.preferredWidth: eventRow.width < 420 ? 90 : 135; elide: Text.ElideRight }
                            Label { text: detail; color: appTheme.secondary; font.pixelSize: 12; Layout.fillWidth: true; elide: Text.ElideRight; ToolTip.visible: eventHover.hovered; ToolTip.text: detail + (frameId >= 0 ? " · Frame " + frameId : ""); HoverHandler { id: eventHover } }
                        }
                        onCountChanged: positionViewAtEnd()
                    }
                }
            }
            UI.RunSummary {
                objectName: "runSummary"
                theme: appTheme
                status: monitor.status
                Layout.preferredWidth: 300
                Layout.minimumWidth: 300
                Layout.maximumWidth: 300
                Layout.fillHeight: true
            }
            UI.Panel {
                objectName: "roiPanel"
                theme: appTheme
                title: monitor.status.resultCycle ? "Inspection ROI · Cycle " + monitor.status.resultCycle : "Latest inspection ROI"
                Layout.preferredWidth: 316
                Layout.fillHeight: true
                RowLayout {
                    anchors.fill: parent
                    spacing: 8
                    Image { objectName: "inspectionRoi"; Layout.fillWidth: true; Layout.fillHeight: true; source: monitor.roiSource; fillMode: Image.PreserveAspectFit; cache: false; Accessible.role: Accessible.Graphic; Accessible.name: monitor.status.resultCycle ? "Inspected ROI for cycle " + monitor.status.resultCycle : "No completed inspection ROI" }
                    Label { visible: !monitor.roiSource; Layout.fillWidth: true; text: monitor.status.resultCycle ? "Cycle " + monitor.status.resultCycle + " · ROI unavailable" : "No completed inspection"; color: appTheme.secondary; wrapMode: Text.WordWrap; font.pixelSize: 12 }
                    Switch { text: "Map"; checked: monitor.overlayEnabled; enabled: !!monitor.roiSource; onToggled: monitor.setOverlayEnabled(checked); Accessible.name: "Show heatmap on the exact inspected ROI" }
                }
            }
        }
        Label { text: root.operationsLocked ? "GUI LOCKED · Switch off Lock GUI to operate · Monitoring continues · UNQUALIFIED / ADVISORY ONLY" : "UNQUALIFIED · ADVISORY ONLY"; color: root.operationsLocked ? appTheme.colors.accent : appTheme.secondary; font.pixelSize: 10; Layout.alignment: Qt.AlignRight }
    }
    Switch {
        id: guiLock
        objectName: "guiLockSwitch"
        x: workspace.x + setupHeader.x + setupHeader.lockX
        y: workspace.y + setupHeader.y + setupHeader.lockY
        width: 112
        height: 32
        font.pixelSize: 12
        text: "Lock GUI"
        checked: root.operationsLocked
        Accessible.name: "Lock GUI operations"
        ToolTip.visible: hovered
        ToolTip.text: root.operationsLocked ? "Switch off Lock GUI to enable commands. Monitoring continues while locked." : "Disable all GUI operations, including Stop, until switched off"
        onToggled: {
            root.operationsLocked = checked
            guiLock.forceActiveFocus(Qt.TabFocusReason)
        }
    }
    UI.RecipeManager {
        id: recipeManager
        objectName: "recipeManager"
        x: Math.max(12, (root.width - width) / 2)
        y: Math.max(12, (root.contentItem.height - height) / 2)
        width: Math.min(1100, root.width - 24)
        height: Math.min(650, root.contentItem.height - 24)
        theme: appTheme
        monitor: root.monitor
        enabled: !root.operationsLocked
    }
    Popup {
        id: settingsPopup
        objectName: "settingsPopup"
        enabled: !root.operationsLocked
        x: Math.max(12, root.width - width - 12)
        y: 64
        width: Math.min(380, root.width - 24)
        height: Math.min(settingsContent.implicitHeight + 32, root.contentItem.height - 76)
        padding: 16
        focus: true
        background: Rectangle { color: appTheme.surface; border.color: appTheme.border; radius: appTheme.radius }
        contentItem: ScrollView {
            id: settingsScroll
            contentWidth: availableWidth
            clip: true
            ColumnLayout {
                id: settingsContent
                width: settingsScroll.availableWidth
                spacing: 12
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "Settings"; font.pixelSize: 18; font.bold: true; color: appTheme.heading; Layout.fillWidth: true }
                    UI.ActionButton { objectName: "closeSettingsButton"; theme: appTheme; text: "Close"; onClicked: settingsPopup.close() }
                }
                Switch { objectName: "settingsLightSwitch"; text: "Light appearance"; checked: monitor.lightMode; onToggled: monitor.setLightMode(checked) }
                Switch { objectName: "settingsMapSwitch"; text: "Show anomaly map on inspected ROI"; checked: monitor.overlayEnabled; onToggled: monitor.setOverlayEnabled(checked) }
                Label { Layout.fillWidth: true; text: "Windows user: " + monitor.sessionUser + "\nTime uses the workstation's local time zone."; color: appTheme.secondary; wrapMode: Text.WrapAnywhere; font.pixelSize: 12 }
                Label { Layout.fillWidth: true; text: "Lock GUI blocks all operations, including Stop and closing. Switch it off to operate again. Monitoring continues while locked."; color: appTheme.secondary; wrapMode: Text.WordWrap; font.pixelSize: 12 }
                Switch { objectName: "settingsLockSwitch"; text: "Lock GUI"; checked: root.operationsLocked; onToggled: { settingsPopup.close(); root.operationsLocked = checked; guiLock.forceActiveFocus(Qt.TabFocusReason) } }
            }
        }
    }
    UI.OfflineInspection {
        enabled: !root.operationsLocked
        id: offlineInspection
        theme: appTheme
        offline: monitor.offline
        x: 12; y: 12
        width: root.width - 24
        height: root.contentItem.height - 24
    }
    UI.CollectionTraining {
        enabled: !root.operationsLocked
        id: collectionTraining
        theme: appTheme
        collection: monitor.collection
        x: 12; y: 12
        width: root.width - 24
        height: root.contentItem.height - 24
    }
    Connections {
        target: monitor.collection
        function onInspectRequested() { collectionTraining.close(); offlineInspection.open() }
    }
    UI.CameraSetup {
        enabled: !root.operationsLocked
        theme: appTheme
        setup: monitor.setup
        x: 12; y: 12
        width: root.width - 24
        height: root.contentItem.height - 24
    }
    UI.ReplayDiagnostics {
        enabled: !root.operationsLocked
        id: diagnostics
        theme: appTheme
        monitor: root.monitor
        x: Math.max(12, (root.width - width) / 2)
        y: 60
        width: Math.min(760, root.width - 24)
        height: Math.min(650, root.contentItem.height - 76)
    }
    Popup {
        id: details
        enabled: !root.operationsLocked
        x: Math.max(12, root.width - width - 18)
        y: 60
        width: Math.min(460, root.width - 36)
        height: Math.min(detailsColumn.implicitHeight + 32, root.contentItem.height - 76)
        padding: 16
        focus: true
        background: Rectangle { color: appTheme.surface; border.color: appTheme.border; radius: appTheme.radius }
        contentItem: ScrollView {
            id: detailsScroll
            contentWidth: availableWidth
            clip: true
            ColumnLayout {
            id: detailsColumn
            width: detailsScroll.availableWidth
            spacing: 10
            Label { text: "RUN DETAILS"; color: appTheme.heading; font.bold: true }
            Label { Layout.fillWidth: true; text: "Run: " + monitor.status.runId + "\nSpecimen: " + monitor.status.runSpecimen + "\nRecipe: " + monitor.status.runRecipe; wrapMode: Text.WrapAnywhere; color: appTheme.text }
            Label { Layout.fillWidth: true; text: monitor.status.synchronization; wrapMode: Text.WordWrap; color: appTheme.secondary }
            Label { text: "Threshold " + monitor.status.threshold + "  |  Review margin " + monitor.status.reviewMargin; color: appTheme.secondary }
            Label { Layout.fillWidth: true; text: "Evidence: " + monitor.status.evidence + "\n" + monitor.status.evidenceDetail; wrapMode: Text.WrapAnywhere; color: appTheme.secondary }
            Label { Layout.fillWidth: true; text: "Normal / review / defect apply to the latest completed inspection. Preview health and the latched alarm are separate. Scores are not probabilities. Stronger red in the heatmap means higher model anomaly, not confirmed damage."; wrapMode: Text.WordWrap; color: appTheme.secondary }
            Label { Layout.fillWidth: true; text: "Offline inspection and Collect / train are available in the Tools menu. Training never activates a live model automatically."; wrapMode: Text.WordWrap; color: appTheme.secondary; font.pixelSize: 12 }
            }
        }
    }
    Dialog {
        id: aboutDialog
        enabled: !root.operationsLocked
        objectName: "aboutDialog"
        title: "About RDT Reliability Monitor"
        anchors.centerIn: parent
        width: Math.min(440, root.width - 48)
        modal: true
        standardButtons: Dialog.Ok
        background: Rectangle { color: appTheme.surface; border.color: appTheme.border; radius: appTheme.radius }
        Label {
            width: parent.width
            text: "RDT Reliability Monitor\nPySide6 / QML\n\nCamera monitoring, inspection review and training tools.\n\nUnqualified · Advisory only"
            wrapMode: Text.WordWrap
            color: appTheme.text
        }
    }
}
