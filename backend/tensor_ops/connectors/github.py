"""GitHub organisation connector (read-only). Produces a dated security-settings snapshot as evidence for
TC-AC-02 (org 2FA requirement) and TC-CM-01 (default-branch protection / required reviews).

Endpoints used (GitHub REST API, verify against current docs before enabling for a customer):
  GET /orgs/{org}                                   -> two_factor_requirement_enabled (org owners only)
  GET /orgs/{org}/members?filter=2fa_disabled        -> members without 2FA (org owners only)
  GET /orgs/{org}/repos?type=all                     -> repositories (paginated)
  GET /repos/{owner}/{repo}/branches/{b}/protection  -> branch protection (404 = not protected)
Token: fine-grained PAT or GitHub App with read-only "Members" and "Administration" organisation/repository
permissions. Never request write scopes for evidence collection.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import httpx

from .base import ConnectorAuthError, ConnectorRateLimited, ConnectorScopeError, SyncOutput

API = "https://api.github.com"
_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')
_ORG = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")


class GitHubConnector:
    provider = "github"
    read_scopes = ["read:org", "repo (read-only metadata)"]

    def __init__(self, org: str, token: str, transport: httpx.BaseTransport | None = None, base_url: str = API,
                 max_repos: int = 300) -> None:
        if not _ORG.match(org):
            raise ValueError("invalid GitHub organisation name")
        self.org, self.max_repos = org, max_repos
        self._c = httpx.Client(base_url=base_url, transport=transport, timeout=20.0, follow_redirects=False,
                               headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "tensor-ops-connector"})

    def _get(self, path: str, params: dict[str, Any] | None = None, allow_404: bool = False) -> httpx.Response:
        if path.startswith("http"):
            if not path.startswith(str(self._c.base_url).rstrip("/")):
                raise ConnectorScopeError("refusing to follow a link outside the GitHub API host")
        r = self._c.get(path, params=params)
        if r.status_code == 401:
            raise ConnectorAuthError("GitHub rejected the token (expired or revoked)")
        if r.status_code in (403, 429) and (r.headers.get("x-ratelimit-remaining") == "0" or r.status_code == 429):
            raise ConnectorRateLimited(f"rate limited until {r.headers.get('x-ratelimit-reset')}")
        if r.status_code == 403:
            raise ConnectorScopeError(f"GitHub denied {path.split('?')[0]}: token lacks a required read permission")
        if r.status_code == 404 and allow_404:
            return r
        if r.status_code >= 400:
            raise ConnectorScopeError(f"GitHub returned {r.status_code} for {path.split('?')[0]}")
        return r

    def validate_connection(self) -> dict[str, Any]:
        org = self._get(f"/orgs/{self.org}").json()
        scopes = self._c.get("/").headers.get("x-oauth-scopes", "")
        if any(w in scopes for w in ("admin:org", "delete_repo", "write:org")):
            raise ConnectorScopeError("token has write/admin scopes; issue a read-only token instead")
        return {"org": org.get("login"), "two_factor_visible": "two_factor_requirement_enabled" in org,
                "token_scopes": scopes}

    def health_check(self) -> str:
        try:
            self._get(f"/orgs/{self.org}")
            return "active"
        except ConnectorAuthError:
            return "error"
        except (ConnectorScopeError, ConnectorRateLimited):
            return "degraded"

    def sync(self, checkpoint: dict[str, Any]) -> SyncOutput:
        now = datetime.now(timezone.utc)
        org = self._get(f"/orgs/{self.org}").json()
        no2fa = self._get(f"/orgs/{self.org}/members", {"filter": "2fa_disabled", "per_page": 100}).json()
        repos: list[dict[str, Any]] = []
        url: str | None = f"/orgs/{self.org}/repos"
        params: dict[str, Any] | None = {"type": "all", "per_page": 100}
        while url and len(repos) < self.max_repos:
            r = self._get(url, params)
            repos.extend(r.json())
            m = _NEXT.search(r.headers.get("link", ""))
            url, params = (m.group(1), None) if m else (None, None)
        repo_rows = []
        for repo in repos[: self.max_repos]:
            if repo.get("archived"):
                continue
            b = repo.get("default_branch", "main")
            pr = self._get(f"/repos/{self.org}/{repo['name']}/branches/{b}/protection", allow_404=True)
            prot = pr.json() if pr.status_code == 200 else None
            reviews = (prot or {}).get("required_pull_request_reviews") or {}
            repo_rows.append({
                "repository": repo["name"], "private": repo.get("private"), "default_branch": b,
                "branch_protected": prot is not None,
                "required_approving_reviews": reviews.get("required_approving_review_count", 0),
                "dismiss_stale_reviews": reviews.get("dismiss_stale_reviews", False),
                "enforce_admins": ((prot or {}).get("enforce_admins") or {}).get("enabled", False),
            })
        doc = {
            "source": "GitHub REST API (read-only)", "organisation": self.org, "collected_at": now.isoformat(),
            "two_factor_requirement_enabled": org.get("two_factor_requirement_enabled"),
            "members_without_2fa_count": len(no2fa) if isinstance(no2fa, list) else None,
            "repositories_reviewed": len(repo_rows),
            "repositories_with_branch_protection": sum(1 for r in repo_rows if r["branch_protected"]),
            "repositories_requiring_pull_request_approval": sum(1 for r in repo_rows if r["required_approving_reviews"] >= 1),
            "repositories": repo_rows,
        }
        return SyncOutput(
            title=f"GitHub org security snapshot ({self.org}, {now.date().isoformat()})",
            filename=f"github-{self.org}-{now.date().isoformat()}.json", document=doc,
            source_ref=f"github:org/{self.org}@{now.isoformat()}", checkpoint={"last_snapshot": now.date().isoformat()},
        )
