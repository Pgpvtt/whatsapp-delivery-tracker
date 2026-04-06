"""
Delivery Tracking Engine
State machine per delivery boy. Processes message sequence into structured records.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from chat_parser import Message

# ── Pattern matchers ──────────────────────────────────────────────────────────
RE_ROUTE_START = re.compile(r'route\s*(\d+)\s*start', re.I)
RE_ROUTE_END   = re.compile(r'route\s*(\d+)\s*end',   re.I)
RE_POD         = re.compile(r'^pod\s*$',               re.I)
RE_BREAK_START = re.compile(r'break\s*start',          re.I)
RE_BREAK_END   = re.compile(r'break\s*end',            re.I)

DELAY_THRESHOLD_MINS  = 30   # flag if POD > 30 min after store arrival
GAP_THRESHOLD_MINS    = 60   # flag if no activity for > 60 min
TRAVEL_THRESHOLD_MINS = 60   # flag if inter-delivery time > 60 min


@dataclass
class DeliveryRecord:
    """One store visit + its POD."""
    delivery_boy: str
    date: str
    route_number: int
    store_name: str
    store_time: Optional[datetime]
    pod_time: Optional[datetime]
    time_spent_mins: Optional[float] = None
    is_delayed: bool = False
    missing_pod: bool = False
    inter_delivery_mins: Optional[float] = None
    high_travel: bool = False

    def finalize(self):
        if self.store_time and self.pod_time:
            delta = (self.pod_time - self.store_time).total_seconds() / 60
            self.time_spent_mins = round(delta, 1)
            self.is_delayed = delta > DELAY_THRESHOLD_MINS
        else:
            self.missing_pod = True


@dataclass
class RouteRecord:
    delivery_boy: str
    date: str
    route_number: int
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    deliveries: int = 0
    not_ended: bool = False

    @property
    def total_mins(self) -> Optional[float]:
        if self.start_time and self.end_time:
            return round((self.end_time - self.start_time).total_seconds() / 60, 1)
        return None

    @property
    def avg_delivery_mins(self) -> Optional[float]:
        if self.total_mins and self.deliveries > 0:
            return round(self.total_mins / self.deliveries, 1)
        return None


@dataclass
class BreakRecord:
    delivery_boy: str
    date: str
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    not_ended: bool = False

    @property
    def duration_mins(self) -> Optional[float]:
        if self.start_time and self.end_time:
            return round((self.end_time - self.start_time).total_seconds() / 60, 1)
        return None


@dataclass
class Exception_:
    delivery_boy: str
    date: str
    timestamp: Optional[datetime]
    kind: str
    detail: str


@dataclass
class BoyState:
    """Mutable per-person state machine."""
    name: str
    current_route: Optional[RouteRecord] = None
    pending_store: Optional[DeliveryRecord] = None
    current_break: Optional[BreakRecord] = None
    last_message_time: Optional[datetime] = None
    last_pod_or_start: Optional[datetime] = None

    deliveries: list = field(default_factory=list)
    routes: list = field(default_factory=list)
    breaks: list = field(default_factory=list)
    exceptions: list = field(default_factory=list)


# ── Engine ────────────────────────────────────────────────────────────────────

def _date_str(ts: datetime) -> str:
    return ts.strftime('%Y-%m-%d')


def _check_gap(state: BoyState, ts: datetime):
    if state.last_message_time:
        gap = (ts - state.last_message_time).total_seconds() / 60
        if gap > GAP_THRESHOLD_MINS:
            state.exceptions.append(Exception_(
                delivery_boy=state.name,
                date=_date_str(ts),
                timestamp=ts,
                kind='No Activity Gap',
                detail=f'{round(gap)} min gap between {state.last_message_time.strftime("%H:%M")} and {ts.strftime("%H:%M")}'
            ))


def process_messages(messages: list) -> dict:
    states: dict = {}

    for msg in messages:
        name = msg.sender
        ts   = msg.timestamp
        text = msg.text.strip()
        date = _date_str(ts)

        if name not in states:
            states[name] = BoyState(name=name)
        s = states[name]

        _check_gap(s, ts)
        s.last_message_time = ts

        # ── Route Start ──────────────────────────────────────────────────────
        m = RE_ROUTE_START.match(text)
        if m:
            route_num = int(m.group(1))
            if s.current_route and not s.current_route.end_time:
                s.exceptions.append(Exception_(
                    delivery_boy=name, date=date, timestamp=ts,
                    kind='Route Not Ended',
                    detail=f'Route {s.current_route.route_number} started but not ended before Route {route_num} start'
                ))
                s.current_route.not_ended = True
                s.routes.append(s.current_route)
            s.current_route = RouteRecord(
                delivery_boy=name, date=date,
                route_number=route_num, start_time=ts
            )
            s.last_pod_or_start = ts
            if s.pending_store:
                s.pending_store.missing_pod = True
                s.pending_store.finalize()
                s.deliveries.append(s.pending_store)
                s.exceptions.append(Exception_(
                    delivery_boy=name, date=date, timestamp=ts,
                    kind='Missing POD',
                    detail=f'No POD after store "{s.pending_store.store_name}"'
                ))
                s.pending_store = None
            continue

        # ── Route End ────────────────────────────────────────────────────────
        m = RE_ROUTE_END.match(text)
        if m:
            route_num = int(m.group(1))
            if s.pending_store:
                s.pending_store.missing_pod = True
                s.pending_store.finalize()
                s.deliveries.append(s.pending_store)
                s.exceptions.append(Exception_(
                    delivery_boy=name, date=date, timestamp=ts,
                    kind='Missing POD',
                    detail=f'No POD after store "{s.pending_store.store_name}"'
                ))
                s.pending_store = None
            if s.current_route and s.current_route.route_number == route_num:
                s.current_route.end_time = ts
                s.routes.append(s.current_route)
                s.current_route = None
            else:
                s.exceptions.append(Exception_(
                    delivery_boy=name, date=date, timestamp=ts,
                    kind='Route End Without Start',
                    detail=f'Route {route_num} ended without a matching start'
                ))
            continue

        # ── Break Start ──────────────────────────────────────────────────────
        if RE_BREAK_START.match(text):
            s.current_break = BreakRecord(delivery_boy=name, date=date, start_time=ts)
            continue

        # ── Break End ────────────────────────────────────────────────────────
        if RE_BREAK_END.match(text):
            if s.current_break:
                s.current_break.end_time = ts
                s.breaks.append(s.current_break)
                s.current_break = None
            else:
                s.exceptions.append(Exception_(
                    delivery_boy=name, date=date, timestamp=ts,
                    kind='Break End Without Start',
                    detail='Break End logged without a preceding Break Start'
                ))
            continue

        # ── POD ──────────────────────────────────────────────────────────────
        if RE_POD.match(text):
            if s.pending_store:
                s.pending_store.pod_time = ts
                s.pending_store.finalize()
                if s.pending_store.is_delayed:
                    s.exceptions.append(Exception_(
                        delivery_boy=name, date=date, timestamp=ts,
                        kind='Long Delay',
                        detail=f'POD for "{s.pending_store.store_name}" took {s.pending_store.time_spent_mins} mins (>{DELAY_THRESHOLD_MINS} min threshold)'
                    ))
                if s.pending_store.inter_delivery_mins is not None and s.pending_store.high_travel:
                    s.exceptions.append(Exception_(
                        delivery_boy=name, date=date, timestamp=ts,
                        kind='High Travel Time',
                        detail=f'Travel to "{s.pending_store.store_name}" took {s.pending_store.inter_delivery_mins} mins (>{TRAVEL_THRESHOLD_MINS} min threshold)'
                    ))
                s.deliveries.append(s.pending_store)
                if s.current_route:
                    s.current_route.deliveries += 1
                s.last_pod_or_start = ts
                s.pending_store = None
            else:
                s.exceptions.append(Exception_(
                    delivery_boy=name, date=date, timestamp=ts,
                    kind='POD Without Store',
                    detail='POD logged without a preceding store visit'
                ))
            continue

        # ── Store Visit (anything else while a route is active) ───────────────
        if s.current_route:
            if s.pending_store:
                s.pending_store.missing_pod = True
                s.pending_store.finalize()
                s.deliveries.append(s.pending_store)
                s.exceptions.append(Exception_(
                    delivery_boy=name, date=date, timestamp=ts,
                    kind='Missing POD',
                    detail=f'No POD after store "{s.pending_store.store_name}"'
                ))
            inter_mins = None
            high_travel = False
            if s.last_pod_or_start and ts:
                inter_mins = round((ts - s.last_pod_or_start).total_seconds() / 60, 1)
                high_travel = inter_mins > TRAVEL_THRESHOLD_MINS
            s.pending_store = DeliveryRecord(
                delivery_boy=name,
                date=date,
                route_number=s.current_route.route_number,
                store_name=text,
                store_time=ts,
                pod_time=None,
                inter_delivery_mins=inter_mins,
                high_travel=high_travel,
            )

    # ── Flush any open state ─────────────────────────────────────────────────
    for name, s in states.items():
        if s.current_route and not s.current_route.end_time:
            s.current_route.not_ended = True
            s.routes.append(s.current_route)
            s.exceptions.append(Exception_(
                delivery_boy=name, date=s.current_route.date,
                timestamp=s.current_route.start_time,
                kind='Route Not Ended',
                detail=f'Route {s.current_route.route_number} started but chat ended without Route End'
            ))
        if s.pending_store:
            s.pending_store.missing_pod = True
            s.pending_store.finalize()
            s.deliveries.append(s.pending_store)

    return states


# ── Summary builders ──────────────────────────────────────────────────────────

def build_delivery_summary(states: dict) -> list:
    rows = []
    for name, s in states.items():
        by_date: dict = {}
        for d in s.deliveries:
            key = (name, d.date)
            if key not in by_date:
                by_date[key] = {
                    'name': name, 'date': d.date,
                    'routes': set(), 'stores': set(),
                    'deliveries': 0, 'times': []
                }
            by_date[key]['routes'].add(d.route_number)
            by_date[key]['stores'].add(d.store_name)
            by_date[key]['deliveries'] += 1
            if d.time_spent_mins:
                by_date[key]['times'].append(d.time_spent_mins)

        for r in s.routes:
            key = (name, r.date)
            if key not in by_date:
                by_date[key] = {
                    'name': name, 'date': r.date,
                    'routes': set(), 'stores': set(),
                    'deliveries': 0, 'times': []
                }
            by_date[key]['routes'].add(r.route_number)

        for key, d in by_date.items():
            name_, date_ = key
            day_routes = [r for r in s.routes if r.date == date_]
            first_start = min((r.start_time for r in day_routes if r.start_time), default=None)
            last_end    = max((r.end_time   for r in day_routes if r.end_time),   default=None)
            working_mins = None
            if first_start and last_end:
                working_mins = round((last_end - first_start).total_seconds() / 60, 1)

            day_breaks = [b for b in s.breaks if b.date == date_]
            break_mins = sum((b.duration_mins or 0) for b in day_breaks)

            net_working = None
            if working_mins is not None:
                net_working = round(working_mins - break_mins, 1)

            avg_time = round(sum(d['times']) / len(d['times']), 1) if d['times'] else None

            rows.append({
                'Name': name_,
                'Date': date_,
                'Total Routes': len(d['routes']),
                'Total Deliveries (POD)': d['deliveries'],
                'Total Stores Covered': len(d['stores']),
                'First Start': first_start.strftime('%H:%M') if first_start else '',
                'Last End': last_end.strftime('%H:%M') if last_end else '',
                'Total Working Time (mins)': working_mins,
                'Total Break Time (mins)': round(break_mins, 1),
                'Net Working Time (mins)': net_working,
                'Avg Time per Delivery (mins)': avg_time,
            })

    rows.sort(key=lambda x: (x['Name'], x['Date']))
    return rows


def build_delivery_details(states: dict) -> list:
    rows = []
    for name, s in states.items():
        for d in s.deliveries:
            inter_str = d.inter_delivery_mins if d.inter_delivery_mins is not None else 'N/A'
            rows.append({
                'Delivery Boy': name,
                'Date': d.date,
                'Route No.': d.route_number,
                'Store Name': d.store_name,
                'Store Arrival': d.store_time.strftime('%H:%M') if d.store_time else '',
                'POD Time': d.pod_time.strftime('%H:%M') if d.pod_time else '',
                'Time Spent (mins)': d.time_spent_mins if not d.missing_pod else 'N/A',
                'Time Between Deliveries (mins)': inter_str,
                'Status': 'Delayed' if d.is_delayed else ('Missing POD' if d.missing_pod else 'OK'),
            })
    rows.sort(key=lambda x: (x['Delivery Boy'], x['Date'], x['Route No.']))
    return rows


def build_route_summary(states: dict) -> list:
    rows = []
    for name, s in states.items():
        for r in s.routes:
            inter_times = [
                d.inter_delivery_mins
                for d in s.deliveries
                if d.route_number == r.route_number
                and d.date == r.date
                and d.inter_delivery_mins is not None
            ]
            avg_inter = round(sum(inter_times) / len(inter_times), 1) if inter_times else None
            max_inter = max(inter_times) if inter_times else None
            min_inter = min(inter_times) if inter_times else None

            rows.append({
                'Delivery Boy': name,
                'Date': r.date,
                'Route No.': r.route_number,
                'Start Time': r.start_time.strftime('%H:%M') if r.start_time else '',
                'End Time': r.end_time.strftime('%H:%M') if r.end_time else '(Not Ended)',
                'Total Deliveries': r.deliveries,
                'Total Time (mins)': r.total_mins,
                'Avg per Delivery (mins)': r.avg_delivery_mins,
                'Avg Inter-Delivery (mins)': avg_inter,
                'Max Inter-Delivery (mins)': max_inter,
                'Min Inter-Delivery (mins)': min_inter,
                'Status': 'Not Ended' if r.not_ended else 'Complete',
            })
    rows.sort(key=lambda x: (x['Delivery Boy'], x['Date'], x['Route No.']))
    return rows


def build_exceptions(states: dict) -> list:
    rows = []
    for name, s in states.items():
        for e in s.exceptions:
            rows.append({
                'Delivery Boy': name,
                'Date': e.date,
                'Time': e.timestamp.strftime('%H:%M') if e.timestamp else '',
                'Exception Type': e.kind,
                'Detail': e.detail,
            })
    rows.sort(key=lambda x: (x['Date'], x['Time']))
    return rows
