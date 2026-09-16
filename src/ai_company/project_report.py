"""Read-only, current-plan progress synthesis. Never a PM verdict or approval."""
from ai_company.contracts import digest

DONE = {'CONTRIBUTION_READY', 'MERGE_READY', 'COMPLETE', 'COMPLETED', 'DONE'}
NEEDS_OPERATOR = {'BLOCKED', 'FAILED', 'STOPPED', 'RECONCILIATION_REQUIRED', 'NEEDS_RECONCILIATION', 'NEEDS_CONTEXT_HANDOFF'}
BLOCKED = NEEDS_OPERATOR | {'WAITING_ROLE_REPAIR',
           'WAITING_QUOTA', 'WAITING_RETRY', 'WAITING_CAPACITY', 'WAITING_DEPENDENCIES', 'WAITING_DEPENDENCY'}


def project_report(overview):
    project = overview['project']
    plans = [p for p in overview.get('plans', []) if p['status'] != 'stale'
             and p.get('request_revision') == project.get('request_revision')]
    plan = plans[-1] if plans else None
    runs = [r for r in overview.get('runs', []) if plan and r['plan_id'] == plan['id']
            and r['plan_digest'] == plan['digest']]
    run = runs[-1] if runs else None
    requests = [r for r in overview.get('pm_requests', [])
                if r.get('request_revision') == project.get('request_revision')]
    request = requests[-1] if requests else None
    tasks = {t['id']: t for t in overview.get('tasks', [])}
    result = dict(source='system', scope='current_plan', project_id=project['id'], goal=project.get('goal', ''),
                  goal_digest=digest(project.get('goal', '')), request_revision=project.get('request_revision'),
                  plan_id=plan['id'] if plan else None, plan_digest=plan['digest'] if plan else None,
                  run_id=run['id'] if run else None, mode=run.get('mode') if run else project.get('source'),
                  completed=[], in_progress=[], blockers=[], next_actions=[], decisions=[],
                  completion_criteria=(plan or {}).get('content', {}).get('completion_criteria', []),
                  goal_assessment='not_assessed', candidate_verified=False,
                  pm_report_ids=[r['id'] for r in overview.get('reports', []) if r.get('source') == 'pm'])
    if plan:
        for index, role in enumerate(plan['content'].get('roles', [])):
            saved = (run or {}).get('roles', {}).get(role['key'], {})
            task = tasks.get(saved.get('task_id'), {})
            status = task.get('status') or saved.get('status') or ('READY' if run else 'PLAN_PENDING')
            item = dict(role_key=role['key'], role_index=index, title=role.get('name', role['key']),
                        task_id=task.get('id') or saved.get('task_id'), status=status,
                        stage=task.get('stage') or saved.get('stage'),
                        reason=task.get('wait_reason') or saved.get('reason'),
                        resume_at=task.get('resume_at') if task else saved.get('resume_at'),
                        candidate_sha=task.get('candidate_sha') or saved.get('candidate_sha'))
            result['completed' if status in DONE else 'in_progress'].append(item)
            if status in BLOCKED or status.startswith('WAITING_'):
                result['blockers'].append(item)
        if plan.get('confirmed_at') is not None:
            result['decisions'].append(dict(kind='plan_confirmed', at=plan['confirmed_at'], plan_id=plan['id'],
                plan_digest=plan['digest'], delegated=bool((run or {}).get('delegation_id'))))
    integration = (run or {}).get('integration') or {}
    candidate = integration.get('candidate_sha') or (run or {}).get('candidate_sha')
    result['candidate_sha'] = candidate
    if integration:
        task = tasks.get(integration.get('task_id'), {})
        status = task.get('status') or integration.get('status')
        result['verification'] = dict(task_id=integration.get('task_id'), status=status,
            stage=task.get('stage') or integration.get('stage'), candidate_sha=candidate,
            pr_url=integration.get('pr_url'), reason=task.get('wait_reason') or integration.get('reason'),
            resume_at=task.get('resume_at') if task else integration.get('resume_at'))
        if status in BLOCKED or (status or '').startswith('WAITING_'):
            result['blockers'].append(dict(result['verification'], title='통합 검수'))
        # MERGE_READY is Dispatcher-owned evidence acceptance, not goal satisfaction.
        # It must be the exact integration task and candidate in this same run.
        result['candidate_verified'] = bool(run.get('mode') == 'live' and status == 'MERGE_READY'
            and task and candidate and task.get('candidate_sha') == candidate)
    related = [a for a in overview.get('approvals', []) if candidate and a.get('artifact_sha') == candidate
               and a.get('id') == (run or {}).get('approval_id')]
    for approval in related:
        result['decisions'].append(dict(kind='candidate', approval_id=approval['id'],
            status=approval['status'], candidate_sha=candidate, comment=approval.get('comment'),
            at=approval.get('decided_at'), subject_digest=approval.get('subject_digest')))
    if not plan:
        if request and request['state'] in ('blocked', 'waiting_quota', 'waiting_retry'):
            result['blockers'].append(dict(title='PM', status=request['state'],
                reason=request.get('wait_reason') or request.get('reason') or (request.get('execution') or {}).get('reason'),
                resume_at=request.get('resume_at') or (request.get('execution') or {}).get('resume_at')))
        result['next_actions'].append(dict(owner='master' if not request else 'pm',
            text='목표를 입력하세요.' if not request else 'PM 제안을 기다립니다.', view='manager'))
    elif plan['status'] == 'proposed':
        result['next_actions'].append(dict(owner='master', text='역할·완료 조건을 확인하고 계획을 확정하세요.', view='manager'))
    elif any(a['status'] == 'pending' for a in related):
        result['next_actions'].append(dict(owner='master', text='후보와 검수 근거를 확인하고 수용 여부를 결정하세요.', view='approvals'))
    elif any(a['status'] == 'expired' for a in related):
        result['next_actions'].append(dict(owner='operator', text='만료된 승인 요청의 대상과 조건을 다시 검토하세요.', view='approvals'))
    elif any(item['status'] in NEEDS_OPERATOR for item in result['blockers']):
        result['next_actions'].append(dict(owner='operator', text='차단 사유와 실행 기록을 확인하고 재개 조건을 검토하세요.', view='progress'))
    elif result['blockers']:
        result['next_actions'].append(dict(owner='coordinator', text='대기 원인과 예약 시각을 확인합니다. 독립 작업은 계속합니다.', view='progress'))
    elif run and run.get('state') in ('blocked', 'rejected'):
        result['blockers'].append(dict(title='실행', status=run['state'], reason=run.get('reason')))
        result['next_actions'].append(dict(owner='master', text='차단 또는 반려 사유를 확인하고 목표·계획을 다시 검토하세요.', view='manager'))
    elif run and run.get('state') == 'completed':
        result['next_actions'].append(dict(owner='master', text='결과 수용이 기록됐습니다. 배포·병합은 별도 결정입니다.', view='approvals'))
    else:
        result['next_actions'].append(dict(owner='coordinator', text='역할별 산출물과 같은 후보의 검사를 이어갑니다.', view='progress'))
    pending_work = (request and request['state'] not in ('completed', 'stale', 'blocked')) or (run and run.get('state') in ('pending', 'preparing', 'running', 'waiting'))
    if pending_work and 'workers' in overview and overview['workers'].get('automation', {}).get('state') not in ('busy', 'idle', 'starting'):
        result['blockers'].append(dict(title='실행기', status='WORKER_UNCONFIRMED', reason='PM·조정기의 최근 가동 신호가 없습니다.'))
        result['next_actions'].insert(0, dict(owner='operator', text='실행기 가동 상태를 확인하세요.', view='manager'))
    if not plan and request and request['state'] == 'blocked':
        result['next_actions'] = [dict(owner='operator', text='PM 차단 사유를 확인하고 실행 설정을 검토하세요.', view='manager')]
    result['digest'] = digest(result)
    return result
