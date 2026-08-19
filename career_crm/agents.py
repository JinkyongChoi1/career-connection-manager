from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

from .core import find_row, read_rows, target_summary


DEFAULT_MODEL = "gpt-5.6-sol"
API_URL = "https://api.openai.com/v1/responses"

BASE_RULES = """You are part of a private career connection manager.
Follow these rules:
- Never invent a fact, relationship, achievement, skill, or source.
- Clearly label unknowns and suggestions.
- Keep the user's voice direct, warm, specific, and professional.
- Treat people as relationships, not sales leads.
- Never claim that a message was sent or a resume was changed.
- Do not include sensitive personal information or speculate about protected traits.
- Return clean Markdown that a human can review.
"""


def compact_record(record: Mapping[str, str], excluded: Sequence[str] = ()) -> str:
    lines = []
    for key, value in record.items():
        if value and key not in excluded:
            lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    return "\n".join(lines) or "- No saved details"


def context(data_dir: Path) -> Dict[str, object]:
    return {
        "target": target_summary(data_dir / "target.md"),
        "people": read_rows(data_dir, "people"),
        "jobs": read_rows(data_dir, "jobs"),
        "companies": read_rows(data_dir, "companies"),
        "interactions": read_rows(data_dir, "interactions"),
    }


def outreach_profile(data_dir: Path) -> str:
    path = data_dir / "outreach_profile.md"
    if not path.exists():
        return "No outreach profile has been saved."
    text = path.read_text(encoding="utf-8").strip()
    return text or "No outreach profile has been saved."


def outreach_playbook(data_dir: Path) -> str:
    path = data_dir / "outreach_playbook.md"
    if not path.exists():
        return "No outreach playbook has been saved."
    text = path.read_text(encoding="utf-8").strip()
    return text or "No outreach playbook has been saved."


def message_prompt(
    data_dir: Path,
    person_id: str,
    job_id: str = "",
    channel: str = "linkedin",
    goal: str = "start a thoughtful conversation",
    stage: str = "auto",
) -> str:
    ctx = context(data_dir)
    person = find_row(ctx["people"], "person_id", person_id)  # type: ignore[arg-type]
    if not person:
        raise ValueError(f"No person found with ID {person_id}")
    job = find_row(ctx["jobs"], "job_id", job_id) if job_id else None  # type: ignore[arg-type]
    interactions = [row for row in ctx["interactions"] if row.get("person_id") == person_id]  # type: ignore[union-attr]
    limits = "300 characters" if channel == "linkedin" else "150 words"
    inferred_stage = "connection_request" if not interactions else "relationship_follow_up"
    resolved_stage = inferred_stage if stage == "auto" else stage
    return f"""{BASE_RULES}

Task: Draft a {channel} message to the person below.
Goal: {goal}
Length limit: {limits}
Outreach stage: {resolved_stage}

Career target:
{json.dumps(ctx['target'], ensure_ascii=False, indent=2)}

Person record:
{compact_record(person, excluded=('source_text',))}

Related job:
{compact_record(job or {}, excluded=('source_text',))}

Past interactions:
{json.dumps(interactions, ensure_ascii=False, indent=2)}

User's outreach profile, rules, and approved examples:
--- PROFILE START ---
{outreach_profile(data_dir)}
--- PROFILE END ---

User's outreach playbook:
--- PLAYBOOK START ---
{outreach_playbook(data_dir)}
--- PLAYBOOK END ---

Drafting sequence:
1. Identify the outreach stage from the goal and interaction history.
2. Follow any user-supplied template closely, changing only the details needed for this person.
3. For `connection_request`, write a different first-contact note: build familiarity with one natural observation, add one specific but modest compliment, express broad interest when useful, and ask only to connect. Do not ask for a chat, exact role, job ID, referral, introduction, application help, or resume review at this stage.
4. For `after_acceptance`, thank the person, explain what Jin is learning, and ask for a casual 15–20 minute conversation with one specific, answerable question. This message must not be a copy of the connection request.
5. For `after_team_chat`, thank the person, mention one concrete learning, explain that interest increased, and ask whether sending a resume or continuing the conversation would be convenient.
6. For `chat_confirmation`, write a concise confirmation email with the agreed date/time, meeting link, purpose, and a warm sign-off. Do not invent missing logistics; use [ADD TIME] or [ADD LINK].
7. For `chat_reminder`, write a short reminder for the day before the scheduled chat that confirms the time and link without restarting the networking pitch.
8. Choose the strongest truthful connection in this order: verified shared school or program; shared current location; shared employer or professional background; similar international or cross-country career path; then a specific public professional interest.
9. Use one strong connection, or at most two connected details. Make shared background conversational; use “also” only when the saved profile proves the overlap, and do not write phrases such as "I also have Columbia ties."
10. Compliments should refer to a specific career choice, project, or professional focus. Avoid generic praise.
11. Ask about the person as a human professional: a transition, decision, market, product, or team visible in their public work. Avoid generic questions about their day-to-day.
12. Never mention a mutual connection unless the saved facts say the user genuinely knows that person well enough to support an introduction.
13. Before returning the draft, check that every "we both" or "fellow" claim is supported and that the draft fits the channel limit.

Return:
1. A primary draft for the requested stage.
2. A one-sentence note explaining what was personalized.
3. Any fact that the user should verify before sending.

Do not pretend the user knows this person unless the saved record proves it.
Do not send the message.
"""


