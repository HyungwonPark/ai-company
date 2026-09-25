"""Bounded, read-only research and pinned document guidance for project roles.

Public material is untrusted data. Only an operator-pinned, locally inspected
document bundle may be delivered to a model; this module never installs or runs
anything found on the web.
"""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import stat
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_FILE_BYTES = 64 * 1024
MAX_BUNDLE_BYTES = 192 * 1024
MAX_SEARCH_BYTES = 128 * 1024
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SEGMENT = re.compile(r"^[A-Za-z0-9_.-]+$")
_COMMIT = re.compile(r"^[a-fA-F0-9]{40}$")
_TERM = re.compile(r"^[a-z][a-z0-9+#.-]{1,39}$")
_SHA = re.compile(r"^[a-f0-9]{64}$")
OFFICIAL_HOSTS = frozenset({"agentskills.io", "docs.github.com", "www.anthropic.com",
                            "developers.openai.com"})


class SkillCatalogError(ValueError):
    """A candidate cannot be treated as approved guidance."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise SkillCatalogError("public source redirected")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _relative(value):
    if not isinstance(value, str) or not value or "\\" in value or "?" in value or "#" in value:
        raise SkillCatalogError("invalid skill file path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise SkillCatalogError("skill file path escapes bundle")
    return value


def _paths(main, references, license_path):
    result = [main, *references]
    if license_path:
        result.append(license_path)
    if len(result) > 12 or len(set(result)) != len(result):
        raise SkillCatalogError("too many or duplicate skill files")
    return tuple(_relative(value) for value in result)


def _read_local(root, relative):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise SkillCatalogError("skill directory is unavailable or symlinked")
    path = root
    for part in relative.split("/"):
        path = path / part
        if path.is_symlink():
            raise SkillCatalogError("skill bundle contains a symlink")
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            raise SkillCatalogError("skill file is not a bounded regular file")
        data = path.read_bytes()
    except OSError as error:
        raise SkillCatalogError("skill file is missing or unreadable") from error
    if len(data) > MAX_FILE_BYTES:
        raise SkillCatalogError("skill file exceeds size limit")
    return data


def _name(data, fallback):
    text = data.decode("utf-8")
    if text.startswith("---\n"):
        match = re.search(r"^name:\s*['\"]?([^\n'\"]{1,100})", text[4:].split("\n---", 1)[0], re.M)
        if match:
            return match.group(1).strip()
    return fallback


def _bundle_hash(source_url, source_ref, file_hashes):
    payload = {"source_url": source_url, "source_ref": source_ref,
               "files": dict(sorted(file_hashes.items()))}
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode()).hexdigest()


def inspect_installed_skill(root, *, skill_id, source_url, source_ref,
                            expected_sha256, reference_paths=(), license_path=None,
                            license_id="unknown", redistribution="not_assessed",
                            providers=(), runners=(), dependencies=(), capabilities=(), permissions=()):
    """Inspect an operator-selected local bundle against an independently pinned hash.

    The caller must supply the expected bundle hash from a trusted catalog;
    computing it from this scan and immediately approving it would not review it.
    """
    if not _ID.fullmatch(skill_id) or not _SHA.fullmatch(expected_sha256):
        raise SkillCatalogError("invalid skill identity or pinned hash")
    if urlsplit(source_url).hostname == "github.com" and _github_source(source_url)[2] != source_ref.lower():
        raise SkillCatalogError("installed skill source and ref differ")
    paths = _paths("SKILL.md", reference_paths, license_path)
    if any(not path.endswith((".md", ".txt")) for path in reference_paths):
        raise SkillCatalogError("document-only guidance cannot include executable references")
    if not license_path or license_id == "unknown" or redistribution not in ("permitted", "internal_only"):
        raise SkillCatalogError("approved guidance needs an explicit license decision")
    if not providers or not runners:
        raise SkillCatalogError("approved guidance needs provider and runner compatibility")
    files = {path: _read_local(root, path) for path in paths}
    if sum(map(len, files.values())) > MAX_BUNDLE_BYTES:
        raise SkillCatalogError("skill bundle exceeds size limit")
    hashes = {path: sha256(data).hexdigest() for path, data in files.items()}
    actual = _bundle_hash(source_url, source_ref, hashes)
    if actual != expected_sha256:
        raise SkillCatalogError("installed skill differs from the reviewed bundle")
    return {"skill_id": skill_id, "name": _name(files["SKILL.md"], skill_id),
            "source_url": source_url, "source_ref": source_ref, "version": source_ref,
            "bundle_sha256": actual, "files": hashes,
            "license": {"id": license_id, "path": license_path,
                        "redistribution": redistribution},
            "compatibility": {"providers": sorted(set(providers)), "runners": sorted(set(runners))},
            "dependencies": list(dependencies), "permissions": list(permissions),
            "capabilities": sorted(set(capabilities)),
            "status": "review_pending" if dependencies else "approved_document",
            "origin": "installed", "inspected_at": _now(),
            "format_check": "pass", "content_review": "operator_pinned",
            "delivery": "not_delivered", "task_evaluation": "not_evaluated"}


def validate_pinned_bundle(entry, root, *, provider, runner):
    """Re-read every file at delivery; return bounded text only for approved guidance."""
    if entry.get("origin") != "installed" or entry.get("status") != "approved_document":
        raise SkillCatalogError("skill has not been approved for document delivery")
    compatibility = entry.get("compatibility", {})
    if provider not in compatibility.get("providers", ()) or runner not in compatibility.get("runners", ()):
        raise SkillCatalogError("skill is incompatible with this provider or runner")
    if entry.get("dependencies"):
        raise SkillCatalogError("skill requires tools or packages not delivered as documents")
    paths = tuple(entry.get("files", {}))
    license_path = entry.get("license", {}).get("path")
    if "SKILL.md" not in paths or license_path not in paths:
        raise SkillCatalogError("incomplete pinned skill bundle")
    files = {path: _read_local(root, _relative(path)) for path in paths}
    hashes = {path: sha256(data).hexdigest() for path, data in files.items()}
    if hashes != entry["files"] or _bundle_hash(entry["source_url"], entry["source_ref"], hashes) != entry["bundle_sha256"]:
        raise SkillCatalogError("skill changed after plan review")
    document_paths = ["SKILL.md", *sorted(path for path in paths if path not in ("SKILL.md", license_path))]
    if any(not path.endswith((".md", ".txt")) for path in document_paths):
        raise SkillCatalogError("executable skill material cannot be delivered")
    return "\n\n".join(f"<skill-document path={json.dumps(path)}>\n{files[path].decode('utf-8')}\n</skill-document>"
                       for path in document_paths)


def _github_source(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise SkillCatalogError("only public GitHub pinned commit URLs are supported")
    parts = parsed.path.strip("/").split("/")
    if (len(parts) < 5 or parts[2] != "blob"
            or not all(_SEGMENT.fullmatch(part) and part not in (".", "..") for part in parts[:2])
            or not _COMMIT.fullmatch(parts[3])):
        raise SkillCatalogError("skill source needs owner/repo/blob/40-character-commit/path")
    relative = _relative("/".join(parts[4:]))
    if not relative.endswith("SKILL.md"):
        raise SkillCatalogError("skill source must identify SKILL.md")
    base = f"https://raw.githubusercontent.com/{parts[0]}/{parts[1]}/{parts[3]}"
    return base, relative, parts[3].lower()


def _public_get(url, max_bytes, timeout):
    # Host and URL are constructed by _github_source or search_public_skills.
    request = Request(url, headers={"Accept": "text/plain", "User-Agent": "AI-Company-Skill-Research/1"})
    with build_opener(_NoRedirect).open(request, timeout=timeout) as response:
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise SkillCatalogError("public response exceeds size limit")
    return data


def fetch_public_skill(url, *, reference_paths=(), license_path=None,
                       max_bytes=MAX_BUNDLE_BYTES, timeout=5):
    """Read exact files from a public pinned GitHub commit; never approve them."""
    base, main, commit = _github_source(url)
    parent = main.rsplit("/", 1)[0] if "/" in main else ""
    relative_refs = tuple(f"{parent}/{path}" if parent else path for path in reference_paths)
    paths = _paths(main, relative_refs, license_path)
    if max_bytes < 1 or max_bytes > MAX_BUNDLE_BYTES or timeout <= 0 or timeout > 15:
        raise SkillCatalogError("unbounded public lookup")
    files = {}
    total = 0
    started = monotonic()
    try:
        for path in paths:
            encoded = "/".join(quote(part, safe="") for part in path.split("/"))
            data = _public_get(f"{base}/{encoded}", min(MAX_FILE_BYTES, max_bytes - total), timeout)
            total += len(data)
            files[path] = data
    except (HTTPError, URLError, OSError, SkillCatalogError) as error:
        return {"status": "lookup_failed", "source_url": url, "source_ref": commit,
                "checked_at": _now(), "lookups_used": len(files) + 1,
                "elapsed_ms": round((monotonic() - started) * 1000), "reason": str(error)[:160]}
    hashes = {path: sha256(data).hexdigest() for path, data in files.items()}
    try:
        name = _name(files[main], "공개 스킬 후보")
        format_check = "utf8_readable"
    except UnicodeDecodeError:
        name, format_check = "공개 스킬 후보", "invalid_utf8"
    return {"skill_id": main.rsplit("/", 2)[-2] if "/" in main else "skill",
            "name": name, "source_url": url,
            "source_ref": commit, "version": commit, "bundle_sha256": _bundle_hash(url, commit, hashes),
            "files": hashes, "license": {"id": "unreviewed", "path": license_path,
                                          "redistribution": "not_assessed"},
            "compatibility": {"providers": [], "runners": []}, "dependencies": [],
            "permissions": [],
            "status": "review_pending", "origin": "public_github", "checked_at": _now(),
            "lookups_used": len(paths), "bytes_read": total,
            "elapsed_ms": round((monotonic() - started) * 1000),
            "format_check": format_check, "content_review": "not_reviewed",
            "delivery": "not_delivered", "task_evaluation": "not_evaluated"}


def search_public_skills(term, *, allowed_terms, max_results=6, timeout=5):
    """Search public repo metadata using one allowlisted, generic technical term."""
    if (not _TERM.fullmatch(term) or term not in allowed_terms or max_results < 1 or max_results > 6
            or timeout <= 0 or timeout > 15):
        raise SkillCatalogError("unapproved or unbounded public search term")
    query = quote(f"agent-skills {term} in:name,description", safe="")
    url = f"https://api.github.com/search/repositories?q={query}&per_page={max_results}"
    started = monotonic()
    try:
        raw = _public_get(url, MAX_SEARCH_BYTES, timeout)
        payload = json.loads(raw)
        items = payload["items"][:max_results]
        repos = [{"repository": item["full_name"], "url": item["html_url"]}
                 for item in items if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", item["full_name"])
                 and item["html_url"] == f"https://github.com/{item['full_name']}"]
        status, reason = ("found" if repos else "no_results"), None
    except (HTTPError, URLError, OSError, ValueError, KeyError, TypeError) as error:
        repos, status, reason = [], "lookup_failed", str(error)[:160]
    return {"status": status, "term": term, "repositories": repos, "checked_at": _now(),
            "lookups_used": 1, "elapsed_ms": round((monotonic() - started) * 1000),
            "max_results": max_results, "reason": reason}


def read_official_reference(url, *, max_bytes=MAX_FILE_BYTES, timeout=5):
    """Read a bounded public primary document; it never becomes approved skill text."""
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in OFFICIAL_HOSTS or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or max_bytes < 1 or max_bytes > MAX_FILE_BYTES or timeout <= 0 or timeout > 15):
        raise SkillCatalogError("unapproved official documentation URL or limit")
    started = monotonic()
    try:
        data = _public_get(url, max_bytes, timeout)
    except (HTTPError, URLError, OSError, SkillCatalogError) as error:
        return {"status": "lookup_failed", "source_url": url, "checked_at": _now(),
                "lookups_used": 1, "elapsed_ms": round((monotonic() - started) * 1000),
                "reason": str(error)[:160]}
    return {"status": "read_unreviewed", "source_url": url, "checked_at": _now(),
            "content_sha256": sha256(data).hexdigest(), "bytes_read": len(data),
            "lookups_used": 1, "elapsed_ms": round((monotonic() - started) * 1000)}


def research_key(*, capabilities, environment, roles, catalog_version):
    """Cache identity over already-redacted capability terms, never a private goal."""
    terms = sorted(set(capabilities))
    if any(not _TERM.fullmatch(term) for term in terms):
        raise SkillCatalogError("research key accepts generic capability terms only")
    value = {"capabilities": terms, "environment": environment,
             "roles": sorted(set(roles)), "catalog_version": catalog_version}
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def recommend_skills(role_capabilities, catalog, *, external_candidates=(), lookup_status=None):
    """Rank pinned installed entries first; public entries remain pending review."""
    if len(external_candidates) > 6:
        raise SkillCatalogError("too many external candidates")
    installed = [entry for entry in catalog if entry.get("status") == "approved_document"]
    result = {}
    for role, needed in role_capabilities.items():
        if not needed:
            result[role] = {"status": "no_additional_skill", "recommendations": []}
            continue
        matching = [entry for entry in [*installed, *external_candidates]
                    if set(needed) & set(entry.get("capabilities", ()))][:3]
        if matching:
            result[role] = {"status": "recommended", "recommendations": [
                {"skill_id": entry["skill_id"], "bundle_sha256": entry["bundle_sha256"],
                 "status": entry["status"], "matched_capabilities": sorted(set(needed) & set(entry["capabilities"]))}
                for entry in matching]}
        else:
            result[role] = {"status": "lookup_failed" if lookup_status == "lookup_failed" else "not_found",
                            "recommendations": []}
    return result
