"""Project selections narrow an operator-owned execution catalog.

Catalog installation is local-only. HTTP callers cannot supply a clone path,
command, credential, runtime evidence, CI definition, or execution permission.
"""
from collections.abc import Mapping
import copy
import json
from pathlib import Path, PurePosixPath
import re

from pydantic import Field, StrictInt, model_validator

from ai_company.automation_contracts import AutomationConfig
from ai_company.contracts import Contract, Digest, Key, Task, Text, digest
from ai_company.flow_contracts import FlowRole, FlowSpec


class ExecutionSpecError(ValueError):
    pass


class ExecutionBudget(Contract):
    max_cost_usd: float | None = Field(default=None, gt=0, allow_inf_nan=False, strict=True)
    max_runtime_seconds: float | None = Field(default=None, gt=0, le=86400, allow_inf_nan=False, strict=True)
    max_executions: StrictInt | None = Field(default=None, ge=1, le=1000)
    max_repairs: StrictInt | None = Field(default=None, ge=0, le=20)


class ExecutionSpecSelection(Contract):
    catalog_id: Key
    catalog_digest: Digest
    allowed_paths: tuple[Text, ...] | None = Field(default=None, min_length=1, max_length=160)
    candidates: dict[FlowRole, tuple[Key, ...]] = Field(default_factory=dict)
    role_candidates: dict[str, tuple[Key, ...]] = Field(default_factory=dict)
    budget: ExecutionBudget = Field(default_factory=ExecutionBudget)

    @model_validator(mode='after')
    def selection_shape(self):
        if self.allowed_paths is not None:
            if len(self.allowed_paths) != len(set(self.allowed_paths)):
                raise ValueError('Duplicate selected path')
            for path in self.allowed_paths:
                confined_path(path)
        for role, candidates in self.role_candidates.items():
            if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,31}', role):
                raise ValueError('Role candidate key must be a plan role key')
            if not candidates or len(candidates) != len(set(candidates)):
                raise ValueError('Role candidates must be a nonempty ordered set')
        return self


class ExecutionSpecRegistration(Contract):
    base_version: StrictInt = Field(ge=0)
    idempotency_key: str = Field(pattern=r'^[a-zA-Z0-9_.:-]{8,128}$')
    selection: ExecutionSpecSelection


class ExecutionSpecReference(Contract):
    version: StrictInt = Field(ge=1)
    digest: Digest
    catalog_id: Key
    catalog_digest: Digest


def confined_path(value):
    base = value.rstrip('/')
    parts = base.split('/')
    if (not base or PurePosixPath(base).is_absolute() or '\\' in value
            or any(c in value for c in '\x00*?[]{}')
            or str(PurePosixPath(base)) != base
            or any(part in ('', '.', '..', '.git', '.github', '.env') or part.startswith('.env.') for part in parts)):
        raise ExecutionSpecError('Execution paths must be confined, explicit source files or directories')
    return base


def path_subset(path, approved):
    """Directory grants may narrow; file grants never become directory grants."""
    base = confined_path(path)
    for limit in approved:
        parent = confined_path(limit)
        if limit.endswith('/') and (base == parent or base.startswith(parent + '/')):
            return True
        if not path.endswith('/') and base == parent and not limit.endswith('/'):
            return True
    return False


def ordered_subset(selected, approved):
    return bool(selected) and tuple(item for item in approved if item in selected) == tuple(selected)


