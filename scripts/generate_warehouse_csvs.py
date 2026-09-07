from __future__ import annotations

import argparse
import csv
import json
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

SEED = 20260805

PERIOD_START = date(2024, 1, 1)
PERIOD_END = date(2025, 11, 30)

N_MEMBERS = 420
N_PROVIDERS = 64
N_CLAIMS = 2400


# plan_id, name, tier, ded_ind, ded_fam, oop_ind, coins, pcp, spec, er, uc,
# rx_generic, rx_brand, premium
PLANS = [
    ("CH-BRZ-6500", "Cascade Bronze 6500", "Bronze", 6500, 13000, 9200, 0.40, 45, 90, 600, 75, 20, 70, 289.00),
    ("CH-SLV-3500", "Cascade Silver 3500", "Silver", 3500, 7000, 8000, 0.30, 30, 60, 450, 60, 15, 55, 412.00),
    ("CH-SLV-HSA4", "Cascade Silver HSA 4000", "Silver", 4000, 8000, 7500, 0.25, 0, 0, 400, 50, 10, 50, 398.00),
    ("CH-GLD-1500", "Cascade Gold 1500", "Gold", 1500, 3000, 6000, 0.20, 25, 50, 350, 40, 10, 45, 561.00),
    ("CH-PLT-500", "Cascade Platinum 500", "Platinum", 500, 1000, 3500, 0.10, 15, 30, 250, 25, 5, 35, 742.00),
]

SPECIALTIES = [
    "Family Medicine",
    "Internal Medicine",
    "Pediatrics",
    "Cardiology",
    "Orthopedics",
    "Dermatology",
    "Radiology",
    "Emergency Medicine",
    "Behavioral Health",
    "Physical Therapy",
    "Gastroenterology",
    "Endocrinology",
    "Pharmacy",
]

PROVIDER_PREFIXES = [
    "Alder Creek", "Summit Ridge", "Harbor Point", "Willow Bend", "Cedar Hollow",
    "Granite Falls", "Larkspur", "Northgate", "Silver Lake", "Foxglove",
    "Bluestem", "Riverstone", "Copperfield", "Marbury", "Thornwood",
    "Kestrel", "Pinehurst", "Elderberry", "Fairhaven", "Graymoor",
    "Juniper Flats", "Windrow", "Ashfield", "Belmont Park", "Cloverdale",
    "Duskwood", "Emberly", "Ferncliff", "Gloaming", "Hartwell",
    "Ivyridge", "Jessup", "Kirkfield", "Lowell Crest",
]

PROVIDER_SUFFIXES = {
    "Family Medicine": ["Family Medicine", "Primary Care", "Family Health"],
    "Internal Medicine": ["Internal Medicine", "Adult Medicine"],
    "Pediatrics": ["Pediatrics", "Children's Clinic"],
    "Cardiology": ["Cardiology", "Heart Center"],
    "Orthopedics": ["Orthopedics", "Bone & Joint"],
    "Dermatology": ["Dermatology", "Skin Clinic"],
    "Radiology": ["Imaging Center", "Radiology"],
    "Emergency Medicine": ["Regional Hospital", "Medical Center"],
    "Behavioral Health": ["Behavioral Health", "Counseling Center"],
    "Physical Therapy": ["Physical Therapy", "Rehab Services"],
    "Gastroenterology": ["Gastroenterology", "Digestive Health"],
    "Endocrinology": ["Endocrinology", "Diabetes Center"],
    "Pharmacy": ["Pharmacy", "Apothecary"],
}

STATES = [("WA", ["Seattle", "Tacoma", "Spokane", "Bellingham", "Olympia"]),
          ("OR", ["Portland", "Eugene", "Salem", "Bend"]),
          ("ID", ["Boise", "Coeur d'Alene"])]

