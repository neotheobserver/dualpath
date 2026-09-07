from __future__ import annotations

import csv
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

W = Path(__file__).resolve().parents[1] / "data" / "warehouse"


def read(name: str) -> list[dict[str, str]]:
    with (W / f"{name}.csv").open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    claims, lines = read("claims"), read("claim_lines")
    members, plans = read("members"), read("plans")
    providers, codes = read("providers"), read("denial_codes")
    provider_of = {p["provider_id"]: p for p in providers}
    member_of = {m["member_id"]: m for m in members}

    assert {m["plan_id"] for m in members} <= {p["plan_id"] for p in plans}, "member -> plan"
    assert {c["member_id"] for c in claims} <= set(member_of), "claim -> member"
    assert {c["provider_id"] for c in claims} <= set(provider_of), "claim -> provider"
    assert {l["claim_id"] for l in lines} <= {c["claim_id"] for c in claims}, "line -> claim"
    assert {c["denial_code"] for c in claims if c["denial_code"]} <= {
        d["denial_code"] for d in codes
    }, "claim -> denial_code"

    sum_allowed: dict[str, Decimal] = defaultdict(Decimal)
    sum_billed: dict[str, Decimal] = defaultdict(Decimal)
    for line in lines:
        sum_allowed[line["claim_id"]] += Decimal(line["allowed_amount"])
        sum_billed[line["claim_id"]] += Decimal(line["billed_amount"])
    for claim in claims:
        cid = claim["claim_id"]
        if cid not in sum_billed:
            continue
        assert Decimal(claim["billed_amount"]) == sum_billed[cid], f"{cid} billed != sum(lines)"
        if claim["status"] == "paid":
            assert Decimal(claim["allowed_amount"]) == sum_allowed[cid], (
                f"{cid} allowed != sum(lines)"
            )

    for claim in claims:
        share = Decimal(claim["plan_paid"]) + Decimal(claim["member_responsibility"])
        if claim["status"] == "paid":
            oon = provider_of[claim["provider_id"]]["network_status"] == "out_of_network"
            expected = Decimal(claim["billed_amount"] if oon else claim["allowed_amount"])
            assert share == expected, (
                f"{claim['claim_id']} ({'oon' if oon else 'in-network'}): {share} != {expected}"
            )
        else:
            assert Decimal(claim["allowed_amount"]) == 0 and Decimal(claim["plan_paid"]) == 0, (
                f"{claim['claim_id']} is {claim['status']} but carries adjudicated money"
            )

    for claim in claims:
        if claim["status"] == "denied":
            assert claim["denial_code"], f"{claim['claim_id']} denied with no code"
        else:
            assert not claim["denial_code"], (
                f"{claim['claim_id']} is {claim['status']} but carries a denial code"
            )
    contradictions = [
        c["claim_id"]
        for c in claims
        if c["denial_code"] == "DN-ELIGIBILITY"
        and (
            not member_of[c["member_id"]]["enrollment_end"]
            or member_of[c["member_id"]]["enrollment_end"] >= c["service_date"]
        )
    ]
    assert not contradictions, f"DN-ELIGIBILITY on enrolled members: {contradictions[:3]}"

    denials = Counter(c["denial_code"] for c in claims if c["denial_code"])
    top, count = denials.most_common(1)[0]
    total = sum(denials.values())
    assert top == "DN-PRIOR-AUTH", f"expected the prior-auth signal to dominate, got {top}"

    print(
        f"  {len(claims):,} claims / {len(lines):,} lines / "
        f"{len(members):,} members / {len(providers)} providers"
    )
    print(f"  most common denial: {top} ({count} of {total} denials, {count / total:.0%})")
    print("  referential integrity  OK")
    print("  financial identities   OK")
    print("  denial consistency     OK")
    print("  planted signal         OK")


if __name__ == "__main__":
    main()
