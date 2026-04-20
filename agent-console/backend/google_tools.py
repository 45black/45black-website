"""Direct Google Workspace tools — Gmail + Calendar, no MCP layer.

Uses ``google-api-python-client`` with a user OAuth2 refresh token to
call the Gmail and Calendar APIs. The refresh token is loaded from:

* **Cloud Run**: Secret Manager (secret ``gmail-oauth-refresh-token``)
* **Local**: ``GMAIL_OAUTH_REFRESH_TOKEN`` in ``.env``

This module returns two things the harness uses:

1. A list of Gemini ``FunctionDeclaration`` dicts (for the tool config).
2. A dispatcher function that takes (tool_name, args) and executes.

Scopes required (set during the OAuth consent flow):
    - https://www.googleapis.com/auth/gmail.modify
    - https://www.googleapis.com/auth/calendar
"""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
]


# --- Credential loading ---------------------------------------------------


def _load_refresh_token() -> str:
    """Get the OAuth refresh token from Secret Manager or env."""
    token = os.environ.get("GMAIL_OAUTH_REFRESH_TOKEN")
    if token:
        return token

    # Try Secret Manager on GCP.
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if project:
        try:
            from google.cloud import secretmanager

            client = secretmanager.SecretManagerServiceClient()
            name = f"projects/{project}/secrets/gmail-oauth-refresh-token/versions/latest"
            resp = client.access_secret_version(request={"name": name})
            return resp.payload.data.decode("utf-8").strip()
        except Exception as exc:
            log.warning("Secret Manager fetch failed: %s", exc)

    raise RuntimeError(
        "No OAuth refresh token. Run `python scripts/oauth_setup.py` first."
    )


def _build_creds() -> Credentials:
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    refresh_token = _load_refresh_token()

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return creds


def _gmail():
    return build("gmail", "v1", credentials=_build_creds(), cache_discovery=False)


def _calendar():
    return build("calendar", "v3", credentials=_build_creds(), cache_discovery=False)


# --- Gmail tools -----------------------------------------------------------


def gmail_list_messages(
    query: str = "is:unread", max_results: int = 20
) -> dict[str, Any]:
    """List messages matching a Gmail search query."""
    svc = _gmail()
    result = svc.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()
    messages = result.get("messages", [])

    out = []
    for msg_stub in messages:
        msg = svc.users().messages().get(
            userId="me", id=msg_stub["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        out.append({
            "id": msg["id"],
            "thread_id": msg["threadId"],
            "from": headers.get("From", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
            "snippet": msg.get("snippet", ""),
            "labels": msg.get("labelIds", []),
        })
    return {"messages": out, "count": len(out)}


def gmail_get_message(message_id: str) -> dict[str, Any]:
    """Get the full text of a single message."""
    svc = _gmail()
    msg = svc.users().messages().get(userId="me", id=message_id, format="full").execute()
    headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}

    body = ""
    payload = msg.get("payload", {})
    if payload.get("body", {}).get("data"):
        body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
    else:
        for part in payload.get("parts", []):
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
                break

    return {
        "id": msg["id"],
        "thread_id": msg["threadId"],
        "from": headers.get("From", ""),
        "to": headers.get("To", ""),
        "subject": headers.get("Subject", ""),
        "date": headers.get("Date", ""),
        "body": body[:8000],
        "labels": msg.get("labelIds", []),
    }


def gmail_create_draft(to: str, subject: str, body: str, thread_id: str = "") -> dict[str, Any]:
    """Create a draft reply. Never sends — always saves as draft for Jon to review."""
    svc = _gmail()
    message = MIMEText(body)
    message["to"] = to
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    draft_body: dict[str, Any] = {"message": {"raw": raw}}
    if thread_id:
        draft_body["message"]["threadId"] = thread_id

    draft = svc.users().drafts().create(userId="me", body=draft_body).execute()
    return {"draft_id": draft["id"], "message_id": draft["message"]["id"]}


