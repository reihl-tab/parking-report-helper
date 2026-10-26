#!/usr/bin/env python3
"""Mirror issue/PR labels and state onto the owner's GitHub Project board.

Labels on the issue are the source of truth; the board is a view of them.
Contributors without board access (Claude's sessions, forks) update labels,
and this script, run by .github/workflows/project-sync.yml, sets the
matching single-select fields (Status, Priority) on the project item.

Safe by default:
- Dry run unless the repo variable PROJECT_SYNC is "on": it prints the plan
  and changes nothing.
- Fields or options missing on the board are reported and skipped, never
  created.
- Only stdlib; the token comes from the environment and is never printed.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

API = "https://api.github.com/graphql"
CONFIG = Path(__file__).resolve().parents[1] / ".github" / "project-sync.json"

Q_PROJECT = """
query($login: String!, $number: Int!) {
  user(login: $login) {
    projectV2(number: $number) {
      id
      fields(first: 50) {
        nodes { ... on ProjectV2SingleSelectField { id name options { id name } } }
      }
    }
  }
}"""
Q_ITEMS = """
query($id: ID!) {
  node(id: $id) {
    ... on Issue { projectItems(first: 50) { nodes { id project { id } } } }
    ... on PullRequest { projectItems(first: 50) { nodes { id project { id } } } }
  }
}"""
M_ADD = """
mutation($project: ID!, $content: ID!) {
  addProjectV2ItemById(input: {projectId: $project, contentId: $content}) {
    item { id }
  }
}"""
M_SET = """
mutation($project: ID!, $item: ID!, $field: ID!, $option: String!) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $project, itemId: $item, fieldId: $field,
    value: {singleSelectOptionId: $option}
  }) { projectV2Item { id } }
}"""

GraphQL = Callable[[str, Mapping[str, Any]], dict[str, Any]]


def desired_values(
    state: str, merged: bool, labels: Iterable[str], fields: Mapping[str, Any]
) -> dict[str, str]:
    """Board field -> option name, from the configured rules.

    Rules are checked in order; the first match wins. A rule matches on
    `state` ("closed", "merged", "open") or on `label`. A field whose rules
    match nothing and that has no `default` is left unchanged."""
    names = {label.lower() for label in labels}
    effective = "merged" if merged else state.lower()
    out: dict[str, str] = {}
    for field, spec in fields.items():
        for rule in spec.get("rules", []):
            if "state" in rule and rule["state"] in (effective, state.lower()):
                out[field] = rule["option"]
                break
            if "label" in rule and rule["label"].lower() in names:
                out[field] = rule["option"]
                break
        else:
            if spec.get("default"):
                out[field] = spec["default"]
    return out


def resolve(
    wanted: Mapping[str, str], board_fields: Iterable[Mapping[str, Any]]
) -> tuple[list[tuple[str, str, str, str]], list[str]]:
    """Match wanted field/option names (case-insensitive) to board IDs.

    Returns ([(field, option, field_id, option_id)], [problems])."""
    by_name = {
        f["name"].lower(): f for f in board_fields if f and f.get("name") is not None
    }
    found, problems = [], []
    for field, option in wanted.items():
        f = by_name.get(field.lower())
        if f is None:
            problems.append(f"board has no single-select field {field!r}")
            continue
        opts = {o["name"].lower(): o["id"] for o in f.get("options") or []}
        oid = opts.get(option.lower())
        if oid is None:
            problems.append(
                f"field {f['name']!r} has no option {option!r} "
                f"(has: {', '.join(o['name'] for o in f.get('options') or [])})"
            )
            continue
        found.append((f["name"], option, f["id"], oid))
    return found, problems


def graphql_client(token: str) -> GraphQL:
    def call(query: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        body = json.dumps({"query": query, "variables": dict(variables)}).encode()
        req = urllib.request.Request(
            API,
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "project-sync",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
        if data.get("errors"):
            messages = "; ".join(e.get("message", "?") for e in data["errors"])
            raise RuntimeError(f"GraphQL error: {messages}")
        return data["data"]

    return call


def subject(event: Mapping[str, Any]) -> tuple[str, str, bool, list[str]] | None:
    """(node_id, state, merged, labels) of the event's issue or PR."""
    item = event.get("issue") or event.get("pull_request")
    if not isinstance(item, Mapping) or not item.get("node_id"):
        return None
    labels = [lbl.get("name", "") for lbl in item.get("labels") or []]
    return item["node_id"], item.get("state", "open"), bool(item.get("merged")), labels


def sync(
    event: Mapping[str, Any],
    config: Mapping[str, Any],
    gql: GraphQL | None,
    apply: bool,
    log: Callable[[str], None] = print,
) -> int:
    subj = subject(event)
    if subj is None:
        log("no issue or pull_request in the event; nothing to do")
        return 0
    node_id, state, merged, labels = subj
    wanted = desired_values(state, merged, labels, config["fields"])
    log(
        f"plan: {json.dumps(wanted, ensure_ascii=False)} (state={state}, merged={merged})"
    )
    if gql is None:
        log("no PROJECT_PAT available: plan only")
        return 0
    proj = config["project"]
    data = gql(Q_PROJECT, {"login": proj["owner"], "number": int(proj["number"])})
    project = (data.get("user") or {}).get("projectV2")
    if not project:
        log(
            f"project {proj['owner']}#{proj['number']} not found or not visible to the token"
        )
        return 1 if apply else 0
    found, problems = resolve(wanted, project["fields"]["nodes"])
    for p in problems:
        log(f"::warning::{p}")
    if not apply:
        for field, option, _, _ in found:
            log(f"dry run: would set {field} = {option}")
        log("dry run: set the repo variable PROJECT_SYNC=on to apply")
        return 0
    nodes = gql(Q_ITEMS, {"id": node_id})["node"]["projectItems"]["nodes"]
    item_id = next(
        (n["id"] for n in nodes if n["project"]["id"] == project["id"]), None
    )
    if item_id is None:
        added = gql(M_ADD, {"project": project["id"], "content": node_id})
        item_id = added["addProjectV2ItemById"]["item"]["id"]
        log("added the item to the project")
    for field, option, field_id, option_id in found:
        gql(
            M_SET,
            {
                "project": project["id"],
                "item": item_id,
                "field": field_id,
                "option": option_id,
            },
        )
        log(f"set {field} = {option}")
    return 0


def main() -> int:
    event = json.loads(
        Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8")
    )
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    token = os.environ.get("PROJECT_PAT", "")
    apply = os.environ.get("PROJECT_SYNC", "") == "on"
    gql = graphql_client(token) if token else None
    try:
        return sync(event, config, gql, apply)
    except Exception as exc:  # noqa: BLE001 - one boundary; never print the token
        print(f"::{'error' if apply else 'warning'}::project sync failed: {exc}")
        return 1 if apply else 0


if __name__ == "__main__":
    sys.exit(main())
