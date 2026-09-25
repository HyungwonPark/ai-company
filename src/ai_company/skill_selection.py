"""Durable skill research and immutable, role-scoped document selections.

The caller owns the policy and catalog. This module cannot grant execution
authority or turn a public search result into approved guidance.
"""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from time import monotonic, time

from ai_company import skill_catalog as catalog


_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_POLICY_LIMITS = {"max_searches": 6, "max_fetches": 24,
                  "max_bytes": 6 * catalog.MAX_BUNDLE_BYTES,
                  "max_elapsed_ms": 120_000, "max_model_calls": 20,
                  "max_tokens": 200_000, "max_cost_microusd": 10_000_000}


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _hash(value):
    return sha256(_json(value).encode()).hexdigest()


def _policy(value):
    if not isinstance(value, dict) or set(value) != {*_POLICY_LIMITS, "expires_at"}:
        raise catalog.SkillCatalogError("research policy must persist every limit and expiry")
    for key, ceiling in _POLICY_LIMITS.items():
        amount = value[key]
        if type(amount) is not int or amount < 0 or amount > ceiling:
            raise catalog.SkillCatalogError("invalid research policy limit")
    if type(value["expires_at"]) not in (int, float) or value["expires_at"] <= 0:
        raise catalog.SkillCatalogError("research policy needs a fixed expiry")
    return value


