"""
Delivery Tracking Engine — v4
New in this version:
  - Route sequence numbering per person per day (1, 2, 3…)
  - Performance Score per delivery (0–100)
  - Route efficiency: Stores per Hour
  - Daily Insights: Efficiency %, Total Working Hours
  - Store Insights builder: visits, avg time, total time per store
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

# ── Performance score weights (penalties, subtracted from 100) ────────────────
PERF_PENALTY_MISSING_POD  = 40
PERF_PENALTY_DELAY        = 20
PERF_PENALTY_HIGH_TRAVEL  = 15
PERF_PENALTY_IDLE         = 10   # per 60 min idle gap on this delivery's travel leg


def _is_exit(text: str) -> bool:
    t = text.strip()
    return bool(RE_POD.match(t) or RE_POD_SUBMITTED.match(t) or RE_CLOSED.match(t))


def _safe_mins(a: Optional[datetime], b: Optional[datetime]) -> Optional[float]:
    if a is None or b is None:
        return None
    return round(max((b - a).total_seconds() / 60, 0), 1)


def _title_case(text: str) -> str:
    return ' '.join(
        w.capitalize() if not w.isupper() or len(w) <= 2 else w
        for w in text.strip().split()
    )


def _perf_score(delayed: bool, high_travel: bool, missing_pod: bool,
                travel_mins: Optional[float]) -> int:
    """
    Simple 0-100 score. Start at 100, subtract penalties.
    Idle penalty: -10 for every full 60 min of travel beyond normal threshold.
    """
    score = 100
    if missing_pod:
        score -= PERF_PENALTY_MISSING_POD
    if delayed:
        score -= PERF_PENALTY_DELAY
    if high_travel and travel_mins is not None:
        score -= PERF_PENALTY_HIGH_TRAVEL
        extra_hours = int(travel_mins // 60)
        score -= extra_hours * PERF_PENALTY_IDLE
    return max(score, 0)


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Delivery:
    delivery_boy: str
    date: str
    route: int
    route_seq: int                       # ← NEW: sequential per person per day
    store: str
    start_time:   Optional[datetime]
    arrival_time: Optional[datetime]
    pod_time:     Optional[datetime]
    route_start_time: Optional[datetime] = None
    route_end_time:   Optional[datetime] = None
    travel_time: Optional[float] = None
    store_time:  Optional[float] = None
    delayed:     bool = False
    high_travel: bool = False
    missing_pod: bool = False
    pod_type:    str  = 'POD'
    perf_score:  int  = 100              # ← NEW

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
        self.perf_score = _perf_score(
            self.delayed, self.high_travel, self.missing_pod, self.travel_time
        )


@dataclass
class RouteRecord:
    delivery_boy: str
    date: str
    route_number: int
    route_seq: int = 1                   # ← NEW: day-level sequence
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

    @property
    def stores_per_hour(self) -> Optional[float]:
        """Efficiency: deliveries per hour of route time."""
        t = self.total_mins
        if t and t > 0 and self.deliveries > 0:
            return round(self.deliveries / (t / 60), 2)
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
    first_activity:    Optional[datetime]      = None
    last_activity:     Optional[datetime]      = None
    # Track route sequence per date: date → count
    _route_seq_by_date: dict = field(default_factory=dict)

    deliveries: list = field(default_factory=list)
    routes:     list = field(default_factory=list)
    breaks:     list = field(default_factory=list)
    exceptions: list = field(default_factory=list)

    def next_route_seq(self, date: str) -> int:
        self._route_seq_by_date[date] = self._route_seq_by_date.get(date, 0) + 1
        return self._route_seq_by_date[date]


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
        text = msg.text.strip()
        date = _date_str(ts)

        if name not in states:
            states[name] = BoyState(name=name)
        s = states[name]

        _check_gap(s, ts)
        s.last_message_time = ts
        if s.first_activity is None or ts < s.first_activity:
            s.first_activity = ts
        if s.last_activity is None or ts > s.last_activity:
            s.last_activity = ts

        # ── Route Start ──────────────────────────────────────────────────────
        m = RE_ROUTE_START.match(text)
        if m:
            route_num = int(m.group(1))
            if s.current_route and not s.current_route.end_time:
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
            seq = s.next_route_seq(date)           # ← assign day-sequence
            s.current_route = RouteRecord(
                delivery_boy=name, date=date,
                route_number=route_num, route_seq=seq, start_time=ts
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
                _stamp_route_times(s, s.current_route)
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
                route_seq=s.current_route.route_seq,   # ← carry sequence
                store=_title_case(text),
                start_time=s.last_exit_time,
                arrival_time=ts,
                pod_time=None,
                route_start_time=s.current_route.start_time,
                route_end_time=None,
            )

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
                'Delivery Boy':        name,
                'Date':                d.date,
                'Route No.':           d.route,
                'Route Seq (Day)':     d.route_seq,          # ← NEW
                'Route Start Time':    _hhmm(d.route_start_time),
                'Route End Time':      _hhmm(d.route_end_time),
                'Store Name':          d.store,
                'Start Time':          _hhmm(d.start_time),
                'Store Arrival':       _hhmm(d.arrival_time),
                'POD Time':            _hhmm(d.pod_time),
                'POD Type':            d.pod_type,
                'Travel Time (mins)':  d.travel_time if d.travel_time is not None else 'N/A',
                'Store Time (mins)':   d.store_time  if d.store_time  is not None else 'N/A',
                'Perf Score':          d.perf_score,          # ← NEW
                'Status': (
                    'Delayed'     if d.delayed     else
                    'Missing POD' if d.missing_pod else
                    'High Travel' if d.high_travel else
                    'OK'
                ),
            })
    rows.sort(key=lambda x: (x['Delivery Boy'], x['Date'], x['Route Seq (Day)'],
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
            travel_times   = [d.travel_time for d in route_deliveries if d.travel_time is not None]
            store_times    = [d.store_time  for d in route_deliveries if d.store_time  is not None]
            perf_scores    = [d.perf_score  for d in route_deliveries]
            stores_covered = len({d.store for d in route_deliveries})
            duration       = r.total_mins
            ok_count       = sum(1 for d in route_deliveries if not d.delayed and not d.missing_pod)
            total_d        = len(route_deliveries)
            efficiency_pct = round(ok_count / total_d * 100, 1) if total_d > 0 else None

            rows.append({
                'Delivery Boy':           name,
                'Date':                   r.date,
                'Route No.':              r.route_number,
                'Route Seq (Day)':        r.route_seq,                    # ← NEW
                'Route Start Time':       _hhmm(r.start_time),
                'Route End Time':         _hhmm(r.end_time) if r.end_time else '(Not Ended)',
                'Total Deliveries':       r.deliveries,
                'Total Stores Covered':   stores_covered,
                'Route Duration (mins)':  duration,
                'Stores per Hour':        r.stores_per_hour,              # ← NEW efficiency
                'Avg Store Time (mins)':  round(sum(store_times)  / len(store_times),  1) if store_times  else None,
                'Avg Travel Time (mins)': round(sum(travel_times) / len(travel_times), 1) if travel_times else None,
                'Max Travel Time (mins)': max(travel_times) if travel_times else None,
                'Min Travel Time (mins)': min(travel_times) if travel_times else None,
                'Avg Perf Score':         round(sum(perf_scores) / len(perf_scores), 1) if perf_scores else None,  # ← NEW
                'Efficiency %':           efficiency_pct,                 # ← NEW
                'Status':                 'Not Ended' if r.not_ended else 'Complete',
            })
    rows.sort(key=lambda x: (x['Delivery Boy'], x['Date'], x['Route Seq (Day)']))
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
        by_date: dict = {}
        for d in s.deliveries:
            key = (name, d.date)
            if key not in by_date:
                by_date[key] = {
                    'name': name, 'date': d.date,
                    'routes': set(), 'stores': set(),
                    'deliveries': 0, 'store_times': [],
                    'all_timestamps': [], 'perf_scores': [],
                    'ok': 0, 'delayed': 0, 'missing': 0,
                }
            by_date[key]['routes'].add(d.route)
            by_date[key]['stores'].add(d.store)
            by_date[key]['deliveries'] += 1
            by_date[key]['perf_scores'].append(d.perf_score)
            if d.store_time is not None:
                by_date[key]['store_times'].append(d.store_time)
            for ts in (d.start_time, d.arrival_time, d.pod_time):
                if ts:
                    by_date[key]['all_timestamps'].append(ts)
            if d.missing_pod:
                by_date[key]['missing'] += 1
            elif d.delayed:
                by_date[key]['delayed'] += 1
            else:
                by_date[key]['ok'] += 1

        for r in s.routes:
            key = (name, r.date)
            if key not in by_date:
                by_date[key] = {
                    'name': name, 'date': r.date,
                    'routes': set(), 'stores': set(),
                    'deliveries': 0, 'store_times': [],
                    'all_timestamps': [], 'perf_scores': [],
                    'ok': 0, 'delayed': 0, 'missing': 0,
                }
            by_date[key]['routes'].add(r.route_number)
            for ts in (r.start_time, r.end_time):
                if ts:
                    by_date[key]['all_timestamps'].append(ts)

        for key, d in by_date.items():
            name_, date_ = key
            day_routes  = [r for r in s.routes if r.date == date_]
            first_start = min((r.start_time for r in day_routes if r.start_time), default=None)
            last_end    = max((r.end_time   for r in day_routes if r.end_time),   default=None)
            working_mins = _safe_mins(first_start, last_end)
            break_mins   = sum((b.duration_mins or 0) for b in s.breaks if b.date == date_)
            net_working  = round(working_mins - break_mins, 1) if working_mins is not None else None
            working_hrs  = round(net_working / 60, 2) if net_working is not None else None

            total_d = d['deliveries']
            ok_rate = round(d['ok'] / total_d * 100, 1) if total_d > 0 else None
            avg_perf = round(sum(d['perf_scores']) / len(d['perf_scores']), 1) if d['perf_scores'] else None
            avg_time = (
                round(sum(d['store_times']) / len(d['store_times']), 1)
                if d['store_times'] else None
            )
            all_ts = d['all_timestamps']
            first_activity = min(all_ts).strftime('%H:%M') if all_ts else _hhmm(first_start)
            last_activity  = max(all_ts).strftime('%H:%M') if all_ts else _hhmm(last_end)

            rows.append({
                'Name':                         name_,
                'Date':                         date_,
                'Total Routes':                 len(d['routes']),
                'Total Deliveries (POD)':       total_d,
                'On Time':                      d['ok'],                  # ← NEW
                'Delayed':                      d['delayed'],             # ← NEW
                'Missing POD':                  d['missing'],             # ← NEW
                'Total Stores Covered':         len(d['stores']),
                'First Activity':               first_activity,
                'Last Activity':                last_activity,
                'Route Start (First)':          _hhmm(first_start),
                'Route End (Last)':             _hhmm(last_end),
                'Total Working Time (mins)':    working_mins,
                'Total Break Time (mins)':      round(break_mins, 1),
                'Net Working Time (mins)':      net_working,
                'Working Hours':                working_hrs,              # ← NEW
                'Avg Time per Delivery (mins)': avg_time,
                'Efficiency %':                 ok_rate,                  # ← NEW: on-time rate
                'Avg Perf Score':               avg_perf,                 # ← NEW
            })

    rows.sort(key=lambda x: (x['Name'], x['Date']))
    return rows


def build_store_insights(states: dict) -> list:
    """
    NEW builder: one row per unique store, aggregated across all delivery boys.
    Columns: Store Name, Total Visits, Delivery Boys, Dates Visited,
             Avg Store Time (mins), Max Store Time (mins), Min Store Time (mins),
             Missing POD Count, Delayed Count
    """
    store_data: dict = {}
    for name, s in states.items():
        for d in s.deliveries:
            key = d.store
            if key not in store_data:
                store_data[key] = {
                    'store': key,
                    'visits': 0,
                    'boys': set(),
                    'dates': set(),
                    'store_times': [],
                    'missing': 0,
                    'delayed': 0,
                }
            sd = store_data[key]
            sd['visits']  += 1
            sd['boys'].add(name)
            sd['dates'].add(d.date)
            if d.store_time is not None:
                sd['store_times'].append(d.store_time)
            if d.missing_pod:
                sd['missing'] += 1
            if d.delayed:
                sd['delayed'] += 1

    rows = []
    for key, sd in store_data.items():
        times = sd['store_times']
        rows.append({
            'Store Name':              sd['store'],
            'Total Visits':            sd['visits'],
            'Delivery Boys':           len(sd['boys']),
            'Dates Visited':           len(sd['dates']),
            'Avg Store Time (mins)':   round(sum(times) / len(times), 1) if times else 'N/A',
            'Max Store Time (mins)':   max(times) if times else 'N/A',
            'Min Store Time (mins)':   min(times) if times else 'N/A',
            'Missing POD Count':       sd['missing'],
            'Delayed Count':           sd['delayed'],
        })
    rows.sort(key=lambda x: -x['Total Visits'])
    return rows
