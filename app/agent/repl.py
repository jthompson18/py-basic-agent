from __future__ import annotations

import asyncio
import json
import os
import shlex
import sys
import threading
import re
import urllib.parse
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text
from rich import box

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.document import Document

from .mcp_client import mcp_manager, HAS_MCP_STDIO, HAS_MCP_HTTP
from .core import run_agent
from . import tools, llm
from .memory import get_memory
from .schemas import Message

# --- console: auto color when TTY; honor NO_COLOR ---
NO_COLOR = bool(os.environ.get("NO_COLOR")) or (not sys.stdout.isatty())
console = Console(no_color=NO_COLOR)

# ---------- single background asyncio loop for all MCP work ----------
_BG_LOOP: asyncio.AbstractEventLoop | None = None
_BG_THREAD: threading.Thread | None = None


def _ensure_bg_loop() -> asyncio.AbstractEventLoop:
    global _BG_LOOP, _BG_THREAD
    if _BG_LOOP and _BG_LOOP.is_running():
        return _BG_LOOP
    loop = asyncio.new_event_loop()

    def _runner():
        asyncio.set_event_loop(loop)
        loop.run_forever()

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    _BG_LOOP = loop
    _BG_THREAD = t
    return loop


def _run_async(coro):
    """Run a coroutine in the persistent background loop and return its result."""
    loop = _ensure_bg_loop()
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result()


def _shutdown_bg_loop():
    global _BG_LOOP
    if not _BG_LOOP:
        return
    # Try to let MCP manager clean up (ignore if not present).
    try:
        _run_async(mcp_manager.close_all())
    except Exception:
        pass
    try:
        _BG_LOOP.call_soon_threadsafe(_BG_LOOP.stop)
    except Exception:
        pass
    _BG_LOOP = None


# ---------- help ----------
def _help_text() -> str:
    return """[bold]Agent REPL — Help[/]

[bold]Commands[/]
[green]/research <question>[/]  Run a research task (Serper search + page fetch + LLM summaries).
[green]/etl -p <path> -t "<transform>" [-l <out>][/]  Local ETL with flags.
  • [bold]-p[/bold] [white]<path>[/white] (required) — CSV or JSON file in your repo (mounted to /app or /app/data).
  • [bold]-t[/bold] [white]"..."[/white] (required) — transform DSL (see below).
  • [bold]-l[/bold] [white]<path>[/white] (optional) — output path.
    - If omitted, saves to [white]./data/transformed_<input>[/white] with format mirrored from input.
    - Format is taken from the [white]-l[/white] extension if given (e.g., .csv or .json).
[green]/etl_from_source -p <url> -t "<transform>" [-l <out>][/]  Remote ETL from a URL ending in .csv or .json.

[green]/where <path>[/]  Show how a local path resolves inside the container and whether it exists.

[green]/mcp add -n <name> -c "<cmd> [args]" [--env KEY=VAL,KEY2=VAL][/]
[green]/mcp add-http -n <name> -u http://host:port[/]  Connect to an MCP HTTP façade.
[green]/mcp list[/], [green]/mcp default <name>[/], [green]/mcp tools [<name>][/], [green]/mcp call <tool> '<JSON>'[/], [green]/mcp remove <name>[/]

[green]/rag ingest [-p PATH] [--glob "*.md,*.txt"][/]  Ingest files into memory (pgvector).
[green]/rag add -t "text" [-s source] [-u uri][/]      Add an ad-hoc snippet to memory.
[green]/rag show -q "query" [-k 6][/]                  Retrieve top-k chunks only (colored panels).
[green]/rag ask  <question> [-k 6][/]                  Retrieve + LLM answer from context only.

[yellow]exit()[/]  Quit the REPL.

[bold]Transform DSL[/]
Chain operations with semicolons. Works for CSV columns and JSON keys.
• [bold]reorder:[/bold][white]colA,colB,colC[/white]  Reorder columns; unspecified columns are appended in original order.
• [bold]rename:[/bold][white]old1->new1,old2->new2[/white]  Rename fields/columns. Quote names with spaces or numeric keys: [white]rename:'1958'->y1958,'unit price'->price[/white]

[bold]Color legend (verbose on)[/]
[bright_black]Step[/] — agent loop step
[cyan]MODEL[/] — raw model output (truncated)
[yellow]TOOL CALL[/] — which tool was invoked
[green]TOOL RESULT[/] — preview of the tool output
[magenta]SUMMARY (search/etl)[/] — LLM summaries of search/ETL
[red]ERROR[/] — errors
[white]FINAL[/] — agent’s final answer

[bold]Keyboard shortcuts[/]
Up/Down — navigate history (filtered)
Tab — completion (commands, paths for -p/-l)
Ctrl-R — reverse history search
Ctrl-A / Ctrl-E — start/end of line
Alt-B / Alt-F — move by word
Ctrl-U / Ctrl-K — kill to start / kill to end
Ctrl-W — delete previous word
Ctrl-Y — yank (paste)
Ctrl-L — clear screen
Ctrl-C — cancel current line
Ctrl-D — exit on empty line

[bold]Notes[/]
• Start quiet with CLI flag: [white]--no-verbose[/white] (e.g., [white]docker compose run --rm app --no-verbose[/white]).
• Local files live under your repo and are mounted at [white]/app[/white] and [white]/app/data[/white].
"""


