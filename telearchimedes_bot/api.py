from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Any

import httpx


RequestFunc = Callable[[str, dict[str, Any]], Any]


class TelegramApiError(RuntimeError):
    pass


class TelegramApi:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://api.telegram.org",
        request_func: RequestFunc | None = None,
    ) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self._request_func = request_func
        self._client = httpx.Client(
            timeout=httpx.Timeout(10.0, connect=3.0),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> TelegramApi:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()

    def request(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: httpx.Timeout | float | None = None,
    ) -> Any:
        payload = payload or {}
        if self._request_func:
            return self._request_func(method, payload)

        try:
            response = self._client.post(
                f"{self.base_url}/bot{self.token}/{method}",
                json=payload,
                timeout=timeout,
            )
            data = response.json()
        except httpx.HTTPError as exc:
            raise TelegramApiError(
                f"{method} failed: HTTP error {exc.__class__.__name__}"
            ) from None
        except ValueError:
            raise TelegramApiError(f"{method} failed: invalid JSON response") from None

        if response.status_code >= 400 or not data.get("ok"):
            description = data.get("description")
            if not isinstance(description, str) or not description:
                description = f"HTTP {response.status_code}"
            raise TelegramApiError(f"{method} failed: {description}")
        return data.get("result")

    def get_me(self) -> dict[str, Any]:
        return self.request("getMe")

    def get_updates(self, *, offset: int | None = None, timeout: int = 30) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        return self.request(
            "getUpdates", payload, timeout=httpx.Timeout(timeout + 10.0, connect=3.0)
        )

    def set_my_commands(self, commands: list[dict[str, str]]) -> Any:
        return self.request("setMyCommands", {"commands": commands})

    def send_message(
        self,
        *,
        chat_id: int | str,
        text: str,
        parse_mode: str | None = None,
        message_thread_id: int | None = None,
        reply_to_message_id: int | None = None,
        disable_web_page_preview: bool = True,
    ) -> Any:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id
        if reply_to_message_id is not None:
            # Still answer when the person deleted the message in the meantime;
            # otherwise the whole send fails and the answer is lost.
            payload["reply_parameters"] = {
                "message_id": reply_to_message_id,
                "allow_sending_without_reply": True,
            }
        return self.request("sendMessage", payload)

    def delete_message(self, *, chat_id: int | str, message_id: int) -> Any:
        return self.request(
            "deleteMessage",
            {
                "chat_id": chat_id,
                "message_id": message_id,
            },
        )
