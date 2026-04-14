import yaml
from pathlib import Path
from typing import Any, Dict

class _Config:
    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._root = Path(__file__).parent.parent
        self._cfg_dir = self._root / "config"

    def _load_yaml(self, rel_path: str) -> Dict[str, Any]:
        key = f"yaml::{rel_path}"
        if key in self._cache:
            return self._cache[key]
        p = self._cfg_dir / rel_path
        if not p.exists():
            self._cache[key] = {}
            return {}
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                self._cache[key] = data
                return data
        except Exception:
            self._cache[key] = {}
            return {}

    def comps_rules(self) -> Dict[str, Any]:
        cfg = self._load_yaml("valuation_comps_rules.yaml")
        if cfg:
            return cfg
        return self._load_yaml("comps_rules.yaml")

    def sector_weights(self) -> Dict[str, Any]:
        return self._load_yaml("sector_weights.yaml")

    def valuation_policy(self) -> Dict[str, Any]:
        return self._load_yaml("valuation_config.yaml")
    
    def get_sector_anchor_params(self) -> Dict[str, Any]:
        return self._load_yaml("sector_params.yaml").get("sector_anchor_params", {})

CONFIG = _Config()