class ExecutionCatalog:
    """A defensive snapshot loaded from trusted local configuration, never HTTP."""
    def __init__(self, entries: Mapping[str, AutomationConfig]):
        if not isinstance(entries, Mapping):
            raise ExecutionSpecError('Catalog must be an operator-owned mapping')
        self._entries = {}
        for key, value in entries.items():
            if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}', key):
                raise ExecutionSpecError('Invalid catalog identity')
            config = AutomationConfig.model_validate(copy.deepcopy(value.model_dump(mode='json') if isinstance(value, AutomationConfig) else value))
            for path in config.allowed_paths:
                confined_path(path)
            ids = [agent.agent_id for agent in config.agents]
            if len(ids) != len(set(ids)):
                raise ExecutionSpecError('Catalog agents must be unique')
            agents = {agent.agent_id: agent for agent in config.agents}
            for role, candidates in config.policy.candidates.items():
                if not candidates or any(key not in agents or role not in agents[key].roles for key in candidates):
                    raise ExecutionSpecError('Catalog candidate pools must name eligible role profiles')
            # Reuse the actual immutable flow contract without opening a clone,
            # registering a task, or claiming runtime configuration evidence.
            FlowSpec(task=Task(task_id='catalog-validation', goal='Validate operator configuration',
                acceptance=('Existing flow configuration policy',), repository=config.repository,
                base_sha=config.base_sha, allowed_paths=config.allowed_paths,
                required_checks=tuple(config.checks), max_repairs=config.policy.max_repairs),
                worktree=config.source_clone, agents=config.agents, policy=config.policy,
                checks=config.checks, mode=config.mode)
            self._entries[key] = config.model_dump(mode='json')

    @classmethod
    def load(cls, path):
        """Load an explicit local operator file; no URL or model-supplied source."""
        path = Path(path)
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ExecutionSpecError('Execution catalog exceeds the local configuration size limit')
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ExecutionSpecError('Execution catalog contains duplicate keys')
                result[key] = value
            return result
        return cls(json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_object))

    def _config(self, catalog_id):
        try:
            return AutomationConfig.model_validate(copy.deepcopy(self._entries[catalog_id]))
        except KeyError as error:
            raise ExecutionSpecError('Trusted execution catalog entry is unavailable') from error

    def resolve(self, selection):
        selection = ExecutionSpecSelection.model_validate(selection)
        config = self._config(selection.catalog_id)
        if digest(config) != selection.catalog_digest:
            raise ExecutionSpecError('Trusted execution catalog version changed')
        paths = selection.allowed_paths or config.allowed_paths
        if any(not path_subset(path, config.allowed_paths) for path in paths):
            raise ExecutionSpecError('Selected paths exceed the trusted catalog')
        pools = dict(config.policy.candidates)
        for role, requested in selection.candidates.items():
            approved = pools[role]
            if role in ('pm', 'final'):
                if requested != approved:
                    raise ExecutionSpecError('PM and final-review pools are fixed by the operator')
            elif not ordered_subset(requested, approved):
                raise ExecutionSpecError('Candidates must narrow the existing ordered role pool')
            pools[role] = requested
        for candidates in selection.role_candidates.values():
            if not ordered_subset(candidates, pools['developer']):
                raise ExecutionSpecError('Logical role candidates must narrow the selected developer pool')
        policy = config.policy.model_dump(mode='json')
        for name, value in selection.budget.model_dump(exclude_none=True).items():
            approved = getattr(config.policy, name)
            if approved is not None and value > approved:
                raise ExecutionSpecError('Requested budget exceeds the trusted policy')
            policy[name] = value
        policy['candidates'] = pools
        data = config.model_dump(mode='json')
        data.update(allowed_paths=paths, policy=policy)
        # Checks, CI, account groups, model settings, retries and isolation retain
        # the operator definition. A subset request never supplies these fields.
        return AutomationConfig.model_validate(data)

    @staticmethod
    def summary(config, *, role_candidates=None):
        policy = config.policy
        return {
            'repository': config.repository, 'base_branch': config.base_branch, 'base_sha': config.base_sha,
            'allowed_paths': list(config.allowed_paths),
            'checks': {key: command.model_dump(mode='json') for key, command in config.checks.items()},
            'candidates': {role: list(pool) for role, pool in policy.candidates.items()},
            'role_candidates': {key: list(pool) for key, pool in (role_candidates or {}).items()},
            'agents': [{'agent_id': agent.agent_id, 'provider': agent.provider, 'model': agent.model,
                        'reasoning_effort': agent.reasoning_effort, 'ultracode_enabled': agent.ultracode_enabled,
                        'roles': list(agent.roles), 'enabled': agent.enabled,
                        'configuration_status': 'requested'} for agent in config.agents],
            'budget': {key: getattr(policy, key) for key in ('max_cost_usd', 'max_runtime_seconds', 'max_executions', 'max_repairs')},
            'ci': config.ci.model_dump(mode='json'), 'mode': config.mode, 'max_parallel': config.max_parallel,
            'limitations': [
                '카탈로그 범위 검사입니다. 실제 저장소·명령 실행·계정 적격성 검증은 worker가 별도로 수행합니다.',
                '명세 등록은 실행 승인이 아닙니다. 최신 계획을 직접 확정해야 실행합니다.',
                '비용은 실행기가 보고한 사용량 기준입니다. 비용 미상이나 Codex 호출에는 호출 전 정확한 금액 보장을 할 수 없습니다.',
                *(['유한한 비용 상한을 선택하면 현행 실행기는 Codex 후보를 제외합니다. 지정된 Codex PM·최종 검수는 적격 후보 없음으로 차단됩니다.']
                  if policy.max_cost_usd is not None else []),
            ],
        }

    def public_entries(self):
        return [{'catalog_id': key, 'catalog_digest': digest(config), **self.summary(config)}
                for key in self._entries for config in [self._config(key)]]


def execution_spec_binding(document):
    fields = ('id', 'project_id', 'version', 'selection', 'configuration_digest', 'resolved',
              'catalog_id', 'catalog_digest', 'created_at', 'created_by')
    return {key: document[key] for key in fields}


def execution_spec_reference(document):
    return {key: document[key] for key in ('version', 'digest', 'catalog_id', 'catalog_digest')}
