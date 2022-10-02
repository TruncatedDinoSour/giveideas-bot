#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""give-ideas-bot"""

import asyncio
import datetime as dt
import json
import logging
import os
import platform
import pprint
import subprocess
import sys
import warnings
from threading import Thread
from multiprocessing import Process
from random import randint as semi_random_int
from time import time as time_timestamp
from traceback import format_exc as get_traceback_str
from typing import Any, Awaitable, Dict, List, Optional

import discord  # type: ignore
import psutil  # type: ignore
import sqlalchemy  # type: ignore
import sqlalchemy_utils  # type: ignore
from distro import name as get_distro_name
from flask import Flask
from sqlalchemy.ext.declarative import declarative_base  # type: ignore

CONFIG: Dict[str, Any] = {
    "prefix": "'",
    "note-prefix": '"',
    "hello-message": "Hello world",
    "bye-message": "Goodbye world",
    "cache-sz": 500,
    "logging": True,
    "sh-timeout": 10,
    "chunk-limit": 4,
    "playing": "",
}
CONFIG_PATH: str = "config.json"
GLOBAL_STATE: Dict[str, Any] = {"exit": 0}

CACHE: Dict[str, Any] = {
    "s2c": {},
    "c2s": {},
}
PFORMAT_CACHE: Dict[str, str] = {}

DB_ENGINE: sqlalchemy.engine.base.Engine = sqlalchemy.create_engine("sqlite:///bot.db")
DB_BASE: sqlalchemy.orm.decl_api.DeclarativeMeta = declarative_base()
DB_SESSION = sqlalchemy.orm.Session(DB_ENGINE)


class Note(DB_BASE):  # type: ignore
    __tablename__ = "notes"

    name: sqlalchemy.Column = sqlalchemy.Column(sqlalchemy.String, primary_key=True)
    content: sqlalchemy.Column = sqlalchemy.Column(sqlalchemy.String)

    def __init__(self, name: str, content: str):
        self.name = name
        self.content = content


def dump_config() -> None:
    log("Dumping config")

    with open(CONFIG_PATH, "w") as cfg:
        json.dump(CONFIG, cfg, indent=4)


def log(message: str) -> None:
    if not CONFIG["logging"]:
        return

    print(f" :: {message}")


def pformat_cached(string: Any) -> str:
    _string: str = f"{type(string)}.{string}"

    if len(PFORMAT_CACHE) > CONFIG["cache-sz"]:
        PFORMAT_CACHE.clear()
    elif _string in PFORMAT_CACHE:
        return PFORMAT_CACHE[_string]

    result: str = pprint.pformat(string)

    PFORMAT_CACHE[_string] = result
    return result


def command_to_str(command: List[List[str]]) -> str:
    _repr: str = repr(command)

    if len(CACHE["c2s"]) > CONFIG["cache-sz"]:
        CACHE["c2s"].clear()
    elif _repr in CACHE["c2s"]:
        return CACHE["c2s"][_repr]

    result: str = "".join(f"{' '.join(line)}\n" for line in command).strip()

    CACHE["c2s"][_repr] = result
    return result


def str_to_command(command: str) -> List[List[str]]:
    if len(CACHE["s2c"]) > CONFIG["cache-sz"]:
        CACHE["s2c"].clear()
    elif command in CACHE["s2c"]:
        return json.loads(CACHE["s2c"][command])

    result: List[List[str]] = [line.split(" ") for line in command.strip().split("\n")]

    CACHE["s2c"][command] = json.dumps(result)

    return result


def m(content: str, message: discord.Message) -> str:
    return f"<@{message.author.id}> {content}"


def uncode(string: str, codedel: str = "`") -> str:
    return string.replace(codedel, "\\".join(codedel))


