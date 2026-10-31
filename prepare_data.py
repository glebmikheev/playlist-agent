import argparse
import urllib.request
from pathlib import Path

from playlist_agent.catalogue import SOURCE_URL, prepare_catalogue


def main():
    parser = argparse.ArgumentParser(
        description="Prepare the pinned Spotify track catalogue."
    )
    parser.add_argument(
        "--source", type=Path, default=Path("data/raw/spotify_tracks_hf.csv")
    )
    parser.add_argument("--output", type=Path, default=Path("data/catalogue.sqlite"))
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()

    if args.download and not args.source.exists():
        args.source.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(SOURCE_URL, timeout=60) as response:
            args.source.write_bytes(response.read())
    print(prepare_catalogue(args.source, args.output))


if __name__ == "__main__":
    main()
