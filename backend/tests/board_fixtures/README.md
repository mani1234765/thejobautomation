# Job-board contract fixtures

Shapes follow the public, unauthenticated APIs JobPilot reads:

| File | API |
|---|---|
| `greenhouse.json` | Greenhouse Job Board API, `GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true` |
| `lever.json` | Lever Postings API, `GET https://api.lever.co/v0/postings/{company}?mode=json` |
| `ashby.json` | Ashby Posting API, `GET https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true` |

Sanitized: companies, people, ids, URLs and descriptions are synthetic (`example-co`). Only field names and
structure mirror the real responses. Each file includes deliberate edge cases (missing id, duplicate id,
unsafe URL, null content) so the adapters' validation is exercised. No real listing text is reproduced.
