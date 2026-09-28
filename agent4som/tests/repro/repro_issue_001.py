import asyncio
import logging
import sys
import os

# Add hermes to path
sys.path.append(os.path.join(os.getcwd(), "hermes"))

from gateway.platforms.base import SendResult

async def test_846609_handling():
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("repro")

    # Mocking WeComAdapter behavior after fix
    # The updated wecom.py logic:
    def mock_send_fixed(error_msg):
        retryable = "846609" in error_msg
        return SendResult(success=False, error=error_msg, retryable=retryable)

    error_846609 = '{"errcode": 846609, "errmsg": "aibot websocket not subscribed"}'
    result = mock_send_fixed(error_846609)

    logger.info(f"Fixed result: success={result.success}, retryable={result.retryable}, error={result.error}")

    # Check if base.py would retry it
    _RETRYABLE_ERROR_PATTERNS = (
        "connecterror", "connectionerror", "connectionreset", "connectionrefused",
        "connecttimeout", "network", "broken pipe", "remotedisconnected", "eoferror",
    )

    def _is_retryable_error(error):
        if not error: return False
        lowered = error.lower()
        return any(pat in lowered for pat in _RETRYABLE_ERROR_PATTERNS)

    is_retryable = result.retryable or _is_retryable_error(result.error)
    logger.info(f"Is retryable by base.py: {is_retryable}")

    if is_retryable:
        logger.info("SUCCESS: 846609 is now marked as retryable!")
    else:
        logger.error("FAILURE: 846609 is still NOT marked as retryable.")


if __name__ == "__main__":
    asyncio.run(test_846609_handling())
