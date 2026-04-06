"""
Delivery Tracking Engine — Production v2
State machine per delivery boy.

Signal hierarchy (case-insensitive, stripped):
  route X start     → office start / route open
  route X end       → return to office
  pod               → store exit / POD
  pod submitted     → final submission (same weight as pod)
  closed            → store exit (alternative to pod)
  break start/end   → break tracking
  <anything else>   → store name (only when route is active)
"""
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from chat_parser import Message

# ── Keyword patterns ──────────────────────────────────────────────────────────
RE_ROUTE_START   = re.compile(r'route\s*(\d+)\s*start',   re.I)
RE_ROUTE_END     = re.compile(r'route\s*(\d+)\s*end',     re.I)
RE_POD           = re.compile(r'^pod\s*$',                 re.I)
RE_POD_SUBMITTED = re.compile(r'^pod\s+submitted\s*$',     re.I)
RE_CLOSED        = re.compile(r'^closed\s*$',              re.I)
RE_BREAK_START   = re.compile(r'break\s*start',            re.I)
RE_BREAK_END     = re.compile(r'break\s*end',              re.I)

# ── Thresholds ─────────────────────────────────────────────────────────────────
DELAY_THRESHOLD_MINS  = 30
GAP_THRESHOLD_MINS    = 60
TRAVEL_THRESHOLD_MINS = 60


def _is_exit_signal(text: str) -> bool:
    """True for pod / pod submitted / closed."""
    t = text.strip()
    return bool(RE_POD.match(t) or RE_POD_SUBMITTED.match(t) or RE_CLOSED.match(t))


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class DeliveryRecord:
    """
    One store leg.
    start_time  = previous POD time  (or route start for first store)
    store_time  = store arrival message timestamp
    pod_time    = POD / closed / pod submitted timestamp
    travel_mins = store_time  - start_time
    store_mins  = pod_time    - store_time
    """
    delivery_boy: str
    date: str
    route_number: int
    store_name: str
    start_time: Optional[datetime]    # leg start (prev POD / route start)
    store_time: Optional[datetime]    # arrival at store
    pod_time: Optional[datetime]      # exit signal
    travel_mins: Optional[float] = None
    store_mins: Optional[float]  = None
    is_delayed: bool  = False
    missing_pod: bool = False
    high_travel: bool = False
    pod_type: str = 'POD'             # 'POD' | 'Closed' | 'POD Submitted' | 'Missing'

    def finalize(self):
        if self.store_time and self.start_time:
            t = (self.store_time - self.start_time).total_seconds() / 60
            self.travel_mins = round(max(t, 0), 1)
            self.high_travel = self.travel_mins > TRAVEL_THRESHOLD_MINS
        if self.store_time and self.pod_time:
            s = (self.pod_time - self.store_time).total_seconds() / 60
            self.store_mins = round(max(s, 0), 1)
            self.is_delayed = self.store_mins > DELAY_THRESHOLD_MINS
        else:
            self.missing_pod = True


@dataclass
class RouteRecord:
    delivery_boy: str
    date: str
    route_number: int
    start_time: Optional[datetime] = None
    end_time: Optional[datetime]   = None
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
    end_time: Optional[datetime]   = None

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
    current_route: Optional[RouteRecord]   = None
    pending_store: Optional[DeliveryRecord] = None
    current_break: Optional[BreakRecord]   = None
    last_message_time: Optional[datetime]  = None
    last_exit_time: Optional[datetime]     = None  # last POD/closed/route-start

    deliveries: list = field(default_factory=list)
    routes:     list = field(default_factory=list)
    breaks:     list = field(default_factory=list)
    exceptions: list = field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _date_str(ts: datetime) -> str:
    return ts.strftime('%Y-%m-%d')


def _check_gap(state: BoyState, ts: datetime):

    if state.last_message_time:
        gap = (ts - state.last_message_time).total_seconds() / 60

        # ⚠️ Medium gap (idle)
        if 60 < gap <= 120:
            state.exceptions.append(Exception_(
                delivery_boy=state.name,
                date=_date_str(ts),
                timestamp=ts,
                kind='Idle Time',
                detail=f'{round(gap)} min gap (possible idle time)'
            ))

        # 🔴 Long gap (break / issue)
        elif gap > 120:
            state.exceptions.append(Exception_(
                delivery_boy=state.name,
                date=_date_str(ts),
                timestamp=ts,
                kind='Long Break',
                detail=f'{round(gap)} min gap (long break / inactive)'
            ))


