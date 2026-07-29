"""
Make audit hashing independent of the writer's session timezone.

The bug: `timestamptz::text` and `to_jsonb(row)` both render timestamps
using the *session's* TimeZone setting. The trigger hashed a row under
whatever timezone the writing connection happened to have, and
`audit_verify_chain` re-rendered it under the reading connection's
timezone. Django pins its connections to UTC, so application writes
verified fine — but a row written from a psql session in Asia/Kolkata
hashed differently than it read back, and verification reported the chain
as broken.

A false positive on tamper detection is not a cosmetic bug. It sends
someone into an integrity incident over nothing, and — worse — it means
the hash was never a stable function of the row's content.

The fix is `SET timezone = 'UTC'` on both function definitions. Postgres
applies that for the duration of each call and restores the caller's
setting afterwards, so hashing is deterministic no matter who writes.

NOTE: this changes how hashes are computed, so chains written before this
migration will not verify against the new function. On an empty or
development database that is irrelevant. On a database with real history,
the old chain must be verified with the old function and closed out — its
head hash recorded externally — before migrating. There is no way to
rewrite historical hashes without defeating the purpose of having them.
"""

from django.db import migrations

AUDIT_FUNCTION = r"""
CREATE OR REPLACE FUNCTION audit_row_change() RETURNS trigger AS $$
DECLARE
    v_pk_column      text := COALESCE(TG_ARGV[0], 'id');
    v_excluded       text[] := string_to_array(COALESCE(TG_ARGV[1], ''), ',');
    v_stream         text := TG_TABLE_NAME;
    v_old            jsonb;
    v_new            jsonb;
    v_changed        text[];
    v_pk             text;
    v_actor_id       bigint;
    v_actor_username text;
    v_request_id     text;
    v_reason_code    text;
    v_reason_text    text;
    v_prev_hash      text;
    v_seq            bigint;
    v_row_hash       text;
    v_now            timestamptz := clock_timestamp();
    v_payload        text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_old := to_jsonb(OLD);
        v_new := NULL;
        v_pk  := v_old ->> v_pk_column;
    ELSIF TG_OP = 'INSERT' THEN
        v_old := NULL;
        v_new := to_jsonb(NEW);
        v_pk  := v_new ->> v_pk_column;
    ELSE
        v_old := to_jsonb(OLD);
        v_new := to_jsonb(NEW);
        v_pk  := v_new ->> v_pk_column;
    END IF;

    IF array_length(v_excluded, 1) IS NOT NULL THEN
        v_old := v_old - v_excluded;
        v_new := v_new - v_excluded;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        SELECT COALESCE(array_agg(n.key ORDER BY n.key), ARRAY[]::text[])
        INTO v_changed
        FROM jsonb_each(v_new) n
        WHERE v_old -> n.key IS DISTINCT FROM n.value;
    ELSE
        v_changed := ARRAY[]::text[];
    END IF;

    v_actor_id       := nullif(current_setting('lims.actor_id', true), '')::bigint;
    v_actor_username := COALESCE(nullif(current_setting('lims.actor_username', true), ''), '');
    v_request_id     := COALESCE(nullif(current_setting('lims.request_id', true), ''), '');
    v_reason_code    := COALESCE(nullif(current_setting('lims.reason_code', true), ''), '');
    v_reason_text    := COALESCE(nullif(current_setting('lims.reason_text', true), ''), '');

    INSERT INTO audit_chain_head (stream, last_hash, last_seq, updated_at)
    VALUES (v_stream, repeat('0', 64), 0, v_now)
    ON CONFLICT (stream) DO NOTHING;

    SELECT last_hash, last_seq INTO v_prev_hash, v_seq
    FROM audit_chain_head
    WHERE stream = v_stream
    FOR UPDATE;

    v_seq := v_seq + 1;

    v_payload := concat_ws('|',
        v_prev_hash, v_stream, v_seq::text, v_now::text, TG_OP,
        TG_TABLE_NAME, v_pk, COALESCE(v_actor_id::text, ''), v_actor_username,
        COALESCE(v_old::text, ''), COALESCE(v_new::text, ''),
        v_reason_code, v_reason_text
    );
    v_row_hash := encode(digest(v_payload, 'sha256'), 'hex');

    INSERT INTO audit_event (
        stream, seq, occurred_at, action, table_name, record_pk,
        actor_id, actor_username, previous_state, new_state, changed_fields,
        reason_code, reason_text, request_id, prev_hash, row_hash
    ) VALUES (
        v_stream, v_seq, v_now, TG_OP, TG_TABLE_NAME, v_pk,
        v_actor_id, v_actor_username, v_old, v_new, to_jsonb(v_changed),
        v_reason_code, v_reason_text, v_request_id, v_prev_hash, v_row_hash
    );

    UPDATE audit_chain_head
    SET last_hash = v_row_hash, last_seq = v_seq, updated_at = v_now
    WHERE stream = v_stream;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql
-- Pins timestamp rendering for the duration of the call, so the hash is a
-- function of the row's content and nothing else.
SET timezone = 'UTC';
"""

VERIFY = r"""
CREATE OR REPLACE FUNCTION audit_verify_chain(p_stream text)
RETURNS TABLE (broken_seq bigint, expected_hash text, stored_hash text) AS $$
DECLARE
    r            record;
    v_prev_hash  text := repeat('0', 64);
    v_expected   text;
    v_payload    text;
BEGIN
    FOR r IN
        SELECT * FROM audit_event WHERE stream = p_stream ORDER BY seq
    LOOP
        v_payload := concat_ws('|',
            v_prev_hash, r.stream, r.seq::text, r.occurred_at::text, r.action,
            r.table_name, r.record_pk, COALESCE(r.actor_id::text, ''),
            r.actor_username, COALESCE(r.previous_state::text, ''),
            COALESCE(r.new_state::text, ''), r.reason_code, r.reason_text
        );
        v_expected := encode(digest(v_payload, 'sha256'), 'hex');

        IF v_expected IS DISTINCT FROM r.row_hash THEN
            broken_seq    := r.seq;
            expected_hash := v_expected;
            stored_hash   := r.row_hash;
            RETURN NEXT;
            RETURN;
        END IF;

        v_prev_hash := r.row_hash;
    END LOOP;
END;
$$ LANGUAGE plpgsql
-- Must match the writer's timezone pinning exactly, or verification of a
-- perfectly good chain fails.
SET timezone = 'UTC';
"""


class Migration(migrations.Migration):
    dependencies = [("audit", "0002_audit_triggers")]

    operations = [
        migrations.RunSQL(sql=AUDIT_FUNCTION + VERIFY, reverse_sql=migrations.RunSQL.noop),
    ]
