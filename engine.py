"""
Delivery Tracking Engine — Business-Ready v3
Improvements:
  - Route start/end times carried into every Delivery record
  - Store names Title-Cased at parse time
  - Daily Summary adds First Activity, Last Activity, Total Active Time
  - Route Summary adds Total Stores Covered + Route Duration
  - Delivery Details adds Route Start Time, Route End Time, POD Submitted Time column
  - All time deltas clamped to >= 0
  - Timestamp-order guard before every subtraction
"""
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from chat_parser import Message

# ── Patterns ──────────────────────────────────────────────────────────────────
RE_ROUTE_START   = re.compile(r'route\s*(\d+)\s*start',  re.I)
RE_ROUTE_END     = re.compile(r'route\s*(\d+)\s*end',    re.I)
RE_POD           = re.compile(r'^pod\s*$',                re.I)
RE_POD_SUBMITTED = re.compile(r'^pod\s+submitted\s*$',    re.I)
RE_CLOSED        = re.compile(r'^closed\s*$',             re.I)
RE_BREAK_START   = re.compile(r'break\s*start',           re.I)
RE_BREAK_END     = re.compile(r'break\s*end',             re.I)

# ── Thresholds ────────────────────────────────────────────────────────────────
DELAY_THRESHOLD  = 30
TRAVEL_THRESHOLD = 60
GAP_THRESHOLD    = 60


def _is_exit(text: str) -> bool:
    t = text.strip()
    return bool(RE_POD.match(t) or RE_POD_SUBMITTED.match(t) or RE_CLOSED.match(t))


def _safe_mins(a: Optional[datetime], b: Optional[datetime]) -> Optional[float]:
    """Return (b - a) in minutes, clamped >= 0. Returns None if either is None."""
    if a is None or b is None:
        return None
    return round(max((b - a).total_seconds() / 60, 0), 1)


def _title_case(text: str) -> str:
    """
    Convert store name to Title Case.
    Handles all-lower, all-upper, and mixed.
    Preserves abbreviations that are already upper (e.g. 'KFC', 'ATM').
    """
    return ' '.join(
        w.capitalize() if not w.isupper() or len(w) <= 2 else w
        for w in text.strip().split()
    )


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Delivery:
    delivery_boy: str
    date: str
    route: int
    store: str
    # Leg timing
    start_time:   Optional[datetime]   # previous POD / route start
    arrival_time: Optional[datetime]   # store arrival
    pod_time:     Optional[datetime]   # exit signal
    # Route-level context (filled in after route closes)
    route_start_time: Optional[datetime] = None
    route_end_time:   Optional[datetime] = None
    # Computed
    travel_time: Optional[float] = None
    store_time:  Optional[float] = None
    delayed:     bool = False
    high_travel: bool = False
    missing_pod: bool = False
    pod_type:    str  = 'POD'

    def finalize(self):
        self.travel_time = _safe_mins(self.start_time, self.arrival_time)
        if self.travel_time is not None:
            self.high_travel = self.travel_time > TRAVEL_THRESHOLD
        st = _safe_mins(self.arrival_time, self.pod_time)
        if st is not None:
            self.store_time = st
            self.delayed    = st > DELAY_THRESHOLD
        else:
            self.missing_pod = True


@dataclass
class RouteRecord:
    delivery_boy: str
    date: str
    route_number: int
    start_time: Optional[datetime] = None
    end_time:   Optional[datetime] = None
    deliveries: int  = 0
    not_ended:  bool = False

    @property
    def total_mins(self) -> Optional[float]:
        return _safe_mins(self.start_time, self.end_time)

    @property
    def avg_delivery_mins(self) -> Optional[float]:
        t = self.total_mins
        if t is not None and self.deliveries > 0:
            return round(t / self.deliveries, 1)
        return None


@dataclass
class BreakRecord:
    delivery_boy: str
    date: str
    start_time: Optional[datetime] = None
    end_time:   Optional[datetime] = None

    @property
    def duration_mins(self) -> Optional[float]:
        return _safe_mins(self.start_time, self.end_time)


@dataclass
class Exception_:
    delivery_boy: str
    date: str
    timestamp: Optional[datetime]
    kind: str
    detail: str


@dataclass
class BoyState:
    name: str
    current_route:     Optional[RouteRecord]  = None
    last_exit_time:    Optional[datetime]      = None
    pending_store:     Optional[Delivery]      = None
    current_break:     Optional[BreakRecord]   = None
    last_message_time: Optional[datetime]      = None
    first_activity:    Optional[datetime]      = None   # earliest message of the day
    last_activity:     Optional[datetime]      = None   # latest message of the day

    deliveries: list = field(default_factory=list)
    routes:     list = field(default_factory=list)
    breaks:     list = field(default_factory=list)
    exceptions: list = field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _date_str(ts: datetime) -> str:
    return ts.strftime('%Y-%m-%d')


