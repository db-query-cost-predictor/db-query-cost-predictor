# Separate evidence and offline replay

This repository contains the runtime and a reviewed publication summary in docs/rq2. The separate RQ2_Final_Evidence_20261106.zip contains the closed final study only: raw generation responses, checks, timings, original frozen identity/queue/source archive and the detailed reviewed report. Pilot and development result directories are excluded.

The original source-used.zip is retained inside the evidence for provenance; do not install it as the new runtime. This release is a post-study engineering refactor. Its source hash differs from the code that collected the historical timings. Replaying decisions against those observations validates the decision/refactoring relationship, not fresh generation or fresh runtime measurements.

After installing the repository package, use a new output directory:

```powershell
python -m rq2_pipeline replay --evidence ../RQ2_Final_Evidence_20261106.zip --output runs/replayed-final
```

The evidence argument can also point to the extracted study folder. Replay reads hashes, the original source archive, stored gate outcomes and raw paired timings. It checks every final label, supplementary profile and the A/A slot counts, and writes a compact HTML/CSV/JSON report with an audit.json record. No API key, Docker, database connection or historical Python import is used.

For the completed study the expected counts are: 15 generated originals, 60 planned cases; 39 primary meets, 8 consistently faster, 1 consistently slower, 1 no clear difference, 8 fixture-rejected and 3 inconclusive. Eight originals meet the rule in all four environments. The supplementary case counts are 47/42/39/36/34; all-four counts 10/8/8/6/5. B was strictly faster in 288 of 539 A/A pairs.

Decisions and reports are replayable from saved evidence. Exact fresh LLM outputs and execution timings are not guaranteed. Stored correctness gates are reconstructed, not rerun, during replay. Detailed reviewed HTML is available inside the evidence archive after extraction.

No generated TPC-H database/table files, credentials, private test logs or teammate environment files are distributed. Generator setup remains with the team's existing data-engineering workflow. See THIRD_PARTY_NOTICES.md for the official TPC terms and derived-work notice.
