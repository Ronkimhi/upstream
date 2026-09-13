"""Which ledger lines record a command run (2026-09-13).

Three gates found their run's ledger line by searching the whole line for the command's name:
check_chain for `run chain`, check_heat for `run heat`, check_scenarios for `run scenarios`. A
line's result text names other commands, so a `request data` line that named the chain refresh
and heat run it was preparing failed the chain gate (CI red from fc66a90) and satisfied the
heat gate's same-day requirement for a heat run that had not happened. The command a line ran
is its third `|` field. Pure, standard library only.
"""


def command_lines(lines, prefixes, day=None, kinds=("RUN", "AMEND")):
    """Lines whose kind (second field) is in `kinds` and whose command (third field) starts with
    one of `prefixes`, optionally only lines dated `day`."""
    out = []
    for line in lines:
        text = str(line)
        if day and not text.startswith(day):
            continue
        parts = [part.strip() for part in text.split("|")]
        if len(parts) < 3 or parts[1] not in kinds:
            continue
        if any(parts[2].startswith(prefix) for prefix in prefixes):
            out.append(line)
    return out
