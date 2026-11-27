"""Authenticated Gemini Live audio relay and account-scoped support tools."""

import asyncio
import re
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from hashlib import sha256
from time import monotonic
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types
from pydantic import ValidationError, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from canopy.admission import DAY, Limit
from canopy.auth import SupabaseAuth, current_customer, get_auth
from canopy.config import Settings
from canopy.contracts import EligibilityInput, EscalationInput, Input
from canopy.corpus import CorpusInvalid
from canopy.embedding import EmbeddingUnavailable, GeminiEmbedder
from canopy.models import Conversation, Customer, Escalation, Message, RefundQuote
from canopy.order_views import order_view
from canopy.retrieval import Citation, SearchRequest, search_policy
from canopy.tools import SupportTools
from canopy.workflow import SupportWorkflow
from canopy.workflow_contracts import Confirmation, QuoteView, TurnInput, is_refund_affirmation

router = APIRouter(prefix="/api", tags=["voice support"])
AUDIO_BYTES = 640  # 20 ms of mono PCM16 at 16 kHz
TRANSCRIPT_LIMIT = 2000
PROMPT = """You are Canopy, Amazora's AI customer support agent. Speak naturally and briefly.
Speak English throughout the call, including greetings, questions, and acknowledgments.
Keep replying in English if the caller switches languages. If you cannot understand what was said,
ask the caller to repeat it in English. Ignore stray words and background conversations.
Leave room for pauses and corrections. Ask one question at a time. Start with a brief greeting.
Look up the customer's order or approved policy before stating any factual details about it.
Use only the results of the tools for order and policy facts. If a source is missing or
conflicting, offer human support. Keep tracking codes and policy source details on screen.
Never ask for passwords or payment details. Never promise a payment, approval, or live transfer.
Say "refund request" and "submitted" in customer-facing speech. Keep internal IDs off the call.
Choose the relevant workflow from the customer's request without asking which feature to use.
When the caller describes a purchase without an order number, check recent orders for a match
before asking them for more information. Ask which order only when the match is ambiguous.
For a return, look up the order and use prepare_refund once you know the items, whole quantities,
and reason. Ask only for missing details. Read back the verified preview amount and selections, and
ask the customer to use the on-screen confirmation. Never accept spoken refund confirmation.
If a return requires staff review, use prepare_refund to route it automatically. If the details
needed for a preview cannot be verified and the caller wants help anyway, use request_human_support.
When the customer asks for a person, use request_human_support immediately. Never ask them to click
a button to create a ticket. Say you sent the request to support only once a tool confirms it.
Call finish_call when the customer asks to end the call or clearly says goodbye or that they are
done. Once the tool responds, say a brief goodbye and do not ask another question.
Acknowledge a verified refund confirmation or cancellation immediately.
If a lookup is slow, a short acknowledgment is enough. Do not repeat a superseded answer.
"""

ORDER_NUMBER = {"type": "string", "description": "An order number such as AMZ-1024"}
DECLARATIONS = [
    {
        "name": "recent_orders",
        "description": "Find recent orders and their items for this customer",
    },
    {
        "name": "lookup_order",
        "description": "Find verified order and tracking details for one owned order",
        "parameters_json_schema": {
            "type": "object",
            "properties": {"order_number": ORDER_NUMBER},
            "required": ["order_number"],
        },
    },
    {
        "name": "search_policy",
        "description": "Find approved Amazora policy excerpts. Query without customer identifiers.",
        "parameters_json_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "prepare_refund",
        "description": "Prepare a refund request or route the return for staff review",
        "parameters_json_schema": {
            "type": "object",
            "properties": {
                "order_number": ORDER_NUMBER,
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "order_item_id": {
                                "type": "string",
                                "description": "ID from lookup_order",
                            },
                            "quantity": {"type": "integer", "minimum": 1},
                        },
                        "required": ["order_item_id", "quantity"],
                    },
                },
                "reason": {
                    "type": "string",
                    "enum": ["change_of_mind", "damaged", "wrong_item", "other"],
                },
                "details": {"type": "string"},
            },
            "required": ["order_number", "items", "reason"],
        },
    },
    {
        "name": "request_human_support",
        "description": "Create a help ticket when the customer explicitly requests a person",
    },
    {
        "name": "finish_call",
        "description": "Finish the call when the customer says goodbye or asks to hang up",
    },
]

