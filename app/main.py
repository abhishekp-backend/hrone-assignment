"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone, time
from typing import Optional
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30), name = "IST")


def create_indexes():
    print(f"Creating indexes in database: {db.name}")

    # Employees: unique employee business code
    db.employees.create_index(
        [("emp_code", 1)],
        unique=True,
        name="uq_employees_emp_code",
    )

    # Employees: department filtering and employee-code ordering
    db.employees.create_index(
        [("department", 1), ("emp_code", 1)],
        name="idx_employees_department_emp_code",
    )

    # Employees: headcount calculations by joining date
    db.employees.create_index(
        [("joined_on", 1)],
        name="idx_employees_joined_on",
    )

    # Attendance: employee/date lookup and uniqueness
    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True,
        name="uq_attendance_emp_code_date",
    )

    # Attendance: date-range queries and date-first sorting
    db.attendance_logs.create_index(
        [("date", -1), ("emp_code", 1)],
        name="idx_attendance_date_emp",
    )

    # Attendance: status filtering within date ranges
    db.attendance_logs.create_index(
        [("status", 1), ("date", -1), ("emp_code", 1)],
        name="idx_attendance_status_date_emp",
    )

    print("Employee indexes:", db.employees.index_information())
    print("Attendance indexes:", db.attendance_logs.index_information())
    print("MongoDB indexes created successfully.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_indexes()
    yield


app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
    lifespan=lifespan,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    punch_in = punch_in.astimezone(IST)

    h, m = map(int, shift_start.split(":"))
    start = punch_in.replace(hour=h, minute=m, second=0, microsecond=0)

    minutes = int((punch_in - start).total_seconds() // 60)
    return minutes if minutes > 10 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    return round((punch_out - punch_in).total_seconds() / 3600, 2)


def compute_overtime(punch_out: datetime, shift_end: str, date_str: str) -> int:
    h, m = map(int, shift_end.split(":"))
    end = datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST)
    return max(0, int((punch_out - end).total_seconds() / 60))

def parse_punch_timestamp(milliseconds: int | float) -> datetime:
    return datetime.fromtimestamp(
        milliseconds / 1000,
        tz=timezone.utc
    )

# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"

class PunchOut(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}) is not None:
        raise HTTPException(409, "emp_code already exists")
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)
    db.employees.insert_one(doc)
    doc.pop("_id", None)
    return doc


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = 1, page_size: int = 20):
    q = {}
    if department:
        q["department"] = department
    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)
    items = list(db.employees.find(q, {"_id": 0}).skip(skip).limit(page_size))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(404, "employee not found")

    ts = (
        parse_punch_timestamp(body.punched_at)
        if body.punched_at is not None
        else datetime.now(timezone.utc)
    )
    d = ts.astimezone(IST).date().isoformat()
    if db.attendance_logs.find_one({"emp_code": body.emp_code, "date": d}):
        raise HTTPException(409, "already punched in for this date")

    shift_start = emp.get("shift_start")
    if not shift_start:
        raise HTTPException(
            status_code=422,
            detail="Employee shift start is missing or invalid"
        )

    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    res = db.attendance_logs.insert_one(doc)
    doc["id"] = str(res.inserted_id)
    doc.pop("_id", None)
    return doc

@app.post("/attendance/punch-out")
def punch_out(body: PunchOut):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    ts = (
        parse_punch_timestamp(body.punched_at)
        if body.punched_at is not None
        else datetime.now(timezone.utc)
    )

    d = ts.astimezone(IST).date().isoformat()

    # Find today's attendance record
    doc = db.attendance_logs.find_one({
        "emp_code": body.emp_code,
        "date": d
    })

    if not doc:
        raise HTTPException(
            status_code=404,
            detail="Punch-in record not found for this date"
        )

    if doc.get("punch_out") is not None:
        raise HTTPException(
            status_code=409,
            detail="Already punched out for this date"
        )

    punch_in_time = doc["punch_in"]

    if punch_in_time.tzinfo is None:
        punch_in_time = punch_in_time.replace(tzinfo=timezone.utc)

    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    if ts < punch_in_time:
        raise HTTPException(
            status_code=400,
            detail="Punch-out time cannot be earlier than punch-in time",
        )

    total_minutes = int((ts - punch_in_time).total_seconds() // 60)

    work_hours = round(total_minutes / 60, 2)

    late_minutes = compute_late_minutes(
        punch_in_time,
        emp["shift_start"]
    )

    attendance_date = datetime.fromisoformat(doc["date"]).date()

    shift_end = datetime.combine(
        attendance_date,
        time.fromisoformat(emp["shift_end"]),
        tzinfo=IST,
    )

    # Handle overnight shifts
    if shift_end <= punch_in_time.astimezone(IST):
        shift_end += timedelta(days=1)

    # Convert to UTC before calculating overtime
    shift_end = shift_end.astimezone(timezone.utc)

    overtime_minutes = max(
            0,
            int((ts - shift_end).total_seconds() // 60)
        )

    if total_minutes < 4 * 60:
        status = "half_day"
        half_day = True
    elif late_minutes > 0:
        status = "late"
        half_day = False
    else:
        status = "present"
        half_day = False

    db.attendance_logs.update_one(
        {"_id": doc["_id"]},
        {
            "$set": {
                "punch_out": ts,
                "work_hours": work_hours,
                "late_minutes": late_minutes,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
                "status": status
            },
            "$push": {
                "history": {
                    "action": "punch_out",
                    "timestamp": ts,
                    "status": status
                }
            }
        }
    )

    updated_doc = db.attendance_logs.find_one({
        "_id": doc["_id"]
    })

    updated_doc["id"] = str(updated_doc.pop("_id"))

    return updated_doc



@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
    docs = list(db.attendance_logs.find(q))
    docs.sort(key=lambda d: d["date"], reverse=True)
    total = len(docs)
    page_docs = docs[(page - 1) * page_size : page * page_size]
    for d in page_docs:
        d["id"] = str(d.pop("_id"))
    return {"items": page_docs, "total": total, "page": page, "page_size": page_size}


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
