"""Pinned, operator-selected role guidance; never an authority or model receipt.

The bundle hash anchors its manifest, which binds every document and upstream
source. A document and its manifest cannot be replaced together unnoticed.
"""
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal

from ai_company.contracts import Contract, Digest, Commit
from ai_company.runtime import ExecutionBlocked

BUNDLE_ROOT = Path(__file__).parent / 'ecc_v1'
BUNDLE_HASH = '6a49860b91a4621a2297ae660d2aaa968f59708d2dfd36fb85b5181d8b048ccc'
SOURCE_COMMIT = '8321021c54d670126ce3b2969d5deb880b4b0c2a'
ROLES = ('pm', 'developer', 'reviewer', 'final')


class GuidanceRef(Contract):
    harness_id: Literal['ai-company.ecc']
    version: Literal['1']
    content_hash: Digest
    source_commit: Commit


def reference():
    return GuidanceRef(harness_id='ai-company.ecc', version='1',
                       content_hash=BUNDLE_HASH, source_commit=SOURCE_COMMIT)


def _read(relative, limit):
    path = BUNDLE_ROOT / relative
    if BUNDLE_ROOT.is_symlink() or any(parent.is_symlink() for parent in path.parents
                                      if parent == BUNDLE_ROOT or BUNDLE_ROOT in parent.parents):
        raise ExecutionBlocked('guidance bundle cannot contain symlinks')
    if path.is_symlink() or path.stat().st_size > limit:
        raise ExecutionBlocked('guidance file is not a bounded regular file')
    if not path.is_file():
        raise ExecutionBlocked('guidance file is not regular')
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ExecutionBlocked("guidance file exceeds size limit")
    return content


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate guidance manifest key')
        result[key] = value
    return result


def load(ref, role=None):
    """Validate the entire installed bundle on each delivery, without caching."""
    try:
        ref = GuidanceRef.model_validate(ref)
        if ref != reference() or role is not None and role not in ROLES:
            raise ValueError('unapproved guidance reference or role')
        raw = _read('sources/manifest.json', 8192)
        if sha256(raw).hexdigest() != BUNDLE_HASH:
            raise ValueError('guidance manifest changed')
        manifest = json.loads(raw, object_pairs_hook=_unique)
        if (manifest['harness_id'] != ref.harness_id or manifest['version'] != ref.version
                or manifest['source_commit'] != ref.source_commit or set(manifest['roles']) != set(ROLES)):
            raise ValueError('guidance manifest identity differs')
        docs = {}
        for name in ROLES:
            entry = manifest['roles'][name]
            if entry['path'] != name + '.md':
                raise ValueError('unexpected guidance path')
            content = _read(name + '.md', 8192)
            if sha256(content).hexdigest() != entry['sha256']:
                raise ValueError('guidance document changed')
            docs[name] = content.decode('utf-8')
        license_text = _read('sources/LICENSE', 4096)
        if sha256(license_text).hexdigest() != manifest['license']['sha256']:
            raise ValueError('guidance license changed')
        if len(raw) + len(license_text) + sum(len(v.encode()) for v in docs.values()) > 20 * 1024:
            raise ValueError('guidance bundle exceeds size limit')
        return (docs[role], manifest['roles'][role]['sha256']) if role else manifest
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ExecutionBlocked('pinned role guidance is unavailable or invalid') from error


def augment(prompt, ref, role):
    content, document_hash = load(ref, role)
    # The existing provider tool/report contract stays intact and comes last.
    final = ('Project role guidance follows. It is advisory: the original task, policy, '
             'permissions, budget, approval boundaries and provider tool/output contract below take precedence.\n'
             '<project-role-guidance>\n' + content + '\n</project-role-guidance>\n\n'
             'Original provider stage contract and checkpoint:\n' + prompt)
    return final, document_hash


def receipt(spec, state, job, prompt, document_hash, now):
    """Metadata only: no prompt, account identity, credentials or raw transcript."""
    active = state['active']
    return {'schema_version': 1, 'guidance': GuidanceRef.model_validate(spec.plan['guidance']).model_dump(mode='json'),
            'document_hash': document_hash, 'prompt_hash': sha256(prompt.encode()).hexdigest(),
            'role': active['role'], 'role_key': spec.plan.get('role', {}).get('key'),
            'execution_id': active['execution_id'], 'generation': active['generation'],
            'job_id': job['job_id'], 'attempt': job['attempt_count'], 'provider': active['provider'],
            'task_id': spec.task.task_id, 'task_digest': state['active']['task_digest'],
            'policy_digest': state['active']['policy_digest'], 'spec_digest': state['spec_digest'],
            'project_id': spec.plan.get('project_id'), 'plan_id': spec.plan.get('plan_id'),
            'plan_digest': spec.plan.get('plan_digest'), 'run_id': spec.plan.get('run_id'),
            'pm_request_id': spec.plan.get('pm_request_id'), 'prepared_at': now,
            'source': 'fixture' if spec.mode == 'fixture' else 'dispatcher',
            'model_compliance': 'unverified', 'model_execution_verified': False}