def _hhmm(ts: Optional[datetime]) -> str:
    return ts.strftime('%H:%M') if ts else ''


def _check_gap(s: BoyState, ts: datetime):
    if s.last_message_time:
        gap = (ts - s.last_message_time).total_seconds() / 60
        if gap > GAP_THRESHOLD:
            s.exceptions.append(Exception_(
                delivery_boy=s.name, date=_date_str(ts), timestamp=ts,
                kind='No Activity Gap',
                detail=(f'{round(gap)} min gap between '
                        f'{_hhmm(s.last_message_time)} and {_hhmm(ts)}')
            ))


def _flush_pending(s: BoyState, name: str, date: str, ts: datetime):
    """Close pending store as Missing POD."""
    if s.pending_store:
        s.pending_store.missing_pod = True
        s.pending_store.pod_type    = 'Missing'
        s.pending_store.finalize()
        s.deliveries.append(s.pending_store)
        s.exceptions.append(Exception_(
            delivery_boy=name, date=date, timestamp=ts,
            kind='Missing POD',
            detail=f'No POD after store "{s.pending_store.store}"'
        ))
        s.pending_store = None


def _stamp_route_times(s: BoyState, route: RouteRecord):
    """Back-fill route_start_time / route_end_time on every delivery in this route."""
    for d in s.deliveries:
        if d.route == route.route_number and d.date == route.date:
            d.route_start_time = route.start_time
            d.route_end_time   = route.end_time


# ── Main processor ────────────────────────────────────────────────────────────

