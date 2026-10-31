import QtQuick
import QtQuick.Controls

Item {
    id: canvas
    required property var theme
    property alias source: image.source
    property var region: []
    property bool editable: true
    property color regionColor: theme.colors.inspectionRoi
    property string regionLabel: "Inspection ROI"
    property string emptyText: "Open an image, then draw an inspection ROI"
    property var backgroundRegions: []
    property var draft: []
    readonly property var displayed: draft.length === 4 ? draft : region
    signal regionEdited(real left, real top, real width, real height)
    signal regionSelected(int role)
    clip: true
    Accessible.name: "Offline inspection ROI editor"

    Rectangle { anchors.fill: parent; color: canvas.theme.colors.imageBackground }
    Image {
        id: image
        anchors.fill: parent
        fillMode: Image.PreserveAspectFit
        cache: false
    }
    Item {
        id: imageArea
        objectName: "offlineImageArea"
        width: image.paintedWidth
        height: image.paintedHeight
        anchors.centerIn: parent
        Repeater {
            model: canvas.backgroundRegions
            Rectangle {
                required property var modelData
                x: modelData.left * imageArea.width
                y: modelData.top * imageArea.height
                width: modelData.width * imageArea.width
                height: modelData.height * imageArea.height
                color: "transparent"
                border.width: 2
                border.color: canvas.theme.colors[modelData.colorRole]
                Rectangle {
                    x: 2; y: 2
                    width: otherLabel.implicitWidth + 8; height: 20
                    color: canvas.theme.colors.overlayBackground
                    Label { id: otherLabel; anchors.centerIn: parent; text: modelData.label; color: canvas.theme.colors[modelData.colorRole]; font.pixelSize: 11 }
                }
            }
        }
        Rectangle {
            id: outline
            visible: canvas.displayed.length === 4 && !!canvas.source
            x: visible ? canvas.displayed[0] * imageArea.width : 0
            y: visible ? canvas.displayed[1] * imageArea.height : 0
            width: visible ? canvas.displayed[2] * imageArea.width : 0
            height: visible ? canvas.displayed[3] * imageArea.height : 0
            color: "transparent"
            border.width: 2
            border.color: canvas.regionColor
            Rectangle {
                x: 2; y: Math.max(2, Math.min(parent.height - height, 2))
                width: roiLabel.implicitWidth + 8; height: 20
                color: canvas.theme.colors.overlayBackground
                Label { id: roiLabel; anchors.centerIn: parent; text: canvas.regionLabel; color: canvas.regionColor; font.pixelSize: 11 }
            }
            Repeater {
                model: [[0,0], [0.5,0], [1,0], [0,0.5], [1,0.5], [0,1], [0.5,1], [1,1]]
                Rectangle {
                    required property var modelData
                    visible: canvas.editable
                    x: modelData[0] * outline.width - 4
                    y: modelData[1] * outline.height - 4
                    width: 8; height: 8
                    color: canvas.theme.colors.overlayBackground
                    border.color: canvas.regionColor
                }
            }
        }
        MouseArea {
            id: pointer
            objectName: "offlineRegionPointer"
            anchors.fill: parent
            enabled: canvas.editable && !!canvas.source && width > 0 && height > 0
            preventStealing: true
            property real originX
            property real originY
            property var original: []
            property string operation: "draw"
            function bounded(value) { return Math.max(0, Math.min(1, value)) }
            onPressed: function(mouse) {
                originX = bounded(mouse.x / width)
                originY = bounded(mouse.y / height)
                original = canvas.region.slice()
                let selectedHit = original.length === 4 && originX >= original[0] - 9 / width && originX <= original[0] + original[2] + 9 / width && originY >= original[1] - 9 / height && originY <= original[1] + original[3] + 9 / height
                if (!selectedHit) {
                    for (let other of canvas.backgroundRegions) {
                        if (other.selectable && originX >= other.left && originX <= other.left + other.width && originY >= other.top && originY <= other.top + other.height) {
                            canvas.regionSelected(other.role)
                            original = [other.left, other.top, other.width, other.height]
                            break
                        }
                    }
                }
                operation = "draw"
                if (original.length === 4) {
                    let left = original[0], top = original[1]
                    let right = left + original[2], bottom = top + original[3]
                    let nearX = 9 / width, nearY = 9 / height
                    let inside = originX >= left - nearX && originX <= right + nearX && originY >= top - nearY && originY <= bottom + nearY
                    if (inside) {
                        operation = ""
                        if (Math.abs(originX - left) <= nearX) operation += "l"
                        else if (Math.abs(originX - right) <= nearX) operation += "r"
                        if (Math.abs(originY - top) <= nearY) operation += "t"
                        else if (Math.abs(originY - bottom) <= nearY) operation += "b"
                        if (!operation) operation = "move"
                    }
                }
                canvas.draft = operation === "draw" ? [originX, originY, 0, 0] : original.slice()
            }
            onPositionChanged: function(mouse) {
                if (!pressed) return
                let px = bounded(mouse.x / width), py = bounded(mouse.y / height)
                if (operation === "draw") {
                    canvas.draft = [Math.min(px, originX), Math.min(py, originY), Math.abs(px - originX), Math.abs(py - originY)]
                } else if (operation === "move") {
                    canvas.draft = [Math.max(0, Math.min(1 - original[2], original[0] + px - originX)), Math.max(0, Math.min(1 - original[3], original[1] + py - originY)), original[2], original[3]]
                } else {
                    let l = original[0], t = original[1], r = l + original[2], b = t + original[3]
                    if (operation.includes("l")) l = Math.min(px, r - 1 / width)
                    if (operation.includes("r")) r = Math.max(px, l + 1 / width)
                    if (operation.includes("t")) t = Math.min(py, b - 1 / height)
                    if (operation.includes("b")) b = Math.max(py, t + 1 / height)
                    canvas.draft = [l, t, r - l, b - t]
                }
            }
            onReleased: {
                let selected = canvas.draft.slice()
                canvas.draft = []
                if (selected.length === 4 && selected[2] > 0 && selected[3] > 0)
                    canvas.regionEdited(selected[0], selected[1], selected[2], selected[3])
            }
            onCanceled: canvas.draft = []
        }
    }
    Label {
        anchors.centerIn: parent
        visible: !canvas.source
        text: canvas.emptyText
        color: canvas.theme.secondary
        width: parent.width - 32
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
    }
}
