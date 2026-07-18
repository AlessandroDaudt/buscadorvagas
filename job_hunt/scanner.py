import csv
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from tinyfish import RateLimitError, TinyFish

from job_hunt.llm_utils import chat_with_llm
from job_hunt.log import get_logger
from job_hunt.notifier import send_telegram

logger = get_logger()

STATE_FILE = Path("state/seen_jobs.json")
LAST_SCAN_FILE = Path("state/last_scan.json")
JOB_HISTORY_FILE = Path("state/job_history.json")
LAST_RUN_REPORT_FILE = Path("state/last_run_report.json")

JOB_URL_RE = re.compile(
    r"/(job|jobs|opening|openings|position|positions|vacancy|vacancies|role|roles|apply)"
    r"/[a-zA-Z0-9_%@.-]{4,}",
    re.IGNORECASE,
)
ATS_JOB_RE = re.compile(
    r"(greenhouse\.io/.+/jobs/\d+"
    r"|lever\.co/[^/]+/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"
    r"|myworkdayjobs\.com/[^?#]+"
    r"|smartrecruiters\.com/[^/]+/[A-Z0-9]+"
    r"|ashbyhq\.com/[^/]+/[a-f0-9-]{32,})",
    re.IGNORECASE,
)
ATS_LISTING_RE = re.compile(
    r"^https?://(jobs\.lever\.co|boards\.greenhouse\.io|apply\.workable\.com"
    r"|jobs\.smartrecruiters\.com)/[^/?#]+/?(\?.*)?$",
    re.IGNORECASE,
)

DEFAULT_SEARCH_SENIORITY = "senior OR staff OR principal OR lead"
DEFAULT_SEARCH_KEYWORDS = (
    '"data scientist" OR "ML engineer" OR "machine learning engineer" '
    'OR "AI engineer" OR MLOps OR "deep learning"'
)


def build_search_query(domain: str, candidate: dict) -> str:
    seniority = candidate.get("search_seniority") or DEFAULT_SEARCH_SENIORITY
    keywords = candidate.get("search_keywords") or DEFAULT_SEARCH_KEYWORDS
    return f'site:{domain} ({seniority}) ({keywords})'

SCORE_PROMPT = """You are evaluating job postings for a candidate. Output ONLY a JSON array, no other text.

CANDIDATE:
{candidate_profile}

RESUME SUMMARY:
{resume_summary}

JOBS TO SCORE:
{jobs_text}

For each job output:
{{
  "job_number": 1,
  "score": 0-100,
  "title": "extracted job title",
  "stack": "key tech from JD (comma-separated, max 6 items)",
  "location_remote": "location + remote policy",
  "reason": "one sentence why this fits or doesn't fit the candidate",
  "worth_applying": true/false
}}

Scoring: 80-100 near-perfect; 60-79 good fit; 40-59 partial; <40 poor.
Set worth_applying=true only if score >= {min_score}.
Include ALL jobs. Output ONLY the JSON array."""

EXPORT_FIELDS = [
    "Company", "Role", "Location", "Application URL",
    "Score (%)", "Stack", "Region", "Reason", "Worth Applying", "Scan Date",
]


def _build_candidate_profile(config: dict) -> str:
    cand = config.get("candidate", {})
    name = cand.get("name", "the candidate")
    profile = cand.get("profile", "")
    seeking = cand.get("seeking", "")
    not_suitable = cand.get("not_suitable", "")

    lines = [f"- {name}"]
    if profile:
        lines.append(f"- {profile}")
    if seeking:
        lines.append(f"- Seeking: {seeking}")
    if not_suitable:
        lines.append(f"- NOT suitable: {not_suitable}")
    return "\n".join(lines)


def is_job_url(url: str) -> bool:
    return bool(JOB_URL_RE.search(url)) or bool(ATS_JOB_RE.search(url))


def is_ats_listing(url: str) -> bool:
    return bool(ATS_LISTING_RE.match(url))


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"seen_urls": []}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


_FETCH_URL_DELAY = 2.5


def _fetch_with_ratelimit(tf: TinyFish, urls: list[str], **kwargs):
    for attempt in range(2):
        try:
            resp = tf.fetch.get_contents(urls, **kwargs)
            time.sleep(len(urls) * _FETCH_URL_DELAY)
            return resp
        except RateLimitError:
            logger.warning("Fetch rate-limited — waiting 65s before retry...")
            time.sleep(65)
        except Exception as e:
            logger.error(f"Fetch error for {urls[:1]}: {e}")
            time.sleep(len(urls) * _FETCH_URL_DELAY)
            return None
    return None