# procedure_code, description, typical billed range, specialty affinity, POS
PROCEDURES = [
    ("99213", "Office visit, established patient, 20-29 min", 140, 260, "Family Medicine", "11"),
    ("99214", "Office visit, established patient, 30-39 min", 210, 380, "Internal Medicine", "11"),
    ("99203", "Office visit, new patient, 30-44 min", 230, 410, "Family Medicine", "11"),
    ("99395", "Preventive visit, established patient, 18-39 yrs", 260, 430, "Family Medicine", "11"),
    ("80053", "Comprehensive metabolic panel", 60, 165, "Internal Medicine", "81"),
    ("85025", "Complete blood count with differential", 40, 120, "Internal Medicine", "81"),
    ("83036", "Hemoglobin A1c", 45, 130, "Endocrinology", "81"),
    ("80061", "Lipid panel", 55, 150, "Internal Medicine", "81"),
    ("71046", "Chest x-ray, 2 views", 120, 310, "Radiology", "22"),
    ("70450", "CT head without contrast", 900, 2400, "Radiology", "22"),
    ("72148", "MRI lumbar spine without contrast", 1400, 3600, "Radiology", "22"),
    ("93000", "Electrocardiogram with interpretation", 90, 240, "Cardiology", "11"),
    ("93306", "Echocardiogram, complete with Doppler", 750, 1900, "Cardiology", "22"),
    ("45378", "Diagnostic colonoscopy", 1600, 3900, "Gastroenterology", "24"),
    ("29881", "Knee arthroscopy with meniscectomy", 5200, 12800, "Orthopedics", "24"),
    ("20610", "Major joint injection", 220, 560, "Orthopedics", "11"),
    ("99284", "Emergency department visit, high severity", 1300, 3400, "Emergency Medicine", "23"),
    ("99283", "Emergency department visit, moderate severity", 700, 1800, "Emergency Medicine", "23"),
    ("90837", "Psychotherapy, 60 minutes", 180, 340, "Behavioral Health", "11"),
    ("90834", "Psychotherapy, 45 minutes", 140, 270, "Behavioral Health", "11"),
    ("97110", "Therapeutic exercise, each 15 min", 60, 145, "Physical Therapy", "11"),
    ("97140", "Manual therapy, each 15 min", 55, 135, "Physical Therapy", "11"),
    ("11102", "Skin biopsy, single lesion", 190, 460, "Dermatology", "11"),
    ("17000", "Destruction of premalignant lesion", 150, 380, "Dermatology", "11"),
    ("J1885", "Ketorolac tromethamine injection, 15 mg", 25, 90, "Emergency Medicine", "23"),
    ("J3301", "Triamcinolone acetonide injection, 10 mg", 30, 110, "Orthopedics", "11"),
]

DIAGNOSES = [
    ("E11.9", "Type 2 diabetes mellitus without complications"),
    ("I10", "Essential hypertension"),
    ("E78.5", "Hyperlipidemia, unspecified"),
    ("M54.50", "Low back pain, unspecified"),
    ("M17.11", "Unilateral primary osteoarthritis, right knee"),
    ("J45.909", "Unspecified asthma, uncomplicated"),
    ("Z00.00", "Routine general medical examination"),
    ("F41.1", "Generalized anxiety disorder"),
    ("F33.1", "Major depressive disorder, recurrent, moderate"),
    ("K21.9", "Gastro-esophageal reflux disease without esophagitis"),
    ("R51.9", "Headache, unspecified"),
    ("R07.9", "Chest pain, unspecified"),
    ("L57.0", "Actinic keratosis"),
    ("Z12.11", "Encounter for screening for malignant neoplasm of colon"),
]

# denial_code, short_description, category, is_appealable, appeal_window_days,
# typical_resolution
DENIAL_CODES = [
    ("DN-PRIOR-AUTH", "Service requires prior authorization that was not on file",
     "Administrative", True, 180,
     "Submit a retrospective authorization request with clinical notes within 90 days of service."),
    ("DN-OON", "Provider is outside the plan network",
     "Network", True, 180,
     "Request a network gap exception, or resubmit with documentation of an emergency."),
    ("DN-NOT-COVERED", "Service is excluded under the member's plan",
     "Benefit", True, 180,
     "Confirm the exclusion in the plan policy; appeal only with evidence of medical necessity."),
    ("DN-MED-NEC", "Service not medically necessary per clinical policy",
     "Clinical", True, 180,
     "Appeal with chart notes and failed-conservative-therapy documentation."),
    ("DN-REFERRAL", "Specialist visit without a required PCP referral",
     "Administrative", True, 180,
     "Obtain a backdated referral from the PCP where the plan permits it."),
    ("DN-DUPLICATE", "Duplicate of a previously adjudicated claim",
     "Administrative", False, 0,
     "No appeal required; the original claim stands."),
    ("DN-ELIGIBILITY", "Member was not enrolled on the date of service",
     "Eligibility", True, 180,
     "Provide proof of enrollment or correct the date of service."),
    ("DN-TIMELY", "Claim received after the timely filing deadline",
     "Administrative", True, 90,
     "Appeal only with proof of timely original submission."),
    ("DN-CODING", "Procedure code invalid, unbundled, or inconsistent with diagnosis",
     "Coding", False, 0,
     "Provider corrects and resubmits as a replacement claim."),
    ("DN-FORMULARY", "Drug is not on the plan formulary",
     "Pharmacy", True, 180,
     "Request a formulary exception with a prescriber statement."),
]


