"""CLI entry points for console_scripts."""

from __future__ import annotations


def main():
    # Root chapterfit.py remains the implementation host for argparse/cmd_*.
    import chapterfit as cf
    cf.main()


if __name__ == "__main__":
    main()
