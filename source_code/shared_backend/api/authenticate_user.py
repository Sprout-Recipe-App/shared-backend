import os
import traceback
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import ClassVar, Optional

import jwt
from fastapi import Body, HTTPException
from jwt import PyJWKClient

from fast_server import APIOperation

from shared_backend.data.user_related.user.user import User, UserAccount, UserIdentity, UserPreferences


class AuthenticateUser(APIOperation):
    METHOD = "POST"
    APPLE_PUBLIC_KEYS_URL = "https://appleid.apple.com/auth/keys"
    VALID_AUDIENCES = [
        "com.ecstasy.sprout",
        os.environ.get("APPLE_WEB_SERVICES_ID", "com.sprout.website"),
    ]

    _post_signup_hooks: ClassVar[list[Callable[[str], Awaitable]]] = []

    @classmethod
    def register_post_signup_hook(cls, hook: Callable[[str], Awaitable]):
        cls._post_signup_hooks.append(hook)

    @classmethod
    async def _verify_apple_token(cls, identity_token: str) -> str:
        jwks_client = PyJWKClient(cls.APPLE_PUBLIC_KEYS_URL)
        signing_key = jwks_client.get_signing_key_from_jwt(identity_token)
        decoded = jwt.decode(
            identity_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=cls.VALID_AUDIENCES,
            issuer="https://appleid.apple.com",
        )
        return decoded["sub"]

    async def execute(
        self,
        identity_token: str = Body(),
        name: Optional[str] = Body(None),
        birthday: Optional[datetime] = Body(None),
        top_goals: list[str] = Body([]),
    ) -> dict:
        try:
            user_id = await self._verify_apple_token(identity_token)
        except Exception:
            traceback.print_exc()
            raise HTTPException(status_code=401, detail="Invalid or expired Apple identity token.")

        try:
            existing_user = await User.find_one({"account.user_id": user_id})
        except Exception as e:
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Database error: {type(e).__name__}: {e}")

        if existing_user:
            updates = {}
            if name and existing_user.identity and existing_user.identity.name != name:
                updates["identity.name"] = name
            if birthday and existing_user.identity and existing_user.identity.birthday != birthday:
                updates["identity.birthday"] = birthday
            if updates:
                await User.update_one({"account.user_id": user_id}, {"$set": updates})
            return {
                "user_id": user_id,
                "is_new_user": False,
                "name": existing_user.identity.name if existing_user.identity else None,
                "email": existing_user.account.email if existing_user.account else None,
            }

        new_user_data = {
            "_id": user_id,
            "account": UserAccount(user_id=user_id).model_dump(mode="json"),
            "preferences": UserPreferences(top_goals=top_goals).model_dump(mode="json"),
            "points": 0,
        }
        if name and birthday:
            new_user_data["identity"] = UserIdentity(name=name, birthday=birthday).model_dump(mode="json")

        await User.insert_one(new_user_data)

        for hook in self._post_signup_hooks:
            await hook(user_id)

        return {"user_id": user_id, "is_new_user": True, "name": name}
