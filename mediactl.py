"""
  ███╗   ███╗███████╗██████╗ ██╗ █████╗  ██████╗████████╗██╗
  ████╗ ████║██╔════╝██╔══██╗██║██╔══██╗██╔════╝╚══██╔══╝██║
  ██╔████╔██║█████╗  ██║  ██║██║███████║██║        ██║   ██║
  ██║╚██╔╝██║██╔══╝  ██║  ██║██║██╔══██║██║        ██║   ██║
  ██║ ╚═╝ ██║███████╗██████╔╝██║██║  ██║╚██████╗   ██║   ███████╗
  ╚═╝     ╚═╝╚══════╝╚═════╝ ╚═╝╚═╝  ╚═╝ ╚═════╝   ╚═╝   ╚══════╝

  ──────────────────────────────────────────────────────────────
                  Media center and home control
  ──────────────────────────────────────────────────────────────

Commands:

MEDIA
  library          Local libraries, media collections, and sync
  media            Media search and metadata
  torrent          Torrent client, downloads and status
  indexer          Torrent search providers
  player           Media players

HOME
  device           Device discovery and control
  camera           Camera snapshots, streaming, and motion detection
  audio            Microphones, speech-text, noise detection, wake words
  tv               TV, apps, and remote keys
  lights           Smart light control
  ir               IR remote control
  network          Network presence, inspection, and known devices
  weather          Current conditions and forecasts

SECURITY
  security         Security control, incident management, signals
  detect           Face recognition, object detection, and quality checks

SYSTEM
  run              Run Mediactl, discovery and scheduler
  web              Mediactl web interface
  users            User accounts, access tags, and pairing
  llm              Model prompts, chat, and tool-assisted requests
  notify           Notifications and remote command listener
"""

import argparse
import json
import shlex
import sys
from pathlib import Path

import os
import time
import threading
import importlib
from typing import Callable
from logui import logui

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


HERE = Path(__file__).parent
_OUT = HERE / ".out"
_REFRESH_TICK = 10
_REFRESH_HOOKS: dict[str, tuple[Callable, int | Callable, float]] = {}
_REFRESH_SEM = threading.Semaphore(4)
_REFRESH_STOP = threading.Event()
_REFRESH_RUNNING: set[str] = set()
_REFRESH_RUNNING_LOCK = threading.Lock()
_scheduler = None
_bootstrapped = False

_MODULES = (
    "media", "library", "indexer", "torrent", "player", "tv", "camera",
    "security", "network", "audio", "llm", "lights", "weather", "ir",
    "notify", "detectors", "device",
)

_PREPARE_HOOKS = (
    ("torrent", "_refresh_active", "torrent", 10),
    ("player", "_refresh_player_status", "player", 30),
    ("audio", "refresh", "audio", 0),
    ("lights", "status", "lights", 30),
    ("weather", "refresh", "weather", 1800),
    ("llm", "refresh", "llm", 0),
    ("detectors", "refresh", "detectors", 0),
    ("device", "_refresh_network", "network", 30),
    ("library", "_library_refresh", "library", lambda modules: int(modules["library"]._load_cfg()["refresh_interval"])),
    ("library", "sync_collections", "media", lambda modules: int(modules["library"]._load_cfg()["lists_refresh_interval"])),
    ("library", "sync_recommendations", "media_recomm", lambda modules: modules["media"].recommendations_interval() if "media" in modules else 0),
    ("library", "_library_sync_meta", "library_sync_meta", lambda modules: int(modules["library"]._load_cfg()["meta_refresh_interval"])),
)


def _ensure_journal():
    _OUT.mkdir(parents=True, exist_ok=True)
    (_OUT / "events.log").touch(exist_ok=True)


def load_module(name: str):
    _ensure_journal()
    return importlib.import_module(name)


def prepare(required: bool = True):
    _ensure_journal()
    modules = {}
    for name in _MODULES:
        try:
            modules[name] = importlib.import_module(name)
        except ImportError:
            if required:
                raise
    if "library" in modules:
        modules["library"]._reset_metadata_missing()
    for name, function, hook, interval in _PREPARE_HOOKS:
        if name not in modules:
            continue
        fn = getattr(modules[name], function)
        if callable(interval):
            interval = lambda get_interval=interval: get_interval(modules)
        register_refresh_hook(fn, module=hook, interval=interval)


def bootstrap():
    global _bootstrapped
    if _bootstrapped:
        return
    prepare(required=False)
    _bootstrapped = True


def run():
    bootstrap()
    scheduler_start()
    cli_module("notify").start()


