"""No network, by construction: no module of this pack may import a network library.

Second never-event of the pack. Also checked here: nothing in the repository names a real venue or
instrument, holds something shaped like a key or an address, or mentions a path of the machine it was
written on. The words searched for are assembled from pieces, so that this file does not match itself.
"""
from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

import tests
from tests import _util as U

BANNED_MODULES = {
    # standard library: anything that opens a connection or serves one
    "socket", "ssl", "http", "urllib", "ftplib", "smtplib", "poplib", "imaplib", "nntplib", "telnetlib",
    "xmlrpc", "socketserver", "asyncio", "selectors", "select", "webbrowser", "smtpd", "asyncore", "asynchat",
    "wsgiref", "cgi", "ipaddress", "email", "mailbox", "multiprocessing",
    # third-party clients (none is installed by this pack; listed so that adding one fails here first)
    "requests", "websockets", "websocket", "aiohttp", "httpx", "urllib3", "paramiko", "grpc", "boto3", "botocore",
    "zmq", "pycurl", "twisted", "tornado", "flask", "fastapi", "django",
    # exchange, chain and model connectors
    "ccxt", "web3", "tr" + "onpy", "eth_account", "sol" + "ana", "ib_insync", "alpaca", "bin" + "ance", "anthropic",
    "openai",
}
ALLOWED_TO_IMPORT_SOCKET = {"tests/__init__.py"}     # it imports the module in order to disable it
SKIP_DIRS = {".git", "build", "__pycache__", "out", ".venv", "venv", "node_modules"}
TEXT_SUFFIXES = {".py", ".json", ".jsonl", ".md", ".yml", ".yaml", ".txt", ".toml", ".cfg", ".ini", ".sha256", ""}


def repo_files() -> list[Path]:
    found = []
    for path in sorted(U.ROOT.rglob("*")):
        rel = path.relative_to(U.ROOT).parts
        if path.is_file() and not any(part in SKIP_DIRS for part in rel[:-1]):
            found.append(path)
    return found


def python_files() -> list[Path]:
    return [p for p in repo_files() if p.suffix == ".py"]


