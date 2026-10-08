# JobAutomation

JobAutomation is a deterministic job research and discovery pipeline.

## Current architecture

- Load companies from CSV
- Research company websites
- Detect ATS type
- Parse careers pages into `Job` objects

## Current capabilities

- Company loading from CSV
- Company website research
- ATS detection for Greenhouse, Lever, Ashby, and unknown
- Greenhouse job parsing from careers HTML
- Job discovery orchestration through the Greenhouse parser

## Testing

Run:

```bash
python -m pytest
```

## Playwright browser driver

Install the Playwright Python dependency with `python -m pip install -r requirements.txt`.
Install its Chromium browser binary separately with `python -m playwright install chromium`.
The application does not download browser binaries automatically.

## Roadmap

- Lever parser
- Ashby parser
- Unknown/manual fallback
- Contact discovery and application tracking later
