#!/usr/bin/env python3
"""Bundle the actual journey UI with frozen synthetic GET responses for offline review.

No production input is accepted. --refresh-fixture creates a new temporary database
using the existing graph browser fixture and exports only the public UI projection.
Ordinary packaging reads the committed fixture; it does not open any database.
"""
import argparse
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'src/ai_company/web'
FIXTURE = ROOT / 'docs/previews/journey/fixture.json'
OVERVIEW_FIELDS = (
    'project', 'roles', 'tasks', 'reports', 'approvals', 'messages', 'harnesses',
    'readiness', 'pm_requests', 'plans', 'runs', 'workers', 'project_report',
    'documents', 'translation_summary', 'collaboration', 'workspace_graph',
    'execution_specs',
)
PROJECT_FIELDS = (
    'id', 'name', 'goal', 'status', 'harness_version', 'request_revision', 'source',
    'created_at', 'updated_at', 'pending_approval_count', 'recent_run', 'recent_pm_request',
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def data_url(data, mime):
    return 'data:' + mime + ';base64,' + base64.b64encode(data).decode('ascii')


def refresh_fixture():
    from ai_company.management import ManagementStore

    spec = importlib.util.spec_from_file_location('journey_fixture_seed', ROOT / 'tests/ui/run_workspace_graph.py')
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    with tempfile.TemporaryDirectory(prefix='ai-company-public-preview-') as directory:
        state = Path(directory)
        store = ManagementStore(state)
        try:
            ids = seed.seed_graph(store, state)
            projects = [{key: row[key] for key in PROJECT_FIELDS if key in row}
                        for row in store.list_projects(summary=True)]
            overviews = {}
            for project in projects:
                raw = store.overview(project['id'])
                overview = {key: raw[key] for key in OVERVIEW_FIELDS if key in raw}
                # These are synthetic clone locations, still unnecessary for public review.
                for task in overview['tasks']:
                    task.pop('worktree', None)
                overviews[project['id']] = overview
            fixture = {
                'fixture': True,
                'read_only': True,
                'provenance': {
                    'generator': 'tests/ui/run_workspace_graph.py:seed_graph',
                    'generator_sha256': digest((ROOT / 'tests/ui/run_workspace_graph.py').read_bytes()),
                    'database': 'new temporary directory; removed after export',
                    'scope': 'synthetic fixture only; no live API, models, credentials or production records',
                    'timestamps': 'frozen API observation; approval expiry is evaluated by the unmodified product UI',
                },
                'fixtures': ids,
                'projects': projects,
                'overviews': overviews,
                'execution_catalog': {'entries': []},
            }
        finally:
            store.close()
    raw = json.dumps(fixture, ensure_ascii=False, indent=2) + '\n'
    # Fail rather than accidentally publish an operational path or credential field.
    for forbidden in ('/home/', '/tmp/', 'access_token', 'refresh_token', 'password_hash', 'private_key', 'client_secret'):
        if forbidden in raw:
            raise ValueError('Non-public fixture field: ' + forbidden)
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(raw, encoding='utf-8')
    print('Created synthetic fixture:', FIXTURE.relative_to(ROOT), digest(raw.encode()))


ADAPTER = r"""
(() => {
  'use strict';
  const fixture = JSON.parse(document.getElementById('preview-fixture').textContent);
  const audit = []; window.PREVIEW_AUDIT = audit;
  const message = '읽기 전용 미리보기입니다. 실제 저장·모델 실행·승인 요청은 하지 않습니다.';
  const response = (value, status=200) => new Response(JSON.stringify(value), {status, headers:{'Content-Type':'application/json'}});
  function notice(text) { document.getElementById('preview-notice').textContent = text; }
  window.fetch = async (input, options={}) => {
    const url = new URL(typeof input==='string' ? input : input.url, 'https://offline-preview.invalid');
    const method = (options.method || input.method || 'GET').toUpperCase();
    audit.push({method, path:url.pathname});
    if(method!=='GET') { notice(message); return response({error:{code:'preview_read_only', message}},405); }
    if(url.origin!=='https://offline-preview.invalid') return response({error:{code:'preview_external',message:'외부 연결은 이 파일에서 지원하지 않습니다.'}},404);
    if(url.pathname==='/api/session') return response({authenticated:true,username:'예시 마스터',login_method:'password',csrf_token:'',password_change_required:false});
    if(url.pathname==='/api/projects') return response({projects:fixture.projects});
    if(url.pathname==='/api/execution-catalog') return response(fixture.execution_catalog);
    const match = /^\/api\/projects\/([^/]+)\/(overview|execution-specs)$/.exec(url.pathname);
    if(match) {
      const overview = fixture.overviews[decodeURIComponent(match[1])];
      if(overview) return response(match[2]==='overview' ? overview : {execution_specs:overview.execution_specs||[]});
    }
    return response({error:{code:'not_found',message:'이 미리보기에 포함되지 않은 예시입니다.'}},404);
  };
  // Remove unsupported hrefs, including after product re-renders. Preventing
  // ordinary clicks alone would leave middle-click and "open in new tab" active.
  function disableLink(link) {
    const href=link.getAttribute('href');
    if(href!==null && !href.startsWith('#')) {
      link.removeAttribute('href');link.removeAttribute('target');
      link.dataset.previewUnavailable='true';link.setAttribute('aria-disabled','true');
      link.setAttribute('role','link');link.setAttribute('tabindex','0');
      link.setAttribute('title','별도 그림·외부 링크는 읽기 전용 미리보기에 포함되지 않습니다.');
    }
  }
  function disableExternalLinks() { document.querySelectorAll('a[href]').forEach(disableLink); }
  const linksObserver=new MutationObserver(disableExternalLinks);
  linksObserver.observe(document.documentElement,{childList:true,subtree:true,attributes:true,attributeFilter:['href']});
  disableExternalLinks();
  for(const type of ['click','auxclick','contextmenu']) document.addEventListener(type, event => {
    const link=event.target.closest('a');if(!link)return;
    disableLink(link);
    if(link.dataset.previewUnavailable==='true') {
      event.preventDefault(); event.stopImmediatePropagation();
      notice('별도 그림·외부 링크는 이 파일에 포함되지 않습니다. 진행·결과·승인은 화면 안에서 확인하세요.');
    }
  }, true);
})();
"""


def package(output):
    sources = {}

    def read(path):
        path = path.resolve()
        if not path.is_relative_to(ROOT):
            raise ValueError('Asset outside repository')
        raw = path.read_bytes()
        sources[str(path.relative_to(ROOT))] = digest(raw)
        return raw

    fixture_bytes = read(FIXTURE)
    fixture = json.loads(fixture_bytes)
    if fixture.get('fixture') is not True or fixture.get('read_only') is not True:
        raise ValueError('Only the fixed synthetic read-only fixture is permitted')
    if any(row.get('source') != 'fixture' for row in fixture['projects']):
        raise ValueError('Project fixture provenance missing')
    read(Path(__file__))
    original_graph_fixture = read(WEB / 'graph-preview/fixture.json')
    modules = {}
    active = set()

    def module(path):
        path = path.resolve()
        if path in modules:
            return modules[path]
        if path in active:
            raise ValueError('Cyclic module import needs a separate bundle design')
        active.add(path)
        source = read(path).decode('utf-8')
        # Native module boundaries are preserved. Only local static import URLs
        # change; declarations, module state and product behavior stay intact.
        def imported(match):
            target = match.group(2)
            if not target.startswith('./') and not target.startswith('../'):
                raise ValueError('Unsupported non-local module import: ' + target)
            return match.group(1) + module(path.parent / target) + match.group(3)
        source = re.sub(r"(^\s*(?:import|export)\s+[^;\n]*?\bfrom\s*['\"])([^'\"]+)(['\"])", imported, source, flags=re.MULTILINE)
        if re.search(r'\bimport\s*\(', source) or re.search(r"\bimport\s*['\"]", source):
            raise ValueError('Dynamic or side-effect import requires an explicit bundler rule')
        active.remove(path)
        modules[path] = data_url(source.encode(), 'text/javascript')
        return modules[path]

    html = read(WEB / 'index.html').decode('utf-8')
    html = html.replace('<link rel="manifest" href="/manifest.webmanifest">', '')
    icon = data_url(read(WEB / 'icon.svg'), 'image/svg+xml')
    html = html.replace('href="/icon.svg"', 'href="' + icon + '"')
    for name in re.findall(r'<link rel="stylesheet" href="/([^\"]+)">', html):
        path = WEB / name
        css = read(path).decode('utf-8')
        def asset(match):
            target = match.group(1).strip('"\'')
            return 'url(' + data_url(read(path.parent / target), 'font/woff2') + ')'
        css = re.sub(r'url\(([^)]+)\)', asset, css)
        html = html.replace(f'<link rel="stylesheet" href="/{name}">', '<style>' + css + '</style>')
    html = html.replace('<script src="/theme.js"></script>', '<script src="' + data_url(read(WEB / 'theme.js'), 'text/javascript') + '"></script>')
    entry = module(WEB / 'app.js')
    data = json.dumps(fixture, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    bootstrap = '<script id="preview-fixture" type="application/json">' + data + '</script>\n<script>' + ADAPTER + '</script>\n'
    html = html.replace('<script type="module" src="/app.js"></script>', bootstrap + '<script type="module" src="' + entry + '"></script>')
    # Also stops the unchanged product's optional service-worker registration.
    policy = "default-src 'none'; script-src 'unsafe-inline' data:; style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; worker-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
    html = html.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n  <meta http-equiv="Content-Security-Policy" content="' + policy + '">')
    banner = '<aside id="preview-banner" aria-label="미리보기 범위"><strong>예시 · 읽기 전용</strong><span>실제 API·모델·승인 아님</span><p id="preview-notice" role="status">프로젝트 → 계획 → 진행 → 승인 → 결과를 확인하세요. 저장 동작은 차단됩니다.</p></aside>'
    html = html.replace('<body>', '<body>\n' + banner)
    font_licenses = read(WEB / 'fonts/licenses.html').decode('utf-8').partition('<body>')[2].rpartition('</body>')[0]
    html = html.replace('</body>', '<details class="preview-license"><summary>글꼴 라이선스</summary>' + font_licenses + '</details>\n</body>')
    html = html.replace('</head>', '<style>#preview-banner{position:relative;z-index:60;padding:12px 18px;border-bottom:1px solid #697469;background:#edf3e9;color:#233520;font:14px/1.6 system-ui,sans-serif}#preview-banner strong{margin-right:12px}#preview-banner p{margin:3px 0 0}html[data-theme=black] #preview-banner{background:#202d24;color:#dce8dd}.preview-license{padding:18px}.preview-license pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>\n</head>')
    manifest = {
        'schema_version': 1,
        'kind': 'offline read-only product preview',
        'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'source_dirty': bool(subprocess.check_output(['git', 'status', '--porcelain', '--', 'src/ai_company/web', str(FIXTURE.relative_to(ROOT)), str(Path(__file__).relative_to(ROOT))], cwd=ROOT, text=True).strip()),
        'sources_sha256': dict(sorted(sources.items())),
        'original_graph_fixture_sha256': digest(original_graph_fixture),
        'fixture_sha256': digest(fixture_bytes),
        'transformations': ['stylesheet/font/icon embedding', 'static relative module URL embedding', 'read-only synthetic fetch boundary', 'preview banner and network/worker CSP'],
        'limitations': ['no real login/API/model/approval operation', 'no writes', 'separate diagram/external pages excluded', 'no APK/service-worker/production verification', 'frozen synthetic timestamps; original product expiry rules apply'],
    }
    metadata = json.dumps(manifest, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    html = html.replace('</body>', '<script id="preview-manifest" type="application/json">' + metadata + '</script>\n</body>')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding='utf-8')
    manifest['html_sha256'] = digest(output.read_bytes())
    output.with_suffix('.manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'file': str(output), 'bytes': output.stat().st_size, 'html_sha256': manifest['html_sha256'], 'fixture_sha256': manifest['fixture_sha256'], 'modules': len(modules)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--refresh-fixture', action='store_true', help='replace only the public synthetic fixture from a fresh temporary DB')
    args = parser.parse_args()
    if args.refresh_fixture:
        refresh_fixture()
    package(args.output)
