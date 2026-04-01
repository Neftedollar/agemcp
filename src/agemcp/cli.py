"""
agemcp.cli
----------

Command-line interface for the AGE MCP server, providing commands to run and manage the server.

This module uses Click for CLI parsing and Rich for enhanced console output.
"""

import json, os

from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent
from typing import Any, Dict, List, Self, TypeVar

import click

from dotenv import dotenv_values, get_key, set_key
from rich.console import Console, Group
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.traceback import install as install_traceback

from agemcp.environment import Environment
from agemcp.settings import get_settings


ENV_PATH = Environment.get_dotenv_path()
ENV_EXAMPLE_PATH = Path(__file__).parent / ".env.example"

console = Console()
install_traceback(show_locals=True, word_wrap=True, console=console)

def init_settings() -> None:
    """Ensure that a .env file exists, before get_settings() is called, created from the .env.example file if missing."""
    if ENV_PATH.exists(): return
    
    if not ENV_EXAMPLE_PATH.exists():
        raise FileNotFoundError(f"Missing .env.example at {ENV_EXAMPLE_PATH}")
        
    ENV_PATH.write_text(ENV_EXAMPLE_PATH.read_text())

    
@dataclass(kw_only=True)
class Setting:
    name: str
    desc: str
    default: str | None 
    choices: list[str] | None
    prefix: str | None
    example: str | None

    value: str | None
    
    @property
    def is_required(self) -> bool:
        """Check if the setting is required (i.e., has no default value, or existing value)."""
        return self.default is None and self.value is None

    @classmethod
    def create(cls, *, 
        name:str, 
        desc:str,
        default:str | None = None,
        choices:list[str] | None = None,
        prefix:str | None = None,
        example:str | None = None
    ) -> Self:
        """Create a new Setting instance with the given parameters."""
        return cls(
            name=name,
            desc=desc,
            default=default,
            choices=choices,
            prefix=prefix,
            example=example,
            value=None
        )

    def panel_part(self) -> Group:
        """The render group that contains a nice looking panel above the prompt given to the user"""
        text = [
            f"[bold cyan]{self.desc}[/bold cyan]"
        ]
        if self.choices:
            text.append("")
            text.append(f"[cyan]Choices: [/cyan]")
            for i, choice in enumerate(self.choices):
                text.append(f"  - {i+1}. {choice}")
        
        if self.prefix:
            text.append("")
            text.append(f"[cyan]Prefix:  [/cyan][dim](Prefixed to value)[/dim]")
            text.append(f"    - {self.prefix}")
        
        if self.example:
            text.append("")
            text.append(f"[cyan]Example: [/cyan]")
            text.append(f"    - {self.example}")

        if self.default:
            text.append("")
            text.append(f"[cyan]Default: [/cyan][dim](Chosen if empty value)[/dim]")
            text.append(f"    - {self.default}")
            
        return Group(*[Panel.fit("\n".join(text), title=f"Configure: {self.name}", border_style="blue")])
    
    def ask(self):
        """Asks the prompt and sets the value to self.value"""
        console.print(self.panel_part())
        default = self.default if self.default else None
        while True:
            val = Prompt.ask(f"Value", default=default, show_default=False, show_choices=False)
            val = val if val is not None else ""

            if self.choices:
                # Check for integer choice short-cut
                try:
                    if int(val) in [(i+1) for i, _ in enumerate(self.choices)]:
                        val = self.choices[int(val) - 1]
                except ValueError:
                    pass

                # Check if valid choice.
                if val not in self.choices:
                    console.print("[bold red]Invalid choice, pick one the available options.[/bold red]")
                    continue

            if prefix := self.prefix:
                if not val.startswith(prefix):
                    val = prefix + val
                
            self.value = val

            if self.is_required and not self.value:
                console.print("[bold red]This setting is required and cannot be skipped.[/bold red]")
                continue

            console.print(f"[dim]>>> Confirmed: {self.value}[/dim]")
            break
        