def _fetch_links(tf: TinyFish, urls: list[str]) -> dict[str, list[str]]:
    result = {}
    for i in range(0, len(urls), 10):
        batch = urls[i: i + 10]
        resp = _fetch_with_ratelimit(tf, batch, format="markdown", links=True)
        if resp:
            for r in resp.results:
                result[r.url] = r.links
    return result


def discover_job_urls(
    tf: TinyFish, company: dict, seen_urls: set, candidate: dict | None = None
) -> list[dict]:
    found_urls: set[str] = set()

    logger.debug(f"  [{company['name']}] Fetching careers page: {company['careers_url']}")
    resp = _fetch_with_ratelimit(tf, [company["careers_url"]], format="markdown", links=True)
    if resp and resp.results:
        links = resp.results[0].links
        direct = [link for link in links if is_job_url(link) and link not in seen_urls]
        ats_pages = list({link for link in links if is_ats_listing(link)})
        found_urls.update(direct)
        logger.debug(f"  [{company['name']}] Careers page: {len(direct)} direct job links, {len(ats_pages)} ATS listing pages")

        if ats_pages:
            logger.debug(f"  [{company['name']}] Expanding {len(ats_pages)} ATS listing page(s)...")
            ats_link_map = _fetch_links(tf, ats_pages[:5])
            ats_jobs = 0
            for page_links in ats_link_map.values():
                for link in page_links:
                    if is_job_url(link) and link not in seen_urls:
                        found_urls.add(link)
                        ats_jobs += 1
            logger.debug(f"  [{company['name']}] ATS expansion: {ats_jobs} additional job links")

    query = build_search_query(company["search_domain"], candidate or {})
    logger.debug(f"  [{company['name']}] Search query: {query}")
    for attempt in range(2):
        try:
            resp = tf.search.query(query, language="en")
            search_new = 0
            for r in resp.results:
                if is_job_url(r.url) and r.url not in seen_urls:
                    found_urls.add(r.url)
                    search_new += 1
            logger.debug(f"  [{company['name']}] Search: {len(resp.results)} results, {search_new} new job URLs")
            time.sleep(13)
            break
        except RateLimitError:
            logger.warning(f"  [{company['name']}] Search rate-limited — waiting 60s...")
            time.sleep(62)
        except Exception as e:
            logger.error(f"  [{company['name']}] Search error: {e}")
            time.sleep(13)
            break

    new = [
        {
            "url": u,
            "title": u.split("/")[-1].replace("-", " ").title(),
            "snippet": "",
            "company": company["name"],
            "location": company["location"],
            "region": company["region"],
        }
        for u in found_urls
    ]
    return new


def fetch_job_details(tf: TinyFish, jobs: list[dict]) -> list[dict]:
    enriched = []
    for i in range(0, len(jobs), 10):
        batch = jobs[i: i + 10]
        urls = [j["url"] for j in batch]
        logger.debug(f"  Fetching details for {len(batch)} job(s): {[j['title'][:40] for j in batch]}")
        resp = _fetch_with_ratelimit(tf, urls, format="markdown")
        if not resp:
            enriched.extend(batch)
            continue
        fetched = {r.url: r for r in resp.results}
        for job in batch:
            r = fetched.get(job["url"])
            if r and r.text:
                job["content"] = r.text[:3000]
                job["title"] = r.title or job["title"]
                logger.debug(f"    Fetched '{job['title']}' — {len(r.text)} chars")
            else:
                logger.debug(f"    No content for: {job['url']}")
            enriched.append(job)
    return enriched


