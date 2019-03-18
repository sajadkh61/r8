import asyncio
import datetime
import functools
import html
import re
import secrets
import sqlite3
import textwrap
import traceback
from functools import wraps
from pathlib import Path
from typing import Optional, Tuple, TypeVar, List

import argon2
import click
import itsdangerous
import texttable
from aiohttp import web

import r8


def get_team(user: str) -> Optional[str]:
    """Get a given user's team."""
    with r8.db:
        row = r8.db.execute("""SELECT tid FROM teams WHERE uid = ?""", (user,)).fetchone()
        if row:
            return row[0]
        return None


@functools.lru_cache()
def get_teams() -> List[str]:
    """Get a list of all teams"""
    with r8.db:
        return [
            x[0] for x in
            r8.db.execute("SELECT DISTINCT tid FROM teams").fetchall()
        ]

def has_solved(user: str, challenge: str) -> bool:
    """Check if a user has solved a challenge."""
    with r8.db:
        return r8.db.execute("""
            SELECT COUNT(*)
            FROM challenges
            NATURAL JOIN flags
            INNER JOIN submissions ON (
                flags.fid = submissions.fid
                AND (
                    submissions.uid = ? OR
                    team = 1 AND submissions.uid IN (SELECT uid FROM teams WHERE tid = (SELECT tid FROM teams WHERE uid = ?))
                )
            )
            WHERE challenges.cid = ?
        """, (user, user, challenge)).fetchone()[0]


def media(src: Optional[str], desc: str, visible: bool = True):
    """
    HTML boilerplate for a bootstrap media element. Commonly used to display challenge icons.

    Args:
        src: Path to image.
        desc: Media body.
        visible: If `False`, a generic challenge icon will be shown instead.
    """
    return textwrap.dedent(f"""
        <div class="media">
            <img class="mr-3" style="max-width: 128px; max-height: 128px;" src="{src if src and visible else "/challenge.svg"}">
            <div class="align-self-center media-body">{desc}</div>
        </div>
        """)


def spoiler(help_text: str, button_text="🕵️ Show Hint") -> str:
    """
    HTML boilerplate for spoiler element in challenge descriptions.
    """
    div_id = secrets.token_hex(5)
    return f"""
            <div>
            <div id="{div_id}-help" class="d-none">
                <hr/>
                {help_text}
            </div>
            <div id="{div_id}-button" class="btn btn-outline-info btn-sm">{button_text}</div>
            <script>
            document.getElementById("{div_id}-button").addEventListener("click", function(){{
                document.getElementById("{div_id}-button").classList.add("d-none");
                document.getElementById("{div_id}-help").classList.remove("d-none");
            }});
            </script>
            </div>
            """


def challenge_form_js(cid: str) -> str:
    """
    JS Boilerplate for simple interactive form submissions in the challenge description.
    """
    return """
        <script>{ // make sure to add a block here so that `let` is scoped.
        let form = document.currentScript.previousElementSibling;
        let response = form.querySelector(".response")
        form.addEventListener("submit", (e) => {
            e.preventDefault();
            let post = {};
            (new FormData(form)).forEach(function(v,k){
                post[k] = v;
            });
            fetchApi(
                "/api/challenges/%s",
                {method: "POST", body: JSON.stringify(post)}
            ).then(json => {
                response.textContent = json['message'];
            }).catch(e => {
                response.textContent = "Error: " + e;
            })
        });
        }</script>
    """ % cid


def challenge_invoke_button(cid: str, button_text: str) -> str:
    """
    "Trigger" button for challenges. Clicking it invokes the challenge's HTTP POST handler.
    """
    return f"""
        <form class="form-inline">
            <button class="btn btn-primary m-1">{button_text}</button>
            <div class="response m-1"></div>
        </form>
        {challenge_form_js(cid)}
    """


def url_for(path: str, absolute: bool = False, user: Optional[str] = None) -> str:
    """
    Construct a URL for the CTF System.
    If absolute is true, construct an absolute URL including the origin.
    If user is passed, add an authentication token to the URL.
    """
    if user:
        token = r8.util.auth_sign.sign(user.encode()).decode()
        if "?" in path:
            path += f"&token={token}"
        else:
            path += f"?token={token}"
    if absolute:
        path = path.lstrip("/")
        path = f"{r8.settings['origin']}/{path}"
    return path


