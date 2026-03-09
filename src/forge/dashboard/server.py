#!/usr/bin/env python3
"""
Enhanced multi-session HTTP server for Forge with session management.
Supports multiple concurrent sessions with planner-executor linking and deletion.
"""

import json
import os
import shutil
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Optional, Any
from urllib.parse import urlparse, parse_qs

DEFAULT_PORT = 8765
ARCHITECT_DIR = Path.home() / '.forge' / 'sessions'
PLANNER_DIR = ARCHITECT_DIR / 'planner'
EXECUTOR_DIR = ARCHITECT_DIR / 'executor'
REGISTRY_FILE = ARCHITECT_DIR / 'registry.jsonl'

class EnhancedSessionHandler(BaseHTTPRequestHandler):
    STATIC_DIR = Path(__file__).parent

    def do_GET(self):
        """Handle GET requests for different endpoints."""
        parsed_path = urlparse(self.path)

        if parsed_path.path in ('/', '/dashboard'):
            self.serve_static('dashboard.html')
        elif parsed_path.path == '/sessions':
            self.handle_sessions()
        elif parsed_path.path == '/artifacts':
            self.handle_artifacts()
        else:
            self.send_error(404, "Endpoint not found")

    def serve_static(self, filename: str):
        """Serve a static HTML file from the dashboard directory."""
        file_path = self.STATIC_DIR / filename
        if not file_path.exists():
            self.send_error(404, f"{filename} not found")
            return
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(file_path.read_bytes())

    def do_DELETE(self):
        """Handle DELETE requests for session removal."""
        if self.path.startswith('/session/'):
            self.handle_delete_session()
        else:
            self.send_error(404, "Endpoint not found")

    def do_OPTIONS(self):
        """Handle OPTIONS requests for CORS."""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def handle_sessions(self):
        """Return all sessions grouped by project."""
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        try:
            sessions = self.get_all_sessions()
            self.wfile.write(json.dumps(sessions, indent=2).encode())
        except Exception as e:
            error_data = {'error': str(e)}
            self.wfile.write(json.dumps(error_data).encode())

    def handle_delete_session(self):
        """Delete a session directory."""
        session_id = self.path.split('/')[-1]

        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'DELETE')
        self.end_headers()

        try:
            result = self.delete_session(session_id)
            self.wfile.write(json.dumps(result).encode())
        except Exception as e:
            error_data = {'error': str(e), 'success': False}
            self.wfile.write(json.dumps(error_data).encode())

    def handle_artifacts(self):
        """Return artifact data for a session directory."""
        parsed_url = urlparse(self.path)
        params = parse_qs(parsed_url.query)
        session_dir = params.get('session_dir', [None])[0]

        if not session_dir:
            self._json_response(400, {'error': 'session_dir required'})
            return

        d = Path(session_dir)
        if not d.exists():
            self._json_response(404, {'error': 'session dir not found'})
            return

        result = {}

        # Code checklist
        checklist = d / 'code-checklist.json'
        if checklist.exists():
            try:
                data = json.loads(checklist.read_text())
                items = data.get('checklist', [])
                result['code_checklist'] = {
                    'total': len(items),
                    'done': sum(1 for i in items if i.get('status') == 'done'),
                    'files_created': len(data.get('files_created', [])),
                    'files_modified': len(data.get('files_modified', [])),
                }
            except Exception:
                pass

        # Code review verdict
        verdict = d / 'code-review-verdict.json'
        if verdict.exists():
            try:
                result['code_review'] = json.loads(verdict.read_text())
            except Exception:
                pass

        # Code review retries
        retries_dir = d / '_retries'
        if retries_dir.exists():
            retry_verdicts = []
            for attempt_dir in sorted(retries_dir.iterdir()):
                v = attempt_dir / 'code-review-verdict.json'
                if v.exists():
                    try:
                        rv = json.loads(v.read_text())
                        rv['attempt'] = attempt_dir.name
                        retry_verdicts.append(rv)
                    except Exception:
                        pass
            if retry_verdicts:
                result['code_review_retries'] = retry_verdicts

        # Code actions
        code_actions = d / 'code-actions.json'
        if code_actions.exists():
            try:
                data = json.loads(code_actions.read_text())
                result['code_actions'] = {
                    'outcome': data.get('outcome'),
                    'files_read': len(data.get('files_read', [])),
                    'files_written': len(data.get('files_written', [])),
                    'commands_run': len(data.get('commands', [])),
                    'errors': len(data.get('errors', [])),
                }
            except Exception:
                pass

        # Collect all step actions and checklists generically
        step_actions = {}
        step_checklists = {}
        for f in d.iterdir():
            if f.name.endswith('-actions.json'):
                step_name = f.name.replace('-actions.json', '')
                try:
                    data = json.loads(f.read_text())
                    step_actions[step_name] = {
                        'outcome': data.get('outcome'),
                        'files_read': len(data.get('files_read', [])),
                        'files_written': len(data.get('files_written', [])),
                        'commands_run': len(data.get('commands', [])),
                        'errors': len(data.get('errors', [])),
                    }
                except Exception:
                    pass
            elif f.name.endswith('-checklist.json'):
                step_name = f.name.replace('-checklist.json', '')
                try:
                    step_checklists[step_name] = json.loads(f.read_text())
                except Exception:
                    pass

        if step_actions:
            result['step_actions'] = step_actions
        if step_checklists:
            result['step_checklists'] = step_checklists

        # Keep legacy keys for backward compatibility
        if 'e2e' in step_actions:
            result['e2e_actions'] = step_actions['e2e']
        if 'e2e' in step_checklists:
            result['e2e_checklist'] = step_checklists['e2e']
        if 'lint' in step_actions:
            result['lint_actions'] = step_actions['lint']

        # PR URL
        pr_url = d / 'pr-url.txt'
        if pr_url.exists():
            result['pr_url'] = pr_url.read_text().strip()

        # Pipeline status
        status_file = d / 'pipeline-status.md'
        if status_file.exists():
            result['pipeline_status_md'] = status_file.read_text()[:2000]

        # Plan file path from agent-state.json
        state_file = d / 'agent-state.json'
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text())
                plan = state.get('plan_file', '')
                result['plan_file'] = plan
                if plan and Path(plan).exists():
                    result['plan_summary'] = Path(plan).read_text()[:100000]
            except Exception:
                pass

        # Checkpoints
        cp_dir = d / 'checkpoints'
        if cp_dir.exists():
            checkpoints = {}
            for f in sorted(cp_dir.iterdir()):
                if f.suffix == '.passed':
                    content = f.read_text()
                    cp = {}
                    for line in content.splitlines():
                        if '=' in line:
                            k, v = line.split('=', 1)
                            cp[k.strip()] = v.strip()
                    checkpoints[f.stem] = cp
            result['checkpoints'] = checkpoints

        # Events
        events_file = d / 'events.jsonl'
        if events_file.exists():
            try:
                events = []
                for line in events_file.read_text().splitlines():
                    if line.strip():
                        events.append(json.loads(line))
                result['events'] = events
            except Exception:
                pass

        self._json_response(200, result)

    def _json_response(self, code: int, data: dict):
        """Send a JSON response with CORS headers."""
        self.send_response(code)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def delete_session(self, session_id: str) -> Dict[str, Any]:
        """Delete a session directory and return status."""
        deleted_paths = []

        # Check planner directory
        planner_path = PLANNER_DIR / session_id
        if planner_path.exists():
            # Kill any running process first
            state_file = planner_path / '.planner-state.json'
            if state_file.exists():
                with open(state_file, 'r') as f:
                    state = json.load(f)
                    pid = state.get('driver_pid', 0)
                    if pid:
                        try:
                            os.kill(pid, 15)  # SIGTERM
                        except OSError:
                            pass

            shutil.rmtree(planner_path)
            deleted_paths.append(str(planner_path))

        # Check executor directory
        executor_path = EXECUTOR_DIR / session_id
        if executor_path.exists():
            # Kill any running process first
            state_file = executor_path / 'agent-state.json'
            if state_file.exists():
                with open(state_file, 'r') as f:
                    state = json.load(f)
                    pid = state.get('driver_pid', 0)
                    if pid:
                        try:
                            os.kill(pid, 15)  # SIGTERM
                        except OSError:
                            pass

            shutil.rmtree(executor_path)
            deleted_paths.append(str(executor_path))

        if deleted_paths:
            return {
                'success': True,
                'deleted': deleted_paths,
                'message': f'Successfully deleted session {session_id}'
            }
        else:
            return {
                'success': False,
                'message': f'Session {session_id} not found'
            }

    def get_all_sessions(self) -> Dict[str, Any]:
        """Get all sessions grouped by project."""
        sessions = {
            'projects': [],
            'total_active': 0,
            'total_sessions': 0
        }

        # Get all planner sessions
        planner_sessions = {}
        if PLANNER_DIR.exists():
            for session_dir in PLANNER_DIR.glob('*'):
                session_info = self.get_planner_info(session_dir)
                if session_info:
                    planner_sessions[str(session_dir)] = session_info

        # Get all executor sessions
        executor_sessions = {}
        if EXECUTOR_DIR.exists():
            for session_dir in EXECUTOR_DIR.glob('*'):
                session_info = self.get_executor_info(session_dir)
                if session_info:
                    executor_sessions[str(session_dir)] = session_info

        # Group sessions by project
        projects = {}

        # Process planner sessions - use session_id as unique key instead of problem statement
        for plan_path, plan_info in planner_sessions.items():
            # Create unique project key using session_id to avoid grouping similar problems
            project_key = f"{plan_info.get('session_id')}_{plan_info.get('problem_statement', 'Unknown Task')[:50]}"
            projects[project_key] = {
                'problem_statement': plan_info.get('problem_statement', 'Unknown Task'),
                'planner': plan_info,
                'executors': [],
                'created_at': plan_info.get('created_at'),
                'repo_dir': plan_info.get('repo_dir'),
                'preset': plan_info.get('preset'),
                'overall_status': 'inactive',
                'session_id': plan_info.get('session_id')
            }

            # Update overall status
            if plan_info.get('status') == 'active':
                projects[project_key]['overall_status'] = 'planning'
                sessions['total_active'] += 1

        # Process executor sessions and link to planners
        for exec_path, exec_info in executor_sessions.items():
            plan_file = exec_info.get('plan_file')
            linked = False

            if plan_file:
                # Find matching planner session by session_dir match
                plan_dir = Path(plan_file).parent
                for project_key, project in projects.items():
                    if project.get('planner') and str(plan_dir) == project['planner']['session_dir']:
                        project['executors'].append(exec_info)
                        linked = True

                        # Update overall status based on executor
                        if exec_info.get('status') == 'active':
                            project['overall_status'] = 'executing'
                            sessions['total_active'] += 1
                        elif project['overall_status'] == 'inactive':
                            # Planner done, executor exists but finished
                            has_failed = any(
                                s.get('status') == 'failed'
                                for s in exec_info.get('steps', {}).values()
                            )
                            project['overall_status'] = 'failed' if has_failed else 'completed'
                        break

            if not linked:
                # Orphaned executor (planner might be deleted)
                project_key = f"Executor: {Path(exec_path).name}"
                if project_key not in projects:
                    projects[project_key] = {
                        'problem_statement': 'Orphaned Executor Session',
                        'planner': None,
                        'executors': [],
                        'created_at': exec_info.get('created_at'),
                        'repo_dir': exec_info.get('repo_dir'),
                        'preset': exec_info.get('preset'),
                        'overall_status': 'orphaned'
                    }
                projects[project_key]['executors'].append(exec_info)

                if exec_info.get('status') == 'active':
                    sessions['total_active'] += 1

        # Convert to list and sort by creation time
        sessions['projects'] = sorted(
            projects.values(),
            key=lambda x: x.get('created_at', ''),
            reverse=True
        )
        sessions['total_sessions'] = len(planner_sessions) + len(executor_sessions)

        return sessions

    def get_planner_info(self, session_dir: Path) -> Optional[Dict[str, Any]]:
        """Extract planner session info."""
        state_file = session_dir / '.planner-state.json'
        if not state_file.exists():
            return None

        with open(state_file, 'r') as f:
            state = json.load(f)

        # Determine if active
        is_active = False
        current_phase = None
        for phase_name, phase_data in state.get('phases', {}).items():
            if phase_data.get('status') == 'in_progress':
                is_active = True
                current_phase = phase_name
                break

        # Check if process is still running
        pid = state.get('driver_pid', 0)
        if pid and is_active:
            try:
                os.kill(pid, 0)  # Check if process exists
            except OSError:
                is_active = False

        return {
            'session_id': session_dir.name,
            'session_dir': str(session_dir),
            'type': 'planner',
            'status': 'active' if is_active else 'completed',
            'current_phase': current_phase,
            'phases': state.get('phases', {}),
            'problem_statement': state.get('problem_statement'),
            'preset': state.get('preset'),
            'repo_dir': state.get('repo_dir'),
            'fast_mode': state.get('fast_mode'),
            'created_at': state.get('created_at'),
            'updated_at': state.get('updated_at'),
            'pid': pid
        }

    def get_executor_info(self, session_dir: Path) -> Optional[Dict[str, Any]]:
        """Extract executor session info."""
        state_file = session_dir / 'agent-state.json'
        if not state_file.exists():
            return None

        with open(state_file, 'r') as f:
            state = json.load(f)

        # Determine if active: check if driver process is still running
        is_active = False
        current_step = state.get('current_step')
        pid = state.get('driver_pid', 0)
        if pid:
            try:
                os.kill(pid, 0)  # Check if process exists
                is_active = True
            except OSError:
                pass

        # Fallback: check step status if no PID
        if not is_active and current_step:
            step_status = state.get('steps', {}).get(current_step, {}).get('status')
            if step_status in ['in_progress', 'running']:
                is_active = True

        return {
            'session_id': session_dir.name,
            'session_dir': str(session_dir),
            'type': 'executor',
            'status': 'active' if is_active else 'completed',
            'current_step': current_step,
            'steps': state.get('steps', {}),
            'plan_file': state.get('plan_file'),
            'preset': state.get('preset'),
            'repo_dir': state.get('repo_dir') or state.get('worktree_path'),
            'is_worktree': state.get('is_worktree'),
            'created_at': state.get('created_at'),
            'updated_at': state.get('updated_at'),
            'pid': pid
        }

    def log_message(self, format, *args):
        # Suppress default logging
        pass

def run_server(port=DEFAULT_PORT):
    server_address = ('', port)
    httpd = HTTPServer(server_address, EnhancedSessionHandler)
    print(f"Forge Dashboard running on http://localhost:{port}")
    print("Press Ctrl+C to stop\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n\n👋 Server stopped")

if __name__ == '__main__':
    port = int(os.environ.get('DASHBOARD_PORT', str(DEFAULT_PORT)))
    run_server(port)