import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from google.genai import types
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect
from test_workflow import selection

from canopy.api import create_app
from canopy.auth import SupabaseAuth
from canopy.models import Conversation, RefundQuote
from canopy.policy import POLICY_VERSION
from canopy.voice import AUDIO_BYTES, get_live_connector

ORIGIN = "http://localhost:3000"


def spoken(**content):
    return types.LiveServerMessage(server_content=types.LiveServerContent(**content))


def sound(seconds=1):
    return types.Content(
        parts=[
            types.Part(
                inline_data=types.Blob(
                    data=bytes(48_000 * seconds), mime_type="audio/pcm;rate=24000"
                )
            )
        ]
    )


class FakeLive:
    def __init__(self):
        self.replies = asyncio.Queue()
        self.received = []
        self.responses = []

    async def send_realtime_input(self, **values):
        self.received.append(values)

    async def send_client_content(self, *, turns, turn_complete):
        assert turn_complete
        await self.send_realtime_input(text=turns.parts[0].text)

    async def send_tool_response(self, *, function_responses):
        self.responses.append(function_responses)

    async def receive(self):
        while True:
            reply = await self.replies.get()
            yield reply
            if reply.server_content and reply.server_content.turn_complete:
                return

    def emit(self, reply):
        self.replies.put_nowait(reply)

    def request(self, name, args=None):
        self.emit(
            types.LiveServerMessage(
                tool_call=types.LiveServerToolCall(
                    function_calls=[
                        types.FunctionCall(
                            id=f"{len(self.responses)}-{name}", name=name, args=args or {}
                        ),
                    ]
                )
            )
        )


@pytest.fixture
def voice_client(settings, engine):
    import httpx
    from conftest import supabase_mock

    settings = settings.model_copy(update={"voice_enabled": True})
    app = create_app(settings)
    calls = []

    @asynccontextmanager
    async def connect(_settings):
        fake = FakeLive()
        calls.append(fake)
        yield fake

    app.dependency_overrides[get_live_connector] = lambda: connect
    with httpx.Client(transport=httpx.MockTransport(supabase_mock)) as upstream:
        with TestClient(app, headers={"Authorization": "Bearer maya-token"}) as client:
            app.state.auth = SupabaseAuth(settings, upstream)
            conversation = client.post("/api/conversations", json={"channel": "call"}).json()["id"]
            yield SimpleNamespace(
                client=client, calls=calls, path=f"/api/conversations/{conversation}"
            )


def test_voice_verifies_origin_role_and_conversation_before_opening_provider(voice_client):
    assert voice_client.client.get("/api/voice").json() == {"enabled": True}
    chat_id = voice_client.client.post("/api/conversations").json()["id"]
    with (
        pytest.raises(WebSocketDisconnect),
        voice_client.client.websocket_connect(
            f"/api/conversations/{chat_id}/voice",
            headers={"Origin": ORIGIN, "Cookie": "canopy_access=maya-token"},
        ),
    ):
        pass
    for origin, token in [
        ("https://other.example", "maya-token"),
        (ORIGIN, "jordan-token"),
        (ORIGIN, "admin-token"),
        (ORIGIN, ""),
    ]:
        with (
            pytest.raises(WebSocketDisconnect),
            voice_client.client.websocket_connect(
                f"{voice_client.path}/voice",
                headers={"Origin": origin, "Cookie": f"canopy_access={token}"},
            ),
        ):
            pass
    assert not voice_client.calls


def receive_event(socket, kind):
    while True:
        event = socket.receive_json()
        if event["type"] == kind:
            return event


