from pathlib import Path

import yaml

from home_controller.main import create_app

destination = Path(__file__).resolve().parents[1] / "openapi" / "openapi.yaml"
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(yaml.safe_dump(create_app().openapi(), sort_keys=False), encoding="utf-8")
print(destination)
