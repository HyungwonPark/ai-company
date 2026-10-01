#!/usr/bin/env python3
"""Build a standalone, offline review page; never publish or contact services."""
from pathlib import Path
import argparse


def package(destination: Path) -> None:
    web = Path(__file__).resolve().parents[1] / 'src/ai_company/web/workspace-preview'
    html = (web / 'index.html').read_text()
    html = html.replace('<link rel="stylesheet" href="./preview.css">', '<style>\n' + (web / 'preview.css').read_text() + '\n</style>')
    for name in ('data.js', 'model.js', 'preview.js'):
        html = html.replace(f'<script defer src="./{name}"></script>', '')
    scripts = '\n'.join((web / name).read_text() for name in ('data.js', 'model.js', 'preview.js'))
    html = html.replace('</body>', '<script>\n' + scripts + '\n</script>\n</body>')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(html)
    print(f'Offline preview: {destination} ({destination.stat().st_size} bytes)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    package(parser.parse_args().output)