def _emit_factory(verbose: bool):
    if not verbose:
        return lambda *_args, **_kwargs: None

    def emit(kind: str, payload):
        if kind == "step":
            console.print(
                f"[bold bright_black]Step {payload['n']}/{payload['max']}[/]")
        elif kind == "model":
            console.print(f"[cyan]MODEL[/]: {payload[:400]}")
        elif kind == "tool_call":
            console.print(
                f"[yellow]TOOL CALL[/]: {payload.get('tool')} {payload.get('input')}")
        elif kind == "tool_result":
            console.print(
                f"[green]TOOL RESULT[/]: {payload.get('tool')} → {(payload.get('preview') or '')[:400]}")
        elif kind == "summary":
            console.print(
                f"[magenta]SUMMARY ({payload.get('type')})[/]: {payload.get('text')}")
        elif kind == "error":
            console.print(f"[red]ERROR[/]: {payload}")
        elif kind == "final":
            console.print(f"[bold white]FINAL[/]: {payload}")

    return emit


def _basename_from_path_or_url(p: str) -> str:
    path = urllib.parse.urlparse(p).path or p
    return os.path.basename(path.rstrip("/"))


def _detect_source_type(src: str) -> str | None:
    path = urllib.parse.urlparse(src).path.lower()
    if path.endswith(".csv"):
        return "csv"
    if path.endswith(".json"):
        return "json"
    return None


def _default_outpath(input_path: str) -> str:
    base = _basename_from_path_or_url(input_path) or "data"
    return os.path.join("./data", f"transformed_{base}")


def _parse_flag_line(flag_line: str) -> Dict[str, str | None]:
    tokens = shlex.split(flag_line)
    out: Dict[str, str | None] = {"p": None, "t": None, "l": None}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-p" and i + 1 < len(tokens):
            out["p"] = tokens[i + 1]
            i += 2
        elif tok == "-t" and i + 1 < len(tokens):
            out["t"] = tokens[i + 1]
            i += 2
        elif tok == "-l" and i + 1 < len(tokens):
            out["l"] = tokens[i + 1]
            i += 2
        else:
            i += 1
    return out


def _parse_env_csv(s: str | None) -> dict:
    if not s:
        return {}
    out = {}
    for pair in s.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _parse_mcp_add_http_flags(rest: str) -> dict:
    # /mcp add-http -n NAME -u http://host:8765
    toks = shlex.split(rest)
    out = {"n": None, "u": None}
    i = 0
    while i < len(toks):
        t = toks[i]
        if t == "-n" and i + 1 < len(toks):
            out["n"] = toks[i + 1]
            i += 2
        elif t == "-u" and i + 1 < len(toks):
            out["u"] = toks[i + 1]
            i += 2
        else:
            i += 1
    return out