def register_refresh_hook(fn, module=None, interval=0):
    """Register a hook; interval is seconds or a callable returning seconds (0 disables)."""
    name = module or fn.__module__.split(".")[-1]
    _REFRESH_HOOKS.setdefault(name, (fn, interval, time.monotonic()))


def refresh_one(module: str):
    """Call a single registered refresh hook by module name (blocks on semaphore).
    Skips if a previous invocation for the same module is still running."""
    row = _REFRESH_HOOKS.get(module)
    if not row:
        raise ValueError(f"no refresh hook for: {module}")
    with _REFRESH_RUNNING_LOCK:
        if module in _REFRESH_RUNNING:
            return
        _REFRESH_RUNNING.add(module)
    fn, _, _ = row
    with _REFRESH_SEM:
        try:
            return fn()
        except Exception as e:
            print(f"  refresh.{module}: {e}")
            try: logui(f"refresh.{module}", msg=str(e))
            except Exception: pass
        finally:
            with _REFRESH_RUNNING_LOCK:
                _REFRESH_RUNNING.discard(module)


def refresh_all():
    logui("mediactl.refresh", event="start", hooks=list(_REFRESH_HOOKS))
    for name in list(_REFRESH_HOOKS):
        threading.Thread(target=refresh_one, args=(name,), daemon=True).start()


def scheduler_start():
    global _scheduler
    if _scheduler and _scheduler.is_alive():
        return
    now = time.monotonic()
    for name, (fn, interval, _) in list(_REFRESH_HOOKS.items()):
        _REFRESH_HOOKS[name] = (fn, interval, now)
    _REFRESH_STOP.clear()
    _scheduler = threading.Thread(target=_refresh_scheduler, daemon=True)
    _scheduler.start()


def scheduler_stop():
    """Signal the periodic scheduler to stop."""
    if not _scheduler or not _scheduler.is_alive():
        return
    logui("mediactl.scheduler", event="stop")
    _REFRESH_STOP.set()
    _scheduler.join()


def _refresh_scheduler():
    logui("mediactl.scheduler", event="running")
    while not _REFRESH_STOP.wait(_REFRESH_TICK):
        try:
            now = time.monotonic()
            for name, (fn, interval, last) in list(_REFRESH_HOOKS.items()):
                seconds = interval() if callable(interval) else interval
                if seconds and now - last >= seconds:
                    _REFRESH_HOOKS[name] = (fn, interval, now)
                    if name in _REFRESH_RUNNING:
                        continue
                    threading.Thread(target=refresh_one, args=(name,), daemon=True).start()
        except Exception as e:
            logui("mediactl.scheduler", event="error", error=repr(e))


CLI_MODULES = {
    "media": "media",
    "library": "library",
    "indexer": "indexer",
    "torrent": "torrent",
    "player": "player",
    "tv": "tv",
    "camera": "camera",
    "detect": "detectors",
    "lights": "lights",
    "ir": "ir",
    "network": "network",
    "weather": "weather",
    "notify": "notify",
    "security": "security",
    "audio": "audio",
    "llm": "llm",
    "web": "web",  # Keep the optional web server dependencies lazy.
    "users": "users",
    "device": "device",
}


def cli_module(name):
    """Resolve a registered module, importing lazy entries on first use."""
    module = CLI_MODULES[name]
    if isinstance(module, str):
        module = CLI_MODULES[name] = load_module(module)
    return module


class ModuleParser(argparse.ArgumentParser):
    """Forward registered modules' arguments unchanged to their own CLI parser."""

    def parse_args(self, args=None, namespace=None):
        argv = sys.argv[1:] if args is None else list(args)
        if argv in (["-h"], ["--help"]):
            argv = ["help"]
        if argv and argv[0] in CLI_MODULES:
            namespace = namespace if namespace is not None else argparse.Namespace()
            namespace.cmd, namespace.args = argv[0], argv[1:]
            return namespace
        return super().parse_args(argv, namespace)

    def format_help(self):
        return __doc__.strip("\n") + "\n"


def make_parser():
    parser = ModuleParser(prog="mediactl", add_help=False, exit_on_error=False)
    parser.add_argument("--schema", action="store_true")
    sub = parser.add_subparsers(dest="cmd")
    for name in CLI_MODULES:
        sub.add_parser(name, add_help=False)
    p = sub.add_parser("help", add_help=False)
    p.add_argument("module", nargs="?", choices=[*CLI_MODULES, "run"])
    p = sub.add_parser("run", add_help=False)
    p.add_argument("action", nargs="?", choices=("help",))
    p.add_argument("-h", "--help", action="store_true", dest="run_help")
    sub.add_parser("exit", add_help=False)
    sub.add_parser("quit", add_help=False)
    return parser


