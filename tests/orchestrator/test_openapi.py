from pathlib import Path

from orchestrator.openapi import schema


def test_the_guis_copy_of_the_schema_is_current():
    copy = Path(__file__).parents[2] / "gui" / "openapi.json"
    assert copy.read_text() == schema(), \
        "the API changed: python -m orchestrator.openapi > gui/openapi.json, then npm run api-types in gui/"
