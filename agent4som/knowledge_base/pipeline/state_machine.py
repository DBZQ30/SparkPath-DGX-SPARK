from enum import Enum
from knowledge_base.models.schemas import FactStatus

class ReviewAction(Enum):
    APPROVE = "approve"
    REJECT = "reject"
    EXPIRE = "expire"

class AdminReviewStateMachine:
    def __init__(self, current_status: FactStatus):
        self.status = current_status

        # Define allowed transitions
        self.transitions = {
            FactStatus.PENDING_REVIEW: {
                ReviewAction.APPROVE: FactStatus.APPROVED,
                ReviewAction.REJECT: FactStatus.REJECTED,
                ReviewAction.EXPIRE: FactStatus.EXPIRED,
            },
            FactStatus.EXPIRED: {
                # Could re-queue
                ReviewAction.APPROVE: FactStatus.APPROVED,
                ReviewAction.REJECT: FactStatus.REJECTED,
            }
        }

    def transition(self, action: ReviewAction) -> FactStatus:
        allowed_actions = self.transitions.get(self.status, {})
        if action not in allowed_actions:
            raise ValueError(f"Invalid transition: Cannot {action.name} from state {self.status.name}")

        self.status = allowed_actions[action]
        return self.status
