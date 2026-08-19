from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence

from .core import (
    INTERACTION_TYPES,
    JOB_STATUSES,
    OPERATION_JOURNAL,
    PERSON_STATUSES,
    PURSUIT_DECISIONS,
    add_job,
    add_person,
    add_task,
    approve_import,
    complete_task,
    delete_interaction,
    ensure_data_dir,
    generate_dashboard,
    log_event,
    preview_import,
    read_rows,
    seed_demo,
    set_pursuit_decision,
    validate,
)
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
from .server import serve


def data_dir(args: argparse.Namespace) -> Path:
    return Path(args.data_dir).expanduser().resolve()


def print_record(row: Dict[str, str], id_field: str) -> None:
    print(f"Saved {id_field}: {row[id_field]}")


def add_common_data_dir(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-dir", default="private_data", help="Editable data folder (default: private_data)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="career", description="Terminal-first career connection manager")
    parser.add_argument("--version", action="version", version="career 0.1.0")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create editable CSV and Markdown files")
    add_common_data_dir(init)

    demo = sub.add_parser("demo", help="Create synthetic demo data and dashboard")
    demo.add_argument("--data-dir", default="demo_data")
    demo.add_argument("--output", default="demo_site/index.html")

    doctor = sub.add_parser("doctor", help="Validate files and linked records")
    add_common_data_dir(doctor)

    imp = sub.add_parser("import", help="Extract people or jobs from pasted text")
    imp.add_argument("source", nargs="?", help="Text file; omit to paste through stdin")
    imp.add_argument("--type", choices=["auto", "person", "job"], default="auto")
    imp.add_argument("--pending", default="pending_import.json")
    add_common_data_dir(imp)

    review = sub.add_parser("review-import", help="Show or approve a pending import")
    review.add_argument("--pending", default="pending_import.json")
    review.add_argument("--approve", action="store_true")
    review.add_argument("--allow-duplicates", action="store_true")
    add_common_data_dir(review)

    person = sub.add_parser("person", help="Add or list people")
    person_sub = person.add_subparsers(dest="action", required=True)
    person_add = person_sub.add_parser("add")
    person_add.add_argument("--name", required=True)
    person_add.add_argument("--title", default="")
    person_add.add_argument("--company", default="")
    person_add.add_argument("--location", default="")
    person_add.add_argument("--url", default="")
    person_add.add_argument("--email", default="")
    person_add.add_argument("--connection-points", default="", help="Semicolon-separated reasons to connect")
    person_add.add_argument("--status", choices=sorted(PERSON_STATUSES), default="to_research")
    person_add.add_argument("--notes", default="")
    add_common_data_dir(person_add)
    person_list = person_sub.add_parser("list")
    add_common_data_dir(person_list)

    job = sub.add_parser("job", help="Add or list jobs")
    job_sub = job.add_subparsers(dest="action", required=True)
    job_add = job_sub.add_parser("add")
    job_add.add_argument("--title", required=True)
    job_add.add_argument("--company", required=True)
    job_add.add_argument("--location", default="")
    job_add.add_argument("--url", default="")
    job_add.add_argument("--status", choices=sorted(JOB_STATUSES), default="saved")
    job_add.add_argument("--description", default="")
    job_add.add_argument("--deadline", default="")
    job_add.add_argument("--notes", default="")
    add_common_data_dir(job_add)
    job_list = job_sub.add_parser("list")
    add_common_data_dir(job_list)
    job_update = job_sub.add_parser("update")
    job_update.add_argument("job_id")
    job_update.add_argument("--status", choices=sorted(JOB_STATUSES))
    job_update.add_argument("--url")
    job_update.add_argument("--fit-score")
    job_update.add_argument("--fit-reasons")
    job_update.add_argument("--next-action")
    job_update.add_argument("--next-action-date")
    job_update.add_argument("--notes")
    job_update.add_argument("--salary-range")
    add_common_data_dir(job_update)
    job_decision = job_sub.add_parser("decision")
    job_decision.add_argument("job_id")
    job_decision.add_argument("decision", choices=sorted(PURSUIT_DECISIONS))
    add_common_data_dir(job_decision)

    task = sub.add_parser("task", help="Add, list, or complete tasks")
    task_sub = task.add_subparsers(dest="action", required=True)
    task_add = task_sub.add_parser("add")
    task_add.add_argument("--title", required=True)
    task_add.add_argument("--due", required=True)
    task_add.add_argument("--type", default="general")
    task_add.add_argument("--priority", choices=["low", "medium", "high"], default="medium")
    task_add.add_argument("--person", default="")
    task_add.add_argument("--job", default="")
    task_add.add_argument("--note", default="")
    add_common_data_dir(task_add)
    task_list = task_sub.add_parser("list")
    add_common_data_dir(task_list)
    task_done = task_sub.add_parser("done")
    task_done.add_argument("task_id")
    task_done.add_argument("--dismiss", action="store_true")
    add_common_data_dir(task_done)

    event = sub.add_parser("log", help="Log outreach, replies, chats, applications, or interviews")
    event.add_argument("type", choices=["outreach", "reply", "coffee_chat", "application", "interview", "note"])
    event.add_argument("--person", default="")
    event.add_argument("--job", default="")
    event.add_argument("--date", default="")
    event.add_argument("--notes", default="")
    event.add_argument("--follow-up-days", type=int, default=7)
    add_common_data_dir(event)

    interaction = sub.add_parser(
        "interaction",
        help="List interactions or safely delete a coffee-chat record",
    )
    interaction_sub = interaction.add_subparsers(dest="action", required=True)
    interaction_list = interaction_sub.add_parser("list")
    interaction_list.add_argument("--type", choices=sorted(INTERACTION_TYPES), default="")
    interaction_list.add_argument("--person", default="", help="Person ID or exact saved name")
    add_common_data_dir(interaction_list)
    interaction_delete = interaction_sub.add_parser(
        "delete",
        help="Preview or confirm deletion of one coffee-chat interaction and its generated tasks",
    )
    interaction_delete.add_argument("interaction_id")
    deletion_mode = interaction_delete.add_mutually_exclusive_group(required=True)
    deletion_mode.add_argument("--dry-run", action="store_true", help="Show the exact deletion plan without writing")
    deletion_mode.add_argument("--confirm", action="store_true", help="Back up and apply the displayed deletion plan")
    interaction_delete.add_argument("--restore-status", choices=sorted(PERSON_STATUSES), default="")
    interaction_delete.add_argument("--restore-next-action")
    interaction_delete.add_argument("--restore-next-action-date")
    interaction_delete.add_argument("--output", default="site/index.html")
    add_common_data_dir(interaction_delete)

    dashboard = sub.add_parser("dashboard", help="Build the static HTML dashboard")
    dashboard.add_argument("action", choices=["build"])
    dashboard.add_argument("--output", default="site/index.html")
    dashboard.add_argument("--demo", action="store_true")
    add_common_data_dir(dashboard)

    server = sub.add_parser("serve", help="Run the clickable dashboard on this computer")
    server.add_argument("--port", type=int, default=8765)
    server.add_argument("--output", default="site/index.html")
    server.add_argument("--demo", action="store_true", help="Label the dashboard as synthetic demo data")
    add_common_data_dir(server)

    agent = sub.add_parser("agent", help="Run human-reviewed career agents")
    agent_sub = agent.add_subparsers(dest="action", required=True)
    message = agent_sub.add_parser("message", help="Draft a LinkedIn or email message")
    message.add_argument("--person", required=True)
    message.add_argument("--job", default="")
    message.add_argument("--channel", choices=["linkedin", "email"], default="linkedin")
    message.add_argument("--goal", default="start a thoughtful conversation")
    message.add_argument(
        "--stage",
        choices=["auto", "connection_request", "after_acceptance", "after_team_chat", "chat_confirmation", "chat_reminder", "relationship_follow_up"],
        default="auto",
    )
    message.add_argument("--model", default="")
    message.add_argument("--dry-run", action="store_true")
    add_common_data_dir(message)

    resume = agent_sub.add_parser("resume", help="Review a JD and tailor resume text")
    resume.add_argument("--job", required=True)
    resume.add_argument("--resume", required=True, help="Plain-text or Markdown resume")
    resume.add_argument("--model", default="")
    resume.add_argument("--dry-run", action="store_true")
    add_common_data_dir(resume)

    person_research = agent_sub.add_parser("person-research", help="Research public professional information")
    person_research.add_argument("--person", required=True)
    person_research.add_argument("--purpose", default="prepare for outreach")
    person_research.add_argument("--model", default="")
    person_research.add_argument("--dry-run", action="store_true")
    add_common_data_dir(person_research)

    company_research = agent_sub.add_parser("company-research", help="Research a company and relevant team")
    company_research.add_argument("--company", required=True, help="Company ID or exact name")
    company_research.add_argument("--job", default="")
    company_research.add_argument("--model", default="")
    company_research.add_argument("--dry-run", action="store_true")
    add_common_data_dir(company_research)

    recruiter_outreach = agent_sub.add_parser(
        "recruiter-outreach",
        help="Find a relevant recruiter or team member and draft staged outreach",
    )
    recruiter_outreach.add_argument("--job", required=True)
    recruiter_outreach.add_argument("--person", default="", help="Optional saved person to research first")
    recruiter_outreach.add_argument(
        "--goal",
        default="find a suitable recruiter or team member and prepare personalized outreach",
    )
    recruiter_outreach.add_argument("--model", default="")
    recruiter_outreach.add_argument("--dry-run", action="store_true")
    add_common_data_dir(recruiter_outreach)
    return parser


