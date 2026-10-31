# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Optional Qt Quick monitor; the default Widgets interface retains auxiliary workflows."""

import logging
import sys
from importlib.resources import files

from PySide6.QtCore import QUrl
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from rdt_monitor.dialogs import QmlMonitorDialogs
from rdt_monitor.presentation import MonitorViewModel


def create_engine(monitor: MonitorViewModel) -> QQmlApplicationEngine:
    """Load packaged QML with explicit Python ownership and screen-aware initial size.

    Args:
        monitor (MonitorViewModel): Strongly retained adapter for this engine's lifetime.

    Returns:
        QQmlApplicationEngine: Loaded engine; caller retains it until the event loop ends.

    Raises:
        RuntimeError: If QML or its resources cannot be loaded.
    """
    engine = QQmlApplicationEngine()
    errors: list[str] = []
    engine.warnings.connect(lambda messages: errors.extend(message.toString() for message in messages))
    QQmlEngine.setObjectOwnership(monitor, QQmlEngine.ObjectOwnership.CppOwnership)
    engine.addImageProvider("frames", monitor.provider)
    engine.addImageProvider("offline", monitor.offline.provider)
    engine.addImageProvider("setup", monitor.setup.provider)
    engine.addImageProvider("collection", monitor.collection.provider)
    initial: dict[str, object] = {"monitor": monitor}
    screen = QApplication.primaryScreen()
    if screen is not None:
        available = screen.availableGeometry().adjusted(16, 16, -16, -48)
        width, height = min(1440, available.width()), min(900, available.height())
        initial.update(
            width=width,
            height=height,
            minimumWidth=min(960, width),
            minimumHeight=min(640, height),
            x=available.center().x() - width // 2,
            y=available.center().y() - height // 2,
        )
    engine.setInitialProperties(initial)
    engine.load(QUrl.fromLocalFile(str(files("rdt_monitor").joinpath("qml/Main.qml"))))
    if not engine.rootObjects():
        monitor.dispose()
        detail = "; ".join(errors) or "No root window was created"
        raise RuntimeError(f"The QML interface could not load: {detail}. Use rdt-monitor for the standard interface")
    monitor.attach_dialogs(QmlMonitorDialogs(engine.rootObjects()[0], monitor.setup))
    return engine


def main() -> int:
    """Launch the optional QML interface for unqualified advisory monitoring.

    Returns:
        int: Qt exit status, or 1 if the packaged shell cannot load.
    """
    application = QApplication(sys.argv)
    QQuickStyle.setStyle("Fusion")
    families = set(QFontDatabase.families())
    family = next((name for name in ("IBM Plex Sans", "Inter") if name in families), application.font().family())
    application.setFont(QFont(family, 10))
    monitor = MonitorViewModel()
    try:
        engine = create_engine(monitor)
    except RuntimeError as error:
        logging.error("%s", error)
        return 1
    application.aboutToQuit.connect(monitor.dispose)
    result = application.exec()
    monitor.dispose()
    del engine
    return result


if __name__ == "__main__":
    sys.exit(main())
