"""Initial catalog, search, session, assistant and commerce schema."""

from alembic import op

from app.common.models import Base
from app.common.schema import EXTENSIONS, POST_CREATE, PRE_DROP

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for statement in EXTENSIONS:
        op.execute(statement)
    Base.metadata.create_all(bind=bind)
    for statement in POST_CREATE:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    for statement in PRE_DROP:
        op.execute(statement)
    Base.metadata.drop_all(bind=bind)