def list_records(folder: Path, name: str, fields: Sequence[str]) -> None:
    rows = read_rows(folder, name)
    if not rows:
        print(f"No {name} found.")
        return
    for row in rows:
        print(" | ".join(row.get(field, "") for field in fields))


def print_interaction_deletion_plan(plan: Mapping[str, object]) -> None:
    interaction = plan["interaction"]
    assert isinstance(interaction, Mapping)
    print("Coffee-chat deletion plan")
    print(
        "Interaction: "
        f"{interaction.get('interaction_id', '')} | {interaction.get('date', '')} | "
        f"{interaction.get('person_name', '')} | {interaction.get('notes', '')}"
    )
    tasks = plan.get("tasks", [])
    if isinstance(tasks, Sequence):
        for task in tasks:
            if isinstance(task, Mapping):
                print(
                    "Task: "
                    f"{task.get('task_id', '')} | {task.get('type', '')} | "
                    f"due {task.get('due_date', '')} | {task.get('status', '')}"
                )
    person_changes = plan.get("person_changes", {})
    if isinstance(person_changes, Mapping) and person_changes:
        rendered = ", ".join(f"{key}={value}" for key, value in person_changes.items())
        print(f"Person schedule: update {rendered}")
    else:
        print("Person schedule: preserve unchanged")
    remaining = plan.get("remaining_coffee_chats", [])
    if isinstance(remaining, Sequence) and remaining:
        rendered = ", ".join(
            f"{item.get('interaction_id', '')} ({item.get('date', '')})"
            for item in remaining
            if isinstance(item, Mapping)
        )
        print(f"Remaining coffee chats: {rendered}")
    for warning in plan.get("warnings", []):
        print(f"Warning: {warning}")
    blockers = plan.get("blockers", [])
    for blocker in blockers:
        print(f"BLOCKED: {blocker}")
    if plan.get("deleted"):
        print("Result: deleted")
        if plan.get("backup_directory"):
            print(f"Backup: {plan['backup_directory']}")
    elif not blockers:
        print("Result: ready; rerun with --confirm to delete")


