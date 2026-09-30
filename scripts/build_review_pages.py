"""Build two public, immutable-source review documents; never read runtime state."""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from urllib.parse import urlsplit
import zipfile

from markdown_it import MarkdownIt

REPO = Path(__file__).resolve().parents[1]
REPOSITORY = 'https://github.com/HyungwonPark/ai-company'
DOCUMENTS = {'index': 'docs/review/index.md', 'manual': 'docs/review/manual.md'}
HEX = re.compile(r'[0-9a-f]{40}\Z')
CSS = (REPO / 'scripts/review_pages.css').read_text()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_source(commit: str, path: str) -> bytes:
    if not HEX.fullmatch(commit):
        raise ValueError('A full document commit is required')
    return subprocess.check_output(['git', 'show', f'{commit}:{path}'], cwd=REPO)


def heading_slug(title: str) -> str:
    return re.sub(r'[^\w\- ]', '', title.lower()).replace(' ', '-')


def link_target(target: str, page: str, commit: str, preview: bool) -> str:
    if target.startswith('#'):
        return target
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme != 'https' or parsed.hostname not in ('github.com', 'docs.github.com', 'learn.chatgpt.com') or parsed.username or parsed.password:
            raise ValueError('Unsupported document link')
        if parsed.hostname == 'github.com' and not re.match(r'/HyungwonPark/ai-company/(?:blob|tree)/[0-9a-f]{40}/', parsed.path):
            raise ValueError('Evidence links must use a full immutable commit')
        return target
    if parsed.query or '\\' in target:
        raise ValueError('Unsupported relative document link')
    aliases = {'index.md': 'index', 'manual.md': 'manual'}
    if parsed.path in aliases:
        name = aliases[parsed.path]
        result = f'{name}.html' if preview else {'index': '/review', 'manual': '/review/manual'}[name]
    else:
        parts = list(PurePosixPath('docs/review', parsed.path).parts)
        normalized: list[str] = []
        for part in parts:
            if part == '..':
                if not normalized:
                    raise ValueError('Document link leaves repository')
                normalized.pop()
            elif part != '.':
                normalized.append(part)
        if not normalized or normalized[0] != 'docs' or parsed.path.startswith('/'):
            raise ValueError('Document link leaves public documentation')
        result = f'{REPOSITORY}/blob/{commit}/' + '/'.join(normalized)
    return result + (f'#{parsed.fragment}' if parsed.fragment else '')


def render(source: str, page: str, commit: str, target_code: str, preview: bool) -> str:
    # Native navigation replaces the manual's duplicate written table of contents.
    source = re.sub(r'^## 목차\n.*?(?=^## |\Z)', '', source, flags=re.M | re.S)
    md = MarkdownIt('commonmark', {'html': False}).enable('table')
    tokens = md.parse(source)
    headings, used = [], {}
    for index, token in enumerate(tokens):
        if token.type == 'heading_open':
            title = tokens[index + 1].content
            base = heading_slug(title)
            count = used.get(base, 0)
            used[base] = count + 1
            identifier = base + (f'-{count}' if count else '')
            token.attrSet('id', identifier)
            if token.tag == 'h2':
                headings.append((identifier, title))
        if token.children:
            for child in token.children:
                if child.type == 'link_open':
                    child.attrSet('href', link_target(child.attrGet('href') or '', page, commit, preview))
                    if (child.attrGet('href') or '').startswith('https://'):
                        child.attrSet('rel', 'noreferrer')
                elif child.type == 'image':
                    raise ValueError('External or embedded document images are not allowed')
    # Mermaid remains available as escaped source without downloading an executable renderer.
    def fence(tokens, index, options, env):
        token = tokens[index]
        code = '<pre><code>' + html.escape(token.content) + '</code></pre>'
        if token.info.strip() == 'mermaid':
            return '<details class="diagram-source"><summary>흐름 도식 원문</summary>' + code + '</details>\n'
        return code + '\n'
    md.renderer.rules['fence'] = fence
    body = md.renderer.render(tokens, md.options, {})
    toc = ''.join(f'<li><a href="#{html.escape(identifier)}">{html.escape(title)}</a></li>' for identifier, title in headings)
    navigation = {'index': 'index.html', 'manual': 'manual.html'} if preview else {'index': '/review', 'manual': '/review/manual'}
    nav = ''.join(f'<a href="{url}"' + (' aria-current="page"' if name == page else '') + f'>{label}</a>' for name, url, label in [('index', navigation['index'], '현황'), ('manual', navigation['manual'], '매뉴얼')])
    title = '작업 현황' if page == 'index' else '운영·개발 매뉴얼'
    source_link = f'{REPOSITORY}/blob/{commit}/{DOCUMENTS[page]}'
    return f'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><meta name="referrer" content="no-referrer"><title>{title} · AI Company</title><style>{CSS}</style></head>
