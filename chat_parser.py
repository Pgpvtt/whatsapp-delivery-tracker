"""
WhatsApp Chat Parser
Handles Android and iOS export formats, multi-line messages, system messages.
"""
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# Android: [12/03/2024, 09:45:32] Name: message
# iOS:     [12/03/2024 09:45:32] Name: message
# Also handles: 12/03/2024, 09:45 - Name: message  (no-second format)
PATTERNS = [
    re.compile(r'^\[(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}),?\s+(\d{1,2}:\d{2}(?::\d{2})?(?:\s?[APap][Mm])?)\]\s+([^:]+?):\s(.*)$'),
    re.compile(r'^(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}),\s+(\d{1,2}:\d{2}(?::\d{2})?(?:\s?[APap][Mm])?)\s+-\s+([^:]+?):\s(.*)$'),
]

SYSTEM_PATTERNS = [
    re.compile(r'messages (to this group are now|and calls are end-to-end)', re.I),
    re.compile(r'created (group|this group)', re.I),
    re.compile(r'added .+', re.I),
    re.compile(r'left$', re.I),
    re.compile(r'removed .+', re.I),
    re.compile(r'changed (the subject|this group)', re.I),
    re.compile(r'security code (changed|with)', re.I),
    re.compile(r'<Media omitted>', re.I),
    re.compile(r'This message was deleted', re.I),
    re.compile(r'image omitted', re.I),
    re.compile(r'video omitted', re.I),
    re.compile(r'document omitted', re.I),
    re.compile(r'audio omitted', re.I),
    re.compile(r'sticker omitted', re.I),
    re.compile(r'GIF omitted', re.I),
    re.compile(r'Contact card omitted', re.I),
    re.compile(r'null$', re.I),
    re.compile(r'Your security code with', re.I),
    re.compile(r'You were added', re.I),
    re.compile(r'Missed voice call', re.I),
    re.compile(r'Missed video call', re.I),
]

DATE_FORMATS = [
    '%d/%m/%Y', '%m/%d/%Y', '%d/%m/%y', '%m/%d/%y',
    '%d-%m-%Y', '%m-%d-%Y', '%d-%m-%y', '%m-%d-%y',
    '%Y/%m/%d', '%Y-%m-%d',
]
TIME_FORMATS = [
    '%H:%M:%S', '%H:%M', '%I:%M:%S %p', '%I:%M %p',
    '%I:%M:%S %P', '%I:%M %P',
]


@dataclass
class Message:
    timestamp: datetime
    sender: str
    text: str
    raw: str

    def __repr__(self):
        return f"[{self.timestamp}] {self.sender}: {self.text[:60]}"


def _parse_datetime(date_str: str, time_str: str) -> Optional[datetime]:
    date_str = date_str.strip()
    time_str = time_str.strip()
    for df in DATE_FORMATS:
        for tf in TIME_FORMATS:
            try:
                return datetime.strptime(f"{date_str} {time_str}", f"{df} {tf}")
            except ValueError:
                continue
    return None


def _is_system_message(sender: str, text: str) -> bool:
    combined = f"{sender}: {text}"
    for pat in SYSTEM_PATTERNS:
        if pat.search(text) or pat.search(combined):
            return True
    return False


def parse_chat(raw_text: str) -> list:
    lines = raw_text.splitlines()
    messages = []
    current = None

    for line in lines:
        matched = False
        for pat in PATTERNS:
            m = pat.match(line)
            if m:
                if current:
                    messages.append(current)
                date_s, time_s, sender, text = m.group(1), m.group(2), m.group(3).strip(), m.group(4).strip()
                ts = _parse_datetime(date_s, time_s)
                if ts and not _is_system_message(sender, text):
                    current = Message(timestamp=ts, sender=sender, text=text, raw=line)
                else:
                    current = None
                matched = True
                break

        if not matched and current:
            current.text += ' ' + line.strip()
            current.raw += '\n' + line

    if current:
        messages.append(current)

    return messages