TOOL_STEPS = {
    "recent_orders": "orders",
    "lookup_order": "order",
    "search_policy": "policy",
    "prepare_refund": "refund",
    "request_human_support": "handoff",
}


class RefundAction(Input):
    type: Literal["refund_action"]
    action: Literal["confirm_refund", "cancel_refund"]
    quote_id: UUID
    confirmed: Literal[True] | None = None

    @model_validator(mode="before")
    @classmethod
    def explicit(cls, value):
        if isinstance(value, dict) and value.get("action") == "confirm_refund":
            if value.get("confirmed") is not True:
                raise ValueError("Explicit refund confirmation is required")
        return value


@asynccontextmanager
async def connect_live(settings: Settings):
    key = settings.gemini_api_key.get_secret_value()
    if not key:
        raise RuntimeError("Voice provider is not configured")
    client = genai.Client(api_key=key, http_options={"api_version": "v1beta"})
    try:
        async with client.aio.live.connect(
            model=settings.gemini_live_model,
            config=types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                system_instruction=PROMPT,
                speech_config={"voice_config": {"prebuilt_voice_config": {"voice_name": "Aoede"}}},
                tools=[{"function_declarations": DECLARATIONS}],
                input_audio_transcription={"language_codes": ["en"]},
                output_audio_transcription={},
                realtime_input_config={
                    "automatic_activity_detection": {"disabled": True},
                    "activity_handling": "START_OF_ACTIVITY_INTERRUPTS",
                },
            ),
        ) as session:
            yield session
    finally:
        await client.aio.aclose()


def get_live_connector() -> Callable:
    return connect_live


def get_voice_embedder(settings: Settings):
    return GeminiEmbedder(settings)


def owned_call(db, conversation_id, customer_id, *, lock=False):
    query = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.customer_id == customer_id,
        Conversation.channel == "call",
    )
    if lock:
        query = query.with_for_update()
    conversation = db.scalar(query)
    if conversation is None:
        raise HTTPException(404, "Conversation not found")
    if conversation.status == "closed":
        raise HTTPException(409, "This conversation is closed")
    return conversation


