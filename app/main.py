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

    db.employees.create_index(
        [("email", 1)],
        unique=True,
        name="uq_employees_email"
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
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(
            status_code=409,
            detail="Employee code already exists"
        )

    if db.employees.find_one({"email": body.email}):
        raise HTTPException(
            status_code=409,
            detail="Email already exists"
        )

    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)

    result = db.employees.insert_one(doc)

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


@app.get("/analytics/leaderboard/late")
def late_comers_leaderboard(
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
):
    match_stage = {"late_minutes": {"$gt": 0}}

    if date_from or date_to:
        match_stage["date"] = {}

        if date_from:
            try:
                datetime.strptime(date_from, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="date_from must use YYYY-MM-DD format",
                )
            match_stage["date"]["$gte"] = date_from

        if date_to:
            try:
                datetime.strptime(date_to, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="date_to must use YYYY-MM-DD format",
                )
            match_stage["date"]["$lte"] = date_to

        if date_from and date_to and date_from > date_to:
            raise HTTPException(
                status_code=400,
                detail="date_from cannot be after date_to",
            )

    pipeline = [
        # 1. Include only attendance records with positive lateness.
        {"$match": match_stage},

        # 2. Calculate total late minutes and late-day count per employee.
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_days": {"$sum": 1},
            }
        },

        # 3. Rank employees by total lateness.
        # Equal totals receive equal ranks; subsequent ranks are skipped.
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {"$rank": {}}
                },
            }
        },

        # 4. Retrieve employee details.
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },

        # 5. Shape the response.
        {
            "$project": {
                "_id": 0,
                "emp_code": "$_id",
                "name": {
                    "$arrayElemAt": ["$employee.name", 0]
                },
                "department": {
                    "$arrayElemAt": ["$employee.department", 0]
                },
                "total_late_minutes": 1,
                "late_days": 1,
                "rank": 1,
            }
        },

        {"$sort": {"total_late_minutes": -1}}
    ]

    results = list(db.attendance_logs.aggregate(pipeline))

    return {
        "count": len(results),
        "items": results,
    }

