from __future__ import annotations

import tempfile
import unittest
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.chat_service import ChatService
from app.conversation_service import ConversationService
from app.local_store import LocalStore
from app.models import ChatRequest


class ConversationServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = LocalStore(Path(self.temporary.name) / "conversation-test.db")
        self.chat_service = ChatService()
        self.chat_service.base_url = ""
        self.chat_service.model = ""
        self.service = ConversationService(
            self.store,
            self.chat_service,
            compact_trigger_tokens=256,
            keep_recent_messages=4,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_compaction_preserves_raw_messages_and_injects_working_summary(self) -> None:
        conversation = self.service.create("EEG 数据处理")
        for index in range(1, 9):
            role = "user" if index % 2 else "assistant"
            content = (
                f"第 {index} 条消息："
                + ("请保留 EEG 预处理约束和数据集选择。" * 8)
            )
            self.store.append_conversation_message(
                conversation.conversation_id,
                role=role,
                content=content,
                estimated_tokens=self.service.estimate_tokens(content),
                metadata={
                    "dataset_id": "dataset_eeg" if index == 1 else None,
                    "workflow_id": "wf_eeg" if index == 2 else None,
                },
                created_at=self.service._now(),
            )

        compacted = await self.service.compact_if_needed(
            conversation.conversation_id,
            force=True,
        )
        self.assertTrue(compacted)

        snapshot = self.service.snapshot(conversation.conversation_id)
        self.assertEqual(snapshot.memory.summary_version, 1)
        self.assertEqual(snapshot.memory.summarized_messages, 4)
        self.assertEqual(snapshot.memory.recent_messages, 4)
        self.assertEqual(len(snapshot.messages), 8)
        self.assertIn(
            "# Conversation Working Summary",
            snapshot.conversation.summary,
        )
        self.assertIn("dataset_eeg", snapshot.conversation.summary)
        self.assertEqual(
            len(self.store.conversation_messages(conversation.conversation_id)),
            8,
        )

        prepared, same_id = self.service.begin_turn(
            ChatRequest(
                conversation_id=conversation.conversation_id,
                message="继续，并沿用前面的约束。",
            )
        )
        self.assertEqual(same_id, conversation.conversation_id)
        self.assertEqual(prepared.conversation_summary, snapshot.conversation.summary)
        self.assertEqual(len(prepared.history), 4)
        self.assertEqual(
            len(self.store.conversation_messages(conversation.conversation_id)),
            9,
        )

    def test_new_turn_creates_server_owned_conversation(self) -> None:
        prepared, conversation_id = self.service.begin_turn(
            ChatRequest(message="开始一个新的本地数据处理任务")
        )
        self.assertEqual(prepared.conversation_id, conversation_id)
        snapshot = self.service.snapshot(conversation_id)
        self.assertEqual(snapshot.memory.recent_messages, 1)
        self.assertEqual(snapshot.recent_messages[0].role, "user")

    def test_lists_conversations_by_recent_activity_with_preview(self) -> None:
        first = self.service.create("第一条会话")
        second = self.service.create("第二条会话")
        self.store.append_conversation_message(
            first.conversation_id,
            role="user",
            content="请处理第一组数据\n并保存结果",
            estimated_tokens=12,
            metadata={"interaction_mode": "task"},
            created_at="2026-09-03T10:00:00+00:00",
        )
        self.store.append_conversation_message(
            second.conversation_id,
            role="assistant",
            content="第二条会话的最新回答",
            estimated_tokens=8,
            metadata={},
            created_at="2026-09-03T11:00:00+00:00",
        )

        conversations = self.service.list()
        self.assertEqual(
            [item.conversation_id for item in conversations],
            [second.conversation_id, first.conversation_id],
        )
        self.assertEqual(conversations[0].message_count, 1)
        self.assertEqual(conversations[0].last_message_role, "assistant")
        self.assertEqual(conversations[1].last_message_preview, "请处理第一组数据 并保存结果")

    def test_searches_titles_and_message_content(self) -> None:
        freesurfer = self.service.create("FreeSurfer 皮层分割")
        registration = self.service.create("图像处理")
        self.store.append_conversation_message(
            registration.conversation_id,
            role="user",
            content="请使用 ANTs 完成配准",
            estimated_tokens=8,
            metadata={},
            created_at="2026-09-03T12:00:00+00:00",
        )

        self.assertEqual(
            [item.conversation_id for item in self.service.list(query="FreeSurfer")],
            [freesurfer.conversation_id],
        )
        self.assertEqual(
            [item.conversation_id for item in self.service.list(query="ANTs")],
            [registration.conversation_id],
        )

    def test_rename_archive_and_restore_preserve_messages(self) -> None:
        conversation = self.service.create("原始标题")
        self.store.append_conversation_message(
            conversation.conversation_id,
            role="user",
            content="需要保留的消息",
            estimated_tokens=6,
            metadata={},
            created_at="2026-09-03T12:00:00+00:00",
        )

        renamed = self.service.update(
            conversation.conversation_id,
            title="  新的   标题  ",
        )
        self.assertEqual(renamed.title, "新的 标题")

        archived = self.service.update(
            conversation.conversation_id,
            archived=True,
        )
        self.assertTrue(archived.archived)
        self.assertEqual(self.service.list(), [])
        self.assertEqual(
            [item.conversation_id for item in self.service.list(archived=True)],
            [conversation.conversation_id],
        )
        self.assertEqual(len(self.service.snapshot(conversation.conversation_id).messages), 1)

        restored = self.service.update(
            conversation.conversation_id,
            archived=False,
        )
        self.assertFalse(restored.archived)
        self.assertEqual(len(self.service.list()), 1)

    def test_pages_cover_equal_timestamps_without_duplicates(self) -> None:
        with patch.object(self.service, "_now", return_value="2026-09-05T10:00:00+00:00"):
            ids = [self.service.create(f"对话 {index}").conversation_id for index in range(47)]
        seen = []
        sizes = []
        cursor = ""
        while True:
            page = self.service.list_page(cursor=cursor)
            sizes.append(len(page.conversations))
            seen.extend(item.conversation_id for item in page.conversations)
            if not page.has_more:
                self.assertIsNone(page.next_cursor)
                break
            self.assertTrue(page.next_cursor)
            self.assertNotEqual(page.next_cursor, cursor)
            cursor = page.next_cursor
        self.assertEqual(sizes, [20, 20, 7])
        self.assertEqual(seen, sorted(ids, reverse=True))

    def test_cursor_survives_deleted_boundary_and_new_conversation(self) -> None:
        with patch.object(self.service, "_now", return_value="2026-09-05T10:00:00+00:00"):
            ids = [self.service.create(f"归档 {index}").conversation_id for index in range(6)]
            for conversation_id in ids:
                self.service.update(conversation_id, archived=True)
        first = self.service.list_page(limit=2, archived=True)
        self.service.delete(first.conversations[-1].conversation_id)
        with patch.object(self.service, "_now", return_value="2026-09-06T10:00:00+00:00"):
            newest = self.service.create("新对话")
            self.service.update(newest.conversation_id, archived=True)
        second = self.service.list_page(limit=2, archived=True, cursor=first.next_cursor)
        self.assertEqual(
            [item.conversation_id for item in second.conversations],
            sorted(ids, reverse=True)[2:4],
        )

    def test_paged_search_filters_archive_scope_and_message_text(self) -> None:
        ids = []
        for index in range(5):
            conversation = self.service.create(f"影像 {index}")
            self.store.append_conversation_message(
                conversation.conversation_id, role="user", content="使用 ANTs 配准",
                estimated_tokens=6, metadata={}, created_at=self.service._now(),
            )
            if index != 4:
                self.service.update(conversation.conversation_id, archived=True)
                ids.append(conversation.conversation_id)
        self.service.update(self.service.create("无关任务").conversation_id, archived=True)
        first = self.service.list_page(limit=2, query="ANTs", archived=True)
        second = self.service.list_page(limit=2, query="ANTs", archived=True, cursor=first.next_cursor)
        self.assertEqual({item.conversation_id for item in first.conversations + second.conversations}, set(ids))
        self.assertFalse(second.has_more)
        self.assertEqual(len(self.service.list_page(query="ANTs").conversations), 1)

    def test_delete_requires_archive_and_cascades_messages(self) -> None:
        _, conversation_id = self.service.begin_turn(ChatRequest(message="需要删除的对话"))
        other = self.service.create("需要保留的对话")
        with self.assertRaisesRegex(ValueError, "已归档"):
            self.service.delete(conversation_id)
        self.assertEqual(len(self.store.conversation_messages(conversation_id)), 1)
        self.service.update(conversation_id, archived=True)
        self.service.delete(conversation_id)
        self.assertIsNone(self.store.get_conversation(conversation_id))
        self.assertEqual(self.store.conversation_messages(conversation_id), [])
        self.assertIsNotNone(self.store.get_conversation(other.conversation_id))
        with self.assertRaises(KeyError):
            self.service.delete(conversation_id)

    def test_list_does_not_transfer_full_summary(self) -> None:
        conversation = self.service.create("带摘要的会话")
        self.store.update_conversation_summary(
            conversation.conversation_id, summary="很长的摘要" * 1000,
            summary_version=1, summarized_through_sequence=0, updated_at=self.service._now(),
        )
        item = self.service.list_page().conversations[0].model_dump()
        self.assertNotIn('summary', item)
        self.assertNotIn('summarized_through_sequence', item)
        self.assertTrue(self.service.snapshot(conversation.conversation_id).conversation.summary)

    def test_long_messages_have_bounded_previews_without_loading_bodies(self) -> None:
        conversation = self.service.create('Long history')
        content = ('message\r\n' * 100_000)
        self.store.append_conversation_message(conversation.conversation_id, role='assistant', content=content,
            estimated_tokens=100_000, metadata={}, created_at=self.service._now())
        with patch.object(self.store, 'conversation_messages', side_effect=AssertionError('List must not load full messages')):
            item = self.service.list_page().conversations[0]
        self.assertEqual(item.last_message_preview, content[:160].replace('\r', ' ').replace('\n', ' '))
        self.assertLess(len(item.model_dump_json()), 800)

    def test_invalid_cursor_is_rejected_and_page_size_is_bounded(self) -> None:
        for cursor in ("%%%", "e30", "W10", "bnVsbA", "中文", "x" * 1025):
            with self.subTest(cursor=cursor[:20]), self.assertRaisesRegex(ValueError, "分页游标"):
                self.service.list_page(cursor=cursor)
        for index in range(53):
            self.service.create(str(index))
        self.assertEqual(len(self.service.list_page(limit=999).conversations), 50)

    def test_legacy_schema_migration_creates_pagination_index(self) -> None:
        database = Path(self.temporary.name) / "legacy.db"
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("""CREATE TABLE conversations (
                conversation_id TEXT PRIMARY KEY, title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '', summary_version INTEGER NOT NULL DEFAULT 0,
                summarized_through_sequence INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
        store = LocalStore(database)
        with store._connect() as connection:
            plan = connection.execute(
                "EXPLAIN QUERY PLAN SELECT conversation_id FROM conversations "
                "WHERE archived = ? AND (updated_at, conversation_id) < (?, ?) "
                "ORDER BY updated_at DESC, conversation_id DESC LIMIT ?",
                (0, "2026-09-05", "conv_a", 21),
            ).fetchall()
        self.assertIn("idx_conversations_archived_updated_id", " ".join(row["detail"] for row in plan))


if __name__ == "__main__":
    unittest.main()