def _parse_mcp_add_stdio_flags(rest: str) -> dict:
    toks = shlex.split(rest)
    out = {"n": None, "c": None, "env": None}
    i = 0
    while i < len(toks):
        t = toks[i]
        if t == "-n" and i + 1 < len(toks):
            out["n"] = toks[i + 1]
            i += 2
        elif t == "-c" and i + 1 < len(toks):
            out["c"] = toks[i + 1]
            i += 2
        elif t == "--env" and i + 1 < len(toks):
            out["env"] = toks[i + 1]
            i += 2
        else:
            i += 1
    return out


def _only_tools_list(x):
    """Normalize various MCP list_tools() shapes to a list of tool dicts."""
    if isinstance(x, dict):
        # common HTTP façade shape: {"tools": [...], ...}
        tools = x.get("tools", [])
        # some facades wrap in "content": [{"type":"json","value":{"tools":[...]}}]
        if not tools and "content" in x and isinstance(x["content"], list):
            for item in x["content"]:
                if isinstance(item, dict) and item.get("type") == "json":
                    v = item.get("value", {})
                    if isinstance(v, dict) and "tools" in v:
                        tools = v["tools"]
                        break
        return tools if isinstance(tools, list) else []
    elif isinstance(x, list):
        # already a plain list of tools
        return [t for t in x if isinstance(t, dict)]
    return []


def _build_transform_spec(spec_str: str) -> dict:
    spec = {"select": None, "rename": {}, "limit": None}
    parts = [p.strip() for p in spec_str.split(";") if p.strip()]
    for part in parts:
        if part.startswith("reorder:"):
            cols = part[len("reorder:"):].strip()
            if cols:
                spec["select"] = [c.strip()
                                  for c in cols.split(",") if c.strip()]
        elif part.startswith("rename:"):
            mapping = part[len("rename:"):].strip()
            if mapping:
                pairs = [p.strip() for p in mapping.split(",") if p.strip()]
                for pair in pairs:
                    if "->" in pair:
                        old, new = [x.strip().strip("'").strip('"')
                                    for x in pair.split("->", 1)]
                        if old and new:
                            spec["rename"][old] = new
        elif part.startswith("limit:"):
            val = part[len("limit:"):].strip()
            try:
                spec["limit"] = int(val)
            except ValueError:
                spec["limit"] = None

    if not spec["rename"]:
        spec.pop("rename", None)
    if not spec.get("select"):
        spec.pop("select", None)
    if spec.get("limit") is None:
        spec.pop("limit", None)
    return spec


async def _run_flagged_etl(path_or_url: str, transform_str: str, out_path: str | None, verbose: bool):
    try:
        stype = _detect_source_type(path_or_url)
        if not stype:
            console.print("[red]Source must end with .csv or .json[/]")
            return
        in_path = path_or_url
        final_out = out_path or _default_outpath(in_path)

        # Decide output format/path
        out_ext = os.path.splitext(
            urllib.parse.urlparse(final_out).path.lower())[1]
        if out_ext in (".csv", ".json"):
            out_fmt = out_ext.lstrip(".")
        else:
            out_fmt = "csv" if stype == "csv" else "json"
            final_out = final_out + ("." + out_fmt)

        # 1) load
        load_op = "load_csv" if stype == "csv" else "load_json"
        load_res = await tools.etl_tool(load_op, path=in_path)

        # 2) transform (+ optional save)
        spec = _build_transform_spec(transform_str)
        transform_op = "transform_csv" if stype == "csv" else "transform_json"
        tr_res = await tools.etl_tool(
            transform_op,
            path=in_path,
            spec=spec,
            save={"format": out_fmt, "path": final_out},
        )

        # 3) summarize via LLM
        summary = await llm.summarize_etl({"load": load_res, "transform": tr_res})
        console.print(
            f"[green]Saved:[/] {tr_res.get('saved_as') or final_out}")
        console.print(f"[magenta]SUMMARY (etl)[/]: {summary}")
    except FileNotFoundError as e:
        console.print(f"[red]File not found:[/] {e}")
        console.print(
            "[bright_black]Tip: place files in repo ./data/ (mounted to /app/data) and use: /etl -p ./data/your.csv -t \"...\"[/]")
    except Exception as e:
        console.print(f"[red]ETL error:[/] {type(e).__name__}: {e}")


