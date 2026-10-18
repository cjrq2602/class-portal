"""
cleanup.py —— 空房清理脚本（PythonAnywhere Tasks 定时调用）
============================================================
用法：PythonAnywhere "Tasks" 标签新建定时任务：
    每小时执行：/home/你的用户名/mysite/.virtualenvs 不需要，
    命令写：python3.10 /home/你的用户名/mysite/cleanup.py
（或带上虚拟环境 python 路径）
功能：清理 10 分钟无活动的房间（标记 abandoned）+ 超过 24 小时的排队残留 + 裁剪聊天。
纯 CPU 秒级操作，不占常驻进程。
"""

from __future__ import annotations

import db
from game import match


def main() -> None:
    db.init_db()
    n_rooms: int = match.cleanup_idle_rooms()
    # 清理超过 1 小时的匹配队列残留
    with db.get_db() as conn:
        cur = conn.execute(
            "DELETE FROM match_queue WHERE joined_at < ?",
            (db.now() - 3600,),
        )
        n_queue: int = cur.rowcount
        # 聊天裁剪到每房 50 条（兜底，正常写入时已裁剪）
        conn.execute(
            "DELETE FROM chat_messages WHERE id NOT IN"
            " (SELECT id FROM chat_messages c2 WHERE c2.room_id=chat_messages.room_id"
            "  ORDER BY id DESC LIMIT 50)"
        )
    print(f"清理完成：废弃房间 {n_rooms} 个，残留队列 {n_queue} 条")


if __name__ == "__main__":
    main()
