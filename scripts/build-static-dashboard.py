#!/usr/bin/env python3
"""Build a static GitHub Pages snapshot of the forge dashboard.

Captures real session data, scrubs secrets, and inlines everything into
a single HTML file that works without a backend server.

Usage:
    python scripts/build-static-dashboard.py [output_path]
"""

import json
import re
import sys
from pathlib import Path

# Import the dashboard server module to reuse its data builders
SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from forge.dashboard.server import EnhancedSessionHandler  # noqa: E402

DASHBOARD_HTML = REPO_ROOT / "src" / "forge" / "dashboard" / "dashboard.html"
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "index.html"

# Patterns that look like secrets — redact these from all output
_SECRET_PATTERNS = [
    re.compile(r"sk-ant-api\S+"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"sbp_[a-f0-9]{20,}"),
    re.compile(r"ghp_\w{20,}"),
    re.compile(r"gho_\w{20,}"),
    re.compile(r"xoxb-\S+"),
    re.compile(r"xoxp-\S+"),
    re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"),
    re.compile(
        r"(?i)(password|secret|api[_-]?key|access[_-]?token|service[_-]?role[_-]?key)"
        r"\s*[=:]\s*\S+"
    ),
    # Email addresses
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    # User home paths
    re.compile(r"/Users/[a-zA-Z0-9._-]+"),
    re.compile(r"/home/[a-zA-Z0-9._-]+"),
]


def scrub(obj):
    """Recursively scrub secrets from any JSON-serializable object."""
    if isinstance(obj, str):
        text = obj
        for p in _SECRET_PATTERNS[:-2]:
            text = p.sub("[REDACTED]", text)
        # Replace user paths with placeholder
        text = re.sub(r"/Users/[a-zA-Z0-9._-]+", "/Users/dev", text)
        text = re.sub(r"/home/[a-zA-Z0-9._-]+", "/home/dev", text)
        return text
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(item) for item in obj]
    return obj


class FakeHandler(EnhancedSessionHandler):
    """Subclass that skips HTTP init but inherits all data methods."""

    def __init__(self):
        # Don't call super().__init__() — skip the BaseHTTPRequestHandler setup
        self._captured = None
        self.path = "/"

    def _json_response(self, code, data):
        self._captured = (code, data)

    def send_response(self, *a, **kw):
        pass

    def send_header(self, *a, **kw):
        pass

    def end_headers(self):
        pass


def build_sessions_data():
    """Call get_all_sessions() against real session data."""
    return FakeHandler().get_all_sessions()


def build_artifacts_data(session_dir: str):
    """Call the artifacts logic for one session."""
    h = FakeHandler()
    h.path = f"/artifacts?session_dir={session_dir}"
    h.handle_artifacts()
    return h._captured[1] if h._captured else {}


