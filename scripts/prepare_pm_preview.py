#!/usr/bin/env python3
"""Add synthetic PM question and review states to the read-only journey preview."""
import hashlib
import json
from pathlib import Path
import tempfile

from ai_company.contracts import digest
from ai_company.management import ManagementStore


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'docs/previews/journey/fixture.json'
OUTPUT = ROOT / 'docs/previews/pm-requirements/fixture.json'


def project(store, name, goal):
    item = store.create_project({'name': name, 'goal': goal})
    item['source'] = 'fixture'
    with store.db:
        store.db.execute('UPDATE management_projects SET document=? WHERE id=?',
                         (json.dumps(item, ensure_ascii=False), item['id']))
    return item


def main():
    fixture = json.loads(BASE.read_text(encoding='utf-8'))
    fixture['provenance']['pm_scenarios'] = 'synthetic temporary management store; no model, approval or execution'
    with tempfile.TemporaryDirectory(prefix='pm-preview-') as directory:
        store = ManagementStore(Path(directory))
        try:
            asking = project(store, 'PM 질문 · 예시', '초보자가 목표를 말하면 AI가 일을 나누는 서비스')
            message = store.post_message(asking['id'], {'content': '첫 화면에서 그래프를 직접 연결하게 해 주세요.'})
            request = store.get_pm_request(message['id'])
            feedback = {'version': 2, 'revision': request['request_revision'], 'goal_digest': request['goal_digest'],
                'problem': '초보자가 목표를 말하고 일을 맡깁니다.',
                'users_and_flow': '처음 쓰는 사용자가 목표를 입력한 뒤 진행을 확인합니다.',
                'scope': ['첫 화면의 시작 방식 결정'], 'exclusions': ['개발 실행과 배포'], 'assumptions': [],
                'questions': [{'id': 'Q1', 'prompt': '첫 버전에서 맡길 대표 업무는 무엇인가요?',
                    'reason': '대표 업무에 따라 필요한 화면과 완료 조건이 달라집니다.',
                    'options': ['웹사이트 제작', '여러 종류의 프로젝트'],
                    'recommendation': '첫 버전은 웹사이트 제작부터 시작하는 안을 권합니다.',
                    'status': 'open'}],
                'findings': [{'id': 'F1', 'evidence': '그래프 연결은 역할·순서를 먼저 이해해야 합니다.',
                    'impact': '초보자가 목표 입력 전에 막힐 수 있습니다.',
                    'alternatives': ['목표 입력부터 시작하고 그래프는 진행 확인에 사용합니다.'],
                    'recommendation': '목표 입력으로 시작하는 구성을 권합니다.', 'blocking': False, 'status': 'open'}],
                'requirements': [{'id': 'R1', 'source': '사용자 목표', 'acceptance': '목표 입력으로 시작합니다.',
                    'verification': '새 사용자가 첫 화면에서 목표 입력을 찾는지 확인합니다.',
                    'role_keys': ['planning']}]}
            request.update(state='answer_needed', requirements_feedback=feedback,
                           reason='대표 업무에 관한 사용자 결정 대기')
            store.save_pm_request(request)
            reply = {'id': 'pm-feedback-' + request['request_id'], 'role': 'assistant',
                     'content': '목표 입력으로 시작하고 그래프는 진행 확인에 쓰는 구성을 권합니다.',
                     'status': 'answer_needed', 'request_id': request['request_id'],
                     'created_at': request['created_at'] + 1, 'source': 'fixture'}
            with store.db:
                store.db.execute('INSERT INTO management_messages VALUES (?,?,?)',
                                 (reply['id'], asking['id'], json.dumps(reply, ensure_ascii=False)))

            reviewing = project(store, '계획 검토 · 예시', '로그인 화면과 검사를 독립적으로 준비합니다.')
            message = store.post_message(reviewing['id'], {'content': '로그인 화면과 검사를 병렬로 진행할 계획을 제안해 주세요.'})
            request = store.get_pm_request(message['id'])
            store.save_pm_request({**request, 'state': 'running', 'configuration_digest': 'c' * 64,
                                   'mode': 'fixture'}, expected_state='pending')
            content = {'summary': '화면과 검사를 분리하고 결과를 함께 확인합니다.',
                'roles': [{'key': key, 'name': name, 'responsibility': duty, 'goal': duty,
                    'acceptance': [acceptance], 'allowed_paths': [path], 'depends_on': []}
                    for key, name, duty, acceptance, path in (
                        ('ui', '화면', '로그인 화면을 구현합니다.', '모바일에서 입력과 오류를 확인합니다.', 'src/ai_company/web/login/'),
                        ('tests', '검사', '로그인 검사를 만듭니다.', '성공·실패 사례를 검사합니다.', 'tests/login/'))],
                'completion_criteria': ['화면과 검사의 결과를 같은 후보에서 확인합니다.'],
                'requirements_review': {'version': 2, 'revision': request['request_revision'],
                    'goal_digest': digest(reviewing['goal']), 'problem': '로그인을 쉽고 정확하게 만듭니다.',
                    'users_and_flow': '사용자가 아이디와 비밀번호를 입력해 작업실에 들어옵니다.',
                    'scope': ['로그인 화면', '로그인 검사'], 'exclusions': ['운영 배포'],
                    'assumptions': ['기존 인증 API를 유지합니다.'], 'questions': [], 'findings': [],
                    'requirements': [{'id': 'R1', 'source': '사용자 목표',
                        'acceptance': '사용자가 모바일에서 로그인할 수 있습니다.',
                        'verification': '격리 브라우저에서 성공·실패 입력과 새로고침을 확인합니다.',
                        'role_keys': ['ui', 'tests']}]}}
            store.complete_pm_request(message['id'], content, evidence={'source': 'fixture',
                'session_id': 'fixture-pm-plan', 'provider': 'codex',
                'verification_level': 'synthetic PM plan; independent review not run'})
            rows = store.list_projects(summary=True)
            for row in rows:
                raw = store.overview(row['id'])
                fixture['projects'].append(row)
                fixture['overviews'][row['id']] = {key: raw[key] for key in raw if key in (
                    'project', 'roles', 'tasks', 'reports', 'approvals', 'messages', 'harnesses', 'readiness',
                    'pm_requests', 'plans', 'runs', 'workers', 'project_report', 'documents',
                    'translation_summary', 'collaboration', 'workspace_graph', 'execution_specs')}
        finally:
            store.close()
    raw = json.dumps(fixture, ensure_ascii=False, indent=2) + '\n'
    for forbidden in ('/home/', '/tmp/', 'access_token', 'refresh_token', 'password_hash', 'private_key', 'client_secret'):
        if forbidden in raw:
            raise ValueError('Non-public preview field: ' + forbidden)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(raw, encoding='utf-8')
    print(json.dumps({'fixture': str(OUTPUT.relative_to(ROOT)), 'sha256': hashlib.sha256(raw.encode()).hexdigest()}, ensure_ascii=False))


if __name__ == '__main__':
    main()
