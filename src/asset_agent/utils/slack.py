"""Slack Webhook notification helper."""

from __future__ import annotations

import requests

from asset_agent.utils.logging import get_logger

logger = get_logger("utils.slack")


def send_slack_notification(
    webhook_url: str,
    *,
    model_name: str,
    success: bool,
    elapsed_seconds: float,
    glb_path: str | None = None,
    errors: list[str] | None = None,
) -> bool:
    """Post a task-completion message to a Slack channel via Incoming Webhook.

    Returns True if the message was sent successfully.
    """
    minutes, secs = divmod(int(elapsed_seconds), 60)
    time_str = f"{minutes}m {secs}s" if minutes else f"{secs}s"

    if success:
        emoji = ":white_check_mark:"
        status = "Success"
        color = "#36a64f"
        detail = f"GLB: `{glb_path}`" if glb_path else ""
    else:
        emoji = ":x:"
        status = "Failed"
        color = "#e01e5a"
        detail = "\n".join(errors[:3]) if errors else "Unknown error"

    payload = {
        "attachments": [
            {
                "color": color,
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"{emoji} *3D Asset Agent* | *{status}*\n"
                                f"*Model:* `{model_name}`\n"
                                f"*Time:* {time_str}\n"
                                f"{detail}"
                            ),
                        },
                    }
                ],
            }
        ]
    }

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.ok:
            logger.info("Slack notification sent for '%s'.", model_name)
            return True
        logger.warning("Slack returned %d: %s", resp.status_code, resp.text)
        return False
    except Exception as exc:
        logger.warning("Slack notification failed: %s", exc)
        return False