def resume_prompt(data_dir: Path, job_id: str, resume_text: str) -> str:
    ctx = context(data_dir)
    job = find_row(ctx["jobs"], "job_id", job_id)  # type: ignore[arg-type]
    if not job:
        raise ValueError(f"No job found with ID {job_id}")
    if not resume_text.strip():
        raise ValueError("Resume text is empty.")
    return f"""{BASE_RULES}

Task: Review the job description and recommend truthful resume tailoring.

Career target:
{json.dumps(ctx['target'], ensure_ascii=False, indent=2)}

Job record and description:
{compact_record(job, excluded=('source_text',))}

Current resume:
--- RESUME START ---
{resume_text}
--- RESUME END ---

Return:
1. A fit summary with strong matches, partial matches, and gaps.
2. The 8–12 most important job keywords, separated into supported and unsupported keywords.
3. Suggested edits to the summary and existing bullets.
4. A tailored resume draft that preserves the user's facts.
5. Questions that must be answered before any unsupported claim could be added.

Do not invent experience, metrics, employers, dates, degrees, skills, or achievements.
Use [VERIFY] when the source material does not support a useful claim.
Do not overwrite the resume file.
"""


def person_research_prompt(data_dir: Path, person_id: str, purpose: str = "prepare for outreach") -> str:
    ctx = context(data_dir)
    person = find_row(ctx["people"], "person_id", person_id)  # type: ignore[arg-type]
    if not person:
        raise ValueError(f"No person found with ID {person_id}")
    return f"""{BASE_RULES}

Task: Research this person's public professional work to {purpose}.

Saved person record:
{compact_record(person, excluded=('source_text', 'email'))}

Career target:
{json.dumps(ctx['target'], ensure_ascii=False, indent=2)}

Use public, professional sources only.
Prefer the person's employer page, authored work, talks, interviews, conference pages, and clearly attributable public profiles.

Return:
1. Identity check: explain why the sources appear to describe the same person.
2. Current role and professional focus.
3. Two to five recent or relevant projects, talks, posts, or themes.
4. Three thoughtful conversation angles connected to the user's career target.
5. Facts to verify or identity ambiguities.
6. Sources as direct URLs next to each factual claim.

Do not search for or report home addresses, family details, private contact data, protected traits, or unrelated personal life.
Do not guess when identity is ambiguous.
"""


