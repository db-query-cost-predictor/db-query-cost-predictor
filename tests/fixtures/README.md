# Test fixtures — SYNTHETIC ONLY

Unit tests build tiny synthetic inputs in memory or in pytest's temporary
directories (for example a two-node JSON plan or a three-run raw JSONL file).

* Synthetic fixtures are never written to `QCP_DATA_ROOT`, `reports/`,
  `reference_docs/` or `reference_pilot/`.
* They are never mixed with, or presented as, collected evidence.
* Numbers in fixtures are arbitrary test values, not measurements.

The tests were written but not run by Claude Code. Run them yourself with:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

(`pyproject.toml` deselects tests marked `integration`; there are none in this milestone.)