class VoiceSupport:
    def __init__(self, app, conversation_id, customer_id, actor_id):
        self.app = app
        self.settings = app.state.settings
        self.conversation_id = conversation_id
        self.customer_id = customer_id
        self.actor_id = actor_id
        self.key = f"voice-{uuid4()}"

    def save_transcript(
        self,
        customer: str,
        assistant: str,
        citations: list[Citation],
        protected_quote: UUID | None = None,
    ):
        customer, assistant = customer.strip(), assistant.strip()
        if not (customer or assistant or citations):
            return False
        with Session(self.app.state.engine) as db, db.begin():
            if customer and not is_refund_affirmation(customer):
                statement = update(RefundQuote).where(
                    RefundQuote.conversation_id == self.conversation_id,
                    RefundQuote.customer_id == self.customer_id,
                    RefundQuote.status == "pending",
                )
                if protected_quote:
                    statement = statement.where(RefundQuote.id != protected_quote)
                db.execute(statement.values(status="cancelled"))
            conversation = owned_call(db, self.conversation_id, self.customer_id, lock=True)
            for role, content in (("customer", customer), ("assistant", assistant)):
                if content:
                    db.add(
                        Message(conversation_id=self.conversation_id, role=role, content=content)
                    )
            if citations:
                evidence = "\n".join(
                    f"{item.title} · {item.section} · {item.source_version}\n{item.excerpt}"
                    for item in citations
                )
                db.add(
                    Message(
                        conversation_id=self.conversation_id,
                        role="system",
                        content=f"Verified policy sources consulted\n{evidence}",
                    )
                )
            conversation.updated_at = datetime.now(UTC)
        return True

    def run_refund(self, body: TurnInput, key: str):
        return SupportWorkflow(
            self.app.state.engine,
            self.settings,
            customer_id=self.customer_id,
            actor_id=self.actor_id,
            model=None,
            embedder=None,
            admission=self.app.state.admission,
        ).run(self.conversation_id, body, key)

    def prepare_refund(self, arguments: dict, call_id: str):
        fields = arguments.copy()
        number = fields.pop("order_number", None)
        if not isinstance(number, str) or not re.fullmatch(r"AMZ-[0-9]{4,10}", number):
            raise HTTPException(422, "A valid order number is required.")
        items = fields.get("items")
        if isinstance(items, list):
            # Gemini can encode an integer-valued argument as a JSON float.
            fields["items"] = []
            for item in items:
                quantity = item.get("quantity") if isinstance(item, dict) else None
                if isinstance(quantity, float) and quantity.is_integer():
                    item = {**item, "quantity": int(quantity)}
                fields["items"].append(item)
        with Session(self.app.state.engine) as db:
            owned_call(db, self.conversation_id, self.customer_id)
            tools = SupportTools(db, customer_id=self.customer_id, actor_user_id=self.actor_id)
            order = order_view(tools.lookup_order(number), db)
            selection = EligibilityInput.model_validate({**fields, "order_id": order.id})
        key = f"{self.key}-draft-{sha256(call_id.encode()).hexdigest()[:20]}"
        result = self.run_refund(TurnInput(selection=selection), key)
        items = {item.order_item_id: item.product_name for item in order.items}
        draft = {
            "order_number": order.order_number,
            "reason": selection.reason,
            "items": [
                {"name": items[item.order_item_id], "quantity": item.quantity}
                for item in selection.items
            ],
        }
        data = {"status": result.state, "message": result.text, **draft}
        if result.quote:
            data.update(amount=result.quote.amount, currency=result.quote.currency)
        event = {
            "type": "refund_preview",
            "id": call_id,
            **draft,
            "quote": result.quote.model_dump(mode="json") if result.quote else None,
            "message": result.text,
        }
        return data, event, result.escalation

    def execute(self, name: str, arguments: dict, order_id: UUID | None):
        with Session(self.app.state.engine) as db:
            owned_call(db, self.conversation_id, self.customer_id)
            tools = SupportTools(db, customer_id=self.customer_id, actor_user_id=self.actor_id)
            if name == "recent_orders" and not arguments:
                views = [order_view(order, db) for order in tools.get_customer_orders(limit=5)]
                return {
                    "orders": [
                        {
                            "order_number": view.order_number,
                            "status": view.status,
                            "items": [item.product_name for item in view.items[:10]],
                        }
                        for view in views
                    ]
                }, None
            if name == "lookup_order" and set(arguments) == {"order_number"}:
                number = arguments["order_number"]
                if not isinstance(number, str) or not re.fullmatch(r"AMZ-[0-9]{4,10}", number):
                    return {"error": "Ask for a valid order number."}, None
                order = tools.lookup_order(number)
                view = order_view(order, db)
                payload = view.model_dump(
                    mode="json",
                    include={"order_number", "status", "created_at", "delivered_at", "shipment"},
                )
                if payload["shipment"]:
                    payload["shipment"]["tracking_number"] = payload["shipment"][
                        "tracking_number"
                    ].removeprefix("SYNTHETIC-")
                payload["items"] = [
                    item.model_dump(
                        mode="json",
                        include={
                            "order_item_id",
                            "product_name",
                            "quantity",
                            "remaining_refundable_quantity",
                        },
                    )
                    for item in view.items[:10]
                ]
                return payload, {"type": "order", "order": payload, "order_id": str(order.id)}
            if name == "search_policy" and set(arguments) == {"query"}:
                if not self.settings.policy_search_enabled:
                    return {"error": "Policy search is unavailable. Offer human support."}, None
                query = SearchRequest(query=arguments["query"], limit=2)
                embedder = get_voice_embedder(self.settings)
                try:
                    result = search_policy(
                        db,
                        embedder,
                        query,
                        before_query=lambda: self.app.state.admission.provider(
                            "embedding", self.actor_id
                        ),
                    )
                finally:
                    embedder.close()
                data = result.model_dump(mode="json", include={"status", "message", "citations"})
                return data, {"type": "policy", **data}
            if name == "request_human_support" and not arguments:
                ticket = db.scalar(
                    select(Escalation.id).where(
                        Escalation.customer_id == self.customer_id,
                        Escalation.idempotency_key == self.key,
                    )
                )
                if ticket is None:
                    ticket = tools.create_escalation(
                        EscalationInput(
                            conversation_id=self.conversation_id,
                            order_id=order_id,
                            reason="human_requested",
                            summary="Customer requested human support during a Canopy voice call.",
                        ),
                        self.key,
                    ).id
                return {"status": "ticket_created"}, {"type": "ticket", "id": str(ticket)}
        return {"error": "That action is unavailable."}, None