@dataclass
class Provider:
    provider_id: str
    provider_name: str
    specialty: str
    network_status: str
    state: str
    city: str

def build_plans() -> list[dict]:
    rows = []
    for (pid, name, tier, di, df, oop, coins, pcp, spec, er, uc, rxg, rxb, prem) in PLANS:
        rows.append({
            "plan_id": pid,
            "plan_name": name,
            "metal_tier": tier,
            "deductible_individual": di,
            "deductible_family": df,
            "oop_max_individual": oop,
            "coinsurance_rate": coins,
            "pcp_copay": pcp,
            "specialist_copay": spec,
            "er_copay": er,
            "urgent_care_copay": uc,
            "rx_generic_copay": rxg,
            "rx_brand_copay": rxb,
            "monthly_premium": prem,
        })
    return rows


def build_members(rng: random.Random, plan_ids: list[str]) -> list[dict]:
    weights = [0.18, 0.30, 0.14, 0.26, 0.12]
    rows = []
    for i in range(1, N_MEMBERS + 1):
        state, cities = rng.choice(STATES)
        plan_id = rng.choices(plan_ids, weights=weights, k=1)[0]

        if rng.random() < 0.72:
            start = date(2024, 1, 1)
        elif rng.random() < 0.6:
            start = date(2025, 1, 1)
        else:
            start = date(2024, rng.randint(2, 12), 1)

        if rng.random() < 0.11:
            end_month = rng.randint(3, 11)
            end_year = 2025 if start.year == 2024 else 2025
            end = date(end_year, end_month, 28)
            if end <= start:
                end = start + timedelta(days=200)
            enrollment_end = end.isoformat()
            is_active = 0
        else:
            enrollment_end = ""
            is_active = 1

        rows.append({
            "member_id": f"M{100000 + i}",
            "birth_year": rng.randint(1945, 2016),
            "gender": rng.choice(["F", "M", "X"]) if rng.random() > 0.02 else "X",
            "state": state,
            "city": rng.choice(cities),
            "plan_id": plan_id,
            "enrollment_start": start.isoformat(),
            "enrollment_end": enrollment_end,
            "risk_score": round(rng.gauss(1.0, 0.42), 3),
            "is_active": is_active,
        })
    for r in rows:
        r["risk_score"] = max(0.25, min(4.5, r["risk_score"]))
    return rows


def build_providers(rng: random.Random) -> list[Provider]:
    used: set[str] = set()
    providers: list[Provider] = []
    i = 0
    while len(providers) < N_PROVIDERS:
        i += 1
        specialty = SPECIALTIES[i % len(SPECIALTIES)]
        prefix = rng.choice(PROVIDER_PREFIXES)
        suffix = rng.choice(PROVIDER_SUFFIXES[specialty])
        name = f"{prefix} {suffix}"
        if name in used:
            continue
        used.add(name)
        state, cities = rng.choice(STATES)
        network = "out_of_network" if rng.random() < 0.22 else "in_network"
        providers.append(Provider(
            provider_id=f"P{5000 + len(providers) + 1}",
            provider_name=name,
            specialty=specialty,
            network_status=network,
            state=state,
            city=rng.choice(cities),
        ))
    return providers


def _month_weight(d: date) -> float:
    """mild seasonality...utilisation peaks in winter and dips in summer"""
    seasonal = {1: 1.18, 2: 1.10, 3: 1.05, 4: 0.98, 5: 0.95, 6: 0.88,
                7: 0.86, 8: 0.92, 9: 1.02, 10: 1.08, 11: 1.12, 12: 1.15}
    return seasonal[d.month]