def gmail_modify_labels(
    message_id: str, add_labels: list[str] | None = None, remove_labels: list[str] | None = None
) -> dict[str, Any]:
    """Add or remove labels from a message."""
    svc = _gmail()
    body: dict[str, Any] = {}
    if add_labels:
        body["addLabelIds"] = add_labels
    if remove_labels:
        body["removeLabelIds"] = remove_labels
    msg = svc.users().messages().modify(userId="me", id=message_id, body=body).execute()
    return {"id": msg["id"], "labels": msg.get("labelIds", [])}


# --- Calendar tools --------------------------------------------------------


def calendar_list_events(
    time_min: str = "", time_max: str = "", max_results: int = 20, calendar_id: str = "primary"
) -> dict[str, Any]:
    """List upcoming events. time_min/max are RFC3339 strings."""
    svc = _calendar()
    if not time_min:
        time_min = datetime.now(timezone.utc).isoformat()
    kwargs: dict[str, Any] = {
        "calendarId": calendar_id,
        "timeMin": time_min,
        "maxResults": max_results,
        "singleEvents": True,
        "orderBy": "startTime",
    }
    if time_max:
        kwargs["timeMax"] = time_max
    result = svc.events().list(**kwargs).execute()
    events = result.get("items", [])
    return {
        "events": [
            {
                "id": e["id"],
                "summary": e.get("summary", "(no title)"),
                "start": e.get("start", {}).get("dateTime", e.get("start", {}).get("date", "")),
                "end": e.get("end", {}).get("dateTime", e.get("end", {}).get("date", "")),
                "location": e.get("location", ""),
                "attendees": [a.get("email", "") for a in e.get("attendees", [])],
                "status": e.get("status", ""),
            }
            for e in events
        ],
        "count": len(events),
    }


def calendar_create_event(
    summary: str,
    start: str,
    end: str,
    description: str = "",
    attendees: list[str] | None = None,
    calendar_id: str = "primary",
) -> dict[str, Any]:
    """Create a calendar event. start/end are RFC3339 datetime strings."""
    svc = _calendar()
    body: dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": start, "timeZone": "Europe/London"},
        "end": {"dateTime": end, "timeZone": "Europe/London"},
    }
    if description:
        body["description"] = description
    if attendees:
        body["attendees"] = [{"email": e} for e in attendees]
    event = svc.events().insert(calendarId=calendar_id, body=body).execute()
    return {"id": event["id"], "link": event.get("htmlLink", "")}


def calendar_update_event(
    event_id: str,
    summary: str = "",
    start: str = "",
    end: str = "",
    description: str = "",
    calendar_id: str = "primary",
) -> dict[str, Any]:
    """Update fields on an existing event. Only non-empty fields are changed."""
    svc = _calendar()
    existing = svc.events().get(calendarId=calendar_id, eventId=event_id).execute()
    if summary:
        existing["summary"] = summary
    if start:
        existing["start"] = {"dateTime": start, "timeZone": "Europe/London"}
    if end:
        existing["end"] = {"dateTime": end, "timeZone": "Europe/London"}
    if description:
        existing["description"] = description
    updated = svc.events().update(calendarId=calendar_id, eventId=event_id, body=existing).execute()
    return {"id": updated["id"], "link": updated.get("htmlLink", "")}


def calendar_freebusy(
    time_min: str, time_max: str, calendar_id: str = "primary"
) -> dict[str, Any]:
    """Check free/busy status for a time range."""
    svc = _calendar()
    body = {
        "timeMin": time_min,
        "timeMax": time_max,
        "items": [{"id": calendar_id}],
    }
    result = svc.freebusy().query(body=body).execute()
    busy = result.get("calendars", {}).get(calendar_id, {}).get("busy", [])
    return {"busy_slots": busy}


# --- Tool registry ---------------------------------------------------------

TOOLS: dict[str, Any] = {
    "gmail_list_messages": gmail_list_messages,
    "gmail_get_message": gmail_get_message,
    "gmail_create_draft": gmail_create_draft,
    "gmail_modify_labels": gmail_modify_labels,
    "calendar_list_events": calendar_list_events,
    "calendar_create_event": calendar_create_event,
    "calendar_update_event": calendar_update_event,
    "calendar_freebusy": calendar_freebusy,
}