class VoiceRelay:
    def __init__(self, socket, provider, support):
        self.socket = socket
        self.provider = provider
        self.support = support
        self.customer = ""
        self.assistant = ""
        self.citations: list[Citation] = []
        self.tools = {}
        self.tool_count = 0
        self.tool_lock = asyncio.Lock()
        self.provider_lock = asyncio.Lock()
        self.interrupted = False
        self.order_id: UUID | None = None
        self.active_quote: QuoteView | None = None
        self.protected_quote: UUID | None = None
        self.caller_turn_pending = False
        self.closing = False
        self.closing_audio = False
        self.close_notice_sent = False
        self.closing_requested = asyncio.Event()

    async def flush(self, *, include_assistant=True):
        customer, assistant, citations = self.customer, self.assistant, self.citations
        self.customer, self.assistant, self.citations = "", "", []
        return await run_in_threadpool(
            self.support.save_transcript,
            customer,
            assistant if include_assistant else "",
            citations,
            self.protected_quote,
        )

    async def progress(self, identifier, step, state):
        await self.socket.send_json(
            {"type": "progress", "id": identifier, "step": step, "state": state}
        )

    async def finish_after_reply(self):
        if not self.close_notice_sent:
            self.close_notice_sent = True
            await self.socket.send_json({"type": "end_after_reply"})

    async def closing_deadline(self):
        await self.closing_requested.wait()
        await asyncio.sleep(8)
        await self.finish_after_reply()
        # Give the browser time to play the goodbye and acknowledge the hang-up.
        await asyncio.sleep(20)
        return True

    async def refund_action(self, command, speaking):
        try:
            action = RefundAction.model_validate_json(command)
        except ValidationError:
            await self.socket.send_json(
                {"type": "refund_error", "message": "This refund action is invalid."}
            )
            return
        async with self.tool_lock:
            quote = self.active_quote
            if not quote or action.quote_id != quote.id:
                await self.socket.send_json(
                    {"type": "refund_error", "message": "Ask Canopy for a current refund preview."}
                )
                return
            if speaking or (self.caller_turn_pending and not self.protected_quote):
                await self.socket.send_json(
                    {
                        "type": "refund_error",
                        "message": "Let Canopy finish processing what you said, then try again.",
                    }
                )
                return
            await self.progress(str(quote.id), action.action, "working")
            body = TurnInput(
                action=action.action,
                confirmation=Confirmation(
                    quote_id=quote.id,
                    policy_version=quote.policy_version,
                    confirmed=True,
                )
                if action.action == "confirm_refund"
                else None,
            )
            try:
                result = await run_in_threadpool(
                    self.support.run_refund, body, f"call-{action.action}-{quote.id}"
                )
            except (HTTPException, SQLAlchemyError) as error:
                await self.progress(str(quote.id), action.action, "unavailable")
                await self.socket.send_json(
                    {
                        "type": "refund_error",
                        "message": error.detail
                        if isinstance(error, HTTPException)
                        else "The refund service is temporarily unavailable.",
                    }
                )
                return
            self.active_quote, self.protected_quote = None, None
            await self.progress(str(quote.id), action.action, "done")
            await self.socket.send_json(
                {"type": "refund_result", "state": result.state, "refund": result.refund}
            )
            async with self.provider_lock:
                await self.provider.send_client_content(
                    turns=types.Content(
                        role="user",
                        parts=[
                            types.Part(
                                text=(
                                    "Verified on-screen action. Acknowledge in English. "
                                    f"Do not call tools. {result.text}"
                                ),
                            )
                        ],
                    ),
                    turn_complete=True,
                )

    async def browser_input(self):
        muted = False
        speaking = False
        credit = 50.0
        last_frame = monotonic()
        while True:
            incoming = await self.socket.receive()
            if incoming["type"] == "websocket.disconnect":
                return
            audio = incoming.get("bytes")
            command = incoming.get("text")
            if audio is not None and len(audio) == AUDIO_BYTES:
                now = monotonic()
                credit = min(50.0, credit + (now - last_frame) * 55) - 1
                last_frame = now
                if credit < 0:
                    await self.socket.close(code=1008)
                    return
                if speaking:
                    async with self.provider_lock:
                        await self.provider.send_realtime_input(
                            audio=types.Blob(data=audio, mime_type="audio/pcm;rate=16000")
                        )
            elif command == "speech_start":
                if not muted and not speaking:
                    speaking = True
                    self.protected_quote = None
                    self.caller_turn_pending = True
                    async with self.provider_lock:
                        await self.provider.send_realtime_input(
                            activity_start=types.ActivityStart()
                        )
            elif command in {"speech_end", "mute"}:
                if command == "mute":
                    muted = True
                if speaking:
                    speaking = False
                    async with self.provider_lock:
                        await self.provider.send_realtime_input(activity_end=types.ActivityEnd())
            elif command == "unmute":
                muted = False
            elif command == "end":
                return True
            elif command and command.startswith("{"):
                await self.refund_action(command, speaking)
            else:
                await self.socket.close(code=1003)
                return

    async def run_tool(self, call):
        try:
            async with self.tool_lock:
                # A single session's tools apply order context in provider order.
                step = TOOL_STEPS.get(call.name)
                if step:
                    await self.progress(call.id, step, "working")
                try:
                    if call.name == "finish_call" and not call.args:
                        self.closing = True
                        self.closing_requested.set()
                        data, event = (
                            {"status": "closing", "instruction": "Say a brief goodbye."},
                            None,
                        )
                    elif call.name == "prepare_refund":
                        data, event, ticket = await run_in_threadpool(
                            self.support.prepare_refund, call.args or {}, call.id
                        )
                        self.active_quote = (
                            QuoteView.model_validate(event["quote"]) if event["quote"] else None
                        )
                        self.protected_quote = self.active_quote.id if self.active_quote else None
                        if ticket:
                            data["ticket_created"] = True
                            await self.socket.send_json(
                                {"type": "ticket", "id": ticket["id"], "routed": True}
                            )
                            await self.progress(call.id, "handoff", "done")
                    else:
                        data, event = await run_in_threadpool(
                            self.support.execute, call.name, call.args or {}, self.order_id
                        )
                except HTTPException as error:
                    data, event = (
                        {
                            "error": "Order not found for this customer."
                            if error.status_code == 404
                            else "The request could not be completed. Offer human support."
                        },
                        None,
                    )
                except (SQLAlchemyError, CorpusInvalid, EmbeddingUnavailable, ValidationError):
                    data, event = {"error": "Data is unavailable. Offer human support."}, None
                if event:
                    if event["type"] == "order":
                        self.order_id = UUID(event.pop("order_id"))
                    if event["type"] == "policy":
                        self.citations = [
                            Citation.model_validate(item) for item in event["citations"]
                        ]
                    await self.socket.send_json(event)
                if step:
                    await self.progress(call.id, step, "unavailable" if "error" in data else "done")
                async with self.provider_lock:
                    await self.provider.send_tool_response(
                        function_responses=types.FunctionResponse(
                            id=call.id, name=call.name, response=data
                        )
                    )
        finally:
            if self.tools.get(call.id) is asyncio.current_task():
                self.tools.pop(call.id)

    async def model_output(self):
        async with asyncio.TaskGroup() as tasks:
            try:
                while True:
                    async for reply in self.provider.receive():
                        await self.forward(reply, tasks)
            finally:
                for task in self.tools.values():
                    task.cancel()

    async def forward(self, reply, tasks):
        if reply.tool_call_cancellation:
            for call_id in reply.tool_call_cancellation.ids or []:
                if task := self.tools.pop(call_id, None):
                    task.cancel()
        if reply.tool_call:
            for call in reply.tool_call.function_calls or []:
                if not call.id or call.id in self.tools:
                    continue
                self.tool_count += 1
                if self.tool_count > 32 or len(self.tools) >= 4:
                    raise RuntimeError("Voice tool limit reached")
                self.tools[call.id] = tasks.create_task(self.run_tool(call))
        content = reply.server_content
        if not content:
            return
        if content.interrupted:
            self.interrupted = True
            await self.socket.send_json({"type": "interrupted"})
            for task in self.tools.values():
                task.cancel()
            self.tools.clear()
        if content.input_transcription and content.input_transcription.text:
            self.customer = (self.customer + content.input_transcription.text)[:TRANSCRIPT_LIMIT]
            await self.socket.send_json(
                {"type": "caption", "role": "customer", "text": self.customer}
            )
        if not self.interrupted:
            if content.output_transcription and content.output_transcription.text:
                self.assistant = (self.assistant + content.output_transcription.text)[
                    :TRANSCRIPT_LIMIT
                ]
                await self.socket.send_json(
                    {"type": "caption", "role": "assistant", "text": self.assistant}
                )
            if content.model_turn:
                for part in content.model_turn.parts or []:
                    blob = part.inline_data
                    if (
                        not part.thought
                        and blob
                        and blob.data
                        and (blob.mime_type or "").startswith("audio/pcm")
                    ):
                        await self.socket.send_bytes(blob.data)
                        if self.closing:
                            self.closing_audio = True
        if content.turn_complete:
            invalidated = bool(
                self.active_quote
                and self.active_quote.id != self.protected_quote
                and self.customer.strip()
                and not is_refund_affirmation(self.customer.strip())
            )
            saved = await self.flush(include_assistant=not self.interrupted)
            if invalidated:
                self.active_quote = None
                await self.socket.send_json({"type": "refund_invalidated"})
            if not self.interrupted:
                self.caller_turn_pending = False
            self.interrupted = False
            await self.socket.send_json({"type": "turn_complete", "saved": saved})
            if self.closing_audio:
                await self.finish_after_reply()

    async def run(self):
        await self.provider.send_client_content(
            turns=types.Content(
                role="user",
                parts=[types.Part(text="The call has connected. Greet the customer in English.")],
            ),
            turn_complete=True,
        )
        await self.socket.send_json({"type": "ready"})
        pumps = [
            asyncio.create_task(self.browser_input()),
            asyncio.create_task(self.model_output()),
            asyncio.create_task(self.closing_deadline()),
        ]
        hung_up = False
        try:
            done, _ = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                hung_up |= task.result() is True
        finally:
            for task in pumps:
                task.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            # Persist received customer speech on hang-up without saving unheard generated audio.
            with suppress(HTTPException, SQLAlchemyError):
                await self.flush(include_assistant=False)
        if hung_up:
            await self.socket.send_json({"type": "ended", "message": "Call ended."})