def _pick_service_date(rng: random.Random) -> date:
    span = (PERIOD_END - PERIOD_START).days
    for _ in range(12):
        d = PERIOD_START + timedelta(days=rng.randint(0, span))
        if rng.random() < _month_weight(d) / 1.18:
            return d
    return PERIOD_START + timedelta(days=rng.randint(0, span))


def _denial_probability(service_date: date, provider: Provider) -> float:
    p = 0.115
    if provider.network_status == "out_of_network":
        p += 0.13
    if service_date >= date(2025, 7, 1) and service_date <= date(2025, 9, 30):
        p += 0.075
    return p


def build_claims(
    rng: random.Random,
    members: list[dict],
    providers: list[Provider],
    plans: list[dict],
) -> tuple[list[dict], list[dict]]:
    plan_by_id = {p["plan_id"]: p for p in plans}
    proc_by_specialty: dict[str, list[tuple]] = {}
    for proc in PROCEDURES:
        proc_by_specialty.setdefault(proc[4], []).append(proc)

    claims: list[dict] = []
    lines: list[dict] = []
    line_seq = 0

    denial_lookup = {d[0]: d for d in DENIAL_CODES}

    for n in range(1, N_CLAIMS + 1):
        claim_id = f"CLM-{100000 + n}"
        member = rng.choice(members)
        plan = plan_by_id[member["plan_id"]]

        candidates = [p for p in providers if p.state == member["state"]] or providers
        provider = rng.choice(candidates)

        service_date = _pick_service_date(rng)

        terminated_before_service = bool(
            member["enrollment_end"] and date.fromisoformat(member["enrollment_end"]) < service_date
        )

        received_date = service_date + timedelta(days=rng.randint(1, 24))

        pool = proc_by_specialty.get(provider.specialty) or PROCEDURES
        n_lines = rng.choices([1, 2, 3, 4], weights=[0.55, 0.27, 0.13, 0.05], k=1)[0]
        chosen = [rng.choice(pool) for _ in range(n_lines)]
        diagnosis = rng.choice(DIAGNOSES)

        claim_billed = 0.0
        claim_allowed = 0.0
        pending_lines = []
        for idx, proc in enumerate(chosen, start=1):
            code, desc, lo, hi, _spec, pos = proc
            units = 1 if code not in ("97110", "97140") else rng.randint(1, 4)
            line_billed = round(rng.uniform(lo, hi) * units, 2)
            if provider.network_status == "in_network":
                allowed_ratio = rng.uniform(0.42, 0.66)
            else:
                allowed_ratio = rng.uniform(0.78, 0.96)
            line_allowed = round(line_billed * allowed_ratio, 2)
            claim_billed += line_billed
            claim_allowed += line_allowed
            line_seq += 1
            pending_lines.append({
                "claim_line_id": f"CL-{200000 + line_seq}",
                "claim_id": claim_id,
                "line_number": idx,
                "procedure_code": code,
                "procedure_desc": desc,
                "diagnosis_code": diagnosis[0],
                "units": units,
                "billed_amount": line_billed,
                "allowed_amount": line_allowed,
                "place_of_service": pos,
            })

        claim_billed = round(claim_billed, 2)
        claim_allowed = round(claim_allowed, 2)
        place_of_service = pending_lines[0]["place_of_service"]
        claim_type = {
            "23": "facility", "22": "facility", "24": "facility", "81": "professional",
        }.get(place_of_service, "professional")
        if provider.specialty == "Pharmacy":
            claim_type = "pharmacy"

        status = "paid"
        denial_code = ""
        paid_date: str = ""

        days_since = (PERIOD_END - service_date).days
        if days_since < 30 and rng.random() < 0.45:
            status = "pending"
        elif terminated_before_service and rng.random() < 0.8:
            status = "denied"
            denial_code = "DN-ELIGIBILITY"
        elif rng.random() < _denial_probability(service_date, provider):
            status = "denied"
            if provider.network_status == "out_of_network" and rng.random() < 0.5:
                denial_code = "DN-OON"
            elif service_date >= date(2025, 7, 1) and service_date <= date(2025, 9, 30) and rng.random() < 0.55:
                denial_code = "DN-PRIOR-AUTH"
            else:
                pool = {
                    "DN-PRIOR-AUTH": 0.25,
                    "DN-OON": 0.10,
                    "DN-NOT-COVERED": 0.14,
                    "DN-MED-NEC": 0.13,
                    "DN-REFERRAL": 0.09,
                    "DN-DUPLICATE": 0.07,
                    "DN-TIMELY": 0.05,
                    "DN-CODING": 0.11,
                    "DN-FORMULARY": 0.06,
                }
                denial_code = rng.choices(
                    list(pool), weights=list(pool.values()), k=1
                )[0]
            if denial_code == "DN-TIMELY":
                received_date = service_date + timedelta(days=rng.randint(400, 520))

        if status == "paid":
            paid = received_date + timedelta(days=rng.randint(3, 34))
            paid_date = paid.isoformat()

            if place_of_service == "11" and claim_allowed < 600:
                is_specialist = provider.specialty not in (
                    "Family Medicine", "Internal Medicine", "Pediatrics")
                copay = plan["specialist_copay"] if is_specialist else plan["pcp_copay"]
                cost_share = float(min(copay, claim_allowed))
            elif place_of_service == "23":
                cost_share = float(min(plan["er_copay"], claim_allowed))
            else:
                cost_share = round(claim_allowed * float(plan["coinsurance_rate"]), 2)
            cost_share = round(min(cost_share, claim_allowed), 2)

            plan_paid = round(claim_allowed - cost_share, 2)

            if provider.network_status == "out_of_network":
                member_resp = round(cost_share + (claim_billed - claim_allowed), 2)
            else:
                member_resp = cost_share

            final_allowed = claim_allowed
        elif status == "denied":
            paid = received_date + timedelta(days=rng.randint(2, 21))
            paid_date = paid.isoformat()
            final_allowed = 0.0
            plan_paid = 0.0
            if denial_code in ("DN-OON", "DN-NOT-COVERED", "DN-FORMULARY"):
                member_resp = claim_billed
            else:
                member_resp = 0.0
        else:  # pending
            final_allowed = 0.0
            plan_paid = 0.0
            member_resp = 0.0

        for ln in pending_lines:
            if status != "paid":
                ln["allowed_amount"] = 0.0
            ln.pop("place_of_service")
            lines.append(ln)

        claims.append({
            "claim_id": claim_id,
            "member_id": member["member_id"],
            "provider_id": provider.provider_id,
            "plan_id": member["plan_id"],
            "claim_type": claim_type,
            "place_of_service": place_of_service,
            "service_date": service_date.isoformat(),
            "received_date": received_date.isoformat(),
            "paid_date": paid_date,
            "status": status,
            "billed_amount": claim_billed,
            "allowed_amount": round(final_allowed, 2),
            "plan_paid": plan_paid,
            "member_responsibility": member_resp,
            "denial_code": denial_code,
        })

    assert all(c["denial_code"] in denial_lookup or c["denial_code"] == "" for c in claims)
    return claims, lines


