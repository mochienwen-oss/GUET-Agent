"""One-time reset for the memory upgrade. Run only with the application stopped."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))

from agent.runtime.user_memory import _uri
from database.db import init_db
import psycopg


def main():
    init_db()
    with psycopg.connect(_uri(), autocommit=False) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute('LOCK TABLE messages, conversations, memory_control, memory_owners, memory_jobs, store IN ACCESS EXCLUSIVE MODE')
                cur.execute('SELECT 1 FROM memory_control WHERE id=1')
                if cur.fetchone():
                    raise RuntimeError('新记忆系统已经初始化，拒绝重复执行清理')
                cur.execute('SELECT COALESCE(MAX(id), 0) FROM messages')
                cutoff = cur.fetchone()[0]
                cur.execute("DELETE FROM store WHERE (prefix ~ '^users\\.[0-9]+$' AND key IN ('memory', 'profile', 'preferences', 'automatic')) OR (prefix ~ '^users\\.[0-9]+\\.conversations\\.[0-9]+$' AND key='summary') OR prefix ~ '^users\\.[0-9]+\\.memory_v2$'")
                cur.execute('UPDATE conversations SET context_json=NULL, context_revision=NULL')
                cur.execute('DELETE FROM memory_jobs')
                cur.execute('DELETE FROM memory_owners')
                cur.execute('INSERT INTO memory_control(id, cutoff_message_id, generation, created_at) VALUES (1, %s, 1, CURRENT_TIMESTAMP)', (cutoff,))
    print('已清除旧记忆与摘要；原始聊天、文件及知识库未删除。')


if __name__ == '__main__':
    main()

