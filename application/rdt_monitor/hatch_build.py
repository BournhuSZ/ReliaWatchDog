"""Include the existing API in wheels built from a checkout or source archive."""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    """Resolve the API source without copying its implementation into this project."""

    def initialize(self, version: str, build_data: dict[str, object]) -> None:
        """Map the checkout or archived API into the installed Python package.

        Args:
            version (str): Hatch build variant, including editable installations.
            build_data (dict[str, object]): Build configuration receiving the resource map.

        Raises:
            FileNotFoundError: If neither source archive nor checkout contains the API.
            TypeError: If the builder's resource mapping has an unexpected shape.
        """
        root = Path(self.root)
        archived = root / "shared" / "keyence_nr_cm01_api.py"
        source = archived if archived.is_file() else root.parent.parent / "tools" / "keyence_nr_cm01_api.py"
        if not source.is_file():
            raise FileNotFoundError("Existing Keyence API is required to build the monitor")
        includes = build_data.setdefault("force_include", {})
        if not isinstance(includes, dict):
            raise TypeError("Expected a dictionary of forced wheel resources")
        includes[str(source)] = "rdt_monitor/keyence_nr_cm01_api.py"