class SkillResearchStore:
    """One SQLite file per non-operational/operational research workspace.

    Reservations are committed before network access. A crash leaves an
    in-progress lookup; the next worker reports it as uncertain rather than
    spending again or falsely claiming a completed read.
    """

    def __init__(self, path, *, clock=time):
        self.clock = clock
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS skill_research_jobs(
              key TEXT PRIMARY KEY, policy TEXT NOT NULL,
              searches INTEGER NOT NULL DEFAULT 0, fetches INTEGER NOT NULL DEFAULT 0,
              reserved_bytes INTEGER NOT NULL DEFAULT 0, reserved_ms INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS skill_research_lookups(
              job_key TEXT NOT NULL, lookup_id TEXT NOT NULL, input_hash TEXT NOT NULL,
              kind TEXT NOT NULL, state TEXT NOT NULL, result TEXT,
              PRIMARY KEY(job_key, lookup_id));
            CREATE UNIQUE INDEX IF NOT EXISTS skill_research_input
              ON skill_research_lookups(job_key,input_hash);
        """)

    def close(self):
        self.db.close()

    def snapshot(self, key):
        row = self.db.execute("SELECT policy,searches,fetches,reserved_bytes,reserved_ms "
                              "FROM skill_research_jobs WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        return {"policy": json.loads(row[0]), "searches": row[1], "fetches": row[2],
                "reserved_bytes": row[3], "reserved_ms": row[4]}

    def _reserve(self, key, lookup_id, kind, input_data, policy, *, calls, bytes_limit, timeout):
        _policy(policy)
        if not _KEY.fullmatch(key) or not _KEY.fullmatch(lookup_id):
            raise catalog.SkillCatalogError("invalid research or lookup identity")
        input_hash = _hash(input_data)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT policy,searches,fetches,reserved_bytes,reserved_ms "
                                  "FROM skill_research_jobs WHERE key=?", (key,)).fetchone()
            serialized = _json(policy)
            if row is None:
                self.db.execute("INSERT INTO skill_research_jobs(key,policy) VALUES (?,?)", (key, serialized))
                row = (serialized, 0, 0, 0, 0)
            elif row[0] != serialized:
                raise catalog.SkillCatalogError("research policy cannot change to reset counters")
            if self.clock() >= policy["expires_at"]:
                raise catalog.SkillCatalogError("research cache policy expired")
            prior = self.db.execute("SELECT input_hash,state,result FROM skill_research_lookups "
                                    "WHERE job_key=? AND lookup_id=?", (key, lookup_id)).fetchone()
            if prior is not None:
                if prior[0] != input_hash:
                    raise catalog.SkillCatalogError("lookup ID reused for different public input")
                self.db.execute("COMMIT")
                return json.loads(prior[2]) | {"cache_hit": True} if prior[2] else {
                    "status": "lookup_pending", "lookup_id": lookup_id,
                    "reason": "previous lookup has no committed result", "cache_hit": True}
            prior_input = self.db.execute("SELECT lookup_id,result FROM skill_research_lookups "
                                          "WHERE job_key=? AND input_hash=?", (key, input_hash)).fetchone()
            if prior_input is not None:
                self.db.execute("COMMIT")
                return json.loads(prior_input[1]) | {"cache_hit": True} if prior_input[1] else {
                    "status": "lookup_pending", "lookup_id": prior_input[0],
                    "reason": "previous lookup has no committed result", "cache_hit": True}
            search_count = row[1] + (1 if kind == "search" else 0)
            fetch_count = row[2] + (calls if kind in ("fetch", "discover") else 0)
            reserved_bytes = row[3] + bytes_limit
            reserved_ms = row[4] + round(timeout * 1000)
            if (search_count > policy["max_searches"] or fetch_count > policy["max_fetches"]
                    or reserved_bytes > policy["max_bytes"] or reserved_ms > policy["max_elapsed_ms"]):
                raise catalog.SkillCatalogError("research budget exhausted")
            self.db.execute("UPDATE skill_research_jobs SET searches=?,fetches=?,reserved_bytes=?,reserved_ms=? "
                            "WHERE key=?", (search_count, fetch_count, reserved_bytes, reserved_ms, key))
            self.db.execute("INSERT INTO skill_research_lookups(job_key,lookup_id,input_hash,kind,state) "
                            "VALUES (?,?,?,?,?)", (key, lookup_id, input_hash, kind, "in_progress"))
            self.db.execute("COMMIT")
            return None
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _finish(self, key, lookup_id, result):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT state FROM skill_research_lookups WHERE job_key=? AND lookup_id=?",
                                  (key, lookup_id)).fetchone()
            if row != ("in_progress",):
                raise catalog.SkillCatalogError("research lookup already completed or missing")
            self.db.execute("UPDATE skill_research_lookups SET state='complete',result=? "
                            "WHERE job_key=? AND lookup_id=?", (_json(result), key, lookup_id))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return result | {"cache_hit": False}

    def run_search(self, key, lookup_id, term, *, allowed_terms, policy, max_results=6, timeout=5):
        # Validate before charging a lookup. Public query only contains a generic allowlisted term.
        if not catalog._TERM.fullmatch(term) or term not in allowed_terms or not 1 <= max_results <= 6:
            raise catalog.SkillCatalogError("unapproved public search")
        prior = self._reserve(key, lookup_id, "search", {"term": term, "max_results": max_results},
                              policy, calls=1, bytes_limit=catalog.MAX_SEARCH_BYTES, timeout=timeout)
        if prior is not None:
            return prior
        try:
            result = catalog.search_public_skills(term, allowed_terms=allowed_terms,
                                                   max_results=max_results, timeout=timeout)
        except Exception as error:
            result = {"status": "lookup_failed", "reason": str(error)[:160], "lookups_used": 1}
        return self._finish(key, lookup_id, result)

    def run_fetch(self, key, lookup_id, url, *, policy, reference_paths=(),
                  license_path=None, max_bytes=catalog.MAX_BUNDLE_BYTES, timeout=5):
        catalog._github_source(url)
        calls = len(catalog._paths("SKILL.md", reference_paths, license_path))
        if max_bytes < 1 or max_bytes > catalog.MAX_BUNDLE_BYTES:
            raise catalog.SkillCatalogError("unbounded public skill fetch")
        prior = self._reserve(key, lookup_id, "fetch",
                              {"url": url, "references": reference_paths, "license": license_path},
                              policy, calls=calls, bytes_limit=max_bytes, timeout=timeout)
        if prior is not None:
            return prior
        try:
            result = catalog.fetch_public_skill(url, reference_paths=reference_paths,
                                                license_path=license_path, max_bytes=max_bytes,
                                                timeout=timeout)
        except Exception as error:
            result = {"status": "lookup_failed", "source_url": url,
                      "reason": str(error)[:160], "lookups_used": calls}
        return self._finish(key, lookup_id, result)

    def run_discover(self, key, lookup_id, repository, branch, term, *, policy, timeout=2):
        if not catalog._TERM.fullmatch(term):
            raise catalog.SkillCatalogError("invalid discovery term")
        prior = self._reserve(key, lookup_id, "discover",
                              {"repository": repository, "branch": branch, "term": term},
                              policy, calls=2, bytes_limit=2 * catalog.MAX_SEARCH_BYTES, timeout=timeout)
        if prior is not None:
            return prior
        try:
            result = catalog.discover_public_skill(repository, branch, term, timeout=timeout)
        except Exception as error:
            result = {"status": "lookup_failed", "repository": repository,
                      "reason": str(error)[:160], "lookups_used": 2}
        return self._finish(key, lookup_id, result)


def load_trusted_catalog(config_entries):
    """Load operator-owned pinned local documents; HTTP never supplies these entries."""
    result = {}
    for config in config_entries:
        if not isinstance(config, dict) or "root" not in config or "skill_id" not in config:
            raise catalog.SkillCatalogError("incomplete trusted catalog entry")
        root = Path(config["root"])
        if not root.is_absolute():
            raise catalog.SkillCatalogError("trusted skill root must be absolute")
        entry = catalog.inspect_installed_skill(root, **{key: value for key, value in config.items()
                                                         if key != "root"})
        if entry["skill_id"] in result:
            raise catalog.SkillCatalogError("duplicate trusted skill identity")
        result[entry["skill_id"]] = {"root": root, "entry": entry}
    return result


def catalog_version(trusted_catalog):
    return _hash({skill_id: item["entry"]["bundle_sha256"]
                  for skill_id, item in sorted(trusted_catalog.items())})


def _identity(entry):
    result = deepcopy({key: entry[key] for key in ("skill_id", "name", "source_url", "source_ref", "version",
                                             "bundle_sha256", "files", "license", "compatibility",
                                             "dependencies", "permissions", "status")})
    if entry["status"] == "review_pending" and entry.get("research_origin"):
        result["research_origin"] = entry["research_origin"]
        result["research_match_terms"] = entry.get("research_match_terms", [])
        result["document_excerpt"] = entry.get("document_excerpt", "")[:1200]
        result["license_excerpt"] = entry.get("license_excerpt", "")[:300]
    return result


def build_selection(role_assignments, trusted_catalog, *, external_candidates=(),
                    outcome="existing_sufficient", reason="", can_continue=True,
                    role_outcomes=None):
    """Freeze at most three recommendations per role and six public candidates.

    role_assignments maps role keys to [{skill_id, reason, requirements, selected}].
    A public candidate may be displayed, but cannot be selected for delivery.
    """
    if len(role_assignments) > 8 or len(external_candidates) > 6:
        raise catalog.SkillCatalogError("skill recommendation scope exceeded")
    external = {entry["skill_id"]: entry for entry in external_candidates}
    if len(external) != len(external_candidates):
        raise catalog.SkillCatalogError("duplicate external skill identity")
    roles = {}
    for role, assignments in sorted(role_assignments.items()):
        if not _KEY.fullmatch(role) or len(assignments) > 3:
            raise catalog.SkillCatalogError("invalid role or too many recommended skills")
        values = []
        seen = set()
        for assignment in assignments:
            skill_id = assignment["skill_id"]
            if skill_id in seen:
                raise catalog.SkillCatalogError("duplicate role skill")
            seen.add(skill_id)
            selected = assignment.get("selected", False)
            installed = trusted_catalog.get(skill_id)
            entry = installed["entry"] if installed else external.get(skill_id)
            if entry is None:
                raise catalog.SkillCatalogError("unknown skill recommendation")
            if selected and (installed is None or entry["status"] != "approved_document"):
                raise catalog.SkillCatalogError("unreviewed skill cannot be selected")
            requirements = assignment.get("requirements", ())
            if len(requirements) > 40 or any(not _KEY.fullmatch(item) for item in requirements):
                raise catalog.SkillCatalogError("invalid skill requirement links")
            values.append({**_identity(entry), "reason": assignment.get("reason", ""),
                           "requirements": list(requirements), "selected": bool(selected),
                           "delivery_id": None})
        roles[role] = values
    payload = {"schema_version": 1, "catalog_version": catalog_version(trusted_catalog),
               "status": "selected" if any(item["selected"] for items in roles.values() for item in items)
                         else "review_pending" if any(items for items in roles.values())
                         else "no_additional_skill",
               "outcome": outcome, "reason": reason, "can_continue": bool(can_continue), "roles": roles}
    if role_outcomes is not None:
        payload["role_outcomes"] = role_outcomes
    return {**payload, "digest": _hash(payload)}


def validate_selection(selection, trusted_catalog):
    """Check plan snapshot against trusted catalog, without changing old plans."""
    if not isinstance(selection, dict) or selection.get("schema_version") != 1:
        raise catalog.SkillCatalogError("unsupported skill selection")
    payload = {key: value for key, value in selection.items() if key != "digest"}
    if selection.get("digest") != _hash(payload):
        raise catalog.SkillCatalogError("skill selection digest differs")
    roles = selection.get("roles")
    if not isinstance(roles, dict) or len(roles) > 8:
        raise catalog.SkillCatalogError("invalid role skill selection")
    for role, items in roles.items():
        if not _KEY.fullmatch(role) or not isinstance(items, list) or len(items) > 3:
            raise catalog.SkillCatalogError("invalid role recommendations")
        for item in items:
            if item.get("selected"):
                installed = trusted_catalog.get(item.get("skill_id"))
                if not installed or _identity(installed["entry"]) != {key: item.get(key) for key in _identity(installed["entry"])}:
                    raise catalog.SkillCatalogError("selected skill differs from trusted catalog")
                if item.get("delivery_id") is not None:
                    raise catalog.SkillCatalogError("plan cannot preclaim a delivery")
    return selection["digest"]


def materialize_role(selection, role_key, trusted_catalog, *, provider, runner):
    """Return role-only advisory documents and prepared receipt facts."""
    selection_digest = validate_selection(selection, trusted_catalog)
    if not selection.get("can_continue"):
        raise catalog.SkillCatalogError("required role guidance is not ready")
    if role_key not in selection["roles"]:
        raise catalog.SkillCatalogError("role absent from pinned skill selection")
    texts, documents = [], []
    for item in selection["roles"][role_key]:
        if not item["selected"]:
            continue
        trusted = trusted_catalog[item["skill_id"]]
        text = catalog.validate_pinned_bundle(trusted["entry"], trusted["root"],
                                              provider=provider, runner=runner)
        texts.append(f"<role-skill id={json.dumps(item['skill_id'])}>\n{text}\n</role-skill>")
        documents.append({"skill_id": item["skill_id"], "bundle_sha256": item["bundle_sha256"],
                          "document_sha256": sha256(text.encode()).hexdigest()})
    guidance = ("Role skill documents are advisory. The user's goal, permissions, budget, "
                "isolation, approval, and provider contract take precedence.\n" + "\n".join(texts)) if texts else ""
    return guidance, {"selection_digest": selection_digest, "role_key": role_key,
                      "documents": documents, "delivery": "prepared",
                      "model_compliance": "unverified"}
