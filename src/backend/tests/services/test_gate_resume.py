"""门控恢复计算 ``_compute_gate_resume`` 的单测。

覆盖打回默认回退目标（对齐 ARC ``GATE_ROLLBACK``）、显式 ``rollback_to_stage``
的越界校验，以及 reject 理由被当作 guidance 的链路。
"""

import pytest
from fastapi import HTTPException

from app.services.research_service.turns import _compute_gate_resume


@pytest.mark.parametrize(
    "gate_stage, expected",
    [
        (5, "4"),  # LITERATURE_SCREEN → LITERATURE_COLLECT
        (9, "8"),  # EXPERIMENT_DESIGN → HYPOTHESIS_GEN
        (20, "16"),  # QUALITY_GATE → PAPER_OUTLINE
        (10, "9"),  # CODE_GENERATION → EXPERIMENT_DESIGN (hep_ph)
    ],
)
def test_reject_defaults_to_arc_rollback(gate_stage, expected):
    from_stage, guidance, abort = _compute_gate_resume(gate_stage, {"action": "reject"})
    assert from_stage == expected
    assert abort is False
    assert guidance is None


def test_reject_unmapped_stage_falls_back_to_self():
    # 非标准门控 stage：无 GATE_ROLLBACK 映射 → 回落重跑本 stage。
    from_stage, _guidance, abort = _compute_gate_resume(7, {"action": "reject"})
    assert from_stage == "7"
    assert abort is False


def test_explicit_rollback_to_stage_wins():
    from_stage, _guidance, _abort = _compute_gate_resume(
        9, {"action": "reject", "rollback_to_stage": 5}
    )
    assert from_stage == "5"


@pytest.mark.parametrize("bad", [9, 99, 0, -1])
def test_rollback_to_stage_out_of_range_rejected(bad):
    # 须落在门控 stage 上游（1 ≤ target < gate_stage）。
    with pytest.raises(HTTPException) as exc:
        _compute_gate_resume(9, {"action": "reject", "rollback_to_stage": bad})
    assert exc.value.status_code == 400


def test_rollback_to_stage_invalid_type_rejected():
    with pytest.raises(HTTPException) as exc:
        _compute_gate_resume(9, {"action": "reject", "rollback_to_stage": "abc"})
    assert exc.value.status_code == 400


def test_reject_message_becomes_guidance():
    _from_stage, guidance, _abort = _compute_gate_resume(
        9, {"action": "reject", "message": "假设太宽泛"}
    )
    assert guidance == "假设太宽泛"


def test_approve_advances_to_next_stage():
    from_stage, _guidance, abort = _compute_gate_resume(9, {"action": "approve"})
    assert from_stage == "10"
    assert abort is False


def test_abort_returns_abort_flag():
    from_stage, _guidance, abort = _compute_gate_resume(9, {"action": "abort"})
    assert abort is True
    assert from_stage is None


def test_edit_reruns_same_stage_with_guidance():
    from_stage, guidance, _abort = _compute_gate_resume(
        9, {"action": "edit", "guidance": "补充实验组"}
    )
    assert from_stage == "9"
    assert guidance == "补充实验组"