@app.get("/analytics/departments/summary")
def department_summary(month: str):
    """
    Monthly department summary.
    month format: YYYY-MM
    """

    # Validate the requested month.
    try:
        month_start = datetime.strptime(month, "%Y-%m")
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format",
        )

    if month_start.strftime("%Y-%m") != month:
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format",
        )

    next_month = (
            month_start.replace(day=28) + timedelta(days=4)
    ).replace(day=1)

    month_start_str = month_start.strftime("%Y-%m-%d")
    next_month_str = next_month.strftime("%Y-%m-%d")

    pipeline = [
        # Include employees who have joined by the end of this month.
        {
            "$match": {
                "joined_on": {"$lt": next_month_str}
            }
        },

        # Find each employee's attendance records for the month.
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"employee_code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {
                                        "$eq": [
                                            "$emp_code",
                                            "$$employee_code",
                                        ]
                                    },
                                    {
                                        "$gte": [
                                            "$date",
                                            month_start_str,
                                        ]
                                    },
                                    {
                                        "$lt": [
                                            "$date",
                                            next_month_str,
                                        ]
                                    },
                                ]
                            }
                        }
                    }
                ],
                "as": "attendance",
            }
        },

        # Keep employees even when they have no attendance records.
        {
            "$unwind": {
                "path": "$attendance",
                "preserveNullAndEmptyArrays": True,
            }
        },

        # Aggregate per employee first, preserving one headcount per person.
        {
            "$group": {
                "_id": {
                    "department": "$department",
                    "emp_code": "$emp_code",
                },
                "attendance_records": {
                    "$sum": {
                        "$cond": [
                            {
                                "$ne": [
                                    {"$ifNull": ["$attendance.date", None]},
                                    None,
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$in": [
                                    "$attendance.status",
                                    ["PRESENT", "WFH", "ON_DUTY"],
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$attendance.half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {
                        "$ifNull": ["$attendance.late_minutes", 0]
                    }
                },
                "total_overtime_minutes": {
                    "$sum": {
                        "$ifNull": ["$attendance.overtime_minutes", 0]
                    }
                },
                "work_hours_sum": {
                    "$sum": {
                        "$ifNull": ["$attendance.work_hours", 0]
                    }
                },
                "work_hours_count": {
                    "$sum": {
                        "$cond": [
                            {"$ne": ["$attendance.work_hours", None]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },

        # Aggregate employee metrics by department.
        {
            "$group": {
                "_id": "$_id.department",
                "headcount": {"$sum": 1},
                "attendance_records": {"$sum": "$attendance_records"},
                "present_days": {"$sum": "$present_days"},
                "total_late_minutes": {"$sum": "$total_late_minutes"},
                "total_overtime_minutes": {
                    "$sum": "$total_overtime_minutes"
                },
                "work_hours_sum": {"$sum": "$work_hours_sum"},
                "work_hours_count": {"$sum": "$work_hours_count"},
            }
        },

        # Average across attendance records, not employee averages.
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": 1,
                "attendance_records": 1,
                "present_days": 1,
                "total_late_minutes": 1,
                "total_overtime_minutes": 1,
                "average_work_hours": {
                    "$cond": [
                        {"$gt": ["$work_hours_count", 0]},
                        {
                            "$round": [
                                {
                                    "$divide": [
                                        "$work_hours_sum",
                                        "$work_hours_count",
                                    ]
                                },
                                2,
                            ]
                        },
                        None,
                    ]
                },
            }
        },

        {"$sort": {"department": 1}},
    ]

    items = list(db.employees.aggregate(pipeline))

    return {
        "month": month,
        "count": len(items),
        "items": items,
    }


@app.get("/attendance/{emp_code}/{date}/history")
def get_attendance_history(emp_code: str, date: str):
    # Find the attendance record for this employee on this date
    record = db.attendance_logs.find_one(
        {"emp_code": emp_code, "date": date},
        {"_id": 0, "emp_code": 1, "date": 1, "history": 1}
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Attendance record not found"
        )

    return {
        "emp_code": record["emp_code"],
        "date": record["date"],
        "history": record.get("history", [])
    }

@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str):
    # 1. Validate the requested date
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="date must be in YYYY-MM-DD format"
        )

    # 2. Find that day's attendance record
    record = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date
    })

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Attendance record not found"
        )

    # 3. Find the employee's scheduled shift
    employee = db.employees.find_one({"emp_code": emp_code})

    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found"
        )

    punch_in = record.get("punch_in")
    punch_out = record.get("punch_out")

    if not punch_in or not punch_out:
        raise HTTPException(
            status_code=400,
            detail="Both punch-in and punch-out are required"
        )

    def parse_timestamp(value):
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )

        if parsed.tzinfo is None:
            raise HTTPException(
                status_code=422,
                detail="Punch timestamps must include a timezone"
            )

        return parsed.astimezone(timezone.utc)

    punch_in_time = parse_timestamp(punch_in)
    punch_out_time = parse_timestamp(punch_out)

    if punch_out_time < punch_in_time:
        raise HTTPException(
            status_code=422,
            detail="Punch-out cannot be before punch-in"
        )

    # 4. Recompute derived fields
    work_hours = round(
        (punch_out_time - punch_in_time).total_seconds() / 3600,
        2
    )

    # Use the same timezone as the rest of your application.
    # This assumes IST is already defined in your code.
    local_in = punch_in_time.astimezone(IST)
    attendance_day = datetime.strptime(date, "%Y-%m-%d").date()

    shift_start = datetime.combine(
        attendance_day,
        time.fromisoformat(employee["shift_start"].zfill(5)),
        tzinfo=IST
    )

    shift_end = datetime.combine(
        attendance_day,
        time.fromisoformat(employee["shift_end"].zfill(5)),
        tzinfo=IST
    )

    if shift_end <= shift_start:
        shift_end += timedelta(days=1)

    # If the employee punched in after midnight for an overnight shift,
    # adjust the scheduled shift to the preceding day.
    if local_in.date() > attendance_day:
        shift_start -= timedelta(days=1)
        shift_end -= timedelta(days=1)

    late_minutes = max(
        0,
        int((local_in - shift_start).total_seconds() // 60)
    )

    overtime_minutes = max(
        0,
        int((
                    punch_out_time - shift_end.astimezone(timezone.utc)
            ).total_seconds() // 60)
    )

    half_day = work_hours < 4

    if half_day:
        status = "half_day"
    elif late_minutes > 0:
        status = "late"
    else:
        status = "present"

    # 5. Record before/after values for the audit history
    fields = {
        "work_hours": work_hours,
        "late_minutes": late_minutes,
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
        "status": status
    }

    changes = {}
    for field, new_value in fields.items():
        old_value = record.get(field)
        if old_value != new_value:
            changes[field] = {
                "before": old_value,
                "after": new_value
            }

    history_entry = {
        "action": "regularize",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "changes": changes
    }

    # 6. Update the record and append to existing history atomically
    db.attendance_logs.update_one(
        {"_id": record["_id"]},
        {
            "$set": fields,
            "$push": {"history": history_entry}
        }
    )

    # 7. Return the updated document
    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]},
        {"_id": 0}
    )

    return updated


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly_summary(emp_code: str, month: str):
    # 1. Validate month format
    try:
        month_start = datetime.strptime(month, "%Y-%m")
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format"
        )

    if month_start.strftime("%Y-%m") != month:
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format"
        )

    # 2. Check that the employee exists
    employee = db.employees.find_one(
        {"emp_code": emp_code},
        {"_id": 0, "emp_code": 1, "name": 1, "department": 1}
    )

    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found"
        )

    # 3. Define the month range
    next_month = (
            month_start.replace(day=28) + timedelta(days=4)
    ).replace(day=1)

    start_date = month_start.strftime("%Y-%m-%d")
    end_date = next_month.strftime("%Y-%m-%d")

    # 4. Aggregate attendance records in MongoDB
    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": start_date,
                    "$lt": end_date
                }
            }
        },
        {
            "$set": {
                "_day_of_week": {
                    "$dayOfWeek": {
                        "$dateFromString": {
                            "dateString": "$date",
                            "format": "%Y-%m-%d"
                        }
                    }
                }
            }
        },
        {
            "$group": {
                "_id": None,

                "attendance_records": {
                    "$sum": 1
                },

                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$status",
                                            [
                                                "PRESENT",
                                                "WFH",
                                                "ON_DUTY"
                                            ]
                                        ]
                                    },
                                    {
                                        "$gte": ["$_day_of_week", 2]
                                    },
                                    {
                                        "$lte": ["$_day_of_week", 6]
                                    }
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1
                                ]
                            },
                            0
                        ]
                    }
                },

                "total_work_hours": {
                    "$sum": {
                        "$ifNull": ["$work_hours", 0]
                    }
                },

                "average_work_hours": {
                    "$avg": "$work_hours"
                },

                "total_late_minutes": {
                    "$sum": {
                        "$ifNull": ["$late_minutes", 0]
                    }
                },

                "total_overtime_minutes": {
                    "$sum": {
                        "$ifNull": ["$overtime_minutes", 0]
                    }
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "attendance_records": 1,
                "present_days": 1,
                "total_work_hours": {"$round": ["$total_work_hours", 2]},
                "average_work_hours": {
                    "$round": ["$average_work_hours", 2]
                },
                "total_late_minutes": 1,
                "total_overtime_minutes": 1
            }
        }
    ]

    result = list(db.attendance_logs.aggregate(pipeline))

    # 5. Return zero totals if no attendance records exist
    summary = result[0] if result else {
        "attendance_records": 0,
        "present_days": 0,
        "total_work_hours": 0,
        "average_work_hours": None,
        "total_late_minutes": 0,
        "total_overtime_minutes": 0
    }

    return {
        "emp_code": employee["emp_code"],
        "name": employee.get("name"),
        "department": employee.get("department"),
        "month": month,
        **summary
    }

