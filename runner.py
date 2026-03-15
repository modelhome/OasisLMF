#!/usr/bin/env python3
"""
OasisLMF runner: applies the insurance Financial Module (FM) to CLIMADA ground-up losses.

Takes the hurricane_losses JSON output from CLIMADA, applies configurable policy terms
(deductible, per-occurrence limit, coverage fraction) using the OasisLMF Financial Module
approach, and produces insured loss distributions suitable for cat bond pricing with FinancePy.

Input JSON (positional file arg or stdin):
  Expects the full CLIMADA hurricane_losses output, optionally extended with a "policy" block.

  Required fields (from CLIMADA output):
    event_loss_table          : list of {event_id, loss_usd, frequency, return_period_years}
    total_exposed_value_usd   : total value of exposed assets in USD

  Optional "policy" block (uses cat bond layer defaults if absent):
    total_insured_value_usd   : insured TIV (default: 25% of exposed value)
    coverage_pct              : fraction of loss covered under policy (default: 1.0)
    deductible_pct            : per-occurrence deductible as fraction of covered loss (default: 0.05)
    per_occurrence_limit_pct  : per-event limit as fraction of TIV (default: 0.25)

Output JSON (stdout):
  scenario                      : inherited from CLIMADA scenario description
  total_exposed_value_usd       : ground-up total value (from CLIMADA)
  total_insured_value_usd       : insured TIV after policy terms
  insured_aai_usd               : average annual insured loss (= expected loss for FinancePy)
  loss_ratio                    : insured AAL / TIV
  policy_terms                  : applied FM parameters (for audit trail)
  event_insured_loss_table      : per-event {event_id, gross_loss_usd, insured_loss_usd, frequency, return_period_years}
  insured_loss_exceedance_curve : {return_periods_years, losses_usd} — EP curve for FinancePy cat bond pricing
"""

import argparse
import json
import sys

import numpy as np


# ---------------------------------------------------------------------------
# Financial Module (FM) core logic
# ---------------------------------------------------------------------------

def apply_fm_layer(gross_losses, coverage_pct, deductible_pct, per_occ_limit):
    """
    Apply a single OasisLMF-style FM policy layer to an array of gross losses.

    Mirrors the OasisLMF calclayer formula for a standard property insurance layer:

        net_loss = min(max(gross * coverage_pct - deductible, 0), limit)

    where deductible = deductible_pct * (gross * coverage_pct).

    Parameters
    ----------
    gross_losses    : array-like of float  — ground-up economic losses per event (USD)
    coverage_pct    : float                — fraction of loss covered (0-1)
    deductible_pct  : float                — per-occurrence deductible as fraction of covered loss (0-1)
    per_occ_limit   : float                — per-occurrence cap on insured loss (USD)

    Returns
    -------
    numpy array of insured losses per event (USD)
    """
    gross = np.asarray(gross_losses, dtype=float)

    # Step 1 – apply coverage fraction
    covered = gross * coverage_pct

    # Step 2 – subtract per-occurrence deductible
    deductible = deductible_pct * covered
    net_of_ded = np.maximum(covered - deductible, 0.0)

    # Step 3 – cap at per-occurrence limit
    insured = np.minimum(net_of_ded, per_occ_limit)

    return insured