def async_exit(msg: Optional[str] = None, code: int = 1) -> None:
    if msg is not None:
        log(f"\033[1m\033[31mEXIT: {msg}\033[0m")

    GLOBAL_STATE["exit"] = code

    with warnings.catch_warnings():
        warnings.simplefilter("error")

        try:
            asyncio.get_event_loop().stop()
        except DeprecationWarning:
            sys.exit()


def get_nth_word(command: List[List[str]], n: int = 0) -> Optional[str]:
    found: int = 0

    for widx, word in enumerate(command):
        if command and all(command):
            found += 1

        if (found - 1) == n:
            result: str = word[0]
            del command[widx][0]
            return result

    return None


class BotCommandsParser:
    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot

    async def _send_message(self, message: str) -> None:
        for chunk_idx, chunk in enumerate(
            message[i : i + 2000] for i in range(0, len(message), 2000)
        ):
            if chunk_idx > CONFIG["chunk-limit"]:
                log("Too many chunks being sent, stopping")
                await self.bot.cchannel.send(
                    f"***Message chunk count exceeded ({CONFIG['chunk-limit']}), message too long!***"
                )
                return

            await self.bot.cchannel.send(f"{chunk}\n")

    def _note_exists(self, note_name: str) -> bool:
        return DB_SESSION.query(Note).filter_by(name=note_name).first() is not None

    def _get_help(self, what: str) -> Optional[str]:
        if (handler := getattr(self, f"cmd_{what}", None)) is None:
            return None

        if handler.__doc__ is None:
            return None

        help_text: str = "\n".join(
            line.strip() for line in uncode(handler.__doc__).split("\n")
        )
        return f"```\n{help_text}\n```"

    async def _send_help(self, what: str, message: discord.Message):
        text: str = f"No such command/help: {what!r}"

        if (_help := self._get_help(what)) is not None:
            text = _help

        await self._send_message(m(f"\n{text}", message))

    async def cmd_say(
        self,
        message: discord.Message,
        command: List[List[str]],
    ) -> None:
        """Repeat a specified string
        Usage: say <content...>"""

        say_content: str = command_to_str(command)

        if not say_content:
            await self._send_help("say", message)
            return

        await self._send_message(say_content)

    async def cmd_set(
        self,
        message: discord.Message,
        command: List[List[str]],
    ) -> None:
        """Set a note to a value (save it)
        Usage: set <name> <content...>"""

        if (note_name := get_nth_word(command)) is None:
            await self._send_help("set", message)
            return

        try:
            DB_SESSION.add(Note(name=note_name, content=command_to_str(command)))
            DB_SESSION.commit()
        except sqlalchemy.exc.IntegrityError:
            DB_SESSION.rollback()
            await self._send_message(m(f"Note {note_name!r} already exists", message))
            return

        await self._send_message(m(f"Note {note_name!r} saved", message))

    async def cmd_del(
        self,
        message: discord.Message,
        command: List[List[str]],
    ) -> None:
        """Delete a note
        Usage: del <name>"""

        if (note_name := get_nth_word(command)) is None:
            await self._send_help("del", message)
            return

        if not self._note_exists(note_name):
            await self._send_message(m(f"Note {note_name!r} doesn't exist", message))
            return

        DB_SESSION.execute(sqlalchemy.delete(Note).where(Note.name == note_name))
        DB_SESSION.commit()

        await self._send_message(m(f"Note {note_name!r} deleted", message))

    async def cmd_cya(
        self,
        message: discord.Message,
        command: List[List[str]],
    ) -> None:
        """Make the bot end itself
        Usage: cya"""

        await self._send_message(CONFIG["bye-message"])
        async_exit("Exiting bot because of the cya command", 0)

    async def cmd_list(
        self,
        message: discord.Message,
        command: List[List[str]],
    ) -> None:
        """List all notes
        Usage: list"""

        notes_list: str = (
            "\n".join(
                f"**-** {''.join(ent)}" for ent in DB_SESSION.query(Note.name).all()
            )
            or "*No notes found*"
        )

        await self._send_message(
            f"""Notes:

{notes_list}

To invoke/expand a note, type '{CONFIG['note-prefix']}<note name>',
for example: {CONFIG['note-prefix']}Hello"""
        )

    async def cmd_help(
        self,
        message: discord.Message,
        command: List[List[str]],
    ) -> None:
        """Get help about a command
        Usage: help [command]"""

        if (cmd_name := get_nth_word(command)) is not None:
            await self._send_help(cmd_name, message)
            return

        _help: str = "\n"
        _nl: str = "\n"

        for attr in dir(self):
            if not attr.startswith("cmd"):
                continue

            _help += f"`{uncode(attr[4:])}` -- {uncode(getattr(self, attr).__doc__.split(_nl)[0] or 'No help available')}\n"

        await self._send_message(m(_help, message))

    async def cmd_sql(
        self,
        message: discord.Message,
        command: List[List[str]],
    ) -> None:
        """Run raw sql queries on the bot's database
        Usage: sql <sql query>"""

        sql_query: str = command_to_str(command)

        if not sql_query:
            await self._send_help("sql", message)
            return

        text: str

        try:
            text = f"""
Executed query `{uncode(sql_query)}`

```py
# Query results

{pformat_cached(DB_SESSION.execute(f'{sql_query}').all())}
```
"""
            DB_SESSION.commit()
        except Exception as err:
            text = f"Executing query failed: {err!r}"

        await self._send_message(m(text, message))

    async def cmd_config(
        self,
        message: discord.Message,
        command: List[List[str]],
    ) -> None:
        """Get bot's config
        Usage: config"""

        await self._send_message(
            m(f"\n```json\n{uncode(json.dumps(CONFIG, indent=4))}\n```", message)
        )

    async def cmd_sh(
        self,
        message: discord.Message,
        command: List[List[str]],
    ) -> None:
        """Run a **blocking** shell command
        Usage: sh <command...>"""

        sh_command: str = command_to_str(command)

        if not sh_command.strip():
            await self._send_message(m("No command specified", message))
            return

        output: str
        try:
            output = subprocess.check_output(
                sh_command,
                shell=True,
                timeout=CONFIG["sh-timeout"],
                stderr=subprocess.STDOUT,
            ).decode()
        except FileNotFoundError:
            output = "sh: Command not found"
        except subprocess.CalledProcessError as err:
            err.output = err.output or b"No output after a non-zero exit code"
            output = f"Exit code: {err.returncode}\n\n{err.output.decode()}"
        except subprocess.TimeoutExpired as err:
            err.output = err.output or b"No output after a command timeout"
            output = f"Command timedout\n\n{err.output.decode()}"

        await self._send_message(
            m(
                f"""
Command: `{uncode(sh_command)}`

Output:
```
{uncode(output)}
```
""",
                message,
            )
        )

    async def cmd_botfetch(
        self,
        message: discord.Message,
        command: List[List[str]],
    ) -> None:
        """Fetch server info
        Usage: botfetch"""

        ram = psutil.virtual_memory()
        uname = os.uname()
        cpu_usage: str = f"{psutil.cpu_percent()}%"
        header: str = f"{psutil.Process().username()}@{uname.nodename}"
        cpu_list: str = ""

        for core, usage in enumerate(psutil.cpu_percent(percpu=True, interval=1), 1):
            cpu_list += f"{' ' * 25}- Core{core}: {usage}%\n"

        fetch: str = f"""
     <----->         {header}
    <  (0)  >        {'-' * len(header)}
    |       |        OS: {get_distro_name()}
   < ------- >           - Type: {sys.platform}
   o         o           - Release: {platform.release()}
   o  0  ()  o               - Version: {platform.version()}
  o           o      Kernel: {uname.sysname} {uname.release}
o o o o o o o o o        - Architecture: {uname.machine}
o o o o o o o o o    Shell: {os.environ.get("SHELL") or '/bin/sh'}
o o o o o o o o o    CPU: {platform.processor()} [{cpu_usage}]
{cpu_list[:-1]}
                     Memory: {ram.used >> 20} / {ram.total >> 20} MB ({ram.percent}%)
                     Uptime: {dt.timedelta(seconds=time_timestamp() - psutil.boot_time())}
"""

        await self._send_message(
            m(
                f"```yml\n{uncode(fetch)}\n```",
                message,
            )
        )

    async def cmd_sayd(
        self,
        message: discord.Message,
        command: List[List[str]],
    ) -> None:
        """Repeat a specified string and then delete the original message
        Usage: sayd <content...>"""

        if not (say := command_to_str(command)):
            await self._send_help("sayd", message)
            return

        await message.delete()
        await self._send_message(say)

    async def cmd_dumpcache(
        self,
        message: discord.Message,
        command: List[List[str]],
    ) -> None:
        """Dump in-memory message cache
        Usage: dumpcache"""

        await self._send_message(f"```py\n{pformat_cached(CACHE)}\n```")

    async def cmd_dumpcachep(
        self,
        message: discord.Message,
        command: List[List[str]],
    ) -> None:
        """Dump in-memory pformat cache
        Usage: dumpcachep"""

        await self._send_message(f"```py\n{pformat_cached(PFORMAT_CACHE)}\n```")

    async def cmd_status(
        self,
        message: discord.Message,
        command: List[List[str]],
    ) -> None:
        """Change activity status
        Usage: status <status...>"""

        if not (status := command_to_str(command)):
            await self._send_help("status", message)
            return

        CONFIG["playing"] = status
        Thread(target=dump_config).start()

        await self.bot._change_status()
        await self._send_message(f"Changed status to `{uncode(status)}`")

    async def cmd_warm(
        self,
        message: discord.Message,
        command: List[List[str]],
    ) -> None:
        """Warm a user
        Usage: warm <user>"""

        if not (warm := command_to_str(command)):
            await self._send_help("warm", message)
            return

        await self._send_message(f":tea::coffee::heart_on_fire: {warm} :heart_on_fire::coffee::tea:")

    async def cmd_banana(
        self,
        message: discord.Message,
        command: List[List[str]],
    ) -> None:
        """Banana a user
        Usage: banana <user>"""

        if not (banana := command_to_str(command)):
            await self._send_help("banana", message)
            return

        await self._send_message(f":banana: {banana} :banana:")


