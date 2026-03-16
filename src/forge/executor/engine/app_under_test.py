"""app_under_test step — start the application and verify it's ready with Playwright."""

import os
import subprocess
import time
from pathlib import Path

from forge.executor.engine import pipeline_ops
from forge.executor.engine.state import PipelineState
from forge.executor.engine.utils import DEFAULT_DEV_PORT, is_bazel_repo

# Inline Playwright script to verify the dev server is up and serving HTML.
_HEALTH_CHECK_SCRIPT = """\
const {{ chromium }} = require('playwright');

(async () => {{
    const browser = await chromium.launch({{ headless: true }});
    const context = await browser.newContext({{ ignoreHTTPSErrors: true }});
    const page = await context.newPage();
    try {{
        const response = await page.goto('{url}', {{ timeout: 30000, waitUntil: 'domcontentloaded' }});
        if (response && response.ok()) {{
            console.log('HEALTHY');
        }} else {{
            console.error('UNHEALTHY: HTTP ' + (response ? response.status() : 'no response'));
            process.exit(1);
        }}
    }} catch (e) {{
        console.error('UNHEALTHY: ' + e.message);
        process.exit(1);
    }} finally {{
        await browser.close();
    }}
}})();
"""


def _find_playwright_runner(preset) -> str | None:
    """Resolve the Playwright runner directory from preset config."""
    vt_config = getattr(preset, "visual_test_config", {})
    runner_dir = vt_config.get("playwright_runner_dir", "")
    if not runner_dir:
        return None
    runner_dir = runner_dir.replace("${PRESET_DIR}", str(preset.preset_dir))
    runner_dir = str(Path(runner_dir).expanduser().resolve())
    if Path(runner_dir).is_dir() and (Path(runner_dir) / "run.js").is_file():
        return runner_dir
    return None


def execute(cwd: str, log_fn=None) -> bool:
    """Start the dev server, verify with Playwright, return success.

    The dev server process stays running for subsequent steps.
    The port and PID are stored in pipeline state.
    """
    def log(msg: str):
        if log_fn:
            log_fn("app_under_test", msg)

    state = pipeline_ops.require_state()
    preset = pipeline_ops.load_preset_for_state(state)
    dev_config = getattr(preset, "dev_server", None)
    if not dev_config:
        log("no dev_server config in preset")
        return False

    # VPN check
    vpn_host = dev_config.get("vpn_check_host")
    if vpn_host:
        import socket
        try:
            sock = socket.create_connection((vpn_host, 443), timeout=5)
            sock.close()
        except OSError:
            log(f"VPN not connected — cannot reach {vpn_host}")
            return False

    # Kill any stale dev server from a previous run
    _kill_existing_dev_server(state)

    # Allocate port
    info = pipeline_ops.allocate_dev_server()
    port = info["port"]

    # Pick command based on repo type
    if is_bazel_repo(cwd) and dev_config.get("bazel_command"):
        dev_cmd = dev_config["bazel_command"]
    else:
        dev_cmd = dev_config.get("command", "")

    if not dev_cmd:
        log("no dev server command configured")
        return False

    dev_cmd = dev_cmd.replace("${PORT}", str(port)).replace("${REPO_ROOT}", cwd)

    log(f"starting dev server on port {port}")
    proc = subprocess.Popen(
        dev_cmd, shell=True, cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )

    # Save PID to state
    def _save(s: PipelineState):
        s.dev_server_pid = proc.pid
    pipeline_ops.state_mgr().update(_save)

    # Wait for server to be healthy using Playwright
    startup_timeout = dev_config.get("startup_timeout", 300)
    health_url = dev_config.get("health_url", f"https://localhost:{port}/")
    health_url = health_url.replace("${PORT}", str(port))

    playwright_dir = _find_playwright_runner(preset)
    if not playwright_dir:
        log("Playwright runner not found — falling back to curl health check")
        return _poll_with_curl(proc, health_url, startup_timeout, port, log)

    return _poll_with_playwright(
        proc, health_url, startup_timeout, port, playwright_dir, log,
    )


def _poll_with_playwright(proc, health_url, timeout, port, playwright_dir, log) -> bool:
    """Poll using Playwright to verify the app actually renders."""
    script = _HEALTH_CHECK_SCRIPT.format(url=health_url)
    run_js = str(Path(playwright_dir) / "run.js")
    poll_interval = 10
    elapsed = 0

    while elapsed < timeout:
        if proc.poll() is not None:
            output = proc.stdout.read().decode(errors="replace")[-500:]
            log(f"dev server crashed (exit {proc.returncode}): {output}")
            _cleanup(proc)
            return False

        try:
            result = subprocess.run(
                ["node", run_js, script],
                capture_output=True, text=True, timeout=70,
                cwd=playwright_dir,
            )
            if result.returncode == 0 and "HEALTHY" in result.stdout:
                log(f"app verified healthy on port {port}")
                return True
        except subprocess.TimeoutExpired:
            pass

        time.sleep(poll_interval)
        elapsed += poll_interval

    log(f"app failed to become healthy within {timeout}s on port {port}")
    _cleanup(proc)
    return False


def _poll_with_curl(proc, health_url, timeout, port, log) -> bool:
    """Fallback: poll with curl if Playwright is not available."""
    poll_interval = 5
    elapsed = 0

    while elapsed < timeout:
        if proc.poll() is not None:
            output = proc.stdout.read().decode(errors="replace")[-500:]
            log(f"dev server crashed (exit {proc.returncode}): {output}")
            _cleanup(proc)
            return False

        try:
            result = subprocess.run(
                ["curl", "-sk", health_url], capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                log(f"dev server responding on port {port}")
                return True
        except subprocess.TimeoutExpired:
            pass

        time.sleep(poll_interval)
        elapsed += poll_interval

    log(f"dev server failed to start within {timeout}s on port {port}")
    _cleanup(proc)
    return False


def _kill_existing_dev_server(state: PipelineState):
    """Kill any dev server from previous state."""
    pid = state.dev_server_pid
    if pid > 0:
        try:
            os.kill(pid, 15)  # SIGTERM
            time.sleep(2)
            os.kill(pid, 9)   # SIGKILL if still alive
        except (ProcessLookupError, PermissionError):
            pass
    pipeline_ops.clear_dev_server_state()


def _cleanup(proc):
    """Kill the dev server process and clear state."""
    try:
        proc.kill()
        proc.wait(timeout=5)
    except Exception:
        pass
    try:
        pipeline_ops.clear_dev_server_state()
    except Exception:
        pass
