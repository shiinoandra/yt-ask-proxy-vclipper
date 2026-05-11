"""Allow the package to be executed with ``python -m youtube_ask_proxy``."""

from youtube_ask_proxy.main import main

if __name__ == "__main__":
    raise SystemExit(main())