def _flush_pending(s: BoyState, name: str, date: str, ts: datetime):
    """Close a pending store with missing POD before starting something new."""
    if s.pending_store:
        s.pending_store.missing_pod = True
        s.pending_store.pod_type = 'Missing'
        s.pending_store.finalize()
        s.deliveries.append(s.pending_store)
        s.exceptions.append(Exception_(
            delivery_boy=name, date=date, timestamp=ts,
            kind='Missing POD',
            detail=f'No POD after store "{s.pending_store.store_name}"'
        ))
        s.pending_store = None


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

        # ── Route Start ──────────────────────────────────────────────────────
        m = RE_ROUTE_START.match(text)
        if m:
            route_num = int(m.group(1))
            if s.current_route and not s.current_route.end_time:
                s.exceptions.append(Exception_(
                    delivery_boy=name, date=date, timestamp=ts,
                    kind='Route Not Ended',
                    detail=(f'Route {s.current_route.route_number} not ended '
                            f'before Route {route_num} start')
                ))
                s.current_route.not_ended = True
                s.routes.append(s.current_route)
            _flush_pending(s, name, date, ts)
            s.current_route = RouteRecord(
                delivery_boy=name, date=date,
                route_number=route_num, start_time=ts
            )
            s.last_exit_time = ts   # office → first store travel starts here
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

        # ── Exit signal: POD / POD Submitted / Closed ─────────────────────────
       if _is_exit_signal(text):

    # Determine pod_type
    if RE_POD_SUBMITTED.match(text):
        ptype = 'POD Submitted'
    elif RE_CLOSED.match(text):
        ptype = 'Closed'
    else:
        ptype = 'POD'

    # ✅ CASE 1: Normal flow (store exists)
    if s.pending_store:
        s.pending_store.pod_time = ts
        s.pending_store.pod_type = ptype
        s.pending_store.finalize()

        if s.pending_store.is_delayed:
            s.exceptions.append(Exception_(
                delivery_boy=name, date=date, timestamp=ts,
                kind='Long Delay',
                detail=f'{ptype} for "{s.pending_store.store_name}" took {s.pending_store.store_mins} mins'
            ))

        if s.pending_store.high_travel:
            s.exceptions.append(Exception_(
                delivery_boy=name, date=date, timestamp=ts,
                kind='High Travel Time',
                detail=f'Travel to "{s.pending_store.store_name}" took {s.pending_store.travel_mins} mins'
            ))

        s.deliveries.append(s.pending_store)

        if s.current_route:
            s.current_route.deliveries += 1

        s.last_exit_time = ts
        s.pending_store = None

    # ⚠️ CASE 2: Attach to last delivery if missed
    elif s.deliveries:
        last_delivery = s.deliveries[-1]

        if last_delivery.pod_time is None:
            last_delivery.pod_time = ts
            last_delivery.pod_type = ptype
            last_delivery.finalize()

            s.last_exit_time = ts

        else:
            s.exceptions.append(Exception_(
                delivery_boy=name, date=date, timestamp=ts,
                kind='POD Without Store',
                detail=f'{ptype} logged without valid store context'
            ))

    # ❌ CASE 3: Truly invalid
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
            s.pending_store = DeliveryRecord(
                delivery_boy=name,
                date=date,
                route_number=s.current_route.route_number,
                store_name=text,
                start_time=s.last_exit_time,   # leg starts from last exit / route start
                store_time=ts,
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
            s.pending_store.pod_type = 'Missing'
            s.pending_store.finalize()
            s.deliveries.append(s.pending_store)

    return states


# ── Report builders ───────────────────────────────────────────────────────────

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
                    'deliveries': 0, 'store_times': []
                }
            by_date[key]['routes'].add(d.route_number)
            by_date[key]['stores'].add(d.store_name)
            by_date[key]['deliveries'] += 1
            if d.store_mins is not None:
                by_date[key]['store_times'].append(d.store_mins)

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
            break_mins  = sum((b.duration_mins or 0) for b in s.breaks if b.date == date_)
            net_working = round(working_mins - break_mins, 1) if working_mins is not None else None
            avg_time    = (
                round(sum(d['store_times']) / len(d['store_times']), 1)
                if d['store_times'] else None
            )# ── PERFORMANCE SCORE ─────────────────────────

total = d['deliveries']

delayed = len([x for x in s.deliveries if x.date == date_ and x.is_delayed])
high_travel = len([x for x in s.deliveries if x.date == date_ and x.high_travel])
missing = len([x for x in s.deliveries if x.date == date_ and x.missing_pod])

idle = len([e for e in s.exceptions if e.date == date_ and e.kind == 'Idle Time'])
long_break = len([e for e in s.exceptions if e.date == date_ and e.kind == 'Long Break'])