def _make_key_bindings():
    kb = KeyBindings()

    @kb.add("c-c")
    def _(event):
        # cancel line
        event.app.current_buffer.reset()

    @kb.add("c-d")
    def _(event):
        # exit on empty, else delete
        buf = event.app.current_buffer
        if not buf.text:
            event.app.exit(result="exit()")
        else:
            buf.delete(1)

    @kb.add("c-l")
    def _(event):
        # clear
        event.app.renderer.clear()

    return kb


class AgentCompleter(Completer):
    def __init__(self):
        self.commands = [
            "/research", "/etl", "/etl_from_source", "/where",
            "/mcp", "/rag", "/help", "exit()"
        ]
        self.path_completer = PathCompleter(expanduser=True)

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        stripped = text.lstrip()

        # command suggestions
        if stripped.startswith("/") and " " not in stripped:
            prefix = stripped
            for cmd in self.commands:
                if cmd.startswith(prefix):
                    yield Completion(cmd, start_position=-len(prefix))
            return

        tokens = stripped.split()
        last = tokens[-1] if tokens else ""
        want_path = False
        frag = ""

        if last in ("-p", "-l"):
            want_path = True
            frag = ""
        elif any(last.startswith(pfx) for pfx in ("/", "./", "../", "data/", "/app/")):
            want_path = True
            frag = last

        if want_path:
            doc = Document(frag, cursor_position=len(frag))
            for c in self.path_completer.get_completions(doc, complete_event):
                yield Completion(c.text, start_position=-len(frag), display=c.display)


def _run_once(query: str, verbose: bool):
    emit = _emit_factory(verbose)
    ans = asyncio.run(run_agent(query, emit=emit, verbose=verbose))
    console.rule("[white]Answer")
    console.print(ans)
    console.rule()


# ---------- RAG: inline helpers for REPL ----------
KB_DEFAULT = os.getenv("KB_PATH", "/knowledge")
DEFAULT_PATTERNS = ("**/*.md", "**/*.txt")
CHUNK_WORDS = 800
OVERLAP_WORDS = 150


def _read_files(root: Path, patterns: Iterable[str]) -> List[Tuple[Path, str]]:
    out: List[Tuple[Path, str]] = []
    for pat in patterns:
        for p in root.rglob(pat):
            if p.is_file():
                try:
                    out.append((p, p.read_text(encoding="utf-8")))
                except Exception:
                    pass
    return out


def _chunk_words(text: str, n: int = CHUNK_WORDS, overlap: int = OVERLAP_WORDS) -> List[str]:
    words = text.split()
    if not words:
        return []
    chunks, i = [], 0
    while i < len(words):
        chunk = " ".join(words[i:i + n]).strip()
        if chunk:
            chunks.append(chunk)
        if i + n >= len(words):
            break
        i += n - overlap
    return chunks


def _highlight_terms(text: str, terms: List[str]) -> Text:
    t = Text(text)
    for term in terms:
        if term:
            t.highlight_words([term], style="yellow bold")
    return t


def _render_hits(hits: List[Dict], query: str, title: str = "RETRIEVAL") -> None:
    terms = [w.strip(",.?:;! ").lower() for w in query.split() if len(w) > 2]
    panels = []
    for i, h in enumerate(hits, start=1):
        head = Text(f"#{i}  ", style="bold green")
        head.append(f"{h.get('source') or 'doc'}  ", style="cyan")

        score = h.get("score")
        if isinstance(score, (int, float)) and score > 0:
            head.append(f"(score {score:.3f}) ", style="dim")

        if h.get("uri"):
            head.append(f"{h['uri']}", style="magenta")

        body = _highlight_terms(h.get("text", ""), terms)
        group = Group(head, Text("─" * 40, style="dim"), body)
        panels.append(Panel(group, box=box.ROUNDED))
    console.print(Panel(Group(*panels), title=title, border_style="green"))


