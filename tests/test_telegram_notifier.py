from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codexmon.cli import main
from codexmon.ledger import RunLedger
from codexmon.telegram_notifier import (
    TelegramCommandResult,
    TelegramNotifier,
    TelegramNotifierError,
    TelegramTransportMessage,
)


class FakeTelegramTransport:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[dict[str, str]] = []

    def send_message(
        self, chat_id: str, text: str, reply_to_message_id: str = ""
    ) -> TelegramTransportMessage:
        if self.fail:
            raise TelegramNotifierError("simulated telegram delivery failure")
        message_id = str(len(self.messages) + 1)
        self.messages.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
                "message_id": message_id,
            }
        )
        return TelegramTransportMessage(chat_id=chat_id, message_id=message_id, raw={"ok": True})


class TelegramNotifierTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "codexmon.db"
        self.ledger = RunLedger(self.db_path)
        self.ledger.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def make_run(self, summary: str = "telegram test") -> str:
        task = self.ledger.create_task(summary)
        return self.ledger.create_run(task.task_id).run_id

    def move_to_running(self, run_id: str) -> None:
        self.ledger.transition_run(run_id, "preflight", "preflight started")
        self.ledger.transition_run(run_id, "workspace_allocated", "workspace assigned")
        self.ledger.assign_workspace(run_id, "/tmp/codexmon/run-telegram", "codexmon/run-telegram")
        self.ledger.transition_run(run_id, "running", "runner launched")

    def test_notify_run_sends_summary_and_records_event(self) -> None:
        run_id = self.make_run("telegram notify")
        transport = FakeTelegramTransport()
        notifier = TelegramNotifier(self.ledger, transport=transport, default_chat_id="1001")

        result = notifier.notify_run(run_id, event_label="상태 변경")
        events = self.ledger.list_events(run_id)

        self.assertTrue(result.delivered)
        self.assertEqual(result.message_ref, "telegram:1001:1")
        self.assertIn(run_id, transport.messages[0]["text"])
        self.assertIn("telegram.message.sent", [event.event_type for event in events])

    def test_notify_run_records_delivery_failure(self) -> None:
        run_id = self.make_run("telegram notify failure")
        notifier = TelegramNotifier(
            self.ledger,
            transport=FakeTelegramTransport(fail=True),
            default_chat_id="1001",
        )

        with self.assertRaises(TelegramNotifierError):
            notifier.notify_run(run_id, event_label="실패 테스트")

        event_types = [event.event_type for event in self.ledger.list_events(run_id)]
        self.assertIn("telegram.message.failed", event_types)

    def test_status_command_returns_summary_and_reply(self) -> None:
        run_id = self.make_run("telegram status")
        transport = FakeTelegramTransport()
        notifier = TelegramNotifier(self.ledger, transport=transport, default_chat_id="1001")

        result = notifier.process_inbound_text(
            text=f"/status {run_id}",
            operator_id="operator-1",
            chat_id="1001",
            message_id="55",
        )

        self.assertTrue(result.accepted)
        self.assertEqual(result.final_state, "queued")
        self.assertEqual(result.reply_message_ref, "telegram:1001:1")
        self.assertIn("state: queued", transport.messages[0]["text"])

    def test_stop_command_halts_nonterminal_run(self) -> None:
        run_id = self.make_run("telegram stop")
        self.move_to_running(run_id)
        transport = FakeTelegramTransport()
        notifier = TelegramNotifier(self.ledger, transport=transport, default_chat_id="1001")

        result = notifier.process_inbound_text(
            text=f"/stop {run_id}",
            operator_id="operator-2",
            chat_id="1001",
            message_id="77",
        )

        self.assertTrue(result.accepted)
        self.assertEqual(result.final_state, "halted")
        self.assertEqual(self.ledger.get_run(run_id).current_state, "halted")

    def test_retry_and_approve_commands_follow_allowed_transitions(self) -> None:
        retry_run_id = self.make_run("telegram retry")
        self.move_to_running(retry_run_id)
        self.ledger.transition_run(
            retry_run_id,
            "awaiting_human",
            "retryable-by-human: operator review requested",
        )

        approval_run_id = self.make_run("telegram approve")
        self.move_to_running(approval_run_id)
        approval_request_id = self.ledger.request_approval(
            approval_run_id,
            requested_by="policy",
            payload={"change_class": "dependency-manifest"},
        )
        self.ledger.transition_run(
            approval_run_id,
            "awaiting_human",
            "approval required",
            approval_request_id=approval_request_id,
        )

        notifier = TelegramNotifier(
            self.ledger,
            transport=FakeTelegramTransport(),
            default_chat_id="1001",
        )

        retry_result = notifier.process_inbound_text(
            text=f"/retry {retry_run_id}",
            operator_id="operator-3",
            chat_id="1001",
            message_id="88",
        )
        approve_result = notifier.process_inbound_text(
            text=f"/approve {approval_run_id}",
            operator_id="operator-4",
            chat_id="1001",
            message_id="89",
        )

        self.assertTrue(retry_result.accepted)
        self.assertEqual(retry_result.final_state, "retry_pending")
        self.assertEqual(self.ledger.get_run(retry_run_id).current_state, "retry_pending")

        self.assertTrue(approve_result.accepted)
        self.assertEqual(approve_result.final_state, "retry_pending")
        self.assertEqual(self.ledger.get_run(approval_run_id).approval_status, "approved")
        self.assertEqual(
            self.ledger.get_approval(approve_result.approval_request_id).status,
            "approved",
        )


class TelegramNotifierCliTestCase(unittest.TestCase):
    def test_telegram_cli_receive_and_notify(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "codexmon.db"
            ledger = RunLedger(db_path)
            ledger.initialize()
            task = ledger.create_task("telegram cli")
            run = ledger.create_run(task.task_id)
            notifier = TelegramNotifier(
                ledger=ledger,
                transport=FakeTelegramTransport(),
                default_chat_id="1001",
            )
            env = os.environ.copy()
            env["CODEXMON_DB_PATH"] = str(db_path)
            with mock.patch.dict(os.environ, env, clear=True):
                with mock.patch("codexmon.cli.build_telegram_notifier", return_value=notifier):
                    notify_buffer = StringIO()
                    with redirect_stdout(notify_buffer):
                        notify_exit_code = main(["telegram", "notify", run.run_id])

                    receive_buffer = StringIO()
                    with redirect_stdout(receive_buffer):
                        receive_exit_code = main(
                            ["telegram", "receive", "/status", run.run_id, "--chat-id", "1001"]
                        )

        self.assertEqual(notify_exit_code, 0)
        self.assertEqual(receive_exit_code, 0)
        self.assertIn("message_ref=telegram:1001:1", notify_buffer.getvalue())
        self.assertIn("accepted=True", receive_buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
