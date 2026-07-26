"""Phase 6: the merge-gating evals must pass. These assert the same guarantees
`python -m evals.run` gates CI on, so a local `pytest` catches a regression too.
The evals reset the database themselves, so this file does not use clean_db.
"""

import pytest

from evals import run as evals

pytestmark = pytest.mark.eval


def test_permission_leak_eval_passes():
    result = evals.permission_leak_eval()
    assert result["passed"], result["detail"]


def test_grounded_answer_eval_passes():
    result = evals.grounded_answer_eval()
    assert result["passed"], result["detail"]