def get_host() -> str:
    """
    Return the hostname of the CTF system.
    """
    scheme, host, *_ = r8.settings['origin'].split(":")
    return host.lstrip("/")


def connection_timeout(f):
    """Decorator to timeout an asyncio TCP connection handler after 60 seconds."""

    @functools.wraps(f)
    async def wrapper(*args, **kwds):
        try:
            await asyncio.wait_for(f(*args, **kwds), 60)
        except asyncio.TimeoutError:
            writer = args[-1]
            writer.write("\nconnection timed out.\n".encode())
            await writer.drain()
            writer.close()

    return wrapper


def tolerate_connection_error(f):
    """Decorator to silently catch all ConnectionErrors for asyncio TCP connections."""

    @functools.wraps(f)
    async def wrapper(*args, **kwds):
        try:
            return await f(*args, **kwds)
        except ConnectionError:
            pass

    return wrapper


def format_address(address: Tuple[str, int]) -> str:
    """Format an `(ip, port)` address tuple."""
    host, port = address
    if not host:
        host = "0.0.0.0"
    return f"{host}:{port}"


_colors = [
    "black",
    "green",
    "yellow",
    "blue",
    "magenta",
    "cyan",
    "white"
]


def echo(namespace: str, message: str, err: bool = False) -> None:
    """
    Print to console with a namespace added in front.

    Args:
        namespace: The message 'category', e.g. the challenge name.
        message: The message.
        err: If `True`, print to stderr.

    For quick and dirty challenge development, it is completely okay to just `print()` instead.
    """
    if err:
        color = "red"
    else:
        color = _colors[hash(str) % len(_colors)]
    click.echo(click.style(f"[{namespace}] ", fg=color) + message, err=err)


THasIP = TypeVar("THasIP", str, tuple, asyncio.StreamWriter, asyncio.BaseTransport, web.Request)


def log(
    ip: THasIP,
    type: str,
    data: Optional[str] = None,
    *,
    cid: Optional[str] = None,
    uid: Optional[str] = None,
) -> int:
    """
    Create a log entry.

    Args:
        ip: IP address which caused this log entry to be created.
        type: Event type, for example "submission attempt"
        data: Additional event data, for example the actually submitted value.
        cid: Challenge this log entry relates to.
        uid: User this log entry relates to.
    """
    if isinstance(ip, web.Request):
        ip = ip.headers.get("X-Forwarded-For", ip.transport)
    if isinstance(ip, (asyncio.StreamWriter, asyncio.BaseTransport)):
        ip = ip.get_extra_info("peername")
    if isinstance(ip, tuple):
        ip = ip[0]
    with r8.db:
        return r8.db.execute(
            "INSERT INTO events (ip, type, data, cid, uid) VALUES (?, ?, ?, ?, ?)",
            (ip, type, data, cid, uid)
        ).lastrowid


def create_flag(
    challenge: str,
    max_submissions: int = 1,
    flag: str = None
) -> str:
    """
    Create a new flag for an existing challenge. When creating flags from challenges,
    see also :meth:`r8.Challenge.log_and_create_flag`.

    Args:
        challenge: Challenge for which the flag is valid.
        max_submissions: Maximum number of times the flag can be redeemed.
        flag: If given, use this as the flag string. Otherwise, generate random flag.
    """
    if flag is None:
        flag = "__flag__{" + secrets.token_hex(16) + "}"
    with r8.db:
        r8.db.execute(
            "INSERT OR REPLACE INTO flags (fid, cid, max_submissions) VALUES (?,?,?)",
            (flag, challenge, max_submissions)
        )
    return flag


