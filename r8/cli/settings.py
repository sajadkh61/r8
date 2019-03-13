import os
import secrets
import sqlite3
from pathlib import Path

import click

import r8
from r8 import util


@click.group("settings")
def cli():
    """View and modify settings."""
    pass


@cli.command()
@util.with_database()
def view():
    """Print all settings"""
    util.run_sql(f"SELECT * FROM settings")


@cli.command()
@click.argument("key")
@click.argument("value")
@util.with_database()
def set(key, value):
    """Update a setting"""
    with r8.db:
        r8.db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    util.run_sql(f"SELECT * FROM settings")


@cli.command()
@click.argument("key")
@util.with_database()
def delete(key):
    """Update a setting"""
    with r8.db:
        r8.db.execute("DELETE FROM settings WHERE key = ?", (key,))
    util.run_sql(f"SELECT * FROM settings")