@app.get("/analytics/departments/{department}/trend")
def department_trend(
        department: str,
        date_from: str,
        date_to: str
):
    # 1. Validate the date range
    try:
        start = datetime.strptime(date_from, "%Y-%m-%d")
        end = datetime.strptime(date_to, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Dates must use YYYY-MM-DD format"
        )

    if (
            start.strftime("%Y-%m-%d") != date_from
            or end.strftime("%Y-%m-%d") != date_to
    ):
        raise HTTPException(
            status_code=422,
            detail="Dates must use YYYY-MM-DD format"
        )

    if start > end:
        raise HTTPException(
            status_code=422,
            detail="date_from must be on or before date_to"
        )

    # 2. Check that the department exists
    if db.employees.count_documents({"department": department}) == 0:
        raise HTTPException(
            status_code=404,
            detail="Department not found"
        )

    # 3. Aggregate daily attendance for employees in this department
    pipeline = [
        {
            "$match": {
                "department": department
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"employee_code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {
                                        "$eq": [
                                            "$emp_code",
                                            "$$employee_code"
                                        ]
                                    },
                                    {"$gte": ["$date", date_from]},
                                    {
                                        "$lte": [
                                            "$date",
                                            date_to
                                        ]
                                    }
                                ]
                            }
                        }
                    },
                    {
                        "$addFields": {
                            "day": {
                                "$dateFromString": {
                                    "dateString": "$date",
                                    "format": "%Y-%m-%d",
                                    "timezone": "Asia/Kolkata"
                                }
                            },
                            "normalized_status": {
                                "$toUpper": {
                                    "$ifNull": ["$status", ""]
                                }
                            }
                        }
                    }
                ],
                "as": "logs"
            }
        },
        {"$unwind": "$logs"},
        {
            "$group": {
                "_id": "$logs.day",
                "attendance_records": {"$sum": 1},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$in": [
                                    "$logs.normalized_status",
                                    ["PRESENT", "WFH", "ON_DUTY"]
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$logs.half_day", True]},
                                    0.5,
                                    1
                                ]
                            },
                            0
                        ]
                    }
                },
                "late_minutes": {
                    "$sum": {
                        "$ifNull": ["$logs.late_minutes", 0]
                    }
                },
                "overtime_minutes": {
                    "$sum": {
                        "$ifNull": ["$logs.overtime_minutes", 0]
                    }
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "day": "$_id",
                "attendance_records": 1,
                "present_days": 1,
                "late_minutes": 1,
                "overtime_minutes": 1
            }
        },
        {"$sort": {"day": 1}},
        {
            "$densify": {
                "field": "day",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [
                        start,
                        datetime.fromordinal(end.toordinal() + 1)
                    ]
                }
            }
        },
        {
            "$fill": {
                "output": {
                    "attendance_records": {"value": 0},
                    "present_days": {"value": 0},
                    "late_minutes": {"value": 0},
                    "overtime_minutes": {"value": 0}
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"day": 1},
                "output": {
                    "moving_average_7d": {
                        "$avg": "$present_days",
                        "window": {
                            "range": [-6, 0],
                            "unit": "day"
                        }
                    }
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$day",
                        "timezone": "Asia/Kolkata"
                    }
                },
                "attendance_records": 1,
                "present_days": 1,
                "late_minutes": 1,
                "overtime_minutes": 1,
                "moving_average_7d": {
                    "$round": ["$moving_average_7d", 2]
                }
            }
        },
        {"$sort": {"date": 1}}
    ]

    items = list(db.employees.aggregate(pipeline))

    return {
        "department": department,
        "date_from": date_from,
        "date_to": date_to,
        "count": len(items),
        "items": items
    }

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
