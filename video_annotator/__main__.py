"""Entry point: python -m video_annotator [folder_path]"""

import sys

from video_annotator.app import run


def main() -> None:
    """CLI entry point (used by pyproject.toml [project.scripts])."""
    sys.exit(run(sys.argv))


if __name__ == "__main__":
    main()
