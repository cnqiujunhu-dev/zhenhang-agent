"""Nested atomicity for composable domain writes; never commits caller work."""
from functools import wraps


def atomic_domain(function):
    @wraps(function)
    def wrapped(session, *args, **kwargs):
        connection = session.connection()
        # pysqlite otherwise may release its first SAVEPOINT as a physical
        # commit even though SQLAlchemy believes an outer transaction exists.
        if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")
        with session.begin_nested():
            return function(session, *args, **kwargs)
    return wrapped
