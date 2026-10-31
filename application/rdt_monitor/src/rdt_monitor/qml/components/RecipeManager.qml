import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Popup {
    id: popup
    required property var theme
    required property var monitor
    readonly property var library: monitor.recipeLibrary
    property string loadError: ""
    modal: true
    focus: true
    padding: 16
    closePolicy: Popup.CloseOnEscape
    background: Rectangle { color: popup.theme.background; border.color: popup.theme.border; radius: 6 }
    onOpened: { loadError = ""; library.refresh() }
    contentItem: ColumnLayout {
        spacing: 10
        RowLayout {
            Layout.fillWidth: true
            Label { text: "Recipe library"; color: popup.theme.heading; font.pixelSize: 20; font.bold: true; Layout.fillWidth: true }
            ActionButton { theme: popup.theme; text: "Close"; onClicked: popup.close() }
        }
        Label { Layout.fillWidth: true; text: "Browse family → UUT → recipe revision. Loading a recipe does not start monitoring."; color: popup.theme.secondary; wrapMode: Text.WordWrap; font.pixelSize: 12 }
        RowLayout {
            Layout.fillWidth: true
            Label { Layout.fillWidth: true; text: library.status.directory; color: popup.theme.secondary; elide: Text.ElideMiddle; ToolTip.visible: folderHover.hovered; ToolTip.text: text; HoverHandler { id: folderHover } }
            ActionButton { theme: popup.theme; text: "Choose folder…"; onClicked: monitor.chooseRecipeFolder() }
            ActionButton { theme: popup.theme; text: "Refresh"; onClicked: library.refresh() }
            ActionButton { theme: popup.theme; text: "Open file…"; onClicked: { popup.close(); monitor.chooseRecipe() } }
        }
        Label { visible: !!library.status.message; Layout.fillWidth: true; text: library.status.message; color: popup.theme.colors.review; wrapMode: Text.WordWrap; font.pixelSize: 12 }
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 8
            Panel {
                theme: popup.theme; title: "UUT families"; Layout.preferredWidth: 190; Layout.fillHeight: true
                ListView {
                    anchors.fill: parent; clip: true; model: library.status.families
                    ScrollBar.vertical: ScrollBar {}
                    Label { anchors.centerIn: parent; visible: parent.count === 0; text: "No recipes found"; color: popup.theme.secondary; font.pixelSize: 12 }
                    delegate: ItemDelegate {
                        required property string modelData
                        width: ListView.view.width; text: modelData; highlighted: library.status.family === modelData
                        onClicked: library.selectFamily(modelData)
                        ToolTip.visible: hovered; ToolTip.text: modelData
                    }
                }
            }
            Panel {
                theme: popup.theme; title: "UUTs"; Layout.preferredWidth: 220; Layout.fillHeight: true
                ListView {
                    anchors.fill: parent; clip: true; model: library.status.uuts
                    ScrollBar.vertical: ScrollBar {}
                    Label { anchors.centerIn: parent; visible: parent.count === 0; text: "Select a family"; color: popup.theme.secondary; font.pixelSize: 12 }
                    delegate: ItemDelegate {
                        required property string modelData
                        width: ListView.view.width; text: modelData; highlighted: library.status.uut === modelData
                        onClicked: library.selectUut(modelData)
                        ToolTip.visible: hovered; ToolTip.text: modelData
                    }
                }
            }
            Panel {
                theme: popup.theme; title: "Recipe revisions"; Layout.preferredWidth: 200; Layout.fillHeight: true
                ListView {
                    anchors.fill: parent; clip: true; model: library.status.revisions
                    ScrollBar.vertical: ScrollBar {}
                    Label { anchors.centerIn: parent; visible: parent.count === 0; text: "Select a UUT"; color: popup.theme.secondary; font.pixelSize: 12 }
                    delegate: ItemDelegate {
                        required property var modelData
                        width: ListView.view.width; text: modelData.recipe + "\n" + modelData.filename
                        highlighted: library.status.selected === modelData.path
                        onClicked: library.selectRecipe(modelData.path)
                        ToolTip.visible: hovered; ToolTip.text: modelData.path
                    }
                }
            }
            Panel {
                theme: popup.theme; title: "UUT information & applied model"; Layout.fillWidth: true; Layout.minimumWidth: 220; Layout.fillHeight: true
                ScrollView {
                    anchors.fill: parent; contentWidth: availableWidth; clip: true
                    Column {
                        width: parent.width; spacing: 8
                        Label { visible: !library.status.selected; width: parent.width; text: "Select a recipe revision to review its details."; color: popup.theme.secondary; wrapMode: Text.WordWrap }
                        Repeater {
                            model: library.status.selected ? [
                                {label:"UUT family", value:library.status.detail.family},
                                {label:"UUT", value:library.status.detail.name},
                                {label:"Article number", value:library.status.detail.article},
                                {label:"Batch", value:library.status.detail.batch},
                                {label:"Recipe", value:library.status.detail.recipe},
                                {label:"Mode", value:library.status.detail.mode},
                                {label:"Applied model", value:library.status.detail.model},
                                {label:"Model SHA-256", value:library.status.detail.digest},
                                {label:"Threshold ± review margin", value:library.status.detail.threshold},
                                {label:"Setup image size", value:library.status.detail.dimensions}
                            ] : []
                            Column {
                                required property var modelData
                                width: parent.width; spacing: 2
                                Label { text: modelData.label; color: popup.theme.secondary; font.pixelSize: 11; font.bold: true }
                                Label { width: parent.width; text: modelData.value; color: popup.theme.text; wrapMode: Text.WrapAnywhere; font.pixelSize: 12; Accessible.name: modelData.label + ": " + text }
                            }
                        }
                    }
                }
            }
        }
        Label { visible: !!popup.loadError; Layout.fillWidth: true; text: popup.loadError; color: popup.theme.colors.defect; wrapMode: Text.WordWrap }
        RowLayout {
            Layout.fillWidth: true
            Label { Layout.fillWidth: true; text: "Model binding is unqualified; selection does not verify or run the model."; color: popup.theme.secondary; font.pixelSize: 11; wrapMode: Text.WordWrap }
            ActionButton { objectName: "loadSelectedRecipe"; theme: popup.theme; text: "Load selected recipe"; enabled: !!library.status.selected && monitor.status.canLoadRecipe; onClicked: { if (monitor.loadLibraryRecipe(library.status.selected)) popup.close(); else popup.loadError = monitor.status.fault || "Recipe could not be loaded. Refresh and try again." } }
        }
    }
}