def require_existing_interaction_data(folder: Path) -> None:
    if (folder / OPERATION_JOURNAL).exists():
        raise ValueError(
            "An interrupted write needs recovery. Run `./career doctor` before listing or previewing interactions."
        )
    required = [folder / "interactions.csv", folder / "people.csv", folder / "tasks.csv"]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError("Career data is not initialized; missing: " + ", ".join(missing))


def run(args: argparse.Namespace) -> int:
    command = args.command
    if command == "init":
        folder = data_dir(args)
        ensure_data_dir(folder)
        print(f"Created career workspace: {folder}")
        print(f"Edit your target: {folder / 'target.md'}")
        return 0
    if command == "demo":
        folder = data_dir(args)
        seed_demo(folder)
        output = generate_dashboard(folder, Path(args.output).resolve(), demo=True)
        print(f"Created synthetic demo: {output}")
        return 0

    if command == "interaction":
        folder = data_dir(args)
        require_existing_interaction_data(folder)
        if args.action == "list":
            people = {
                row.get("person_id", ""): row.get("name", "")
                for row in read_rows(folder, "people")
            }
            person_filter = args.person.casefold().strip()
            rows = []
            for row in read_rows(folder, "interactions"):
                if args.type and row.get("type") != args.type:
                    continue
                person_id = row.get("person_id", "")
                person_name = people.get(person_id, "")
                if person_filter and person_filter not in {person_id.casefold(), person_name.casefold()}:
                    continue
                rows.append((row, person_name))
            rows.sort(key=lambda item: (item[0].get("date", ""), item[0].get("created_at", ""), item[0].get("interaction_id", "")))
            if not rows:
                print("No matching interactions found.")
                return 0
            for row, person_name in rows:
                print(
                    " | ".join([
                        row.get("interaction_id", ""), row.get("type", ""),
                        row.get("date", ""), person_name or row.get("person_id", ""),
                        row.get("notes", ""),
                    ])
                )
            return 0

        plan = delete_interaction(
            folder,
            args.interaction_id,
            confirm=args.confirm,
            restore_status=args.restore_status,
            restore_next_action=args.restore_next_action,
            restore_next_action_date=args.restore_next_action_date,
        )
        print_interaction_deletion_plan(plan)
        blockers = plan.get("blockers", [])
        if blockers:
            return 2
        if args.confirm:
            output = generate_dashboard(folder, Path(args.output).resolve())
            print(f"Built dashboard: {output}")
        return 0

    folder = data_dir(args)
    ensure_data_dir(folder)
    if command == "doctor":
        errors = validate(folder)
        if errors:
            print(f"Found {len(errors)} problem(s):")
            for error in errors:
                print(f"- {error}")
            return 1
        print("All files and links are valid.")
        return 0
    if command == "import":
        text = Path(args.source).read_text(encoding="utf-8") if args.source else sys.stdin.read()
        if not text.strip():
            raise ValueError("No text was provided.")
        payload = preview_import(folder, text, args.type)
        pending = Path(args.pending).resolve()
        pending.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"\nReview or edit: {pending}")
        print(f"Approve with: ./career review-import --pending {pending} --approve --data-dir {folder}")
        return 0
    if command == "review-import":
        pending = Path(args.pending).resolve()
        if not pending.exists():
            raise ValueError(f"Pending import not found: {pending}")
        payload = json.loads(pending.read_text(encoding="utf-8"))
        if not args.approve:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0
        saved, skipped = approve_import(folder, payload, args.allow_duplicates)
        print(f"Saved {saved} record(s).")
        for item in skipped:
            print(f"Skipped {item}")
        if saved and not skipped:
            pending.unlink()
        return 0 if not skipped else 2
    if command == "person":
        if args.action == "list":
            list_records(folder, "people", ["person_id", "name", "title", "company_name", "status", "next_action_date"])
        else:
            row = add_person(folder, {"name": args.name, "title": args.title, "company_name": args.company, "location": args.location, "profile_url": args.url, "email": args.email, "connection_points": args.connection_points, "status": args.status, "notes": args.notes})
            print_record(row, "person_id")
        return 0
    if command == "job":
        if args.action == "list":
            list_records(folder, "jobs", ["job_id", "title", "company_name", "location", "status", "next_action_date"])
        elif args.action == "update":
            from .core import update_row
            changes = {
                key: value for key, value in {
                    "status": args.status,
                    "source_url": args.url,
                    "fit_score": args.fit_score,
                    "fit_reasons": args.fit_reasons,
                    "next_action": args.next_action,
                    "next_action_date": args.next_action_date,
                    "notes": args.notes,
                    "salary_range": args.salary_range,
                }.items() if value is not None
            }
            row = update_row(folder, "jobs", "job_id", args.job_id, changes)
            print(f"Updated job: {row['job_id']}")
        elif args.action == "decision":
            row = set_pursuit_decision(folder, args.job_id, args.decision)
            print(f"Job {row['job_id']} decision: {row['pursuit_decision']}")
        else:
            row = add_job(folder, {"title": args.title, "company_name": args.company, "location": args.location, "source_url": args.url, "status": args.status, "description": args.description, "deadline": args.deadline, "notes": args.notes})
            print_record(row, "job_id")
        return 0
    if command == "task":
        if args.action == "list":
            list_records(folder, "tasks", ["task_id", "due_date", "priority", "title", "status"])
        elif args.action == "done":
            row = complete_task(folder, args.task_id, args.dismiss)
            print(f"Task {row['task_id']} is {row['status']}.")
        else:
            row = add_task(folder, {"title": args.title, "due_date": args.due, "type": args.type, "priority": args.priority, "person_id": args.person, "job_id": args.job, "note": args.note})
            print_record(row, "task_id")
        return 0
    if command == "log":
        interaction, task = log_event(folder, args.type, args.person, args.job, args.date, args.notes, args.follow_up_days)
        print(f"Logged interaction: {interaction['interaction_id']}")
        if task:
            print(f"Created reminder: {task['task_id']} due {task['due_date']}")
        return 0
    if command == "dashboard":
        output = generate_dashboard(folder, Path(args.output).resolve(), demo=args.demo)
        print(f"Built dashboard: {output}")
        return 0
    if command == "serve":
        if not 1 <= args.port <= 65535:
            raise ValueError("Port must be between 1 and 65535.")
        serve(folder, Path(args.output).resolve(), args.port, demo=args.demo)
        return 0
    if command == "agent":
        if args.action == "message":
            prompt = message_prompt(folder, args.person, args.job, args.channel, args.goal, args.stage)
            agent_name = "message-agent"
            subject = args.person
            use_web = False
        elif args.action == "resume":
            resume_path = Path(args.resume).expanduser().resolve()
            if resume_path.suffix.lower() not in {".txt", ".md"}:
                raise ValueError("Resume input must be a plain-text or Markdown file for this MVP.")
            prompt = resume_prompt(folder, args.job, resume_path.read_text(encoding="utf-8"))
            agent_name = "resume-agent"
            subject = args.job
            use_web = False
        elif args.action == "person-research":
            prompt = person_research_prompt(folder, args.person, args.purpose)
            agent_name = "person-research-agent"
            subject = args.person
            use_web = True
        elif args.action == "company-research":
            prompt = company_research_prompt(folder, args.company, args.job)
            agent_name = "company-research-agent"
            subject = args.company
            use_web = True
        else:
            prompt = recruiter_outreach_prompt(folder, args.job, args.person, args.goal)
            agent_name = "recruiter-outreach-agent"
            subject = f"{args.job}-{args.person or 'search'}"
            use_web = True
        if args.dry_run:
            print(prompt)
            return 0
        model = args.model or DEFAULT_MODEL
        report = run_openai(prompt, web_search=use_web, model=model)
        path = save_report(folder, agent_name, subject, report, model=model)
        print(report)
        print(f"\nSaved human-review draft: {path}")
        return 0
    return 1


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = build_parser()
    try:
        code = run(parser.parse_args(argv))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        code = 1
    raise SystemExit(code)
