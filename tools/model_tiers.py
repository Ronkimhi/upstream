#!/usr/bin/env python3
"""Single source of truth: which Claude model each governed command should run on.

`tasks/lessons.md` ("Match model cost to task shape", 2026-08-30) said mechanical,
contract-explicit work should run on a fast model and judgment, orchestration, adversarial
review, and final acceptance should stay on the strongest available reasoning path. It stayed
prose -- no agent contract in `.claude/agents/` named a model -- until Ron reported a stock
search (`run deepdive`/`run redteam`) ran on an unnecessarily expensive one. That is the
second failure of the same rule, so it is enforced here rather than remembered a third time.
See `tools/check_model.py` for the gate and `tasks/lessons.md` for the promotion record.

Keyed by the same `command_key()` `tools/agent_registry.py` already uses to resolve a
concrete invocation like `run deepdive MYRG ai-infrastructure` back to its command-table
shape `run deepdive <TICKER> <chain>` -> `run deepdive`.

Two tiers, and nothing above them is ever the default. Opus and Fable stay available for a
manual escalation Ron asks for in the turn that needs it -- defaulting to the biggest model
available is exactly the failure this file exists to close.
"""

# FAST == STRONG == sonnet by Ron's decision, 2026-09-01 ("Work only with Sonnet.
# That's it!"), after the 10-link test: the haiku profile batch shipped five profiles
# carrying VERTIV's numbers under other issuers' names (caught and rebuilt at 665cf63,
# gate gap in tasks/backlog.md), and its three failed rounds plus the repair cost more
# tokens than one sonnet run doing the job correctly once. The two-tier structure and
# the command table stay, so a future cheaper-model decision is one line here, dated.
# Opus and Fable remain manual-escalation-only, unchanged.
FAST = "claude-sonnet-5"
STRONG = "claude-sonnet-5"

TIER_MODEL = {"fast": FAST, "strong": STRONG}

# command_key -> tier. "fast": repetitive, contract-explicit, mechanical work (issuer mapping,
# profile scaffolding, log ingestion). "strong": campaign selection, prioritization,
# orchestration, semantic evidence review, adversarial review, and final acceptance -- the
# exact split tasks/lessons.md states.
COMMAND_TIER = {
    "run radar": "strong",
    "run campaign init": "strong",
    "run digest": "strong",
    "check health": "strong",
    "run impact": "strong",
    "run impact --queue": "strong",
    "run themes": "fast",
    "run chain": "strong",
    "run universe": "fast",
    "run universe-audit": "strong",
    "run heat": "strong",
    "run scenarios": "strong",
    "run screen": "strong",
    "run profile": "fast",
    "run profile --campaign": "fast",
    "run selection": "strong",
    "run deepdive": "strong",
    "run redteam": "strong",
    "run devil": "strong",
    # Owners named 2026-09-01 (contract audit, Ron): bookkeeping and reporting shapes,
    # mechanical by construction. `refresh` inherits the judgment of the stage it re-runs,
    # but the command shape itself is contract-explicit.
    "refresh": "fast",
    "request data": "fast",
    "log trade": "fast",
    "note": "fast",
    "run review": "fast",
}

COMMAND_MODEL = {cmd: TIER_MODEL[tier] for cmd, tier in COMMAND_TIER.items()}


def tier_for(key: str):
    return COMMAND_TIER.get(key)


def model_for(key: str):
    return COMMAND_MODEL.get(key)


if __name__ == "__main__":  # tiny CLI so the table can be read by hand
    for cmd, tier in sorted(COMMAND_TIER.items()):
        print(f"{cmd:28} {tier:7} {TIER_MODEL[tier]}")
