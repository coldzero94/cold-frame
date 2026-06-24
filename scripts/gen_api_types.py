"""Emit the UI API JSON Schema from the wire contract (cold_frame/ui/contract.py).

Step 1 of the codegen pipeline (step 2 = json-schema-to-typescript → api.generated.ts); both run
from `pnpm -C frontend run gen:types`. Writes the committed, language-neutral
``frontend/src/api.schema.json`` — the single source the TS client is generated from.
"""

from __future__ import annotations

import json
from pathlib import Path

from cold_frame.ui.contract import build_api_schema

_OUT = Path(__file__).resolve().parents[1] / "frontend" / "src" / "api.schema.json"


def main() -> None:
    _OUT.write_text(json.dumps(build_api_schema(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {_OUT}")


if __name__ == "__main__":
    main()
