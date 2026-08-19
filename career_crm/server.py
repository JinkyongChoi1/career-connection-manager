from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Tuple

from .agents import (
    DEFAULT_MODEL,
    company_research_prompt,
    message_prompt,
    person_research_prompt,
    recruiter_outreach_prompt,
    resume_prompt,
    run_openai,
    save_report,
)
from .core import generate_dashboard, metrics, set_job_status, set_person_status, set_pursuit_decision


MAX_BODY_BYTES = 2_000_000


def execute_agent(data_dir: Path, payload: Dict[str, object]) -> Tuple[str, str]:
    action = str(payload.get("action", ""))
    model = str(payload.get("model", "")) or DEFAULT_MODEL
    dry_run = bool(payload.get("dry_run", False))
    if action == "message":
        person_id = str(payload.get("person_id", ""))
        prompt = message_prompt(
            data_dir,
            person_id,
            str(payload.get("job_id", "")),
            str(payload.get("channel", "linkedin")),
            str(payload.get("goal", "start a thoughtful conversation")),
            str(payload.get("stage", "auto")),
        )
        agent_name, subject, use_web = "message-agent", person_id, False
    elif action == "resume":
        job_id = str(payload.get("job_id", ""))
        prompt = resume_prompt(data_dir, job_id, str(payload.get("resume_text", "")))
        agent_name, subject, use_web = "resume-agent", job_id, False
    elif action == "person-research":
        person_id = str(payload.get("person_id", ""))
        prompt = person_research_prompt(data_dir, person_id, str(payload.get("purpose", "prepare for outreach")))
        agent_name, subject, use_web = "person-research-agent", person_id, True
    elif action == "company-research":
        company_id = str(payload.get("company_id", ""))
        prompt = company_research_prompt(data_dir, company_id, str(payload.get("job_id", "")))
        agent_name, subject, use_web = "company-research-agent", company_id, True
    elif action == "recruiter-outreach":
        job_id = str(payload.get("job_id", ""))
        person_id = str(payload.get("person_id", ""))
        prompt = recruiter_outreach_prompt(
            data_dir,
            job_id,
            person_id,
            str(payload.get("goal", "find a suitable recruiter or team member and prepare personalized outreach")),
        )
        agent_name, subject, use_web = "recruiter-outreach-agent", f"{job_id}-{person_id or 'search'}", True
    else:
        raise ValueError("Choose one of the five available agents.")
    if dry_run:
        return prompt, ""
    report = run_openai(prompt, web_search=use_web, model=model)
    path = save_report(data_dir, agent_name, subject, report, model=model)
    return report, str(path)


def serve(data_dir: Path, output: Path, port: int = 8765, demo: bool = False) -> None:
    generate_dashboard(data_dir, output, demo=demo)

    class Handler(BaseHTTPRequestHandler):
        def send_json(self, status: int, payload: Dict[str, object]) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            if self.path not in {"/", "/index.html"}:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            generate_dashboard(data_dir, output, demo=demo)
            raw = output.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self) -> None:
            if self.path not in {"/api/agent", "/api/job-decision", "/api/job-status", "/api/person-status"}:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_BODY_BYTES:
                    raise ValueError("Request must be between 1 byte and 2 MB.")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("Request body must be a JSON object.")
                if self.path == "/api/person-status":
                    row = set_person_status(
                        data_dir,
                        str(payload.get("person_id", "")),
                        str(payload.get("status", "")),
                    )
                    generate_dashboard(data_dir, output, demo=demo)
                    self.send_json(HTTPStatus.OK, {
                        "ok": True,
                        "person_id": row["person_id"],
                        "status": row["status"],
                        "next_action": row["next_action"],
                        "next_action_date": row["next_action_date"],
                        "metrics": metrics(data_dir),
                    })
                elif self.path == "/api/job-status":
                    row = set_job_status(
                        data_dir,
                        str(payload.get("job_id", "")),
                        str(payload.get("status", "")),
                    )
                    generate_dashboard(data_dir, output, demo=demo)
                    self.send_json(HTTPStatus.OK, {
                        "ok": True,
                        "job_id": row["job_id"],
                        "status": row["status"],
                        "metrics": metrics(data_dir),
                    })
                elif self.path == "/api/job-decision":
                    row = set_pursuit_decision(
                        data_dir,
                        str(payload.get("job_id", "")),
                        str(payload.get("decision", "")),
                    )
                    generate_dashboard(data_dir, output, demo=demo)
                    self.send_json(HTTPStatus.OK, {
                        "ok": True,
                        "job_id": row["job_id"],
                        "decision": row["pursuit_decision"],
                        "decided_at": row["pursuit_decided_at"],
                    })
                else:
                    result, report_path = execute_agent(data_dir, payload)
                    if report_path:
                        generate_dashboard(data_dir, output, demo=demo)
                    self.send_json(HTTPStatus.OK, {"ok": True, "result": result, "report_path": report_path})
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})

        def log_message(self, format: str, *args: object) -> None:
            print(f"dashboard: {format % args}")

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Dashboard running at http://127.0.0.1:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        server.server_close()