class Signer:
    """Lazy-initialized signer. We need to do this because the secret is in the db."""

    def __init__(self, salt):
        self._signer = None
        self._salt = salt

    def _init(self):
        if not self._signer:
            # We could in theory fall back to a random value if we don't have settings,
            # but not having a DB connection would be unexpected and we want to fail.
            self._signer = itsdangerous.Signer(
                r8.settings["secret"],
                salt=self._salt
            )

    def sign(self, value):
        self._init()
        return self._signer.sign(value)

    def unsign(self, signed_value):
        self._init()
        return self._signer.unsign(signed_value)


auth_sign = Signer("auth")

database_rows = click.option(
    '--rows',
    type=int,
    default=100,
    help='Number of rows'
)


def with_database(echo=False):
    def decorator(f):
        @click.option(
            "--database",
            type=click.Path(exists=True),
            envvar="R8_DATABASE",
            default="r8.db",
            help="SQLite database to use. Also sourced from $R8_DATABASE.",
        )
        @wraps(f)
        def wrapper(database, **kwds):
            if echo:
                r8.echo("r8", f"Loading database ({database})...")
            r8.db = sqlite3_connect(database)
            with r8.db:
                r8.settings = {
                    k: v for (k, v) in
                    r8.db.execute("SELECT key, value FROM settings").fetchall()
                }
            return f(**kwds)

        return wrapper

    return decorator


def backup_db(f):
    @click.option("--backup/--no-backup", default=True, show_default=True,
                  help="Backup database to ~/.r8 before execution.")
    @wraps(f)
    def wrapper(backup, **kwds):
        if backup:
            backup_dir = Path.home() / ".r8"
            backup_dir.mkdir(exist_ok=True)
            time = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            with open(backup_dir / f"backup-{time}.sql", 'w') as out:
                for line in r8.db.iterdump():
                    out.write('%s\n' % line)
        return f(**kwds)

    return wrapper


def sqlite3_connect(filename):
    """
    Wrapper around sqlite3.connect that enables convenience features.
    """
    db = sqlite3.connect(filename)
    db.execute("PRAGMA foreign_keys = ON")
    return db


def run_sql(query: str, parameters=None, *, rows: int = 10) -> None:
    """
    Run SQL query against the database and pretty-print the result.
    """
    with r8.db:
        try:
            cursor = r8.db.execute(query, parameters or ())
        except Exception as e:
            return click.secho(str(e), fg="red")
        data = cursor.fetchmany(rows)
    table = texttable.Texttable(click.get_terminal_size()[0])
    if data:
        table.set_cols_align(["r" if isinstance(x, int) else "l" for x in data[0]])
    if cursor.description:
        table.set_deco(table.BORDER | table.HEADER | table.VLINES)
        header = [x[0] for x in cursor.description]
        table.add_rows([header] + data)
        print(table.draw())
    else:
        print("Statement did not return data.")


ph = argon2.PasswordHasher()


def hash_password(s: str) -> str:
    return ph.hash(s)


def verify_hash(hash: str, password: str) -> bool:
    return ph.verify(hash, password)


_control_char_trans = {
    x: x + 0x2400
    for x in range(32)
}
_control_char_trans[127] = 0x2421
_control_char_trans = str.maketrans(_control_char_trans)


def console_escape(text: str):
    return text.translate(_control_char_trans)


def correct_flag(flag: str) -> str:
    """
    Fixup slightly misformatted flag input.
    """
    filtered = flag.replace(" ", "").lower()
    match = re.search(r"[0-9a-f]{32}", filtered)
    if match:
        return "__flag__{" + match.group(0) + "}"
    return flag