def build_denial_codes() -> list[dict]:
    return [
        {
            "denial_code": code,
            "short_description": desc,
            "category": cat,
            "is_appealable": 1 if appealable else 0,
            "appeal_window_days": window,
            "typical_resolution": resolution,
        }
        for (code, desc, cat, appealable, window, resolution) in DENIAL_CODES
    ]


def build_providers_rows(providers: list[Provider]) -> list[dict]:
    return [
        {
            "provider_id": p.provider_id,
            "provider_name": p.provider_name,
            "specialty": p.specialty,
            "network_status": p.network_status,
            "state": p.state,
            "city": p.city,
        }
        for p in providers
    ]


SCHEMA_DOCS = {
    "claims": {
        "description": (
            "One row per adjudicated claim header. This is the fact table for all "
            "spend, denial and utilisation questions.\n"
            "Financial rules that always hold:\n"
            "  - plan_paid is what the plan paid; member_responsibility is what the "
            "member owes.\n"
            "  - For PAID claims from an in-network provider: "
            "plan_paid + member_responsibility = allowed_amount.\n"
            "  - For PAID claims from an out-of-network provider the member is also "
            "balance-billed the difference between billed_amount and allowed_amount, "
            "so plan_paid + member_responsibility = billed_amount instead.\n"
            "  - DENIED and PENDING claims have allowed_amount = 0 and plan_paid = 0. "
            "Always filter on status before summing money."
        ),
        "types": {
            "place_of_service": "VARCHAR",
        },
        "columns": {
            "claim_id": "VARCHAR primary key, format 'CLM-100001'.",
            "member_id": "VARCHAR foreign key to members.member_id.",
            "provider_id": "VARCHAR foreign key to providers.provider_id.",
            "plan_id": "VARCHAR foreign key to plans.plan_id; the plan in force for the member.",
            "claim_type": "VARCHAR, one of 'professional', 'facility', 'pharmacy'.",
            "place_of_service": "VARCHAR CMS place-of-service code: '11' office, '22' outpatient hospital, '23' emergency room, '24' ambulatory surgical centre, '81' independent laboratory. Compare as text, never as a number.",
            "service_date": "DATE the care was delivered. Use this for utilisation and trend questions.",
            "received_date": "DATE the claim arrived at the plan. Use this for timely-filing questions.",
            "paid_date": "DATE the claim finished adjudication; NULL while status = 'pending'. Use this for cash-flow and turnaround questions.",
            "status": "VARCHAR, one of 'paid', 'denied', 'pending'. Always filter on this before summing money.",
            "billed_amount": "DOUBLE, what the provider charged.",
            "allowed_amount": "DOUBLE, the contracted amount the plan recognises. 0 for denied and pending claims.",
            "plan_paid": "DOUBLE, the plan's share. Sum this to answer 'how much did we pay'.",
            "member_responsibility": "DOUBLE, the member's share (copay, coinsurance, or -- out of network -- also the balance above the allowed amount).",
            "denial_code": "VARCHAR foreign key to denial_codes.denial_code. NULL unless status = 'denied', so use 'denial_code IS NOT NULL' rather than comparing to an empty string.",
        },
    },
    "claim_lines": {
        "description": (
            "One row per service line within a claim. Join to claims on claim_id. "
            "Use this table -- not claims -- for anything about specific procedures "
            "or diagnoses. Line amounts sum to the claim header amounts."
        ),
        "columns": {
            "claim_line_id": "VARCHAR primary key, format 'CL-200001'.",
            "claim_id": "VARCHAR foreign key to claims.claim_id.",
            "line_number": "BIGINT sequence within the claim, starting at 1.",
            "procedure_code": "VARCHAR CPT or HCPCS code, e.g. '99213', 'J1885'. Never cast to a number.",
            "procedure_desc": "VARCHAR human-readable procedure description.",
            "diagnosis_code": "VARCHAR ICD-10-CM code, e.g. 'E11.9'.",
            "units": "BIGINT count of units billed on the line.",
            "billed_amount": "DOUBLE charged for this line.",
            "allowed_amount": "DOUBLE allowed for this line; 0 unless the parent claim is paid.",
        },
    },
    "members": {
        "description": (
            "One row per enrolled member. Fully synthetic and de-identified: there "
            "are no names, addresses or dates of birth, only a birth year. A member "
            "with an empty enrollment_end is still enrolled."
        ),
        "columns": {
            "member_id": "VARCHAR primary key, format 'M100001'.",
            "birth_year": "BIGINT year of birth. Approximate age = 2025 - birth_year.",
            "gender": "VARCHAR, 'F', 'M' or 'X'.",
            "state": "VARCHAR two-letter state code: WA, OR or ID.",
            "city": "VARCHAR city of residence.",
            "plan_id": "VARCHAR foreign key to plans.plan_id.",
            "enrollment_start": "DATE coverage began.",
            "enrollment_end": "DATE coverage ended, NULL if still enrolled. Test with IS NULL.",
            "risk_score": "DOUBLE morbidity index; 1.0 is average. Higher means sicker.",
            "is_active": "BIGINT, 1 if currently enrolled, else 0.",
        },
    },
    "providers": {
        "description": "One row per contracted or non-contracted provider organisation. All names are fictional.",
        "columns": {
            "provider_id": "VARCHAR primary key, format 'P5001'.",
            "provider_name": "VARCHAR fictional practice name.",
            "specialty": "VARCHAR clinical specialty, e.g. 'Cardiology', 'Physical Therapy'.",
            "network_status": "VARCHAR, 'in_network' or 'out_of_network'. Out-of-network claims deny more often and cost members more.",
            "state": "VARCHAR two-letter state code.",
            "city": "VARCHAR practice city.",
        },
    },
    "plans": {
        "description": (
            "One row per benefit plan design. The numeric benefit values here are "
            "the authoritative cost-sharing amounts; the policy documents describe "
            "the rules in prose. When a question asks for a dollar figure, prefer "
            "this table over the documents."
        ),
        "columns": {
            "plan_id": "VARCHAR primary key, e.g. 'CH-GLD-1500'.",
            "plan_name": "VARCHAR marketing name, e.g. 'Cascade Gold 1500'.",
            "metal_tier": "VARCHAR, 'Bronze', 'Silver', 'Gold' or 'Platinum'.",
            "deductible_individual": "BIGINT annual individual deductible, in dollars.",
            "deductible_family": "BIGINT annual family deductible, in dollars.",
            "oop_max_individual": "BIGINT annual individual out-of-pocket maximum, in dollars.",
            "coinsurance_rate": "DOUBLE member coinsurance after deductible, as a fraction (0.20 = 20%).",
            "pcp_copay": "BIGINT primary-care visit copay, in dollars. 0 on the HSA plan, which uses coinsurance instead.",
            "specialist_copay": "BIGINT specialist visit copay, in dollars.",
            "er_copay": "BIGINT emergency room copay, in dollars.",
            "urgent_care_copay": "BIGINT urgent care copay, in dollars.",
            "rx_generic_copay": "BIGINT generic prescription copay, in dollars.",
            "rx_brand_copay": "BIGINT brand prescription copay, in dollars.",
            "monthly_premium": "DOUBLE monthly premium, in dollars.",
        },
    },
    "denial_codes": {
        "description": (
            "Lookup table describing each denial reason, its category, and whether "
            "it can be appealed. Join from claims.denial_code to translate a code "
            "into something a human can act on."
        ),
        "columns": {
            "denial_code": "VARCHAR primary key, e.g. 'DN-PRIOR-AUTH'.",
            "short_description": "VARCHAR one-line explanation of the denial.",
            "category": "VARCHAR, one of 'Administrative', 'Network', 'Benefit', 'Clinical', 'Eligibility', 'Coding', 'Pharmacy'.",
            "is_appealable": "BIGINT, 1 if the denial can be appealed, else 0.",
            "appeal_window_days": "BIGINT days the member has to file an appeal; 0 when not appealable.",
            "typical_resolution": "VARCHAR standard operational next step for this denial.",
        },
    },
}

