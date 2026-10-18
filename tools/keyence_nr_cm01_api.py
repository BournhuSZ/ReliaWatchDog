"""Standalone OpenCV camera API based on camera_detection_app.CameraCapture.

Requires opencv-python and numpy. This is not a Keyence SDK: it works only
when the NR-CM01 is exposed by its driver as a video-capture device. Camera
indices identify generic devices, not a verified manufacturer or model.

Example:
    with KeyenceNRCM01Camera(camera_index=0) as camera:
        frame = camera.read()
        camera.save_snapshot("snapshot.jpg")

Run ``python keyence_nr_cm01_api.py --camera 0`` for a live preview.
"""

import argparse
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


class KeyenceNRCM01Camera:
    """Capture BGR images through the existing Windows/OpenCV camera path.

    Use from a single thread. Opening verifies that a frame can be read;
    unsupported backends are released before trying the next backend.
    """

    def __init__(self, camera_index: int = 0):
        """Select the video device index shown by scan_cameras()."""
        self.camera_index = camera_index
        self.cap: Optional[cv2.VideoCapture] = None
        self.backend: Optional[int] = None

    def open(self) -> bool:
        """Open and verify the camera; return False if all backends fail."""
        if self.cap is not None and self.cap.isOpened():
            return True
        self.close()
        for backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY):
            capture = cv2.VideoCapture(self.camera_index, backend)
            try:
                if capture.isOpened():
                    success, frame = capture.read()
                    if success and frame is not None:
                        self.cap = capture
                        self.backend = backend
                        return True
            finally:
                if self.cap is not capture:
                    capture.release()
        return False

    def read(self) -> np.ndarray:
        """Read one BGR frame; raise RuntimeError if closed or capture fails."""
        if self.cap is None or not self.cap.isOpened():
            raise RuntimeError("Camera is not open. Call open() first.")
        success, frame = self.cap.read()
        if not success or frame is None:
            raise RuntimeError(f"Failed to read camera index {self.camera_index}.")
        return frame

    def get_properties(self) -> dict:
        """Return actual width, height, FPS, index, and selected backend."""
        if self.cap is None or not self.cap.isOpened():
            raise RuntimeError("Camera is not open. Call open() first.")
        return {
            "camera_index": self.camera_index,
            "backend": self.backend,
            "width": int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "fps": self.cap.get(cv2.CAP_PROP_FPS),
        }

    def save_snapshot(self, path: str) -> Path:
        """Capture and save an image; its parent directory must exist."""
        destination = Path(path)
        if not cv2.imwrite(str(destination), self.read()):
            raise OSError(f"Failed to save snapshot to {destination}.")
        return destination

    def close(self) -> None:
        """Release the device; safe to call more than once."""
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.backend = None

    @staticmethod
    def scan_cameras(max_cameras: int = 10) -> list[int]:
        """Return readable device indices, without identifying their model."""
        available = []
        for camera_index in range(max_cameras):
            camera = KeyenceNRCM01Camera(camera_index)
            try:
                if camera.open():
                    available.append(camera_index)
            finally:
                camera.close()
        return available

    def __enter__(self) -> "KeyenceNRCM01Camera":
        """Open the camera or raise an error, for use in a with statement."""
        if not self.open():
            raise RuntimeError(
                f"Could not open camera index {self.camera_index}. "
                "Check its driver, connection, and whether another app is using it."
            )
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """Always release the device when leaving a with statement."""
        self.close()


def main() -> None:
    """Scan devices or preview a camera until Q or Escape is pressed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", type=int, default=0, help="Video device index")
    parser.add_argument("--scan", action="store_true", help="List readable device indices")
    args = parser.parse_args()
    if args.scan:
        print(f"Available camera indices: {KeyenceNRCM01Camera.scan_cameras()}")
        return
    try:
        with KeyenceNRCM01Camera(args.camera) as camera:
            print(camera.get_properties())
            print("Press Q or Escape to close the preview.")
            while True:
                cv2.imshow("Keyence NR-CM01 Camera", camera.read())
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
    finally:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()