def recruiter_outreach_prompt(
    data_dir: Path,
    job_id: str,
    person_id: str = "",
    goal: str = "find a suitable recruiter or team member and prepare personalized outreach",
) -> str:
    ctx = context(data_dir)
    job = find_row(ctx["jobs"], "job_id", job_id)  # type: ignore[arg-type]
    if not job:
        raise ValueError(f"No job found with ID {job_id}")
    person = find_row(ctx["people"], "person_id", person_id) if person_id else None  # type: ignore[arg-type]
    if person_id and not person:
        raise ValueError(f"No person found with ID {person_id}")
    selected_instruction = (
        "Research the selected person first. Also identify a better recruiter or team contact if public evidence shows the selected person is not relevant."
        if person else
        "Find public evidence for suitable recruiters, sourcers, hiring managers, or current team members connected to this role."
    )
    return f"""{BASE_RULES}

Task: {goal}.
{selected_instruction}

Job record and full saved description:
{compact_record(job, excluded=('source_text',))}

Selected person, if any:
{compact_record(person or {}, excluded=('source_text', 'email'))}

User's private outreach profile:
--- PROFILE START ---
{outreach_profile(data_dir)}
--- PROFILE END ---

Research rules:
- Use public professional sources only, and attach a direct URL to every fact about a person.
- Prefer current evidence: a company bio, an authored post, a public professional profile, a conference page, or a recent job-related post.
- Apply a current-employment gate before recommending anyone: verify that the person's present employer is the hiring company using a current profile or another source updated within the last 90 days.
- An old TikTok post, a stale search-result title, or past TikTok experience is not evidence of current employment.
- If current sources conflict, label the person "former or unverified" and exclude them from Best person and Backup contacts.
- Explain whether each person appears to be a recruiter, hiring manager, team member, or uncertain.
- Do not claim that someone recruits for this exact role without evidence.
- Do not infer gender, race, ethnicity, nationality, religion, disability, sexual orientation, age, or immigration status from a name, photo, language, or appearance.
- Do not rank people because of a protected trait. A community or identity connection may be mentioned only when the person explicitly self-identifies in a public professional context and it is respectful and genuinely relevant.
- Do not find private emails, phone numbers, home addresses, family details, or other private data.
- Treat shared schools, employers, fields, career changes, professional interests, and public communities as possible overlap—not proof of a relationship.
- A compliment must name a specific piece of work or insight. Avoid flattery.
- "Add value" means offering a useful perspective, relevant experience, thoughtful question, or resource; never promise help the user cannot provide.
- For the initial connection request, lead with familiarity and shared context. Do not name the exact role or job ID, and do not ask about the role, an introduction, a referral, a call, the application process, or a resume.
- Follow the user's approved templates and examples closely. Prefer their demonstrated phrasing and personalization order over generic recruiting language.
- Personalization priority for this user is: shared current location; international or cross-country career path; verified school/employer/community overlap; then a specific professional observation.
- Mention general interest in the company lightly, and move role-specific discussion to the after-acceptance message.
- Never mention a mutual connection unless the saved profile confirms the user genuinely knows that person.

Return clean Markdown with these sections:
1. Best person to contact: name, current role, why relevant, confidence, and evidence.
2. Backup contacts: up to four people, each with role, reason, confidence, and source.
3. Personalization evidence table: public fact about the person, matching fact from the user's profile, why it is a natural connection, source, and verification status.
4. Connection request: warm and specific, no more than 300 characters.
5. After acceptance: ask for a casual team conversation before application-process questions; no more than 100 words.
6. Follow-up after the team conversation: thank the recruiter, name one real learning, express interest, and ask permission to send a resume; use [ADD LEARNING] until the conversation occurs.
7. If no introduction is offered: a short, gracious response with one easy role question.
8. Verify before sending: list every uncertain, stale, or potentially sensitive detail.

Do not send messages, contact people, or state that an introduction happened.
If no suitable person can be verified, say so and provide better search queries instead of inventing a contact.
"""