def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"refusing to write an empty table: {path}")
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=list(rows[0].keys()), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/warehouse", help="output directory")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rng = random.Random(SEED)

    plans = build_plans()
    members = build_members(rng, [p["plan_id"] for p in plans])
    providers = build_providers(rng)
    claims, lines = build_claims(rng, members, providers, plans)

    write_csv(out / "plans.csv", plans)
    write_csv(out / "members.csv", members)
    write_csv(out / "providers.csv", build_providers_rows(providers))
    write_csv(out / "claims.csv", claims)
    write_csv(out / "claim_lines.csv", lines)
    write_csv(out / "denial_codes.csv", build_denial_codes())

    with (out / "_schema_docs.json").open(
        "w", encoding="utf-8", newline="\n"
    ) as fh:
        fh.write(json.dumps(SCHEMA_DOCS, indent=2) + "\n")

    paid = sum(1 for c in claims if c["status"] == "paid")
    denied = sum(1 for c in claims if c["status"] == "denied")
    pending = sum(1 for c in claims if c["status"] == "pending")
    total_paid = sum(c["plan_paid"] for c in claims)

    print(f"wrote {out}/")
    print(f"  plans.csv        {len(plans):>6} rows")
    print(f"  members.csv      {len(members):>6} rows")
    print(f"  providers.csv    {len(providers):>6} rows")
    print(f"  claims.csv       {len(claims):>6} rows  "
          f"({paid} paid / {denied} denied / {pending} pending)")
    print(f"  claim_lines.csv  {len(lines):>6} rows")
    print(f"  denial_codes.csv {len(DENIAL_CODES):>6} rows")
    print("  _schema_docs.json  table + column descriptions")
    print(f"  total plan_paid  ${total_paid:,.2f}")


if __name__ == "__main__":
    main()
