# Architectural decisions and trade-offs

Each entry states the decision, what was rejected, and what the decision
costs. Decisions without a stated cost are usually decisions nobody
actually made.

---

## 1. Invariants live in the database, not the service layer

**Decision.** Business invariants — non-negative stock, legal state
transitions, one sample per storage position, append-only audit — are
enforced by constraints and triggers. Service functions duplicate some of
these checks, but only to produce readable errors.

**Rejected: enforce in the service layer alone.** The service layer is one
door. `Model.objects.filter(...).update()` is another, a data migration is
a third, a `manage.py shell` session is a fourth, and psql is a fifth. In a
lab context the fourth and fifth are not hypothetical — they are how
urgent corrections actually get made at 11pm. An invariant that only holds
on the API path is not an invariant.

**Rejected: enforce in both, treating them as equals.** They are not
equals, and pretending otherwise leads to the common failure where a team
believes `SELECT FOR UPDATE` is their guarantee. The database is the
guarantee; application checks are ergonomics.

**Cost.** Logic lives in PL/pgSQL, which is harder to test, harder to
debug, and invisible to developers reading only Python. Mitigated by
keeping trigger logic small and mechanical, generating the state machine
table from the Python definition so there is one source of truth, and
testing each trigger by attempting the violation directly in SQL.

---

## 2. Pessimistic locking for stock allocation

**Decision.** `SELECT ... FOR UPDATE` on candidate lots, under
`READ COMMITTED`, with a conditional UPDATE behind it and a CHECK
constraint behind that.

**Rejected: optimistic concurrency (version column + retry).** Contention
here is genuinely high and bursty — a morning run has many benches booking
the same reagent within seconds. Optimistic control turns every loser into
a wasted transaction plus a retry, and the retries collide again. Blocking
converts contention into waiting rather than into work thrown away.

**Rejected: `SERIALIZABLE`.** Once the rows a transaction depends on are
locked `FOR UPDATE`, there is no remaining read-skew window for
`SERIALIZABLE` to close. It would add 40001 serialization failures that
every caller must be written to retry, cost throughput, and buy a guarantee
already held. Isolation levels are not a difficulty ladder.

**Rejected: Redis distributed lock (Redlock).** This was specified in the
original brief and is the decision most worth explaining, because it looks
sophisticated and is actively harmful here.

A row lock lives in the same system as the data it protects, so it cannot
disagree with that data. A Redis lease is a separate system making a
*claim* about the data: it can expire while the Postgres transaction is
still open, at which point a second worker acquires it and both believe
they hold exclusive access. That is precisely Kleppmann's critique of
Redlock — it is unsafe for correctness without fencing tokens, and the
fencing token here would be a row version the database is already
maintaining for free.

Layering Redlock over `SELECT FOR UPDATE` therefore adds a failure mode
and no safety. Redis has a real place in this architecture — caching
storage-tree reads, and admission control to shed load before it reaches
the connection pool — but not as a correctness device.

**Cost.** Blocking means a slow transaction holding a hot lot's lock stalls
everyone behind it. Mitigated by keeping reservation transactions short
(no external calls inside the lock) and by `connect_timeout` so a stuck
connection fails rather than accumulating. A pathological case would need
lock timeouts and queue-depth monitoring; neither is implemented.

**Verification.** The layering claim was tested by deletion. With the row
lock removed, the 50-thread test still passed (the compare-and-swap held).
With the CAS also removed, Postgres rejected the write and the test failed.
This is documented because it contradicts the intuitive story, and the
intuitive story is the one most systems ship with.

---

## 3. Materialised path (ltree) for the storage hierarchy

**Decision.** `parent_id` FK for referential integrity, plus an `ltree`
`path` column derived by trigger, with a GiST index.

**Rejected: adjacency list alone.** "Show everything in Freezer 3" becomes
a recursive CTE on the single most common query in a storage UI.

**Rejected: closure table.** Gives O(1) subtree reads and cheap
re-parenting, at the cost of a second table holding O(depth) rows per node
and a join on every query. It wins when the tree is deep and constantly
restructured. Physical lab storage is the opposite: you occasionally move
a rack between freezers, you do not restructure a building. Optimising for
re-parenting would be optimising for the rare case.

**Cost.** Re-parenting rewrites every descendant's path. Accepted because
it is rare, and it happens atomically inside the trigger that changed the
parent. Paths are also derived data that can in principle drift from the FK
— which is why the FK, not the path, is the source of truth, and the path
is never written by application code.

**A bug this caused, worth recording.** Because the path is
trigger-populated, a freshly saved Python instance held `''`. An empty
ltree is an ancestor of *every* path, so a subtree query built from a stale
instance silently returned the entire sample table instead of nothing — a
failure that opens rather than closes. Fixed by re-reading `path` after
save and by refusing subtree queries on an empty path. Derived columns
populated outside the ORM need an explicit read-back; the ORM will not do
it for you.

