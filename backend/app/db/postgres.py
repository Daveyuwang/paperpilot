from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create all tables on startup (dev mode); production uses Alembic."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("ALTER TABLE papers ADD COLUMN IF NOT EXISTS guest_id VARCHAR(64)"))
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_papers_guest_created_at "
                "ON papers (guest_id, created_at DESC)"
            )
        )
        await conn.execute(text("ALTER TABLE papers ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(36)"))
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_papers_workspace_id "
                "ON papers (workspace_id)"
            )
        )
        await conn.execute(text("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS guest_id VARCHAR(64)"))
        await conn.execute(text("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(36)"))
        await conn.execute(text("ALTER TABLE sessions ALTER COLUMN paper_id DROP NOT NULL"))
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_sessions_guest_paper_last_active "
                "ON sessions (guest_id, paper_id, last_active DESC)"
            )
        )