if total > 0:

    on_time_score = ((total - delayed) / total) * 40
    delay_penalty = (delayed / total) * 20
    travel_penalty = (high_travel / total) * 15
    missing_penalty = (missing / total) * 10

    idle_penalty = min((idle + long_break) * 2, 15)

    performance_score = round(
        on_time_score
        - delay_penalty
        - travel_penalty
        - missing_penalty
        - idle_penalty,
        1
    )
else:
    performance_score = 0


# ✅ MUST BE HERE (outside if-else)
rows.append({
                'Name':                           name_,
                'Date':                           date_,
                'Total Routes':                   len(d['routes']),
                'Total Deliveries (POD)':         d['deliveries'],
                'Total Stores Covered':           len(d['stores']),
                'First Start':                    first_start.strftime('%H:%M') if first_start else '',
                'Last End':                       last_end.strftime('%H:%M')    if last_end    else '',
                'Total Working Time (mins)':      working_mins,
                'Total Break Time (mins)':        round(break_mins, 1),
                'Net Working Time (mins)':        net_working,
                'Avg Time per Delivery (mins)':   avg_time,
            })

    rows.sort(key=lambda x: (x['Name'], x['Date']))
    return rows


def build_delivery_details(states: dict) -> list:
    rows = []
    for name, s in states.items():
        for d in s.deliveries:
            rows.append({
                'Delivery Boy':             name,
                'Date':                     d.date,
                'Route No.':                d.route_number,
                'Store Name':               d.store_name,
                'Start Time':               d.start_time.strftime('%H:%M') if d.start_time else '',
                'Store Arrival':            d.store_time.strftime('%H:%M') if d.store_time  else '',
                'POD Time':                 d.pod_time.strftime('%H:%M')   if d.pod_time    else '',
                'Travel Time (mins)':       d.travel_mins if d.travel_mins is not None else 'N/A',
                'Store Time (mins)':        d.store_mins  if d.store_mins  is not None else 'N/A',
                'POD Type':                 d.pod_type,
                'Status':                   (
                    'Delayed'     if d.is_delayed else
                    'Missing POD' if d.missing_pod else
                    'High Travel' if d.high_travel else
                    'OK'
                ),
            })
    rows.sort(key=lambda x: (x['Delivery Boy'], x['Date'], x['Route No.']))
    return rows


def build_route_summary(states: dict) -> list:
    rows = []
    for name, s in states.items():
        for r in s.routes:
            route_deliveries = [
                d for d in s.deliveries
                if d.route_number == r.route_number and d.date == r.date
            ]
            travel_times = [d.travel_mins for d in route_deliveries if d.travel_mins is not None]
            store_times  = [d.store_mins  for d in route_deliveries if d.store_mins  is not None]

            rows.append({
                'Delivery Boy':             name,
                'Date':                     r.date,
                'Route No.':                r.route_number,
                'Start Time':               r.start_time.strftime('%H:%M') if r.start_time else '',
                'End Time':                 r.end_time.strftime('%H:%M')   if r.end_time   else '(Not Ended)',
                'Total Deliveries':         r.deliveries,
                'Total Time (mins)':        r.total_mins,
                'Avg Store Time (mins)':    round(sum(store_times)  / len(store_times),  1) if store_times  else None,
                'Avg Travel Time (mins)':   round(sum(travel_times) / len(travel_times), 1) if travel_times else None,
                'Max Travel Time (mins)':   max(travel_times) if travel_times else None,
                'Min Travel Time (mins)':   min(travel_times) if travel_times else None,
                'Status':                   'Not Ended' if r.not_ended else 'Complete',
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
                'Time':           e.timestamp.strftime('%H:%M') if e.timestamp else '',
                'Exception Type': e.kind,
                'Detail':         e.detail,
            })
    rows.sort(key=lambda x: (x['Date'], x['Time']))
    return rows


def build_store_search(states: dict) -> list:
    """Flat list of every delivery for store-name search."""
    rows = []
    for name, s in states.items():
        for d in s.deliveries:
            rows.append({
                'Delivery Boy': name,
                'Date':         d.date,
                'Route No.':    d.route_number,
                'Store Name':   d.store_name,
                'Store Arrival':d.store_time.strftime('%H:%M') if d.store_time else '',
                'POD Time':     d.pod_time.strftime('%H:%M')   if d.pod_time   else '',
                'Travel Time (mins)': d.travel_mins if d.travel_mins is not None else 'N/A',
                'Store Time (mins)':  d.store_mins  if d.store_mins  is not None else 'N/A',
                'POD Type':     d.pod_type,
                'Status':       (
                    'Delayed'     if d.is_delayed else
                    'Missing POD' if d.missing_pod else
                    'High Travel' if d.high_travel else
                    'OK'
                ),
            })
    return rows