def main():
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT

    print("Reading dashboard HTML template...")
    html = DASHBOARD_HTML.read_text()

    print("Building sessions data from real forge sessions...")
    sessions = build_sessions_data()
    sessions = scrub(sessions)

    # Count sessions for log
    projects = sessions.get("projects", [])
    print(f"  → {len(projects)} projects")

    # Re-fetch raw (un-scrubbed) sessions to extract original session_dir paths
    raw_sessions = build_sessions_data()
    raw_projects = raw_sessions.get("projects", [])

    print("Building artifacts data for each session...")
    artifacts_by_dir = {}
    for project in raw_projects:
        # Each project has a planner and a list of executors
        for entity in [project.get("planner")] + (project.get("executors") or []):
            if entity and entity.get("session_dir"):
                sd = entity["session_dir"]
                raw_artifacts = build_artifacts_data(sd)
                artifacts_by_dir[sd] = scrub(raw_artifacts)

    print(f"  → {len(artifacts_by_dir)} artifact bundles collected")

    # Now scrub the original session_dir paths in our artifact mapping
    scrubbed_artifacts = {}
    for original_path, data in artifacts_by_dir.items():
        scrubbed_path = re.sub(r"/Users/[a-zA-Z0-9._-]+", "/Users/dev", original_path)
        scrubbed_artifacts[scrubbed_path] = data

    print("Injecting data and disabling network calls...")

    # Inject data as global JS constants — use window.* so they're accessible
    # from other script blocks (const declarations are not attached to window)
    static_data_js = f"""
<script>
// Static data baked in by build-static-dashboard.py
window.STATIC_SESSIONS = {json.dumps(sessions, indent=2)};
window.STATIC_ARTIFACTS = {json.dumps(scrubbed_artifacts, indent=2)};
window.STATIC_MODE = true;
</script>
"""

    # Inject static data right after <body>
    html = html.replace("<body>", f"<body>\n{static_data_js}", 1)

    # Replace fetchData() to use static data
    new_fetch_data = """async function fetchData() {
  if (window.STATIC_MODE) {
    const data = window.STATIC_SESSIONS;
    const dataJSON = JSON.stringify(data);
    if (dataJSON === lastDataJSON && !isFirstRender) return;
    lastDataJSON = dataJSON;
    Object.keys(artifactCache).forEach(k => delete artifactCache[k]);
    render(data);
    return;
  }
  try {
    const res = await fetch(`${API_BASE}/sessions`);
    if (res.ok) {
      const data = await res.json();
      const dataJSON = JSON.stringify(data);
      if (dataJSON === lastDataJSON && !isFirstRender) return;
      lastDataJSON = dataJSON;
      Object.keys(artifactCache).forEach(k => delete artifactCache[k]);
      render(data);
      return;
    }
  } catch {}
  document.getElementById('main').innerHTML = `
    <div class="empty">
      <div class="empty-icon">&#9888;</div>
      <div class="empty-text">Cannot connect to Forge dashboard server.<br>Run: <code>forge dashboard</code></div>
    </div>
  `;
}"""

    old_fetch_data = """async function fetchData() {
  try {
    const res = await fetch(`${API_BASE}/sessions`);
    if (res.ok) {
      const data = await res.json();
      const dataJSON = JSON.stringify(data);
      if (dataJSON === lastDataJSON && !isFirstRender) return; // Nothing changed, skip re-render
      lastDataJSON = dataJSON;
      // Clear artifact cache when session data changes
      Object.keys(artifactCache).forEach(k => delete artifactCache[k]);
      render(data);
      return;
    }
  } catch {}
  document.getElementById('main').innerHTML = `
    <div class="empty">
      <div class="empty-icon">&#9888;</div>
      <div class="empty-text">Cannot connect to Forge dashboard server.<br>Run: <code>forge dashboard</code></div>
    </div>
  `;
}"""

    if old_fetch_data not in html:
        print("WARNING: could not find fetchData() to replace")
    else:
        html = html.replace(old_fetch_data, new_fetch_data)

    # Replace fetchArtifacts() to use static data
    new_fetch_artifacts = """async function fetchArtifacts(sessionDir) {
  if (!sessionDir) return null;
  if (artifactCache[sessionDir]) return artifactCache[sessionDir];
  if (window.STATIC_MODE) {
    const data = window.STATIC_ARTIFACTS[sessionDir] || null;
    if (data) artifactCache[sessionDir] = data;
    return data;
  }
  try {
    const res = await fetch(`${API_BASE}/artifacts?session_dir=${encodeURIComponent(sessionDir)}`);
    if (res.ok) {
      const data = await res.json();
      artifactCache[sessionDir] = data;
      return data;
    }
  } catch {}
  return null;
}"""

    old_fetch_artifacts = """async function fetchArtifacts(sessionDir) {
  if (!sessionDir) return null;
  if (artifactCache[sessionDir]) return artifactCache[sessionDir];
  try {
    const res = await fetch(`${API_BASE}/artifacts?session_dir=${encodeURIComponent(sessionDir)}`);
    if (res.ok) {
      const data = await res.json();
      artifactCache[sessionDir] = data;
      return data;
    }
  } catch {}
  return null;
}"""

    if old_fetch_artifacts not in html:
        print("WARNING: could not find fetchArtifacts() to replace")
    else:
        html = html.replace(old_fetch_artifacts, new_fetch_artifacts)

    # Disable auto-refresh polling
    html = html.replace(
        "fetchData();\nsetInterval(fetchData, 3000);",
        "fetchData();\n// auto-refresh disabled in static mode"
    )

    # Add a "demo" banner
    banner = """
<div style="background: linear-gradient(135deg, rgba(129,140,248,0.15), rgba(192,132,252,0.15)); border-bottom: 1px solid rgba(129,140,248,0.3); padding: 12px 24px; text-align: center; font-size: 14px; color: #a5b4fc;">
  <strong>Live Demo</strong> &mdash; Static snapshot of the forge dashboard with real session data.
  Run <code style="background: rgba(0,0,0,0.3); padding: 2px 8px; border-radius: 4px;">forge dashboard</code> locally for the live version.
  &nbsp;<a href="https://github.com/elitecoder/forge" style="color: #a78bfa; text-decoration: underline;">Source &rarr;</a>
</div>
"""
    html = html.replace("<body>", f"<body>\n{banner}", 1)

    # Final secret check
    secret_check_patterns = [
        r"sk-ant-api[A-Za-z0-9_-]{10,}",
        r"sbp_[a-f0-9]{30,}",
        r"ANTHROPIC_API_KEY=sk-",
        r"eyJ[A-Za-z0-9_-]{30,}\.eyJ[A-Za-z0-9_-]{30,}\.",
    ]
    for pattern in secret_check_patterns:
        matches = re.findall(pattern, html)
        if matches:
            print(f"FATAL: secret leaked through scrubber: {pattern}")
            print(f"  Found {len(matches)} matches, e.g.: {matches[0][:50]}")
            sys.exit(1)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html)
    size_kb = len(html) // 1024
    print(f"\nDone! Wrote {output} ({size_kb} KB)")


if __name__ == "__main__":
    main()