def score_jobs(jobs: list[dict], resume: str, config: dict) -> list[dict]:
    if not jobs:
        return []
    if config.get("scoring", {}).get("engine") == "explainable":
        return _score_jobs_explainable(jobs, config)

    jobs_text = "\n\n".join(
        f"JOB {i + 1}:\nCompany: {j['company']} | Location: {j['location']}\n"
        f"Title: {j['title']}\nURL: {j['url']}\n"
        f"Content:\n{j.get('content', j.get('snippet', ''))[:1500]}"
        for i, j in enumerate(jobs)
    )

    min_score = config.get("candidate", {}).get("min_score", 55)
    candidate_profile = _build_candidate_profile(config)

    prompt = SCORE_PROMPT.format(
        candidate_profile=candidate_profile,
        resume_summary=resume[:2500],
        jobs_text=jobs_text,
        min_score=min_score,
    )

    logger.debug(f"  Scoring {len(jobs)} job(s) via LLM (min_score={min_score})...")
    t0 = time.time()
    try:
        raw = chat_with_llm(
            config,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        elapsed = time.time() - t0
        start, end = raw.find("["), raw.rfind("]") + 1
        if start == -1:
            logger.error("  LLM returned no JSON array")
            return []
        scored = json.loads(raw[start:end])
        logger.debug(f"  LLM scoring complete in {elapsed:.1f}s — {len(scored)} results parsed")
    except Exception as e:
        logger.error(f"  Scoring error: {e}")
        return []

    results = []
    for item in scored:
        score = item.get("score", 0)
        title = item.get("title", "?")
        reason = item.get("reason", "")
        worth = item.get("worth_applying", False)
        logger.debug(f"    [{score:3d}] {title} — {reason[:80]}")
        if not worth:
            continue
        idx = item.get("job_number", 0) - 1
        if 0 <= idx < len(jobs):
            job = jobs[idx].copy()
            job.update(
                {
                    "score": score,
                    "extracted_title": title,
                    "stack": item.get("stack", ""),
                    "location_remote": item.get("location_remote", job["location"]),
                    "reason": reason,
                }
            )
            results.append(job)

    passing = len(results)
    logger.debug(f"  {passing}/{len(scored)} jobs passed min_score threshold")
    return sorted(results, key=lambda x: x["score"], reverse=True)


def _score_jobs_explainable(jobs: list[dict], config: dict) -> list[dict]:
    from uuid import UUID, uuid4

    from pydantic import HttpUrl

    from job_hunt.analysis.service import JobAnalyzer
    from job_hunt.domain.models import CandidateProfile, SearchPreferences, UnifiedJob
    from job_hunt.llm.config import LLMSettings
    from job_hunt.normalization import detect_work_mode

    profile = CandidateProfile.model_validate(config["candidate_profile"])
    preferences = SearchPreferences.model_validate(config["search_preferences"])
    settings = LLMSettings.from_application_config(config)
    analyzer = JobAnalyzer(profile, preferences, settings)
    minimum_score = preferences.filters.minimum_score
    results: list[dict] = []
    for raw_job in jobs:
        try:
            job = UnifiedJob(
                id=UUID(raw_job["job_id"]) if raw_job.get("job_id") else uuid4(),
                source_name="tinyfish",
                original_url=HttpUrl(raw_job["url"]),
                company=raw_job["company"],
                title=raw_job["title"],
                description=raw_job.get("content", raw_job.get("snippet", "")),
                location=raw_job.get("location"),
                work_mode=detect_work_mode(
                    raw_job.get("title"),
                    raw_job.get("location"),
                    raw_job.get("content", "")[:5000],
                ),
                apply_url=HttpUrl(raw_job["url"]),
            )
            analysis = analyzer.analyze(job)
        except Exception as exc:
            logger.warning("Explainable analysis skipped invalid job (%s)", type(exc).__name__)
            continue
        if analysis.total_score < minimum_score:
            continue
        item = raw_job.copy()
        item.update(
            {
                "score": round(analysis.total_score),
                "extracted_title": job.title,
                "stack": ", ".join(analysis.strengths[:3]),
                "location_remote": job.location or job.work_mode.value,
                "reason": analysis.explanation,
                "worth_applying": True,
                "analysis": analysis.model_dump(mode="json"),
                "job_id": str(job.id),
            }
        )
        results.append(item)
    return sorted(results, key=lambda item: item["score"], reverse=True)


def format_telegram_message(top_jobs: list[dict], date_str: str) -> str:
    lines = [f"<b>Job Hunt — {date_str}</b>", f"<i>{len(top_jobs)} matches found</i>\n"]
    for i, job in enumerate(top_jobs, 1):
        lines.append(
            f"<b>#{i}</b> | {job['company']} | {job.get('extracted_title', job['title'])}\n"
            f"📍 {job.get('location_remote', job['location'])}\n"
            f"🔧 {job.get('stack', 'N/A')}\n"
            f"✅ {job.get('reason', '')}\n"
            f"<a href=\"{job['url']}\">Apply</a>\n"
        )
    lines.append('Reply "apply to #N" to draft application.')
    return "\n".join(lines)


def _send_rich_telegram_alerts(top_jobs: list[dict], telegram_config: dict) -> bool:
    import os
    from uuid import UUID, uuid4

    from pydantic import HttpUrl

    from job_hunt.domain.models import JobAnalysisResult, SalaryEstimateResult, UnifiedJob
    from job_hunt.normalization import detect_work_mode
    from job_hunt.telegram import CallbackSigner, TelegramClient

    client = TelegramClient(telegram_config["token"], telegram_config["chat_id"])
    callback_secret = os.getenv("TELEGRAM_CALLBACK_SECRET")
    signer = CallbackSigner(callback_secret) if callback_secret else None
    sent = 0
    for raw in top_jobs:
        if not raw.get("analysis"):
            logger.warning("Rich Telegram alert skipped because structured analysis is missing")
            continue
        try:
            job = UnifiedJob(
                id=UUID(raw["job_id"]) if raw.get("job_id") else uuid4(),
                source_name=str(raw.get("source_name", "tinyfish")),
                original_url=HttpUrl(raw["url"]),
                company=raw["company"],
                title=raw.get("extracted_title") or raw["title"],
                description=raw.get("content", ""),
                location=raw.get("location"),
                work_mode=detect_work_mode(raw.get("location"), raw.get("content", "")),
                apply_url=HttpUrl(raw["url"]),
            )
            analysis = JobAnalysisResult.model_validate(raw["analysis"])
            salary = (
                SalaryEstimateResult.model_validate(raw["salary_estimate"])
                if raw.get("salary_estimate")
                else None
            )
            client.send_job_alert(job, analysis, salary=salary, signer=signer)
            sent += 1
        except Exception as exc:
            logger.warning("Rich Telegram alert failed (%s)", type(exc).__name__)
    return sent == len(top_jobs)


def _export_to_csv(jobs: list[dict], label: str) -> Path:
    date_str = datetime.now().strftime("%Y-%m-%d")
    out_path = Path("output") / f"jobs_{date_str}.csv"
    out_path.parent.mkdir(exist_ok=True)

    def _row(j: dict) -> dict:
        worth = j.get("worth_applying")
        return {
            "Company": j.get("company", ""),
            "Role": j.get("extracted_title") or j.get("title", ""),
            "Location": j.get("location_remote") or j.get("location", ""),
            "Application URL": j.get("url", ""),
            "Score (%)": j.get("score", ""),
            "Stack": j.get("stack", ""),
            "Region": j.get("region", ""),
            "Reason": j.get("reason", ""),
            "Worth Applying": "Yes" if worth else ("No" if worth is False else ""),
            "Scan Date": j.get("scan_date", ""),
        }

    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=EXPORT_FIELDS)
        writer.writeheader()
        for j in jobs:
            writer.writerow(_row(j))

    logger.info(f"Results exported to CSV ({label}): {out_path}")
    return out_path


