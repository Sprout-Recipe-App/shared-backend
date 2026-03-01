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
            return await self._authenticate(identity_token, name, birthday, top_goals)
        except HTTPException:
            raise
        except Exception:
            traceback.print_exc()
            raise HTTPException(status_code=500, detail="Internal server error during authentication.")

    async def _authenticate(self, identity_token: str, name, birthday, top_goals) -> dict:
        try:
            user_id = await self._verify_apple_token(identity_token)
        except Exception:
            traceback.print_exc()
            raise HTTPException(status_code=401, detail="Invalid or expired Apple identity token.")

        existing_user = await User.find_one({"account.user_id": user_id})

        if existing_user:
            updates = {}
            if name:
                if not existing_user.identity:
                    updates["identity"] = {"name": name}
                elif existing_user.identity.name != name:
                    updates["identity.name"] = name
            if birthday:
                if existing_user.identity and existing_user.identity.birthday != birthday:
                    updates["identity.birthday"] = birthday
                elif not existing_user.identity and name:
                    updates.setdefault("identity", {})["birthday"] = birthday
            if updates:
                await User.update_one({"account.user_id": user_id}, {"$set": updates})

            resolved_name = name or (existing_user.identity.name if existing_user.identity else None)
            return {
                "user_id": user_id,
                "is_new_user": False,
                "name": resolved_name,
                "email": existing_user.account.email if existing_user.account else None,
            }

        new_user = User(
            id=user_id,
            account=UserAccount(user_id=user_id),
            identity=UserIdentity(name=name, birthday=birthday) if name and birthday else None,
            preferences=UserPreferences(top_goals=top_goals),
        )
        await new_user.save()

        for hook in self._post_signup_hooks:
            await hook(user_id)

        return {"user_id": user_id, "is_new_user": True, "name": name}