def process_messages(messages: list) -> dict:
    states: dict = {}

    for msg in messages:
        name = msg.sender
        ts   = msg.timestamp
        text = msg.text.strip()   # preserve original case; patterns use re.I
        date = _date_str(ts)

        if name not in states:
            states[name] = BoyState(name=name)
        s = states[name]

        _check_gap(s, ts)
        s.last_message_time = ts
        # Track first/last activity per person (used in Daily Summary)
        if s.first_activity is None or ts < s.first_activity:
            s.first_activity = ts
        if s.last_activity is None or ts > s.last_activity:
            s.last_activity = ts

        # ── Route Start ──────────────────────────────────────────────────────
        m = RE_ROUTE_START.match(text)
        if m:
            route_num = int(m.group(1))
            if s.current_route and not s.current_route.end_time:
                # Auto-close unclosed route before opening new one
                s.current_route.not_ended = True
                _stamp_route_times(s, s.current_route)
                s.routes.append(s.current_route)
                s.exceptions.append(Exception_(
                    delivery_boy=name, date=date, timestamp=ts,
                    kind='Route Not Ended',
                    detail=(f'Route {s.current_route.route_number} not ended '
                            f'before Route {route_num} start')
                ))
            _flush_pending(s, name, date, ts)
            s.current_route  = RouteRecord(
                delivery_boy=name, date=date,
                route_number=route_num, start_time=ts
            )
            s.last_exit_time = ts
            continue

        # ── Route End ────────────────────────────────────────────────────────
        m = RE_ROUTE_END.match(text)
        if m:
            route_num = int(m.group(1))
            _flush_pending(s, name, date, ts)
            if s.current_route and s.current_route.route_number == route_num:
                s.current_route.end_time = ts
                _stamp_route_times(s, s.current_route)  # ← back-fill end time
                s.routes.append(s.current_route)
                s.current_route = None
            else:
                s.exceptions.append(Exception_(
                    delivery_boy=name, date=date, timestamp=ts,
                    kind='Route End Without Start',
                    detail=f'Route {route_num} ended without a matching start'
                ))
            continue

        # ── Break Start ───────────────────────────────────────────────────────
        if RE_BREAK_START.match(text):
            s.current_break = BreakRecord(delivery_boy=name, date=date, start_time=ts)
            continue

        # ── Break End ─────────────────────────────────────────────────────────
        if RE_BREAK_END.match(text):
            if s.current_break:
                s.current_break.end_time = ts
                s.breaks.append(s.current_break)
                s.current_break = None
            else:
                s.exceptions.append(Exception_(
                    delivery_boy=name, date=date, timestamp=ts,
                    kind='Break End Without Start',
                    detail='Break End without a preceding Break Start'
                ))
            continue

        # ── Exit signal: POD / POD Submitted / Closed ─────────────────────────
        if _is_exit(text):
            if RE_POD_SUBMITTED.match(text):
                ptype = 'POD Submitted'
            elif RE_CLOSED.match(text):
                ptype = 'Closed'
            else:
                ptype = 'POD'

            if s.pending_store:
                s.pending_store.pod_time = ts
                s.pending_store.pod_type = ptype
                s.pending_store.finalize()
                if s.pending_store.delayed:
                    s.exceptions.append(Exception_(
                        delivery_boy=name, date=date, timestamp=ts,
                        kind='Long Delay',
                        detail=(f'{ptype} for "{s.pending_store.store}" took '
                                f'{s.pending_store.store_time} mins '
                                f'(>{DELAY_THRESHOLD} min threshold)')
                    ))
                if s.pending_store.high_travel:
                    s.exceptions.append(Exception_(
                        delivery_boy=name, date=date, timestamp=ts,
                        kind='High Travel Time',
                        detail=(f'Travel to "{s.pending_store.store}" took '
                                f'{s.pending_store.travel_time} mins '
                                f'(>{TRAVEL_THRESHOLD} min threshold)')
                    ))
                s.deliveries.append(s.pending_store)
                if s.current_route:
                    s.current_route.deliveries += 1
                s.last_exit_time = ts
                s.pending_store  = None
            else:
                s.exceptions.append(Exception_(
                    delivery_boy=name, date=date, timestamp=ts,
                    kind='POD Without Store',
                    detail=f'{ptype} logged without a preceding store visit'
                ))
            continue

        # ── Store Visit ───────────────────────────────────────────────────────
        if s.current_route:
            _flush_pending(s, name, date, ts)
            s.pending_store = Delivery(
                delivery_boy=name,
                date=date,
                route=s.current_route.route_number,
                store=_title_case(text),          # ← Title Case applied here
                start_time=s.last_exit_time,
                arrival_time=ts,
                pod_time=None,
                route_start_time=s.current_route.start_time,  # ← route context
                route_end_time=None,                           #   (end filled later)
            )
        # messages outside an active route are silently ignored

    # ── Flush open state at EOF ───────────────────────────────────────────────
    for name, s in states.items():
        if s.current_route and not s.current_route.end_time:
            s.current_route.not_ended = True
            _stamp_route_times(s, s.current_route)
            s.routes.append(s.current_route)
            s.exceptions.append(Exception_(
                delivery_boy=name, date=s.current_route.date,
                timestamp=s.current_route.start_time,
                kind='Route Not Ended',
                detail=(f'Route {s.current_route.route_number} started '
                        f'but chat ended without Route End')
            ))
        if s.pending_store:
            s.pending_store.missing_pod = True
            s.pending_store.pod_type    = 'Missing'
            s.pending_store.finalize()
            s.deliveries.append(s.pending_store)

    return states


# ── Report builders ───────────────────────────────────────────────────────────

def build_delivery_details(states: dict) -> list:
    rows = []
    for name, s in states.items():
        for d in s.deliveries:
            rows.append({
                'Delivery Boy':       name,
                'Date':               d.date,
                'Route No.':          d.route,
                'Route Start Time':   _hhmm(d.route_start_time),   # ← NEW
                'Route End Time':     _hhmm(d.route_end_time),      # ← NEW
                'Store Name':         d.store,                       # Title Case
                'Start Time':         _hhmm(d.start_time),
                'Store Arrival':      _hhmm(d.arrival_time),
                'POD Time':           _hhmm(d.pod_time),
                'POD Type':           d.pod_type,
                'Travel Time (mins)': d.travel_time if d.travel_time is not None else 'N/A',
                'Store Time (mins)':  d.store_time  if d.store_time  is not None else 'N/A',
                'Status': (
                    'Delayed'     if d.delayed     else
                    'Missing POD' if d.missing_pod else
                    'High Travel' if d.high_travel else
                    'OK'
                ),
            })
    rows.sort(key=lambda x: (x['Delivery Boy'], x['Date'], x['Route No.'],
                              x['Store Arrival']))
    return rows


def build_store_search(states: dict) -> list:
    return build_delivery_details(states)


