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

## Roadmap

- Lever parser
- Ashby parser
- Unknown/manual fallback
- Contact discovery and application tracking later