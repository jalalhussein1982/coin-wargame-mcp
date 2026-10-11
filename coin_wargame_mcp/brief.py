"""Situation briefs for either seat.

The insurgent brief is the engine's own ``state_encoder.encode_for_llm`` output: the
host model sees exactly what the vendored Gemini Red Team saw. The government brief
is written here with the same section headings and the game's fog rule: insurgent
strength appears as the intelligence estimate, never as the true count.
"""

from __future__ import annotations

from . import _engine as E


def insurgent_brief(gs: E.GameStateStore) -> tuple[str, list[str]]:
    text = E.state_encoder.encode_for_llm(gs)
    return text, _opportunities_from(text)


def _opportunities_from(text: str) -> list[str]:
    out: list[str] = []
    in_section = False
    for line in text.splitlines():
        if line.startswith("## STRATEGIC OPPORTUNITIES"):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section and line.strip().startswith("* "):
            out.append(line.strip()[2:])
    return out


def _estimate_per_province(eng) -> dict[str, int]:
    """The server's lagged, noisy per-province estimate (``Engine.estimates``). This is the only
    insurgent number the government seat ever gets; it is never derived from the true share."""
    return eng.estimates


def government_brief(eng) -> tuple[str, list[str]]:
    gs = eng.gs
    g = gs.get_global_state()
    provinces = gs.get_all_provinces()
    est = _estimate_per_province(eng)
    est_total = sum(est.values())
    f = g.faction_forces
    gov_deployed = sum(p.government_forces for p in provinces)

    header = (
        f"# STRATEGIC SITUATION ASSESSMENT - TURN {g.turn}\n"
        f"Active Phase: {'AWAITING GOVERNMENT' if g.active_faction == 'government' else 'INSURGENT ACTING'}\n"
        f"Government Resources: {g.government_resources}\n"
        f"Government Legitimacy: {g.government_legitimacy}/100"
    )
    forces = (
        "## FORCE SUMMARY\n"
        f"Government:\n  - Total Forces: {f.government_total}\n  - Deployed: {gov_deployed}\n"
        f"  - Reserves: {f.government_reserves}\n\n"
        f"Insurgent (intelligence estimate): ~{est_total}\n"
        f"Estimated Ratio: {gov_deployed / max(est_total, 1):.1f}:1 government"
    )

    def row(p) -> str:
        return f"  - {p.name} ({p.id}): gov={p.government_forces}, ins~{est[p.id]}, sentiment={p.sentiment:+d}"

    gov_p = [p for p in provinces if p.control == E.ControlStatus.GOVERNMENT]
    ins_p = [p for p in provinces if p.control == E.ControlStatus.INSURGENT]
    con_p = [p for p in provinces if p.control == E.ControlStatus.CONTESTED]
    control = ["## TERRITORIAL CONTROL"]
    control.append(f"Insurgent Controlled ({len(ins_p)}):")
    control += [row(p) for p in ins_p] or ["  - None"]
    control.append(f"\nContested ({len(con_p)}):")
    control += [row(p) for p in con_p] or ["  - None"]
    control.append(f"\nGovernment Controlled ({len(gov_p)}):")
    for p in gov_p:
        threat = "LOW" if est[p.id] == 0 else ("HIGH" if p.government_forces / max(est[p.id], 1) < 1.5 else
                                              "MEDIUM" if p.government_forces / max(est[p.id], 1) <= 3 else "LOW")
        control.append(f"  - {p.name}: gov={p.government_forces}, ins~{est[p.id]}, threat={threat}")

    mali = ["## MALI BORDER CORRIDOR (Primary Infiltration Axis)"]
    for p in provinces:
        if p.borders_mali:
            mali.append(
                f"  {p.name} ({p.id}): Control={p.control.value.upper()}, Gov={p.government_forces}, "
                f"Ins~{est[p.id]}, Sentiment={p.sentiment:+d}, Infra={p.infrastructure:.1f}"
            )

    hostile = [p for p in provinces if p.sentiment < -20]
    neutral = [p for p in provinces if -20 <= p.sentiment <= 20]
    pro = [p for p in provinces if p.sentiment > 20]
    sentiment = ["## POPULATION SENTIMENT (Legitimacy Base)"]
    sentiment.append(f"\nAnti-Government ({len(hostile)} provinces):")
    sentiment += [f"  - {p.name}: {p.sentiment:+d}" for p in hostile] or ["  - None"]
    sentiment.append(f"\nNeutral/Contested ({len(neutral)} provinces):")
    sentiment += [f"  - {p.name}: {p.sentiment:+d}" for p in neutral] or ["  - None"]
    sentiment.append(f"\nPro-Government ({len(pro)} provinces):")
    sentiment.append(f"  - {len(pro)} provinces" if pro else "  - None")

    res = g.government_resources
    assess = "STRONG - Can sustain major operations" if res >= 60 else (
        "ADEQUATE - Limited operations possible" if res >= 25 else "CRITICAL - Prioritize cheap actions")
    resources = (
        "## RESOURCE STATUS\n"
        f"Government Resources: {res}\nAssessment: {assess}\n\n"
        "Cheap options: patrol (3), tribal_engagement (5), media_campaign (5), deploy (5)\n"
        "Diplomatic requests (request_international_community) cost nothing."
    )

    sc = g.scenario_conditions
    scenario = ["## SCENARIO CONDITIONS"]
    if sc.garrison_mutiny:
        scenario.append(f"GARRISON MUTINY: Active at {sc.mutiny_location} ({sc.mutinous_forces} forces defected)")
    if sc.mali_incursion_active:
        scenario.append(f"MALI INCURSION: Active along {sc.primary_attack_axis}")
    scenario.append(f"INTERNATIONAL INTERVENTION: {sc.international_intervention or 'None'}")

    patterns = ["## INSURGENT PATTERNS (Last 5 Turns)"]
    counts: dict[str, int] = {}
    targets: dict[str, int] = {}
    for t in g.turn_history[-5:]:
        for a in t.get("actions", []):
            if a.get("faction") == "insurgent" and a.get("action"):
                counts[a["action"].get("action_type", "?")] = counts.get(a["action"].get("action_type", "?"), 0) + 1
                tgt = a["action"].get("target_province") or a["action"].get("source_province")
                if tgt:
                    targets[tgt] = targets.get(tgt, 0) + 1
    if counts:
        patterns.append("Frequent Actions:")
        patterns += [f"  - {k}: {v} times" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:3]]
        patterns.append("Target Priority:")
        patterns += [f"  - {k}: {v} times" for k, v in sorted(targets.items(), key=lambda kv: -kv[1])[:3]]
    else:
        patterns.append("No observed insurgent actions yet. Establish a baseline before committing reserves.")

    opps: list[str] = []
    for p in provinces:
        if p.control == E.ControlStatus.CONTESTED:
            opps.append(f"CLEAR TARGET: {p.name} - contested, gov {p.government_forces} vs ins~{est[p.id]}")
    for p in provinces:
        if p.control == E.ControlStatus.GOVERNMENT and p.sentiment < 10:
            opps.append(f"GOVERNANCE TARGET: {p.name} - sentiment {p.sentiment:+d}, service_provision/tribal_engagement")
    for p in provinces:
        if p.borders_mali and est[p.id] > 0:
            opps.append(f"BORDER INTERDICTION: {p.name} - infiltration axis with ins~{est[p.id]}")
    if g.government_legitimacy < 40:
        opps.append("LEGITIMACY CRITICAL: media_campaign and amnesty_program before kinetic operations")
    opportunities = ["## STRATEGIC OPPORTUNITIES"] + [f"  * {o}" for o in opps[:5]]

    text = "\n\n".join([
        header, forces, "\n".join(control), "\n".join(mali), "\n".join(sentiment), resources,
        "\n".join(scenario), "\n".join(patterns), "\n".join(opportunities),
    ])
    return text, opps[:5]