def test_voice_streams_audio_cuts_a_reply_and_saves_transcript_for_staff(voice_client):
    client, path = voice_client.client, voice_client.path
    with client.websocket_connect(
        f"{path}/voice", headers={"Origin": ORIGIN, "Cookie": "canopy_access=maya-token"}
    ) as socket:
        assert socket.receive_json() == {"type": "ready"}
        fake = voice_client.calls[0]

        async def script():
            fake.emit(
                spoken(model_turn=sound(), output_transcription={"text": "Your last order was…"})
            )

        socket.portal.call(script)
        assert socket.receive_json()["text"] == "Your last order was…"
        assert len(socket.receive_bytes()) == 48_000

        socket.send_bytes(bytes(AUDIO_BYTES))
        socket.send_text("speech_start")
        socket.send_bytes(bytes(AUDIO_BYTES))
        socket.send_text("speech_end")

        async def interrupt():
            fake.emit(spoken(interrupted=True))
            fake.emit(spoken(model_turn=sound(), output_transcription={"text": "Discard this"}))
            fake.emit(spoken(input_transcription={"text": "Actually the headphones."}))
            fake.emit(spoken(turn_complete=True))

        socket.portal.call(interrupt)
        assert socket.receive_json() == {"type": "interrupted"}
        assert socket.receive_json()["text"] == "Actually the headphones."
        assert socket.receive_json() == {"type": "turn_complete", "saved": True}

        socket.portal.call(fake.request, "lookup_order", {"order_number": "AMZ-1024"})
        assert socket.receive_json()["state"] == "working"
        event = receive_event(socket, "order")
        assert event["order"]["order_number"] == "AMZ-1024"
        socket.portal.call(fake.request, "request_human_support")
        ticket = receive_event(socket, "ticket")["id"]

        socket.send_text("end")
        assert receive_event(socket, "ended")["message"] == "Call ended."
        forwarded = [frame["audio"].data for frame in fake.received if frame.get("audio")]
        assert forwarded == [bytes(AUDIO_BYTES)]
        assert any("activity_start" in item for item in fake.received)
        assert any("activity_end" in item for item in fake.received)

    history = client.get(f"{path}/history").json()
    assert [item["content"] for item in history] == ["Actually the headphones."]
    context = client.get(
        f"/api/admin/escalations/{ticket}/context",
        headers={
            "Authorization": "Bearer support-token",
        },
    ).json()
    assert "Actually the headphones." in str(context)
    assert "AMZ-1024" in str(context)
    assert client.get("/api/refunds").json() == []


@pytest.mark.parametrize("reason", ["change_of_mind", "damaged"])
def test_voice_prefills_refund_and_routes_exceptions(voice_client, reason):
    client, path = voice_client.client, voice_client.path
    assert client.get("/api/conversations?channel=chat").json() == []
    assert client.get("/api/conversations?channel=call").json()[0]["channel"] == "call"
    with client.websocket_connect(
        f"{path}/voice", headers={"Origin": ORIGIN, "Cookie": "canopy_access=maya-token"}
    ) as socket:
        assert socket.receive_json()["type"] == "ready"
        fake = voice_client.calls[0]
        socket.portal.call(fake.request, "lookup_order", {"order_number": "AMZ-1024"})
        item = receive_event(socket, "order")["order"]["items"][0]
        socket.portal.call(
            fake.request,
            "prepare_refund",
            {
                "order_number": "AMZ-1024",
                "reason": reason,
                "items": [{"order_item_id": item["order_item_id"], "quantity": 1}],
            },
        )
        if reason == "damaged":
            ticket = receive_event(socket, "ticket")
            assert ticket["routed"] is True
            assert receive_event(socket, "refund_preview")["quote"] is None
            context = client.get(f"/api/escalations/{ticket['id']}").json()
            assert context["reason"] == "refund_exception"
            assert client.get("/api/refunds").json() == []
        else:
            preview = receive_event(socket, "refund_preview")
            assert preview["items"] == [{"name": item["product_name"], "quantity": 1}]
            quote = preview["quote"]
            assert quote["amount"] == "89.00"
            socket.portal.call(
                fake.emit,
                spoken(
                    input_transcription={"text": "Return one pair: changed my mind."},
                    turn_complete=True,
                ),
            )
            assert receive_event(socket, "turn_complete")["saved"]
            assert client.get(f"{path}/quote").json()["id"] == quote["id"]
            action = {"type": "refund_action", "action": "confirm_refund", "quote_id": quote["id"]}
            socket.send_json({**action, "confirmed": "yes"})
            assert receive_event(socket, "refund_error")
            assert client.get("/api/refunds").json() == []
            socket.send_json({**action, "confirmed": True})
            assert receive_event(socket, "refund_result")["refund"]["simulated"] is True
            assert len(client.get("/api/refunds").json()) == 1
        socket.send_text("end")
        assert receive_event(socket, "ended")