@router.get("/voice")
def voice_availability(request: Request, _customer: Customer = Depends(current_customer)):
    return {"enabled": request.app.state.settings.voice_enabled}


def admit_call(socket, auth, conversation_id):
    token = socket.cookies.get("canopy_access")
    if not token:
        raise HTTPException(401, "Sign in to start a call.")
    principal = auth.verify(token)
    if principal.role != "customer":
        raise HTTPException(403, "This call is for customer accounts.")
    app = socket.app
    with Session(app.state.engine) as db:
        customer_id = db.scalar(select(Customer.id).where(Customer.auth_user_id == principal.id))
        if customer_id is None:
            raise HTTPException(403, "No customer profile is linked to this account.")
        owned_call(db, conversation_id, customer_id)
    app.state.admission.take(
        Limit("voice.actor", str(principal.id), 3),
        Limit("voice.actor.day", str(principal.id), 12, DAY),
        Limit("gemini.live", "", app.state.settings.gemini_live_calls_per_day, DAY),
    )
    return VoiceSupport(app, conversation_id, customer_id, principal.id)


@router.websocket("/conversations/{conversation_id}/voice")
async def voice_call(
    socket: WebSocket,
    conversation_id: UUID,
    auth: SupabaseAuth = Depends(get_auth),
    connector: Callable = Depends(get_live_connector),
):
    settings = socket.app.state.settings
    if socket.headers.get("origin") not in settings.allowed_origins or not settings.voice_enabled:
        await socket.close(code=1008)
        return
    try:
        support = await run_in_threadpool(admit_call, socket, auth, conversation_id)
    except (HTTPException, SQLAlchemyError) as error:
        if isinstance(error, HTTPException) and error.status_code == 429:
            await socket.accept()
            await socket.send_json({"type": "error", "message": "Voice call limit reached."})
        await socket.close(code=1008)
        return
    await socket.accept()
    try:
        async with asyncio.timeout(settings.voice_max_call_seconds):
            async with connector(settings) as provider:
                await VoiceRelay(socket, provider, support).run()
        with suppress(RuntimeError, WebSocketDisconnect):
            await socket.close()
    except WebSocketDisconnect:
        pass
    except TimeoutError:
        with suppress(RuntimeError, WebSocketDisconnect):
            await socket.send_json(
                {"type": "ended", "message": "This call reached its time limit."}
            )
            await socket.close()
    except Exception:
        # Provider errors can include API and user input details.
        with suppress(RuntimeError, WebSocketDisconnect):
            await socket.send_json(
                {
                    "type": "error",
                    "message": "The voice connection ended. You can call again or type.",
                }
            )
            await socket.close(code=1011)
