"""One-column compatibility migration; never creates bank identities or grants."""
import re

from sqlalchemy import inspect,text


def migrate_family_profile_members(engine, *, backup=None):
    table='extension_family_members'
    inspector=inspect(engine)
    if table not in inspector.get_table_names():return False
    column=next(row for row in inspector.get_columns(table) if row['name']=='helper_user_id')
    if column['nullable']:return False
    if engine.dialect.name=='postgresql':
        with engine.begin() as connection:
            connection.execute(text('ALTER TABLE extension_family_members ALTER COLUMN helper_user_id DROP NOT NULL'))
        return True
    if engine.dialect.name!='sqlite':raise RuntimeError('Family profile migration requires reviewed dialect')
    if backup and engine.url.database not in (None,':memory:'):backup(engine.url.database,version=23)
    raw=engine.raw_connection();connection=raw.driver_connection
    foreign_keys=connection.execute('PRAGMA foreign_keys').fetchone()[0]
    try:
        connection.execute('PRAGMA foreign_keys=OFF')
        connection.execute('BEGIN IMMEDIATE')
        ddl=connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()[0]
        temporary='extension_family_members_v23_migration'
        if connection.execute('SELECT 1 FROM sqlite_master WHERE name=?',(temporary,)).fetchone():
            raise RuntimeError('Unexpected family-profile migration table')
        updated,count=re.subn(r'(?i)(\bhelper_user_id\s+VARCHAR\(36\))\s+NOT NULL',r'\1',ddl)
        if count!=1:raise RuntimeError('Unreviewed family member column definition')
        updated,count=re.subn(r'(?i)^CREATE TABLE\s+(?:"extension_family_members"|extension_family_members)',
            'CREATE TABLE '+temporary,updated,count=1)
        if count!=1:raise RuntimeError('Unreviewed family member table definition')
        before=connection.execute('SELECT * FROM extension_family_members ORDER BY id').fetchall()
        old_fk=connection.execute('PRAGMA foreign_key_check').fetchall()
        indexes=connection.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",(table,)).fetchall()
        connection.execute(updated)
        connection.execute('INSERT INTO extension_family_members_v23_migration SELECT * FROM extension_family_members')
        connection.execute('DROP TABLE extension_family_members')
        connection.execute('ALTER TABLE extension_family_members_v23_migration RENAME TO extension_family_members')
        for (statement,) in indexes:connection.execute(statement)
        if before!=connection.execute('SELECT * FROM extension_family_members ORDER BY id').fetchall():
            raise RuntimeError('Family member rows changed during compatibility migration')
        if sorted(old_fk)!=sorted(connection.execute('PRAGMA foreign_key_check').fetchall()):
            raise RuntimeError('Family profile migration changed foreign-key integrity')
        connection.commit()
        return True
    except BaseException:
        connection.rollback();raise
    finally:
        connection.execute('PRAGMA foreign_keys='+str(int(bool(foreign_keys))))
        raw.close()
