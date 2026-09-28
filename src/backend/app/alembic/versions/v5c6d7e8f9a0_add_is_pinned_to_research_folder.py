"""add is_pinned to research_folder

Revision ID: v5c6d7e8f9a0
Revises: u4b5c6d7e8f9a
Create Date: 2026-09-10 00:00:00.000000

1. 新增 ``research_folder.is_pinned``（NOT NULL，server_default false）。置顶从
   「往 sort_order 里塞极小值」升级为独立布尔维度：sort_order 只表达用户手动
   指定的位置，置顶/取消置顶是可逆的开关，取消后能回到原位。
2. 新增排序覆盖索引 ``(user_id, is_pinned, sort_order)``，与 ``list_folders`` /
   ``get_folder_tree`` 的 ``order_by(is_pinned DESC, sort_order, created_time)``
   前缀对齐，避免加了置顶后每次列表都走全表 sort。

存量数据全部为 false，行为与升级前一致（老的 0 号文件夹仍按创建时间排序）。
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "v5c6d7e8f9a0"
down_revision = "u4b5c6d7e8f9a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "research_folder",
        sa.Column(
            "is_pinned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_research_folder_user_pinned_order",
        "research_folder",
        ["user_id", "is_pinned", "sort_order"],
    )


def downgrade():
    op.drop_index(
        "ix_research_folder_user_pinned_order", table_name="research_folder"
    )
    op.drop_column("research_folder", "is_pinned")
