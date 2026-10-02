from django.db import migrations


def install_postgis(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql': return
    schema_editor.execute('CREATE EXTENSION IF NOT EXISTS postgis')
    schema_editor.execute('''ALTER TABLE market_stalllocation ADD COLUMN coordinates geography(Point,4326)
        GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography) STORED''')
    schema_editor.execute('CREATE INDEX market_stalllocation_coordinates_gist ON market_stalllocation USING GIST (coordinates)')


def uninstall_postgis(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql': return
    schema_editor.execute('ALTER TABLE market_stalllocation DROP COLUMN coordinates')


class Migration(migrations.Migration):
    dependencies = [('market', '0001_initial')]
    operations = [migrations.RunPython(install_postgis, uninstall_postgis)]
