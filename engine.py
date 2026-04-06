"""
Delivery Tracking Engine — Fixed
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
RE_POD_SUBMITTED = re.compile(r'^pod\s+submitted\s*$',    re.I)   # FIX 1: was missing
RE_CLOSED        = re.compile(r'^closed\s*$',             re.I)
RE_BREAK_START   = re.compile(r'break\s*start',           re.I)   # FIX 2: was missing
RE_BREAK_END     = re.compile(r'break\s*end',             re.I)   # FIX 2: was missing

# ── Thresholds ────────────────────────────────────────────────────────────────
DELAY_THRESHOLD  = 30
TRAVEL_THRESHOLD = 60
GAP_THRESHOLD    = 60


def _is_exit(text: str) -> bool:
    t = text.strip()
    return bool(RE_POD.match(t) or RE_POD_SUBMITTED.match(t) or RE_CLOSED.match(t))


# ── Data classes ──────────────────────────────────────────────────────────────

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
    store_time:  Optional[float] = None

    delayed:     bool = False
    high_travel: bool = False
    missing_pod: bool = False
    pod_type:    str  = 'POD'

    def finalize(self):
        if self.start_time and self.arrival_time:
            t = (self.arrival_time - self.start_time).total_seconds() / 60
            self.travel_time = round(max(t, 0), 1)          # FIX 3: no negatives
            self.high_travel = self.travel_time > TRAVEL_THRESHOLD
        if self.arrival_time and self.pod_time:
            s = (self.pod_time - self.arrival_time).total_seconds() / 60
            self.store_time = round(max(s, 0), 1)           # FIX 3: no negatives
            self.delayed = self.store_time > DELAY_THRESHOLD
        else:
            self.missing_pod = True


@dataclass
class RouteRecord:                                          # FIX 4: was missing entirely
    delivery_boy: str
    date: str
    route_number: int
    start_time: Optional[datetime] = None
    end_time:   Optional[datetime] = None
    deliveries: int  = 0
    not_ended:  bool = False

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
class BreakRecord:                                         # FIX 5: was missing entirely
    delivery_boy: str
    date: str
    start_time: Optional[datetime] = None
    end_time:   Optional[datetime] = None

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
    name: str
    current_route:     Optional[RouteRecord] = None        # FIX 4: now RouteRecord not int
    last_exit_time:    Optional[datetime]    = None
    pending_store:     Optional[Delivery]    = None
    current_break:     Optional[BreakRecord] = None        # FIX 5: re-added
    last_message_time: Optional[datetime]    = None        # FIX 6: re-added for gap detection

    deliveries: list = field(default_factory=list)
    routes:     list = field(default_factory=list)         # FIX 4: re-added
    breaks:     list = field(default_factory=list)         # FIX 5: re-added
    exceptions: list = field(default_factory=list)         # FIX 6: re-added


# ── Helpers ───────────────────────────────────────────────────────────────────

def _date_str(ts: datetime) -> str:
    return ts.strftime('%Y-%m-%d')


def _check_gap(s: BoyState, ts: datetime):
    if s.last_message_time:
        gap = (ts - s.last_message_time).total_seconds() / 60
        if gap > GAP_THRESHOLD:
            s.exceptions.append(Exception_(
                delivery_boy=s.name, date=_date_str(ts), timestamp=ts,
                kind='No Activity Gap',
                detail=(f'{round(gap)} min gap between '
                        f'{s.last_message_time.strftime("%H:%M")} and {ts.strftime("%H:%M")}')
            ))


def _flush_pending(s: BoyState, name: str, date: str, ts: datetime):
    """Close a pending store as Missing POD before opening something new."""
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


# ── Main processor ────────────────────────────────────────────────────────────

def process_messages(messages: list) -> dict:
    states: dict = {}

    for msg in messages:
        name = msg.sender
        ts   = msg.timestamp
        # FIX 7: preserve original case for store names — do NOT call .lower() on entire text
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
                s.current_route.not_ended = True
                s.routes.append(s.current_route)
                s.exceptions.append(Exception_(
                    delivery_boy=name, date=date, timestamp=ts,
                    kind='Route Not Ended',
                    detail=(f'Route {s.current_route.route_number} not ended '
                            f'before Route {route_num} start')
                ))
            _flush_pending(s, name, date, ts)
            s.current_route = RouteRecord(
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
                s.routes.append(s.current_route)
                s.current_route = None
            else:
                s.exceptions.append(Exception_(
                    delivery_boy=name, date=date, timestamp=ts,
                    kind='Route End Without Start',
                    detail=f'Route {route_num} ended without a matching start'
                ))
            continue

        # FIX 2: Break Start / Break End — were missing, causing "Break Start"
        #         to be treated as a store name
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
                    detail='Break End without a preceding Break Start'
                ))
            continue

        # FIX 1: POD Submitted now recognised
        # ── Exit signal ───────────────────────────────────────────────────────
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
                store=text,                                # FIX 7: original case
                start_time=s.last_exit_time,
                arrival_time=ts,
                pod_time=None,
            )
        # messages outside a route are silently ignored

    # ── Flush open state at EOF ───────────────────────────────────────────────
    for name, s in states.items():
        if s.current_route and not s.current_route.end_time:
            s.current_route.not_ended = True
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
                'Delivery Boy':      name,
                'Date':              d.date,
                'Route No.':         d.route,
                'Store Name':        d.store,
                'Start Time':        d.start_time.strftime('%H:%M')   if d.start_time   else '',
                'Store Arrival':     d.arrival_time.strftime('%H:%M') if d.arrival_time else '',
                'POD Time':          d.pod_time.strftime('%H:%M')     if d.pod_time     else '',
                'Travel Time (mins)':d.travel_time if d.travel_time is not None else 'N/A',
                'Store Time (mins)': d.store_time  if d.store_time  is not None else 'N/A',
                'POD Type':          d.pod_type,
                'Status': (
                    'Delayed'     if d.delayed     else
                    'Missing POD' if d.missing_pod else
                    'High Travel' if d.high_travel else
                    'OK'
                ),
            })
    rows.sort(key=lambda x: (x['Delivery Boy'], x['Date'], x['Route No.']))
    return rows


def build_store_search(states: dict) -> list:
    return build_delivery_details(states)


def build_route_summary(states: dict) -> list:  # FIX 4: fully restored
    rows = []
    for name, s in states.items():
        for r in s.routes:
            route_deliveries = [
                d for d in s.deliveries
                if d.route == r.route_number and d.date == r.date
            ]
            travel_times = [d.travel_time for d in route_deliveries if d.travel_time is not None]
            store_times  = [d.store_time  for d in route_deliveries if d.store_time  is not None]

            rows.append({
                'Delivery Boy':           name,
                'Date':                   r.date,
                'Route No.':              r.route_number,
                'Start Time':             r.start_time.strftime('%H:%M') if r.start_time else '',
                'End Time':               r.end_time.strftime('%H:%M')   if r.end_time   else '(Not Ended)',
                'Total Deliveries':       r.deliveries,
                'Total Time (mins)':      r.total_mins,
                'Avg Store Time (mins)':  round(sum(store_times)  / len(store_times),  1) if store_times  else None,
                'Avg Travel Time (mins)': round(sum(travel_times) / len(travel_times), 1) if travel_times else None,
                'Max Travel Time (mins)': max(travel_times) if travel_times else None,
                'Min Travel Time (mins)': min(travel_times) if travel_times else None,
                'Status':                 'Not Ended' if r.not_ended else 'Complete',
            })
    rows.sort(key=lambda x: (x['Delivery Boy'], x['Date'], x['Route No.']))
    return rows


def build_exceptions(states: dict) -> list:     # FIX 6: fully restored
    rows = []
    for name, s in states.items():
        for e in s.exceptions:
            rows.append({
                'Delivery Boy':   name,
                'Date':           e.date,
                'Time':           e.timestamp.strftime('%H:%M') if e.timestamp else '',
                'Exception Type': e.kind,
                'Detail':         e.detail,
            })
    rows.sort(key=lambda x: (x['Date'], x['Time']))
    return rows


def build_delivery_summary(states: dict) -> list:  # FIX 8: fully restored
    rows = []
    for name, s in states.items():
        by_date: dict = {}

        for d in s.deliveries:
            key = (name, d.date)
            if key not in by_date:
                by_date[key] = {
                    'name': name, 'date': d.date,
                    'routes': set(), 'stores': set(),
                    'deliveries': 0, 'store_times': []
                }
            by_date[key]['routes'].add(d.route)
            by_date[key]['stores'].add(d.store)
            by_date[key]['deliveries'] += 1
            if d.store_time is not None:
                by_date[key]['store_times'].append(d.store_time)

        for r in s.routes:
            key = (name, r.date)
            if key not in by_date:
                by_date[key] = {
                    'name': name, 'date': r.date,
                    'routes': set(), 'stores': set(),
                    'deliveries': 0, 'store_times': []
                }
            by_date[key]['routes'].add(r.route_number)

        for key, d in by_date.items():
            name_, date_ = key
            day_routes  = [r for r in s.routes if r.date == date_]
            first_start = min((r.start_time for r in day_routes if r.start_time), default=None)
            last_end    = max((r.end_time   for r in day_routes if r.end_time),   default=None)
            working_mins = (
                round((last_end - first_start).total_seconds() / 60, 1)
                if first_start and last_end else None
            )
            break_mins = sum(
                (b.duration_mins or 0)
                for b in s.breaks if b.date == date_
            )
            net_working = round(working_mins - break_mins, 1) if working_mins is not None else None
            avg_time = (
                round(sum(d['store_times']) / len(d['store_times']), 1)
                if d['store_times'] else None
            )

            rows.append({
                'Name':                         name_,
                'Date':                         date_,
                'Total Routes':                 len(d['routes']),
                'Total Deliveries (POD)':       d['deliveries'],
                'Total Stores Covered':         len(d['stores']),
                'First Start':                  first_start.strftime('%H:%M') if first_start else '',
                'Last End':                     last_end.strftime('%H:%M')    if last_end    else '',
                'Total Working Time (mins)':    working_mins,
                'Total Break Time (mins)':      round(break_mins, 1),
                'Net Working Time (mins)':      net_working,
                'Avg Time per Delivery (mins)': avg_time,
            })

    rows.sort(key=lambda x: (x['Name'], x['Date']))
    return rows