def _initialize_database(config: dict):
    if not config.get("persistence", {}).get("enabled", False):
        return None
    try:
        from job_hunt.persistence.database import Database, get_database_url
        from job_hunt.persistence.migration import upgrade_database

        database_url = get_database_url()
        upgrade_database(database_url)
        logger.info("Relational job history enabled")
        return Database(database_url)
    except Exception as database_error:
        logger.error(f"Database initialization failed: {database_error}")
        return None


def _persist_collected_jobs(jobs: list[dict], database):
    from job_hunt.domain.models import UnifiedJob
    from job_hunt.normalization import detect_work_mode
    from job_hunt.persistence.job_ingestion import JobIngestionService

    unified_jobs: list[tuple[dict, UnifiedJob]] = []
    for job in jobs:
        try:
            unified_jobs.append(
                (job, UnifiedJob(
                    source_name="tinyfish",
                    original_url=job["url"],
                    company=job["company"],
                    title=job["title"],
                    description=job.get("content", ""),
                    location=job.get("location"),
                    work_mode=detect_work_mode(
                        job.get("title"),
                        job.get("location"),
                        job.get("content", "")[:5000],
                    ),
                    apply_url=job["url"],
                ))
            )
        except Exception as validation_error:
            logger.warning(f"  Invalid collected job ignored by database: {validation_error}")
    decisions: Counter[str] = Counter()
    if unified_jobs:
        with database.session() as session:
            ingestion = JobIngestionService(session)
            for raw_job, unified_job in unified_jobs:
                outcome = ingestion.ingest(unified_job)
                raw_job["job_id"] = outcome.job_id
                decisions[outcome.decision.value] += 1
        logger.info(f"  Database history updated: {dict(decisions)}")
    return decisions


