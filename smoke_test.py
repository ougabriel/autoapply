"""Quick end-to-end smoke test of the core services (no server needed)."""
from app import db
from app.services import profiles, sponsor_match, cv_router, filters, tailoring, integrity_gate

db.init_db()
print("DB initialised.")

print("Profiles found:", profiles.list_profiles())
p = profiles.load_profile("racheal")
print("Loaded profile:", p.candidate, "| lanes:", list(p.cvLanes))

print("Sponsor register loaded:", sponsor_match.register_loaded())
for emp in ["Barchester Healthcare", "HC-One", "Totally Made Up Care Ltd XYZ"]:
    print(f"  is_sponsor({emp!r}) =", sponsor_match.is_sponsor(emp))

title = "Health Care Assistant"
desc = ("We are seeking a caring Health Care Assistant to provide person-centred "
        "personal care, support with dementia care, safe moving and handling, and "
        "infection prevention and control. Care Certificate welcome. Full training provided.")
lane = cv_router.route(p, title, desc)
print("Routed lane:", lane, "->", cv_router.cv_file_for_lane(p, lane))

decision = filters.evaluate(p, "Barchester Healthcare", title, desc)
print("Filter:", decision.keep, "-", decision.reason)

tailored = tailoring.build_letter(p, "Barchester Healthcare", title, desc, lane)
print("Matched strengths:", tailored.matched_strengths)
print("Integrity OK:", tailored.gate.ok)
print("Violations:", tailored.gate.violations)

# Negative test: a letter that breaks the rules must be caught.
bad = "I would leverage my visa sponsorship and seamless NVQ Diploma \u2014 truly robust."
res = integrity_gate.check_text(bad, p, is_prose=True)
print("\nNegative test ok (should be False):", res.ok)
for v in res.violations:
    print("  violation:", v)
for w in res.warnings:
    print("  warning:", w)

print("\nSMOKE TEST PASSED" if tailored.gate.ok and not res.ok else "\nSMOKE TEST FAILED")