---

## 4. Audit trail written by triggers, not middleware

**Decision.** A generic PL/pgSQL trigger attached per table, with the
acting user passed down through transaction-scoped Postgres session
variables set by middleware.

**Rejected: Django signals.** They only fire for ORM operations on
individual instances. `bulk_create`, `queryset.update()`, raw SQL, and
migrations all bypass them — and those are exactly the writes an auditor
most wants to see.

**Rejected: application middleware writing audit rows.** Same gap, plus it
audits *intent* rather than *effect*: it records what the code meant to do,
not what the database actually committed.

**Cost.** The trigger cannot know *why* a change happened. That context is
supplied by the service layer through `set_config('lims.reason_code', ...,
true)` — transaction-scoped, so it cannot leak into the next request on a
pooled connection. It also means a write from psql with no session
variables set produces an audit row with a null actor. That is the correct
outcome: an unattributed change is recorded as unattributed rather than
going unrecorded.

---

## 5. Per-stream hash chains, not one global chain

**Decision.** One chain per audited table, each with its own head row.

**Rejected: a single global chain.** Simpler to verify and gives a total
order over all system events, but every mutation anywhere would serialise
behind one row lock. Every sample move would queue behind every stock
decrement. That is a system-wide throughput ceiling in exchange for
tidiness.

**Cost.** No total ordering across tables, so "what happened system-wide at
14:32" requires merging streams by timestamp rather than reading one
sequence. Acceptable: audit queries are overwhelmingly scoped to one record
or one table.

**Limit.** Chaining detects tampering only while the attacker cannot
recompute the chain. Anyone with UPDATE rights on `audit_event` plus
SHA-256 can rewrite it forward. Closing that requires anchoring chain heads
outside the database's blast radius. Not implemented; stated in the README.

---

## 6. Placement history instead of a location column on Sample

**Decision.** `SamplePlacement` rows with `removed_at IS NULL` meaning
"current", enforced by two partial unique indexes: one open placement per
sample, one sample per occupied grid position.

**Rejected: `location_id` + `row` + `col` on Sample.** Simpler to query and
loses the custody trail — which is the specific thing a regulator asks for.
Chain of custody is not a feature bolted onto sample tracking; it is what
sample tracking is.

**Cost.** "Where is this sample" is a join rather than a column read, and
"what is in this box" needs the placement table. Both are indexed, and the
history is reconstructible for free, which the denormalised version never
is.

The occupancy guarantee is worth noting: two technicians scanning the same
well concurrently is a real race, and it is settled by a partial unique
index rather than by a check-then-insert that would race.

---

## 7. Reservations expire

**Decision.** Every hold carries a TTL; a sweeper reclaims lapsed ones.

**Rejected: holds persist until explicitly settled.** A worker that dies
between `reserve` and `consume` strands stock permanently — the inventory
equivalent of a leaked lock, and it presents to users as "the system says
we have none but the shelf is full."

**Cost.** A legitimately slow operation can have its reservation swept out
from under it, and `consume` then fails. The TTL is configurable
(`RESERVATION_TTL_SECONDS`, default 15 minutes) and should exceed the
slowest realistic protocol step. A heartbeat/extension mechanism would be
the better answer for long runs; not implemented.

---

## 8. Idempotency keys on reservations, unique barcodes for accessioning

**Decision.** Both retry-safety mechanisms rest on unique constraints, not
on pre-checks.

**Rejected: check-then-act (`if not exists: create`).** It races. Two
concurrent retries of the same request both find nothing and both create.
Letting the constraint arbitrate and catching the violation is the only
version that holds under concurrency.

**Cost.** Duplicate handling becomes exception handling, which reads less
naturally. Worth it: barcode scanners double-fire and networks time out
constantly, so this path is exercised in normal operation, not just in
edge cases. Batch accessioning reports duplicates separately rather than
failing, so one re-scanned tube does not force an operator to redo a
96-well rack.

---

## 9. Django, and no SQLite fallback

**Decision.** Django + DRF on PostgreSQL only.

**Why Django.** A LIMS needs a large amount of unglamorous back-office CRUD
— locations, freezers, racks, reagents, lots, suppliers, test definitions.
Django admin provides that essentially free, which is weeks of work in a
framework that does not. The concurrency story is identical in any language
because Postgres does the locking, and real lab load is hundreds of samples
a day rather than thousands of requests a second. The trade was
admin-for-free against raw throughput that is not needed.

**Why no SQLite.** The schema depends on ltree, partial unique indexes,
row-level locking, and PL/pgSQL. A SQLite convenience mode would silently
drop every guarantee above and let tests pass without testing anything.
Requiring Postgres everywhere — including in the test runner — is the only
way the test suite means what it says.

**Cost.** Contributors need a running Postgres. `docker compose up -d db`
covers it.
