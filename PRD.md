---
title: Career Connection Manager PRD
tags: [career, product-management, personal-crm]
status: draft
updated: 2026-08-08
---

# Career Connection Manager

## Product summary

Career Connection Manager is a local, terminal-first tool for running a focused job search.
It turns pasted text, CSV files, and structured Markdown into people, jobs, tasks, and reminders shown on a private HTML dashboard.

**Working promise:** Paste messy career information, get a clear next action.

## Initial career target

**Target statement:** Pursue Strategy and Business Operations roles at frontier AI companies in the San Francisco Bay Area or Seattle, using eight years of experience and an advanced degree to help teams make high-impact decisions and scale new products or businesses.

### Candidate profile

- Eight years of professional experience.
- Advanced degree holder.
- Interested in work that connects strategy, operations, products, and company growth.

### Target role areas

- Strategy and Operations
- Business Operations
- Strategic Initiatives
- Product Strategy
- Go-to-Market Strategy and Operations
- Chief of Staff roles with strong strategy and execution ownership

### Target seniority

- Primary: Senior, Lead, or experienced individual-contributor roles.
- Secondary: Manager roles that value advanced-degree training and roughly eight years of experience.
- Review manually: Principal, Director, and executive roles because title levels differ across companies.

### Target locations

- San Francisco Bay Area, including San Francisco, the Peninsula, and South Bay.
- Seattle metropolitan area.
- Remote roles only when the team is based in one of the target areas or the role closely matches the career target.

### Search keywords

`strategy`, `business operations`, `bizops`, `strategic initiatives`, `product strategy`, `go-to-market`, `GTM`, `operations`, `chief of staff`, `AI`, `foundation models`, `frontier models`, `AI agents`, `developer platform`, `enterprise AI`, `research`, `commercialization`, `scaling`

### Fit rules

A strong-fit role matches a target function, a target location, and an organization building or enabling frontier AI systems.
The fit review should also check scope, years of experience, degree preferences, decision ownership, cross-functional work, and whether the role supports important products or company growth.

## Problem statement

A job seeker keeps goals, contacts, job posts, notes, and follow-ups in different places.
This makes it easy to forget a person, miss a deadline, or spend time cleaning data instead of building relationships.

Existing personal CRMs focus on general relationships, while job trackers focus on applications.
This product connects career goals, target people, target jobs, and daily actions in one simple system.

## Target user

The first user is a busy job seeker who is comfortable using a terminal and wants full control of their own data.
The first release is designed for one person, one device, and one active job search.

## Jobs to be done

- When I find people or jobs online, help me save them quickly without filling a long form.
- When I start my day, tell me whom to contact, what to follow up on, and which jobs need action.
- When I prepare for a conversation or application, show the goal, company, person, job description, and past notes together.
- When I review my search, show whether I am taking useful actions and getting results.

## Goals

- Turn pasted text into editable records in under 60 seconds for at least 80% of imports.
- Make every active contact and job have a clear status and next action.
- Show all overdue and due-today work on one dashboard page.
- Let the user correct any AI extraction before data is saved.
- Produce a polished public demo that explains product choices, architecture, and measured results without exposing private data.

## Non-goals for v1

- Do not send emails, LinkedIn messages, or applications automatically; the risk of a wrong action is too high for the first release.
- Do not build a full LinkedIn connection sync; broad member-data access needs platform approval and creates privacy risk.
- Do not scrape sites that block scraping or require a logged-in session; v1 accepts user-provided text and allowed public feeds.
- Do not support teams, accounts, cloud sync, or mobile apps; they do not test the core idea.
- Do not rank people by social value; the product should support thoughtful relationships, not treat people like sales leads.

## Core user stories

### Job seeker

- As a job seeker, I want to save my target role, areas, companies, keywords, and seniority so that the system knows what fits my search.
- As a job seeker, I want to paste messy text so that the system can suggest structured people and job records.
- As a job seeker, I want to review and edit suggested fields before saving so that wrong AI output does not enter my data.
- As a job seeker, I want to link a person to a company or job so that I can understand why the relationship matters.
- As a job seeker, I want to record outreach, replies, chats, and applications so that I always know the current state.
- As a job seeker, I want a daily action list so that I know what to do next.
- As a job seeker, I want a contact brief before a coffee chat so that I can prepare quickly.
- As a job seeker, I want to export or edit my data as CSV and Markdown so that I always control it.