<body><a class="skip" href="#document">본문으로</a><header class="masthead"><a class="brand" href="{navigation['index']}">AI Company <span>Review</span></a><nav aria-label="문서">{nav}</nav><fieldset class="theme"><legend>테마</legend><label><input id="theme-light" type="radio" name="theme" checked>Light</label><label><input id="theme-black" type="radio" name="theme">Black</label></fieldset></header>
<div class="reading"><aside><details class="contents"><summary>목차</summary><ol>{toc}</ol></details><p class="read-only">읽기 전용 · 마지막 보고 기준<br>실시간 상태가 아닙니다.</p></aside><main id="document" tabindex="-1"><div class="provenance"><a href="{source_link}" rel="noreferrer">문서 원본 · {commit[:7]}</a><details><summary>버전</summary><dl><dt>문서 커밋</dt><dd>{commit}</dd><dt>설치 기준 코드</dt><dd><a href="{REPOSITORY}/commit/{target_code}" rel="noreferrer">{target_code}</a></dd></dl><p>문서 기준일과 관측·보고 시각은 아래 원문을 따릅니다. 생성 시각은 관측 시각을 갱신하지 않습니다.</p></details></div><article>{body}</article><footer>AI Company · 공개 문서 · 승인·실행 기능 없음</footer></main></div></body></html>'''


def build(commit: str, target_code: str, output: Path) -> dict:
    if not HEX.fullmatch(commit) or not HEX.fullmatch(target_code):
        raise ValueError('Full document and target code commits are required')
    if output.exists():
        raise ValueError('Output must be a new directory; keep the current publication untouched')
    sources = {page: git_source(commit, filename) for page, filename in DOCUMENTS.items()}
    files: dict[str, bytes] = {}
    for page, data in sources.items():
        text = data.decode('utf-8')
        if not re.search(r'\[최종 수정일: \d{4}-\d{2}-\d{2}, v[\d.]+\]', text):
            raise ValueError('Document date and version must be present')
        for preview in (False, True):
            name = f'preview/{page}.html' if preview else {'index': 'site/index.html', 'manual': 'site/manual/index.html'}[page]
            files[name] = render(text, page, commit, target_code, preview).encode('utf-8')
    csp_hash = base64.b64encode(hashlib.sha256(CSS.encode()).digest()).decode()
    files['review-routes.caddy'] = ('''@review_documents path /review /review/*
handle @review_documents {
    route {
        @review_write not method GET HEAD
        respond @review_write 405
        header Cache-Control "no-store"
        header X-Content-Type-Options "nosniff"
        header Referrer-Policy "no-referrer"
        header Content-Security-Policy "default-src 'none'; style-src 'sha256-''' + csp_hash + ''''; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
        @review_index path /review /review/
        rewrite @review_index /index.html
        @review_manual path /review/manual /review/manual/
        rewrite @review_manual /manual/index.html
        @review_generated path /index.html /manual/index.html
        file_server @review_generated {
            root /data/review-documents/current
        }
        respond "문서를 찾을 수 없습니다." 404
    }
}
''').encode()
    manifest = {
        'schema_version': 1, 'document_commit': commit, 'target_code_commit': target_code,
        'access': 'public reviewed documentation only; no authentication or live state',
        'sources': {DOCUMENTS[p]: digest(b) for p, b in sources.items()},
        'generator_sha256': digest(Path(__file__).read_bytes()), 'stylesheet_sha256': digest(CSS.encode()),
        'files': {p: digest(b) for p, b in files.items()}, 'model_calls': 0,
    }
    files['manifest.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()
    output.mkdir(parents=True)
    for name, data in files.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    with zipfile.ZipFile(output / 'AI-Company-review-preview.zip', 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--target-code-commit', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.source_commit, args.target_code_commit, args.output)
    print(json.dumps({'status': 'BUILT', 'document_commit': args.source_commit,
                      'zip_sha256': digest((args.output / 'AI-Company-review-preview.zip').read_bytes())}))
