from concurrent.futures import ThreadPoolExecutor, as_completed

import typer
from rich.console import Console

from proxmox_tools import config
from proxmox_tools.api import MAX_WORKERS, ProxmoxAPI, ProxmoxAPIError

console = Console()


def fail(message: str) -> None:
    console.print(f"[red]{message}[/red]")
    raise typer.Exit(1)


def get_api() -> ProxmoxAPI:
    try:
        ctx = config.get_active_context()
    except config.ConfigError as exc:
        fail(str(exc))
    return ProxmoxAPI(
        api_url=ctx["api_url"],
        token_id=ctx["token_id"],
        token_secret=ctx["token_secret"],
        verify_ssl=ctx["verify_ssl"],
    )


def fetch_vms(api: ProxmoxAPI) -> list[dict]:
    try:
        return api.get_vms()
    except ProxmoxAPIError as exc:
        fail(str(exc))


def run_parallel(func, items: list) -> list[tuple]:
    results = []
    if not items:
        return results
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(items))) as executor:
        futures = {executor.submit(func, item): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                results.append((item, future.result(), None))
            except ProxmoxAPIError as exc:
                results.append((item, None, exc))
    return results
