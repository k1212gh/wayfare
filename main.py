"""CLI entry point for ScreenAtlas pipeline."""

import argparse
import logging
import sys
from pathlib import Path

from config import PipelineConfig
from pipeline import Pipeline


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_run(args: argparse.Namespace) -> None:
    """Run the full pipeline on an APK."""
    apk = Path(args.apk).resolve()
    if not apk.exists():
        print(f"APK not found: {apk}")
        sys.exit(1)

    config = PipelineConfig(
        apk_path=str(apk),
        device_serial=args.device,
        is_emulator=args.emulator,
        workspace_root=args.workspace,
        droidbot_timeout=args.timeout,
        droidbot_policy=args.policy,
    )
    pipeline = Pipeline(config)
    pipeline.run()
    print(f"\nDone! Tour ID: {config.tour_id}")
    print(f"Output: {config.output_dir / config.screenmap_output_filename}")


def cmd_resume(args: argparse.Namespace) -> None:
    """Resume a pipeline from a specific stage."""
    config = PipelineConfig(
        workspace_root=args.workspace,
        tour_id=args.tour,
    )
    if not config.tour_dir.exists():
        print(f"Tour not found: {config.tour_dir}")
        sys.exit(1)

    pipeline = Pipeline(config)
    pipeline.run(from_stage=args.stage)


def cmd_serve(args: argparse.Namespace) -> None:
    """Start the web visualization server."""
    import uvicorn

    uvicorn.run(
        "dashboard.backend.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="screenatlas",
        description="ScreenAtlas — APK → Screen Map pipeline (static + TapWalker + LLM)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    # --- run ---
    p_run = sub.add_parser("run", help="Run the full pipeline")
    p_run.add_argument("--apk", required=True, help="Path to .apk file")
    p_run.add_argument("--device", default="emulator-5554", help="ADB device serial")
    p_run.add_argument("--emulator", action="store_true", default=True)
    p_run.add_argument("--workspace", default="workspace")
    p_run.add_argument("--timeout", type=int, default=600, help="DroidBot timeout (sec)")
    p_run.add_argument("--policy", default="dfs_greedy", help="DroidBot policy")
    p_run.set_defaults(func=cmd_run)

    # --- resume ---
    p_res = sub.add_parser("resume", help="Resume from a stage")
    p_res.add_argument("--tour", required=True, help="Tour ID")
    p_res.add_argument("--stage", required=True, choices=[f"stage{i}" for i in range(1, 7)])
    p_res.add_argument("--workspace", default="workspace")
    p_res.set_defaults(func=cmd_resume)

    # --- serve ---
    p_srv = sub.add_parser("serve", help="Start web visualization server")
    p_srv.add_argument("--host", default="127.0.0.1")
    p_srv.add_argument("--port", type=int, default=8000)
    p_srv.add_argument("--reload", action="store_true")
    p_srv.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    setup_logging(args.verbose)
    args.func(args)


if __name__ == "__main__":
    main()
