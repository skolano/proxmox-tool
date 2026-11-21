from typing import Optional

import questionary
import typer
from rich.table import Table

from proxmox_tool import config
from proxmox_tool.commands.common import console, fail

app = typer.Typer(help="Manage configured Proxmox VE clusters.")

NO_CONTEXTS = "No contexts configured. Run 'proxmox-tool context add'."


@app.command("add")
def add():
    """Add a cluster context interactively."""
    name = (questionary.text("Context name:").ask() or "").strip()
    if not name:
        fail("Cancelled: name is required.")

    if name in config.list_contexts():
        overwrite = questionary.confirm(
            f"Context '{name}' already exists. Overwrite?", default=False
        ).ask()
        if not overwrite:
            console.print("Cancelled.")
            raise typer.Exit(1)

    api_url = questionary.text("API URL (e.g. https://pve.example.com:8006):").ask()
    token_id = questionary.text("Token ID (e.g. user@realm!token-name):").ask()
    token_secret = questionary.password("Token secret:").ask()
    verify_ssl = questionary.confirm("Verify SSL certificate?", default=True).ask()

    if not all([api_url, token_id, token_secret]) or verify_ssl is None:
        fail("Cancelled: all fields are required.")

    config.add_context(name, api_url, token_id, token_secret, verify_ssl)
    console.print(f"[green]Context '{name}' added.[/green]")


@app.command("list")
def list_contexts():
    """List configured contexts."""
    contexts = config.list_contexts()
    current = config.get_current_context_name()

    if not contexts:
        console.print(NO_CONTEXTS)
        return

    table = Table()
    table.add_column("Active")
    table.add_column("Name")
    table.add_column("API URL")

    for name, ctx in contexts.items():
        table.add_row("*" if name == current else "", name, ctx["api_url"])

    console.print(table)


@app.command("use")
def use(name: Optional[str] = typer.Argument(None, help="Context to activate.")):
    """Switch the active context; without a name, open a selection menu."""
    contexts = config.list_contexts()
    if not contexts:
        fail(NO_CONTEXTS)

    if name is None:
        name = questionary.select("Select active context:", choices=list(contexts.keys())).ask()
        if name is None:
            raise typer.Exit(1)

    try:
        config.set_current_context(name)
    except config.ConfigError as exc:
        fail(str(exc))

    console.print(f"[green]Active context: {name}[/green]")


@app.command("current")
def current():
    """Show the active context."""
    try:
        ctx = config.get_active_context()
    except config.ConfigError as exc:
        fail(str(exc))

    console.print(f"{ctx['name']} ({ctx['api_url']})")
