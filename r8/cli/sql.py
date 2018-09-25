import os
import sqlite3

import click

import r8
from r8 import util


@click.group("sql")
def cli():
    """Database commands."""
    pass


@cli.command("init")
@click.argument("database", type=click.Path(), envvar="R8_DATABASE", default="r8.db")
def sql_init(database) -> None:
    """Initialize database."""
    if os.path.exists(database):
        return r8.echo("r8", "Database already exists.", err=True)
    conn = util.sqlite3_connect(database)
    conn.executescript("""
        CREATE TABLE users (
            uid TEXT PRIMARY KEY NOT NULL,
            password TEXT NOT NULL
        );
        CREATE TABLE challenges (
            cid TEXT PRIMARY KEY NOT NULL,
            team BOOLEAN NOT NULL DEFAULT 0,
            t_start DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            t_stop DATETIME NOT NULL
        );
        CREATE TABLE flags (
            fid TEXT PRIMARY KEY NOT NULL,
            cid TEXT NOT NULL,
            max_submissions INTEGER NOT NULL,
            FOREIGN KEY (cid) REFERENCES challenges(cid)
        );
        CREATE TABLE submissions (
            uid TEXT NOT NULL,
            fid TEXT NOT NULL,
            timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (uid) REFERENCES users(uid),
            FOREIGN KEY (fid) REFERENCES flags(fid),
            PRIMARY KEY (uid, fid)
        );
        CREATE TABLE events (
            time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ip TEXT NOT NULL,
            type TEXT NOT NULL,
            data TEXT,
            cid TEXT,
            uid TEXT,
            FOREIGN KEY (cid) REFERENCES challenges(cid),
            FOREIGN KEY (uid) REFERENCES users(uid)
        );
        CREATE TABLE teams (
            uid TEXT PRIMARY KEY NOT NULL,
            tid TEXT NOT NULL,
            FOREIGN KEY (uid) REFERENCES users(uid)
        );
        CREATE TABLE data (
          cid TEXT NOT NULL,
          key TEXT NOT NULL,
          value TEXT NOT NULL,
          FOREIGN KEY (cid) REFERENCES challenges(cid),
          PRIMARY KEY (cid, key)
        );
    """)
    r8.echo("r8", f"{database} initialized!")


@cli.command("stmt")
@util.with_database
@util.backup_db
@util.database_rows
@click.argument("query", nargs=-1, required=True)
def sql_stmt(rows, query):
    """
    Run a single SQL query on the database.
    There are no safeguards in place here:
    If you drop the table, the table is dropped.
    """
    util.run_sql(" ".join(query), rows=rows)


@cli.command("file")
@util.with_database
@util.backup_db
@click.argument(
    "input",
    type=click.File("r"),
)
def sql_file(input):
    """
    Run a SQL file on the database.
    There are no safeguards in place here: If you drop the table, the table is dropped.
    Foreign key constrains are deferred until transactions are commited, i.e. the database
    can be in an inconsistent state in between.
    """
    r8.db.set_trace_callback(print)

    with r8.db:
        try:
            r8.db.execute("PRAGMA defer_foreign_keys = ON")
            r8.db.executescript(input.read())
            r8.db.execute("PRAGMA defer_foreign_keys = OFF")
        except sqlite3.OperationalError as e:
            click.secho(str(e), fg="red")


@cli.command("tables")
@util.with_database
def tables():
    """Print overview of all tables."""
    table_names = [
        x[0] for x in
        r8.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    ]
    for table in table_names:
        click.secho(f"[{table}]", fg="green")
        util.run_sql(f"SELECT * FROM {table} LIMIT 1")