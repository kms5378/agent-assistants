from __future__ import annotations

import time
from typing import Optional

from app.api.main import AppContainer
from app.contracts import OutboundMessage
from app.core.time import utcnow
from app.models import Reminder
from app.services.reminders import ReminderService


def run_worker_loop(container: Optional[AppContainer] = None) -> None:
    resolved = container or AppContainer.build()
    while True:
        run_worker_once(resolved)
        time.sleep(resolved.settings.worker_poll_seconds)


def run_worker_once(container: AppContainer) -> int:
    claim_session = container.session_factory()
    try:
        reminder_service = ReminderService(claim_session, container.settings.default_timezone)
        reminders = reminder_service.claim_due_reminders(limit=container.settings.reminder_batch_size)
        reminder_ids = [reminder.id for reminder in reminders]
        claim_session.commit()
    finally:
        claim_session.close()

    processed = 0
    for reminder_id in reminder_ids:
        process_session = container.session_factory()
        try:
            reminder = process_session.get(Reminder, reminder_id)
            if reminder is None:
                continue
            adapter = container.telegram_adapter
            reminder_service = ReminderService(process_session, container.settings.default_timezone)
            outbound = OutboundMessage(
                platform=reminder.source_platform,
                chat_id=reminder.source_chat_id,
                text=reminder_service.build_notification_text(reminder),
            )
            try:
                adapter.send_message(outbound)
            except Exception as exc:  # pragma: no cover - network failure path
                reminder_service.mark_delivery_result(reminder=reminder, success=False, error=str(exc))
            else:
                reminder_service.mark_delivery_result(reminder=reminder, success=True)
            process_session.commit()
            processed += 1
        finally:
            process_session.close()
    return processed


if __name__ == "__main__":  # pragma: no cover
    run_worker_loop()
