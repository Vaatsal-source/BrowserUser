"""The no-key planner must respect common instructions to withhold submission."""

import pytest

from privacy_guard.gateway import ModelGateway


def plan(goal, *, visible=True):
    gateway = ModelGateway()
    payload = gateway.prepare(
        goal,
        {
            "fields": [
                {"index": 1, "label": "Full name", "tag": "input", "input_type": "text", "filled": True},
                {
                    "index": 2,
                    "label": "Submit demo form",
                    "tag": "button",
                    "input_type": "submit",
                    "in_viewport": visible,
                },
            ]
        },
        [],
        [],
        [],
    )
    return gateway.demo_action(payload)


@pytest.mark.parametrize(
    "goal",
    [
        "Fill this form",
        "Fill this form. Stop before submitting.",
        "Fill this form but do not submit it",
        "Fill and don't submit",
        "Fill and don’t submit",
        "Fill without submitting",
        "Never submit this form",
        "Submit only after I check my information",
        "Fill now and submit later",
        "Don't click the submit button",
    ],
)
def test_submission_withheld(goal):
    assert plan(goal).action == "done"


def test_explicit_submission_proposed_but_not_executed():
    action = plan("Fill and submit this demo form")
    assert action.action == "click"
    assert action.element_index == 2


def test_offscreen_submission_requires_scroll_first():
    assert plan("Fill and submit this demo form", visible=False).action == "scroll"
