# WhatsApp Delivery Tracker

Parse WhatsApp group chat exports into structured delivery reports with Excel output and an interactive web dashboard.

---

## Setup

```bash
pip install -r requirements.txt
```

---

## Usage

### Option A — Web App (recommended)
```bash
python app.py
# Open http://localhost:5050 in your browser
# Drag & drop your .txt export → instant dashboard + Excel download
```

### Option B — CLI (no browser needed)
```bash
python tracker_cli.py your_chat.txt [output.xlsx]
# Prints tables to terminal + writes delivery_report.xlsx
```

---

## How to Export WhatsApp Chat

1. Open the WhatsApp group on your phone
2. Tap ⋮ (Android) or group name (iOS) → More → Export Chat
3. Choose **"Without Media"**
4. Save / share the `.txt` file

---

## Message Format Expected

```
Route 1 Start          ← begins a route
Sharma General Store   ← store visit (any name)
POD                    ← proof of delivery
Krishna Medical
POD
Break Start
Break End
Route 1 End            ← closes the route
```

---

## Output Sections

| Sheet | Contents |
|-------|----------|
| Delivery Summary | Per-person per-day: deliveries, working time, breaks, avg time |
| Delivery Details | Every store visit: arrival, POD time, duration, status |
| Route Summary    | Each route: start/end, delivery count, efficiency |
| Exceptions & Flags | Missing PODs, long delays, gaps, unclosed routes |
| Charts           | Visual bar/donut charts |

---

## Edge Cases Handled

- Multi-line messages
- Android & iOS date formats
- Overlapping routes (multiple delivery boys)
- Missing POD after store
- Route started but not ended
- Delay > 30 min flagged
- Inactivity gap > 60 min flagged
- Break time excluded from net working time
- UTF-8, UTF-8-BOM, Latin-1, CP1252 encodings
