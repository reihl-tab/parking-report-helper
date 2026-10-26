#!/usr/bin/env python3
"""Propose issue/PR label and state changes to the owner's GitHub Project board.

Labels are an input, not the source of truth: manual board edits win.
Contributors without board access (Claude's sessions, forks) update labels,
and this script, run by .github/workflows/project-sync.yml, carries a label
or state change onto the matching single-select field (Status, Priority)
only when the board still shows what the labels said before the change.
Design note: docs/PROJECT-SYNC.md.

Safe by default:
- Dry run unless the repo variable PROJECT_SYNC is "on": it prints the plan
  and changes nothing.
- Manual board values win: a field is written only when it is empty or
  still equals the value derived from the labels *before* this event
  (compare-and-set). Anything else is a manual edit and is left alone.
- Protected fields (the curated Queue text) are never written.
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
    ... on Issue { projectItems(first: 50) { nodes { ...item } } }
    ... on PullRequest { projectItems(first: 50) { nodes { ...item } } }
  }
}
fragment item on ProjectV2Item {
  id
  project { id }
  fieldValues(first: 50) {
    nodes {
      ... on ProjectV2ItemFieldSingleSelectValue {
        name
        field { ... on ProjectV2SingleSelectField { name } }
      }
    }
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

SET, NOOP, MANUAL = "set", "noop", "manual"


def desired_values(
    state: str,
    merged: bool,
    labels: Iterable[str],
    fields: Mapping[str, Any],
    protected: Iterable[str] = (),
) -> dict[str, str]:
    """Board field -> option name, from the configured rules.

    Rules are checked in order; the first match wins. A rule matches on
    `state` ("closed", "merged", "open") or on `label`. A field whose rules
    match nothing and that has no `default` is left unchanged."""
    names = {label.lower() for label in labels}
    effective = "merged" if merged else state.lower()
    guarded = {name.lower() for name in protected}
    out: dict[str, str] = {}
    for field, spec in fields.items():
        if field.lower() in guarded:
            continue
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


def previous_subject(
    event: Mapping[str, Any], state: str, merged: bool, labels: list[str]
) -> tuple[str, bool, list[str]] | None:
    """(state, merged, labels) just before this event, or None when unknown.

    Only label and close/reopen events have a known "before"; for anything
    else (opened, edited, ...) the sync may fill empty fields but never
    overwrite a value it cannot prove it set itself."""
    action = event.get("action")
    label = (event.get("label") or {}).get("name")
    if action == "labeled" and label:
        return state, merged, [n for n in labels if n.lower() != label.lower()]
    if action == "unlabeled" and label:
        return state, merged, [*labels, label]
    if action == "closed":
        return "open", False, labels
    if action == "reopened":
        return "closed", False, labels
    return None


def decide(desired: str, current: str | None, previous: str | None) -> str:
    """Compare-and-set: manual board values win.

    - board already shows the desired option -> NOOP
    - field is empty -> SET
    - board still shows what the labels said before this event -> SET
    - anything else was edited by hand -> MANUAL (left alone)"""
    if current is not None and current.lower() == desired.lower():
        return NOOP
    if current is None:
        return SET
    if previous is not None and current.lower() == previous.lower():
        return SET
    return MANUAL


def current_values(item: Mapping[str, Any]) -> dict[str, str]:
    """Board field name (lower-case) -> current single-select option name."""
    out: dict[str, str] = {}
    for node in (item.get("fieldValues") or {}).get("nodes") or []:
        if not node or node.get("name") is None:
            continue
        field = (node.get("field") or {}).get("name")
        if field:
            out[field.lower()] = node["name"]
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
    protected = config.get("protected_fields", [])
    wanted = desired_values(state, merged, labels, config["fields"], protected)
    before = previous_subject(event, state, merged, labels)
    was = (
        desired_values(*before, config["fields"], protected)
        if before is not None
        else {}
    )
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
    nodes = gql(Q_ITEMS, {"id": node_id})["node"]["projectItems"]["nodes"]
    item = next((n for n in nodes if n["project"]["id"] == project["id"]), None)
    current = current_values(item) if item else {}
    guarded = {name.lower() for name in protected}
    writes = []
    for field, option, field_id, option_id in found:
        if field.lower() in guarded:  # defence in depth; desired_values skips them
            log(f"::warning::refusing to write protected field {field!r}")
            continue
        verdict = decide(option, current.get(field.lower()), was.get(field))
        if verdict == NOOP:
            log(f"{field} already {option}")
        elif verdict == MANUAL:
            log(
                f"{field}: board shows {current.get(field.lower())!r} (manual edit), "
                f"labels say {option!r}; manual value wins, not changed"
            )
        else:
            writes.append((field, option, field_id, option_id))
    if not apply:
        for field, option, _, _ in writes:
            log(f"dry run: would set {field} = {option}")
        if item is None and writes:
            log("dry run: would add the item to the project")
        log("dry run: set the repo variable PROJECT_SYNC=on to apply")
        return 0
    if not writes:
        return 0
    if item is None:
        added = gql(M_ADD, {"project": project["id"], "content": node_id})
        item_id = added["addProjectV2ItemById"]["item"]["id"]
        log("added the item to the project")
    else:
        item_id = item["id"]
    for field, option, field_id, option_id in writes:
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
