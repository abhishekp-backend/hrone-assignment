def create_indexes():
    # Employees: unique business identifier
    employees.create_index(
        [("emp_code", 1)],
        unique=True,
        name="uq_employees_emp_code",
    )

    # Employees: department filtering and sorted employee listing
    employees.create_index(
        [("department", 1), ("emp_code", 1)],
        name="idx_employees_department_emp_code",
    )

    # Employees: headcount by joining date
    employees.create_index(
        [("joined_on", 1)],
        name="idx_employees_joined_on",
    )

    # Attendance: employee/date lookup and one record per employee per day
    attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True,
        name="uq_attendance_emp_code_date",
    )

    # Attendance: date-range filtering and status filtering
    attendance_logs.create_index(
        [("date", 1), ("status", 1), ("emp_code", 1)],
        name="idx_attendance_date_status_emp",
    )

    # Attendance: status-first queries and date ranges
    attendance_logs.create_index(
        [("status", 1), ("date", 1), ("emp_code", 1)],
        name="idx_attendance_status_date_emp",
    )
