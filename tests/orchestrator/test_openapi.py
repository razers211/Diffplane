import json
from pathlib import Path

from orchestrator.openapi import schema


def test_the_guis_copy_of_the_schema_is_current():
    copy = Path(__file__).parents[2] / "gui" / "openapi.json"
    assert copy.read_text() == schema(), \
        "the API changed: python -m orchestrator.openapi > gui/openapi.json, then npm run api-types in gui/"


# The main routes: their answers are a contract (GUI types, the CLI, scripts), so each keeps a model.
TYPED = {
    ("get", "/health"), ("get", "/auth/me"), ("get", "/fabrics"), ("get", "/devices"), ("get", "/segments"),
    ("get", "/services"), ("post", "/diff-jobs"), ("get", "/diff-jobs"), ("get", "/diff-jobs/{job_id}"),
    ("get", "/devices/{name}/diff"), ("get", "/drift"), ("post", "/deploy-jobs"), ("get", "/deploy-jobs"),
    ("get", "/deploy-jobs/{job_id}"), ("post", "/deploy-jobs/{job_id}/approve"),
    ("post", "/deploy-jobs/{job_id}/cancel"), ("post", "/fabrics/{site}/intent/preview"),
    ("post", "/fabrics/{site}/intent/import"),
}


def test_the_main_routes_declare_their_answers():
    paths = json.loads(schema())["paths"]
    for method, path in sorted(TYPED):
        answers = paths[path][method]["responses"]
        ok = next(v for k, v in answers.items() if k.startswith("2"))
        body = json.dumps(ok["content"]["application/json"]["schema"])
        assert "#/components/schemas/" in body, f"{method.upper()} {path} has no response model"


def test_an_answer_is_the_dict_the_route_built():
    """A key the route left out stays out: no route adds nulls for its model's unset fields."""
    import importlib
    from typing import get_args
    import pkgutil

    from fastapi.routing import APIRoute
    import orchestrator.routers as routers

    routes = [r for m in pkgutil.iter_modules(routers.__path__)
              for r in importlib.import_module(f"orchestrator.routers.{m.name}").router.routes]
    ours = lambda m: getattr(m, "__module__", "") == "orchestrator.answers"   # noqa: E731
    typed = [r for r in routes if isinstance(r, APIRoute)
             and (ours(r.response_model) or any(map(ours, get_args(r.response_model))))]
    assert len(typed) == len(TYPED)
    assert typed and all(r.response_model_exclude_unset for r in typed), \
        [r.path for r in typed if not r.response_model_exclude_unset]