def company_research_prompt(data_dir: Path, company: str, job_id: str = "") -> str:
    ctx = context(data_dir)
    company_rows = ctx["companies"]  # type: ignore[assignment]
    company_record = next((row for row in company_rows if row.get("company_id") == company or row.get("name", "").lower() == company.lower()), None)
    jobs = [row for row in ctx["jobs"] if row.get("company_id") == company or row.get("company_name", "").lower() == company.lower()]  # type: ignore[union-attr]
    if job_id:
        selected = find_row(ctx["jobs"], "job_id", job_id)  # type: ignore[arg-type]
        if not selected:
            raise ValueError(f"No job found with ID {job_id}")
        jobs = [selected]
        if not company_record:
            company_record = {"name": selected.get("company_name", "")}
    if not company_record:
        company_record = {"name": company}
    return f"""{BASE_RULES}

Task: Research this company and, where public evidence allows, the team related to the saved role.

Saved company record:
{compact_record(company_record)}

Related job records:
{json.dumps(jobs, ensure_ascii=False, indent=2)}

Career target:
{json.dumps(ctx['target'], ensure_ascii=False, indent=2)}

Prefer the company website, product and research pages, official announcements, job pages, reputable reporting, and public talks by team members.

Return:
1. What the company builds and for whom.
2. Current products, strategy, business model, and important recent changes.
3. What the relevant team appears to own, clearly separating fact from inference.
4. Likely challenges for a Strategy or Business Operations hire.
5. Five interview or coffee-chat questions.
6. Unknowns, contradictions, and facts to verify.
7. Sources as direct URLs next to each factual claim.

Do not present a team structure as fact unless a reliable public source supports it.
"""


def extract_output_text(payload: Mapping[str, object]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    chunks: List[str] = []
    sources: List[str] = []
    for item in payload.get("output", []):  # type: ignore[union-attr]
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text" and content.get("text"):
                chunks.append(str(content["text"]))
                for annotation in content.get("annotations", []):
                    if not isinstance(annotation, dict) or annotation.get("type") != "url_citation":
                        continue
                    url = str(annotation.get("url", "")).strip()
                    title = str(annotation.get("title", "Source")).strip() or "Source"
                    if url:
                        source = f"- [{title}]({url})"
                        if source not in sources:
                            sources.append(source)
    if not chunks:
        raise ValueError("The API response did not contain output text.")
    result = "\n".join(chunks).strip()
    if sources:
        result += "\n\n## API-returned sources\n\n" + "\n".join(sources)
    return result


def run_openai(prompt: str, web_search: bool = False, model: str = "") -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY is not set. Export it in your shell, or rerun the command with --dry-run to inspect the agent prompt."
        )
    body: Dict[str, object] = {
        "model": model or os.environ.get("CAREER_AGENT_MODEL", DEFAULT_MODEL),
        "input": prompt,
    }
    if web_search:
        body["tools"] = [{"type": "web_search"}]
        body["tool_choice"] = "auto"
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            message = json.loads(detail).get("error", {}).get("message", detail)
        except json.JSONDecodeError:
            message = detail
        raise ValueError(f"OpenAI API error ({exc.code}): {message}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Could not reach the OpenAI API: {exc.reason}") from exc
    return extract_output_text(payload)


def save_report(data_dir: Path, agent_name: str, subject: str, content: str, model: str = "") -> Path:
    folder = data_dir / "agent_outputs"
    folder.mkdir(parents=True, exist_ok=True)
    safe_subject = re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-")[:50] or "report"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = folder / f"{stamp}_{agent_name}_{safe_subject}.md"
    title = f"{agent_name.replace('-', ' ').title()}: {subject}"
    body = f"""---
title: {json.dumps(title)}
tags: [career, agent, {agent_name}]
status: draft
updated: {datetime.now().date().isoformat()}
---

# {title}

> [!warning] Human review required
> This is an agent-produced draft or research report.
> Verify factual claims and sources before using it.

{content.strip()}

## Run details

- Agent: `{agent_name}`
- Model: `{model or os.environ.get('CAREER_AGENT_MODEL', DEFAULT_MODEL)}`
- Generated: `{datetime.now().replace(microsecond=0).isoformat()}`
"""
    path.write_text(body, encoding="utf-8")
    return path
