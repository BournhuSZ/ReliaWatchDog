"""Recipe library grouping and explicit selection behavior."""

from dataclasses import replace
from uuid import uuid4

from rdt_monitor.recipe_library import RecipeLibrary
from rdt_monitor.recipes import RecipeProfile, SpecimenDetails, StoredRecipe, save_recipe
from rdt_monitor.replay import fixture_recipe


def test_library_groups_revisions_and_reports_invalid_files(tmp_path):
    base = StoredRecipe(
        "training",
        fixture_recipe(),
        640,
        480,
        profile=RecipeProfile(uuid4().hex, uuid4().hex, "Demo"),
        specimen=SpecimenDetails("Part A", "Lot", "Family A", "001234567890"),
    )
    save_recipe(tmp_path / "one.json", base)
    save_recipe(tmp_path / "two.json", replace(base, profile=replace(base.profile, revision=uuid4().hex)))
    save_recipe(tmp_path / "legacy.json", replace(base, specimen=SpecimenDetails()))
    (tmp_path / "bad.json").write_text("{bad", encoding="utf-8")
    library = RecipeLibrary()
    library.setDirectory(str(tmp_path))
    assert library.status["families"] == ["Family A", "Unassigned family"]
    assert "1 invalid" in library.status["message"]
    library.selectFamily("Family A")
    assert library.status["uuts"] == ["Part A · 001234567890"]
    library.selectUut("Part A · 001234567890")
    assert len(library.status["revisions"]) == 2
    path = library.status["revisions"][0]["path"]
    library.selectRecipe(path)
    assert library.status["detail"]["article"] == "001234567890"
    library.selectFamily("Unassigned family")
    assert library.status["selected"] == ""
    library.selectRecipe(path)
    assert library.status["selected"] == ""
