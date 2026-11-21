#
#  ██╗  ██╗██╗     ███╗   ██╗ ██████╗
#  ██║ ██╔╝██║     ████╗  ██║██╔═══██╗
#  █████╔╝ ██║     ██╔██╗ ██║██║   ██║
#  ██╔═██╗ ██║     ██║╚██╗██║██║   ██║
#  ██║  ██╗███████╗██║ ╚████║╚██████╔╝
#  ╚═╝  ╚═╝╚══════╝╚═╝  ╚═══╝ ╚═════╝
# ------------------------------------
# Szymon Kolano | https://github.com/skolano

import typer

from proxmox_tool import __version__
from proxmox_tool.commands import context, describe, get

app = typer.Typer(help="Query Proxmox VE clusters from the terminal.")
app.add_typer(context.app, name="context")
app.add_typer(get.app, name="get")
app.add_typer(describe.app, name="describe")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def root(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version and exit."
    ),
):
    pass


def main():
    app()


if __name__ == "__main__":
    main()
