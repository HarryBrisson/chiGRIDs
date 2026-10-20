import json
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load_env_json(var):
    path = os.environ.get(var)
    if not path:
        pytest.skip(f"set {var} to run this data test")
    with open(path) as fh:
        data = json.load(fh)
    if isinstance(data, dict) and "data" in data and "features" not in data:
        data = data["data"]  # some portal exports wrap the FeatureCollection in {"data": ...}
    return data


@pytest.fixture(scope="session")
def city_boundary():
    return _load_env_json("CHIGRID_BOUNDARY")


@pytest.fixture(scope="session")
def street_centerlines():
    return _load_env_json("CHIGRID_CENTERLINES")
