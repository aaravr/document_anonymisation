"""Command-line interface: ``python -m sanitiser run`` and friends."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import typer

from sanitiser.config import load_profile, load_seed_list
from sanitiser.pipeline import sanitise_document
from sanitiser.replace.registry import Registry

app = typer.Typer(add_completion=False, help="Test-data document sanitiser.")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@app.command()
def run(
    input: Path = typer.Option(..., "--input", "-i", help="Input file or directory"),
    output: Path = typer.Option(..., "--output", "-o", help="Output directory"),
    profile: str = typer.Option("strict_test_data", "--profile", "-p"),
    spacy_model: Optional[str] = typer.Option(None, "--spacy-model"),
    seed: Optional[int] = typer.Option(None, "--seed"),
    seed_list: Optional[Path] = typer.Option(None, "--seed-list"),
    registry_in: Optional[Path] = typer.Option(None, "--registry-in",
                                                help="Reuse an existing replacement_map.json"),
    registry_out: Optional[Path] = typer.Option(None, "--registry-out",
                                                  help="Write the merged registry here"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Sanitise a single file or every file under a directory.

    The same replacement map is used across every file in the run so that
    'Victor Dodig' in document A and 'Mr Dodig' in document B both map to the
    same synthetic name.
    """
    _setup_logging(verbose)
    prof = load_profile(profile)
    if spacy_model:
        prof.spacy_model = spacy_model
    if seed is not None:
        prof.seed = seed
    seeds = load_seed_list(seed_list) if seed_list else None

    # Build / load registry
    if registry_in and Path(registry_in).exists():
        registry = Registry.load(registry_in)
    else:
        registry = Registry(seed=prof.seed)

    if input.is_file():
        files = [input]
    elif input.is_dir():
        files = sorted(p for p in input.iterdir()
                       if p.is_file() and p.suffix.lower() in {".txt", ".docx", ".pdf"})
    else:
        typer.secho("Input does not exist: " + str(input), fg=typer.colors.RED)
        raise typer.Exit(2)

    results = []
    for f in files:
        try:
            summary = sanitise_document(
                f, output, profile=prof, seed_list=seeds, registry=registry,
            )
            results.append(summary.model_dump())
        except Exception as exc:
            logging.exception("failed on %s", f)
            results.append({"input_path": str(f), "status": "error", "error": str(exc)})

    out_dir = Path(output)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "batch_summary.json").write_text(
        json.dumps({"results": results}, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    if registry_out:
        registry.save(registry_out)
    else:
        registry.save(out_dir / "replacement_map.json")
    typer.echo("done. " + str(len(results)) + " file(s) processed.")


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