### Portfolio reviewer

- As a hiring manager, I want to see the user problem, product choices, working demo, and results so that I can judge the candidate's PM skill.
- As a hiring manager, I want sample data and a simple setup path so that I can try the product without seeing private information.

## Key objects and fields

| Object | Required fields | Useful optional fields |
|---|---|---|
| Career target | statement, role areas, seniority | locations, keywords, excluded roles |
| Company | name, target status | industry, location, careers URL, notes |
| Person | name, company or context, relationship status | title, profile URL, keywords, last contact, next action |
| Job | title, company, source URL or source note, status | location, description, deadline, fit notes |
| Interaction | person, type, date | notes, result, job link |
| Task | type, due date, status | person link, job link, priority, note |

Use stable IDs to connect objects, such as `person_id`, `company_id`, and `job_id`.
Do not use a person's name as an ID because names can repeat or change.

## Status rules

### Person status

`to_research` → `ready_to_reach_out` → `reached_out` → `replied` → `chat_scheduled` → `relationship_active`

A person can also be `paused` or `closed`.

### Job status

`saved` → `researching` → `ready_to_apply` → `applied` → `interviewing` → `offer` → `accepted`

A job can also be `rejected`, `withdrawn`, or `closed`.

## P0: must-have requirements

### 1. Career target

The user can create and edit one active career target from the terminal or a structured Markdown file.

Acceptance criteria:

- [ ] The target includes a statement, role areas, seniority, target companies, and keywords.
- [ ] Missing required fields produce a plain-language error.
- [ ] The dashboard shows the active target at the top.

### 2. Paste-and-review import

The user can paste plain text containing people, jobs, or both, and the agent proposes structured records.

Acceptance criteria:

- [ ] The command accepts text from a file or standard input.
- [ ] The output labels uncertain or missing fields instead of inventing facts.
- [ ] The user sees a preview and can edit, approve, or cancel before saving.
- [ ] The original source text and import time are kept for checking.
- [ ] Duplicate candidates are shown before a new record is created.

### 3. Local source of truth

The system saves data in documented CSV or Markdown files and validates their structure before dashboard generation.

Acceptance criteria:

- [ ] A human can edit the files without using the app.
- [ ] Invalid rows show the file, row, field, and suggested fix.
- [ ] Unknown extra fields are preserved or clearly rejected; they are never silently deleted.
- [ ] A backup is made before a command changes existing data.

### 4. Contact and job tracking

The user can add, edit, link, search, filter, and archive people and jobs from the terminal.

Acceptance criteria:

- [ ] Every active record has a status and next action or an explicit `none` value.
- [ ] A person can link to many jobs, and a job can link to many people.
- [ ] Search works across names, companies, job titles, keywords, and notes.
- [ ] Archiving hides a record from active views without deleting it.

### 5. Actions and reminders

The system creates tasks for outreach, follow-up, coffee chats, and application deadlines.

Acceptance criteria:

- [ ] Tasks can be due today, upcoming, overdue, done, or dismissed.
- [ ] Completing outreach updates the person's last-contact date and can suggest a follow-up date.
- [ ] A scheduled chat appears in both the person's history and the dashboard agenda.
- [ ] No message is sent and no application is submitted without a separate user action outside v1.

### 6. Static HTML dashboard

One terminal command builds a private, responsive HTML dashboard from the local files.

Acceptance criteria:

- [ ] The home view shows the career target, today's actions, overdue work, upcoming chats, and pipeline counts.
- [ ] People and jobs have searchable, filterable list views and useful detail views.
- [ ] Empty states explain how to add the first item.
- [ ] The dashboard shows when it was last built and warns when input data is invalid.
- [ ] A demo mode uses fake data and contains no private records.

### 7. Basic product analytics

The system calculates useful local metrics without sending personal data to an outside analytics service.

Acceptance criteria:

- [ ] The dashboard shows outreach sent, reply rate, chats held, applications sent, and interview rate.
- [ ] A Messages view shows the exact saved outreach and replies with their linked person, job, and date.
- [ ] Clicking a person's name expands their saved connection points and actual sent-message history without leaving the People view.
- [ ] Each metric has a visible definition and date range.
- [ ] The user can exclude test or demo records from metrics.

### 8. Human-reviewed agent system

The user can run separate terminal agents for message drafting, resume tailoring, person research, company or team research, and recruiter outreach research.

Acceptance criteria:

- [ ] The message agent drafts LinkedIn or email text from saved facts and never sends it.
- [ ] The message agent uses explicit relationship stages, saved approved examples, and the user's personalization hierarchy.
- [ ] A first connection request builds familiarity and does not name the exact role or ask for a referral, introduction, call, application help, or resume review unless the user explicitly requests it.
- [ ] The resume agent compares a saved job description with resume text and never invents experience or overwrites the source file.
- [ ] The person research agent uses public professional sources, checks identity, includes URLs, and avoids sensitive personal information.
- [ ] The company research agent separates sourced facts from inference and includes unknowns and contradictions.
- [ ] The recruiter outreach agent can start from a job, find publicly verifiable recruiters or team contacts, and explain each contact's relevance and confidence.
- [ ] The recruiter outreach agent compares sourced professional facts with the user's private outreach profile and drafts connection, post-acceptance, follow-up, and fallback messages.
- [ ] The recruiter outreach agent never infers protected traits from names or photos and never treats a protected trait as evidence that someone owns a role.
- [ ] Every agent supports a dry-run that reveals its full prompt without calling an API.
- [ ] Every completed output is saved as a draft with a visible human-review warning.
- [ ] Research agents access the web only when the user explicitly runs them.
- [ ] No agent sends messages, submits applications, changes source records, or publishes research automatically.

## P1: nice-to-have requirements

- Import public jobs from company feeds that allow it, starting with Greenhouse and Lever.
- Score job fit against the career target, while showing the reasons and letting the user override the score.
- Add reusable voice preferences and examples for outreach drafts.
- Generate a one-page briefing for a person, company, job, or coffee chat.
- Read calendar exports or an approved calendar API to suggest scheduled chats.
- Add a local search assistant that answers questions using only the saved data.

## P2: future considerations

- Approved LinkedIn integration if the needed product access becomes available.
- Gmail or Outlook integration with clear consent and narrow permissions.
- Automatic monitoring of allowed company careers pages.
- Multi-device sync with encryption and a clear privacy model.
- Relationship-change alerts, such as a contact starting a new job.
- Browser extension or share action for quicker capture.

## Main experience

1. The user writes a career target and runs a setup command. The tool validates it and builds the first dashboard.
2. The user copies people or job text into a `.txt` file or terminal command. The agent extracts fields and shows a review screen.
3. The user fixes any errors and approves the import. The tool saves records and reports duplicates or missing data.
4. The user logs outreach, applications, replies, and chats with short commands. Each event updates status, history, and the next action.
5. The user rebuilds or auto-refreshes the dashboard. The “Today” page shows the smallest useful set of next actions.

## Example commands

```bash
career init
career import --type auto inbox.txt
career review-import
career person add
career log outreach --person PERSON_ID
career log application --job JOB_ID
career task done TASK_ID
career dashboard build
career doctor
```

The final command names may change after testing with users.
The PRD defines user outcomes, not a fixed technical interface.

## Data and AI rules

- Local files are the source of truth, and generated HTML is a view that can be rebuilt.
- AI output is a suggestion until the user approves it.
- The system must show uncertainty and source text for extracted facts.
- The system must not guess contact details, employment history, or application facts.
- Private data must be excluded from logs, screenshots, tests, and the public portfolio repository.
- The public demo must use clearly labeled synthetic data.

## Success metrics

### Leading indicators

- At least 80% of test imports create a usable draft without manual retyping.
- Median time from paste to approved record is below 60 seconds.
- At least 95% of active records have a valid status and next action.
- At least 90% of dashboard builds succeed without manual file repair.
- The user completes at least 70% of due actions each week during a four-week test.

