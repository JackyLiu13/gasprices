"""Read and write the repo's data files through the GitHub API.

Lambda has no git and a read-only code directory, so the CSVs are pulled in at
the start of a run and pushed back at the end. Keeping git as the store rather
than moving to S3 is deliberate: `backfill_forecasts.py` recovers published
forecasts by reading `pred` arrays out of commit history, so the history is an
operational data store here, not a nicety.

Everything is stdlib urllib — the whole backend has no third-party imports, and
that is what lets it deploy as a plain zip with no layers and no container.

ONE COMMIT PER RUN. The obvious approach, PUT /contents once per file, makes a
separate commit each time and can leave station_prices.csv updated while
data.json is stale if the third call fails. The Trees API costs a few more
requests and lands everything atomically.

THE CONFLICT IS NOT HYPOTHETICAL. The scheduled build and a logged price can run
at the same time, and git refs only move fast-forward — the second writer gets a
422. Re-read, re-apply, retry. Never force: that is how a price you logged at
the pump disappears.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request

API = "https://api.github.com"
UA = "gasprices-lambda/1.0"


class GitHubError(RuntimeError):
    pass


class GitHubStore:
    def __init__(self, repo: str, token: str, branch: str = "main"):
        self.repo = repo            # "owner/name"
        self.token = token
        self.branch = branch

    # --- plumbing ----------------------------------------------------------

    def _req(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"{API}/repos/{self.repo}/{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": UA,
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            # GitHub explains itself in the body; without this a 403 arrives as
            # a bare "Forbidden" and you cannot tell a missing token scope from
            # branch protection from a bad path. Callers that handle specific
            # codes still re-raise, so the code stays inspectable.
            detail = ""
            try:
                detail = json.loads(e.read()).get("message", "")
            except Exception:
                pass
            e.gp_detail = detail
            e.gp_where = f"{method} {path}"
            raise
        return json.loads(raw) if raw else {}

    # --- reading -----------------------------------------------------------

    def read(self, path: str) -> str | None:
        """File contents, or None if it doesn't exist yet."""
        try:
            got = self._req("GET", f"contents/{path}?ref={self.branch}")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise GitHubError(f"GET {path}: {e.code} {e.reason}") from e
        return base64.b64decode(got["content"]).decode("utf-8")

    def pull(self, paths: list[str], dest) -> list[str]:
        """Copy `paths` from the repo into the `dest` directory tree."""
        pulled = []
        for p in paths:
            text = self.read(p)
            if text is None:
                continue
            target = dest / p
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
            pulled.append(p)
        return pulled

    # --- writing -----------------------------------------------------------

    def commit(self, files: dict[str, str], message: str,
               retries: int = 3) -> str | None:  # noqa: C901
        """Commit several files atomically. Returns the sha, or None if the
        content already matches what's on the branch."""
        if not files:
            return None

        last: Exception | None = None
        for attempt in range(retries):
            # Re-read the ref every attempt: on a conflict the whole point is
            # that someone else moved it since we last looked.
            ref = self._req("GET", f"git/ref/heads/{self.branch}")
            base_sha = ref["object"]["sha"]
            base_commit = self._req("GET", f"git/commits/{base_sha}")

            changed = {p: c for p, c in files.items() if self.read(p) != c}
            if not changed:
                return None

            tree = self._req("POST", "git/trees", {
                "base_tree": base_commit["tree"]["sha"],
                # `content` inline avoids a separate blob call per file.
                "tree": [{"path": p, "mode": "100644", "type": "blob",
                          "content": c} for p, c in changed.items()],
            })
            commit = self._req("POST", "git/commits", {
                "message": message,
                "tree": tree["sha"],
                "parents": [base_sha],
            })

            try:
                self._req("PATCH", f"git/refs/heads/{self.branch}",
                          {"sha": commit["sha"]})
                return commit["sha"]
            except urllib.error.HTTPError as e:
                # 422 is "not a fast-forward" — the branch moved under us.
                if e.code in (409, 422) and attempt < retries - 1:
                    last = e
                    time.sleep(1 + attempt)
                    continue
                raise GitHubError(
                    f"update ref: {e.code} {e.reason}"
                    f" — {getattr(e, 'gp_detail', '')}") from e

        raise GitHubError(f"gave up after {retries} attempts: {last}")


def from_env() -> GitHubStore:
    """Build a store from the environment the Lambda template provides."""
    repo = os.environ.get("GP_REPO")
    token = os.environ.get("GP_GITHUB_TOKEN")
    if not repo or not token:
        raise GitHubError("GP_REPO and GP_GITHUB_TOKEN must both be set")
    return GitHubStore(repo, token, os.environ.get("GP_BRANCH", "main"))