def test_voice_consent_cannot_submit_and_changed_instructions_invalidate_quote(
    voice_client, engine
):
    client, path = voice_client.client, voice_client.path
    conversation_id = UUID(path.rsplit("/", 1)[1])
    with Session(engine) as db, db.begin():
        db.add(
            RefundQuote(
                conversation_id=conversation_id,
                customer_id=db.get(Conversation, conversation_id).customer_id,
                order_id=selection().order_id,
                selection=selection().model_dump(mode="json"),
                amount=89,
                currency="USD",
                policy_version=POLICY_VERSION,
                expires_at=datetime.now(UTC) + timedelta(minutes=15),
            )
        )

    with client.websocket_connect(
        f"{path}/voice", headers={"Origin": ORIGIN, "Cookie": "canopy_access=maya-token"}
    ) as socket:
        assert socket.receive_json()["type"] == "ready"
        fake = voice_client.calls[0]
        for speech in ("Yes please.", "Actually, check a different order."):
            socket.portal.call(
                fake.emit,
                spoken(
                    input_transcription={"text": speech},
                    turn_complete=True,
                ),
            )
            assert socket.receive_json()["text"] == speech
            assert socket.receive_json()["saved"]
            quote = client.get(f"{path}/quote").json()
            assert (quote is not None) == speech.startswith("Yes")
            assert client.get("/api/refunds").json() == []
        socket.send_text("end")
        assert socket.receive_json()["type"] == "ended"


class BrowserLive(FakeLive):
    def __init__(self):
        super().__init__()
        self.spoken_turns = 0

    async def send_realtime_input(self, **values):
        if values.get("text", "").startswith("The call has connected"):
            self.emit(
                spoken(
                    model_turn=sound(seconds=6),
                    output_transcription={"text": "Your latest order was…"},
                )
            )
        elif values.get("text", "").startswith("Verified on-screen action."):
            self.emit(
                spoken(
                    model_turn=sound(),
                    output_transcription={"text": "Your refund request was submitted."},
                )
            )
            self.emit(spoken(turn_complete=True))
        if "activity_start" in values:
            self.emit(spoken(interrupted=True))
        if "activity_end" in values:
            self.spoken_turns += 1
            if self.spoken_turns > 1:
                self.emit(spoken(input_transcription={"text": "Thanks, goodbye."}))
                self.request("finish_call")
                return
            self.emit(
                spoken(
                    input_transcription={
                        "text": "Actually, return one pair of headphones. I changed my mind."
                    }
                )
            )
            self.emit(spoken(turn_complete=True))
            self.request("lookup_order", {"order_number": "AMZ-1027"})

    async def send_tool_response(self, *, function_responses):
        await super().send_tool_response(function_responses=function_responses)
        if function_responses.name == "lookup_order":
            item = function_responses.response["items"][0]
            self.request(
                "prepare_refund",
                {
                    "order_number": "AMZ-1027",
                    "reason": "change_of_mind",
                    "items": [{"order_item_id": item["order_item_id"], "quantity": 1}],
                },
            )
        if function_responses.name == "prepare_refund":
            self.emit(spoken(output_transcription={"text": "Confirm the refund on screen."}))
            self.emit(spoken(turn_complete=True))
        if function_responses.name == "finish_call":
            self.emit(
                spoken(
                    model_turn=sound(), output_transcription={"text": "Goodbye. Have a great day."}
                )
            )
            self.emit(spoken(turn_complete=True))


@asynccontextmanager
async def browser_live(_settings):
    yield BrowserLive()
