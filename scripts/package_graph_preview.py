#!/usr/bin/env python3
"""Package the shared graph renderer and sanitized fixture for offline review."""
import argparse
import json
from pathlib import Path


def package(output):
    web = Path(__file__).resolve().parents[1] / 'src/ai_company/web'
    preview = web / 'graph-preview'
    html = (preview / 'index.html').read_text()
    for href in ('../workspace-graph.css', './preview.css'):
        css = (preview / href).read_text()
        html = html.replace(f'<link rel="stylesheet" href="{href}">', '<style>' + css + '</style>')
    fixture = json.loads((preview / 'fixture.json').read_text())
    assert fixture['fixture'] and all(s['source'] == 'fixture' for s in fixture['workspace_graph']['snapshots'])
    data = json.dumps(fixture, ensure_ascii=False).replace('<', '\\u003c')
    renderer = (web / 'workspace-graph-ui.js').read_text().replace('export function ', 'function ')
    script = (preview / 'preview.js').read_text().replace("import {createWorkspaceGraph} from '../workspace-graph-ui.js';", '')
    # The fixed data short-circuits fetch even when opened through file://.
    script = f'window.GRAPH_FIXTURE={data};\n' + renderer + '\n' + script
    html = html.replace('<script type="module" src="./preview.js"></script>', '<script type="module">' + script + '</script>')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html)
    print(f'Offline graph preview: {output} ({output.stat().st_size} bytes)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    package(parser.parse_args().output)
