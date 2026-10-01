"""Package the isolated task-first fixture without external resources."""
from pathlib import Path
import argparse


def package(output: Path):
    source = Path(__file__).resolve().parents[1] / "docs/previews/task-workspace"
    html = (source / "index.html").read_text()
    html = html.replace('<link rel="stylesheet" href="./style.css">', '<style>\n' + (source / "style.css").read_text() + '\n</style>')
    html = html.replace('<script defer src="./preview.js"></script>', '')
    html = html.replace('</body>', '<script>\n' + (source / "preview.js").read_text() + '\n</script></body>')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    package(parser.parse_args().output)