def schema():
    """Print module-owned CLI metadata for the web UI and LLM tools."""
    commands = {name: cli_module(name).CLI_SCHEMA for name in CLI_MODULES}
    print(json.dumps(commands, indent=2))


def dispatch(args):
    if getattr(args, "schema", False):
        return schema()
    if args.cmd == "run":
        if args.run_help or args.action == "help":
            print("mediactl run — Run Mediactl, discovery and scheduler; Ctrl-C stops it")
            return
        return run()
    if args.cmd in CLI_MODULES:
        return cli_module(args.cmd).cli(args.args)
    if args.cmd == "help":
        if args.module == "run":
            return dispatch(make_parser().parse_args(["run", "help"]))
        print(cli_module(args.module).__doc__.strip() if args.module else __doc__.strip("\n"))


def launch(argv, parser=None):
    """Run a command with shared terminal error and interrupt handling."""
    try:
        args = (parser or make_parser()).parse_args(argv)
        if args.cmd or getattr(args, "schema", False):
            return dispatch(args)
    except SystemExit:
        pass
    except KeyboardInterrupt:
        print("\n  stopped")
    except Exception as e:
        print(f"ERROR: {e}")


def prompt_session():
    """Build the interactive input UI from module-owned command metadata."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings

    bindings = KeyBindings()

    @bindings.add("c-d")
    @bindings.add("c-r")
    def ignore_key(event):
        pass

    class CommandCompleter(Completer):
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            try:
                words = shlex.split(text)
            except ValueError:
                return  # Let the user finish a quoted argument.
            prefix = words.pop() if words and not text[-1:].isspace() else ""
            if not words:
                choices = [*CLI_MODULES, "run", "help", "exit", "quit", "--help"]
            elif words == ["help"]:
                choices = [*CLI_MODULES, "run"]
            elif words == ["run"]:
                choices = ["help", "--help"]
            elif words[0] in CLI_MODULES:
                try:
                    ops = cli_module(words[0]).CLI_SCHEMA["ops"]
                except (ImportError, SystemExit):
                    return
                if len(words) == 1:
                    choices = [*ops, "help", "--help"]
                elif words[1] in ops:
                    choices = [*ops[words[1]].get("params", []), "--help"]
                else:
                    return
            else:
                return
            for choice in sorted(choices):
                if choice.startswith(prefix):
                    yield Completion(choice, start_position=-len(prefix))

    history = Path(__file__).parent / ".out" / "cli_history"
    history.parent.mkdir(parents=True, exist_ok=True)
    return PromptSession(
        "mediactl> ",
        history=FileHistory(str(history)),
        auto_suggest=AutoSuggestFromHistory(),
        completer=CommandCompleter(),
        key_bindings=bindings,
        complete_while_typing=False,
        bottom_toolbar="Tab complete | Up/Down history | Ctrl-C exit",
    )


def repl(parser):
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if interactive:
        from prompt_toolkit.patch_stdout import patch_stdout
        session = prompt_session()
        print(__doc__.split("\nCommands:", 1)[0].strip("\n"))
        print("mediactl — type 'help' for commands, 'exit' to quit.")

    try:
        while True:
            try:
                if interactive:
                    # Preserve ANSI output from logui while background events arrive.
                    with patch_stdout(raw=True):
                        line = session.prompt().strip()
                else:
                    line = input().strip()
            except EOFError:
                if interactive:
                    print()
                break
            if not line:
                continue
            try:
                argv = shlex.split(line)
            except ValueError as e:
                print(f"ERROR: {e}")
                continue
            if argv in (["exit"], ["quit"]):
                break
            launch(argv, parser)
    except KeyboardInterrupt:
        print()


def main():
    parser = make_parser()
    argv = sys.argv[1:]
    # Help is available before configuration, including on a fresh checkout.
    if argv and (argv[0] in ("help", "-h", "--help") or
                 (argv[0] in (*CLI_MODULES, "run") and
                  ((len(argv) == 1 and argv[0] in CLI_MODULES) or argv[1:] == ["help"] or
                   "--help" in argv[1:] or "-h" in argv[1:]))):
        launch(argv, parser)
        return
    prepare()

    if argv:
        try:
            launch(argv, parser)
            while _scheduler and _scheduler.is_alive():
                _scheduler.join(timeout=1)
        except KeyboardInterrupt:
            pass
        finally:
            scheduler_stop()
            if _scheduler:
                cli_module("notify").listen_stop()
        return
    try:
        bootstrap()
        repl(parser)
    finally:
        scheduler_stop()
        if "web" in sys.modules:
            sys.modules["web"].stop()
        cli_module("notify").listen_stop()


if __name__ == "__main__":
    main()