class Bot(discord.Client):
    """The bot"""

    def __init__(self) -> None:
        log("Setting bot up")

        intents: discord.Intents = discord.Intents.default()
        intents.members = True

        self.cchannel: Optional[discord.TextChannel] = None
        self.parser = BotCommandsParser(self)

        log("Making the bot")
        super().__init__(intents=intents)

    async def _change_status(self) -> None:
        log("Changing activity status")

        await self.change_presence(
            activity=discord.Game(name=CONFIG["playing"])
        )

    def bot(self, token: Optional[str]) -> None:
        log("Beginning to do checks and run bot")

        if not token:
            async_exit("Token must not be empty, null or false")

        self.run(token)

    async def on_ready(self) -> None:
        for guild in self.guilds:
            for channel in guild.text_channels[::-1]:
                if (
                    channel.permissions_for(guild.me).send_messages
                    and str(channel.type) == "text"
                ):
                    self.cchannel = channel

        if self.cchannel is None:
            async_exit("No text channels I have access to")
            return

        log(f"Bot loaded, I am {self.user}")
        await self.parser._send_message(CONFIG["hello-message"])

        await self._change_status()

    async def on_message(self, message) -> None:
        if message.author.bot or not any(
            message.content.startswith(p)
            for p in (CONFIG["prefix"], CONFIG["note-prefix"])
        ):
            return

        self.cchannel = message.channel

        command_str: str = message.content.removeprefix(CONFIG["prefix"])
        command: List[List[str]] = str_to_command(command_str)

        if command_str.startswith(CONFIG["note-prefix"]):
            note_name: str = command[0][0].removeprefix(CONFIG["note-prefix"])

            if (
                note := DB_SESSION.query(Note.content).filter_by(name=note_name).first()
            ) is not None:
                await self.parser._send_message(
                    "\n".join(f"> {line}" for line in "".join(note).split("\n"))
                )
                return

            if not note_name.strip():
                await self.parser.cmd_list(message, command)
                return

            await self.parser._send_message(m(f"Note {note_name!r} not found", message))
            return

        try:
            _bot_roles: discord.Member = self.cchannel.guild.get_member(
                self.user.id
            ).roles
        except AttributeError as e:
            log(f"Error getting bot roles: {e}")
            return

        if not any(
            role in message.author.roles and not role.name[0] == "@"
            for role in _bot_roles
        ):
            await self.parser._send_message(
                m("You have no permission to use this bot", message)
            )
            return

        log(f"{message.author} executed {command!r} ({command_str!r})")

        if not command:
            await self.parser._send_message(
                m("No command or note name specified", message)
            )
            return

        if (cmd := get_nth_word(command)) is not None:
            handler: Optional[Awaitable] = getattr(self.parser, f"cmd_{cmd}", None)

            if handler is None:
                await self.parser._send_message(
                    m(
                        f"Unknown command: {cmd!r}",
                        message,
                    )
                )
                return

            try:
                await handler(message, command)  # type: ignore
            except Exception:
                tb: str = get_traceback_str()

                print(tb)

                await self.parser._send_message(
                    m(
                        f"""
Oops! Ran into an error:

```py
{tb}
```
""",
                        message,
                    )
                )

            return

        formatted_command: str = pformat_cached(command)

        log(f"Invalid tokens: {formatted_command}")

        _content: str = uncode(
            f"{command_str!r}\n\n# Turned into:\n\n{formatted_command}", "```"
        )

        await self.parser._send_message(
            m(
                f"""Your command expression did not return any valid tokens
```py
{_content}
```
""",
                message,
            )
        )


