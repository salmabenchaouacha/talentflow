"""Les workflows n8n livrés sont importables : JSON valide, graphe cohérent, API appelée."""

import json
from pathlib import Path

import pytest

WORKFLOWS = sorted((Path(__file__).parents[1] / "n8n" / "workflows").glob("*.json"))


def test_four_workflows_shipped():
    assert len(WORKFLOWS) == 4


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_workflow_graph_is_consistent(path):
    wf = json.loads(path.read_text(encoding="utf-8"))
    names = {n["name"] for n in wf["nodes"]}
    assert len(names) == len(wf["nodes"])
    for src, conn in wf["connections"].items():
        assert src in names
        for outputs in conn["main"]:
            for target in outputs:
                assert target["node"] in names
    http = [n for n in wf["nodes"] if n["type"] == "n8n-nodes-base.httpRequest"]
    assert http, "chaque workflow appelle l'API TalentFlow"
    for n in http:
        assert n["parameters"]["url"].startswith("={{ $env.TF_API_BASE_URL }}")
        headers = n["parameters"]["headerParameters"]["parameters"]
        assert any(h["name"] == "X-API-Key" for h in headers)


def test_no_tags_to_avoid_duplicate_tag_import_error():
    for path in WORKFLOWS:
        assert json.loads(path.read_text(encoding="utf-8")).get("tags") == []
