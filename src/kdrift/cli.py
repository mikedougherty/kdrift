"""CLI entrypoint."""

import click

from kdrift import config, logging


@click.group()
@click.option("--log-level", default="INFO", help="Log level (DEBUG, INFO, WARNING, ERROR)")
@click.pass_context
def main(ctx: click.Context, log_level: str) -> None:
    """Kustomize manifest drift detection tool."""
    cfg = config.AppConfig()
    logging.configure_logging(log_level=log_level, log_format=cfg.log_format)
    ctx.ensure_object(dict)
    ctx.obj["config"] = cfg


@main.command()
@click.pass_context
def run(ctx: click.Context) -> None:
    """Run the main operation."""
    click.echo("Hello from kdrift!")
