import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from career_crm.core import (
    add_job,
    add_person,
    approve_import,
    complete_task,
    ensure_data_dir,
    generate_dashboard,
    log_event,
    preview_import,
    read_rows,
    seed_demo,
    set_job_status,
    set_person_status,
    set_pursuit_decision,
    validate,
)
from career_crm.agents import (
    company_research_prompt,
    extract_output_text,
    message_prompt,
    person_research_prompt,
    recruiter_outreach_prompt,
    resume_prompt,
)
from career_crm.server import execute_agent


class CareerCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name) / "data"
        ensure_data_dir(self.data)

    def tearDown(self):
        self.temp.cleanup()

    def test_init_creates_valid_workspace(self):
        self.assertEqual(validate(self.data), [])
        self.assertTrue((self.data / "target.md").exists())
        self.assertTrue((self.data / "outreach_profile.md").exists())
        self.assertTrue((self.data / "people.csv").exists())
        self.assertIn("connection_points", (self.data / "people.csv").read_text(encoding="utf-8").splitlines()[0])

    def test_unknown_column_is_rejected_clearly(self):
        path = self.data / "people.csv"
        header = path.read_text(encoding="utf-8").splitlines()[0]
        path.write_text(header + ",mystery_field\n", encoding="utf-8")
        errors = validate(self.data)
        self.assertTrue(any("unsupported columns: mystery_field" in error for error in errors))

    def test_import_preview_and_approval(self):
        text = """Name: Avery Kim
Title: BizOps Lead
Company: Example AI
LinkedIn: https://www.linkedin.com/in/avery-example

Job title: Strategy Lead
Company: Example AI
URL: https://example.com/jobs/1
"""
        payload = preview_import(self.data, text)
        self.assertEqual(len(payload["items"]), 2)
        self.assertEqual(payload["items"][0]["kind"], "person")
        self.assertEqual(payload["items"][1]["kind"], "job")
        saved, skipped = approve_import(self.data, payload)
        self.assertEqual(saved, 2)
        self.assertEqual(skipped, [])
        self.assertEqual(len(read_rows(self.data, "people")), 1)
        self.assertEqual(len(read_rows(self.data, "jobs")), 1)
        self.assertEqual(len(read_rows(self.data, "companies")), 1)
        self.assertTrue(read_rows(self.data, "jobs")[0]["company_id"])

    def test_full_careers_page_becomes_one_job(self):
        text = """TikTok Careers
Data Science Intern (Advertisement Team) - 2026 Start (PhD)
Location:
San Jose
Employment Type:
Intern
Job Code:
A22204
Apply to this job

Responsibilities
Team Introduction:
We drive monetization through data science.
- Design and interpret experiments.

Qualifications
Minimum Qualifications
- Currently pursuing a PhD degree.
Preferred Qualifications
- Experience with cross-functional teams.

Job Information
Applications will be reviewed on a rolling basis.
The hourly rate range is $55 - $55.
About TikTok
TikTok is a short-form video platform.
"""
        payload = preview_import(self.data, text)
        self.assertEqual(len(payload["items"]), 1)
        item = payload["items"][0]
        self.assertEqual(item["kind"], "job")
        self.assertEqual(item["record"]["title"], "Data Science Intern (Advertisement Team) - 2026 Start (PhD)")
        self.assertEqual(item["record"]["company_name"], "TikTok")
        self.assertEqual(item["record"]["location"], "San Jose")
        self.assertIn("Job code: A22204", item["record"]["notes"])
        self.assertIn("Design and interpret experiments", item["record"]["description"])
        self.assertEqual(item["record"]["salary_range"], "$55 - $55")

    def test_duplicate_import_is_skipped(self):
        person = add_person(self.data, {
            "name": "Avery Kim", "company_name": "Example AI",
            "profile_url": "https://www.linkedin.com/in/avery-example",
        })
        payload = preview_import(
            self.data,
            "Name: Avery Kim\nCompany: Example AI\nLinkedIn: https://www.linkedin.com/in/avery-example",
            "person",
        )
        self.assertEqual(payload["items"][0]["duplicate_ids"], [person["person_id"]])
        saved, skipped = approve_import(self.data, payload)
        self.assertEqual(saved, 0)
        self.assertEqual(len(skipped), 1)

    def test_outreach_updates_person_and_creates_followup(self):
        person = add_person(self.data, {"name": "Avery Kim"})
        interaction, task = log_event(self.data, "outreach", person_id=person["person_id"])
        self.assertEqual(interaction["type"], "outreach")
        self.assertIsNotNone(task)
        expected_due = (date.today() + timedelta(days=7)).isoformat()
        self.assertEqual(task["due_date"], expected_due)
        updated = read_rows(self.data, "people")[0]
        self.assertEqual(updated["status"], "reached_out")
        self.assertEqual(updated["next_action_date"], expected_due)
        done = complete_task(self.data, task["task_id"])
        self.assertEqual(done["status"], "done")

    def test_person_status_toggle_updates_record_and_dashboard(self):
        person = add_person(self.data, {"name": "Avery Kim"})
        updated = set_person_status(self.data, person["person_id"], "reached_out")
        self.assertEqual(updated["status"], "reached_out")
        self.assertEqual(updated["next_action"], "Follow up")
        self.assertEqual(updated["next_action_date"], (date.today() + timedelta(days=7)).isoformat())
        self.assertEqual(len([row for row in read_rows(self.data, "tasks") if row["type"] == "follow_up"]), 1)
        self.assertEqual(len([row for row in read_rows(self.data, "interactions") if row["type"] == "outreach"]), 1)
        output = Path(self.temp.name) / "site" / "index.html"
        generate_dashboard(self.data, output)
        page = output.read_text(encoding="utf-8")
        self.assertIn("person-status-select", page)
        self.assertIn("/api/person-status", page)
        self.assertIn('value="reached_out" selected', page)
        self.assertNotIn("window.location.reload()", page)
        self.assertIn("select.dataset.previous = data.status", page)
        self.assertIn("applyTableFilters('people-table')", page)
        self.assertIn('data-metric="outreach"', page)

    def test_replied_status_logs_metrics_events_once(self):
        person = add_person(self.data, {"name": "Avery Kim"})
        set_person_status(self.data, person["person_id"], "replied")
        set_person_status(self.data, person["person_id"], "replied")
        interactions = read_rows(self.data, "interactions")
        self.assertEqual(sum(row["type"] == "outreach" for row in interactions), 1)
        self.assertEqual(sum(row["type"] == "reply" for row in interactions), 1)

    def test_coffee_chat_creates_calendar_and_one_day_reminder_tasks(self):
        person = add_person(self.data, {"name": "Avery Kim"})
        chat_date = (date.today() + timedelta(days=5)).isoformat()
        interaction, task = log_event(self.data, "coffee_chat", person_id=person["person_id"], event_date=chat_date)
        self.assertEqual(interaction["type"], "coffee_chat")
        self.assertEqual(task["type"], "coffee_chat")
        self.assertIn("calendar invite", task["title"])
        updated = read_rows(self.data, "people")[0]
        self.assertEqual(updated["status"], "chat_scheduled")
        self.assertEqual(updated["next_action"], "Prepare for coffee chat")
        self.assertEqual(updated["next_action_date"], chat_date)
        tasks = read_rows(self.data, "tasks")
        reminders = [row for row in tasks if row["type"] == "chat_reminder"]
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0]["due_date"], (date.today() + timedelta(days=4)).isoformat())

    def test_coffee_chat_requires_an_explicit_exact_date(self):
        person = add_person(self.data, {"name": "Avery Kim"})
        with self.assertRaisesRegex(ValueError, "Coffee chat date is required"):
            log_event(self.data, "coffee_chat", person_id=person["person_id"])
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            log_event(
                self.data,
                "coffee_chat",
                person_id=person["person_id"],
                event_date="2026-08-15T14:00:00",
            )
        self.assertEqual(read_rows(self.data, "interactions"), [])
        self.assertEqual(read_rows(self.data, "people")[0]["status"], "to_research")

    def test_people_table_shows_and_sorts_recorded_coffee_chat_dates(self):
        empty_output = Path(self.temp.name) / "empty-site" / "index.html"
        generate_dashboard(self.data, empty_output)
        self.assertIn('colspan="7" class="empty"', empty_output.read_text(encoding="utf-8"))

        person = add_person(self.data, {"name": "Avery Kim"})
        status_only = add_person(self.data, {"name": "Jordan Lee"})
        set_person_status(self.data, status_only["person_id"], "chat_scheduled")
        past = date.today() - timedelta(days=2)
        nearest = date.today() + timedelta(days=3)
        later = date.today() + timedelta(days=7)
        for chat_date, notes in [
            (past, "Past conversation"),
            (later, "4:00 PM ET · Video call"),
            (nearest, "10:00 AM MT · Google Meet"),
        ]:
            log_event(
                self.data,
                "coffee_chat",
                person_id=person["person_id"],
                event_date=chat_date.isoformat(),
                notes=notes,
            )

        output = Path(self.temp.name) / "site" / "index.html"
        generate_dashboard(self.data, output)
        page = output.read_text(encoding="utf-8")
        self.assertIn("<th>Coffee chat</th>", page)
        self.assertIn('<option value="coffeeChatSort">Sort: coffee chat</option>', page)
        self.assertIn(f'data-coffee-chat="{nearest.isoformat()}"', page)
        self.assertIn(f'data-coffee-chat-sort="0-{nearest.isoformat()}"', page)
        self.assertIn('data-coffee-chat="" data-coffee-chat-sort="2"', page)
        self.assertIn(f'<time datetime="{nearest.isoformat()}">{nearest.strftime("%b")} {nearest.day}, {nearest.year}</time>', page)
        self.assertIn("+2 more", page)
        self.assertIn("Coffee chat schedule", page)
        self.assertIn("Past conversation", page)
        self.assertIn("4:00 PM ET · Video call", page)
        self.assertIn("10:00 AM MT · Google Meet", page)
        self.assertIn("Date not logged", page)
        self.assertIn('colspan="7"', page)
        schedule = page.split("Coffee chat schedule", 1)[1].split("Draft messages", 1)[0]
        self.assertLess(schedule.index(nearest.isoformat()), schedule.index(later.isoformat()))
        self.assertLess(schedule.index(later.isoformat()), schedule.index(past.isoformat()))

    def test_application_updates_job(self):
        job = add_job(self.data, {"title": "Strategy Lead", "company_name": "Example AI"})
        log_event(self.data, "application", job_id=job["job_id"])
        updated = read_rows(self.data, "jobs")[0]
        self.assertEqual(updated["status"], "applied")
        self.assertEqual(updated["next_action"], "Check application status")

    def test_job_fit_details_are_collapsed_and_status_is_editable(self):
        job = add_job(self.data, {
            "title": "Strategy Lead",
            "company_name": "Example AI",
            "status": "researching",
            "fit_score": "87",
            "fit_reasons": "Strong strategy match;Relevant AI experience",
        })
        updated = set_job_status(self.data, job["job_id"], "applied")
        self.assertEqual(updated["status"], "applied")
        self.assertEqual(read_rows(self.data, "jobs")[0]["status"], "applied")
        with self.assertRaisesRegex(ValueError, "Job status must be one of"):
            set_job_status(self.data, job["job_id"], "not_a_status")

        output = Path(self.temp.name) / "site" / "index.html"
        generate_dashboard(self.data, output)
        page = output.read_text(encoding="utf-8")
        self.assertIn('<details class="job-fit-details">', page)
        self.assertIn("<summary>87%</summary>", page)
        self.assertIn("<li>Strong strategy match</li>", page)
        self.assertNotIn('<details class="job-fit-details" open>', page)
        self.assertIn("job-status-select status-applied", page)
        self.assertIn('value="applied" selected', page)
        self.assertIn("/api/job-status", page)
        self.assertIn("applyTableFilters('jobs-table')", page)

    def test_demo_dashboard_is_generated(self):
        seed_demo(self.data)
        output = Path(self.temp.name) / "site" / "index.html"
        generate_dashboard(self.data, output, demo=True)
        page = output.read_text(encoding="utf-8")
        self.assertIn("Career Connection Manager", page)
        self.assertIn("Synthetic demo data", page)
        self.assertIn("Senior Strategy &amp; Operations Lead", page)
        self.assertIn("Agent Center", page)
        self.assertIn("Message agent", page)
        self.assertIn("Resume agent", page)
        self.assertIn("Person agent", page)
        self.assertIn("Company agent", page)
        self.assertIn("Recruiter outreach agent", page)
        self.assertIn("person-toggle", page)
        self.assertIn("Connection points", page)
        self.assertIn("<th>Location</th>", page)
        self.assertIn("<th>Coffee chat</th>", page)
        self.assertIn("Coffee chat schedule", page)
        self.assertIn("10:00 AM PT · Video call", page)
        self.assertIn("Actual messages sent", page)
        self.assertIn("Draft messages", page)
        self.assertIn("Asked about the strategy team", page)
        self.assertIn("Salary", page)
        self.assertIn("Pursue?", page)
        self.assertIn("data-view=\"messages\"", page)
        self.assertIn("Messages", page)
        self.assertEqual(validate(self.data), [])

    def test_message_draft_is_labeled_and_does_not_count_as_sent(self):
        person = add_person(self.data, {"name": "Avery Kim"})
        log_event(
            self.data,
            "note",
            person_id=person["person_id"],
            notes="Hi Avery, I would love to connect!",
        )
        interactions = read_rows(self.data, "interactions")
        interactions[0]["result"] = "message_draft"
        from career_crm.core import write_rows
        write_rows(self.data, "interactions", interactions)
        output = Path(self.temp.name) / "site" / "index.html"
        generate_dashboard(self.data, output)
        page = output.read_text(encoding="utf-8")
        self.assertIn("Draft messages", page)
        self.assertIn("Hi Avery, I would love to connect!", page)
        self.assertIn(">Draft<", page)
        self.assertEqual(read_rows(self.data, "people")[0]["status"], "to_research")

    def test_pursuit_decision_is_saved_with_timestamp(self):
        job = add_job(self.data, {"title": "Strategy Lead", "company_name": "Example AI"})
        decided = set_pursuit_decision(self.data, job["job_id"], "pursue")
        self.assertEqual(decided["pursuit_decision"], "pursue")
        self.assertTrue(decided["pursuit_decided_at"])
        persisted = read_rows(self.data, "jobs")[0]
        self.assertEqual(persisted["pursuit_decision"], "pursue")
        reset = set_pursuit_decision(self.data, job["job_id"], "undecided")
        self.assertEqual(reset["pursuit_decided_at"], "")

    def test_agent_prompts_use_saved_context_and_safety_rules(self):
        seed_demo(self.data)
        message = message_prompt(self.data, "per_maya", "job_strategy")
        self.assertIn("Maya Chen", message)
        self.assertIn("Do not send the message", message)
        self.assertIn("write a different first-contact note", message)
        self.assertIn("Make shared background conversational", message)
        self.assertIn("User's outreach playbook", message)
        connection = message_prompt(self.data, "per_maya", "job_strategy", stage="connection_request")
        self.assertIn("Outreach stage: connection_request", connection)
        self.assertIn("Do not ask for a chat, exact role, job ID", connection)
        after_acceptance = message_prompt(self.data, "per_maya", "job_strategy", stage="after_acceptance")
        self.assertIn("casual 15–20 minute conversation", after_acceptance)
        self.assertIn("must not be a copy of the connection request", after_acceptance)
        resume = resume_prompt(self.data, "job_strategy", "Experience\n- Led a research project")
        self.assertIn("Senior Strategy & Operations Lead", resume)
        self.assertIn("Do not invent experience", resume)
        person = person_research_prompt(self.data, "per_jordan")
        self.assertIn("public professional work", person)
        self.assertIn("Do not search for or report home addresses", person)
        company = company_research_prompt(self.data, "com_cascade", "job_bizops")
        self.assertIn("Cascade Intelligence", company)
        self.assertIn("separating fact from inference", company)
        recruiter = recruiter_outreach_prompt(self.data, "job_strategy", "per_maya")
        self.assertIn("Best person to contact", recruiter)
        self.assertIn("no more than 300 characters", recruiter)
        self.assertIn("Do not infer gender", recruiter)
        self.assertIn("current-employment gate", recruiter)
        self.assertIn("former or unverified", recruiter)

    def test_recruiter_outreach_agent_supports_search_and_selected_person(self):
        seed_demo(self.data)
        search_prompt, search_path = execute_agent(self.data, {
            "action": "recruiter-outreach",
            "job_id": "job_strategy",
            "dry_run": True,
        })
        self.assertIn("Find public evidence for suitable recruiters", search_prompt)
        self.assertEqual(search_path, "")
        selected_prompt, selected_path = execute_agent(self.data, {
            "action": "recruiter-outreach",
            "job_id": "job_strategy",
            "person_id": "per_maya",
            "dry_run": True,
        })
        self.assertIn("Research the selected person first", selected_prompt)
        self.assertIn("Maya Chen", selected_prompt)
        self.assertEqual(selected_path, "")

    def test_agent_response_keeps_web_citation_urls(self):
        payload = {
            "output": [{
                "content": [{
                    "type": "output_text",
                    "text": "A sourced finding.",
                    "annotations": [{
                        "type": "url_citation",
                        "title": "Example source",
                        "url": "https://example.com/source",
                    }],
                }],
            }],
        }
        text = extract_output_text(payload)
        self.assertIn("A sourced finding.", text)
        self.assertIn("[Example source](https://example.com/source)", text)

    def test_clickable_agent_endpoint_logic_supports_dry_run(self):
        seed_demo(self.data)
        result, report_path = execute_agent(self.data, {
            "action": "message",
            "person_id": "per_maya",
            "job_id": "job_strategy",
            "channel": "linkedin",
            "stage": "connection_request",
            "dry_run": True,
        })
        self.assertIn("Maya Chen", result)
        self.assertIn("Outreach stage: connection_request", result)
        self.assertIn("Do not send the message", result)
        self.assertEqual(report_path, "")


if __name__ == "__main__":
    unittest.main()