def run_scan(config: dict, companies: list[dict]) -> None:
    scan_start = time.time()
    total = len(companies)
    logger.info(f"=== Scan started — {total} companies to check ===")
    logger.info(f"Candidate: {config.get('candidate', {}).get('name', 'unknown')}")
    logger.info(f"Min score: {config.get('candidate', {}).get('min_score', 55)} | Top N: {config.get('candidate', {}).get('top_n', 5)}")
    provider = config.get("llm_provider") or "openrouter"
    model_by_provider = {
        "openrouter": config.get("openrouter_model", "default"),
        "anthropic": config.get("anthropic_model", "default"),
        "claude_cli": config.get("claude_cli_model") or "claude default",
    }
    logger.info(f"LLM provider: {provider} | Model: {model_by_provider.get(provider, 'default')}")

    try:
        tf = TinyFish(api_key=config["tinyfish_api_key"])
        logger.debug("TinyFish client initialised")
    except Exception as e:
        logger.error(f"TinyFish init error: {e}")
        return

    resume_path = Path(config.get("candidate", {}).get("resume_path", "resume/YOUR_RESUME.md"))
    resume = resume_path.read_text()
    database = _initialize_database(config)
    logger.debug(f"Resume loaded: {resume_path} ({len(resume)} chars)")

    min_score = config.get("candidate", {}).get("min_score", 55)
    top_n = config.get("candidate", {}).get("top_n", 5)

    state = load_state()
    seen_urls: set = set(state.get("seen_urls", []))
    logger.info(f"State loaded — {len(seen_urls)} previously seen URLs")

    all_scored_jobs: list[dict] = []
    ingestion_decisions: Counter[str] = Counter()
    jobs_collected_total = 0
    errors: list[str] = []
    companies_scanned = 0
    companies_with_jobs = 0

    for idx, company in enumerate(companies, 1):
        logger.info(f"[{idx}/{total}] Scanning {company['name']}...")
        try:
            new_jobs = discover_job_urls(tf, company, seen_urls, config.get("candidate", {}))
            if not new_jobs:
                logger.info("  No new job URLs found")
                companies_scanned += 1
                continue

            logger.info(f"  {len(new_jobs)} new job URL(s) — fetching details...")
            new_jobs = fetch_job_details(tf, new_jobs)
            jobs_collected_total += len(new_jobs)
            if database is not None:
                try:
                    ingestion_decisions.update(_persist_collected_jobs(new_jobs, database))
                except Exception as persistence_error:
                    logger.error(f"  Database persistence failed: {persistence_error}")
            seen_urls.update(j["url"] for j in new_jobs)

            logger.info(f"  Scoring {len(new_jobs)} job(s)...")
            scored: list[dict] = []
            try:
                for i in range(0, len(new_jobs), 10):
                    batch = new_jobs[i: i + 10]
                    logger.debug(f"  Scoring batch {i // 10 + 1} ({len(batch)} jobs)...")
                    batch_scored = score_jobs(batch, resume, config)
                    scored.extend(batch_scored)
            except Exception as score_err:
                logger.error(f"  Scoring failed: {score_err}")
                errors.append(f"⚠️ Scoring failed for {company['name']}: {score_err}")
                logger.warning(f"  Saving {len(new_jobs)} unscored job(s) as fallback")
                scored = new_jobs

            if scored:
                all_scored_jobs.extend(scored)
                companies_with_jobs += 1
                titles = [j.get("extracted_title") or j.get("title", "?") for j in scored[:3]]
                logger.info(f"  {len(scored)} job(s) saved: {', '.join(titles)}{' ...' if len(scored) > 3 else ''}")

            companies_scanned += 1

        except Exception as company_err:
            msg = f"❌ {company['name']}: {company_err}"
            errors.append(msg)
            logger.error(f"  Company scan failed: {company_err}")
            continue

    state["seen_urls"] = list(seen_urls)
    state["last_scan"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    logger.debug("State saved")

    top_jobs = sorted(
        [j for j in all_scored_jobs if j.get("score", 0) >= min_score],
        key=lambda x: x.get("score", 0), reverse=True
    )[:top_n]

    scan_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for job in all_scored_jobs:
        job["scan_date"] = scan_date

    LAST_SCAN_FILE.parent.mkdir(exist_ok=True)
    LAST_SCAN_FILE.write_text(json.dumps(all_scored_jobs, indent=2))
    logger.debug(f"Last scan saved: {len(all_scored_jobs)} total jobs → {LAST_SCAN_FILE}")

    history: list[dict] = []
    if JOB_HISTORY_FILE.exists():
        try:
            history = json.loads(JOB_HISTORY_FILE.read_text())
        except Exception:
            history = []
    existing_urls = {j["url"] for j in history}
    new_entries = [j for j in all_scored_jobs if j["url"] not in existing_urls]
    history.extend(new_entries)
    JOB_HISTORY_FILE.write_text(json.dumps(history, indent=2))
    if database is not None:
        database.dispose()
    logger.debug(f"Job history updated: +{len(new_entries)} new entries ({len(history)} total)")

    elapsed = time.time() - scan_start
    from job_hunt.reports import SearchRunReport

    run_report = SearchRunReport(
        sources_consulted=[company["name"] for company in companies[:companies_scanned]],
        source_errors={"scanner": " | ".join(errors)} if errors else {},
        jobs_collected=jobs_collected_total,
        jobs_new=ingestion_decisions["new"],
        jobs_updated=ingestion_decisions["updated"] + ingestion_decisions["republished"],
        duplicates_removed=ingestion_decisions["duplicate"] + ingestion_decisions["unchanged"],
        jobs_analyzed=len(all_scored_jobs),
        jobs_above_threshold=len(top_jobs),
        duration_seconds=elapsed,
        errors=errors,
    )
    LAST_RUN_REPORT_FILE.write_text(run_report.model_dump_json(indent=2), encoding="utf-8")
    logger.info("\n" + run_report.as_text())
    logger.info(
        f"=== Scan complete — {companies_scanned}/{total} companies, "
        f"{len(all_scored_jobs)} jobs found, {len(top_jobs)} top matches "
        f"({elapsed / 60:.1f} min) ==="
    )

    if top_jobs:
        logger.info("Top matches:")
        for j in top_jobs:
            logger.info(f"  [{j.get('score', '?'):3}] {j.get('extracted_title') or j.get('title')} @ {j['company']} — {j.get('reason', '')[:80]}")

    date_str = datetime.now().strftime("%d %b %Y")
    tg = config.get("telegram", {})
    telegram_configured = bool(
        tg.get("enabled", True) and tg.get("token") and tg.get("chat_id")
    )

    # Always persist results to CSV when there are scored jobs — this is the
    # durable record regardless of whether Telegram is configured.
    csv_path = _export_to_csv(all_scored_jobs, "scan results") if all_scored_jobs else None

    if errors and telegram_configured:
        error_msg = f"<b>Job Hunt Errors — {date_str}</b>\n" + "\n".join(errors)
        send_telegram(tg["token"], tg["chat_id"], error_msg)

    if not top_jobs:
        logger.info("No matching jobs found today.")
        if telegram_configured:
            msg = f"<b>Job Hunt — {date_str}</b>\nNo new matches today."
            send_telegram(tg["token"], tg["chat_id"], msg)
        return

    msg = format_telegram_message(top_jobs, date_str)
    logger.info("\n" + msg)

    # Telegram is an optional notification on top of the CSV. When it's not
    # configured we simply skip it — no error, the CSV already holds the results.
    if telegram_configured:
        sent = (
            _send_rich_telegram_alerts(top_jobs, tg)
            if tg.get("rich_alerts")
            else send_telegram(tg["token"], tg["chat_id"], msg)
        )
        if sent:
            logger.info(f"Telegram notification sent. Results also saved to CSV: {csv_path}")
        else:
            logger.warning(f"Telegram send failed — results saved to CSV: {csv_path}")
    else:
        logger.info(f"Telegram not configured — results saved to CSV: {csv_path}")
        logger.info("Add telegram.token and telegram.chat_id to config.json to enable notifications.")