def compute_ep_curve(losses, frequencies, return_periods):
    """
    Compute an Exceedance Probability (EP) curve from event losses and annual frequencies.

    Sorts events by loss descending and accumulates exceedance probability
    (sum of frequencies for all events with loss >= threshold).

    Parameters
    ----------
    losses         : array-like of float  — insured loss per event (USD)
    frequencies    : array-like of float  — annual frequency of each event
    return_periods : list of int/float    — return periods in years to evaluate

    Returns
    -------
    list of float — insured loss at each requested return period
    """
    losses = np.asarray(losses, dtype=float)
    frequencies = np.asarray(frequencies, dtype=float)

    # Sort descending by loss; cumulative frequency = exceedance probability
    order = np.argsort(losses)[::-1]
    sorted_losses = losses[order]
    exc_prob = np.cumsum(frequencies[order])

    losses_at_rp = []
    for rp in return_periods:
        target_prob = 1.0 / rp
        if len(sorted_losses) == 0:
            losses_at_rp.append(0.0)
        elif target_prob <= exc_prob.min():
            # Beyond most extreme event — return largest observed loss
            losses_at_rp.append(float(sorted_losses[0]))
        elif target_prob > exc_prob.max():
            losses_at_rp.append(0.0)
        else:
            loss = float(np.interp(target_prob, exc_prob[::-1], sorted_losses[::-1]))
            losses_at_rp.append(loss)

    return losses_at_rp


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="OasisLMF FM runner — converts CLIMADA ground-up losses to insured losses"
    )
    parser.add_argument(
        "input",
        nargs="?",
        default="-",
        help="Path to CLIMADA hurricane_losses JSON output (or '-' for stdin)",
    )
    args = parser.parse_args()

    if args.input == "-":
        inp = json.load(sys.stdin)
    else:
        with open(args.input) as fh:
            inp = json.load(fh)

    # ------------------------------------------------------------------
    # Read CLIMADA output fields — default to a Cat 4 Miami scenario so
    # the image is smoke-testable with `echo '{}' | docker run --rm -i ...`
    # ------------------------------------------------------------------
    DEFAULT_EXPOSED_VALUE = 800e9
    DEFAULT_EVENT_LOSS_TABLE = [
        {"event_id": "DEFAULT_000", "loss_usd": 120e9, "frequency": 0.02, "return_period_years": 50.0},
        {"event_id": "DEFAULT_001", "loss_usd": 60e9,  "frequency": 0.02, "return_period_years": 50.0},
        {"event_id": "DEFAULT_002", "loss_usd": 20e9,  "frequency": 0.02, "return_period_years": 50.0},
    ]

    event_loss_table = inp.get("event_loss_table", DEFAULT_EVENT_LOSS_TABLE)
    total_exposed_value = float(inp.get("total_exposed_value_usd", DEFAULT_EXPOSED_VALUE))
    scenario = inp.get("scenario", "Hurricane scenario")

    # ------------------------------------------------------------------
    # Policy terms — defaults represent a typical cat bond risk layer:
    #   • 25% TIV insured (concentrated commercial/residential book)
    #   • 5% deductible (franchise / per-occurrence retention)
    #   • 25% of TIV per-occurrence limit (single-layer cat bond trigger)
    # ------------------------------------------------------------------
    policy = inp.get("policy", {})
    tiv = float(policy.get("total_insured_value_usd", total_exposed_value * 0.25))
    coverage_pct = float(policy.get("coverage_pct", 1.0))
    deductible_pct = float(policy.get("deductible_pct", 0.05))
    per_occ_limit_pct = float(policy.get("per_occurrence_limit_pct", 0.25))
    per_occ_limit = per_occ_limit_pct * tiv

    # ------------------------------------------------------------------
    # Extract event arrays
    # ------------------------------------------------------------------
    gross_losses = np.array([e["loss_usd"] for e in event_loss_table], dtype=float)
    frequencies = np.array([e["frequency"] for e in event_loss_table], dtype=float)
    event_ids = [e["event_id"] for e in event_loss_table]

    # Scale gross losses from total economic exposure to insured portfolio.
    # CLIMADA reports losses over the full exposed value; we take the insured fraction.
    insured_fraction = tiv / total_exposed_value if total_exposed_value > 0 else 1.0
    gross_insured_book = gross_losses * insured_fraction

    # ------------------------------------------------------------------
    # Apply Financial Module
    # ------------------------------------------------------------------
    insured_losses = apply_fm_layer(
        gross_insured_book,
        coverage_pct=coverage_pct,
        deductible_pct=deductible_pct,
        per_occ_limit=per_occ_limit,
    )

    # ------------------------------------------------------------------
    # Derived statistics
    # ------------------------------------------------------------------
    insured_aal = float(np.sum(insured_losses * frequencies))
    loss_ratio = insured_aal / tiv if tiv > 0 else 0.0

    # EP curve at standard return periods used in cat bond term sheets
    return_periods = [10, 25, 50, 100, 200, 250, 500, 1000]
    ep_losses = compute_ep_curve(insured_losses, frequencies, return_periods)

    # ------------------------------------------------------------------
    # Build output event table
    # ------------------------------------------------------------------
    event_insured_loss_table = []
    for ev_id, gross, ins_loss, freq in zip(event_ids, gross_insured_book, insured_losses, frequencies):
        rp = float(1.0 / freq) if freq > 0 else float("inf")
        event_insured_loss_table.append(
            {
                "event_id": ev_id,
                "gross_loss_usd": round(float(gross), 2),
                "insured_loss_usd": round(float(ins_loss), 2),
                "frequency": float(freq),
                "return_period_years": round(rp, 1),
            }
        )
    event_insured_loss_table.sort(key=lambda x: x["insured_loss_usd"], reverse=True)

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------
    result = {
        "scenario": scenario,
        "total_exposed_value_usd": total_exposed_value,
        "total_insured_value_usd": tiv,
        "insured_aai_usd": round(insured_aal, 2),
        "loss_ratio": round(loss_ratio, 6),
        "policy_terms": {
            "coverage_pct": coverage_pct,
            "deductible_pct": deductible_pct,
            "per_occurrence_limit_usd": round(per_occ_limit, 2),
            "per_occurrence_limit_pct": per_occ_limit_pct,
        },
        "event_insured_loss_table": event_insured_loss_table,
        "insured_loss_exceedance_curve": {
            "return_periods_years": return_periods,
            "losses_usd": [round(v, 2) for v in ep_losses],
        },
    }

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