def main() -> int:
    """Entry/main function"""

    if not os.path.exists(CONFIG_PATH):
        log(f"Making new config: {CONFIG_PATH!r}")
        dump_config()

        log("Please configure the bot to run it")
        return 0

    print(" || Loading config... ", end="")
    with open(CONFIG_PATH, "r") as cfg:
        CONFIG.update(json.load(cfg))
    print("done")

    # Ping server
    log("Setting up the ping server")
    _ping_app: Flask = Flask("")

    logging.basicConfig(level=logging.CRITICAL)
    logging.getLogger("werkzeug").disabled = True
    _ping_app.logger.disabled = True

    @_ping_app.route("/")
    def _ping():
        return ""

    def _ping_app_run():
        try:
            _port: int = semi_random_int(2000, 9000)
            log(f"Running ping server at port {_port!r}")
            _ping_app.run(host="0.0.0.0", port=_port)
        except KeyboardInterrupt:
            pass

    log("Running the ping server")
    ping_p: Process = Process(target=_ping_app_run)
    ping_p.start()
    # ---

    if not sqlalchemy_utils.database_exists(DB_ENGINE.url):
        log(f"Creating database: {DB_ENGINE.url!r}")
        DB_BASE.metadata.create_all(DB_ENGINE)

    try:
        Bot().bot(os.environ.get("GI_TOKEN"))
    except Exception:
        print(get_traceback_str())
    finally:
        log("Terminating ping server")
        ping_p.terminate()

        dump_config()

        log(f"Exiting with code {GLOBAL_STATE['exit']!r}")
        return GLOBAL_STATE["exit"]


if __name__ == "__main__":
    assert main.__annotations__.get("return") is int, "main() should return an integer"
    sys.exit(main())