async def _rag_ingest_dir(path: str, patterns: Iterable[str]) -> Dict[str, int]:
    mem = get_memory()
    files = _read_files(Path(path).resolve(), patterns)
    total_chunks = 0
    for fpath, text in files:
        chunks = _chunk_words(text)
        for idx, chunk in enumerate(chunks):
            meta = {"chunk": idx + 1, "chunks": len(chunks)}
            await mem.aadd(content=chunk, source=fpath.name, uri=str(fpath), meta=meta)
            total_chunks += 1
    return {"files": len(files), "chunks": total_chunks}


async def _rag_add_text(text: str, source: str, uri: str) -> None:
    mem = get_memory()
    await mem.aadd(content=text, source=source, uri=uri, meta={})


async def _rag_retrieve(query: str, k: int) -> List[Dict]:
    mem = get_memory()
    hits = await mem.aquery(query, k=k)
    out = []
    for h in hits:
        # pg_store returns dicts with the following keys
        out.append({
            "score": h.get("score"),
            "source": h.get("source"),
            "uri": h.get("uri"),
            "text": h.get("content"),
            "meta": h.get("meta"),
        })
    return out


async def _rag_ask(question: str, k: int) -> Dict[str, object]:
    hits = await _rag_retrieve(question, k=k)
    context = "\n\n---\n\n".join(
        f"[{h['source'] or 'doc'}] {h['text']}" for h in hits)

    # Use your existing LLM client (llm.chat) which expects Message objects. :contentReference[oaicite:1]{index=1}
    system = "You are a precise assistant. Answer ONLY from CONTEXT. If not in CONTEXT, reply 'I don't know.'"
    user = f"QUESTION:\n{question}\n\nCONTEXT:\n{context}"
    msgs = [Message(role="system", content=system),
            Message(role="user", content=user)]
    try:
        answer = await llm.chat(msgs, temperature=0.0)
    except Exception as e:
        answer = f"(chat error: {type(e).__name__}: {e})"

    return {"answer": answer, "hits": hits}


# ---------- public API ----------
def run_task(query: str, verbose: bool = True) -> None:
    _run_once(query, verbose)


