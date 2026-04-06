import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from chat_parser import Message

# ================== REGEX ==================
RE_ROUTE_START = re.compile(r'route\s*(\d+)\s*start', re.I)
RE_ROUTE_END = re.compile(r'route\s*(\d+)\s*end', re.I)
RE_POD = re.compile(r'^pod$', re.I)
RE_CLOSED = re.compile(r'^closed$', re.I)

# ================== THRESHOLDS ==================
DELAY_THRESHOLD = 30
TRAVEL_THRESHOLD = 60


# ================== DATA CLASSES ==================
@dataclass
class Delivery:
    delivery_boy: str
    date: str
    route: int
    store: str
    start_time: Optional[datetime]
    arrival_time: Optional[datetime]
    pod_time: Optional[datetime]

    travel_time: Optional[float] = None
    store_time: Optional[float] = None

    delayed: bool = False
    high_travel: bool = False
    missing_pod: bool = False

    def finalize(self):
        if self.start_time and self.arrival_time:
            t = (self.arrival_time - self.start_time).total_seconds() / 60
            self.travel_time = round(t, 1)
            self.high_travel = t > TRAVEL_THRESHOLD

        if self.arrival_time and self.pod_time:
            s = (self.pod_time - self.arrival_time).total_seconds() / 60
            self.store_time = round(s, 1)
            self.delayed = s > DELAY_THRESHOLD
        else:
            self.missing_pod = True


@dataclass
class BoyState:
    name: str
    current_route: Optional[int] = None
    last_exit_time: Optional[datetime] = None
    pending_store: Optional[Delivery] = None

    deliveries: list = field(default_factory=list)


# ================== MAIN ==================
def process_messages(messages):
    states = {}

    for msg in messages:
        name = msg.sender
        ts = msg.timestamp
        text = msg.text.strip().lower()
        date = ts.strftime('%Y-%m-%d')

        if name not in states:
            states[name] = BoyState(name)

        s = states[name]

        # ROUTE START
        m = RE_ROUTE_START.match(text)
        if m:
            s.current_route = int(m.group(1))
            s.last_exit_time = ts
            continue

        # ROUTE END
        if RE_ROUTE_END.match(text):
            s.current_route = None
            continue

        # POD / CLOSED
        if RE_POD.match(text) or RE_CLOSED.match(text):
            if s.pending_store:
                s.pending_store.pod_time = ts
                s.pending_store.finalize()
                s.deliveries.append(s.pending_store)
                s.last_exit_time = ts
                s.pending_store = None
            continue

        # STORE
        if s.current_route:
            if s.pending_store:
                s.pending_store.missing_pod = True
                s.pending_store.finalize()
                s.deliveries.append(s.pending_store)

            s.pending_store = Delivery(
                delivery_boy=name,
                date=date,
                route=s.current_route,
                store=text,
                start_time=s.last_exit_time,
                arrival_time=ts,
                pod_time=None
            )

    return states


# ================== BUILDERS ==================
def build_delivery_details(states):
    rows = []

    for name, s in states.items():
        for d in s.deliveries:
            rows.append({
                "Delivery Boy": name,
                "Date": d.date,
                "Route No.": d.route,
                "Store Name": d.store,
                "Start Time": d.start_time.strftime("%H:%M") if d.start_time else "",
                "Store Arrival": d.arrival_time.strftime("%H:%M") if d.arrival_time else "",
                "POD Time": d.pod_time.strftime("%H:%M") if d.pod_time else "",
                "Travel Time (mins)": d.travel_time,
                "Store Time (mins)": d.store_time,
                "Status": (
                    "Delayed" if d.delayed else
                    "High Travel" if d.high_travel else
                    "Missing POD" if d.missing_pod else
                    "OK"
                )
            })

    return rows


def build_store_search(states):
    return build_delivery_details(states)


def build_route_summary(states):
    return []


def build_exceptions(states):
    return []


def build_delivery_summary(states):
    return []
