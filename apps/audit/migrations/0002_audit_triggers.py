"""
Generic audit trigger, hash chaining, append-only enforcement, and a chain
verification function.

The trigger is attached per table with `SELECT audit_attach('sample', 'id')`
so adding a new audited table is one line rather than a new function.

On hashing: each row's hash covers the previous row's hash plus this row's
content, so the stream is a chain. Recomputing the chain detects any edit
to or removal of a historical row. See the module docstring in models.py
for the honest limits of that guarantee — in particular, that it is
tamper-evident rather than tamper-proof until chain heads are anchored
outside this database.
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

    -- Never let secrets into the audit trail. Password hashes and tokens
    -- are stripped before anything is stored or hashed.
    IF array_length(v_excluded, 1) IS NOT NULL THEN
        v_old := v_old - v_excluded;
        v_new := v_new - v_excluded;
    END IF;

    -- Which columns actually moved. An UPDATE that changes nothing still
    -- gets a row (it is a fact that someone ran it) but with an empty list.
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

    -- Establish the stream lazily, then take the row lock that orders
    -- concurrent writers within this stream. Scoping the chain per table
    -- keeps a sample move from queueing behind a stock decrement.
    INSERT INTO audit_chain_head (stream, last_hash, last_seq, updated_at)
    VALUES (v_stream, repeat('0', 64), 0, v_now)
    ON CONFLICT (stream) DO NOTHING;

    SELECT last_hash, last_seq INTO v_prev_hash, v_seq
    FROM audit_chain_head
    WHERE stream = v_stream
    FOR UPDATE;

    v_seq := v_seq + 1;

    -- jsonb::text is canonical in Postgres (sorted keys, normalised
    -- whitespace), so the same logical row always hashes identically.
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

    RETURN NULL;  -- AFTER trigger; return value is ignored
END;
$$ LANGUAGE plpgsql;
"""

ATTACH_HELPER = r"""
CREATE OR REPLACE FUNCTION audit_attach(
    p_table text,
    p_pk_column text DEFAULT 'id',
    p_excluded_columns text DEFAULT ''
) RETURNS void AS $$
BEGIN
    EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', 'audit_' || p_table || '_trg', p_table);
    EXECUTE format(
        'CREATE TRIGGER %I AFTER INSERT OR UPDATE OR DELETE ON %I '
        'FOR EACH ROW EXECUTE FUNCTION audit_row_change(%L, %L)',
        'audit_' || p_table || '_trg', p_table, p_pk_column, p_excluded_columns
    );
END;
$$ LANGUAGE plpgsql;
"""

IMMUTABILITY = r"""
CREATE OR REPLACE FUNCTION audit_event_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_event is append-only; % is not permitted', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_event_immutable_trg
    BEFORE UPDATE OR DELETE ON audit_event
    FOR EACH ROW EXECUTE FUNCTION audit_event_immutable();
"""

VERIFY = r"""
-- Re-walks a stream and reports the first row whose hash does not follow
-- from its predecessor. Returns no rows when the chain is intact.
--
-- Note this deliberately does NOT run inside the audit trigger: verifying
-- on every write would turn an O(1) insert into an O(n) scan. It is a
-- scheduled integrity job, invoked from `manage.py verify_audit_chain`.
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
            RETURN;  -- everything after a break is meaningless
        END IF;

        v_prev_hash := r.row_hash;
    END LOOP;
END;
$$ LANGUAGE plpgsql;
"""

# Tables whose every change must be traceable. app_user is audited with
# credentials excluded — knowing that someone's role changed matters;
# storing their password hash in a second table does not.
ATTACHMENTS = r"""
SELECT audit_attach('sample', 'id');
SELECT audit_attach('sample_placement', 'id');
SELECT audit_attach('storage_location', 'id');
SELECT audit_attach('item_lot', 'id');
SELECT audit_attach('stock_reservation', 'id');
SELECT audit_attach('inventory_item', 'id');
SELECT audit_attach('test_result', 'id');
SELECT audit_attach('specification', 'id');
SELECT audit_attach('app_user', 'id', 'password');
"""

REVERSE = r"""
DROP TRIGGER IF EXISTS audit_sample_trg ON sample;
DROP TRIGGER IF EXISTS audit_sample_placement_trg ON sample_placement;
DROP TRIGGER IF EXISTS audit_storage_location_trg ON storage_location;
DROP TRIGGER IF EXISTS audit_item_lot_trg ON item_lot;
DROP TRIGGER IF EXISTS audit_stock_reservation_trg ON stock_reservation;
DROP TRIGGER IF EXISTS audit_inventory_item_trg ON inventory_item;
DROP TRIGGER IF EXISTS audit_test_result_trg ON test_result;
DROP TRIGGER IF EXISTS audit_specification_trg ON specification;
DROP TRIGGER IF EXISTS audit_app_user_trg ON app_user;
DROP TRIGGER IF EXISTS audit_event_immutable_trg ON audit_event;
DROP FUNCTION IF EXISTS audit_verify_chain(text);
DROP FUNCTION IF EXISTS audit_event_immutable();
DROP FUNCTION IF EXISTS audit_attach(text, text, text);
DROP FUNCTION IF EXISTS audit_row_change();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0001_initial"),
        ("samples", "0002_state_machine_trigger"),
        ("storage", "0003_ltree_path_triggers"),
        ("inventory", "0001_initial"),
        ("testing", "0001_initial"),
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql=AUDIT_FUNCTION + ATTACH_HELPER + IMMUTABILITY + VERIFY + ATTACHMENTS,
            reverse_sql=REVERSE,
        ),
    ]