DECLARATIONS: list[dict[str, Any]] = [
    {
        "name": "gmail_list_messages",
        "description": "List Gmail messages matching a search query. Returns id, from, subject, snippet, labels.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Gmail search query, e.g. 'is:unread' or 'from:client@example.com'"},
                "max_results": {"type": "integer", "description": "Max messages to return (default 20)"},
            },
        },
    },
    {
        "name": "gmail_get_message",
        "description": "Get the full text body of a single Gmail message by ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "Gmail message ID"},
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "gmail_create_draft",
        "description": "Create a draft email reply. NEVER sends — saves as a draft for Jon to review and send manually.",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject line"},
                "body": {"type": "string", "description": "Plain text email body"},
                "thread_id": {"type": "string", "description": "Thread ID to reply to (optional)"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "gmail_modify_labels",
        "description": "Add or remove labels from a Gmail message. Use to classify, archive, or flag messages.",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "Gmail message ID"},
                "add_labels": {"type": "array", "items": {"type": "string"}, "description": "Label IDs to add"},
                "remove_labels": {"type": "array", "items": {"type": "string"}, "description": "Label IDs to remove"},
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "calendar_list_events",
        "description": "List upcoming calendar events. Returns summary, start/end times, location, attendees.",
        "parameters": {
            "type": "object",
            "properties": {
                "time_min": {"type": "string", "description": "Start of range (RFC3339, e.g. '2026-04-21T00:00:00Z'). Defaults to now."},
                "time_max": {"type": "string", "description": "End of range (RFC3339). Optional."},
                "max_results": {"type": "integer", "description": "Max events to return (default 20)"},
                "calendar_id": {"type": "string", "description": "Calendar ID (default 'primary')"},
            },
        },
    },
    {
        "name": "calendar_create_event",
        "description": "Create a new calendar event with summary, time range, optional description and attendees.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Event title"},
                "start": {"type": "string", "description": "Start datetime (RFC3339)"},
                "end": {"type": "string", "description": "End datetime (RFC3339)"},
                "description": {"type": "string", "description": "Event description (optional)"},
                "attendees": {"type": "array", "items": {"type": "string"}, "description": "Attendee email addresses (optional)"},
                "calendar_id": {"type": "string", "description": "Calendar ID (default 'primary')"},
            },
            "required": ["summary", "start", "end"],
        },
    },
    {
        "name": "calendar_update_event",
        "description": "Update an existing calendar event. Only non-empty fields are changed.",
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Calendar event ID"},
                "summary": {"type": "string", "description": "New title (optional)"},
                "start": {"type": "string", "description": "New start (RFC3339, optional)"},
                "end": {"type": "string", "description": "New end (RFC3339, optional)"},
                "description": {"type": "string", "description": "New description (optional)"},
                "calendar_id": {"type": "string", "description": "Calendar ID (default 'primary')"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "calendar_freebusy",
        "description": "Check free/busy status for a time range. Returns busy slots.",
        "parameters": {
            "type": "object",
            "properties": {
                "time_min": {"type": "string", "description": "Start (RFC3339)"},
                "time_max": {"type": "string", "description": "End (RFC3339)"},
                "calendar_id": {"type": "string", "description": "Calendar ID (default 'primary')"},
            },
            "required": ["time_min", "time_max"],
        },
    },
]


def dispatch(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Execute a Google Workspace tool call. Returns the result dict."""
    fn = TOOLS.get(name)
    if not fn:
        return {"error": f"unknown google tool: {name}"}
    try:
        return fn(**args)
    except Exception as exc:
        log.error("Google tool %s failed: %s", name, exc)
        return {"error": str(exc)}


def available() -> bool:
    """Quick check: can we build credentials?"""
    try:
        _load_refresh_token()
        return True
    except RuntimeError:
        return False