@dataclass
class DotEnvFile:
    path: Path

    _values = {}

    @property
    def values(self) -> Dict[str, str | None]:
        if not self._values:
            self._values = dotenv_values(self.path)
        return self._values

    def set(self, key: str, value: str | None) -> None:
        self._values[key] = value

    def get(self, key: str, default: str | None = None) -> str | None:
        """Get a value from the .env file, returning default if not found."""
        return self._values.get(key, default)

    def save(self) -> None:
        if not self.path.exists():
            self.path.write_text("")

        # Remove _META key before saving (in memory and on disk)
        if "_META" in self._values:
            del self._values["_META"]
            try:
                from dotenv import unset_key
                unset_key(self.path, "_META")
            except ImportError:
                pass

        for key, value in self.values.items():
            set_key(self.path, key, str(value))

    @classmethod
    def from_path(cls, path: Path | str) -> Self:
        p = Path(path)
        return cls(path=p)

@click.group()
@click.pass_context
def cli(ctx: click.Context) -> None:
    """
    Main CLI entry point for the AGE MCP server.
    """


@cli.command()
def config() -> None:
    """Update / create configuration settings."""

    init_settings()
    console = Console()
    env_path = ENV_PATH
    
            

    def get_settings_from_meta_config() -> List[Setting]:
        env_example_path = ENV_EXAMPLE_PATH
        env_example = dotenv_values(env_example_path)
        meta_str = env_example.get("_META")
        if not meta_str:
            console.print("[bold red]No _META found in .env.example.[/bold red]")
            return []
        meta = json.loads(meta_str)
        settings = meta.get("settings", [])
        setting_objects = []
        for setting in settings:
            setting = dict(setting)
            setting_obj = Setting.create(
                name=setting.get("name", ""),
                desc=setting.get("desc", ""),
                default=setting.get("default"),
                choices=setting.get("choices"),
                prefix=setting.get("prefix"),
                example=setting.get("example"),
            )
            setting_objects.append(setting_obj)
        return setting_objects

    settings = get_settings_from_meta_config()

    # Load .env and parse _META
    env_file = DotEnvFile.from_path(env_path)
    
    # print all values from it
    existing_values = {key: value for key, value in env_file.values.items()}
    

    for setting in settings:

        if val := existing_values.get(setting.name):
            console.print(f"[dim]>>> Found existing value for {setting.name}: {val}[/dim]")
            setting.value = val
            setting.default = val

    # Now, iterate over each setting, asking them
    # to fill out the settings
    for setting in settings:
        setting.ask()
        
    # Now, update the env_file with the new setting.value's and save it
    for setting in settings:
        env_file.set(setting.name, setting.value)
        
    env_file.save()

if ENV_PATH.exists():

    @cli.command()
    def settings() -> None:
        """Show all current configuration settings."""
        console.print(get_settings().model_dump_json(indent=4))


    @cli.command()
    @click.option('--port', default=get_settings().mcp.port, help='Port to run the server on.')
    @click.option('--host', default=get_settings().mcp.host, help='Host to run the server on.')
    @click.option('--transport', type=click.Choice(['sse', 'streamable-http', 'stdio']), help='Transport to use.', default=get_settings().mcp.transport)
    @click.option('--log-level', type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']), default=get_settings().mcp.log_level, help='Log level to use.')
    def run(port: int, host: str, transport: str, log_level: str) -> None:
        """Launch the AGEMCP server.

        Override any settings from the .env file using standardized pydantic-settings syntax of
        
        <model>__<field>=<value> => MCP__PORT=7999
        
        Note: see all settings using the `settings` command.

        """
        import asyncio

        from typing import cast

        from fastmcp.cli.run import LogLevelType, TransportType, run_command

        spec = get_settings().app.package_path / "server.py"
        server_spec = str(spec)
        # Log for debug/info
        if log_level in ["DEBUG", "INFO"]:
            console.log(f"Running server via direct import: {server_spec}")

        # Map CLI args to run_command
        kwargs = {
            "server_spec": server_spec,
            "transport": cast(TransportType, transport),
            "log_level": cast(LogLevelType, log_level),
        }
        # host/port only for HTTP transports, not stdio
        if transport != "stdio":
            kwargs["host"] = host
            kwargs["port"] = port
        asyncio.run(run_command(**kwargs))
    
def main() -> None:
    """
    Main entry point for the CLI application.

    Invokes the CLI group, enabling command-line interaction.
    """
    cli()
