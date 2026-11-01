import ast
import importlib
import json
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND_ROOT / "app"


def test_app_root_contains_only_package_and_service_entrypoints():
    assert {path.name for path in APP_ROOT.glob("*.py")} == {
        "__init__.py",
        "main.py",
        "worker.py",
    }


@pytest.mark.parametrize(
    "package",
    ["api", "core", "db", "documents", "evaluation", "integrations", "retrieval", "talent"],
)
def test_functional_packages_are_importable(package):
    module = importlib.import_module(f"app.{package}")

    assert Path(module.__file__).resolve() == APP_ROOT / package / "__init__.py"


def test_langgraph_entrypoints_resolve_to_exported_graphs():
    config = json.loads((BACKEND_ROOT / "langgraph.json").read_text(encoding="utf-8"))

    for graph_id, entrypoint in config["graphs"].items():
        source, export = entrypoint.rsplit(":", 1)
        path = (BACKEND_ROOT / source).resolve()
        assert path.is_relative_to(APP_ROOT), graph_id
        assert path.is_file(), f"{graph_id}: {source} does not exist"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assignments = {
            target.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        assert export in assignments, f"{graph_id}: {export} is not exported"
