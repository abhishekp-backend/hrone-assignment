# REVIEW.md

List every defect found in the starter `app/main.py`, including helper functions and endpoints.

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `compute_late_minutes()` | Builds shift start using the punch-in date, which can miscalculate lateness for overnight shifts. | Test a shift starting at 22:00 with a punch-in after midnight. | Calculate the correct shift-start date and compare timezone-consistent timestamps. |
| 2 | `compute_late_minutes()` | Does not validate the shift time; malformed or impossible values can fail or produce incorrect calculations. | Pass `abc` or `25:90` as `shift_start`. | Validate shift times before calculating lateness. |
| 3 | `compute_work_hours()` | Returns negative hours when punch-out precedes punch-in. | Pass punch-in at 18:00 and punch-out at 17:00 on the same day. | Reject punch-out timestamps earlier than punch-in. |
| 4 | `compute_overtime()` | Always constructs shift end on the supplied date, so overnight shifts calculate overtime incorrectly. | Test a 22:00–06:00 shift with punch-out at 06:30 the next day. | Calculate shift end on the correct date, accounting for overnight shifts. |
| 5 | `compute_overtime()` | Assumes compatible timestamp timezones; comparing naive and aware datetimes can raise `TypeError`. | Pass a timezone-aware `punch_out` timestamp. | Normalize timestamps to a consistent timezone before comparison. |
| 6 | `EmployeeIn` | Shift times and `joined_on` are unvalidated strings, allowing malformed employee data into the database. | Create an employee with `shift_start="invalid"` or an impossible date. | Validate shift-time and date formats at the request boundary. |
| 7 | `create_employee()` | The duplicate check and insert are separate, so concurrent requests can create duplicate employee codes. | Send simultaneous requests with the same `emp_code`. | Add a unique index on `emp_code` and handle duplicate-key errors as HTTP 409. |
| 8 | `create_employee()` | `datetime.now()` stores a naive creation timestamp. | Inspect `created_at` and compare it with timezone-aware timestamps. | Store timezone-aware UTC timestamps. |
| 9 | `list_employees()` | Uses `page * page_size` as the skip offset, causing page 1 to skip the first page of records. | Request `page=1&page_size=20`; the first 20 records are skipped. | Use `(page - 1) * page_size`. |
| 10 | `list_employees()` | Counts the entire collection even when filtering by department. | Filter to a department with fewer employees than the total collection. | Use `count_documents(q)` with the same filter as the listing query. |
| 11 | `list_employees()` | Does not constrain page and page size, permitting invalid values and excessively large responses. | Request `page=0`, a negative size, or an excessive size. | Validate positive pagination values and impose a maximum page size. |
| 12 | `PunchInIn` / `punch_in()` | Uses truthiness to check `punched_at`, so timestamp `0` is treated as omitted. | Submit `punched_at=0`; the server substitutes the current time. | Check `punched_at is not None` and validate the timestamp range. |
| 13 | `punch_in()` | Does not check whether the employee exists before accessing `emp["shift_start"]`. | Punch in using an unknown employee code; an internal error may result. | Return HTTP 404 when the employee is missing. |
| 14 | `punch_in()` | Uses naive local timestamps, making attendance dates dependent on the server timezone. | Test near midnight or run the application under a different timezone. | Normalize timestamps to timezone-aware values and derive the attendance date in IST. |
| 15 | `punch_in()` | The existence check followed by insertion is not atomic, allowing concurrent duplicate attendance records. | Send two simultaneous punch-ins for the same employee and date. | Add a unique compound index on `(emp_code, date)` and return HTTP 409 on duplicate-key errors. |
| 16 | `punch_in()` | Assumes employee shift configuration exists and is valid before calculating lateness. | Punch in for an employee with a missing or malformed shift start. | Validate the shift configuration and return a clear client error. |
| 17 | `list_attendance()` | Loads all matching records into application memory and sorts them in Python before pagination. | Populate many records and observe increasing latency and memory use. | Sort and paginate in MongoDB and add indexes suitable for common filters. |
| 18 | `list_attendance()` | Sorts only by date, so records sharing a date have no explicit deterministic order across pages. | Request consecutive pages containing records with identical dates. | Add a stable secondary sort key such as `_id`. |
| 19 | `list_attendance()` | Accepts arbitrary date strings and reversed date ranges. | Submit malformed dates or `date_from` later than `date_to`. | Validate ISO dates and reject reversed ranges. |
| 20 | `list_attendance()` | Does not validate page and page-size values before slicing the result list. | Request page zero or a negative page size. | Validate pagination bounds and set a maximum page size. |

## Examined and not considered defects

- `compute_work_hours()` uses elapsed seconds divided by 3,600, which is appropriate for valid timestamps; rounding to two decimal places is a reasonable representation choice.
- The `$gte` and `$lte` operators provide inclusive date-range filtering when dates are stored in a consistent, sortable ISO format.
- HTTP 409 is a reasonable response for duplicate employee codes; the defect is the lack of database-enforced uniqueness under concurrency, not the status code.
- The existing `history` array can support an audit trail, provided updates append entries consistently and preserve the required history schema.
