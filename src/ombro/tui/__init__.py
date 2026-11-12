"""The terminal interface (stage 10). A layer over the core: it reads views,
drives chat and interviews through ombro.ui, and adds nothing the command
line lacks. Textual is imported only here, so runs and `ombro tick` never
load it."""


def main() -> int:
    from ombro.tui.app import run_app

    return run_app()