def run_repl(verbose: bool = True) -> None:
    console.print(
        "[bold]Agent REPL[/] — type "
        "[green]/research ...[/], [green]/etl ...[/], [green]/etl_from_source ...[/], "
        "[green]/rag ...[/], [green]/mcp ...[/].  Type [green]/help[/] for details."
    )

    hist_path = os.path.expanduser("~/.py_basic_agent_history")
    session = PromptSession(
        history=FileHistory(hist_path),
        auto_suggest=AutoSuggestFromHistory(),
        completer=AgentCompleter(),
        key_bindings=_make_key_bindings(),
    )

    while True:
        try:
            line = session.prompt("> ")
        except (EOFError, KeyboardInterrupt):
            line = "exit()"

        line = (line or "").strip()
        if not line:
            continue
        if line == "exit()":
            break

        # ----- Commands -----
        if line == "/help":
            console.print(_help_text())
            continue

        if line.startswith("/where "):
            _, p = line.split(" ", 1)
            rp = os.path.realpath(p)
            exists = os.path.exists(rp)
            console.print(f"path: {rp}  exists: {exists}")
            continue

        if line.startswith("/research "):
            _, q = line.split(" ", 1)
            run_task(q, verbose=verbose)
            continue

        if line.startswith("/etl "):
            _, flags = line.split(" ", 1)
            f = _parse_flag_line(flags)
            if not (f["p"] and f["t"]):
                console.print(
                    "[red]/etl requires -p <path> and -t \"<transform>\"[/]")
            else:
                asyncio.run(_run_flagged_etl(f["p"], f["t"], f["l"], verbose))
            continue

        if line.startswith("/etl_from_source "):
            _, flags = line.split(" ", 1)
            f = _parse_flag_line(flags)
            if not (f["p"] and f["t"]):
                console.print(
                    "[red]/etl_from_source requires -p <url> and -t \"<transform>\"[/]")
            else:
                asyncio.run(_run_flagged_etl(f["p"], f["t"], f["l"], verbose))
            continue

        if line.startswith("/mcp "):
            # subcommands: add, add-http, list, default, tools, call, remove
            parts = shlex.split(line)
            sub = parts[1] if len(parts) > 1 else None

            try:
                if sub == "add-http":
                    rest = line.split(" ", 2)[2] if len(parts) >= 3 else ""
                    opts = _parse_mcp_add_http_flags(rest)
                    if not (opts["n"] and opts["u"]):
                        console.print(
                            "[red]/mcp add-http -n <name> -u http://host:port[/]")
                    else:
                        _run_async(mcp_manager.add_http(opts["n"], opts["u"]))
                        console.print(
                            f"[green]HTTP MCP added:[/] {opts['n']} → {opts['u']}")
                elif sub == "add":
                    if not HAS_MCP_STDIO:
                        console.print(
                            "[red]MCP stdio client not included in this build.[/]")
                    else:
                        rest = line.split(" ", 2)[2] if len(parts) >= 3 else ""
                        opts = _parse_mcp_add_stdio_flags(rest)
                        if not (opts["n"] and opts["c"]):
                            console.print(
                                "[red]/mcp add -n <name> -c \"command ...\" [--env K=V,...][/]")
                        else:
                            env = _parse_env_csv(opts.get("env"))
                            _run_async(mcp_manager.add_stdio(
                                opts["n"], opts["c"], env))
                            console.print(
                                f"[green]STDIO MCP added:[/] {opts['n']}")
                elif sub == "list":
                    names = _run_async(mcp_manager.list_servers())
                    console.print("servers: " + ", ".join(names)
                                  if names else "(none)")
                elif sub == "default":
                    name = parts[2] if len(parts) > 2 else None
                    if not name:
                        console.print("[red]/mcp default <name>[/]")
                    else:
                        _run_async(mcp_manager.set_default(name))
                        console.print(f"default server: {name}")
                elif sub == "tools":
                    name = parts[2] if len(parts) > 2 else None
                    tools_list = _run_async(mcp_manager.list_tools(name))
                    only = _only_tools_list(tools_list)
                    if not only:
                        console.print("(no tools)")
                    else:
                        for t in only:
                            console.print(
                                f"- {t.get('name')} — {t.get('description')}")
                elif sub == "call":
                    if len(parts) < 4:
                        console.print(
                            "[red]/mcp call <server|-> <tool> '<JSON>'[/]")
                    else:
                        server = None if parts[2] == "-" else parts[2]
                        tool = parts[3]
                        args_json = " ".join(parts[4:]) if len(
                            parts) > 4 else "{}"
                        try:
                            args = json.loads(args_json)
                        except Exception as e:
                            console.print(f"[red]Invalid JSON:[/] {e}")
                            continue
                        resp = _run_async(mcp_manager.call(
                            tool, args, server_name=server))
                        console.print(json.dumps(resp, indent=2))
                elif sub == "remove":
                    if len(parts) < 3:
                        console.print("[red]/mcp remove <name>[/]")
                    else:
                        _run_async(mcp_manager.remove(parts[2]))
                        console.print(f"removed: {parts[2]}")
                else:
                    console.print("[red]Unknown /mcp subcommand[/]")
            except Exception as e:
                console.print(f"[red]MCP error:[/] {type(e).__name__}: {e}")
            continue

        if line.startswith("/rag "):
            # split only once to avoid shlex on entire line (apostrophe-safe)
            try:
                _, rest = line.split(" ", 1)
            except ValueError:
                console.print(
                    "[bold cyan]/rag subcommands:[/] ingest | add | show | ask")
                continue

            sub, rest_args = (rest.split(" ", 1) + [""])[:2]

            if sub == "ingest":
                # /rag ingest [-p PATH] [--glob "*.md,*.txt"]
                try:
                    args = shlex.split(rest_args)
                except ValueError:
                    # fall back gracefully if quoting is odd
                    args = rest_args.split()
                path = KB_DEFAULT
                patterns = DEFAULT_PATTERNS
                i = 0
                while i < len(args):
                    if args[i] in ("-p", "--path") and i + 1 < len(args):
                        path = args[i + 1]
                        i += 2
                    elif args[i] == "--glob" and i + 1 < len(args):
                        patterns = tuple(x.strip()
                                         for x in args[i + 1].split(",") if x.strip())
                        i += 2
                    else:
                        i += 1
                res = asyncio.run(_rag_ingest_dir(path, patterns))
                console.print(Panel(
                    f"INGEST DONE: files={res['files']} chunks={res['chunks']}", border_style="green"))
                continue

            if sub == "add":
                # /rag add -t "text" [-s source] [-u uri]
                try:
                    args = shlex.split(rest_args)
                except ValueError:
                    args = rest_args.split()
                text = None
                source = "adhoc"
                uri = "mem://adhoc"
                i = 0
                while i < len(args):
                    if args[i] in ("-t", "--text") and i + 1 < len(args):
                        text = args[i + 1]
                        i += 2
                    elif args[i] in ("-s", "--source") and i + 1 < len(args):
                        source = args[i + 1]
                        i += 2
                    elif args[i] in ("-u", "--uri") and i + 1 < len(args):
                        uri = args[i + 1]
                        i += 2
                    else:
                        i += 1
                if not text:
                    console.print("[bold red]Missing -t/--text[/]")
                else:
                    asyncio.run(_rag_add_text(text, source, uri))
                    console.print(Panel("ADDED ✓", border_style="green"))
                continue

            if sub == "show":
                # /rag show -q "query" [-k 6]
                try:
                    args = shlex.split(rest_args)
                except ValueError:
                    args = rest_args.split()
                query = None
                k = 6
                i = 0
                while i < len(args):
                    if args[i] in ("-q", "--query") and i + 1 < len(args):
                        query = args[i + 1]
                        i += 2
                    elif args[i] in ("-k", "--k") and i + 1 < len(args):
                        try:
                            k = int(args[i + 1])
                        except Exception:
                            pass
                        i += 2
                    else:
                        i += 1
                if not query:
                    console.print("[bold red]Missing -q/--query[/]")
                else:
                    hits = asyncio.run(_rag_retrieve(query, k))
                    _render_hits(hits, query, title=f"RETRIEVAL k={k}")
                continue

            if sub == "ask":
                # /rag ask <question> [-k N]
                # Grab optional trailing "-k N" without running shlex over apostrophes in the question.
                m = re.search(r"(?:^|\s)-k\s+(\d+)\s*$", rest_args)
                if m:
                    try:
                        k = int(m.group(1))
                    except Exception:
                        k = 6
                    question = rest_args[:m.start()].strip()
                else:
                    k = 6
                    question = rest_args.strip()

                # Strip surrounding quotes if the whole question is quoted
                if len(question) >= 2 and question[0] == question[-1] and question[0] in ("'", '"'):
                    question = question[1:-1]

                if not question:
                    console.print(
                        "[bold red]Usage:[/] /rag ask <question> [-k 6]")
                else:
                    res = asyncio.run(_rag_ask(question, k))
                    _render_hits(res["hits"], question,
                                 title=f"RETRIEVAL for: {question}")
                    console.print(
                        Panel(Text(str(res["answer"])), title="ANSWER", border_style="cyan"))
                continue

            console.print(
                "[bold cyan]/rag subcommands:[/] ingest | add | show | ask")
            continue

    _shutdown_bg_loop()


if __name__ == "__main__":
    verbose = True
    if "--no-verbose" in sys.argv:
        verbose = False
    run_repl(verbose=verbose)