def build_route_summary(states: dict) -> list:
    rows = []
    for name, s in states.items():
        for r in s.routes:
            route_deliveries = [
                d for d in s.deliveries
                if d.route == r.route_number and d.date == r.date
            ]
            travel_times  = [d.travel_time for d in route_deliveries if d.travel_time is not None]
            store_times   = [d.store_time  for d in route_deliveries if d.store_time  is not None]
            stores_covered = len({d.store for d in route_deliveries})  # ← unique stores
            duration       = r.total_mins

            rows.append({
                'Delivery Boy':           name,
                'Date':                   r.date,
                'Route No.':              r.route_number,
                'Route Start Time':       _hhmm(r.start_time),           # ← explicit label
                'Route End Time':         _hhmm(r.end_time) if r.end_time else '(Not Ended)',
                'Total Deliveries':       r.deliveries,
                'Total Stores Covered':   stores_covered,                 # ← NEW
                'Route Duration (mins)':  duration,                       # ← renamed for clarity
                'Avg Store Time (mins)':  round(sum(store_times)  / len(store_times),  1) if store_times  else None,
                'Avg Travel Time (mins)': round(sum(travel_times) / len(travel_times), 1) if travel_times else None,
                'Max Travel Time (mins)': max(travel_times) if travel_times else None,
                'Min Travel Time (mins)': min(travel_times) if travel_times else None,
                'Status':                 'Not Ended' if r.not_ended else 'Complete',
            })
    rows.sort(key=lambda x: (x['Delivery Boy'], x['Date'], x['Route No.']))
    return rows


def build_exceptions(states: dict) -> list:
    rows = []
    for name, s in states.items():
        for e in s.exceptions:
            rows.append({
                'Delivery Boy':   name,
                'Date':           e.date,
                'Time':           _hhmm(e.timestamp),
                'Exception Type': e.kind,
                'Detail':         e.detail,
            })
    rows.sort(key=lambda x: (x['Date'], x['Time']))
    return rows


def build_delivery_summary(states: dict) -> list:
    rows = []
    for name, s in states.items():
        # group deliveries by (name, date)
        by_date: dict = {}
        for d in s.deliveries:
            key = (name, d.date)
            if key not in by_date:
                by_date[key] = {
                    'name': name, 'date': d.date,
                    'routes': set(), 'stores': set(),
                    'deliveries': 0, 'store_times': [],
                    'all_timestamps': [],
                }
            by_date[key]['routes'].add(d.route)
            by_date[key]['stores'].add(d.store)
            by_date[key]['deliveries'] += 1
            if d.store_time is not None:
                by_date[key]['store_times'].append(d.store_time)
            for ts in (d.start_time, d.arrival_time, d.pod_time):
                if ts: by_date[key]['all_timestamps'].append(ts)

        for r in s.routes:
            key = (name, r.date)
            if key not in by_date:
                by_date[key] = {
                    'name': name, 'date': r.date,
                    'routes': set(), 'stores': set(),
                    'deliveries': 0, 'store_times': [],
                    'all_timestamps': [],
                }
            by_date[key]['routes'].add(r.route_number)
            for ts in (r.start_time, r.end_time):
                if ts: by_date[key]['all_timestamps'].append(ts)

        for key, d in by_date.items():
            name_, date_ = key
            day_routes  = [r for r in s.routes if r.date == date_]
            first_start = min((r.start_time for r in day_routes if r.start_time), default=None)
            last_end    = max((r.end_time   for r in day_routes if r.end_time),   default=None)

            working_mins = _safe_mins(first_start, last_end)

            break_mins = sum(
                (b.duration_mins or 0)
                for b in s.breaks if b.date == date_
            )
            net_working = round(working_mins - break_mins, 1) if working_mins is not None else None

            avg_time = (
                round(sum(d['store_times']) / len(d['store_times']), 1)
                if d['store_times'] else None
            )

            # First / Last activity across ALL messages for this person+date
            all_ts = d['all_timestamps']
            first_activity = min(all_ts).strftime('%H:%M') if all_ts else (
                first_start.strftime('%H:%M') if first_start else '')
            last_activity  = max(all_ts).strftime('%H:%M') if all_ts else (
                last_end.strftime('%H:%M') if last_end else '')

            rows.append({
                'Name':                         name_,
                'Date':                         date_,
                'Total Routes':                 len(d['routes']),
                'Total Deliveries (POD)':       d['deliveries'],
                'Total Stores Covered':         len(d['stores']),
                'First Activity':               first_activity,           # ← renamed / improved
                'Last Activity':                last_activity,            # ← renamed / improved
                'Route Start (First)':          _hhmm(first_start),      # ← NEW: office departure
                'Route End (Last)':             _hhmm(last_end),         # ← NEW: office return
                'Total Working Time (mins)':    working_mins,
                'Total Break Time (mins)':      round(break_mins, 1),
                'Net Working Time (mins)':      net_working,
                'Avg Time per Delivery (mins)': avg_time,
            })

    rows.sort(key=lambda x: (x['Name'], x['Date']))
    return rows