def submit_flag(
    flag: str,
    user: str,
    ip: THasIP,
    force: bool = False
) -> str:
    """
    Returns:
        the challenge id
    Raises:
        ValueError, if there is an input error.
    """
    with r8.db:
        user_exists = r8.db.execute("""
          SELECT 1 FROM users
          WHERE uid = ?
        """, (user,)).fetchone()
        if not user_exists:
            r8.log(ip, "flag-err-unknown", flag)
            raise ValueError("Unknown user.")

        flag, cid = (r8.db.execute("""
          SELECT fid, cid FROM flags
          NATURAL INNER JOIN challenges
          WHERE fid = ? OR fid = ?
        """, (flag, correct_flag(flag))).fetchone() or [flag, None])
        if not cid:
            r8.log(ip, "flag-err-unknown", flag, uid=user)
            raise ValueError("Unknown Flag ¯\\_(ツ)_/¯")

        is_active = r8.db.execute("""
          SELECT 1 FROM challenges
          WHERE cid = ? 
          AND datetime('now') BETWEEN t_start AND t_stop
        """, (cid,)).fetchone()
        if not is_active and not force:
            r8.log(ip, "flag-err-inactive", flag, uid=user, cid=cid)
            raise ValueError("Challenge is not active.")

        is_already_submitted = r8.db.execute("""
          SELECT COUNT(*) FROM submissions 
          NATURAL INNER JOIN flags
          NATURAL INNER JOIN challenges
          WHERE cid = ? AND (
          uid = ? OR
          challenges.team = 1 AND submissions.uid IN (SELECT uid FROM teams WHERE tid = (SELECT tid FROM teams WHERE uid = ?))
          )
        """, (cid, user, user)).fetchone()[0]
        if is_already_submitted:
            r8.log(ip, "flag-err-solved", flag, uid=user, cid=cid)
            raise ValueError("Challenge already solved.")

        is_oversubscribed = r8.db.execute("""
          SELECT 1 FROM flags
          WHERE fid = ?
          AND (SELECT COUNT(*) FROM submissions WHERE flags.fid = submissions.fid) >= max_submissions
        """, (flag,)).fetchone()
        if is_oversubscribed and not force:
            r8.log(ip, "flag-err-used", flag, uid=user, cid=cid)
            raise ValueError("Flag already used too often.")

        r8.log(ip, "flag-submit", flag, uid=user, cid=cid)
        r8.db.execute("""
          INSERT INTO submissions (uid, fid) VALUES (?, ?)
        """, (user, flag))
    return cid


async def get_challenges(user: str):
    """Get challenges to display for a specific user"""
    with r8.db:
        cursor = r8.db.execute("""
            SELECT
                challenges.cid AS cid,
                CAST(strftime('%s',t_start) AS INTEGER) AS start,
                CAST(strftime('%s',t_stop) AS INTEGER) AS stop,
                IFNULL(solved,0) as solved,
                IFNULL(solves,0) as solves,
                team
            FROM challenges
            NATURAL LEFT JOIN (
                SELECT cid, MAX(CAST(strftime('%s',submissions.timestamp) AS INTEGER)) AS solved
                FROM submissions
                NATURAL JOIN flags
                NATURAL JOIN challenges
                WHERE (
                    uid = ? OR
                    team = 1 AND submissions.uid IN (SELECT uid FROM teams WHERE tid = (SELECT tid FROM teams WHERE uid = ?))
                )
                GROUP BY cid
            )
            NATURAL LEFT JOIN (
                SELECT cid, COUNT(*) AS solves
                FROM submissions
                NATURAL JOIN flags
                GROUP BY cid
            )
            WHERE t_start < datetime('now')  -- hide not yet active challenges
        """, (user, user))
        column_names = tuple(x[0] for x in cursor.description)
        results = [
            {
                key: value
                for key, value in zip(column_names, row)
            } for row in cursor.fetchall()
        ]
        results = [
            x for x in results
            if x["solved"] or await r8.challenges[x["cid"]].visible(user)
        ]
        for challenge in results:
            challenge["team"] = bool(challenge["team"])
            inst = r8.challenges[challenge["cid"]]
            try:
                challenge["title"] = str(inst.title)
            except Exception:
                challenge["title"] = "Title Error"
                challenge["tags"] = []
                challenge["description"] = f"<pre>{html.escape(traceback.format_exc())}</pre>"
                continue

            try:
                challenge["tags"] = [str(x) for x in inst.tags]
            except Exception:
                challenge["tags"] = []
                challenge["description"] = f"<pre>{html.escape(traceback.format_exc())}</pre>"
                continue

            try:
                challenge["description"] = await inst.description(user, bool(challenge["solved"]))
            except Exception:
                challenge["description"] = f"<pre>{html.escape(traceback.format_exc())}</pre>"
        return results
