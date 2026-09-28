import pytest
from knowledge_base.pipeline.state_machine import AdminReviewStateMachine, ReviewAction
from knowledge_base.models.schemas import FactStatus

def test_admin_card_state_machine_approve():
    sm = AdminReviewStateMachine(current_status=FactStatus.PENDING_REVIEW)
    new_status = sm.transition(ReviewAction.APPROVE)
    assert new_status == FactStatus.APPROVED

def test_admin_card_state_machine_reject():
    sm = AdminReviewStateMachine(current_status=FactStatus.PENDING_REVIEW)
    new_status = sm.transition(ReviewAction.REJECT)
    assert new_status == FactStatus.REJECTED

def test_admin_card_state_machine_expired():
    sm = AdminReviewStateMachine(current_status=FactStatus.PENDING_REVIEW)
    new_status = sm.transition(ReviewAction.EXPIRE)
    assert new_status == FactStatus.EXPIRED

def test_invalid_transition_raises_error():
    # Cannot approve an already approved fact
    sm = AdminReviewStateMachine(current_status=FactStatus.APPROVED)
    with pytest.raises(ValueError) as exc:
        sm.transition(ReviewAction.APPROVE)
    assert "Invalid transition" in str(exc.value)
