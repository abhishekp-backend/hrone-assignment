# DECISIONS.md

### 1. Indexes
I created indexes to support employee lookups, prevent duplicate employee codes and emails, enforce one attendance record per employee per date, and speed up attendance queries by date and status. I also use department-related indexes for employee filtering. I considered adding more indexes for every filter but avoided unnecessary indexes because they increase storage and write costs.

### 2. Punch-in race
If two identical punch-in requests arrive simultaneously, the unique compound index on employee code and date prevents both records from being inserted. One request succeeds; the other receives a duplicate-key error, which the API should handle safely.

### 3. Ties
The late leaderboard sorts employees by late minutes and applies the requested limit. If employees tie at the cutoff, the result depends on the secondary sort and limit logic; I would use employee code as a deterministic tie-breaker.

### 4. Headcount
The department summary starts from employees and uses a lookup into attendance logs. This keeps employees with no matching logs in the aggregation, so they are still included in headcount.

### 5. Scaling and explain endpoints
For 100x the data, I would review query plans, add only justified indexes, and consider pre-aggregating analytics. I tested query plans through these endpoints:

- `GET /admin/explain/attendance_list?page=1&page_size=20&status=PRESENT`
- `GET /admin/explain/employee_monthly?emp_code=EMP001&year=2026&month=10`
- `GET /admin/explain/department_summary?month=2026-10`
- `GET /admin/explain/late_leaderboard?limit=10`
- `GET /admin/explain/department_trend?department=Engineering&date_from=2026-10-01&date_to=2026-10-11`

These endpoints use execution statistics to help inspect query performance.