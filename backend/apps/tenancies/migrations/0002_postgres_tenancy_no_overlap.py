from django.db import migrations


def add_postgres_exclusion_constraint(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    schema_editor.execute(
        """
        ALTER TABLE tenancies_tenancy
        ADD CONSTRAINT tenancy_unit_period_no_overlap
        EXCLUDE USING gist (
            unit_id WITH =,
            daterange(
                start_date,
                CASE WHEN status = 'ended' THEN COALESCE(actual_end_date, end_date) ELSE end_date END,
                '[)'
            ) WITH &&
        )
        WHERE (status IN ('invited', 'accepted_pending_start', 'active', 'scheduled_to_end', 'ended'))
        """
    )


def remove_postgres_exclusion_constraint(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("ALTER TABLE tenancies_tenancy DROP CONSTRAINT IF EXISTS tenancy_unit_period_no_overlap")


class Migration(migrations.Migration):
    dependencies = [("tenancies", "0001_initial")]

    operations = [
        migrations.RunPython(add_postgres_exclusion_constraint, remove_postgres_exclusion_constraint),
    ]
