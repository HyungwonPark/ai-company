"""Version-bound translation artifacts; no authority to change source documents."""
import copy
import json
import math
import re
import time
from uuid import uuid4

from ai_company.contracts import digest

SOURCE_KEYS = ('id', 'kind', 'project_id', 'source_version', 'author_role', 'source_ref', 'fields', 'protected')
DEFAULT_CONFIG = dict(provider='codex', model='gpt-5.6-luna', model_version='0.154.0',
    reasoning_effort='low', target_language='ko', prompt_version='ko-translation-v1', glossary_version='v1',
    parser_version='translation-json-v2',
    quota_group=None, credential_ref=None, max_attempts=2, timeout_seconds=60,
    max_total_seconds=120, max_chars=12000, max_output_chars=24000)
ACTIVE = ('pending', 'waiting_quota', 'waiting_retry')
PART_BOUNDARY = r'(?<=[.!?])(?=\s)|(?<=\n)|(?<=[가-힣]:)(?=\s)'


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS translation_jobs(id TEXT PRIMARY KEY, cache_key TEXT UNIQUE NOT NULL,
        document TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS translation_links(project_id TEXT NOT NULL, document_id TEXT NOT NULL,
        source_digest TEXT NOT NULL, job_id TEXT NOT NULL, PRIMARY KEY(project_id,document_id,source_digest));
      CREATE TABLE IF NOT EXISTS translation_settings(project_id TEXT PRIMARY KEY, document TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS translation_saved_rechecks(id TEXT PRIMARY KEY, cache_key TEXT UNIQUE NOT NULL,
        document TEXT NOT NULL);
    ''')


def source_digest(document):
    return digest({key: document[key] for key in SOURCE_KEYS})


def configuration(value=None):
    value = value or {}
    if set(value) - set(DEFAULT_CONFIG):
        raise ValueError('unknown translation configuration')
    config = {**DEFAULT_CONFIG, **value}
    if config['provider'] not in ('codex', 'claude') or config['target_language'] != 'ko':
        raise ValueError('unsupported translation provider/language')
    expected = {'codex': 'gpt-5.6-luna', 'claude': 'claude-haiku-4-5-20251001'}
    if config['model'] != expected[config['provider']]:
        raise ValueError('translation model must be an explicitly supported lightweight candidate')
    if config['provider'] == 'claude' and (config['model_version'] != '2.1.270'
            or config['max_attempts'] not in (1, 2) or config['timeout_seconds'] > 60
            or config['max_total_seconds'] > 120):
        raise ValueError('Haiku requires CLI 2.1.270, at most two attempts, 60 seconds per attempt and 120 seconds total')
    for key in ('model', 'model_version', 'prompt_version', 'glossary_version', 'parser_version'):
        if not isinstance(config[key], str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', config[key]):
            raise ValueError('invalid translation configuration identifier')
    if config['reasoning_effort'] != 'low':
        raise ValueError('translation requires explicit low effort; no automatic promotion')
    for key, upper in [('max_attempts', 3), ('timeout_seconds', 120), ('max_total_seconds', 360),
                       ('max_chars', 24000), ('max_output_chars', 48000)]:
        val = config[key]
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val) or not 1 <= val <= upper:
            raise ValueError('invalid bounded translation budget')
    if not isinstance(config['max_attempts'], int):
        raise ValueError('max_attempts must be integer')
    for key in ('quota_group', 'credential_ref'):
        if config[key] is not None and (not isinstance(config[key], str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', config[key])):
            raise ValueError('invalid credential group reference')
    return config


def needs_translation(text):
    # Korean sentences stay exact; mixed documents are split by sentence/newline.
    cleaned = re.sub(r'`[^`]*`|https?://\S+|\b[0-9a-f]{40,64}\b', '', text)
    return sum(character.isalpha() for character in cleaned) >= 2 and not re.search(r'[가-힣]', cleaned)


def unresolved_mixed(fields):
    for text in fields.values():
        for part in re.split(PART_BOUNDARY, text):
            if part.rstrip().endswith(':'):
                continue  # Korean heading with an original brand is preserved.
            cleaned = re.sub(r'`[^`]*`|https?://\S+|\b[0-9a-f]{40,64}\b', '', part)
            foreign_script = any(c.isalpha() and not c.isascii() and not ('가' <= c <= '힣') for c in cleaned)
            if re.search(r'[가-힣]', cleaned) and (foreign_script or re.search(r'[A-Za-z]+\s+[A-Za-z]+', cleaned)):
                return True
    return False


def segments(fields):
    result = {}
    for name, text in fields.items():
        for index, part in enumerate(re.split(PART_BOUNDARY, text)):
            if needs_translation(part):
                result[f'{name}::{index}'] = part
    return result


TOKEN = re.compile(r'`[^`]+`|https?://[^\s]+|(?:/[A-Za-z0-9_.-]+){1,}|\b[0-9a-f]{7,64}\b|\b\d+(?:[.,:]\d+)*(?:%|[A-Za-z]+)?\b|\b[A-Z][A-Z0-9_]{1,}\b|\b[A-Za-z][\w]*_[\w]+\b')
REPAIR_PARSER = 'translation-json-v3'
REPAIR_PROMPT = 'ko-translation-v2'
SAVED_RECHECK_PARSER = 'translation-json-v4-saved'
SAVED_REVIEW_VERSION = 'saved-korean-review-v1'
PATH_LITERAL = r'(?<![A-Za-z0-9_./~-])(?:[~./]*[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+/?|/[A-Za-z0-9_.-]+/?)'
PATH_TOKEN = re.compile(PATH_LITERAL)
TOKEN_V3 = re.compile(r'`[^`]+`|https?://[^\s]+|(?P<path>' + PATH_LITERAL + r')|\b[0-9a-f]{7,64}\b|\b\d+(?:[.,:]\d+)*(?:%|[A-Za-z]+)?\b|\b[A-Z][A-Z0-9_]{1,}\b|\b[A-Za-z][\w]*_[\w]+\b')


def protected_literals(text, parser_version):
    if parser_version not in (REPAIR_PARSER, SAVED_RECHECK_PARSER):
        return TOKEN.findall(text)
    # Backtick-enclosed code stays byte-exact, including a filename's final dot.
    # An unquoted path in prose excludes sentence-final dots, and includes its
    # full relative prefix (the legacy matcher protected only slash suffixes).
    return [match[0].rstrip('.') if match['path'] else match[0] for match in TOKEN_V3.finditer(text)]


def _literal_count(text, literal, parser_version):
    if parser_version in (REPAIR_PARSER, SAVED_RECHECK_PARSER) and PATH_TOKEN.fullmatch(literal):
        return sum(match[0].rstrip('.') == literal for match in PATH_TOKEN.finditer(text))
    return text.count(literal)


GUARDS = ((r'\b(?:not|never|no|cannot|mustn.t|don.t|do not)\b', r'않|안\s|금지|불가|없|거부|아니|마세|마십|말아|말 것'),
          (r'\b(?:only if|if|unless|provided that)\b', r'경우|때|조건|한해|면'),
          (r'\bonly if\b', r'경우에만|때만|때에만|한해|조건.*만'),
          (r'\bpending\b', r'대기|보류|pending'),
          (r'\b(?:except|exception|excluding)\b', r'제외|예외'),
          (r'\b(?:at most|maximum|limit|cap)\b', r'최대|상한|한도|제한|이하'))


def _guard_present(target, pattern, parser):
    if re.search(pattern, target):
        return True
    # Saved-only v4 recognizes two reviewed Korean forms. Earlier parsers and
    # every other condition/exception/limit guard retain their exact behavior.
    if parser == SAVED_RECHECK_PARSER:
        if pattern == GUARDS[0][1]:
            return bool(re.search(r'(?<![가-힣])아닙니다(?=[.!?\s]|$)', target))
        if pattern == GUARDS[3][1]:
            return bool(re.search(r'(?<![가-힣])미결(?=\s*(?:항목|사항|상태|검토|$))', target))
    return False


def validate_fields(job, translated):
    original = job['source']['fields']
    required = segments(original)
    if not isinstance(translated, dict) or set(translated) != set(required):
        raise ValueError('translation must preserve all requested segment identifiers')
    if sum(len(v) for v in translated.values() if isinstance(v, str)) > job['config']['max_output_chars']:
        raise ValueError('translation output exceeds limit')
    for key, source in required.items():
        target = translated[key]
        if not isinstance(target, str) or not target.strip() or not re.search(r'[가-힣]', target):
            raise ValueError('translation must contain Korean text')
        if (re.match(r'\s*', source)[0] != re.match(r'\s*', target)[0]
                or re.search(r'\s*$', source)[0] != re.search(r'\s*$', target)[0]):
            raise ValueError('translation changed paragraph separators')
        parser = job['config'].get('parser_version')
        for token in protected_literals(source, parser):
            if _literal_count(target, token, parser) != _literal_count(source, token, parser):
                raise ValueError('protected literal missing or duplicated')
        for source_pattern, target_pattern in GUARDS:
            if re.search(source_pattern, source, re.I) and not _guard_present(target, target_pattern, parser):
                raise ValueError('negation, condition, exception, or limit marker missing')
    fields = {}
    for name, text in original.items():
        parts = re.split(PART_BOUNDARY, text)
        fields[name] = ''.join(translated.get(f'{name}::{index}', part) for index, part in enumerate(parts))
    return fields


class TranslationStore:
    def __init__(self, db, *, clock=time.time):
        self.db, self.clock = db, clock

    def _save(self, job):
        job['updated_at'] = self.clock()
        self.db.execute('UPDATE translation_jobs SET document=? WHERE id=?', (json.dumps(job, ensure_ascii=False), job['id']))

    def _get(self, job_id):
        row = self.db.execute('SELECT document FROM translation_jobs WHERE id=?', (job_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def get_result(self, job_id):
        """Read an immutable completed artifact for trusted approval auditing."""
        job = self._get(job_id)
        if not job or job['status'] != 'completed':
            raise ValueError('translation is not a completed immutable result')
        if source_digest(job['source']) != job['source_digest']:
            raise ValueError('translation source digest no longer matches')
        return copy.deepcopy(job)

    def sync(self, project_id, documents, config=None):
        config = configuration(config)
        if config['parser_version'] == SAVED_RECHECK_PARSER:
            raise ValueError('saved recheck parser cannot schedule model calls')
        documents = list(documents.values()) if isinstance(documents, dict) else documents
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute('INSERT OR REPLACE INTO translation_settings VALUES (?,?)', (project_id, json.dumps(config)))
            for document in documents:
                source = {key: copy.deepcopy(document[key]) for key in SOURCE_KEYS}
                if source['project_id'] != project_id or not isinstance(source['fields'], dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in source['fields'].items()):
                    raise ValueError('invalid source document')
                sd = source_digest(source)
                if document.get('source_digest', sd) != sd:
                    raise ValueError('source digest does not match canonical document')
                key = digest({'source_digest': sd, 'execution_policy_digest': digest(config), **{k: config[k] for k in ('target_language', 'provider', 'model', 'model_version', 'reasoning_effort', 'prompt_version', 'glossary_version')}})
                existing = self.db.execute('SELECT id FROM translation_jobs WHERE cache_key=?', (key,)).fetchone()
                if existing:
                    job_id = existing[0]
                else:
                    job_id = uuid4().hex
                    oversized = sum(len(v) for v in source['fields'].values()) > config['max_chars']
                    mixed = unresolved_mixed(source['fields'])
                    necessary = bool(segments(source['fields']))
                    status = 'failed' if oversized else ('blocked' if mixed else ('pending' if necessary else 'not_required'))
                    job = dict(id=job_id, cache_key=key, source=source, source_digest=sd, config=config,
                        status=status, fields={} if necessary else source['fields'], attempts=0, spent_seconds=0,
                        lease_token=None, lease_expires_at=None, execution_identity=None, execution_started=False,
                        resume_at=None, reason='document_length_limit' if oversized else ('mixed_sentence_requires_segmentation' if mixed else None),
                        created_at=self.clock(), updated_at=self.clock(), observed_configuration=None,
                        semantic_validation='not_independently_verified')
                    self.db.execute('INSERT INTO translation_jobs VALUES (?,?,?)', (job_id, key, json.dumps(job, ensure_ascii=False)))
                linked = self.db.execute('SELECT job_id FROM translation_links WHERE project_id=? AND document_id=? AND source_digest=?',
                                         (project_id, source['id'], sd)).fetchone()
                if linked:
                    replacement = self._get(linked[0])
                    if self._matches_replacement(replacement, self._get(job_id)):
                        # A reviewed repair for this exact failed source/configuration
                        # stays selected on the next ordinary worker synchronization.
                        job_id = replacement['id']
                self.db.execute('''INSERT INTO translation_links VALUES (?,?,?,?)
                    ON CONFLICT(project_id,document_id,source_digest) DO UPDATE SET job_id=excluded.job_id
                    WHERE translation_links.job_id != excluded.job_id''', (project_id, source['id'], sd, job_id))
        return [self.read(d) for d in documents]

    def _matches_replacement(self, replacement, original):
        if (not replacement or not original or replacement.get('repair_of') != original['id']
                or replacement.get('repair_base_cache_key') != original['cache_key']
                or replacement['source_digest'] != original['source_digest']
                or source_digest(replacement['source']) != original['source_digest']):
            return False
        config = configuration({**original['config'], 'parser_version': REPAIR_PARSER, 'prompt_version': REPAIR_PROMPT})
        if replacement['config'] == config:
            return True
        parent = self._get(replacement.get('recheck_of'))
        return bool(parent and parent['config'] == config and self._matches_replacement(parent, original)
                    and replacement['status'] == 'completed'
                    and replacement.get('recheck_original_digest') == digest(parent)
                    and replacement['config'] == {**config, 'parser_version': SAVED_RECHECK_PARSER})

    def inspect_saved(self, failed_job_id, review):
        """Read one native result and its source-bound operator review; no writes/calls."""
        from ai_company.adapters.translation_cli import TranslationCLI
        original = self._get(failed_job_id)
        if (not original or original['status'] != 'failed' or original['config']['parser_version'] != REPAIR_PARSER
                or source_digest(original['source']) != original['source_digest']):
            raise ValueError('saved recheck requires an unchanged failed v3 repair')
        parent = self._get(original.get('repair_of'))
        if not self._matches_replacement(original, parent):
            raise ValueError('saved recheck requires the original repair lineage')
        replay = TranslationCLI.replay(original)
        expected = {'version', 'job_id', 'source_digest', 'raw_sha256', 'fields_digest', 'verdict', 'reviewer', 'findings'}
        if (not isinstance(review, dict) or set(review) != expected or review['version'] != SAVED_REVIEW_VERSION
                or review['job_id'] != original['id'] or review['source_digest'] != original['source_digest']
                or review['raw_sha256'] != replay['reprocessing']['raw_sha256']
                or review['fields_digest'] != digest(replay['fields']) or review['verdict'] not in ('PASS', 'BLOCK')
                or not isinstance(review['reviewer'], str) or not 1 <= len(review['reviewer']) <= 200
                or not isinstance(review['findings'], list) or len(review['findings']) > 32
                or any(not isinstance(item, str) or not 1 <= len(item) <= 2000 for item in review['findings'])
                or (review['verdict'] == 'PASS') != (review['findings'] == [])):
            raise ValueError('saved recheck requires the exact source/output-bound semantic review')
        config = {**original['config'], 'parser_version': SAVED_RECHECK_PARSER}
        fields, reason = None, None
        try:
            fields = validate_fields({**original, 'config': config}, replay['fields'])
        except ValueError as exc:
            reason = str(exc)
        if review['verdict'] == 'BLOCK':
            reason = 'semantic_review_blocked'
        return original, replay, dict(version=SAVED_RECHECK_PARSER, job_id=original['id'],
            original_digest=digest(original), source_digest=original['source_digest'],
            raw_sha256=replay['reprocessing']['raw_sha256'], fields_digest=digest(replay['fields']),
            review=copy.deepcopy(review), review_digest=digest(review), status='rejected' if reason else 'completed',
            reason=reason, new_model_calls=0, fields=fields if reason is None else None)

    def recheck_saved(self, failed_job_id, *, expected_original_digest, review):
        """Trusted operator-only recheck. Never enqueue work or change a failed record."""
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            original, replay, inspection = self.inspect_saved(failed_job_id, review)
            if inspection['original_digest'] != expected_original_digest:
                raise ValueError('saved recheck original changed after review')
            key = digest(inspection)
            row = self.db.execute('SELECT document FROM translation_saved_rechecks WHERE cache_key=?', (key,)).fetchone()
            if row:
                return json.loads(row[0])
            record = {**inspection, 'id': uuid4().hex, 'cache_key': key, 'created_at': self.clock(), 'result_job_id': None}
            record.pop('fields')
            if inspection['status'] == 'completed':
                document = copy.deepcopy(original)
                document.update(id=uuid4().hex, cache_key=digest({'saved_recheck': key}), status='completed',
                    fields=inspection['fields'], config={**original['config'], 'parser_version': SAVED_RECHECK_PARSER},
                    created_at=self.clock(), updated_at=self.clock(), started_at=None, reason=None, resume_at=None,
                    lease_token=None, lease_expires_at=None, execution_identity=None, execution_started=False,
                    recheck_of=failed_job_id, recheck_original_digest=digest(original), recheck_id=record['id'],
                    semantic_validation='saved_output_reviewed')
                proof = {**replay['reprocessing'], 'kind': 'saved_output_semantic_recheck',
                         'parser_version': SAVED_RECHECK_PARSER, 'review_digest': digest(review), 'recheck_id': record['id']}
                document['execution_result'] = {**copy.deepcopy(original['execution_result']), 'reprocessing': proof}
                self.db.execute('INSERT INTO translation_jobs VALUES (?,?,?)',
                                (document['id'], document['cache_key'], json.dumps(document, ensure_ascii=False)))
                changed = self.db.execute('UPDATE translation_links SET job_id=? WHERE project_id=? AND document_id=? AND source_digest=? AND job_id=?',
                    (document['id'], original['source']['project_id'], original['source']['id'], original['source_digest'], failed_job_id))
                if changed.rowcount != 1:
                    raise ValueError('saved recheck source is no longer selected')
                record['result_job_id'] = document['id']
            self.db.execute('INSERT INTO translation_saved_rechecks VALUES (?,?,?)',
                            (record['id'], key, json.dumps(record, ensure_ascii=False)))
            return record

    def restore_saved_selection(self, recheck_id, *, expected_result_job_id):
        """Restore only a recheck's display link; retain every artifact and ledger."""
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT document FROM translation_saved_rechecks WHERE id=?', (recheck_id,)).fetchone()
            record = json.loads(row[0]) if row else None
            if (not record or record['status'] != 'completed' or record['version'] != SAVED_RECHECK_PARSER
                    or record['result_job_id'] != expected_result_job_id):
                raise ValueError('restore requires the exact completed saved recheck')
            original = self._get(record['job_id'])
            result = self.get_result(expected_result_job_id)
            if (not original or digest(original) != record['original_digest']
                    or result.get('recheck_id') != recheck_id or result.get('recheck_of') != original['id']
                    or result['source_digest'] != original['source_digest']):
                raise ValueError('restore lineage changed')
            identity = (original['source']['project_id'], original['source']['id'], original['source_digest'])
            current = self.db.execute('SELECT job_id FROM translation_links WHERE project_id=? AND document_id=? AND source_digest=?', identity).fetchone()
            if not current or current[0] not in (original['id'], expected_result_job_id):
                raise ValueError('restore cannot overwrite a newer display selection')
            self.db.execute('UPDATE translation_links SET job_id=? WHERE project_id=? AND document_id=? AND source_digest=? AND job_id=?',
                            (original['id'], *identity, expected_result_job_id))
            return dict(recheck_id=recheck_id, selected_job_id=original['id'], artifacts_preserved=True)

    def repair(self, failed_job_id, *, expected_original_digest, replay=None):
        """Trusted operator repair, not an HTTP/model capability or automatic retry.

        Keep the failed job immutable and inherit its consumed execution budget.
        A native replay may complete without another model call; otherwise only
        the original budget's remaining attempts can run under the normal queue.
        """
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            original = self._get(failed_job_id)
            if (not original or digest(original) != expected_original_digest
                    or original['status'] != 'failed'
                    or source_digest(original['source']) != original['source_digest']):
                raise ValueError('repair requires the exact immutable failed job')
            if original['config'].get('parser_version') == REPAIR_PARSER:
                raise ValueError('repair cannot reset a repaired execution budget')
            if original.get('execution_result', {}).get('cgroup_stopped') is not True:
                raise ValueError('repair requires confirmed execution termination')
            config = configuration({**original['config'], 'parser_version': REPAIR_PARSER, 'prompt_version': REPAIR_PROMPT})
            key = digest({'repair_of': failed_job_id, 'source_digest': original['source_digest'], 'config': config})
            existing = self.db.execute('SELECT id FROM translation_jobs WHERE cache_key=?', (key,)).fetchone()
            if existing:
                previous = self._get(existing[0])
                if replay is not None and previous.get('repair_result_digest') != digest(replay):
                    raise ValueError('repair already exists with another result')
                return self._public(previous)
            document = copy.deepcopy(original)
            document.update(id=uuid4().hex, cache_key=key, config=config, status='pending', fields={},
                created_at=self.clock(), updated_at=self.clock(), started_at=None,
                lease_token=None, lease_expires_at=None, execution_identity=None,
                execution_started=False, execution_result={}, observed_configuration=None,
                resume_at=None, reason=None, repair_of=failed_job_id,
                repair_base_cache_key=original['cache_key'], repair_result_digest=None,
                inherited_attempts=original['attempts'], inherited_spent_seconds=original['spent_seconds'])
            if replay is not None:
                proof = replay.get('reprocessing', {})
                if (replay.get('category') != 'success' or replay.get('tool_calls')
                        or replay.get('cgroup_stopped') is not True
                        or proof.get('original_failed_job_id') != failed_job_id
                        or proof.get('original_failed_status') != original['status']
                        or proof.get('original_failure_reason') != original.get('reason')
                        or proof.get('source_digest') != original['source_digest']
                        or proof.get('parser_version') != REPAIR_PARSER
                        or proof.get('new_model_calls') != 0
                        or not re.fullmatch(r'[0-9a-f]{64}', str(proof.get('raw_sha256')))):
                    raise ValueError('repair replay requires bound native evidence')
                document.update(status='completed', fields=validate_fields(document, replay.get('fields')),
                                observed_configuration=copy.deepcopy(replay.get('observed_configuration')),
                                execution_result={'reprocessing': copy.deepcopy(proof)},
                                repair_result_digest=digest(replay))
            elif (document['attempts'] >= config['max_attempts']
                    or document['spent_seconds'] + config['timeout_seconds'] > config['max_total_seconds']):
                raise ValueError('repair has no remaining original execution budget')
            self.db.execute('INSERT INTO translation_jobs VALUES (?,?,?)', (document['id'], key, json.dumps(document, ensure_ascii=False)))
            # A repair never changes another source version or a newer selection.
            changed = self.db.execute('UPDATE translation_links SET job_id=? WHERE project_id=? AND document_id=? AND source_digest=? AND job_id=?',
                (document['id'], original['source']['project_id'], original['source']['id'], original['source_digest'], failed_job_id))
            if changed.rowcount != 1:
                raise ValueError('repair source is no longer the selected failed job')
            return self._public(document)

    def read(self, document):
        sd = source_digest(document)
        row = self.db.execute('SELECT job_id FROM translation_links WHERE project_id=? AND document_id=? AND source_digest=?',
                             (document['project_id'], document['id'], sd)).fetchone()
        if not row:
            return None
        job = self._get(row[0])
        if not job or job['source_digest'] != sd or source_digest(job['source']) != sd:
            raise ValueError('translation source digest no longer matches')
        return self._public(job)

    @staticmethod
    def _public(job):
        config = job['config']
        return {key: copy.deepcopy(job[key]) for key in ('id', 'status', 'fields', 'source_digest', 'reason', 'resume_at', 'observed_configuration', 'semantic_validation')} | dict(
            model=config['model'], requested_configuration={'provider': config['provider'], 'model': config['model'],
            'reasoning_effort': config['reasoning_effort'], 'model_version': config['model_version']},
            version=job['id'], prompt_version=config['prompt_version'], glossary_version=config['glossary_version'],
            parser_version=config.get('parser_version', 'translation-json-v1'),
            reprocessing=copy.deepcopy(job.get('execution_result', {}).get('reprocessing')),
            created_at=job['created_at'], started_at=job.get('started_at'),
            completed_at=job['updated_at'] if job['status'] == 'completed' else None,
            substitution_reason=('Explicit lightweight Haiku alternative: Codex Luna tool-free execution is unverified.'
                                 if config['provider'] == 'claude' else None))

    def summary(self, project_id):
        row = self.db.execute('SELECT document FROM translation_settings WHERE project_id=?', (project_id,)).fetchone()
        config = json.loads(row[0]) if row else configuration()
        jobs = [json.loads(r[0]) for r in self.db.execute('SELECT DISTINCT j.document FROM translation_jobs j JOIN translation_links l ON l.job_id=j.id WHERE l.project_id=?', (project_id,))]
        counts = {}
        for job in jobs:
            counts[job['status']] = counts.get(job['status'], 0) + 1
        waiting = [j['resume_at'] for j in jobs if j['resume_at'] is not None]
        status = next((state for state in ('running', 'blocked', 'waiting_quota', 'waiting_retry', 'pending', 'failed') if counts.get(state)), 'available')
        matching = []
        for job in jobs:
            parent = self._get(job['repair_of']) if job.get('repair_of') else None
            repaired = parent and digest(parent['config']) == digest(config) and self._matches_replacement(job, parent)
            if digest(job['config']) == digest(config) or repaired:
                matching.append(job)
        observed_jobs = [j for j in matching if j.get('observed_configuration')]
        observed = None
        if observed_jobs:
            latest = max(observed_jobs, key=lambda j: (j['updated_at'], j['id']))
            observed = {**copy.deepcopy(latest['observed_configuration']), 'job_id': latest['id'], 'source_digest': latest['source_digest']}
            result = latest.get('execution_result', {})
            observed['session_id'] = result.get('session_id') or result.get('reprocessing', {}).get('original_session_id')
            observed['evidence'] = {key: copy.deepcopy(result[key]) for key in
                ('category', 'cgroup_stopped', 'effective_tools', 'total_cost_usd') if key in result}
            if result.get('reprocessing'):
                observed['evidence']['reprocessing'] = {key: copy.deepcopy(result['reprocessing'][key]) for key in
                    ('kind', 'original_failed_job_id', 'original_session_id', 'raw_sha256', 'parser_version', 'new_model_calls')
                    if key in result['reprocessing']}
        relevant = [j for j in matching if j['status'] == status and j.get('reason')]
        reason = max(relevant, key=lambda j: (j['updated_at'], j['id']))['reason'] if relevant else None
        if not jobs and config['provider'] == 'codex':
            reason = 'tool_free_execution_not_verified'
        return dict(status='unconfigured' if not row else status,
            model=config['model'], candidate_model='gpt-5.6-luna', provider=config['provider'], quota_group=config['quota_group'], counts=counts,
            requested_configuration={k: config[k] for k in ('provider', 'model', 'reasoning_effort', 'model_version')},
            observed_configuration=observed, resume_at=min(waiting) if waiting else None, reason=reason)

    def _quota(self, config):
        tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'quota_groups', 'credential_groups'} <= tables:
            return 'blocked', None, 'shared_credential_group_not_registered'
        row = self.db.execute('SELECT credential_ref,group_id FROM credential_groups WHERE provider=?', (config['provider'],)).fetchone()
        if not row or tuple(row) != (config['credential_ref'], config['quota_group']):
            return 'blocked', None, 'shared_credential_group_mismatch'
        group = self.db.execute('SELECT state,resume_at,reason FROM quota_groups WHERE group_id=?', (config['quota_group'],)).fetchone()
        if not group or group[0] == 'DISABLED':
            return 'blocked', None, 'shared_credential_unavailable'
        if group[0] != 'AVAILABLE' and (group[1] is None or group[1] > self.clock()):
            return 'waiting_quota', group[1], group[2] or 'shared_account_quota'
        return None

    def recover(self, execution_alive):
        recovered = 0
        probes = {}
        for row in self.db.execute('SELECT document FROM translation_jobs').fetchall():
            job = json.loads(row[0])
            if job['status'] == 'running' and job['lease_expires_at'] <= self.clock() and job['execution_started']:
                key = (job['id'], job['lease_token'], digest(job.get('execution_identity')))
                probes[key] = execution_alive(copy.deepcopy(job.get('execution_identity')))
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            for row in self.db.execute('SELECT document FROM translation_jobs').fetchall():
                job = json.loads(row[0])
                if job['status'] != 'running' or job['lease_expires_at'] > self.clock():
                    continue
                # Lease expiry alone never authorizes replay of a model call.
                probe_key = (job['id'], job['lease_token'], digest(job.get('execution_identity')))
                if job['execution_started'] and probes.get(probe_key) is not False:
                    job['reason'] = 'execution_termination_unconfirmed'
                else:
                    job['spent_seconds'] += job['config']['timeout_seconds'] if job['execution_started'] else 0
                    job.update(status='waiting_retry', resume_at=self.clock(), lease_token=None,
                               reason='worker_interrupted_after_confirmed_termination')
                    recovered += 1
                self._save(job)
        return recovered

    def claim(self, worker_id, *, adapter_ready=False, execution_alive=None):
        if execution_alive is not None:
            self.recover(execution_alive)
        prepared_ready = {}
        if callable(adapter_ready):
            # CLI probes must never hold the shared SQLite writer lock. New
            # configurations appearing after this snapshot wait for the next pass.
            configs = [json.loads(r[0])['config'] for r in self.db.execute('SELECT document FROM translation_jobs')]
            for config in configs:
                key = digest(config)
                if key not in prepared_ready:
                    prepared_ready[key] = adapter_ready(copy.deepcopy(config)) is True
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            jobs = [json.loads(r[0]) for r in self.db.execute('SELECT document FROM translation_jobs ORDER BY rowid')]
            if any(j['status'] == 'running' for j in jobs):
                return None
            for job in jobs:
                ready = prepared_ready.get(digest(job['config']), False) if callable(adapter_ready) else adapter_ready
                eligible = job['status'] in ACTIVE or (ready and job['status'] == 'blocked'
                    and job['reason'] == 'tool_free_execution_not_verified')
                if not eligible or (job['resume_at'] is not None and job['resume_at'] > self.clock()):
                    continue
                config = job['config']
                if job['attempts'] >= config['max_attempts'] or job['spent_seconds'] + config['timeout_seconds'] > config['max_total_seconds']:
                    job.update(status='failed', reason='translation_budget_exhausted')
                elif not ready:
                    job.update(status='blocked', reason='tool_free_execution_not_verified')
                elif (quota := self._quota(config)):
                    job.update(status=quota[0], resume_at=quota[1], reason=quota[2])
                else:
                    job.update(status='running', lease_token=uuid4().hex, worker_id=worker_id,
                        lease_expires_at=self.clock()+config['timeout_seconds']+30, started_at=self.clock(),
                        execution_started=False, execution_identity=None, reason=None, resume_at=None)
                    job['observed_configuration'] = None
                    job['attempts'] += 1
                    self._save(job)
                    return copy.deepcopy(job)
                self._save(job)
        return None

    def started(self, job_id, lease_token, execution_identity):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            job = self._get(job_id)
            if not job or job['status'] != 'running' or job['lease_token'] != lease_token:
                return False
            job.update(execution_started=True, execution_identity=copy.deepcopy(execution_identity))
            self._save(job)
            return True

    def finish(self, job_id, lease_token, result):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            job = self._get(job_id)
            if not job or job['status'] != 'running' or job['lease_token'] != lease_token:
                return False
            elapsed = max(0, self.clock()-job['started_at'])
            job['spent_seconds'] += elapsed
            category = result.get('category')
            job['execution_result'] = {key: copy.deepcopy(result[key]) for key in
                ('category', 'reason', 'session_id', 'cgroup_stopped', 'evidence_dir', 'total_cost_usd',
                 'effective_tools', 'requested_configuration', 'substitution_reason') if key in result}
            if result.get('reprocessing'):
                job['execution_result']['reprocessing'] = copy.deepcopy(result['reprocessing'])
            if isinstance(result.get('observed_configuration'), dict):
                job['observed_configuration'] = {key: copy.deepcopy(result['observed_configuration'][key]) for key in
                    ('provider', 'model', 'reasoning_effort', 'source', 'scope', 'status', 'backend_model_verified')
                    if key in result['observed_configuration']}
            job.update(lease_token=None, lease_expires_at=None, resume_at=None)
            if category == 'success':
                try:
                    if elapsed > job['config']['timeout_seconds'] or result.get('tool_calls'):
                        raise ValueError('execution exceeded time budget or used tools')
                    job['fields'] = validate_fields(job, result.get('fields'))
                    observed = result.get('observed_configuration')
                    if observed is not None:
                        if not isinstance(observed, dict):
                            raise ValueError('invalid observed configuration')
                        observed = {k: observed[k] for k in ('provider', 'model', 'reasoning_effort', 'source', 'scope', 'status', 'backend_model_verified') if k in observed}
                    job.update(status='completed', reason=None, observed_configuration=observed)
                except (ValueError, TypeError) as exc:
                    job.update(status='failed', reason=str(exc))
            elif category in ('quota', 'rate_limit', 'transient_network'):
                reset = result.get('reset_at')
                if isinstance(reset, bool) or not isinstance(reset, (int, float)) or not math.isfinite(reset):
                    reset = self.clock()+60
                reset = max(self.clock()+30, reset)
                job.update(status='waiting_quota' if category != 'transient_network' else 'waiting_retry', resume_at=reset, reason=category)
                if category != 'transient_network':
                    self.db.execute("UPDATE quota_groups SET state=CASE WHEN state='DISABLED' THEN state ELSE 'COOLDOWN' END, resume_at=MAX(COALESCE(resume_at,0),?),reason=? WHERE group_id=?", (reset, category, job['config']['quota_group']))
            else:
                job.update(status='blocked' if category == 'blocked' else 'failed', reason=result.get('reason', category or 'invalid_result'))
                if category == 'authentication':
                    self.db.execute("UPDATE quota_groups SET state='DISABLED',reason='authentication' WHERE group_id=?", (job['config']['quota_group'],))
            self._save(job)
        return True
