from pathlib import Path

from src.main import main


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
        ]
    )

    assert exit_code == 0
    assert calls == [
        (
            Path("companies.csv"),
            {
                "applications_path": Path("custom/applications.json"),
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