### Lagging indicators

- The user misses no recorded coffee chat or application deadline during a four-week test.
- At least three target users can complete the core flow without live help.
- At least two PM reviewers can explain the product problem and main tradeoff after viewing the portfolio page.
- The final case study includes one measured learning that caused a product change.

## Risks and safeguards

| Risk | Safeguard |
|---|---|
| AI extracts wrong facts | Preview, confidence flags, source text, and user approval |
| Duplicate people or jobs | Matching preview and manual merge |
| Private data leaks into GitHub | Separate private data folder, ignore rules, fake demo data, and secret scan |
| Reminder overload | One daily view, clear priority, snooze, and dismissal |
| LinkedIn access is unavailable | Keep LinkedIn sync outside v1 and support manual paste/export |
| Careers-page formats change | Start with documented public feeds and keep adapters separate |
| The project becomes too large | Ship the full manual workflow before integrations or message drafting |

## Recommended technical shape

Use a small Python command-line app because it handles text, CSV, Markdown, and HTML generation well.
Use validated data models, local files for v1, a template engine for static HTML, and tests built around sample data.

Keep four parts separate: import, validation/storage, workflow rules, and dashboard rendering.
This makes later APIs replaceable without changing the core records.

## Delivery phases

### Phase 0: define and test the workflow

Use sample files to test the data model and manually walk through one full job-search week.
The output is a schema, fake dataset, and low-detail dashboard sketch.

### Phase 1: usable local MVP

Build manual add/edit commands, validation, status rules, reminders, and the static dashboard.
The phase is done when one real user can run the system for one week without editing code.

### Phase 2: agentic capture

Add pasted-text extraction, uncertainty labels, duplicate checks, and approval before save.
The phase is done when 80% of test imports produce usable drafts in under 60 seconds.

### Phase 3: allowed job feeds

Add one Greenhouse adapter and one Lever adapter, then test failures and changed fields.
The phase is done when imports are repeatable and never overwrite user edits silently.

### Phase 4: portfolio case study

Publish the fake-data demo, setup guide, architecture, product decisions, user tests, metrics, and a short demo video.
The case study should show what was cut, what failed, what changed after testing, and why.

## Portfolio deliverables

- A short README with the problem, user, value, demo, setup, screenshots, and roadmap.
- This PRD plus a small architecture diagram and data dictionary.
- A public demo dataset that tells a realistic job-search story.
- A 60–90 second video showing paste → review → dashboard → completed action.
- Three short user-test notes and the product changes caused by them.
- A results section with speed, extraction quality, completion rate, and known limits.
- Clear privacy, responsible-AI, and API-use notes.

## Open questions

- **[Product, blocking]** Is the first user only the creator, or should the MVP be tested with other job seekers?
- **[Product, blocking]** Which frontier AI companies should be placed in the first target-company list?
- **[Design, non-blocking]** Should the main view optimize for today's tasks or the full search pipeline?
- **[Engineering, blocking]** Should CSV or Markdown be the main editable format when the same record changes often?
- **[Data, non-blocking]** What rules define a duplicate person or job when information is incomplete?
- **[Legal/privacy, blocking for integrations]** Which public career feeds and API terms allow storage and display of job data?
- **[Product, non-blocking]** What follow-up timing should be suggested by default?

## Launch decision

The MVP is ready when the creator can complete the full weekly workflow using real private data and a reviewer can complete the same flow using fake data.
LinkedIn sync, automatic sending, and broad web scraping are not required for launch.

## Reference notes

- [Dex](https://getdex.com/) shows the value of one place for relationships, notes, reminders, and contact updates.
- [Shubham Saboo's GitHub profile](https://github.com/Shubhamsaboo) shows a clear builder identity, ready-to-run examples, and a strong flagship repository.
- [LinkedIn API access](https://learn.microsoft.com/en-us/linkedin/shared/authentication/getting-access) explains that open access is narrow and most member-data permissions need approval.
- [Greenhouse Job Board API](https://developer.greenhouse.io/job-board.html) and [Lever Postings API](https://github.com/lever/postings-api) are practical starting points for allowed public job imports.
