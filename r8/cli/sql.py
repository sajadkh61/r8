import json
import os
import secrets
import sqlite3
from pathlib import Path

import click

import r8
from r8 import util


@click.group("sql")
def cli():
    """Database commands."""
    pass


@cli.command()
@click.option("--origin", required=True,
              help="Origin for absolute URLs generated by r8. Use http://localhost:8000 for local development.")
@click.option("--static-dir", help="Folder to serve static files from.",
              type=click.Path(exists=True, dir_okay=True, file_okay=False, resolve_path=True),
              multiple=True, default=[str(Path(__file__).parent.parent / "static")])
@click.option("--host", help="Hostname r8 should listen on.", show_default=True, default='127.0.0.1')
@click.option("--port", help="Port r8 should listen on.", show_default=True, type=int, default=8000)
@click.option("--database", type=click.Path(dir_okay=False), envvar="R8_DATABASE", default="r8.db")
def init(origin, static_dir, host, port, database) -> None:
    """Initialize database."""
    if os.path.exists(database):
        raise click.UsageError("Database already exists.")
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
            uid TEXT
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
        CREATE TABLE settings (
            key TEXT PRIMARY KEY NOT NULL,
            value TEXT NOT NULL
        );
    """)
    conn.executemany(
        "INSERT INTO settings (key, value) VALUES (?,?)",
        [(k, json.dumps(v)) for k, v in [
            ("secret", secrets.token_hex(32)),
            ("static_dir", static_dir),
            ("origin", origin.rstrip("/")),
            ("host", host),
            ("port", port),
        ]]
    )
    conn.commit()
    r8.echo("r8", f"{database} initialized!")


@cli.command()
@util.with_database()
@util.backup_db
@util.database_rows
@click.argument("query", nargs=-1, required=True)
def stmt(rows, query):
    """Run a single SQL query on the database."""
    util.run_sql(" ".join(query), rows=rows)


@cli.command()
@util.with_database()
@util.backup_db
@click.argument(
    "input",
    type=click.File("r"),
)
@click.option('--debug', is_flag=True)
def file(input, debug):
    """
    Run a SQL file on the database.
    Foreign key constraints are deferred until transactions are commited, i.e. the database
    can be in an inconsistent state in between.
    """
    if debug:
        r8.db.set_trace_callback(print)

    with r8.db:
        try:
            r8.db.execute("PRAGMA defer_foreign_keys = ON")
            r8.db.executescript(input.read())
            r8.db.execute("PRAGMA defer_foreign_keys = OFF")
        except sqlite3.OperationalError as e:
            click.secho(str(e), fg="red")


@cli.command()
@util.with_database()
def tables():
    """Print overview of all tables."""
    table_names = [
        x[0] for x in
        r8.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    ]
    for table in table_names:
        click.secho(f"[{table}]", fg="green")
        util.run_sql(f"SELECT * FROM {table} LIMIT 1")
