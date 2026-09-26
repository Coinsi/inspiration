"""Current-account operations; external login adapters share the same user record."""

import base64
import io

from fastapi import APIRouter, Depends, File, Request, UploadFile
from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth_controls import limit_auth
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.errors import CapabilityUnsupported
from app.core.security import create_access_token, hash_password, verify_password
from app.models.identity import User, UserIdentity
from app.modules.identity import schemas

router = APIRouter(tags=["account"])
DB = Depends(get_db, scope="function")
USER = Depends(get_current_user)


def locked_user(db, user):
    return db.scalar(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


@router.get("/auth/providers")
def providers():
    # Only enabled, server-configured adapters will be listed here.
    return {"registration_enabled": True, "providers": []}


@router.put("/me/profile", response_model=schemas.UserOut)
def update_profile(data: schemas.ProfileIn, user: User = USER, db: Session = DB):
    user = locked_user(db, user)
    user.display_name, user.bio = data.display_name, data.bio
    db.flush()
    return user


@router.post("/me/avatar", response_model=schemas.UserOut)
def upload_avatar(file: UploadFile = File(...), user: User = USER, db: Session = DB):
    raw = file.file.read(5 * 1024 * 1024 + 1)
    if not raw or len(raw) > 5 * 1024 * 1024:
        raise CapabilityUnsupported("头像大小不能超过5 MB")
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if (
                source.format not in {"JPEG", "PNG", "WEBP"}
                or source.width * source.height > 20_000_000
                or getattr(source, "is_animated", False)
            ):
                raise ValueError()
            image = ImageOps.fit(ImageOps.exif_transpose(source).convert("RGB"), (256, 256))
            output = io.BytesIO()
            image.save(output, "WEBP", quality=88)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise CapabilityUnsupported("请选择有效的静态 JPG、PNG 或 WebP 图片") from exc
    user = locked_user(db, user)
    user.avatar_data = "data:image/webp;base64," + base64.b64encode(output.getvalue()).decode(
        "ascii"
    )
    db.flush()
    return user


@router.delete("/me/avatar", response_model=schemas.UserOut)
def remove_avatar(user: User = USER, db: Session = DB):
    user = locked_user(db, user)
    user.avatar_data = None
    db.flush()
    return user


@router.get("/me/security")
def security(user: User = USER, db: Session = DB):
    identities = db.scalars(select(UserIdentity).where(UserIdentity.user_id == user.id))
    return {
        "has_password": bool(user.password_hash),
        "identities": [
            {"id": item.id, "provider": item.provider, "created_at": item.created_at}
            for item in identities
        ],
    }


@router.post("/me/password", response_model=schemas.TokenOut)
def change_password(
    data: schemas.PasswordIn, request: Request, user: User = USER, db: Session = DB
):
    limit_auth(db, request, "password")
    user = locked_user(db, user)
    if not user.password_hash or not verify_password(data.current_password, user.password_hash):
        # A wrong current password must not erase the valid browser login.
        raise CapabilityUnsupported("当前密码不正确")
    if verify_password(data.new_password, user.password_hash):
        raise CapabilityUnsupported("新密码不能与当前密码相同")
    user.password_hash = hash_password(data.new_password)
    user.auth_version += 1
    db.flush()
    return schemas.TokenOut(
        access_token=create_access_token(str(user.id), {"ver": user.auth_version})
    )