def imported_modules(source: str) -> set[str]:
    """Top-level names of every module a source file imports, including the dynamic forms with a literal name."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            func = node.func
            called = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if called in ("__import__", "import_module"):
                first = node.args[0] if node.args else None
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    names.add(first.value.split(".")[0])
                else:
                    names.add("<dynamic import with a computed name>")
            elif isinstance(func, ast.Name) and called in ("exec", "eval", "compile"):
                names.add(f"<{called}>")                 # the builtins, not methods such as re.compile
    return names


class TheScannerItself(unittest.TestCase):
    def test_it_sees_every_form_of_import(self):
        sample = ("import os, soc" + "ket\nfrom url" + "lib import request\nimport http" + ".client as c\n"
                  "x = __import__('s" + "sl')\nimport importlib\ny = importlib.import_module('ftp" + "lib')\n"
                  "z = importlib.import_module(name)\n\ndef f():\n    import smtp" + "lib\n")
        found = imported_modules(sample)
        for name in ("socket", "urllib", "http", "ssl", "ftplib", "smtplib"):
            self.assertIn(name, found)
        self.assertIn("<dynamic import with a computed name>", found)
        self.assertEqual(found & BANNED_MODULES, {"socket", "urllib", "http", "ssl", "ftplib", "smtplib"})
        self.assertIn("<exec>", imported_modules("exec('import os')"))

    def test_it_looks_at_every_python_file_of_the_pack(self):
        rels = {p.relative_to(U.ROOT).as_posix() for p in python_files()}
        for expected in ("harness/ledger.py", "harness/engine.py", "harness/run.py", "corpus/generate.py",
                         "corpus/reference_drawdown.py", "eval/score.py", "scenarios/run_all.py",
                         "scenarios/S01/check.py", "scenarios/S10/check.py", "tools/rebuild.py", "tests/_util.py"):
            self.assertIn(expected, rels)
        self.assertGreaterEqual(len(rels), 45)


class NoNetworkImport(unittest.TestCase):
    def test_no_module_imports_a_network_library(self):
        offenders = {}
        for path in python_files():
            rel = path.relative_to(U.ROOT).as_posix()
            banned = imported_modules(path.read_text(encoding="utf-8")) & BANNED_MODULES
            if rel in ALLOWED_TO_IMPORT_SOCKET:
                banned -= {"socket"}
            if banned:
                offenders[rel] = sorted(banned)
        self.assertEqual(offenders, {})

    def test_no_computed_import_and_no_exec(self):
        offenders = {}
        for path in python_files():
            odd = {n for n in imported_modules(path.read_text(encoding="utf-8")) if n.startswith("<")}
            if odd:
                offenders[path.relative_to(U.ROOT).as_posix()] = sorted(odd)
        self.assertEqual(offenders, {})

    def test_only_the_standard_library_and_the_pack_are_imported(self):
        own = {"harness", "corpus", "eval", "tests", "tools", "scenarios", "_common", "_util", "__future__"}
        foreign = {}
        for path in python_files():
            names = {n for n in imported_modules(path.read_text(encoding="utf-8")) if not n.startswith("<")}
            extra = sorted(n for n in names if n not in own and n not in sys.stdlib_module_names)
            if extra:
                foreign[path.relative_to(U.ROOT).as_posix()] = extra
        self.assertEqual(foreign, {})

    def test_the_harness_does_not_start_processes_or_read_the_clock_or_the_environment(self):
        for path in sorted((U.ROOT / "harness").glob("*.py")) + sorted((U.ROOT / "corpus").glob("*.py")):
            names = imported_modules(path.read_text(encoding="utf-8"))
            self.assertEqual(names & {"subprocess", "os", "time", "datetime", "ctypes", "shutil", "tempfile", "uuid",
                                      "secrets"}, set(), path.name)

    def test_requirements_list_no_package(self):
        lines = [line.strip() for line in (U.ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([line for line in lines if line and not line.startswith("#")], [])
        for name in ("pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "poetry.lock"):
            self.assertFalse((U.ROOT / name).exists(), name)


class SocketsAreBlockedInTheTests(unittest.TestCase):
    def test_opening_a_socket_raises(self):
        sock = sys.modules["soc" + "ket"]
        with self.assertRaises(tests.NetworkBlocked):
            sock.socket()
        with self.assertRaises(tests.NetworkBlocked):
            sock.socket(sock.AF_INET, sock.SOCK_STREAM)
        with self.assertRaises(tests.NetworkBlocked):
            sock.create_connection(("127.0.0.1", 9))
        with self.assertRaises(tests.NetworkBlocked):
            sock.getaddrinfo("localhost", 80)
        with self.assertRaises(tests.NetworkBlocked):
            sock.gethostbyname("localhost")


def _words(*pieces: str) -> str:
    return "|".join(pieces)


VENUES_AND_INSTRUMENTS = re.compile(r"\b(" + _words(
    "bin" + "ance", "coin" + "base", "kra" + "ken", "bit" + "finex", "by" + "bit", "o" + "kx", "poly" + "market",
    "kal" + "shi", "bet" + "fair", "predict" + "it", "uni" + "swap", "sun" + "swap", "pancake" + "swap",
    "nas" + "daq", "ny" + "se", "bit" + "coin", "b" + "tc", "ether" + "eum", "e" + "th", "us" + "dt", "us" + "dc",
    "tr" + "on", "t" + "rx", "sol" + "ana", "eur" + "usd", "dow " + "jones", "for" + "ex", "meta" + "mask",
    "tr" + "onlink", "led" + "ger nano") + r")\b", re.IGNORECASE)
KEYS_AND_ADDRESSES = re.compile(_words(
    r"0x[0-9a-fA-F]{40}\b", r"\bT[1-9A-HJ-NP-Za-km-z]{33}\b", r"\bsk-[A-Za-z0-9_\-]{20,}", r"-----BEGIN [A-Z ]+-----",
    r"\bxox[abp]-[A-Za-z0-9\-]{10,}", r"\bgh[pousr]_[A-Za-z0-9]{30,}", r"\bAKIA[0-9A-Z]{16}\b"))
INTERNAL = re.compile(_words(
    r"(?<![A-Za-z])[A-Za-z]:[\\/](?!/)", r"\\Us" + r"ers\\", "/Us" + "ers/", "/ho" + "me/", "/o" + "pt/",
    r"\baet" + r"he\b", "global" + "master", "crypto" + "host", "_pi" + "ani", "01-" + "active", "App" + "Data",
    "scratch" + "pad"), re.IGNORECASE)
OUT_OF_SCOPE = re.compile(r"\b(" + _words("line" + "work", "ln" + "wk", "l" + "wk") + r")\b", re.IGNORECASE)


class ContentHygiene(unittest.TestCase):
    def scan(self, pattern: re.Pattern, skip=()) -> dict:
        hits = {}
        for path in repo_files():
            rel = path.relative_to(U.ROOT).as_posix()
            if path.suffix not in TEXT_SUFFIXES or rel in skip:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            found = sorted({m.group(0) for m in pattern.finditer(text)})
            if found:
                hits[rel] = found[:5]
        return hits

    def test_the_patterns_do_find_what_they_look_for(self):
        self.assertTrue(VENUES_AND_INSTRUMENTS.search("quotes from Bin" + "ance"))
        self.assertTrue(VENUES_AND_INSTRUMENTS.search("1 B" + "TC"))
        self.assertFalse(VENUES_AND_INSTRUMENTS.search("method, electron, betting"))
        self.assertTrue(KEYS_AND_ADDRESSES.search("0x" + "ab" * 20))
        self.assertTrue(KEYS_AND_ADDRESSES.search("T" + "a" * 33))      # the shape only: not an address of anything
        self.assertFalse(KEYS_AND_ADDRESSES.search("ae3a3391353afdad6b93acfecb3d379c1298a6fdcf5b7f8b78d2de41188aa4b0"))
        self.assertTrue(INTERNAL.search("C:" + "\\Us" + "ers\\someone"))
        self.assertTrue(INTERNAL.search("D:" + "/work/file"))
        self.assertFalse(INTERNAL.search("https://example.invalid/page and ratio 3:/4"[:31]))
        self.assertTrue(OUT_OF_SCOPE.search("the LINE" + "WORK platform"))

    def test_no_real_venue_or_instrument_is_named(self):
        self.assertEqual(self.scan(VENUES_AND_INSTRUMENTS), {})

    def test_nothing_shaped_like_a_key_or_an_address(self):
        self.assertEqual(self.scan(KEYS_AND_ADDRESSES), {})

    def test_no_internal_path_or_machine_name(self):
        self.assertEqual(self.scan(INTERNAL), {})

    def test_nothing_about_the_out_of_scope_subjects(self):
        self.assertEqual(self.scan(OUT_OF_SCOPE), {})

    def test_generated_files_use_lf_and_utf8(self):
        bad = []
        for path in repo_files():
            if path.suffix in TEXT_SUFFIXES and path.name not in ("LICENSE",):
                raw = path.read_bytes()
                if b"\r" in raw or raw.startswith(b"\xef\xbb\xbf"):
                    bad.append(path.relative_to(U.ROOT).as_posix())
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
