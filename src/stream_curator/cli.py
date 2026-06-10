"""Minimal CLI for the push homepage."""

from __future__ import annotations

import json
import sys

import click

from .config import get_settings
from .logging import setup_logging
from .push_service import (
    PUSH_CARD_COUNT,
    create_store,
    get_push_page_payload,
    refresh_push_page_payload,
)
from .push_worker import run_worker_loop, run_worker_once
from .worker_process import (
    get_worker_process_status,
    start_worker_process,
    stop_worker_process,
)


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging.")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    setup_logging(verbose)


@cli.command("bootstrap")
def bootstrap_cmd() -> None:
    """Initialize local SQLite storage for the push cache."""
    settings = get_settings()
    store = create_store(settings)
    store.bootstrap()
    sys.stdout.write(str(settings.db_path))
    sys.stdout.flush()


@cli.group("client")
def client_group() -> None:
    """Read push-page payloads for the desktop client."""


@client_group.command("push")
@click.option("--refresh", is_flag=True, help="Promote the next ready page before reading.")
@click.option("--limit", default=PUSH_CARD_COUNT, show_default=True, type=int)
@click.option(
    "--ensure-current/--no-ensure-current",
    default=True,
    show_default=True,
    help="Try to fill the cache if the current page is missing.",
)
def client_push_cmd(refresh: bool, limit: int, ensure_current: bool) -> None:
    settings = get_settings()
    if refresh:
        payload = refresh_push_page_payload(settings=settings, limit=limit)
    else:
        payload = get_push_page_payload(settings=settings, ensure_current=ensure_current, limit=limit)
    _write_json(payload)


@cli.group("worker")
def worker_group() -> None:
    """Run or manage the background cache worker."""


@worker_group.command("once")
def worker_once_cmd() -> None:
    settings = get_settings()
    summary = run_worker_once(settings=settings)
    _write_json(summary.to_dict())


@worker_group.command("loop")
@click.option("--poll-seconds", default=None, type=int)
@click.option("--max-cycles", default=0, show_default=True, type=int)
def worker_loop_cmd(poll_seconds: int | None, max_cycles: int) -> None:
    settings = get_settings()
    summaries = run_worker_loop(
        settings=settings,
        poll_seconds=poll_seconds,
        max_cycles=max_cycles,
    )
    if max_cycles > 0:
        _write_json([summary.to_dict() for summary in summaries])


@worker_group.command("start")
@click.pass_context
def worker_start_cmd(ctx: click.Context) -> None:
    settings = get_settings()
    result = start_worker_process(
        project_root=settings.project_root,
        verbose=bool(ctx.obj.get("verbose")),
    )
    _write_json(result.to_dict())


@worker_group.command("stop")
def worker_stop_cmd() -> None:
    settings = get_settings()
    result = stop_worker_process(project_root=settings.project_root)
    _write_json(result.to_dict())


@worker_group.command("status")
def worker_status_cmd() -> None:
    settings = get_settings()
    _write_json(get_worker_process_status(project_root=settings.project_root).to_dict())


def _write_json(payload: object) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    cli()
