import logging
import json
import os
import datetime
import csv
import io
import re
from flask import send_file, Flask, request, render_template, redirect, url_for, session, flash, Response
from functools import wraps
# ... in main.py, at the top ...
from flask import send_file, Flask, request, render_template, redirect, url_for, session, flash, Response, abort
# ...
# --- START: Re-added itsdangerous for secure links ---
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature

# --- END: Re-added ---

# --- Configuration ---
app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

# --- START: Initialize Serializer ---
s = URLSafeTimedSerializer(app.secret_key)
# --- END: Initialize Serializer ---


# --- Constants ---
COMPONENTS_DB = 'components.json'
REQUESTS_DB = 'requests.json'
USERS_DB = 'users.json'

# --- Logging (Unchanged) ---
logging.basicConfig(
    filename='activity.log', level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
audit_handler = logging.FileHandler('audit.log')
audit_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
audit_logger = logging.getLogger('audit_logger')
audit_logger.setLevel(logging.INFO)
audit_logger.addHandler(audit_handler)


# --- JSON Helper Functions (Unchanged) ---

# ... (in main.py, near the other helper functions) ...

def get_augmented_components():
    """
    Loads components and augments them with calculated working/not working counts.
    """
    components = load_components()
    requests = load_requests()

    # Create a map to store not_working counts
    not_working_map = {}
    for req in requests:
        if req['status'] == 'Returned' and req.get('not_working_count', 0) > 0:
            comp_name = req['component_name']
            count = req['not_working_count']
            not_working_map[comp_name] = not_working_map.get(comp_name, 0) + count

    # Augment the components list with new data
    augmented_components = []
    for comp in components:
        not_working_count = not_working_map.get(comp['name'], 0)
        working_count = comp['total'] - not_working_count

        # Add the new calculated fields
        comp['not_working_count'] = not_working_count
        comp['working_count'] = working_count

        # 'available' field from components.json already represents (working - issued)

        augmented_components.append(comp)

    return augmented_components

def load_components():
    with open(COMPONENTS_DB, 'r') as f: return json.load(f)


def save_components(data):
    with open(COMPONENTS_DB, 'w') as f: json.dump(data, f, indent=4)


def load_requests():
    if not os.path.exists(REQUESTS_DB) or os.path.getsize(REQUESTS_DB) == 0: return []
    with open(REQUESTS_DB, 'r') as f: return json.load(f)


def save_requests(data):
    with open(REQUESTS_DB, 'w') as f: json.dump(data, f, indent=4)


def load_staff_users():
    with open(USERS_DB, 'r') as f: return json.load(f)


def get_staff_by_email(email):
    for user in load_staff_users():
        if user['email'] == email:
            return user
    return None


# --- Email Parsing Logic (Unchanged) ---
CAMPUS_MAP = {'ch': 'Chennai'}
SCHOOL_MAP = {'en': 'School of Engineering', 'sc': 'School of Computing'}
DEPT_MAP = {
    'rai': 'Robotics & AI', 'cse': 'Computer Science', 'ece': 'Electronics & Comm.'
}
YEAR_MAP = {
    '25': '1st Year', '24': '2nd Year', '23': '3rd Year', '22': '4th Year'
}
STUDENT_DOMAIN = 'ch.students.amrita.edu'


def parse_student_email(email):
    try:
        username, domain = email.split('@')
        if domain != STUDENT_DOMAIN: return None
        parts = username.split('.')
        if len(parts) != 3: return None
        campus_code, school_code, id_part = parts[0], parts[1], parts[2]
        dept_code = id_part[2:5]
        roll_number = id_part[5:]
        year_code = roll_number[:2]
        user_data = {
            'email': email, 'role': 'student',
            'name': f"Student {roll_number}",
            'campus': CAMPUS_MAP.get(campus_code, campus_code.upper()),
            'school': SCHOOL_MAP.get(school_code, school_code.upper()),
            'department': DEPT_MAP.get(dept_code, dept_code.upper()),
            'year': YEAR_MAP.get(year_code, f"Year {year_code}"),
            'roll_number': roll_number
        }
        return user_data
    except Exception as e:
        app.logger.error(f"Failed to parse email {email}: {e}")
        return None


# --- Routes ---

@app.route('/')
def home():
    error = session.pop('error', None)
    return render_template('index.html', error=error)


# --- Login Route (Unchanged) ---
@app.route('/login', methods=['POST'])
def login():
    email = request.form.get('email')
    password = request.form.get('password')
    staff_user = get_staff_by_email(email)
    if staff_user:
        if staff_user['password'] == password:
            app.logger.info(f'LOGIN SUCCESS (Staff): User "{email}" ({staff_user["role"]}) logged in.')
            session['user'] = staff_user
            if staff_user['role'] == 'admin':
                return redirect(url_for('admin_dashboard'))
            elif staff_user['role'] == 'hod':
                return redirect(url_for('hod_dashboard'))
            elif staff_user['role'] == 'mentor':
                return redirect(url_for('mentor_dashboard'))
            else:
                return redirect(url_for('tech_dashboard'))
        else:
            app.logger.warning(f'LOGIN FAILED (Staff): Bad password for user "{email}"')
            session['error'] = 'Invalid email or password.'
            return redirect(url_for('home'))
    student_user = parse_student_email(email)
    if student_user:
        app.logger.info(f'LOGIN SUCCESS (Student): Parsed user "{email}"')
        session['user'] = student_user
        return redirect(url_for('student_dashboard'))
    app.logger.warning(f'LOGIN FAILED: Unknown user or invalid email format "{email}"')
    session['error'] = 'Invalid email or password.'
    return redirect(url_for('home'))


@app.route('/logout')
def logout():
    email = session.pop('user', {'email': 'Unknown'})['email']
    app.logger.info(f'LOGOUT: User "{email}" logged out.')
    return redirect(url_for('home'))


# --- Security Wrapper (Unchanged) ---
def role_required(roles):
    # Make sure roles is a list, even if a single string is passed
    if isinstance(roles, str):
        roles = [roles]

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user' not in session or session['user']['role'] not in roles:
                flash('You do not have permission to access this page.', 'error')
                return redirect(url_for('home'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator
# --- END: MODIFIED Security Wrapper ---


# --- Student Routes ---
@app.route('/student_dashboard')
@role_required('student')
def student_dashboard():
    user = session['user']

    # --- START MODIFICATION ---
    # Use the new helper function to get all component data
    all_components = load_components()
    # --- END MODIFICATION ---

    all_requests = load_requests()
    my_requests = [req for req in all_requests if req['student_email'] == user['email']]
    today = datetime.date.today().strftime("%Y-%m-%d")
    max_date = (datetime.date.today() + datetime.timedelta(days=30)).strftime("%Y-%m-%d")

    # Filter components for the *request form*
    for comp in all_components:
        comp['available'] = comp['working_quantity'] - comp['issued_quantity']
    # --- END MODIFICATION ---
    available_components_for_form = [c for c in all_components if c['available'] > 0]

    return render_template('student_dashboard.html',
                           user=user,
                           components=all_components,  # Pass augmented list to availability table
                           available_components=available_components_for_form,  # Pass filtered list to form
                           my_requests=my_requests,
                           today=today,
                           max_date=max_date)


# ... (in main.py, replace the old /request_component) ...

# --- START: HEAVILY MODIFIED REQUEST ROUTE (with Pre-flight Validation) ---
@app.route('/request_component', methods=['POST'])
@role_required('student')
def request_component():
    user = session['user']
    all_requests = load_requests()
    all_components = load_components()

    mentor_name = request.form.get('mentor_name')
    mentor_email = request.form.get('mentor_email')
    project_description = request.form.get('project_description')
    return_date_str = request.form.get('return_date')
    component_names = request.form.getlist('component[]')
    quantities = request.form.getlist('quantity[]')
    for comp in all_components:
        comp['available'] = comp['working_quantity'] - comp['issued_quantity']
    if not component_names or not quantities or len(component_names) != len(quantities):
        flash('Error: Mismatched component or quantity data. Please try again.', 'error')
        return redirect(url_for('student_dashboard'))
    # --- START MODIFICATION: Server-side date check ---
    request_date_dt = datetime.datetime.now()
    return_date_dt = datetime.datetime.strptime(return_date_str, '%Y-%m-%d')

    # --- THIS IS THE FIXED LINE ---
    # We must compare .date() to .date()
    duration = (return_date_dt.date() - request_date_dt.date()).days + 1

    if duration > 30:
        flash(
            f"Error: The selected return date is more than 30 days away. The maximum borrowing period is 30 days.",
            'error')
        return redirect(url_for('student_dashboard'))
    if duration < 1:
        flash(f"Error: The return date must be today or in the future.", 'error')
        return redirect(url_for('student_dashboard'))
    # --- END MODIFICATION ---
    # --- START: NEW PRE-FLIGHT VALIDATION LOGIC ---

    # 1. Aggregate all requested items in the batch
    batch_requirements = {}
    for i in range(len(component_names)):
        comp_name = component_names[i].strip()  # Clean whitespace
        try:
            quantity = int(quantities[i])
            if quantity <= 0:
                flash(f"Error: Invalid quantity for '{comp_name}'. Must be 1 or more.", 'error')
                return redirect(url_for('student_dashboard'))

            # Add to the total needed for this batch
            batch_requirements[comp_name] = batch_requirements.get(comp_name, 0) + quantity

        except ValueError:
            flash(f"Error: Invalid quantity for '{comp_name}'.", 'error')
            return redirect(url_for('student_dashboard'))

    if not batch_requirements:
        flash('Error: No valid components submitted.', 'error')
        return redirect(url_for('student_dashboard'))

    # 2. Check aggregated requirements against the database
    for comp_name, total_needed in batch_requirements.items():
        component_obj = next((c for c in all_components if c['name'] == comp_name), None)

        if not component_obj:
            flash(f"Error: Component name mismatch. Item '{comp_name}' does not exist.", 'error')
            return redirect(url_for('student_dashboard'))

        # --- MODIFICATION: Check new calculated availability ---
        available_stock = component_obj['working_quantity'] - component_obj['issued_quantity']
        if total_needed > available_stock:
            flash(
                f"Error: Insufficient stock for '{comp_name}'. You requested {total_needed}, but only {available_stock} are available.",
                'error')
            return redirect(url_for('student_dashboard'))

        # Check 2: Insufficient Quantity
        if total_needed > component_obj['available']:
            flash(
                f"Error: Insufficient stock for '{comp_name}'. You requested {total_needed}, but only {component_obj['available']} are available.",
                'error')
            return redirect(url_for('student_dashboard'))

    # --- END: NEW PRE-FLIGHT VALIDATION LOGIC ---
    # If we get here, all items are valid and in stock.

    batch_id = f"B-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
    batch_token = s.dumps(batch_id)

    request_date = datetime.datetime.now()
    return_date = datetime.datetime.strptime(return_date_str, '%Y-%m-%d')
    duration = (return_date - request_date).days + 1
    current_request_id = len(all_requests) + 1
    new_requests_list = []

    # Create the individual request objects (now that we know they are valid)
    for comp_name, quantity in batch_requirements.items():
        component_obj = next((c for c in all_components if c['name'] == comp_name), None)

        new_request = {
            "id": current_request_id,
            "batch_id": batch_id,
            "status": "Pending Mentor",
            "request_timestamp": request_date.strftime("%Y-%m-%d %H:%M"),
            # --- START MODIFICATION: Add new remark fields ---
            "hod_remarks": None,
            "incharge_remarks": None,
            # --- END MODIFICATION ---
            "student_email": user['email'],
            "student_name": user['name'],
            "student_dept": user['department'],
            "student_year": user['year'],
            "component_id": component_obj['id'],
            "component_name": component_obj['name'],
            "quantity": quantity,
            "project_description": project_description,
            "due_date": return_date_str,
            "duration_days": duration,
            "mentor_name": mentor_name,
            "mentor_email": mentor_email,
            "mentor_approval_token": batch_token,
            "mentor_remarks": None,
            "mentor_approval_timestamp": None,
            "hod_approval_timestamp": None,
            "approver_email": None,
            "approval_timestamp": None,
            "issue_timestamp": None,
            "actual_return_timestamp": None,
            # --- START: ADD THESE NEW FIELDS ---
            "working_count": None,
            "not_working_count": None,
            "tech_remarks": None
            # --- END: ADD THESE NEW FIELDS ---
        }

        new_requests_list.append(new_request)
        current_request_id += 1

    all_requests.extend(new_requests_list)
    save_requests(all_requests)

    app.logger.info(
        f'REQUEST BATCH SUBMITTED: User "{user["email"]}" submitted batch {batch_id} with {len(new_requests_list)} items. Awaiting Mentor.')
    flash(f'{len(new_requests_list)} component request(s) have been validated and submitted as a batch.', 'success')
    return redirect(url_for('student_dashboard'))


# --- END: HEAVILY MODIFIED REQUEST ROUTE ---

# --- END: MODIFIED /request_component ---

# --- START: MODIFIED /approve/mentor/<token> ---
@app.route('/approve/mentor/<token>', methods=['GET', 'POST'])
def mentor_approval(token):
    try:
        # --- MODIFICATION: Token now contains the batch_id ---
        batch_id = s.loads(token, max_age=259200)
    except SignatureExpired:
        return render_template('mentor_response.html', title="Expired",
                               message="This approval link has expired (older than 72 hours). Please ask the student to resubmit their request."), 400
    except BadTimeSignature:
        return render_template('mentor_response.html', title="Invalid Link",
                               message="This approval link is invalid or has already been used."), 400

    all_requests = load_requests()

    # --- MODIFICATION: Find *all* requests in the batch that are pending ---
    batch_requests = [req for req in all_requests if
                      req.get('batch_id') == batch_id and req['status'] == 'Pending Mentor']

    if not batch_requests:
        # Check if they were *already* processed
        already_processed = any(req.get('batch_id') == batch_id for req in all_requests)
        if already_processed:
            return render_template('mentor_response.html', title="Already Processed",
                                   message="This request batch has already been processed (approved, rejected, or updated)."), 400
        else:
            return render_template('mentor_response.html', title="Not Found",
                                   message="This request batch could not be found."), 404

        # ... in main.py, inside @app.route('/approve/mentor/<token>', methods=['GET', 'POST']) ...

    if request.method == 'POST':
        new_status = request.form.get('new_status')  # "Approved" or "Rejected"
        mentor_remarks = request.form.get('mentor_remarks', '').strip()

        # --- START: MODIFIED BACKEND VALIDATION ---


        # This logic now runs only if:
        # 1. The status is "Rejected"
        # 2. The status is "Approved" AND the disclaimer was checked

        approval_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        for req in batch_requests:
            req['mentor_approval_timestamp'] = approval_time
            req['mentor_approval_token'] = None
            req['mentor_remarks'] = mentor_remarks if mentor_remarks else None

            if new_status == 'Approved':
                req['status'] = 'Pending HOD'
            elif new_status == 'Rejected':
                req['status'] = 'Rejected'

        save_requests(all_requests)

        if new_status == 'Approved':
            return render_template('mentor_response.html', title="Approved",
                                   message="Thank you. The request batch has been approved and forwarded to the HOD.")
        else:
            return render_template('mentor_response.html', title="Rejected",
                                   message="The request batch has been marked as rejected.")

    # If GET request, show the approval form
    return render_template('mentor_approval.html',
                           batch_requests=batch_requests,
                           shared_request=batch_requests[0],
                           token=token)


# --- START: MODIFIED MENTOR DASHBOARD ROUTES ---
@app.route('/mentor_dashboard')
@role_required('mentor')
def mentor_dashboard():
    all_requests = load_requests()
    pending_mentor_requests = [req for req in all_requests if req['status'] == 'Pending Mentor']
    pending_mentor_requests.sort(key=lambda x: x['request_timestamp'], reverse=True)

    # --- MODIFICATION: Group requests by batch_id for the template ---
    grouped_requests = {}
    for req in pending_mentor_requests:
        batch_id = req.get('batch_id')
        if not batch_id:
            # Handle old requests without a batch_id as individual batches
            batch_id = f"req-{req['id']}"

        if batch_id not in grouped_requests:
            grouped_requests[batch_id] = []
        grouped_requests[batch_id].append(req)

    return render_template('mentor_dashboard.html',
                           user=session['user'],
                           grouped_requests=grouped_requests)  # Pass grouped data


@app.route('/mentor/update_request', methods=['POST'])
@role_required('mentor')
def mentor_update_request():
    # --- MODIFICATION: We now receive batch_id instead of request_id ---
    batch_id = request.form.get('batch_id')
    new_status = request.form.get('new_status')

    if not batch_id:
        flash('Error: No Batch ID provided.', 'error')
        return redirect(url_for('mentor_dashboard'))

    all_requests = load_requests()

    # Find all requests in this batch
    if batch_id.startswith('req-'):
        # Handle single, non-batch request
        req_id = int(batch_id.split('-')[1])
        batch_requests = [req for req in all_requests if req['id'] == req_id and req['status'] == 'Pending Mentor']
    else:
        # Handle normal batch
        batch_requests = [req for req in all_requests if
                          req.get('batch_id') == batch_id and req['status'] == 'Pending Mentor']

    if not batch_requests:
        flash('Error: Request batch not found or already processed.', 'error')
        return redirect(url_for('mentor_dashboard'))

    approval_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # --- MODIFICATION: Update all requests in the batch ---
    for req in batch_requests:
        req['mentor_approval_timestamp'] = approval_time
        req['mentor_approval_token'] = None  # Invalidate token
        # Note: No remarks from dashboard
        if new_status == 'Approved':
            req['status'] = 'Pending HOD'
        elif new_status == 'Rejected':
            req['status'] = 'Rejected'

    save_requests(all_requests)

    if new_status == 'Approved':
        flash(f'Request batch {batch_id} approved and forwarded to HOD.', 'success')
    elif new_status == 'Rejected':
        flash(f'Request batch {batch_id} has been rejected.', 'success')

    return redirect(url_for('mentor_dashboard'))


# --- END: MODIFIED MENTOR DASHBOARD ROUTES ---

# ... (in main.py, HOD Routes section) ...

# --- START: MODIFIED HOD ROUTES (for Batch Approval) ---
@app.route('/hod_dashboard')
@role_required('hod')
def hod_dashboard():
    # --- START MODIFICATION ---
    all_requests = load_requests()
    components = all_components = load_components()
    for comp in all_components:
        comp['available_for_issue'] = comp['working_quantity'] - comp['issued_quantity']
    # --- END MODIFICATION ---
    available_components_for_form = [c for c in all_components if c['available_for_issue'] > 0]
    # Load components

    # 1. Get requests for the 'Pending' tab
    pending_hod_requests = []
    # 2. Get requests for the 'History' tab
    other_requests = []

    for req in all_requests:
        if req['status'] == 'Pending HOD':
            pending_hod_requests.append(req)
        else:
            other_requests.append(req)  # All other requests go to history

    pending_hod_requests.sort(key=lambda x: x['request_timestamp'], reverse=True)
    other_requests.sort(key=lambda x: x['request_timestamp'], reverse=True)

    # Group the pending ones by batch_id
    grouped_requests = {}
    for req in pending_hod_requests:
        batch_id = req.get('batch_id', f"req-{req['id']}")
        if batch_id not in grouped_requests:
            grouped_requests[batch_id] = []
        grouped_requests[batch_id].append(req)

    return render_template('hod_dashboard.html',
                           user=session['user'],
                           grouped_requests=grouped_requests,
                           other_requests=other_requests,  # Pass history
                           components=components)  # Pass components

# ... in main.py, replace the entire @app.route('/hod/update_request') function ...

@app.route('/hod/update_request', methods=['POST'])
@role_required('hod')
def hod_update_request():
    batch_id = request.form.get('batch_id')
    new_status = request.form.get('new_status')

    # --- START MODIFICATION: Get remarks from form ---
    hod_remarks = request.form.get('hod_remarks', '').strip()
    # --- END MODIFICATION ---

    if not batch_id:
        flash('Error: No Batch ID provided.', 'error')
        return redirect(url_for('hod_dashboard'))

    all_requests = load_requests()

    if batch_id.startswith('req-'):
        req_id = int(batch_id.split('-')[1])
        batch_requests = [req for req in all_requests if req['id'] == req_id and req['status'] == 'Pending HOD']
    else:
        batch_requests = [req for req in all_requests if
                          req.get('batch_id') == batch_id and req['status'] == 'Pending HOD']

    if not batch_requests:
        flash('Error: Request batch not found or already processed.', 'error')
        return redirect(url_for('hod_dashboard'))

    approval_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    for req in batch_requests:
        req['hod_approval_timestamp'] = approval_time

        # --- START MODIFICATION: Save remarks ---
        req['hod_remarks'] = hod_remarks if hod_remarks else None
        # --- END MODIFICATION ---

        if new_status == 'Approved':
            req['status'] = 'Pending Incharge'
        elif new_status == 'Rejected':
            req['status'] = 'Rejected'

    save_requests(all_requests)

    if new_status == 'Approved':
        flash(f'Request batch {batch_id} approved and forwarded to Lab Incharge.', 'success')
    elif new_status == 'Rejected':
        flash(f'Request batch {batch_id} has been rejected (Remarks saved).', 'success')

    return redirect(url_for('hod_dashboard'))
# --- END: MODIFIED HOD ROUTES ---
@app.route('/admin')
@role_required('admin')
def admin_dashboard():
    all_requests = load_requests()

    components = all_components = load_components()
    for comp in all_components:
        comp['available_for_issue'] = comp['working_quantity'] - comp['issued_quantity']
    # --- END MODIFICATION ---
    # available_components_for_form = [c for c in all_components if c['available'] > 0]
    # --- MODIFICATION: Split requests into pending batches and history ---
    pending_incharge_requests = []
    other_requests = []
    for req in all_requests:
        if req['status'] == 'Pending Incharge':
            pending_incharge_requests.append(req)
        else:
            other_requests.append(req)

    # Group the pending ones by batch_id
    grouped_pending_requests = {}
    for req in pending_incharge_requests:
        # Use batch_id, or treat single legacy requests as their own batch
        batch_id = req.get('batch_id', f"req-{req['id']}")
        if batch_id not in grouped_pending_requests:
            grouped_pending_requests[batch_id] = []
        grouped_pending_requests[batch_id].append(req)

    # Sort the history
    other_requests.sort(key=lambda x: x['request_timestamp'], reverse=True)

    return render_template('admin_dashboard.html',
                           user=session['user'],
                           # Pass the new grouped data
                           grouped_pending_requests=grouped_pending_requests,
                           other_requests=other_requests,
                           components=components)


# ... in main.py, replace the entire @app.route('/admin/update_request') function ...

@app.route('/admin/update_request', methods=['POST'])
@role_required('admin')
def admin_update_request():
    batch_id = request.form.get('batch_id')
    new_status = request.form.get('new_status')  # "Approved" or "Rejected"
    admin_user = session['user']

    # --- START MODIFICATION: Get remarks from form ---
    incharge_remarks = request.form.get('incharge_remarks', '').strip()
    # --- END MODIFICATION ---

    all_requests = load_requests()
    all_components = load_components()

    if not batch_id:
        flash('Error: No Batch ID provided.', 'error')
        return redirect(url_for('admin_dashboard'))

    if batch_id.startswith('req-'):
        req_id = int(batch_id.split('-')[1])
        batch_requests = [req for req in all_requests if req['id'] == req_id and req['status'] == 'Pending Incharge']
    else:
        batch_requests = [req for req in all_requests if
                          req.get('batch_id') == batch_id and req['status'] == 'Pending Incharge']

    if not batch_requests:
        flash('Error: Request batch not found or already processed.', 'error')
        return redirect(url_for('admin_dashboard'))

    approval_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    approved_count = 0
    rejected_count = 0

    # --- START: NEW PARTIAL APPROVAL LOGIC ---
    for req in batch_requests:
        req['approval_timestamp'] = approval_time
        req['approver_email'] = admin_user['email']

        if new_status == 'Approved':
            # --- START: NEW PARTIAL APPROVAL LOGIC ---
            for req in batch_requests:
                req['approval_timestamp'] = approval_time
                req['approver_email'] = admin_user['email']

                target_component = next((comp for comp in all_components if comp['name'] == req['component_name']),
                                        None)

                # Check stock for *this specific item*
                if target_component:
                    available_stock = target_component['working_quantity'] - target_component['issued_quantity']

                    if available_stock >= req['quantity']:
                        # Stock is OK! Approve and update issued_quantity.
                        target_component['issued_quantity'] += req['quantity']

                        req['status'] = 'Approved'
                        req['incharge_remarks'] = incharge_remarks if incharge_remarks else "Approved."
                        approved_count += 1

                        audit_logger.info(
                            f'APPROVAL by {admin_user["email"]}: Req #{req["id"]}. Item {req["component_name"]} issued.')
                    else:
                        # Stock is NOT OK! Auto-reject this item.
                        req['status'] = 'Rejected'
                        rejection_note = f"Auto-rejected: Insufficient stock (Only {available_stock} available)."
                        req['incharge_remarks'] = f"{rejection_note} {incharge_remarks}".strip()
                        rejected_count += 1
                else:
                    # Component not found in DB
                    req['status'] = 'Rejected'
                    req[
                        'incharge_remarks'] = f"Auto-rejected: Component not found in database. {incharge_remarks}".strip()
                    rejected_count += 1
            # --- END: NEW PARTIAL APPROVAL LOGIC ---

        elif new_status == 'Rejected':
            # Admin is manually rejecting the whole batch
            req['status'] = 'Rejected'
            req['incharge_remarks'] = incharge_remarks if incharge_remarks else "Manually Rejected."
            rejected_count += 1
    # --- END: NEW PARTIAL APPROVAL LOGIC ---

    save_components(all_components)  # Save stock changes
    save_requests(all_requests)  # Save request status changes

    # Flash a summary message
    if approved_count > 0 and rejected_count > 0:
        flash(
            f'Batch {batch_id} partially approved: {approved_count} item(s) approved, {rejected_count} item(s) rejected (insufficient stock).',
            'warning')
    elif approved_count > 0:
        flash(f'Batch {batch_id} fully approved ({approved_count} item(s)).', 'success')
    elif rejected_count > 0:
        flash(f'Batch {batch_id} fully rejected ({rejected_count} item(s)).', 'success')

    return redirect(url_for('admin_dashboard'))
# --- END: MODIFIED Admin (Incharge) Routes ---


@app.route('/admin/download_report')
@role_required(['admin', 'hod'])
def admin_download_report():
    all_requests = load_requests()
    if not all_requests:
        flash('No requests to download.', 'error')
        return redirect(url_for('admin_dashboard'))
    output = io.StringIO()
    writer = csv.writer(output)
    headers = [
        'Request ID', 'Batch ID', 'Student ID', 'Student name', 'Department', 'Year of study',
        'Component ID', 'Component Name', 'Quantity', 'Purpose', 'Duration (Days)', 'Status',
        'Mentor Name', 'Mentor Approval',
        # --- START MODIFICATION ---
        'Mentor Remarks', 'HOD Approval', 'HOD Remarks',
        'Incharge Approval', 'Incharge Remarks',
        # --- END MODIFICATION ---
        'Component Issue Time', 'Due date', 'Date of return','Working Returned', 'Not Working Returned', 'Technician Remarks'
    ]
    writer.writerow(headers)
    for req in all_requests:
        writer.writerow([
            req.get('id', 'N/A'), req.get('batch_id', 'N/A'), req.get('student_email', 'N/A'),
            req.get('student_name', 'N/A'), req.get('student_dept', 'N/A'),
            req.get('student_year', 'N/A'), req.get('component_id', 'N/A'),
            req.get('component_name', 'N/A'), req.get('quantity', 'N/A'),
            req.get('project_description', 'N/A'),
            req.get('duration_days', 'N/A'), req.get('status', 'N/A'),
            req.get('mentor_name', 'N/A'),
            req.get('mentor_approval_timestamp', 'N/A'),
            # --- START MODIFICATION ---
            req.get('mentor_remarks', 'N/A'),
            req.get('hod_approval_timestamp', 'N/A'),
            req.get('hod_remarks', 'N/A'),
            req.get('approver_email', 'N/A'),  # This is Incharge Approval
            req.get('incharge_remarks', 'N/A'),
            # --- END MODIFICATION ---
            req.get('issue_timestamp', 'N/A'),
            req.get('due_date', 'N/A'), req.get('actual_return_timestamp', 'N/A'),
            req.get('working_count', 'N/A'),
            req.get('not_working_count', 'N/A'),
            req.get('tech_remarks', 'N/A')
        ])

    output.seek(0)
    return Response(
        output, mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=RACE_Lab_Full_Report.csv"}
    )


@app.route('/admin/download_audit_log')
@role_required(['admin', 'hod'])
def admin_download_audit_log():
    approval_pattern = re.compile(r"APPROVAL by (.*?): Req #(\d+). Stock (.*?) (\d+) -> (\d+)")
    collection_pattern = re.compile(r"COLLECTION by (.*?): Req #(\d+). Stock (.*?) (\d+) -> (\d+)")
    manual_pattern = re.compile(r'MANUAL UPDATE by (.*?): "(.*?)".*?Avail: (\d+) -> (\d+)')
    output = io.StringIO()
    writer = csv.writer(output)
    headers = ['Time (IST)', 'Action', 'Performed By', 'Req ID', 'Item', 'From(quantity)', 'To(quantity)']
    writer.writerow(headers)
    try:
        with open('audit.log', 'r') as f:
            for line in f:
                try:
                    timestamp, message = line.strip().split(' - ', 1)
                except ValueError:
                    continue
                action, performed_by, req_id, item, qty_from, qty_to = ('UNKNOWN', 'N/A', 'N/A', 'N/A', 'N/A', 'N/A')
                match_approval = approval_pattern.search(message)
                match_collection = collection_pattern.search(message)
                match_manual = manual_pattern.search(message)
                if match_approval:
                    action = 'APPROVAL'
                    performed_by, req_id, item, qty_from, qty_to = match_approval.groups()
                elif match_collection:
                    action = 'COLLECTION'
                    performed_by, req_id, item, qty_from, qty_to = match_collection.groups()
                elif match_manual:
                    action = 'MANUAL UPDATE'
                    performed_by, item, qty_from, qty_to = match_manual.groups()
                    req_id = 'N/A'
                writer.writerow([timestamp, action, performed_by, req_id, item, qty_from, qty_to])
    except FileNotFoundError:
        flash('The audit log file was not found.', 'error')
        return redirect(url_for('admin_dashboard'))
    output.seek(0)
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=RACE_Lab_Audit_Log.csv"}
    )


# --- Lab Technician (Lab Assistant) Routes (All Unchanged) ---
@app.route('/tech')
@role_required('technician')
def tech_dashboard():
    all_requests = load_requests()
    components = load_components()
    approved_requests = [req for req in all_requests if req['status'] == 'Approved']
    dispatched_requests = [req for req in all_requests if req['status'] == 'ISSUED']
    return render_template('tech_dashboard.html', user=session['user'],
                           approved_requests=approved_requests,
                           dispatched_requests=dispatched_requests,
                           components=components)



@app.route('/tech/dispatch', methods=['POST'])
@role_required('technician')
def tech_dispatch_item():
    request_id = int(request.form.get('request_id'))
    all_requests = load_requests()
    target_request = next((req for req in all_requests if req['id'] == request_id), None)
    if target_request and target_request['status'] == 'Approved':
        target_request['status'] = 'ISSUED'
        target_request['issue_timestamp'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        save_requests(all_requests)
        app.logger.info(f'ITEM ISSUED: Tech "{session["user"]["email"]}" ISSUED req #{request_id}')
        flash(f'Request #{request_id} marked as ISSUED.', 'success')
    else:
        flash('Error: Could not Issue request.', 'error')
    return redirect(url_for('tech_dashboard'))


# --- START: MODIFIED COLLECTION ROUTE (Handles GET and POST) ---
@app.route('/tech/collect_form/<int:request_id>', methods=['GET', 'POST'])
@role_required('technician')
def tech_collect_item_form(request_id):
    tech_user = session['user']
    all_requests = load_requests()
    all_components = load_components()

    target_request = next((req for req in all_requests if req['id'] == request_id), None)

    # Check if request is valid for collection
    if not target_request:
        abort(404)  # Not found
    print(target_request['status'])
    if target_request['status'].lower() != 'issued':
        flash('Error: This item is not in "Dispatched" state. Cannot collect.', 'error')
        return redirect(url_for('tech_dashboard'))

    target_component = next((comp for comp in all_components if comp['name'] == target_request['component_name']), None)

    if request.method == 'POST':
        # --- This is the POST logic (submitting the form) ---
        try:
            working_count = int(request.form.get('working_count'))
            not_working_count = int(request.form.get('not_working_count'))
            tech_remarks = request.form.get('tech_remarks', '').strip()
            total_returned = working_count + not_working_count

            # ... (Validation is unchanged) ...
            if total_returned != target_request['quantity']:
                flash(f"Error: Total items ({total_returned}) does not match issued ({target_request['quantity']}).",
                      'error')
                return redirect(url_for('tech_collect_item_form', request_id=request_id))

            # --- START: MODIFIED JSON UPDATE LOGIC ---
            if target_component:
                # 1. Decrease issued quantity by total returned
                target_component['issued_quantity'] -= total_returned
                # 2. Decrease working quantity by items that broke
                target_component['working_quantity'] -= not_working_count
                # 3. Increase not working quantity
                target_component['not_working_quantity'] += not_working_count

                # Sanity check to prevent negative numbers
                if target_component['issued_quantity'] < 0: target_component['issued_quantity'] = 0
                if target_component['working_quantity'] < 0: target_component['working_quantity'] = 0

                audit_logger.info(
                    f'COLLECTION by {tech_user["email"]}: Req #{request_id}. {working_count} working, {not_working_count} not working.')
                save_components(all_components)
            # --- END: MODIFIED JSON UPDATE LOGIC ---

            else:
                audit_logger.warning(
                    f'COLLECTION (No Stock Update): Tech {tech_user["email"]} collected Req #{request_id} but component "{target_request["component_name"]}" not in DB.')

            # Update the request object with new data
            target_request['status'] = 'Returned'
            target_request['actual_return_timestamp'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            target_request['working_count'] = working_count
            target_request['not_working_count'] = not_working_count
            target_request['tech_remarks'] = tech_remarks if tech_remarks else "N/A"

            save_requests(all_requests)
            flash(f'Request #{request_id} marked as Returned. {working_count} item(s) added back to stock.', 'success')
            return redirect(url_for('tech_dashboard'))

        except ValueError:
            flash('Error: Invalid count. Please enter numbers.', 'error')
            return redirect(url_for('tech_collect_item_form', request_id=request_id))
        except Exception as e:
            flash(f'An error occurred: {e}', 'error')
            return redirect(url_for('tech_collect_item_form', request_id=request_id))

    # --- This is the GET logic (showing the form) ---
    return render_template('tech_collect_form.html',
                           user=tech_user,
                           request=target_request)


# --- END: MODIFIED COLLECTION ROUTE ---


@app.route('/tech/add_inventory', methods=['POST'])
@role_required('technician')
def tech_add_inventory():
    """Adds a completely new component to the components.json database."""
    tech_user = session['user']

    new_id = request.form.get('new_component_id').upper()
    new_name = request.form.get('new_component_name')
    # --- MODIFICATION: New fields from form ---
    new_total = int(request.form.get('new_total'))
    new_working = int(request.form.get('new_working'))

    if new_total < new_working:
        flash('Error: "Working" count cannot be greater than "Total" count.', 'error')
        return redirect(url_for('tech_dashboard'))

    all_components = load_components()

    # ... (Duplicate checks are unchanged) ...
    for comp in all_components:
        if comp['id'] == new_id:
            flash(f'Error: Component ID "{new_id}" already exists.', 'error')
            return redirect(url_for('tech_dashboard'))
        if comp['name'].lower() == new_name.lower():
            flash(f'Error: Component Name "{new_name}" already exists.', 'error')
            return redirect(url_for('tech_dashboard'))

    # --- MODIFICATION: New component structure ---
    new_component = {
        "id": new_id,
        "name": new_name,
        "total_quantity": new_total,
        "working_quantity": new_working,
        "not_working_quantity": new_total - new_working,  # Calculated
        "issued_quantity": 0  # Starts at 0
    }
    all_components.append(new_component)
    save_components(all_components)

    audit_logger.info(
        f'NEW COMPONENT by {tech_user["email"]}: "{new_name}". Total: {new_total}, Working: {new_working}')
    flash(f'Success: Component "{new_name}" has been added.', 'success')
    return redirect(url_for('tech_dashboard'))


@app.route('/tech/update_inventory', methods=['POST'])
@role_required('technician')
def tech_update_inventory():
    component_name = request.form.get('component_name')
    # --- MODIFICATION: New fields from form ---
    new_total = int(request.form.get('new_total'))
    new_working = int(request.form.get('new_working'))
    tech_user = session['user']

    all_components = load_components()
    target_component = next((comp for comp in all_components if comp['name'] == component_name), None)

    if target_component:
        # Get old values for logging
        old_total = target_component['total_quantity']
        old_working = target_component['working_quantity']

        # --- MODIFICATION: Update logic ---
        # Set new totals
        target_component['total_quantity'] = new_total
        target_component['working_quantity'] = new_working
        # Recalculate not_working based on new total and working
        # (This assumes 'issued' items are a subset of 'working')
        # A more complex calc is needed if issued items can be non-working

        # Simpler: just update the two fields provided.
        # We must respect the 'not_working' and 'issued' counts.
        # This update is complex. Let's simplify:
        # The technician update *sets* the new total and working counts.
        # We must calculate the *new* not_working count.

        # Example: Total 30, Working 25, Issued 10, NotWorking 5
        # Tech says: New Total is 40, New Working is 35
        # This implies 10 new items were added, all working.
        # New NotWorking = 5 (unchanged)
        # New Issued = 10 (unchanged)

        # Let's assume the tech is ONLY updating total and working counts,
        # and not_working_quantity will be derived.

        current_not_working = target_component['not_working_quantity']
        if new_total < (new_working + current_not_working):
            flash('Error: New total is less than the sum of new working and existing not-working items.', 'error')
            return redirect(url_for('tech_dashboard'))

        # Update the component
        target_component['total_quantity'] = new_total
        target_component['working_quantity'] = new_working
        target_component['not_working_quantity'] = new_total - new_working

        audit_logger.info(
            f'MANUAL UPDATE by {tech_user["email"]}: "{component_name}". Total: {old_total}->{new_total}, Working: {old_working}->{new_working}')
        save_components(all_components)
        flash(f'Inventory for "{component_name}" updated.', 'success')
    else:
        flash('Error: Component not found.', 'error')
    return redirect(url_for('tech_dashboard'))

import os
# --- Run App ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, debug=True)