"""The orchestrator's OpenAPI schema, from which the GUI's API types are generated.

    python -m orchestrator.openapi > gui/openapi.json && (cd gui && npm run api-types)

tests/orchestrator/test_openapi.py fails while gui/openapi.json differs from the API, and the GUI's
`npm test` while gui/src/schema.d.ts differs from gui/openapi.json.
"""

import json

from orchestrator.app import app


def schema() -> str:
    return json.dumps(app.openapi(), indent=2) + "\n"


if __name__ == "__main__":
    print(schema(), end="")
