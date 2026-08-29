import csv
from pathlib import Path

from src.main import format_http_performance, main
from src.research.scraper import HttpPerformanceMetrics
from src.research.company_loader import load_companies
from src.research.models import Company, Contact, Job, ResearchResult


def test_main_runs_pipeline_with_default_output_paths(monkeypatch, capsys):
    calls = []

    def fake_run_pipeline(companies_path, **kwargs):
        calls.append((companies_path, kwargs))
        return {
            "companies_processed": 2,
            "jobs_discovered": 3,
            "applications_created": 1,
            "drafts_created": 4,
        }

    monkeypatch.setattr("src.main.run_pipeline", fake_run_pipeline)

    exit_code = main(["--companies", "data/input/companies.csv"])

    assert exit_code == 0
    assert calls == [
        (
            Path("data/input/companies.csv"),
            {
                "applications_path": Path("data/output/applications.json"),
                "contacts_path": Path("data/output/contacts.json"),
                "drafts_path": Path("data/output/email_drafts.json"),
            },
        )
    ]
    assert capsys.readouterr().out == (
        "Pipeline complete: companies=2, jobs=3, applications=1, drafts=4\n"
    )


def test_main_allows_output_path_overrides(monkeypatch):
    calls = []

    def fake_run_pipeline(companies_path, **kwargs):
        calls.append((companies_path, kwargs))
        return {
            "companies_processed": 0,
            "jobs_discovered": 0,
            "applications_created": 0,
            "drafts_created": 0,
        }

    monkeypatch.setattr("src.main.run_pipeline", fake_run_pipeline)

    exit_code = main(
        [
            "--companies",
            "companies.csv",
            "--applications-path",
            "custom/applications.json",
            "--drafts-path",
            "custom/drafts.json",
            "--contacts-path",
            "custom/contacts.json",
        ]
    )

    assert exit_code == 0
    assert calls == [
        (
            Path("companies.csv"),
            {
                "applications_path": Path("custom/applications.json"),
                "contacts_path": Path("custom/contacts.json"),
                "drafts_path": Path("custom/drafts.json"),
            },
        )
    ]


def test_main_returns_nonzero_when_pipeline_fails(monkeypatch, capsys):
    def fake_run_pipeline(*args, **kwargs):
        raise RuntimeError("companies file is invalid")

    monkeypatch.setattr("src.main.run_pipeline", fake_run_pipeline)

    exit_code = main(["--companies", "companies.csv"])

    assert exit_code == 1
    assert capsys.readouterr().err == "Pipeline failed: companies file is invalid\n"


def test_main_optionally_prints_http_performance(monkeypatch, capsys):
    def fake_run_pipeline(companies_path, **kwargs):
        assert companies_path == Path("companies.csv")
        metrics = kwargs["http_metrics"]
        assert isinstance(metrics, HttpPerformanceMetrics)
        metrics.record_network_request(
            "https://example.com/careers",
            1.25,
            success=True,
            status_code=200,
        )
        metrics.record_cache_hit()
        return {
            "companies_processed": 1,
            "jobs_discovered": 2,
            "applications_created": 0,
            "drafts_created": 0,
        }

    monkeypatch.setattr("src.main.run_pipeline", fake_run_pipeline)

    exit_code = main(["--companies", "companies.csv", "--http-performance"])

    assert exit_code == 0
    assert capsys.readouterr().out == (
        "Pipeline complete: companies=1, jobs=2, applications=0, drafts=0\n"
        "HTTP performance: requests=1, cache_hits=1, failures=0, "
        "network_time=1.25s, slowest=https://example.com/careers (1.25s)\n"
    )


def test_http_performance_redacts_sensitive_query_values():
    metrics = HttpPerformanceMetrics()
    metrics.record_network_request(
        "https://example.com/jobs?team=android&token=secret",
        0.5,
        success=True,
        status_code=200,
    )

    assert "secret" not in format_http_performance(metrics)
    assert "token=REDACTED" in format_http_performance(metrics)


def test_project_companies_csv_has_five_fields_and_loads():
    companies_path = Path("data/input/companies.csv")

    with companies_path.open("r", newline="", encoding="utf-8") as file:
        rows = list(csv.reader(file))

    assert rows[0] == [
        "name",
        "website",
        "careers_url",
        "industry",
        "location",
    ]
    assert all(len(row) == 5 for row in rows[1:])

    companies = load_companies(str(companies_path))

    assert len(companies) == len(rows) - 1
    assert all(isinstance(company, Company) for company in companies)


def test_main_runs_pipeline_with_project_companies_input(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "src.orchestrator.research_company",
        lambda company, **_: ResearchResult(
            company_name=company.name,
            url=company.website or "",
            text="Example company",
            contacts=[
                Contact(
                    email="hiring@example.com",
                    company=company.name,
                    source="test",
                )
            ],
        ),
    )
    monkeypatch.setattr(
        "src.orchestrator.discover_jobs",
        lambda company, **_: [
            Job(
                title="Example Engineer",
                company=company.name,
                url="https://example.com/jobs/engineer",
            )
        ],
    )

    applications_path = tmp_path / "applications.json"
    contacts_path = tmp_path / "contacts.json"
    drafts_path = tmp_path / "email_drafts.json"

    exit_code = main(
        [
            "--companies",
            "data/input/companies.csv",
            "--applications-path",
            str(applications_path),
            "--drafts-path",
            str(drafts_path),
            "--contacts-path",
            str(contacts_path),
        ]
    )

    assert exit_code == 0
    assert applications_path.exists()
    assert contacts_path.exists()
    assert drafts_path.exists()
    assert capsys.readouterr().out == (
        "Pipeline complete: companies=10, jobs=10, applications=1, drafts=1\n"
    )
