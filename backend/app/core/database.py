"""数据库会话与引擎(SQLAlchemy 2.0,同步)。"""
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Iterator[Session]:
    """Use Depends(get_db, scope='function'): commit before sending the response.

    Request-scoped yield teardown runs after the response in modern FastAPI; a
    follow-up upload/read can otherwise race the commit and see a false 404.
    Streaming endpoints must capture detached file metadata before returning.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
