"""
Maintain `storage_location.path` in the database.

The path is derived data, and derived data maintained by application code
drifts the moment anything writes through another door — a data migration,
a bulk import, a psql session. Deriving it in a trigger means the invariant
"path always equals the chain of ancestor codes" holds for every writer.

Re-parenting a subtree rewrites its descendants' paths. That is the cost of
choosing a materialised path over a closure table, and it is paid here,
once, in the one place that can guarantee it happens atomically with the
parent change.
"""

from django.db import migrations

SET_PATH = """
CREATE OR REPLACE FUNCTION storage_location_set_path() RETURNS trigger AS $$
DECLARE
    parent_path ltree;
BEGIN
    IF NEW.parent_id IS NULL THEN
        NEW.path := NEW.code::ltree;
    ELSE
        SELECT path INTO parent_path
        FROM storage_location WHERE id = NEW.parent_id;

        IF parent_path IS NULL THEN
            RAISE EXCEPTION 'parent location % has no materialised path', NEW.parent_id;
        END IF;

        -- Re-parenting into your own subtree would create a cycle that the
        -- FK cannot catch, and would make the path rewrite below infinite.
        IF TG_OP = 'UPDATE' AND parent_path <@ OLD.path THEN
            RAISE EXCEPTION
                'cannot move location % into its own subtree (% is inside %)',
                NEW.id, parent_path, OLD.path
                USING ERRCODE = 'check_violation';
        END IF;

        NEW.path := parent_path || NEW.code::ltree;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

CASCADE_PATH = """
CREATE OR REPLACE FUNCTION storage_location_cascade_path() RETURNS trigger AS $$
BEGIN
    IF NEW.path IS DISTINCT FROM OLD.path THEN
        -- Replace the old prefix with the new one on every descendant.
        -- This UPDATE touches only `path`, so it does not re-fire either
        -- trigger below (both are scoped to `OF parent_id, code`) and the
        -- rewrite terminates in one pass.
        UPDATE storage_location
        SET path = NEW.path || subpath(path, nlevel(OLD.path))
        WHERE path <@ OLD.path AND id <> NEW.id;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""

TRIGGERS = """
CREATE TRIGGER storage_location_set_path_trg
    BEFORE INSERT OR UPDATE OF parent_id, code ON storage_location
    FOR EACH ROW EXECUTE FUNCTION storage_location_set_path();

CREATE TRIGGER storage_location_cascade_path_trg
    AFTER UPDATE OF parent_id, code ON storage_location
    FOR EACH ROW EXECUTE FUNCTION storage_location_cascade_path();
"""

# GiST is the index type that answers both directions of the hierarchy
# question — ancestors (@>) and descendants (<@) — from one structure.
INDEX = """
CREATE INDEX idx_storage_location_path_gist
    ON storage_location USING GIST (path);
"""

REVERSE = """
DROP INDEX IF EXISTS idx_storage_location_path_gist;
DROP TRIGGER IF EXISTS storage_location_cascade_path_trg ON storage_location;
DROP TRIGGER IF EXISTS storage_location_set_path_trg ON storage_location;
DROP FUNCTION IF EXISTS storage_location_cascade_path();
DROP FUNCTION IF EXISTS storage_location_set_path();
"""


class Migration(migrations.Migration):
    dependencies = [("storage", "0002_alter_storagelocation_code_and_more")]

    operations = [
        migrations.RunSQL(sql=SET_PATH + CASCADE_PATH + TRIGGERS + INDEX, reverse_sql=REVERSE),
    ]
