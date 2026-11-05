# Microbiology master data — for review

Module 2 (Microbiology & Environment), build-order **step 1**: media master +
culture register. No dependency on the Laboratory pipeline, so it can land
before Module 1 is finished.

## What's here

| File | What it is |
|---|---|
| `apps/core/identifiers.py` | Shared public-ID generator (Global Standards §2). Used by every module, not just this one. |
| `apps/microbiology/models.py` | Media master, media lots, growth-promotion qualification, organism master, culture lots, culture movements. |
| `apps/microbiology/services.py` | All state changes. Views and admin call these, never the models directly. |
| `apps/microbiology/admin.py` | Clickable admin, delete disabled everywhere. |
| `apps/microbiology/tests/` | 18 tests covering the spec's business rules. |

**Do not copy** `config/test_settings.py` or `pytest.ini` — they exist only so
this code could be tested standalone before the repo had it. The project's own
settings replace them.

## Rules actually enforced (not just documented)

- Expired or growth-promotion-**failed** media is excluded from pickers *and*
  rejected server-side
- Cultures past expiry or past their passage limit cannot be issued
- Disposal requires a reason code; the record and its movement history survive
- No hard deletes anywhere
- Public IDs are server-generated, sequential **per tenant**, never editable

## Audit — now wired to the real service

Originally this had a `_audit()` placeholder. That was written before I'd seen
`apps/audit`, and it was the wrong shape: it assumed the application writes
audit rows.

It doesn't. Postgres triggers do, reading the actor from the database session
bound by `apps/audit/middleware.py`. The docstring in `apps/audit/context.py`
explains why that beats ORM signals, and it's right — data migrations and bulk
`update()` are exactly the writes an auditor cares about, and signals miss them.

So the placeholder is **deleted**, not wired. What the services now do is supply
the *why*, via `set_reason()` inside the transaction, before the write:

| Service | Reason code | Why it needs one |
|---|---|---|
| `create_media_lot` | `expiry_override` | Expiry set to something other than the master's shelf life |
| `record_media_qualification` | caller's code, else `growth_promotion_{pass\|fail}` | Changes whether the lot may be used at all |
| `dispose_culture` | caller's reason code (mandatory) | Irreversible |

Routine creates get no `set_reason` — the trigger's own before/after snapshot
already describes them fully.

### ⚠️ One thing I could not verify

**Do the audit triggers attach to new tables automatically, or does each new
table need registering?** There's a `management/` folder in `apps/audit` I
didn't get to look at. If triggers are attached per-table by a migration or a
command, the microbiology tables need the same treatment or they will write
**no audit rows at all** — which would be worse than the old stub, because it
would look like it was working.

Please check this before approving. It's the one thing standing between this
code and being genuinely audit-compliant.

## The decision this code forces

**This module is multi-tenant. The rest of the repo is not.**

Every model here carries an `organization` FK, IDs sequence per-organization,
and there are tests asserting one org never sees another's lots. Meanwhile
`Sample`, `User` and `AuditEvent` have no tenant column, and no `Organization`
model exists in the repo.

Neither side is wrong — tenancy is in the Global Standards doc, not in the
Laboratory module spec, so there was no way to know. But the two halves
currently disagree about what the product is, and that has to be resolved
before much more gets built on either side.

If we add tenancy: `Organization` should live in `apps/accounts` next to
`User`, not here. The class in `models.py` is a placeholder — delete it and
repoint the FKs rather than keeping two definitions.

If we defer it: strip the `organization` field and the isolation tests from
this module so the codebase is at least consistent, and accept that the
retrofit later touches every table, query, endpoint and test.

My view is that now is the cheapest this will ever be, but it's your call as
much as mine — it's your code that changes.

## Smaller things worth a second opinion

1. **`public_id` is unique per tenant, not globally.** Per-org sequences restart
   at 1 so each lab sees contiguous numbering and learns nothing about another
   tenant's volume. A test caught the collision this causes with a global unique
   constraint. If Laboratory already numbers differently, say so and I'll match
   it — it all lives in `apps/core/identifiers.py`.

2. **`CultureMovement.test_reference` is a plain string** until Laboratory's test
   instances exist. Becomes a real FK then.

3. **`test_catalogue.result_type` needs `count`, `pass_fail` and `qualitative`.**
   Microbiology results are plate counts and absent/present calls, not numbers
   with a spec range. This is the one genuine cross-module dependency.

## Run it

```bash
python manage.py migrate
python manage.py test apps.microbiology    # expect 18 tests, OK
```

Tests are Django `TestCase`, matching the rest of the repo — no new test
framework introduced.
