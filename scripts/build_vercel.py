"""Prepare the static assets used by the Vercel deployment."""
from argparse import ArgumentParser
from pathlib import Path
from shutil import copy2


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'static'
PUBLIC = ROOT / 'public'
ASSETS = ('app.js', 'styles.css')


def build_assets() -> None:
    PUBLIC.mkdir(exist_ok=True)
    for name in ASSETS:
        source = STATIC / name
        if not source.is_file():
            raise FileNotFoundError(f'Missing Vercel asset: {source}')
        copy2(source, PUBLIC / name)


def main() -> None:
    parser = ArgumentParser(description='Build static assets for Vercel.')
    parser.add_argument('--assets-only', action='store_true',
                        help='Copy CDN assets without copying index.html.')
    args = parser.parse_args()
    if not args.assets_only:
        parser.error('Vercel builds must use --assets-only so / remains on the Flask function.')
    build_assets()
    print(f'Copied {len(ASSETS)} assets to {PUBLIC}')


if __name__ == '__main__':
    main()
